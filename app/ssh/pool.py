import asyncio
import logging
from typing import Dict

import asyncssh

from app.ssh.models import NodeConfig

logger = logging.getLogger(__name__)


class SSHConnectionPool:
    """
    Pool de conexiones SSH directas a cada nodo del cluster.
    - Lazy: conecta bajo demanda.
    - Reconnect: si conn.is_closed() -> reconecta.
    - Thread-safe por nodo via asyncio.Lock.
    - Keepalive configurado via asyncssh.
    """

    def __init__(self, nodes: list[NodeConfig], connect_timeout: int = 10, keepalive_interval: int = 30):
        self._nodes: Dict[str, NodeConfig] = {n.name: n for n in nodes}
        self._conns: Dict[str, asyncssh.SSHClientConnection | None] = {n.name: None for n in nodes}
        self._locks: Dict[str, asyncio.Lock] = {n.name: asyncio.Lock() for n in nodes}
        self._connect_timeout = connect_timeout
        self._keepalive_interval = keepalive_interval

    @property
    def node_names(self) -> list[str]:
        return list(self._nodes.keys())

    def get_node_config(self, name: str) -> NodeConfig:
        return self._nodes[name]

    async def get_connection(self, node_name: str) -> asyncssh.SSHClientConnection:
        """Retorna conexión activa para el nodo, reconectando si es necesario."""
        if node_name not in self._nodes:
            raise ValueError(f"Nodo desconocido: {node_name}")
        async with self._locks[node_name]:
            conn = self._conns[node_name]
            if conn is not None and not conn.is_closed():
                # health ping ligero (opcional, no bloqueante)
                return conn
            # (re)conectar
            cfg = self._nodes[node_name]
            logger.info("SSH connect %s@%s (%s)", cfg.username, cfg.host, cfg.name)
            try:
                conn = await asyncssh.connect(
                    host=cfg.host,
                    username=cfg.username,
                    password=cfg.password,
                    known_hosts=None,
                    connect_timeout=self._connect_timeout,
                    keepalive_interval=self._keepalive_interval,
                )
            except Exception as e:
                logger.warning("SSH connect failed %s: %s", cfg.name, e)
                raise
            self._conns[node_name] = conn
            return conn

    async def try_get_connection(self, node_name: str, timeout: float = 5.0) -> asyncssh.SSHClientConnection | None:
        """Intenta conectar con timeout, retorna None si falla (para scoring)."""
        try:
            return await asyncio.wait_for(self.get_connection(node_name), timeout=timeout)
        except Exception as e:
            logger.debug("try_get_connection %s failed: %s", node_name, e)
            return None

    async def run_on_node(self, node_name: str, command: str, timeout: float = 10.0) -> asyncssh.SSHCompletedProcess | None:
        """Helper: obtiene conexión y ejecuta comando. Reconecta 1 vez si la sesión murió."""
        conn = await self.try_get_connection(node_name, timeout=self._connect_timeout)
        if conn is None:
            return None
        try:
            result = await asyncio.wait_for(conn.run(command), timeout=timeout)
            return result
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as e:
            logger.warning("run_on_node %s failed, retrying once: %s", node_name, e)
            # invalidar y reintentar una vez
            async with self._locks[node_name]:
                old = self._conns[node_name]
                if old is not None:
                    old.close()
                    self._conns[node_name] = None
            conn2 = await self.try_get_connection(node_name, timeout=self._connect_timeout)
            if conn2 is None:
                return None
            try:
                return await asyncio.wait_for(conn2.run(command), timeout=timeout)
            except Exception as e2:
                logger.warning("retry run_on_node %s failed: %s", node_name, e2)
                return None

    async def warmup(self, concurrency: int = 3):
        """Pre-conecta todos los nodos en paralelo (best effort, no falla si uno cae)."""
        sem = asyncio.Semaphore(concurrency)

        async def _connect_one(name: str):
            async with sem:
                await self.try_get_connection(name, timeout=self._connect_timeout)

        await asyncio.gather(*[_connect_one(n) for n in self.node_names], return_exceptions=True)

    async def close_all(self):
        for name, conn in self._conns.items():
            if conn is not None and not conn.is_closed():
                try:
                    conn.close()
                    await conn.wait_closed()
                except Exception:
                    pass
                logger.info("SSH closed %s", name)
        self._conns = {k: None for k in self._conns}

    async def is_reachable(self, node_name: str) -> bool:
        conn = await self.try_get_connection(node_name, timeout=3.0)
        if conn is None:
            return False
        try:
            res = await asyncio.wait_for(conn.run("echo ok"), timeout=3.0)
            return res.exit_status == 0
        except Exception:
            return False
