"""Publicaciones del blog (RF-56 a RF-58).

Cada publicación tiene un borrador (`bloques`) que editan los admins y, si se publicó, una
instantánea (`publicado`) con los datos de los gráficos ya resueltos: es lo único que ve el
público. Editar el borrador no cambia lo publicado hasta que se vuelve a publicar (RF-57).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.engine import get_engine
from app.db.schema import posts
from app.services.job_store import resolve_database

MAX_SLUG = 80


class PostNotFoundError(Exception):
    pass


class SlugTakenError(Exception):
    pass


@dataclass
class Post:
    id: int
    slug: str
    titulo: str
    resumen: str
    project_id: int | None
    autor_id: int | None
    created_at: datetime
    updated_at: datetime
    bloques: list[dict] = field(default_factory=list)  # secciones de texto
    figuras: list[dict] = field(default_factory=list)  # cálculos con su vista y permisos
    palabras_clave: list[str] = field(default_factory=list)
    autores: str = ""
    anonimizar: bool = True
    publicado: dict | None = field(default=None, repr=False)
    publicado_at: datetime | None = None

    @property
    def estado(self) -> str:
        return "publicado" if self.publicado is not None else "borrador"

    @property
    def cambios_sin_publicar(self) -> bool:
        """El borrador se editó después de la última publicación."""
        return self.publicado_at is not None and self.updated_at > self.publicado_at


def slugify(text: str) -> str:
    """`Efectos olvidados en Retail` → `efectos-olvidados-en-retail`."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:MAX_SLUG].strip("-") or "publicacion"


class PostStore:
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
    def _row_to_post(row: Row) -> Post:
        m = row._mapping
        return Post(
            id=m["id"],
            slug=m["slug"],
            titulo=m["titulo"],
            resumen=m["resumen"] or "",
            project_id=m["project_id"],
            autor_id=m["autor_id"],
            created_at=m["created_at"],
            updated_at=m["updated_at"],
            bloques=m["bloques"] or [],
            figuras=m["figuras"] or [],
            palabras_clave=m["palabras_clave"] or [],
            autores=m["autores"] or "",
            anonimizar=bool(m["anonimizar"]),
            publicado=m["publicado"],
            publicado_at=m["publicado_at"],
        )

    @staticmethod
    async def _get(conn: AsyncConnection, post_id: int) -> Post:
        row = (await conn.execute(select(posts).where(posts.c.id == post_id))).first()
        if row is None:
            raise PostNotFoundError(f"Publicación {post_id} no existe")
        return PostStore._row_to_post(row)

    @staticmethod
    async def _slug_owner(conn: AsyncConnection, slug: str) -> int | None:
        return await conn.scalar(select(posts.c.id).where(posts.c.slug == slug))

    @classmethod
    async def _free_slug(cls, conn: AsyncConnection, base: str) -> str:
        """`base`, o `base-2`, `base-3`… si ya está tomado."""
        slug, n = base, 1
        while await cls._slug_owner(conn, slug) is not None:
            n += 1
            suffix = f"-{n}"
            slug = base[: MAX_SLUG - len(suffix)] + suffix
        return slug

    async def create(
        self,
        *,
        titulo: str,
        resumen: str,
        project_id: int | None,
        autor_id: int | None,
        bloques: list[dict],
        figuras: list[dict] | None = None,
    ) -> Post:
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Post:
            slug = await self._free_slug(conn, slugify(titulo))
            post_id = await conn.scalar(
                insert(posts)
                .values(
                    slug=slug, titulo=titulo, resumen=resumen, project_id=project_id,
                    autor_id=autor_id, bloques=bloques, figuras=figuras or [], palabras_clave=[],
                    created_at=now, updated_at=now,
                )
                .returning(posts.c.id)
            )
            return await self._get(conn, post_id)

        return await self._write(fn)

    async def get(self, post_id: int) -> Post:
        return await self._read(lambda conn: self._get(conn, post_id))

    async def list_all(self) -> list[Post]:
        async def fn(conn: AsyncConnection) -> list[Post]:
            rows = await conn.execute(select(posts).order_by(posts.c.updated_at.desc(), posts.c.id.desc()))
            return [self._row_to_post(r) for r in rows]

        return await self._read(fn)

    async def list_published(self) -> list[Post]:
        async def fn(conn: AsyncConnection) -> list[Post]:
            rows = await conn.execute(
                select(posts)
                .where(posts.c.publicado.is_not(None))
                .order_by(posts.c.publicado_at.desc(), posts.c.id.desc())
            )
            return [self._row_to_post(r) for r in rows]

        return await self._read(fn)

    async def get_published(self, slug: str) -> Post:
        async def fn(conn: AsyncConnection) -> Post:
            row = (
                await conn.execute(
                    select(posts).where(posts.c.slug == slug, posts.c.publicado.is_not(None))
                )
            ).first()
            if row is None:
                raise PostNotFoundError(f"No hay una publicación «{slug}»")
            return self._row_to_post(row)

        return await self._read(fn)

    async def update(self, post_id: int, **fields) -> Post:
        """Actualiza el borrador (titulo, slug, resumen, bloques, figuras, palabras_clave, autores, anonimizar)."""
        allowed = {"titulo", "slug", "resumen", "bloques", "figuras", "palabras_clave", "autores", "anonimizar"}
        values = {k: v for k, v in fields.items() if k in allowed}

        async def fn(conn: AsyncConnection) -> Post:
            await self._get(conn, post_id)
            if "slug" in values:
                owner = await self._slug_owner(conn, values["slug"])
                if owner is not None and owner != post_id:
                    raise SlugTakenError(f"La dirección «{values['slug']}» ya la usa otra publicación")
            if values:
                await conn.execute(
                    update(posts).where(posts.c.id == post_id).values(**values, updated_at=datetime.now(UTC))
                )
            return await self._get(conn, post_id)

        return await self._write(fn)

    async def publish(self, post_id: int, snapshot: dict) -> Post:
        """Guarda la instantánea pública. `updated_at` queda igual a `publicado_at`."""
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Post:
            await self._get(conn, post_id)
            await conn.execute(
                update(posts)
                .where(posts.c.id == post_id)
                .values(publicado=snapshot, publicado_at=now, updated_at=now)
            )
            return await self._get(conn, post_id)

        return await self._write(fn)

    async def unpublish(self, post_id: int) -> Post:
        async def fn(conn: AsyncConnection) -> Post:
            await self._get(conn, post_id)
            await conn.execute(
                update(posts).where(posts.c.id == post_id).values(publicado=None, publicado_at=None)
            )
            return await self._get(conn, post_id)

        return await self._write(fn)

    async def delete(self, post_id: int) -> None:
        async def fn(conn: AsyncConnection) -> None:
            await self._get(conn, post_id)
            await conn.execute(sql_delete(posts).where(posts.c.id == post_id))

        await self._write(fn)


post_store = PostStore()
