# 求解器能力自动发现与注册（capability auto-discovery & registration）

> 战役节点 `p348w2-n03` · 落库 TensorPlatform · 代码 `tools/capability_discovery/`
> 产物 `artifacts/capability_discovery/` · 证据分级：本工具链自身不自称 REAL，
> 它只决定**别人**的能力该记什么等级。

---

## 1. 案例介绍：为什么需要"自动发现"

平台原有的能力台账有三条通路，**全部要求能力先被人登记**：

| 既有模块 | 做什么 | 前提 |
|---|---|---|
| `src/capability_ledger_auto/` | 按 `config/capabilities.json` 的模式规则扫代码/测试/报告，判等级 | 能力定义必须先写进注册表 |
| `tools/capability_upgrade/` | 证据 ingest → 判据机读裁决 → 自动升级 | 能力 slot 与 `criteria` 必须先写进注册表 |
| `tools/audit/capability_ledger_upgrade.py` | 能力卡片升账（REAL 必须挂 sha256 工件） | 卡片必须先被人写出来 |
| `tools/audit/capability_ledger.py` | 自述等级 vs 报告判词对账 | 台账条目必须先存在 |

于是出现结构性瓶颈：求解器仓写了真实算法、跑过验证，但**没人记得去台账里加一行**，
台账就长期落后于代码现实；而下一个人看台账，会以为能力不存在（或停在旧等级）。

本节点补上闭环的前半段：让**测试自己声明能力**，由平台自动扫描、定级、登记、
出差异报告，最后把新发现的能力直接灌进既有的 `capability_upgrade` 自动升级管线。
关键设计取向：自动登记只允许**保守低估**——机器可以替人把"没登记的能力"记成
`WITHHELD`，但不允许替人把"没验证过的能力"记成 `REAL`。

### 仓内定位（零领域语义）

本模块不出现任何能力名、物理量、船型或学科词条。能力身份 100% 来自被扫描测试源码里
的 marker 元数据；`tools/capability_discovery/*.py` 里出现的 `synth.*` 只在**测试夹具**
（`fixtures/`）中，用于自证闭环，并用"换一套词汇结构同构"的对照证明扫描器认的是语法
不是语义（见 §5.2）。

---

## 2. 怎么做的

### 2.1 能力声明约定（求解器仓侧，一种写法即可）

```python
import pytest

@pytest.mark.capability(
    id="solver.quadrature.trapezoid",     # 能力唯一标识（必填，字面量）
    label="trapezoid rule vs closed form",# 可选，人类可读
    level="REAL",                          # 可选，声明等级（不声明 → 默认 WITHHELD 封顶）
    anchors=["ref:closed_form:quadratic"], # 可选，验证锚（解析解/文献/基线标识）
    params={"n_samples": {"min": 32, "max": 4096}},  # 可选，参数区间
    tags=["numeric"],                      # 可选
)
def test_trapezoid_matches_closed_form():
    ...
```

识别的书写格式（C1 判据要求 ≥5，本实现静态 6 种 + 运行期 1 种）：

| # | `marker_format` | 写法 |
|---|---|---|
| 1 | `function_kwargs` | `@pytest.mark.capability(id=..., level=...)` |
| 2 | `function_positional_id` | `@pytest.mark.capability("some.id", level=...)` |
| 3 | `function_bare` | `@pytest.mark.capability`（无参，id 由测试节点名派生） |
| 4 | `class_kwargs` | 类级 marker，类内 `test_*` 继承 |
| 5 | `module_pytestmark` | `pytestmark = [pytest.mark.capability(...)]` |
| 6 | `param_marks` | `pytest.param(..., marks=pytest.mark.capability(...))` |
| 7 | `runtime_marker` | 插件在运行期采集到的 marker（可解非字面量 id） |

结局修饰符 `@pytest.mark.skip/skipif/xfail` 被识别并**只影响等级上限**，不影响能力身份。

元数据必须是**字面量**。非字面量（如 `id=SOME_CONST`）不猜测：静态扫描记一条
`diagnostics` 交人审，运行期插件用 pytest 求值后的真值补上（`runtime_marker`）。

### 2.2 五段流水线

```
测试源码 ──markers.MarkerScanner(AST)──▶ DiscoveryRecord ×N
        ──extract.extract_features───▶ CapabilityFeature（按 id 聚合，区间合并）
pytest  ──plugin（-p tools.capability_discovery.plugin）──▶ outcomes.json
        ──grading.grade_feature（策略即数据）──▶ Grade（等级 + 逐条理由）
        ──store.Ledger.apply_grade（哈希链 + 幂等 + 回滚）──▶ capability_ledger
        ──pipeline.report_markdown──▶ 差异报告（JSON + Markdown，人审入口）
        ──export──▶ capability_upgrade 注册表 + 证据 feed（C7）
```

