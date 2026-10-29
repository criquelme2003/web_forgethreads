"""Datos de muestra para probar la app sin crearlos a mano.

Crea, si no existen (se reconocen por el nombre):
- «Demo · Expertos»: proyecto de expertos abierto, con las etiquetas de docs/ejemplos_fe y un
  input ya ingresado por un experto de muestra (su invitación queda usada). Los cálculos se
  lanzan desde la app, como con cualquier input.
- «Demo · Simulación»: proyecto de simulación abierto, sin cálculos.
- Una publicación del blog por proyecto (`seed_demo_posts`), ya publicada, que muestra las
  funciones del blog. Sus figuras buscan cálculos que todavía no existen (`DEMO_CALCULATIONS`):
  se ven «en preparación» hasta que se ejecutan y se vuelve a publicar.

Quedan a nombre del superadmin. Se puede correr a mano (`uv run python -m app.services.demo_seed`) o al iniciar la app con
SEED_DEMO_DATA=true; en ambos casos es idempotente.
"""

import asyncio
import logging
from datetime import timedelta

from app.core.config import Settings
from app.services.fe_input import load_fe_input
from app.services.job_store import JobStore
from app.services.post_store import PostStore
from app.services.project_store import ProjectStore
from app.services.user_store import User, UserStore

logger = logging.getLogger(__name__)

DEMO_EXPERT_PROJECT = "Demo · Expertos"
DEMO_SIMULATION_PROJECT = "Demo · Simulación"
DEMO_EXPERT_EMAIL = "experto.demo@example.com"

# Mismas matrices que docs/ejemplos_fe (la imagen Docker solo copia app/).
DEMO_CAUSES = ["Precio", "Publicidad", "Capacitación", "Tecnología"]
DEMO_EFFECTS = ["Ventas", "Costos", "Clima laboral"]
DEMO_CC = """,Precio,Publicidad,Capacitación,Tecnología
Precio,1,0.3,0.1,0.2
Publicidad,0.6,1,0.2,0.3
Capacitación,0.2,0.4,1,0.7
Tecnología,0.5,0.6,0.3,1
"""
DEMO_CE = """,Ventas,Costos,Clima laboral
Precio,0.9,0.4,0.1
Publicidad,0.8,0.6,0.2
Capacitación,0.3,0.5,0.8
Tecnología,0.6,0.7,0.4
"""
DEMO_EE = """,Ventas,Costos,Clima laboral
Ventas,1,0.6,0.5
Costos,0.4,1,0.3
Clima laboral,0.7,0.2,1
"""


async def seed_demo(projects: ProjectStore, owner_id: int | None = None) -> list[str]:
    """Crea los proyectos de muestra que falten. Devuelve los nombres de los que creó."""
    existing = {p.nombre for p in await projects.list_projects()}
    created: list[str] = []

    if DEMO_EXPERT_PROJECT not in existing:
        data = load_fe_input(DEMO_CC, DEMO_CE, DEMO_EE, causes=DEMO_CAUSES, effects=DEMO_EFFECTS)
        if not data.valid:  # no debería pasar: son matrices fijas
            raise ValueError(f"Matrices de muestra inválidas: {[e.message for e in data.errors]}")
        project = await projects.create_project(
            nombre=DEMO_EXPERT_PROJECT,
            descripcion="Proyecto de muestra con un input ya ingresado (matrices de docs/ejemplos_fe).",
            tipo="expertos",
            owner_id=owner_id,
            causas=DEMO_CAUSES,
            efectos=DEMO_EFFECTS,
        )
        await projects.update_project(project.id, estado="abierto")
        _, token = await projects.create_invitation(project.id, DEMO_EXPERT_EMAIL, timedelta(days=14))
        await projects.submit_input(token, data.cc.tolist(), data.ce.tolist(), data.ee.tolist())
        created.append(DEMO_EXPERT_PROJECT)

    if DEMO_SIMULATION_PROJECT not in existing:
        await projects.create_project(
            nombre=DEMO_SIMULATION_PROJECT,
            descripcion="Proyecto de muestra para lanzar cálculos MaxMin.",
            tipo="simulacion",
            owner_id=owner_id,
        )
        created.append(DEMO_SIMULATION_PROJECT)

    if created:
        logger.info("Datos de muestra creados: %s", ", ".join(created))
    return created


# Cálculos que buscan las publicaciones de muestra: ejecutarlos y volver a publicar.
DEMO_CALCULATIONS = [
    (DEMO_EXPERT_PROJECT, "cálculo por experto con THR 0,2 y orden máximo 3"),
    (DEMO_SIMULATION_PROJECT, "MaxMin con N = 1000, c = 2,5 y THR 0,2"),
    (DEMO_SIMULATION_PROJECT, "barrido con THR 0,2 (cualquier lista de N y c)"),
]

