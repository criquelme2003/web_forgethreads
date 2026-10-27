# web_forgethreads

Backend FastAPI + SPA React que lanza cómputos MaxMin como jobs SLURM en un cluster de 3 nodos CUDA vía SSH.

## Comandos

```bash
uv sync --dev                                   # dependencias
uv run pytest                                   # tests (requiere AUTH_USERNAME, AUTH_PASSWORD, SESSION_SECRET_KEY; ver CI)
TEST_DATABASE_URL=postgresql://... uv run pytest # mismos tests contra PostgreSQL (la base se vacía en cada test)
uv run alembic revision --autogenerate -m "..."  # migración tras cambiar app/db/schema.py; `uv run alembic check` verifica
uv run ruff check app tests                     # lint
npm ci --prefix app/front && npm run build --prefix app/front   # build del SPA → app/front/dist
uv run uvicorn app.main:app --reload            # servidor local
```

`tests/test_login.py::test_front_login_served` falla si no existe `app/front/dist`.

## Arquitectura

- `app/api/` — routers FastAPI. Dependencias compartidas en `api/deps.py` (`require_user`, `require_superadmin`, `get_job_store`, `get_project_store`, `get_user_store`, `get_post_store`, `get_ssh_pool`, `get_node_selector`); usarlas siempre en vez de instanciar servicios en el router.
- `app/db/` — SQLAlchemy Core async: `schema.py` (tablas), `engine.py` (un motor por URL; aplica las migraciones de Alembic en `migrations/` la primera vez que se usa), `copy_sqlite.py` (copia una base SQLite antigua a PostgreSQL).
- `app/services/` — lógica: `job_store` (jobs; kinds `maxmin`, `fe`, `sweep`), `project_store` (proyectos, co-admins, invitaciones e inputs con versiones; misma base), `user_store` (superadmin y admins, contraseñas con argon2), `post_store` (publicaciones del blog: borrador e instantánea publicada), `mailer` (SMTP de invitaciones), `node_selector` (elige GPU libre, cache TTL), `slurm_submit` (arma comandos `sbatch`), `fe_input` / `fe_remote` (matrices de expertos y SFTP), `fe_compare` (comparación de caminos entre expertos), `sweep` (combinaciones, estimación de duración y estadísticas del barrido).
- `app/ssh/` — pool asyncssh singleton, creado y cerrado en `app/lifespan.py`.
- `app/repositories/slurm.py` — consultas de estado SLURM.
- `app/core/config.py` — `Settings` (pydantic-settings, lee `.env`); `get_settings()` está cacheado. La base es `DATABASE_URL` (PostgreSQL en producción) o, si falta, SQLite en `JOBS_DB_PATH`.
- Despliegue: `deployment.yaml` (app) y `postgres.yaml` (PostgreSQL con respaldo diario); pasos en [docs/despliegue.md](docs/despliegue.md).
- `app/front/` — SPA Vite servida por FastAPI en `/front`.

### Usuarios y acceso

- Roles: **superadmin** (sale de `AUTH_USERNAME` / `AUTH_PASSWORD`; se crea o actualiza al iniciar y en cada ingreso, y recibe los proyectos sin dueño) y **admin** (lo crea el superadmin en `/front/users`). La sesión guarda `user_id`; `require_user` devuelve el `User` y corta el acceso si se desactivó.
- Un admin nuevo no tiene contraseña: recibe un enlace de activación de un solo uso (`/front/activate/<token>`, solo se guarda el hash, vence en `ACTIVATION_TTL_HOURS`). Regenerarlo sirve para restablecer la contraseña.
- Cada proyecto tiene dueño (`projects.owner_id`) y co-admins (`project_admins`). Un admin solo ve los proyectos de los que es miembro (404 en los ajenos) y los jobs de esos proyectos o los sueltos que lanzó él (`jobs.user_id`). Borrar el proyecto y gestionar co-admins es del dueño o del superadmin. Al desactivar un admin, sus proyectos pasan al co-admin más antiguo o a quien desactiva.

### Flujo de un job

1. `POST /app/execute_maxmin` valida y llama a `submit_with_failover` (`app/api/launch.py`): consulta los nodos sin caché (`NodeSelector.ranked_candidates`; los flags `+DRAIN`, `+NOT_RESPONDING`, `*`, etc. dejan el nodo no disponible), corre `sbatch new_job.sh`, parsea el JOBID y revisa con `squeue` que no quedó bloqueado (`ReqNodeNotAvail`…). Si falla o quedó bloqueado, lo cancela y prueba el siguiente nodo (RF-46). Con `nodo` explícito no hay failover.
2. `JobStore.create` genera un token por job.
3. Se encadena `sbatch --dependency=afterany:<id> notifier.sh` con ese token (corre aunque el job falle).
4. `notifier.sh` llama a `POST /app/job_callback` con `Authorization: Bearer <token>` → se actualiza el registro.
5. `GET /app/maxmin/jobs/{id}` devuelve el job y los efectos por orden leídos de su log (`app/services/maxmin_log.py`); el SPA los grafica en `/front/maxmin/jobs/<id>`.

