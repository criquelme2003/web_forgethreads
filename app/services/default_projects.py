"""Proyectos por defecto: los trabajos de referencia del grupo.

Crea, si no existen (se reconocen por el nombre):
- «forgethreads · Aceleración CUDA»: proyecto de simulación abierto para replicar los MaxMin y los
  barridos de N y c del trabajo de forgethreads (feCUDA/comprobacion_empirica).
- «Tesis Claudio Araya · Valdivia»: proyecto de expertos cerrado con los 10 informantes clave del
  estudio de contaminación atmosférica de Valdivia que trae forgeffects (`load_test_data`), con
  16 incentivos (a1…a16) y 4 conductas (b1…b4), los mismos nombres que usa forgeffects.
- El barrido de la comprobación empírica de forgethreads, ya terminado (`seed_forgethreads_sweep`):
  un job `sweep` con el CSV `sweep_n_block_merged_order.csv` de feCUDA (7 N × 19 c, 20 repeticiones,
  THR 0,5), importado tal cual en vez de ejecutarlo en el cluster.
- Una publicación del blog por proyecto (`seed_default_posts`), ya publicada. Sus figuras buscan los
  cálculos de `DEFAULT_CALCULATIONS`: las del barrido salen del importado y el resto se ven «en
  preparación» hasta que se ejecutan y se vuelve a publicar.

A diferencia de los datos de muestra, se crean siempre al iniciar (SEED_DEFAULT_PROJECTS=true por
defecto) y quedan a nombre del superadmin. Si se borra uno, vuelve a crearse al reiniciar.
También se puede correr a mano: `uv run python -m app.services.default_projects`.
"""

import asyncio
import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from app.core.config import Settings
from app.services.demo_seed import seed_demo_posts
from app.services.fe_input import load_fe_input
from app.services.job_store import JobNotFoundError, JobStore
from app.services.post_store import PostStore
from app.services.project_store import ProjectStore
from app.services.sweep import combination_problem
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