_EXPERT_QUERY = {"kind": "fe", "agregado": False, "thr": 0.2, "maxorder": 3,
                 "descripcion": "Cálculo por experto con THR 0,2 y orden máximo 3"}
_MAXMIN_QUERY = {"kind": "maxmin", "n": 1000, "c": 2.5, "thr": 0.2,
                 "descripcion": "MaxMin sobre una red de 1000 nodos con c = 2,5 y THR 0,2"}
_SWEEP_QUERY = {"kind": "sweep", "thr": 0.2, "descripcion": "Barrido de N y c con THR 0,2"}

DEMO_POSTS = [
    {
        "project": DEMO_EXPERT_PROJECT,
        "titulo": "Demostración: efectos olvidados en un negocio minorista",
        "slug": "demo-efectos-olvidados",
        "autores": "Equipo forgethreads",
        "palabras_clave": ["efectos olvidados", "matrices de incidencia", "demostración"],
        "resumen": (
            "Publicación de muestra que recorre las funciones del blog con el proyecto «Demo · Expertos». "
            "Un experto evaluó la incidencia entre cuatro causas (precio, publicidad, capacitación y tecnología) "
            "y tres efectos (ventas, costos y clima laboral). Las figuras se completan cuando se ejecuta el "
            "cálculo por experto con THR 0,2 y orden máximo 3."
        ),
        "bloques": [
            (
                "## Introducción\n\nEn un negocio minorista, las decisiones sobre **precio**, **publicidad**, "
                "**capacitación** y **tecnología** afectan las ventas, los costos y el clima laboral. Algunas de esas "
                "influencias no son directas: pasan por otros factores y por eso se olvidan al planificar.\n\n"
                "> Esta publicación es una demostración: sus datos provienen del proyecto de muestra."
            ),
            (
                "## Metodología\n\nUn experto completó tres matrices de incidencia con valores de 0 a 1:\n\n"
                "| Matriz | Relaciona | Tamaño |\n|---|---|---|\n| CC | causas con causas | 4 × 4 |\n"
                "| CE | causas con efectos | 4 × 3 |\n| EE | efectos con efectos | 3 × 3 |\n\n"
                "Sobre ellas se buscaron los efectos olvidados con "
                "[forgeffects](https://github.com/claudio-araya/forgeffects), con un umbral (THR) de 0,2 y "
                "caminos de hasta 3 pasos."
            ),
            (
                "## Resultados\n\nLa {{fig:f1}} muestra los caminos indirectos de orden 2 más fuertes: cada franja "
                "parte en una causa, pasa por un intermediario y llega a un efecto. Haz clic en una causa para ver "
                "solo sus caminos.\n\nLa {{fig:f2}} reúne todos los órdenes en un hive plot: los caminos más "
                "largos (más oscuros) son los más indirectos."
            ),
            "## Discusión\n\n_Al ejecutar el cálculo, comenta aquí qué intermediarios concentran más caminos._",
            (
                "## Conclusiones\n\n- Las figuras de esta publicación se publican porque el texto las cita y "
                "tienen permiso.\n- La figura «f3» (tabla de caminos) existe en el borrador, pero no se cita: no se publica.\n"
                "- Los datos de la Figura 1 se pueden descargar desde la barra lateral."
            ),
            (
                "## Referencias\n\n- Kaufmann, A. y Gil Aluja, J. (1988). _Modelos para la investigación de efectos "
                "olvidados_. Milladoiro."
            ),
        ],
        "figuras": [
            {"ref": "f1", "buscar": _EXPERT_QUERY, "vista": "sankey", "orden": 2, "top": 50, "publico": True,
             "en_cuerpo": True, "descargar_entradas": True, "descargar_resultados": True,
             "pie": "Caminos indirectos de orden 2 con mayor incidencia media."},
            {"ref": "f2", "buscar": _EXPERT_QUERY, "vista": "hive", "top": 50, "publico": True,
             "pie": "Todos los órdenes; el color indica el orden y el grosor la incidencia."},
            {"ref": "f3", "buscar": _EXPERT_QUERY, "vista": "tabla", "top": 25, "publico": True,
             "pie": "Tabla de caminos (no citada: no se publica)."},
        ],
    },
    {
        "project": DEMO_SIMULATION_PROJECT,
        "titulo": "Demostración: orden efectivo de MaxMin en redes aleatorias",
        "slug": "demo-orden-efectivo",
        "autores": "Equipo forgethreads",
        "palabras_clave": ["MaxMin", "orden efectivo", "redes aleatorias", "demostración"],
        "resumen": (
            "Publicación de muestra con el proyecto «Demo · Simulación». Muestra cuántos efectos aparecen en cada "
            "orden de la composición MaxMin y cómo cambia el orden efectivo con el tamaño de la red y su "
            "conectividad. Las figuras se completan al ejecutar un MaxMin (N = 1000, c = 2,5, THR 0,2) y un "
            "barrido con THR 0,2."
        ),
        "bloques": [
            (
                "## Introducción\n\nAl componer una relación consigo misma con la operación MaxMin aparecen efectos "
                "nuevos en cada orden, hasta que la relación se estabiliza. ¿Cuántos órdenes hacen falta?"
            ),
            (
                "## Metodología\n\nSe generan redes aleatorias de N nodos con conectividad promedio c y se aplica "
                "MaxMin con un umbral de 0,2. Un barrido repite el cálculo para varias combinaciones de N y c."
            ),
            (
                "## Resultados\n\nLa {{fig:m1}} muestra los efectos encontrados en cada orden para una red de 1000 "
                "nodos. La {{fig:s1}} resume el barrido: el orden efectivo medio según c, para cada N, y la "
                "{{fig:s2}} lo muestra según N, para cada c."
            ),
            "## Conclusiones\n\n_Completa esta sección cuando las figuras tengan datos._",
        ],
        "figuras": [
            {"ref": "m1", "buscar": _MAXMIN_QUERY, "vista": "efectos", "escala": "log", "publico": True,
             "en_cuerpo": True, "descargar_entradas": True, "descargar_resultados": True,
             "pie": "Efectos encontrados por orden (escala logarítmica)."},
            {"ref": "s1", "buscar": _SWEEP_QUERY, "vista": "por_n", "publico": True, "descargar_resultados": True,
             "pie": "Media del orden efectivo con su error estándar."},
            {"ref": "s2", "buscar": _SWEEP_QUERY, "vista": "por_c", "publico": True,
             "pie": "La banda va del mínimo al máximo observado."},
        ],
    },
]


