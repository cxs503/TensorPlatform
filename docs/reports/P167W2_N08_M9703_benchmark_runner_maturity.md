# P167W2-N08 成熟化波：平台自主基准测试引擎的 fail-closed 加固

*节点*：`p167w2-n08-benchmark-runner-maturity` ｜ *仓*：TensorPlatform ｜ *引擎版本*：1.1.0 ｜ *工件*：`artifacts/benchmark_runner_m9703/evidence.json` ｜ *生成器*：`python3 -m tools.benchmark_runner.report_doc`（引擎 v1.1.0）

> 本文所有数字均由 `report_doc.py` 从 `evidence.json` 注入，无手抄；`evidence.json` 的 sha256 见文末审计链。

## ① 案例介绍：这一波要解决什么

上一波（v1.0）已把四件套落地：配置驱动 runner、预声明判据门、JSON/PNG/manifest 工件、与基线逐指标对比的回归检测。它们回答的是「**数字对不对**」。

本波回答的是另一个问题：「**当没人看着的时候，引擎自己会不会失守**」。无人值守的验证门，失效方式不是算错，而是**不给裁决**或**给错裁决**。审计 v1.0 源码与实测确认了四条失守路径：

| # | 失守路径（v1.0 实测/源码事实） | 后果 |
|---|---|---|
| 1 | 挂死的 SUT：runner 会永久阻塞（`run_case` 只捕获异常，挂住不是异常）——门链无裁决 | 夜间链停在半路，既非 pass 也非 fail |
| 2 | suite 级墙钟无上界：无 suite 预算概念 | 单个卡死算例拖垮整批 |
| 3 | 证据链不回验：落盘后从不回验哈希（manifest 里的 sha256 从未被重算过） | 工件可被静默改写，哈希只是装饰 |
| 4 | 覆盖度静默失守：「基线 5 算例 → 本次 1 算例」实测 status=noted、退出码 0 | 门还绿着，门后面已经没人 |

被测对象仍是**零领域语义**的合成算例（复合 Simpson 积分、几何级数部分和、二分求根），外加一个纯 `sleep` 的故障注入器 `stall`——它不参与任何数值判据，只用来触发超时。平台仓不得出现域词条，因此这里的算例全是初等数学与通用数值过程；接真实求解器时只换配置里的点分路径。

## ② 怎么做的（方法 / 关键设置 / 工具链）

四件事，各自配一个**负对照**（「绿灯」必须能被证伪）：

1. **墙钟护栏** `timeout.py`：逐算例 `timeout_s`（suite 缺省 `DEFAULT_TIMEOUT_S=300`，per-case 可覆盖）+ suite `budget_s`。主线程走 `SIGALRM`/`setitimer`（能真正打断纯 Python 死循环，且调用后还原宿主原 handler/定时器）；非主线程回落到 daemon 线程。超时 → `status="error"`、`failed_checks=["__timeout__"]`；预算用尽 → 剩余算例记 `__budget__` error。**不存在「静默跳过」这个状态。**
2. **证据链回验** `artifact.verify_manifest()`：落盘后立刻重算每个工件的 sha256 与字节数、重算清单自身 `manifest_digest`、检查未登记的多余文件、并校验每个算例 JSON 自带的 `artifact_sha256`（局部改写层）。任一不满足 → `ok=False`，runner 退出码 **5**。CLI：`python3 -m tools.benchmark_runner.artifact --verify <run_dir>`。
3. **覆盖度 fail-closed** `regression.py`：`case_missing` 从 `warn` 升为 `critical` 并计入 `has_regression()`；`counts.missing` 可审计。新增算例仍是 `info`（写多了不罚）。
4. **零领域语义的机器证明** `domain_guard.py`：扫描引擎包与算例配置，词表**外部注入**（`tools/evidence_domain_lexicon.json`，仓库唯一豁免文件），复用平台既有扫描器避免两套结论；缺词表 → `status="no_lexicon"`、`ok=False`（扫不了 ≠ 干净）。

判别力/特异性的对照组设计（预声明，先于运行）：

