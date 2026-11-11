# P167W2B-N09 平台自主基准测试引擎成熟化：无人值守批级 campaign + 批级判据门 + 既有验证面回归 + KPI 对账

> 本文件由 `tools/benchmark_runner/campaign_report.py` 从 `artifacts/benchmark_runner/campaign/20261110T2355Z/campaign.json` 与各 suite 工件**程序化注入**（零手抄）；证据链 = `manifest.json → 各 suite manifest.json → cases/*.json → config_sha256 → git_sha`。

## 关键结论（全部由工件注入）

| 结论项 | 值 | 出处 |
|---|---|---|
| 批级判据通过 | **True** | `campaign.json:passed` |
| 步骤符合预声明期望 | 12.0/12.0（ratio=1） | `campaign.json:metrics` |
| 负向对照挂住 | 4/4 | `campaign.json:metrics` |
| 真实既有验证面无人值守算例（本单新增） | 14 | `campaign.json:reconciliation.delta_by_suite` |
| 无人值守算例：外部基线 + 引擎增量 = 合计 | 65 + 29 = **94.0**（目标 75.0） | `campaign.json:reconciliation` |
| 基线/增量 id 重叠（重复计数） | 0 | `campaign.json:reconciliation.overlap_ids` |
| 回归检测漂移探针 | p02:clean→code0、p05:warn→code1、p16:critical→code2 | `campaign.json:probes` |
| 证据链破损数 | 0 | `campaign.json:metrics` |

## ① 案例介绍（被检验对象＝引擎本身）

本单是**平台共性能力**节点，不含应用域算例（铁律：域词条/域判据/域算例不进 TensorPlatform）。被检验的不是任何物理场，而是「一批 benchmark 能不能在无人值守条件下自己跑完、自己裁决、自己留证、自己发现退化」这套**机制**。

三类被检对象，全部用确定性、纯函数、可闭式对照的算例，且配置里只写点分路径：

| 族 | 作用 | suite |
|---|---|---|
| 合成近似算法（正向面） | SUT 分辨率可调、参考解为闭式解，检验判据门 | `fx_integration` `fx_series` `fx_rootfinding` |
| 判据门带内/带外标定 | 预声明 3% 容忍；实测 ≈1% 必过、≈5% 必挂（fail-closed） | `fx_gate_band_pass` `fx_gate_band_fail` |
| **仓库既有验证面**（本单新增） | 把平台已有机制（记账 ETL 数值工具／证据工件哈希链／回归分级器）作为 SUT 无人工介入重跑 | `pf_surfaces`（14 算例） |
| 故障注入负向对照 | 无判据、欠分辨率、预算耗尽、挂死超时——都必须挂住 | `fx_negative_control` `fx_budget_negative` `fx_timeout_negative` |

**逐 suite 期望与实测**（`expect` 是运行前写死的，不是看到结果才写的）：

| suite | 期望 | 实测 verdict | 退出码 | 算例 通过/总 | max rel.error | 回归状态 |
|---|---|---|---|---|---|---|
| `fx_integration` | pass | PASS | 0 | 5/5 | 1.359e-06 | no_baseline |
| `fx_series` | pass | PASS | 0 | 4/4 | 9.766e-04 | no_baseline |
| `fx_rootfinding` | pass | PASS | 0 | 4/4 | 2.700e-07 | no_baseline |
| `pf_surfaces` | pass | PASS | 0 | 14/14 | 0 | no_baseline |
| `fx_gate_band_pass` | pass | PASS | 0 | 2/2 | 0.015625 | no_baseline |
| `fx_gate_band_fail` | fail | FAIL | 1 | 0/2 | 0.0471013 | no_baseline |
| `fx_negative_control` | fail | FAIL | 1 | 0/2 | 0.03125 | no_baseline |
| `fx_budget_negative` | fail | FAIL | 1 | 0/2 | — | no_baseline |
| `fx_timeout_negative` | fail | FAIL | 1 | 1/2 | 0 | no_baseline |

## ② 怎么做的

模块（全部落在 `tools/benchmark_runner/`，零应用域词条）：

