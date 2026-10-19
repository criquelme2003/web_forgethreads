"""Lanzamiento común de jobs SLURM: elección de nodo, sbatch del cómputo y notifier encadenado.

Prevención al enviar (RF-46): el estado de los nodos se consulta sin la caché del selector y,
si en un nodo falla la preparación, el `sbatch` o el job queda bloqueado (ReqNodeNotAvail,
nodo drenado, etc.), se cancela y se prueba con el siguiente nodo disponible. Todo ocurre antes
de encadenar el notifier, así que no hay relanzamiento de jobs ya aceptados (RF-43).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, status

from app.core.config import Settings
from app.services.job_store import JobKind, JobStore
from app.services.node_selector import NodeSelector
from app.services.slurm_submit import (
    JobIdParseError,
    blocking_reason,
    build_job_state_command,
    build_notifier_command,
    build_scancel_command,
    parse_job_id,
    parse_job_state,
    redact_token,
)
from app.ssh.models import NodeStatus
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

# Recibe el nodo elegido y devuelve el comando sbatch (puede subir archivos a ese nodo antes).
PrepareCommand = Callable[[str], Awaitable[str]]


class _AttemptFailed(Exception):
    """El job no quedó encolado en este nodo; se puede probar con el siguiente."""


async def candidate_nodes(
    nodo: str | None, pool: SSHConnectionPool, selector: NodeSelector, settings: Settings
) -> list[str]:
    """Nodos donde intentar el envío, en orden. Con `nodo` explícito no hay failover."""
    if nodo is not None:
        if nodo not in pool.node_names:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nodo desconocido: {nodo}")
        statuses = await selector.get_all_status(force_refresh=True)
        node_status = next((s for s in statuses if s.name == nodo), None)
        if node_status is None or not node_status.is_available:
            detail = f"Nodo no disponible: {nodo}"
            if node_status is not None:
                detail += f" ({_describe(node_status)})"
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)
        return [nodo]

    ranked, statuses = await selector.ranked_candidates(
        force_refresh=True, gpu_idle_threshold=settings.gpu_idle_threshold
    )
    if not ranked:
        estados = "; ".join(f"{s.name}: {_describe(s)}" for s in statuses) or "sin nodos configurados"
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No hay nodos disponibles ({estados})")
    return [s.name for s in ranked]


def _describe(s: NodeStatus) -> str:
    if not s.reachable:
        return "inalcanzable"
    text = s.state.value
    if s.state_flags:
        text += "+" + "+".join(f.lower() for f in s.state_flags)
    if s.reason:
        text += f", motivo: {s.reason}"
    return text


async def _submit_on_node(
    pool: SSHConnectionPool, settings: Settings, node: str, command: str, script: str
) -> str:
    """sbatch en `node` y verificación de que el job no quedó bloqueado. Devuelve el JOBID."""
    logger.info("Lanzando %s en %s: %s", script, node, command)
    result = await pool.run_on_node(node, command)
    if result is None or result.exit_status != 0:
        stderr = (result.stderr if result else "") or "sin respuesta del nodo"
        logger.warning("%s falló en %s: %s", script, node, stderr)
        raise _AttemptFailed(f"sbatch falló ({str(stderr).strip()[:200]})")
    try:
        job_id = parse_job_id(str(result.stdout))
    except JobIdParseError as e:
        logger.warning("No se pudo parsear JOBID en %s: %s", node, e)
        raise _AttemptFailed("no se pudo determinar el JOBID")

    if settings.submit_check_delay_s > 0:
        await asyncio.sleep(settings.submit_check_delay_s)
    check = await pool.run_on_node(node, build_job_state_command(job_id))
    parsed = parse_job_state(str(check.stdout)) if check is not None and check.exit_status == 0 else None
    reason = blocking_reason(*parsed) if parsed else None
    if reason:
        logger.warning("Job %s quedó bloqueado en %s (%s); se cancela", job_id, node, reason)
        cancel = await pool.run_on_node(node, build_scancel_command(job_id))
        if cancel is None or cancel.exit_status != 0:
            logger.warning("No se pudo cancelar el job %s en %s", job_id, node)
        raise _AttemptFailed(f"job {job_id} bloqueado: {reason}")
    return job_id


async def _chain_notifier(
    *,
    pool: SSHConnectionPool,
    store: JobStore,
    settings: Settings,
    node: str,
    job_id: str,
    script: str,
    kind: JobKind,
    params: dict | None,
    project_id: int | None,
    input_id: int | None,
    input_version: int | None,
    user_id: int | None,
) -> None:
    # El token se genera recién aquí (JobStore.create) y se pasa a notifier.sh, que es
    # el único script que llama a /app/job_callback y por tanto el único que lo necesita.
    record = await store.create(
        job_id, node=node, kind=kind, params=params, project_id=project_id, input_id=input_id,
        input_version=input_version, user_id=user_id,
    )

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


async def submit_with_failover(
    *,
    nodo: str | None,
    pool: SSHConnectionPool,
    selector: NodeSelector,
    store: JobStore,
    settings: Settings,
    prepare: PrepareCommand,
    script: str,
    kind: JobKind,
    params: dict | None = None,
    project_id: int | None = None,
    input_id: int | None = None,
    input_version: int | None = None,
    user_id: int | None = None,
) -> tuple[str, str]:
    """Envía el job al mejor nodo disponible, probando los siguientes si falla. Devuelve (JOBID, nodo).

    `prepare(node)` devuelve el comando sbatch para ese nodo (y puede subirle archivos); si lanza
    HTTPException, ese nodo se descarta. Lanza HTTPException 502 si ningún nodo acepta el job.
    """
    attempts: list[str] = []
    for node in await candidate_nodes(nodo, pool, selector, settings):
        try:
            command = await prepare(node)
            job_id = await _submit_on_node(pool, settings, node, command, script)
        except HTTPException as e:
            attempts.append(f"{node}: {e.detail}")
            continue
        except _AttemptFailed as e:
            attempts.append(f"{node}: {e}")
            continue
        if attempts:
            logger.info("%s %s encolado en %s tras fallar en: %s", script, job_id, node, "; ".join(attempts))
        await _chain_notifier(
            pool=pool, store=store, settings=settings, node=node, job_id=job_id, script=script,
            kind=kind, params=params, project_id=project_id, input_id=input_id,
            input_version=input_version, user_id=user_id,
        )
        return job_id, node

    raise HTTPException(
        status.HTTP_502_BAD_GATEWAY, f"No se pudo lanzar {script}: {'; '.join(attempts)}"
    )
