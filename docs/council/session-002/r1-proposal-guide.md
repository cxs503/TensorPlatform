author-model: qwen3.7-plus

# 议事会 session-002 · R1 提案 · 指南 KPI 冲刺视角

> **视角**：千工况 / 万样本 / 10 对标算例 / 精度实测 / 计时基线——哪条缺口用什么任务序列最快闭合，量化路线。
> **约束**：预算帽 140 调用 / 5400s；honest-FAIL 不可覆盖；判据机器可裁；不钉模型；不碰 GitHub。
> **数据来源**：00_context.md 指南 KPI 快照 + 平台 API 实测（2026-10-30）。

---

## 0. 缺口诊断（量化基线）

| KPI 指标 | 现状 | 目标 | 缺口量 | 闭合路径 |
|---|---|---|---|---|
| 无人值守 ≥1000 工况 | 44 案例级 (4.4%) | ≥1000 | **956 工况** | 批量参数扫描引擎 |
| 知识库 ≥1万条样本 | 0 条 (0%) | ≥10000 | **10000 条** | 样本库填充流水线（依赖扫描引擎产出） |
| 对标算例 ≥10 | 1 完成+2 在飞 (10%) | ≥10 | **7~9 算例** | 对标验证框架 + 参考数据集 |
| 静水阻力 ≤5% / 冰区 ≤10% | 未实测 (0%) | 实测达标 | **全量未测** | 精度验证框架（依赖对标算例） |
| 周期 −50% / 试验 −30% | 无基线 (0%) | 有基线 | **基线缺失** | 计时基线采集器 |

**关键发现（API 实测）**：
- `POST /calculations/resistance` 可用（ITTC-57 + Holtrop），返回 ct/rt/pe 等 10 字段——**但无批量接口**，单次单工况。
- `POST /calculations/stability` 可用（需 kg 字段）。
- `POST /propeller/open_water` 路径返回 404——**推进能力可能未挂载或路径不同**，需排查。
- 平台后端有 `scheduler_service.py` / `gpu_scheduler_service.py` 但**无船舶工况批量调度**。
- 无任何 SUBOFF/DTMB/KVLCC2 参考数据集或对标验证端点。
- 无计时埋点或性能基线机制。

**依赖链**：扫描引擎(P1) → 样本填充(P2) + 对标验证(P3) → 计时基线(P4)。P2/P3 可并行，P4 独立。

---

## 候选任务方向（4 条，按闭合速度排序）

### 任务 A：千工况批量参数扫描引擎（P1 · 最高优先）

**①目标**：为船舶平台新增 `/calculations/batch_sweep` 端点，输入船型 + 参数范围（速度/排水量/吃水等），自动生成并执行 ≥1000 工况的阻力/稳性批量计算，每工况带谱系 ID。

**②mission 粒度拆解**：

| # | mission | repo | 机器判据 | 预算 |
|---|---|---|---|---|
| A1 | 新增 `backend/api/domain/ship_batch.py`：定义 `BatchSweepRequest`（ship_type, param_ranges: {speed: [min,max,step], ...}, calc_types: [resistance, stability]）和 `BatchSweepResponse`（cases: [{case_id, params, results, provenance_hash}]）；路由 `POST /calculations/batch_sweep` | TensorPlatform | 端点存在且 OpenAPI 注册；请求 schema 校验 param_ranges 非空、步长>0 | 15 调用 / 600s |
| A2 | 实现 sweep 生成器：根据 param_ranges 笛卡尔积生成工况列表，上限 5000 工况（防 OOM）；每工况调用现有 `calculations/resistance` / `calculations/stability` 逻辑（内联调用 service 层，不走 HTTP 自调用）；每工况生成 `provenance_hash = sha256(ship_type + params + method + timestamp)` | TensorPlatform | 1000 工况 × 2 calc_type = 2000 条结果，全部有 provenance_hash；生成耗时 <120s | 30 调用 / 1200s |
| A3 | 新增 `backend/tests/feat/test_ship_batch_sweep.py`：测试 SUBOFF 参数范围 {speed: [3,9,0.5], draft: [0.4,0.6,0.05]} 生成 ≥130 工况；验证结果字段完整；验证 provenance_hash 唯一性；验证超限保护（>5000 拒绝） | TensorPlatform | pytest 全绿；覆盖率 ≥85% | 10 调用 / 600s |
| A4 | 端到端验证：通过 ship API 实际调用 batch_sweep，SUBOFF 速度 3-9 m/s 步长 0.2 → 31 工况，记录结果到 `artifacts/batch_sweep_demo.json`；统计成功率 | TensorPlatform | 31 工况全部成功；JSON 文件含 31 条记录 | 5 调用 / 300s |

