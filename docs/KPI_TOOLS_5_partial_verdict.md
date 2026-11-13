# KPI-TOOLS-5 返修轮2：子集探针的裁决词（PARTIAL）与 KPI 台账读槽保护

**任务**：TensorPlatform#1283 重开·管到底第2轮（m17187）
**分支**：`agent/m17187`（自 main `f5bbcdb2a5a7` 切出）
**日期**：2026-11-13 · **环境**：Linux / Python 3.11 / 本地工作树 + 外协方（loong64）

## 0. 结论（先说三件事）

1. **不重复实现**：原 PR#1283 声明的 12 条交付路径**在 main 全部存在**——其中 11 条与返修轮
   （`6858301ea2b1`）**逐字节一致**，`backend/main.py` 一条为合并后的正常演进（新增了别的
   router 接线）。对账表见 `docs/pr1283/reconciliation.md`（由 `scripts/reconcile_pr1283.py`
   读 git 对象程序化生成，实测 `present=12/12`、`identical_to_repair=11`）。重开单若再写一遍
   就是重复 PR + 热文件冲突。
2. **本轮真实增量**：修复该接入面已登记缺口（`docs/kpi/KPI_TOOLS_5_measure_m16760.md` §⑤
   缺口 3）——**子集探针把「可用」判成「失败」**：`partial` 标记写进 ledger 的时序在
   `verdict()` 之后，故 `verdict()` 永远看不到它，`--only`/`tool_ids` 子集一律返回 `FAIL`。
   修复后裁决词升级为 `PASS / FAIL / PARTIAL` 三值，`PARTIAL` = 「目标根本没被检验」。
3. **连带缺陷（本轮新发现并修复）**：`report.write_artifacts()` 不区分全量/子集，子集跑也会
   覆盖 `artifacts/external_tools/latest.json`——即 HTTP KPI（`/coverage`、`/benchmark`）的
   **唯一读槽**。一次 1 类的子集探针就能把平台 KPI 永久打成 `real=1 < target=5`。现在子集台账
   只留 stamped 取证文件，不占读槽；`coverage()/benchmark()` 遇到 `partial` 台账**拒绝作答**
   （fail-closed，而不是拿子集数字冒充 KPI）。

## 1. 案例介绍：这个 KPI 与这条缺陷

KPI-TOOLS-5 锚点原文：「≥5 类外部工具接入平台并可用」。平台把它定义成**可机器裁决的三段式**
（缺一段不得记 REAL）：探针命中环境里的库/可执行文件 → 适配器真跑一次 → 结果对一个**独立
基准**成立（闭式解 / 整数恒等 / 跨语言同值）。规格是数据（`data/external_tools/classes.json`），
机制不含任何学科词条。

**被测对象与规格规模（读数直接来自规格文件与本次运行台账）**

| 项 | 值 | 来源 |
|---|---|---|
| 工具类数 | 10 | `classes.json` |
| 类别数 | 10 | `classes.json` |
| 不变式条数 | 27 | 本次全量运行台账 |
| 目标类数 | 5 | `classes.json`（`target_classes`） |
| 本轮被触发的调用面 | `POST /api/v1/external-tools/probe`（子集）、`GET …/coverage`、`GET …/benchmark`、CLI `run --only` | `backend/api/external_tools.py` / `tools/external_tools/cli.py` |

**缺陷复现（修复前，仓库真实规格，只探 1 类）**

```
$ python -m tools.external_tools.cli --out-dir /tmp/et_dbg run --only version_control
suite=external-tools/KPI-TOOLS-5 target=5 real=1 shortfall=4 verdict=FAIL
  REAL      version_control            HEAD=f5bbcdb2a5a7 commits=2219 tracked=7776 blob_reproduced=1
digest=033cebe04477d882 value_digest=cdd02f9489f232d1
```

唯一被探的类 `status=REAL`、不变式全过，裁决却是 `FAIL`——调用方（门禁、面板）只看
`verdict` 就把「这个工具可用」读成了「KPI 不达标」。

## 2. 怎么做的（方法 / 流程 / 关键设置）

修复刻意分成**三处最小改动**，每处都能单独被测试钉住：

| # | 位置 | 改动 | 为什么必须在这 |
|---|---|---|---|
| 1 | `tools/external_tools/harness.py` | `run_suite()` 把 `partial` / `selected_tool_ids` 写进 **ledger 本体**；`verdict()` 升级为三值判定：required 类有 `FAIL` → `FAIL`；覆盖达标 → `PASS`；`partial` 且被选类全 `REAL` → `PARTIAL`；其余 → `FAIL` | 裁决词必须能从台账**独立重算**，不能依赖调用方传参（原缺陷正是时序依赖） |
| 2 | `backend/services/external_tools_service.py` | `probe()` 先落 `partial` / `kpi_evaluated` 再算 `verdict`；`coverage()/benchmark()` 经 `_reject_partial()` 拒绝子集台账 | 服务层是 HTTP 面的唯一入口，语义要在这里闭环 |
| 3 | `tools/external_tools/report.py` | `write_artifacts()` 仅在**非子集**时才写 `latest.json`（读槽），子集仍落 stamped ledger + markdown（取证） | 一只探针不许污染 KPI 读槽 |

