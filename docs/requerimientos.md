# Requerimientos: proyectos, expertos y barridos

Estado: requerimientos cerrados (2026-10-05). Los puntos marcados **[supuesto]** están pendientes de confirmación.
Los cambios del lado del cluster están detallados en [scripts_wf_requerimientos.md](scripts_wf_requerimientos.md).

## Contexto

La aplicación hoy permite a un único usuario (definido en `.env`) lanzar cálculos MaxMin sueltos en el cluster SLURM (`new_job.sh` + `notifier.sh` encadenado, callback a `/app/job_callback`). Los nuevos requerimientos introducen la entidad **proyecto**, con dos tipos:

- **Expertos**: varios expertos ingresan las matrices CC, CE y EE del grafo bipartito encadenado; los efectos olvidados se calculan con [`forgeffects`](https://github.com/claudio-araya/forgeffects) y los caminos se muestran en un Sankey.
- **Simulación**: el flujo MaxMin actual más un **barrido** sobre `N` y `c` con repeticiones, para obtener el orden máximo promedio. Replica [`feCUDA/comprobacion_empirica`](https://github.com/criquelme2003/feCUDA/tree/gpu_paths/comprobacion_empirica).

## 1. Roles y acceso

| ID | Requerimiento |
|---|---|
| RF-01 | Hay dos roles. **Superadmin**: ve y gestiona todo, y crea, desactiva y reactiva administradores. **Admin**: solo ve y gestiona sus propios proyectos. |
| RF-02 | El superadmin inicial se crea desde las variables de entorno al iniciar la aplicación. Las contraseñas se guardan con hash (argon2 o bcrypt). |
| RF-03 | Cada proyecto tiene **un único dueño** (`owner_id`) y no se comparte. El backend aplica el filtro por dueño en todos los recursos del proyecto: cálculos, invitaciones, inputs y resultados. |
| RF-04 | **[supuesto]** Al desactivar un admin, el superadmin puede reasignar sus proyectos a otro admin. |
| RF-05 | Los expertos no son usuarios: solo existen como invitación dentro de un proyecto. |

## 2. Proyectos

| ID | Requerimiento |
|---|---|
| RF-06 | Un proyecto tiene nombre, descripción, tipo (`expertos` \| `simulacion`), dueño y estado. |
| RF-07 | Los jobs MaxMin existentes se migran a un proyecto de simulación del superadmin. |

## 3. Proyecto de expertos

### 3.1 Configuración

| ID | Requerimiento |
|---|---|
| RF-08 | Al crear el proyecto, el admin define las etiquetas de `m` causas y `n` efectos, y los parámetros de cálculo: `THR` y `maxorder`. |
| RF-09 | Estados: `borrador → abierto → cerrado`. Las etiquetas y los parámetros solo se editan en `borrador`; desde `abierto` quedan fijos, para que los resultados de todos los expertos sean comparables. |

### 3.2 Invitaciones

| ID | Requerimiento |
|---|---|
| RF-10 | El admin invita a expertos por correo. Se genera un enlace con un token de un solo uso; solo se guarda su hash. **[supuesto]** El enlace expira a los 14 días. |
| RF-11 | El token se consume al confirmar el envío del input, no al abrir el enlace. |
| RF-12 | Envío configurable: `manual` (por ahora; el sistema muestra el enlace y el admin lo copia y lo envía) o `smtp` (futuro). Mientras sea manual, la identidad del experto la garantiza el admin. |
| RF-13 | El admin puede revocar una invitación pendiente o regenerarla, lo que invalida el token anterior. |
| RF-14 | Las invitaciones solo se aceptan mientras el proyecto está `abierto`. |

### 3.3 Ingreso del input (interfaz del experto)

| ID | Requerimiento |
|---|---|
| RF-15 | El experto sube 3 CSV obligatorios: CC (`m×m`), CE (`m×n`) y EE (`n×n`). Cada uno lleva la primera fila y la primera columna con las etiquetas. |
| RF-16 | Validaciones: las etiquetas coinciden con las del proyecto (si vienen en otro orden, se reordenan); dimensiones correctas; sin celdas vacías; valores en [0,1]; separador `,` o `;` y decimal `.` o `,` (Excel en configuración chilena exporta con `;` y coma decimal). |
| RF-17 | Antes de confirmar, se muestra una vista previa con los errores marcados por celda. |
| RF-18 | La diagonal de CC y EE se fuerza a 1 por reflexividad. |
| RF-19 | Después de enviar, el experto solo ve una confirmación. **No ve resultados.** |
| RF-20 | Hay un input por invitación. El admin puede excluir un input de los análisis conjuntos sin borrarlo. |

### 3.4 Cálculo por experto

| ID | Requerimiento |
|---|---|
| RF-21 | Al recibir un input se lanza automáticamente `forgeffects.FE` con los tensores `(1,m,m)`, `(1,m,n)` y `(1,n,n)`, las etiquetas, `THR` y `maxorder` del proyecto, y **[supuesto]** `reps=1`. Corre en el nodo del cluster que elige el `NodeSelector`, con GPU. |
| RF-22 | El resultado es, por cada orden, una tabla `From, Through_1..k, To, Mean`. `Count` y `SD` no aportan información con un solo experto (el bootstrap remuestrea informantes). |
| RF-23 | Si el cálculo falla, el input queda guardado con el cálculo en estado `error` y el log visible. El admin dueño puede reintentarlo; el reintento crea un nuevo cálculo con el mismo input. |

### 3.5 Análisis conjunto

| ID | Requerimiento |
|---|---|
| RF-24 | **Cálculo agregado**: el admin lanza `FE` sobre los inputs no excluidos apilados (`k` expertos, `k ≥ 2`). El admin define `reps` del bootstrap; `THR` y `maxorder` son los del proyecto. El resultado incluye `Count`, `Mean` y `SD`. |
| RF-25 | Se pueden lanzar varios cálculos agregados; cada uno registra qué inputs incluyó. |
| RF-26 | **Comparación**: Sankeys de los expertos elegidos lado a lado, y una tabla de caminos con una columna por experto (si tiene el camino y su `Mean`) más el número de expertos que lo comparten. |

### 3.6 Visualización

| ID | Requerimiento |
|---|---|
| RF-27 | Sankey con D3 (`d3-sankey`). Cada nodo se dibuja por posición (`etiqueta@capa`, capa 0 = From, 1 = Through_1, …) porque `d3-sankey` no admite ciclos y los caminos sí pueden repetir nodos. Filtro por orden y por nodo de origen. |
| RF-28 | El ancho de cada enlace es `Mean` en los resultados por experto; en los agregados se puede elegir entre `Mean` y `Count`. |

## 4. Proyecto de simulación

### 4.1 Cálculo individual

| ID | Requerimiento |
|---|---|
| RF-29 | El flujo MaxMin actual, ahora asociado a un proyecto. |
| RF-30 | Validación corregida según `matrix_construction.py`: `N` par y `c ≤ N/2 − 1` (hoy el backend solo exige `c < N` y acepta casos que fallan en el cluster). `c` pasa a ser decimal. |

### 4.2 Barrido

| ID | Requerimiento |
|---|---|
| RF-31 | **Entrada**: una lista de valores de `N`, una lista de valores de `c` (decimales), `R` repeticiones, `THR` fijo por barrida y una semilla base (entero ≥ 0, por defecto 0). Todo se guarda con el barrido. |
| RF-32 | Una combinación es válida si `N` es par y `c ≤ N/2 − 1` (regla estricta). Antes de lanzar se muestran las combinaciones válidas, las descartadas y el total de ejecuciones, y se pide confirmación. |
| RF-33 | Se lanza **un solo job de SLURM** (`sweep_job.sh`) con `notifier.sh` encadenado (ver RNF-05). |
| RF-34 | El script del nodo replica `test2.py`: calentamiento de GPU, bucle `c` → `n` → repetición en secuencia, `float16`, `ORDER = 100`, liberación de memoria de GPU por repetición y escritura a disco después de cada combinación. La semilla es `seed = base × 10⁹ + rep × 1000 + n` (con base 0 coincide con `test2.py`). El backend valida que `R × 1000 + max(N) < 10⁹`. |
| RF-35 | **Resultado**: CSV crudo `c, n, repeticion, orden_efectivo`. El backend lo descarga y calcula por (`n`, `c`): media, desviación estándar, error estándar de la media, mínimo, máximo y número de repeticiones. Las repeticiones fallidas se informan aparte. |
| RF-36 | Si el job se corta por tiempo, se conservan las combinaciones ya escritas y el barrido queda como `parcial`, no como `error`. |
| RF-37 | **Visualización**: (1) un panel por `n` con la media de `L(n,c)` según `c` en escala log, barras de error estándar, un marcador por régimen de `c` (c < 1; 1 ≤ c ≤ 5/4; 5/4 < c < 2; 2 ≤ c ≤ 3; c > 3) y líneas de referencia en 1, 5/4, 2 y 3; (2) un gráfico por `c` del orden según `n` en escala log, con la media y una banda entre mínimo y máximo. Se pueden exportar el CSV crudo y el agregado. |

## 5. Requerimientos no funcionales

| ID | Requerimiento |
|---|---|
| RNF-01 | La base pasa a SQLAlchemy con migraciones (Alembic), manteniendo SQLite. |
| RNF-02 | El barrido corre en un solo job, así que el límite es la duración: el backend estima el tiempo, rechaza barridos que superen un máximo configurable y pide el `--time` de SLURM según la estimación. |
| RNF-03 | Los resultados grandes (caminos, CSV del barrido) se descargan por SFTP con el pool SSH existente; el callback solo informa estado y resumen. |
| RNF-04 | Tests de autorización por rol y por dueño para cada endpoint nuevo. |
| RNF-05 | El notifier se encadena con `--dependency=afterany` (hoy `afterok`). Con `afterok`, si el job de cómputo falla o se corta por tiempo, el notifier queda pendiente para siempre y nunca reporta el error. Afecta a `build_notifier_command` en `app/services/slurm_submit.py`. |

## 6. Modelo de datos (orientativo)

```
User              id, email, password_hash, rol (superadmin|admin), activo
Project           id, owner_id, nombre, descripcion, tipo, estado,
                  causas[], efectos[], thr, maxorder            (expertos)
ExpertInvitation  id, project_id, email, token_hash, expira_at, usado_at, revocado_at
ExpertInput       id, project_id, invitation_id, cc, ce, ee, excluido, created_at
Computation       id, project_id, tipo (fe_experto|fe_agregado|maxmin|barrido),
                  params (JSON), input_ids[], estado (pending|success|error|parcial),
                  slurm_job_id, notifier_job_id, token_hash, resultado (JSON / ruta), logs
```

`Computation` generaliza al `JobRecord` actual.

## 7. Fuera de alcance por ahora

- Envío real de correos (SMTP).
- Proyectos compartidos entre administradores.
- Acceso del experto a sus resultados.
- Ver el avance de un barrido mientras corre.
