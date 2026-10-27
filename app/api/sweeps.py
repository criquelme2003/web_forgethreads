"""Barridos de simulación (RF-31 a RF-37, RNF-02): vista previa, lanzamiento y resultados.

Un barrido es un solo job de SLURM (`sweep_job.sh`, kind `sweep`) dentro de un proyecto de
simulación. El CSV crudo se descarga del nodo la primera vez que se consultan los resultados y se
cachea en `sweep_results_dir`; las estadísticas por (`n`, `c`) se calculan en el backend.
"""

import logging
import re
from pathlib import Path
from typing import Annotated

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.deps import (
    get_job_store,
    get_node_selector,
    get_project_store,
    get_ssh_pool,
    require_user,
)
from app.api.job_callback import JobStatusResponse, get_visible_job
from app.api.launch import submit_with_failover
from app.api.projects import get_project_or_404
from app.core.config import Settings, get_settings
from app.services.fe_remote import download_sweep_results
from app.services.job_store import JobRecord, JobStore
from app.services.node_selector import NodeSelector
from app.services.project_store import Project, ProjectStore
from app.services.slurm_submit import build_sweep_job_command
from app.services.sweep import (
    SEED_BLOCK,
    SweepPlan,
    combination_problem,
    combination_stats,
    format_number,
    parse_raw_csv,
    plan_sweep,
    slurm_time,
    stats_csv,
)
from app.services.user_store import User
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sweeps"])

# Mismos rangos que un cálculo MaxMin individual (ExecuteMaxMinRequest).
MAX_N = 10_000
MAX_C = 10_000
MAX_VALUES = 200  # valores por lista: lo que cabe en un formulario
_JOB_ID_RE = re.compile(r"^\d+$")


def _unique(values: list) -> list:
    return list(dict.fromkeys(values))


class SweepRequest(BaseModel):
    ns: list[int] = Field(..., min_length=1, max_length=MAX_VALUES, description="Valores de N (nodos)")
    cs: list[float] = Field(..., min_length=1, max_length=MAX_VALUES, description="Valores de c (decimales)")
    reps: int = Field(..., ge=1, description="Repeticiones por combinación")
    thr: float = Field(..., ge=0, le=1, description="Threshold, fijo en todo el barrido")
    seed_base: int = Field(default=0, ge=0, description="Semilla base (0 = semillas de test2.py)")

    @field_validator("ns")
    @classmethod
    def _ns(cls, v: list[int]) -> list[int]:
        if any(n <= 0 or n > MAX_N for n in v):
            raise ValueError(f"Cada N debe estar entre 1 y {MAX_N}")
        return _unique(v)

    @field_validator("cs")
    @classmethod
    def _cs(cls, v: list[float]) -> list[float]:
        if any(not (0 < c <= MAX_C) for c in v):
            raise ValueError(f"Cada c debe ser mayor que 0 y a lo más {MAX_C}")
        return _unique(v)

    @model_validator(mode="after")
    def _seeds(self) -> "SweepRequest":
        # La semilla es base × 10⁹ + rep × 1000 + n: rep × 1000 + n no debe invadir el bloque
        # de la base siguiente (RF-34).
        if self.reps * 1000 + max(self.ns) >= SEED_BLOCK:
            raise ValueError("R × 1000 + max(N) debe ser menor que 10⁹")
        return self


class SweepLaunchRequest(SweepRequest):
    nodo: str | None = Field(default=None, description="Nodo objetivo; si se omite, se elige automáticamente")


class CombinationOut(BaseModel):
    n: int
    c: float


class SkippedOut(CombinationOut):
    reason: str


class SweepPreviewResponse(BaseModel):
    validas: list[CombinationOut]
    omitidas: list[SkippedOut]
    reps: int
    ejecuciones: int  # repeticiones en total
    estimado_s: float
    time_limit: str  # --time que se pedirá a SLURM
    time_limit_s: int
    max_s: int
    excede: bool  # el --time supera el máximo configurado (RNF-02)
    warnings: list[str]


class SweepLaunchResponse(BaseModel):
    job_id: str
    node: str
    preview: SweepPreviewResponse


class StatsOut(BaseModel):
    n: int
    c: float
    media: float
    desviacion_estandar: float | None
    error_estandar: float | None
    minimo: int
    maximo: int
    repeticiones: int


class FailureOut(BaseModel):
    n: int | None = None
    c: float | None = None
    rep: int | None = None
    error: str


