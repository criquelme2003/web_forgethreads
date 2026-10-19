import pytest
from pydantic import ValidationError

from app.api.execute_maxmin import ExecuteMaxMinRequest
from app.services.slurm_submit import build_new_job_command

VALID_PAYLOAD = {
    "project_id": 1,
    "numero_nodos": 100,
    "threshold": 0.5,
    "conectividad_promedio": 10,
    "seed": 1,
}


def _payload(**overrides) -> dict:
    return {**VALID_PAYLOAD, **overrides}


def test_valid_payload_is_accepted() -> None:
    req = ExecuteMaxMinRequest.model_validate(_payload())
    assert req.numero_nodos == 100
    assert req.conectividad_promedio == 10


@pytest.mark.parametrize(
    "overrides",
    [
        {"numero_nodos": 4, "conectividad_promedio": 1},  # c = N/2 − 1
        {"numero_nodos": 10000, "conectividad_promedio": 4999},
        {"threshold": 0},
        {"threshold": 1},
        {"conectividad_promedio": 0.125},  # c decimal
        {"numero_nodos": 100, "conectividad_promedio": 49},
    ],
)
def test_boundary_values_are_accepted(overrides: dict) -> None:
    ExecuteMaxMinRequest.model_validate(_payload(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"numero_nodos": 0},
        {"numero_nodos": -2},
        {"numero_nodos": 10002},
        {"threshold": -0.01},
        {"threshold": 1.01},
        {"conectividad_promedio": 0},
        {"conectividad_promedio": -1},
        {"project_id": None},
    ],
)
def test_out_of_range_values_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        ExecuteMaxMinRequest.model_validate(_payload(**overrides))


def test_numero_nodos_debe_ser_par() -> None:
    with pytest.raises(ValidationError, match="numero_nodos debe ser par"):
        ExecuteMaxMinRequest.model_validate(_payload(numero_nodos=101, conectividad_promedio=1))


@pytest.mark.parametrize(
    "overrides",
    [
        {"numero_nodos": 100, "conectividad_promedio": 49.5},  # > N/2 − 1
        {"numero_nodos": 10, "conectividad_promedio": 9},  # la regla antigua (c < N) lo aceptaba
        {"numero_nodos": 2, "conectividad_promedio": 0.5},  # N/2 − 1 = 0
    ],
)
def test_conectividad_respeta_matrix_construction(overrides: dict) -> None:
    with pytest.raises(ValidationError, match=r"numero_nodos/2 − 1"):
        ExecuteMaxMinRequest.model_validate(_payload(**overrides))


@pytest.mark.parametrize(("c", "expected"), [(8, "--conectividad 8 "), (8.0, "--conectividad 8 "), (2.5, "--conectividad 2.5 ")])
def test_conectividad_en_el_comando(c: float, expected: str) -> None:
    cmd = build_new_job_command("scripts_wf", numero_nodos=100, threshold=0.5, conectividad_promedio=c, seed=1)
    assert expected in cmd
