# 报告门禁灰度上线：从「审计出的 27% 误放行」到生产单一实现（m2030）

> EDIT_WHITELIST: backend/services/, backend/api/, backend/tests/, tools/audit/, docs/reports/, artifacts/
> 仓：TensorPlatform（平台席，不触碰 TensorPlatform 内的船舶业务代码）
> 工件：`artifacts/report_gate_rollout/rollout.json` + 两张图；测量脚本 `tools/audit/report_gate_rollout_measure.py`（只读复算）

## ① 案例介绍

**物理背景/系统背景**：平台以「论文级六要素」为报告验收标准（owner 2026-10-29 升级：案例介绍 / 方法与流程 / 计算结果 / 结果图 / 注意点 / 误差判定）。报告门禁
`backend/services/report_gate.py::check_report_quality`（六要素 v2）早在 2026-11-01 就已实现并带单测，同日审计
（`docs/reports/report_gate_audit_2026-11-01.json`）量化了更严重的结论：**生产仍在跑 keyword-only 的 v1 门禁，v1 对六要素 v2 的误放行率 35.84%**。
换句话说：审计结论没有落到生产 = 一条从未被清偿的「验证欠账」，而且 v1 判据在生产模块里内联一份、测试里手抄一份（副本漂移风险）。

**几何/参数表（本案的"几何"= 门禁语义与灰度参数）**：

| 项 | 值 | 说明 |
|---|---|---|
| 语料 | 全库 `tensor_reports` = **1364** 篇（含 markdown / figures / qa_status） | psql 只读导出 |
| v1 判据 | 4 组关键词、组间被拍平成 OR；`figures` 为空时正文有「图」字即过；无第 ⑥ 要素 | 灰度前线上行为 |
| v2 判据 | 6 要素、组内 OR 组间 AND；图必须真存在且 caption 非空（或无图理由）；⑥ 需误差数字 + 显式「是否<3%」结论，或声明无基准 | 已实现于共享模块 |
| 灰度开关 | `TP_REPORT_GATE_MODE` ∈ {off, warn, enforce}，默认 **warn**；`POST /tensor/reports/qa/check` 可逐单覆盖 `mode` | 本单新增 |
| 判据真身 | `services/report_gate.check_report_quality_v1`（逐字档案）+ `check_report_quality`（v2） | 单一实现 |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **判据收口**：把生产模块 `api/tensor_reports.py` 内联的 v1 判据**删除**，逐字迁入 `services/report_gate.py` 成为
   `check_report_quality_v1`；API 只保留兼容别名 `_check_report_quality = check_report_quality_v1` 与 `evaluate_report()` 调用。
   结构性守卫单测（`test_api_module_does_not_refork_gate`）因此**由红转绿**——它此前是红的（生产一直在 fork 判据）。
2. **统一判决 `evaluate_report(markdown, figures, mode)`**：一次算两版判决，显式输出 `decided_by / diverges / would_flip / v2_advisory_issues`；
   `off|warn` 由 v1 写 `qa_status`（warn 期 v2 只作 advisory 并随响应返回，`qa_rejection_reason` 追加 `[advisory:v2]` 前缀），`enforce` 由 v2 写。
3. **为什么默认 warn 而不是直接 enforce**：全库有 396 篇历史报告的 `qa_status` 会翻盘，在多席饱和、报告流在飞时直接切 enforce 会打断其他席的交付；
   灰度把「判据能力」与「状态迁移」解耦，enforce 由 owner 一个环境变量打开。
4. **对账测量**（`tools/audit/report_gate_rollout_measure.py`，纯只读）：全库重放 v1/v2/三档判决 + **v1 真身 twin 检查**
   （共享 `check_report_quality_v1` vs 灰度前内联版 `git show 57e72968` 逐单比对）。
5. **测试**：`backend/tests/test_report_gate_rollout.py`（新增 15 例）+ `test_report_gate_validation.py`（既有 16 例）。
   对抗样本 A/B/C/D = 参数表替代物理背景 / 只提「图」字 / 有误差数字无结论 / 有图无 caption；另设「合规控制」防误拒、honest-FAIL 无基准声明应放行。
6. 工具链：Python 3.11 + pytest + psql(只读) + matplotlib。

## ③ 计算结果（全部由 `rollout.json` 程序化注入）

| 指标 | 值 |
|---|---|
| 语料规模 | 1364 篇 |
| v1 通过率（灰度前线上） | 84.02% |
| v2 六要素通过率（若 enforce） | 58.80% |
| v1 误放行篇数 | 370 |
| v1 误放行率（占全库） | 27.13% |
| 误放行占 v1 放行数 | 32.29% |
| off 档状态翻转 | 0 |
| warn 档状态翻转 | 0 |
| enforce 档状态翻转（成本上界） | 396 |
| v1 twin 不一致 | 0/1364（对照 rev 57e72968） |

**六要素逐项通过率（语料级）**：

| 要素 | 通过率 |
|---|---|
| physics_statement | 82.11% |
| method | 92.30% |
| validation | 96.48% |
| figures | 79.47% |
| limitations | 95.97% |
| error_verdict | 86.07% |

