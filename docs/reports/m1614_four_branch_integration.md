# m1614 · benchmark 残余四分支收编整合报告

**基线**：`main` 正典 v3.0（#272 benchmark 数据集正典 + #279 稳性受证合成），工作树基线
`6e24298`（= main `2e0dc4b` 全量 + 正典）。
**任务**：四个平行分支的「数据判定面」已被正典取代，但各带正典缺的**机制增量**；
把增量收编为单一整合分支，数据判定冲突一律以正典为准并记录弃用理由。

---

## 1. 收编清单（逐分支：保留 / 合成 / 弃用 + 理由）

图见 `docs/reports/m1614/fig1_branch_merge_matrix.png`（程序生成，见
`scripts/m1614_integration_figures.py`）。

### ① `agent/m1388`（1a2f59c）— 对标链路 schema 错配修复 + `calc_ct` 复原

| 文件 | 处置 | 理由 |
|---|---|---|
| `backend/api/domain/ship_benchmark.py` | **整文件收编（作为基底）** | 修复三处真实缺陷：载荷键 `speed`→`speed_ms`；响应字段 `ct` 不存在导致 `calc_ct` 恒 0；改为**进程内物理核**（`services.ship_resistance.resistance_breakdown`），HTTP 跳变可选（`SHIP_BENCHMARK_REMOTE=1`）。新增 `dataset_status`/`withheld`/`calc_rt_n` 与 `/calculations/benchmark_cases_audit`。正典 `benchmark_dataset_audit`（facade → `benchmark_integrity`）签名兼容，无需改动。 |
| `backend/api/calculations.py` | **hunk 级收编** | 只取「下游可控」增量：`ResistanceResponse` 增加 `total_resistance_coefficient` 与 `method`，并用 `services.ship_resistance.ct_from_rt` 计算。**不取**其把阻力端点整体重写为 `resistance_breakdown` 的 hunk——正典端点已有 `wetted_surface_m2` 输入校验、`form_factor` (1+k) 与 `friction_coefficient_cf`，整段替换会丢失这些能力。 |
| `backend/services/ship_resistance.py` | **不取（正典已有）** | 正典 #272 已并入同一物理核（`resistance_breakdown`/`ct_from_rt`/`displacement_from_cb`/`schoenherr_cf`）。 |
| `backend/tests/feat/test_benchmark_chain_m1388.py` | **整文件收编 + 数据判定适配** | 17 项测试全绿。其中 5 项原本断言「SUBOFF Ct/Rt 差 7.4–9.5x → INCONSISTENT、DTMB Fr 隐含 L=181 m → INCONSISTENT、12/12 withheld」，与正典 v3.0 定谳（SUBOFF 淡水 998.2、Rt=92.52 N 定谳为可复核；DTMB/KVLCC2 因缺实测湿面积 withheld）**直接冲突**。按「正典胜出」改为：SUBOFF 4 行 CONSISTENT、DTMB/KVLCC2 PARTIAL、verified=4/withheld=8；**门禁机制本身**改用注入腐坏行（Rt×7.4）验证仍必红。 |
| `backend/tests/feat/test_ship_benchmark.py` | **整文件收编 + 1 项适配** | `test_honest_fail_requires_verified_reference_rows` 原断言 `verified_cases==0`（其前提是旧数据集全不合格），按正典改为「4 行 verified 全超 15% → 建议升级成立」，门禁语义由上面的注入用例覆盖。 |
| `scripts/audit_benchmark_chain_m1388.py` + `artifacts/m1388/*` | **整文件收编 + 检查族适配** | 正典 `benchmark_integrity` 的检查族为 C1–C5（`froude_closure`/`displacement_closure`/`resistance_closure`/`friction_floor`/`form_bands`），原脚本硬编码 m1388 的三类 `I1/I2/I3` 标识会 KeyError；已改为按正典族名建矩阵（5 列）。工件在正典数据集上**重新生成**。 |
| `docs/reports/m1388_benchmark_chain_audit.md` | 收编（加正典说明头） | 正文保留为历史审计记录。 |

### ② `agent/m1406`（1eec305）— `/calculations/benchmark_audit` 账本端点 + C_T 反推 + 端口探测

