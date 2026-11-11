# P338-W3-N07 平台治理三守卫成熟化（能力台账阈值门 · 报告内容指纹门 · 节点状态漂移门）

- mission: `13935`  node: `p338w3-n07-platform-governance-gates`
- repo: `TensorPlatform`  git_commit: `41b0d3b8c87b0acd3184d9d6a0b505eacac9a6cb`
- generated_at: `2026-11-11T06:07:39.676867+00:00`
- 验收 JSON: `artifacts/platform_governance_gates/acceptance.json`  all_pass = `True`

## ① 案例介绍（机制背景与参数）

本节点建设平台治理三守卫，全部为**域无关机制**：能力 slug、阈值、节点/mission 状态均以数据注入，代码中不出现任何学科词条（由 `tools/evidence_domain_scan.py` 机器检查，见 J7）。

1. **能力台账自动升级守卫**：把一份证据束按 6 项要求打分为完整度，`score ≥ promote` 且无矛盾、且关键要求（独立验证、摘要可校验）均满足时升 REAL；`score < demote`、出现矛盾、输入非法或REAL 的证据被侵蚀时降级，全程 fail-closed。
2. **报告正文内容指纹门**：以 `report_id ↔ mission_id ↔ body_sha256 ↔ metadata_sha256` 四方绑定，把重复正文、重复 id、指纹篡改、身份错配变成 fail-closed 的账目缺陷。
3. **节点状态漂移门**：`campaign_nodes.status` 与 mission 终态映射比对，检出并收敛漂移，包括“blocked 挂着已 merged 的单”这一原本不可见的路由盲区。

参数表（来自 `config/platform_governance_gates.json`，先进配置后跑实验，未按结果回调）：

| 参数 | 值 |
|---|---|
| promote 阈值 | 0.8 |
| demote 阈值 | 0.5 |
| 证据要求数 N | 6 |
| 关键要求（逐项硬门） | independent_verification, sha256_verified |
| 漂移告警阈值 | 0.3 |
| 合成能力数（J1/J2） | 5 / 6 |
| 报告负控数（J3） | 4 |
| 漂移节点数（J4） | 10（另 1 个 orphan 负控） |

## ② 怎么做的（方法 / 实验设计 / 工具链）

### 实验设计（预注册）

- **决策**：是否用“完整度阈值门”替换既有的全或无证据链判定，并新增报告指纹门与漂移门。
- **备择方案**：A 新完整度阈值门（本单）；B 既有全或无链（`tools/capability_auto_audit/level_machine.py` 的 complete/broken 二值视图，作为基线）。
- **假设**：在 6 个合成能力上，A 的决策正确数严格高于 B，且不放松任何 fail-closed 约束。
- **指标**：决策正确数/总数；完整度绝对误差；负控判红数；漂移检出/收敛数；审计字段完整率；p95 延迟；pytest 用例数与零域语义命中数。
- **基线/对照**：B 全或无链；J3 用 2 份合法报告作阳性对照；J4 用 1 个无 mission 节点作 orphan 负控（不得被猜成 done）；J5 用单字节篡改探针作负控。
- **预注册阈值与停止规则**：J1 误差=0；J2 6/6 且基线 <6/6；J3 4/4 判红；J4 10/10 检出且收敛后残留=0；J6 p95 ≤100 ms；J7 ≥15 用例、≤120 s、零域词命中。任一 J 失败即本单不通过，不调阈值重跑。
- **变量控制**：合成证据固定、阈值来自配置文件、HTTP 调用 20 次取 p95、pytest 单进程；唯一变化的是判定机制 A/B。
- **重复性理由**：机制判定为确定性函数，6 个能力覆盖 1.0/0.833/0.667/0.5/0.333/0 六档完整度；延迟取 20 次以抑制调度抖动；pytest 全量而非抽样。
- **环境**：Python 3.11、FastAPI 0.136、pytest 9.2、matplotlib 3.10（Agg）；同机 in-process TestClient；无外部网络依赖。
- **资源/安全限制**：全部合成数据，不读写生产库；漂移收敛只产出命令计划，不直接改 `campaign_nodes`（见 ⑤ 局限）。

### 工具链

- `tools/platform_governance_gates/evidence.py`：6 项要求打分、目录扫描（reports/artifacts/benchmarks/verified）、sha256 校验、矛盾识别。
- `ledger_guard.py`：阈值决策 + 关键要求硬门 + 原子状态 + 复用 `AppendOnlyAuditLog` 的 sha256 链审计。
- `report_fingerprint.py`：正文规范化指纹、元数据指纹、四方绑定与 7 类缺陷。
- `status_drift.py`：mission 终态→节点状态映射、漂移检出、自动收敛计划、orphan 不猜测。
- `api.py`：三个 POST 端点 + health；响应内嵌 `latency_ms`。
- `acceptance.py`：本报告全部数字与图的唯一生成入口（`python3 -m tools.platform_governance_gates.acceptance`）。

## ③ 计算结果（程序化注入，零手抄）

### 判据总表