### 2.3 定级规则（`config/capability_discovery/policy.json`，策略即数据）

| 测试结局 | 等级上限 | 理由 |
|---|---|---|
| `passed` | 测试**声明**的等级 | 唯一能升到 REAL 的结局 |
| `passed` 但未声明等级 | `WITHHELD` | "能跑"不是"说得出对什么" |
| `passed` 且声明 REAL 但无锚点 | `WITHHELD` | 没有对照就没有精度证据 |
| `failed` / `error` | `MOCK` | 声明被自家测试证伪（与既有 grader 口径一致） |
| `skipped` / `xfailed` / `xpassed` / `not_run` | `WITHHELD` | 没跑出结果就没有证据 |
| 挂了 skip/skipif/xfail 修饰符 | `WITHHELD` 封顶 | 预期失败 ≠ 验证通过 |
| 同一能力被声明成多个等级 | `WITHHELD` 封顶 | 声明冲突需人审 |
| 源文件缺 sha256 | `WITHHELD` 封顶 | 来源不可追溯 |

策略文件的 sha256 会写进台账（`policy_digest`）与每条 Grade，等级可回溯到"用哪套规则定的"。

### 2.4 台账结构与回滚

条目字段：`capability_id / label / level / state / first_seen / updated_at / revision /
grade{level,outcome_state,reasons,confidence,policy_digest} /
evidence{declared_level,anchors,params,tags,tests,sources,marker_formats,outcomes,declared_levels_seen,fingerprint} /
level_history[] / retraction / provenance{run_id} / entry_hash`。

* **幂等**：证据指纹未变则不写盘、不追加链记录 → 重复运行台账字节不变；
* **哈希链**：每次写操作（`registered/updated/retracted/restored`）追加
  `prev_hash→hash` 记录，`verify_chain()` 同时校验条目自哈希与链记录，
  手改一个字节就会被抓出来；
* **回滚（C6 判据的回滚机制）**：`retract --reason ...` 撤回并留痕（人/时间/原因/原等级），
  `restore --reason ...` 恢复；**自动登记不覆盖人工撤回**（撤回态能力再次被扫到时只报
  `held_retracted`，不会悄悄复活）；
* **缺失只报告不删除**：测试被删/丢 marker 的能力进 `missing` 列表，等人裁决。

### 2.5 CLI

```bash
PYTHONPATH=. python -m tools.capability_discovery.cli scan      --roots tests/
PYTHONPATH=. python -m tools.capability_discovery.cli register  --roots tests/ \
    --outcomes artifacts/capability_discovery/outcomes.json \
    --ledger artifacts/capability_discovery/ledger.json [--write]
PYTHONPATH=. python -m tools.capability_discovery.cli retract   --ledger ... --capability X --reason "..."
PYTHONPATH=. python -m tools.capability_discovery.cli restore   --ledger ... --capability X --reason "..."
PYTHONPATH=. python -m tools.capability_discovery.cli verify    --ledger ...
PYTHONPATH=. python -m tools.capability_discovery.cli export-upgrade --ledger ... --validate
PYTHONPATH=. python -m tools.capability_discovery.demo          # 端到端演示 + 产物落盘
```

---

## 3. 计算结果

一次端到端演示（命令 `PYTHONPATH=. python -m tools.capability_discovery.demo`，产物全部
由程序写入 `artifacts/capability_discovery/`，本表数字从中读取，零手抄）：

| 量 | 值 | 出处 |
|---|---|---|
| 现场运行测试 | `1 failed, 9 passed, 1 skipped, 1 xfailed`（夹具里 1 例故意失败，用于验证 MOCK 定级） | `fixture_index.json:test_run.summary_line` |
| marker 记录（静态 13 + 运行期 1，合并后） | 14 条 | `discovery_report.json:inputs.records` |
| 运行期补充记录 | 1 条（非字面量 id 的能力） | `inputs.runtime_records_added` |
| 发现能力数 | 10 | `summary.discovered` |
| 首次登记 / 二次运行 | 新增 10 / 新增 0、更新 0、未变 10 | `summary.added`、`fixture_index.json:idempotency` |
| 台账幂等 | 两次 sha256 相同（字节不变）、链长 10→10 | `idempotency.ledger_sha256_first/second` |
| 等级分布 | REAL 3 / MOCK 1 / WITHHELD 6 | `summary.levels` |
| marker 格式覆盖 | 7（6 静态 + 1 运行期） | `summary.marker_formats` |
| 扫描诊断 | 1（非字面量 id，交人审） | `summary.diagnostics` |
| 台账哈希链 | OK（10 条写操作） | `summary.chain_message` |
| 导出联动 | 10 slot + 10 判据被 `capability_upgrade` 接受 | `fixture_index.json:upgrade_linkage` |

