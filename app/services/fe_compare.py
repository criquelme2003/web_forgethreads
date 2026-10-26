"""Comparación de los caminos de varios expertos (RF-26).

Une, por orden, los caminos de los cálculos elegidos: cada camino queda con el `Mean` de cada
experto (o nada, si ese experto no lo tiene) y el número de expertos que lo comparten.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class ComparedPath:
    path: list[str]  # From, Through…, To
    means: list[float | None]  # uno por cálculo, en el orden recibido
    shared: int  # expertos que tienen el camino


@dataclass
class ComparedOrder:
    order: int
    columns: list[str]  # nombres de las columnas de etiquetas (From, Through1…, To)
    paths: list[ComparedPath] = field(default_factory=list)


def _is_label(column: str) -> bool:
    return column in ("From", "To") or column.startswith("Through")


def compare_paths(
    tables_per_job: Sequence[Sequence[tuple[int, list[str], list[list]]]],
) -> list[ComparedOrder]:
    """`tables_per_job[i]`: tablas (orden, columnas, filas) del cálculo i.

    Los caminos de cada orden quedan ordenados por cuántos expertos los comparten y luego por
    el mayor `Mean`.
    """
    n = len(tables_per_job)
    by_order: dict[int, ComparedOrder] = {}
    index: dict[int, dict[tuple[str, ...], ComparedPath]] = {}
    for i, tables in enumerate(tables_per_job):
        for order, columns, rows in tables:
            label_idx = [j for j, c in enumerate(columns) if _is_label(c)]
            if "Mean" not in columns or not label_idx:
                continue
            mean_idx = columns.index("Mean")
            compared = by_order.setdefault(
                order, ComparedOrder(order=order, columns=[columns[j] for j in label_idx])
            )
            paths = index.setdefault(order, {})
            for row in rows:
                key = tuple(str(row[j]) for j in label_idx)
                item = paths.get(key)
                if item is None:
                    item = ComparedPath(path=list(key), means=[None] * n, shared=0)
                    paths[key] = item
                    compared.paths.append(item)
                if item.means[i] is None:
                    item.shared += 1
                try:
                    item.means[i] = float(row[mean_idx])
                except (TypeError, ValueError):
                    item.means[i] = None
                    item.shared -= 1

    for compared in by_order.values():
        compared.paths.sort(
            key=lambda p: (-p.shared, -max((m for m in p.means if m is not None), default=0.0))
        )
    return [by_order[o] for o in sorted(by_order)]