| 模块 | 职责 |
|---|---|
| `runner.py`（N08 既有） | 单 suite：配置驱动执行 → 判据门 → 工件 → 回归；退出码 0/1/2/3/4/5 |
| `gate.py`（N08 既有） | 判据门，fail-closed（无判据/缺指标/NaN/inf/bool/未知算子 → FAIL） |
| `artifact.py`（N08 既有） | 逐算例 JSON + manifest sha256 链 + 落盘后立即回验 |
| `regression.py`（N08 既有） | 与基线逐指标比相对漂移，>3% warn，>3×3% critical |
| **`unattended.py`（本单新增）** | **批级编排**：逐 suite 执行 → 期望裁决 → 漂移探针 → KPI 对账 → 批级判据 → 证据工件 + 3 张图；一个退出码 |
| **`kpi_baseline.py`（本单新增）** | 冻结外部基线：KPI ETL 同一条 SQL 读出 + 名字清单 + sha256，查不到即 `unavailable`（不猜） |
| **`surfaces.py`（本单新增）** | 既有验证面适配层：薄包装 + **独立参考解**（整数截断／decimal 定点／手写 hashlib），不复用 SUT 算法路径 |
| **`campaign_report.py`（本单新增）** | 报告数字注入（本文件即它的输出） |

流程（一条命令，无人工判读）：

```bash
python3 -m tools.benchmark_runner.kpi_baseline --capture \
    --out artifacts/benchmark_runner/kpi_baseline.json
python3 -m tools.benchmark_runner.unattended \
    --campaign tools/benchmark_runner/campaigns/pf_default.json
```

漂移探针的构造（回归检测**独立于**判据门的证据）：同一积分算例换成「数值逐位不变、`evals` 放大 (1+overhead)」的包装 SUT（`p02` +2%→期望 clean、`p05` +5%→期望 warn、`p16` +16%→期望 critical）。阈值 3%、critical 阈值 9%：判据门仍绿（数值没变），只有回归检测该开口——两个门的分工因此可测。

## ③ 计算结果（数字全部由工件注入）

### 3.1 无人值守算例逐条证据（判据通过且哈希链完整者才计入）

