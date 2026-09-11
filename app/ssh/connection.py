from app.core.config import get_settings
from app.ssh.pool import SSHConnectionPool


def get_pool_from_settings() -> SSHConnectionPool:
    """Factory para crear pool a partir de Settings (usado en lifespan)."""
    s = get_settings()
    return SSHConnectionPool(
        nodes=s.get_cluster_nodes(),
        connect_timeout=s.ssh_connect_timeout,
        keepalive_interval=s.ssh_keepalive_interval,
    )