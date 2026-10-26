"""Barrido de simulación (RF-31 a RF-37): combinaciones válidas, estimación de duración y estadísticas.

El barrido corre en un solo job (`sweep_job.sh`) que replica `comprobacion_empirica/test2.py`:
para cada `c` y cada `N` hace `R` repeticiones de MaxMin y escribe `c,n,repeticion,orden_efectivo`
en `jobs_results/<JOBID>.csv`. El backend descarga ese CSV y calcula las estadísticas por (`n`, `c`).
"""

import csv
import io
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

# Semilla de cada repetición: base × 10⁹ + rep × 1000 + n (con base 0 coincide con test2.py, RF-34).
SEED_BLOCK = 10**9
RAW_COLUMNS = ("c", "n", "repeticion", "orden_efectivo")
AGGREGATE_COLUMNS = (
    "n", "c", "media", "desviacion_estandar", "error_estandar", "minimo", "maximo", "repeticiones",
)
# Regímenes de c para el marcador de cada punto (RF-37) y sus líneas de referencia.
REGIME_LIMITS = (1.0, 1.25, 2.0, 3.0)


@dataclass
class Combination:
    n: int
    c: float


@dataclass
class SkippedCombination:
    n: int
    c: float
    reason: str


@dataclass
class SweepPlan:
    valid: list[Combination]
    skipped: list[SkippedCombination]
    reps: int
    total_runs: int
    estimated_s: float  # duración estimada del cómputo, sin margen
    time_limit_s: int  # lo que se pide a SLURM con --time (con margen y sobrecarga)
    warnings: list[str] = field(default_factory=list)


def combination_problem(n: int, c: float) -> str | None:
    """Regla estricta de matrix_construction.py (RF-32): N par, c > 0 y c ≤ N/2 − 1."""
    if n % 2 != 0:
        return "N impar"
    if c <= 0:
        return "c ≤ 0"
    if c > n / 2 - 1:
        return "c > N/2 − 1"
    return None


def run_seconds(n: int, base_s: float, s_per_kn3: float) -> float:
    """Duración estimada de una repetición: MaxMin sobre una matriz n×n crece como n³."""
    return base_s + s_per_kn3 * (n / 1000) ** 3


def plan_sweep(
    ns: Iterable[int],
    cs: Iterable[float],
    reps: int,
    *,
    run_base_s: float,
    run_s_per_kn3: float,
    margin: float,
    overhead_s: float,
) -> SweepPlan:
    """Combinaciones en el orden en que las recorre el job (c afuera, n adentro) y su duración."""
    valid: list[Combination] = []
    skipped: list[SkippedCombination] = []
    for c in cs:
        for n in ns:
            problem = combination_problem(n, c)
            if problem:
                skipped.append(SkippedCombination(n=n, c=c, reason=problem))
            else:
                valid.append(Combination(n=n, c=c))
    estimated = sum(reps * run_seconds(x.n, run_base_s, run_s_per_kn3) for x in valid)
    warnings = []
    if valid and max(x.n for x in valid) >= 1000:
        warnings.append(
            "Con N ≥ 1000 la fórmula de semillas de test2.py (rep × 1000 + n) repite semillas "
            "entre repeticiones de distintos N"
        )
    return SweepPlan(
        valid=valid,
        skipped=skipped,
        reps=reps,
        total_runs=reps * len(valid),
        estimated_s=estimated,
        time_limit_s=math.ceil(estimated * margin + overhead_s),
        warnings=warnings,
    )


def slurm_time(seconds: int) -> str:
    """Formato de `sbatch --time`: HH:MM:SS, o D-HH:MM:SS desde un día."""
    seconds = max(60, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    hms = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}-{hms}" if days else hms


def format_number(value: float) -> str:
    """c entero sin decimales ("2", no "2.0"), igual que en el comando de new_job."""
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


# --- resultados ----------------------------------------------------------------------------------


@dataclass
class RawRow:
    c: float
    n: int
    rep: int
    order: int


@dataclass
class CombinationStats:
    n: int
    c: float
    mean: float
    sd: float | None  # desviación estándar muestral; None con una sola repetición
    sem: float | None  # error estándar de la media
    min: int
    max: int
    count: int


def parse_raw_csv(text: str) -> list[RawRow]:
    """Filas del CSV crudo `c,n,repeticion,orden_efectivo`; ignora las que no se pueden leer."""
    rows: list[RawRow] = []
    reader = csv.DictReader(io.StringIO(text))
    for record in reader:
        try:
            rows.append(
                RawRow(
                    c=float(record["c"]),
                    n=int(float(record["n"])),
                    rep=int(float(record["repeticion"])),
                    order=int(float(record["orden_efectivo"])),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def combination_stats(rows: Iterable[RawRow]) -> list[CombinationStats]:
    """Media, desviación estándar, error estándar, mínimo, máximo y repeticiones por (n, c) (RF-35)."""
    groups: dict[tuple[int, float], list[int]] = {}
    for row in rows:
        groups.setdefault((row.n, row.c), []).append(row.order)
    out = []
    for (n, c), orders in sorted(groups.items()):
        sd = statistics.stdev(orders) if len(orders) > 1 else None
        out.append(
            CombinationStats(
                n=n,
                c=c,
                mean=statistics.fmean(orders),
                sd=sd,
                sem=sd / math.sqrt(len(orders)) if sd is not None else None,
                min=min(orders),
                max=max(orders),
                count=len(orders),
            )
        )
    return out


def stats_csv(stats: Iterable[CombinationStats]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(AGGREGATE_COLUMNS)
    for s in stats:
        writer.writerow(
            [s.n, format_number(s.c), s.mean, "" if s.sd is None else s.sd,
             "" if s.sem is None else s.sem, s.min, s.max, s.count]
        )
    return buffer.getvalue()
