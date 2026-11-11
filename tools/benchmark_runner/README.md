# benchmark_runner —— 平台自主基准测试引擎（P167W2-N08）

配置驱动、无人值守、机器可裁的 benchmark 引擎。**零领域语义**：本目录源码与
算例配置内不出现任何应用域词条；被测系统（SUT）与参考解都通过
`"module:function"` 点分路径注入，接真实求解器时**不需要改引擎**。

## 四件套

| 模块 | 职责 |
|---|---|
| `runner.py` | 读 suite 配置 → 逐算例执行 → 判据 → 工件 → 回归；CLI + 退出码 |
| `gate.py` | 判据门，fail-closed（无判据/缺指标/非有限值/未知算子一律 FAIL） |
| `artifact.py` | 证据工件：逐算例 JSON + suite `result.json` + PNG + `manifest.json`（sha256 链）+ provenance |
| `regression.py` | 与基线逐指标对比，相对漂移 > 3%（可配）告警，分级 warn / critical |
| `kpi_bridge.py` | 只读工件树，按 `case_id` 去重统计"通过且带哈希链"的无人值守算例数 |
| `report_figure.py` | 跨 suite 汇总图（误差 vs 预声明阈值、判据余量） |

辅助：`synthetic.py`（合成 SUT / 参考解）、`instrumented.py`（数值不变但工作量放大的包装
SUT，用于回归检测的判别力/特异性演示）、`model.py`、`config.py`。

## 快速开始

```bash
# 单 suite：执行 + 判据 + 工件 + 回归
python3 -m tools.benchmark_runner.runner \
    --config tools/benchmark_runner/configs/fx_integration.json \
    --out artifacts/benchmark_runner \
    --baseline artifacts/benchmark_runner/baselines/fx_integration.json

# 首次建立基线（之后用同一命令做回归检测）
python3 -m tools.benchmark_runner.runner --config ... --baseline ... --update-baseline

# KPI 桥（只读工件，按 case_id 去重）
python3 -m tools.benchmark_runner.kpi_bridge --root artifacts/benchmark_runner

# 汇总图
python3 -m tools.benchmark_runner.report_figure --root artifacts/benchmark_runner \
    --out artifacts/benchmark_runner/summary.png
```

退出码：`0` 全过且无回归 · `1` 判据门不通过 · `2` 回归漂移/覆盖度下降 · `3` 配置非法 ·
`4` 内部错误 · `5` 证据链回验失败（工件 sha256 对不上）。

```bash
# 证据链回验（tamper detection；退出码 0/1）
python3 -m tools.benchmark_runner.artifact --verify artifacts/benchmark_runner/<suite>/<run_id>

# 零领域语义守卫（CI gate；退出码 0/1）
python3 -m tools.benchmark_runner.domain_guard --json
```

## 配置语法

```json
{
  "suite": "fx_example",
  "version": 1,
  "drift_threshold": 0.03,
  "cases": [
    {"id": "c1",
     "workload":  "pkg.mod:sut_function",
     "inputs":    {"a": 0.0},
     "reference": {"workload": "pkg.mod:reference_function", "inputs": {"a": 0.0}},
     "timeout_s": 60.0,            // 可选：覆盖 suite 级墙钟预算
     "criteria": [{"metric": "rel_error", "op": "<=", "value": 1e-9}]}
  ],
  "suite_criteria": [
    {"metric": "n_cases", "op": ">=", "value": 1},
    {"metric": "pass_ratio", "op": ">=", "value": 1.0}
  ]
}
```

判据算子：`<= < >= > == !=`（`==`/`!=` 支持 `tol`）、`in_range`（`min`/`max`）、
`monotonic`（`direction=increasing|decreasing|non_decreasing|non_increasing`，可选 `tol`）、
`finite`（标量或序列）。

自动派生指标：`value`、`reference`、`abs_error`、`rel_error = |value-reference| / max(|reference|, eps)`；
SUT 返回的其它数值项（如 `iterations`、`evals`）自动并入 metrics 供判据使用。
suite 级自动派生：`n_cases`、`n_passed`、`n_failed`、`n_error`、`n_timeout`（超时个数）、
`n_skipped_budget`（预算用尽未跑的个数）、`pass_ratio`、`max/mean_rel_error`、`max_abs_error`
——所以"本批不允许出现超时"可以写成预声明判据：`{"metric": "n_timeout", "op": "<=", "value": 0}`。