### Cálculo de caminos (forgeffects)

1. `POST /app/fe/validate` parsea los CSV de CC, CE y EE (`app/services/fe_input.py`) y devuelve errores por celda para la vista previa.
2. `POST /app/fe/execute` sube `CC.npy`, `CE.npy`, `EE.npy` y `meta.json` por SFTP a `<scripts_wf>/jobs_inputs/<request_id>/` (`app/services/fe_remote.py`) y lanza `sbatch fe_job.sh --input-dir ...` + notifier (`app/api/launch.py`).
3. El callback guarda el resumen (`orders`, `rows_per_order`, `k`) en `jobs.result`.
4. `GET /app/fe/jobs/{id}/paths` descarga por SFTP `jobs_results/<id>/paths_order_<o>.csv` la primera vez y los cachea en `fe_results_dir`.
5. El SPA (`/front/fe/jobs/<id>`) los muestra como Sankey por orden o hive plot (ejes Origen, Intermedios y Destino, color por orden) (`components/Paths*.jsx`, modelo común en `pathsModel.js`); el filtro de origen es compartido y se cambia con un clic en un nodo de origen.

### Proyectos

- Vistas del proyecto en el SPA (RF-61): `/front/projects/<id>` (portada: datos, resumen, pasos, admins e invitaciones), `/individual` (por experto o MaxMin individual), `/lote` (agregados o barridos) y `/blog` (publicaciones).
- Tipos `expertos` (estados `borrador → abierto → cerrado`, reabrible) y `simulacion` (`abierto ↔ cerrado`). Las etiquetas solo se editan en `borrador`. El proyecto no tiene `thr` ni `maxorder`: se indican en cada lanzamiento.
- Los cálculos de un proyecto son filas de `jobs` con `project_id` (e `input_id` si vienen de un experto). `POST /app/execute_maxmin` exige `project_id` de un proyecto de simulación abierto.
- Invitaciones (correo y nombre opcional del experto): solo se guarda el hash del token; el enlace (`/front/invite/<token>`) se muestra al crear, regenerar o reenviar por lotes (`/invitations/resend`, a quienes no han respondido; regenera cada token). Con `INVITATION_DELIVERY=smtp` además se envía por correo (puerto 465 = SSL implícito). Solo se elimina a un experto sin input; al resto se lo excluye.
- `/app/invite/{token}` es público (sin sesión): info, validación contra las etiquetas del proyecto y envío. El primer envío consume la invitación y guarda el input (matrices normalizadas en `expert_inputs`); durante `INPUT_EDIT_DAYS` el mismo enlace sirve para modificarlo (modo `edicion`, con las matrices precargadas) y cada modificación es una versión en `expert_input_versions`. No lanza nada en el cluster.
- Cada cálculo guarda la versión del input usada (`jobs.input_version`); si después hay una más nueva, se marca «input modificado». Los jobs de un input devuelven `experto` (nombre, correo, versiones).
- `POST /app/projects/{id}/compute` (`thr`, `maxorder`, `nodo?`, `input_ids?`; por defecto los no excluidos) lanza un `fe_job.sh` por input. Los errores de lanzamiento se devuelven por input; reintentar es volver a lanzar.
- Análisis conjunto: `POST /app/projects/{id}/aggregate` (`thr`, `maxorder`, `reps`, `nodo?`, `input_ids?`; k ≥ 2) apila las versiones vigentes en tensores `(k, filas, cols)` y lanza un solo `fe_job.sh`; es un job `fe` sin `input_id` con `params.inputs` = `[{input_id, version}]`, y los jobs devuelven `expertos`. `GET /app/projects/{id}/compare?job_ids=…` une por orden los caminos de cálculos por experto (uno por experto, terminados): `Mean` de cada uno y cuántos lo comparten; el SPA lo muestra en `/front/projects/<id>/compare?jobs=…`.
- Barridos (proyectos de simulación): `POST /app/projects/{id}/sweeps/preview` devuelve combinaciones válidas (N par, c ≤ N/2 − 1) y omitidas, ejecuciones y `--time` estimado (`SWEEP_*`); `POST /app/projects/{id}/sweeps` lanza `sbatch --time=… sweep_job.sh` (kind `sweep`) y lo rechaza si supera `SWEEP_MAX_HOURS`. `GET /app/sweeps/{id}` descarga una vez `jobs_results/<id>.csv` a `sweep_results_dir` y calcula media, desviación, error estándar, mín, máx y repeticiones por (n, c), también en estado `partial`; `crudo.csv` y `agregado.csv` lo exportan. El SPA lo grafica en `/front/sweeps/<id>` (`components/SweepCharts.jsx`).
- Al abrir la base, los jobs MaxMin sin proyecto se mueven a un proyecto de simulación «MaxMin (jobs anteriores)».
- Datos de muestra (`app/services/demo_seed.py`): «Demo · Expertos» (abierto, con un input de `docs/ejemplos_fe`) y «Demo · Simulación» (vacío). Se crean si faltan con `SEED_DEMO_DATA=true` al iniciar o con `uv run python -m app.services.demo_seed`.

