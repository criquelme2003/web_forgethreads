"""Lectura y validación de las matrices CC, CE y EE que ingresa un experto (CSV con etiquetas).

Cada CSV lleva la primera fila y la primera columna con las etiquetas; la celda de la esquina se
ignora. Se acepta separador `,` (decimal `.`) o `;` (decimal `.` o `,`, como exporta Excel en
configuración chilena). Las etiquetas de CE y de las filas de CC/EE pueden venir en otro orden:
se reordenan según las columnas de CC (causas) y de EE (efectos).
"""

import csv
import io
import math
from dataclasses import dataclass, field

import numpy as np

MATRICES = ("CC", "CE", "EE")
MAX_LABELS = 200  # por lado (causas o efectos)


@dataclass
class CellError:
    matrix: str
    message: str
    row: int | None = None  # índice de fila de datos (0 = primera fila bajo el encabezado)
    col: int | None = None  # índice de columna de datos (0 = primera columna tras las etiquetas)


@dataclass
class ParsedCsv:
    corner: str = ""
    col_labels: list[str] = field(default_factory=list)
    row_labels: list[str] = field(default_factory=list)
    cells: list[list[str]] = field(default_factory=list)  # texto crudo, para la vista previa
    values: list[list[float | None]] = field(default_factory=list)


@dataclass
class FeInput:
    causes: list[str]
    effects: list[str]
    previews: dict[str, ParsedCsv]
    errors: list[CellError]
    warnings: list[str]
    cc: np.ndarray | None = None  # (m, m) float32
    ce: np.ndarray | None = None  # (m, n)
    ee: np.ndarray | None = None  # (n, n)

    @property
    def valid(self) -> bool:
        return not self.errors and self.cc is not None


def _parse_number(text: str, decimal_comma: bool) -> float | None:
    s = text.strip()
    if decimal_comma:
        s = s.replace(",", ".")
    try:
        value = float(s)
    except ValueError:
        return None
    return value


def parse_csv(name: str, text: str, errors: list[CellError]) -> ParsedCsv:
    """Parsea un CSV con etiquetas; agrega a `errors` todos los problemas por celda."""
    parsed = ParsedCsv()
    text = (text or "").lstrip("﻿")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        errors.append(CellError(name, "el archivo está vacío"))
        return parsed

    delimiter = ";" if ";" in lines[0] else ","
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)]
    header, body = rows[0], rows[1:]
    if len(header) < 2:
        errors.append(CellError(name, f"el encabezado no tiene etiquetas (separador detectado: '{delimiter}')"))
        return parsed

    parsed.corner = header[0]
    parsed.col_labels = header[1:]
    width = len(parsed.col_labels)
    for j, label in enumerate(parsed.col_labels):
        if not label:
            errors.append(CellError(name, "etiqueta de columna vacía", row=None, col=j))

    for i, row in enumerate(body):
        label = row[0] if row else ""
        cells = row[1:]
        parsed.row_labels.append(label)
        if not label:
            errors.append(CellError(name, "etiqueta de fila vacía", row=i, col=None))
        if len(cells) != width:
            errors.append(
                CellError(name, f"la fila tiene {len(cells)} valores y el encabezado {width} etiquetas", row=i)
            )
        cells = (cells + [""] * width)[:width]
        parsed.cells.append(cells)
        values: list[float | None] = []
        for j, cell in enumerate(cells):
            if cell == "":
                errors.append(CellError(name, "celda vacía", row=i, col=j))
                values.append(None)
                continue
            value = _parse_number(cell, decimal_comma=delimiter == ";")
            if value is None:
                errors.append(CellError(name, f"'{cell}' no es un número", row=i, col=j))
            elif not math.isfinite(value) or value < 0 or value > 1:
                errors.append(CellError(name, f"{cell} está fuera de [0,1]", row=i, col=j))
                value = None
            values.append(value)
        parsed.values.append(values)

    if not body:
        errors.append(CellError(name, "no hay filas de datos"))
    return parsed


def _duplicates(labels: list[str]) -> list[str]:
    seen, dup = set(), []
    for label in labels:
        if label in seen and label not in dup:
            dup.append(label)
        seen.add(label)
    return dup


def _check_axis(name: str, axis: str, labels: list[str], expected: list[str], errors: list[CellError]) -> bool:
    """Verifica que `labels` sea una permutación de `expected`."""
    dup = _duplicates(labels)
    if dup:
        errors.append(CellError(name, f"etiquetas de {axis} repetidas: {', '.join(dup)}"))
        return False
    missing = [label for label in expected if label not in labels]
    extra = [label for label in labels if label not in expected]
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"faltan {', '.join(missing)}")
        if extra:
            parts.append(f"sobran {', '.join(extra)}")
        errors.append(CellError(name, f"las etiquetas de {axis} no coinciden ({'; '.join(parts)})"))
        return False
    return True


def _reordered(parsed: ParsedCsv, rows: list[str], cols: list[str]) -> np.ndarray:
    row_idx = [parsed.row_labels.index(label) for label in rows]
    col_idx = [parsed.col_labels.index(label) for label in cols]
    return np.array(
        [[parsed.values[i][j] for j in col_idx] for i in row_idx],
        dtype=np.float32,
    )


def load_fe_input(cc_text: str, ce_text: str, ee_text: str) -> FeInput:
    """Parsea y valida las tres matrices; si son válidas, las devuelve ordenadas y como float32."""
    errors: list[CellError] = []
    warnings: list[str] = []
    previews = {
        "CC": parse_csv("CC", cc_text, errors),
        "CE": parse_csv("CE", ce_text, errors),
        "EE": parse_csv("EE", ee_text, errors),
    }
    cc, ce, ee = previews["CC"], previews["CE"], previews["EE"]
    causes, effects = cc.col_labels, ee.col_labels
    result = FeInput(causes=causes, effects=effects, previews=previews, errors=errors, warnings=warnings)

    for side, labels in (("causas", causes), ("efectos", effects)):
        if len(labels) > MAX_LABELS:
            errors.append(CellError("CC" if side == "causas" else "EE", f"hay {len(labels)} {side}; el máximo es {MAX_LABELS}"))
    overlap = [label for label in causes if label in effects]
    if overlap:
        errors.append(CellError("EE", f"etiquetas repetidas entre causas y efectos: {', '.join(overlap)}"))

    axes_ok = all(
        [
            _check_axis("CC", "columna", cc.col_labels, causes, errors),
            _check_axis("CC", "fila", cc.row_labels, causes, errors),
            _check_axis("CE", "fila", ce.row_labels, causes, errors),
            _check_axis("CE", "columna", ce.col_labels, effects, errors),
            _check_axis("EE", "columna", ee.col_labels, effects, errors),
            _check_axis("EE", "fila", ee.row_labels, effects, errors),
        ]
    )
    if errors or not axes_ok:
        return result

    for name, parsed in previews.items():
        if parsed.row_labels and (
            parsed.row_labels != (causes if name != "EE" else effects)
            or parsed.col_labels != (effects if name != "CC" else causes)
        ):
            warnings.append(f"{name}: las etiquetas venían en otro orden y se reordenaron")

    result.cc = _reordered(cc, causes, causes)
    result.ce = _reordered(ce, causes, effects)
    result.ee = _reordered(ee, effects, effects)
    # Reflexividad: la diagonal de CC y EE vale 1 (RF-18).
    for name, matrix in (("CC", result.cc), ("EE", result.ee)):
        if not np.all(np.diag(matrix) == 1):
            warnings.append(f"{name}: la diagonal se fijó en 1 (reflexividad)")
        np.fill_diagonal(matrix, 1)
    return result