# Las primeras publicaciones de muestra buscaban cálculos con THR 0,5, que dejaban las figuras
# sin resultados; si siguen con esa búsqueda (nadie la cambió), se actualizan a THR 0,2.
_OUTDATED_THR = 0.5


def _outdated_demo(post) -> bool:
    return any(
        (f.get("buscar") or {}).get("thr") == _OUTDATED_THR and "THR 0,5" in (f.get("buscar") or {}).get("descripcion", "")
        for f in post.figuras
    )


async def seed_demo_posts(
    projects: ProjectStore,
    posts: PostStore,
    jobs: JobStore,
    owner: User,
    settings: Settings,
    pool=None,
) -> list[str]:
    """Crea y publica las publicaciones de muestra que falten (o que sigan con la búsqueda antigua).

    Devuelve sus direcciones.
    """
    from fastapi import HTTPException

    from app.api.posts import Figure, PostUpdate, _Renderer

    by_name = {p.nombre: p for p in await projects.list_projects()}
    existing = {p.slug: p for p in await posts.list_all()}
    created: list[str] = []
    for spec in DEMO_POSTS:
        project = by_name.get(spec["project"])
        if project is None:
            continue
        current = existing.get(spec["slug"])
        if current is not None and not _outdated_demo(current):
            continue
        body = PostUpdate(
            slug=spec["slug"], resumen=spec["resumen"], autores=spec["autores"],
            palabras_clave=spec["palabras_clave"],
            bloques=[{"tipo": "texto", "markdown": md} for md in spec["bloques"]],
            figuras=spec["figuras"],
        )
        post = current or await posts.create(
            titulo=spec["titulo"], resumen="", project_id=project.id, autor_id=owner.id, bloques=[],
        )
        post = await posts.update(
            post.id,
            slug=body.slug, resumen=body.resumen, autores=body.autores, palabras_clave=body.palabras_clave,
            bloques=[b.model_dump() for b in body.bloques],
            figuras=[Figure.model_validate(f).model_dump(exclude_none=True) for f in spec["figuras"]],
        )
        try:
            view = await _Renderer(post, project, owner, jobs, projects, settings, pool).render()
            await posts.publish(post.id, view)
        except HTTPException as e:  # queda como borrador
            logger.warning("No se pudo publicar %s: %s", spec["slug"], e.detail)
        created.append(spec["slug"])
    if created:
        logger.info("Publicaciones de muestra creadas o actualizadas: %s", ", ".join(created))
    return created


def main() -> None:
    from app.core.config import get_settings

    settings = get_settings()

    async def run() -> list[str]:
        database = settings.effective_database_url
        owner = await UserStore(database).ensure_superadmin(settings.auth_username, settings.auth_password)
        projects = ProjectStore(database)
        created = await seed_demo(projects, owner.id)
        created += await seed_demo_posts(projects, PostStore(database), JobStore(database), owner, settings)
        return created

    created = asyncio.run(run())
    print(f"Creados: {', '.join(created)}" if created else "Los datos de muestra ya existían.")
    print("Para completar las figuras de las publicaciones de muestra, ejecuta y vuelve a publicar:")
    for project, calc in DEMO_CALCULATIONS:
        print(f"  - {project}: {calc}")


if __name__ == "__main__":
    main()
