"""Proyectos por defecto: los trabajos de referencia del grupo.

Crea, si no existen (se reconocen por el nombre):
- «forgethreads · Aceleración CUDA»: proyecto de simulación abierto para replicar los MaxMin y los
  barridos de N y c del trabajo de forgethreads (feCUDA/comprobacion_empirica).
- «Tesis Claudio Araya · Valdivia»: proyecto de expertos cerrado con los 10 informantes clave del
  estudio de contaminación atmosférica de Valdivia que trae forgeffects (`load_test_data`), con
  16 incentivos (a1…a16) y 4 conductas (b1…b4), los mismos nombres que usa forgeffects.
- Una publicación del blog por proyecto (`seed_default_posts`), ya publicada. Los proyectos no traen
  cálculos: las figuras buscan los de `DEFAULT_CALCULATIONS` y se ven «en preparación» hasta que se
  ejecutan y se vuelve a publicar.

A diferencia de los datos de muestra, se crean siempre al iniciar (SEED_DEFAULT_PROJECTS=true por
defecto) y quedan a nombre del superadmin. Si se borra uno, vuelve a crearse al reiniciar.
También se puede correr a mano: `uv run python -m app.services.default_projects`.
"""

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path

from app.core.config import Settings
from app.services.demo_seed import seed_demo_posts
from app.services.fe_input import load_fe_input
from app.services.job_store import JobStore
from app.services.post_store import PostStore
from app.services.project_store import ProjectStore
from app.services.user_store import User

logger = logging.getLogger(__name__)

FORGETHREADS_PROJECT = "forgethreads · Aceleración CUDA"
THESIS_PROJECT = "Tesis Claudio Araya · Valdivia"

FORGETHREADS_DESCRIPTION = (
    "Aceleración de búsqueda de efectos olvidados en grafos utilizando CUDA (Carlos Riquelme Ávila, "
    "Universidad Católica de Temuco). forgethreads paraleliza en GPU la convolución max-min sobre las "
    "celdas de la matriz de adyacencia y logra entre 5,7× y 17× frente a forgeffects, con un límite "
    "práctico de 2000 nodos. Este proyecto replica la comprobación empírica: MaxMin sobre redes "
    "aleatorias y barridos de N y c para medir el orden efectivo."
)
THESIS_DESCRIPTION = (
    "Tesis de Claudio Araya Toro (Universidad Católica de Temuco), autor de forgeffects con Julio Rojas "
    "Mora. Datos del estudio «Application of the Forgotten Effects Theory for Assessing the Public Policy "
    "on Air Pollution of the Commune of Valdivia, Chile» (Manna, Rojas-Mora y Mondaca-Marino, 2018): "
    "10 informantes clave evaluaron 16 incentivos (a1…a16) y 4 conductas (b1…b4). Son las matrices que "
    "trae forgeffects (load_test_data); el cálculo agregado reproduce el ejemplo de su README."
)

THESIS_DATA = Path(__file__).parent / "default_data" / "valdivia.json"
THESIS_CAUSES = [f"a{i}" for i in range(1, 17)]
THESIS_EFFECTS = [f"b{i}" for i in range(1, 5)]


def thesis_inputs() -> list[dict[str, list[list[float]]]]:
    """Matrices CC, CE y EE de cada informante, en el orden del dataset."""
    return json.loads(THESIS_DATA.read_text(encoding="utf-8"))["informantes"]


def _csv(labels_rows: list[str], labels_cols: list[str], matrix: list[list[float]]) -> str:
    rows = ["," + ",".join(labels_cols)]
    rows += [f"{label}," + ",".join(str(v) for v in row) for label, row in zip(labels_rows, matrix)]
    return "\n".join(rows) + "\n"


