# Requerimientos: proyectos, expertos y barridos

Estado: requerimientos cerrados (2026-10-05; sección 6, robustez del encolado, agregada el 2026-10-13; reunión del 2026-10-19 incorporada, ver abajo). Los puntos marcados **[supuesto]** están pendientes de confirmación y los de la sección 9 siguen abiertos.
Los cambios del lado del cluster están detallados en [scripts_wf_requerimientos.md](scripts_wf_requerimientos.md).

**Cambios de la reunión del 2026-10-19**

- Varios admins por proyecto (dueño + co-admins) y alta de admins por el superadmin desde la app: RF-01, RF-03, RF-04, RF-48, RF-49.
- Los expertos no tienen cuenta ni rol de lector: todo es por enlace (RF-05 confirmado).
- El experto puede modificar su input durante una semana después de enviarlo: RF-11, RF-19, RF-20, RF-52, RF-53.
- Reenvío por lotes a quienes no han respondido y eliminación de expertos sin input: RF-50, RF-51.
- Cada resultado indica a qué experto(s) pertenece: RF-10, RF-54.
- Hive plot formalizado y chord diagram retirado de la UI: RF-55.
- Reporte del proyecto tipo blog y difusión de resultados por correo, también a los expertos si el admin los agrega: RF-56 a RF-60.
- Pendientes: validar el flujo de invitación y los gráficos nuevos, límites de cálculo (consulta al profesor Ignacio) y el mecanismo de acceso al reporte (sección 9).

**Implementado** (2026-10-19): roles, alta y desactivación de admins, co-admins y filtro por membresía (RF-01 a RF-05, RF-48, RF-49); invitaciones con nombre, edición del input con versiones, reenvío por lotes, eliminación y atribución (RF-10, RF-11, RF-19, RF-20, RF-50 a RF-54); RF-55. Queda pendiente el reporte (RF-56 a RF-60), que depende de D-01.

**Implementado** (2026-10-26): análisis conjunto, con cálculo agregado y comparación de expertos (RF-24 a RF-26, RF-28), y barrido de simulación (RF-31 a RF-37, RNF-02). La estimación de duración del barrido usa coeficientes configurables (`SWEEP_*`) que hay que calibrar con barridos reales, y ambos dependen de que scripts_wf implemente `fe_job` con k ≥ 2 (T2), `sweep_job` (T3) y el estado `partial` del notifier (T1).

## Contexto

La aplicación hoy permite a un único usuario (definido en `.env`) lanzar cálculos MaxMin sueltos en el cluster SLURM (`new_job.sh` + `notifier.sh` encadenado, callback a `/app/job_callback`). Los nuevos requerimientos introducen la entidad **proyecto**, con dos tipos:

- **Expertos**: varios expertos ingresan las matrices CC, CE y EE del grafo bipartito encadenado; los efectos olvidados se calculan con [`forgeffects`](https://github.com/claudio-araya/forgeffects) y los caminos se muestran en un Sankey.
- **Simulación**: el flujo MaxMin actual más un **barrido** sobre `N` y `c` con repeticiones, para obtener el orden máximo promedio. Replica [`feCUDA/comprobacion_empirica`](https://github.com/criquelme2003/feCUDA/tree/gpu_paths/comprobacion_empirica).

## 1. Roles y acceso

| ID | Requerimiento |
|---|---|
| RF-01 | Hay dos roles. **Superadmin**: ve y gestiona todo, y es el único que crea, desactiva y reactiva administradores (RF-48). **Admin**: solo ve y gestiona los proyectos de los que es dueño o co-admin (RF-49). |
| RF-02 | El superadmin inicial se crea desde las variables de entorno al iniciar la aplicación. Las contraseñas se guardan con hash (argon2 o bcrypt). |
| RF-03 | Cada proyecto tiene **un dueño** (`owner_id`, quien lo creó) y cero o más **co-admins**. El backend aplica el filtro por membresía (dueño o co-admin) en todos los recursos del proyecto: cálculos, invitaciones, inputs, resultados y reporte. |
| RF-04 | **[supuesto]** Al desactivar un admin, el superadmin reasigna la propiedad de sus proyectos a otro admin (por defecto, a un co-admin del proyecto si lo hay). Un co-admin desactivado pierde el acceso a todos sus proyectos. |
| RF-05 | Los expertos no son usuarios: no tienen cuenta ni rol de lector. Solo existen como invitación dentro de un proyecto y todo su acceso es por enlace: el de la invitación (RF-10) y, si el admin los agrega como destinatarios, el del reporte (RF-59). |
| RF-48 | **Alta de admins.** El superadmin crea admins desde la app con correo y nombre. **[supuesto]** No define la contraseña: el sistema genera un enlace de activación de un solo uso (solo se guarda su hash; se entrega como las invitaciones, según `INVITATION_DELIVERY`, RF-12) con el que el admin elige su contraseña. El enlace vence a las 72 horas y se puede regenerar, lo que también sirve para restablecer una contraseña olvidada. |
| RF-49 | **Co-admins.** El dueño agrega o quita co-admins eligiendo entre los admins activos (no crea usuarios: eso es del superadmin, RF-48). Un co-admin tiene los mismos permisos que el dueño sobre el proyecto, **[supuesto]** salvo eliminar el proyecto y gestionar co-admins. El superadmin puede hacer ambas cosas en cualquier proyecto. |

## 2. Proyectos

| ID | Requerimiento |
|---|---|
| RF-06 | Un proyecto tiene nombre, descripción, tipo (`expertos` \| `simulacion`), dueño, co-admins y estado. |
| RF-07 | Los jobs MaxMin existentes se migran a un proyecto de simulación del superadmin. |

### 2.1 Reporte y difusión de resultados

Los resultados no se muestran a los expertos en la app (RF-19). Se comunican con un reporte que redactan los admins del proyecto y que se envía por correo a una lista de destinatarios.

| ID | Requerimiento |
|---|---|
| RF-56 | **Reporte del proyecto.** Cada proyecto tiene una página de reporte tipo blog que redactan sus admins. **[supuesto]** Secciones: descripción del proyecto (se precarga con la del proyecto), metodología, resultados, observaciones y conclusiones, en Markdown. En la sección de resultados se insertan cálculos del proyecto con su gráfico (Sankey o hive, con el orden y el filtro guardados) y su tabla de caminos, con la atribución del RF-54. Los parámetros (`THR`, `maxorder`, `reps`, número de expertos) se completan solos a partir de los cálculos insertados. |
| RF-57 | **[supuesto]** Estados del reporte: `borrador → publicado`. Un reporte publicado se puede editar (los destinatarios ven la última versión publicada) y despublicar, lo que corta el acceso de todos los enlaces. Publicar no envía nada: el envío es una acción aparte (RF-60). |
| RF-58 | **[supuesto]** En el reporte, los expertos aparecen anonimizados («Experto 1», «Experto 2»…) por defecto; el admin puede elegir mostrar sus nombres. Dentro de la app, el admin siempre ve nombre y correo (RF-54). |
| RF-59 | **Destinatarios.** El admin mantiene la lista de destinatarios del reporte: agrega expertos del proyecto (uno a uno o todos los que respondieron) y correos externos, y puede quitarlos en cualquier momento. Quitar a alguien revoca su acceso. |
| RF-60 | **Acceso de solo lectura por enlace** (propuesta, pendiente de decisión D-01). El admin envía el reporte a los destinatarios; cada uno recibe un **enlace personal** (token propio, solo se guarda su hash) que vence a los **[supuesto]** 30 días. Si el enlace venció o se perdió, el destinatario escribe su correo en la página del reporte y, si está en la lista, el sistema le envía un enlace nuevo; la respuesta es la misma si no está, para no revelar quién está en la lista. La recuperación requiere `INVITATION_DELIVERY=smtp`; en modo manual el admin regenera el enlace y lo envía. |

## 3. Proyecto de expertos

### 3.1 Configuración

| ID | Requerimiento |
|---|---|
| RF-08 | Al crear el proyecto, el admin define las etiquetas de `m` causas y `n` efectos. `THR` y `maxorder` no son del proyecto: se indican al lanzar cada cálculo (RF-21). |
| RF-09 | Estados: `borrador → abierto → cerrado` (un proyecto cerrado se puede reabrir, sin volver a `borrador`). Las etiquetas solo se editan en `borrador`; desde `abierto` quedan fijas, para que los inputs de todos los expertos sean comparables. |

### 3.2 Invitaciones

| ID | Requerimiento |
|---|---|
| RF-10 | El admin invita a expertos por correo y, opcionalmente, con su nombre (para identificarlos en los resultados, RF-54). Se genera un enlace personal con un token; solo se guarda su hash. **[supuesto]** El enlace expira a los 14 días si el experto no responde. |
| RF-11 | Abrir el enlace no lo consume. Al confirmar el primer envío del input, el enlace pasa a servir solo para modificar ese input durante el plazo de edición (RF-52); cuando el plazo vence, el token queda consumido. |
| RF-12 | Envío configurable con `INVITATION_DELIVERY`: `manual` (por defecto; el sistema muestra el enlace y el admin lo copia y lo envía) o `smtp` (además del enlace, se envía por correo con la configuración `SMTP_*`; puerto 465 = SSL implícito). En modo manual, la identidad del experto la garantiza el admin. |
| RF-13 | El admin puede revocar una invitación pendiente o regenerarla, lo que invalida el token anterior. |
| RF-14 | Las invitaciones solo se aceptan mientras el proyecto está `abierto`. |
| RF-50 | **Reenvío por lotes.** El admin reenvía el enlace a todos los expertos que no han respondido (invitaciones sin input y no revocadas, incluidas las expiradas), con la opción de quitar algunos antes de confirmar. Como solo se guarda el hash, reenviar regenera el token (RF-13), lo que invalida el enlace anterior y renueva el vencimiento. Con `smtp` se envían los correos y se informa el resultado por invitación; con `manual` se muestran los enlaces nuevos para copiarlos. |
| RF-51 | **Eliminar expertos.** El admin puede eliminar a un experto que no ha enviado input (invitación pendiente, expirada o revocada): sale de la lista y su enlace deja de funcionar. A un experto con input no se lo elimina: se lo excluye (RF-20). |

### 3.3 Ingreso del input (interfaz del experto)

| ID | Requerimiento |
|---|---|
| RF-15 | El experto sube 3 CSV obligatorios: CC (`m×m`), CE (`m×n`) y EE (`n×n`). Cada uno lleva la primera fila y la primera columna con las etiquetas. |
| RF-16 | Validaciones: las etiquetas coinciden con las del proyecto (si vienen en otro orden, se reordenan); dimensiones correctas; sin celdas vacías; valores en [0,1]; separador `,` o `;` y decimal `.` o `,` (Excel en configuración chilena exporta con `;` y coma decimal). |
| RF-17 | Antes de confirmar, se muestra una vista previa con los errores marcados por celda. |
| RF-18 | La diagonal de CC y EE se fuerza a 1 por reflexividad. |
| RF-19 | Después de enviar, el experto ve una confirmación y, mientras dure el plazo de edición (RF-52), puede volver al mismo enlace para ver y modificar sus matrices. **No ve resultados en la app**: solo recibe el reporte si el admin lo agrega como destinatario (RF-59). |
| RF-20 | Hay un input por invitación (con sus versiones, RF-53). El admin puede excluir un input de los análisis conjuntos sin borrarlo. |
| RF-52 | **Edición después del envío.** El experto puede modificar su input durante **7 días** desde el primer envío (configurable), solo mientras el proyecto está `abierto`. Cada modificación pasa por las mismas validaciones y vista previa (RF-16, RF-17). **[supuesto]** El plazo no se reinicia al modificar. Cerrar el proyecto suspende la edición; si se reabre dentro del plazo, la edición vuelve a estar disponible. |
| RF-53 | **[supuesto]** Cada modificación crea una versión nueva del input y se conservan las anteriores. Los cálculos nuevos usan la versión vigente; los ya lanzados guardan la versión que usaron y, si después hay una más nueva, se marcan «input modificado después del cálculo» para que el admin decida si relanzarlos. La lista de inputs muestra la fecha de la última modificación. |

### 3.4 Cálculo por experto

| ID | Requerimiento |
|---|---|
| RF-21 | Al recibir un input solo se guarda; no se lanza ningún cálculo. El admin lanza `forgeffects.FE` sobre los inputs que elige (por defecto, los no excluidos) indicando `THR` y `maxorder`, con los tensores `(1,m,m)`, `(1,m,n)` y `(1,n,n)`, las etiquetas del proyecto y **[supuesto]** `reps=1`. Cada lanzamiento crea un cálculo por input, que registra sus parámetros; un input puede tener varios cálculos con parámetros distintos. Corre en el nodo que elige el `NodeSelector` (o el que indique el admin), con GPU. |
| RF-22 | El resultado es, por cada orden, una tabla `From, Through_1..k, To, Mean`. `Count` y `SD` no aportan información con un solo experto (el bootstrap remuestrea informantes). |
| RF-23 | Si el cálculo falla, el input queda guardado con el cálculo en estado `error` y el log visible. Si el lanzamiento falla, se informa por input y los demás siguen. Para reintentar, un admin del proyecto vuelve a lanzar ese input: se crea un nuevo cálculo con el mismo input. |
| RF-54 | **Atribución.** Todo cálculo y todo resultado indica a qué experto(s) pertenece, en la lista de cálculos, en la vista de caminos y en las exportaciones: nombre y correo de la invitación en los cálculos por experto, y la lista de expertos incluidos en los agregados (RF-25). Si el input se modificó después del cálculo, se indica la versión usada (RF-53). |

### 3.5 Análisis conjunto

| ID | Requerimiento |
|---|---|
| RF-24 | **Cálculo agregado**: el admin lanza `FE` sobre los inputs no excluidos apilados (`k` expertos, `k ≥ 2`). El admin define `reps` del bootstrap, `THR` y `maxorder`. El resultado incluye `Count`, `Mean` y `SD`. |
| RF-25 | Se pueden lanzar varios cálculos agregados; cada uno registra qué inputs incluyó. |
| RF-26 | **Comparación**: Sankeys de los expertos elegidos lado a lado, y una tabla de caminos con una columna por experto (si tiene el camino y su `Mean`) más el número de expertos que lo comparten. |

### 3.6 Visualización

| ID | Requerimiento |
|---|---|
| RF-27 | Sankey con D3 (`d3-sankey`). Cada nodo se dibuja por posición (`etiqueta@capa`, capa 0 = From, 1 = Through_1, …) porque `d3-sankey` no admite ciclos y los caminos sí pueden repetir nodos. Filtro por orden y por nodo de origen. |
| RF-28 | El ancho de cada enlace es `Mean` en los resultados por experto; en los agregados se puede elegir entre `Mean` y `Count`. |
| RF-55 | Además del Sankey, hay una vista de los mismos caminos como **hive plot** (ejes Origen, Intermedios y Destino, color por orden). El filtro de origen es común a ambas vistas y se cambia con un clic en un nodo de origen. El chord diagram se retiró de la UI (reunión del 2026-10-19). Pendiente de validación (P-02). |

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
| RNF-01 | La base pasa a SQLAlchemy con migraciones (Alembic). En producción se usa PostgreSQL (`postgres.yaml`, con respaldo diario); SQLite queda para desarrollo local y tests. |
| RNF-02 | El barrido corre en un solo job, así que el límite es la duración: el backend estima el tiempo, rechaza barridos que superen un máximo configurable y pide el `--time` de SLURM según la estimación. |
| RNF-03 | Los resultados grandes (caminos, CSV del barrido) se descargan por SFTP con el pool SSH existente; el callback solo informa estado y resumen. |
| RNF-04 | Tests de autorización por rol y por membresía del proyecto (dueño, co-admin, ajeno) para cada endpoint nuevo, y de acceso por enlace (invitación y reporte). |
| RNF-05 | El notifier se encadena con `--dependency=afterany` (hoy `afterok`). Con `afterok`, si el job de cómputo falla o se corta por tiempo, el notifier queda pendiente para siempre y nunca reporta el error. Afecta a `build_notifier_command` en `app/services/slurm_submit.py`. |

## 6. Robustez del encolado

Problema observado en producción: jobs que quedan `pending` para siempre en la app porque su nodo no está disponible.

Hechos del entorno que condicionan la solución:

- **Cada nodo es un cluster SLURM independiente** con disco local: un job enviado a un nodo solo puede correr en ese nodo, y su JOBID solo vale dentro de él.
- **No hay `sacct`** en ningún nodo (`Slurm accounting storage is disabled`). Un job terminado solo aparece en `scontrol show job` durante `MinJobAge` (300 s por defecto); después, la única evidencia son los archivos en el disco del nodo.
- Sin historial, si un nodo pierde su estado de SLURM los **JOBID pueden reutilizarse**.

### 6.1 Por qué un cálculo puede quedar sin resultado

| Caso | Cómo se detecta | ¿Se resuelve solo? |
|---|---|---|
| Esperando en la cola por recursos o prioridad | `PENDING`, motivo `Resources`, `Priority`, `BeginTime` | Sí |
| Ejecutándose | `RUNNING` o `COMPLETING` | Sí |
| Nodo no disponible | `PENDING` con motivo `ReqNodeNotAvail`, `NodeDown`, `PartitionDown` o `PartitionInactive`, o nodo `DRAIN`/`DOWN` en `scontrol show node` | No |
| Retenido o mal configurado | `PENDING` con motivo `JobHeldUser`, `JobHeldAdmin`, `BadConstraints`, `PartitionConfig` o `launch failed requeued held` | No |
| Notifier sin poder correr | notifier `PENDING` con motivo `DependencyNeverSatisfied` | No |
| Terminado, pero el callback se perdió | fuera de la cola; existe `jobs_failed_notify/<id>.json` o `jobs_results/<id>.*` | Recuperable |
| Desaparecido | ni en `squeue`, ni en `scontrol show job`, ni archivos de resultado | No |
| Nodo inalcanzable por SSH | falla la conexión del pool | Desconocido hasta que vuelva |

### 6.2 Requerimientos

| ID | Requerimiento |
|---|---|
| RF-38 | Por cada cálculo se guardan: nodo, JOBID del cómputo, JOBID del notifier, nombre del job y hora de envío. |
| RF-39 | **Revisión periódica cada 1 minuto** (configurable) de los cálculos sin resultado. Una consulta por nodo, no por job: `squeue -h -j <id1>,<id2>,… -o "%i\|%j\|%T\|%r\|%V"` y `scontrol show node`. Si no hay cálculos pendientes, no se consulta nada. El intervalo debe ser bastante menor que `MinJobAge`, para alcanzar a ver el estado final de cada job. |
| RF-40 | **Verificación de identidad**: un JOBID solo se da por bueno si el nombre del job y la hora de envío coinciden con los guardados. Si no coinciden, el JOBID fue reutilizado y el cálculo se trata como `desaparecido`. |
| RF-41 | Estados del cálculo, visibles en la lista con su motivo: `en_cola`, `ejecutando`, `bloqueado` (nodo no disponible, retenido, dependencia imposible, tiempo de espera excedido), `nodo_inalcanzable`, `desaparecido` y `reemplazado`, además de los finales `success`, `error` y `parcial`. |
| RF-42 | **Si el job ya no está en la cola**, se consulta `scontrol show job` para leer el estado final y el código de salida. Si tampoco aparece, se buscan por SFTP, en este orden: (1) `jobs_failed_notify/<id>.json`: el callback se perdió y se procesa como si hubiera llegado; (2) `jobs_results/<id>.*`: se procesa el resultado; (3) solo `logs/*.<id>`: `error` con el log adjunto; (4) nada: `desaparecido`. |
| RF-43 | **Relanzamiento manual.** Sobre un cálculo `bloqueado`, `nodo_inalcanzable` o `desaparecido`, un admin del proyecto puede cancelarlo (`scancel` del job y del notifier, si el nodo responde) y relanzarlo en otro nodo con los mismos parámetros. El relanzamiento crea un cálculo nuevo vinculado al original. No hay relanzamiento automático. |
| RF-44 | Al relanzar, el original pasa a `reemplazado` y su token se revoca: si su nodo revive y el job termina, el callback tardío se rechaza y no pisa el resultado del relanzamiento. |
| RF-45 | El backend guarda los inputs de cada cálculo (parámetros y matrices de FE) para poder relanzarlo en otro nodo. Hoy las matrices solo quedan en el disco del nodo original. |
| RF-46 | **Prevención al enviar**: antes del `sbatch` se verifica el estado del nodo sin la caché del selector. Si el `sbatch` falla, se prueba con el siguiente nodo disponible. Ocurre antes de que exista el job, así que no contradice el RF-43. |
| RF-47 | Un cálculo que pasa más de un **tiempo máximo en cola** (configurable, 2 horas por defecto) sin empezar se marca `bloqueado (tiempo de espera excedido)`, aunque SLURM no dé un motivo claro. |

## 7. Modelo de datos (orientativo)

```
User              id, email, nombre, password_hash, rol (superadmin|admin), activo,
                  activacion_token_hash, activacion_expira_at
Project           id, owner_id, nombre, descripcion, tipo, estado,
                  causas[], efectos[]                          (expertos)
ProjectAdmin      project_id, user_id                          (co-admins)
ExpertInvitation  id, project_id, email, nombre, token_hash, expira_at, usado_at, revocado_at
ExpertInput       id, project_id, invitation_id, excluido, created_at, updated_at,
                  editable_hasta, version_vigente
ExpertInputVersion id, input_id, version, cc, ce, ee, created_at
Report            id, project_id, estado (borrador|publicado), secciones (JSON),
                  calculos_insertados (JSON), anonimizar, publicado_at, updated_at
ReportRecipient   id, report_id, email, invitation_id?, token_hash, expira_at,
                  revocado_at, enviado_at
Computation       id, project_id, tipo (fe_experto|fe_agregado|maxmin|barrido),
                  params (JSON), inputs (JSON / ruta), input_ids[], input_versions[],
                  estado (ver RF-41), motivo_estado,
                  nodo, slurm_job_id, notifier_job_id, job_name, submitted_at,
                  ultima_revision_at, reemplaza_a (id), token_hash, token_revocado,
                  resultado (JSON / ruta), logs
```

`Computation` generaliza al `JobRecord` actual.

## 8. Fuera de alcance por ahora

- Cuentas o rol de lector para expertos y destinatarios del reporte (todo es por enlace, RF-05).
- Que el experto vea los resultados de sus cálculos en la app (solo recibe el reporte, RF-59).
- Ver el avance de un barrido mientras corre.
- Relanzamiento automático de cálculos bloqueados.

## 9. Pendientes

| ID | Pendiente | Responsable |
|---|---|---|
| P-01 | **Validar el flujo de invitación** de punta a punta, en modo `manual` y `smtp`: crear la invitación, entregar el enlace, abrirlo, validar y enviar los CSV, y ver el input en el proyecto. Cuando estén implementados, también la edición posterior (RF-52) y el reenvío por lotes (RF-50). | Equipo, en la próxima reunión |
| P-02 | **Validar los gráficos nuevos** (hive plot, RF-55) con datos reales: legibilidad con muchos nodos, colores por orden y filtro de origen. | Equipo |
| P-03 | **Límites de cálculo**: consultar con el profesor Ignacio. Por definir: `m` y `n` máximos, `maxorder` máximo, rango de `THR`, `reps` máximo del bootstrap, número máximo de expertos en un agregado, duración máxima de un job o barrido (RNF-02) y cálculos simultáneos por proyecto. Mientras no se definan, no se agregan límites nuevos. | Consulta al profesor Ignacio |
| D-01 | **Acceso al reporte** (RF-60). Propuesta: enlace personal por destinatario, con vencimiento, y un segundo paso solo para recuperarlo (el destinatario pide un enlace nuevo con su correo y se envía si está en la lista). Alternativa más estricta: un código de un solo uso por correo en cada ingreso; protege mejor contra enlaces reenviados, a costa de un paso más para el destinatario. En ambos casos el admin solo mantiene la lista. | Por decidir |
