import logging
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.services.job_store import job_store
from app.services.node_selector import NodeSelector
from app.services.project_store import project_store
from app.services.user_store import User, UserNotFoundError, UserStore, user_store
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger("ssh_pool")

def get_job_store():
    return job_store


def get_project_store():
    return project_store


def get_user_store():
    return user_store


async def require_user(
    request: Request, users: Annotated[UserStore, Depends(get_user_store)]
) -> User:
    """Devuelve el usuario de la sesión o lanza 401 si no hay sesión válida.

    Aplica idle timeout: si no hubo actividad en los últimos
    `session_idle_timeout_seconds`, la sesión se invalida aunque la cookie
    siga vigente (max_age). Cada request válido renueva `last_seen`
    (sliding window). Un admin desactivado pierde el acceso en el siguiente request (RF-04).
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    settings = _get_effective_settings(request)
    last_seen = request.session.get("last_seen")
    now = time.time()
    if last_seen is None or (now - last_seen) > settings.session_idle_timeout_seconds:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to inactivity",
        )

    try:
        user = await users.get(int(user_id))
    except (UserNotFoundError, ValueError):
        user = None
    if user is None or not user.activo:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    request.session["last_seen"] = now
    return user


async def require_superadmin(user: Annotated[User, Depends(require_user)]) -> User:
    """Solo el superadmin gestiona administradores (RF-01, RF-48)."""
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo el superadmin puede hacer esto")
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