| 文件 | 处置 | 理由 |
|---|---|---|
| `backend/services/benchmark_integrity.py` | **hunk 级收编（追加两个纯函数）** | 只取 `ship_platform_bases()`（:8001 → :8002 → :8010 候选探测，`SHIP_PLATFORM_URL` 优先）与 `total_resistance_coefficient()`（C_T = R_T/(½ρSV²)，缺 `ct` 时反推；无面积返回 `None` 不猜）。**不取**整文件：正典版是 m1561 裁决后的中文闭合校验闸门（PASS/WITHHELD/WARN/FAIL + `check_*` 族），m1406 版是较早的 REAL/WITHHELD 口径，整取会回退正典。 |
| `backend/api/domain/ship_benchmark.py`（m1406 侧 hunk） | **合成** | 端点 `/calculations/benchmark_audit` 落到正典 API：`audit_dataset(_BENCHMARK_PATH)`；端口探测 + `speed_ms` 回声校验（响应 `speed_ms` 与请求不一致即丢弃，防止 schema 再次错配）并入 `_remote_resistance`；C_T 反推并入远程分支。 |
| `scripts/benchmark_audit.py` | **整文件收编 + 接口适配** | `audit_dataset` 正典签名为 `Path`（原脚本传 dict）；输出目录改 `runs/m1614`；图为正典族名重写；新增 `resistance_response()`：真端点不可用时**进程内同参调用**端点函数（`:8001` 线上跑的是旧构建，只认 `speed`/`displacement`，直接 POST 会 422），并标注 `transport`。 |
| `scripts/gen_benchmark_doc.py`、`docs/benchmarks/*` | 整文件收编 + 说明头 | 文档数字按正典重生成。 |
| `runs/m1406/benchmark_audit.json` | **弃用（改为 `runs/m1614/` 重生成）** | 原工件在 m1406 自己的数据集版本上生成；数据集判定以正典为准，工件必须与正典一一对应。 |
| `backend/tests/test_benchmark_integrity.py`（m1406 侧） | **不取** | 正典版（210 行）测正典闸门 API；m1406 版测其 REAL/WITHHELD 口径，整取即回退。增量语义另立 `backend/tests/test_benchmark_audit_endpoint.py`（9 项，覆盖账本端点 / C_T 反推 / 端口探测 / 端点透出 C_T）。 |

### ③ `agent/m1407`（d0dc5c3）— 六类基线审计工具 + normalize

| 文件 | 处置 | 理由 |
|---|---|---|
| `tools/audit/baseline_integrity.py` | **整文件收编 + 两处口径适配** | C1–C6 六类审计（Ct↔Rt、Froude、单位、物理性、跨库、门禁策略）。适配 1：**C3 单位混用**须区分「未声明」与「记录里已声明尺度基准」——正典 DTMB 行把实船服务航速留在 `speed_kn`、模型速度按 Fr 反算写 `speed_ms`，并在 `speed_basis`/`legacy_values` 里写明，故声明过的偏差判 `WARN`（回显依据），未声明才 `FAIL`。适配 2：**C6 门禁策略**对显式 `gate_enabled=False` 的 honest-FAIL 算例判 `WARN`（其判决在消费侧被 WITHHELD），而非笼统 FAIL。 |
| `tools/audit/normalize_benchmark_reference.py` | **整文件收编 + 默认行为反转** | 原默认**直接写盘**覆盖 `benchmark_reference.json`，与正典「唯一裁决者」冲突且危险；改为**默认 dry-run**，必须 `--apply` 才写。dry-run 输出（如 DTMB Rt=7.0 N、KVLCC2 Rt≈1.91 MN）与正典判定冲突处以正典为准，工具仅作复核。 |
| `backend/services/ship_proxy.py`（m1407 侧重构） | **弃用（只取消费侧门禁语义）** | 冲突有两处：①正典把 `suboff_model` 的 300% 容差作为 honest-FAIL 历史记录保留（`backend/tests/test_benchmark_integrity.py::test_proxy_table_passes_after_fix` 断言 `tolerance_pct>=300` 且注释含 honest-FAIL），m1407 的表内改判与之冲突；②该分支的 `backend/tests/test_ship_proxy.py` 会把端点默认值从正典已修的 `:8001` **回退到 `:8002`**（`assert proxy.base_url == "http://localhost:8002/api/v1"`），属明确回退。**保留的语义**：新增 `POLICY_MAX_TOLERANCE_PCT`、给 `suboff_model` 加 `gate_enabled=False`、`benchmark_validate` 对非门禁算例输出 `WITHHELD`（不再把宽容差当绿灯）——正典表与正典测试全部不变。 |
| `backend/tests/test_baseline_integrity.py` | **按工具重写（10 项）** | 原文件测 m1407 的 proxy 重构 API（`derive_rt_from_ct`/`wall_sided_gm`/`gate_enabled` 表），与正典冲突；改写为测**审计工具**：正典数据集 PASS、注入腐坏必红（Rt÷6、Fr 与实船速度混配 → 隐含 L=181 m）、C3 声明/未声明分野、C6 门禁策略。 |
| `artifacts/baseline_integrity/audit_canon_v3.{json,md}` | 重新生成 | 原 `audit_before/after` 描述的是 m1407 自己的数据版本（已被正典取代），有误导性；改为在正典数据集上重跑并命名 `audit_canon_v3`。 |
| `docs/audit/BASELINE_INTEGRITY.md`、`docs/audit/figures/*` | 整文件收编 + 说明头 | 图保留（其结论——「v1 的 SUBOFF 12.5 N 反解湿面积 0.71 m² 与 R1 差 7.6 倍」——与正典独立一致）。 |

