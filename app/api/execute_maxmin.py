import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import get_job_store, get_node_selector, get_ssh_pool, require_user
from app.core.config import Settings, get_settings
from app.services.job_store import JobStore
from app.services.node_selector import NoAvailableNodeError, NodeSelector
from app.services.session_token import issue_auth_token
from app.services.slurm_submit import (
    JobIdParseError,
    build_new_job_command,
    build_notifier_command,
    parse_job_id,
    redact_token,
)
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execute"])


class ExecuteMaxMinRequest(BaseModel):
    numero_nodos: int = Field(..., gt=0, le=100000, description="Número de nodos", alias="numero_nodos")
    threshold: float = Field(..., ge=0, le=1_000_000, description="Threshold")
    conectividad_promedio: int = Field(..., ge=0, le=100000, description="Conectividad promedio")
    seed: int = Field(..., description="Seed")
    nodo: str | None = Field(default=None, description="Nodo/GPU objetivo; si se omite, se elige automáticamente")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class ExecuteMaxMinAckResponse(BaseModel):
    job_id: str
    status: str = "pending"


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
        # keep last wins
        normalized[key] = v
    return normalized


async def _resolve_node(nodo: str | None, pool: SSHConnectionPool, selector: NodeSelector) -> str:
    if nodo is None:
        try:
            best = await selector.select_best()
        except NoAvailableNodeError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No hay nodos disponibles: {e}")
        return best.name

    if nodo not in pool.node_names:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nodo desconocido: {nodo}")
    statuses = await selector.get_all_status()
    is_available = any(s.name == nodo and s.is_available for s in statuses)
    if not is_available:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nodo no disponible: {nodo}")
    return nodo


@router.post("/app/execute_maxmin", response_model=ExecuteMaxMinAckResponse)
@router.post("/execute_maxmin", response_model=ExecuteMaxMinAckResponse, include_in_schema=False)
async def execute_maxmin(
    payload: dict,
    user: Annotated[str, Depends(require_user)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    store: Annotated[JobStore, Depends(get_job_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExecuteMaxMinAckResponse:
    """
    Lanza el flujo real de jobs SLURM: sbatch new_job.sh, luego sbatch --dependency=afterok
    notifier.sh encadenado, en el nodo elegido (explícito o vía NodeSelector). Ruta requerida:
    app/execute_maxmin.
    """
    normalized = _normalize_payload(payload)
    try:
        data = ExecuteMaxMinRequest.model_validate(normalized)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=e.errors())

    nodo = await _resolve_node(data.nodo, pool, selector)
    auth_token = issue_auth_token(settings.auth_password, settings.session_secret_key)

    new_job_cmd = build_new_job_command(
        scripts_wf_dir=settings.scripts_wf_dir,
        numero_nodos=data.numero_nodos,
        threshold=data.threshold,
        conectividad_promedio=data.conectividad_promedio,
        seed=data.seed,
        auth_token=auth_token,
    )
    logger.info("Lanzando new_job en %s: %s", nodo, redact_token(new_job_cmd, auth_token))
    result = await pool.run_on_node(nodo, new_job_cmd)
    if result is None or result.exit_status != 0:
        logger.warning("new_job.sh falló en %s", nodo)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No se pudo lanzar new_job.sh en nodo {nodo}")

    try:
        job_id = parse_job_id(str(result.stdout))
    except JobIdParseError as e:
        logger.warning("No se pudo parsear JOBID en %s: %s", nodo, e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No se pudo determinar el JOBID de new_job.sh")

    callback_url = f"{settings.public_callback_base_url}/app/job_callback"
    notifier_cmd = build_notifier_command(
        scripts_wf_dir=settings.scripts_wf_dir,
        job_id=job_id,
        auth_token=auth_token,
        callback_url=callback_url,
    )
    logger.info("Lanzando notifier en %s: %s", nodo, redact_token(notifier_cmd, auth_token))
    notifier_result = await pool.run_on_node(nodo, notifier_cmd)
    if notifier_result is None or notifier_result.exit_status != 0:
        logger.warning("notifier.sh falló en %s para job %s; new_job puede seguir corriendo sin notificar", nodo, job_id)
        await store.mark_error(
            job_id,
            node=nodo,
            logs=f"No se pudo encadenar notifier.sh: el job {job_id} puede seguir corriendo en SLURM sin notificación",
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"new_job {job_id} se encoló pero no se pudo lanzar notifier.sh",
        )

    await store.create(job_id, node=nodo)
    return ExecuteMaxMinAckResponse(job_id=job_id, status="pending")