# Barrido importado de feCUDA/comprobacion_empirica. El ID queda fuera del rango de SLURM
# (MaxJobId por defecto: 67043328) para que nunca choque con un job real.
SWEEP_DATA = Path(__file__).parent / "default_data" / "forgethreads_sweep.csv"
SWEEP_JOB_ID = "990000001"
SWEEP_NS = [100, 250, 500, 1000, 2500, 5000, 10000]
SWEEP_CS = [0.125, 0.25, 0.5, 1.0, 1.125, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 4.0, 6.0, 8.0, 10.0, 25.0, 50.0]
SWEEP_REPS = 20
SWEEP_THR = 0.5
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
    (FORGETHREADS_PROJECT, "MaxMin con N = 1000, c = 2,5 y THR 0,5 (el barrido ya viene importado)"),
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
            "y procesa grafos de hasta 2000 nodos, frente a los 1200 de la referencia. Las figuras del barrido "
            "muestran la comprobación empírica del orden efectivo: 7 tamaños de red, 19 conectividades y 20 "
            "repeticiones por combinación."
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
                "efectos nuevos). El barrido cubre N = 100, 250, 500, 1000, 2500, 5000 y 10000 y c entre 0,125 y "
                "50, con 20 repeticiones por combinación (132 combinaciones, 2640 ejecuciones)."
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
            (
                "## Discusión\n\nEl orden efectivo es bajo con conectividad baja (c < 1), sube con fuerza en la "
                "ventana 1 ≤ c ≤ 3 y vuelve a bajar cuando dominan los atajos (c > 3). El máximo crece con N: "
                "de unos 12 órdenes con 100 nodos a cerca de 80 con 10000."
            ),
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
            "La política de descontaminación de Valdivia usa incentivos para cambiar la conducta de los hogares, "
            "pero la contaminación por uso de leña siguió aumentando: un problema complejo y con incertidumbre en "
            "el que muchas influencias son indirectas. Diez informantes clave evaluaron cómo 16 incentivos inciden "
            "entre sí y sobre 4 conductas, y con forgeffects se agregaron sus evaluaciones en tensores para buscar "
            "los efectos olvidados mediante remuestreo empírico. El estudio concluye que el subsidio al transporte "
            "no reduce la contaminación residencial, que mejorar la aislación térmica de las viviendas la reduce "
            "más que mejorar la calidad de la leña, y que la educación y la investigación y desarrollo son claves "
            "para el cambio de conducta y la eficiencia energética."
        ),
        "bloques": [
            (
                "## Introducción\n\nValdivia enfrenta altos niveles de contaminación atmosférica en invierno, "
                "originados sobre todo en el uso residencial de leña para calefacción. La política pública aplicada "
                "se apoya en **incentivos** para que los hogares cambien su conducta; aun así, la contaminación "
                "siguió aumentando. Es un problema de política ambiental complejo, con incertidumbre y con "
                "relaciones que no son directas: un incentivo puede actuar a través de otros incentivos o conductas "
                "antes de llegar al efecto buscado.\n\n"
                "La teoría de los efectos olvidados (Kaufmann y Gil Aluja, 1988) está pensada para ese tipo de "
                "relaciones: busca las incidencias indirectas que se pasan por alto al planificar. Este trabajo la "
                "aplica a los datos del estudio de Manna, Rojas-Mora y Mondaca-Marino (2018) con "
                "[forgeffects](https://github.com/claudio-araya/forgeffects), un paquete de Python que agrega las "
                "evaluaciones de varios informantes clave en tensores 3D y aprovecha la GPU si está disponible."
            ),
            (
                "## Metodología\n\nDiez informantes clave completaron tres matrices de incidencia con valores "
                "de 0 a 1:\n\n"
                "| Matriz | Relaciona | Tamaño |\n|---|---|---|\n| CC | incentivos con incentivos | 16 × 16 |\n"
                "| CE | incentivos con conductas | 16 × 4 |\n| EE | conductas con conductas | 4 × 4 |\n\n"
                "Los incentivos se nombran a1…a16 y las conductas b1…b4. Las matrices de los informantes se apilan "
                "en tensores (informantes × filas × columnas) que forman un **grafo bipartito encadenado**: "
                "incentivos → incentivos → conductas → conductas.\n\n"
                "La función `FE()` de forgeffects aplica convoluciones max-min iterativas sobre ese grafo. Con "
                "**remuestreo empírico** genera 10 000 réplicas: en cada una, el valor de cada celda se toma de un "
                "percentil al azar de lo que respondieron los informantes en esa celda. Sobre las réplicas registra, "
                "para cada camino indirecto, en cuántas aparece (`Count`), su incidencia media (`Mean`) y su "
                "desviación estándar (`SD`). Un camino es un efecto olvidado si su incidencia supera el grado de verdad "
                "**THR = 0,5** y la de la relación directa; se exploran caminos de hasta **3 pasos** (órdenes 2 y "
                "3). La función `directEffects()` complementa el análisis con las incidencias directas medias, sus "
                "intervalos de confianza y una prueba t unilateral con bootstrap."
            ),
            (
                "## Resultados\n\nCon estos parámetros, la documentación de forgeffects reporta **3725 efectos "
                "olvidados de orden 2**. Los que más se repiten entre las réplicas son:\n\n"
                "| Desde | A través de | Hasta | Count | Mean | SD |\n|:-:|:-:|:-:|--:|--:|--:|\n"
                "| a10 | a11 | a14 | 2134 | 0,660 | 0,088 |\n| a13 | a16 | a1 | 2038 | 0,639 | 0,080 |\n"
                "| a3 | a6 | a9 | 1910 | 0,642 | 0,081 |\n| a8 | a13 | a10 | 1905 | 0,714 | 0,101 |\n"
                "| a13 | a6 | a9 | 1698 | 0,662 | 0,083 |\n\n"
                "El análisis de efectos directos entrega 316 relaciones directas (entre incentivos, de incentivos a "
                "conductas y entre conductas) con su media, intervalo de confianza y valor p.\n\n"
                "La {{fig:f1}} muestra los efectos olvidados de orden 2 más fuertes del cálculo agregado: cada "
                "franja parte en un incentivo, pasa por un intermediario y llega a otro incentivo o a una conducta. "
                "Haz clic en un origen para ver solo sus caminos. La {{fig:f2}} reúne todos los órdenes en un hive "
                "plot, y la {{fig:f3}} muestra los caminos de un solo informante, para comparar con el resultado "
                "agregado."
            ),
            (
                "## Discusión\n\nLos caminos más frecuentes son **incentivo → incentivo → incentivo**: buena parte "
                "del efecto de la política no llega directo a las conductas, sino a través de otros incentivos que "
                "lo refuerzan. Por eso evaluar cada incentivo por separado subestima su alcance, y quitar uno puede "
                "debilitar a otros que dependen de él.\n\n"
                "El remuestreo sobre la distribución de las respuestas permite distinguir los caminos que aparecen "
                "en muchas réplicas (`Count` alto), respaldados por el conjunto de informantes, de los que dependen de "
                "respuestas extremas, y la desviación estándar muestra cuánto varía su intensidad. Comparar el cálculo agregado con el de un informante ({{fig:f3}}) muestra "
                "cuánto cambia el diagnóstico si se consulta a una sola persona."
            ),
            (
                "## Conclusiones\n\n"
                "- El **subsidio al transporte** no tiene efecto en la reducción de la contaminación residencial.\n"
                "- Mejorar la **aislación térmica** de las viviendas puede reducir la contaminación más que mejorar "
                "la **calidad de la leña**.\n"
                "- La **educación** y la **investigación y desarrollo** pueden cumplir un papel importante para "
                "promover el cambio de conducta y mejorar la eficiencia energética.\n"
                "- Los efectos olvidados muestran que los incentivos actúan sobre todo a través de otros incentivos: "
                "la política debe diseñarse como un conjunto y no como medidas aisladas.\n"
                "- forgeffects lleva este análisis a Python con tensores: agrega a varios informantes, entrega la "
                "frecuencia, la media y la desviación de cada camino y puede usar la GPU."
            ),
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
    """Importa el barrido de forgethreads y crea y publica las publicaciones que falten.

    Si el barrido se importa recién y la publicación de forgethreads ya estaba publicada sin cambios
    pendientes, la vuelve a publicar para que sus figuras lo muestren. Devuelve las direcciones creadas.
    """
    imported = await seed_forgethreads_sweep(projects, jobs, settings, owner.id)
    created = await seed_demo_posts(projects, posts, jobs, owner, settings, pool, specs=DEFAULT_POSTS)
    if imported:
        await _republish(DEFAULT_POSTS[0]["slug"], projects, posts, jobs, owner, settings, pool)
    return created


async def seed_forgethreads_sweep(
    projects: ProjectStore, jobs: JobStore, settings: Settings, owner_id: int | None = None
) -> bool:
    """Registra el barrido de feCUDA como un job `sweep` terminado del proyecto de forgethreads.

    Deja su CSV crudo en `sweep_results_dir`, donde la app lo busca antes de ir al nodo; si el
    archivo falta (por ejemplo, otro volumen), lo vuelve a copiar. Devuelve True si creó el job.
    """
    project = next((p for p in await projects.list_projects() if p.nombre == FORGETHREADS_PROJECT), None)
    if project is None:
        return False
    local = Path(settings.sweep_results_dir) / f"{SWEEP_JOB_ID}.csv"
    if not local.is_file():
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SWEEP_DATA, local)
    try:
        await jobs.get(SWEEP_JOB_ID)
        return False
    except JobNotFoundError:
        pass
    valid = [(n, c) for c in SWEEP_CS for n in SWEEP_NS if combination_problem(n, c) is None]
    await jobs.create(
        SWEEP_JOB_ID, node="importado", kind="sweep", project_id=project.id, user_id=owner_id,
        params={
            "ns": SWEEP_NS, "cs": SWEEP_CS, "reps": SWEEP_REPS, "thr": SWEEP_THR, "seed_base": 0,
            "combinaciones": len(valid), "ejecuciones": len(valid) * SWEEP_REPS,
            "origen": "feCUDA/comprobacion_empirica/sweep_n_block_merged_order.csv",
        },
    )
    await jobs.update_from_callback(
        SWEEP_JOB_ID, "success", logs="Resultados importados de feCUDA/comprobacion_empirica (no se ejecutó en el cluster).",
        result={"kind": "sweep", "importado": True},
    )
    logger.info("Barrido de forgethreads importado como job %s", SWEEP_JOB_ID)
    return True


async def _republish(slug, projects, posts, jobs, owner, settings, pool) -> None:
    from fastapi import HTTPException

    from app.api.posts import _Renderer

    post = next((p for p in await posts.list_all() if p.slug == slug), None)
    if post is None or post.publicado is None or post.cambios_sin_publicar:
        return
    project = await projects.get_project(post.project_id)
    try:
        await posts.publish(post.id, await _Renderer(post, project, owner, jobs, projects, settings, pool).render())
    except HTTPException as e:
        logger.warning("No se pudo volver a publicar %s: %s", slug, e.detail)


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
