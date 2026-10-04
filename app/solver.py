"""单一材质下的整数规划排料求解(CP-SAT)。

建模要点:
- 每个成品实例必须且只能分配到一根原料上(成品不可拼接);
- 每根原料: 成品总长 + 锯缝总宽 <= 原料长度;
- 每取下一件计一次锯缝, 若最后一件恰好用尽原料(无余料)则该次锯缝不计;
- 尾料 >= 阈值可复用(不计损耗), 否则计入损耗; 损耗 = 锯缝损耗 + 不可复用尾料;
- 字典序三阶段优化: 采购总价 -> 损耗总长 -> 使用根数, 前项严格优先(以等值约束固定)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from ortools.sat.python import cp_model

# 求解状态
OPTIMAL = "optimal"          # 三阶段均证明最优
FEASIBLE = "feasible"        # 有完整可行解, 但预算耗尽未能证明最优
INFEASIBLE = "infeasible"    # 已证明无可行解
NO_SOLUTION = "no_solution"  # 预算耗尽且尚未找到任何完整可行解


@dataclass
class Item:
    demand_id: str
    instance_no: int
    length: int


@dataclass
class Bar:
    source: str        # "new_stock" | "remnant"
    stock_id: str
    bar_index: int
    length: int
    price: int         # 余料为 0


@dataclass
class BarResult:
    bar: Bar
    item_indices: List[int]
    kerf_count: int
    remnant_length: int
    remnant_reusable: bool


@dataclass
class SolveResult:
    status: str
    bars: List[BarResult] = field(default_factory=list)
    purchase_cost: int = 0
    waste_length: int = 0
    bars_used: int = 0
    message: str = ""


class _Budget:
    """跨阶段共享的毫秒预算。"""

    def __init__(self, budget_ms: int):
        self._deadline = time.monotonic() + budget_ms / 1000.0

    def remaining_ms(self) -> float:
        return max(0.0, (self._deadline - time.monotonic()) * 1000.0)

    def exhausted(self) -> bool:
        return self.remaining_ms() <= 0.0


def solve_material(
    items: List[Item],
    bars: List[Bar],
    kerf: int,
    threshold: int,
    budget_ms: int,
    num_workers: int = 1,
) -> SolveResult:
    """对单一材质求解。items 非空, bars 非空。"""
    budget = _Budget(budget_ms)
    n_items, n_bars = len(items), len(bars)
    max_len = max(b.length for b in bars)

    model = cp_model.CpModel()
    x = {}
    for b in range(n_bars):
        for i in range(n_items):
            if items[i].length <= bars[b].length:
                x[b, i] = model.new_bool_var(f"x_{b}_{i}")

    # 每个成品实例恰好分配一次(不可拼接、不可重复利用)
    for i in range(n_items):
        model.add_exactly_one(x[b, i] for b in range(n_bars) if (b, i) in x)

    used = [model.new_bool_var(f"used_{b}") for b in range(n_bars)]
    exact = [model.new_bool_var(f"exact_{b}") for b in range(n_bars)]
    small = [model.new_bool_var(f"small_{b}") for b in range(n_bars)]
    load, count, kerf_count, rem, waste_rem, waste_len = [], [], [], [], [], []

    for b in range(n_bars):
        L = bars[b].length
        assigned = [x[b, i] for i in range(n_items) if (b, i) in x]
        n_b = model.new_int_var(0, n_items, f"n_{b}")
        model.add(n_b == sum(assigned))
        load_b = model.new_int_var(0, L, f"load_{b}")
        model.add(load_b == sum(items[i].length * x[b, i] for i in range(n_items) if (b, i) in x))
        # used <=> 至少分配一件
        model.add(n_b >= 1).only_enforce_if(used[b])
        model.add(n_b == 0).only_enforce_if(used[b].negated())
        # exact <=> 少计一次锯缝时恰好用尽原料(最后一件不计锯缝)
        model.add(load_b + (n_b - 1) * kerf == L).only_enforce_if(exact[b])
        model.add(load_b + (n_b - 1) * kerf != L).only_enforce_if(exact[b].negated())
        kc = model.new_int_var(0, n_items, f"kerf_{b}")
        model.add(kc == n_b - exact[b])
        # 容量: 成品总长 + 锯缝 <= 原料长度
        model.add(load_b + kc * kerf <= L)
        rem_b = model.new_int_var(0, L, f"rem_{b}")
        model.add(rem_b == L - load_b - kc * kerf)
        # small <=> 尾料 < 阈值(不可复用)
        model.add(rem_b <= threshold - 1).only_enforce_if(small[b])
        model.add(rem_b >= threshold).only_enforce_if(small[b].negated())
        # waste_rem = rem(仅当已使用且尾料不可复用)
        wr = model.new_int_var(0, L, f"wrem_{b}")
        model.add(wr <= rem_b)
        model.add(wr <= L * small[b])
        model.add(wr <= L * used[b])
        model.add(wr >= rem_b - L * (2 - small[b] - used[b]))
        wl = model.new_int_var(0, L + n_items * kerf, f"wlen_{b}")
        model.add(wl == kc * kerf + wr)
        load.append(load_b)
        count.append(n_b)
        kerf_count.append(kc)
        rem.append(rem_b)
        waste_rem.append(wr)
        waste_len.append(wl)

    total_cost = sum(bars[b].price * used[b] for b in range(n_bars) if bars[b].price > 0)
    total_waste = sum(waste_len)
    total_bars = sum(used)

    best: Optional[dict] = None

    def run_phase(objective, phase_name: str) -> Optional[int]:
        """返回该阶段最优/最优已知目标值; 无解或超时返回 None 并设置状态。"""
        nonlocal best
        model.clear_objective()
        if objective is not None:
            model.minimize(objective)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = budget.remaining_ms() / 1000.0
        solver.parameters.num_workers = num_workers
        status = solver.solve(model)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            best = {
                "assign": {
                    (b, i): solver.value(v) for (b, i), v in x.items() if solver.value(v)
                },
                "used": [solver.value(u) for u in used],
                "kerf_count": [solver.value(kc) for kc in kerf_count],
                "rem": [solver.value(r) for r in rem],
                "cost": int(solver.value(total_cost)),
                "waste": int(solver.value(total_waste)),
                "bars": int(solver.value(total_bars)),
            }
            return int(solver.objective_value) if objective is not None else 0
        if status == cp_model.INFEASIBLE:
            return -1  # 已证明不可行
        return None  # 超时未找到解

    # 阶段 1: 最小化采购总价
    r1 = run_phase(total_cost, "cost")
    if r1 == -1:
        return SolveResult(status=INFEASIBLE, message="已证明现有原料无法满足全部需求")
    if r1 is None:
        return SolveResult(status=NO_SOLUTION, message="预算耗尽, 尚未找到完整可行解")
    model.add(total_cost <= r1)

    # 阶段 2: 在采购价不劣化下最小化损耗总长
    if budget.exhausted():
        return _finalize(best, FEASIBLE, items, bars, threshold, "预算耗尽, 返回当前最优可行解")
    r2 = run_phase(total_waste, "waste")
    if r2 is not None and r2 != -1:
        model.add(total_waste <= r2)

    # 阶段 3: 在前两项不劣化下最小化使用根数
    if budget.exhausted():
        return _finalize(best, FEASIBLE, items, bars, threshold, "预算耗尽, 返回当前最优可行解")
    r3 = run_phase(total_bars, "bars")

    if r3 is None and r2 is None:
        # 阶段 2、3 均无改进信息, 但阶段 1 已有完整解
        return _finalize(best, FEASIBLE, items, bars, threshold, "预算耗尽, 返回当前最优可行解")
    if r3 is None or r2 is None:
        return _finalize(best, FEASIBLE, items, bars, threshold, "预算耗尽, 返回当前最优可行解")
    return _finalize(best, OPTIMAL, items, bars, threshold, "已证明字典序最优")


def _finalize(
    best: dict,
    status: str,
    items: List[Item],
    bars: List[Bar],
    threshold: int,
    message: str,
) -> SolveResult:
    results: List[BarResult] = []
    for b, bar in enumerate(bars):
        if not best["used"][b]:
            continue
        idx = [i for (bb, i) in best["assign"] if bb == b]
        # 切割顺序: 长件优先, 便于现场下料
        idx.sort(key=lambda i: (-items[i].length, items[i].demand_id, items[i].instance_no))
        remnant = best["rem"][b]
        results.append(
            BarResult(
                bar=bar,
                item_indices=idx,
                kerf_count=best["kerf_count"][b],
                remnant_length=remnant,
                remnant_reusable=remnant >= threshold,
            )
        )
    return SolveResult(
        status=status,
        bars=results,
        purchase_cost=best["cost"],
        waste_length=best["waste"],
        bars_used=best["bars"],
        message=message,
    )
