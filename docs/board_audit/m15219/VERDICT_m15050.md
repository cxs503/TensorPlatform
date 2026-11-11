# m15219 交差核验报告 —— 被审单 m15050（H6-MATURITY-GATE，TensorPlatform）

> 交差核验（非重做）。被审单状态 succeeded / pr_gate=#1215 merged；本单只做**取证 + 判级 + 落表**。
> 核验脚本 `docs/board_audit/m15219/verify_m15050.py`（**不 import 被审工具**，成熟度/账本/覆盖率全部独立重算），
> 机器可读结果 `docs/board_audit/m15219/verify_output.json`；图 `fig1_audit_checks.png` / `fig2_maturity_recount.png` / `fig3_gate_coverage.png`。
> 审计时点 2026-11-11 20:40Z 前后；生态基线 main=`1649792e5`；被审工作树 `/root/tp-agent/work/m15050` head=`6a1a550f3`。

## ① 独立性声明与适用边界

- 独立点：五级成熟度画像、增量账本 21 条方向、链指针、门禁覆盖率都由本单**从原始源文件/DB 重算**，被审工件里的数字只作为被核对象；负控变异由本单**自造**（3 例），不复用被审单的 5 例变异。
- 复用点（不可避免）：调用被审 CLI `python3 -m tools.maturity_gate_h6 run|verify|mutate` 作为被核行为本身（复现性、负控、变异基准复跑）。这属于“我的实验”，不是它的自证。
- 未做/不可判：① 无集群复跑（单机毫秒级，`run` 0.3 s，递交集群会切断 `/root/tp-agent/work/m15050` 数据溯源）；② 被审单引用的第三源 `live_probe_2026-11-11` 在本机仍缺失，第三方口径核对不可判；③ CI/GitHub 侧 x86 复现未跑（本机 loongarch64）。

## ② 三件套取证（run_artifacts / benchmark / repro_cmd）

| 核验项 | 结论 | 关键证据 |
|---|---|---|
| A1 run_artifacts 存在性 | ✅ | 11 个文件全在，缺失=无；gate.json sha256=`4304a97a43a11eed…`（20281 B）|
| A2 repro_cmd 可执行+确定性 | ✅ | 重跑 gate.json sha256=`4304a97a43a11eed…` == 工件 == 基准 `4304a97a43a11eed…`；`bash runs/m15050/repro_cmd.sh` rc=0；gate.json **无时间戳**，故 sha 恒定（可复现不是靠“同一秒”）|
| A3 成熟度画像独立重算 | ✅ | 基线 score 0.869048 / 当前 0.750000，直方图逐格一致，差异=无 |
| A4 增量账本独立重算 | ✅ | 重算 {'PROMOTE': 3, 'DEMOTE': 17, 'HOLD': 1, 'NEW': 0, 'DROPPED': 0}；逐能力 21 条方向/Δ秩一致，链指针 True、链头 True，差异=无 |
| A9 单元测试复跑 | ✅ | `pytest tools/maturity_gate_h6/tests -q` → 24 passed（本单复跑 rc=0）|
| A7 报告六要素 | ✅ | docs/H6_MATURITY_GATE_G1.md（10069 B）①-⑥ 全在，显式回答“<3%”=True |

## ③ 负控实验（本审计自造变异，验证门到底能不能失败）

| 用例 | 变异 | 期望 | verify rc | 检出 |
|---|---|---|---|---|
| T0_clean_control | 无（干净副本） | verified | 0 | ✅ 干净副本未被误报 |
| P1_verdict_tamper | gate.json verdict PASS→FAIL | detected | 1 | ✅ 检出 |
| P2_criteria_tamper | gate.json criteria[0].pass 取反 | detected | 1 | ✅ 检出 |
| T1_score_tamper | gate.json maturity.after.maturity_score 0.75→0.99 | detected | 0 | ❌ 未检出 |
| T2_level_flip | increment_ledger.json calc.resistance current_level DEMOTE→REAL | detected | 0 | ❌ 未检出 |
| T3_quote_tamper | increment_ledger.json calc.resistance evidence_refs.line_sha256 末位翻转 | detected | 0 | ❌ 未检出 |

变异基准独立复跑（`mutate` 子命令）：recall=1.0（声明 1.0）、false_positives=0（声明 0）、match=True → ✅ 声明可复现。

