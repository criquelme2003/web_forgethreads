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
    UniqueConstraint,
    false,
    true,
)

from app.db.columns import JSONType, UTCDateTime

metadata = MetaData()

# Administradores (RF-01). `email` es el usuario de ingreso; el superadmin inicial usa AUTH_USERNAME.
# Sin `password_hash` el admin aún no activa su cuenta (RF-48).
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, unique=True),
    Column("nombre", Text, nullable=False, server_default=""),
    Column("rol", String(20), nullable=False),
    Column("password_hash", Text),
    Column("activo", Boolean, nullable=False, server_default=true()),
    Column("activacion_token_hash", String(64), unique=True),
    Column("activacion_expira_at", UTCDateTime),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

projects = Table(
    "projects",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", Text, nullable=False),
    Column("descripcion", Text, nullable=False, server_default=""),
    Column("tipo", String(20), nullable=False),
    Column("estado", String(20), nullable=False),
    Column("owner_id", Integer, ForeignKey("users.id", name="fk_projects_owner_id_users"), index=True),
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
    Column("input_version", Integer),
    Column("user_id", Integer),  # quién lo lanzó
)

# Co-admins de un proyecto (RF-49); el dueño está en projects.owner_id.
project_admins = Table(
    "project_admins",
    metadata,
    Column("project_id", Integer, ForeignKey("projects.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("created_at", UTCDateTime, nullable=False),
)

expert_invitations = Table(
    "expert_invitations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False, index=True),
    Column("email", Text, nullable=False),
    Column("nombre", Text),
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
    # Versión vigente (cc/ce/ee de arriba) y hasta cuándo el experto la puede modificar (RF-52).
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", UTCDateTime),
    Column("editable_hasta", UTCDateTime),
)

# Todas las versiones de cada input, incluida la vigente (RF-53).
expert_input_versions = Table(
    "expert_input_versions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("input_id", Integer, ForeignKey("expert_inputs.id"), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("cc", JSONType, nullable=False),
    Column("ce", JSONType, nullable=False),
    Column("ee", JSONType, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("input_id", "version", name="uq_expert_input_versions_input_version"),
)

# Publicaciones del blog (RF-56 a RF-58). Borrador: secciones de texto (`bloques`), figuras con sus
# permisos, palabras clave y autores. `publicado`: instantánea pública de la última publicación.
posts = Table(
    "posts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String(120), nullable=False, unique=True),
    Column("titulo", Text, nullable=False),
    Column("resumen", Text, nullable=False, server_default=""),
    Column(
        "project_id", Integer, ForeignKey("projects.id", name="fk_posts_project_id_projects"), index=True
    ),
    Column("autor_id", Integer, ForeignKey("users.id", name="fk_posts_autor_id_users")),
    Column("bloques", JSONType, nullable=False),
    Column("figuras", JSONType),
    Column("palabras_clave", JSONType),
    Column("autores", Text, nullable=False, server_default=""),
    Column("anonimizar", Boolean, nullable=False, server_default=true()),
    Column("publicado", JSONType),
    Column("publicado_at", UTCDateTime),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)
