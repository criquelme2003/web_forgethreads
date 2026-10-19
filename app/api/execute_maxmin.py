import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.api.deps import (
    get_job_store,
    get_node_selector,
    get_project_store,
    get_ssh_pool,
    require_user,
)
from app.api.launch import submit_with_failover
from app.core.config import Settings, get_settings
from app.services.job_store import JobStore
from app.services.node_selector import NodeSelector
from app.services.project_store import ProjectNotFoundError, ProjectStore
from app.services.slurm_submit import build_new_job_command
from app.services.user_store import User
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execute"])


class ExecuteMaxMinAckResponse(BaseModel):
    job_id: str
    status: str = "pending"


class ExecuteMaxMinRequest(BaseModel):
    project_id: int = Field(..., description="Proyecto de simulación al que pertenece el cálculo")
    numero_nodos: int = Field(..., gt=0, le=10000, description="Número de nodos", alias="numero_nodos")
    threshold: float = Field(..., ge=0, le=1, description="Threshold")
    conectividad_promedio: float = Field(..., gt=0, le=10000, description="Conectividad promedio (decimal)")
    seed: int = Field(..., description="Seed")
    nodo: str | None = Field(default=None, description="Nodo/GPU objetivo; si se omite, se elige automáticamente")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @model_validator(mode="after")
    def _check_matrix_construction(self) -> "ExecuteMaxMinRequest":
        # Misma regla que matrix_construction.py en el cluster (RF-30): N par y c ≤ N/2 − 1.
        if self.numero_nodos % 2 != 0:
            raise ValueError("numero_nodos debe ser par")
        if self.conectividad_promedio > self.numero_nodos / 2 - 1:
            raise ValueError("conectividad_promedio debe ser ≤ numero_nodos/2 − 1")
        return self


# Soporta alias comunes del front (numero_nodos, nodos_totales, etc)
def _normalize_payload(payload: dict) -> dict:
    mapping = {
        "numero_nodos": "numero_nodos",
        "nodos_totales": "numero_nodos",
        "numeroDeNodos": "numero_nodos",
        "num_nodos": "numero_nodos",
        "conectividad_promedio": "conectividad_promedio",
        "conectividad": "conectividad_promedio",
        "c": "conectividad_promedio",
        "threshold": "threshold",
        "seed": "seed",
        "nodo": "nodo",
        "project_id": "project_id",
    }
    normalized = {}
    for k, v in payload.items():
        key = mapping.get(k, k)
        normalized[key] = v
    return normalized


@router.post("/app/execute_maxmin", response_model=ExecuteMaxMinAckResponse)
async def execute_maxmin(
    payload: dict,
    user: Annotated[User, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
):
    """
    Encola un cómputo MaxMin: elige nodo, lanza new_job.sh en SLURM y encadena
    notifier.sh (afterany) para que reporte el resultado a /app/job_callback.
    """
    normalized = _normalize_payload(payload)
    try:
        data = ExecuteMaxMinRequest.model_validate(normalized)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=e.errors(include_context=False, include_url=False))

    try:
        project = await projects.get_project(data.project_id)
    except ProjectNotFoundError:
        project = None
    if project is None or not (user.is_superadmin or project.is_member(user.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Proyecto {data.project_id} no existe")
    if project.tipo != "simulacion":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El proyecto no es de simulación")
    if project.estado != "abierto":
        raise HTTPException(status.HTTP_409_CONFLICT, f"El proyecto está {project.estado}")

    new_job_cmd = build_new_job_command(
        scripts_wf_dir=settings.scripts_wf_dir,
        numero_nodos=data.numero_nodos,
        threshold=data.threshold,
        conectividad_promedio=data.conectividad_promedio,
        seed=data.seed,
    )

    async def prepare(node: str) -> str:
        return new_job_cmd

    job_id, _ = await submit_with_failover(
        nodo=data.nodo,
        pool=pool,
        selector=selector,
        store=store,
        settings=settings,
        prepare=prepare,
        script="new_job.sh",
        kind="maxmin",
        params=data.model_dump(exclude={"nodo", "project_id"}),
        project_id=project.id,
        user_id=user.id,
    )

    return ExecuteMaxMinAckResponse(job_id=job_id, status="pending")
