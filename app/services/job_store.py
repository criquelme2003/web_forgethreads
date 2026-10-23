import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.engine import get_engine
from app.db.schema import jobs

JobStatus = Literal["pending", "success", "error", "partial"]
JobKind = Literal["maxmin", "fe"]


class JobNotFoundError(Exception):
    pass


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus
    node: str
    created_at: datetime
    updated_at: datetime
    token: str = field(repr=False, default="")
    effective_order: int | None = None
    computation_time_s: float | None = None
    logs: str | None = None
    kind: JobKind = "maxmin"
    params: dict | None = None  # parámetros con que se lanzó el job
    result: dict | None = None  # resumen que reporta el notifier (ej. orders/rows_per_order de fe_job)
    project_id: int | None = None  # proyecto al que pertenece el cálculo (None: jobs sueltos)
    input_id: int | None = None  # input de experto que originó el cálculo (proyectos de expertos)
    input_version: int | None = None  # versión del input usada (RF-53)
    user_id: int | None = None  # admin que lanzó el cálculo


def resolve_database(database: str | None) -> str:
    """URL o ruta de la base: la indicada o la de la configuración (`DATABASE_URL`/`JOBS_DB_PATH`)."""
    if database is not None:
        return database
    try:
        from app.core.config import get_settings

        return get_settings().effective_database_url
    except Exception:  # noqa: BLE001  (sin .env: SQLite por defecto)
        return "data/jobs.db"


class JobStore:
    """Registro persistente de los jobs SLURM (SQLite o PostgreSQL, según la configuración)."""

    def __init__(self, database: str | None = None) -> None:
        # URL de SQLAlchemy o ruta de un archivo SQLite; si es None, se resuelve desde Settings.
        self._database = database

    async def _engine(self) -> AsyncEngine:
        return await get_engine(resolve_database(self._database))

    @staticmethod
    def _row_to_record(row: Row) -> JobRecord:
        m = row._mapping
        return JobRecord(
            job_id=m["job_id"],
            status=m["status"],
            node=m["node"],
            token=m["token"],
            effective_order=m["effective_order"],
            computation_time_s=m["computation_time_s"],
            logs=m["logs"],
            created_at=m["created_at"],
            updated_at=m["updated_at"],
            kind=m["kind"],
            params=m["params"],
            result=m["result"],
            project_id=m["project_id"],
            input_id=m["input_id"],
            input_version=m["input_version"],
            user_id=m["user_id"],
        )

    @staticmethod
    def _record_values(record: JobRecord) -> dict:
        return {
            "job_id": record.job_id,
            "status": record.status,
            "node": record.node,
            "token": record.token,
            "effective_order": record.effective_order,
            "computation_time_s": record.computation_time_s,
            "logs": record.logs,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "kind": record.kind,
            "params": record.params,
            "result": record.result,
            "project_id": record.project_id,
            "input_id": record.input_id,
            "input_version": record.input_version,
            "user_id": record.user_id,
        }

    async def _replace(self, record: JobRecord) -> None:
        """Inserta el registro reemplazando el que tenga el mismo job_id."""
        engine = await self._engine()
        async with engine.begin() as conn:
            await conn.execute(delete(jobs).where(jobs.c.job_id == record.job_id))
            await conn.execute(insert(jobs).values(**self._record_values(record)))

    async def create(
        self,
        job_id: str,
        node: str,
        kind: JobKind = "maxmin",
        params: dict | None = None,
        project_id: int | None = None,
        input_id: int | None = None,
        input_version: int | None = None,
        user_id: int | None = None,
    ) -> JobRecord:
        """Crea el registro del job con un token propio (usado como --auth-token del notifier)."""
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        record = JobRecord(
            job_id=job_id, status="pending", node=node, created_at=now, updated_at=now, token=token,
            kind=kind, params=params, project_id=project_id, input_id=input_id,
            input_version=input_version, user_id=user_id,
        )
        await self._replace(record)
        return record

    async def verify_token(self, job_id: str, token: str) -> bool:
        engine = await self._engine()
        async with engine.connect() as conn:
            stored = await conn.scalar(select(jobs.c.token).where(jobs.c.job_id == job_id))
        if stored is None:
            return False
        return secrets.compare_digest(stored, token or "")

    async def mark_error(self, job_id: str, node: str, logs: str) -> JobRecord:
        """Registra un job que quedó encolado en SLURM pero cuyo notifier no se pudo lanzar."""
        now = datetime.now(UTC)
        record = JobRecord(
            job_id=job_id, status="error", node=node, created_at=now, updated_at=now, logs=logs, token=""
        )
        engine = await self._engine()
        async with engine.begin() as conn:
            # si ya existe, conserva token, created_at y el origen del cálculo
            existing = (
                await conn.execute(select(jobs).where(jobs.c.job_id == job_id))
            ).first()
            if existing:
                old = self._row_to_record(existing)
                record.token = old.token
                record.created_at = old.created_at
                record.kind = old.kind
                record.params = old.params
                record.project_id = old.project_id
                record.input_id = old.input_id
                record.input_version = old.input_version
                record.user_id = old.user_id
            await conn.execute(delete(jobs).where(jobs.c.job_id == job_id))
            await conn.execute(insert(jobs).values(**self._record_values(record)))
        return record

    async def update_from_callback(
        self,
        job_id: str,
        status: JobStatus,
        effective_order: int | None = None,
        computation_time_s: float | None = None,
        logs: str | None = None,
        result: dict | None = None,
    ) -> JobRecord:
        engine = await self._engine()
        async with engine.begin() as conn:
            updated = await conn.execute(
                update(jobs)
                .where(jobs.c.job_id == job_id)
                .values(
                    status=status,
                    effective_order=effective_order,
                    computation_time_s=computation_time_s,
                    logs=logs,
                    result=result,
                    updated_at=datetime.now(UTC),
                )
            )
            if updated.rowcount == 0:
                raise JobNotFoundError(f"Unknown job_id: {job_id}")
            row = (await conn.execute(select(jobs).where(jobs.c.job_id == job_id))).one()
        return self._row_to_record(row)

    async def get(self, job_id: str) -> JobRecord:
        engine = await self._engine()
        async with engine.connect() as conn:
            row = (await conn.execute(select(jobs).where(jobs.c.job_id == job_id))).first()
        if row is None:
            raise JobNotFoundError(f"Unknown job_id: {job_id}")
        return self._row_to_record(row)

    async def list_all(self) -> list[JobRecord]:
        engine = await self._engine()
        async with engine.connect() as conn:
            rows = await conn.execute(select(jobs).order_by(jobs.c.created_at.desc()))
            return [self._row_to_record(r) for r in rows]

    async def list_by_project(self, project_id: int) -> list[JobRecord]:
        engine = await self._engine()
        async with engine.connect() as conn:
            rows = await conn.execute(
                select(jobs)
                .where(jobs.c.project_id == project_id)
                .order_by(jobs.c.created_at.desc())
            )
            return [self._row_to_record(r) for r in rows]

    # compat con código antiguo que usaba list()
    async def list(self) -> list[JobRecord]:
        return await self.list_all()

    async def clear(self) -> None:
        engine = await self._engine()
        async with engine.begin() as conn:
            await conn.execute(delete(jobs))


# Singleton a nivel de módulo: se importa directamente en vez de pasar por
# request.app.state/request.state, cuya propagación entre requests no es confiable en
# todos los entornos ASGI (uvicorn con `fastapi run`/`fastapi dev`). Python cachea el módulo
# tras el primer import, así que todos los importadores comparten esta misma instancia.
job_store = JobStore()