### Blog (publicaciones)

- `app/api/posts.py`: admins en `/app/posts` (CRUD, `/preview`, `/publish`, `/unpublish`); público sin sesión en `/app/blog`, `/app/blog/{slug}` y `/app/blog/{slug}/figuras/{n}/{entradas|resultados}.csv`. SPA: `/front/blog` y `/front/blog/<slug>` (públicas, `pages/Blog.jsx`), `/front/posts/<id>` (editor) y la pestaña «Publicaciones» del proyecto (`/projects/<id>/blog`).
- Una publicación = secciones Markdown (`bloques`) + `figuras` (job, vista, orden, origen, top, leyenda y permisos `publico`, `descargar_entradas`, `descargar_resultados`). La editan los admins del proyecto (o su autor si no tiene proyecto); varias por proyecto. La plantilla trae las secciones de un artículo y el último cálculo terminado como figura citada, sin permiso.
- Una figura se publica solo si el texto la cita (`{{fig:<ref>}}`) y tiene `publico`; se numera por orden de cita y se muestra en la barra lateral (`components/PostView.jsx`). Citar una figura inexistente o sin permiso impide publicar (422).
- Editor: secciones en la columna principal y lista de figuras en una barra lateral fija; cada figura se edita en un modal con vista previa (`POST /app/posts/{id}/figure-preview`, sin guardar). Con `en_cuerpo`, la figura también va en el texto, bajo la sección de su primera cita. En la vista pública las figuras son una lista desplegable (la cita abre la suya) y «Descargar PDF» imprime la publicación con todas las figuras (diálogo del navegador, estilos `@media print`).
- Publicar guarda en `posts.publicado` una instantánea con los datos de cada figura (solo los `top` caminos que dibuja, sin el ID del job). `_privado` dentro de la instantánea guarda el job de las descargas permitidas y nunca se envía al público. Los expertos van como «Experto N» salvo `anonimizar=false` (nombre); el correo nunca se publica.

El contrato con el cluster está en el README de [scripts_wf](https://github.com/criquelme2003/scripts_wf).

## Convenciones

- Mensajes de error, docstrings y comentarios en español.
- Todo valor interpolado en un comando remoto pasa por `shlex.quote`.
- Nunca loguear tokens: usar `redact_token`.
- Los tests sobrescriben `get_settings` (ver `tests/conftest.py`) y no abren conexiones SSH reales. Usan el fixture `database` (nunca `data/jobs.db`) y `run_sql` en vez de `sqlite3`, para que corran también contra PostgreSQL.
- Cambios de esquema solo con migraciones de Alembic; no crear tablas ni columnas desde los stores.
- Endpoints nuevos bajo `/app/...`, protegidos con `Depends(require_user)` salvo el callback del cluster, los enlaces públicos con token (`/app/invite/...`, `/app/activate/...`) y el blog publicado (`/app/blog/...`). Todo recurso de un proyecto pasa por `get_project_or_404(project_id, store, user)` (`app/api/projects.py`), que aplica el filtro por membresía.

## Commits

- Autor: criquelme2003 <carlitisamuel@gmail.com> (configurar con `git config user.name` / `git config user.email` antes de commitear).
- No incluir Co-Authored-By, Claude-Session ni ninguna atribución a Claude en commits ni PRs.
- No abrir pull requests: los abre el usuario.

## Requerimientos en curso

- [docs/requerimientos.md](docs/requerimientos.md): proyectos, expertos, cálculo de caminos con forgeffects, barridos y blog.
- [docs/scripts_wf_requerimientos.md](docs/scripts_wf_requerimientos.md): cambios del lado del cluster (repo scripts_wf).

## Límites

- No ejecutar `ssh`, `sbatch` ni `kubectl` contra el cluster SLURM ni el cluster k8s reales (bloqueado en `.claude/settings.local.json`).
- No leer ni editar `.env` / `.env_deploy`; usar `.env.example` como referencia.
- El repo no está formateado con `ruff format`: no reformatear archivos completos dentro de un cambio funcional.