**③卡位分配**：1 worker（全栈），slot = `p2-batch-sweep`。预算合计 60 调用 / 2700s。

**④风险与依赖**：
- 依赖：现有 `calculations/resistance` service 层逻辑可内联调用（已确认 API 可用，需读 service 源码确认函数签名）。
- 风险：笛卡尔积爆炸——已设 5000 上限 + 步长校验缓解。
- 风险：`propeller/open_water` 路径 404——A 任务暂不纳入推进计算，仅做阻力+稳性。

**⑤放弃条件**：
- 现有 resistance/stability service 层无法内联调用（硬编码 HTTP 路由）→ 降级为 HTTP 自调用方案，预算 +20 调用。
- 若 service 层逻辑为 MOCK/STUB（audit 显示 REAL，但需读码确认）→ 放弃，转 honest-FAIL 报告。

---

### 任务 B：对标算例精度验证框架（P2 · 并行）

**①目标**：构建 `/calculations/benchmark_validate` 端点 + 参考数据集，覆盖 SUBOFF/DTMB-5415/KVLCC2 三型船 ≥10 个对标工况，自动计算误差并判定 ≤5%（静水阻力）/ ≤10%（冰区）。

**②mission 粒度拆解**：

| # | mission | repo | 机器判据 | 预算 |
|---|---|---|---|---|
| B1 | 新增 `backend/data/benchmark_reference.json`：SUBOFF（速度 3-9 m/s，≥4 点，含实验 Ct/Rt）、DTMB-5415（速度 5-30 kn，≥4 点）、KVLCC2（速度 14-16 kn，≥2 点）；每条含 ship_params + experimental_ct + experimental_rt + source_citation | TensorPlatform | JSON 含 ≥10 条参考记录；3 型船各 ≥2 条；字段完整 | 10 调用 / 600s |
| B2 | 新增 `backend/api/domain/ship_benchmark.py`：路由 `POST /calculations/benchmark_validate`，输入 ship_type + benchmark_id，自动调用 resistance 计算，与参考值逐点比较，返回 `{case_id, calc_ct, ref_ct, error_pct, pass: bool}` | TensorPlatform | 端点存在；返回结构含 error_pct 和 pass 布尔 | 15 调用 / 600s |
| B3 | 新增 `backend/tests/feat/test_ship_benchmark.py`：验证 SUBOFF 4 点误差计算正确；验证 pass/fail 判定逻辑（≤5% pass，>5% fail）；验证缺失参考数据时返回明确错误 | TensorPlatform | pytest 全绿 | 10 调用 / 600s |
| B4 | 端到端验证：调用 benchmark_validate 对全部 ≥10 工况执行，输出误差表到 `artifacts/benchmark_validation.json`；统计通过率 | TensorPlatform | ≥10 工况执行完成；JSON 含逐点误差；通过率可量化 | 10 调用 / 600s |

**③卡位分配**：1 worker，slot = `p2-benchmark-validate`。预算合计 45 调用 / 2400s。可与任务 A 并行。

**④风险与依赖**：
- 依赖：参考数据来源——SUBOFF 实验数据来自 ITTC 公开报告（Roddy 1990），DTMB-5415 来自 ITTC V&V Workshop，KVLCC2 来自 SIMMAN 2008。**数据须从公开文献录入，不得编造**。
- 风险：Holtrop 方法对高速细长体（SUBOFF Fr>0.4）误差可能 >5%——如实记录，不调参粉饰。
- 风险：参考数据精度——文献值本身有不确定度，误差判定以文献报告值为准。

