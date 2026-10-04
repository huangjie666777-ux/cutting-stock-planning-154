"""请求/响应模式与输入校验。

校验规则:
- 所有整数字段严格校验, 拒绝布尔值与非整型;
- 长度为正整数毫米, 价格为非负整数分, 数量/根数为正整数;
- 需求 ID、新料 ID、余料 ID 各自及原料整体不得重复;
- 成品实例总数 <= 60, 原料总根数 <= 24。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

MAX_ITEMS = 60
MAX_BARS = 24


class Demand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    material: str = Field(min_length=1)
    length: StrictInt = Field(gt=0)
    quantity: StrictInt = Field(ge=1)


class NewStock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    material: str = Field(min_length=1)
    length: StrictInt = Field(gt=0)
    price: StrictInt = Field(ge=0)
    available: StrictInt = Field(ge=1)


class Remnant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    material: str = Field(min_length=1)
    length: StrictInt = Field(gt=0)


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demands: List[Demand] = Field(min_length=1)
    new_stock: List[NewStock] = Field(default_factory=list)
    remnants: List[Remnant] = Field(default_factory=list)
    kerf_width: StrictInt = Field(ge=0)
    remnant_threshold: StrictInt = Field(ge=0)
    time_budget_ms: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def check_ids_and_limits(self) -> "OptimizeRequest":
        demand_ids = [d.id for d in self.demands]
        if len(demand_ids) != len(set(demand_ids)):
            raise ValueError("duplicate demand id")
        stock_ids = [s.id for s in self.new_stock] + [r.id for r in self.remnants]
        if len(stock_ids) != len(set(stock_ids)):
            raise ValueError("duplicate stock/remnant id")
        total_items = sum(d.quantity for d in self.demands)
        if total_items > MAX_ITEMS:
            raise ValueError(f"too many items: {total_items} > {MAX_ITEMS}")
        total_bars = sum(s.available for s in self.new_stock) + len(self.remnants)
        if total_bars > MAX_BARS:
            raise ValueError(f"too many stock bars: {total_bars} > {MAX_BARS}")
        if total_bars == 0:
            raise ValueError("no stock available")
        return self


class CutPiece(BaseModel):
    demand_id: str
    instance_no: int
    length: int


class BarPlan(BaseModel):
    source: str  # "new_stock" | "remnant"
    stock_id: str
    bar_index: int  # 同规格新料的第几根(1 起); 余料恒为 1
    bar_length: int
    cuts: List[CutPiece]
    kerf_count: int
    kerf_loss: int
    remnant_length: int
    remnant_reusable: bool


class Metrics(BaseModel):
    purchase_cost: int
    waste_length: int
    bars_used: int


class OptimizeResponse(BaseModel):
    status: str  # "optimal" | "feasible" | "infeasible" | "no_solution"
    metrics: Metrics | None = None
    bars: List[BarPlan] = []
    message: str = ""
