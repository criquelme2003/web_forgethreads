from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_job_store, get_project_store, require_user
from app.services.job_store import JobNotFoundError, JobRecord, JobStore
from app.services.maxmin_log import parse_maxmin_log
from app.services.project_store import InputAuthor, ProjectNotFoundError, ProjectStore
from app.services.user_store import User

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


class ExpertRef(BaseModel):
    """Experto al que pertenece el cálculo (RF-54)."""

    input_id: int
    email: str
    nombre: str | None
    input_version: int  # versión del input usada en el cálculo
    version_vigente: int  # versión actual del input (RF-53)


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
    project_id: int | None = None
    input_id: int | None = None
    experto: ExpertRef | None = None
    expertos: list[ExpertRef] | None = None  # expertos incluidos en un cálculo agregado (RF-25)

    @classmethod
    def from_record(
        cls,
        r: JobRecord,
        author: InputAuthor | None = None,
        authors: dict[int, InputAuthor] | None = None,
    ) -> "JobStatusResponse":
        """`author`: experto de un cálculo por experto; `authors`: los de un agregado, por input."""
        included = aggregate_inputs(r)
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
            project_id=r.project_id,
            input_id=r.input_id,
            experto=ExpertRef(
                input_id=author.input_id, email=author.email, nombre=author.nombre,
                input_version=r.input_version or 1, version_vigente=author.version,
            )
            if author
            else None,
            expertos=[
                ExpertRef(
                    input_id=a.input_id, email=a.email, nombre=a.nombre,
                    input_version=version, version_vigente=a.version,
                )
                for a, version in (((authors or {}).get(i), v) for i, v in included)
                if a is not None
            ]
            if included
            else None,
        )


def aggregate_inputs(record: JobRecord) -> list[tuple[int, int]]:
    """(input_id, versión) de cada experto incluido en un cálculo agregado; vacío si no lo es."""
    return [
        (int(i["input_id"]), int(i.get("version") or 1))
        for i in (record.params or {}).get("inputs") or []
        if isinstance(i, dict) and i.get("input_id") is not None
    ]


async def job_responses(records: list[JobRecord], projects: ProjectStore) -> list[JobStatusResponse]:
    """Respuestas con el experto de cada cálculo, o los de un agregado (RF-54)."""
    ids = [r.input_id for r in records if r.input_id is not None]
    ids += [i for r in records for i, _ in aggregate_inputs(r)]
    authors = await projects.input_authors(ids)
    return [JobStatusResponse.from_record(r, authors.get(r.input_id), authors) for r in records]


async def can_see_job(record: JobRecord, user: User, projects: ProjectStore) -> bool:
    """Superadmin: todo. Admin: cálculos de sus proyectos y los sueltos que lanzó él (RF-03)."""
    if user.is_superadmin:
        return True
    if record.project_id is None:
        return record.user_id == user.id
    try:
        project = await projects.get_project(record.project_id)
    except ProjectNotFoundError:
        return False
    return project.is_member(user.id)


async def get_visible_job(
    job_id: str, user: User, store: JobStore, projects: ProjectStore
) -> JobRecord:
    """El job si el usuario puede verlo; si no, 404 como si no existiera."""
    try:
        record = await store.get(job_id)
    except JobNotFoundError:
        record = None
    if record is None or not await can_see_job(record, user, projects):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {job_id}")
    return record


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
    user: Annotated[User, Depends(require_user)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    status: str | None = None,
) -> list[JobStatusResponse]:
    """Lista los trabajos visibles para el usuario; ?status=pending filtra pendientes (para polling)."""
    records = [r for r in await store.list_all() if not status or r.status == status]
    if not user.is_superadmin:
        mine = await projects.accessible_project_ids(user.id)
        records = [
            r for r in records
            if (r.project_id in mine if r.project_id is not None else r.user_id == user.id)
        ]
    return await job_responses(records, projects)


@router.get("/app/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    user: Annotated[User, Depends(require_user)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> JobStatusResponse:
    record = await get_visible_job(job_id, user, store, projects)
    return (await job_responses([record], projects))[0]


class EffectsByOrder(BaseModel):
    order: int
    count: int


class MaxMinResultResponse(BaseModel):
    job: JobStatusResponse
    effects_by_order: list[EffectsByOrder]
    convergence_step: int | None = None
    m: int | None = None
    b: int | None = None
    order_limit: int | None = None


@router.get("/app/maxmin/jobs/{job_id}", response_model=MaxMinResultResponse)
async def get_maxmin_result(
    job_id: str,
    user: Annotated[User, Depends(require_user)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> MaxMinResultResponse:
    """Job MaxMin con los efectos por orden leídos de su log (vacío mientras está pendiente)."""
    record = await get_visible_job(job_id, user, store, projects)
    if record.kind != "maxmin":
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"El job {job_id} no es un cálculo MaxMin")
    parsed = parse_maxmin_log(record.logs)
    return MaxMinResultResponse(
        job=JobStatusResponse.from_record(record),
        effects_by_order=[EffectsByOrder(order=o, count=c) for o, c in parsed.effects_by_order],
        convergence_step=parsed.convergence_step,
        m=parsed.m,
        b=parsed.b,
        order_limit=parsed.order_limit,
    )
