# 船舶平台能力证据门 capgate：把「手工证据等级」变成机器判据

*工件*: `artifacts/capgate/capgate.json`（生成于 2026-11-01T05:57:23）· *注册表快照*: `capabilities_snapshot_20261101.json` · *任务*: mission 1476

## ① 案例介绍（对象 / 参数）

平台 `platform_harness/capabilities.json` 是 21 项能力证据等级的**唯一声明源**——agent 侧 `audit_capabilities` 工具直接读它，所有「REAL/MOCK/STUB」判断、能力覆盖 KPI 都由它派生。它由人手维护，26 个字段里**没有任何机检指针**（无 `verified_by`、无探针、无基准出处）。历史上已实证「声明 REAL、实测伪造」（m1032 报告：敞水 KQ≡KT/10、ITTC-78 总阻力 +41%）。本单把这条证据链改成机器可判。

| 项 | 值 |
| --- | --- |
| 注册能力数 | 21 |
| 声明 REAL | 18 |
| 声明 REAL 且带 verified_by | 0 |
| 学科域数 | 9 |
| 在线探测端点数 | 21 |
| 端点已挂载 | 11 |

## ② 怎么做的（方法 / 流程 / 工具链）

五层探针，每层都有可机器裁的判据，纯标准库实现（`tools/capgate/capgate.py`，pytest 18 例 `tools/capgate/tests/test_capgate.py`，出图 matplotlib）：

1. **schema 门**：`REAL`/`WITHHELD` 必须声明 `verified_by`（探针 id），且 id 必须在探针表里存在，否则报 `UNVERIFIED_EVIDENCE` / `DANGLING_PROBE`。
2. **9 学科域覆盖矩阵**：能力 id → 学科域（阻力/推进/耐波/操纵/稳性/结构/振动噪声/绿色航行/冰区航行），判定 `SERVABLE_REAL`（REAL 且声明可达且探针未判未挂载）、`REGISTERED_ONLY`（有登记但不可服务）、`UNREGISTERED`（完全无能力位）。
3. **在线端点可达性探针**：对每个声明端点做一次只读探测（GET 直连 / POST 空体），404·405→`NOT_MOUNTED`，422→`MOUNTED_VALIDATION`，5xx→`MOUNTED_ERROR`；与 `reachable_direct` 冲突即 `REACH_DRIFT`。
4. **物理/解析探针**（离线纯函数，附基准出处）：敞水序列退化（KT/KQ 对 J 恒定＝伪造指纹，ITTC 7.5-02-03-01）、ITTC-1957 摩擦线（cf=0.075/(lgRe−2)²）、静水力四恒等式（TPC≡ρAwp/100、Δ≡ρ∇、Cb≡∇/(LBd)、**dΔ/dd≈ρAwp 作微-积分交叉检验**）、GM≡KB+BMt−KG、硬编码回声指纹。
5. **对账**：声明等级 × 探针判据 → `DRIFT_DECLARED_REAL_PROBE_FAIL` / `DRIFT_UNREACHABLE` / `UNVERIFIED` / `PROBE_PASS` / `CONSISTENT`。

## ③ 计算结果（数字表）

### 3.1 门禁总账（程序化注入）

| 指标 | 值 |
| --- | --- |
| n_capabilities | 21 |
| n_declared_REAL | 18 |
| n_declared_REAL_with_verified_by | 0 |
| verified_by_coverage | 0.0 |
| n_schema_findings | 18 |
| n_domains | 9 |
| domain_real_rate | 0.2222 |
| n_drift | 9 |
| n_unverified | 9 |
| n_probe_pass | 0 |
| reach_probe_count | 21 |
| reach_mounted | 11 |
| n_reach_conflicts | 10 |

### 3.2 9 学科域覆盖

| 学科域 | 注册能力 | 证据等级 | 可服务 | 判定 |
| --- | --- | --- | --- | --- |
| 阻力 | calc.resistance, shiphull.generate, geometry.generate | calc.resistance=REAL, shiphull.generate=REAL, geometry.generate=REAL | calc.resistance, shiphull.generate | **SERVABLE_REAL** |
| 推进 | propeller.open_water | propeller.open_water=REAL | — | **REGISTERED_ONLY** |
| 耐波 | — | — | — | **UNREGISTERED** |
| 操纵 | — | — | — | **UNREGISTERED** |
| 稳性 | calc.stability, geometry.hydro_curves | calc.stability=REAL, geometry.hydro_curves=REAL | calc.stability, geometry.hydro_curves | **SERVABLE_REAL** |
| 结构 | — | — | — | **UNREGISTERED** |
| 振动噪声 | misc.stubs | misc.stubs=STUB | — | **REGISTERED_ONLY** |
| 绿色航行 | misc.stubs | misc.stubs=STUB | — | **REGISTERED_ONLY** |
| 冰区航行 | misc.stubs | misc.stubs=STUB | — | **REGISTERED_ONLY** |