class SweepResultResponse(BaseModel):
    job: JobStatusResponse
    estadisticas: list[StatsOut]  # vacío mientras no termina
    omitidas: list[SkippedOut]
    fallas: list[FailureOut]  # repeticiones que lanzaron una excepción en el nodo
    sin_datos: list[CombinationOut]  # combinaciones válidas sin ninguna repetición (barrido cortado)


def _plan(body: SweepRequest, settings: Settings) -> SweepPlan:
    return plan_sweep(
        body.ns, body.cs, body.reps,
        run_base_s=settings.sweep_run_base_s, run_s_per_kn3=settings.sweep_run_s_per_kn3,
        margin=settings.sweep_time_margin, overhead_s=settings.sweep_overhead_s,
    )


def _preview(plan: SweepPlan, settings: Settings) -> SweepPreviewResponse:
    max_s = int(settings.sweep_max_hours * 3600)
    return SweepPreviewResponse(
        validas=[CombinationOut(n=x.n, c=x.c) for x in plan.valid],
        omitidas=[SkippedOut(n=x.n, c=x.c, reason=x.reason) for x in plan.skipped],
        reps=plan.reps,
        ejecuciones=plan.total_runs,
        estimado_s=round(plan.estimated_s, 1),
        time_limit=slurm_time(plan.time_limit_s),
        time_limit_s=plan.time_limit_s,
        max_s=max_s,
        excede=plan.time_limit_s > max_s,
        warnings=plan.warnings,
    )


async def _simulation_project(project_id: int, projects: ProjectStore, user: User) -> Project:
    project = await get_project_or_404(project_id, projects, user)
    if project.tipo != "simulacion":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El proyecto no es de simulación")
    return project


@router.post("/app/projects/{project_id}/sweeps/preview", response_model=SweepPreviewResponse)
async def preview_sweep(
    project_id: int,
    body: SweepRequest,
    user: Annotated[User, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> SweepPreviewResponse:
    """Combinaciones válidas y descartadas, total de ejecuciones y duración estimada (RF-32, RNF-02)."""
    await _simulation_project(project_id, projects, user)
    return _preview(_plan(body, settings), settings)


@router.post("/app/projects/{project_id}/sweeps", response_model=SweepLaunchResponse)
async def launch_sweep(
    project_id: int,
    body: SweepLaunchRequest,
    user: Annotated[User, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> SweepLaunchResponse:
    """Lanza el barrido como un solo job (sweep_job.sh) con el notifier encadenado (RF-33)."""
    project = await _simulation_project(project_id, projects, user)
    if project.estado != "abierto":
        raise HTTPException(status.HTTP_409_CONFLICT, f"El proyecto está {project.estado}")
    plan = _plan(body, settings)
    preview = _preview(plan, settings)
    if not plan.valid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ninguna combinación de N y c es válida")
    if preview.excede:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"El barrido pediría {preview.time_limit} a SLURM y el máximo es "
            f"{slurm_time(preview.max_s)}: reduce las combinaciones o las repeticiones",
        )

    command = build_sweep_job_command(
        scripts_wf_dir=settings.scripts_wf_dir,
        time_limit=preview.time_limit,
        ns=body.ns,
        cs=[format_number(c) for c in body.cs],
        reps=body.reps,
        threshold=body.thr,
        seed_base=body.seed_base,
    )

    async def prepare(node: str) -> str:
        return command

    job_id, node = await submit_with_failover(
        nodo=body.nodo,
        pool=pool,
        selector=selector,
        store=store,
        settings=settings,
        prepare=prepare,
        script="sweep_job.sh",
        kind="sweep",
        params={
            "ns": body.ns,
            "cs": body.cs,
            "reps": body.reps,
            "thr": body.thr,
            "seed_base": body.seed_base,
            "time_limit": preview.time_limit,
            "estimado_s": preview.estimado_s,
            "combinaciones": len(plan.valid),
            "ejecuciones": plan.total_runs,
        },
        project_id=project.id,
        user_id=user.id,
    )
    return SweepLaunchResponse(job_id=job_id, node=node, preview=preview)


# --- resultados ------------------------------------------------------------------------------


async def _get_sweep(job_id: str, user: User, store: JobStore, projects: ProjectStore) -> JobRecord:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {job_id}")
    record = await get_visible_job(job_id, user, store, projects)
    if record.kind != "sweep":
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"El job {job_id} no es un barrido")
    return record


def _has_results(record: JobRecord) -> bool:
    # Un barrido cortado por tiempo conserva las combinaciones terminadas (RF-36).
    return record.status in ("success", "partial")


async def _ensure_raw_csv(record: JobRecord, settings: Settings, pool: SSHConnectionPool) -> Path:
    """CSV crudo del barrido, descargado del nodo la primera vez."""
    local = Path(settings.sweep_results_dir) / f"{record.job_id}.csv"
    if not local.is_file():
        try:
            await download_sweep_results(pool, record.node, settings.scripts_wf_dir, record.job_id, local)
        except (asyncssh.Error, OSError) as e:
            logger.warning("No se pudo descargar el CSV del barrido %s desde %s: %s", record.job_id, record.node, e)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"No se pudieron descargar los resultados del nodo {record.node}",
            )
    return local


def _failures(result: dict | None) -> list[FailureOut]:
    out = []
    for item in (result or {}).get("failures") or []:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                FailureOut(
                    n=item.get("n"), c=item.get("c"), rep=item.get("rep"), error=str(item.get("error", "")),
                )
            )
        except ValueError:
            out.append(FailureOut(error=str(item)))
    return out


