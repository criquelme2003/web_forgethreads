from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    auth_username: str
    auth_password: str
    session_secret_key: str
    # --- Cluster nodes (homogéneos, conexión directa) ---
    # Se mantienen cuda3_* por compatibilidad y se añaden cuda1/cuda2
    cuda1_ip: str | None = None
    cuda1_username: str | None = None
    cuda1_password: str | None = None
    cuda2_ip: str | None = None
    cuda2_username: str | None = None
    cuda2_password: str | None = None
    cuda3_ip: str | None = None
    cuda3_username: str | None = None
    cuda3_password: str | None = None

    # Tuning SLURM / SSH pool
    slurm_poll_interval: int = 15  # TTL cache selector (s)
    ssh_connect_timeout: int = 10
    ssh_keepalive_interval: int = 30

    def get_cluster_nodes(self) -> list["NodeConfig"]:
        """Retorna solo nodos configurados (ip != None)."""
        from app.ssh.models import NodeConfig

        nodes: list[NodeConfig] = []
        for idx, (ip, user, pwd) in enumerate(
            [
                (self.cuda1_ip, self.cuda1_username, self.cuda1_password),
                (self.cuda2_ip, self.cuda2_username, self.cuda2_password),
                (self.cuda3_ip, self.cuda3_username, self.cuda3_password),
            ],
            start=1,
        ):
            if ip:
                # fallback: si user/pwd no seteados, usar los de cuda3 si existen
                nodes.append(
                    NodeConfig(
                        name=f"cuda{idx}",
                        host=ip,
                        username=user or self.cuda3_username or "",
                        password=pwd or self.cuda3_password or "",
                    )
                )
        return nodes
    
# ENABLE CACHE PERSISTENCE AND SINGLETON PATTERN
@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
