from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.api.deps import get_node_selector, require_user
from app.services.node_selector import NodeSelector

router = APIRouter(tags=["parameters"], prefix="/front")


def generate_page(input: str, node_info: str = "") -> str:
    return f"""<!DOCTYPE html>
  <html lang="es">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Parámetros</title>
  </head>
  <body>
    <h1>NVIDIA-SMI Result {node_info}</h1>
    <pre>{input}</pre>
  </body>
  </html>
  """


@router.get("/gpu_status", response_class=HTMLResponse)
async def parameters_form(
    user: Annotated[str, Depends(require_user)],
    req: Request,
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    prefer_idle: bool = False,
    gpu_idle_threshold: int | None = None,
) -> str:
    """
    - Por defecto: selección por score (GPU priorizada).
    - Si prefer_idle=true: primero busca nodo con GPU en idle (nvidia-smi util < threshold).
      Si existe lo usa; si no, fallback automático a scoring normal.
      Ej: /front/gpu_status?prefer_idle=true  o  ?prefer_idle=true&gpu_idle_threshold=10
    """
    from fastapi.responses import HTMLResponse

    from app.core.config import get_settings
    from app.services.node_selector import NoAvailableNodeError

    if gpu_idle_threshold is None:
        try:
            gpu_idle_threshold = get_settings().gpu_idle_threshold
        except Exception:
            gpu_idle_threshold = 5

    # Intenta primero con GPU requerida (prioridad), fallback a cualquier nodo
    # Si prefer_idle, delega la lógica idle al selector (sencilla y con fallback)
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
            return HTMLResponse(content=generate_page(f"No hay nodos disponibles: {e}", "- Error"), status_code=503)

    output = str(result.stdout) if result and result.stdout else "No output"
    idle_tag = ""
    if prefer_idle and getattr(node, "has_idle_gpu", None) is not None:
        # si pasamos por idle path, node ya trae info
        if node.has_idle_gpu:
            idle_tag = f" | GPU idle: sí (utils={node.gpu_utils})"
        else:
            idle_tag = " | GPU idle: no (fallback score)"
    node_info = f"- Nodo: {node.name} ({node.host}) | GPUs libres: {node.gpus_free}/{node.gpus_total} | CPUs libres: {node.cpus_free}/{node.cpus_total} | Estado: {node.state.value}{idle_tag}"
    return generate_page(output, node_info)


@router.get("/cluster_status", response_class=HTMLResponse)
async def cluster_status(
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
    check_idle: bool = False,
    gpu_idle_threshold: int | None = None,
) -> str:
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
        # enriquece con idle para mostrar en tabla
        idle_nodes = await selector.find_idle_gpu_nodes(gpu_idle_threshold=gpu_idle_threshold)
        idle_set = {n.name for n in idle_nodes}
        statuses = await selector.get_all_status()
        # re-inyectar has_idle_gpu desde idle_nodes (ya están enriquecidos, comparten objetos cache)
        for s in statuses:
            if s.name in idle_set:
                s.has_idle_gpu = True
            elif s.has_idle_gpu is None:
                # los no-idle ya fueron marcados False en find_idle_gpu_nodes
                pass
    else:
        statuses = await selector.get_all_status()
    # columnas extra si check_idle
    if check_idle:
        header_extra = "<th>GPU idle?</th><th>GPU utils</th>"
        rows = ""
        for s in sorted(statuses, key=lambda x: selector.score(x), reverse=True):
            score = selector.score(s)
            idle_str = "—"
            utils_str = "—"
            if s.has_idle_gpu is not None:
                idle_str = "sí" if s.has_idle_gpu else "no"
                utils_str = str(s.gpu_utils) if s.gpu_utils is not None else "—"
            rows += f"<tr><td>{s.name}</td><td>{s.host}</td><td>{s.state.value}</td><td>{s.gpus_free}/{s.gpus_total}</td><td>{s.cpus_free}/{s.cpus_total}</td><td>{s.pending_jobs}</td><td>{score:.1f}</td><td>{'sí' if s.reachable else 'no'}</td><td>{idle_str}</td><td>{utils_str}</td></tr>\n"
        extra_note = f"<p>GPU idle threshold: {gpu_idle_threshold}% (util &lt; threshold = idle)</p>"
    else:
        header_extra = ""
        rows = ""
        for s in sorted(statuses, key=lambda x: selector.score(x), reverse=True):
            score = selector.score(s)
            rows += f"<tr><td>{s.name}</td><td>{s.host}</td><td>{s.state.value}</td><td>{s.gpus_free}/{s.gpus_total}</td><td>{s.cpus_free}/{s.cpus_total}</td><td>{s.pending_jobs}</td><td>{score:.1f}</td><td>{'sí' if s.reachable else 'no'}</td></tr>\n"
        extra_note = "<p><a href='?check_idle=true'>Ver con check GPU idle (nvidia-smi)</a></p>"
    return f"""<!DOCTYPE html>
  <html lang=\"es\">
  <head><meta charset=\"UTF-8\" /><title>Cluster Status</title></head>
  <body>
    <h1>Cluster Status (GPU priorizada)</h1>
    {extra_note}
    <table border=\"1\" cellpadding=\"5\" cellspacing=\"0\">
      <tr><th>Nodo</th><th>Host</th><th>Estado SLURM</th><th>GPUs libres</th><th>CPUs libres</th><th>Jobs pendientes</th><th>Score</th><th>Reachable</th>{header_extra}</tr>
      {rows}
    </table>
  </body>
  </html>
  """


@router.get("/gpu_status/{node_name}", response_class=HTMLResponse)
async def gpu_status_by_node(
    node_name: str,
    user: Annotated[str, Depends(require_user)],
    req: Request,
) -> str:
    """Debug: fuerza consulta a un nodo concreto (sin selector)."""
    from app.api.deps import get_ssh_pool

    pool = get_ssh_pool(req)
    result = await pool.run_on_node(node_name, "nvidia-smi", timeout=10.0)
    if result is None:
        return generate_page(f"Error: no se pudo conectar a {node_name}", f"- Nodo: {node_name}")
    return generate_page(str(result.stdout), f"- Nodo: {node_name} (directo)")
