import asyncio
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

JobStatus = Literal["pending", "success", "error"]


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
            conn.commit()
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
        # crea tabla si no existe (idempotente)
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
        )

    async def create(self, job_id: str, node: str) -> JobRecord:
        """Crea el registro del job con un token propio (usado como --auth-token del notifier)."""
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        record = JobRecord(job_id=job_id, status="pending", node=node, created_at=now, updated_at=now, token=token)
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO jobs (job_id, status, node, token, effective_order, computation_time_s, logs, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        record.job_id,
                        record.status,
                        record.node,
                        record.token,
                        record.effective_order,
                        record.computation_time_s,
                        record.logs,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
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
                cur = conn.execute("SELECT token, created_at FROM jobs WHERE job_id=?", (job_id,))
                existing = cur.fetchone()
                if existing:
                    record.token = existing["token"]
                    record.created_at = _parse_dt(existing["created_at"])
                conn.execute(
                    "INSERT OR REPLACE INTO jobs (job_id, status, node, token, effective_order, computation_time_s, logs, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        record.job_id,
                        record.status,
                        record.node,
                        record.token,
                        record.effective_order,
                        record.computation_time_s,
                        record.logs,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
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
                    "UPDATE jobs SET status=?, effective_order=?, computation_time_s=?, logs=?, updated_at=? WHERE job_id=?",
                    (status, effective_order, computation_time_s, logs, now.isoformat(), job_id),
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