**但正控通过 ≠ 全覆盖**：`verify` 只比对 5 个字段（`tools/maturity_gate_h6/core.py:604-620`：verdict / criteria_pass_rate / thresholds_sha256 / criteria / increments.chain_head），本单实测盲区 = ['T1_score_tamper', 'T2_level_flip', 'T3_quote_tamper']（成熟度块与 `increment_ledger.json` 条目不在比对面）。

## ④ 基准裁判（唯一裁判：tensor_benchmark_cases）

- 注册行：id=12，case_name=`gate 覆盖率计量`，metric=`门禁覆盖率=可失败门/产出线`，tolerance=`=100%`，ref_source=`KPI-GATE-100 计量口径（产出线×门禁矩阵可失败性）`，**status=pending**（从未 passed）。
- 本单独立计量（KPI-GATE-100 口径 = 100 × |(G1∧G2∧G3_prod) 的产出线| / 5，逐线探针见 result JSON）：**got_strict=60%**、**got_structural=80%**，容差 `=100%` → **不在容差内**。
- 逐线探针：

| 产出线 | G1 门存在 | G2 可失败 | G3 生产拒绝 | G3 负控测试 |
|---|---|---|---|---|
| PR | True | True | True | False |
| 报告 | True | True | True | True |
| 基准 | True | True | True | False |
| 知识 | True | True | False | True |
| 论文 | True | False | False | False |

- 与既有计量（docs/board_audit/m15168/evidence/artifacts/result.json：current=60.0 / current_structural=80.0）**独立重算一致**（60/80），差异来自**分母时点**：报告线 qa_status pending 从 9395 → 9449 份（产出在长，门没长）。
- 关键判断：H6 成熟度门**不在**这 5 条产出线里，所以它做得再好也不改变该指标；被审工件里也没有任何“产出线覆盖率”计量（mission_self_measurement={'gate_json_has_coverage_metric': True, 'mission_measured_output_lines': False}）→ 该基准 case 对本锚点既未测、也未达，`status=pending` 属实，**不构成本单的 L4 证据**。

## ⑤ 分级判定（L0-L5；级别=功能实质，与报告篇幅无关）

| 级别 | 判据 | 结论 | 证据 |
|---|---|---|---|
| L1 数学/单元验证 | 有可失败单测 | ✅ | pytest 24 passed |
| L2 独立功能实现（输入→计算→输出） | 真代码+入口+运行工件+确定复现 | ✅ | core.py 616 行（run_gate:519 / verify_artifact:604）、`python3 -m tools.maturity_gate_h6 run` rc=0、工件 7 件 sha 恒定 |
| L3 系统集成（接入主求解器/平台） | 决策路径调用方 | ❌ | 无 backend/api/ops/CI/战役门调用方；仅 scripts/post_h6_maturity_report.py 读产物 PNG 发报告 → 不达 L3 |
| L4 基准验证（完整算例+误差分析+可复现，外部基准过容差） | 注册基准 case 达标 | ❌ | 注册基准 case 容差==100% got_strict=60.0 got_structural=80.0 status=['pending']；自造变异基准是**自指基准**（门测门），不作 L4 |
| L5 科研工程成果 | — | ❌ | 需对外部基准/文献给出可复现的误差结论 |

**裁定级别 = L2**（L2 消账，L3/L4 明确不达）。
与返修前 m14961 的 L1 相比：本单**确实补齐了 L2 的三件套**（入口 `__main__.py`、7 个运行工件、一键复现脚本），且工件确定性可复现（不是摆样子）——这部分是硬增量。

## ⑥ 严重度排序发现（含复现）

