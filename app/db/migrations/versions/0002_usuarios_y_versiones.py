"""Usuarios (superadmin y admins), co-admins de proyectos y versiones de los inputs de expertos.

- `users` y `project_admins` (RF-01, RF-48, RF-49).
- `projects.owner` (texto) pasa a `projects.owner_id`. Los proyectos existentes quedan sin dueño
  y se asignan al superadmin cuando se crea o inicia sesión (`UserStore.ensure_superadmin`).
- `expert_invitations.nombre` (RF-10).
- `expert_inputs.version`, `updated_at` y `editable_hasta`, más `expert_input_versions` con la
  versión 1 de cada input existente (RF-52, RF-53). Los inputs anteriores no quedan editables.
- `jobs.input_version` y `jobs.user_id` (versión usada y quién lanzó el cálculo).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.columns import JSONType, UTCDateTime

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("nombre", sa.Text, nullable=False, server_default=""),
        sa.Column("rol", sa.String(20), nullable=False),
        sa.Column("password_hash", sa.Text),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("activacion_token_hash", sa.String(64), unique=True),
        sa.Column("activacion_expira_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
    )

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer))
        batch.create_foreign_key("fk_projects_owner_id_users", "users", ["owner_id"], ["id"])
        batch.drop_column("owner")
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "project_admins",
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", UTCDateTime, nullable=False),
    )

    op.add_column("expert_invitations", sa.Column("nombre", sa.Text))

    op.add_column(
        "expert_inputs", sa.Column("version", sa.Integer, nullable=False, server_default="1")
    )
    op.add_column("expert_inputs", sa.Column("updated_at", UTCDateTime))
    op.add_column("expert_inputs", sa.Column("editable_hasta", UTCDateTime))
    op.execute("UPDATE expert_inputs SET updated_at = created_at")

    op.create_table(
        "expert_input_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("input_id", sa.Integer, sa.ForeignKey("expert_inputs.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("cc", JSONType, nullable=False),
        sa.Column("ce", JSONType, nullable=False),
        sa.Column("ee", JSONType, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("input_id", "version", name="uq_expert_input_versions_input_version"),
    )
    op.create_index("ix_expert_input_versions_input_id", "expert_input_versions", ["input_id"])
    op.execute(
        "INSERT INTO expert_input_versions (input_id, version, cc, ce, ee, created_at) "
        "SELECT id, 1, cc, ce, ee, created_at FROM expert_inputs"
    )

    op.add_column("jobs", sa.Column("input_version", sa.Integer))
    op.add_column("jobs", sa.Column("user_id", sa.Integer))
    op.execute("UPDATE jobs SET input_version = 1 WHERE input_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("jobs", "user_id")
    op.drop_column("jobs", "input_version")
    op.drop_index("ix_expert_input_versions_input_id", "expert_input_versions")
    op.drop_table("expert_input_versions")
    with op.batch_alter_table("expert_inputs") as batch:
        batch.drop_column("editable_hasta")
        batch.drop_column("updated_at")
        batch.drop_column("version")
    with op.batch_alter_table("expert_invitations") as batch:
        batch.drop_column("nombre")
    op.drop_table("project_admins")
    op.drop_index("ix_projects_owner_id", "projects")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("fk_projects_owner_id_users", type_="foreignkey")
        batch.add_column(sa.Column("owner", sa.Text))
        batch.drop_column("owner_id")
    op.drop_table("users")