async def seed_default_projects(projects: ProjectStore, owner_id: int | None = None) -> list[str]:
    """Crea los proyectos por defecto que falten. Devuelve los nombres de los que creó."""
    existing = {p.nombre for p in await projects.list_projects()}
    created: list[str] = []

    if FORGETHREADS_PROJECT not in existing:
        await projects.create_project(
            nombre=FORGETHREADS_PROJECT,
            descripcion=FORGETHREADS_DESCRIPTION,
            tipo="simulacion",
            owner_id=owner_id,
        )
        created.append(FORGETHREADS_PROJECT)

    if THESIS_PROJECT not in existing:
        # Se validan todas las matrices antes de crear el proyecto, para no dejarlo a medias.
        parsed = []
        for i, item in enumerate(thesis_inputs(), start=1):
            data = load_fe_input(
                _csv(THESIS_CAUSES, THESIS_CAUSES, item["cc"]),
                _csv(THESIS_CAUSES, THESIS_EFFECTS, item["ce"]),
                _csv(THESIS_EFFECTS, THESIS_EFFECTS, item["ee"]),
                causes=THESIS_CAUSES,
                effects=THESIS_EFFECTS,
            )
            if not data.valid:  # no debería pasar: son matrices fijas
                raise ValueError(f"Matrices del informante {i} inválidas: {[e.message for e in data.errors]}")
            parsed.append(data)
        project = await projects.create_project(
            nombre=THESIS_PROJECT,
            descripcion=THESIS_DESCRIPTION,
            tipo="expertos",
            owner_id=owner_id,
            causas=THESIS_CAUSES,
            efectos=THESIS_EFFECTS,
        )
        await projects.update_project(project.id, estado="abierto")
        for i, data in enumerate(parsed, start=1):
            _, token = await projects.create_invitation(
                project.id, f"informante{i}@valdivia.example", timedelta(days=1), nombre=f"Informante {i}"
            )
            await projects.submit_input(token, data.cc.tolist(), data.ce.tolist(), data.ee.tolist())
        # Los datos del estudio están completos: no se esperan más respuestas.
        await projects.update_project(project.id, estado="cerrado")
        created.append(THESIS_PROJECT)

    if created:
        logger.info("Proyectos por defecto creados: %s", ", ".join(created))
    return created


# Cálculos que buscan las publicaciones: ejecutarlos y volver a publicar. Los parámetros son los
# de los trabajos originales (THR 0,5: test2.py de feCUDA y el ejemplo del README de forgeffects).
DEFAULT_CALCULATIONS = [
    (FORGETHREADS_PROJECT, "MaxMin con N = 1000, c = 2,5 y THR 0,5"),
    (FORGETHREADS_PROJECT, "barrido con THR 0,5 (por ejemplo N = 100…10000 y c = 0,125…50, 20 repeticiones)"),
    (THESIS_PROJECT, "cálculo agregado de los 10 informantes con THR 0,5, orden máximo 3 y 10000 repeticiones"),
    (THESIS_PROJECT, "cálculo por experto con THR 0,5 y orden máximo 3 (al menos un informante)"),
]

_MAXMIN_QUERY = {"kind": "maxmin", "n": 1000, "c": 2.5, "thr": 0.5,
                 "descripcion": "MaxMin sobre una red de 1000 nodos con c = 2,5 y THR 0,5"}
_SWEEP_QUERY = {"kind": "sweep", "thr": 0.5, "descripcion": "Barrido de N y c con THR 0,5"}
_AGGREGATE_QUERY = {"kind": "fe", "agregado": True, "thr": 0.5, "maxorder": 3,
                    "descripcion": "Cálculo agregado de los informantes con THR 0,5 y orden máximo 3"}
_EXPERT_QUERY = {"kind": "fe", "agregado": False, "thr": 0.5, "maxorder": 3,
                 "descripcion": "Cálculo por informante con THR 0,5 y orden máximo 3"}

