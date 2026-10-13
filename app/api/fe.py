"""Cálculo de caminos (efectos olvidados) con forgeffects sobre las matrices CC, CE y EE de un experto."""

import csv
import logging
import re
import uuid
from pathlib import Path
from typing import Annotated

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_job_store, get_node_selector, get_ssh_pool, require_user
from app.api.launch import launch_with_notifier, resolve_node
from app.core.config import Settings, get_settings
from app.services.fe_input import FeInput, load_fe_input
from app.services.fe_remote import build_fe_input_files, download_fe_results, upload_fe_input
from app.services.job_store import JobNotFoundError, JobRecord, JobStore
from app.services.node_selector import NodeSelector
from app.services.slurm_submit import build_fe_job_command
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["fe"])

MAX_CSV_CHARS = 1_000_000
# Con un solo experto el bootstrap no aplica: Count y SD no aportan información (RF-21, RF-22).
SINGLE_EXPERT_REPS = 1
SINGLE_EXPERT_HIDDEN_COLUMNS = ("Count", "SD")
_JOB_ID_RE = re.compile(r"^\d+$")


class FeMatricesRequest(BaseModel):
    cc: str = Field(..., max_length=MAX_CSV_CHARS, description="CSV de CC (m×m) con etiquetas")
    ce: str = Field(..., max_length=MAX_CSV_CHARS, description="CSV de CE (m×n) con etiquetas")
    ee: str = Field(..., max_length=MAX_CSV_CHARS, description="CSV de EE (n×n) con etiquetas")


class FeExecuteRequest(FeMatricesRequest):
    thr: float = Field(..., ge=0, le=1, description="Threshold de forgeffects")
    maxorder: int = Field(..., ge=2, le=10, description="Orden máximo de los caminos")
    nodo: str | None = Field(default=None, description="Nodo objetivo; si se omite, se elige automáticamente")


class CellErrorOut(BaseModel):
    matrix: str
    message: str
    row: int | None = None
    col: int | None = None


class MatrixPreview(BaseModel):
    corner: str
    row_labels: list[str]
    col_labels: list[str]
    cells: list[list[str]]


class FeValidationResponse(BaseModel):
    valid: bool
    causes: list[str]
    effects: list[str]
    errors: list[CellErrorOut]
    warnings: list[str]
    previews: dict[str, MatrixPreview]


class FeExecuteResponse(BaseModel):
    job_id: str
    status: str = "pending"
    node: str
    warnings: list[str] = []


class PathsTable(BaseModel):
    order: int
    columns: list[str]
    rows: list[list[str | float | int]]


class FePathsResponse(BaseModel):
    job_id: str
    k: int | None
    causes: list[str]
    effects: list[str]
    tables: list[PathsTable]


def _validation_response(data: FeInput) -> FeValidationResponse:
    return FeValidationResponse(
        valid=data.valid,
        causes=data.causes,
        effects=data.effects,
        errors=[CellErrorOut(**vars(e)) for e in data.errors],
        warnings=data.warnings,
        previews={
            name: MatrixPreview(
                corner=p.corner, row_labels=p.row_labels, col_labels=p.col_labels, cells=p.cells
            )
            for name, p in data.previews.items()
        },
    )


@router.post("/app/fe/validate", response_model=FeValidationResponse)
async def validate_fe_input(
    body: FeMatricesRequest,
    user: Annotated[str, Depends(require_user)],
) -> FeValidationResponse:
    """Vista previa: parsea las tres matrices y devuelve los errores por celda sin lanzar nada."""
    return _validation_response(load_fe_input(body.cc, body.ce, body.ee))


