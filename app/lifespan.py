import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.db.engine import display_url, dispose_engines, get_engine
from app.repositories.slurm import SlurmRepository
from app.services.demo_seed import seed_demo, seed_demo_posts
from app.services.job_store import job_store
from app.services.node_selector import NodeSelector
from app.services.post_store import post_store
from app.services.project_store import project_store
from app.services.user_store import user_store
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
    
    # Aplica las migraciones al arrancar para que un problema con la base se vea en el log de
    # inicio; si falla, la app arranca igual y se reintenta en la primera consulta.
    database = settings.effective_database_url
    try:
        await get_engine(database)
        logger.info("Base de datos lista: %s", display_url(database))
    except Exception as e:  # noqa: BLE001
        logger.error("No se pudo preparar la base de datos %s: %s", display_url(database), e)

    # Superadmin inicial desde AUTH_USERNAME / AUTH_PASSWORD (RF-02); también se asegura al ingresar.
    superadmin = None
    try:
        superadmin = await user_store.ensure_superadmin(settings.auth_username, settings.auth_password)
    except Exception as e:  # noqa: BLE001
        logger.error("No se pudo crear el superadmin: %s", e)

    if settings.seed_demo_data and superadmin is not None:
        try:
            await seed_demo(project_store, owner_id=superadmin.id)
            await seed_demo_posts(project_store, post_store, job_store, superadmin, settings, pool)
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
        await dispose_engines()