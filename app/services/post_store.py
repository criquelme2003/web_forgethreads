"""Publicaciones del blog (RF-56 a RF-58).

Cada publicación tiene un borrador (`bloques`) que editan los admins y, si se publicó, una
instantánea (`publicado`) con los datos de los gráficos ya resueltos: es lo único que ve el
público. Editar el borrador no cambia lo publicado hasta que se vuelve a publicar (RF-57).

Una publicación con formato `pdf` no tiene secciones ni figuras: es un documento subido
(`post_pdfs`) con título, autores, resumen y palabras clave. El borrador apunta a su PDF con
`pdf_id` y la instantánea al publicado (`_privado.pdf_id`); los PDF que ya no usa ninguno de los
dos se borran.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.engine import get_engine
from app.db.schema import post_pdfs, posts
from app.services.job_store import resolve_database

MAX_SLUG = 80


class PostNotFoundError(Exception):
    pass


class SlugTakenError(Exception):
    pass


class PdfNotFoundError(Exception):
    pass


@dataclass
class PdfFile:
    id: int
    post_id: int
    nombre: str
    tamano: int
    sha256: str
    created_at: datetime
    contenido: bytes | None = field(default=None, repr=False)

    def meta(self) -> dict:
        return {"nombre": self.nombre, "tamano": self.tamano, "subido_at": self.created_at.isoformat()}


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
    formato: str = "articulo"  # `articulo` o `pdf`
    pdf_id: int | None = None
    publicado: dict | None = field(default=None, repr=False)
    publicado_at: datetime | None = None

    @property
    def estado(self) -> str:
        return "publicado" if self.publicado is not None else "borrador"

    @property
    def cambios_sin_publicar(self) -> bool:
        """El borrador se editó después de la última publicación."""
        return self.publicado_at is not None and self.updated_at > self.publicado_at

    @property
    def pdf_publicado_id(self) -> int | None:
        return ((self.publicado or {}).get("_privado") or {}).get("pdf_id")


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
            formato=m["formato"] or "articulo",
            pdf_id=m["pdf_id"],
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
        formato: str = "articulo",
    ) -> Post:
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Post:
            slug = await self._free_slug(conn, slugify(titulo))
            post_id = await conn.scalar(
                insert(posts)
                .values(
                    slug=slug, titulo=titulo, resumen=resumen, project_id=project_id,
                    autor_id=autor_id, bloques=bloques, figuras=figuras or [], palabras_clave=[],
                    formato=formato, created_at=now, updated_at=now,
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
            post = await self._get(conn, post_id)
            await self._drop_unused_pdfs(conn, post)
            return post

        return await self._write(fn)

    async def unpublish(self, post_id: int) -> Post:
        async def fn(conn: AsyncConnection) -> Post:
            await self._get(conn, post_id)
            await conn.execute(
                update(posts).where(posts.c.id == post_id).values(publicado=None, publicado_at=None)
            )
            post = await self._get(conn, post_id)
            await self._drop_unused_pdfs(conn, post)
            return post

        return await self._write(fn)

    async def delete(self, post_id: int) -> None:
        async def fn(conn: AsyncConnection) -> None:
            await self._get(conn, post_id)
            await conn.execute(sql_delete(post_pdfs).where(post_pdfs.c.post_id == post_id))
            await conn.execute(sql_delete(posts).where(posts.c.id == post_id))

        await self._write(fn)

    # --- PDF -------------------------------------------------------------------------------------

    @staticmethod
    async def _drop_unused_pdfs(conn: AsyncConnection, post: Post) -> None:
        """Borra los PDF de la publicación que no usan ni el borrador ni lo publicado."""
        keep = [i for i in (post.pdf_id, post.pdf_publicado_id) if i is not None]
        stmt = sql_delete(post_pdfs).where(post_pdfs.c.post_id == post.id)
        if keep:
            stmt = stmt.where(post_pdfs.c.id.not_in(keep))
        await conn.execute(stmt)

    @staticmethod
    def _row_to_pdf(row: Row, with_content: bool) -> PdfFile:
        m = row._mapping
        return PdfFile(
            id=m["id"], post_id=m["post_id"], nombre=m["nombre"], tamano=m["tamano"],
            sha256=m["sha256"], created_at=m["created_at"],
            contenido=bytes(m["contenido"]) if with_content else None,
        )

    async def set_pdf(self, post_id: int, nombre: str, contenido: bytes) -> Post:
        """Reemplaza el PDF del borrador; el publicado se conserva hasta volver a publicar."""
        now = datetime.now(UTC)

        async def fn(conn: AsyncConnection) -> Post:
            await self._get(conn, post_id)
            pdf_id = await conn.scalar(
                insert(post_pdfs)
                .values(
                    post_id=post_id, nombre=nombre, tamano=len(contenido),
                    sha256=hashlib.sha256(contenido).hexdigest(), contenido=contenido, created_at=now,
                )
                .returning(post_pdfs.c.id)
            )
            await conn.execute(update(posts).where(posts.c.id == post_id).values(pdf_id=pdf_id, updated_at=now))
            post = await self._get(conn, post_id)
            await self._drop_unused_pdfs(conn, post)
            return post

        return await self._write(fn)

    async def get_pdf(self, pdf_id: int | None, post_id: int, with_content: bool = True) -> PdfFile:
        """Un PDF de la publicación (sin `with_content`, solo sus datos)."""
        if pdf_id is None:
            raise PdfNotFoundError("La publicación no tiene PDF")
        cols = [c for c in post_pdfs.c if with_content or c.name != "contenido"]

        async def fn(conn: AsyncConnection) -> PdfFile:
            row = (
                await conn.execute(
                    select(*cols).where(post_pdfs.c.id == pdf_id, post_pdfs.c.post_id == post_id)
                )
            ).first()
            if row is None:
                raise PdfNotFoundError("El PDF de la publicación ya no existe")
            return self._row_to_pdf(row, with_content)

        return await self._read(fn)


post_store = PostStore()