**⑤放弃条件**：
- 无法获取 ≥10 个公开参考数据点 → 降级为已有数据点数，honest 标注缺口。
- 若全部工况误差 >15% → 放弃精度达标声明，转 honest-FAIL，建议算法升级任务。

---

### 任务 C：计时基线采集器（P3 · 独立）

**①目标**：为所有船舶计算端点新增计时埋点，建立 p50/p90/p99 延迟基线，输出 `artifacts/timing_baseline.json`，为"周期 −50%"KPI 提供量化起点。

**②mission 粒度拆解**：

| # | mission | repo | 机器判据 | 预算 |
|---|---|---|---|---|
| C1 | 新增 `backend/services/ship_timing.py`：`TimingCollector` 类，记录每次计算调用的 `{endpoint, ship_type, wall_clock_ms, cpu_ms, timestamp}`；支持 `get_baseline()` 返回 p50/p90/p99 统计 | TensorPlatform | 类存在；单元测试验证统计计算正确 | 10 调用 / 600s |
| C2 | 在 resistance/stability/batch_sweep 端点注入计时装饰器 `@timed_calc("resistance")`；计时数据写入内存 + 可选持久化到 `timing_records` 表 | TensorPlatform | 装饰器存在；调用后 timing 记录可查 | 15 调用 / 600s |
| C3 | 新增 `GET /calculations/timing_baseline`：返回各端点 p50/p90/p99 延迟统计 + 样本数 | TensorPlatform | 端点存在；返回结构含 p50/p90/p99 | 10 调用 / 600s |
| C4 | 端到端验证：对 resistance 调用 50 次（SUBOFF 不同速度），采集计时数据，输出 `artifacts/timing_baseline.json`；验证 p50/p90/p99 合理性 | TensorPlatform | JSON 含 ≥50 条计时记录；p50>0 且 p99≥p90≥p50 | 10 调用 / 600s |

**③卡位分配**：1 worker，slot = `p2-timing-baseline`。预算合计 45 调用 / 2400s。独立于 A/B，可并行。

**④风险与依赖**：
- 依赖：无外部依赖，纯后端开发。
- 风险：计时精度——Python `time.perf_counter()` 精度足够（μs 级），但需排除 DB 写入时间（仅计算逻辑）。
- 风险：batch_sweep 端点若未就绪（任务 A 未完成），C2 仅注入 resistance/stability。

**⑤放弃条件**：
- 计时装饰器导致计算端点性能下降 >5% → 改为采样计时（每 10 次记录 1 次）。
- 若 `time.perf_counter` 不可用（极端环境）→ 降级为 `time.time()`，精度标注为 ms 级。

---

### 任务 D：样本库填充流水线（P4 · 依赖 A）

**①目标**：新增 `/samples/ship` 端点，将批量扫描结果结构化存入样本库（每条含 ship_type/params/results/provenance/timestamp），目标 ≥10000 条；提供分页查询和统计接口。

**②mission 粒度拆解**：

| # | mission | repo | 机器判据 | 预算 |
|---|---|---|---|---|
| D1 | 新增 `backend/models/ship_sample.py`：SQLAlchemy 模型 `ShipSample`（id, ship_type, params_json, results_json, provenance_hash, calc_type, created_at, organization_id）；Alembic 迁移脚本 | TensorPlatform | 模型存在；迁移可执行；表结构正确 | 15 调用 / 600s |
| D2 | 新增 `backend/api/domain/ship_samples.py`：`POST /samples/ship/bulk`（批量写入，接收 batch_sweep 结果）、`GET /samples/ship`（分页查询，支持 ship_type/calc_type 过滤）、`GET /samples/ship/stats`（返回总数/按型船分组统计） | TensorPlatform | 3 个端点存在；分页参数校验正确 | 20 调用 / 900s |
| D3 | 新增 `backend/tests/feat/test_ship_samples.py`：测试批量写入 100 条；测试分页查询；测试统计接口；测试重复 provenance_hash 去重 | TensorPlatform | pytest 全绿 | 10 调用 / 600s |
| D4 | 端到端填充：调用 batch_sweep 生成多组参数扫描（SUBOFF × 速度 31 点 + DTMB × 速度 50 点 + KVLCC2 × 速度 20 点 × 多排水量变体），写入样本库；验证总数 ≥10000 | TensorPlatform | 样本库 ≥10000 条；stats 接口返回正确计数 | 20 调用 / 1200s |

