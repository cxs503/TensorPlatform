# 跨任务回归守卫（Cross-Mission Regression Guard）

**节点**：`p380w2-n01-regression-guard` · **仓**：`TensorPlatform` ·
**实现**：`tools/regression_guard/`（`3117` 模块 / `3492` 条 import 边的真实仓扫描，
扫描耗时 `22.0 s`） ·
**判据文件**：`artifacts/regression_guard/criteria.json` ·
**报告 digest**：`aa2ab953b42e5526ce52b4266f346f08d025677a644de1fede2ff4ab49a28e63`

> 一句话：代码变更落地的瞬间，本机制回答"谁的验收测试会看到它、要重跑哪些、比基线退化了没有"，
> 并给出可机读的 JSON 结论与退出码；**判据全部由程序产出，本文数字由 `tools/regression_guard/make_doc.py` 从工件渲染，零手抄。**

## 0. 判据逐条对照

| 判据 | 任务书要求 | 实测（程序产出） | 结论 |
|---|---|---|---|
| (a) | 注入求解器内核变更，识别并重跑所有依赖任务测试（≥3 个任务） | 识别任务 3 个：T-PCE-KERNEL-M9011, T-PCE-P152W5-N07, T-PCE-PIPELINE；检出率 100.0%；真实重跑 44 个用例 | PASS |
| (a+) | 植入真实数值缺陷后必须报回归 | 沙箱注入 `0.9×` 系数缩放 → 回归用例 3 个，判定 `regression` | PASS |
| (b) | 注入平台 API 端点变更，识别并重跑依赖任务测试，检出率 100% | 识别任务 2 个；检出率 100.0%；重跑 42 个用例；import 闭包额外选中 tests/test_report_reconcile.py（均带理由链）；判定 `pass_with_known_failures`（既有红 7 例，新回归 0 例） | PASS |
| (c) | 无变更场景不触发多余测试（误触发率 < 1%） | 空变更选择 0 个；111 个无依赖路径场景共选择 0 个；误触发率 0.00% | PASS |
| (d) | 结构化 JSON 报告（变更类型 / 受影响任务 / 测试结果差异） | 顶层键 10 个，含 change_classes/affected tasks/deltas/verdict.exit_code；digest `aa2ab953b42e5526…` | PASS |
| (e) | 负控：无依赖任务→不触发（退出码 0）；已缓存→复用不重跑 | 负控退出码 0；111 个负控场景全部 exit 0 = True；热缓存重跑 0 个用例，命中率 100.0% | PASS |

## 1. 案例介绍（要解决的失效模式）

平台的能力台账（`feature_ledger`）记录了能力与证据，战役 DAG（`campaign_nodes`）记录了节点依赖，
但二者与**代码**之间没有机器可读的连线：求解器内核或 API 端点被改动后，没有任何机制保证
"依赖该代码的历史验收测试被重跑"。结果是能力退化只在下一次全量回归或线上报错时才暴露。

本节点把"变更 → 依赖 → 重跑 → 比对"做成一条可复算的流水线，输入三样、输出一样：

| 输入 | 形态 | 来源 |
|---|---|---|
| 变更清单 change manifest | `{changes:[{path,kind,symbols?}],base_rev,head_rev}` | `git diff --name-status` 或人工/CI 注入 |
| 依赖台账 task ledger | `{artifacts[]→features[]→tasks[] + nodes[]}` | `config/regression_guard/task_ledger.json`；也接受 `feature_ledger` 行（`from_feature_ledger_rows`） |
| 代码依赖图 import graph | 每模块的已解析 import 边 + 顶层符号 | 仓内 AST 扫描（`tools/regression_guard/scanner.py`） |
| **输出** | 结构化报告 + 退出码 0/1/2 | `artifacts/regression_guard/report_*.json` |

平台铁律「零领域语义」在实现里是硬约束：`tools/regression_guard/` 与 `tests/regression_guard/`
**不含任何领域专属词条**，自测夹具全部是合成包名（`pkgs/solver`、`pkgs/platform`、`feat.solver_core`）；
真实仓的四条受管链路以**配置**形式放在 `config/regression_guard/task_ledger.json`，域能力要到
`feature_ledger` 行注入时才进入。

## 2. 怎么做的（方法 / 流程 / 关键设置）

### 2.1 流水线

```
change manifest ──► impact analysis ──► cache probe ──► pytest batches ──► baseline diff ──► report + exit code
      │                  │  (ledger + ast graph)              │                │
      │                  └─ 每条选择都带 reason trail          └─ 内容寻址键     └─ pass→fail = regression
      └─ change_class 由台账 artifact.kind 决定（solver_core / platform_api / platform_ledger / platform_mechanism）
```

