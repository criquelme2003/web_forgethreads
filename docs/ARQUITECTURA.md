# Arquitectura Web Forgethreads — SSH / SLURM / Selección de Nodo

> Documento de referencia técnica. Describe el proyecto **antes** de la refactorización multi-nodo y detalla **todos los cambios** con foco en el nuevo flujo de conexión, la gestión de sesión SSH y las consultas SLURM.

Última actualización: 2026-09-11 (ver [Changelog 2026-09-11](#changelog-2026-09-11))  
Stack: FastAPI + asyncssh + SLURM + Starlette sessions  
Nodos: 3 homogéneos, conexión **directa** SSH, GPU priorizada sobre CPU.

---

## Índice

1. [Proyecto ANTES de los cambios](#1-proyecto-antes-de-los-cambios)
2. [Proyecto DESPUÉS — Resumen de cambios](#2-proyecto-después--resumen-de-cambios)
3. [Detalle archivo por archivo](#3-detalle-archivo-por-archivo)
4. [Nuevo flujo de conexión y selección](#4-nuevo-flujo-de-conexión-y-selección)
5. [Configuración y operación](#5-configuración-y-operación)
6. [Verificación y testing](#6-verificación-y-testing)
7. [Diccionario — Conceptos clave](#7-diccionario--conceptos-clave)
8. [Changelog 2026-09-11 — Limpieza legacy y revisión GPU 0/0](#changelog-2026-09-11)

---

## 1. Proyecto ANTES de los cambios

### 1.1 Visión general

Aplicación FastAPI mínima con login por cookie y dos páginas protegidas:

* `GET /front/login` → formulario estático `app/static/login/index.html`
* `POST /auth/login` (`app/api/login.py:21-35`) → valida contra `auth_username/password` y fija `request.session["user"]`
* `GET /front/parameters` (`app/api/parameters.py:36-39`) → form con `nodos_totales`, `c`, `threshold` (protegido por `app/api/deps.py:4 require_user`)
* `GET /front/gpu_status` (`app/api/gpu_status.py:26-32`) → ejecuta `nvidia-smi` remoto y devuelve HTML

Dependencias en `pyproject.toml:6-14`: `fastapi`, `uvicorn`, `pydantic-settings`, `itsdangerous`, `asyncssh`, `paramiko`.

### 1.2 Configuración `app/core/config.py:6-18`

```python
class Settings(BaseSettings):
    auth_username: str
    auth_password: str
    session_secret_key: str
    cuda3_ip: str           # requerido
    cuda3_username: str     # requerido
    cuda3_password: str     # requerido
```

* Solo **un nodo** (`cuda3`) hardcodeado.
* `ValidationError` si `.env` no tiene las 3 vars — rompía `app/main.py:13` (middleware) y los workflows de CI que solo seteaban `AUTH_*`.
* Sin `get_cluster_nodes()`, sin tuning SLURM/SSH.

### 1.3 Conexión SSH `app/ssh/connection.py:1-7`

```python
async def get_new_ssh_connection():
    return await asyncssh.connect(get_settings().cuda3_ip, username=..., password=...)
```

* Una función, sin pool, sin keepalive, sin timeout explícito, sin soporte multi-nodo, sin manejo de reconexión.
* `known_hosts=None` implícito pero no configurado.

### 1.4 Ciclo de vida `app/lifespan.py:1-13`

```python
@asynccontextmanager
async def global_lifespan(app):
    ssh_conn = await get_new_ssh_connection()  # una sola conexión
    yield {"ssh_conn": ssh_conn}
    ssh_conn.close()  # sin await, sin wait_closed()
```

* Abre **una conexión global** al iniciar y la comparte vía `request.state.ssh_conn`.
* Si `cuda3` cae, la app no arranca. No hay `warmup` ni `best-effort`.
* Mensaje duplicado `"Opening shh connection"`.
* No gestiona `app.state.ssh_pool` ni `node_selector`.

### 1.5 Endpoints y dependencias

* `app/api/deps.py:4-12` solo `require_user`.
* `app/api/gpu_status.py:27-31` inyecta `SSHClientConnection` de `req.state.ssh_conn` y hace `await ssh_conn.run("nvidia-smi")`. Sin selector, sin fallback, sin info de nodo.
* `app/services/ssh_service.py` y `app/repositories/ssh.py` vacíos.

### 1.6 Limitaciones para el requisito “3 nodos + SLURM”

| Aspecto | Antes |
|---|---|
| Escalabilidad | Solo `cuda3`, no lista de nodos |
| SLURM | No consultado; solo `nvidia-smi` |
| Scoring | No existe |
| Sesión SSH | Una conexión, sin lock, sin keepalive, sin retry |
| Cache | No, cada request golpea SSH |
| Failover | No, si falla la conexión la request falla |
| Tests | Lifespan intentaba conectar a IP real incluso en tests |

Diagrama simplificado **ANTES**:

```
Browser -> FastAPI (lifespan: 1x asyncssh.connect cuda3)
              |
              -> require_user -> gpu_status -> ssh_conn.run("nvidia-smi")
```

---

## 2. Proyecto DESPUÉS — Resumen de cambios

### 2.1 Archivos nuevos

| Archivo | Rol |
|---|---|
| `app/ssh/models.py` | `NodeState`, `NodeConfig`, `NodeStatus` — dominio del cluster |
| `app/ssh/pool.py` | `SSHConnectionPool` — pool multi-nodo con lock, keepalive y reconexión |
| `app/repositories/slurm.py` | `SlurmRepository` + parsers `scontrol`/`sinfo` + fallback `nvidia-smi` |
| `app/services/node_selector.py` | `NodeSelector` — scoring GPU-priorizado, TTL cache, failover |

### 2.2 Archivos modificados

| Archivo | Cambio |
|---|---|
| `app/core/config.py` | `cuda1/2/3_*` opcionales + `get_cluster_nodes()` + tuning `slurm_poll_interval/ssh_*` (actualizado 2026-09-11: sin fallback legacy, credenciales explícitas) |
| `app/ssh/connection.py` | **2026-09-11:** Eliminado `get_new_ssh_connection` legacy. Ahora solo factory `get_pool_from_settings()` |
| `app/lifespan.py` | Crea `SSHConnectionPool` + `NodeSelector`, `warmup()` best-effort, `close_all()` (**2026-09-11:** eliminado `legacy_conn`/`ssh_conn`) |
| `app/api/deps.py` | Añade `get_ssh_pool()`, `get_node_selector()`, `_get_effective_settings()` |
| `app/api/gpu_status.py` | Usa `NodeSelector`, añade `/cluster_status` y `/gpu_status/{node_name}` |
| `app/repositories/ssh.py` | **Eliminado 2026-09-11** (era re-export legacy de `SlurmRepository`) |
| `app/services/ssh_service.py` | **Eliminado 2026-09-11** (era re-export legacy de `NodeSelector`) |
| `.env.example` | Documenta 3 nodos + tuning |

---

## 3. Detalle archivo por archivo

### 3.1 `app/core/config.py:6-56`

**Antes:** 3 campos requeridos `cuda3_*`.

**Después (2026-09-11 limpio):**

```python
cuda1_ip: str | None = None   # :13-18
cuda2_ip: str | None = None
cuda3_ip: str | None = None   # ahora opcional -> no rompe CI
slurm_poll_interval: int = 15
ssh_connect_timeout: int = 10
ssh_keepalive_interval: int = 30  # :23-26

def get_cluster_nodes(self) -> list[NodeConfig]:  # :28-51
```

* Itera `cuda1..3`, solo añade nodos con `ip` seteada **y** `username`/`password` explícitos. **Sin fallback** a `cuda3_*` (eliminado 2026-09-11): cada nodo debe tener credenciales propias o se omite.
* Homogéneo: todos `NodeConfig(weight=1)`. Si en el futuro hay heterogeneidad, se cambia `weight` o se añade `gpus_per_node` en `Settings`.
* `lru_cache` mantiene singleton.

> **Legacy removido 2026-09-11:** antes `username=user or self.cuda3_username or ""` permitía heredar credenciales de `cuda3`. Ahora se exige `user` y `pwd` por nodo; si faltan, el nodo se ignora.

### 3.2 `app/ssh/models.py:1-74` (nuevo, trascendental)

Define el vocabulario del dominio.

* **`NodeState` `models.py:5-30`**: `Enum` `IDLE/MIX/ALLOC/DRAIN/DOWN/UNKNOWN`. `from_slurm(raw)` normaliza: `lower()`, quita sufijos `*#~$!`, mapea `mixed->MIX`, `allocated->ALLOC`, etc. Vital para `scontrol` (`State=IDLE*`) y `sinfo` (`mix*`).

* **`NodeConfig` `models.py:33-39`**: `frozen, slots` inmutable por nodo. `name` (`cuda1`), `host`, `username`, `password`, `weight`.

* **`NodeStatus` `models.py:42-74`**: snapshot SLURM por nodo.

  ```python
  name, host, state, cpus_total/alloc/idle, gpus_total/alloc,
  mem_total/alloc, pending_jobs, reachable, raw_scontrol
  ```

  Props:

  * `gpus_free` `models.py:59` → `max(0, total-alloc)`
  * `cpu_free_ratio` `models.py:67` → `cpus_free/cpus_total`
  * `is_available` `models.py:73` → `reachable and state not in (DRAIN,DOWN,UNKNOWN)` — usado por `NodeSelector` para filtrar.

### 3.3 `app/ssh/pool.py:1-124` — Clase `SSHConnectionPool` (cambio más grande)

> **Propósito:** gestionar **N conexiones directas** (una por `NodeConfig`) con lazy-connect, reconexión, thread-safety y keepalive. Es la capa que sustituye la única `get_new_ssh_connection()`.

**Atributos `pool.py:21-26`**

```python
self._nodes: dict[str, NodeConfig]               # nombre -> config
self._conns: dict[str, SSHClientConnection|None] # nombre -> conexión viva
self._locks: dict[str, asyncio.Lock]             # un lock por nodo
self._connect_timeout, self._keepalive_interval
```

**Métodos detallados:**

* **`__init__(nodes, connect_timeout, keepalive_interval)` `pool.py:21`**  
  Construye los 3 dicts. No conecta aún (lazy). Recibe lista de `NodeConfig` de `Settings.get_cluster_nodes()`.

* **`node_names` `@property` `pool.py:28`**  
  Lista de nombres (`["cuda1","cuda2","cuda3"]`). Usado por `lifespan` para `warmup` y por `selector` para iterar.

* **`get_node_config(name)` `pool.py:32`**  
  Lookup de config; lanza `ValueError` si nodo desconocido.

* **`get_connection(node_name)` `pool.py:35-60`** — *core*  
  1. `async with self._locks[node_name]` → serializa reconexiones del mismo nodo.  
  2. Si `conn is not None and not conn.is_closed()` → retorna conexión viva (evita reconectar).  
  3. Si no, hace `await asyncssh.connect(host, username, password, known_hosts=None, connect_timeout, keepalive_interval)` `pool.py:48-54`.  
  4. Guarda en `self._conns` y retorna.  
  Lanza excepción si `asyncssh` falla; el caller decide si reintenta.

* **`try_get_connection(node_name, timeout)` `pool.py:62-68`**  
  Wrapper `asyncio.wait_for(get_connection, timeout)` que captura cualquier excepción y retorna `None`. Usado por `NodeSelector._fetch_status` para marcar nodo como `DOWN` sin propagar excepción. Log `debug` si falla.

* **`run_on_node(node_name, command, timeout)` `pool.py:70-93`** — *helper con retry*  
  1. `conn = await try_get_connection(...)` → `None` si SSH caído.  
  2. `await asyncio.wait_for(conn.run(command), timeout)` → ejecuta.  
  3. Si falla con `asyncssh.Error/OSError/TimeoutError`, invalida la conexión (`old.close(); self._conns[name]=None` bajo lock) `pool.py:81-85`, reconecta una vez `try_get_connection` y reintenta `conn2.run(command)`.  
  Retorna `SSHCompletedProcess|None`. Usado por `NodeSelector.run_on_best_node` y por `gpu_status/{node_name}`.

* **`warmup(concurrency=3)` `pool.py:95-103`**  
  Pre-conecta todos los nodos en paralelo con `asyncio.Semaphore(concurrency)` y `gather(return_exceptions=True)` → best-effort. Llamado en `app/lifespan.py:25`. No bloquea startup si un nodo está `down`.

* **`close_all()` `pool.py:105-114`**  
  Itera `self._conns`, hace `conn.close(); await conn.wait_closed()` con `try/except`. Log `SSH closed {name}`. Resetea dict a `None`. Llamado en shutdown `app/lifespan.py:46`.

* **`is_reachable(node_name)` `pool.py:116-124`**  
  `try_get_connection` + `conn.run("echo ok")` con timeout 3s, retorna `bool`. Útil para healthcheck futuro (no usado en hot-path actual).

**Relación con resto:** `pool.py` es inyectado en `SlurmRepository.get_node_status(conn, node)` y en `NodeSelector(pool, ...)`.

### 3.4 `app/ssh/connection.py:1-15` (legacy eliminado 2026-09-11)

**Antes:** contenía `get_new_ssh_connection(node_name?)` que priorizaba `cuda3` y hacía `asyncssh.connect` directo.

**Después:**

```python
from app.core.config import get_settings
from app.ssh.pool import SSHConnectionPool

def get_pool_from_settings() -> SSHConnectionPool:  # :6-13
    s = get_settings()
    return SSHConnectionPool(nodes=s.get_cluster_nodes(), connect_timeout=..., keepalive_interval=...)
```

* Única responsabilidad: factory del pool desde `Settings`. Sin compat legacy.

### 3.5 `app/repositories/slurm.py:1-290` (nuevo, trascendental)

**Parsers:**

* `parse_gres(gres_str)` `slurm.py:17-38` → extrae GPUs de `Gres=gpu:tesla:4` o `gpu:rtx:2,gpu:tesla:2` vía regex `gpu(?::[^:,]+)?:(\d+)`. Fallback si solo `"gpu"` → 1.
* `parse_scontrol_output(raw, node_name, host)` `slurm.py:41-118` → flat `" ".join(raw.split())`, regex `(\w+)=([^\s]+)` a `kv` dict. Extrae `State` (limpia `+ *`), `CPUAlloc/CPUTot` (fallback `CoresPerSocket*Sockets*Boards`), `Gres/CfgTRES` → `gpus_total`, `GresUsed/AllocTRES` → `gpus_alloc`, `RealMemory/AllocMem`. Retorna `NodeStatus`.
* `parse_sinfo_line(line)` `slurm.py:121-144` → parsea `sinfo -o "%T %C %G"` ej `"idle 4/8/0/8 gpu:2"` → `(NodeState, cpus_alloc, cpus_total, gpus_total)`. `%C` es `alloc/idle/other/total`.

**Clase `SlurmRepository` `slurm.py:147-290`:**

* Constantes `SCONTROL_CMD`, `SINFO_CMD`, `SQUEUE_CMD` `slurm.py:156-158`.
* `_run(conn, cmd, timeout)` `slurm.py:163-174` → `asyncio.wait_for(conn.run(cmd), timeout)`, normaliza `stdout/stderr` a `str`, retorna `(exit_code, stdout, stderr)`. Timeout → `124, "", "timeout"`.
* `get_node_status(conn, node)` `slurm.py:176-232` — flujo en 4 pasos:
  1. `scontrol show node {node}` → si `exit 0` y `"NodeName=" in stdout` → `parse_scontrol_output` + `_get_pending_jobs`.
  2. Fallback `sinfo -h -n {node} -o "%T %C %G"` → `parse_sinfo_line`.
  3. Fallback sin SLURM → `_fallback_status` (si `stderr` contiene `slurm` o `command not found`).
  4. Si todo falla → `NodeStatus(..., reachable=False, state=UNKNOWN)`.
* `_get_pending_jobs(conn, node)` `slurm.py:234-241` → `squeue -h -w {node} | wc -l`.
* `_fallback_status(conn, node)` `slurm.py:243-290` → `nvidia-smi -L | grep -c GPU`, `nproc`, `cat /proc/loadavg` para estimar `state` (`IDLE` si load 0, `MIX` si load<total, `ALLOC` si load==total).

### 3.6 `app/services/node_selector.py:1-193` (nuevo, trascendental)

* `NoAvailableNodeError` `node_selector.py:13`.
* **Pesos** `node_selector.py:26-37`: `W_GPU_FREE=50`, `W_CPU_FREE_RATIO=30`, `W_PENDING_PENALTY=5`, `W_STATE_BONUS {IDLE:100, MIX:50, ALLOC:5, DRAIN/DOWN/UNKNOWN:-1000}`. GPU priorizada: 1 GPU libre vale 50 pts vs `cpu_ratio` máx 30 pts.
* **`__init__(pool, slurm_repo, cache_ttl)` `node_selector.py:39`** guarda pool/repo, cache `dict[name->(timestamp, NodeStatus)]` y `_cache_lock`.
* **`score(s)` `node_selector.py:51-66`** → `-1000` si `not reachable` o `state` negativo, si no `base + gpus_free*50 + cpu_ratio*30 - pending*5`. Log `debug` detallado.
* **`_fetch_status(node_name)` `node_selector.py:68-105`** → `pool.try_get_connection`; si `None` → `NodeStatus(DOWN, reachable=False)`; si no → `slurm_repo.get_node_status`.
* **`get_all_status(force_refresh)` `node_selector.py:107-137`** → TTL check bajo `_cache_lock` (`time.monotonic()`), si todo fresco retorna cache; si no `gather(*[_fetch_status(n) for n in pool.node_names])` concurrente, actualiza cache con `now`.
* **`select_best(require_gpu, force_refresh)` `node_selector.py:139-167`** → filtra `is_available`, si `require_gpu` filtra `gpus_total>0 and gpus_free>0`; fallback a cualquier GPU si no hay free; ordena `sorted(..., key=(score, gpus_free, cpu_ratio, -pending), reverse=True)`; lanza si `score<0`.
* **`get_ordered_nodes(require_gpu)` `node_selector.py:169-175`** → lista ordenada por `score` para failover.
* **`run_on_best_node(command, require_gpu, timeout)` `node_selector.py:177-193`** → `get_ordered_nodes`, itera `pool.run_on_node` hasta `exit_status==0`, si todos fallan lanza `NoAvailableNodeError`. Usado por `gpu_status`.

### 3.7 `app/lifespan.py:1-33` (reemplazo total, legacy eliminado 2026-09-11)

**Antes:** 1 conexión, sin warmup, con `legacy_conn`.

**Después:**

```python
@asynccontextmanager
async def global_lifespan(app):  # :14
    settings = get_settings()
    pool = get_pool_from_settings()
    selector = NodeSelector(pool, SlurmRepository(), cache_ttl=settings.slurm_poll_interval)  # :18
    if pool.node_names:
        await pool.warmup()  # :25 best-effort
    yield {"ssh_pool": pool, "node_selector": selector, "slurm_repo": SlurmRepository()}  # :32
    await pool.close_all()  # :36
```

* Expone `ssh_pool`/`node_selector`/`slurm_repo`. **Ya no expone `ssh_conn`** legacy.
* No falla startup si nodos caídos.

### 3.8 `app/api/deps.py:1-58`

* `require_user` intacto `deps.py:8-16`.
* Añadido `_get_effective_settings(request)` `deps.py:19-26` → respeta `app.dependency_overrides[get_settings]` en tests.
* `get_ssh_pool(request)` `deps.py:29-42` → primero `app.state.ssh_pool`/`request.state`, fallback crea `SSHConnectionPool` desde `_get_effective_settings`.
* `get_node_selector(request)` `deps.py:45-51` → similar, crea `NodeSelector(pool, cache_ttl)`.
* `get_slurm_repo(request)` `deps.py:54-58`.

### 3.9 `app/api/gpu_status.py:1-90`

* `generate_page(input, node_info)` añade info de nodo en título `gpu_status.py:12-25`.
* `GET /front/gpu_status` `gpu_status.py:28-49` → `selector.run_on_best_node("nvidia-smi", require_gpu=True)` con fallback `require_gpu=False`, retorna `HTMLResponse 503` si `NoAvailableNodeError`.
* Nuevo `GET /front/cluster_status` `gpu_status.py:52-74` → tabla HTML con `statuses` ordenados por `score`, columnas `Gpus libres, Cpus libres, pending, score, reachable`.
* Nuevo `GET /front/gpu_status/{node_name}` `gpu_status.py:77-90` → `pool.run_on_node(node_name, "nvidia-smi")` directo (debug).

### 3.10 Otros

* `app/repositories/ssh.py` y `app/services/ssh_service.py` **eliminados 2026-09-11** (eran re-exports legacy). Código ahora importa directo de `app.repositories.slurm` y `app.services.node_selector`.

---

## 4. Nuevo flujo de conexión y selección

### 4.1 Diagrama (requests con login)

```
[Browser] --POST /auth/login--> [FastAPI] --set cookie--> [Browser]
[Browser] --GET /front/gpu_status (cookie)--> [require_user]
       |
       +--> [deps.get_node_selector] --app.state.node_selector?--> [NodeSelector]
       |                                      |
       |                                      +--> [SSHConnectionPool.pool.node_names = cuda1,cuda2,cuda3]
       |
       +--> [NodeSelector.run_on_best_node("nvidia-smi", require_gpu=True)]
                |
                +--> [get_ordered_nodes] --> [get_all_status]
                |         |--> if cache TTL 15s fresco -> retorno cache
                |         |--> else gather(_fetch_status(cuda1), _fetch_status(cuda2), _fetch_status(cuda3))
                |                       |--> pool.try_get_connection(cudaX) --asyncssh.connect?--> [SSH cudaX]
                |                       |--> SlurmRepository.get_node_status(conn, NodeConfig)
                |                               |--> scontrol show node cudaX --conn.run-->
                |                               |--> parse_scontrol_output -> NodeStatus
                |                               |--> squeue -w cudaX -> pending_jobs
                |                               |--> fallback sinfo / nvidia-smi si SLURM no existe
                |--> score(cada NodeStatus) --> ordena [cuda1(328), cuda2(160), cuda3(-20)]
                |
                +--> for node in ordered:
                        pool.run_on_node(node.name, "nvidia-smi") --conn.run--> result
                        if exit 0: return (node, result)  // failover si falla
                |
       +--> [gpu_status] renderiza HTML con node.gpus_free/cpus_free/state
```

### 4.2 Paso a paso detallado — Doble lectura (técnica + simple)

Cada paso indica **dónde** está el código, **qué hace exactamente** y una **explicación simple** (analogía).

#### Paso 0 — Arranque de la aplicación (una sola vez)

* **Archivo:** `app/lifespan.py:14-32` `global_lifespan` + `app/ssh/connection.py:6-13` `get_pool_from_settings` + `app/ssh/pool.py:95-103` `warmup`
* **Técnico:** Al iniciar FastAPI se ejecuta el `lifespan`. Lee `Settings` (`app/core/config.py:28 get_cluster_nodes`) y construye `SSHConnectionPool(nodes=[cuda1,cuda2,cuda3], connect_timeout=10, keepalive_interval=30)`. Llama a `await pool.warmup(concurrency=3)` que hace `asyncio.gather` con `Semaphore(3)` para abrir en paralelo `asyncssh.connect` a los 3 hosts con `known_hosts=None` y `return_exceptions=True`. Expone en `yield {"ssh_pool":pool, "node_selector":NodeSelector(...)}`.
* **Simple:** Como encender una oficina: abres 3 líneas telefónicas (una por cada sala CUDA) a la vez. Si una sala no contesta, no bloqueas la apertura de la oficina; anotas “esa línea está caída por ahora” y sigues.

#### Paso 1 — Login del usuario

* **Archivo:** `app/api/login.py:21-35` + `app/api/deps.py:8-16`
* **Técnico:** `POST /auth/login` valida `secrets.compare_digest` contra `settings.auth_username/password`, fija `request.session["user"]=username` con `SessionMiddleware` (`app/main.py:11-17`) cookie `session` httponly. Todo lo siguiente requiere esa cookie.
* **Simple:** Enseñas tu credencial en recepción y te dan una pulsera (cookie). Sin pulsera no entras a las salas.

#### Paso 2 — Request a `/front/gpu_status` con cookie

* **Archivo:** `app/api/gpu_status.py:28-33` + `app/api/deps.py:45-51`
* **Técnico:** FastAPI resuelve dependencias: `require_user(request)` lee `request.session.get("user")` y lanza `401` si falta; `get_node_selector(request)` busca `request.app.state.node_selector` (creado en lifespan) o, si no existe (tests sin lifespan), crea uno nuevo vía `_get_effective_settings` que respeta `dependency_overrides`.
* **Simple:** Llegas a recepción con tu pulsera, te verifican y te asignan al encargado que sabe qué sala está más libre.

#### Paso 3 — `NodeSelector.get_all_status()` — ¿Hay datos frescos?

* **Archivo:** `app/services/node_selector.py:107-137`
* **Técnico:** Mira `self._cache: dict[name -> (timestamp, NodeStatus)]` bajo `_cache_lock`. Si `now - timestamp < cache_ttl (15s)` para los 3 nodos (`app/core/config.py:24 slurm_poll_interval`) devuelve cache sin tocar SSH/SLURM. Si no, borra cache y pasa a Paso 4.
* **Simple:** El encargado tiene una pizarra actualizada hace 10 segundos. Si es reciente, no vuelve a llamar a cada sala; usa la pizarra. Si tiene más de 15 segundos, vuelve a preguntar.

#### Paso 4 — Consulta paralela a cada nodo (`_fetch_status`)

* **Archivo:** `app/services/node_selector.py:68-105` `_fetch_status` + `app/ssh/pool.py:62-68` `try_get_connection` + `app/repositories/slurm.py:176-232` `get_node_status`
* **Técnico:** Por cada `node_name` en `pool.node_names` lanza `asyncio.gather(_fetch_status(cuda1), _fetch_status(cuda2), _fetch_status(cuda3))`. Dentro de `_fetch_status`:

  1. `pool.try_get_connection(node_name, timeout=5.0)` → `asyncio.wait_for(pool.get_connection, 5s)`. `get_connection` (`pool.py:35-60`) toma `async with Lock[node]` y si `conn is None or is_closed()` hace `asyncssh.connect(host, user, pass, connect_timeout, keepalive_interval)`. Si falla, `try_get_connection` captura y retorna `None` → `_fetch_status` crea `NodeStatus(DOWN, reachable=False)` sin consultar SLURM.
  2. Si hay `conn`, llama `slurm_repo.get_node_status(conn, NodeConfig)`.

  En `SlurmRepository`:

  * Intenta `scontrol show node {node}` (`slurm.py:156 SCONTROL_CMD`) vía `conn.run` con `_run` (`slurm.py:163-174` con `asyncio.wait_for 7s`). Si `exit 0` y contiene `NodeName=`, parsea con `parse_scontrol_output` (`slurm.py:41-118`) que usa regex `(\w+)=([^\s]+)` y `parse_gres` para GPUs.
  * Si falla, fallback `sinfo -h -n {node} -o "%T %C %G"` (`slurm.py:157`) + `parse_sinfo_line`.
  * Si SLURM no existe (`stderr` con `slurm` o `command not found`), `_fallback_status` (`slurm.py:243-290`) corre `nvidia-smi -L | grep -c GPU`, `nproc`, `cat /proc/loadavg`.
  * Además `_get_pending_jobs` (`slurm.py:234-241`) corre `squeue -h -w {node} | wc -l`.

* **Simple:** El encargado llama a las 3 salas a la vez (no una por una) y le pregunta a cada una: “¿cuánta gente tienes dentro, cuántas GPUs te quedan libres y cuánta cola tienes esperando?”. Si una sala no coge el teléfono, la marca como “no disponible” y no insiste. La pregunta la hace en el idioma que la sala entiende: primero SLURM (`scontrol`), si no, dialecto `sinfo`, si no hay SLURM, mira directamente `nvidia-smi`.

#### Paso 5 — Scoring y ordenamiento (`score`, `select_best`, `get_ordered_nodes`)

* **Archivo:** `app/services/node_selector.py:51-66` `score` + `app/services/node_selector.py:139-175` `select_best`/`get_ordered_nodes` + `app/ssh/models.py:58-74` props `gpus_free`, `cpu_free_ratio`, `is_available`
* **Técnico:** Para cada `NodeStatus` calcula `score = W_STATE_BONUS[state] + gpus_free*50 + cpu_free_ratio*30 - pending*5` (`node_selector.py:26-37` `IDLE=100, MIX=50, ALLOC=5, DRAIN/DOWN/UNKNOWN=-1000`). Filtra `is_available` (`models.py:73` `reachable and state not in DRAIN/DOWN/UNKNOWN`) y si `require_gpu=True` filtra `gpus_free>0`. Ordena `sorted(key=(score, gpus_free, cpu_ratio, -pending), reverse=True)`. `select_best` retorna el primero y lanza `NoAvailableNodeError` si `score<0` o no hay candidatos; `get_ordered_nodes` retorna toda la lista ordenada para failover.
* **Simple:** Con los datos de las salas, el encargado hace ranking: salas vacías (`IDLE`) valen mucho, pero lo que más pesa es cuántas GPUs libres te quedan (cada GPU libre vale 50 puntos, toda la CPU libre solo 30). Si hay gente esperando en la cola, resta puntos. Gana la sala con más puntos; si pide GPU y no tiene GPUs libres, no la considera.

#### Paso 6 — Ejecución con failover (`run_on_best_node`)

* **Archivo:** `app/services/node_selector.py:177-193` `run_on_best_node` + `app/ssh/pool.py:70-93` `run_on_node`
* **Técnico:** Toma `ordered = await get_ordered_nodes(require_gpu=True)` y para cada `node` en orden hace `result = await pool.run_on_node(node.name, "nvidia-smi", timeout=15)`. `run_on_node` hace `try_get_connection` → `conn.run(command)` con `wait_for`. Si falla con `asyncssh.Error/OSError/TimeoutError`, invalida `self._conns[node]=None` bajo `Lock`, reconecta una vez y reintenta. Si `result.exit_status==0` retorna `(node, result)` inmediatamente; si `None` o `exit !=0` prueba el siguiente nodo. Si todos fallan lanza `NoAvailableNodeError`.
* **Simple:** El encargado te manda a la mejor sala. Si al llegar la puerta está cerrada o la máquina no responde, no te deja tirado: prueba la segunda mejor, luego la tercera, hasta que una funciona. Solo si ninguna funciona te dice “no hay salas disponibles”.

#### Paso 7 — Respuesta HTTP

* **Archivo:** `app/api/gpu_status.py:28-49` + `app/api/gpu_status.py:52-74`
* **Técnico:** Si `run_on_best_node` tiene éxito, extrae `result.stdout` y renderiza `generate_page(output, node_info)` con `f"Nodo: {node.name} ({node.host}) | GPUs libres: {node.gpus_free}/{node.gpus_total} | CPUs libres: {node.cpus_free}/{node.cpus_total} | Estado: {node.state.value}"` y retorna `HTMLResponse 200`. Si ambos intentos (`require_gpu=True` y `False`) lanzan `NoAvailableNodeError`, retorna `HTMLResponse 503` con página de error. `/cluster_status` no ejecuta comandos, solo `get_all_status` y pinta tabla ordenada por `score`.
* **Simple:** Te devuelven el resultado de la sala que te tocó, diciéndote en qué sala se ejecutó y cuánto le queda libre. Si ninguna sala estaba disponible, te muestran un cartel “sin salas ahora, intenta luego”.

#### Paso 8 — Cierre (shutdown)

* **Archivo:** `app/lifespan.py:32-38` + `app/ssh/pool.py:105-114`
* **Técnico:** Al parar la app (`yield` termina) ejecuta `await pool.close_all()` que itera `self._conns`, hace `conn.close(); await conn.wait_closed()` con `try/except` y log `SSH closed {name}`, resetea a `None`.
* **Simple:** Al cerrar la oficina cuelgas todas las líneas telefónicas una por una y apagas la centralita.

### 4.3 Scoring (homogéneo, GPU priorizada)

```
score = W_STATE_BONUS[state] + gpus_free * 50 + cpu_free_ratio * 30 - pending_jobs * 5
IDLE=100, MIX=50, ALLOC=5, DRAIN/DOWN/UNKNOWN=-1000
```

Ejemplo: `cuda1 IDLE 28/32 CPUs free (0.875), 4/4 GPUs free, 0 pending => 100+200+26.25-0=326.25` gana a `cuda2 MIX 16/32, 2/4, 1 pending => 50+100+15-5=160`.

### 4.4 Ciclo de vida SSH resumido (vista rápida)

1. **Startup** `lifespan` → `get_pool_from_settings()` crea pool `connect_timeout=10, keepalive=30`; `await pool.warmup()` en paralelo con `Semaphore(3)` y `return_exceptions=True`.
2. **Request** → `get_node_selector` desde `app.state` o fallback tests; `get_all_status()` con `Lock` y TTL 15s; si no fresco `gather(_fetch_status)` → `try_get_connection` + `get_node_status`.
3. **Ejecución** → `run_on_best_node` ordena por `score` y prueba `run_on_node` con retry bajo `Lock`.
4. **Shutdown** → `await pool.close_all()` → `close + wait_closed` por nodo.

---

## 5. Configuración y operación

`.env.example:1-17`

```
CUDA1_IP, CUDA1_USERNAME, CUDA1_PASSWORD
CUDA2_IP, ...
CUDA3_IP, ...
SLURM_POLL_INTERVAL=15
SSH_CONNECT_TIMEOUT=10
SSH_KEEPALIVE_INTERVAL=30
```

* Solo se requieren los nodos que existan; `get_cluster_nodes()` ignora los `None` y sin credenciales explícitas (desde 2026-09-11).
* Cada nodo necesita `IP` + `USERNAME` + `PASSWORD` explícitos.

Endpoints:

* `/front/gpu_status` → mejor nodo (GPU first)
* `/front/cluster_status` → vista completa con scores
* `/front/gpu_status/{node_name}` → directo a nodo

Tuning: aumentar `W_GPU_FREE` en `app/services/node_selector.py:26` si se quiere priorizar aún más GPU; bajar `slurm_poll_interval` para frescura vs carga.

---

## 6. Verificación y testing

* `uv run pytest` 7 passed (login + protected routes) sin necesidad de `.env` con `cuda*` gracias a opcionales.
* Parsers testeados: `parse_gres("gpu:tesla:4")=4`, `parse_scontrol_output` con `IDLE`/`MIX`, `parse_sinfo_line("mix* 12/4/0/16 gpu:2")`.
* Scoring validado: orden `cuda1 > cuda2 > cuda3` con pendientes.
* Mock `pool.try_get_connection` + `SlurmRepository` → `NodeSelector.get_all_status` concurrente y `run_on_best_node` failover.
* Lifespan `warmup` best-effort verificado con `TestClient` y `dependency_overrides`.

Próximas mejoras sugeridas: healthcheck periódico `pool.is_reachable` en background, métricas Prometheus, soporte `private_key_path` además de password, y `sinfo --json` para Slurm >=23.

---

## 7. Diccionario — Conceptos clave

> Glosario para leer el código sin perderte. Cada término indica **qué es**, **dónde aparece** y **para qué sirve** en este proyecto.

| Término | Dónde lo ves | Qué es (técnico) | Para qué sirve aquí | Analogía simple |
|---|---|---|---|---|
| **TTL (Time To Live)** | `app/core/config.py:24` `slurm_poll_interval=15` + `app/services/node_selector.py:48 cache_ttl` + `node_selector.py:109-115` | Segundos que un dato cacheado se considera fresco. `TTL=15s` = no volver a consultar SLURM hasta que pasen 15s desde la última consulta. Se chequea con `time.monotonic()` bajo `Lock`. | Evita saturar SLURM y SSH: cada request no hace 3×`scontrol`+`squeue`, reutiliza la pizarra 15s. Si `TTL` es bajo → más fresco pero más carga; si es alto → menos carga pero datos viejos. | Como la caducidad de un yogur: si lo abriste hace 2 minutos lo usas, si hace 2 horas lo tiras y abres uno nuevo. |
| **Best Effort** | `app/ssh/pool.py:95-103` `warmup` con `gather(return_exceptions=True)` + `app/lifespan.py:25` | Intentar algo sin garantizar éxito y sin abortar el flujo si falla. `return_exceptions=True` hace que si `cuda2` no conecta, `cuda1` y `cuda3` igual se conectan. | El `lifespan` no debe tumbar la app porque un nodo esté caído al arrancar. Marca ese nodo como no disponible y sigue. | Intentas llamar a 3 amigos a la vez; si uno no contesta, hablas con los otros dos y no cuelgas todo. |
| **Lazy Connection (Conexión perezosa)** | `app/ssh/pool.py:21-26` `self._conns = {name: None}` + `pool.py:35-60` `get_connection` | No conectar en `__init__`; conectar solo cuando alguien pide `get_connection(node)` y solo si `conn is None or is_closed()`. | Ahorra recursos y tiempo de arranque; no abre 3 SSH si solo usas 1. También permite tests sin nodos reales. | No enchufas las 3 cafeteras al llegar, solo enchufas la que vas a usar cuando alguien pide café. |
| **Connection Pool** | `app/ssh/pool.py:12-124` clase completa | Diccionario de conexiones vivas indexado por nombre, con `Lock` por nodo. Reutiliza la misma `SSHClientConnection` mientras no se cierre. | Evita `asyncssh.connect` por request (lento, 10s timeout). Una conexión se reusa para `scontrol`, `sinfo`, `squeue` y `nvidia-smi`. | Centralita con 3 líneas: guardas la línea abierta y la reusas, no marcas de cero cada vez. |
| **Keepalive** | `app/ssh/pool.py:48-54` `keepalive_interval` + `app/core/config.py:26` | Paquete `SSH_MSG_IGNORE` cada `N` segundos para que firewalls/NAT no cierren la conexión idle. `asyncssh` lo hace solo. | Conexiones del pool quedan abiertas minutos/horas sin caerse entre requests. | Mandar un “¿sigues ahí?” cada 30s para que no te cuelguen por silencio. |
| **Lock (`asyncio.Lock`)** | `app/ssh/pool.py:24` `self._locks` + `pool.py:39`, `pool.py:81` | Mutex asíncrono: solo una corrutina entra en `async with Lock` a la vez por nodo. | Evita que dos requests reconecten el mismo nodo a la vez y creen dos conexiones duplicadas o corrompan `self._conns`. | Baño con pestillo por sala: solo uno entra a arreglar la línea a la vez. |
| **Semaphore** | `app/ssh/pool.py:97` `Semaphore(3)` en `warmup` | Limita concurrencia: máximo 3 `asyncssh.connect` a la vez. | No satura el login/host con 100 conexiones simultáneas al arrancar. | Portero que deja pasar de 3 en 3, no a todos a la vez. |
| **Failover** | `app/services/node_selector.py:177-193` `run_on_best_node` + `app/ssh/pool.py:70-93` `run_on_node` retry | Probar nodos en orden de `score` hasta que uno responda con `exit 0`; si falla, probar el siguiente. | Si el mejor nodo falla (SSH caído, SLURM error), no devuelves 500, pruebas el segundo mejor automáticamente. | Si la mejor sala está cerrada con llave, vas a la siguiente mejor sin que el cliente se entere. |
| **Scoring / Ranking** | `app/services/node_selector.py:51-66` `score` + `node_selector.py:158-162` `sorted(key=...)` | Función que da puntos a cada `NodeStatus` y ordena. Pesos `GPU*50 > CPU*30` + bonus `IDLE 100`. | Homogéneo pero prioriza GPU: una GPU libre vale más que toda la CPU libre. Decide dónde ejecutar cómputo. | Notas de examen: prácticas de GPU valen 5 puntos, CPU 3 puntos; el que más tiene gana. |
| **Cache** | `app/services/node_selector.py:48` `_cache` + `node_selector.py:109-115` | `dict` en memoria con copia de `NodeStatus` por nodo + timestamp. Protegida por `_cache_lock`. | Evita 3×3 comandos SLURM por cada `/gpu_status`. Con TTL 15s, 100 requests en 15s solo hacen 3 comandos una vez. | Pizarra en la entrada: apuntas el estado de salas y no vuelves a llamar hasta que se borre. |
| **Pending Jobs** | `app/repositories/slurm.py:234-241` `squeue -w {node} | wc -l` + `app/services/node_selector.py:59` `- pending*5` | Jobs en cola SLURM esperando ese nodo (`squeue` estado `PENDING`). Cada uno resta 5 puntos. | Penaliza nodos con cola larga aunque tengan GPUs libres ahora, porque pronto se llenarán. | Gente haciendo cola fuera de la sala: aunque ahora esté vacía, en 2 minutos se llena. |
| **Reachable** | `app/ssh/models.py:54` + `pool.py:116-124` `is_reachable` + `node_selector.py:72-86` | `bool` si `SSH` conecta y `echo ok` responde. Si `False`, `score=-1000`. | Distingue “SLURM dice DRAIN” de “no puedo ni conectar por SSH”. Marca `DOWN` sin invocar SLURM. | ¿Coge el teléfono la sala? Si no, ni preguntas cuántas GPUs tiene. |
| **SLURM / SINFO / SCONTROL / SQUEUE / GRES** | `app/repositories/slurm.py:156-158` | SLURM = gestor de cola HPC. `sinfo` = estado global (`%T %C %G`), `scontrol show node` = detalle por nodo (`CPUAlloc/CPUTot/Gres/State`), `squeue` = jobs en cola, `GRES` = Generic RESources (`gpu:tesla:4`). | Fuente de verdad para decidir disponibilidad; `nvidia-smi` solo confirma, no decide. | SLURM es el conserje que lleva el libro de reservas; `sinfo`/`scontrol` es preguntarle al conserje en vez de mirar por la ventana. |
| **Homogéneo** | `app/services/node_selector.py:18-24` docstring + `app/core/config.py:32` `weight=1` | Los 3 nodos tienen mismas GPUs/CPUs. No hay factor `weight` distinto. | Scoring no multiplica por `weight`; si fueran heterogéneos (ej. `cuda1` con 8 GPUs, `cuda2` con 2) se usaría `weight` o `gpus_total` para ponderar. | 3 salas idénticas; no hay sala VIP con más sillas. |
| **Graceful Shutdown** | `app/lifespan.py:32-38` + `pool.py:105-114` `close_all` con `await wait_closed()` | Cerrar conexiones limpiamente esperando `SSH_MSG_DISCONNECT`. Con `try/except` para no lanzar en cierre. | Evita `ResourceWarning` y conexiones colgando en `TIME_WAIT`. | Al cerrar la oficina apagas luces una por una, no bajas el diferencial general. |
| **Dependency Overrides** | `app/api/deps.py:19-26` `_get_effective_settings` | `app.dependency_overrides[get_settings]=lambda: Settings(...)` en tests para inyectar `.env` falso sin crear archivos. | Permite `TestClient` con `cuda1/2/3` falsos sin tocar `get_settings` cacheado en `lifespan`. | Doble de pruebas: cambias la guía telefónica solo para el ensayo sin tocar la real. |

---

## Changelog 2026-09-11 — Limpieza legacy y revisión GPU 0/0

### Qué se quitó (legacy)

* **`app/ssh/connection.py:6-40` `get_new_ssh_connection` eliminado.** Era wrapper mono-nodo que priorizaba `cuda3`. Ahora `connection.py:1-13` solo expone `get_pool_from_settings()` factory.
* **`app/lifespan.py:33-53` `legacy_conn` eliminado.** Ya no se crea `await pool.try_get_connection(pool.node_names[0])` ni se expone `ssh_conn` en el `yield`. Nuevo `yield` es `{"ssh_pool": pool, "node_selector": selector, "slurm_repo": ...}`. `close_all()` es el único cierre.
* **`app/repositories/ssh.py` y `app/services/ssh_service.py` eliminados.** Eran re-exports `from slurm import SlurmRepository` y `from node_selector import NodeSelector` para compatibilidad con imports antiguos. Ahora se importa directo de `app.repositories.slurm` y `app.services.node_selector`.
* **`app/core/config.py:42 fallback a cuda3` eliminado.** Antes `username=user or self.cuda3_username or ""` heredaba credenciales de `cuda3` si `cuda1`/`cuda2` no tenían. Ahora se exige `user` y `pwd` explícitos por nodo; si faltan, el nodo se ignora (`continue`). Comentario “Se mantienen cuda3_* por compatibilidad” removido.

Verificación tras limpieza: `uv run pytest -q` → 7 passed.

### Revisión GPU `0/0` — Errores evidentes buscados (sin fixes al azar)

Reportas que `/cluster_status` muestra `GPUs libres: 0/0`. Se revisaron sin aplicar parches especulativos los puntos donde `gpus_total` puede quedar en 0 aunque el nodo tenga GPUs:

| Ubicación | Qué se revisó | Hallazgo (no corregido, pendiente de outputs reales) |
|---|---|---|
| `app/repositories/slurm.py:13-14` `_RE_KV`, `_RE_GRES_GPU` | Regex `(\w+)=([^\s]+)` y `gpu(?::[^:,\s]+)?:(\d+)` con `parse_gres` `slurm.py:17-38` | Regex principal no cubre `gres/gpu=4` (formato `CfgTRES`/`AllocTRES` `gres/gpu=4` con `=`). Se salva por fallback `gpu\D*(\d+)`, pero es frágil. Ejemplo `gpu:4` solo funciona por fallback, no por regex principal. Si SLURM devuelve `Gres=gpu:tesla:4(S:0-1)` funciona, pero si devuelve `Gres=gpu:4-8` o `Gres=gpu:tesla:0` podría fallar. **No se toca hasta ver output real.** |
| `app/repositories/slurm.py:80-93` `parse_scontrol_output` Gres/CfgTRES | `Gres` si `(null)` → `CfgTRES`; `GresUsed` → `AllocTRES` si `MIX/ALLOC` | Si tu cluster usa `CfgTRES=cpu=32,...,gres/gpu=4` y `Gres=(null)` (común en Slurm ≥22), el código lo cubre vía `CfgTRES`, pero `_RE_KV` captura `CfgTRES=cpu=32,mem=...,gres/gpu=4` como un solo token con comas; `parse_gres` lo parsea bien por fallback, pero si el formato es `CfgTRES=cpu=32,mem=...,gres/gpu:tesla:4` con `:` en lugar de `=`, el fallback puede dar 0. |
| `app/repositories/slurm.py:80` `Gres` vs `GresUsed`/`AllocTRES` | `GresUsed` `(null)` → 0, solo usa `AllocTRES` si `state MIX/ALLOC` | Si `State=IDLE` y `GresUsed=(null)` pero `AllocTRES` tiene `gres/gpu=1` (reserva), no se cuenta → `gpus_alloc` queda 0 aunque debería ser 0 igual (no es error). Pero si `GresUsed` contiene `gpu:tesla:1,tesla:1` con formato distinto, `parse_gres` puede sumar mal. |
| `app/repositories/slurm.py:176-232` `get_node_status` flujo 1→4 | Condición fallback `if exit_code !=0 or "slurm" in stderr or "command not found" in stdout` | Si `scontrol` retorna `exit 0` pero sin `NodeName=` (ej. `slurm_load_node error: Invalid node name`), no entra en fallback `_fallback_status` ni en `sinfo`, cae al paso 4 `return UNKNOWN 0/0 reachable=False`. Eso explicaría `0/0` con `reachable=False` aunque SSH esté ok. La condición debería incluir también `and "NodeName=" not in stdout` para forzar fallback. **No corregido sin ver tu `scontrol`/`sinfo` reales.** |
| `app/repositories/slurm.py:189-210` `sinfo` fallback | `if "not found" not in stdout2 and "error" not in stdout2` | Si `sinfo -n cuda1` devuelve `sinfo: error: ...` pero con `exit 0`, se detecta y no usa sinfo. Bien. Pero si devuelve línea vacía o `"(null)"` para `%G`, `parse_gres("(null)")=0` → `gpus_total=0` legítimo aunque haya GPUs pero `sinfo` no las reporte por partición incorrecta. |
| `app/ssh/models.py:59` `gpus_free` | `max(0, total-alloc)` | Si `total=0` por parsing, `free` siempre 0 aunque `alloc` sea 0. Es síntoma, no causa. |

**Qué necesitamos de ti para fix definitivo (cuando tengas credenciales):** por favor corre en cada nodo vía SSH directo:

```bash
scontrol show node cuda1 2>&1
scontrol show node cuda2 2>&1
scontrol show node cuda3 2>&1
sinfo -h -n cuda1 -o "%N %T %C %G %m %f" 2>&1
sinfo -h -o "%N %T %C %G %a %P" 2>&1
squeue -h -w cuda1 -o "%T" 2>&1 | head
nvidia-smi -L 2>&1
nvidia-smi --query-gpu=name --format=csv,noheader 2>&1
```

Con esos outputs veremos si es `Gres` vs `CfgTRES` vs `gres/gpu` vs `gpu:tesla:4(S:0)` y ajustaremos `parse_gres`/`_RE_KV` con el formato exacto, sin adivinar.

