"""Proyectos (expertos | simulación), invitaciones a expertos e inputs, persistidos en SQLite.

Usa la misma base que `JobStore`: los cálculos de un proyecto son filas de `jobs` con
`project_id` (y `input_id` cuando los originó el input de un experto).
"""

import asyncio
import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.services.job_store import _ensure_schema as _ensure_jobs_schema
from app.services.job_store import _parse_dt

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


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return _parse_dt(value) if value else None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    _ensure_jobs_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT NOT NULL DEFAULT '',
            tipo TEXT NOT NULL,
            estado TEXT NOT NULL,
            owner TEXT,
            causas TEXT,
            efectos TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expert_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            email TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expira_at TEXT NOT NULL,
            usado_at TEXT,
            revocado_at TEXT,
            enviado_at TEXT,
            envio_error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expert_inputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            invitation_id INTEGER NOT NULL UNIQUE REFERENCES expert_invitations(id),
            cc TEXT NOT NULL,
            ce TEXT NOT NULL,
            ee TEXT NOT NULL,
            excluido INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _migrate_orphan_maxmin_jobs(conn: sqlite3.Connection, owner: str | None) -> None:
    """RF-07: los jobs MaxMin anteriores a los proyectos pasan a un proyecto de simulación."""
    row = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind='maxmin' AND project_id IS NULL"
    ).fetchone()
    if not row[0]:
        return
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO projects (nombre, descripcion, tipo, estado, owner, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            MIGRATED_PROJECT_NAME,
            "Cálculos MaxMin lanzados antes de que existieran los proyectos.",
            "simulacion", "abierto", owner, now, now,
        ),
    )
    conn.execute(
        "UPDATE jobs SET project_id=? WHERE kind='maxmin' AND project_id IS NULL", (cur.lastrowid,)
    )
    conn.commit()


