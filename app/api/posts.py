"""Blog: publicaciones científicas sobre los proyectos y sus resultados (RF-56 a RF-58).

Una publicación tiene secciones de texto en Markdown (Introducción, Metodología, Resultados…) y
figuras: cálculos del proyecto con la vista elegida (Sankey, hive plot, tabla de caminos, efectos
por orden o paneles del barrido). El público ve las figuras en una barra lateral, numeradas en el
orden en que el texto las cita.

Una figura se publica solo si el texto la cita (`{{fig:<ref>}}`) y el autor le dio permiso
(`publico`). Sus datos crudos (entradas y resultados) se descargan como CSV solo con un permiso
aparte para cada uno. El gráfico recibe solo lo que dibuja (los `top` caminos más fuertes, sin el
ID del job); las figuras sin permiso no salen del servidor.

Al publicar se guarda una instantánea con los datos de cada figura ya resueltos: el público la
lee en `/app/blog` sin sesión, aunque el cluster no responda, y no ve los cambios del borrador
hasta que se vuelve a publicar (RF-57).

Acceso: una publicación de un proyecto la editan sus admins (dueño y co-admins); una sin
proyecto, su autor. El superadmin edita todas. Para las demás se responde 404.
"""

import csv
import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import (
    get_job_store,
    get_post_store,
    get_project_store,
    get_ssh_pool,
    get_user_store,
    require_user,
)
from app.api.fe import SINGLE_EXPERT_HIDDEN_COLUMNS, load_paths_tables
from app.api.job_callback import aggregate_inputs, can_see_job
from app.api.links import front_link
from app.api.projects import get_project_or_404
from app.api.sweeps import _ensure_raw_csv
from app.core.config import Settings, get_settings
from app.services.job_store import JobNotFoundError, JobRecord, JobStore
from app.services.maxmin_log import parse_maxmin_log
from app.services.post_store import MAX_SLUG, Post, PostNotFoundError, PostStore, SlugTakenError
from app.services.project_store import Project, ProjectNotFoundError, ProjectStore
from app.services.sweep import combination_stats, parse_raw_csv
from app.services.user_store import User, UserNotFoundError, UserStore
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["blog"])

MAX_TITLE = 200
MAX_SUMMARY = 3000
MAX_AUTHORS = 300
MAX_KEYWORDS = 10
MAX_KEYWORD = 50
MAX_SECTIONS = 40
MAX_FIGURES = 30
MAX_MARKDOWN = 30_000
MAX_CAPTION = 1000
MAX_TOP = 200  # caminos por orden que recibe el gráfico público
DEFAULT_TOP = 50

FIG_REF = re.compile(r"\{\{fig:([a-z0-9_-]{1,20})\}\}")

# Vistas que admite cada tipo de cálculo.
VIEWS: dict[str, set[str]] = {
    "fe": {"sankey", "hive", "tabla"},
    "maxmin": {"efectos"},
    "sweep": {"por_n", "por_c", "tabla"},
}
DEFAULT_VIEW = {"fe": "sankey", "maxmin": "efectos", "sweep": "por_n"}
DOWNLOADS = ("entradas", "resultados")


def _publishable(kind: str) -> tuple[str, ...]:
    """Estados con resultados: un barrido cortado conserva las combinaciones terminadas (RF-36)."""
    return ("success", "partial") if kind == "sweep" else ("success",)


# --- borrador ----------------------------------------------------------------------------------


class Section(BaseModel):
    tipo: Literal["texto"] = "texto"
    markdown: str = Field(default="", max_length=MAX_MARKDOWN)


class Figure(BaseModel):
    """Un cálculo con la vista elegida (orden y filtro de origen guardados) y sus permisos."""

    ref: str = Field(..., pattern=r"^[a-z0-9_-]{1,20}$", description="Clave para citarla: {{fig:<ref>}}")
    job_id: str = Field(..., pattern=r"^\d+$", max_length=30)
    vista: Literal["sankey", "hive", "tabla", "efectos", "por_n", "por_c"]
    orden: int | None = Field(default=None, ge=1)
    origen: str | None = Field(default=None, max_length=100)
    top: int = Field(default=DEFAULT_TOP, ge=5, le=MAX_TOP)
    escala: Literal["lineal", "log"] = "lineal"
    pie: str = Field(default="", max_length=MAX_CAPTION)
    publico: bool = Field(default=False, description="Permiso para mostrar el gráfico en la publicación")
    descargar_entradas: bool = False
    descargar_resultados: bool = False


