# web_forgethreads

Backend FastAPI + SPA React que lanza cómputos MaxMin como jobs SLURM en un cluster de 3 nodos CUDA vía SSH.

## Comandos

```bash
uv sync --dev                                   # dependencias
uv run pytest                                   # tests (requiere AUTH_USERNAME, AUTH_PASSWORD, SESSION_SECRET_KEY; ver CI)
uv run ruff check app tests                     # lint
npm ci --prefix app/front && npm run build --prefix app/front   # build del SPA → app/front/dist
uv run uvicorn app.main:app --reload            # servidor local
```

`tests/test_login.py::test_front_login_served` falla si no existe `app/front/dist`.

## Arquitectura

- `app/api/` — routers FastAPI. Dependencias compartidas en `api/deps.py` (`require_user`, `get_job_store`, `get_ssh_pool`, `get_node_selector`); usarlas siempre en vez de instanciar servicios en el router.
- `app/services/` — lógica: `job_store` (SQLite), `node_selector` (elige GPU libre, cache TTL), `slurm_submit` (arma comandos `sbatch`), `fe_input` / `fe_remote` (matrices de expertos y SFTP).
- `app/ssh/` — pool asyncssh singleton, creado y cerrado en `app/lifespan.py`.
- `app/repositories/slurm.py` — consultas de estado SLURM.
- `app/core/config.py` — `Settings` (pydantic-settings, lee `.env`); `get_settings()` está cacheado.
- `app/front/` — SPA Vite servida por FastAPI en `/front`.

### Flujo de un job

1. `POST /app/execute_maxmin` valida, resuelve nodo y corre `sbatch new_job.sh` por SSH → parsea JOBID.
2. `JobStore.create` genera un token por job.
3. Se encadena `sbatch --dependency=afterany:<id> notifier.sh` con ese token (corre aunque el job falle).
4. `notifier.sh` llama a `POST /app/job_callback` con `Authorization: Bearer <token>` → se actualiza el registro.

### Cálculo de caminos (forgeffects)

1. `POST /app/fe/validate` parsea los CSV de CC, CE y EE (`app/services/fe_input.py`) y devuelve errores por celda para la vista previa.
2. `POST /app/fe/execute` sube `CC.npy`, `CE.npy`, `EE.npy` y `meta.json` por SFTP a `<scripts_wf>/jobs_inputs/<request_id>/` (`app/services/fe_remote.py`) y lanza `sbatch fe_job.sh --input-dir ...` + notifier (`app/api/launch.py`).
3. El callback guarda el resumen (`orders`, `rows_per_order`, `k`) en `jobs.result`.
4. `GET /app/fe/jobs/{id}/paths` descarga por SFTP `jobs_results/<id>/paths_order_<o>.csv` la primera vez y los cachea en `fe_results_dir`.

El contrato con el cluster está en el README de [scripts_wf](https://github.com/criquelme2003/scripts_wf).

## Convenciones

- Mensajes de error, docstrings y comentarios en español.
- Todo valor interpolado en un comando remoto pasa por `shlex.quote`.
- Nunca loguear tokens: usar `redact_token`.
- Los tests sobrescriben `get_settings` (ver `tests/conftest.py`) y no abren conexiones SSH reales.
- Endpoints nuevos bajo `/app/...`, protegidos con `Depends(require_user)` salvo el callback del cluster.

## Commits

- Autor: criquelme2003 <carlitisamuel@gmail.com> (configurar con `git config user.name` / `git config user.email` antes de commitear).
- No incluir Co-Authored-By, Claude-Session ni ninguna atribución a Claude en commits ni PRs.
- No abrir pull requests: los abre el usuario.

## Requerimientos en curso

- [docs/requerimientos.md](docs/requerimientos.md): proyectos, expertos, cálculo de caminos con forgeffects y barridos.
- [docs/scripts_wf_requerimientos.md](docs/scripts_wf_requerimientos.md): cambios del lado del cluster (repo scripts_wf).

## Límites

- No ejecutar `ssh`, `sbatch` ni `kubectl` contra el cluster SLURM ni el cluster k8s reales (bloqueado en `.claude/settings.local.json`).
- No leer ni editar `.env` / `.env_deploy`; usar `.env.example` como referencia.
- El repo no está formateado con `ruff format`: no reformatear archivos completos dentro de un cambio funcional.
