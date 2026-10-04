"""编排层: 按材质拆分、预算分配、结果汇总与长度守恒核算。"""

from __future__ import annotations

import time
from typing import Dict, List

from .schemas import (
    BarPlan,
    CutPiece,
    Metrics,
    OptimizeRequest,
    OptimizeResponse,
)
from .solver import (
    FEASIBLE,
    INFEASIBLE,
    NO_SOLUTION,
    OPTIMAL,
    Bar,
    Item,
    SolveResult,
    solve_material,
)

_STATUS_RANK = {OPTIMAL: 0, FEASIBLE: 1, NO_SOLUTION: 2, INFEASIBLE: 3}


def run_optimize(req: OptimizeRequest) -> OptimizeResponse:
    start = time.monotonic()
    deadline_ms = req.time_budget_ms

    # 展开成品实例与原料根数, 按材质分组
    items_by_mat: Dict[str, List[Item]] = {}
    for d in req.demands:
        for k in range(1, d.quantity + 1):
            items_by_mat.setdefault(d.material, []).append(Item(d.id, k, d.length))

    bars_by_mat: Dict[str, List[Bar]] = {}
    for s in req.new_stock:
        for k in range(1, s.available + 1):
            bars_by_mat.setdefault(s.material, []).append(
                Bar("new_stock", s.id, k, s.length, s.price)
            )
    for rmn in req.remnants:
        bars_by_mat.setdefault(rmn.material, []).append(
            Bar("remnant", rmn.id, 1, rmn.length, 0)
        )

    # 某材质需求无任何同材质原料 -> 直接证明不可行
    for mat, items in items_by_mat.items():
        if not bars_by_mat.get(mat):
            return OptimizeResponse(
                status=INFEASIBLE,
                message=f"材质 {mat!r} 无可用原料, 需求不可满足",
            )

    materials = sorted(items_by_mat)
    results: Dict[str, SolveResult] = {}
    for idx, mat in enumerate(materials):
        elapsed_ms = (time.monotonic() - start) * 1000.0
        remaining = deadline_ms - elapsed_ms
        if remaining <= 0:
            results[mat] = SolveResult(status=NO_SOLUTION, message="预算耗尽, 该材质未求解")
            continue
        # 剩余预算在尚未求解的材质间均分
        share = remaining / (len(materials) - idx)
        results[mat] = solve_material(
            items_by_mat[mat],
            bars_by_mat[mat],
            req.kerf_width,
            req.remnant_threshold,
            int(share),
        )

    # 任一材质不可行 -> 整体不可行(已证明)
    if any(r.status == INFEASIBLE for r in results.values()):
        bad = [m for m, r in results.items() if r.status == INFEASIBLE]
        return OptimizeResponse(
            status=INFEASIBLE,
            message=f"材质 {bad} 已证明无可行解, 不返回部分排料",
        )
    # 任一材质无完整解 -> 整体不返回部分排料
    if any(r.status == NO_SOLUTION for r in results.values()):
        return OptimizeResponse(
            status=NO_SOLUTION,
            message="预算耗尽, 尚未找到覆盖全部需求的完整解, 不返回部分排料",
        )

    status = OPTIMAL if all(r.status == OPTIMAL for r in results.values()) else FEASIBLE

    bars_out: List[BarPlan] = []
    cost = waste = used = 0
    for mat in materials:
        res = results[mat]
        cost += res.purchase_cost
        waste += res.waste_length
        used += res.bars_used
        for br in res.bars:
            cuts = [
                CutPiece(
                    demand_id=items_by_mat[mat][i].demand_id,
                    instance_no=items_by_mat[mat][i].instance_no,
                    length=items_by_mat[mat][i].length,
                )
                for i in br.item_indices
            ]
            plan = BarPlan(
                source=br.bar.source,
                stock_id=br.bar.stock_id,
                bar_index=br.bar.bar_index,
                bar_length=br.bar.length,
                cuts=cuts,
                kerf_count=br.kerf_count,
                kerf_loss=br.kerf_count * req.kerf_width,
                remnant_length=br.remnant_length,
                remnant_reusable=br.remnant_reusable,
            )
            _check_conservation(plan)
            bars_out.append(plan)

    return OptimizeResponse(
        status=status,
        metrics=Metrics(purchase_cost=cost, waste_length=waste, bars_used=used),
        bars=bars_out,
        message="; ".join(sorted({r.message for r in results.values()})),
    )


def _check_conservation(plan: BarPlan) -> None:
    """长度守恒: 成品总长 + 锯缝损耗 + 尾料 == 原料长度。"""
    total = sum(c.length for c in plan.cuts) + plan.kerf_loss + plan.remnant_length
    if total != plan.bar_length:
        raise RuntimeError(f"长度不守恒: {plan.stock_id}#{plan.bar_index}")
    if plan.remnant_length == 0 and plan.cuts:
        # 恰好用尽时最后一件不计锯缝: 锯缝次数 = 件数 - 1
        if plan.kerf_count != len(plan.cuts) - 1:
            raise RuntimeError(f"锯缝计数错误: {plan.stock_id}#{plan.bar_index}")
    elif plan.kerf_count != len(plan.cuts):
        raise RuntimeError(f"锯缝计数错误: {plan.stock_id}#{plan.bar_index}")
