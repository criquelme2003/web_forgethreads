from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_job_store, require_user
from app.services.job_store import JobNotFoundError, JobRecord, JobStore

router = APIRouter(tags=["jobs"])


# Campos del payload del notifier que no forman parte del resumen del job.
_PAYLOAD_FIELDS = {"job_id", "status", "logs", "authToken", "auth_token"}


class JobCallbackPayload(BaseModel):
    job_id: str
    status: Literal["success", "error", "partial"]
    effective_order: int | None = Field(default=None, alias="effective-order")
    computation_time_s: float | None = Field(default=None, alias="computation-time(s)")
    auth_token: str | None = Field(default=None, alias="authToken")
    logs: str | None = None

    # extra=allow: el resto del resumen (kind, orders, rows_per_order...) se guarda como `result`.
    model_config = {"populate_by_name": True, "extra": "allow"}

    def summary(self) -> dict:
        """Resumen del job tal como lo escribió el script del cluster, sin los campos del notifier."""
        data = self.model_dump(by_alias=True, exclude_none=True)
        return {k: v for k, v in data.items() if k not in _PAYLOAD_FIELDS}


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["pending", "success", "error", "partial"]
    kind: str = "maxmin"
    node: str
    created_at: datetime
    updated_at: datetime
    effective_order: int | None = None
    computation_time_s: float | None = None
    logs: str | None = None
    params: dict | None = None
    result: dict | None = None

    @classmethod
    def from_record(cls, r: JobRecord) -> "JobStatusResponse":
        return cls(
            job_id=r.job_id,
            status=r.status,
            kind=r.kind,
            node=r.node,
            created_at=r.created_at,
            updated_at=r.updated_at,
            effective_order=r.effective_order,
            computation_time_s=r.computation_time_s,
            logs=r.logs,
            params=r.params,
            result=r.result,
        )


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return auth_header[len("Bearer ") :]


@router.post("/app/job_callback")
async def job_callback(
    payload: JobCallbackPayload,
    request: Request,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> dict:
    token = _extract_bearer_token(request)
    if not await store.verify_token(payload.job_id, token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    try:
        await store.update_from_callback(
            payload.job_id,
            status=payload.status,
            effective_order=payload.effective_order,
            computation_time_s=payload.computation_time_s,
            logs=payload.logs,
            result=payload.summary() or None,
        )
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {payload.job_id}")

    return {"received": True}


@router.get("/app/jobs", response_model=list[JobStatusResponse])
async def list_jobs(
    user: Annotated[str, Depends(require_user)],
    store: Annotated[JobStore, Depends(get_job_store)],
    status: str | None = None,
) -> list[JobStatusResponse]:
    """Lista trabajos; ?status=pending filtra pendientes (para polling). SQLite persiste entre reinicios."""
    records = await store.list_all()
    return [JobStatusResponse.from_record(r) for r in records if not status or r.status == status]


@router.get("/app/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    user: Annotated[str, Depends(require_user)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> JobStatusResponse:
    try:
        record = await store.get(job_id)
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {job_id}")
    return JobStatusResponse.from_record(record)
