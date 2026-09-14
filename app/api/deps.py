import logging

from fastapi import HTTPException, Request, status

from app.repositories.slurm import SlurmRepository
from app.services.job_store import JobStore
from app.services.job_store import job_store as _job_store_singleton
from app.services.node_selector import NodeSelector
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)


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
    from app.core.config import get_settings

    override = request.app.dependency_overrides.get(get_settings)
    if override is not None:
        return override()
    return get_settings()


def get_ssh_pool(request: Request) -> SSHConnectionPool:
    from_app_state = getattr(request.app.state, "ssh_pool", None)
    from_request_state = request.state.__dict__.get("ssh_pool")
    pool = from_app_state or from_request_state
    # lifespan con yield dict expone via app.state + request.state
    if pool is None:
        # fallback para tests sin lifespan (TestClient sin warmup)
        from app.ssh.pool import SSHConnectionPool

        settings = _get_effective_settings(request)
        pool = SSHConnectionPool(
            nodes=settings.get_cluster_nodes(),
            connect_timeout=settings.ssh_connect_timeout,
            keepalive_interval=settings.ssh_keepalive_interval,
        )
        print(
            f"[DEBUG ssh_pool] FALLBACK: pool NUEVO id={id(pool)} "
            f"(app.state={from_app_state}, request.state={from_request_state})"
        )
    else:
        print(
            f"[DEBUG ssh_pool] usando pool id={id(pool)} "
            f"(origen={'app.state' if from_app_state is not None else 'request.state'})"
        )
    return pool


def get_node_selector(request: Request) -> NodeSelector:
    selector = getattr(request.app.state, "node_selector", None) or request.state.__dict__.get("node_selector")
    if selector is None:
        settings = _get_effective_settings(request)
        pool = get_ssh_pool(request)
        selector = NodeSelector(pool=pool, cache_ttl=settings.slurm_poll_interval)
    return selector


def get_slurm_repo(request: Request) -> SlurmRepository:
    repo = getattr(request.app.state, "slurm_repo", None)
    if repo is None:
        return SlurmRepository()
    return repo


def get_job_store() -> JobStore:
    """Singleton a nivel de módulo (ver app/services/job_store.py); no depende de request.app.state."""
    return _job_store_singleton
