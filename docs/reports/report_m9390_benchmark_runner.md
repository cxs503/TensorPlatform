# P167W2-N08 平台自主基准测试引擎：无人值守 runner + fail-closed 判据门 + 证据工件 + 回归检测

> 本文件数字由 `tools/benchmark_runner/report_gen.py` 从 `artifacts/benchmark_runner/**` 程序化注入（零手抄）；证据链 = `manifest.json → cases/*.json → config_sha256 → git_sha`。

## 关键结论（全部由工件注入）

| 结论项 | 值 | 出处 |
|---|---|---|
| 正向算例通过 | 13 / 13 | §3.2 `result.json` |
| 正向算例集最大相对误差 | 9.766e-04 | §3.1 |
| 是否 <3% | **是** | 由上一行 max rel. error 与 3% 比较得出（机器判定）|
| 负对照被拦住 | 2 / 2 判 FAIL | §3.5 |
| 回归检测特异性（+2%） | 退出码 0 | §3.3 |
| 回归检测判别力（+5% / +15%） | 退出码 2 / 2 | §3.3 |
| 无人值守算例（去重、带哈希链） | 13 | §3.4 |
| Simpson 实测收敛阶（n=32→128） | 4.00（理论 4） | §3.1 两算例误差之比 |

## ① 案例介绍

本单是**平台共性能力**节点，不含应用域算例（铁律：域词条/域判据/域算例不进 TensorPlatform）。
被检验的对象是「基准测试引擎」本身：它能不能在**无人值守**条件下，把
「跑算例 → 判通过/不通过 → 留证据 → 跟基线比」这四步全部机器化，且**不空转**。

为此设计了三族**合成数学**算例（SUT = 分辨率/迭代可调的近似算法，参考解 = 闭式解）：

| 族 | SUT | 参考解 | 可调分辨率 | 算例数 |
|---|---|---|---|---|
| `fx_integration` | 复合 Simpson 数值积分 | 初等函数闭式积分 | 子区间数 n | 5 |
| `fx_series` | 几何级数部分和 | r/(1−r) 极限 | 项数 terms | 4 |
| `fx_rootfinding` | 二分求根 x²−c | √c | 容差 tol | 4 |
| `fx_negative_control` | 上述的**欠分辨率**变体 + **无判据**算例 | 同上 | — | 2（必须 FAIL） |

另有 3 个**回归注入** suite（`fx_integration_drift_p02/p05/p15`）：数值逐位不变，只把求解工作量
`evals` 放大 2% / 5% / 15%，用来分别检验回归检测器的**特异性**（不误报）与**判别力**（该报就报）。

## ② 怎么做的

引擎落在 `tools/benchmark_runner/`（四件套 + 两个辅助器）：

| 模块 | 职责 |
|---|---|
| `runner.py` | 配置驱动无人值守执行器：`"module:function"` 点分路径注入 SUT/参考解 → 执行 → 派生 `abs_error`/`rel_error` → 判据门 → 工件 → 回归检测；退出码 0/1/2/3/4 |
| `gate.py` | 判据门。判据是**数据**（算子 `<= < >= > == != in_range monotonic finite`），**fail-closed**：无判据/缺指标/NaN/inf/bool/未知算子 → FAIL |
| `artifact.py` | 工件：逐算例 JSON（inputs/outputs/metrics/checks/provenance/`artifact_sha256`）+ suite `result.json` + PNG + `manifest.json`（逐文件 sha256 + `config_sha256` + `git_sha` + 引擎版本）|
| `regression.py` | 与基线逐指标比相对漂移（缺省 3%），分级 warn / critical（>3×）；另报 pass→fail 翻转、算例消失、新增算例 |
| `kpi_bridge.py` | 只读工件树，按 `case_id` 去重统计「通过且哈希链完整」的无人值守算例数（`unattended_cases` 口径）|
| `report_figure.py` | 跨 suite 汇总图（误差 vs 预声明阈值、判据余量）|

**零领域语义的机制保证**：引擎只认点分路径 + 字典；接真实求解器只需改配置，引擎代码一行不动。
`grep -nwE` 审计引擎源码与配置，未出现任何应用域词条（命令见 ⑤）。

**无人值守**：单条命令完成五步，退出码即结论（0 全过 / 1 门不通过 / 2 回归告警 / 3 配置非法 / 4 内部错误），
无人工判读环节；确定性已验证（同配置两次运行指标逐位一致）。

