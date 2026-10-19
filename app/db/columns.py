"""Tipos de columna que se guardan igual en SQLite y en PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Fecha con zona horaria, siempre en UTC.

    En PostgreSQL es `timestamptz`. En SQLite se guarda como texto ISO 8601 (`...+00:00`), el mismo
    formato que usaba la base anterior, así que las bases SQLite existentes se leen sin convertir y
    las comparaciones de texto (`expira_at > ?`) siguen siendo cronológicas.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String())
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.isoformat() if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# JSON en texto en SQLite (compatible con las columnas TEXT anteriores) y JSONB en PostgreSQL.
# none_as_null: None se guarda como NULL de SQL, no como el literal JSON `null`.
JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