| # | 严重度 | 发现 | 证据/复现 |
|---|---|---|---|
| F1 | **中-高**（声明过宽） | 报告 §⑤ 称“篡改 gate.json 会被 `--verify` 检出”，实测只覆盖 5 字段；篡改 `maturity.after.maturity_score`（0.75→0.99）、翻转账本条目等级、翻转引文 `line_sha256` 三种篡改 `verify` 全部仍返回 verified=true | `verify_m15050.py` A5 用例 T1/T2/T3；`core.py:604-620` 只比对 verdict/criteria_pass_rate/thresholds_sha256/criteria/chain_head |
| F2 | **中**（诚实性） | 报告 §⑦.6 写“源缺失 → 判据取 null → 不通过，绝不静默当作通过”，但工件里源 `live_probe_2026-11-11` available=false 的同时，8 条判据**无一为 null**、verdict=PASS、criteria_pass_rate=1.0 —— 显式缺口只留在 `sources[]`，未进入判据面 | A10：criteria_with_null_value=[]，unavailable_sources=['live_probe_2026-11-11']，verdict=PASS |
| F3 | 中 | 无决策路径调用方（backend/api/ops/.github 0 命中），仅 `scripts/post_h6_maturity_report.py:31,32,40` 读产物 PNG 发报告 → 门没有接入平台运行回路，L3 不达 | A8：decision_path_callers=[]，downstream_readers=['scripts/post_h6_maturity_report.py:31', 'scripts/post_h6_maturity_report.py:32', 'scripts/post_h6_maturity_report.py:40'] |
| F4 | 中 | 注册基准 case（id=12，tolerance=`=100%`）未达：got 60%/80%，且 status=pending；ops 侧也没有把该门纳入 5 条产出线的门矩阵 | A6 逐线探针 + SQL 读 tensor_benchmark_cases |
| F5 | 低-中（可复现性口子） | 工件 `artifacts/maturity_gate_h6/*` 与 `runs/m15050/*` **未在 main**（`git cat-file -e HEAD:artifacts/maturity_gate_h6/gate.json` → fatal；写出侧门禁把 artifacts/runs 滤除，同 m15032 的“23 个文件被门禁滤除”）；main 只有代码+docs → 复现必须回 `work/m15050` 重跑 | 本单在 main=1649792e5 实测；入口 `tools/maturity_gate_h6/__main__.py` 与 docs 在 main |
| F6 | 低 | `run_metadata.json` 记 `verdict: PASS`、`maturity_score_before 0.869048 → after 0.75`（成熟度**下降**）；表述容易被读成能力提升，实际语义是“自述虚高被对账拆穿” | A3 重算复现该 -0.119048 |

## ⑦ 被审单报告中的过度声明（逐条）

1. “**结构性防造假能力**：篡改 gate.json 会被 `--verify` 检出” —— **过度**，覆盖面见 F1（仅 5 字段）。
2. “五级成熟度 + 交叉一致性”实现无异议（本单独立重算逐格一致），但“**门禁**”一词易被读成平台级门；它目前是**独立工具**，不在任何运行回路（F3）。
3. 报告 §⑤ 的 mutation benchmark 表“ref vs got 全一致”成立，但它是**自指基准**（同一套代码自己注入自己检），不能替代外部基准（F4）。
4. 报告 §⑦.6 “宁拒不猜”的机制描述与工件不符（F2）。

## ⑧ 未核查（unknown / not_run，不记为通过）

- 集群双节点 loong64 复跑：未做（单机 0.3 s 级，且集群无本机工作树路径）。
- CI x86 侧复现：未做。
- 引文绑定所依赖的语料快照与线上 `tensor_reports` 正文的**逐字一致性**：只核了工件声明的 match_rate=1.0/10 条，未逐条抓原文（被审工具自身做该重算，属自指面）。
- `verify` 对 `docs/H6_MATURITY_GATE_G1.md` 的人读表数字篡改是否检出：仅复跑了被审单 `mutate`（M5 命中），未做本单独立变异（改动会被写回被审工作树）。

## ⑨ 处置（disposition）

- **请求修改（request changes）**，但**不做返修重置**：级别 L2 确认，m15050 的 L2 交付成立、工件确定性可复现，可消账。
- 需修三处（下轮）：F1 把 `--verify` 比对面扩到 `maturity` 与 `increment_ledger.json` 条目；F2 让 `available=false` 的源真正把相关判据压成 null（并让 PASS 不可能在 null 下出现）；F4/F3 若要把该门升到 L3/L4，必须接一个真实运行回路（例如把 H6 门注册进战役/报告门的判定路径）并登记一个达标基准 case。
- 基准 case id=12 **保持 pending**（got 60/80% ≠ `=100%`），本单不改其状态，避免自证通过。

---

### 附：核验项总表

| 核验项 | 通过 |
|---|---|
| A1_run_artifacts | ✅ |
| A2_repro_determinism | ✅ |
| A3_maturity_recount | ✅ |
| A4_increment_recount | ✅ |
| A5_negative_controls | ✅ |
| A6_benchmark_judge | ❌ |
| A7_report_sections | ✅ |
| A8_integration_callers | ❌ |
| A9_tests | ✅ |
| A10_claim_vs_artifact | ❌ |
