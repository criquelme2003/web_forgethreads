"""Gestión de administradores (RF-01, RF-04, RF-48) y activación de cuentas.

Solo el superadmin crea, desactiva y reactiva admins. Un admin nuevo no tiene contraseña: recibe
un enlace de activación de un solo uso (`/front/activate/<token>`) con el que la elige. Regenerar
el enlace sirve también para restablecer una contraseña olvidada.
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import get_user_store, require_superadmin, require_user
from app.api.links import front_link
from app.core.config import Settings, get_settings
from app.services.mailer import MailError, build_activation_message, send_mail
from app.services.user_store import (
    MIN_PASSWORD_LENGTH,
    ActivationUnavailableError,
    User,
    UserExistsError,
    UserNotFoundError,
    UserStore,
    normalize_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])


class UserOut(BaseModel):
    id: int
    email: str
    nombre: str
    rol: str
    estado: str  # activo | pendiente (sin contraseña aún) | inactivo
    created_at: datetime
    activacion_expira_at: datetime | None
    proyectos: int = 0  # proyectos de los que es dueño

    @classmethod
    def from_user(cls, u: User, proyectos: int = 0) -> "UserOut":
        return cls(
            id=u.id, email=u.email, nombre=u.nombre, rol=u.rol, estado=u.state(),
            created_at=u.created_at, activacion_expira_at=u.activacion_expira_at,
            proyectos=proyectos,
        )


class UserLinkOut(UserOut):
    """Respuesta al crear un admin o regenerar su enlace: el enlace solo se ve en este momento."""

    link: str
    envio: Literal["manual", "smtp"]
    envio_error: str | None = None


class UserCreate(BaseModel):
    email: EmailStr
    nombre: str = Field(..., min_length=1, max_length=200)


class DeactivateRequest(BaseModel):
    reasignar_a: int | None = Field(
        default=None,
        description="Nuevo dueño de sus proyectos; si se omite, su co-admin más antiguo o quien desactiva",
    )


class ReassignedOut(BaseModel):
    project_id: int
    nombre: str
    owner_id: int


class DeactivateResponse(BaseModel):
    usuario: UserOut
    reasignados: list[ReassignedOut]


class DirectoryEntry(BaseModel):
    id: int
    email: str
    nombre: str
    rol: str


class ActivationInfo(BaseModel):
    email: str
    nombre: str


class ActivationRequest(BaseModel):
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=256)


class ActivationResponse(BaseModel):
    success: bool = True


async def _get_user_or_404(user_id: int, users: UserStore) -> User:
    try:
        return await users.get(user_id)
    except UserNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Usuario {user_id} no existe")


async def _deliver(
    user: User, token: str, settings: Settings, request: Request
) -> UserLinkOut:
    """Envía el enlace por correo si está configurado SMTP; siempre se devuelve para copiarlo."""
    link = front_link(settings, request, f"activate/{token}")
    error = None
    if settings.smtp_enabled:
        expires = user.activacion_expira_at.strftime("%Y-%m-%d %H:%M UTC")
        try:
            await send_mail(settings, build_activation_message(settings, user.email, user.nombre, link, expires))
        except MailError as e:
            logger.warning("Activación del usuario %s: %s", user.id, e)
            error = str(e)
    return UserLinkOut(
        **UserOut.from_user(user).model_dump(),
        link=link,
        envio="smtp" if settings.smtp_enabled else "manual",
        envio_error=error,
    )


# --- superadmin --------------------------------------------------------------------------------


@router.get("/app/users", response_model=list[UserOut])
async def list_users(
    admin: Annotated[User, Depends(require_superadmin)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> list[UserOut]:
    counts = await users.owned_counts()
    return [UserOut.from_user(u, counts.get(u.id, 0)) for u in await users.list_users()]


@router.post("/app/users", response_model=UserLinkOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    request: Request,
    admin: Annotated[User, Depends(require_superadmin)],
    settings: Annotated[Settings, Depends(get_settings)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> UserLinkOut:
    """Crea un admin y devuelve su enlace de activación (RF-48)."""
    email = normalize_email(str(body.email))
    if email == normalize_email(settings.auth_username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese usuario es el superadmin de la configuración")
    try:
        user, token = await users.create_admin(
            email, body.nombre.strip(), timedelta(hours=settings.activation_ttl_hours)
        )
    except UserExistsError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return await _deliver(user, token, settings, request)


@router.post("/app/users/{user_id}/activation-link", response_model=UserLinkOut)
async def regenerate_activation(
    user_id: int,
    request: Request,
    admin: Annotated[User, Depends(require_superadmin)],
    settings: Annotated[Settings, Depends(get_settings)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> UserLinkOut:
    """Nuevo enlace para activar la cuenta o restablecer la contraseña; el anterior deja de servir."""
    user = await _get_user_or_404(user_id, users)
    if user.is_superadmin:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña del superadmin se cambia en la configuración"
        )
    if not user.activo:
        raise HTTPException(status.HTTP_409_CONFLICT, "El usuario está desactivado: reactívalo primero")
    user, token = await users.regenerate_activation(
        user_id, timedelta(hours=settings.activation_ttl_hours)
    )
    return await _deliver(user, token, settings, request)


@router.post("/app/users/{user_id}/deactivate", response_model=DeactivateResponse)
async def deactivate_user(
    user_id: int,
    body: DeactivateRequest,
    admin: Annotated[User, Depends(require_superadmin)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> DeactivateResponse:
    """Desactiva al admin y reasigna sus proyectos (RF-04); pierde el acceso de inmediato."""
    user = await _get_user_or_404(user_id, users)
    if user.is_superadmin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El superadmin no se puede desactivar")
    if body.reasignar_a is not None:
        target = await _get_user_or_404(body.reasignar_a, users)
        if target.id == user.id or not target.activo:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Los proyectos se reasignan a otro usuario activo"
            )
    user, moves = await users.deactivate(user_id, body.reasignar_a, fallback_owner=admin.id)
    return DeactivateResponse(
        usuario=UserOut.from_user(user),
        reasignados=[
            ReassignedOut(project_id=m.project_id, nombre=m.nombre, owner_id=m.new_owner_id)
            for m in moves
        ],
    )


@router.post("/app/users/{user_id}/activate", response_model=UserOut)
async def reactivate_user(
    user_id: int,
    admin: Annotated[User, Depends(require_superadmin)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> UserOut:
    """Reactiva al admin. Recupera el acceso a los proyectos donde sigue como co-admin."""
    await _get_user_or_404(user_id, users)
    return UserOut.from_user(await users.set_active(user_id, True))


# --- cualquier admin ---------------------------------------------------------------------------


@router.get("/app/users/directory", response_model=list[DirectoryEntry])
async def users_directory(
    user: Annotated[User, Depends(require_user)],
    users: Annotated[UserStore, Depends(get_user_store)],
) -> list[DirectoryEntry]:
    """Usuarios activos que ya eligieron contraseña: candidatos a co-admin o a dueño (RF-49)."""
    return [
        DirectoryEntry(id=u.id, email=u.email, nombre=u.nombre, rol=u.rol)
        for u in await users.list_users()
        if u.state() == "activo"
    ]


# --- activación (pública, con el token del enlace) ---------------------------------------------


@router.get("/app/activate/{token}", response_model=ActivationInfo)
async def get_activation(
    token: str, users: Annotated[UserStore, Depends(get_user_store)]
) -> ActivationInfo:
    try:
        user = await users.find_by_activation_token(token)
    except ActivationUnavailableError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "El enlace no es válido o expiró. Pide uno nuevo al superadmin.",
        )
    return ActivationInfo(email=user.email, nombre=user.nombre)


@router.post("/app/activate/{token}", response_model=ActivationResponse)
async def activate(
    token: str,
    body: ActivationRequest,
    users: Annotated[UserStore, Depends(get_user_store)],
) -> ActivationResponse:
    """Fija la contraseña del admin y consume el enlace. Después inicia sesión con su correo."""
    try:
        user = await users.activate(token, body.password)
    except ActivationUnavailableError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "El enlace no es válido o expiró. Pide uno nuevo al superadmin.",
        )
    logger.info("Usuario %s activó su cuenta", user.id)
    return ActivationResponse()
