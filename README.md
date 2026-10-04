# 型材定尺裁切排料后端

基于 FastAPI + OR-Tools CP-SAT 的整数优化排料服务(无前端)。给定成品需求、新料规格与现有余料, 在毫秒级预算内求解字典序最优下料方案。

## 运行

```bash
.venv/bin/python -m uvicorn app.main:app --port 8154
```

环境变量 `MAX_CONCURRENT_SOLVES`(默认 2) 限制并发求解数, 超限时新请求立即返回 503。

## 接口

### GET /health
恒返回 `{"status": "ok"}`。求解在线程池中执行, 不阻塞事件循环, 健康检查始终可用。

### POST /optimize
请求体(长度均为整数毫米, 价格为整数分):

| 字段 | 说明 |
|---|---|
| `demands[]` | `id, material, length, quantity` 成品需求 |
| `new_stock[]` | `id, material, length, price, available` 新料规格 |
| `remnants[]` | `id, material, length` 现有余料(不计采购价) |
| `kerf_width` | 锯缝宽度 |
| `remnant_threshold` | 尾料可复用阈值 |
| `time_budget_ms` | 总求解预算(毫秒), 覆盖全部优化阶段 |

响应 `status`:
- `optimal`: 三阶段均证明最优;
- `feasible`: 有覆盖全部需求的完整解, 但预算耗尽未能证明最优;
- `infeasible`: 已证明无可行解;
- `no_solution`: 预算耗尽且尚未找到完整解。

`optimal`/`feasible` 时返回每根原料的 `source/stock_id/bar_index`、按顺序的 `cuts`(成品 ID + 实例号)、`kerf_count/kerf_loss`、`remnant_length/remnant_reusable`, 以及三项指标 `purchase_cost / waste_length / bars_used`。无完整解时不返回任何部分排料(`bars` 为空)。

## 规则与边界

- 成品不可拼接, 仅使用同材质原料; 每个成品实例恰好分配一次, 余料不会被重复利用。
- 锯缝: 每取下一件计一次; 最后一件恰好用尽原料(尾料为 0)时该次不计。
- 损耗 = 锯缝损耗 + 小于阈值的尾料; 达到阈值的尾料可复用, 不计损耗。
- 优化目标字典序: 新料采购总价 → 不可复用损耗总长 → 使用原料根数, 前项严格优先(以前序目标等值约束固化后再优化下一项), 非贪心启发式。
- 预算按剩余时间分配给各优化阶段与各材质子问题; 阶段 1 已证明不可行才报 `infeasible`, 阶段全部证明最优才报 `optimal`。
- 长度守恒: 每根 `Σ成品长 + 锯缝损耗 + 尾料 == 原料长`, 服务端返回前逐根核算。

## 输入校验(违反返回 422)

- 整数字段严格校验: 拒绝布尔、浮点、字符串数字与负值; 长度/数量/根数为正, 价格非负。
- 需求 ID 不得重复; 新料与余料 ID 合并后不得重复。
- 成品实例总数 ≤ 60, 原料总根数(新料可用根数之和 + 余料根数) ≤ 24。
- 至少存在一根原料; 某材质需求无同材质原料时直接判定 `infeasible`。

## 并发与隔离

请求间无共享状态, 每个请求独立建模求解; 信号量限制并发, 失败或超时均释放容量。

## 代码结构

- `app/schemas.py` 请求/响应模式与校验
- `app/solver.py` 单材质 CP-SAT 建模与三阶段字典序优化
- `app/service.py` 材质拆分、预算分配、结果汇总与守恒核算
- `app/main.py` FastAPI 入口、并发限制、健康检查
- `tests/` 校验、求解规则、API 共 34 项测试
- `examples/sample_request.json` 样例请求

## 测试与演示

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app tests
curl -s -X POST http://127.0.0.1:8154/optimize \
  -H 'Content-Type: application/json' \
  -d @examples/sample_request.json | python3 -m json.tool
```
