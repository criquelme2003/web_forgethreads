"""Proyectos (expertos | simulación), invitaciones a expertos e inputs.

Usa la misma base que `JobStore`: los cálculos de un proyecto son filas de `jobs` con
`project_id` (y `input_id` cuando los originó el input de un experto).
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.engine import get_engine
from app.db.schema import expert_inputs, expert_invitations, jobs, projects
from app.services.job_store import resolve_database

ProjectType = Literal["expertos", "simulacion"]
ProjectState = Literal["borrador", "abierto", "cerrado"]
InvitationState = Literal["pendiente", "usada", "revocada", "expirada"]

INITIAL_STATE: dict[str, ProjectState] = {"expertos": "borrador", "simulacion": "abierto"}
# Transiciones permitidas por tipo (RF-09). Reabrir un proyecto cerrado no vuelve a borrador:
# las etiquetas quedaron fijas al abrirlo.
TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "expertos": {"borrador": {"abierto"}, "abierto": {"cerrado"}, "cerrado": {"abierto"}},
    "simulacion": {"abierto": {"cerrado"}, "cerrado": {"abierto"}},
}
MIGRATED_PROJECT_NAME = "MaxMin (jobs anteriores)"


class ProjectNotFoundError(Exception):
    pass


class InvitationNotFoundError(Exception):
    pass


class InvitationUnavailableError(Exception):
    """La invitación no se puede usar: fue usada, revocada o expiró."""


class InputNotFoundError(Exception):
    pass


@dataclass
class Project:
    id: int
    nombre: str
    descripcion: str
    tipo: ProjectType
    estado: ProjectState
    owner: str | None
    created_at: datetime
    updated_at: datetime
    causas: list[str] = field(default_factory=list)
    efectos: list[str] = field(default_factory=list)


@dataclass
class Invitation:
    id: int
    project_id: int
    email: str
    expira_at: datetime
    created_at: datetime
    usado_at: datetime | None = None
    revocado_at: datetime | None = None
    enviado_at: datetime | None = None
    envio_error: str | None = None

    def state(self, now: datetime | None = None) -> InvitationState:
        if self.usado_at:
            return "usada"
        if self.revocado_at:
            return "revocada"
        if self.expira_at <= (now or datetime.now(UTC)):
            return "expirada"
        return "pendiente"


@dataclass
class ExpertInput:
    id: int
    project_id: int
    invitation_id: int
    created_at: datetime
    cc: list[list[float]] = field(repr=False, default_factory=list)
    ce: list[list[float]] = field(repr=False, default_factory=list)
    ee: list[list[float]] = field(repr=False, default_factory=list)
    excluido: bool = False


def hash_token(token: str) -> str:
    """Solo se guarda el hash del token de invitación (RF-10)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _migrate_orphan_maxmin_jobs(conn: AsyncConnection, owner: str | None) -> None:
    """RF-07: los jobs MaxMin anteriores a los proyectos pasan a un proyecto de simulación."""
    orphans = and_(jobs.c.kind == "maxmin", jobs.c.project_id.is_(None))
    count = await conn.scalar(select(func.count()).select_from(jobs).where(orphans))
    if not count:
        return
    now = datetime.now(UTC)
    project_id = await conn.scalar(
        insert(projects)
        .values(
            nombre=MIGRATED_PROJECT_NAME,
            descripcion="Cálculos MaxMin lanzados antes de que existieran los proyectos.",
            tipo="simulacion",
            estado="abierto",
            owner=owner,
            created_at=now,
            updated_at=now,
        )
        .returning(projects.c.id)
    )
    await conn.execute(update(jobs).where(orphans).values(project_id=project_id))


