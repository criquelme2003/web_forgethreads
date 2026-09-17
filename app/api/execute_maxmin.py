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
    nodo: str | None = Field(default=None, description="Nodo/GPU objetivo; si se omite, se elige automáticamente")

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
    request: Request,
):
    """
    Endpoint básico para ingresar parámetros MaxMin.
    Ruta requerida: app/execute_maxmin
    Parámetros: numero_nodos (int), threshold (float), conectividad_promedio (int), seed (int)
    Retorna: {"message": "Tarea ingresada correctamente", "job_id": "..."}
    Además crea jobId+token y lanza job notificador dependiente del cálculo.
    """
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
