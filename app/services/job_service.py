import logging
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

# Almacen en memoria (suficiente para flujo simple). Para prod cambiar a DB.
_JOBS: dict[str, dict[str, Any]] = {}  # jobId -> {token, params, status, max_depth, created_at}


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def create_job(job_id: str, params: dict, token: str | None = None) -> str:
    token = token or generate_token()
    _JOBS[job_id] = {
        "token": token,
        "params": params,
        "status": "pending",
        "max_depth": None,
        "created_at": time.time(),
        "notifier_job_id": None,
    }
    logger.info("job created %s token=***", job_id)
    return token


def verify_token(job_id: str, token: str) -> bool:
    rec = _JOBS.get(job_id)
    if not rec:
        return False
    # compare_digest evita timing attacks
    return secrets.compare_digest(rec.get("token", ""), token or "")


def set_result(job_id: str, max_depth: int | float) -> bool:
    rec = _JOBS.get(job_id)
    if not rec:
        # si llega resultado de job no registrado (reinicio), lo creamos igual
        _JOBS[job_id] = {"token": "", "params": {}, "status": "done", "max_depth": max_depth, "created_at": time.time()}
        logger.warning("set_result para job desconocido %s, creado", job_id)
        return True
    rec["max_depth"] = max_depth
    rec["status"] = "done"
    rec["done_at"] = time.time()
    logger.info("job %s done max_depth=%s", job_id, max_depth)
    return True


def get_job(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def list_jobs() -> dict:
    return dict(_JOBS)


def clear_all() -> None:
    _JOBS.clear()
