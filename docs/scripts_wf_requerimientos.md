# scripts_wf: requerimientos para la nueva versión

Documento de trabajo para un agente que desarrolla en el repositorio [`scripts_wf`](https://github.com/criquelme2003/scripts_wf).
Es autocontenido: no requiere acceso al backend `web_forgethreads`, salvo para consultar el contrato descrito en la sección 3.

## 1. Contexto

`scripts_wf` contiene los scripts que el backend `web_forgethreads` (FastAPI) lanza por SSH en un cluster SLURM con GPU. El patrón actual es:

```
backend ──ssh──▶ sbatch new_job.sh --nodos N --thr T --conectividad C --seed S
                   └─ devuelve "Submitted batch job <JOBID>"
         ──ssh──▶ sbatch --dependency=afterok:<JOBID> notifier.sh \
                     --job-id <JOBID> --auth-token <TOKEN> --callback-url <URL>
                   └─ notifier.py lee jobs_results/<JOBID>.json y logs/forgethreads-new-job.<JOBID>
                      y hace POST al backend con Authorization: Bearer <TOKEN>
```

El backend agrega dos funcionalidades nuevas que necesitan scripts nuevos en el cluster:

1. **Cálculo de efectos olvidados con `forgeffects`** sobre las matrices CC, CE y EE que ingresan expertos (grafo bipartito encadenado). Se calcula por experto y también de forma agregada (varios expertos apilados).
2. **Barrido de simulación**: un solo job que recorre listas de `N` y `c` con `R` repeticiones y reporta el orden efectivo de cada ejecución. Debe replicar [`feCUDA/comprobacion_empirica/test2.py`](https://github.com/criquelme2003/feCUDA/blob/gpu_paths/comprobacion_empirica/test2.py) (rama `gpu_paths`).

### Estado actual del repo

| Archivo | Rol |
|---|---|
| `new_job.sh` / `new_job.py` | Cómputo MaxMin individual con `forgethreads.maxmin` sobre una matriz de `matrix_construction.sparse_supercritical_block_matrix`. Escribe `jobs_results/<JOBID>.json`. |
| `notifier.sh` / `notifier.py` | Lee el resultado y el log del job padre y hace POST al backend con 3 intentos; si fallan, guarda `jobs_failed_notify/<id>.json`. |
| `matrix_construction.py` | Matriz por bloques `[NN NM; 0 MM]`, reflexiva. Lanza `ValueError` si `c / (N/2 − 1) > 1`. |
| `parser.py` | Parsers argparse de `new_job` y `notifier`. |
| `forgethreads.cpython-310-x86_64-linux-gnu.so` | Extensión nativa (requiere Python 3.10). |
| `environment.yml` | Entorno conda local `./.conda_env` (Python 3.10, `numpy`, `requests`). |
| `tests/` | `test_job_dependency.sh` (integración con SLURM real) y `mock_callback_server.py`. |

## 2. Tareas

### T1. Notifier: encadenamiento con `afterany` y estado `partial`

**Problema:** con `--dependency=afterok`, si el job de cómputo falla o se corta por tiempo, SLURM nunca libera la dependencia: el notifier queda pendiente para siempre y el error nunca llega al backend. El backend pasará a lanzar el notifier con `--dependency=afterany:<JOBID>`. El notifier debe soportar los tres desenlaces.

Requisitos:

- **Log del job padre**: hoy la ruta está fija como `logs/forgethreads-new-job.<id>`. Debe buscarse por id con `logs/*.<id>`, para que funcione con cualquier `--job-name` (`new_job`, `fe_job`, `sweep_job`). Mantener la compatibilidad con los jobs existentes.
- **Límite de tamaño del log**: enviar solo los últimos `NOTIFIER_MAX_LOG_BYTES` bytes (por defecto 200000). Los logs de un barrido largo pueden ser grandes.
- **Determinación del estado** a partir de `jobs_results/<id>.json`:

  | Situación | `status` enviado |
  |---|---|
  | El JSON existe y no tiene `"state": "running"` | `success` |
  | El JSON existe con `"state": "running"` (el job murió antes de terminar) | `partial` |
  | El JSON no existe o no es JSON válido | `error` |

- El payload conserva los campos actuales (`status`, `logs`, `job_id`) más todo el contenido del JSON de resumen, igual que hoy.

### T2. `fe_job.sh` / `fe_job.py`: efectos olvidados con `forgeffects`

**Entrada:**

```
sbatch fe_job.sh --input-dir jobs_inputs/<request_id>
```

El backend sube el directorio por SFTP antes de lanzar el job:

```
jobs_inputs/<request_id>/
  CC.npy      float32, forma (k, m, m)
  CE.npy      float32, forma (k, m, n)
  EE.npy      float32, forma (k, n, n)
  meta.json   {"causes": [m etiquetas], "effects": [n etiquetas],
               "thr": 0.5, "maxorder": 3, "reps": 1}
```

`k` es el número de expertos: 1 para el cálculo por experto, ≥ 2 para el agregado.

**Proceso:**

1. Validar que las formas sean consistentes con `k`, `m` y `n` y con las etiquetas, y que los valores estén en [0,1]. Si algo falla, terminar con código ≠ 0 y un mensaje claro en el log.
2. Ejecutar:
   ```python
   forgeffects.FE(CC, CE, EE, causes=..., effects=..., THR=thr, maxorder=maxorder, reps=reps, device="GPU")
   ```
3. **Verificar empíricamente** a qué orden corresponde cada elemento de la lista devuelta (la documentación indica que `resultado[0]` es el orden 2) y qué devuelve un orden sin caminos (DataFrame vacío, `None`, etc.).

**Salida:**

```
jobs_results/<JOBID>/paths_order_<o>.csv   una por orden; columnas tal como las devuelve FE
                                           (From, Through_1..k, To, Count, Mean, SD)
jobs_results/<JOBID>.json                  resumen para el notifier:
  {"kind": "fe", "k": 1, "orders": [2, 3], "rows_per_order": {"2": 120, "3": 87},
   "computation-time(s)": 12.3}
```

El resumen se escribe al final; si no existe, el notifier reporta `error`.

### T3. `sweep_job.sh` / `sweep_job.py`: barrido de simulación

**Entrada:**

```
sbatch --time=<HH:MM:SS> sweep_job.sh --ns 100,250,500 --cs 0.125,0.25,1,2.5 \
       --reps 20 --thr 0.5 --seed-base 0
```

**Proceso (replica `test2.py`):**

1. **Validar cada combinación con la regla estricta:** `N` par, `c > 0` y `c ≤ N/2 − 1`. Las combinaciones inválidas se omiten y se listan en el resumen.

   `test2.py` usa `c < N/2`, que deja pasar casos que `matrix_construction.py` rechaza: aquí se usa la regla estricta.
2. **Calentamiento de GPU**: `sparse_supercritical_block_matrix(100, 100, 4, seed=0)`, `reshape(1, 200, 200)`, `float16` y una llamada a `ft.maxmin` descartada.
3. **Bucle en secuencia**: `for c in cs: for n in ns: for rep in range(reps):`
   - `seed = seed_base * 10**9 + rep * 1000 + n`. Con `seed_base = 0` se obtiene exactamente la semilla de `test2.py`.
   - `E, _, _ = sparse_supercritical_block_matrix(n // 2, n // 2, c, seed=seed)`
   - `m1 = E.reshape(1, n, n).astype(np.float16)`; `m2 = m1.copy()`
   - `_, _, eff_order = ft.maxmin(m1, m2, thr, 100)`
   - Liberar el pool de memoria de CuPy en cada repetición (`cp.get_default_memory_pool().free_all_blocks()`), como en `test2.py`.
   - Si una repetición lanza una excepción, registrarla en el resumen (`c`, `n`, `rep`, mensaje) y **continuar**.
4. Después de cada combinación (`c`, `n`), escribir las filas en el CSV y hacer `flush`. Si el job se corta, todo lo ya escrito queda disponible.

**Salida:**

```
jobs_results/<JOBID>.csv    encabezado: c,n,repeticion,orden_efectivo   (mismo formato que test2.py)
                            solo repeticiones exitosas; eff_order como int
jobs_results/<JOBID>.json   resumen; se crea al inicio y se actualiza después de cada combinación:
  {"kind": "sweep", "state": "running" | "completed",
   "params": {"ns": [...], "cs": [...], "reps": 20, "thr": 0.5, "seed_base": 0},
   "skipped_combinations": [{"n": 100, "c": 50, "reason": "c > N/2 - 1"}],
   "completed_combinations": 37, "total_combinations": 40,
   "failures": [{"c": 2.5, "n": 100, "rep": 3, "error": "..."}],
   "computation-time(s)": 812.4}
```

`state` pasa a `completed` solo al terminar todo el barrido. Así el notifier (T1) distingue `success` de `partial`.

**Opcional:** capturar `SIGTERM` (SLURM lo envía antes de matar el job por tiempo) para escribir el CSV y el resumen pendientes antes de salir.

### T4. Entorno

- Agregar a `environment.yml`: `forgeffects` (pip), `tensorflow` con soporte GPU, `pandas` y `cupy`. Las versiones deben ser compatibles con Python 3.10 y con la versión de CUDA de los nodos (revisar con `nvidia-smi`).
- No hacen falta `numba` ni `matplotlib`: `test2.py` los importa, pero aquí no se grafica.
- **Confirmar si el cluster exige `#SBATCH --gres=gpu:1`** para usar la GPU. Hoy `new_job.sh` no lo pide. Aplicar lo mismo a `fe_job.sh` y `sweep_job.sh`.

### T5. README

- Documentar `fe_job`, `sweep_job` y el notifier con `afterany` y el estado `partial`.
- Corregir lo desactualizado: `new_job.sh` **no** recibe `--auth-token` (el parser no lo tiene); el token es **por job**, generado por el backend, no por sesión; y el resultado se escribe en `jobs_results/<JOBID>.json`, con extensión.

### T6. Tests

- **Tests unitarios con pytest** que corran sin SLURM ni GPU. Usar `SLURM_JOB_ID` falso en el entorno, y mocks de `forgethreads`, `forgeffects` y `cupy`. Deben cubrir:
  - la fórmula de semillas (con `seed_base = 0` coincide con `rep*1000 + n`);
  - la validación de combinaciones;
  - el formato del CSV y del resumen;
  - que el barrido continúe tras una repetición fallida;
  - la validación de entrada de `fe_job`;
  - la búsqueda de logs y la tabla de estados del notifier (`success`, `partial`, `error`).
- **Test de integración:** extender `tests/test_job_dependency.sh` para cubrir `afterany` con un job que falla, y verificar que el callback llega con `status: error`.

## 3. Contrato con el backend

Esto es lo que el backend va a ejecutar y esperar. Cualquier cambio aquí debe coordinarse con `web_forgethreads`.

| Paso | Comando o archivo |
|---|---|
| Subir input de FE | SFTP a `$HOME/<scripts_wf>/jobs_inputs/<request_id>/` (CC.npy, CE.npy, EE.npy, meta.json) |
| Lanzar FE | `cd <scripts_wf> && sbatch fe_job.sh --input-dir jobs_inputs/<request_id>` |
| Lanzar barrido | `cd <scripts_wf> && sbatch --time=<HH:MM:SS> sweep_job.sh --ns ... --cs ... --reps R --thr T --seed-base B` |
| Lanzar MaxMin individual | sin cambios: `sbatch new_job.sh --nodos N --thr T --conectividad C --seed S` |
| Notifier (todos) | `sbatch --dependency=afterany:<JOBID> notifier.sh --job-id <JOBID> --auth-token <TOKEN> --callback-url <URL>` |
| JOBID | El backend lo parsea con la regex `Submitted batch job (\d+)` sobre el stdout de `sbatch`. No cambiar esa salida. |
| Callback | `POST <URL>`, `Authorization: Bearer <TOKEN>`, JSON con `status` ∈ {`success`, `error`, `partial`}, `job_id`, `logs` y los campos del resumen. |
| Descarga de resultados | SFTP desde `jobs_results/<JOBID>/` (FE) o `jobs_results/<JOBID>.csv` (barrido). |

## 4. Restricciones

- No romper `new_job.sh`, sus argumentos ni su salida: el backend actual depende de ellos.
- No cambiar la semántica de `matrix_construction.py`.
- Todo corre con `./.conda_env/bin/python` (Python 3.10), por la extensión `forgethreads`.
- No versionar `.conda_env/`, `jobs_results/`, `jobs_inputs/`, `logs/` ni `jobs_failed_notify/`.
- Convenciones de trabajo del usuario: commits sin línea `Co-Authored-By` de Claude, y sin abrir pull requests (el usuario los abre).

## 5. Criterios de aceptación

1. Un barrido con `--seed-base 0` y los mismos `ns`, `cs`, `reps` y `thr` que `test2.py` produce **el mismo CSV** (mismas filas y mismos órdenes) que `test2.py` para las combinaciones que ambos consideran válidas.
2. Un barrido cortado por `--time` deja un CSV con las combinaciones terminadas, y el callback llega con `status: partial`.
3. Un `fe_job` con `k = 1` y otro con `k ≥ 2` generan un CSV por orden y un resumen correcto. Un input con formas inconsistentes termina en `status: error` con el motivo en `logs`.
4. Un job que falla con `afterany` genera un callback con `status: error`. Hoy ese callback nunca llega.
5. Los tests unitarios pasan sin SLURM ni GPU.
