"""Entorno de Alembic.

La app aplica las migraciones sola al conectarse (`app/db/engine.py`), pasando su conexión en
`config.attributes["connection"]`. Desde la consola (`uv run alembic upgrade head`) se conecta a
la URL de la configuración (`DATABASE_URL` o `JOBS_DB_PATH`).
"""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.schema import metadata

config = context.config
target_metadata = metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite no soporta la mayoría de los ALTER TABLE: Alembic recrea la tabla.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def _console_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.core.config import get_settings
    from app.db.engine import normalize_url

    return normalize_url(get_settings().effective_database_url)


async def _run_async() -> None:
    engine = create_async_engine(_console_url(), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
elif context.is_offline_mode():
    context.configure(url=_console_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