**阈值先于结果**：所有阈值写在 suite 配置（`criteria` / `suite_criteria`）里，runner 代码内没有任何
具体阈值常量；改阈值必须改配置，`config_sha256` 随工件留痕。

## ③ 计算结果（数字全部由工件注入）

### 3.1 逐算例：测量值 vs 闭式参考解 vs 预声明阈值

| suite | case | inputs | measured | reference (闭式解) | rel. error | 预声明容忍 | margin | verdict |
|---|---|---|---|---|---|---|---|---|
| fx_integration | `fx_int_sin_n32` | `{"func": "sin", "a": 0.0, "b": 1.5, "n": 32}` | 0.929263 | 0.929263 | 2.683e-08 | 1.000e-06 | 37.27 | PASS |
| fx_integration | `fx_int_sin_n128` | `{"func": "sin", "a": 0.0, "b": 1.5, "n": 128}` | 0.929263 | 0.929263 | 1.048e-10 | 1.000e-09 | 9.544 | PASS |
| fx_integration | `fx_int_exp_n64` | `{"func": "exp", "a": 0.0, "b": 1.0, "n": 64}` | 1.71828 | 1.71828 | 3.311e-10 | 1.000e-09 | 3.02 | PASS |
| fx_integration | `fx_int_cubic_n64` | `{"func": "cubic", "a": 0.0, "b": 2.0, "n": 64}` | 2 | 2 | 0 | 1.000e-12 | — | PASS |
| fx_integration | `fx_int_cos_n16` | `{"func": "cos", "a": 0.0, "b": 2.0, "n": 16}` | 0.909299 | 0.909297 | 1.359e-06 | 1.000e-05 | 7.359 | PASS |
| fx_negative_control | `fx_neg_coarse_integral` | `{"func": "sin", "a": 0.0, "b": 1.5, "n": 4}` | 0.929367 | 0.929263 | 1.117e-04 | 1.000e-12 | 8.950e-09 | **FAIL** |
| fx_negative_control | `fx_neg_no_criteria` | `{"ratio": 0.5, "terms": 5}` | 0.96875 | 1 | 0.03125 | — | — | **FAIL** |
| fx_rootfinding | `fx_root_sqrt2_tol1e12` | `{"c": 2.0, "lo": 1.0, "hi": 2.0, "tol": 1e-12}` | 1.41421 | 1.41421 | 1.117e-12 | 1.000e-11 | 8.949 | PASS |
| fx_rootfinding | `fx_root_sqrt10_tol1e12` | `{"c": 10.0, "lo": 3.0, "hi": 4.0, "tol": 1e-12}` | 3.16228 | 3.16228 | 4.071e-13 | 1.000e-11 | 24.56 | PASS |
| fx_rootfinding | `fx_root_sqrt3_tol1e9` | `{"c": 3.0, "lo": 1.0, "hi": 2.0, "tol": 1e-09}` | 1.73205 | 1.73205 | 3.335e-10 | 1.000e-08 | 29.98 | PASS |
| fx_rootfinding | `fx_root_sqrt2_tol1e6` | `{"c": 2.0, "lo": 1.0, "hi": 2.0, "tol": 1e-06}` | 1.41421 | 1.41421 | 2.700e-07 | 1.000e-06 | 3.704 | PASS |
| fx_series | `fx_series_r050_n20` | `{"ratio": 0.5, "terms": 20}` | 0.999999 | 1 | 9.537e-07 | 1.000e-05 | 10.49 | PASS |
| fx_series | `fx_series_r025_n20` | `{"ratio": 0.25, "terms": 20}` | 0.333333 | 0.333333 | 9.094e-13 | 1.000e-10 | 110 | PASS |
| fx_series | `fx_series_r050_n10` | `{"ratio": 0.5, "terms": 10}` | 0.999023 | 1 | 9.766e-04 | 0.01 | 10.24 | PASS |
| fx_series | `fx_series_r080_n40` | `{"ratio": 0.8, "terms": 40}` | 3.99947 | 4 | 1.329e-04 | 0.001 | 7.523 | PASS |

> `margin = 预声明容忍 / 实测相对误差`，>1 表示通过判据门；`measured` 是 SUT 输出，`reference` 是闭式解（非拟合、非查表）。

### 3.2 suite 级门 + 工件哈希