### 3.3 在线端点可达性（:8001 实测，21 端点）

- 已挂载 11 个 / 未挂载 10 个。
- **声明 `reachable_direct=true` 但实测 404 的能力 4 个**：`geometry.generate`, `geometry.verify_official`, `gov.stats`, `propeller.open_water`
- STUB 桶端点（振动/绿色/冰区/优化）实测**已挂载** 4 个：`/api/v1/green-shipping/efficiency`, `/api/v1/ice-navigation/route`, `/api/v1/optimization/mdo`, `/api/v1/vibration/predict`

| 端点 | 分类 |
| --- | --- |
| `ai.predict:/api/v1/advanced-ai/predict` | NOT_MOUNTED |
| `ai.predict:/api/v1/pinn/predict` | NOT_MOUNTED |
| `calc.resistance:/api/v1/calculations/resistance` | MOUNTED_VALIDATION |
| `calc.stability:/api/v1/calculations/stability` | MOUNTED_VALIDATION |
| `dev.gate_5415:/api/v1/geometry/generate` | NOT_MOUNTED |
| `geometry.generate:/api/v1/geometry/generate` | NOT_MOUNTED |
| `geometry.hydro_curves:/api/v1/geometry/hydro-curves/{ship_name}` | MOUNTED_ERROR |
| `geometry.list_models:/api/v1/geometry/categories` | NOT_MOUNTED |
| `geometry.list_models:/api/v1/geometry/models/{category}` | NOT_MOUNTED |
| `geometry.verify_official:/api/v1/geometry/generate` | NOT_MOUNTED |
| `gov.stats:/api/v1/data-governance/stats` | NOT_MOUNTED |
| `knowledge.docs:/api/v1/knowledge/books` | MOUNTED_OK |
| `knowledge.docs:/api/v1/knowledge/docs` | MOUNTED_OK |
| `methods.computation:/api/v1/computation/methods` | NOT_MOUNTED |
| `misc.stubs:/api/v1/green-shipping/efficiency` | MOUNTED_VALIDATION |
| `misc.stubs:/api/v1/ice-navigation/route` | MOUNTED_VALIDATION |
| `misc.stubs:/api/v1/optimization/mdo` | MOUNTED_VALIDATION |
| `misc.stubs:/api/v1/vibration/predict` | MOUNTED_VALIDATION |
| `propeller.open_water:/api/v1/propeller/open-water` | NOT_MOUNTED |
| `shiphull.generate:/api/v1/ship-hull/generate` | MOUNTED_VALIDATION |
| `stdmodels.lookup:/api/v1/standard-models/ships` | MOUNTED_OK |

### 3.4 探针自验证（合成自洽样本 + 历史缺陷回归夹具）

共 10 次探针运行：PASS 5 / FAIL 5（合成自洽样本必须全 PASS、历史缺陷回归夹具必须全 FAIL，误报漏报均为 0）。

| 探针 | 载体 | 判定 | 基准 | 关键量 |
| --- | --- | --- | --- | --- |
| propeller_series | propeller.open_water | **PASS** | ITTC 7.5-02-03-01 | ratio_spread=0.204053589832, KT_monotone_decreasing=True, eta_o_range=[0.130589, 0.742723] |
| propeller_series | propeller.open_water | **FAIL** | ITTC 7.5-02-03-01 | ratio_spread=0.0, KT_monotone_decreasing=True, eta_o_range=[0.159155, 1.114085] |
| ittc57_friction | calc.resistance | **PASS** | ITTC-1957 平板摩擦线 | n=3, max_rel_err=0.0, mean_rel_err=0.0 |
| ittc57_friction | calc.resistance | **FAIL** | ITTC-1957 平板摩擦线 | n=3, max_rel_err=0.41, mean_rel_err=0.41 |
| hydro_identities | geometry.hydro_curves | **PASS** | TPC/Δ/Cb 定义 + 阿基米德原理（微-积分交叉检验） | tpc_max_rel_err=0.0, disp_max_rel_err=0.0, cb_max_rel_err=0.0 |
| hydro_identities | geometry.hydro_curves | **FAIL** | TPC/Δ/Cb 定义 + 阿基米德原理（微-积分交叉检验） | tpc_max_rel_err=0.024390244, disp_max_rel_err=0.024390244, cb_max_rel_err=0.0 |
| gm_identity | calc.stability | **PASS** | 初稳性 GM=KB+BMt−KG（静水力解析恒等） | GM_reported=3.5, GM_from_identity=3.5, rel_err=0.0 |
| gm_identity | calc.stability | **FAIL** | 初稳性 GM=KB+BMt−KG（静水力解析恒等） | GM_reported=-3.0, GM_from_identity=3.5, rel_err=1.857143 |
| hardcode_echo | misc.stubs | **FAIL** | MOCK 指纹法（misc.stubs 桶） | n_runs=2, distinct_fingerprints=1, n_numeric_leaves=3 |
| hardcode_echo | misc.stubs | **PASS** | MOCK 指纹法（misc.stubs 桶） | n_runs=2, distinct_fingerprints=2, n_numeric_leaves=1 |

