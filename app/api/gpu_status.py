from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import get_node_selector, get_ssh_pool, require_user
from app.services.node_selector import NoAvailableNodeError, NodeSelector

router = APIRouter(tags=["parameters"], prefix="/app")


class GpuStatusResponse(BaseModel):
    node_info: str
    output: str


class ClusterRow(BaseModel):
    name: str
    host: str
    state: str
    gpus_free: int
    gpus_total: int
    cpus_free: int
    cpus_total: int
    pending_jobs: int
    score: float
    reachable: bool
    idle_str: str | None = None
    utils_str: str | None = None


class ClusterStatusResponse(BaseModel):
    rows: list[ClusterRow]
    check_idle: bool
    gpu_idle_threshold: int


@router.get("/gpu_status", response_model=GpuStatusResponse)
async def gpu_status(
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    prefer_idle: bool = True,
    gpu_idle_threshold: int | None = None,
) -> GpuStatusResponse:
    """
    - Por defecto: intenta GPU idle (nvidia-smi util < threshold) vía `enrich_status_with_idle` `app/repositories/slurm.py:299`.
      Si hay nodo con GPU idle lo usa; si no, fallback automático a scoring normal `app/services/node_selector.py:51`.
      Ej: /app/gpu_status  (idle por defecto)  o  ?prefer_idle=false para forzar solo score, o ?prefer_idle=true&gpu_idle_threshold=10
    """
    from app.core.config import get_settings

    if gpu_idle_threshold is None:
        try:
            gpu_idle_threshold = get_settings().gpu_idle_threshold
        except Exception:
            gpu_idle_threshold = 5

    # Intenta primero con GPU requerida (prioridad), fallback a cualquier nodo
    try:
        node, result = await selector.run_on_best_node(
            "nvidia-smi", require_gpu=True, prefer_idle_gpu=prefer_idle, gpu_idle_threshold=gpu_idle_threshold
        )
    except NoAvailableNodeError:
        try:
            node, result = await selector.run_on_best_node(
                "nvidia-smi", require_gpu=False, prefer_idle_gpu=prefer_idle, gpu_idle_threshold=gpu_idle_threshold
            )
        except NoAvailableNodeError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"No hay nodos disponibles: {e}")

    output = str(result.stdout) if result and result.stdout else "No output"
    idle_tag = ""
    if prefer_idle and getattr(node, "has_idle_gpu", None) is not None:
        if node.has_idle_gpu:
            idle_tag = f" | GPU idle: sí (utils={node.gpu_utils})"
        else:
            idle_tag = " | GPU idle: no (fallback score)"
    node_info = f"- Nodo: {node.name} ({node.host}) | GPUs libres: {node.gpus_free}/{node.gpus_total} | CPUs libres: {node.cpus_free}/{node.cpus_total} | Estado: {node.state.value}{idle_tag}"
    return GpuStatusResponse(node_info=node_info, output=output)


@router.get("/cluster_status", response_model=ClusterStatusResponse)
async def cluster_status(
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    check_idle: bool = False,
    gpu_idle_threshold: int | None = None,
) -> ClusterStatusResponse:
    """
    Disponibilidad de los 3 nodos (SLURM + score), ordenados por score descendente.
    - Si check_idle=true: consulta nvidia-smi util en paralelo y agrega cols GPU idle.
      Ej: /app/cluster_status?check_idle=true
    """
    from app.core.config import get_settings

    if gpu_idle_threshold is None:
        try:
            gpu_idle_threshold = get_settings().gpu_idle_threshold
        except Exception:
            gpu_idle_threshold = 5

    if check_idle:
        idle_nodes = await selector.find_idle_gpu_nodes(gpu_idle_threshold=gpu_idle_threshold, force_refresh=True)
        idle_set = {n.name for n in idle_nodes}
        statuses = await selector.get_all_status(force_refresh=True)
        for s in statuses:
            if s.name in idle_set:
                s.has_idle_gpu = True
    else:
        statuses = await selector.get_all_status(force_refresh=True)

    rows: list[ClusterRow] = []
    for s in sorted(statuses, key=lambda x: selector.score(x), reverse=True):
        row = ClusterRow(
            name=s.name,
            host=s.host,
            state=s.state.value,
            gpus_free=s.gpus_free,
            gpus_total=s.gpus_total,
            cpus_free=s.cpus_free,
            cpus_total=s.cpus_total,
            pending_jobs=s.pending_jobs,
            score=selector.score(s),
            reachable=s.reachable,
        )
        if check_idle:
            row.idle_str = "sí" if s.has_idle_gpu else ("no" if s.has_idle_gpu is not None else "—")
            row.utils_str = str(s.gpu_utils) if s.gpu_utils is not None else "—"
        rows.append(row)

    return ClusterStatusResponse(rows=rows, check_idle=check_idle, gpu_idle_threshold=gpu_idle_threshold)


@router.get("/gpu_status/{node_name}", response_model=GpuStatusResponse)
async def gpu_status_by_node(
    node_name: str,
    user: Annotated[str, Depends(require_user)],
    request: Request,
) -> GpuStatusResponse:
    """Debug: fuerza consulta a un nodo concreto (sin selector)."""
    pool = get_ssh_pool(request)
    result = await pool.run_on_node(node_name, "nvidia-smi", timeout=10.0)
    if result is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Error: no se pudo conectar a {node_name}")
    return GpuStatusResponse(node_info=f"- Nodo: {node_name} (directo)", output=str(result.stdout))