逐能力等级（`fixture_index.json:capabilities`）：

| 能力（夹具，合成词汇） | 结局 | 等级 | 依据 |
|---|---|---|---|
| `synth.grid.trapezoid` | 2 passed | **REAL** | 声明 REAL + 2 锚点 + 通过 |
| `synth.grid.richardson` | passed | **REAL** | `param_marks` + 声明 REAL + 锚点 |
| `synth.algebra.vector_norm` | 2 passed | **REAL** | 类级 marker + 声明 REAL + 锚点 |
| `synth.case.contradiction` | failed | **MOCK** | 自家测试证伪声明 |
| `synth.claims.no_anchor` | passed | WITHHELD | 声明 REAL 但无锚点 |
| `synth.case.skipped` | skipped | WITHHELD | 未执行 |
| `synth.case.xfail` | xfailed | WITHHELD | 预期失败 |
| `synth.family.module_scope` | 2 passed | WITHHELD | 模块级声明，未声明等级 |
| `synth.dynamic.value` | passed | WITHHELD | 仅运行期发现，无锚点 |
| `derived.test_synthetic_core.test_bare_marker_case` | passed | WITHHELD | 裸 marker，未声明等级 |

### 判据对照（机器可算）

| 判据 | 要求 | 实测 | 证据 |
|---|---|---|---|
| C1 测试扫描器 | 识别 ≥5 种 marker 格式 | **7 种**（≥5 ✓） | `TestMarkerFormats::test_at_least_five_formats_recognised` |
| C2 能力提取 | 正确提取能力特征 | 10/10 能力、等级全对（见上表） | `test_levels_match_hand_computed_expectation` |
| C3 台账登记 | 新增能力字段完整 | 8 顶层 + 10 evidence 字段断言通过 | `TestRegistration::test_new_capability_registered_with_complete_fields` |
| C4 差异报告 | JSON 含新增/更新/缺失 | `schema=capability_discovery.report.v1`，六列表齐 | `TestReport::test_report_shape_and_counts` |
| C5 零领域语义 | 仅解析 marker 元数据 | 换全套词汇后结构同构（10 vs 10、签名逐项相等） | `TestZeroDomainSemantics::test_structure_is_isomorphic_under_vocabulary_swap` |
| C6 现有测试不下降 | 通过数不减 | 同一 129 项存量子集：121/129 → 122/129 通过，回归 **0**、消失 **0** | `artifacts/capability_discovery/c6_pytest_baseline.json` |
| C7 升级管线联动 | 可进自动升级管线 | 10 slot + 10 判据被对方 `load_registry/apply_registry` 接受；证据可 `ingest` | `TestUpgradePipelineLinkage` |

---

## 4. 结果图

`artifacts/capability_discovery/figures/discovery_summary.png`（由
`tools/capability_discovery.figures` 从台账与差异报告 JSON 直接读数生成）：

* 左：等级分布——自动登记后 3 REAL / 1 MOCK / 6 WITHHELD，高估面为零；
* 中：逐能力测试结局构成——能看出哪些能力只有部分用例通过/被跳过；
* 右：marker 格式覆盖——7 种格式，C1 判据的图形证据。

---

## 5. 注意点（踩过的坑与局限）

### 5.1 踩过的坑

1. **参数化用例的 nodeid 不匹配**：静态扫描看不到 `[8]` 这类参数后缀，运行期 nodeid 带后缀，
   最初直接匹配导致这些能力被判 `not_run`。修法：索引时剥掉 `[...]` 建后缀键，
   多条运行记录落同一键时取**矛盾度最高**的结局（宁可低估）。
2. **运行期发现会"越界"**：只扫 `tests/foo.py` 却传了整场 session 的结局文件时，
   会凭空发现别处能力。修法：运行期记录同样受 `roots` 范围约束。