| suite | run_id | cases passed | pass_ratio | max rel. error | suite gate | manifest sha256 (前 16) |
|---|---|---|---|---|---|---|
| fx_integration | `run_fx_integration` | 5/5 | 1 | 1.359e-06 | PASS | `4cd35ced0ce172e4` |
| fx_negative_control | `negctl_run1` | 0/2 | 0 | 0.03125 | **FAIL** | `8e12520fb7c20ebf` |
| fx_rootfinding | `run_fx_rootfinding` | 4/4 | 1 | 2.700e-07 | PASS | `fbe1ef9fc79bb2a4` |
| fx_series | `run_fx_series` | 4/4 | 1 | 9.766e-04 | PASS | `1dd8ffa77d4b973b` |

### 3.3 回归检测的判别力与特异性（回归注入 suite，基线=run_fx_integration）

| 注入 suite | gate | regression | 告警数 | warn | critical | 退出码 | 漂移样例 |
|---|---|---|---|---|---|---|---|
| `fx_integration_drift_p02` | PASS | clean | 0 | 0 | 0 | 0 | —（无告警） |
| `fx_integration_drift_p05` | PASS | regression | 5 | 5 | 0 | 2 | fx_int_cos_n16.evals drifted 5.00% (17 -> 17.85) > 3.0% |
| `fx_integration_drift_p15` | PASS | regression | 5 | 0 | 5 | 2 | fx_int_cos_n16.evals drifted 15.00% (17 -> 19.55) > 3.0% |

### 3.4 无人值守算例账本（KPI 口径）

按 `case_id` 去重、要求「通过且带 manifest 哈希链」的口径：**13** 个。逐条：

| case_id | suite | run_id | rel. error | case artifact sha256（前 16）|
|---|---|---|---|---|
| `fx_int_cos_n16` | fx_integration | `run_fx_integration` | 1.359e-06 | `f526c0e951a90814` |
| `fx_int_cubic_n64` | fx_integration | `run_fx_integration` | 0 | `d1a658c71a3591ae` |
| `fx_int_exp_n64` | fx_integration | `run_fx_integration` | 3.311e-10 | `572994766cd9314c` |
| `fx_int_sin_n128` | fx_integration | `run_fx_integration` | 1.048e-10 | `08642edb4a762de2` |
| `fx_int_sin_n32` | fx_integration | `run_fx_integration` | 2.683e-08 | `c28fa61697d13413` |
| `fx_root_sqrt10_tol1e12` | fx_rootfinding | `run_fx_rootfinding` | 4.071e-13 | `19b60bf5170df2ea` |
| `fx_root_sqrt2_tol1e12` | fx_rootfinding | `run_fx_rootfinding` | 1.117e-12 | `20b76082f0aed124` |
| `fx_root_sqrt2_tol1e6` | fx_rootfinding | `run_fx_rootfinding` | 2.700e-07 | `ee4ac4bc7617bbd2` |
| `fx_root_sqrt3_tol1e9` | fx_rootfinding | `run_fx_rootfinding` | 3.335e-10 | `dbffc280102f66cd` |
| `fx_series_r025_n20` | fx_series | `run_fx_series` | 9.094e-13 | `6706c2ff030b2224` |
| `fx_series_r050_n10` | fx_series | `run_fx_series` | 9.766e-04 | `193391402da9729c` |
| `fx_series_r050_n20` | fx_series | `run_fx_series` | 9.537e-07 | `a9247933096dd879` |
| `fx_series_r080_n40` | fx_series | `run_fx_series` | 1.329e-04 | `ccf8b7db48befbab` |

### 3.5 负对照（门若空转，这里会绿）

| case | measured | reference | rel. error | 预声明容忍 | 失败判据 | verdict |
|---|---|---|---|---|---|---|
| `fx_neg_coarse_integral` | 0.929367 | 0.929263 | 1.117e-04 | 1.000e-12 | `rel_error:<=` | **FAIL** |
| `fx_neg_no_criteria` | 0.96875 | 1 | 0.03125 | — | `__none__:__no_criteria__` | **FAIL** |

## ④ 结果图

图由 `tools/benchmark_runner/artifact.py:render_suite_figure` 与 `report_figure.py` 生成，
图上的每个数字都直接读自 `result.json`（不重算、不手输）；PNG 的 sha256 在各自 `manifest.json` 里。

