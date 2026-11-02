"""Publicaciones en PDF: `posts.formato`, `posts.pdf_id` y la tabla `post_pdfs`.

Una publicación con formato `pdf` es un documento subido (título, autores, resumen y palabras
clave aparte) que el blog solo muestra. Los PDF se guardan en la base para que queden en el
respaldo diario junto con el resto de la publicación.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.columns import UTCDateTime

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch:
        batch.add_column(sa.Column("formato", sa.String(20), nullable=False, server_default="articulo"))
        batch.add_column(sa.Column("pdf_id", sa.Integer))
    op.create_table(
        "post_pdfs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "post_id", sa.Integer, sa.ForeignKey("posts.id", name="fk_post_pdfs_post_id_posts"),
            nullable=False,
        ),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("tamano", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("contenido", sa.LargeBinary, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
    )
    op.create_index("ix_post_pdfs_post_id", "post_pdfs", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_post_pdfs_post_id", "post_pdfs")
    op.drop_table("post_pdfs")
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("pdf_id")
        batch.drop_column("formato")
