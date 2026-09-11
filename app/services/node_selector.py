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

    async def select_best(self, require_gpu: bool = False, force_refresh: bool = False) -> NodeStatus:
        """
        Selecciona mejor nodo disponible.
        - Filtra no disponibles y sin GPU si require_gpu=True.
        - Ordena por score descendente.
        - Si empate, prioriza más GPUs libres, luego más CPU libre.
        """
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

    async def get_ordered_nodes(self, require_gpu: bool = False) -> list[NodeStatus]:
        """Retorna todos los candidatos ordenados por score (para failover)."""
        statuses = await self.get_all_status()
        candidates = [s for s in statuses if s.is_available]
        if require_gpu:
            candidates = [s for s in candidates if s.gpus_total > 0]
        return sorted(candidates, key=lambda s: self.score(s), reverse=True)

    async def run_on_best_node(self, command: str, require_gpu: bool = False, timeout: float = 15.0):
        """
        Ejecuta comando en el mejor nodo con failover.
        Intenta en orden de ranking hasta que uno responda.
        Retorna (NodeStatus, result).
        """
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