3. **`pytest.param` 的修饰写法**：`pytest.param(...)` 与 `pytest.mark.param` 的链式名不同，
   判据写死在 `mark` 上导致 `param_marks` 漏检（C1 只剩 5 种）。修法：接受
   `pytest.param` / `mark.param` 两种链路。
4. **自哈希校验的空洞**：只比对"条目 entry_hash 字段 vs 链记录"时，手改条目内容
   （不改 entry_hash 字段）检不出来。修法：`verify_chain()` 重算条目正文哈希。
5. **夹具套件污染平台测试**：夹具里有故意失败的用例，若放在 `tests/fixtures/` 会被
   `pytest tests/` 收走并计入失败。修法：夹具放 `tools/capability_discovery/fixtures/`
   并加 `conftest.py: collect_ignore_glob=["*"]`，需要真跑时 `--noconftest` 或复制到临时目录。
6. **marker 未注册**：`--strict-markers` 下未注册的 `capability` marker 会直接报错。
   已在 `backend/pyproject.toml` 的 `markers` 注册一行（零领域语义的通用说明）。

### 5.2 局限（适用范围）

* **不能验证算法正确性**：本工具验证的是"测试声明了能力且该测试通过"，不是"算法算得对"。
  物理正确性仍要 `anchors` 指向的解析解/文献/基线去背书，这也是为什么无锚点不给 REAL。
* **参数化实例被折叠**：同一能力下 N 个参数用例在静态视图是 1 个节点（运行期补回结局，
  但 `tests` 计数按节点算），细粒度用例数需看 `outcomes`。
* **诊断而非猜测**：非字面量元数据静态扫不到，只能靠运行期；若 CI 不跑测试，
  这类能力会缺席（`diagnostics` 里明确列出）。
* **与 `src/capability_ledger_auto/` 互补而非替代**：后者按注册表模式扫描"某能力有没有
  实现/测试/报告"，本模块负责"有哪些能力、证据强度多少"。两者可以并联使用。
* **等级不是概率**：`confidence` 是确定性启发式（通过率 × 锚点因子 × 等级因子），
  只用于排序，不可当概率读。

---

## 6. 误差判定

本节点是**平台机制**任务，不含物理场量，因此没有"误差 <3%"这一类的物理基准。
按报告纪律显式声明替代基准与判定：

| 判定项 | 基准 | 实测 | 是否满足 |
|---|---|---|---|
| 能力提取精度 | 手算等级表（§3 逐能力表，依据 §2.3 规则逐条推导） | 10/10 一致，不一致 0 项 | 是（0% 误判，<3% ✓） |
| 等级高估 | 保守口径下不得有未验证能力被判 REAL | REAL 3 项均有 passed + 锚点 + 声明；无锚点/跳过/失败项均 ≤ WITHHELD | 是（0 项高估） |
| 登记幂等漂移 | 同输入两次运行台账 sha256 相同 | `1887f762…` == `1887f762…`，链长 10→10 | 是（0 字节漂移，<3% ✓） |
| 平台测试回归 | `git archive HEAD` 干净树跑同一 129 项子集 | 121/129 → 122/129 通过（既有 8 项离线对账失败属 HEAD 既有，1 项转通过，0 项转失败），新增 86 项全通过 | 是（0 回归） |

未覆盖/未完成部分（如实申报）：
* 未在真实求解器仓（TensorLBM/FEM/FVM…）的测试套件上做规模化登记实验——本节点的
  验收范围是机制 + 合成夹具；规模化登记属于后续战役（需要各仓先加 marker）。
* `xpassed`（xfail 却通过）目前一律压到 WITHHELD，尚未实现"意外通过需复核"的工单流。
* 台账为单文件 JSON；容量到万级条目时需要换 SQLite（与 `capability_upgrade` 的 store 对齐）。

---

## 7. 测试与复现

```bash
PYTHONPATH=. python -m pytest -q tests/test_capability_discovery_markers.py \
    tests/test_capability_discovery_grading.py tests/test_capability_discovery_ledger.py \
    tests/test_capability_discovery_pipeline.py tests/test_capability_discovery_plugin.py
# 86 passed

PYTHONPATH=. python -m tools.capability_discovery.demo      # 重新生成全部产物
PYTHONPATH=. python -m tools.capability_discovery.figures   # 重新生成图
```

审计链：`artifacts/capability_discovery/ledger.json` 的 `chain` 数组（10 条写操作，
`prev_hash→hash` 逐条可验）、`discovery_report.json`（差异报告）、
`outcomes.json`（现场 pytest 结局）、`exported_upgrade_registry.json`（C7 导出）。