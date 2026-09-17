import logging

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.repositories.slurm import SlurmRepository
from app.services.job_store import JobStore
from app.services.job_store import job_store as _job_store_singleton
from app.services.node_selector import NodeSelector
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger("ssh_pool")


def require_user(request: Request) -> str:
    """Devuelve el usuario de la sesión o lanza 401 si no hay sesión válida."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def _get_effective_settings(request: Request):
    """Respeta dependency_overrides para tests."""
    override = request.app.dependency_overrides.get(get_settings)
    if override is not None:
        return override()
    return get_settings()


def get_ssh_pool(request: Request) -> SSHConnectionPool:
    # Si hay override de settings (tests), crea pool aislado con esos settings para no contaminar singleton
    if request.app.dependency_overrides.get(get_settings) is not None:
        from app.ssh.pool import SSHConnectionPool

        settings = _get_effective_settings(request)
        pool = SSHConnectionPool(
            nodes=settings.get_cluster_nodes(),
            connect_timeout=settings.ssh_connect_timeout,
            keepalive_interval=settings.ssh_keepalive_interval,
        )
        logger.debug("FALLBACK TEST pool NUEVO id=%s", id(pool))
        return pool
    # Producción: singleton al estilo job_service._JOBS, evita abrir SSH por request
    from app.ssh.connection import get_pool_singleton

    pool = get_pool_singleton()
    logger.debug("SINGLETON pool id=%s nodes=%s", id(pool), pool.node_names)
    return pool


def get_node_selector(request: Request) -> NodeSelector:
    if request.app.dependency_overrides.get(get_settings) is not None:
        settings = _get_effective_settings(request)
        pool = get_ssh_pool(request)
        sel = NodeSelector(pool=pool, cache_ttl=settings.slurm_poll_interval)
        logger.debug("FALLBACK TEST selector NUEVO id=%s pool_id=%s", id(sel), id(pool))
        return sel
    from app.ssh.connection import get_selector_singleton

    sel = get_selector_singleton()
    logger.debug("SINGLETON selector id=%s pool_id=%s", id(sel), id(sel.pool))
    return sel


def get_slurm_repo(request: Request) -> SlurmRepository:
    if request.app.dependency_overrides.get(get_settings) is not None:
        return SlurmRepository()
    from app.ssh.connection import get_slurm_singleton

    return get_slurm_singleton()
