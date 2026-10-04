from app.schemas import OptimizeRequest
from app.service import run_optimize


def solve(**overrides):
    base = {
        "demands": [{"id": "A", "material": "M", "length": 100, "quantity": 2}],
        "new_stock": [
            {"id": "S1", "material": "M", "length": 1000, "price": 100, "available": 3}
        ],
        "remnants": [],
        "kerf_width": 5,
        "remnant_threshold": 50,
        "time_budget_ms": 5000,
    }
    base.update(overrides)
    return run_optimize(OptimizeRequest.model_validate(base))


def test_basic_optimal_and_conservation():
    res = solve()
    assert res.status == "optimal"
    assert res.metrics.bars_used == 1
    assert res.metrics.purchase_cost == 100
    bar = res.bars[0]
    assert sum(c.length for c in bar.cuts) + bar.kerf_loss + bar.remnant_length == 1000
    assert bar.kerf_count == 2  # 有余料, 每件都计锯缝


def test_exact_fit_skips_last_kerf():
    # 两件 500, 原料 1005: 500+5+500 = 1005 恰好用尽? 余料 0 -> 只计一次锯缝
    res = solve(
        demands=[{"id": "A", "material": "M", "length": 500, "quantity": 2}],
        new_stock=[{"id": "S1", "material": "M", "length": 1005, "price": 10, "available": 1}],
    )
    assert res.status == "optimal"
    bar = res.bars[0]
    assert bar.remnant_length == 0
    assert bar.kerf_count == 1
    assert bar.kerf_loss == 5


def test_remnant_preferred_over_new_stock():
    res = solve(
        remnants=[{"id": "R1", "material": "M", "length": 500}],
        demands=[{"id": "A", "material": "M", "length": 100, "quantity": 2}],
    )
    assert res.status == "optimal"
    assert res.metrics.purchase_cost == 0
    assert all(b.source == "remnant" for b in res.bars)


def test_material_isolation():
    res = solve(
        demands=[
            {"id": "A", "material": "M", "length": 100, "quantity": 1},
            {"id": "B", "material": "N", "length": 100, "quantity": 1},
        ],
        new_stock=[
            {"id": "S1", "material": "M", "length": 1000, "price": 10, "available": 1},
            {"id": "S2", "material": "N", "length": 1000, "price": 20, "available": 1},
        ],
    )
    assert res.status == "optimal"
    assert res.metrics.purchase_cost == 30


def test_missing_material_infeasible():
    res = solve(
        demands=[{"id": "A", "material": "X", "length": 100, "quantity": 1}],
    )
    assert res.status == "infeasible"
    assert res.bars == []


def test_too_short_stock_infeasible():
    res = solve(
        new_stock=[{"id": "S1", "material": "M", "length": 50, "price": 1, "available": 5}],
    )
    assert res.status == "infeasible"
    assert res.bars == []


def test_waste_excludes_reusable_remnant():
    # 原料 1000, 切 100 -> 余料 895 >= 阈值 50, 损耗仅锯缝 5
    res = solve(demands=[{"id": "A", "material": "M", "length": 100, "quantity": 1}])
    assert res.status == "optimal"
    assert res.metrics.waste_length == 5
    assert res.bars[0].remnant_reusable is True


def test_waste_includes_small_remnant():
    # 原料 130, 切 100 -> 余料 25 < 阈值 50, 损耗 = 5 + 25 = 30
    res = solve(
        demands=[{"id": "A", "material": "M", "length": 100, "quantity": 1}],
        new_stock=[{"id": "S1", "material": "M", "length": 130, "price": 1, "available": 1}],
    )
    assert res.status == "optimal"
    assert res.metrics.waste_length == 30
    assert res.bars[0].remnant_reusable is False


def test_lexicographic_cost_first():
    # 便宜短料两根(2*60=120) vs 贵长料一根(150): 应选两根便宜料, 尽管根数更多
    res = solve(
        demands=[{"id": "A", "material": "M", "length": 400, "quantity": 2}],
        new_stock=[
            {"id": "CHEAP", "material": "M", "length": 410, "price": 60, "available": 2},
            {"id": "EXP", "material": "M", "length": 1000, "price": 150, "available": 1},
        ],
        kerf_width=5,
    )
    assert res.status == "optimal"
    assert res.metrics.purchase_cost == 120
    assert res.metrics.bars_used == 2


def test_no_partial_output_on_timeout():
    res = solve(
        demands=[{"id": "A", "material": "M", "length": 100, "quantity": 20}],
        new_stock=[{"id": "S1", "material": "M", "length": 1000, "price": 1, "available": 20}],
        time_budget_ms=1,
    )
    if res.status != "optimal":
        assert res.status in ("feasible", "no_solution")
    if res.status == "no_solution":
        assert res.bars == []
        assert res.metrics is None