配套：CLI 退出码契约扩展 `3 = PARTIAL`（`0 PASS / 1 FAIL / 2 规格坏 / 3 子集未评估`），
`__init__` 导出 `VERDICT_PASS/FAIL/PARTIAL` 常量；对账器 `scripts/reconcile_pr1283.py`
只读 git 对象、按 blob sha256 逐条比对，产物 `docs/pr1283/reconciliation.{json,md}`。

**测试**（`tests/test_external_tools_partial.py`，8 条）：子集全 REAL→PARTIAL、子集里不变式
违规→仍 FAIL、子集里工具缺失→仍 FAIL、服务层 PARTIAL 不落 KPI 台账、HTTP `/probe` 子集返回
`verdict=PARTIAL`、CLI 子集退出码 3 且不占 `latest.json`、`coverage()` 拒绝 partial 台账、
markdown 原样输出 PARTIAL。

## 3. 计算结果（全部来自实跑，可复算）

**表 1 · 修复前后同一命令的对比（同一环境、同一规格、同一 commit 内容）**

| 项 | 修复前 | 修复后 |
|---|---|---|
| `verdict` | `FAIL` | **`PARTIAL`**（打印带 `subset：KPI 未评估`） |
| CLI 退出码 | 1 | **3** |
| 被探类状态 | REAL | REAL（不变） |
| `digest` | `033cebe04477d882` | `033cebe04477d882`（**不变**） |
| `value_digest` | `cdd02f9489f232d1` | `cdd02f9489f232d1`（**不变**） |
| `latest.json`（KPI 读槽） | 被子集覆盖 | **不写**（仅 stamped 取证文件） |

双指纹逐位不变 → 本轮**只改裁决词与台账提升规则，没有动一次观测值**，不存在「改口径刷绿」。

**表 2 · 全量运行仍必须是 PASS（回归面）**

```
$ python -m tools.external_tools.cli --out-dir /tmp/et_full run
suite=external-tools/KPI-TOOLS-5 target=5 real=10 shortfall=0 verdict=PASS   (exit 0)
digest=796a47bd3a898b3b  value_digest=8e7c6edae8634485
```

10/10 类 REAL，`digest` 与 11-12 的计量单（`KPI_TOOLS_5_measure_m16760.md`，`796a47bd3a898b3b`）
**一致**；`value_digest` 因 `version_control.commit_count` 随仓库提交增长（2211→2219）而漂移，
属预期环境漂移，已在 §5 记录。

**表 3 · ref-vs-got 判据（`tools/external_tools/benchmark.build`，本次全量台账）**

| 项 | 值 |
|---|---|
| 不变式条数 / 通过 | 27 / 27 |
| 可评相对误差条数 | 11 |
| 相对误差 <3% | 11 |
| 相对误差 <15% | 11 |
| 最大相对误差 | 2.82716e-16 |
| 仅绝对误差可评 | 10 |
| 无基准（结构/长度类） | 6 |

**表 4 · 测试**

| 套件 | 结果 |
|---|---|
| `tests/test_external_tools.py` + `test_external_tools_benchmark.py`（既有） | 24 passed |
| `tests/test_external_tools_partial.py`（本轮新增 8 条） | 8 passed |
| 合计（三文件同跑） | **32 passed**，0 failed |

**表 5 · 全量测试 A/B（外协方 loong64，同一命令；递交即排队，结果由集群工件回填）**

| 运行 | 基线（main `f5bbcdb2a5a7`） | 本分支（`agent/m17187`） |
|---|---|---|
| job | `cj1446`（`repo=TensorPlatform ref=main`） | `cj1449`（`stage_dir=本工作树`） |
| 命令 | `pytest -q tests/ --continue-on-collection-errors -p no:cacheprovider` | 同左 |
| 状态 | 递交后排队（节点各 128 核、排队 585 单），结果见 finish 记录 | 同左 |

**表 6 · 本机全量测试的环境事实（不是回归）**

| 项 | 值 |
|---|---|
| `pytest -q tests/`（本机 99，默认收集） | **收集期中断：48 errors**（中断点在收集阶段，未执行任何用例） |
| 错误类型分布（去重） | `ModuleNotFoundError: ops.taskplane`（`ops` 被同名目录遮蔽）20 · 相对导入越界 3 · 缺失可选依赖（sklearn 等）· 缺失模块/数据文件若干 |
| 与本单元改动的交集 | **0**：48 条全部位于未触碰的模块（`jobs/`、`src/`、`tools/coupling`、`runs/` 等），本环境缺依赖所致；CI（`.github/workflows/ci.yml`）另有依赖安装步骤 |

本单元因此以**三文件定向套件 32 passed**（表 4）+ **全量 CLI 实跑 PASS**（表 2）作主证据，
全仓 A/B 交给集群同镜像同命令对照（表 5），不在本机缺依赖的环境里自证清白。

## 4. 结果图