class ProjectStore:
    def __init__(self, database: str | None = None, migration_owner: str | None = None) -> None:
        # URL de SQLAlchemy o ruta de un archivo SQLite; si es None, se resuelve desde Settings.
        self._database = database
        self._migration_owner = migration_owner
        self._migrated = False

    async def _engine(self) -> AsyncEngine:
        engine = await get_engine(resolve_database(self._database))
        if not self._migrated:
            async with engine.begin() as conn:
                await _migrate_orphan_maxmin_jobs(conn, self._migration_owner)
            self._migrated = True
        return engine

    async def _read(self, fn):
        """Ejecuta `fn(conn)` en una conexión de solo lectura."""
        engine = await self._engine()
        async with engine.connect() as conn:
            return await fn(conn)

    async def _write(self, fn):
        """Ejecuta `fn(conn)` en una transacción: se confirma al terminar o se revierte si falla."""
        engine = await self._engine()
        async with engine.begin() as conn:
            return await fn(conn)

    # --- proyectos -------------------------------------------------------------------------

    @staticmethod
    def _row_to_project(row: Row) -> Project:
        m = row._mapping
        return Project(
            id=m["id"],
            nombre=m["nombre"],
            descripcion=m["descripcion"] or "",
            tipo=m["tipo"],
            estado=m["estado"],
            owner=m["owner"],
            created_at=m["created_at"],
            updated_at=m["updated_at"],
            causas=m["causas"] or [],
            efectos=m["efectos"] or [],
        )

    @staticmethod
    async def _get_project(conn: AsyncConnection, project_id: int) -> Project:
        row = (await conn.execute(select(projects).where(projects.c.id == project_id))).first()
        if row is None:
            raise ProjectNotFoundError(f"Proyecto {project_id} no existe")
        return ProjectStore._row_to_project(row)

    async def create_project(
        self,
        *,
        nombre: str,
        descripcion: str,
        tipo: ProjectType,
        owner: str | None,
        causas: list[str] | None = None,
        efectos: list[str] | None = None,
    ) -> Project:
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Project:
            project_id = await conn.scalar(
                insert(projects)
                .values(
                    nombre=nombre, descripcion=descripcion, tipo=tipo, estado=INITIAL_STATE[tipo],
                    owner=owner, causas=causas or [], efectos=efectos or [],
                    created_at=now, updated_at=now,
                )
                .returning(projects.c.id)
            )
            return await self._get_project(conn, project_id)

        return await self._write(fn)

    async def get_project(self, project_id: int) -> Project:
        return await self._read(lambda conn: self._get_project(conn, project_id))

    async def list_projects(self) -> list[Project]:
        async def fn(conn: AsyncConnection) -> list[Project]:
            rows = await conn.execute(
                select(projects).order_by(projects.c.created_at.desc(), projects.c.id.desc())
            )
            return [self._row_to_project(r) for r in rows]

        return await self._read(fn)

    async def project_counts(self) -> dict[int, dict[str, int]]:
        """Conteos por proyecto para la lista: invitaciones, inputs y cálculos."""

        async def fn(conn: AsyncConnection) -> dict[int, dict[str, int]]:
            counts: dict[int, dict[str, int]] = {}
            for key, column in (
                ("invitaciones", expert_invitations.c.project_id),
                ("inputs", expert_inputs.c.project_id),
                ("calculos", jobs.c.project_id),
            ):
                rows = await conn.execute(
                    select(column, func.count()).where(column.is_not(None)).group_by(column)
                )
                for pid, n in rows:
                    counts.setdefault(pid, {})[key] = n
            return counts

        return await self._read(fn)

    async def update_project(self, project_id: int, **fields) -> Project:
        """Actualiza los campos dados (nombre, descripcion, causas, efectos, estado)."""
        allowed = {"nombre", "descripcion", "causas", "efectos", "estado"}
        values = {k: v for k, v in fields.items() if k in allowed}

        async def fn(conn: AsyncConnection) -> Project:
            await self._get_project(conn, project_id)
            if values:
                await conn.execute(
                    update(projects)
                    .where(projects.c.id == project_id)
                    .values(**values, updated_at=datetime.now(UTC))
                )
            return await self._get_project(conn, project_id)

        return await self._write(fn)

    async def delete_project(self, project_id: int) -> None:
        """Borra el proyecto y sus invitaciones. Quien llama verifica que no tenga inputs ni cálculos."""

        async def fn(conn: AsyncConnection) -> None:
            await self._get_project(conn, project_id)
            await conn.execute(
                sql_delete(expert_invitations).where(expert_invitations.c.project_id == project_id)
            )
            await conn.execute(sql_delete(projects).where(projects.c.id == project_id))

        await self._write(fn)

    # --- invitaciones ----------------------------------------------------------------------

    @staticmethod
    def _row_to_invitation(row: Row) -> Invitation:
        m = row._mapping
        return Invitation(
            id=m["id"],
            project_id=m["project_id"],
            email=m["email"],
            expira_at=m["expira_at"],
            created_at=m["created_at"],
            usado_at=m["usado_at"],
            revocado_at=m["revocado_at"],
            enviado_at=m["enviado_at"],
            envio_error=m["envio_error"],
        )

    @staticmethod
    async def _get_invitation(conn: AsyncConnection, project_id: int, invitation_id: int) -> Invitation:
        row = (
            await conn.execute(
                select(expert_invitations).where(
                    expert_invitations.c.id == invitation_id,
                    expert_invitations.c.project_id == project_id,
                )
            )
        ).first()
        if row is None:
            raise InvitationNotFoundError(f"Invitación {invitation_id} no existe")
        return ProjectStore._row_to_invitation(row)

    async def create_invitation(self, project_id: int, email: str, ttl: timedelta) -> tuple[Invitation, str]:
        """Crea la invitación y devuelve el token en claro, que no se vuelve a poder leer."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Invitation:
            await self._get_project(conn, project_id)
            invitation_id = await conn.scalar(
                insert(expert_invitations)
                .values(
                    project_id=project_id, email=email, token_hash=hash_token(token),
                    expira_at=now + ttl, created_at=now,
                )
                .returning(expert_invitations.c.id)
            )
            return await self._get_invitation(conn, project_id, invitation_id)

        return await self._write(fn), token

    async def list_invitations(self, project_id: int) -> list[Invitation]:
        async def fn(conn: AsyncConnection) -> list[Invitation]:
            rows = await conn.execute(
                select(expert_invitations)
                .where(expert_invitations.c.project_id == project_id)
                .order_by(expert_invitations.c.id.desc())
            )
            return [self._row_to_invitation(r) for r in rows]

        return await self._read(fn)

    async def get_invitation(self, project_id: int, invitation_id: int) -> Invitation:
        return await self._read(lambda conn: self._get_invitation(conn, project_id, invitation_id))

    async def revoke_invitation(self, project_id: int, invitation_id: int) -> Invitation:
        async def fn(conn: AsyncConnection) -> Invitation:
            inv = await self._get_invitation(conn, project_id, invitation_id)
            if inv.usado_at:
                raise InvitationUnavailableError("La invitación ya fue usada")
            if inv.revocado_at is None:
                await conn.execute(
                    update(expert_invitations)
                    .where(expert_invitations.c.id == invitation_id)
                    .values(revocado_at=datetime.now(UTC))
                )
            return await self._get_invitation(conn, project_id, invitation_id)

        return await self._write(fn)

    async def regenerate_invitation(
        self, project_id: int, invitation_id: int, ttl: timedelta
    ) -> tuple[Invitation, str]:
        """Nuevo token y nueva vigencia; el token anterior deja de servir (RF-13)."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Invitation:
            inv = await self._get_invitation(conn, project_id, invitation_id)
            if inv.usado_at:
                raise InvitationUnavailableError("La invitación ya fue usada")
            await conn.execute(
                update(expert_invitations)
                .where(expert_invitations.c.id == invitation_id)
                .values(
                    token_hash=hash_token(token), expira_at=now + ttl, revocado_at=None,
                    enviado_at=None, envio_error=None,
                )
            )
            return await self._get_invitation(conn, project_id, invitation_id)

        return await self._write(fn), token

    async def set_delivery(self, invitation_id: int, error: str | None) -> None:
        """Registra el resultado del envío por correo."""

        async def fn(conn: AsyncConnection) -> None:
            await conn.execute(
                update(expert_invitations)
                .where(expert_invitations.c.id == invitation_id)
                .values(enviado_at=None if error else datetime.now(UTC), envio_error=error)
            )

        await self._write(fn)

    async def find_invitation_by_token(self, token: str) -> Invitation:
        async def fn(conn: AsyncConnection) -> Invitation:
            row = (
                await conn.execute(
                    select(expert_invitations).where(
                        expert_invitations.c.token_hash == hash_token(token or "")
                    )
                )
            ).first()
            if row is None:
                raise InvitationNotFoundError("Invitación no válida")
            return self._row_to_invitation(row)

        return await self._read(fn)

    # --- inputs de expertos ----------------------------------------------------------------

    @staticmethod
    def _row_to_input(row: Row) -> ExpertInput:
        m = row._mapping
        return ExpertInput(
            id=m["id"],
            project_id=m["project_id"],
            invitation_id=m["invitation_id"],
            created_at=m["created_at"],
            cc=m["cc"],
            ce=m["ce"],
            ee=m["ee"],
            excluido=bool(m["excluido"]),
        )

    @staticmethod
    async def _get_input(conn: AsyncConnection, project_id: int, input_id: int) -> ExpertInput:
        row = (
            await conn.execute(
                select(expert_inputs).where(
                    expert_inputs.c.id == input_id, expert_inputs.c.project_id == project_id
                )
            )
        ).first()
        if row is None:
            raise InputNotFoundError(f"Input {input_id} no existe")
        return ProjectStore._row_to_input(row)

    async def submit_input(
        self,
        token: str,
        cc: list[list[float]],
        ce: list[list[float]],
        ee: list[list[float]],
    ) -> ExpertInput:
        """Guarda el input y consume el token en la misma transacción (RF-11, RF-20).

        Lanza InvitationUnavailableError si el token ya se usó, se revocó o expiró entre la
        validación y la confirmación (dos envíos simultáneos: solo uno gana).
        """
        now = datetime.now(UTC)
        token_hash = hash_token(token)

        async def fn(conn: AsyncConnection) -> ExpertInput:
            # El UPDATE condicionado es atómico: de dos envíos simultáneos solo uno lo aplica.
            inv = (
                await conn.execute(
                    update(expert_invitations)
                    .where(
                        expert_invitations.c.token_hash == token_hash,
                        expert_invitations.c.usado_at.is_(None),
                        expert_invitations.c.revocado_at.is_(None),
                        expert_invitations.c.expira_at > now,
                    )
                    .values(usado_at=now)
                    .returning(expert_invitations.c.id, expert_invitations.c.project_id)
                )
            ).first()
            if inv is None:
                raise InvitationUnavailableError("La invitación ya no está disponible")
            input_id = await conn.scalar(
                insert(expert_inputs)
                .values(
                    project_id=inv.project_id, invitation_id=inv.id, cc=cc, ce=ce, ee=ee,
                    created_at=now,
                )
                .returning(expert_inputs.c.id)
            )
            return await self._get_input(conn, inv.project_id, input_id)

        return await self._write(fn)

    async def list_inputs(self, project_id: int) -> list[ExpertInput]:
        async def fn(conn: AsyncConnection) -> list[ExpertInput]:
            rows = await conn.execute(
                select(expert_inputs)
                .where(expert_inputs.c.project_id == project_id)
                .order_by(expert_inputs.c.id.desc())
            )
            return [self._row_to_input(r) for r in rows]

        return await self._read(fn)

    async def get_input(self, project_id: int, input_id: int) -> ExpertInput:
        return await self._read(lambda conn: self._get_input(conn, project_id, input_id))

    async def set_input_excluded(self, project_id: int, input_id: int, excluido: bool) -> ExpertInput:
        async def fn(conn: AsyncConnection) -> ExpertInput:
            await self._get_input(conn, project_id, input_id)
            await conn.execute(
                update(expert_inputs).where(expert_inputs.c.id == input_id).values(excluido=excluido)
            )
            return await self._get_input(conn, project_id, input_id)

        return await self._write(fn)

    async def count_inputs(self, project_id: int) -> int:
        async def fn(conn: AsyncConnection) -> int:
            return await conn.scalar(
                select(func.count())
                .select_from(expert_inputs)
                .where(expert_inputs.c.project_id == project_id)
            )

        return await self._read(fn)


project_store = ProjectStore()