class PostCreate(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=MAX_TITLE)
    project_id: int | None = None
    plantilla: bool = Field(default=True, description="Precargar las secciones y el último cálculo del proyecto")


class PostUpdate(BaseModel):
    titulo: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=MAX_SLUG)
    resumen: str | None = Field(default=None, max_length=MAX_SUMMARY)
    autores: str | None = Field(default=None, max_length=MAX_AUTHORS)
    palabras_clave: list[str] | None = Field(default=None, max_length=MAX_KEYWORDS)
    bloques: list[Section] | None = Field(default=None, max_length=MAX_SECTIONS)
    figuras: list[Figure] | None = Field(default=None, max_length=MAX_FIGURES)
    anonimizar: bool | None = None

    @field_validator("palabras_clave")
    @classmethod
    def _keywords(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = list(dict.fromkeys(k.strip() for k in v if k.strip()))
        if any(len(k) > MAX_KEYWORD for k in cleaned):
            raise ValueError(f"Cada palabra clave puede tener hasta {MAX_KEYWORD} caracteres")
        return cleaned

    @field_validator("figuras")
    @classmethod
    def _unique_refs(cls, v: list[Figure] | None) -> list[Figure] | None:
        refs = [f.ref for f in v or []]
        dup = sorted({r for r in refs if refs.count(r) > 1})
        if dup:
            raise ValueError(f"Figuras con la misma clave: {', '.join(dup)}")
        return v


class ProjectRef(BaseModel):
    id: int
    nombre: str
    tipo: str


class PostOut(BaseModel):
    id: int
    slug: str
    titulo: str
    resumen: str
    autores: str
    palabras_clave: list[str]
    proyecto: ProjectRef | None
    autor: str | None
    bloques: list[dict]
    figuras: list[dict]
    anonimizar: bool
    estado: Literal["borrador", "publicado"]
    cambios_sin_publicar: bool
    publicado_at: datetime | None
    created_at: datetime
    updated_at: datetime
    url: str  # enlace público (solo funciona si está publicada)


class PublicProject(BaseModel):
    nombre: str
    tipo: str


class PublicPostSummary(BaseModel):
    slug: str
    titulo: str
    resumen: str
    autores: str
    palabras_clave: list[str]
    proyecto: PublicProject | None
    publicado_at: datetime | None
    graficos: int


class PublicPost(PublicPostSummary):
    parametros: dict
    ficha: dict | None
    bloques: list[dict]
    figuras: list[dict]
    avisos: list[str] = []  # solo en la vista previa: figuras que no se publicarán y por qué


CurrentUser = Annotated[User, Depends(require_user)]
Posts = Annotated[PostStore, Depends(get_post_store)]
Projects = Annotated[ProjectStore, Depends(get_project_store)]
Jobs = Annotated[JobStore, Depends(get_job_store)]
Users = Annotated[UserStore, Depends(get_user_store)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
Pool = Annotated[SSHConnectionPool, Depends(get_ssh_pool)]


# --- acceso ------------------------------------------------------------------------------------


async def _project_or_none(project_id: int | None, projects: ProjectStore) -> Project | None:
    if project_id is None:
        return None
    try:
        return await projects.get_project(project_id)
    except ProjectNotFoundError:
        return None


def can_edit(post: Post, project: Project | None, user: User) -> bool:
    if user.is_superadmin:
        return True
    if post.project_id is not None:
        return project is not None and project.is_member(user.id)
    return post.autor_id == user.id


async def _editable_post(
    post_id: int, user: User, posts: PostStore, projects: ProjectStore
) -> tuple[Post, Project | None]:
    try:
        post = await posts.get(post_id)
    except PostNotFoundError:
        post = None
    project = await _project_or_none(post.project_id, projects) if post else None
    if post is None or not can_edit(post, project, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Publicación {post_id} no existe")
    return post, project


async def _author_name(autor_id: int | None, users: UserStore) -> str | None:
    if autor_id is None:
        return None
    try:
        author = await users.get(autor_id)
    except UserNotFoundError:
        return None
    return author.nombre or None  # el correo del autor no se muestra al público


def _post_out(post: Post, project: Project | None, autor: str | None, settings: Settings, request: Request) -> PostOut:
    return PostOut(
        id=post.id,
        slug=post.slug,
        titulo=post.titulo,
        resumen=post.resumen,
        autores=post.autores,
        palabras_clave=post.palabras_clave,
        proyecto=ProjectRef(id=project.id, nombre=project.nombre, tipo=project.tipo) if project else None,
        autor=autor,
        bloques=post.bloques,
        figuras=post.figuras,
        anonimizar=post.anonimizar,
        estado=post.estado,
        cambios_sin_publicar=post.cambios_sin_publicar,
        publicado_at=post.publicado_at,
        created_at=post.created_at,
        updated_at=post.updated_at,
        url=front_link(settings, request, f"blog/{post.slug}"),
    )


def _figure_error(ref: str, message: str, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, f"Figura «{ref}»: {message}")


async def _figure_record(fig: Figure, user: User, store: JobStore, projects: ProjectStore) -> JobRecord:
    """El job de la figura, si el usuario lo ve y admite la vista elegida."""
    try:
        record = await store.get(fig.job_id)
    except JobNotFoundError:
        record = None
    if record is None or not await can_see_job(record, user, projects):
        raise _figure_error(fig.ref, f"el cálculo {fig.job_id} no existe")
    if fig.vista not in VIEWS.get(record.kind, set()):
        raise _figure_error(fig.ref, f"la vista «{fig.vista}» no corresponde a un cálculo {record.kind}")
    return record


def cited_refs(sections: list[dict]) -> list[str]:
    """Claves de figura citadas en el texto, en orden de primera cita (así se numeran)."""
    return list(dict.fromkeys(m for s in sections for m in FIG_REF.findall(s.get("markdown", ""))))


# --- plantilla ---------------------------------------------------------------------------------

_METHOD_TEXT = {
    "expertos": (
        "## Metodología\n\n"
        "Un panel de expertos evaluó, en una escala de 0 a 1, la incidencia de cada causa sobre las demás "
        "causas y sobre cada efecto. Sobre estas matrices de incidencia se identificaron los **efectos "
        "olvidados**: relaciones indirectas que se transmiten a través de factores intermedios y que no "
        "son visibles en las relaciones directas."
    ),
    "simulacion": (
        "## Metodología\n\n"
        "Se generaron redes aleatorias de N nodos con conectividad promedio c y se aplicó la composición "
        "MaxMin de forma iterada, registrando el orden a partir del cual no aparecen efectos nuevos "
        "(**orden efectivo**)."
    ),
}
_EMPTY_SECTIONS = ["## Discusión\n\n", "## Conclusiones\n\n", "## Referencias\n\n"]


def _template(project: Project | None, records: list[JobRecord]) -> tuple[list[dict], list[dict]]:
    """Secciones de un artículo (RF-56) y, como figura citada, el cálculo terminado más reciente.

    La figura queda sin permiso de publicación: el autor debe darlo para poder publicar.
    """
    if project is None:
        sections = ["## Introducción\n\n", "## Metodología\n\n", "## Resultados\n\n", *_EMPTY_SECTIONS]
        return [Section(markdown=s).model_dump() for s in sections], []
    # Agregados y barridos primero: resumen a todos los expertos o a muchas simulaciones.
    done = [r for r in records if r.kind in VIEWS and r.status in _publishable(r.kind)]
    done.sort(key=lambda r: (bool(aggregate_inputs(r)) or r.kind == "sweep", r.created_at), reverse=True)
    figures = [Figure(ref="f1", job_id=done[0].job_id, vista=DEFAULT_VIEW[done[0].kind])] if done else []
    results = "## Resultados\n\n" + (
        "La {{fig:f1}} muestra el resultado principal. _Describe qué se observa._"
        if figures else "_Agrega figuras y cítalas en el texto._"
    )
    sections = [
        "## Introducción\n\n" + (project.descripcion.strip() or "_Pregunta de investigación y contexto._"),
        _METHOD_TEXT.get(project.tipo, "## Metodología\n\n"),
        results,
        *_EMPTY_SECTIONS,
    ]
    return (
        [Section(markdown=s).model_dump() for s in sections],
        [f.model_dump(exclude_none=True) for f in figures],
    )


# --- instantánea -------------------------------------------------------------------------------


class _Renderer:
    """Arma la vista pública: textos, ficha y datos de las figuras citadas y con permiso."""

    def __init__(self, post: Post, project: Project | None, user: User, store: JobStore,
                 projects: ProjectStore, settings: Settings, pool: SSHConnectionPool) -> None:
        self.post, self.project, self.user = post, project, user
        self.store, self.projects, self.settings, self.pool = store, projects, settings, pool
        self._labels: dict[int, dict[int, str]] = {}  # por proyecto: input_id → etiqueta
        self.thr: set[float] = set()
        self.maxorder: set[int] = set()
        self.reps: set[int] = set()
        self.expertos: set[int] = set()
        self.errores: list[str] = []  # impiden publicar
        self.avisos: list[str] = []  # informativos

    async def expert_labels(self, project_id: int | None) -> dict[int, str]:
        """«Experto 1», «Experto 2»… por orden de llegada del input (RF-58).

        Sin anonimizar se usa el nombre de la invitación; el correo nunca se publica.
        """
        if project_id is None:
            return {}
        if project_id not in self._labels:
            inputs = sorted(await self.projects.list_inputs(project_id), key=lambda i: i.id)
            authors = {} if self.post.anonimizar else await self.projects.input_authors([i.id for i in inputs])
            self._labels[project_id] = {
                item.id: (authors.get(item.id) and authors[item.id].nombre) or f"Experto {n}"
                for n, item in enumerate(inputs, 1)
            }
        return self._labels[project_id]

    async def render(self) -> dict:
        figures = {f["ref"]: Figure.model_validate(f) for f in self.post.figuras}
        for fig in figures.values():  # una figura inválida (job borrado) se informa por su clave
            await _figure_record(fig, self.user, self.store, self.projects)
        cited = cited_refs(self.post.bloques)
        for ref in cited:
            if ref not in figures:
                self.errores.append(f"El texto cita {{{{fig:{ref}}}}}, pero no hay una figura «{ref}».")
            elif not figures[ref].publico:
                self.errores.append(
                    f"El texto cita la figura «{ref}», pero no tiene permiso para mostrarse en la publicación."
                )
        for ref, fig in figures.items():
            if ref not in cited:
                self.avisos.append(f"La figura «{ref}» no se cita en el texto: no se publicará.")
            elif not fig.publico and (fig.descargar_entradas or fig.descargar_resultados):
                self.avisos.append(f"Las descargas de la figura «{ref}» requieren que la figura se publique.")

        out_figures, private = [], {}
        for ref in cited:
            fig = figures.get(ref)
            if fig is None or not fig.publico:
                continue
            numero = len(out_figures) + 1
            view, record = await self.figure(fig)
            downloads = [d for d in DOWNLOADS if getattr(fig, f"descargar_{d}")]
            out_figures.append({**view, "numero": numero, "ref": ref, "descargas": downloads})
            if downloads:
                labels = await self.expert_labels(record.project_id)
                private[str(numero)] = {
                    "job_id": record.job_id,
                    "descargas": downloads,
                    "etiquetas": {str(k): v for k, v in labels.items()},
                }

        project = self.project
        return {
            "titulo": self.post.titulo,
            "resumen": self.post.resumen,
            "autores": self.post.autores,
            "palabras_clave": self.post.palabras_clave,
            "proyecto": {"nombre": project.nombre, "tipo": project.tipo} if project else None,
            "ficha": await self.project_card() if project else None,
            "graficos": len(out_figures),
            "parametros": {
                "thr": sorted(self.thr),
                "maxorder": sorted(self.maxorder),
                "reps": sorted(self.reps),
                "expertos": len(self.expertos),
            },
            "bloques": [{"tipo": "texto", "markdown": s.get("markdown", "")} for s in self.post.bloques],
            "figuras": out_figures,
            "_privado": {"figuras": private},  # para las descargas; nunca se envía al público
        }

    async def project_card(self) -> dict:
        project = self.project
        inputs = [i for i in await self.projects.list_inputs(project.id) if not i.excluido]
        records = await self.store.list_by_project(project.id)
        return {
            "nombre": project.nombre,
            "tipo": project.tipo,
            "estado": project.estado,
            "causas": project.causas,
            "efectos": project.efectos,
            "expertos": len(inputs),
            "calculos": sum(1 for r in records if r.status == "success"),
            "barridos": sum(1 for r in records if r.status in ("success", "partial") and r.kind == "sweep"),
        }

    async def figure(self, fig: Figure) -> tuple[dict, JobRecord]:
        record = await _figure_record(fig, self.user, self.store, self.projects)
        if record.status not in _publishable(record.kind):
            raise _figure_error(
                fig.ref, f"el cálculo {record.job_id} está en estado {record.status}", status.HTTP_409_CONFLICT
            )
        out = {"kind": record.kind, "vista": fig.vista, "pie": fig.pie, "fecha": record.created_at.isoformat()}
        try:
            if record.kind == "fe":
                out.update(await self.fe_data(record, fig))
            elif record.kind == "maxmin":
                out.update(self.maxmin_data(record, fig))
            else:
                out.update(await self.sweep_data(record))
        except HTTPException as e:  # resultados no disponibles en el nodo (502)
            raise _figure_error(fig.ref, str(e.detail), e.status_code) from e
        return out, record

    async def fe_data(self, record: JobRecord, fig: Figure) -> dict:
        params, result = record.params or {}, record.result or {}
        k = result.get("k")
        tables = await load_paths_tables(
            record, self.settings, self.pool, SINGLE_EXPERT_HIDDEN_COLUMNS if k == 1 else ()
        )
        orders = [t.order for t in tables]
        if fig.vista in ("sankey", "tabla"):
            orden = fig.orden if fig.orden is not None else (orders[0] if orders else None)
            if orders and orden not in orders:
                raise _figure_error(fig.ref, f"el cálculo {record.job_id} no tiene caminos de orden {orden}")
            selected = [t for t in tables if t.order == orden]
        else:
            orden = None
            selected = tables
        out_tables = []
        for table in selected:
            # El gráfico recibe solo lo que dibuja: los `top` caminos más fuertes del origen elegido.
            rows = [r for r in table.rows if not fig.origen or str(r[0]) == fig.origen]
            if "Mean" in table.columns:
                mean = table.columns.index("Mean")
                rows.sort(key=lambda r: r[mean], reverse=True)
            out_tables.append(
                {"order": table.order, "columns": table.columns, "rows": rows[: fig.top], "total": len(rows)}
            )

        labels = await self.expert_labels(record.project_id)
        included = [i for i, _ in aggregate_inputs(record)] or ([record.input_id] if record.input_id else [])
        self.expertos.update(included)
        if params.get("thr") is not None:
            self.thr.add(params["thr"])
        if params.get("maxorder") is not None:
            self.maxorder.add(params["maxorder"])
        if k and k > 1 and params.get("reps"):
            self.reps.add(params["reps"])
        return {
            "orden": orden,
            "origen": fig.origen,
            "top": fig.top,
            "meta": {
                "thr": params.get("thr"),
                "maxorder": params.get("maxorder"),
                "reps": params.get("reps") if k and k > 1 else None,
                "expertos": [labels.get(i, "Experto") for i in included],
                "agregado": bool(aggregate_inputs(record)),
            },
            "datos": {
                "causes": params.get("causes", []),
                "effects": params.get("effects", []),
                "orders": orders,
                "tables": out_tables,
            },
        }

    def maxmin_data(self, record: JobRecord, fig: Figure) -> dict:
        params = record.params or {}
        parsed = parse_maxmin_log(record.logs)
        if params.get("threshold") is not None:
            self.thr.add(params["threshold"])
        return {
            "escala": fig.escala,
            "meta": {
                "n": params.get("numero_nodos"),
                "c": params.get("conectividad_promedio"),
                "thr": params.get("threshold"),
                "orden_efectivo": record.effective_order,
                "convergencia": parsed.convergence_step,
            },
            "datos": {"effects_by_order": [{"order": o, "count": c} for o, c in parsed.effects_by_order]},
        }

    async def sweep_data(self, record: JobRecord) -> dict:
        params = record.params or {}
        local = await _ensure_raw_csv(record, self.settings, self.pool)
        stats = combination_stats(parse_raw_csv(local.read_text(encoding="utf-8")))
        if params.get("thr") is not None:
            self.thr.add(params["thr"])
        return {
            "meta": {"thr": params.get("thr"), "reps": params.get("reps"), "parcial": record.status == "partial"},
            "datos": {
                "estadisticas": [
                    {
                        "n": s.n, "c": s.c, "media": s.mean, "desviacion_estandar": s.sd,
                        "error_estandar": s.sem, "minimo": s.min, "maximo": s.max, "repeticiones": s.count,
                    }
                    for s in stats
                ]
            },
        }


def _public(post: Post, view: dict, publicado_at: datetime | None, avisos: list[str] | None = None) -> PublicPost:
    # Sin las claves internas (`_privado`). La dirección es la vigente, aunque se haya cambiado
    # después de publicar.
    data = {k: v for k, v in view.items() if not k.startswith("_")}
    return PublicPost(**{**data, "slug": post.slug}, publicado_at=publicado_at, avisos=avisos or [])


# --- edición (admins) --------------------------------------------------------------------------


@router.get("/app/posts", response_model=list[PostOut])
async def list_posts(
    request: Request, user: CurrentUser, posts: Posts, projects: Projects, users: Users,
    settings: SettingsDep, project_id: int | None = None,
) -> list[PostOut]:
    """Publicaciones que el usuario puede editar; `?project_id=` filtra las de un proyecto."""
    out = []
    cache: dict[int, Project | None] = {}
    for post in await posts.list_all():
        if project_id is not None and post.project_id != project_id:
            continue
        if post.project_id is not None and post.project_id not in cache:
            cache[post.project_id] = await _project_or_none(post.project_id, projects)
        project = cache.get(post.project_id) if post.project_id is not None else None
        if can_edit(post, project, user):
            out.append(_post_out(post, project, await _author_name(post.autor_id, users), settings, request))
    return out


@router.post("/app/posts", response_model=PostOut, status_code=status.HTTP_201_CREATED)
async def create_post(
    body: PostCreate, request: Request, user: CurrentUser, posts: Posts, projects: Projects,
    store: Jobs, users: Users, settings: SettingsDep,
) -> PostOut:
    """Nueva publicación; un proyecto puede tener varias."""
    project = await get_project_or_404(body.project_id, projects, user) if body.project_id is not None else None
    records = await store.list_by_project(project.id) if project and body.plantilla else []
    bloques, figuras = _template(project, records) if body.plantilla else ([], [])
    autor = await _author_name(user.id, users)
    post = await posts.create(
        titulo=body.titulo.strip(), resumen="", project_id=project.id if project else None,
        autor_id=user.id, bloques=bloques, figuras=figuras,
    )
    if autor:
        post = await posts.update(post.id, autores=autor)
    return _post_out(post, project, autor, settings, request)


@router.get("/app/posts/{post_id}", response_model=PostOut)
async def get_post(
    post_id: int, request: Request, user: CurrentUser, posts: Posts, projects: Projects,
    users: Users, settings: SettingsDep,
) -> PostOut:
    post, project = await _editable_post(post_id, user, posts, projects)
    return _post_out(post, project, await _author_name(post.autor_id, users), settings, request)


@router.patch("/app/posts/{post_id}", response_model=PostOut)
async def update_post(
    post_id: int, body: PostUpdate, request: Request, user: CurrentUser, posts: Posts,
    projects: Projects, store: Jobs, users: Users, settings: SettingsDep,
) -> PostOut:
    """Guarda el borrador. Las figuras se validan aquí (cálculo visible y vista), sin ir al cluster."""
    post, project = await _editable_post(post_id, user, posts, projects)
    fields = body.model_dump(exclude_unset=True, exclude={"bloques", "figuras"})
    for key in ("titulo", "autores"):
        if key in fields:
            fields[key] = fields[key].strip()
    if body.bloques is not None:
        fields["bloques"] = [s.model_dump() for s in body.bloques]
    if body.figuras is not None:
        for fig in body.figuras:
            await _figure_record(fig, user, store, projects)
        fields["figuras"] = [f.model_dump(exclude_none=True) for f in body.figuras]
    try:
        post = await posts.update(post_id, **fields)
    except SlugTakenError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return _post_out(post, project, await _author_name(post.autor_id, users), settings, request)


@router.get("/app/posts/{post_id}/preview", response_model=PublicPost)
async def preview_post(
    post_id: int, user: CurrentUser, posts: Posts, projects: Projects, store: Jobs,
    settings: SettingsDep, pool: Pool,
) -> PublicPost:
    """El borrador guardado tal como lo verá el público, con avisos sobre las figuras."""
    post, project = await _editable_post(post_id, user, posts, projects)
    renderer = _Renderer(post, project, user, store, projects, settings, pool)
    view = await renderer.render()
    return _public(post, view, None, renderer.errores + renderer.avisos)


@router.post("/app/posts/{post_id}/publish", response_model=PostOut)
async def publish_post(
    post_id: int, request: Request, user: CurrentUser, posts: Posts, projects: Projects,
    store: Jobs, users: Users, settings: SettingsDep, pool: Pool,
) -> PostOut:
    """Publica (o vuelve a publicar) el borrador: guarda la instantánea pública (RF-57)."""
    post, project = await _editable_post(post_id, user, posts, projects)
    renderer = _Renderer(post, project, user, store, projects, settings, pool)
    view = await renderer.render()
    if renderer.errores:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, " ".join(renderer.errores))
    post = await posts.publish(post_id, view)
    return _post_out(post, project, await _author_name(post.autor_id, users), settings, request)


@router.post("/app/posts/{post_id}/unpublish", response_model=PostOut)
async def unpublish_post(
    post_id: int, request: Request, user: CurrentUser, posts: Posts, projects: Projects,
    users: Users, settings: SettingsDep,
) -> PostOut:
    """Retira la publicación del blog; el borrador se conserva."""
    _, project = await _editable_post(post_id, user, posts, projects)
    post = await posts.unpublish(post_id)
    return _post_out(post, project, await _author_name(post.autor_id, users), settings, request)


@router.delete("/app/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: int, user: CurrentUser, posts: Posts, projects: Projects) -> Response:
    await _editable_post(post_id, user, posts, projects)
    await posts.delete(post_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- lectura pública ---------------------------------------------------------------------------


async def _published(slug: str, posts: PostStore) -> Post:
    try:
        return await posts.get_published(slug)
    except PostNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Publicación no encontrada")


@router.get("/app/blog", response_model=list[PublicPostSummary])
async def list_blog(posts: Posts) -> list[PublicPostSummary]:
    """Publicaciones publicadas, de la más reciente a la más antigua. Sin sesión."""
    keys = ("titulo", "resumen", "autores", "palabras_clave", "proyecto", "graficos")
    return [
        PublicPostSummary(**{k: post.publicado.get(k) for k in keys}, slug=post.slug, publicado_at=post.publicado_at)
        for post in await posts.list_published()
    ]


@router.get("/app/blog/{slug}", response_model=PublicPost)
async def get_blog_post(slug: str, posts: Posts) -> PublicPost:
    """Última versión publicada. Un borrador o una publicación retirada responde 404."""
    post = await _published(slug, posts)
    return _public(post, post.publicado, post.publicado_at)


def _csv(rows: list[list]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _matrix_rows(label: str, name: str, matrix: list, rows: list[str], cols: list[str]) -> list[list]:
    return [
        [label, name, rows[i] if i < len(rows) else i + 1, cols[j] if j < len(cols) else j + 1, value]
        for i, line in enumerate(matrix)
        for j, value in enumerate(line)
    ]


async def _inputs_csv(record: JobRecord, labels: dict[str, str], projects: ProjectStore) -> str:
    """Entradas del cálculo: matrices de los expertos (formato largo) o parámetros de la simulación."""
    params = record.params or {}
    if record.kind != "fe":
        return _csv([["parametro", "valor"]] + [
            [k, " ".join(map(str, v)) if isinstance(v, list) else v] for k, v in params.items()
        ])
    pairs = aggregate_inputs(record) or (
        [(record.input_id, record.input_version or 1)] if record.input_id else []
    )
    matrices = await projects.version_matrices(pairs)
    causes, effects = params.get("causes", []), params.get("effects", [])
    rows = [["experto", "matriz", "fila", "columna", "valor"]]
    for input_id, version in pairs:
        cc, ce, ee = matrices.get((input_id, version), ([], [], []))
        label = labels.get(str(input_id), "Experto")
        rows += _matrix_rows(label, "CC", cc, causes, causes)
        rows += _matrix_rows(label, "CE", ce, causes, effects)
        rows += _matrix_rows(label, "EE", ee, effects, effects)
    return _csv(rows)


async def _results_csv(record: JobRecord, settings: Settings, pool: SSHConnectionPool) -> str | Path:
    """Resultados completos: todos los caminos, los efectos por orden o el CSV crudo del barrido."""
    if record.kind == "sweep":
        return await _ensure_raw_csv(record, settings, pool)
    if record.kind == "maxmin":
        parsed = parse_maxmin_log(record.logs)
        return _csv([["orden", "efectos"]] + [[o, c] for o, c in parsed.effects_by_order])
    k = (record.result or {}).get("k")
    tables = await load_paths_tables(record, settings, pool, SINGLE_EXPERT_HIDDEN_COLUMNS if k == 1 else ())
    metrics = [c for c in (tables[0].columns if tables else []) if c in ("Count", "Mean", "SD")]
    rows = [["orden", "origen", "intermedios", "destino", *metrics]]
    for t in tables:
        idx = {c: i for i, c in enumerate(t.columns)}
        through = [i for c, i in idx.items() if c.startswith("Through")]
        for r in t.rows:
            rows.append([
                t.order, r[idx["From"]], " > ".join(str(r[i]) for i in through), r[idx["To"]],
                *(r[idx[m]] for m in metrics),
            ])
    return _csv(rows)


@router.get("/app/blog/{slug}/figuras/{numero}/{archivo}.csv")
async def download_figure_data(
    slug: str, numero: int, archivo: Literal["entradas", "resultados"], posts: Posts, projects: Projects,
    store: Jobs, settings: SettingsDep, pool: Pool,
) -> Response:
    """Datos crudos de una figura publicada, solo si el autor dio permiso para descargarlos. Sin sesión."""
    post = await _published(slug, posts)
    entry = (post.publicado.get("_privado") or {}).get("figuras", {}).get(str(numero))
    if not entry or archivo not in entry.get("descargas", []):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Estos datos no están disponibles para descarga")
    try:
        record = await store.get(entry["job_id"])
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Estos datos ya no están disponibles")
    filename = f"{post.slug}_figura{numero}_{archivo}.csv"
    if archivo == "entradas":
        content = await _inputs_csv(record, entry.get("etiquetas", {}), projects)
    else:
        try:
            content = await _results_csv(record, settings, pool)
        except HTTPException:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Los resultados no están disponibles en este momento")
    if isinstance(content, Path):  # CSV crudo del barrido, tal cual
        return FileResponse(content, media_type="text/csv", filename=filename)
    return Response(
        content, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
