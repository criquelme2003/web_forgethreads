"""Lectura del log de new_job.sh (MaxMin) para graficar los efectos encontrados por orden.

Formato que escribe la extensión C++ (una línea por evento):

    [MAXMIN C++] M: 5000B: 1
    [MAXMIN C++] ORDER-100
    [MAXMIN C++] Efectos encontrados en orden 1 : 20000
    ...
    [MAXMIN C++] Convergencia en step 27
"""

import re
from dataclasses import dataclass, field

_RE_EFFECTS = re.compile(r"Efectos encontrados en orden\s+(\d+)\s*:\s*(\d+)")
_RE_CONVERGENCE = re.compile(r"Convergencia en step\s+(\d+)")
_RE_SIZE = re.compile(r"M:\s*(\d+)\s*B:\s*(\d+)")
_RE_ORDER_LIMIT = re.compile(r"ORDER-(\d+)")


@dataclass
class MaxMinLog:
    effects_by_order: list[tuple[int, int]] = field(default_factory=list)  # (orden, efectos)
    convergence_step: int | None = None
    m: int | None = None  # tamaño de la matriz
    b: int | None = None  # batch
    order_limit: int | None = None  # ORDER máximo con que se llamó a maxmin


def parse_maxmin_log(text: str | None) -> MaxMinLog:
    """Extrae los efectos por orden y la convergencia. Ignora líneas que no reconoce.

    Si un orden aparece repetido (por ejemplo, un log concatenado), se conserva el último.
    """
    result = MaxMinLog()
    by_order: dict[int, int] = {}
    for line in (text or "").splitlines():
        if m := _RE_EFFECTS.search(line):
            by_order[int(m.group(1))] = int(m.group(2))
        elif m := _RE_CONVERGENCE.search(line):
            result.convergence_step = int(m.group(1))
        elif m := _RE_SIZE.search(line):
            result.m, result.b = int(m.group(1)), int(m.group(2))
        elif m := _RE_ORDER_LIMIT.search(line):
            result.order_limit = int(m.group(1))
    result.effects_by_order = sorted(by_order.items())
    return result
