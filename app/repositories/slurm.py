import asyncio
import re
import logging
from typing import Optional

import asyncssh

from app.ssh.models import NodeConfig, NodeStatus, NodeState

logger = logging.getLogger(__name__)

# Regex para scontrol show node
_RE_KV = re.compile(r"(\w+)=([^\s]+)")
_RE_GRES_GPU = re.compile(r"gpu(?::[^:,\s]+)?:(\d+)", re.IGNORECASE)


def parse_gres(gres_str: str) -> int:
    """Extrae total GPUs de Gres ej: 'gpu:tesla:4' 'gpu:4' 'gpu:rtx:2,gpu:tesla:2'."""
    if not gres_str or gres_str in ("(null)", "none"):
        return 0
    total = 0
    for m in _RE_GRES_GPU.finditer(gres_str):
        try:
            total += int(m.group(1))
        except ValueError:
            pass
    # fallback: si solo dice 'gpu' sin count, asumir 1
    if total == 0 and "gpu" in gres_str.lower():
        # intentar 'gpu:2' sin prefijo
        m2 = re.search(r"gpu\D*(\d+)", gres_str, re.IGNORECASE)
        if m2:
            try:
                total = int(m2.group(1))
            except ValueError:
                total = 1
        else:
            total = 1
    return total


def parse_scontrol_output(raw: str, node_name: str, host: str) -> NodeStatus:
    """
    Parsea salida de `scontrol show node <name>`.
    Ejemplo:
    NodeName=cuda1 Arch=x86_64 CoresPerSocket=8
       CPUAlloc=4 CPUTot=32 CPULoad=1.23
       Gres=gpu:tesla:4
       State=IDLE
       RealMemory=64000 AllocMem=16000 FreeMem=48000
    """
    # scontrol puede devolver múltiples líneas y pares k=v con espacios
    flat = " ".join(raw.strip().split())
    kv: dict[str, str] = {}
    for m in _RE_KV.finditer(flat):
        kv[m.group(1)] = m.group(2)

    state_raw = kv.get("State", "unknown")
    # State puede ser "IDLE+MIXED" etc; nos quedamos con el primero
    state_token = state_raw.split("+")[0].split("*")[0]
    state = NodeState.from_slurm(state_token)

    try:
        cpus_alloc = int(kv.get("CPUAlloc", 0))
    except ValueError:
        cpus_alloc = 0
    try:
        cpus_total = int(kv.get("CPUTot", kv.get("CPUTotal", 0)))
    except ValueError:
        cpus_total = 0
    # Fallback si CPUTot no está: inferir de Cores*Sockets
    if cpus_total == 0:
        try:
            cpus_total = int(kv.get("CoresPerSocket", 0)) * int(kv.get("Sockets", 1)) * int(kv.get("Boards", 1))
        except ValueError:
            pass

    # CPUs idle no siempre viene; calcular
    cpus_idle = max(0, cpus_total - cpus_alloc)

    gres = kv.get("Gres", "")
    # Gres puede estar vacío y GPUs estar en "GresUsed" o "CfgTRES"
    if not gres or gres == "(null)":
        gres = kv.get("CfgTRES", "")
    gpus_total = parse_gres(gres)

    # GresUsed indica alloc
    gres_used = kv.get("GresUsed", "")
    gpus_alloc = parse_gres(gres_used) if gres_used and gres_used != "(null)" else 0
    # Si no hay GresUsed pero State=ALLOC/MIX, no podemos saber alloc exacto -> estimar 0 si idle
    if gpus_total > 0 and gpus_alloc == 0 and state in (NodeState.ALLOC, NodeState.MIX):
        # intentar AllocTRES
        alloc_tres = kv.get("AllocTRES", "")
        gpus_alloc = parse_gres(alloc_tres)

    try:
        mem_total = int(kv.get("RealMemory", 0))
    except ValueError:
        mem_total = 0
    try:
        mem_alloc = int(kv.get("AllocMem", 0))
    except ValueError:
        mem_alloc = 0

    return NodeStatus(
        name=node_name,
        host=host,
        state=state,
        cpus_total=cpus_total,
        cpus_alloc=cpus_alloc,
        cpus_idle=cpus_idle,
        gpus_total=gpus_total,
        gpus_alloc=gpus_alloc,
        mem_total_mb=mem_total,
        mem_alloc_mb=mem_alloc,
        pending_jobs=0,
        reachable=True,
        raw_scontrol=raw,
    )


def parse_sinfo_line(line: str) -> tuple[NodeState, int, int, int]:
    """
    Parsea línea de `sinfo -h -o "%T %C %G"`: ej "idle 4/8/0/8 gpu:2"
    Retorna (state, cpus_alloc, cpus_total, gpus_total)
    """
    parts = line.strip().split()
    if not parts:
        raise ValueError("empty sinfo line")
    state = NodeState.from_slurm(parts[0])
    cpus_alloc = 0
    cpus_total = 0
    if len(parts) >= 2:
        # %C es "alloc/idle/other/total"
        c_tokens = parts[1].split("/")
        if len(c_tokens) == 4:
            try:
                cpus_alloc = int(c_tokens[0])
                cpus_total = int(c_tokens[3])
            except ValueError:
                pass
    gpus_total = 0
    if len(parts) >= 3:
        gpus_total = parse_gres(parts[2])
    return state, cpus_alloc, cpus_total, gpus_total