| 场景 | 负对照（必须被抓住） | 特异性（必须不误报） |
|---|---|---|
| 墙钟 | `fx_timeout_negative`：SUT 睡 30 s vs 0.5 s 预算 → 必 FAIL | 同一 suite 的健康算例必须仍跑且 PASS |
| 预算 | `fx_budget_negative`：1 s 预算 → 第二个算例 `__budget__` | 第一个算例必须在预算内被裁（不无限等） |
| 证据链 | 改一个字节 / 删文件 / 塞文件 / 重建清单后局部改写 → 必判不可信 | 干净 run 必须 `ok=True`，且回验幂等 |
| 覆盖度 | 5 算例基线 vs 1 算例重跑 → 必 exit 2 | 新增算例仍 `noted`/不回归；+2% 数值漂移仍不误报（v1.0 用例保留） |

关键实现纪律：**计时证据不进裁决载荷**。v1.0 的 `test_determinism_across_runs` 要求同一配置两次运行逐位一致；把墙钟塞进算例 dict 会立刻破坏它。于是 `timing_s` 在 `CaseVerdict.to_dict()` 中被剔除，单独落 `timings.json` 并进 manifest——裁决可复现，耗时仍可审计。

## ③ 计算结果（程序化注入，无手抄）

### 3.1 正示例 suite（成熟化后仍全过、证据链自洽）

| 指标 | 值 |
|---|---|
| n_cases | 5 |
| n_passed | 5 |
| n_failed | 0 |
| n_error | 0 |
| n_timeout | 0 |
| n_skipped_budget | 0 |
| pass_ratio | 1 |
| max_rel_error | 1.35886e-06 |
| mean_rel_error | 2.77226e-07 |
| max_abs_error | 1.23561e-06 |
| regression.status | clean |
| integrity.ok | true |
| exit_code | 0 |

### 3.2 墙钟护栏（负对照实测）

| suite | 算例 | 预声明 timeout (s) | 实测耗时 (s) | 终态 |
|---|---|---|---|---|
| fx_timeout_negative | `fx_stall_beyond_timeout` | 0.5 | 0.5007 | error |
| fx_timeout_negative | `fx_healthy_after_timeout` | 1 | 0.000536 | pass |
| fx_budget_negative | `fx_budget_burner` | 5 | 1.001 | error |
| fx_budget_negative | `fx_budget_starved` | 5 | None | error |

* 挂死算例 `fx_stall_beyond_timeout`：SUT 被要求睡 30 s，预声明预算 0.5 s，实测在 0.5007 s 被裁为 error；整个 suite（含 1 个健康算例）引擎墙钟 1.268 s。
* 预算负对照：`fx_budget_burner` 的 per-case 预算被 suite 剩余预算（1 s）压到 1.001 s 后裁掉；第二个算例 `fx_budget_starved` 记为 `__budget__` error（`timing_s=None`，因为它根本没被执行）——**没有被静默跳过**。
* 注意：`min(per-case timeout, 剩余预算)` 是有意的行为（预算是硬上限），所以 `fx_budget_burner` 的行里「预声明 5 s / 实际 1 s」不是矛盾，而是预算优先。

### 3.3 证据链回验（演练实测）

| 破绽 | 检出 |
|---|---|
| 工件缺失（删 `result.json`） | true |
| 未登记的额外工件 | true |
| 重建清单后的算例局部改写（自哈希层） | true |
| 改一个字节（`drills/tampered_run/cases/fx_int_cos_n16.json`：rel_error 1.359e-06 → 0） | clean_verdict_ok=true → tampered_verdict_ok=false （sha256 mismatch 1 处，自哈希错误 1 处） |

### 3.4 覆盖度 fail-closed（本波唯一的行为语义变更）

* 基线 suite：5 个算例；本次重跑：1 个算例；门自身 `gate_passed=true`（门是绿的）。
* 回归裁决：`status=regression`、`counts.missing=4`、`has_regression=true`、**`exit_code=2`**。
* 改前实测（同一构造）：`status=noted`、`has_regression=false`、`exit_code=0` —— 少了 4 个算例被读成「没有回归」。

