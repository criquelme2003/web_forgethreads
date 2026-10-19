"""Copia los datos de una base SQLite de la app a otra base (normalmente PostgreSQL).

Uso (la base destino sale de DATABASE_URL, o se indica con --to):

    python -m app.db.copy_sqlite data/jobs.db
    python -m app.db.copy_sqlite respaldo.db --to postgresql://usuario:clave@host:5432/base

El archivo SQLite no se modifica: se trabaja sobre una copia temporal, que se lleva al esquema
vigente con las mismas migraciones que usa la app. La base destino debe estar vacía; si ya tiene
datos, la copia se cancela sin escribir nada.
"""

import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from sqlalchemy import func, insert, select, text

from app.db.engine import display_url, dispose_engines, get_engine, normalize_url
from app.db.schema import metadata

# Tablas con id autoincremental: en PostgreSQL hay que adelantar su secuencia tras copiar ids.
_SERIAL_TABLES = ("users", "projects", "expert_invitations", "expert_inputs", "expert_input_versions")


class CopyError(Exception):
    pass


async def copy_database(source: Path, target: str) -> dict[str, int]:
    """Copia todas las tablas de `source` a `target`. Devuelve las filas copiadas por tabla."""
    if not source.is_file():
        raise CopyError(f"No existe el archivo {source}")
    if normalize_url(target) == normalize_url(str(source)):
        raise CopyError("La base de origen y la de destino son la misma")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "origen.db"
        shutil.copy2(source, work)
        source_engine = await get_engine(str(work))
        target_engine = await get_engine(target)
        tables = metadata.sorted_tables  # padres antes que hijos (claves foráneas)

        async with target_engine.connect() as conn:
            for table in tables:
                if await conn.scalar(select(func.count()).select_from(table)):
                    raise CopyError(
                        f"La base destino ya tiene datos en '{table.name}'; la copia necesita una "
                        "base vacía"
                    )

        copied: dict[str, int] = {}
        async with source_engine.connect() as src, target_engine.begin() as dst:
            for table in tables:
                rows = [dict(r._mapping) for r in await src.execute(select(table))]
                if rows:
                    await dst.execute(insert(table), rows)
                copied[table.name] = len(rows)
            if dst.dialect.name == "postgresql":
                for name in _SERIAL_TABLES:
                    await dst.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                            f"(SELECT COALESCE(MAX(id), 0) + 1 FROM {name}), false)"
                        )
                    )
        await dispose_engines()
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copia una base SQLite de la app a otra base.")
    parser.add_argument("origen", type=Path, help="archivo SQLite de origen (ej. data/jobs.db)")
    parser.add_argument("--to", dest="destino", help="URL de la base destino (por defecto, DATABASE_URL)")
    args = parser.parse_args(argv)

    target = args.destino
    if not target:
        from app.core.config import get_settings

        target = get_settings().database_url
        if not target:
            parser.error("falta la base destino: define DATABASE_URL o usa --to")

    print(f"Copiando {args.origen} → {display_url(target)}")
    try:
        copied = asyncio.run(copy_database(args.origen, target))
    except CopyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    for name, n in copied.items():
        print(f"  {name}: {n} filas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