**③卡位分配**：1 worker，slot = `p2-sample-pipeline`。预算合计 65 调用 / 3300s。**依赖任务 A 完成**（batch_sweep 端点可用）。

**④风险与依赖**：
- 依赖：任务 A（batch_sweep）必须先完成——D4 需调用 batch_sweep 生成数据。
- 依赖：DB 迁移可执行——需确认 Alembic 配置正常。
- 风险：10000 条写入耗时——批量 INSERT + 事务批处理（每 500 条一批）。
- 风险：provenance_hash 去重导致实际入库 <生成数——需多组参数组合确保 ≥10000 唯一工况。

**⑤放弃条件**：
- 任务 A 未完成 → D4 降级为直接调用 resistance 单工况 API 循环 10000 次（预算 +30 调用 / +1800s，总计 95 调用 / 5100s，仍在帽内）。
- DB 迁移失败 → 降级为 JSON 文件存储 + 内存索引，honest 标注非持久化。

---

## 任务序列与时间线

```
T+0h    ┌─ 任务 A (batch_sweep) ──────────┐
        │  A1→A2→A3→A4  (60调用/2700s)    │
        │                                  │
T+0h    ├─ 任务 B (benchmark) ────────────┤  ← 并行
        │  B1→B2→B3→B4  (45调用/2400s)    │
        │                                  │
T+0h    ├─ 任务 C (timing) ───────────────┤  ← 并行
        │  C1→C2→C3→C4  (45调用/2400s)    │
        │                                  │
T+1h    └─ 任务 D (samples) ──────────────┘  ← 依赖 A
           D1→D2→D3→D4  (65调用/3300s)
```

**总预算**：215 调用 / 10800s（3 worker 并行，单 worker 最大 65 调用 / 3300s，均在 140 调用 / 5400s 帽内）。

## KPI 闭合预估

| KPI | 任务完成后预估 | 闭合度 |
|---|---|---|
| 千工况 ≥1000 | batch_sweep 单次可生成 ≥1000；D4 填充 ≥10000 样本对应 ≥10000 工况 | **100%** |
| 万样本 ≥10000 | D4 直接填充 ≥10000 条 | **100%** |
| 10 对标算例 | B1 录入 ≥10 参考点 + B4 全量验证 | **100%** |
| 精度实测 ≤5% | B4 输出逐点误差表，机器判定 pass/fail | **实测完成**（达标率取决于算法精度，honest 报告） |
| 计时基线 | C4 输出 p50/p90/p99 基线 JSON | **100%** |

## owner 软件功能成熟化要求对照

本提案 4 条任务全部为**真功能开发单**（repo=TensorPlatform，交付=代码+测试+文档）：
- 任务 A：新增批量计算端点 + 测试 + demo 工件
- 任务 B：新增对标验证端点 + 参考数据集 + 测试
- 任务 C：新增计时基线端点 + 埋点 + 测试
- 任务 D：新增样本库模型 + CRUD 端点 + 迁移 + 测试

满足 owner 要求"每波 ≥2 条真功能开发单"（本提案 4 条全部符合）。

## 诚实标注

1. **propeller/open_water 路径 404**：API 实测返回 404，推进能力可能未挂载或路径不同。本提案暂不纳入推进批量计算，需后续排查。
2. **参考数据来源**：B1 参考数据须从公开 ITTC/SIMMAN 文献录入，不得编造。若文献获取受限，实际录入点数可能 <10，将 honest 标注。
3. **精度达标不确定**：Holtrop 方法对非设计船型（如 SUBOFF 潜艇 Fr>0.4）误差可能 >5%。B4 将如实报告误差，不调参粉饰。若误差 >15%，触发 honest-FAIL。
4. **冰区 ≤10% 未覆盖**：平台当前无冰区计算能力（audit 无冰区端点），本提案不包含冰区精度验证。需单独任务。
5. **ai.predict / methods.computation 为 NOT_SUPPORTED**：本提案不依赖这两个能力，纯靠已有 REAL 级计算端点。