**本单不出图，理由**：改动是**后端裁决语义 + 台账提升规则**，没有场量、没有网格、没有随参数
演化的物理量；唯一可画的「量」是 27 条不变式的误差，而它们已经在表 3 里逐项列出且量级到
1e-16，画出来只是把表格换个形状（信息量不增）。按报告纪律显式声明：**纯后端语义修复，无适用
于云图/比较曲线的物理场，故不出图**。KPI 原有的覆盖度图/残差图由 `scripts/run_kpi_tools_5.py`
在需要时程序化生成，不属本轮改动面。

## 5. 注意点（坑 / 局限 / 适用范围）

1. **`partial` 的时序陷阱**：`ledger["partial"] = bool(only)` 原先写在 `harness.verdict(ledger)`
   **之后**，于是 `verdict()` 永远看不到子集标记。凡「派生量依赖台账字段」的地方都要保证字段
   先落账；本轮的修法是把 `partial` 下沉进 `run_suite()` 的返回值（结构性修复，不靠调用顺序）。
2. **KPI 读槽污染**：`latest.json` 是 `GET /coverage`、`/benchmark` 的唯一输入。子集跑写它会
   造成「一次 1 类探针 → 平台 KPI 恒为不达标」的隐性事故。现规则：**只有全量跑能提升**
   （`kpi_evaluated=true`），子集只留 stamped 取证。
3. **向后兼容**：只认 `PASS/FAIL` 的消费者（`scripts/run_kpi_tools_5.py`、`sensitivity.py`、
   门禁）遇到 `PARTIAL` 仍按「非 PASS」处理（退出码非 0，fail-closed），不会因此变绿。
4. **部署态仍读不到 KPI（未闭环，如实记录）**：对本环境的平台实例实测
   `GET /api/v1/external-tools/classes`、`/coverage`、`/benchmark` 均返回 **404**（该实例路由表
   根本不含本接入面，比 m16760 记录的 503 更早的镜像）。这是 `docs/kpi/KPI_TOOLS_5_measure_m16760.md`
   §⑤ 缺口 1（部署态必须 materialize 台账）的**未完成部分**，不在本单元改动范围（会牵动
   `backend/main.py` 启动钩子与调度，属另一功能点，须单独 PR）。
5. **干净检出仍 coverage=503**：`artifacts/` 被 gitignore，合并门也会滤掉工件，所以 main 上
   `coverage()` 恒为「没有台账」。这是设计上的 fail-closed（宁可不答，不猜数），但意味着
   KPI 数字必须靠 `python -m tools.external_tools.cli run` 现场复算——计量单必须随单附运行
   环境与双指纹，否则数字无法追认。
6. **`value_digest` 会随仓库本身漂移**：`version_control` 类不变式含 `commit_count`，任何新提交
   都会改变数值指纹（本轮 2211→2219）。结构指纹 `digest` 不受影响（只含 status 与不变式
   通过位）。跨单比对请用 `digest`，`value_digest` 只在同一 commit 内容下可比。
7. **`PARTIAL` 不是免罪符**：子集里任一 required 类 `FAIL` 或 `WITHHELD`，裁决仍是 `FAIL`；
   只有「被选的类全部 REAL」才给 `PARTIAL`。这条由两条独立测试钉住。

## 6. 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准 | 实测误差 | <3% ? |
|---|---|---|---|
| `numerical_quadrature.quad_value`（∫₀¹4/(1+t²)dt） | π = 3.141592653589793 | rel 2.83e-16 | **是** |
| `native_build_toolchain.h10_value`（gcc -O2 编译后执行 H10） | Python 同式计算结果 | rel 1.52e-16 | **是** |
| `relational_store.arithmetic_answer` / `series_sum` | SQL 整数恒等 Σi 与 Σi² | abs 0，rel 0 | **是** |
| `version_control.blob_id_reproduced` / `blob_catfile_roundtrip` | `git hash-object` 复算的 blob id | 逐位相等（1=1） | **是** |
| `json_contract_validation.invalid_inputs_rejected` | 3 条非法载荷必须被拒 | 3=3 | **是** |
| `hdf5_store.gzip_attr_preserved` / `gzip_filter_active` | gzip 过滤器存续 | 1=1 | **是** |
| 其余 11 条（残差型 / 结构型：`…_abs_err`、`png_magic_ok`、`server_version_len` 等） | 判据是绝对误差容限或结构断言 | abs_err ≤ 1.1e-9，或按位相等 | **是**（absolute 口径） |

**逐项回答**：可评估相对误差的 11 条**全部 <3%**（最大 2.82716e-16，离 3% 差 14 个数量级）；
其余 16 条为整数恒等/结构判据，**没有相对误差可算**（分母概念不成立），其绝对误差全部为 0 或
在声明容限内——不构成「未达标」，也不允许被记成「<3% 已验证」。27/27 条不变式通过，0 失败。

**诚实边界**：以上只证明「被选中的 10 类工具**在本工作树环境**里确实可用且数值对得上」，
不证明它们在**部署实例**里可用（§5 第 4 条：部署态 404，KPI 不可读）。