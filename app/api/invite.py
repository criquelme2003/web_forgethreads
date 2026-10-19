"""Interfaz pública del experto: se accede con el token de la invitación, sin sesión (RF-05).

El experto ve el proyecto y sus etiquetas, valida sus matrices con vista previa y confirma el
envío. El primer envío consume la invitación (RF-11); durante `input_edit_days` el mismo enlace
sirve para modificar el input, y cada modificación es una versión nueva (RF-52, RF-53). Los
cálculos los lanza el admin después, con el THR y el orden máximo que elija (RF-21).
El experto no ve resultados (RF-19).
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_project_store
from app.api.fe import FeMatricesRequest, FeValidationResponse, _validation_response
from app.core.config import Settings, get_settings
from app.services.fe_input import FeInput, load_fe_input
from app.services.project_store import (
    ExpertInput,
    Invitation,
    InvitationNotFoundError,
    InvitationUnavailableError,
    Project,
    ProjectStore,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app/invite", tags=["invite"])

_UNAVAILABLE = {
    "usada": "Tu respuesta fue recibida y el plazo para modificarla terminó.",
    "revocada": "Esta invitación fue revocada.",
    "expirada": "Esta invitación expiró. Pide un enlace nuevo a quien te invitó.",
}


class CurrentMatrices(BaseModel):
    """Input ya enviado, en el orden de las etiquetas del proyecto, para modificarlo."""

    cc: list[list[float]]
    ce: list[list[float]]
    ee: list[list[float]]


class InviteInfo(BaseModel):
    proyecto: str
    descripcion: str
    email: str
    nombre: str | None
    causas: list[str]
    efectos: list[str]
    expira_at: str
    modo: Literal["envio", "edicion"]  # primer envío o modificación dentro del plazo (RF-52)
    plazo_edicion_dias: int
    editable_hasta: str | None = None
    version: int | None = None
    actual: CurrentMatrices | None = None


class SubmitResponse(BaseModel):
    received: bool = True
    version: int
    editable_hasta: datetime | None


async def _resolve(
    token: str, projects: ProjectStore
) -> tuple[Invitation, Project, ExpertInput | None]:
    """Invitación usable de un proyecto abierto (RF-14), o el error que verá el experto.

    Una invitación usada sigue sirviendo mientras su input esté dentro del plazo de edición
    (RF-52); cerrar el proyecto suspende la edición.
    """
    try:
        inv = await projects.find_invitation_by_token(token)
    except InvitationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitación no válida")
    state = inv.state()
    item = None
    if state == "usada":
        item = await projects.find_input_by_invitation(inv.id)
        if item is None or not item.editable():
            raise HTTPException(status.HTTP_410_GONE, _UNAVAILABLE[state])
    elif state != "pendiente":
        raise HTTPException(status.HTTP_410_GONE, _UNAVAILABLE[state])
    project = await projects.get_project(inv.project_id)
    if project.estado != "abierto":
        raise HTTPException(status.HTTP_409_CONFLICT, "El proyecto no está recibiendo respuestas en este momento.")
    return inv, project, item


def _load(body: FeMatricesRequest, project: Project) -> FeInput:
    return load_fe_input(body.cc, body.ce, body.ee, causes=project.causas, effects=project.efectos)


@router.get("/{token}", response_model=InviteInfo)
async def get_invite(
    token: str,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> InviteInfo:
    inv, project, item = await _resolve(token, projects)
    return InviteInfo(
        proyecto=project.nombre,
        descripcion=project.descripcion,
        email=inv.email,
        nombre=inv.nombre,
        causas=project.causas,
        efectos=project.efectos,
        expira_at=inv.expira_at.isoformat(),
        modo="edicion" if item else "envio",
        plazo_edicion_dias=settings.input_edit_days,
        editable_hasta=item.editable_hasta.isoformat() if item and item.editable_hasta else None,
        version=item.version if item else None,
        actual=CurrentMatrices(cc=item.cc, ce=item.ce, ee=item.ee) if item else None,
    )


@router.post("/{token}/validate", response_model=FeValidationResponse)
async def validate_invite_input(
    token: str,
    body: FeMatricesRequest,
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> FeValidationResponse:
    """Vista previa con errores por celda, contra las etiquetas del proyecto (RF-16, RF-17)."""
    _, project, _ = await _resolve(token, projects)
    return _validation_response(_load(body, project))


@router.post("/{token}/submit", response_model=SubmitResponse)
async def submit_invite_input(
    token: str,
    body: FeMatricesRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> SubmitResponse:
    """Guarda el input (primer envío o versión nueva). No lanza cálculos: los lanza el admin (RF-21)."""
    _, project, _ = await _resolve(token, projects)
    data = _load(body, project)
    if not data.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_validation_response(data).model_dump(),
        )
    try:
        item = await projects.submit_input(
            token, data.cc.tolist(), data.ce.tolist(), data.ee.tolist(),
            edit_window=timedelta(days=settings.input_edit_days),
        )
    except InvitationUnavailableError:
        raise HTTPException(status.HTTP_410_GONE, "Esta invitación ya no está disponible.")
    logger.info("Input %s (versión %s) recibido en el proyecto %s", item.id, item.version, project.id)
    return SubmitResponse(version=item.version, editable_hasta=item.editable_hasta)
