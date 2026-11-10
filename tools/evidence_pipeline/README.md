# tools/evidence_pipeline — 证据治理全链路（domain-neutral）

把平台此前三块孤立能力合成一条 **fail-closed** 机器可裁的证据链：

```
benchmark 配置 → 生成器执行 → gate 判定 → 工件 → 入库门（身份+重放） → 回归检测 → 哈希链 provenance
```

* **自主基准引擎**：`runner.py` 消费配置，自动执行 `generators/<node>.py`，`gate.py` 判定
  `pass/fail`，`artifact.py` 产出工件。
* **入库门**：`artifact.py --gate` 对工件做 A1–A9 九项检查（schema / 自摘要 / 状态 / 判据自洽 /
  payload 摘要 / **生成器内容哈希** / **配置摘要** / **完整重放**），任一失败即拒绝，不修复、不放行。
* **跨库守恒性**：`conservation_gate.py` 是配置里的一种 gate 类型，由 runner 统一调度；四个指标
  （零阶矩 / 一阶矩 / 正性 / 超调）的定义**委托** `tools.coupling.conservation`，不二次实现。
* **回归闭环**：`regression.py` 对比已入册基线，漂移超阈值自动重跑，分类
  `stable / no_baseline / drift_reproducible / drift_unverified / drift_nondeterministic`；
  重新入册必须显式 `--accept-drift`。
* **provenance**：复用 `tools.coupling.provenance.ExchangeLedger`，每阶段一条哈希链 entry，
  写记录前重算 `chain_valid`。

## 用法

```bash
# 跑一个 benchmark（首次运行用 --update-baseline 入册基线）
python3 -m tools.evidence_pipeline.runner \
    --config config/evidence_pipeline/benchmarks/syn-alpha-quadrature.json \
    --out-dir artifacts/P167W2-N11/runs --update-baseline

# 复核一个工件（入库门，含生成器重放）
python3 -m tools.evidence_pipeline.artifact \
    --gate artifacts/P167W2-N11/runs/syn-alpha-quadrature.artifact.json \
    --config config/evidence_pipeline/benchmarks/syn-alpha-quadrature.json

# 全判据验收（C1–C10）+ 论文级报告 + 图
python3 -m tools.evidence_pipeline.acceptance --json artifacts/P167W2-N11/acceptance.json
python3 -m tools.evidence_pipeline.gen_report
```

退出码契约：`0` 通过 / `2` 判据失败 / `3` 入库门拒绝 / `4` 回归漂移 / `5` 生成器失败 /
`6` 拒绝静默重新入册。

## 配置格式

```json
{
  "node": "<node id>",
  "generator": "tools/evidence_pipeline/generators/<file>.py",
  "params": {"...": "..."},
  "timeout_s": 120,
  "gates": [
    {"id": "quality", "type": "numeric",
     "thresholds": {"metric_a": {"min": 1.9}, "metric_b": {"max": 0.002}}},
    {"id": "conservation", "type": "conservation",
     "fields": {"src": "src", "dst": "dst"},
     "thresholds": {"sum_rel_residual": 1e-14, "moment_rel_residual": 1e-12,
                    "nonneg_violation": 1e-12, "overshoot_ratio": 1.02}}
  ],
  "regression": {"enabled": true, "baseline": "artifacts/.../<node>.json",
                 "tol": 1e-6, "reruns": 2, "floors": {"metric_a": 1e-9}}
}
```

* `numeric`：`min` / `max` 至少一项；配置点名的指标在 payload 里缺失 = **失败**（不是静默通过）。
* `conservation`：四个阈值必须全给；`overshoot_ratio` 是**比值上界**（≥ 1.0），
  其余三项是残差容差。
* `regression.floors`：近零指标的相对漂移分母下限（否则 `|Δ|/|base|` 会发散）。

## 生成器契约

生成器是独立脚本，被以子进程方式执行（确定性环境、硬超时），必须写出：

```json
{"schema": "evidence-pipeline/payload/1", "node": "<node id>",
 "metrics": {"<name>": <finite float>},
 "fields": {"src": {"mesh": {"lo": [[...]], "hi": [[...]], "name": "..."},
                    "values": [...], "kind": "density", "name": "..."},
            "dst": {...}},
 "notes": "..."}
```

## 测试与验收

```bash
python3 -m pytest tools/evidence_pipeline/tests -q      # 39 cases
python3 -m tools.evidence_pipeline.acceptance           # C1–C10
```

报告：`docs/P167W2-N11-evidence-pipeline.md`（由 `gen_report.py` 从工件生成，数字零手抄）。