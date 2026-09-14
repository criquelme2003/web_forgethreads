import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
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
    effective_order: int | None = None
    computation_time_s: float | None = None
    logs: str | None = None


class JobStore:
    """Registro in-memory de jobs SLURM lanzados, sin persistencia entre reinicios."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, job_id: str, node: str) -> JobRecord:
        now = datetime.now(UTC)
        record = JobRecord(job_id=job_id, status="pending", node=node, created_at=now, updated_at=now)
        async with self._lock:
            self._jobs[job_id] = record
        return record

    async def mark_error(self, job_id: str, node: str, logs: str) -> JobRecord:
        """Registra un job que quedó encolado en SLURM pero cuyo notifier no se pudo lanzar."""
        now = datetime.now(UTC)
        record = JobRecord(
            job_id=job_id, status="error", node=node, created_at=now, updated_at=now, logs=logs
        )
        async with self._lock:
            self._jobs[job_id] = record
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
            record = self._jobs.get(job_id)
            if record is None:
                raise JobNotFoundError(f"Unknown job_id: {job_id}")
            record.status = status
            record.effective_order = effective_order
            record.computation_time_s = computation_time_s
            record.logs = logs
            record.updated_at = datetime.now(UTC)
            return record

    async def get(self, job_id: str) -> JobRecord:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobNotFoundError(f"Unknown job_id: {job_id}")
            return record


# Singleton a nivel de módulo: se importa directamente en vez de pasar por
# request.app.state/request.state, cuya propagación entre requests no es confiable en
# todos los entornos ASGI (uvicorn con `fastapi run`/`fastapi dev`). Python cachea el módulo
# tras el primer import, así que todos los importadores comparten esta misma instancia.
job_store = JobStore()
