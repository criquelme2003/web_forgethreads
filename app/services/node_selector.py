import asyncio
import logging
import time
from typing import Optional

from app.repositories.slurm import SlurmRepository
from app.ssh.models import NodeConfig, NodeStatus, NodeState
from app.ssh.pool import SSHConnectionPool

logger = logging.getLogger(__name__)


class NoAvailableNodeError(RuntimeError):
    pass


class NodeSelector:
    """
    Selección de nodo para cómputo.
    - Nodos homogéneos: mismo peso, prioriza GPU libre > CPU libre > menos jobs pendientes.
    - Conexión directa: consulta cada nodo vía su propio SSH.
    - Cache TTL para no saturar SLURM (default 15s).
    - Failover: intento ordenado por score.
    """

    # Pesos scoring (GPU priorizado)
    W_GPU_FREE = 50.0
    W_CPU_FREE_RATIO = 30.0
    W_PENDING_PENALTY = 5.0
    W_STATE_BONUS = {
        NodeState.IDLE: 100.0,
        NodeState.MIX: 50.0,
        NodeState.ALLOC: 5.0,
        NodeState.DRAIN: -1000.0,
        NodeState.DOWN: -1000.0,
        NodeState.UNKNOWN: -1000.0,
    }

    def __init__(
        self,
        pool: SSHConnectionPool,
        slurm_repo: SlurmRepository | None = None,
        cache_ttl: int = 15,
    ):
        self.pool = pool
        self.slurm_repo = slurm_repo or SlurmRepository()
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, NodeStatus]] = {}  # name -> (timestamp, status)
        self._cache_lock = asyncio.Lock()

    def score(self, s: NodeStatus) -> float:
        if not s.reachable:
            return -1000.0
        base = self.W_STATE_BONUS.get(s.state, -1000.0)
        if base < 0:
            return base
        gpu_score = s.gpus_free * self.W_GPU_FREE
        cpu_score = s.cpu_free_ratio * self.W_CPU_FREE_RATIO
        penalty = s.pending_jobs * self.W_PENDING_PENALTY
        # homogéneos: no weight extra, pero si hay heterogeneous se podría multiplicar por weight
        total = base + gpu_score + cpu_score - penalty
        logger.debug(
            "score %s: state=%s base=%.1f gpu_free=%d (%.1f) cpu_ratio=%.2f (%.1f) pending=%d -> %.1f",
            s.name, s.state.value, base, s.gpus_free, gpu_score, s.cpu_free_ratio, cpu_score, s.pending_jobs, total
        )
        return total

    async def _fetch_status(self, node_name: str) -> NodeStatus:
        cfg = self.pool.get_node_config(node_name)
        conn = await self.pool.try_get_connection(node_name, timeout=5.0)
        if conn is None:
            logger.warning("Node %s unreachable (SSH)", node_name)
            return NodeStatus(
                name=cfg.name,
                host=cfg.host,
                state=NodeState.DOWN,
                cpus_total=0,
                cpus_alloc=0,
                cpus_idle=0,
                gpus_total=0,
                gpus_alloc=0,
                mem_total_mb=0,
                mem_alloc_mb=0,
                pending_jobs=0,
                reachable=False,
            )
        try:
            status = await self.slurm_repo.get_node_status(conn, cfg)
            return status
        except Exception as e:
            logger.warning("get_node_status failed %s: %s", node_name, e)
            return NodeStatus(
                name=cfg.name,
                host=cfg.host,
                state=NodeState.UNKNOWN,
                cpus_total=0,
                cpus_alloc=0,
                cpus_idle=0,
                gpus_total=0,
                gpus_alloc=0,
                mem_total_mb=0,
                mem_alloc_mb=0,
                pending_jobs=0,
                reachable=False,
            )

    async def get_all_status(self, force_refresh: bool = False) -> list[NodeStatus]:
        """Retorna estado de todos los nodos, usando cache TTL si no se fuerza."""
        now = time.monotonic()
        async with self._cache_lock:
            # cache hit?
            if not force_refresh and self._cache:
                all_fresh = all(now - ts < self.cache_ttl for ts, _ in self._cache.values())
                if all_fresh and len(self._cache) == len(self.pool.node_names):
                    return [st for _, st in self._cache.values()]

        # fetch concurrente con timeout global
        tasks = [self._fetch_status(name) for name in self.pool.node_names]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        statuses: list[NodeStatus] = []
        async with self._cache_lock:
            for name, res in zip(self.pool.node_names, results):
                if isinstance(res, Exception):
                    logger.warning("fetch %s exception: %s", name, res)
                    cfg = self.pool.get_node_config(name)
                    res = NodeStatus(
                        name=cfg.name, host=cfg.host, state=NodeState.DOWN,
                        cpus_total=0, cpus_alloc=0, cpus_idle=0,
                        gpus_total=0, gpus_alloc=0, mem_total_mb=0, mem_alloc_mb=0,
                        reachable=False
                    )
                # mypy guard
                assert isinstance(res, NodeStatus)
                statuses.append(res)
                self._cache[name] = (now, res)
        return statuses

    async def select_best(self, require_gpu: bool = False, force_refresh: bool = False, prefer_idle_gpu: bool = False, gpu_idle_threshold: int = 5) -> NodeStatus:
        """
        Selecciona mejor nodo disponible.
        - Filtra no disponibles y sin GPU si require_gpu=True.
        - Si prefer_idle_gpu=True: primero intenta nodos con GPU en idle (util < threshold).
          Si hay al menos uno, elige el mejor entre ellos por score; si no, fallback a scoring normal.
        - Ordena por score descendente. Si empate, prioriza más GPUs libres, luego más CPU libre.
        """
        # Fast-path sencillo: si pide preferencia idle, delega
        if prefer_idle_gpu:
            try:
                idle_best = await self.select_idle_gpu_node(
                    require_gpu=require_gpu,
                    gpu_idle_threshold=gpu_idle_threshold,
                    force_refresh=force_refresh,
                )
                if idle_best is not None:
                    return idle_best
                logger.info("No idle GPU found (threshold=%d), fallback to score", gpu_idle_threshold)
            except Exception as e:
                logger.warning("prefer_idle_gpu failed, fallback to score: %s", e)

        statuses = await self.get_all_status(force_refresh=force_refresh)
        candidates = [s for s in statuses if s.is_available]
        if require_gpu:
            candidates = [s for s in candidates if s.gpus_total > 0 and s.gpus_free > 0]
        if not candidates:
            # fallback: si pedía GPU pero no hay free, intentar con GPU aunque esté alloc
            if require_gpu:
                candidates = [s for s in statuses if s.is_available and s.gpus_total > 0]
            if not candidates:
                raise NoAvailableNodeError(f"No hay nodos disponibles (require_gpu={require_gpu}) estados: {[(s.name, s.state.value, s.reachable) for s in statuses]}")

        # ordenar por score, luego por gpus_free, luego cpu ratio, luego menos pending
        ranked = sorted(
            candidates,
            key=lambda s: (self.score(s), s.gpus_free, s.cpu_free_ratio, -s.pending_jobs),
            reverse=True,
        )
        best = ranked[0]
        if self.score(best) < 0:
            raise NoAvailableNodeError(f"Mejor nodo no disponible: {best.name} score={self.score(best)}")
        logger.info("Selected node %s score=%.1f gpu_free=%d cpu_free=%.2f state=%s", best.name, self.score(best), best.gpus_free, best.cpu_free_ratio, best.state.value)
        return best

    # --- GPU idle: forma sencilla de priorizar nodo con GPU en idle ---
    async def _check_idle_for_status(self, status: NodeStatus, threshold: int = 5) -> NodeStatus:
        """Consulta nvidia-smi en el nodo y rellena has_idle_gpu/gpu_utils. Retorna status enrichado."""
        if not status.reachable:
            status.has_idle_gpu = False
            status.gpu_utils = []
            return status
        conn = await self.pool.try_get_connection(status.name, timeout=5.0)
        if conn is None:
            status.has_idle_gpu = False
            status.gpu_utils = []
            return status
        try:
            await self.slurm_repo.enrich_status_with_idle(conn, status, threshold=threshold)
        except Exception as e:
            logger.debug("idle check %s failed: %s", status.name, e)
            status.has_idle_gpu = False
            status.gpu_utils = []
        return status

    async def find_idle_gpu_nodes(
        self, require_gpu: bool = False, gpu_idle_threshold: int = 5, force_refresh: bool = False
    ) -> list[NodeStatus]:
        """
        Retorna lista de nodos disponibles que tienen al menos una GPU en idle (util < threshold).
        - Consulta nvidia-smi en paralelo solo sobre candidatos is_available.
        - Vacía si ninguno tiene GPU idle.
        Uso sencillo:
            idle_nodes = await selector.find_idle_gpu_nodes()
            if idle_nodes: best = max(idle_nodes, key=selector.score)
        """
        statuses = await self.get_all_status(force_refresh=force_refresh)
        candidates = [s for s in statuses if s.is_available]
        if require_gpu:
            candidates = [s for s in candidates if s.gpus_total > 0 or s.gpus_total == 0]  # si gpus_total==0 pero nvidia-smi puede revelar GPUs, no filtrar agresivo
            # filtrar luego por has_idle_gpu
        if not candidates:
            return []
        # check idle en paralelo
        enriched = await asyncio.gather(*[self._check_idle_for_status(s, threshold=gpu_idle_threshold) for s in candidates])
        idle_nodes = [s for s in enriched if s.has_idle_gpu]
        # si filtrado require_gpu estricto, asegurar que realmente tiene GPU
        if require_gpu:
            idle_nodes = [s for s in idle_nodes if (s.gpus_total > 0 or (s.gpu_utils and len(s.gpu_utils) > 0))]
        # ordenar por score para que el caller tenga ranking
        idle_nodes.sort(key=lambda s: (self.score(s), s.gpus_free, s.cpu_free_ratio, -s.pending_jobs), reverse=True)
        logger.info("Idle GPU nodes (threshold=%d): %s", gpu_idle_threshold, [(n.name, n.gpu_utils, self.score(n)) for n in idle_nodes])
        return idle_nodes

    async def select_idle_gpu_node(
        self, require_gpu: bool = False, gpu_idle_threshold: int = 5, force_refresh: bool = False
    ) -> NodeStatus | None:
        """
        Forma sencilla: selecciona un nodo con GPU en idle; si no hay ninguno retorna None.
        Caller debe hacer fallback a select_best():
            node = await selector.select_idle_gpu_node() or await selector.select_best()
        O usar select_best(prefer_idle_gpu=True) que hace esto automáticamente.
        """
        idle_nodes = await self.find_idle_gpu_nodes(
            require_gpu=require_gpu, gpu_idle_threshold=gpu_idle_threshold, force_refresh=force_refresh
        )
        if not idle_nodes:
            return None
        return idle_nodes[0]

    async def select_best_with_idle_priority(
        self, require_gpu: bool = False, gpu_idle_threshold: int = 5, force_refresh: bool = False
    ) -> NodeStatus:
        """
        Azúcar sintáctico explícito: intenta idle primero, si no hay fallback a score.
        Equivalente a select_best(prefer_idle_gpu=True).
        """
        idle = await self.select_idle_gpu_node(
            require_gpu=require_gpu, gpu_idle_threshold=gpu_idle_threshold, force_refresh=force_refresh
        )
        if idle is not None:
            logger.info("Selected idle GPU node %s utils=%s", idle.name, idle.gpu_utils)
            return idle
        return await self.select_best(require_gpu=require_gpu, force_refresh=force_refresh, prefer_idle_gpu=False)

    async def get_ordered_nodes(self, require_gpu: bool = False) -> list[NodeStatus]:
        """Retorna todos los candidatos ordenados por score (para failover)."""
        statuses = await self.get_all_status()
        candidates = [s for s in statuses if s.is_available]
        if require_gpu:
            candidates = [s for s in candidates if s.gpus_total > 0]
        return sorted(candidates, key=lambda s: self.score(s), reverse=True)

    async def get_ordered_nodes_with_idle_priority(
        self, require_gpu: bool = False, gpu_idle_threshold: int = 5, force_refresh: bool = False
    ) -> list[NodeStatus]:
        """
        Retorna candidatos ordenados con idle primero:
        - Los que tienen GPU idle al frente (ordenados por score).
        - Resto detrás (ordenados por score).
        Útil para run_on_best_node con preferencia idle pero con failover completo.
        """
        idle_nodes = await self.find_idle_gpu_nodes(
            require_gpu=require_gpu, gpu_idle_threshold=gpu_idle_threshold, force_refresh=force_refresh
        )
        if idle_nodes:
            # ordered normal sin duplicados
            normal_ordered = await self.get_ordered_nodes(require_gpu=require_gpu)
            idle_names = {n.name for n in idle_nodes}
            rest = [n for n in normal_ordered if n.name not in idle_names]
            return idle_nodes + rest
        return await self.get_ordered_nodes(require_gpu=require_gpu)

    async def run_on_best_node(
        self, command: str, require_gpu: bool = False, timeout: float = 15.0, prefer_idle_gpu: bool = False, gpu_idle_threshold: int = 5
    ):
        """
        Ejecuta comando en el mejor nodo con failover.
        - Si prefer_idle_gpu=True: intenta idle primero (util < threshold), si no hay idle usa score.
        Intenta en orden de ranking hasta que uno responda.
        Retorna (NodeStatus, result).
        """
        if prefer_idle_gpu:
            ordered = await self.get_ordered_nodes_with_idle_priority(
                require_gpu=require_gpu, gpu_idle_threshold=gpu_idle_threshold
            )
        else:
            ordered = await self.get_ordered_nodes(require_gpu=require_gpu)
        if not ordered:
            raise NoAvailableNodeError("No hay nodos para ejecutar comando")
        last_err = None
        for node in ordered:
            result = await self.pool.run_on_node(node.name, command, timeout=timeout)
            if result is not None and result.exit_status == 0:
                return node, result
            last_err = result
            logger.warning("run_on_best_node failed on %s, trying next", node.name)
        raise NoAvailableNodeError(f"Todos los nodos fallaron para '{command}' last={last_err}")