| 图 | 文件（worktree 内路径）| 说明 |
|---|---|---|
| 汇总图 | `artifacts/benchmark_runner/summary.png` | (a) 15 个算例的实测相对误差（绿=PASS / 红=FAIL）与预声明阈值（蓝短横）；(b) 判据余量 `阈值/实测`，>1 通过。负对照两例的余量 ≪1，一眼可见门不是摆设 |
| 积分族 | `artifacts/benchmark_runner/fx_integration/run_fx_integration/figures/fx_integration_verdict.png` | (a) 逐算例误差 vs 阈值；(b) 误差随子区间数 n 的下降曲线；同族 n=32→128 的实测收敛阶在「关键结论」块里由两算例误差直接相除得到（理论 4 阶） |
| 级数族 | `artifacts/benchmark_runner/fx_series/run_fx_series/figures/fx_series_verdict.png` | (a) 逐算例误差 vs 阈值；(b) 误差随项数的下降 |
| 求根族 | `artifacts/benchmark_runner/fx_rootfinding/run_fx_rootfinding/figures/fx_rootfinding_verdict.png` | (a) 逐算例误差 vs 阈值；(b) 误差随容差 tol 的下降 |
| 负对照 | `artifacts/benchmark_runner/fx_negative_control/negctl_run1/figures/fx_negative_control_verdict.png` | 两例均为红色（欠分辨率 + 无判据 fail-closed）|

> 这些图是**判据门裁决的可视化**，不是场量云图——本单是平台机制节点，被检验对象是引擎而非某个物理场，
> 故不存在场量云图；对照基线曲线即"测量值 vs 闭式参考解"的逐算例误差（§3.1 表 + 图 (a)/(b)）。

## ⑤ 注意点（踩过的坑、局限、适用范围）

### 踩过的坑（都是"跑绿≠修好"的实例）
1. **机器零基线会造出假回归告警**：Simpson 对三次式精确 → `fx_int_cubic_n64` 的 `rel_error` 是 `0.0`；
   朴素相对漂移 `|cur−base|/|base|` 在 base=0 处爆成天文数字，把一次 `0.0 → 2e-16` 的浮点噪声报成回归。
   处置：引入分母地板 `ZERO_FLOOR=1e-12`（`regression.drift_of`），并用单测钉死该行为
   （`test_regression_zero_baseline_does_not_false_alarm`）。假告警与漏报一样是不诚实。
2. **默认策略把显式参数吃掉了**：首版 `_NON_TRACKED`（分辨率类指标不参与漂移）无差别生效，
   连 `--track evals` 也被压掉 → 回归注入演示"全绿"。是端到端测试
   （`test_end_to_end_drift_exit_code_2`）把它逼出来的：**显式 track 必须优先于默认策略**。
   这正是本单要求"回归检测器要有判别力控制"的价值——没有 +5%/+15% 的正对照，这个 bug 会静默存在。
3. **suite 无聚合判据时语义含糊**：首版让 `gate.evaluate([])` 返回 FAIL，于是"没写 suite_criteria"的
   整批直接失败，却与"算例失败"混在同一个退出码里。处置：**suite 级判据在配置期强制非空**
   （退出码 3=配置非法），运行期 fail-closed 只负责算例级。两类失败必须能区分。
4. **工件自己把出处写脏**：`git status` 会把本次运行刚写出的 `artifacts/**` 算进 `git_dirty`。
   处置：`collect_provenance(ignore_paths=...)` 排除输出目录，`git_dirty` 才真正表示"源码是否偏离 HEAD"。
5. **漂移告警是非方向性的**：+5% 与 −5% 都会报同一等级，因为"哪边更好"是领域判断，引擎不该内置。
   引擎只报幅度与方向（`baseline → current`），等级按幅度（>3% warn，>3×3% critical）。

### 复现审计命令（worktree 根目录，0 命中 = 引擎与配置无应用域词条）

```bash
grep -rniE 'ship|hull|seakeep|propuls|froude|wigley|suboff|kvlcc|resistance|ice_|冰区|阻力|船体|螺旋桨' \
     tools/benchmark_runner/*.py tools/benchmark_runner/configs/*.json | wc -l
# 输出：0
```