| 判据 | 实测 | 通过 | 证据工件 |
|---|---|---|---|
| J1 | 0.0 | True | `artifacts/platform_governance_gates/j1_completeness.json` |
| J2 | 6/6（基线 4/6） | True | `artifacts/platform_governance_gates/j2_promotion.json` |
| J3 | 4/4 | True | `artifacts/platform_governance_gates/j3_report_gate.json` |
| J4 | 10/10 detected, 10/10 converged, residual=0 | True | `artifacts/platform_governance_gates/j4_drift.json` |
| J5 | 6 entries, chain_ok=True, tamper_detected=True | True | `artifacts/platform_governance_gates/j5_audit.json` |
| J6 | worst p95=22.392 ms <= 100 ms | True | `artifacts/platform_governance_gates/j6_latency.json` |
| J7 | 56 tests, 0 failures, 3.0s, domain_hits=0 | True | `artifacts/platform_governance_gates/j7_pytest.json` |

### J1 完整度 vs 人工判读

| 能力 slug | 人工预注册值 | 计算值 | 绝对误差 |
|---|---|---|---|
| `demo.cap_alpha` | 1.000000 | 1.000000 | 0.000000 |
| `demo.cap_beta` | 0.833333 | 0.833333 | 0.000000 |
| `demo.cap_gamma` | 0.666667 | 0.666667 | 0.000000 |
| `demo.cap_delta` | 0.333333 | 0.333333 | 0.000000 |
| `demo.cap_epsilon` | 0.000000 | 0.000000 | 0.000000 |

最大绝对误差 = **0.000000**（判据要求 0）。

### J2 升级/降级/保持决策

| 能力 slug | 当前级 | 完整度 | 期望级 | 实际级 | 动作 | 正确 | 全或或无基线正确 |
|---|---|---|---|---|---|---|---|
| `demo.prom_mock` | MOCK | 1.000000 | REAL | REAL | UPGRADE | True | True |
| `demo.prom_stub` | STUB | 0.833333 | REAL | REAL | UPGRADE | True | False |
| `demo.prom_withheld` | WITHHELD | 1.000000 | REAL | REAL | UPGRADE | True | True |
| `demo.dem_real_gap` | REAL | 0.333333 | WITHHELD | WITHHELD | DOWNGRADE | True | True |
| `demo.dem_empty` | MOCK | 0.000000 | WITHHELD | WITHHELD | DOWNGRADE | True | False |
| `demo.hold_middle` | WITHHELD | 0.500000 | WITHHELD | WITHHELD | HOLD | True | True |

新门 **6/6**，全或无基线 **4/6**。

### J3 报告指纹负控

| 负控 | 期望缺陷码 | 实际检出码 | 判红 |
|---|---|---|---|
| duplicate_body | DUPLICATE_BODY | DUPLICATE_BODY | True |
| duplicate_report_id | DUPLICATE_REPORT_ID | DUPLICATE_REPORT_ID | True |
| tampered_body_fingerprint | DECLARED_BODY_MISMATCH | DECLARED_BODY_MISMATCH | True |
| mission_metadata_mismatch | ID_MISMATCH | ID_MISMATCH | True |

阳性对照 gate_ok = **True**；负控判红 **4/4**。

### J4 节点状态漂移

| 节点 | 观测状态 | 期望状态 | merged_but_blocked | 路由 |
|---|---|---|---|---|
| n01 | blocked | done | True | converge:done |
| n02 | done | failed | False | converge:failed |
| n03 | pending | in-flight | False | converge:in-flight |
| n04 | in-flight | done | False | converge:done |
| n05 | blocked | failed | False | converge:failed |
| n06 | failed | done | False | converge:done |
| n07 | pending | done | False | converge:done |
| n08 | in-flight | pending | False | converge:pending |
| n09 | done | in-flight | False | converge:in-flight |
| n10 | blocked | done | False | converge:done |

检出 **10**，收敛 **10**，收敛后残留漂移 **0**；orphan 负控 **1** 个（不猜测、不收敛）；漂移率 90.91% > 阈值 0.3 → alert=**True**。

### J5 审计日志

| seq | ts | 能力 | from→to | 完整度 before→after | Δ | 证据摘要 |
|---|---|---|---|---|---|---|
| 1 | 2026-11-11T06:07:31.902762+00:00 | `demo.dem_empty` | MOCK→WITHHELD | 0.0→0.0 | 0.0 | `495896244478…` |
| 2 | 2026-11-11T06:07:31.902762+00:00 | `demo.dem_real_gap` | REAL→WITHHELD | 0.0→0.333333 | 0.333333 | `55b91fb51daa…` |
| 3 | 2026-11-11T06:07:31.902762+00:00 | `demo.prom_mock` | MOCK→REAL | 0.0→1.0 | 1.0 | `4b1e0e3f7aeb…` |
| 4 | 2026-11-11T06:07:31.902762+00:00 | `demo.prom_stub` | STUB→REAL | 0.0→0.833333 | 0.833333 | `3eea5899c782…` |
| 5 | 2026-11-11T06:07:31.902762+00:00 | `demo.prom_withheld` | WITHHELD→REAL | 0.0→1.0 | 1.0 | `b7290087cc40…` |
| 6 | 2026-11-11T06:07:31.902762+00:00 | `demo.hold_middle` | WITHHELD→WITHHELD | 0.0→0.5 | 0.5 | `486a1766f18b…` |