| suite | case | inputs | measured | reference | rel.error | artifact_sha256(前12) |
|---|---|---|---|---|---|---|
| `fx_gate_band_pass` | `gb_tail_p0097` | `{"ratio": 0.9, "terms": 44}` | 8.91272 | 9 | 0.00969774 | `5d492f348d17` |
| `fx_gate_band_pass` | `gb_tail_p0156` | `{"ratio": 0.5, "terms": 6}` | 0.984375 | 1 | 0.015625 | `4aa4f7d7ca6b` |
| `fx_integration` | `fx_int_cos_n16` | `{"func": "cos", "a": 0.0, "b": 2.0, "n": 16}` | 0.909299 | 0.909297 | 1.359e-06 | `15a27c803edd` |
| `fx_integration` | `fx_int_cubic_n64` | `{"func": "cubic", "a": 0.0, "b": 2.0, "n": 64}` | 2 | 2 | 0 | `417e545ee433` |
| `fx_integration` | `fx_int_exp_n64` | `{"func": "exp", "a": 0.0, "b": 1.0, "n": 64}` | 1.71828 | 1.71828 | 3.311e-10 | `90af5b64e7dc` |
| `fx_integration` | `fx_int_sin_n128` | `{"func": "sin", "a": 0.0, "b": 1.5, "n": 128}` | 0.929263 | 0.929263 | 1.048e-10 | `2f0e1d657132` |
| `fx_integration` | `fx_int_sin_n32` | `{"func": "sin", "a": 0.0, "b": 1.5, "n": 32}` | 0.929263 | 0.929263 | 2.683e-08 | `de4d0b6555e6` |
| `fx_rootfinding` | `fx_root_sqrt10_tol1e12` | `{"c": 10.0, "lo": 3.0, "hi": 4.0, "tol": 1e-12}` | 3.16228 | 3.16228 | 4.071e-13 | `9e1292d69c42` |
| `fx_rootfinding` | `fx_root_sqrt2_tol1e12` | `{"c": 2.0, "lo": 1.0, "hi": 2.0, "tol": 1e-12}` | 1.41421 | 1.41421 | 1.117e-12 | `806914e311cb` |
| `fx_rootfinding` | `fx_root_sqrt2_tol1e6` | `{"c": 2.0, "lo": 1.0, "hi": 2.0, "tol": 1e-06}` | 1.41421 | 1.41421 | 2.700e-07 | `a5bf893f89b0` |
| `fx_rootfinding` | `fx_root_sqrt3_tol1e9` | `{"c": 3.0, "lo": 1.0, "hi": 2.0, "tol": 1e-09}` | 1.73205 | 1.73205 | 3.335e-10 | `54fd52c7b18f` |
| `fx_series` | `fx_series_r025_n20` | `{"ratio": 0.25, "terms": 20}` | 0.333333 | 0.333333 | 9.094e-13 | `6ebda8a62b8e` |
| `fx_series` | `fx_series_r050_n10` | `{"ratio": 0.5, "terms": 10}` | 0.999023 | 1 | 9.766e-04 | `73c7f34a8dc9` |
| `fx_series` | `fx_series_r050_n20` | `{"ratio": 0.5, "terms": 20}` | 0.999999 | 1 | 9.537e-07 | `98fda4985ced` |
| `fx_series` | `fx_series_r080_n40` | `{"ratio": 0.8, "terms": 40}` | 3.99947 | 4 | 1.329e-04 | `426778cd2e10` |
| `fx_timeout_negative` | `fx_healthy_after_timeout` | `{"func": "cubic", "a": 0.0, "b": 2.0, "n": 16}` | 2 | 2 | 0 | `7b464417dbb2` |
| `fx_timeout_negative` | `fx_stall_beyond_timeout` | `{"seconds": 30.0}` | — | — | — | `68708daa64f6` |
| `pf_surfaces` | `pf_artifact_digest_order_invariance` | `{}` | 1 | 1 | 0 | `ea8b2bfd6127` |
| `pf_surfaces` | `pf_artifact_digest_payload_sensitivity` | `{}` | 1 | 1 | 0 | `2305441a76b8` |
| `pf_surfaces` | `pf_artifact_manifest_tamper_detected` | `{}` | 1 | 1 | 0 | `d4366258688d` |
| `pf_surfaces` | `pf_kpi_hour_epoch_zero` | `{"ts_iso": "1970-01-01T00:00:00+00:00"}` | 0 | 0 | 0 | `7ebc68c59cfc` |
| `pf_surfaces` | `pf_kpi_hour_offset_west` | `{"ts_iso": "2026-03-01T23:59:59-05:00"}` | 1.772e+09 | 1.772e+09 | 0 | `49af1dff4897` |
| `pf_surfaces` | `pf_kpi_hour_utc_min` | `{"ts_iso": "2026-11-10T05:37:12.345678+00:00"}` | 1.794e+09 | 1.794e+09 | 0 | `29fe2d11db82` |
| `pf_surfaces` | `pf_kpi_pct_missing` | `{"value": null, "ndigits": 1}` | 0 | 0 | 0 | `06d5a64aebd3` |
| `pf_surfaces` | `pf_kpi_pct_round` | `{"value": 0.12345, "ndigits": 1}` | 12.3 | 12.3 | 0 | `0ef2f949e69b` |
| `pf_surfaces` | `pf_kpi_ratio_eighths` | `{"num": 7, "den": 8, "ndigits": 3}` | 0.875 | 0.875 | 0 | `de79e003d52c` |
| `pf_surfaces` | `pf_kpi_ratio_third` | `{"num": 1, "den": 3, "ndigits": 4}` | 0.3333 | 0.3333 | 0 | `c98851d07483` |
| `pf_surfaces` | `pf_kpi_ratio_zero_denominator` | `{"num": 5, "den": 0, "ndigits": 4}` | 0 | 0 | 0 | `0cff5acd9a2d` |
| `pf_surfaces` | `pf_regression_clean_within_tolerance` | `{"current_value": 1.02, "threshold": 0.03}` | 0 | 0 | 0 | `6cc57e309f03` |
| `pf_surfaces` | `pf_regression_critical_far_above` | `{"current_value": 1.16, "threshold": 0.03}` | 2 | 2 | 0 | `deede16f9088` |
| `pf_surfaces` | `pf_regression_warn_above_tolerance` | `{"current_value": 1.05, "threshold": 0.03}` | 1 | 1 | 0 | `f24cd99be6ab` |

### 3.2 KPI 对账（外部基线 + 引擎增量，重叠显式扣除）

