import asyncssh
from app.core.config import get_settings
from app.ssh.models import NodeConfig
from app.ssh.pool import SSHConnectionPool

# --- Compatibilidad: función legacy usada por lifespan antiguo y tests ---
async def get_new_ssh_connection(node_name: str | None = None):
    """
    Legacy: conecta a cuda3 por defecto.
    Nuevo: si se pasa node_name, conecta al nodo solicitado.
    Preferir usar SSHConnectionPool para multi-nodo.
    """
    settings = get_settings()
    nodes = settings.get_cluster_nodes()
    if not nodes:
        raise RuntimeError("No hay nodos configurados (revisa .env CUDA*_IP)")
    target: NodeConfig | None = None
    if node_name:
        for n in nodes:
            if n.name == node_name:
                target = n
                break
        if target is None:
            raise ValueError(f"Nodo {node_name} no configurado")
    else:
        # compat: prioritar cuda3 si existe, si no el primero
        for n in nodes:
            if n.name == "cuda3":
                target = n
                break
        target = target or nodes[0]

    return await asyncssh.connect(
        target.host,
        username=target.username,
        password=target.password,
        known_hosts=None,
        connect_timeout=settings.ssh_connect_timeout,
        keepalive_interval=settings.ssh_keepalive_interval,
    )


def get_pool_from_settings() -> SSHConnectionPool:
    """Factory para crear pool a partir de Settings (usado en lifespan)."""
    s = get_settings()
    return SSHConnectionPool(
        nodes=s.get_cluster_nodes(),
        connect_timeout=s.ssh_connect_timeout,
        keepalive_interval=s.ssh_keepalive_interval,
    )