### ④ `agent/m1383`（f5929c1）— 消费侧「禁止伪真值授予 PASS」

| 文件 | 处置 | 理由 |
|---|---|---|
| `tools/audit/benchmark_reference_integrity.py` | **整文件收编（零改动）** | 独立第二审阅者（C1–C4 + 湿面积反推 + 趋势单调性）。在正典数据集上独立复现 `SUBOFF-01..04 = REAL`（Rt 92.52 N、S 5.42 m²、反推比 1.0001）、其余 8 行 WITHHELD，与正典一致——这是「第三份独立证据」。 |
| `tools/audit/make_benchmark_integrity_figures.py`、`artifacts/benchmark_integrity/*` | 整文件收编 | 图/工件保留（重新生成于正典数据集）。 |
| `backend/api/domain/ship_benchmark.py`（m1383 侧 hunk） | **语义合成，字段弃用** | 其消费侧门禁（伪真值不得给 PASS、完美命中仍须 WITHHELD）语义已由 m1388 基底的 `withheld`/`dataset_status` 机制实现；m1383 另加的 `reference_evidence`/`reference_issues`/`scored`/`reference_trust` 与 `integrity_verdicts` 是第二套并行字段，会造成两套判定口径，**弃用**（避免双闸门）。 |
| `backend/data/benchmark_reference.json` | **弃用（正典 v3.0 胜出）** | m1383/m1407 两版数据集与正典冲突（如 SUBOFF Rt 列、DTMB Fr 列、是否 12/12 withheld）。正典版已含 `reference_status`/`audit_flags`/`adjudication_reason`/`legacy_values` 溯源，本轮**未改动数据集一个字节**。 |
| `backend/tests/test_benchmark_reference_integrity.py` | **整文件收编 + 两组测试适配** | 18 项全绿。`TestRegistryMetadata` 原断言 m1383 自定义 `integrity` 块，改为正典字段 `reference_status`/`audit_flags`/`legacy_values`，并新增「独立工具复算 92.52 N」一致性用例；`TestConsumerGate` 改为测整合后的 `withheld`/`dataset_status`/`DATASET-WITHHELD` 语义。 |

---

## 2. 冲突裁决汇总（一律正典胜出）

| # | 冲突点 | 正典立场 | 分支立场 | 裁决 |
|---|---|---|---|---|
| 1 | SUBOFF 参考行是否可作判据 | 淡水 998.2 kg/m³、Rt=92.52 N@3 m/s，Ct↔Rt 闭合 <1% → **可复核** | m1383/m1388/m1407：Ct 与 Rt 差 7.4–9.5x → WITHHELD | 正典（三个独立工具在正典数据上均复现 92.52 N，闭合比 1.0001） |
| 2 | DTMB-5415 Fr/速度列 | 模型速度按 Fr 反算，实船航速入 `legacy_values`，整列 WITHHELD | m1383/m1388：判 INCONSISTENT | 正典（缺实测湿面积 → withheld；不宣称"矛盾"） |
| 3 | `suboff_model` 300% 容差 | 保留为 honest-FAIL 历史记录，测试锁定 `>=300` | m1407：改为 15% + `gate_enabled=False` | 正典表不动；**只**补 `gate_enabled=False` + 消费侧 WITHHELD（既不改数，也不给绿灯） |
| 4 | 端点默认 :8002 vs :8001 | :8001（`services/ship_api_endpoint`） | m1407 测试断言 :8002 | 正典（m1407 该测试弃用） |
| 5 | 数据集 `integrity` 块 vs `reference_status` | `reference_status`+`adjudication_reason` | m1383 `integrity` 块 | 正典字段；工具保留 |

---

## 3. 关键机制增量（正典缺、本轮补上）

1. **对标链不再依赖 HTTP**：默认进程内调用物理核；远程跳仅在 `SHIP_BENCHMARK_REMOTE=1` 时启用，且带 `speed_ms` 回声校验与多端口探测。
2. **端点透出 C_T 与模型来源**：`total_resistance_coefficient` + `method`，消费侧无需再猜字段；实测反推误差 4.3e-9（见 `runs/m1614/benchmark_audit.json`）。
3. **账本端点** `/calculations/benchmark_audit`：12 行逐条 C1–C5 判定 + 数据集级 binding。
4. **六类审计工具**（C1–C6）可作为 CI 闸门，且在正典数据集上 PASS、在注入腐坏时必红。
5. **fake-gate 收口**：`gate_enabled=False` 的算例在消费侧输出 WITHHELD，不再有「300% 容差永远通过」的绿灯。

