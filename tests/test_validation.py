import pytest
from pydantic import ValidationError

from app.schemas import OptimizeRequest

BASE = {
    "demands": [{"id": "A", "material": "M", "length": 100, "quantity": 1}],
    "new_stock": [
        {"id": "S1", "material": "M", "length": 1000, "price": 10, "available": 1}
    ],
    "remnants": [],
    "kerf_width": 3,
    "remnant_threshold": 50,
    "time_budget_ms": 1000,
}


def make(**overrides):
    payload = {**BASE, **overrides}
    return OptimizeRequest.model_validate(payload)


def test_ok():
    req = make()
    assert req.demands[0].length == 100


@pytest.mark.parametrize("field,value", [
    ("length", True), ("length", -5), ("length", 0), ("length", 1.5), ("length", "100"),
    ("quantity", False), ("quantity", 0), ("quantity", -1),
])
def test_demand_rejects_bad_numbers(field, value):
    d = {"id": "A", "material": "M", "length": 100, "quantity": 1, field: value}
    with pytest.raises(ValidationError):
        make(demands=[d])


@pytest.mark.parametrize("field,value", [
    ("price", True), ("price", -1), ("available", 0), ("available", -2), ("length", 0),
])
def test_stock_rejects_bad_numbers(field, value):
    s = {"id": "S1", "material": "M", "length": 1000, "price": 10, "available": 1, field: value}
    with pytest.raises(ValidationError):
        make(new_stock=[s])


def test_duplicate_demand_id():
    ds = [
        {"id": "A", "material": "M", "length": 100, "quantity": 1},
        {"id": "A", "material": "M", "length": 200, "quantity": 1},
    ]
    with pytest.raises(ValidationError, match="duplicate demand id"):
        make(demands=ds)


def test_duplicate_stock_id_across_new_and_remnant():
    with pytest.raises(ValidationError, match="duplicate stock"):
        make(remnants=[{"id": "S1", "material": "M", "length": 300}])


def test_item_limit():
    with pytest.raises(ValidationError, match="too many items"):
        make(demands=[{"id": "A", "material": "M", "length": 10, "quantity": 61}])


def test_bar_limit():
    stock = [{"id": "S1", "material": "M", "length": 1000, "price": 1, "available": 24}]
    with pytest.raises(ValidationError, match="too many stock bars"):
        make(new_stock=stock, remnants=[{"id": "R", "material": "M", "length": 100}])


def test_no_stock_rejected():
    with pytest.raises(ValidationError, match="no stock"):
        make(new_stock=[])


def test_bool_kerf_rejected():
    with pytest.raises(ValidationError):
        make(kerf_width=True)
