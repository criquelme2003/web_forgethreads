import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import get_node_selector, get_ssh_pool, require_user
from app.core.config import get_settings
from app.services import job_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execute"])


class ExecuteMaxMinRequest(BaseModel):
    numero_nodos: int = Field(..., gt=0, le=100000, description="Número de nodos", alias="numero_nodos")
    threshold: float = Field(..., ge=0, le=1_000_000, description="Threshold")
    conectividad_promedio: int = Field(..., ge=0, le=100000, description="Conectividad promedio")
    seed: int = Field(..., description="Seed")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class ExecuteMaxMinResponse(BaseModel):
    message: str = "Tarea ingresada correctamente"
    data: ExecuteMaxMinRequest
    job_id: str | None = None
    notifier_job_id: str | None = None
    job_token: str | None = None  # expuesto para debug front (ver JSON)
    node: str | None = None
    pwd: dict | None = None  # {"node": str, "output": str, "exit_status": int}
    request: dict | None = None  # eco del payload normalizado


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
    }
    normalized = {}
    for k, v in payload.items():
        key = mapping.get(k, k)
        # keep last wins
        normalized[key] = v
    return normalized


@router.post("/app/execute_maxmin", response_model=ExecuteMaxMinResponse)
@router.post("/execute_maxmin", response_model=ExecuteMaxMinResponse, include_in_schema=False)
async def execute_maxmin(
    payload: dict,
    user: Annotated[str, Depends(require_user)],
    request: Request,
):
    """
    Endpoint básico para ingresar parámetros MaxMin.
    Ruta requerida: app/execute_maxmin
    Parámetros: numero_nodos (int), threshold (float), conectividad_promedio (int), seed (int)
    Retorna: {"message": "Tarea ingresada correctamente", "job_id": "..."}
    Además crea jobId+token y lanza job notificador dependiente del cálculo.
    """
    # Normaliza alias y valida con pydantic
    normalized = _normalize_payload(payload)
    try:
        data = ExecuteMaxMinRequest.model_validate(normalized)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=e.errors())

    # --- Crea jobId/token y lanza SLURM (si hay nodos) ---
    pseudo_job_id = None
    notifier_job_id = None
    token = job_service.generate_token()
    node_name_for_pwd: str | None = None
    pwd_result: dict | None = None
    callback_base = None
    try:
        callback_base = get_settings().callback_base_url if hasattr(get_settings(), "callback_base_url") else None
    except Exception:
        callback_base = None
    if not callback_base:
        callback_base = f"{request.url.scheme}://{request.headers.get('host', 'localhost:8000')}"

    try:
        from app.repositories.slurm import SlurmRepository

        selector = get_node_selector(request)
        pool = get_ssh_pool(request)
        try:
            best_node = await selector.select_best(require_gpu=False)
            node_name = best_node.name
        except Exception:
            node_name = pool.node_names[0] if pool.node_names else None

        node_name_for_pwd = node_name

        if node_name:
            # pwd simple en el nodo seleccionado (requisito front debug)
            try:
                pwd_res = await pool.run_on_node(node_name, "pwd", timeout=5.0)
                if pwd_res is not None:
                    pwd_result = {
                        "node": node_name,
                        "output": (pwd_res.stdout or "").strip(),
                        "exit_status": pwd_res.exit_status,
                    }
                else:
                    pwd_result = {"node": node_name, "output": None, "error": "no connection"}
            except Exception as e:
                pwd_result = {"node": node_name, "error": str(e)}

            conn = await pool.try_get_connection(node_name, timeout=5.0)
            if conn is not None:
                slurm = SlurmRepository()
                wrap_calc = f"echo $((10 + RANDOM % 90)) > /tmp/result_${{SLURM_JOB_ID}}.txt; sleep 1; cat /tmp/result_${{SLURM_JOB_ID}}.txt"
                pseudo_job_id = await slurm.submit_job(conn, wrap_calc, job_name=f"maxmin_{data.seed}")
                job_service.create_job(pseudo_job_id, data.model_dump(), token)
                callback_url = f"{callback_base}/app/jobs/{pseudo_job_id}/result"
                wrap_notifier = (
                    f"MAXD=$(cat /tmp/result_{pseudo_job_id}.txt 2>/dev/null || echo 0); "
                    f"curl -s -X POST {callback_url} -H 'Authorization: Bearer {token}' "
                    f"-H 'Content-Type: application/json' -d '{{\\\"max_depth\\\": '$MAXD'}}' || true"
                )
                try:
                    notifier_job_id = await slurm.submit_notifier_job(conn, pseudo_job_id, wrap_notifier, job_name=f"notifier_{pseudo_job_id}")
                    rec = job_service.get_job(pseudo_job_id)
                    if rec is not None:
                        rec["notifier_job_id"] = notifier_job_id
                except Exception as e:
                    logger.warning("notifier submit failed job %s: %s", pseudo_job_id, e)
            else:
                raise RuntimeError("no SSH conn")
        else:
            raise RuntimeError("no nodes")
    except Exception as e:
        logger.warning("SLURM submit falló, usando mock jobId: %s", e)
        pseudo_job_id = f"mock_{uuid.uuid4().hex[:8]}"
        job_service.create_job(pseudo_job_id, data.model_dump(), token)
        notifier_job_id = None
        if pwd_result is None:
            pwd_result = {"output": None, "error": "mock (sin SSH)", "node": node_name_for_pwd}

    return ExecuteMaxMinResponse(
        message="Tarea ingresada correctamente",
        data=data,
        job_id=pseudo_job_id,
        notifier_job_id=notifier_job_id,
        job_token=token,
        node=node_name_for_pwd,
        pwd=pwd_result,
        request=normalized,
    )


# Front opcional JSON? El HTML se sirve desde /front/execute_maxmin
# Para que POST desde form html funcione con application/json, el front usará fetch.
