"""Motores de base de datos por URL y aplicación de las migraciones de Alembic.

La URL sale de `DATABASE_URL`; sin ella se usa SQLite en `JOBS_DB_PATH` (desarrollo y tests).
Cada URL tiene un solo motor por proceso y las migraciones se aplican la primera vez que
se usa, así que los stores no necesitan un paso de inicialización aparte.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Los tests lo desactivan: cada uno corre en su propio event loop y una conexión de asyncpg no
# se puede reusar desde otro loop.
use_pool = True

_engines: dict[str, AsyncEngine] = {}
_migrated: set[str] = set()
_locks: dict[str, asyncio.Lock] = {}


def normalize_url(value: str) -> str:
    """Convierte una ruta o URL de base de datos en una URL con driver async.

    - Ruta sin esquema (`data/jobs.db`) → `sqlite+aiosqlite:///data/jobs.db`.
    - `postgres://` o `postgresql://` → `postgresql+asyncpg://`.
    """
    if "://" not in value:
        return f"sqlite+aiosqlite:///{value}"
    scheme, rest = value.split("://", 1)
    if scheme in ("postgres", "postgresql"):
        return f"postgresql+asyncpg://{rest}"
    if scheme == "sqlite":
        return f"sqlite+aiosqlite://{rest}"
    return value


def display_url(url: str) -> str:
    """URL sin contraseña, para mostrar o loguear."""
    return make_url(normalize_url(url)).render_as_string(hide_password=True)


def _create_engine(url: str) -> AsyncEngine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        # Sin pool: cada operación abre su conexión. Evita conexiones atadas a un event loop
        # anterior (tests con varios asyncio.run) y el costo de abrir SQLite es despreciable.
        return create_async_engine(url, poolclass=NullPool)
    if not use_pool:
        return create_async_engine(url, poolclass=NullPool)
    return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url:
        cfg.set_main_option("sqlalchemy.url", normalize_url(url).replace("%", "%%"))
    return cfg


def _upgrade(connection: Connection) -> None:
    cfg = alembic_config()
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def get_engine(url: str) -> AsyncEngine:
    """Motor de la URL, con el esquema ya migrado a la última versión."""
    url = normalize_url(url)
    engine = _engines.get(url)
    if engine is None:
        engine = _engines[url] = _create_engine(url)
    if url not in _migrated:
        lock = _locks.setdefault(url, asyncio.Lock())
        async with lock:
            if url not in _migrated:
                async with engine.begin() as conn:
                    await conn.run_sync(_upgrade)
                _migrated.add(url)
    return engine


async def dispose_engines() -> None:
    """Cierra las conexiones abiertas (al apagar la app)."""
    for engine in list(_engines.values()):
        await engine.dispose()
    _engines.clear()
    _migrated.clear()
    _locks.clear()
