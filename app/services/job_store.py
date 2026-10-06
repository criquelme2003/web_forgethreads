import asyncio
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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


_COLUMNS = (
    "job_id, status, node, token, effective_order, computation_time_s, logs, "
    "created_at, updated_at, kind, params, result"
)
# Columnas agregadas después de la versión inicial de la tabla: se crean si faltan.
_ADDED_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'maxmin'",
    "params": "TEXT",
    "result": "TEXT",
}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Crea la tabla si no existe y agrega las columnas nuevas a bases antiguas (idempotente)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            node TEXT NOT NULL,
            token TEXT NOT NULL,
            effective_order INTEGER,
            computation_time_s REAL,
            logs TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, ddl in _ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")
    conn.commit()


def _dump(value: dict | None) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _load(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _parse_dt(s: str) -> datetime:
    try:
        # ISO8601 con Z o sin
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return datetime.now(UTC)


class JobStore:
    """Registro persistente en sqlite de jobs SLURM. Reemplaza dict in-memory para sobrevivir reinicios."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path  # si None, se resuelve lazy desde Settings
        self._lock = asyncio.Lock()
        self._init_done = False

    def _resolve_db_path(self) -> str:
        if self._db_path is not None:
            return self._db_path
        try:
            from app.core.config import get_settings

            p = get_settings().jobs_db_path
            return p or "data/jobs.db"
        except Exception:
            return "data/jobs.db"

    def _ensure_db(self) -> None:
        db_path = self._resolve_db_path()
        # :memory: no crea directorio
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # usa URI para :memory: compartido entre conexiones si se requiere
        uri_path = db_path
        if db_path == ":memory:":
            # file memdb con shared cache para que múltiples conexiones vean misma tabla
            uri_path = "file:memdb1?mode=memory&cache=shared"
            conn = sqlite3.connect(uri_path, uri=True, check_same_thread=False)
        else:
            conn = sqlite3.connect(uri_path, check_same_thread=False)
        try:
            _ensure_schema(conn)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        db_path = self._resolve_db_path()
        # asegura tabla en cada conexión (necesario para :memory: y nuevos archivos)
        if db_path == ":memory:":
            uri_path = "file:memdb1?mode=memory&cache=shared"
            conn = sqlite3.connect(uri_path, uri=True, check_same_thread=False)
        else:
            # asegura directorio
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        return conn

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],  # type: ignore
            node=row["node"],
            token=row["token"],
            effective_order=row["effective_order"],
            computation_time_s=row["computation_time_s"],
            logs=row["logs"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            kind=row["kind"],  # type: ignore
            params=_load(row["params"]),
            result=_load(row["result"]),
        )

    @staticmethod
    def _record_values(record: JobRecord) -> tuple:
        return (
            record.job_id,
            record.status,
            record.node,
            record.token,
            record.effective_order,
            record.computation_time_s,
            record.logs,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
            record.kind,
            _dump(record.params),
            _dump(record.result),
        )

    async def create(
        self, job_id: str, node: str, kind: JobKind = "maxmin", params: dict | None = None
    ) -> JobRecord:
        """Crea el registro del job con un token propio (usado como --auth-token del notifier)."""
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        record = JobRecord(
            job_id=job_id, status="pending", node=node, created_at=now, updated_at=now, token=token,
            kind=kind, params=params,
        )
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO jobs ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    self._record_values(record),
                )
                conn.commit()
            finally:
                conn.close()
        return record

    async def verify_token(self, job_id: str, token: str) -> bool:
        async with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT token FROM jobs WHERE job_id=?", (job_id,))
                row = cur.fetchone()
            finally:
                conn.close()
        if row is None:
            return False
        return secrets.compare_digest(row["token"], token or "")

    async def mark_error(self, job_id: str, node: str, logs: str) -> JobRecord:
        """Registra un job que quedó encolado en SLURM pero cuyo notifier no se pudo lanzar."""
        now = datetime.now(UTC)
        record = JobRecord(
            job_id=job_id, status="error", node=node, created_at=now, updated_at=now, logs=logs, token=""
        )
        # si ya existe, conserva token y created_at
        async with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT token, created_at, kind, params FROM jobs WHERE job_id=?", (job_id,)
                )
                existing = cur.fetchone()
                if existing:
                    record.token = existing["token"]
                    record.created_at = _parse_dt(existing["created_at"])
                    record.kind = existing["kind"]
                    record.params = _load(existing["params"])
                conn.execute(
                    f"INSERT OR REPLACE INTO jobs ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    self._record_values(record),
                )
                conn.commit()
            finally:
                conn.close()
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
        async with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,))
                row = cur.fetchone()
                if row is None:
                    raise JobNotFoundError(f"Unknown job_id: {job_id}")
                # actualiza
                now = datetime.now(UTC)
                conn.execute(
                    "UPDATE jobs SET status=?, effective_order=?, computation_time_s=?, logs=?, "
                    "result=?, updated_at=? WHERE job_id=?",
                    (
                        status, effective_order, computation_time_s, logs, _dump(result),
                        now.isoformat(), job_id,
                    ),
                )
                conn.commit()
                cur = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,))
                row = cur.fetchone()
                return self._row_to_record(row)
            finally:
                conn.close()

    async def get(self, job_id: str) -> JobRecord:
        async with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,))
                row = cur.fetchone()
                if row is None:
                    raise JobNotFoundError(f"Unknown job_id: {job_id}")
                return self._row_to_record(row)
            finally:
                conn.close()

    async def list_all(self) -> list[JobRecord]:
        async with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC")
                rows = cur.fetchall()
                return [self._row_to_record(r) for r in rows]
            finally:
                conn.close()

    # compat con código antiguo que usaba list()
    async def list(self) -> list[JobRecord]:
        return await self.list_all()

    async def clear(self) -> None:
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM jobs")
                conn.commit()
            finally:
                conn.close()

    # sync helper para tests que no quieren async
    def clear_sync(self) -> None:
        self._ensure_db()
        db_path = self._resolve_db_path()
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            conn.execute("DELETE FROM jobs")
            conn.commit()
        finally:
            conn.close()


# Singleton a nivel de módulo: se importa directamente en vez de pasar por
# request.app.state/request.state, cuya propagación entre requests no es confiable en
# todos los entornos ASGI (uvicorn con `fastapi run`/`fastapi dev`). Python cachea el módulo
# tras el primer import, así que todos los importadores comparten esta misma instancia.
job_store = JobStore()