---

## 4. 门禁与证据

命令与原始输出见 `docs/reports/m1614/gate_evidence.md`。

| 门禁项 | 结果 |
|---|---|
| 正典门禁 `tests/test_benchmark_canon.py` + `backend/tests/test_benchmark_integrity.py` + `tests/test_m1564_stability_canon.py --noconftest` | **57 passed**（全绿） |
| 收编/新增测试（m1388 链 17 + ship_benchmark 25 + 账本端点 9 + 六类审计 10 + 真值审计 18） | **79 passed**（全绿） |
| 双套件 `backend/tests` + `tests/` 相对基线（`git worktree` @ HEAD）签名对照 | 失败 ID 集 **157 vs 157，新增红 0**（157 条为缺 psycopg2/pgserver/pytest-asyncio 的既有环境红） |
| AST 全部变更 `.py` | 全部通过 |

### 关键数字（工件直读，零手抄）

- 正典数据集判定：PASS 4 / WITHHELD 8 / WARN 0 / FAIL 0，`binding=True`
  （`runs/m1614/benchmark_audit.json` → `dataset_audit.verdict_counts`）。
- 三份独立复核一致：`benchmark_integrity`（端点账本）、`tools/audit/benchmark_reference_integrity.py`
  （m1383）、`tools/audit/baseline_integrity.py`（m1407，C1 Ct↔Rt）均把 SUBOFF 4 行判为可复核，
  其余 8 行 withheld（图 `fig2_dataset_verdicts.png`）。
- 阻力端点 vs 闭式解：Re 3.8e-10、Fn 3.5e-07、**C_T 4.3e-09**、ITTC-57 Cf 1.1e-07、R_f 0、
  `R_t−(R_f+R_r)` 0、残阻律 1.0e-06 —— **全部 <3%**（`runs/m1614/benchmark_audit.json` → `engine_ledger.checks`）。
- 旧载荷键复现：`speed=3.0` 被端点静默按 **1.0 m/s** 计算（比值 3.0），即历史 `calc_ct→0` 的根因
  （`legacy_payload_probe`）。
- 对标链实测：SUBOFF 4 例中 3 例误差 ≤15%（1.2–34.6%）；DTMB-5415 10.7–18.2%、KVLCC2 49.5–56.1%
  （后两者参考行 withheld，不构成精度判决）——`artifacts/m1388/chain_results.json`，图 `fig3_model_error_spectrum.png`。

### 误差判定（逐项，是否 <3%）

| 量 | 基准 | 误差 | <3% |
|---|---|---|---|
| C_T（端点） | `R_T/(0.5ρSV²)` 闭式 | 4.3e-09 | ✅ |
| Re / Fn | `VL/ν`、`V/√(gL)` | 3.8e-10 / 3.5e-07 | ✅ |
| Cf（ITTC-1957） | `0.075/(log10Re−2)²` | 1.1e-07 | ✅ |
| R_f / R_t 可加性 | `0.5ρSV²Cf`、`R_f+R_r` | 0 / 0 | ✅ |
| SUBOFF 参考行 C_T↔R_T | 淡水 998.2、S=5.42 m² | 0.01%（92.52 N） | ✅ |
| SUBOFF 模型 vs 参考 C_t | Roddy 1990 | 1.2%–34.6%（3/4 例 ≤15%） | ❌ 部分（如实记录：高 Fr 端 34.6% 超限，模型为轴对体标定拟合） |
| KVLCC2 模型 vs 参考 C_t | SIMMAN 2008 | 49.5%–56.1% | ❌（honest-FAIL：模型非商船方法，且参考行 withheld） |

### 未做 / 局限

1. 数据集本身**一个字节未改**（正典为唯一裁决者）；`normalize_benchmark_reference.py` 默认 dry-run。
2. `suboff_model` 的 300% 容差按正典保留（历史记录），只补 `gate_enabled=False` + 消费侧 WITHHELD；
   彻底删除该旁路容差需正典层面裁决。
3. 线上 `:8001` 仍是旧构建（只认 `speed`/`displacement`），本轮未部署；账本因此使用进程内同参回退，
   已在 `engine_transport` 标注。
4. 78 项既有环境红（缺 psycopg2/pgserver/pytest-asyncio）未修（属部署依赖，不属本单范围）。