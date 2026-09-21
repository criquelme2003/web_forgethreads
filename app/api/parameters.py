import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import get_node_selector, require_user
from app.services.node_selector import NodeSelector

logger = logging.getLogger(__name__)

router = APIRouter(tags=["parameters"], prefix="/front")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _render_form(request: Request, selector: NodeSelector) -> HTMLResponse:
    node_options = ""
    node_hint = "Nodo/GPU específico donde se lanzará el job."
    try:
        statuses = await selector.get_all_status(force_refresh=True)
    except Exception as e:
        logger.warning("No se pudo consultar disponibilidad de nodos para el formulario: %s", e)
        node_hint = "No se pudo consultar el cluster; se usará selección automática."
    else:
        available = [s for s in statuses if s.is_available]
        for s in available:
            node_options += (
                f'<option value="{s.name}">{s.name} '
                f"({s.gpus_free}/{s.gpus_total} GPU libres)</option>\n"
            )
        if not available:
            node_hint = "Ningún nodo disponible por ahora; se usará selección automática."
    return templates.TemplateResponse(
        request, "parameters_form.html", {"node_options": node_options, "node_hint": node_hint}
    )


@router.get("/parameters", response_class=HTMLResponse)
async def parameters_form(
    request: Request,
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> HTMLResponse:
    return await _render_form(request, selector)
