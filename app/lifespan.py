import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.repositories.slurm import SlurmRepository
from app.services.demo_seed import seed_demo
from app.services.node_selector import NodeSelector
from app.services.project_store import project_store
from app.ssh.connection import clear_singletons, get_pool_from_settings, set_singletons

logger = logging.getLogger(__name__)


@asynccontextmanager
async def global_lifespan(app: FastAPI):
    settings = get_settings()
    pool = get_pool_from_settings()
    print(f"[DEBUG ssh_pool] pool creado en lifespan id={id(pool)}")
    selector = NodeSelector(pool=pool, slurm_repo=SlurmRepository(), cache_ttl=settings.slurm_poll_interval)
    # Fija singletons al estilo job_service._JOBS para que deps no creen pool por request
    set_singletons(pool, selector)

    # Warmup best-effort: no bloquea startup si un nodo está caído
    if pool.node_names:
        print(f"Opening SSH pool for nodes: {pool.node_names}")
        logger.info("Warming up SSH pool %s", pool.node_names)
        logger.debug("[DEBUG ssh_pool] pool creado en lifespan id=%s", id(pool))
        try:
            await pool.warmup()
        except Exception as e:
            logger.warning("Pool warmup failed (continues): %s", e)
        print(f"SSH pool ready: {pool.node_names}")
        logger.debug("[DEBUG ssh_pool] SSH pool ready id=%s", id(pool))
    else:
        print("No SSH nodes configured (revisa .env CUDA*_IP)")
        logger.warning("No SSH nodes configured")
    
    if settings.seed_demo_data:
        try:
            await seed_demo(project_store, owner=settings.auth_username)
        except Exception as e:  # noqa: BLE001  (los datos de muestra nunca impiden arrancar)
            logger.warning("No se pudieron crear los datos de muestra: %s", e)

    yield

    print("Closing SSH pool")
    try:
        await pool.close_all()
    except Exception as e:
        logger.warning("Error closing pool: %s", e)
    finally:
        clear_singletons()