@router.post("/app/fe/execute", response_model=FeExecuteResponse)
async def execute_fe(
    body: FeExecuteRequest,
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> FeExecuteResponse:
    """Valida las matrices, las sube al nodo por SFTP y lanza fe_job.sh con el notifier encadenado."""
    data = load_fe_input(body.cc, body.ce, body.ee)
    if not data.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_validation_response(data).model_dump(),
        )

    node = await resolve_node(body.nodo, pool, selector)
    request_id = uuid.uuid4().hex
    meta = {
        "causes": data.causes,
        "effects": data.effects,
        "thr": body.thr,
        "maxorder": body.maxorder,
        "reps": SINGLE_EXPERT_REPS,
    }
    files = build_fe_input_files(data.cc, data.ce, data.ee, meta)
    try:
        input_dir = await upload_fe_input(pool, node, settings.scripts_wf_dir, request_id, files)
    except (asyncssh.Error, OSError) as e:
        logger.warning("No se pudo subir el input de fe_job a %s: %s", node, e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No se pudo subir el input al nodo {node}")

    job_id = await launch_with_notifier(
        pool=pool,
        store=store,
        settings=settings,
        node=node,
        command=build_fe_job_command(settings.scripts_wf_dir, input_dir),
        script="fe_job.sh",
        kind="fe",
        params={**meta, "request_id": request_id},
    )
    return FeExecuteResponse(job_id=job_id, node=node, warnings=data.warnings)


async def _get_fe_record(job_id: str, store: JobStore) -> JobRecord:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {job_id}")
    try:
        record = await store.get(job_id)
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job_id: {job_id}")
    if record.kind != "fe":
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"El job {job_id} no es un cálculo de caminos")
    if record.status != "success":
        raise HTTPException(status.HTTP_409_CONFLICT, f"El job {job_id} está en estado {record.status}")
    return record


def _result_orders(record: JobRecord) -> list[int]:
    orders = (record.result or {}).get("orders") or []
    return [int(o) for o in orders]


async def _ensure_local_results(
    record: JobRecord, settings: Settings, pool: SSHConnectionPool
) -> Path:
    """Devuelve el directorio local con los CSV del job, descargándolos del nodo si falta alguno."""
    local_dir = Path(settings.fe_results_dir) / record.job_id
    orders = _result_orders(record)
    missing = [o for o in orders if not (local_dir / f"paths_order_{o}.csv").is_file()]
    if missing:
        try:
            await download_fe_results(
                pool, record.node, settings.scripts_wf_dir, record.job_id, missing, local_dir
            )
        except (asyncssh.Error, OSError, ValueError) as e:
            logger.warning("No se pudieron descargar los caminos de %s desde %s: %s", record.job_id, record.node, e)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"No se pudieron descargar los resultados del nodo {record.node}",
            )
    return local_dir


_NUMERIC_COLUMNS = {"Count", "Mean", "SD"}


def _cell(column: str, value: str) -> str | float | int:
    """Las etiquetas (From, Through…, To) quedan como texto aunque parezcan números."""
    if column not in _NUMERIC_COLUMNS:
        return value
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def _read_table(path: Path, order: int, hidden: tuple[str, ...]) -> PathsTable:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        keep = [i for i, col in enumerate(header) if col not in hidden]
        rows = [[_cell(header[i], row[i]) for i in keep] for row in reader if row]
    return PathsTable(order=order, columns=[header[i] for i in keep], rows=rows)


@router.get("/app/fe/jobs/{job_id}/paths", response_model=FePathsResponse)
async def get_fe_paths(
    job_id: str,
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
) -> FePathsResponse:
    """Tablas de caminos por orden (From, Through…, To, Mean). Se descargan del nodo la primera vez."""
    record = await _get_fe_record(job_id, store)
    local_dir = await _ensure_local_results(record, settings, pool)
    k = (record.result or {}).get("k")
    hidden = SINGLE_EXPERT_HIDDEN_COLUMNS if k == 1 else ()
    params = record.params or {}
    return FePathsResponse(
        job_id=record.job_id,
        k=k,
        causes=params.get("causes", []),
        effects=params.get("effects", []),
        tables=[
            _read_table(local_dir / f"paths_order_{o}.csv", o, hidden) for o in _result_orders(record)
        ],
    )


@router.get("/app/fe/jobs/{job_id}/paths_order_{order}.csv", response_class=FileResponse)
async def download_fe_paths_csv(
    job_id: str,
    order: int,
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
) -> FileResponse:
    """CSV crudo de un orden, tal como lo escribió fe_job en el nodo."""
    record = await _get_fe_record(job_id, store)
    if order not in _result_orders(record):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"El job {job_id} no tiene caminos de orden {order}")
    local_dir = await _ensure_local_results(record, settings, pool)
    filename = f"paths_order_{order}.csv"
    return FileResponse(
        local_dir / filename, media_type="text/csv", filename=f"fe_{job_id}_{filename}"
    )