class SlurmRepository:
    """
    Consultas SLURM vía SSH. Diseñado para conexión directa:
    - Cada nodo responde sobre sí mismo via scontrol/sinfo.
    - Si scontrol falla, fallback a sinfo.
    - Si SLURM no está disponible, fallback a nvidia-smi + lscpu (best-effort) para no dejar nodo como down.
    """

    # Comandos SLURM (parametrizados para evitar inyección)
    SCONTROL_CMD = "scontrol show node {node} 2>&1"
    SINFO_CMD = "sinfo -h -n {node} -o \"%T %C %G\" 2>&1"
    SQUEUE_CMD = "squeue -h -w {node} -o \"%T\" 2>&1 | wc -l"
    # Fallbacks sin SLURM
    GPU_FALLBACK_CMD = "nvidia-smi --query-gpu=count --format=csv,noheader 2>&1 || echo 0"
    GPU_USED_FALLBACK = "nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>&1 | wc -l"

    async def _run(self, conn: asyncssh.SSHClientConnection, cmd: str, timeout: float = 7.0) -> tuple[int, str, str]:
        try:
            result = await asyncio.wait_for(conn.run(cmd), timeout=timeout)
            stdout = result.stdout if isinstance(result.stdout, str) else str(result.stdout or "")
            stderr = result.stderr if isinstance(result.stderr, str) else str(result.stderr or "")
            return result.exit_status or 0, stdout.strip(), stderr.strip()
        except asyncio.TimeoutError:
            logger.warning("SLURM command timeout: %s", cmd)
            return 124, "", "timeout"
        except Exception as e:
            logger.warning("SLURM command error %s: %s", cmd, e)
            return 1, "", str(e)

    async def get_node_status(self, conn: asyncssh.SSHClientConnection, node: NodeConfig) -> NodeStatus:
        # 1) Intentar scontrol (más rico)
        exit_code, stdout, stderr = await self._run(conn, self.SCONTROL_CMD.format(node=node.name))
        if exit_code == 0 and "NodeName=" in stdout:
            try:
                status = parse_scontrol_output(stdout, node.name, node.host)
                # enriquecer con pending jobs
                status.pending_jobs = await self._get_pending_jobs(conn, node)
                return status
            except Exception as e:
                logger.warning("parse_scontrol failed for %s: %s", node.name, e)

        # 2) Fallback sinfo
        exit_code2, stdout2, _ = await self._run(conn, self.SINFO_CMD.format(node=node.name))
        if exit_code2 == 0 and stdout2 and "not found" not in stdout2.lower() and "error" not in stdout2.lower():
            try:
                state, cpus_alloc, cpus_total, gpus_total = parse_sinfo_line(stdout2.splitlines()[0])
                status = NodeStatus(
                    name=node.name,
                    host=node.host,
                    state=state,
                    cpus_total=cpus_total,
                    cpus_alloc=cpus_alloc,
                    cpus_idle=max(0, cpus_total - cpus_alloc),
                    gpus_total=gpus_total,
                    gpus_alloc=0,  # sinfo no da alloc GPU, estimar 0
                    mem_total_mb=0,
                    mem_alloc_mb=0,
                    pending_jobs=await self._get_pending_jobs(conn, node),
                    reachable=True,
                    raw_scontrol=stdout2,
                )
                return status
            except Exception as e:
                logger.warning("parse_sinfo failed for %s: %s", node.name, e)

        # 3) Fallback sin SLURM: nvidia-smi + lscpu
        if exit_code != 0 or "slurm" in stderr.lower() or "command not found" in stdout.lower():
            logger.info("SLURM not available on %s, using fallback", node.name)
            return await self._fallback_status(conn, node)

        # 4) Nodo no alcanzable o error desconocido
        return NodeStatus(
            name=node.name,
            host=node.host,
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
            raw_scontrol=stdout or stderr,
        )

    async def _get_pending_jobs(self, conn: asyncssh.SSHClientConnection, node: NodeConfig) -> int:
        exit_code, stdout, _ = await self._run(conn, self.SQUEUE_CMD.format(node=node.name), timeout=5.0)
        if exit_code == 0:
            try:
                return int(stdout.strip().split()[0])
            except (ValueError, IndexError):
                return 0
        return 0

    async def _fallback_status(self, conn: asyncssh.SSHClientConnection, node: NodeConfig) -> NodeStatus:
        # GPUs totales via nvidia-smi -L | wc -l o query
        _, gpu_out, _ = await self._run(conn, "nvidia-smi -L 2>&1 | grep -c \"GPU\" || echo 0", timeout=5.0)
        try:
            gpus_total = int(gpu_out.strip().split()[0])
        except (ValueError, IndexError):
            gpus_total = 0

        # CPU total via nproc
        _, cpu_out, _ = await self._run(conn, "nproc 2>&1 || echo 0", timeout=3.0)
        try:
            cpus_total = int(cpu_out.strip().split()[0])
        except (ValueError, IndexError):
            cpus_total = 0

        # Load: si > 80% considerar MIX, si 0 idle
        _, load_out, _ = await self._run(conn, "cat /proc/loadavg 2>&1 | awk '{print $1}'", timeout=3.0)
        try:
            load = float(load_out.strip().split()[0])
            cpu_alloc_est = int(min(cpus_total, round(load)))
        except (ValueError, IndexError):
            cpu_alloc_est = 0

        # Estado estimado
        if cpus_total == 0:
            state = NodeState.UNKNOWN
        elif cpu_alloc_est == 0:
            state = NodeState.IDLE
        elif cpu_alloc_est < cpus_total:
            state = NodeState.MIX
        else:
            state = NodeState.ALLOC

        return NodeStatus(
            name=node.name,
            host=node.host,
            state=state,
            cpus_total=cpus_total,
            cpus_alloc=cpu_alloc_est,
            cpus_idle=max(0, cpus_total - cpu_alloc_est),
            gpus_total=gpus_total,
            gpus_alloc=0,  # sin lock, asumir 0 free
            mem_total_mb=0,
            mem_alloc_mb=0,
            pending_jobs=0,
            reachable=True,
            raw_scontrol="fallback",
        )
