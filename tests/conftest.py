import asyncio
import os
import tempfile

# El lifespan de la app usa la configuración real (no los overrides de los tests): se apunta a
# una base temporal antes de que se lea, para no tocar data/jobs.db.
os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="wf-tests-"), "jobs.db")
os.environ.pop("DATABASE_URL", None)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.api.deps import get_job_store, get_project_store
from app.core.config import Settings, get_settings
from app.db import engine as db_engine
from app.db.engine import get_engine
from app.db.schema import metadata
from app.main import app
from app.services.job_store import JobStore
from app.services.project_store import ProjectStore

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "testpass"

# Rutas protegidas: requieren cookie de sesión válida.
# (path, marcadores que deben aparecer en el JSON devuelto tras autenticarse)
PROTECTED_ROUTES = [
    (
        "/app/ui-meta",
        (
            '"database"',
            '"gpu_idle_threshold"',
        ),
    ),
    (
        "/app/nodes/available",
        (
            '"options"',
            '"hint"',
        ),
    ),
    (
        "/app/cluster_status",
        (
            '"rows"',
            '"check_idle"',
            '"gpu_idle_threshold"',
        ),
    ),
]


def _override_settings() -> Settings:
    return Settings(
        auth_username=TEST_USERNAME,
        auth_password=TEST_PASSWORD,
        session_secret_key="test-secret-key-0123456789",
        cuda3_password="1234",
        cuda3_username="test_user",
        cuda3_ip="192.192.192",
        public_callback_base_url="http://testserver",
        submit_check_delay_s=0,
    )


# Con TEST_DATABASE_URL los tests corren contra esa base (ej. un PostgreSQL local), que se vacía
# antes de cada test. Sin ella, cada test usa un SQLite temporal.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
sqlite_only = pytest.mark.skipif(bool(TEST_DATABASE_URL), reason="prueba propia de SQLite")
db_engine.use_pool = False


async def _empty_tables(url: str) -> None:
    engine = await get_engine(url)
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            names = ", ".join(t.name for t in metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(metadata.sorted_tables):
                await conn.execute(delete(table))


def run_sql(database: str, statement):
    """Ejecuta una sentencia SQLAlchemy en la base del test y devuelve las filas (si hay)."""

    async def run():
        engine = await get_engine(database)
        async with engine.begin() as conn:
            result = await conn.execute(statement)
            return result.all() if result.returns_rows else None

    return asyncio.run(run())


@pytest.fixture
def database(tmp_path) -> str:
    """URL o ruta de la base del test. Los tests nunca tocan data/jobs.db."""
    if not TEST_DATABASE_URL:
        return str(tmp_path / "jobs.db")
    asyncio.run(_empty_tables(TEST_DATABASE_URL))
    return TEST_DATABASE_URL


@pytest.fixture
def client(database) -> TestClient:
    app.dependency_overrides[get_settings] = _override_settings
    store = JobStore(database)
    app.dependency_overrides[get_job_store] = lambda: store
    projects = ProjectStore(database)
    app.dependency_overrides[get_project_store] = lambda: projects
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
