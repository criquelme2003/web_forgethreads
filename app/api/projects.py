"""Proyectos de expertos y de simulación: CRUD, estados, invitaciones e inputs de expertos.

Por ahora hay un único usuario (el de `.env`); el dueño se guarda en `owner` para cuando
existan los roles (RF-01..RF-03), pero todavía no se filtra por él.
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
    require_user,
)
from app.api.fe import launch_fe_job
from app.api.job_callback import JobStatusResponse
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
    InvitationNotFoundError,
    InvitationUnavailableError,
    Project,
    ProjectNotFoundError,
    ProjectStore,
)
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


class ProjectOut(BaseModel):
    id: int
    nombre: str
    descripcion: str
    tipo: str
    estado: str
    owner: str | None
    created_at: datetime
    updated_at: datetime
    causas: list[str]
    efectos: list[str]
    transiciones: list[str]  # estados a los que se puede pasar desde el actual
    conteos: dict[str, int] = {}

    @classmethod
    def from_project(cls, p: Project, conteos: dict[str, int] | None = None) -> "ProjectOut":
        return cls(
            **{k: getattr(p, k) for k in (
                "id", "nombre", "descripcion", "tipo", "estado", "owner", "created_at",
                "updated_at", "causas", "efectos",
            )},
            transiciones=sorted(TRANSITIONS[p.tipo].get(p.estado, set())),
            conteos=conteos or {},
        )


class InvitationCreate(BaseModel):
    email: EmailStr


class InvitationOut(BaseModel):
    id: int
    email: str
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
            id=inv.id, email=inv.email, estado=inv.state(), created_at=inv.created_at,
            expira_at=inv.expira_at, usado_at=inv.usado_at, revocado_at=inv.revocado_at,
            enviado_at=inv.enviado_at, envio_error=inv.envio_error,
        )


class InvitationLinkOut(InvitationOut):
    """Respuesta al crear o regenerar: el enlace solo se puede ver en este momento."""

    link: str
    envio: Literal["manual", "smtp"]


class ComputationOut(BaseModel):
    job_id: str
    status: str
    node: str
    created_at: datetime
    thr: float | None = None
    maxorder: int | None = None


class InputOut(BaseModel):
    id: int
    invitation_id: int
    email: str | None
    created_at: datetime
    excluido: bool
    calculos: list[ComputationOut]  # del más reciente al más antiguo


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


async def get_project_or_404(project_id: int, store: ProjectStore) -> Project:
    try:
        return await store.get_project(project_id)
    except ProjectNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Proyecto {project_id} no existe")


def _require_expertos(project: Project) -> None:
    if project.tipo != "expertos":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El proyecto no es de expertos")


def invitation_link(token: str, settings: Settings, request: Request) -> str:
    base = (settings.public_callback_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/front/invite/{token}"


async def _deliver(
    inv: Invitation, token: str, project: Project, settings: Settings, request: Request, store: ProjectStore
) -> InvitationLinkOut:
    """Envía la invitación por correo si está configurado SMTP; el enlace siempre se devuelve."""
    link = invitation_link(token, settings, request)
    if settings.smtp_enabled:
        msg = build_invitation_message(
            settings, inv.email, project.nombre, link, inv.expira_at.strftime("%Y-%m-%d %H:%M UTC")
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
) -> tuple[str, str]:
    """Lanza forgeffects sobre el input de un experto (RF-21). Lanza HTTPException si falla."""
    cc, ce, ee = input_matrices(item)
    return await launch_fe_job(
        pool=pool, store=store, selector=selector, settings=settings, nodo=nodo, cc=cc, ce=ce, ee=ee,
        causes=project.causas, effects=project.efectos, thr=thr, maxorder=maxorder,
        project_id=project.id, input_id=item.id,
    )


# --- proyectos -------------------------------------------------------------------------------


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> list[ProjectOut]:
    counts = await projects.project_counts()
    return [ProjectOut.from_project(p, counts.get(p.id)) for p in await projects.list_projects()]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> ProjectOut:
    expert = body.tipo == "expertos"
    project = await projects.create_project(
        nombre=body.nombre.strip(),
        descripcion=body.descripcion.strip(),
        tipo=body.tipo,
        owner=user,
        causas=body.causas if expert else None,
        efectos=body.efectos if expert else None,
    )
    return ProjectOut.from_project(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> ProjectOut:
    project = await get_project_or_404(project_id, projects)
    counts = await projects.project_counts()
    return ProjectOut.from_project(project, counts.get(project.id))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> ProjectOut:
    """Nombre y descripción se editan siempre; etiquetas y parámetros solo en borrador (RF-09)."""
    project = await get_project_or_404(project_id, projects)
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
    return ProjectOut.from_project(project)


@router.post("/{project_id}/estado", response_model=ProjectOut)
async def change_state(
    project_id: int,
    body: ProjectStateChange,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> ProjectOut:
    project = await get_project_or_404(project_id, projects)
    allowed = TRANSITIONS[project.tipo].get(project.estado, set())
    if body.estado not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No se puede pasar de {project.estado} a {body.estado}",
        )
    project = await projects.update_project(project_id, estado=body.estado)
    return ProjectOut.from_project(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> Response:
    """Solo se borran proyectos sin inputs ni cálculos, para no perder resultados."""
    await get_project_or_404(project_id, projects)
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
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> list[JobStatusResponse]:
    await get_project_or_404(project_id, projects)
    return [JobStatusResponse.from_record(r) for r in await store.list_by_project(project_id)]


# --- invitaciones ----------------------------------------------------------------------------


@router.get("/{project_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    project_id: int,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> list[InvitationOut]:
    _require_expertos(await get_project_or_404(project_id, projects))
    return [InvitationOut.from_invitation(i) for i in await projects.list_invitations(project_id)]


@router.post(
    "/{project_id}/invitations", response_model=InvitationLinkOut, status_code=status.HTTP_201_CREATED
)
async def create_invitation(
    project_id: int,
    body: InvitationCreate,
    request: Request,
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> InvitationLinkOut:
    """Crea la invitación (RF-10). El enlace se muestra solo en esta respuesta; si se pierde, se regenera."""
    project = await get_project_or_404(project_id, projects)
    _require_expertos(project)
    if project.estado == "cerrado":
        raise HTTPException(status.HTTP_409_CONFLICT, "El proyecto está cerrado")
    inv, token = await projects.create_invitation(
        project_id, str(body.email).strip().lower(), timedelta(days=settings.invitation_ttl_days)
    )
    return await _deliver(inv, token, project, settings, request, projects)


@router.post("/{project_id}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    project_id: int,
    invitation_id: int,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> InvitationOut:
    _require_expertos(await get_project_or_404(project_id, projects))
    try:
        inv = await projects.revoke_invitation(project_id, invitation_id)
    except InvitationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invitación {invitation_id} no existe")
    except InvitationUnavailableError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return InvitationOut.from_invitation(inv)


@router.post("/{project_id}/invitations/{invitation_id}/regenerate", response_model=InvitationLinkOut)
async def regenerate_invitation(
    project_id: int,
    invitation_id: int,
    request: Request,
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
) -> InvitationLinkOut:
    """Nuevo enlace para una invitación no usada; el anterior deja de funcionar (RF-13)."""
    project = await get_project_or_404(project_id, projects)
    _require_expertos(project)
    if project.estado == "cerrado":
        raise HTTPException(status.HTTP_409_CONFLICT, "El proyecto está cerrado")
    try:
        inv, token = await projects.regenerate_invitation(
            project_id, invitation_id, timedelta(days=settings.invitation_ttl_days)
        )
    except InvitationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invitación {invitation_id} no existe")
    except InvitationUnavailableError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return await _deliver(inv, token, project, settings, request, projects)


# --- inputs de expertos ----------------------------------------------------------------------


def _input_out(item: ExpertInput, email: str | None, jobs: list[JobRecord]) -> InputOut:
    return InputOut(
        id=item.id,
        invitation_id=item.invitation_id,
        email=email,
        created_at=item.created_at,
        excluido=item.excluido,
        calculos=[
            ComputationOut(
                job_id=j.job_id, status=j.status, node=j.node, created_at=j.created_at,
                thr=(j.params or {}).get("thr"), maxorder=(j.params or {}).get("maxorder"),
            )
            for j in jobs
        ],
    )


@router.get("/{project_id}/inputs", response_model=list[InputOut])
async def list_inputs(
    project_id: int,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> list[InputOut]:
    _require_expertos(await get_project_or_404(project_id, projects))
    emails = {i.id: i.email for i in await projects.list_invitations(project_id)}
    jobs_by_input: dict[int, list[JobRecord]] = {}
    for job in await store.list_by_project(project_id):
        if job.input_id is not None:
            jobs_by_input.setdefault(job.input_id, []).append(job)
    return [
        _input_out(item, emails.get(item.invitation_id), jobs_by_input.get(item.id, []))
        for item in await projects.list_inputs(project_id)
    ]


@router.patch("/{project_id}/inputs/{input_id}", response_model=InputOut)
async def update_input(
    project_id: int,
    input_id: int,
    body: InputUpdate,
    user: Annotated[str, Depends(require_user)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> InputOut:
    """Excluye o reincluye un input de los análisis conjuntos sin borrarlo (RF-20)."""
    _require_expertos(await get_project_or_404(project_id, projects))
    try:
        item = await projects.set_input_excluded(project_id, input_id, body.excluido)
    except InputNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Input {input_id} no existe")
    inv = await projects.get_invitation(project_id, item.invitation_id)
    jobs = [j for j in await store.list_by_project(project_id) if j.input_id == item.id]
    return _input_out(item, inv.email, jobs)


@router.post("/{project_id}/compute", response_model=ComputeResponse)
async def compute_inputs(
    project_id: int,
    body: ComputeRequest,
    user: Annotated[str, Depends(require_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    projects: Annotated[ProjectStore, Depends(get_project_store)],
    store: Annotated[JobStore, Depends(get_job_store)],
    pool: Annotated[SSHConnectionPool, Depends(get_ssh_pool)],
    selector: Annotated[NodeSelector, Depends(get_node_selector)],
) -> ComputeResponse:
    """Lanza un cálculo por experto con el THR y el orden máximo indicados (RF-21).

    Cada input puede tener varios cálculos con distintos parámetros. Si un lanzamiento falla,
    los demás siguen y el error se informa en `errores` (RF-23: se reintenta lanzando de nuevo).
    """
    project = await get_project_or_404(project_id, projects)
    _require_expertos(project)
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
                pool=pool, selector=selector, store=store, settings=settings,
            )
        except HTTPException as e:
            logger.warning("No se pudo lanzar el cálculo del input %s: %s", item.id, e.detail)
            errores.append(LaunchErrorOut(input_id=item.id, detail=str(e.detail)))
            continue
        lanzados.append(LaunchedOut(input_id=item.id, job_id=job_id, node=node))
    return ComputeResponse(lanzados=lanzados, errores=errores)
