import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.repositories.slurm import SlurmRepository
from app.services.node_selector import NodeSelector
from app.ssh.connection import get_pool_from_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def global_lifespan(app: FastAPI):
    settings = get_settings()
    pool = get_pool_from_settings()
    selector = NodeSelector(pool=pool, slurm_repo=SlurmRepository(), cache_ttl=settings.slurm_poll_interval)

    # Warmup best-effort: no bloquea startup si un nodo está caído
    if pool.node_names:
        print(f"Opening SSH pool for nodes: {pool.node_names}")
        logger.info("Warming up SSH pool %s", pool.node_names)
        try:
            await pool.warmup()
        except Exception as e:
            logger.warning("Pool warmup failed (continues): %s", e)
        print(f"SSH pool ready: {pool.node_names}")
    else:
        print("No SSH nodes configured (revisa .env CUDA*_IP)")
        logger.warning("No SSH nodes configured")

    yield {"ssh_pool": pool, "node_selector": selector, "slurm_repo": SlurmRepository()}

    print("Closing SSH pool")
    try:
        await pool.close_all()
    except Exception as e:
        logger.warning("Error closing pool: %s", e)