* **影响解析**：路径按 `artifact.paths`（精确 / 目录前缀 / 逐段通配）命中 → 能力（含 `depends_on` 传递闭包）
  → 任务 → 测试选择器；同时从静态 import 图向外 BFS，把"间接导入该模块的测试文件"补进来。
  每条选择记录 `reasons`（`artifact`/`feature`/`feature_closure`/`task`/`import`），
  `import` 理由另由**第二套实现**（正则文本匹配 `textual_import_edge`）独立复核，报告中给出 `confirmed=true/false`。
* **符号级收窄**：变更可声明 `symbols`（如只改 `relax`）；import 边按"导入名 ∩ 可见名"逐跳传播，
  只影响真正用到该符号的测试。
* **缓存**：键 = sha256(选择器, pytest 参数, 测试文件 hash, 测试文件传递依赖闭包 hash)。
  命中即复用历史结果，**不启动任何子进程**（判据 e 的"已缓存→不重跑"由此保证）。
* **执行**：每个"测试根"（`tests/`、`backend/tests/`）单独一个 pytest 子进程，解析 JUnit XML 得逐用例结果
  （见 §5 的坑：两个测试根共进程会互相遮蔽顶层包）。
* **比对**：与基线 `outcomes` 做逐用例差分——`regressions`（pass→fail）、`newly_failing`、`fixed`、
  `pre_existing_failures`（改前就红，严禁冒充新回归）、`unchanged_passed`。
* **判定（fail-closed）**：无任何依赖 → `no_dependents`(0)；有回归 → `regression`(1)；
  有未收集/未运行的选择器 → `error`(2)（**没跑过 ≠ 绿灯**）；新出现的红 → `fail`(1)；
  只有基线里本来就红的用例复跑仍红 → `pass_with_known_failures`(0)（不阻塞变更、但如实点名）；否则 `pass`(0)。

### 2.2 真实仓受管链路（`config/regression_guard/task_ledger.json`）

| artifact_id | 受管代码 → 任务数 |
|---|---|
| solver.pce_kernel | backend/services/pce_uq/kernel.py（非侵入 PCE 内核）→ 3 个任务 |
| platform.audit_api | backend/api/audit.py（审计 HTTP 面）→ 2 个任务 |
| platform.capability_ledger | tools/capability_discovery/store.py（能力台账存储）→ 3 个任务 |
| platform.regression_guard | tools/regression_guard（本机制自守）→ 1 个任务 |
| n-integration / n-release | 战役 DAG：四条链路的下游节点，仅报告“风险半径”，不额外跑测试 |

### 2.3 自测（pytest）

`tests/regression_guard/` 共 25 个用例，覆盖判据 a/b/c/d/e 与实现不变量（负控、缓存失效、fail-closed、
目录选择器、缺失选择器不拖垮批次、基线语义、报告 digest 冷热一致）。
本次运行：25 passed, 0 failed, 0 error(s), 25.08 s — `python3 -m pytest tests/regression_guard -q -p no:cacheprovider`

## 3. 计算结果（真实仓，全部程序产出）

| 演示 | 场景 | 重跑的测试文件 | 执行/缓存 | 判定 | 退出码 |
|---|---|---|---|---|---|
| D1 | 真实求解器内核变更（冷启动） | tests/test_pce_p152w5_n07.py, tests/test_pce_uq_kernel_m9011.py, tests/test_uq_pipeline/test_pipeline.py | 44 执行 / 0 缓存 | pass | 0 |
| D2 | 同一变更，热缓存 | tests/test_pce_p152w5_n07.py, tests/test_pce_uq_kernel_m9011.py, tests/test_uq_pipeline/test_pipeline.py | 0 执行 / 44 缓存 | pass | 0 |
| D3 | 基线采集（全部受管任务） | 10 个选择器 | 150 执行 / 0 缓存 | pass_with_known_failures | 0 |
| D4 | 沙箱植入真实缺陷（真实 PCE 内核代码） | tests/test_pce_uq_kernel_m9011.py, tests/test_uq_pipeline/test_pipeline.py | 28 执行 / 0 缓存 | regression | 1 |
| D5 | 真实 API 端点变更 vs 基线 | backend/tests/feat/test_drift_and_inference.py, backend/tests/test_audit_unit.py, tests/test_report_reconcile.py | 42 执行 / 0 缓存 | pass_with_known_failures | 0 |
| D6 | 负控：111 个无依赖路径 + 空变更 | — | 0 执行 | no_dependents | 0 |

补充量：基线共记录 `171` 个用例；D4 沙箱复制了真实内核的
`12` 个文件（按 import 闭包自动复制，保证沙箱自洽）；
D6 的 `111` 个无依赖路径场景（`docs/*.md`、`artifacts/*.json`）合计选择 `0` 个测试。

## 4. 结果图