| 项 | 值 | 出处 |
|---|---|---|
| 外部基线（KPI ETL DB 代理口径） | 65 | `artifacts/benchmark_runner/kpi_baseline.json` sha256=`e3542dbb70340cde` |
| 基线捕获时刻 | 2026-11-10T23:35:55+00:00 | 同上 |
| 基线名字清单哈希 | `a97b9ce40e1c4ee73b286fba2ae6fd3337f70e2f9834d7122cf163a4f1f254cb` | 同上 |
| 引擎增量 · `fx_gate_band_pass` | 2 | `campaign.json:reconciliation.delta_by_suite` |
| 引擎增量 · `fx_integration` | 5 | `campaign.json:reconciliation.delta_by_suite` |
| 引擎增量 · `fx_rootfinding` | 4 | `campaign.json:reconciliation.delta_by_suite` |
| 引擎增量 · `fx_series` | 4 | `campaign.json:reconciliation.delta_by_suite` |
| 引擎增量 · `pf_surfaces` | 14 | `campaign.json:reconciliation.delta_by_suite` |
| 引擎增量合计 | 29 | `campaign.json:reconciliation` |
| 与基线 id 重叠（若>0 已扣除） | 0 | 同上 |
| **合计** | **94.0** | 同上 |
| 目标 | 75.0 | `campaigns/pf_default.json:targets` |

基线 SQL（与 `tools/guide_kpi_ledger.py` 的 KPI 定义**逐字一致**，本单不改上游口径）：

```sql
SELECT count(DISTINCT substring(goal from '案例 ([a-zA-Z0-9_]+)（')) AS cases FROM tensor_dev_missions WHERE goal LIKE '%案例 %（Tensor%' AND status='succeeded'
```

### 3.3 回归检测漂移探针（特异性 + 判别力）

| 探针 | 注入漂移 | 观测漂移 | 期望分级 | 实测分级 | 判据门 | 结论 |
|---|---|---|---|---|---|---|
| `p02` | 2% | 2.00% | clean | code 0 | PASS | PASS |
| `p05` | 5% | 5.00% | warn | code 1 | PASS | PASS |
| `p16` | 16% | 16.00% | critical | code 2 | PASS | PASS |

### 3.4 批级判据（预声明，机器裁）

| 判据 | 阈值 | 实测 | 通过 |
|---|---|---|---|
| `steps_ok_ratio` == | 1 | 1 | PASS |
| `negative_controls_held` == | 4 | 4 | PASS |
| `integrity_failures` == | 0 | 0 | PASS |
| `drift_specificity_ok` == | 1 | 1 | PASS |
| `drift_power_ok` == | 1 | 1 | PASS |
| `unattended_cases_delta` >= | 10 | 29 | PASS |
| `unattended_cases` >= | 75 | 94 | PASS |

## ④ 结果图

![campaign_kpi_vs_target.png](artifacts/benchmark_runner/campaign/20261110T2355Z/figures/campaign_kpi_vs_target.png)

*左：无人值守算例 KPI（外部基线 + 引擎增量 vs 目标线）；右：逐 suite 实测裁决与预声明期望的一致性（负向对照必须挂住）*（sha256=`503822d5edcf4c42`，81483 bytes）

![regression_probes.png](artifacts/benchmark_runner/campaign/20261110T2355Z/figures/regression_probes.png)

*回归检测标定：注入 2%/5%/16% 工作量漂移的观测漂移与 warn/critical 阈值线（左绿=特异性，红=判别力）*（sha256=`c4e0325504820adf`，44835 bytes）

![suite_error_vs_threshold.png](artifacts/benchmark_runner/campaign/20261110T2355Z/figures/suite_error_vs_threshold.png)

*跨 suite 汇总：各算例实测相对误差 vs 预声明容忍（对数轴）*（sha256=`98e3152cc95a58ad`，202559 bytes）


## ⑤ 注意点（踩过的坑与适用范围）

1. **`evals` 在回归器里默认不跟踪**（`_NON_TRACKED`：规模参数不算退化）。漂移探针必须显式 `track_metrics=["evals"]`，否则探针会「静默干净」——第一次跑就踩到，配置里已写死。

2. **同一 case_id 在漂移探针里复用**（回归对比按 id 配对），探针工件因此单独写到 `artifacts/benchmark_runner/probes/`，否则会把主工件树里正向算例的证据换成探针证据。

3. **KPI 65 是外部（DB 代理）口径**，本单不改上游 ETL：`kpi_baseline.py` 只冻结「同一 SQL 在捕获时刻的值 + 名字清单」，引擎增量按 id 求差并入，重叠显式扣除。若上游把 ETL 改成以引擎工件为权威源，应改为整树 `kpi_bridge.collect()` 计数，两种口径不可混写。

4. **基线是双向的**：`establish_if_missing` 只在基线缺失时建立，已存在则只比对——否则每次都覆盖基线，「回归检测」会退化成「自己跟自己比」。

