import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_node_selector, require_user
from app.services.node_selector import NodeSelector

logger = logging.getLogger(__name__)

router = APIRouter(tags=["parameters"], prefix="/app")


class NodeOption(BaseModel):
    value: str
    label: str


class AvailableNodesResponse(BaseModel):
    options: list[NodeOption]
    hint: str


@router.get("/nodes/available", response_model=AvailableNodesResponse)
async def available_nodes(
    user: Annotated[str, Depends(require_user)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> AvailableNodesResponse:
    """Opciones de nodo/GPU para el formulario de parámetros (antes render server-side con Jinja)."""
    options: list[NodeOption] = []
    node_hint = "Nodo/GPU específico donde se lanzará el job."
    try:
        statuses = await selector.get_all_status(force_refresh=True)
    except Exception as e:
        logger.warning("No se pudo consultar disponibilidad de nodos para el formulario: %s", e)
        node_hint = "No se pudo consultar el cluster; se usará selección automática."
    else:
        available = [s for s in statuses if s.is_available]
        for s in available:
            options.append(
                NodeOption(
                    value=s.name,
                    label=f"{s.name} ({s.gpus_free}/{s.gpus_total} GPU libres)",
                )
            )
        if not available:
            node_hint = "Ningún nodo disponible por ahora; se usará selección automática."
    return AvailableNodesResponse(options=options, hint=node_hint)
