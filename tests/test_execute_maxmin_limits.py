import pytest
from pydantic import ValidationError

from app.api.execute_maxmin import ExecuteMaxMinRequest

VALID_PAYLOAD = {
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
        {"numero_nodos": 1, "conectividad_promedio": 0},
        {"numero_nodos": 10000},
        {"threshold": 0},
        {"threshold": 1},
        {"conectividad_promedio": 0, "numero_nodos": 1},
        {"conectividad_promedio": 9999, "numero_nodos": 10000},
    ],
)
def test_boundary_values_are_accepted(overrides: dict) -> None:
    ExecuteMaxMinRequest.model_validate(_payload(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"numero_nodos": 0},
        {"numero_nodos": -1},
        {"numero_nodos": 10001},
        {"threshold": -0.01},
        {"threshold": 1.01},
        {"conectividad_promedio": -1},
        {"conectividad_promedio": 10001},
    ],
)
def test_out_of_range_values_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        ExecuteMaxMinRequest.model_validate(_payload(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"numero_nodos": 10, "conectividad_promedio": 10},  # igual
        {"numero_nodos": 10, "conectividad_promedio": 20},  # mayor
    ],
)
def test_conectividad_debe_ser_menor_que_numero_nodos(overrides: dict) -> None:
    with pytest.raises(ValidationError, match="conectividad_promedio debe ser menor que numero_nodos"):
        ExecuteMaxMinRequest.model_validate(_payload(**overrides))


def test_conectividad_menor_que_nodos_es_valida() -> None:
    req = ExecuteMaxMinRequest.model_validate(_payload(numero_nodos=10, conectividad_promedio=9))
    assert req.conectividad_promedio < req.numero_nodos
