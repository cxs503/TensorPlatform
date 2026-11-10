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

退出码：`0` 全过且无回归 · `1` 判据门不通过 · `2` 回归漂移告警 · `3` 配置非法 · `4` 内部错误。

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

## fail-closed 语义（本引擎最重要的性质）

* 算例**没写判据** → 运行期判 FAIL（`__none__:__no_criteria__`），绝不静默通过；
* suite**没写 `suite_criteria`** → 配置非法（退出码 3）；
* 指标缺失 / NaN / inf / bool / 字符串 → FAIL；
* 基线缺失 → 回归状态 `no_baseline`，**不是** "clean"（"没比过"≠"没退化"）；
* 只有 `manifest.json` 存在（哈希链完整）的 run 才计入 KPI。

## 证据链

`manifest.json` 记录每个工件（逐算例 JSON、`result.json`、PNG）的 sha256 + 字节数，
外加 `config_sha256` / `git_sha` / `engine_version`；每个算例 JSON 自带 `artifact_sha256`。
报告里的任何数字都可沿 `manifest → 工件 → config_sha256 → git_sha` 回溯（零手抄的前提）。

## 测试

```bash
python3 -m pytest tests/test_benchmark_runner_p167w2_n08.py -q    # 22 passed
```

测试覆盖判别力（负对照必挂、漂移 +5%/+15% 必报）与特异性（+2% 不报、机器零不误报），
而不只是"跑通"。