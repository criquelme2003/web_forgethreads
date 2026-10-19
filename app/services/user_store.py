"""Administradores de la aplicación: superadmin y admins (RF-01, RF-02, RF-48).

El superadmin inicial sale de AUTH_USERNAME / AUTH_PASSWORD y se crea (o se actualiza) al iniciar
la app y en cada ingreso suyo. Los admins los crea el superadmin: el sistema genera un enlace de
activación de un solo uso con el que el admin elige su contraseña; regenerarlo sirve también para
restablecer una contraseña olvidada.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.engine import get_engine
from app.db.schema import project_admins, projects, users
from app.services.job_store import resolve_database
from app.services.project_store import hash_token

Role = Literal["superadmin", "admin"]
UserState = Literal["activo", "pendiente", "inactivo"]

MIN_PASSWORD_LENGTH = 10
_hasher = PasswordHasher()


class UserNotFoundError(Exception):
    pass


class UserExistsError(Exception):
    pass


class ActivationUnavailableError(Exception):
    """El enlace de activación no existe, expiró o ya se usó."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def normalize_email(email: str) -> str:
    return email.strip().lower()


@dataclass
class User:
    id: int
    email: str
    nombre: str
    rol: Role
    activo: bool
    created_at: datetime
    updated_at: datetime
    password_hash: str | None = None
    activacion_expira_at: datetime | None = None

    @property
    def is_superadmin(self) -> bool:
        return self.rol == "superadmin"

    @property
    def display(self) -> str:
        return self.nombre or self.email

    def state(self) -> UserState:
        if not self.activo:
            return "inactivo"
        return "activo" if self.password_hash else "pendiente"


@dataclass
class Reassignment:
    project_id: int
    nombre: str
    new_owner_id: int


