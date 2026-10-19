"""Proyectos de expertos y de simulación: CRUD, estados, admins, invitaciones e inputs de expertos.

Acceso (RF-01, RF-03, RF-49): el superadmin ve todo; un admin solo los proyectos de los que es
dueño o co-admin. Para un proyecto ajeno se responde 404, igual que si no existiera. Borrar el
proyecto y gestionar sus co-admins es del dueño (o del superadmin).
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.api.deps import (
    get_job_store,
    get_node_selector,
    get_project_store,
    get_ssh_pool,
    get_user_store,
    require_user,
)
from app.api.fe import launch_fe_job
from app.api.job_callback import JobStatusResponse, job_responses
from app.api.links import front_link
from app.core.config import Settings, get_settings
from app.services.fe_input import MAX_LABELS
from app.services.job_store import JobRecord, JobStore
from app.services.mailer import MailError, build_invitation_message, send_mail
from app.services.node_selector import NodeSelector
from app.services.project_store import (
    TRANSITIONS,
    ExpertInput,
    InputNotFoundError,
    Invitation,
    InvitationHasInputError,
    InvitationNotFoundError,
    InvitationUnavailableError,
    Project,
    ProjectNotFoundError,
    ProjectStore,
)
from app.services.user_store import User, UserStore
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app/projects", tags=["projects"])

MAX_NAME = 200
MAX_DESCRIPTION = 5000
MAX_LABEL_CHARS = 100


def _clean_labels(labels: list[str] | None, side: str) -> list[str] | None:
    if labels is None:
        return None
    cleaned = [label.strip() for label in labels]
    if not cleaned:
        raise ValueError(f"Debe haber al menos una etiqueta de {side}")
    if len(cleaned) > MAX_LABELS:
        raise ValueError(f"Hay {len(cleaned)} {side}; el máximo es {MAX_LABELS}")
    for label in cleaned:
        if not label:
            raise ValueError(f"Hay una etiqueta de {side} vacía")
        if len(label) > MAX_LABEL_CHARS:
            raise ValueError(f"La etiqueta '{label[:20]}…' supera {MAX_LABEL_CHARS} caracteres")
        # ';' cambiaría el separador detectado en los CSV; los saltos de línea romperían las filas.
        if any(ch in label for ch in ";\n\r"):
            raise ValueError(f"La etiqueta '{label}' no puede contener ';' ni saltos de línea")
    dup = sorted({label for label in cleaned if cleaned.count(label) > 1})
    if dup:
        raise ValueError(f"Etiquetas de {side} repetidas: {', '.join(dup)}")
    return cleaned


class ExpertParams(BaseModel):
    """Etiquetas de causas y efectos de un proyecto de expertos (RF-08).

    THR y el orden máximo no son del proyecto: el admin los indica al lanzar cada cálculo.
    """

    causas: list[str] | None = None
    efectos: list[str] | None = None

    @field_validator("causas")
    @classmethod
    def _causas(cls, v: list[str] | None) -> list[str] | None:
        return _clean_labels(v, "causas")

    @field_validator("efectos")
    @classmethod
    def _efectos(cls, v: list[str] | None) -> list[str] | None:
        return _clean_labels(v, "efectos")

    @model_validator(mode="after")
    def _disjoint(self):
        if self.causas and self.efectos:
            shared = [c for c in self.causas if c in self.efectos]
            if shared:
                raise ValueError(f"Una etiqueta no puede ser causa y efecto: {', '.join(shared)}")
        return self


class ProjectCreate(ExpertParams):
    nombre: str = Field(..., min_length=1, max_length=MAX_NAME)
    descripcion: str = Field(default="", max_length=MAX_DESCRIPTION)
    tipo: Literal["expertos", "simulacion"]

    @model_validator(mode="after")
    def _expert_fields(self):
        if self.tipo == "expertos":
            missing = [k for k in ("causas", "efectos") if getattr(self, k) is None]
            if missing:
                raise ValueError(f"Un proyecto de expertos requiere: {', '.join(missing)}")
        return self


class ProjectUpdate(ExpertParams):
    nombre: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    descripcion: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


class ProjectStateChange(BaseModel):
    estado: Literal["borrador", "abierto", "cerrado"]


class PersonOut(BaseModel):
    id: int
    email: str
    nombre: str
    activo: bool = True


class ProjectOut(BaseModel):
    id: int
    nombre: str
    descripcion: str
    tipo: str
    estado: str
    owner: PersonOut | None
    admins: list[PersonOut]  # co-admins (RF-49)
    puede_gestionar: bool  # el usuario puede borrar el proyecto y gestionar co-admins
    created_at: datetime
    updated_at: datetime
    causas: list[str]
    efectos: list[str]
    transiciones: list[str]  # estados a los que se puede pasar desde el actual
    conteos: dict[str, int] = {}

    @classmethod
    def from_project(
        cls,
        p: Project,
        people: dict[int, User],
        user: User,
        conteos: dict[str, int] | None = None,
    ) -> "ProjectOut":
        def person(uid: int | None) -> PersonOut | None:
            u = people.get(uid) if uid is not None else None
            return PersonOut(id=u.id, email=u.email, nombre=u.nombre, activo=u.activo) if u else None

        return cls(
            **{k: getattr(p, k) for k in (
                "id", "nombre", "descripcion", "tipo", "estado", "created_at",
                "updated_at", "causas", "efectos",
            )},
            owner=person(p.owner_id),
            admins=[a for a in (person(uid) for uid in p.admin_ids) if a],
            puede_gestionar=can_manage(p, user),
            transiciones=sorted(TRANSITIONS[p.tipo].get(p.estado, set())),
            conteos=conteos or {},
        )


class AdminAdd(BaseModel):
    user_id: int


class OwnerChange(BaseModel):
    user_id: int


class InvitationCreate(BaseModel):
    email: EmailStr
    nombre: str | None = Field(default=None, max_length=200, description="Para identificarlo en los resultados")


class InvitationOut(BaseModel):
    id: int
    email: str
    nombre: str | None
    estado: str
    created_at: datetime
    expira_at: datetime
    usado_at: datetime | None
    revocado_at: datetime | None
    enviado_at: datetime | None
    envio_error: str | None

    @classmethod
    def from_invitation(cls, inv: Invitation) -> "InvitationOut":
        return cls(
            id=inv.id, email=inv.email, nombre=inv.nombre, estado=inv.state(), created_at=inv.created_at,
            expira_at=inv.expira_at, usado_at=inv.usado_at, revocado_at=inv.revocado_at,
            enviado_at=inv.enviado_at, envio_error=inv.envio_error,
        )


class InvitationLinkOut(InvitationOut):
    """Respuesta al crear o regenerar: el enlace solo se puede ver en este momento."""

    link: str
    envio: Literal["manual", "smtp"]


class ResendRequest(BaseModel):
    invitation_ids: list[int] | None = Field(
        default=None, description="Invitaciones a reenviar; si se omite, todas las que no han respondido"
    )


class ResendSkipped(BaseModel):
    invitation_id: int
    detail: str


class ResendResponse(BaseModel):
    reenviadas: list[InvitationLinkOut]
    omitidas: list[ResendSkipped]


class ComputationOut(BaseModel):
    job_id: str
    status: str
    node: str
    created_at: datetime
    thr: float | None = None
    maxorder: int | None = None
    input_version: int = 1
    desactualizado: bool = False  # el input se modificó después del cálculo (RF-53)


class InputOut(BaseModel):
    id: int
    invitation_id: int
    email: str | None
    nombre: str | None
    created_at: datetime
    updated_at: datetime | None
    version: int
    editable_hasta: datetime | None
    editable: bool  # el experto todavía puede modificarlo (RF-52)
    excluido: bool
    calculos: list[ComputationOut]  # del más reciente al más antiguo


class InputVersionOut(BaseModel):
    version: int
    created_at: datetime


class InputUpdate(BaseModel):
    excluido: bool


class ComputeRequest(BaseModel):
    """Cálculo por experto con los parámetros que elige el admin (RF-21)."""

    thr: float = Field(..., ge=0, le=1, description="Threshold de forgeffects")
    maxorder: int = Field(..., ge=2, le=10, description="Orden máximo de los caminos")
    nodo: str | None = Field(default=None, description="Nodo objetivo; si se omite, se elige automáticamente")
    input_ids: list[int] | None = Field(
        default=None, description="Inputs a calcular; si se omite, todos los no excluidos"
    )


class LaunchedOut(BaseModel):
    input_id: int
    job_id: str
    node: str


class LaunchErrorOut(BaseModel):
    input_id: int
    detail: str


class ComputeResponse(BaseModel):
    lanzados: list[LaunchedOut]
    errores: list[LaunchErrorOut]


# --- helpers ---------------------------------------------------------------------------------


def can_access(project: Project, user: User) -> bool:
    return user.is_superadmin or project.is_member(user.id)


def can_manage(project: Project, user: User) -> bool:
    """Borrar el proyecto y gestionar co-admins: dueño o superadmin (RF-49)."""
    return user.is_superadmin or project.owner_id == user.id


async def get_project_or_404(project_id: int, store: ProjectStore, user: User) -> Project:
    """El proyecto, si el usuario tiene acceso; si no, 404 (no se revela que existe)."""
    try:
        project = await store.get_project(project_id)
    except ProjectNotFoundError:
        project = None
    if project is None or not can_access(project, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Proyecto {project_id} no existe")
    return project


def _require_manage(project: Project, user: User) -> None:
    if not can_manage(project, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo el dueño del proyecto puede hacer esto")


async def _people(users: UserStore) -> dict[int, User]:
    return {u.id: u for u in await users.list_users()}


async def _project_out(
    project: Project, user: User, users: UserStore, projects: ProjectStore, counts: bool = True
) -> ProjectOut:
    conteos = (await projects.project_counts()).get(project.id) if counts else None
    return ProjectOut.from_project(project, await _people(users), user, conteos)


def _require_expertos(project: Project) -> None:
    if project.tipo != "expertos":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El proyecto no es de expertos")


def invitation_link(token: str, settings: Settings, request: Request) -> str:
    return front_link(settings, request, f"invite/{token}")


async def _deliver(
    inv: Invitation, token: str, project: Project, settings: Settings, request: Request, store: ProjectStore
) -> InvitationLinkOut:
    """Envía la invitación por correo si está configurado SMTP; el enlace siempre se devuelve."""
    link = invitation_link(token, settings, request)
    if settings.smtp_enabled:
        msg = build_invitation_message(
            settings, inv.email, project.nombre, link, inv.expira_at.strftime("%Y-%m-%d %H:%M UTC"),
            nombre=inv.nombre, edit_days=settings.input_edit_days,
        )
        try:
            await send_mail(settings, msg)
            error = None
        except MailError as e:
            logger.warning("Invitación %s: %s", inv.id, e)
            error = str(e)
        await store.set_delivery(inv.id, error)
        inv = await store.get_invitation(project.id, inv.id)
    return InvitationLinkOut(
        **InvitationOut.from_invitation(inv).model_dump(),
        link=link,
        envio="smtp" if settings.smtp_enabled else "manual",
    )


def input_matrices(item: ExpertInput) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return tuple(np.asarray(m, dtype=np.float32) for m in (item.cc, item.ce, item.ee))  # type: ignore[return-value]


async def launch_expert_computation(
    *,
    project: Project,
    item: ExpertInput,
    thr: float,
    maxorder: int,
    nodo: str | None,
    pool: SSHConnectionPool,
    selector: NodeSelector,
    store: JobStore,
    settings: Settings,
    user_id: int | None = None,
) -> tuple[str, str]:
    """Lanza forgeffects sobre la versión vigente del input de un experto (RF-21, RF-53).

    Lanza HTTPException si falla.
    """
    cc, ce, ee = input_matrices(item)
    return await launch_fe_job(
        pool=pool, store=store, selector=selector, settings=settings, nodo=nodo, cc=cc, ce=ce, ee=ee,
        causes=project.causas, effects=project.efectos, thr=thr, maxorder=maxorder,
        project_id=project.id, input_id=item.id, input_version=item.version, user_id=user_id,
    )


# --- proyectos -------------------------------------------------------------------------------

CurrentUser = Annotated[User, Depends(require_user)]
Projects = Annotated[ProjectStore, Depends(get_project_store)]
Users = Annotated[UserStore, Depends(get_user_store)]


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, projects: Projects, users: Users) -> list[ProjectOut]:
    """Todos los proyectos para el superadmin; los propios y aquellos donde es co-admin, para un admin."""
    counts = await projects.project_counts()
    people = await _people(users)
    found = await projects.list_projects(None if user.is_superadmin else user.id)
    return [ProjectOut.from_project(p, people, user, counts.get(p.id)) for p in found]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, user: CurrentUser, projects: Projects, users: Users
) -> ProjectOut:
    expert = body.tipo == "expertos"
    project = await projects.create_project(
        nombre=body.nombre.strip(),
        descripcion=body.descripcion.strip(),
        tipo=body.tipo,
        owner_id=user.id,
        causas=body.causas if expert else None,
        efectos=body.efectos if expert else None,
    )
    return await _project_out(project, user, users, projects, counts=False)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, user: CurrentUser, projects: Projects, users: Users) -> ProjectOut:
    project = await get_project_or_404(project_id, projects, user)
    return await _project_out(project, user, users, projects)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int, body: ProjectUpdate, user: CurrentUser, projects: Projects, users: Users
) -> ProjectOut:
    """Nombre y descripción se editan siempre; etiquetas y parámetros solo en borrador (RF-09)."""
    project = await get_project_or_404(project_id, projects, user)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    expert_fields = {k for k in fields if k in ("causas", "efectos")}
    if expert_fields:
        _require_expertos(project)
        if project.estado != "borrador":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Las etiquetas solo se pueden editar en borrador",
            )
        causas = fields.get("causas", project.causas)
        efectos = fields.get("efectos", project.efectos)
        shared = [c for c in causas if c in efectos]
        if shared:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Una etiqueta no puede ser causa y efecto: {', '.join(shared)}",
            )
    for key in ("nombre", "descripcion"):
        if key in fields:
            fields[key] = fields[key].strip()
    project = await projects.update_project(project_id, **fields)
    return await _project_out(project, user, users, projects)


@router.post("/{project_id}/estado", response_model=ProjectOut)
async def change_state(
    project_id: int, body: ProjectStateChange, user: CurrentUser, projects: Projects, users: Users
) -> ProjectOut:
    project = await get_project_or_404(project_id, projects, user)
    allowed = TRANSITIONS[project.tipo].get(project.estado, set())
    if body.estado not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No se puede pasar de {project.estado} a {body.estado}",
        )
    project = await projects.update_project(project_id, estado=body.estado)
    return await _project_out(project, user, users, projects)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    user: CurrentUser,
    projects: Projects,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> Response:
    """Solo el dueño borra, y solo proyectos sin inputs ni cálculos, para no perder resultados."""
    project = await get_project_or_404(project_id, projects, user)
    _require_manage(project, user)
    if await projects.count_inputs(project_id) or await store.list_by_project(project_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El proyecto tiene inputs o cálculos; ciérralo en vez de borrarlo",
        )
    await projects.delete_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/jobs", response_model=list[JobStatusResponse])
async def list_project_jobs(
    project_id: int,
    user: CurrentUser,
    projects: Projects,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> list[JobStatusResponse]:
    await get_project_or_404(project_id, projects, user)
    return await job_responses(await store.list_by_project(project_id), projects)


# --- dueño y co-admins (RF-03, RF-04, RF-49) -------------------------------------------------


async def _active_user(user_id: int, users: UserStore) -> User:
    people = await _people(users)
    target = people.get(user_id)
    if target is None or target.state() != "activo":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El usuario no existe o no está activo")
    return target


@router.post("/{project_id}/admins", response_model=ProjectOut)
async def add_project_admin(
    project_id: int, body: AdminAdd, user: CurrentUser, projects: Projects, users: Users
) -> ProjectOut:
    """Agrega un co-admin entre los admins activos. No crea usuarios: eso es del superadmin."""
    project = await get_project_or_404(project_id, projects, user)
    _require_manage(project, user)
    target = await _active_user(body.user_id, users)
    if target.id == project.owner_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese usuario ya es el dueño del proyecto")
    project = await projects.add_admin(project_id, target.id)
    return await _project_out(project, user, users, projects)


@router.delete("/{project_id}/admins/{user_id}", response_model=ProjectOut)
async def remove_project_admin(
    project_id: int, user_id: int, user: CurrentUser, projects: Projects, users: Users
) -> ProjectOut:
    """El dueño quita co-admins; un co-admin también puede salir del proyecto."""
    project = await get_project_or_404(project_id, projects, user)
    if user_id != user.id:
        _require_manage(project, user)
    if user_id not in project.admin_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ese usuario no es co-admin del proyecto")
    project = await projects.remove_admin(project_id, user_id)
    return await _project_out(project, user, users, projects)


@router.post("/{project_id}/owner", response_model=ProjectOut)
async def change_project_owner(
    project_id: int, body: OwnerChange, user: CurrentUser, projects: Projects, users: Users
) -> ProjectOut:
    """Transfiere la propiedad (RF-04). El dueño anterior queda como co-admin."""
    project = await get_project_or_404(project_id, projects, user)
    _require_manage(project, user)
    target = await _active_user(body.user_id, users)
    previous = project.owner_id
    project = await projects.set_owner(project_id, target.id)
    if previous is not None and previous != target.id:
        project = await projects.add_admin(project_id, previous)
    return await _project_out(project, user, users, projects)


# --- invitaciones ----------------------------------------------------------------------------


async def _expert_project(project_id: int, projects: ProjectStore, user: User) -> Project:
    project = await get_project_or_404(project_id, projects, user)
    _require_expertos(project)
    return project


def _require_not_closed(project: Project) -> None:
    if project.estado == "cerrado":
        raise HTTPException(status.HTTP_409_CONFLICT, "El proyecto está cerrado")


def _not_found_invitation(invitation_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Invitación {invitation_id} no existe")


@router.get("/{project_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(project_id: int, user: CurrentUser, projects: Projects) -> list[InvitationOut]:
    await _expert_project(project_id, projects, user)
    return [InvitationOut.from_invitation(i) for i in await projects.list_invitations(project_id)]


@router.post(
    "/{project_id}/invitations", response_model=InvitationLinkOut, status_code=status.HTTP_201_CREATED
)
async def create_invitation(
    project_id: int,
    body: InvitationCreate,
    request: Request,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Projects,
) -> InvitationLinkOut:
    """Crea la invitación (RF-10). El enlace se muestra solo en esta respuesta; si se pierde, se regenera."""
    project = await _expert_project(project_id, projects, user)
    _require_not_closed(project)
    nombre = (body.nombre or "").strip() or None
    inv, token = await projects.create_invitation(
        project_id, str(body.email).strip().lower(), timedelta(days=settings.invitation_ttl_days), nombre
    )
    return await _deliver(inv, token, project, settings, request, projects)


@router.post("/{project_id}/invitations/resend", response_model=ResendResponse)
async def resend_invitations(
    project_id: int,
    body: ResendRequest,
    request: Request,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Projects,
) -> ResendResponse:
    """Reenvío por lotes a quienes no han respondido (RF-50).

    Como solo se guarda el hash, reenviar regenera el token: el enlace anterior deja de funcionar
    y el vencimiento se renueva. Se omiten las invitaciones respondidas o revocadas.
    """
    project = await _expert_project(project_id, projects, user)
    _require_not_closed(project)
    invitations = {i.id: i for i in await projects.list_invitations(project_id)}
    if body.invitation_ids is None:
        ids = [i.id for i in invitations.values() if i.usado_at is None and i.revocado_at is None]
    else:
        ids = list(dict.fromkeys(body.invitation_ids))
    reenviadas: list[InvitationLinkOut] = []
    omitidas: list[ResendSkipped] = []
    for inv_id in sorted(ids):
        inv = invitations.get(inv_id)
        if inv is None:
            omitidas.append(ResendSkipped(invitation_id=inv_id, detail="no existe"))
            continue
        if inv.usado_at or inv.revocado_at:
            omitidas.append(ResendSkipped(invitation_id=inv_id, detail=f"está {inv.state()}"))
            continue
        try:
            inv, token = await projects.regenerate_invitation(
                project_id, inv_id, timedelta(days=settings.invitation_ttl_days)
            )
        except (InvitationNotFoundError, InvitationUnavailableError) as e:
            omitidas.append(ResendSkipped(invitation_id=inv_id, detail=str(e)))
            continue
        reenviadas.append(await _deliver(inv, token, project, settings, request, projects))
    return ResendResponse(reenviadas=reenviadas, omitidas=omitidas)


@router.post("/{project_id}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    project_id: int, invitation_id: int, user: CurrentUser, projects: Projects
) -> InvitationOut:
    await _expert_project(project_id, projects, user)
    try:
        inv = await projects.revoke_invitation(project_id, invitation_id)
    except InvitationNotFoundError:
        raise _not_found_invitation(invitation_id)
    except InvitationUnavailableError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return InvitationOut.from_invitation(inv)


@router.post("/{project_id}/invitations/{invitation_id}/regenerate", response_model=InvitationLinkOut)
async def regenerate_invitation(
    project_id: int,
    invitation_id: int,
    request: Request,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Projects,
) -> InvitationLinkOut:
    """Nuevo enlace para una invitación no usada; el anterior deja de funcionar (RF-13)."""
    project = await _expert_project(project_id, projects, user)
    _require_not_closed(project)
    try:
        inv, token = await projects.regenerate_invitation(
            project_id, invitation_id, timedelta(days=settings.invitation_ttl_days)
        )
    except InvitationNotFoundError:
        raise _not_found_invitation(invitation_id)
    except InvitationUnavailableError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return await _deliver(inv, token, project, settings, request, projects)


@router.delete("/{project_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invitation(
    project_id: int, invitation_id: int, user: CurrentUser, projects: Projects
) -> Response:
    """Elimina a un experto que no ha enviado input (RF-51). A los demás se los excluye."""
    await _expert_project(project_id, projects, user)
    try:
        await projects.delete_invitation(project_id, invitation_id)
    except InvitationNotFoundError:
        raise _not_found_invitation(invitation_id)
    except InvitationHasInputError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- inputs de expertos ----------------------------------------------------------------------


def _input_out(item: ExpertInput, inv: Invitation | None, jobs: list[JobRecord]) -> InputOut:
    return InputOut(
        id=item.id,
        invitation_id=item.invitation_id,
        email=inv.email if inv else None,
        nombre=inv.nombre if inv else None,
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
        editable_hasta=item.editable_hasta,
        editable=item.editable(),
        excluido=item.excluido,
        calculos=[
            ComputationOut(
                job_id=j.job_id, status=j.status, node=j.node, created_at=j.created_at,
                thr=(j.params or {}).get("thr"), maxorder=(j.params or {}).get("maxorder"),
                input_version=j.input_version or 1,
                desactualizado=(j.input_version or 1) < item.version,
            )
            for j in jobs
        ],
    )


@router.get("/{project_id}/inputs", response_model=list[InputOut])
async def list_inputs(
    project_id: int,
    user: CurrentUser,
    projects: Projects,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> list[InputOut]:
    await _expert_project(project_id, projects, user)
    invitations = {i.id: i for i in await projects.list_invitations(project_id)}
    jobs_by_input: dict[int, list[JobRecord]] = {}
    for job in await store.list_by_project(project_id):
        if job.input_id is not None:
            jobs_by_input.setdefault(job.input_id, []).append(job)
    return [
        _input_out(item, invitations.get(item.invitation_id), jobs_by_input.get(item.id, []))
        for item in await projects.list_inputs(project_id)
    ]


@router.get("/{project_id}/inputs/{input_id}/versions", response_model=list[InputVersionOut])
async def list_input_versions(
    project_id: int, input_id: int, user: CurrentUser, projects: Projects
) -> list[InputVersionOut]:
    """Versiones del input, de la más reciente a la más antigua (RF-53)."""
    await _expert_project(project_id, projects, user)
    try:
        versions = await projects.list_input_versions(project_id, input_id)
    except InputNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Input {input_id} no existe")
    return [InputVersionOut(version=v.version, created_at=v.created_at) for v in versions]


@router.patch("/{project_id}/inputs/{input_id}", response_model=InputOut)
async def update_input(
    project_id: int,
    input_id: int,
    body: InputUpdate,
    user: CurrentUser,
    projects: Projects,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> InputOut:
    """Excluye o reincluye un input de los análisis conjuntos sin borrarlo (RF-20)."""
    await _expert_project(project_id, projects, user)
    try:
        item = await projects.set_input_excluded(project_id, input_id, body.excluido)
    except InputNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Input {input_id} no existe")
    inv = await projects.get_invitation(project_id, item.invitation_id)
    jobs = [j for j in await store.list_by_project(project_id) if j.input_id == item.id]
    return _input_out(item, inv, jobs)


@router.post("/{project_id}/compute", response_model=ComputeResponse)
async def compute_inputs(
    project_id: int,
    body: ComputeRequest,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Projects,
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> ComputeResponse:
    """Lanza un cálculo por experto con el THR y el orden máximo indicados (RF-21).

    Cada input puede tener varios cálculos con distintos parámetros; cada uno usa la versión
    vigente del input y la registra (RF-53). Si un lanzamiento falla, los demás siguen y el
    error se informa en `errores` (RF-23: se reintenta lanzando de nuevo).
    """
    project = await _expert_project(project_id, projects, user)
    inputs = await projects.list_inputs(project_id)
    if body.input_ids is None:
        selected = [i for i in inputs if not i.excluido]
    else:
        by_id = {i.id: i for i in inputs}
        unknown = [i for i in body.input_ids if i not in by_id]
        if unknown:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Inputs inexistentes: {', '.join(map(str, unknown))}"
            )
        selected = [by_id[i] for i in dict.fromkeys(body.input_ids)]
    if not selected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay inputs para calcular")

    lanzados: list[LaunchedOut] = []
    errores: list[LaunchErrorOut] = []
    for item in selected:
        try:
            job_id, node = await launch_expert_computation(
                project=project, item=item, thr=body.thr, maxorder=body.maxorder, nodo=body.nodo,
                pool=pool, selector=selector, store=store, settings=settings, user_id=user.id,
            )
        except HTTPException as e:
            logger.warning("No se pudo lanzar el cálculo del input %s: %s", item.id, e.detail)
            errores.append(LaunchErrorOut(input_id=item.id, detail=str(e.detail)))
            continue
        lanzados.append(LaunchedOut(input_id=item.id, job_id=job_id, node=node))
    return ComputeResponse(lanzados=lanzados, errores=errores)