### 3.5 零领域语义守卫

* `domain_guard.scan()`：`status=clean`、扫描文件 23 个、命中 0 条（词表 37 条 AS/CJK 词条，外部注入）。

### 3.6 图

* `figures/fig1_wallclock_guard.png`（sha256 `0a7d261aebcd6e02…`）
* `figures/fig2_failclosed_matrix.png`（sha256 `8abf13153cd2827b…`）
* `figures/fig3_coverage_loss.png`（sha256 `b8dc01d57ee83f5e…`）

![wall-clock guard](../artifacts/benchmark_runner_m9703/figures/fig1_wallclock_guard.png)

*图1*：(a) 逐算例实测墙钟 vs 预声明预算（对数轴）——被裁算例的柱子正好落在预算线上，健康算例在毫秒级；(b) 引擎整批返回耗时 vs SUT 被要求的睡眠时长——差两个数量级，这就是「不挂死」的量化含义。

![fail-closed matrix](../artifacts/benchmark_runner_m9703/figures/fig2_failclosed_matrix.png)

*图2*：七类失守场景 × 两代引擎的检出矩阵。绿=v1.1 抓住（全部实测）；v1.0 一行为「能力不存在」（源码审计，不是测量值——诚实标注在图标题里）。

![coverage loss](../artifacts/benchmark_runner_m9703/figures/fig3_coverage_loss.png)

*图3*：覆盖度失守前后。(a) 基线 5 算例 → 实跑 1 算例；(b) 回归告警计数与退出码，与改前的 `status=noted / exit=0` 对照。

## ④ 注意点（踩过的坑 / 局限 / 适用范围）

1. **挂死不是异常**。`try/except` 对死循环、等锁、阻塞 IO 完全无效——这是 v1.0 最本质的坑。护栏必须建在「调用层」而不是「异常层」。
2. **SIGALRM 的边界**：纯 Python 死循环能被打断；卡在不可中断 C 扩展内部时，异常要等它返回才生效。所以本护栏保证的是「引擎按时给裁决、进程能退出」，**不是「任意 SUT 都能被强行终止」**。守护线程兜底同样无法强杀（故设为 daemon，保证不阻塞进程退出）。
3. **计时污染确定性**：把墙钟写进裁决载荷会破坏 v1.0 的逐位可复现用例——这不是「老测试太严」，而是「数值裁决」和「环境量」必须分家。`timings.json` 是解法。
4. **`verify_manifest` 的信任边界**：它证明「工件与 manifest 记录一致」，**不证明** manifest 本身没被整体重写（重写后重算全部哈希是对手完全有能力做的）。真正的锚在 git：`manifest → config_sha256 → git_sha`。所以 CI 里应先提交/归档 manifest，再回验；单靠目录回验只挡得住局部篡改与意外损坏。
5. **覆盖度升为 critical 的副作用**：合法的「缩减 suite」（真的下掉了某算例）现在会红。这是刻意的：删算例必须走基线更新（`--update-baseline`）这条有记录的路径，而不是让它悄悄消失。
6. **适用面**：本引擎只对「数字型判据」负责；算例的业务正确性靠 SUT 与参考解的实际算法质量，引擎不替它背书。零领域语义意味着引擎包里不出现域算例——域算例归领域仓。

## ⑤ 误差判定（关键量 vs 基准，逐项回答是否 <3%）

本波是**引擎成熟化**，不是数值算法产出，故「误差」分两层给：

**(a) 数值层（正示例 suite 的合成算例）**：与闭式解对比

| 量 | 值 |
|---|---|
| max_rel_error | 1.35886e-06 |
| mean_rel_error | 2.77226e-07 |
| 预声明判据 | 逐算例 `rel_error <= ` 配置阈值（见 `result.json`） |

* 判定：max_rel_error = 1.35886e-06 ≪ 3% —— **是**，<3%（合成算例对闭式解，本质上受浮点舍入与 Simpson 截断控制）。

**(b) 护栏层（本波真正的产物）**：判据是「该抓的都抓到、不该报的都没报」