| 图 | 内容 |
|---|---|
| `artifacts/regression_guard/figures/fig1_pipeline.png` | 变更→依赖→重跑→比对 流水线，含两条"合法零测试"路径 |
| `artifacts/regression_guard/figures/fig2_metrics.png` | 检出率/缓存命中率（须 100%）与误触发率（须 <1%） |
| `artifacts/regression_guard/figures/fig3_evolution.png` | 各演示的重执行量（执行 vs 缓存）——热缓存归零 |
| `artifacts/regression_guard/figures/fig4_fanout.png` | 每个受管 artifact 的影响半径（任务数 / 测试文件数） |

## 5. 注意点（踩过的坑 + 局限）

1. **两个测试根同进程会互相遮蔽顶层包**：`backend/pyproject.toml` 的 `pythonpath` 把 `backend/` 放到
   `sys.path` 前端，于是 `import tools` 解析到 `backend/tools`（存在同名目录），根级 `tests/conftest.py`
   的插件 `tools.repro_digest.pytest_plugin` 随即导入失败，整批 pytest 直接 abort（exit 4）。
   修法：执行层按测试根分批（一个根一个子进程）+ `PYTHONPATH` 固定仓根。**症状极具误导性**（报的是插件缺失，
   真正的病因是包名遮蔽），故记录在此。
2. **"没跑"不得判绿**：选择器不存在 / 收集失败时 pytest 只报 usage error，若不识别就会把"零结果"
   当成"零失败"。守卫把 `not_run` 当作无证据，判 `error`(2)，且不写入缓存。
3. **既有失败不等于新回归**：真实仓里 `backend/tests/feat/test_drift_and_inference.py`（缺运行夹具）与
   `tests/test_capability_discovery_artifacts.py`（1 例）在本环境本来就是红的。基线记录后它们落在
   `pre_existing_failures`，`regressions` 为空——诚实结论是"本次变更未引入新退化"，不是"全绿"。
4. **缓存粒度 = 选择器（文件）而非单用例**：缓存条目保存该文件展开后的全部用例结果，否则热缓存的报告会
   把 `file::test_a`、`file::test_b` 坍缩成 `file`，delta 粒度失真。
5. **精确率无法完全独立验证**：传递闭包式的"应该被跑"没有外部真值；本报告的量是
   *检出率（对照人工声明的期望集合）* 与 *误触发率（对照 111 个无依赖路径 + 空变更）*，
   不宣称"零漏选"的数学保证。这是本节点的**已知局限**。
6. **静态 import 图的能力边界**：动态导入（`importlib`、字符串路径、插件注册）与跨仓依赖（TensorLBM 等）
   不在 AST 图内；跨仓需把对方仓的模块以台账路径 + `stage_dir` 检出后并入扫描，或由对方仓 CI 事后上报。
7. **仓库扫描耗时**：`~22.0 s`（`3117` 模块）。
   生产用法应缓存图产物（`cli.py scan --out`）并随 commit 失效，而非每次重扫。

## 6. 误差判定（关键量 vs 基准）

| 关键量 | 基准/判据 | 实测 | 偏差 | 是否 <3% / 达标 |
|---|---|---|---|---|
| 求解器变更检出率 (a) | 100% | 100.00% | +0.00 pp | 是（等于判据，误差 0） |
| API 变更检出率 (b) | 100% | 100.00% | +0.00 pp | 是（误差 0） |
| 误触发率 (c) | < 1% | 0.00% | +0.00 pp | 是（0.00% < 1%） |
| 热缓存重跑数 (e) | 0 | 0 | +0 | 是（恒等） |
| 负控退出码 (e) | 0 | 0 | +0 | 是（恒等） |
| 植入缺陷回归检出 (a+) | ≥1 | 3 | +2 | 是 |

结论：四个百分比型判据（检出率 ×2、误触发率、缓存命中）均为**离散计数之比**，
分母与分子由同一份工件给出，偏差为目标值的 `0`（检出率、缓存）与 `0.00%`（误触发）；
植入缺陷回归检出数为 `3 ≥ 1`，满足判据。
**是否存在 <3% 的误差要求未达标项：否。** 需要显式声明的例外是 §5 第 5 条——
"应跑集合"的完备性没有外部真值，本机制给出的是可审计的理由链，不是数学完备性证明。

## 7. 复现

```bash
python3 -m pytest tests/regression_guard -q                 # 25 个用例（判据 a–e）
python3 tools/regression_guard/demo_real_repo.py            # 真实仓 7 个演示 → criteria.json
python3 tools/regression_guard/make_figures.py              # 4 张图
python3 tools/regression_guard/make_doc.py                  # 本文
# 单次使用（CI 里最常用）：
python3 -m tools.regression_guard.cli from-git \
  --ledger config/regression_guard/task_ledger.json \
  --rev-range origin/main..HEAD --report artifacts/regression_guard/report.json \
  --cache-dir .cache/regression_guard
```