5. **适用范围**：本引擎判的是「数值/机制一致性」，不是物理正确性。把真实求解器接进来只需改配置里的点分路径（引擎代码零改动），但域判据（误差阈值该多少）必须由领域仓以配置注入，平台不得内置。

6. **不确定项**：`pf_surfaces` 的 14 个算例是「仓库既有纯函数面」，不是全部验证面；带 IO/数据库的机制面（如端到端 API 门）本轮未纳入，因为它们需要外部服务，不是纯无人值守可复现的。


## ⑥ 误差判定

| suite | 算例数 | 实测 max rel.error | 预声明容忍 | 是否 <3% | 是否 ≤容忍 |
|---|---|---|---|---|---|
| `fx_integration` | 5 | 1.359e-06 | 1.000e-05 | 是 | 是 |
| `fx_series` | 4 | 9.766e-04 | 0.01 | 是 | 是 |
| `fx_rootfinding` | 4 | 2.700e-07 | 1.000e-06 | 是 | 是 |
| `pf_surfaces` | 14 | 0 | 1.000e-12 | 是 | 是 |
| `fx_gate_band_pass` | 2 | 0.015625 | 0.03 | 是 | 是 |
| `fx_gate_band_fail` | 0 | — | 0.03 | — | — |
| `fx_negative_control` | 0 | — | 1.000e-12 | — | — |
| `fx_budget_negative` | 0 | — | 1.000e-12 | — | — |
| `fx_timeout_negative` | 1 | 0 | 1.000e-12 | 是 | 是 |

**关键量答复**：全部有参考解的算例中最大相对误差 = `0.015625`，是否 <3% = **是**。负向对照（欠分辨率/无判据/预算耗尽/挂死）按设计**必须**超阈值挂住，它们的「误差大」是预期的判别力证据，不计入上表。


## 审计链

* campaign 配置 sha256 = `5d2997b9d51994b11209a4a39b61556e48d0c27d8e2d0690509d71b25fae1f39`（`tools/benchmark_runner/campaigns/pf_default.json`）
* campaign run_id = `20261110T2355Z`，引擎版本 = `1.1.0`，git_sha = `f133b92808f66409f6b1db5c1d7bca754b8c15b2`
* 工件清单 = `artifacts/benchmark_runner/campaign/20261110T2355Z/manifest.json` sha256=`3a2abe0a17c1d643e527391d144d6ca44f4a4b0895e339b7c4c230ae649291d7`（逐文件 sha256 + manifest_digest）
* 各 suite 证据 = `artifacts/benchmark_runner/<suite>/<run_id>/manifest.json`
  * `fx_integration` manifest sha256=`8e67ad8cf9614e9c2e6be3efdfc2f564743aacc4eaaf9218ba8eb865e860a1e5`（integrity ok=True，checked=9）
  * `fx_series` manifest sha256=`d501b36a29e7ffce2cabb1f81e13ef955c7a8a3f5f3435df36ef33b12b74810d`（integrity ok=True，checked=8）
  * `fx_rootfinding` manifest sha256=`eaabf21d89767547754f106e4abfc9103ed0dd124b912355fcb38467ca01ded0`（integrity ok=True，checked=8）
  * `pf_surfaces` manifest sha256=`78d7f982ab5af51f2e8d0ad83f5a52c9743ef6cf665a4ae43dbef1c186332785`（integrity ok=True，checked=18）
  * `fx_gate_band_pass` manifest sha256=`3911ad5f20f970f295187b82b5dbff23bd5fb1d72bda739e3e1b4a2015d260f0`（integrity ok=True，checked=6）
  * `fx_gate_band_fail` manifest sha256=`2bd19c1b5c916b054969649c94d6e1a93054eed635bc8b5f4e1088a52a5b542b`（integrity ok=True，checked=6）
  * `fx_negative_control` manifest sha256=`9b9b928aad084606d726fe78a912d2d90cd36db55432c09ae7c8367037135674`（integrity ok=True，checked=6）
  * `fx_budget_negative` manifest sha256=`9f5d419674dcb11652dbe5596d685544d710fb5f8399abb56f4b34c11f6b7b12`（integrity ok=True，checked=6）
  * `fx_timeout_negative` manifest sha256=`059e7c885ffe228bb3e21ca08306fb19ad93738aff03075636259f28761c35ff`（integrity ok=True，checked=6）
