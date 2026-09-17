from __future__ import annotations

from app.core.config import get_settings
from app.ssh.pool import SSHConnectionPool

# Singleton al estilo job_service._JOBS: un único pool/selector compartido por toda la app.
# Evita el bug de re-crear pool por request cuando request.state/app.state no coincide
# con el endpoint (fallback de deps.py).
_pool_singleton: SSHConnectionPool | None = None
_selector_singleton: object | None = None  # NodeSelector, tipado lazy para evitar ciclo
_slurm_singleton: object | None = None


def get_pool_from_settings() -> SSHConnectionPool:
    """Factory para crear pool a partir de Settings (usado en lifespan)."""
    s = get_settings()
    return SSHConnectionPool(
        nodes=s.get_cluster_nodes(),
        connect_timeout=s.ssh_connect_timeout,
        keepalive_interval=s.ssh_keepalive_interval,
    )


def get_pool_singleton() -> SSHConnectionPool:
    """Retorna el singleton del pool; si no existe lo crea lazy desde Settings."""
    global _pool_singleton
    if _pool_singleton is None:
        _pool_singleton = get_pool_from_settings()
    return _pool_singleton


def get_selector_singleton() -> NodeSelector:
    """Singleton del NodeSelector que reutiliza el mismo pool."""
    global _selector_singleton, _pool_singleton
    if _selector_singleton is not None:
        return _selector_singleton  # type: ignore[return-value]
    from app.repositories.slurm import SlurmRepository
    from app.services.node_selector import NodeSelector

    pool = get_pool_singleton()
    s = get_settings()
    _selector_singleton = NodeSelector(pool=pool, slurm_repo=SlurmRepository(), cache_ttl=s.slurm_poll_interval)
    return _selector_singleton  # type: ignore[return-value]


def get_slurm_singleton() -> SlurmRepository:
    global _slurm_singleton
    if _slurm_singleton is None:
        from app.repositories.slurm import SlurmRepository

        _slurm_singleton = SlurmRepository()
    return _slurm_singleton  # type: ignore[return-value]


def set_singletons(pool: SSHConnectionPool, selector, slurm_repo) -> None:
    """Fijado por lifespan para que deps reutilicen las instancias ya warmeadas."""
    global _pool_singleton, _selector_singleton, _slurm_singleton
    _pool_singleton = pool
    _selector_singleton = selector
    _slurm_singleton = slurm_repo


def clear_singletons() -> None:
    global _pool_singleton, _selector_singleton, _slurm_singleton
    _pool_singleton = None
    _selector_singleton = None
    _slurm_singleton = None