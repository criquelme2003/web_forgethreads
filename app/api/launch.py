"""Lanzamiento común de jobs SLURM: elección de nodo, sbatch del cómputo y notifier encadenado."""

import logging

from fastapi import HTTPException, status

from app.core.config import Settings
from app.services.job_store import JobKind, JobStore
from app.services.node_selector import NoAvailableNodeError, NodeSelector
from app.services.slurm_submit import (
    JobIdParseError,
    build_notifier_command,
    parse_job_id,
    redact_token,
)
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)


async def resolve_node(nodo: str | None, pool: SSHConnectionPool, selector: NodeSelector) -> str:
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


async def launch_with_notifier(
    *,
    pool: SSHConnectionPool,
    store: JobStore,
    settings: Settings,
    node: str,
    command: str,
    script: str,
    kind: JobKind,
    params: dict | None = None,
) -> str:
    """Corre `command` (un sbatch) en `node`, registra el job y encadena notifier.sh.

    Devuelve el JOBID. Lanza HTTPException 502 si algún paso falla en el cluster.
    """
    logger.info("Lanzando %s en %s: %s", script, node, command)
    result = await pool.run_on_node(node, command)
    if result is None or result.exit_status != 0:
        logger.warning("%s falló en %s: %s", script, node, result.stderr if result else None)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No se pudo lanzar {script} en nodo {node}")

    try:
        job_id = parse_job_id(str(result.stdout))
    except JobIdParseError as e:
        logger.warning("No se pudo parsear JOBID en %s: %s", node, e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No se pudo determinar el JOBID de {script}")

    # El token se genera recién aquí (JobStore.create) y se pasa a notifier.sh, que es
    # el único script que llama a /app/job_callback y por tanto el único que lo necesita.
    record = await store.create(job_id, node=node, kind=kind, params=params)

    callback_url = f"{settings.public_callback_base_url}/app/job_callback"
    notifier_cmd = build_notifier_command(
        scripts_wf_dir=settings.scripts_wf_dir,
        job_id=job_id,
        auth_token=record.token,
        callback_url=callback_url,
    )
    logger.info("Lanzando notifier en %s: %s", node, redact_token(notifier_cmd, record.token))
    notifier_result = await pool.run_on_node(node, notifier_cmd)
    if notifier_result is None or notifier_result.exit_status != 0:
        logger.warning(
            "notifier.sh falló en %s para job %s; %s puede seguir corriendo sin notificar",
            node, job_id, script,
        )
        await store.mark_error(
            job_id,
            node=node,
            logs=f"No se pudo encadenar notifier.sh: el job {job_id} puede seguir corriendo en SLURM sin notificación",
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"{script} {job_id} se encoló pero no se pudo lanzar notifier.sh",
        )
    return job_id