## fail-closed 语义（本引擎最重要的性质）

* 算例**没写判据** → 运行期判 FAIL（`__none__:__no_criteria__`），绝不静默通过；
* suite**没写 `suite_criteria`** → 配置非法（退出码 3）；
* 指标缺失 / NaN / inf / bool / 字符串 → FAIL；
* 基线缺失 → 回归状态 `no_baseline`，**不是** "clean"（"没比过"≠"没退化"）；
* 只有 `manifest.json` 存在（哈希链完整）的 run 才计入 KPI；
* **挂死 ≠ 无事**：逐算例墙钟预算用尽 → `status="error"`（`__timeout__`）；suite 预算用尽 →
  剩余算例 `status="error"`（`__budget__`）。无人值守运行不允许"永远等下去"；
* **算例消失 = 回归**：基线的算例在当前 run 里不见了 → `case_missing` critical、退出码 2
  （"门还绿着但覆盖面缩水"曾是本引擎的静默失守路径，v1.1 起堵住）；
* **"有产物" ≠ "产物可信"**：落盘后立即回验 manifest 哈希链；对不上 → 退出码 5。
  回验覆盖四类破绽：重算 sha256/字节数不一致、清单 digest 被改、未登记的多余工件、
  算例 JSON 自带 `artifact_sha256` 与内容不符（局部篡改）。

## 零领域语义的机器证明

`domain_guard.py` 扫描本包源码与算例配置，命中域词条即 CI 红；词表是**外部配置**
（`tools/evidence_domain_lexicon.json`），引擎内不含任何域词条——包括"证明自己干净"
这件事本身也不靠人看，靠扫描。缺词表 → `status="no_lexicon"`、`ok=False`（扫不了 ≠ 干净）。

## 证据链

`manifest.json` 记录每个工件（逐算例 JSON、`result.json`、PNG）的 sha256 + 字节数，
外加 `config_sha256` / `git_sha` / `engine_version`；每个算例 JSON 自带 `artifact_sha256`。
报告里的任何数字都可沿 `manifest → 工件 → config_sha256 → git_sha` 回溯（零手抄的前提）。

## 测试

```bash
python3 -m pytest tests/test_benchmark_runner_p167w2_n08.py -q              # 22 passed（本体）
python3 -m pytest tests/test_benchmark_runner_m9703_maturity.py -q          # 16 passed（成熟化波）
```

测试覆盖判别力（负对照必挂、漂移 +5%/+15% 必报、挂死算例必裁超时、篡改工件必判不可信、
算例消失必报回归）与特异性（+2% 不报、机器零不误报、新增算例不报、干净 run 判 ok），
而不只是"跑通"。
---

# P167W2B-N09：批级无人值守 campaign + 既有验证面 + KPI 对账

N08 的粒度是「一条 suite」。N09 把粒度抬到「**一批**」：正向面 + 负向对照 + 漂移探针
+ 外部基线对账，一条命令、一个退出码，仍然零领域语义。

## 新增模块

| 模块 | 职责 |
|---|---|
| `unattended.py` | 批级编排：逐 suite 执行并核对**预声明期望** → 漂移探针（回归检测标定）→ KPI 对账 → 批级判据 → 工件 + 3 张图 + 清单哈希回验。退出码 `0` 全过 / `1` 有判据不通过 / `3` 配置非法 / `4` 内部错误 |
| `surfaces.py` | **既有验证面**适配层：把仓库已有的纯函数机制（记账 ETL 数值工具／证据工件哈希链／回归分级器）作为 SUT 薄包装接入，参考解走**独立路径**（整数截断／`decimal` 定点／手写 `hashlib`），避免「同一个 bug 写两遍」的假验证 |
| `kpi_baseline.py` | 外部基线冻结：用 KPI ETL **同一条 SQL**（只读）读出 `unattended_cases` + 名字清单 + sha256；查不到 → `status="unavailable"`（不猜、不塞 0） |
| `campaign_report.py` | 报告数字注入：从 `campaign.json` + 各 suite 工件生成六要素 markdown 与机器可读验收 JSON（零手抄） |

