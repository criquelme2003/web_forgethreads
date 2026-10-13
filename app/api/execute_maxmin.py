import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.api.deps import get_job_store, get_node_selector, get_ssh_pool, require_user
from app.api.launch import launch_with_notifier, resolve_node
from app.core.config import Settings, get_settings
from app.services.job_store import JobStore
from app.services.slurm_submit import build_new_job_command

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execute"])


class ExecuteMaxMinAckResponse(BaseModel):
    job_id: str
    status: str = "pending"


class ExecuteMaxMinRequest(BaseModel):
    numero_nodos: int = Field(..., gt=0, le=10000, description="Número de nodos", alias="numero_nodos")
    threshold: float = Field(..., ge=0, le=1, description="Threshold")
    conectividad_promedio: int = Field(..., ge=0, le=10000, description="Conectividad promedio")
    seed: int = Field(..., description="Seed")
    nodo: str | None = Field(default=None, description="Nodo/GPU objetivo; si se omite, se elige automáticamente")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @model_validator(mode="after")
    def _check_conectividad_menor_que_nodos(self) -> "ExecuteMaxMinRequest":
        if self.conectividad_promedio >= self.numero_nodos:
            raise ValueError("conectividad_promedio debe ser menor que numero_nodos")
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
    }
    normalized = {}
    for k, v in payload.items():
        key = mapping.get(k, k)
        normalized[key] = v
    return normalized


@router.post("/app/execute_maxmin", response_model=ExecuteMaxMinAckResponse)
async def execute_maxmin(
    payload: dict,
    user: Annotated[str, Depends(require_user)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
):
    """
    Encola un cómputo MaxMin: elige nodo, lanza new_job.sh en SLURM y encadena
    notifier.sh (afterany) para que reporte el resultado a /app/job_callback.
    """
    normalized = _normalize_payload(payload)
    try:
        data = ExecuteMaxMinRequest.model_validate(normalized)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=e.errors())

    selector = get_node_selector(request)
    pool = get_ssh_pool(request)
    nodo = await resolve_node(data.nodo, pool, selector)

    new_job_cmd = build_new_job_command(
        scripts_wf_dir=settings.scripts_wf_dir,
        numero_nodos=data.numero_nodos,
        threshold=data.threshold,
        conectividad_promedio=data.conectividad_promedio,
        seed=data.seed,
    )
    job_id = await launch_with_notifier(
        pool=pool,
        store=store,
        settings=settings,
        node=nodo,
        command=new_job_cmd,
        script="new_job.sh",
        kind="maxmin",
        params=data.model_dump(exclude={"nodo"}),
    )

    return ExecuteMaxMinAckResponse(job_id=job_id, status="pending")