### 局限（如实申报）
1. **判据算子只有四类半**：标量比较（`<= < >= > == !=`）、区间（`in_range`）、单调性（`monotonic`）、
   有限性（`finite`）。分布检验、收敛阶、守恒量残差、CDF/K-S 这类判据尚未内建——当前靠 SUT 侧先折算成
   标量指标再判（可行但会丢掉一部分信息）。扩展点明确：`gate.SUPPORTED_OPS` + 一个 `_check_*` 函数。
2. **回归只按 `case_id` 配对**：算例改名等价于"消失 + 新增"（报 warn/info，不报漂移）。改名要同步改基线。
3. **KPI 口径未闭环（honest 缺口）**：`tools/guide_kpi_ledger.py` 里 `unattended_cases` 的 SQL 口径是
   「DB 里 mission goal 命中『案例 X（Tensor…』的去重计数」（10-31 看板口径 65 案例级）。本单的 13 个
   算例落在工件账本 `artifacts/benchmark_runner/unattended_cases.json`（只读 KPI 桥产出），
   **没有**写进 DB、也没有改 KPI ETL 的口径公式。所以「DB 侧 65→≥75」这一推进需要平台侧把
   账本并入口径（或由上游按算例铺任务单）——本单只保证引擎侧的算例是**可计数的、带哈希链的**。
4. **引擎不含并发/超时/资源限额**：无人值守的调度语义交给外部 scheduler（本波在控制面顺序执行，
   14 个算例端到端 <1 s；大算例应走集群）。引擎不重试、不做统计显著性检验。
5. **仅验证了确定性 SUT**：合成 SUT 无随机、无跨 ISA 差异；真实求解器若带随机性或 loong64/x86 差异，
   需叠加仓库已有的 `tools/repro_digest` 跨 ISA 协议，本单未合并该协议。

### 适用范围
任何「有参考解或既有基线、需要重复跑、需要机器裁」的算例集合：数值内核回归、API 契约回归、
参数扫描守门、以及后续把领域仓的 reference case 以点分路径注入即可复用的场景（引擎本身不改）。

## ⑥ 误差判定

口径：`rel_error = |实测 − 闭式参考| / max(|参考|, 1e-300)`，逐项数字见 §3.1（程序化注入）。

* **正向 13 个算例是否 <3%？** 是。最大相对误差见「关键结论（全部由工件注入）」块的
  `正向算例集最大相对误差` 一行：约 1e-3 量级（最差的是项数最少的级数算例 `fx_series_r050_n10`，
  其预声明容忍为 1e-2，余量约 10 倍；其余算例 1e-7~1e-13），远优于 3% 的验收线；
  且每个算例都通过了自己的**预声明**容忍（逐项 margin 见 §3.1）。
* **参考值来源**：全部为闭式解（Sin/Cos/Exp/多项式原函数、几何级数极限 r/(1−r)、√c），
  由机器精度级公式给出，无实验不确定度、无拟合参数 ⇒ 本单不涉及"对标实验误差>15% 不可接受"的问题；
  误差是**算法离散误差**（Simpson O(h⁴)、二分 O(2⁻ⁿ)），可随分辨率单调下降（图 (b) 已证）。
* **负对照是否被拦住？** 两例全部 FAIL（欠分辨率例 1.1e-4 相对误差 vs 预声明 1e-12；
  无判据例被判 `__none__:__no_criteria__`）⇒ 判据门有否决权，不是空转。
* **回归检测是否既灵敏又不误报？** +2% 注入：`clean`、退出码 0（不误报）；+5%：warn、退出码 2；
  +15%：critical、退出码 2；且这三例的判据门始终 PASS（§3.3）——说明"门通过"与"有回归"是两个
  独立的裁决维度，不能互相代替。
* **未达成的部分（honest）**：DB 侧 `unattended_cases` 从 65 推进到 ≥75 未完成（见 ⑤ 局限 3）；
  另有多判据类型、并发调度、跨 ISA 复现协议三项未纳入本单。

## 修订记录（诚实申报）

| 报告 id | 内容 | 说明 |
|---|---|---|
| 11073 | v1 首次发布 | 工件提交 509c4150 之前的正文；缺源码域词审计命令与 KPI 口径对齐说明 |
| 11075 | v2 | 正文含上述补充；**该条 `summary` 字段在一次接口探针测试中被误写为占位串**，以 v3 的 summary 为准（报告接口不可改/不可删，只能重发新版本，故留痕于此）|
| 本版（v3） | 最终 | 与工件提交 3a83de33 一致；summary 完整 |
