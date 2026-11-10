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