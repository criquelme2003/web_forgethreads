"""Tablas de la aplicación (SQLAlchemy Core).

Los cambios de esquema se hacen con una migración nueva en `app/db/migrations/versions/`;
este módulo describe el esquema vigente y lo usan los stores para armar las consultas.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    false,
)

from app.db.columns import JSONType, UTCDateTime

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", Text, nullable=False),
    Column("descripcion", Text, nullable=False, server_default=""),
    Column("tipo", String(20), nullable=False),
    Column("estado", String(20), nullable=False),
    Column("owner", Text),
    Column("causas", JSONType),
    Column("efectos", JSONType),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

# Un cálculo en el cluster (MaxMin o forgeffects). `token` autentica el callback del notifier.
jobs = Table(
    "jobs",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("status", String(20), nullable=False),
    Column("node", Text, nullable=False),
    Column("token", Text, nullable=False),
    Column("effective_order", Integer),
    Column("computation_time_s", Float),
    Column("logs", Text),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    Column("kind", String(20), nullable=False, server_default="maxmin"),
    Column("params", JSONType),
    Column("result", JSONType),
    Column("project_id", Integer, index=True),
    Column("input_id", Integer),
)

expert_invitations = Table(
    "expert_invitations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False, index=True),
    Column("email", Text, nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expira_at", UTCDateTime, nullable=False),
    Column("usado_at", UTCDateTime),
    Column("revocado_at", UTCDateTime),
    Column("enviado_at", UTCDateTime),
    Column("envio_error", Text),
    Column("created_at", UTCDateTime, nullable=False),
)

expert_inputs = Table(
    "expert_inputs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False, index=True),
    Column(
        "invitation_id", Integer, ForeignKey("expert_invitations.id"), nullable=False, unique=True
    ),
    Column("cc", JSONType, nullable=False),
    Column("ce", JSONType, nullable=False),
    Column("ee", JSONType, nullable=False),
    Column("excluido", Boolean, nullable=False, server_default=false()),
    Column("created_at", UTCDateTime, nullable=False),
)