def _skipped(params: dict) -> list[SkippedOut]:
    return [
        SkippedOut(n=n, c=c, reason=problem)
        for c in params.get("cs") or []
        for n in params.get("ns") or []
        if (problem := combination_problem(n, c))
    ]


@router.get("/app/sweeps/{job_id}", response_model=SweepResultResponse)
async def get_sweep(
    job_id: str,
    user: Annotated[User, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
) -> SweepResultResponse:
    """Estado del barrido y, si terminó (o quedó parcial), las estadísticas por (n, c) (RF-35)."""
    record = await _get_sweep(job_id, user, store, projects)
    params = record.params or {}
    stats = []
    if _has_results(record):
        local = await _ensure_raw_csv(record, settings, pool)
        stats = combination_stats(parse_raw_csv(local.read_text(encoding="utf-8")))
    fallas = _failures(record.result)
    sin_datos: list[CombinationOut] = []
    if _has_results(record):
        with_data = {(s.n, s.c) for s in stats}
        failed = {(f.n, f.c) for f in fallas}
        sin_datos = [
            CombinationOut(n=n, c=c)
            for c in params.get("cs") or []
            for n in params.get("ns") or []
            if combination_problem(n, c) is None and (n, c) not in with_data and (n, c) not in failed
        ]
    return SweepResultResponse(
        job=JobStatusResponse.from_record(record),
        estadisticas=[
            StatsOut(
                n=s.n, c=s.c, media=s.mean, desviacion_estandar=s.sd, error_estandar=s.sem,
                minimo=s.min, maximo=s.max, repeticiones=s.count,
            )
            for s in stats
        ],
        omitidas=_skipped(params),
        fallas=fallas,
        sin_datos=sin_datos,
    )


@router.get("/app/sweeps/{job_id}/crudo.csv", response_class=FileResponse)
async def download_raw_csv(
    job_id: str,
    user: Annotated[User, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
) -> FileResponse:
    """CSV crudo `c,n,repeticion,orden_efectivo`, tal como lo escribió sweep_job en el nodo (RF-37)."""
    record = await _get_sweep(job_id, user, store, projects)
    if not _has_results(record):
        raise HTTPException(status.HTTP_409_CONFLICT, f"El barrido {job_id} está en estado {record.status}")
    local = await _ensure_raw_csv(record, settings, pool)
    return FileResponse(local, media_type="text/csv", filename=f"barrido_{job_id}_crudo.csv")


@router.get("/app/sweeps/{job_id}/agregado.csv")
async def download_aggregate_csv(
    job_id: str,
    user: Annotated[User, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
) -> Response:
    """Estadísticas por (n, c) en CSV (RF-37)."""
    record = await _get_sweep(job_id, user, store, projects)
    if not _has_results(record):
        raise HTTPException(status.HTTP_409_CONFLICT, f"El barrido {job_id} está en estado {record.status}")
    local = await _ensure_raw_csv(record, settings, pool)
    content = stats_csv(combination_stats(parse_raw_csv(local.read_text(encoding="utf-8"))))
    return Response(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="barrido_{job_id}_agregado.csv"'},
    )
