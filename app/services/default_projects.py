"""Proyectos por defecto: los trabajos de referencia del grupo.

Crea, si no existen (se reconocen por el nombre):
- «forgethreads · Aceleración CUDA»: proyecto de simulación abierto para replicar los MaxMin y los
  barridos de N y c del trabajo de forgethreads (feCUDA/comprobacion_empirica).
- «Tesis Claudio Araya · Valdivia»: proyecto de expertos cerrado con los 10 informantes clave del
  estudio de contaminación atmosférica de Valdivia que trae forgeffects (`load_test_data`), con
  16 incentivos (a1…a16) y 4 conductas (b1…b4), los mismos nombres que usa forgeffects.
- El barrido del experimento de profundidad de parada del paper de forgethreads, ya terminado
  (`seed_forgethreads_sweep`): un job `sweep` con `sweep_n_block_merged_order.csv` de feCUDA (7 N × 19 c,
  20 repeticiones, THR 0,5), con el orden efectivo como η₀ del paper (el CSV de feCUDA cuenta desde 0).
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
    "«CUDA Acceleration and Empirical Stopping-Depth Regimes in Iterative Max–Min Forgotten Effects» "
    "(Riquelme-Ávila, Rojas-Mora y Mardones-Arias, Universidad Católica de Temuco). forgethreads evalúa la "
    "convolución max-min celda a celda en CUDA con memoria cuadrática: es entre 12,8× y 44× más rápido que "
    "forgeffects y completa grafos densos de hasta 32 000 nodos en una RTX 4090. Incluye el experimento de "
    "profundidad de parada: 2640 instancias Bernoulli por bloques (7 N × 19 c × 20 repeticiones, ε = 0,5)."
)
THESIS_DESCRIPTION = (
    "«Cálculo de efectos olvidados con múltiples expertos mediante procesamiento tensorial en Python», "
    "trabajo de título de Claudio Esteban Araya Toro (Ingeniería Civil Informática, Universidad Católica de "
    "Temuco, enero de 2025; profesor guía: Julio Rojas Mora). Desarrolla forgeffects y lo valida contra "
    "foRgotten (R) con los datos de Valdivia: 10 expertos, 16 causas (a1…a16) y 4 efectos (b1…b4)."
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


# Cálculos que buscan las publicaciones: ejecutarlos y volver a publicar. Los parámetros son los de
# los trabajos originales: el paper de forgethreads (ε = 0,5) y la validación de la tesis (Cuadro 6.1).
DEFAULT_CALCULATIONS = [
    (FORGETHREADS_PROJECT, "MaxMin con N = 2500, c = 2,5, THR 0,5 y seed 2500 (el barrido ya viene importado)"),
    (THESIS_PROJECT, "cálculo agregado de los 10 expertos con THR 0,5, orden máximo 3 y 16384 repeticiones"),
    (THESIS_PROJECT, "cálculo por experto con THR 0,5 y orden máximo 3 (al menos un experto)"),
]

_MAXMIN_QUERY = {"kind": "maxmin", "n": 2500, "c": 2.5, "thr": 0.5,
                 "descripcion": "MaxMin sobre una red de 2500 nodos con c = 2,5 y THR 0,5"}
_SWEEP_QUERY = {"kind": "sweep", "thr": 0.5, "descripcion": "Barrido de N y c con THR 0,5"}
_AGGREGATE_QUERY = {"kind": "fe", "agregado": True, "thr": 0.5, "maxorder": 3,
                    "descripcion": "Cálculo agregado de los 10 expertos con THR 0,5 y orden máximo 3"}
_EXPERT_QUERY = {"kind": "fe", "agregado": False, "thr": 0.5, "maxorder": 3,
                 "descripcion": "Cálculo por experto con THR 0,5 y orden máximo 3"}

DEFAULT_POSTS = [
    {
        "project": FORGETHREADS_PROJECT,
        "titulo": "Aceleración CUDA y regímenes de profundidad de parada en efectos olvidados max-min",
        "slug": "forgethreads-aceleracion-cuda",
        "autores": "Carlos Riquelme-Ávila, Julio Rojas-Mora y Elliott Mardones-Arias",
        "palabras_clave": ["efectos olvidados", "convolución max-min", "CUDA", "profundidad de parada",
                           "estructura aleatoria por bloques"],
        "resumen": (
            "Calcular efectos olvidados exige convoluciones max-min repetidas sobre una matriz de influencia, "
            "con costo aritmético cúbico por orden y, en las implementaciones que usan broadcasting de tensores, "
            "memoria intermedia también cúbica. forgethreads es una implementación CUDA que evalúa cada "
            "actualización max-min directamente en la celda de salida y guarda solo matrices de tamaño "
            "cuadrático: en instancias densas es entre 12,8× y 44× más rápida que la implementación basada en "
            "TensorFlow y amplía el mayor tamaño completado de 1200 a 32 000 nodos en una RTX 4090. El "
            "experimento de profundidad de parada, con 2640 instancias Bernoulli por bloques, muestra un perfil "
            "de dos ventanas que depende del tamaño: las profundidades altas aparecen cerca de dos ventanas de "
            "grado efectivo por bloque (c ≈ 1 y c ≈ 2), con un valle entre ellas y un descenso dominado por "
            "atajos para c grande."
        ),
        "bloques": [
            (
                "## Introducción\n\nEl método de los efectos olvidados (Kaufmann y Gil-Aluja, 1988) estudia "
                "influencias indirectas que no se ven en la matriz de incidencia directa. Dada una matriz de "
                "influencia E ∈ [0, 1]^(N×N), aplica repetidamente la convolución max-min para obtener los efectos "
                "indirectos acumulados entre pares de nodos.\n\n"
                "El costo es alto: cada orden evalúa N² celdas con una reducción sobre N nodos intermedios, O(N³). "
                "La entrada ocupa O(N²), pero la convolución de forgeffects (TensorFlow) materializa los valores "
                "candidatos de todos los tríos (i, k, j), es decir O(N³) de memoria intermedia, lo que limita el "
                "tamaño de grafo incluso en GPU de gama alta.\n\n"
                "Este trabajo hace dos aportes conectados: una implementación CUDA con memoria de trabajo "
                "cuadrática y un estudio empírico de **η₀**, la profundidad de parada: el último orden de "
                "propagación en que todavía aparece un efecto significativo. η₀ fija cuántos pasos max-min hay "
                "que ejecutar y acota el orden en que puede surgir un efecto olvidado."
            ),
            (
                "## Metodología\n\n**Propagación.** E⁽¹⁾ = E y E⁽ʳ⁺¹⁾ᵢⱼ = maxₖ min(E⁽ʳ⁾ᵢₖ, Eₖⱼ), con Eᵢᵢ = 1 "
                "para que la iteración sea acumulativa. Los incrementos son Δ⁽ʳ⁾ = E⁽ʳ⁾ − E⁽ʳ⁻¹⁾; el par (i, j) "
                "tiene un incremento significativo en el orden r si Δ⁽ʳ⁾ᵢⱼ > ε. Con q_r, el número de incrementos "
                "significativos del orden r, la propagación se detiene cuando q_r = 0 y η₀ es el último orden con "
                "q_r > 0. Con ε = 0,5 cada par puede tener a lo más un incremento significativo, así que η₀ es una "
                "profundidad determinista.\n\n"
                "**Kernel CUDA.** Cada bloque CUDA calcula una celda (i, j): sus hilos recorren subconjuntos del "
                "índice intermedio k cargados en memoria compartida, reducen min(E⁽ʳ⁾ᵢₖ, Eₖⱼ), guardan el argmax "
                "para reconstruir caminos y suman atómicamente q_r si el incremento supera ε. Solo se guardan E, "
                "E⁽ʳ⁾, E⁽ʳ⁺¹⁾, el argmax y el contador: memoria O(N²). La interfaz Python usa PyBind11 y DLPack.\n\n"
                "**Diseño Bernoulli por bloques.** Los nodos se dividen en bloques N y M (α = 0,5) con estructura "
                "unidireccional: N → N, N → M y M → M, sin retorno de M a N. Las entradas fuera de la diagonal son "
                "binarias y se sortean con p_N = c/(n − 1) y p_M = c/(n_M − 1), de modo que c es el grado de "
                "salida esperado de cada fila. En el diseño balanceado d_MM = c y d_NN ≈ c/2: los grados efectivos "
                "cruzan 1 cerca de c = 1 (bloque M) y de c = 2 (bloque N). El barrido usa "
                "n ∈ {100, 250, 500, 1000, 2500, 5000, 10000} y c ∈ {1/8, 1/4, 1/2, 1, 9/8, 5/4, 3/2, 7/4, 2, 9/4, "
                "5/2, 11/4, 3, 4, 6, 8, 10, 25, 50}, con 20 repeticiones por celda: 132 celdas admisibles (se "
                "excluye (100, 50), donde p_M > 1) y 2640 observaciones."
            ),
            (
                "## Resultados\n\n**Tiempo de ejecución** (RTX 4090, ms, media de 10 ejecuciones tras un "
                "calentamiento; caminos sin armar en ambas implementaciones):\n\n"
                "| Nodos | forgethreads | forgeffects | Aceleración |\n|--:|--:|--:|--:|\n"
                "| 150 | 0,183 | 8,050 | 44,02× |\n| 300 | 0,226 | 8,885 | 39,31× |\n"
                "| 600 | 0,753 | 15,578 | 20,67× |\n| 700 | 1,019 | 19,778 | 19,40× |\n"
                "| 900 | 1,874 | 40,612 | 21,68× |\n| 1200 | 4,465 | 57,175 | 12,80× |\n\n"
                "**Memoria.** forgeffects completa 1200 nodos y se queda sin memoria desde 1300; forgethreads "
                "completa todos los tamaños probados, de 1300 a 32 000 nodos.\n\n"
                "**Profundidad de parada por régimen** (media, cuantil 0,95 y máximo sobre todas las "
                "observaciones):\n\n"
                "| Régimen | Intervalo | Celdas | Media (Q.95) | Máx. |\n|---|---|--:|--:|--:|\n"
                "| Baja conectividad | c < 1 | 21 | 4,97 (10,0) | 13 |\n"
                "| Ventana del bloque M | 1 ≤ c ≤ 5/4 | 21 | 34,87 (79,0) | 101 |\n"
                "| Interumbral | 5/4 < c < 2 | 14 | 28,99 (49,0) | 57 |\n"
                "| Ventana del bloque N | 2 ≤ c ≤ 3 | 35 | 33,64 (70,0) | 102 |\n"
                "| Dominado por atajos | c > 3 | 41 | 10,38 (25,0) | 34 |\n\n"
                "La separación entre las ventanas y el valle depende del tamaño: con n ≤ 500 los contrastes "
                "ventana − valle son negativos o no concluyentes, cerca de n = 1000 se acercan a cero y desde "
                "n = 2500 ambos intervalos bootstrap son positivos; en n = 10 000 llegan a 23,9 (ventana M) y 15,4 "
                "(ventana N).\n\n"
                "La {{fig:s1}} muestra el orden efectivo medio según c para cada N, como la figura 1 del paper, y "
                "la {{fig:s2}} lo muestra según N para cada c. La {{fig:m1}} muestra q_r, los efectos encontrados "
                "en cada orden, para una instancia de 2500 nodos con c = 5/2, en la ventana del bloque N."
            ),
            (
                "## Discusión\n\nAumentar c no hace simplemente más profunda la propagación. Dentro del diseño "
                "muestreado, las profundidades altas aparecen cerca de las dos ventanas de grado efectivo por "
                "bloque, separadas por un valle interumbral y seguidas de un descenso dominado por atajos. El perfil "
                "se explica mejor por los grados efectivos de cada bloque que por la conectividad global.\n\n"
                "Los conteos q_r muestran que η₀ es una propiedad del borde derecho de la distribución de "
                "descubrimientos: en n = 2500 el total de descubrimientos sigue creciendo de c = 2 a c = 3, pero la "
                "profundidad media cae de 49,00 en c = 5/2 a 36,95 en c = 3, porque la conectividad adicional "
                "adelanta los descubrimientos a órdenes más tempranos.\n\n"
                "El diseño binario es un diseño de control para el criterio de parada: separa la propagación del "
                "soporte de la variabilidad de los pesos. En matrices difusas con pesos, los cruces de umbral "
                "dependen también de la distribución de los pesos."
            ),
            (
                "## Conclusiones\n\n- forgethreads evita los tensores intermedios cúbicos con propagación max-min "
                "en la celda de salida: en la RTX 4090 obtuvo aceleraciones de 12,8× a 44× frente a la referencia "
                "TensorFlow en media precisión y completó instancias densas de hasta 32 000 nodos.\n"
                "- En 2640 instancias Bernoulli por bloques, las profundidades de parada altas aparecen cerca de "
                "dos ventanas de grado efectivo, separadas por un valle interumbral y seguidas de un descenso "
                "dominado por atajos; el perfil es consistente con una organización por grados efectivos de bloque "
                "y no solo por la conectividad global.\n"
                "- Un mayor número de descubrimientos no implica una mayor profundidad de parada cuando los "
                "descubrimientos se desplazan a órdenes más tempranos.\n"
                "- Trabajo futuro: describir la profundidad de parada por regímenes de grado efectivo, probar "
                "bloques no balanceados y tamaños mayores, registrar profundidades por clase de par y desarrollar "
                "kernels para grafos dispersos (listas de adyacencia o CSR)."
            ),
            (
                "## Referencias\n\n- Kaufmann, A. y Gil-Aluja, J. (1988). _Models for the Research of Forgotten "
                "Effects_. Milladoiro.\n- Chávez-Bustamante, F., Mardones-Arias, E., Rojas-Mora, J. y Tijmes-Ihl, J. "
                "(2023). A forgotten effects approach to the analysis of complex economic systems: Identifying "
                "indirect effects on trade networks. _Mathematics_, 11(3), 531.\n"
                "- Li, N., Yu, X. y Liu, W. (2025). Max–min transitive closure of randomly generated fuzzy matrix: "
                "Bernoulli and classical probabilistic models. _Axioms_, 14(9), 690.\n"
                "- Araya-Toro, C. y Rojas-Mora, J. (2024). _forgeffects: A Python package for analyzing forgotten "
                "and direct effects_, versión 0.2.x.\n"
                "- Riquelme Ávila, C. (2026). _feCUDA: Finding extremal CUDA paths_. "
                "https://github.com/criquelme2003/feCUDA"
            ),
        ],
        "figuras": [
            {"ref": "s1", "buscar": _SWEEP_QUERY, "vista": "por_n", "publico": True, "descargar_resultados": True,
             "pie": "Orden efectivo medio según c, para cada N, con su error estándar."},
            {"ref": "s2", "buscar": _SWEEP_QUERY, "vista": "por_c", "publico": True,
             "pie": "Orden efectivo según N, para cada c; la banda va del mínimo al máximo observado."},
            {"ref": "m1", "buscar": _MAXMIN_QUERY, "vista": "efectos", "escala": "log", "publico": True,
             "en_cuerpo": True, "descargar_entradas": True, "descargar_resultados": True,
             "pie": "Efectos encontrados por orden (q_r) en una instancia con n = 2500 y c = 5/2 (escala logarítmica)."},
        ],
    },
    {
        "project": THESIS_PROJECT,
        "titulo": "Cálculo de efectos olvidados con múltiples expertos mediante procesamiento tensorial en Python",
        "slug": "tesis-claudio-araya-valdivia",
        "autores": "Claudio Esteban Araya Toro",
        "palabras_clave": ["teoría de los efectos olvidados", "procesamiento tensorial", "efectos directos",
                           "Python", "forgeffects"],
        "resumen": (
            "Este trabajo aborda la optimización del cálculo de la teoría de los efectos olvidados mediante el "
            "desarrollo de un paquete denominado forgeffects para Python, el cual se basa principalmente en "
            "procesamiento tensorial. El enfoque propuesto busca superar las limitaciones de las herramientas "
            "existentes, mejorando tanto la eficiencia como la velocidad de los cálculos, con especial énfasis en "
            "la integración de múltiples expertos para modelar relaciones de causa y efecto. Además, el paquete "
            "incorpora la capacidad de calcular efectos directos como una extensión del análisis. La solución "
            "utiliza tensores para realizar operaciones con cada experto, lo que permite agilizar "
            "significativamente los procesos computacionales. Para validar la efectividad del enfoque, se "
            "realizaron comparativas de rendimiento con su implementación anterior en el entorno de desarrollo R, "
            "evidenciando mejoras significativas en términos de escalabilidad y reproducibilidad."
        ),
        "bloques": [
            (
                "## Introducción\n\nLa teoría de los efectos olvidados (Kaufmann y Gil Aluja, 1988) identifica "
                "impactos indirectos en sistemas complejos, donde las relaciones de causa y efecto no son "
                "inmediatamente evidentes. Uno de sus principales desafíos es medir y agregar la incidencia, una "
                "noción subjetiva que se complica cuando la evalúan varios expertos, cada uno con su perspectiva, "
                "experiencia e incertidumbre.\n\n"
                "El paquete de R foRgotten (Mardones-Arias y Rojas-Mora, 2022) modela cada relación como un vector "
                "de incidencias de varios expertos, pero resulta insuficiente para grandes volúmenes de datos por "
                "su velocidad y capacidad de cómputo, en especial en los efectos olvidados con remuestreo "
                "empírico.\n\n"
                "El objetivo de este trabajo es implementar una solución para el cálculo de efectos olvidados con "
                "múltiples expertos mediante procesamiento tensorial en Python: diagnosticar las limitaciones de "
                "foRgotten, diseñar e implementar "
                "[forgeffects](https://github.com/claudio-araya/forgeffects) con TensorFlow y validar que reproduce "
                "sus resultados."
            ),
            (
                "## Metodología\n\n**Datos.** Matrices de incidencia de 10 expertos del estudio de Manna, "
                "Rojas-Mora y Colther (2018) sobre la política de contaminación atmosférica de Valdivia, que el "
                "paquete incluye:\n\n"
                "| Matriz | Relaciona | Tamaño |\n|---|---|---|\n| CC | causa con causa | 10 × 16 × 16 |\n"
                "| CE | causa con efecto | 10 × 16 × 4 |\n| EE | efecto con efecto | 10 × 4 × 4 |\n\n"
                "**Efectos olvidados, `FE()`.** Las matrices se apilan como tensores (expertos × filas × columnas) "
                "y forman un grafo bipartito encadenado. El remuestreo empírico genera `rep` réplicas en que cada "
                "celda toma un percentil al azar de las respuestas de los expertos; sobre ellas se aplican "
                "convoluciones max-min iterativas y se arman los caminos, con su frecuencia (`Count`), media "
                "(`Mean`) y desviación estándar (`SD`).\n\n"
                "**Efectos directos, `directEffects()`.** Centra los datos en la media, genera réplicas bootstrap "
                "y calcula un estadístico t unilateral que compara la incidencia media con el umbral, junto con su "
                "intervalo de confianza y valor p.\n\n"
                "**Pruebas.** Con THR = 0,5 y orden máximo 3, se midió el tiempo promedio de tres ejecuciones de "
                "`FE()` en foRgotten y en forgeffects (CPU y GPU) con 2¹⁰, 2¹¹, 2¹², 2¹³ y 2¹⁴ repeticiones, en un "
                "AMD Ryzen 7 5700X con 16 GB de RAM y una NVIDIA GeForce RTX 3060 Ti de 8 GB (Ubuntu 22.04.5). Para "
                "validar, se compararon las frecuencias de los diez caminos más frecuentes de orden 2 y 3 con "
                "2¹⁴ = 16 384 repeticiones, y los valores p de los efectos directos entre efectos (EE, 10 000 "
                "repeticiones, 95 % de confianza), con la prueba de Shapiro-Wilk, una prueba t pareada y el "
                "tamaño del efecto (d de Cohen)."
            ),
            (
                "## Resultados\n\n**Rendimiento de `FE()`** (segundos, promedio de tres ejecuciones):\n\n"
                "| Repeticiones | foRgotten | forgeffects CPU | forgeffects GPU |\n|--:|--:|--:|--:|\n"
                "| 1024 | 53,378 | 0,245 | 0,238 |\n| 2048 | 153,167 | 0,374 | 0,355 |\n"
                "| 4096 | 442,315 | 0,524 | 0,474 |\n| 8192 | 1403,2 | 0,982 | 0,806 |\n"
                "| 16 384 | 4865,967 | 1,994 | 1,506 |\n\n"
                "Una ley de potencia describe mejor los tres casos: foRgotten crece con exponente 1,811, cercano a "
                "cuadrático, mientras forgeffects lo hace con 1,196 en CPU y 1,085 en GPU, cerca de lineal.\n\n"
                "**Validación de efectos olvidados** (frecuencias con 16 384 repeticiones):\n\n"
                "| Camino de orden 2 | foRgotten | forgeffects |\n|---|--:|--:|\n"
                "| a10 → a11 → a14 | 3559 | 3525 |\n| a13 → a16 → a1 | 3276 | 3273 |\n"
                "| a3 → a6 → a9 | 3107 | 3123 |\n| a8 → a13 → a10 | 3096 | 3069 |\n"
                "| a14 → a11 → a4 | 2746 | 2781 |\n| a13 → a6 → a9 | 2734 | 2739 |\n"
                "| a9 → a8 → a13 | 2714 | 2637 |\n| a7 → a6 → a9 | 2616 | 2569 |\n"
                "| a15 → a11 → a3 | 2592 | 2561 |\n| a8 → a6 → a9 | 2591 | 2619 |\n\n"
                "En orden 2 las diferencias normalizadas son normales (Shapiro-Wilk W = 0,960, p = 0,787) y la "
                "prueba t pareada no encuentra diferencias (p = 1; d de Cohen ≈ 3,4 × 10⁻¹⁵). En orden 3, cuyo "
                "camino más frecuente es b3 → b2 → b4 → b1 (21 y 22 apariciones), el resultado es el mismo "
                "(W = 0,989, p = 0,996; t pareada p = 1). En los efectos directos, los valores p de las 12 "
                "relaciones entre efectos tampoco difieren (W = 0,902, p = 0,166; t pareada p = 1; "
                "d ≈ 1,2 × 10⁻¹⁴); por ejemplo, b1 → b2 da 0,925 en foRgotten y 0,934 en forgeffects.\n\n"
                "La {{fig:f1}} muestra los caminos de orden 2 del cálculo agregado de los 10 expertos, para "
                "comparar con la tabla; la {{fig:f2}} reúne los órdenes 2 y 3 en un hive plot, y la {{fig:f3}} "
                "muestra los caminos de un solo experto."
            ),
            (
                "## Discusión\n\nEl crecimiento casi cuadrático de foRgotten hace prohibitivos los análisis con "
                "muchas repeticiones empíricas: 2¹⁴ repeticiones tardan más de 80 minutos. forgeffects, con "
                "crecimiento casi lineal, las resuelve en menos de 2 segundos, aunque la diferencia entre CPU y GPU "
                "es pequeña.\n\n"
                "La validación muestra que ambos paquetes producen resultados coherentes y comparables, pero vale "
                "solo para los casos y parámetros evaluados y no debe generalizarse a todas las configuraciones.\n\n"
                "Se identificaron limitaciones de TensorFlow en GPU: reserva por defecto toda la memoria disponible "
                "y, en el armado de caminos, las iteraciones y comparaciones generan overhead y fragmentación de "
                "memoria, incluso con `memory_growth=True`."
            ),
            (
                "## Conclusiones\n\n- forgeffects, implementado en Python, reproduce de manera consistente los "
                "resultados de foRgotten en R en los casos evaluados, tanto en los efectos olvidados como en los "
                "efectos directos.\n"
                "- Su rendimiento subcuadrático y su implementación en un entorno ampliamente utilizado como Python "
                "lo hacen una herramienta robusta y escalable, y un marco sólido para aplicaciones que requieren "
                "flexibilidad, eficiencia computacional y reproducibilidad.\n"
                "- Al estar bajo licencia MIT, se espera que la comunidad pueda mejorarlo o reimplementarlo.\n"
                "- Trabajo futuro: optimizar el algoritmo con CUDA, para controlar mejor la memoria de la GPU, y "
                "llevarlo a una plataforma web donde los expertos ingresen las matrices de forma colaborativa, el "
                "cálculo se delegue a una API y los resultados se exploren gráficamente."
            ),
            (
                "## Referencias\n\n- Kaufmann, A. y Gil Aluja, J. (1988). _Modelos para la investigación de efectos "
                "olvidados_. Milladoiro.\n- Mardones-Arias, E. y Rojas-Mora, J. (2022). _foRgotten_ (paquete de R, "
                "versión 1.1.0). https://github.com/ElliottMardones/foRgotten\n"
                "- Manna, E. M., Rojas-Mora, J. y Colther, C. (2018). Application of the Forgotten Effects Theory "
                "for Assessing the Public Policy on Air Pollution of the Commune of Valdivia, Chile. "
                "https://doi.org/10.1007/978-3-319-65687-8_6\n"
                "- Chávez-Bustamante, F., Mardones-Arias, E., Rojas-Mora, J. y Tijmes-Ihl, J. (2023). A Forgotten "
                "Effects Approach to the Analysis of Complex Economic Systems: Identifying Indirect Effects on Trade "
                "Networks. _Mathematics_, 11(3).\n"
                "- Abadi, M. et al. (2015). _TensorFlow: Large-Scale Machine Learning on Heterogeneous Distributed "
                "Systems_."
            ),
        ],
        "figuras": [
            {"ref": "f1", "buscar": _AGGREGATE_QUERY, "vista": "sankey", "orden": 2, "top": 50, "publico": True,
             "en_cuerpo": True, "descargar_entradas": True, "descargar_resultados": True,
             "pie": "Efectos olvidados de orden 2 del cálculo agregado de los 10 expertos."},
            {"ref": "f2", "buscar": _AGGREGATE_QUERY, "vista": "hive", "top": 50, "publico": True,
             "pie": "Órdenes 2 y 3; el color indica el orden y el grosor la incidencia."},
            {"ref": "f3", "buscar": _EXPERT_QUERY, "vista": "sankey", "orden": 2, "top": 50, "publico": True,
             "pie": "Caminos de orden 2 de un experto (el cálculo por experto más reciente)."},
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
    archivo falta (por ejemplo, otro volumen) o cambió en el repo, lo vuelve a copiar. Devuelve True si creó el job.
    """
    project = next((p for p in await projects.list_projects() if p.nombre == FORGETHREADS_PROJECT), None)
    if project is None:
        return False
    local = Path(settings.sweep_results_dir) / f"{SWEEP_JOB_ID}.csv"
    if not local.is_file() or local.read_bytes() != SWEEP_DATA.read_bytes():
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