链校验 ok=**True**，字段完整率=**True**，单字节篡改探针检出=**True**。

### J6 API 延迟

| 端点 | p50 ms | p95 ms | max ms | 判据 |
|---|---|---|---|---|
| ledger_scan | 15.415 | 22.392 | 34.49 | ≤100.0 ms |
| report_gate | 9.261 | 14.431 | 14.884 | ≤100.0 ms |
| drift_scan | 10.032 | 19.818 | 20.033 | ≤100.0 ms |

最差 p95 = **22.392 ms**（20 次调用）。

### J7 pytest 与零域语义

- 用例数 **56**，失败 **0**，错误 **0**
- 墙钟 **3.031 s**（限 120.0 s）
- 零域语义扫描：16 个文件，命中 **0**

### 能力台账 dogfood（自评诚实落账）

- 能力位 `platform.governance_gates` → 等级 **WITHHELD**（动作 HOLD，原因 `CRITICAL_EVIDENCE_MISSING`）
- 完整度 **0.833333**，缺失项 ['independent_verification']
- 关键要求硬门阻止了 5/6 分数直接升 REAL：独立验证仍待外部验收门注入，注入后自动升级。证据见 `artifacts/platform_governance_gates/ledger_entry/capability_entry.json`。

## ④ 结果图

- `artifacts/platform_governance_gates/figures/fig_completeness.png` — J1: six-requirement completeness per demo capability with promote/demote thresholds; computed values equal the hand table (max abs error 0).
- `artifacts/platform_governance_gates/figures/fig_decision_matrix.png` — J2: per-capability decision correctness, completeness gate vs the pre-existing all-or-nothing chain view (6/6 vs 4/6).
- `artifacts/platform_governance_gates/figures/fig_report_gate.png` — J3: report fingerprint negative controls, all four closed with the expected defect code (4/4).
- `artifacts/platform_governance_gates/figures/fig_drift.png` — J4: campaign-node status before/after convergence for the ten drifted fixtures (residual drift = 0).
- `artifacts/platform_governance_gates/figures/fig_latency.png` — J6: p95 latency of the three governance endpoints vs the 100 ms criterion.

说明：本单是平台机制/后端任务，没有物理场量，因此不产场量云图；图 1–5 分别对应完整度阈值、A/B 决策对照、报告负控判红、漂移前后状态、端点延迟。

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

- **阈值粒度**：4 项要求只能给出 0/0.25/0.5/0.75/1.0，0.8 阈值会退化成“只允许满分”。因此把要求细化为 6 项（4 项契约维度 + 出处完整 + 证据深度），使 5/6=0.833 可升、3/6=0.5 保持、2/6=0.333 降级，阈值带才有意义。
- **不能只靠总分**：独立验证与摘要可校验被设为关键要求，5/6 的高分不能掩盖它们缺失。这条规则让本单自身的能力位诚实停在 WITHHELD。
- **收敛边界**：J4 检测器只产出收敛计划，不直接写 `campaign_nodes` 表；调度器/战役机取用 `updates` 后落库。因此本单证明“可判、可收敛”，不声称已改生产库。
- **延迟口径**：J6 是 in-process TestClient，不含网络/TLS/网关；生产 p95 需另测。
- **审计范围**：复用既有 append-only sha256 链；单进程追加，跨进程并发写不在本单范围。
- **合成数据**：所有能力 slug 均为 `demo.*`，不对应任何学科能力；本单不替代领域验证。

## ⑥ 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 关键量 | 基准/期望 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| J1 完整度 | 预注册人工表 | max abs error=0.000000 | 0 | **是** |
| J2 决策正确率 | 6/6 | 6/6 (100.00%) | 0.00% | **是** |
| J3 负控判红率 | 4/4 | 4/4 (100.00%) | 0.00% | **是** |
| J4 漂移检出/收敛 | 10/10 + 残留 0 | 10/10 + 残留 0 | 0.00% | **是** |
| J5 审计字段完整率 | 100% | 100.00% | 0.00% | **是** |
| J6 端点延迟 | ≤100 ms（绝对判据） | p95=22.392 ms | 余量 77.61% | 绝对判据，非相对误差；**通过** |
| J7 pytest 时长 | ≤120 s | 3.031 s | 余量 97.47% | 绝对判据，非相对误差；**通过** |

结论：J1–J4 的关键量相对基准误差均为 0%（<3%）；J5–J7 为完整性/绝对时限判据，已逐项给出实测与余量，不存在含糊项。

## 附：复现命令

```bash
python3 -m pytest tests/platform_governance_gates -q
python3 -m tools.platform_governance_gates.acceptance
```
