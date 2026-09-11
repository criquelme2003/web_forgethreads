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
) -> str:
    from fastapi.responses import HTMLResponse

    from app.services.node_selector import NoAvailableNodeError

    # Intenta primero con GPU requerida (prioridad), fallback a cualquier nodo
    try:
        node, result = await selector.run_on_best_node("nvidia-smi", require_gpu=True)
    except NoAvailableNodeError:
        try:
            node, result = await selector.run_on_best_node("nvidia-smi", require_gpu=False)
        except NoAvailableNodeError as e:
            return HTMLResponse(content=generate_page(f"No hay nodos disponibles: {e}", "- Error"), status_code=503)

    output = str(result.stdout) if result and result.stdout else "No output"
    node_info = f"- Nodo: {node.name} ({node.host}) | GPUs libres: {node.gpus_free}/{node.gpus_total} | CPUs libres: {node.cpus_free}/{node.cpus_total} | Estado: {node.state.value}"
    return generate_page(output, node_info)


@router.get("/cluster_status", response_class=HTMLResponse)
async def cluster_status(
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> str:
    """Vista de disponibilidad de los 3 nodos (SLURM + score)."""
    statuses = await selector.get_all_status()
    rows = ""
    for s in sorted(statuses, key=lambda x: selector.score(x), reverse=True):
        score = selector.score(s)
        rows += f"<tr><td>{s.name}</td><td>{s.host}</td><td>{s.state.value}</td><td>{s.gpus_free}/{s.gpus_total}</td><td>{s.cpus_free}/{s.cpus_total}</td><td>{s.pending_jobs}</td><td>{score:.1f}</td><td>{'sí' if s.reachable else 'no'}</td></tr>\n"
    return f"""<!DOCTYPE html>
  <html lang=\"es\">
  <head><meta charset=\"UTF-8\" /><title>Cluster Status</title></head>
  <body>
    <h1>Cluster Status (GPU priorizada)</h1>
    <table border=\"1\" cellpadding=\"5\" cellspacing=\"0\">
      <tr><th>Nodo</th><th>Host</th><th>Estado SLURM</th><th>GPUs libres</th><th>CPUs libres</th><th>Jobs pendientes</th><th>Score</th><th>Reachable</th></tr>
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