## 批级配置（`campaigns/pf_default.json`）

```json
{
  "campaign": "pf_default",
  "drift_threshold": 0.03,
  "targets": {"unattended_cases": 75},
  "reconciliation": {"baseline": "artifacts/benchmark_runner/kpi_baseline.json"},
  "suites": [
    {"id": "fx_integration", "config": "...", "expect": "pass"},
    {"id": "fx_gate_band_fail", "config": "...", "expect": "fail", "control": true}
  ],
  "drift_probes": [
    {"id": "p02", "base": "fx_integration", "overhead": 0.02, "expect_status": "clean"},
    {"id": "p05", "base": "fx_integration", "overhead": 0.05, "expect_status": "warn"}
  ],
  "criteria": [
    {"metric": "steps_ok_ratio", "op": "==", "value": 1.0},
    {"metric": "unattended_cases", "op": ">=", "value": 75}
  ]
}
```

* `expect`：**运行前**写死的期望裁决（`pass`/`fail`）。实测不符 → 批级失败（负向对照没挂住也算失败）。
* `baseline`：`establish_if_missing`（缺才建，有则只比对）／`update`／`none`。每次都覆盖基线会让
  「回归检测」退化成「自己跟自己比」，因此缺省是"缺才建"。
* `drift_probes`：把基础 suite 的算例换成「数值逐位不变、`evals` 放大 `1+overhead`」的包装 SUT，
  用同一基线比对。判据门仍绿、只有回归检测开口——这才是「回归检测独立于判据门」的可测证据。
  **注意**：`evals` 在 `regression._NON_TRACKED` 里（规模参数不算退化），探针必须显式
  `track_metrics: ["evals"]`，否则会「静默干净」。探针工件单独写到 `<out>/probes/`，
  不污染主工件树的算例证据。
* `criteria`：批级判据，复用 `gate.py` 的算子与 fail-closed 语义。

## 命令

```bash
# 1) 冻结外部基线（只读 SQL；需要 TP_PG_PASSWORD/PGPASSWORD）
python3 -m tools.benchmark_runner.kpi_baseline --capture \
    --out artifacts/benchmark_runner/kpi_baseline.json

# 2) 跑完整批（正向 + 负向对照 + 漂移探针 + KPI 对账）
python3 -m tools.benchmark_runner.unattended \
    --campaign tools/benchmark_runner/campaigns/pf_default.json

# 3) 生成报告（数字注入）
python3 -m tools.benchmark_runner.campaign_report \
    --run artifacts/benchmark_runner/campaign/<run_id> \
    --md docs/reports/P167W2B_N09_unattended_campaign.md \
    --json docs/reports/P167W2B_N09_campaign_report.json
```

## 对账口径（不改上游 ETL）

```
unattended_cases = 外部基线（KPI ETL DB 代理口径，SQL 溯源、冻结工件）
                 + 引擎增量（engine-verified distinct case_id：判据通过 + sha256 链完整）
                 − 与基线 id 重叠（显式扣除，不重复计数）
```

基线不可用 → 对账 `status="unavailable"`、`verdict="fail"`（「没比过」≠「达标」）。
若上游把该 KPI 改为以引擎工件为权威源，应整树 `kpi_bridge.collect()` 计数；两种口径不可混写。

## 测试

```bash
python3 -m pytest tests/test_benchmark_runner_p167w2b_n09.py -q     # 34 passed
```

覆盖：campaign 配置 fail-closed（未知期望/无判据/重复 id/探针悬空）、探针构造与分级映射、
对账 fail-closed 与重叠扣除、基线 SQL 与 ETL 定义逐字一致、`psql` 缺失时基线不可用、
既有验证面语义（小时桶/比率/百分比/摘要不变式/篡改检出/回归分级）、判据带内通过 / 带外必挂、
端到端 campaign（含负向对照挂住、工件哈希回验、PNG 可打开、报告数字来自工件）、零领域语义守卫。