class UserStore:
    def __init__(self, database: str | None = None) -> None:
        # URL de SQLAlchemy o ruta de un archivo SQLite; si es None, se resuelve desde Settings.
        self._database = database

    async def _engine(self) -> AsyncEngine:
        return await get_engine(resolve_database(self._database))

    async def _read(self, fn):
        engine = await self._engine()
        async with engine.connect() as conn:
            return await fn(conn)

    async def _write(self, fn):
        engine = await self._engine()
        async with engine.begin() as conn:
            return await fn(conn)

    @staticmethod
    def _row_to_user(row: Row) -> User:
        m = row._mapping
        return User(
            id=m["id"],
            email=m["email"],
            nombre=m["nombre"] or "",
            rol=m["rol"],
            activo=bool(m["activo"]),
            created_at=m["created_at"],
            updated_at=m["updated_at"],
            password_hash=m["password_hash"],
            activacion_expira_at=m["activacion_expira_at"],
        )

    @staticmethod
    async def _get(conn: AsyncConnection, user_id: int) -> User:
        row = (await conn.execute(select(users).where(users.c.id == user_id))).first()
        if row is None:
            raise UserNotFoundError(f"Usuario {user_id} no existe")
        return UserStore._row_to_user(row)

    async def get(self, user_id: int) -> User:
        return await self._read(lambda conn: self._get(conn, user_id))

    async def get_by_email(self, email: str) -> User:
        async def fn(conn: AsyncConnection) -> User:
            row = (
                await conn.execute(select(users).where(users.c.email == normalize_email(email)))
            ).first()
            if row is None:
                raise UserNotFoundError(f"Usuario {email} no existe")
            return self._row_to_user(row)

        return await self._read(fn)

    async def list_users(self) -> list[User]:
        async def fn(conn: AsyncConnection) -> list[User]:
            rows = await conn.execute(select(users).order_by(users.c.rol.desc(), users.c.id))
            return [self._row_to_user(r) for r in rows]

        return await self._read(fn)

    async def owned_counts(self) -> dict[int, int]:
        """Proyectos de los que es dueño cada usuario."""

        async def fn(conn: AsyncConnection) -> dict[int, int]:
            rows = await conn.execute(
                select(projects.c.owner_id, func.count())
                .where(projects.c.owner_id.is_not(None))
                .group_by(projects.c.owner_id)
            )
            return {uid: n for uid, n in rows}

        return await self._read(fn)

    async def ensure_superadmin(self, username: str, password: str) -> User:
        """Crea o actualiza el superadmin de AUTH_USERNAME / AUTH_PASSWORD (RF-02).

        Si la contraseña de la configuración cambió, se actualiza el hash. Los proyectos sin dueño
        (los anteriores a los usuarios) quedan a su nombre.
        """
        email = normalize_email(username)
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> User:
            row = (await conn.execute(select(users).where(users.c.email == email))).first()
            if row is None:
                user_id = await conn.scalar(
                    insert(users)
                    .values(
                        email=email, nombre="", rol="superadmin", activo=True,
                        password_hash=hash_password(password), created_at=now, updated_at=now,
                    )
                    .returning(users.c.id)
                )
            else:
                user = self._row_to_user(row)
                user_id = user.id
                changes: dict = {}
                if not user.is_superadmin:
                    changes["rol"] = "superadmin"
                if not user.activo:
                    changes["activo"] = True
                if not verify_password(user.password_hash, password):
                    changes["password_hash"] = hash_password(password)
                if changes:
                    await conn.execute(
                        update(users).where(users.c.id == user_id).values(**changes, updated_at=now)
                    )
            await conn.execute(
                update(projects).where(projects.c.owner_id.is_(None)).values(owner_id=user_id)
            )
            return await self._get(conn, user_id)

        return await self._write(fn)

    async def authenticate(self, email: str, password: str) -> User | None:
        """Usuario activo con esa contraseña, o None."""
        try:
            user = await self.get_by_email(email)
        except UserNotFoundError:
            verify_password(None, password)
            return None
        if not user.activo or not verify_password(user.password_hash, password):
            return None
        return user

    async def create_admin(self, email: str, nombre: str, ttl: timedelta) -> tuple[User, str]:
        """Crea un admin sin contraseña y devuelve el token de activación en claro (RF-48)."""
        email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> User:
            exists = await conn.scalar(select(users.c.id).where(users.c.email == email))
            if exists is not None:
                raise UserExistsError(f"Ya existe un usuario con el correo {email}")
            user_id = await conn.scalar(
                insert(users)
                .values(
                    email=email, nombre=nombre, rol="admin", activo=True,
                    activacion_token_hash=hash_token(token), activacion_expira_at=now + ttl,
                    created_at=now, updated_at=now,
                )
                .returning(users.c.id)
            )
            return await self._get(conn, user_id)

        return await self._write(fn), token

    async def regenerate_activation(self, user_id: int, ttl: timedelta) -> tuple[User, str]:
        """Nuevo enlace de activación (también para restablecer la contraseña); invalida el anterior."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> User:
            await self._get(conn, user_id)
            await conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(
                    activacion_token_hash=hash_token(token), activacion_expira_at=now + ttl,
                    updated_at=now,
                )
            )
            return await self._get(conn, user_id)

        return await self._write(fn), token

    async def find_by_activation_token(self, token: str) -> User:
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> User:
            row = (
                await conn.execute(
                    select(users).where(
                        users.c.activacion_token_hash == hash_token(token or ""),
                        users.c.activacion_expira_at > now,
                        users.c.activo,
                    )
                )
            ).first()
            if row is None:
                raise ActivationUnavailableError("El enlace no es válido o expiró")
            return self._row_to_user(row)

        return await self._read(fn)

    async def activate(self, token: str, password: str) -> User:
        """Fija la contraseña y consume el token en la misma operación."""
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> User:
            row = (
                await conn.execute(
                    update(users)
                    .where(
                        users.c.activacion_token_hash == hash_token(token or ""),
                        users.c.activacion_expira_at > now,
                        users.c.activo,
                    )
                    .values(
                        password_hash=hash_password(password), activacion_token_hash=None,
                        activacion_expira_at=None, updated_at=now,
                    )
                    .returning(users.c.id)
                )
            ).first()
            if row is None:
                raise ActivationUnavailableError("El enlace no es válido o expiró")
            return await self._get(conn, row.id)

        return await self._write(fn)

    async def set_active(self, user_id: int, activo: bool) -> User:
        async def fn(conn: AsyncConnection) -> User:
            await self._get(conn, user_id)
            values: dict = {"activo": activo, "updated_at": datetime.now(UTC)}
            if not activo:
                values.update(activacion_token_hash=None, activacion_expira_at=None)
            await conn.execute(update(users).where(users.c.id == user_id).values(**values))
            return await self._get(conn, user_id)

        return await self._write(fn)

    async def deactivate(
        self, user_id: int, reassign_to: int | None, fallback_owner: int
    ) -> tuple[User, list[Reassignment]]:
        """Desactiva al admin y reasigna sus proyectos (RF-04).

        Cada proyecto pasa a `reassign_to` si se indica; si no, a su co-admin activo más antiguo y,
        si no tiene, a `fallback_owner` (el superadmin que desactiva). El nuevo dueño deja de
        figurar como co-admin.
        """
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> tuple[User, list[Reassignment]]:
            await self._get(conn, user_id)
            owned = (
                await conn.execute(
                    select(projects.c.id, projects.c.nombre).where(projects.c.owner_id == user_id)
                )
            ).all()
            moves: list[Reassignment] = []
            for pid, nombre in owned:
                target = reassign_to
                if target is None:
                    target = await conn.scalar(
                        select(project_admins.c.user_id)
                        .join(users, users.c.id == project_admins.c.user_id)
                        .where(
                            project_admins.c.project_id == pid,
                            project_admins.c.user_id != user_id,
                            users.c.activo,
                        )
                        .order_by(project_admins.c.created_at, project_admins.c.user_id)
                        .limit(1)
                    )
                if target is None:
                    target = fallback_owner
                await conn.execute(
                    update(projects)
                    .where(projects.c.id == pid)
                    .values(owner_id=target, updated_at=now)
                )
                await conn.execute(
                    project_admins.delete().where(
                        project_admins.c.project_id == pid, project_admins.c.user_id == target
                    )
                )
                moves.append(Reassignment(project_id=pid, nombre=nombre, new_owner_id=target))
            await conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(activo=False, activacion_token_hash=None, activacion_expira_at=None, updated_at=now)
            )
            return await self._get(conn, user_id), moves

        return await self._write(fn)


user_store = UserStore()
