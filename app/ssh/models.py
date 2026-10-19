from dataclasses import dataclass
from enum import Enum


class NodeState(str, Enum):
    IDLE = "idle"
    MIX = "mix"
    ALLOC = "alloc"
    DRAIN = "drain"
    DOWN = "down"
    UNKNOWN = "unknown"

    @classmethod
    def from_slurm(cls, raw: str) -> "NodeState":
        """Normaliza estados SLURM (mayúsculas, con sufijos *, #, etc).

        El sufijo `*` (sinfo) indica que el nodo no responde: se trata como DOWN.
        """
        token = raw.strip().lower().split()[0]  # "idle", "mix*", "drain"
        if token.endswith("*"):
            return cls.DOWN
        cleaned = token.rstrip("*#~$!-@^%")  # sufijos SLURM
        for state in cls:
            if cleaned == state.value:
                return state
        # aliases SLURM: idle, mixed->mix, allocated->alloc
        if cleaned in ("mixed", "mix"):
            return cls.MIX
        if cleaned in ("allocated", "alloc"):
            return cls.ALLOC
        if cleaned in ("draining", "drain", "drained", "maint", "reserved"):
            # Mantención o reserva: no acepta jobs comunes, igual que un nodo drenado.
            return cls.DRAIN
        if cleaned in ("down", "fail", "error"):
            return cls.DOWN
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class NodeConfig:
    name: str  # cuda1, cuda2, cuda3
    host: str
    username: str
    password: str
    weight: int = 1


@dataclass(slots=True)
class NodeStatus:
    name: str
    host: str
    state: NodeState
    cpus_total: int
    cpus_alloc: int
    cpus_idle: int
    gpus_total: int
    gpus_alloc: int
    mem_total_mb: int
    mem_alloc_mb: int
    pending_jobs: int = 0
    reachable: bool = True
    raw_scontrol: str | None = None
    state_flags: tuple[str, ...] = ()  # flags de State en scontrol (ej. ("DRAIN",) de IDLE+DRAIN)
    reason: str | None = None  # Reason= de scontrol (motivo del drain/down)
    # --- GPU idle (opcional, se rellena bajo demanda) ---
    has_idle_gpu: bool | None = None  # None = no consultado, True/False = resultado
    gpu_utils: list[int] | None = None  # util % por GPU si se consultó (ej [0, 45])

    @property
    def gpus_free(self) -> int:
        return max(0, self.gpus_total - self.gpus_alloc)

    @property
    def cpus_free(self) -> int:
        return max(0, self.cpus_total - self.cpus_alloc)

    @property
    def cpu_free_ratio(self) -> float:
        if self.cpus_total == 0:
            return 0.0
        return self.cpus_free / self.cpus_total

    @property
    def is_available(self) -> bool:
        return self.reachable and self.state not in (NodeState.DRAIN, NodeState.DOWN, NodeState.UNKNOWN)

    @property
    def is_gpu_idle(self) -> bool | None:
        """Atajo para has_idle_gpu (None si no se ha consultado)."""
        return self.has_idle_gpu