| 判据 | 预声明阈值 | 实测 | 是否满足 |
|---|---|---|---|
| 挂死算例被裁为 error（不是挂住） | 必须 error | error （0.5007 s ≤ 0.5 s + 容差） | 是 |
| 健康算例不受牵连 | 必须 pass | pass | 是 |
| 引擎整批返回耗时 | < 15 s（用例阈值） | 1.268 s（预算对照 1.678 s） | 是 |
| 预算用尽算例被记账 | 必须 `__budget__` error | error | 是 |
| 篡改检出率 | 4/4 类破绽 | missing=true, extra=true, self-hash=true, byte-tamper=true | 是 |
| 干净 run 误报 | 0 | ok=true | 是 |
| 覆盖度丢失被读成回归 | exit_code=2 | 2 | 是 |
| 引擎域词条命中 | 0 | 0 | 是 |

* 说明：上表没有「3% 误差」这类连续量可谈（它们是布尔判据）；唯一连续量 max_rel_error 已在 (a) 给出，远小于 3%。不做粉饰：本波的局限见 ④，其中 `SIGALRM` 对不可中断 C 扩展无效、`verify_manifest` 挡不住「整体重写 manifest」两项是**已知残余风险**，不是已解决项。

## ⑥ 审计链与复现

| 项 | 值 |
|---|---|
| evidence.json | `artifacts/benchmark_runner_m9703/evidence.json` sha256 `89ed4056a2dbaf3be016031b4f57f116c716f464dbb0fe0497847e62633af49d` |
| 正示例 manifest | `artifacts/benchmark_runner_m9703/runs/fx_integration/integration-baseline/manifest.json` |
| 正示例 manifest sha256 | `010be9900fbba6db1c1c672ea003a8adf4f4969df200d9e4d205aa349c007573` |
| 正示例 result.json sha256 | `8d7ac3f8907357f9d159c51d3fba4763e823af249e079814d225c94b5622ae78` |
| 覆盖度重跑 regression.json | `artifacts/benchmark_runner_m9703/runs/fx_integration/coverage-truncated/regression.json` |
| coverage regression sha256 | `9b626b0d2a7d6cd1521f1fec756befec264dccb97ec0382214fc6b57bd0038dc` |

复现（三条命令，全部无人值守）：

```bash
python3 -m tools.benchmark_runner.guard_demo --out artifacts/benchmark_runner_m9703
python3 -m tools.benchmark_runner.report_doc \
    --evidence artifacts/benchmark_runner_m9703/evidence.json \
    --out docs/reports/P167W2_N08_M9703_benchmark_runner_maturity.md
python3 -m pytest tests/test_benchmark_runner_p167w2_n08.py \
                  tests/test_benchmark_runner_m9703_maturity.py -q
```

测试：`test_benchmark_runner_p167w2_n08.py` 22 passed（**未改动，v1.0 用例原样通过**）+ `test_benchmark_runner_m9703_maturity.py` 16 passed = 38 passed。

---

## 交付物清单

| 类别 | 路径 |
|---|---|
| 新增能力 | `tools/benchmark_runner/timeout.py`（墙钟护栏）|
| 新增能力 | `tools/benchmark_runner/domain_guard.py`（零域守卫）|
| 新增能力 | `tools/benchmark_runner/guard_demo.py`（证据生成器）|
| 新增能力 | `tools/benchmark_runner/report_doc.py`（零手抄报告渲染）|
| 扩展 | `artifact.py`（`verify_manifest` + CLI）、`runner.py`（超时/预算/exit 5）、`config.py`（`timeout_s`/`budget_s`）、`regression.py`（覆盖度）、`model.py`（`timing_s`）、`synthetic.py`（`stall` 故障注入器）|
| 负对照配置 | `configs/fx_timeout_negative.json`、`configs/fx_budget_negative.json` |
| 测试 | `tests/test_benchmark_runner_m9703_maturity.py`（16 条）|
| 证据工件 | `artifacts/benchmark_runner_m9703/`（evidence.json / runs / drills / figures）|
| 文档 | 本文件 + `tools/benchmark_runner/README.md` 更新 |