### 3.5 声明 vs 探针对账

| 能力 | 声明 | 探针 | 状态 |
| --- | --- | --- | --- |
| `ai.predict` | NOT_SUPPORTED | — | **DECLARED_ONLY** |
| `calc.resistance` | REAL | PASS,FAIL | **DRIFT_DECLARED_REAL_PROBE_FAIL** |
| `calc.stability` | REAL | PASS,FAIL | **DRIFT_DECLARED_REAL_PROBE_FAIL** |
| `dev.gate_5415` | REAL | — | **DRIFT_UNREACHABLE** |
| `dev.git` | REAL | — | **UNVERIFIED** |
| `dev.logs` | REAL | — | **UNVERIFIED** |
| `dev.nginx_audit` | REAL | — | **UNVERIFIED** |
| `dev.probe` | REAL | — | **UNVERIFIED** |
| `dev.restart` | REAL | — | **UNVERIFIED** |
| `dev.services` | REAL | — | **UNVERIFIED** |
| `geometry.generate` | REAL | — | **DRIFT_UNREACHABLE** |
| `geometry.hydro_curves` | REAL | PASS,FAIL | **DRIFT_DECLARED_REAL_PROBE_FAIL** |
| `geometry.list_models` | REAL | — | **DRIFT_UNREACHABLE** |
| `geometry.verify_official` | REAL | — | **DRIFT_UNREACHABLE** |
| `gov.stats` | REAL | — | **DRIFT_UNREACHABLE** |
| `knowledge.docs` | REAL | — | **UNVERIFIED** |
| `methods.computation` | NOT_SUPPORTED | — | **DECLARED_ONLY** |
| `misc.stubs` | STUB | FAIL,PASS | **CONSISTENT** |
| `propeller.open_water` | REAL | PASS,FAIL | **DRIFT_DECLARED_REAL_PROBE_FAIL** |
| `shiphull.generate` | REAL | — | **UNVERIFIED** |
| `stdmodels.lookup` | REAL | — | **UNVERIFIED** |

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **夹具 ≠ 在线复测**：3.4 的 FAIL 来自「历史缺陷回归夹具」（出处已在夹具 `source` 字段写明），用于证明探针抓假能力，**不构成对被审 API 的在线复测结论**；在线精度结论保持 WITHHELD。
2. **模板路径不能当常量探**：`/geometry/hydro-curves/{ship_name}` 直接探测返回 500（`船舶 'DTWH...' 不存在`——占位符被当成船名），因此 500 只能判 `MOUNTED_ERROR` 不能判功能可用；探针已按此口径编码。
3. **端口陈旧**：平台 :8002 已下线（m1412 复盘），但 `ops/tp_worker.py::_tool_ship_api` 仍先探 8002再回落 8001，每次工具调用白等一个连接超时——本单不改（超出白名单），记作 infra 待办。
4. **STUB 桶的真实状态被低估**：`misc.stubs` 的 4 个端点实测都已挂载（能过 FastAPI 校验），证据等级仍为 STUB 的意思应是「有壳无真算法」；这要求后续用物理探针逐个验，不能靠挂载与否判断。
5. **域覆盖口径**：一个学科域只要有 1 个 REAL+可达能力即记 SERVABLE_REAL，这是**乐观上界**；按「该学科全部关键量都可算」的严格口径只会更低。

## ⑥ 误差判定（逐项 + 显式回答「是否<3%」）

本单是审计/门禁类工件，无可对标的连续场量基准；可判定的误差是恒等式残差与检出率：

| 关键量 | 基准 | 实测残差 | 是否 <3% |
| --- | --- | --- | --- |
| TPC/Δ/Cb 三恒等式 | 解析定义（矩形船型解析解） | 0.00e+00 / 0.00e+00 / 0.00e+00 | **是** |
| dΔ/dd = ρ·Awp（微-积分交叉） | 解析恒等 | 0.00e+00 | **是** |
| GM = KB+BMt−KG | 初稳性解析恒等 | 0.00e+00 | **是** |
| cf vs ITTC-1957 | ITTC-1957 摩擦线 | 0.00e+00 | **是** |
| 历史缺陷检出率（回归夹具） | 5 个已知缺陷样本 | 检出 5/5，误报 0/5 | **是（100% 检出）** |

**显式回答**：探针自身对解析基准的残差全部为 0（机器精度），**是 <3%**；检出率 100%、误报 0。但**被审平台 API 的在线物理精度本轮未测**（探针只做了可达性与自身判据校验），该结论维持 WITHHELD，不作粉饰。

## ④ 图件

`artifacts/capgate/capgate.png`（随报告内嵌）：左图 9 学科域覆盖判定，右图证据链机检覆盖（声明 REAL / 带 verified_by / 域可服务 / 漂移 / 未验证）。