DEFAULT_POSTS = [
    {
        "project": FORGETHREADS_PROJECT,
        "titulo": "forgethreads: aceleración de la búsqueda de efectos olvidados con CUDA",
        "slug": "forgethreads-aceleracion-cuda",
        "autores": "Carlos Riquelme Ávila",
        "palabras_clave": ["efectos olvidados", "CUDA", "GPU", "convolución max-min", "forgethreads"],
        "resumen": (
            "Los efectos olvidados son relaciones indirectas entre pares de nodos cuya intensidad supera la de "
            "su conexión directa. Su cálculo mediante convoluciones max-min iterativas cuesta O(n³) por orden, "
            "lo que limita la escala de las soluciones existentes. forgethreads paraleliza en GPU la convolución "
            "max-min sobre las celdas de la matriz de adyacencia: es entre 5,7× y 17× más rápido que forgeffects "
            "y procesa grafos de hasta 2000 nodos, frente a los 1200 de la referencia. Las figuras replican en "
            "el cluster la comprobación empírica del orden efectivo."
        ),
        "bloques": [
            (
                "## Introducción\n\nEn un grafo ponderado, un **efecto olvidado** es un camino indirecto entre dos "
                "nodos cuya intensidad, medida con la convolución max-min, supera la de la arista directa. "
                "Las soluciones existentes, [forgeffects](https://github.com/claudio-araya/forgeffects) (Python y "
                "TensorFlow) y foRgotten (R y C++), dan resultados correctos en grafos medianos, pero forgeffects "
                "agota la memoria de video sobre los 1240 nodos y foRgotten opera solo en CPU.\n\n"
                "**forgethreads** es un módulo en CUDA C++ con interfaz Python (PyBind11 y DLpack) que acelera esa "
                "búsqueda en GPU."
            ),
            (
                "## Metodología\n\nCada hilo CUDA calcula una celda de la convolución\n\n"
                "`C[b,i,j] = max_k min(A[b,i,k], B[b,k,j])`\n\n"
                "y registra un efecto olvidado cuando `C[b,i,j] − A[b,i,j] ≥ θ`. El kernel no materializa el "
                "tensor intermedio de n × n × n que usa forgeffects, así que la memoria es O(n²) en vez de O(n³), y "
                "el cálculo termina antes del orden máximo si un orden no descubre aristas nuevas.\n\n"
                "Para la comprobación empírica se generan redes aleatorias de N nodos con conectividad promedio c y "
                "se aplica MaxMin con un umbral de 0,5, registrando el **orden efectivo** (el último orden con "
                "efectos nuevos). Un barrido repite el cálculo para varias combinaciones de N y c."
            ),
            (
                "## Resultados\n\nEn una RTX 4090 (θ = 0,3, hasta orden 4, media de 10 ejecuciones):\n\n"
                "| Nodos | forgeffects (ms) | forgethreads (ms) | Aceleración |\n|---:|---:|---:|---:|\n"
                "| 200 | 10,2 | 0,6 | 17,0× |\n| 400 | 29,1 | 3,5 | 8,3× |\n| 600 | 81,6 | 11,1 | 7,4× |\n"
                "| 800 | 181,1 | 25,7 | 7,0× |\n| 1000 | 345,0 | 55,5 | 6,2× |\n| 1200 | 594,2 | 103,8 | 5,7× |\n"
                "| 1600 | — | 236,8 | — |\n| 2000 | — | 442,3 | — |\n\n"
                "«—»: forgeffects no termina por falta de memoria de video.\n\n"
                "La {{fig:m1}} muestra los efectos encontrados en cada orden para una red de 1000 nodos. "
                "La {{fig:s1}} resume el barrido: el orden efectivo medio según c, para cada N, y la "
                "{{fig:s2}} lo muestra según N, para cada c."
            ),
            "## Discusión\n\n_Al ejecutar el barrido, compara el orden efectivo con el de feCUDA/comprobacion_empirica._",
            (
                "## Conclusiones\n\n- forgethreads es entre 5,7× y 17× más rápido que forgeffects entre 200 y 1200 "
                "nodos.\n- Bajar la memoria de O(n³) a O(n²) permite llegar a 2000 nodos.\n"
                "- Trabajo futuro: varias GPU, grafos dinámicos, comparación con foRgotten y grafos dispersos (CSR)."
            ),
            (
                "## Referencias\n\n- Kaufmann, A. y Gil Aluja, J. (1988). _Modelos para la investigación de efectos "
                "olvidados_. Milladoiro.\n- Araya Toro, C. y Rojas Mora, J. (2024). _forgeffects: A Python Package for "
                "Analyzing Forgotten and Direct Effects_. Universidad Católica de Temuco.\n"
                "- Mardones-Arias, E. y Rojas-Mora, J. (2022). _foRgotten_. Paquete de R, versión 1.1.0."
            ),
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
    {
        "project": THESIS_PROJECT,
        "titulo": "Efectos olvidados en la política de contaminación atmosférica de Valdivia",
        "slug": "tesis-claudio-araya-valdivia",
        "autores": "Claudio Araya Toro",
        "palabras_clave": ["efectos olvidados", "forgeffects", "contaminación atmosférica", "Valdivia",
                           "informantes clave"],
        "resumen": (
            "Diez informantes clave evaluaron cómo 16 incentivos de la política pública de contaminación "
            "atmosférica de Valdivia inciden entre sí y sobre 4 conductas. Con forgeffects se agregan sus "
            "matrices en tensores y se buscan los efectos olvidados (caminos indirectos más fuertes que la "
            "relación directa) mediante remuestreo. Las figuras se completan al ejecutar el cálculo agregado con "
            "THR 0,5 y orden máximo 3, los parámetros del ejemplo de forgeffects."
        ),
        "bloques": [
            (
                "## Introducción\n\nLa teoría de los efectos olvidados (Kaufmann y Gil Aluja, 1988) busca "
                "relaciones de causalidad indirectas que se pasan por alto al planificar. Este trabajo la aplica a "
                "la política de contaminación atmosférica de la comuna de Valdivia, a partir del estudio de Manna, "
                "Rojas-Mora y Mondaca-Marino (2018), con [forgeffects](https://github.com/claudio-araya/forgeffects), "
                "un paquete de Python que agrega las opiniones de varios informantes en tensores 3D."
            ),
            (
                "## Metodología\n\nCada informante completó tres matrices de incidencia con valores de 0 a 1:\n\n"
                "| Matriz | Relaciona | Tamaño |\n|---|---|---|\n| CC | incentivos con incentivos | 16 × 16 |\n"
                "| CE | incentivos con conductas | 16 × 4 |\n| EE | conductas con conductas | 4 × 4 |\n\n"
                "Las matrices de los 10 informantes se apilan en tensores (informantes × filas × columnas) que "
                "forman un grafo bipartito encadenado. Se aplican convoluciones max-min iterativas y, con remuestreo "
                "empírico, se cuenta cuántas veces aparece cada camino (`Count`) y su incidencia media (`Mean`) y "
                "desviación (`SD`). Un camino es efecto olvidado si supera el umbral de significancia (THR = 0,5), "
                "hasta caminos de 3 pasos."
            ),
            (
                "## Resultados\n\nLa {{fig:f1}} muestra los efectos olvidados de orden 2 más fuertes del cálculo "
                "agregado: cada franja parte en un incentivo, pasa por un intermediario y llega a otro incentivo o "
                "a una conducta. Haz clic en un origen para ver solo sus caminos.\n\nLa {{fig:f2}} reúne todos los "
                "órdenes en un hive plot, y la {{fig:f3}} muestra los caminos de un solo informante, para comparar "
                "con el resultado agregado."
            ),
            (
                "## Discusión\n\n_Al ejecutar los cálculos, compara los caminos de orden 2 con los del README de "
                "forgeffects (por ejemplo a10 → a11 → a14 y a13 → a16 → a1)._"
            ),
            "## Conclusiones\n\n_Completa esta sección cuando las figuras tengan datos._",
            (
                "## Referencias\n\n- Kaufmann, A. y Gil Aluja, J. (1988). _Modelos para la investigación de efectos "
                "olvidados_. Milladoiro.\n- Manna, E. M., Rojas-Mora, J. y Mondaca-Marino, C. (2018). Application of "
                "the Forgotten Effects Theory for Assessing the Public Policy on Air Pollution of the Commune of "
                "Valdivia, Chile. En _From Science to Society_ (pp. 61-72). Springer.\n"
                "- Araya Toro, C. y Rojas Mora, J. (2024). _forgeffects: A Python Package for Analyzing Forgotten "
                "and Direct Effects_. Universidad Católica de Temuco."
            ),
        ],
        "figuras": [
            {"ref": "f1", "buscar": _AGGREGATE_QUERY, "vista": "sankey", "orden": 2, "top": 50, "publico": True,
             "en_cuerpo": True, "descargar_entradas": True, "descargar_resultados": True,
             "pie": "Efectos olvidados de orden 2 con mayor incidencia media (10 informantes)."},
            {"ref": "f2", "buscar": _AGGREGATE_QUERY, "vista": "hive", "top": 50, "publico": True,
             "pie": "Todos los órdenes; el color indica el orden y el grosor la incidencia."},
            {"ref": "f3", "buscar": _EXPERT_QUERY, "vista": "sankey", "orden": 2, "top": 50, "publico": True,
             "pie": "Caminos de orden 2 de un informante (el cálculo por informante más reciente)."},
        ],
    },
]


async def seed_default_posts(
    projects: ProjectStore,
    posts: PostStore,
    jobs: JobStore,
    owner: User,
    settings: Settings,
    pool=None,
) -> list[str]:
    """Crea y publica las publicaciones de los proyectos por defecto que falten. Devuelve sus direcciones."""
    return await seed_demo_posts(projects, posts, jobs, owner, settings, pool, specs=DEFAULT_POSTS)


def main() -> None:
    from app.core.config import get_settings
    from app.services.user_store import UserStore

    settings = get_settings()

    async def run() -> list[str]:
        database = settings.effective_database_url
        owner = await UserStore(database).ensure_superadmin(settings.auth_username, settings.auth_password)
        projects = ProjectStore(database)
        created = await seed_default_projects(projects, owner.id)
        created += await seed_default_posts(projects, PostStore(database), JobStore(database), owner, settings)
        return created

    created = asyncio.run(run())
    print(f"Creados: {', '.join(created)}" if created else "Los proyectos por defecto ya existían.")
    print("Para completar las figuras de sus publicaciones, ejecuta y vuelve a publicar:")
    for project, calc in DEFAULT_CALCULATIONS:
        print(f"  - {project}: {calc}")


if __name__ == "__main__":
    main()
