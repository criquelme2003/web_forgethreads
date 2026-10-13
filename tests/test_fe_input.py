import json
from pathlib import Path

import numpy as np
import pytest

from app.services.fe_input import load_fe_input
from app.services.fe_remote import build_fe_input_files

CC = "x,a,b\na,1,0.2\nb,0.3,1\n"
CE = "x,e1,e2,e3\na,0.1,0.2,0.3\nb,0.4,0.5,0.6\n"
EE = "x,e1,e2,e3\ne1,1,0.1,0.2\ne2,0.3,1,0.4\ne3,0.5,0.6,1\n"


def _errors(result) -> list[str]:
    return [f"{e.matrix}: {e.message}" for e in result.errors]


def test_valid_input_is_parsed() -> None:
    result = load_fe_input(CC, CE, EE)

    assert result.valid, _errors(result)
    assert result.causes == ["a", "b"]
    assert result.effects == ["e1", "e2", "e3"]
    assert result.cc.shape == (2, 2) and result.ce.shape == (2, 3) and result.ee.shape == (3, 3)
    assert result.cc.dtype == np.float32
    assert result.ce[1, 2] == np.float32(0.6)
    assert result.warnings == []


def test_semicolon_and_decimal_comma_are_accepted() -> None:
    cc = "﻿;a;b\r\na;1;0,2\r\nb;0,3;1\r\n"

    result = load_fe_input(cc, CE, EE)

    assert result.valid, _errors(result)
    assert result.cc[0, 1] == np.float32(0.2)


def test_labels_in_other_order_are_reordered() -> None:
    ce = "x,e3,e1,e2\nb,0.6,0.4,0.5\na,0.3,0.1,0.2\n"

    result = load_fe_input(CC, ce, EE)

    assert result.valid, _errors(result)
    np.testing.assert_allclose(result.ce, [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], rtol=1e-6)
    assert any("reordenaron" in w for w in result.warnings)


def test_diagonal_is_forced_to_one() -> None:
    cc = "x,a,b\na,0.5,0.2\nb,0.3,0\n"

    result = load_fe_input(cc, CE, EE)

    assert result.valid
    assert list(np.diag(result.cc)) == [1, 1]
    assert any("diagonal" in w for w in result.warnings)


def test_cell_errors_are_reported_with_position() -> None:
    cc = "x,a,b\na,1,\nb,1.5,abc\n"

    result = load_fe_input(cc, CE, EE)

    assert not result.valid
    cells = {(e.row, e.col): e.message for e in result.errors if e.matrix == "CC"}
    assert cells[(0, 1)] == "celda vacía"
    assert "fuera de [0,1]" in cells[(1, 0)]
    assert "no es un número" in cells[(1, 1)]


def test_label_mismatch_is_reported() -> None:
    ce = "x,e1,e2,zz\na,0.1,0.2,0.3\nb,0.4,0.5,0.6\n"

    result = load_fe_input(CC, ce, EE)

    assert not result.valid
    assert any("faltan e3" in m and "sobran zz" in m for m in _errors(result))


def test_wrong_row_width_is_reported() -> None:
    ce = "x,e1,e2,e3\na,0.1,0.2\nb,0.4,0.5,0.6\n"

    result = load_fe_input(CC, ce, EE)

    assert not result.valid
    assert any("2 valores" in m for m in _errors(result))


def test_overlap_between_causes_and_effects_is_rejected() -> None:
    ce = "x,a,e2,e3\na,0.1,0.2,0.3\nb,0.4,0.5,0.6\n"
    ee = "x,a,e2,e3\na,1,0.1,0.2\ne2,0.3,1,0.4\ne3,0.5,0.6,1\n"

    result = load_fe_input(CC, ce, ee)

    assert not result.valid
    assert any("entre causas y efectos" in m for m in _errors(result))


def test_empty_file_is_rejected() -> None:
    result = load_fe_input("", CE, EE)

    assert not result.valid
    assert any(e.matrix == "CC" and "vacío" in e.message for e in result.errors)


def test_input_files_match_fe_job_contract(tmp_path) -> None:
    result = load_fe_input(CC, CE, EE)
    meta = {"causes": result.causes, "effects": result.effects, "thr": 0.5, "maxorder": 3, "reps": 1}

    files = build_fe_input_files(result.cc, result.ce, result.ee, meta)

    assert set(files) == {"CC.npy", "CE.npy", "EE.npy", "meta.json"}
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    assert np.load(tmp_path / "CC.npy").shape == (1, 2, 2)
    assert np.load(tmp_path / "CE.npy").shape == (1, 2, 3)
    assert np.load(tmp_path / "EE.npy").dtype == np.float32
    assert json.loads(files["meta.json"]) == meta


@pytest.mark.parametrize("folder", ["docs/ejemplos_fe", "docs/ejemplos_fe/excel_cl"])
def test_example_csvs_are_valid(folder: str) -> None:
    base = Path(__file__).resolve().parent.parent / folder
    texts = [(base / f"{name}.csv").read_text(encoding="utf-8") for name in ("CC", "CE", "EE")]

    result = load_fe_input(*texts)

    assert result.valid, _errors(result)
    assert (len(result.causes), len(result.effects)) == (4, 3)
