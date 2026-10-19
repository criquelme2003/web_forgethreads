"""Interfaz pública del experto: se accede con el token de la invitación, sin sesión (RF-05).

El experto ve el proyecto y sus etiquetas, valida sus matrices con vista previa y confirma el
envío. Al confirmar se guarda el input y se consume el token (RF-11); los cálculos los lanza
el admin después, con el THR y el orden máximo que elija (RF-21).
Después del envío no ve resultados (RF-19).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_project_store
from app.api.fe import FeMatricesRequest, FeValidationResponse, _validation_response
from app.services.fe_input import FeInput, load_fe_input
from app.services.project_store import (
    Invitation,
    InvitationNotFoundError,
    InvitationUnavailableError,
    Project,
    ProjectStore,
)

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


@router.post("/{token}/submit", response_model=SubmitResponse)
async def submit_invite_input(
    token: str,
    body: FeMatricesRequest,
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> SubmitResponse:
    """Guarda el input y consume el token. No lanza cálculos: los lanza el admin (RF-21)."""
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
    logger.info("Input %s recibido en el proyecto %s", item.id, project.id)
    return SubmitResponse()
