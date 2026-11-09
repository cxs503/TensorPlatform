# m8263 · P170W2-N10 阈值口径注册表 v2 与派生一致性门

本节点的可发表报告由运行器程序化生成，**单一事实源**在：

* `artifacts/criteria_governance_v2/REPORT.md`（论文级六要素报告）
* `artifacts/criteria_governance_v2/report.json`（C1–C7 判据数字 + 产物路径）
* `artifacts/criteria_governance_v2/registry.json`（67 条判据全量注册表，含取证审计链）
* `artifacts/criteria_governance_v2/coverage_ledger.json`（有来源/可派生/可裁占比）
* `artifacts/criteria_governance_v2/drift_pins.json`（CI 钉桩：语义哈希 + 出处绑定哈希）
* `artifacts/criteria_governance_v2/drift_controls.json`（篡改 20/20、注释 0/20、出处修订正控）
* `artifacts/criteria_governance_v2/figures/`（4 张图）

复现：

```bash
python3 -m tools.criteria_governance.gen_synthetic_bundle
python3 tools/criteria_governance/run_v2.py
python3 -m pytest tests/test_criteria_governance_v2.py tests/test_criteria_governance.py -q
```

判据一览：C1 现网抽样 50 条 ≥30、source_type 缺失 0；C2 5/5 条声明阶偏差 ≤0.3（最大 0.0093）；
C3 阈值 ±10% 注入 20/20 判红；C4 纯注释/排版 0/20 误报；C5 3/3 条 `threshold(h)` 在 h/2、h/4 比值 ∈[0.5,2]；
C6 有来源 100% / 可派生 20.9% / 可裁 28.4%；C7 阈值=实测 → 降级 empirical 且禁止入可裁集合。