**误放行报告的要素缺失分解**（一篇可命中多项）：

| issue | 篇数 | 占误放行 |
|---|---|---|
| missing_figures | 189 | 51.1% |
| missing_physics_statement | 137 | 37.0% |
| missing_error_verdict | 126 | 34.1% |
| missing_validation | 26 | 7.0% |
| missing_limitations | 7 | 1.9% |
| figures_missing_caption | 5 | 1.4% |

**误放行样本（前 6 条，供人工抽检）**：

| id | 标题 | issue codes |
|---|---|---|
| 11 | Mission 5 补报：UI Probe 工件创建 — TensorPhysics 研究编排平台 | missing_figures |
| 14 | Mission 7 — 船舶总体性能平台审计报告（补报） | missing_physics_statement, missing_figures |
| 20 | TensorBEM v0.5.0 审计与基线 | missing_figures |
| 24 | 【QA】批次质检 10,11,14 | missing_error_verdict |
| 34 | Mission 20 Backfill: SDAA Sweep cylinder_3d (Re=40) — honest-FAIL | missing_physics_statement, missing_figures, missing_error_verdict |
| 37 | 【QA】批次质检 27,28,29 | missing_physics_statement |

**测试结果**：`pytest backend/tests/test_report_gate_rollout.py backend/tests/test_report_gate_validation.py -q` → **31 passed**（其中 `test_api_module_does_not_refork_gate` 由红转绿）。

## ④ 结果图

* `artifacts/report_gate_rollout/fig_pass_rates.png`：v1 通过率 / v2 通过率 / v1 误放行率三联柱状图。
* `artifacts/report_gate_rollout/fig_rollout.png`：左=误放行的要素缺失分解（横向条形），右=灰度三档的 `qa_status` 翻转数（off/warn 必须为 0）。
（本仓无 CJK 字体，图内标签改用英文以保证可读；数值与上表同源。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **「审计已完成」≠「欠账已清偿」**：v2 判据与审计结论在 2026-11-01 就存在，生产却仍是 v1；判断验证欠账是否清偿，判据是**生产路径是否真的执行了它**，不是审计产物是否存在。
2. **结构性守卫单测此前是红的**（`test_api_module_does_not_refork_gate` 断言生产 import 共享实现）——红单在饱和流水线里被当成"已知噪音"。本单以它为入口定位真缺陷；建议后续巡检把"红单"当门禁而非背景。
3. **v2 仍是关键词判据**：语义层（数字是否真由程序注入、结论是否与数字自洽、"无图理由"是否为托词）尚未检查；本单只证明 v1→v2 的判定差异，不主张 v2 已不可绕过。
4. **创建路径未设门禁**：`POST /tensor/reports` 仍直接落库（`qa_status=pending`），门禁只在 `/qa/check` 生效；enforce 前需确认 QA 巡检实际覆盖新报告，否则 enforce 只作用于被点检的报告。
5. **advisory 落库方式**：`qa_rejection_reason` 是单文本列，v2 advisory 以 `[advisory:v2]` 前缀并入；若后续要按 issue 统计，应升列为 jsonb。
6. **适用范围**：v1/v2 判决对 `figures` 的类型敏感（允许 dict 列表与旧 name 字符串列表）；语料实测 `figures` 存在 jsonb 字符串形态，测量脚本已做归一化，生产 DB 取值为 jsonb。
7. 语料在增长：本单 v2 通过率 58.80%（n=1364），审计时 50.3%（n=837），差异来自语料扩充而非判据变更（判据未改一字）。

## ⑥ 误差判定

本案无物理量基准（门禁是判定器，不是求解器），因此按「无基准须声明原因」处理：**基准 = 灰度前线上 v1 的逐单判决**（可由配置的边界样本替代）。

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| off 档 `qa_status` | 灰度前 v1 逐单判决 | 0/1364 = 0.0000% | ✅ 是（0） |
| warn 档 `qa_status` | 灰度前 v1 逐单判决 | 0/1364 = 0.0000% | ✅ 是（0） |
| 共享 v1 档案 vs 灰度前内联 v1 | `git show 57e72968` 逐单重放 | 0/1364 = 0.0000% | ✅ 是（0） |
| 对抗样本召回（A/B/C/D 全打回） | 4/4 应打回 | 4/4，漏放 0 | ✅ 是 |
| 合规控制误拒 | 1/1 应放行 | 0 误拒 | ✅ 是 |
| v1 误放行率（本单要清偿的欠账） | v2 六要素判决 | 27.13%（非误差，是缺陷暴露量） | — 不计入 <3% 口径 |

结论：三处与灰度前行为的一致性误差均为 **0% < 3%**，warn 默认档对在飞流程零扰动；enforce 档会翻 396 篇（29.03%），这是**未清偿欠账的规模**，不是回归。

## 附：本单未做（honest 留痕）

* 未切 enforce（需 owner 决策 + QA 巡检覆盖新报告的证据），默认 warn。
* 未做 v2 的语义层加固（数字来源校验/结论-数字自洽），只做灰度与对账。
* 未改动 `POST /tensor/reports` 的创建路径门禁（列入遗留，见注意点 4）。
