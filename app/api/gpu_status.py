from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import get_node_selector, require_user
from app.services.node_selector import NodeSelector

router = APIRouter(tags=["parameters"], prefix="/front")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _render_simple(request: Request, output: str, node_info: str = "", status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "simple_output.html",
        {"title": "NVIDIA-SMI Result", "heading": "NVIDIA-SMI Result", "node_info": node_info, "output": output},
        status_code=status_code,
    )


@router.get("/gpu_status", response_class=HTMLResponse)
async def parameters_form(
    user: Annotated[str, Depends(require_user)],
    req: Request,
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    prefer_idle: bool = True,
    gpu_idle_threshold: int | None = None,
) -> HTMLResponse:
    """
    - Por defecto: intenta GPU idle (nvidia-smi util < threshold) vía `enrich_status_with_idle` `app/repositories/slurm.py:299`.
      Si hay nodo con GPU idle lo usa; si no, fallback automático a scoring normal `app/services/node_selector.py:51`.
      Ej: /front/gpu_status  (idle por defecto)  o  ?prefer_idle=false para forzar solo score, o ?prefer_idle=true&gpu_idle_threshold=10
    """
    from app.core.config import get_settings
    from app.services.node_selector import NoAvailableNodeError

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
            return _render_simple(req, f"No hay nodos disponibles: {e}", "- Error", status_code=503)

    output = str(result.stdout) if result and result.stdout else "No output"
    idle_tag = ""
    if prefer_idle and getattr(node, "has_idle_gpu", None) is not None:
        if node.has_idle_gpu:
            idle_tag = f" | GPU idle: sí (utils={node.gpu_utils})"
        else:
            idle_tag = " | GPU idle: no (fallback score)"
    node_info = f"- Nodo: {node.name} ({node.host}) | GPUs libres: {node.gpus_free}/{node.gpus_total} | CPUs libres: {node.cpus_free}/{node.cpus_total} | Estado: {node.state.value}{idle_tag}"
    return _render_simple(req, output, node_info)


@router.get("/cluster_status", response_class=HTMLResponse)
async def cluster_status(
    req: Request,
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    check_idle: bool = False,
    gpu_idle_threshold: int | None = None,
) -> HTMLResponse:
    """
    Vista de disponibilidad de los 3 nodos (SLURM + score).
    - Si check_idle=true: consulta nvidia-smi util en paralelo y muestra col GPU idle.
      Ej: /front/cluster_status?check_idle=true
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

    rows = []
    for s in sorted(statuses, key=lambda x: selector.score(x), reverse=True):
        row = {
            "name": s.name,
            "host": s.host,
            "state": s.state.value,
            "gpus_free": s.gpus_free,
            "gpus_total": s.gpus_total,
            "cpus_free": s.cpus_free,
            "cpus_total": s.cpus_total,
            "pending_jobs": s.pending_jobs,
            "score": selector.score(s),
            "reachable": s.reachable,
        }
        if check_idle:
            row["idle_str"] = "sí" if s.has_idle_gpu else ("no" if s.has_idle_gpu is not None else "—")
            row["utils_str"] = str(s.gpu_utils) if s.gpu_utils is not None else "—"
        rows.append(row)

    return templates.TemplateResponse(
        req,
        "cluster_status.html",
        {"rows": rows, "check_idle": check_idle, "gpu_idle_threshold": gpu_idle_threshold},
    )


@router.get("/gpu_status/{node_name}", response_class=HTMLResponse)
async def gpu_status_by_node(
    node_name: str,
    user: Annotated[str, Depends(require_user)],
    req: Request,
) -> HTMLResponse:
    """Debug: fuerza consulta a un nodo concreto (sin selector)."""
    from app.api.deps import get_ssh_pool

    pool = get_ssh_pool(req)
    result = await pool.run_on_node(node_name, "nvidia-smi", timeout=10.0)
    if result is None:
        return _render_simple(req, f"Error: no se pudo conectar a {node_name}", f"- Nodo: {node_name}")
    return _render_simple(req, str(result.stdout), f"- Nodo: {node_name} (directo)")
