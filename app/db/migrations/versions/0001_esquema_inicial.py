"""Esquema inicial: proyectos, jobs, invitaciones e inputs de expertos.

Las bases SQLite creadas antes de Alembic ya tienen estas tablas: la migración solo crea lo que
falta (tablas, columnas agregadas a `jobs` con el tiempo e índices), así que sirve tanto para una
base vacía como para una existente.

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.columns import JSONType, UTCDateTime

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Columnas de `jobs` que no existían en su primera versión.
_JOBS_ADDED_COLUMNS = (
    ("kind", lambda: sa.Column("kind", sa.String(20), nullable=False, server_default="maxmin")),
    ("params", lambda: sa.Column("params", JSONType)),
    ("result", lambda: sa.Column("result", JSONType)),
    ("project_id", lambda: sa.Column("project_id", sa.Integer)),
    ("input_id", lambda: sa.Column("input_id", sa.Integer)),
)

_INDEXES = (
    ("ix_jobs_project_id", "jobs", "project_id"),
    ("ix_expert_invitations_project_id", "expert_invitations", "project_id"),
    ("ix_expert_inputs_project_id", "expert_inputs", "project_id"),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "projects" not in tables:
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("nombre", sa.Text, nullable=False),
            sa.Column("descripcion", sa.Text, nullable=False, server_default=""),
            sa.Column("tipo", sa.String(20), nullable=False),
            sa.Column("estado", sa.String(20), nullable=False),
            sa.Column("owner", sa.Text),
            sa.Column("causas", JSONType),
            sa.Column("efectos", JSONType),
            sa.Column("created_at", UTCDateTime, nullable=False),
            sa.Column("updated_at", UTCDateTime, nullable=False),
        )

    if "jobs" not in tables:
        op.create_table(
            "jobs",
            sa.Column("job_id", sa.Text, primary_key=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("node", sa.Text, nullable=False),
            sa.Column("token", sa.Text, nullable=False),
            sa.Column("effective_order", sa.Integer),
            sa.Column("computation_time_s", sa.Float),
            sa.Column("logs", sa.Text),
            sa.Column("created_at", UTCDateTime, nullable=False),
            sa.Column("updated_at", UTCDateTime, nullable=False),
            *(make() for _, make in _JOBS_ADDED_COLUMNS),
        )
    else:
        existing = {c["name"] for c in inspector.get_columns("jobs")}
        for name, make in _JOBS_ADDED_COLUMNS:
            if name not in existing:
                op.add_column("jobs", make())

    if "expert_invitations" not in tables:
        op.create_table(
            "expert_invitations",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("email", sa.Text, nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expira_at", UTCDateTime, nullable=False),
            sa.Column("usado_at", UTCDateTime),
            sa.Column("revocado_at", UTCDateTime),
            sa.Column("enviado_at", UTCDateTime),
            sa.Column("envio_error", sa.Text),
            sa.Column("created_at", UTCDateTime, nullable=False),
        )

    if "expert_inputs" not in tables:
        op.create_table(
            "expert_inputs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "invitation_id",
                sa.Integer,
                sa.ForeignKey("expert_invitations.id"),
                nullable=False,
                unique=True,
            ),
            sa.Column("cc", JSONType, nullable=False),
            sa.Column("ce", JSONType, nullable=False),
            sa.Column("ee", JSONType, nullable=False),
            sa.Column("excluido", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("created_at", UTCDateTime, nullable=False),
        )

    inspector = sa.inspect(op.get_bind())
    for name, table, column in _INDEXES:
        if name not in {ix["name"] for ix in inspector.get_indexes(table)}:
            op.create_index(name, table, [column])


def downgrade() -> None:
    for table in ("expert_inputs", "expert_invitations", "jobs", "projects"):
        op.drop_table(table)
