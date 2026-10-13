"""Interfaz pública del experto: se accede con el token de la invitación, sin sesión (RF-05).

El experto ve el proyecto y sus etiquetas, valida sus matrices con vista previa y confirma el
envío. Al confirmar se consume el token (RF-11) y se lanza el cálculo en segundo plano (RF-21).
Después del envío no ve resultados (RF-19).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_job_store, get_node_selector, get_project_store, get_ssh_pool
from app.api.fe import FeMatricesRequest, FeValidationResponse, _validation_response
from app.api.projects import launch_expert_computation
from app.core.config import Settings, get_settings
from app.services.fe_input import FeInput, load_fe_input
from app.services.job_store import JobStore
from app.services.node_selector import NodeSelector
from app.services.project_store import (
    ExpertInput,
    Invitation,
    InvitationNotFoundError,
    InvitationUnavailableError,
    Project,
    ProjectStore,
)
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app/invite", tags=["invite"])

_UNAVAILABLE = {
    "usada": "Esta invitación ya fue usada: tu respuesta fue recibida.",
    "revocada": "Esta invitación fue revocada.",
    "expirada": "Esta invitación expiró. Pide un enlace nuevo a quien te invitó.",
}


class InviteInfo(BaseModel):
    proyecto: str
    descripcion: str
    email: str
    causas: list[str]
    efectos: list[str]
    expira_at: str


class SubmitResponse(BaseModel):
    received: bool = True


async def _resolve(token: str, projects: ProjectStore) -> tuple[Invitation, Project]:
    """Invitación usable de un proyecto abierto (RF-14), o el error que verá el experto."""
    try:
        inv = await projects.find_invitation_by_token(token)
    except InvitationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitación no válida")
    state = inv.state()
    if state != "pendiente":
        raise HTTPException(status.HTTP_410_GONE, _UNAVAILABLE[state])
    project = await projects.get_project(inv.project_id)
    if project.estado != "abierto":
        raise HTTPException(status.HTTP_409_CONFLICT, "El proyecto no está recibiendo respuestas en este momento.")
    return inv, project


def _load(body: FeMatricesRequest, project: Project) -> FeInput:
    return load_fe_input(body.cc, body.ce, body.ee, causes=project.causas, effects=project.efectos)


@router.get("/{token}", response_model=InviteInfo)
async def get_invite(
    token: str,
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> InviteInfo:
    inv, project = await _resolve(token, projects)
    return InviteInfo(
        proyecto=project.nombre,
        descripcion=project.descripcion,
        email=inv.email,
        causas=project.causas,
        efectos=project.efectos,
        expira_at=inv.expira_at.isoformat(),
    )


@router.post("/{token}/validate", response_model=FeValidationResponse)
async def validate_invite_input(
    token: str,
    body: FeMatricesRequest,
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> FeValidationResponse:
    """Vista previa con errores por celda, contra las etiquetas del proyecto (RF-16, RF-17)."""
    _, project = await _resolve(token, projects)
    return _validation_response(_load(body, project))


async def _launch_in_background(
    project: Project,
    item: ExpertInput,
    pool: SSHConnectionPool,
    selector: NodeSelector,
    store: JobStore,
    projects: ProjectStore,
    settings: Settings,
) -> None:
    try:
        await launch_expert_computation(
            project=project, item=item, pool=pool, selector=selector, store=store,
            projects=projects, settings=settings,
        )
    except HTTPException as e:
        # El input queda guardado con el error; el admin lo ve y puede reintentar (RF-23).
        logger.warning("No se pudo lanzar el cálculo del input %s: %s", item.id, e.detail)


@router.post("/{token}/submit", response_model=SubmitResponse)
async def submit_invite_input(
    token: str,
    body: FeMatricesRequest,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> SubmitResponse:
    _, project = await _resolve(token, projects)
    data = _load(body, project)
    if not data.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_validation_response(data).model_dump(),
        )
    try:
        item = await projects.submit_input(
            token, data.cc.tolist(), data.ce.tolist(), data.ee.tolist()
        )
    except InvitationUnavailableError:
        raise HTTPException(status.HTTP_410_GONE, "Esta invitación ya no está disponible.")
    background.add_task(_launch_in_background, project, item, pool, selector, store, projects, settings)
    return SubmitResponse()
