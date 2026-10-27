"""Publicaciones del blog (RF-56 a RF-58): borrador editable y última versión publicada.

El borrador son las secciones de texto (`bloques`, Markdown), las figuras (`figuras`: cálculos
con su vista y sus permisos de publicación y descarga), `palabras_clave` y `autores`.
`publicado` es la instantánea que ve el público, con los datos de las figuras ya resueltos, de
modo que no depende del cluster ni cambia hasta que se vuelve a publicar.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.columns import JSONType, UTCDateTime

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("titulo", sa.Text, nullable=False),
        sa.Column("resumen", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "project_id", sa.Integer, sa.ForeignKey("projects.id", name="fk_posts_project_id_projects"),
        ),
        sa.Column("autor_id", sa.Integer, sa.ForeignKey("users.id", name="fk_posts_autor_id_users")),
        sa.Column("bloques", JSONType, nullable=False),
        sa.Column("figuras", JSONType),
        sa.Column("palabras_clave", JSONType),
        sa.Column("autores", sa.Text, nullable=False, server_default=""),
        sa.Column("anonimizar", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("publicado", JSONType),
        sa.Column("publicado_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
    )
    op.create_index("ix_posts_project_id", "posts", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_posts_project_id", "posts")
    op.drop_table("posts")
