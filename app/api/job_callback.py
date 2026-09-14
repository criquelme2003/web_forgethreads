from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_job_store, require_user
from app.core.config import Settings, get_settings
from app.services.job_store import JobNotFoundError, JobStore
from app.services.session_token import TokenInvalidError, verify_auth_token

router = APIRouter(tags=["jobs"])


class JobCallbackPayload(BaseModel):
    job_id: str
    status: Literal["success", "error"]
    effective_order: int | None = Field(default=None, alias="effective-order")
    computation_time_s: float | None = Field(default=None, alias="computation-time(s)")
    auth_token: str | None = Field(default=None, alias="authToken")
    logs: str | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["pending", "success", "error"]
    node: str
    created_at: datetime
    updated_at: datetime
    effective_order: int | None = None
    computation_time_s: float | None = None
    logs: str | None = None


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return auth_header[len("Bearer ") :]


@router.post("/app/job_callback")
async def job_callback(
    payload: JobCallbackPayload,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> dict:
    token = _extract_bearer_token(request)
    try:
        verify_auth_token(
            token,
            settings.auth_password,
            settings.session_secret_key,
            max_age=settings.session_max_age_seconds,
        )
    except TokenInvalidError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    try:
        await store.update_from_callback(
            payload.job_id,
            status=payload.status,
            effective_order=payload.effective_order,
            computation_time_s=payload.computation_time_s,
            logs=payload.logs,
        )
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {payload.job_id}")

    return {"received": True}


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
    return JobStatusResponse(
        job_id=record.job_id,
        status=record.status,
        node=record.node,
        created_at=record.created_at,
        updated_at=record.updated_at,
        effective_order=record.effective_order,
        computation_time_s=record.computation_time_s,
        logs=record.logs,
    )