class ProjectStore:
    def __init__(self, db_path: str | None = None, migration_owner: str | None = None) -> None:
        self._db_path = db_path  # si None, se resuelve lazy desde Settings (igual que JobStore)
        self._migration_owner = migration_owner
        self._lock = asyncio.Lock()
        self._migrated = False

    def _resolve_db_path(self) -> str:
        if self._db_path is not None:
            return self._db_path
        try:
            from app.core.config import get_settings

            return get_settings().jobs_db_path or "data/jobs.db"
        except Exception:  # noqa: BLE001  (sin .env: valor por defecto, igual que JobStore)
            return "data/jobs.db"

    def _connect(self) -> sqlite3.Connection:
        db_path = self._resolve_db_path()
        if db_path == ":memory:":
            conn = sqlite3.connect("file:memdb1?mode=memory&cache=shared", uri=True, check_same_thread=False)
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        if not self._migrated:
            _migrate_orphan_maxmin_jobs(conn, self._migration_owner)
            self._migrated = True
        return conn

    async def _run(self, fn):
        async with self._lock:
            conn = self._connect()
            try:
                return fn(conn)
            finally:
                conn.close()

    # --- proyectos -------------------------------------------------------------------------

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            nombre=row["nombre"],
            descripcion=row["descripcion"] or "",
            tipo=row["tipo"],
            estado=row["estado"],
            owner=row["owner"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            causas=json.loads(row["causas"]) if row["causas"] else [],
            efectos=json.loads(row["efectos"]) if row["efectos"] else [],
        )

    @staticmethod
    def _get_project(conn: sqlite3.Connection, project_id: int) -> Project:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
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
        now = datetime.now(UTC).isoformat()

        def fn(conn: sqlite3.Connection) -> Project:
            cur = conn.execute(
                "INSERT INTO projects (nombre, descripcion, tipo, estado, owner, causas, efectos, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    nombre, descripcion, tipo, INITIAL_STATE[tipo], owner,
                    json.dumps(causas or [], ensure_ascii=False),
                    json.dumps(efectos or [], ensure_ascii=False),
                    now, now,
                ),
            )
            conn.commit()
            return self._get_project(conn, cur.lastrowid)

        return await self._run(fn)

    async def get_project(self, project_id: int) -> Project:
        return await self._run(lambda conn: self._get_project(conn, project_id))

    async def list_projects(self) -> list[Project]:
        def fn(conn: sqlite3.Connection) -> list[Project]:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC, id DESC").fetchall()
            return [self._row_to_project(r) for r in rows]

        return await self._run(fn)

    async def project_counts(self) -> dict[int, dict[str, int]]:
        """Conteos por proyecto para la lista: invitaciones, inputs y cálculos."""

        def fn(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
            counts: dict[int, dict[str, int]] = {}
            for key, sql in (
                ("invitaciones", "SELECT project_id, COUNT(*) FROM expert_invitations GROUP BY project_id"),
                ("inputs", "SELECT project_id, COUNT(*) FROM expert_inputs GROUP BY project_id"),
                ("calculos", "SELECT project_id, COUNT(*) FROM jobs WHERE project_id IS NOT NULL GROUP BY project_id"),
            ):
                for pid, n in conn.execute(sql):
                    counts.setdefault(pid, {})[key] = n
            return counts

        return await self._run(fn)

    async def update_project(self, project_id: int, **fields) -> Project:
        """Actualiza los campos dados (nombre, descripcion, causas, efectos, estado)."""
        allowed = {"nombre", "descripcion", "causas", "efectos", "estado"}
        values = {k: v for k, v in fields.items() if k in allowed}

        def fn(conn: sqlite3.Connection) -> Project:
            self._get_project(conn, project_id)
            if values:
                sets, params = [], []
                for key, value in values.items():
                    if key in ("causas", "efectos"):
                        value = json.dumps(value, ensure_ascii=False)
                    sets.append(f"{key}=?")
                    params.append(value)
                sets.append("updated_at=?")
                params.append(datetime.now(UTC).isoformat())
                conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", (*params, project_id))
                conn.commit()
            return self._get_project(conn, project_id)

        return await self._run(fn)

    async def delete_project(self, project_id: int) -> None:
        """Borra el proyecto y sus invitaciones. Quien llama verifica que no tenga inputs ni cálculos."""

        def fn(conn: sqlite3.Connection) -> None:
            self._get_project(conn, project_id)
            conn.execute("DELETE FROM expert_invitations WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            conn.commit()

        await self._run(fn)

    # --- invitaciones ----------------------------------------------------------------------

    @staticmethod
    def _row_to_invitation(row: sqlite3.Row) -> Invitation:
        return Invitation(
            id=row["id"],
            project_id=row["project_id"],
            email=row["email"],
            expira_at=_parse_dt(row["expira_at"]),
            created_at=_parse_dt(row["created_at"]),
            usado_at=_dt(row["usado_at"]),
            revocado_at=_dt(row["revocado_at"]),
            enviado_at=_dt(row["enviado_at"]),
            envio_error=row["envio_error"],
        )

    @staticmethod
    def _get_invitation(conn: sqlite3.Connection, project_id: int, invitation_id: int) -> Invitation:
        row = conn.execute(
            "SELECT * FROM expert_invitations WHERE id=? AND project_id=?", (invitation_id, project_id)
        ).fetchone()
        if row is None:
            raise InvitationNotFoundError(f"Invitación {invitation_id} no existe")
        return ProjectStore._row_to_invitation(row)

    async def create_invitation(self, project_id: int, email: str, ttl: timedelta) -> tuple[Invitation, str]:
        """Crea la invitación y devuelve el token en claro, que no se vuelve a poder leer."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        def fn(conn: sqlite3.Connection) -> Invitation:
            self._get_project(conn, project_id)
            cur = conn.execute(
                "INSERT INTO expert_invitations (project_id, email, token_hash, expira_at, created_at) "
                "VALUES (?,?,?,?,?)",
                (project_id, email, hash_token(token), _iso(now + ttl), _iso(now)),
            )
            conn.commit()
            return self._get_invitation(conn, project_id, cur.lastrowid)

        return await self._run(fn), token

    async def list_invitations(self, project_id: int) -> list[Invitation]:
        def fn(conn: sqlite3.Connection) -> list[Invitation]:
            rows = conn.execute(
                "SELECT * FROM expert_invitations WHERE project_id=? ORDER BY id DESC", (project_id,)
            ).fetchall()
            return [self._row_to_invitation(r) for r in rows]

        return await self._run(fn)

    async def get_invitation(self, project_id: int, invitation_id: int) -> Invitation:
        return await self._run(lambda conn: self._get_invitation(conn, project_id, invitation_id))

    async def revoke_invitation(self, project_id: int, invitation_id: int) -> Invitation:
        def fn(conn: sqlite3.Connection) -> Invitation:
            inv = self._get_invitation(conn, project_id, invitation_id)
            if inv.usado_at:
                raise InvitationUnavailableError("La invitación ya fue usada")
            conn.execute(
                "UPDATE expert_invitations SET revocado_at=COALESCE(revocado_at, ?) WHERE id=?",
                (_iso(datetime.now(UTC)), invitation_id),
            )
            conn.commit()
            return self._get_invitation(conn, project_id, invitation_id)

        return await self._run(fn)

    async def regenerate_invitation(
        self, project_id: int, invitation_id: int, ttl: timedelta
    ) -> tuple[Invitation, str]:
        """Nuevo token y nueva vigencia; el token anterior deja de servir (RF-13)."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        def fn(conn: sqlite3.Connection) -> Invitation:
            inv = self._get_invitation(conn, project_id, invitation_id)
            if inv.usado_at:
                raise InvitationUnavailableError("La invitación ya fue usada")
            conn.execute(
                "UPDATE expert_invitations SET token_hash=?, expira_at=?, revocado_at=NULL, "
                "enviado_at=NULL, envio_error=NULL WHERE id=?",
                (hash_token(token), _iso(now + ttl), invitation_id),
            )
            conn.commit()
            return self._get_invitation(conn, project_id, invitation_id)

        return await self._run(fn), token

    async def set_delivery(self, invitation_id: int, error: str | None) -> None:
        """Registra el resultado del envío por correo."""

        def fn(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE expert_invitations SET enviado_at=?, envio_error=? WHERE id=?",
                (None if error else _iso(datetime.now(UTC)), error, invitation_id),
            )
            conn.commit()

        await self._run(fn)

    async def find_invitation_by_token(self, token: str) -> Invitation:
        def fn(conn: sqlite3.Connection) -> Invitation:
            row = conn.execute(
                "SELECT * FROM expert_invitations WHERE token_hash=?", (hash_token(token or ""),)
            ).fetchone()
            if row is None:
                raise InvitationNotFoundError("Invitación no válida")
            return self._row_to_invitation(row)

        return await self._run(fn)

    # --- inputs de expertos ----------------------------------------------------------------

    @staticmethod
    def _row_to_input(row: sqlite3.Row) -> ExpertInput:
        return ExpertInput(
            id=row["id"],
            project_id=row["project_id"],
            invitation_id=row["invitation_id"],
            created_at=_parse_dt(row["created_at"]),
            cc=json.loads(row["cc"]),
            ce=json.loads(row["ce"]),
            ee=json.loads(row["ee"]),
            excluido=bool(row["excluido"]),
        )

    @staticmethod
    def _get_input(conn: sqlite3.Connection, project_id: int, input_id: int) -> ExpertInput:
        row = conn.execute(
            "SELECT * FROM expert_inputs WHERE id=? AND project_id=?", (input_id, project_id)
        ).fetchone()
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

        def fn(conn: sqlite3.Connection) -> ExpertInput:
            cur = conn.execute(
                "UPDATE expert_invitations SET usado_at=? WHERE token_hash=? AND usado_at IS NULL "
                "AND revocado_at IS NULL AND expira_at > ?",
                (_iso(now), hash_token(token), _iso(now)),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise InvitationUnavailableError("La invitación ya no está disponible")
            inv = conn.execute(
                "SELECT id, project_id FROM expert_invitations WHERE token_hash=?", (hash_token(token),)
            ).fetchone()
            cur = conn.execute(
                "INSERT INTO expert_inputs (project_id, invitation_id, cc, ce, ee, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (inv["project_id"], inv["id"], json.dumps(cc), json.dumps(ce), json.dumps(ee), _iso(now)),
            )
            conn.commit()
            return self._get_input(conn, inv["project_id"], cur.lastrowid)

        return await self._run(fn)

    async def list_inputs(self, project_id: int) -> list[ExpertInput]:
        def fn(conn: sqlite3.Connection) -> list[ExpertInput]:
            rows = conn.execute(
                "SELECT * FROM expert_inputs WHERE project_id=? ORDER BY id DESC", (project_id,)
            ).fetchall()
            return [self._row_to_input(r) for r in rows]

        return await self._run(fn)

    async def get_input(self, project_id: int, input_id: int) -> ExpertInput:
        return await self._run(lambda conn: self._get_input(conn, project_id, input_id))

    async def set_input_excluded(self, project_id: int, input_id: int, excluido: bool) -> ExpertInput:
        def fn(conn: sqlite3.Connection) -> ExpertInput:
            self._get_input(conn, project_id, input_id)
            conn.execute("UPDATE expert_inputs SET excluido=? WHERE id=?", (int(excluido), input_id))
            conn.commit()
            return self._get_input(conn, project_id, input_id)

        return await self._run(fn)

    async def count_inputs(self, project_id: int) -> int:
        def fn(conn: sqlite3.Connection) -> int:
            return conn.execute(
                "SELECT COUNT(*) FROM expert_inputs WHERE project_id=?", (project_id,)
            ).fetchone()[0]

        return await self._run(fn)


project_store = ProjectStore()
