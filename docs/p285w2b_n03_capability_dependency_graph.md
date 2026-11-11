# 跨仓能力依赖图谱与级联失效分析（p285w2b-n03 / mission m12438）

**仓库**：TensorPlatform（零领域语义共性机制）
**分支**：`agent/m12438`
**日期**：2026-11-10
**证据等级自评**：REAL（机器判据全部通过，工件与判据数字全部可复算；模型假设的适用边界见 §5）

---

## 1. 案例介绍：问题与背景

### 1.1 物理/工程背景（抽象层）

平台的能力台账（`artifacts/capability_ledger/capability_ledger.json`，21 条能力）记录每条
能力当前被裁决的证据等级（`REAL / WITHHELD / MOCK / STUB / NOT_SUPPORTED`），既有的
`tools/capability_semantics` 记录了这些能力之间的**依赖边**（13 条）并做了环/孤立点检测。

能力台账是**离散裁决**，但真实世界里能力退化不是点事件而是**传播事件**：上游能力被裁定
从 REAL 降为 MOCK 时，所有"输出正确性依赖它"的下游能力的可信度都会随之衰减，而台账逐条
独立记等级，结构上看不出这件事。于是出现两类治理盲区：

1. **级联盲区**：上游降级后下游台账仍写 REAL，没人知道下游其实已经不可信；
2. **预警缺失**：等级变化是离散事件，但没有"变化 → 重算 → 受影响清单"的自动回路。

本节点要造的就是这条回路：**能力台账 → 依赖图 → 退化传播模拟 → 自动预警 + 只读 API**。

### 1.2 对象与参数（配置化的"参数表"）

| 项 | 值 | 出处 |
|---|---|---|
| 图节点数 | 21 | 台账条目数（`capability_ledger.json`） |
| 图边数 | 13 | 台账配置 `config/capability_semantics.json` 的 `depends_on` |
| 基准图边数 | 13（≥10 判据） | `artifacts/capability_graph/baseline_edges.json` |
| 等级词表 | NOT_SUPPORTED 0 / STUB 1 / MOCK 2 / WITHHELD 3 / REAL 4 | `config/capability_graph.json` |
| 自身置信度映射 | REAL 1.0 / WITHHELD 0.75 / MOCK 0.4 / STUB 0.15 / NOT_SUPPORTED 0.0 | 同上 |
| 默认依赖边权 `w` | 0.75 | 同上（`edges.default_weight`） |
| 推断边权 | 0.5（弱于声明边） | 同上（`edges.inferred_weight`） |
| 置信带 | REAL≥0.85 / WITHHELD≥0.60 / MOCK≥0.30 / STUB≥0.10 | 同上（`confidence_bands`） |
| 预设级联链 | 3 条（C1/C2/C3） | `artifacts/capability_graph/manual_chains.json` |
| 预警延迟预算 / API 预算 | 1000 ms / 500 ms | `performance` / `acceptance` |

所有能力 id、边、权重、阈值、判据数字一律**经配置文件注入**；代码内不含任何域词条
（§4.5 机器扫描 0 命中）。

---

## 2. 怎么做的：方法与流程

### 2.1 模块结构（全部新增于本仓，零领域语义）

```
config/capability_graph.json            判据/参数/来源路径（单一注入面）
tools/capability_graph/
  model.py     节点/边/快照 + 等级↔置信度显式映射（级联只能降级护栏）
  builder.py   图构建：台账(节点+等级) × 配置(声明边) × 证据文本(保守推断边)
  cascade.py   级联传播：own_drop → deg(max 传播) → eff_after → 等级回映射
  alerts.py    变化驱动预警：应用变化与重算在同一调用内完成，延迟端到端计量
  checks.py    Jaccard / 人工链误差 / 判据敏感性负对照 / 零领域语义扫描
  cli.py       audit（判据+工件+图）/ simulate（单次事件）/ show
backend/api/capability_graph.py         GET /api/v1/capability-graph 等 6 个只读端点
backend/main.py                         挂载（try/except，挂载失败不拖垮主应用）
tests/test_capability_graph.py          21 个机器判据测试
artifacts/capability_graph/             图快照 / 验收报告 / 预警流 / 两张图
```

### 2.2 级联传播模型（可手算，与实现解耦）

设 `d` 为 `n` 的直接上游，`w(d→n)` 为依赖强度：

```
own_drop(n) = clip((own_before - own_after)/own_before, 0, 1)   # own_before=0 时取 0
deg(n)      = max_{d ∈ deps(n)} w(d→n) · max(own_drop(d), deg(d))
D(n)        = max(own_drop(n), deg(n))
eff_after(n)= own_after(n) · (1 − deg(n))
eff_before(n) = own_before(n);   置信度相对衰减 ≡ deg(n)
等级回映射：置信带命中 + 不得高于节点自身证据等级（级联只能降级）
```

**为什么是 `max`（最坏短板）而不是乘积/求和**：能力对上游的正确性依赖是短板效应——任一
依赖不可信，输出即不可信。乘积会把两条各 0.5 的依赖夸大成 0.25 的"双重惩罚"，求和不满足
[0,1] 有界与单调。`max` + 边权同时满足有界、单调、可手算三条要求，且能处理有环图（deg 从 0
起单调松弛到不动点，迭代次数与收敛标志显式返回，不静默截断）。

**为什么置信带 + 等级上限**：传播结果必须能回写台账语义（等级），否则治理动作无处落地；
上限保证"级联不会把证据本来就弱的能力抬成 REAL"（机器可测，见测试
`test_cascade_never_upgrades_dependents`）。

### 2.3 三类边来源与可审计出处

| 来源 | 规则 | 权重 |
|---|---|---|
| `declared` | 既有台账配置的 `depends_on`（p266w1-n10 人工审计产物） | `default_weight` = 0.75 |
| `ledger_evidence_text` | 下游能力的证据记录里 ≥2 条独立记录提到上游能力 id 才成立（保守推断） | `inferred_weight` = 0.5 |
| 快照端点补登记 | 边端点若不是台账条目 → 登记为 `dangling` 节点（置信度 0，绝不当 REAL） | — |

每条边的 `sources` 列表与 `corroborations` 计数都写进 `graph_snapshot.json`，可逐条溯源。

### 2.4 预警回路

```
apply_level_change(cap, to_level)
  t0 = perf_counter()
  simulate(snapshot, event)        # 造派生快照，不改原图
  → alert{severity, affected[...path/hop/level_before→after], latency_ms, within_budget}
  t1 = perf_counter(); latency = t1 - t0
  持久化 append-only JSONL（每行带指纹 alr-xxxx 与实测延迟）
```

`severity` 规则（配置化）：任一受影响节点等级掉 ≥2 级 → `critical`；受影响节点 ≥3 或有掉级
→ `high`；有其他变化 → `medium`；无退化（纯升级/无变化）→ `info`。**超预算的预警照常输出但
标记 `within_budget=False`**——掩盖慢预警等于掩盖故障。

### 2.5 只读 API

`GET /api/v1/capability-graph`（图快照：节点含等级/自身置信度、边含权重/来源、摘要、最近
预警、判据自检）、`/edges`、`/alerts`、`/metrics`、`POST /simulate`（级联模拟，只读）、
`POST /refresh`（重建缓存）。快照首次构建后缓存，缓存键 = 各来源文件 `mtime_ns` 指纹；
`simulate` 的 `elapsed_ms` 与 `/alerts` 的 `latency_ms`/`within_budget` 都回给调用方。

---

## 3. 计算结果（判据数字，全部程序化注入 `artifacts/capability_graph/cascade_acceptance.json`）

### 3.1 五项机器判据

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| 依赖图 Jaccard（vs 基准 13 条已知依赖） | ≥0.8 | **1.0** | PASS |
| 3 条预设级联链 与人工分析一致（最大相对误差） | ≤10% | **0.0%**（13/6/4 节点全集受影响清单也完全一致） | PASS |
| 预警端到端延迟（12 次事件最大值） | ≤1000 ms | **1.19 ms**（首次调用 1.19 ms；预算占用 0.12%） | PASS |
| `GET /api/v1/capability-graph` 响应 | ≤500 ms | **0.62 ms**（5 次：0.624/0.143/0.134/0.118/0.150；payload 8 363 B） | PASS |
| 零领域语义扫描（9 个代码文件） | 0 命中 | **0 命中** | PASS |
| pytest | ≥10 全绿 | **21 passed**（`tests/test_capability_graph.py`） | PASS |

环境：Python 3.11.6 / loongarch64 / Linux-6.6.52（判读注架构：与 GitHub CI x86 不同）。
图构建耗时 `build_ms` = 3.08 ms。

### 3.2 链 C1：`geometry.generate` REAL → MOCK（地震式，11 个节点受影响）

| 节点 | hop | 承接退化 deg | 有效置信度 after | 等级 before→after | 人工值误差 |
|---|---|---|---|---|---|
| geometry.generate | 0 | 0.000 | 0.400 | REAL→**MOCK** | 0.0% |
| geometry.hydro_curves | 1 | 0.450 | 0.550 | REAL→**MOCK** | 0.0% |
| geometry.list_models | 1 | 0.450 | 0.550 | REAL→**MOCK** | 0.0% |
| shiphull.generate | 1 | 0.450 | 0.550 | REAL→**MOCK** | 0.0% |
| geometry.verify_official | 1 | 0.450 | 0.550 | REAL→**MOCK** | 0.0% |
| stdmodels.lookup | 2 | 0.3375 | 0.6625 | REAL→WITHHELD | 0.0% |
| calc.resistance | 2 | 0.3375 | 0.6625 | REAL→WITHHELD | 0.0% |
| calc.stability | 2 | 0.3375 | 0.6625 | REAL→WITHHELD | 0.0% |
| dev.gate_5415 | 2 | 0.3375 | 0.6625 | REAL→WITHHELD | 0.0% |
| propeller.open_water | 3 | 0.253125 | 0.746875 | REAL→WITHHELD | 0.0% |
| ai.predict | 3 | 0.253125 | 0.000 | NOT_SUPPORTED→NOT_SUPPORTED | 0.0%（`confidence_zero_basis`） |
| gov.stats / misc.stubs | — | 0 | 1.0 / 0.15 | 不变（未受影响） | 0.0% |

预警：`severity=critical`，受影响清单 11 条，路径链逐条给出（例如
`geometry.generate → geometry.hydro_curves → calc.resistance → propeller.open_water`）。

### 3.3 链 C2 / C3（窄链，用来验"不该传播的没传播"）

| 链 | 事件 | 受影响集合 | 最大相对误差 | 反向校验 |
|---|---|---|---|---|
| C2 | `geometry.list_models` → MOCK | list_models, stdmodels.lookup, verify_official, dev.gate_5415（4） | 0.0% | `calc.resistance` 未被误伤（0.0 退化）✓ |
| C3 | `calc.resistance` → MOCK | calc.resistance, propeller.open_water, ai.predict（3） | 0.0% | `calc.stability` 未被误伤 ✓ |

### 3.4 判据敏感性负对照（防止"恒真判据"）

Jaccard 判据本身要能被破坏才算判据。对派生图做受控破坏：

| 破坏 | Jaccard | 仍通过 0.8？ |
|---|---|---|
| 删 1 条边 | 0.923 | 是 |
| 删 2 条边 | 0.846 | 是 |
| **删 3 条边** | **0.769** | **否** ← 首个破阈破坏 |
| 删 5 条边 | 0.615 | 否 |
| 反向 1 条边 | 0.857 | 是 |
| **反向 3 条边** | **0.625** | **否** |
| 全部边重复 | 1.000 | 是（去重正确） |

结论：判据具备鉴别力（`criterion_is_discriminative=true`），且去重/方向处理正确。

---

## 4. 结果图

图文件（程序生成，`tools/capability_graph/cli.py::render_figures`）：

1. `artifacts/capability_graph/figures/dependency_graph.png` —— 依赖图谱：按依赖深度分列布局，
   节点颜色 = 证据等级（绿 REAL / 黄 WITHHELD / 橙 MOCK / 紫 STUB / 红 NOT_SUPPORTED），
   箭头粗细 ∝ 边权 0.75。
2. `artifacts/capability_graph/figures/cascade_confidence.png` —— 三条链的逐节点有效置信度：
   蓝色横条 = 人工分析值，红色点 = 引擎输出；三条链子图标题标注各自最大相对误差（0.0%）。

两图均已在平台报告内嵌（figures 字段）。

---

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

### 5.1 局限一（最重要）：Jaccard 基准与派生图同源

基准图 13 条边来自既有台账配置（p266w1-n10 的人工审计产物），派生图的 `declared` 边也来自
同一文件。**因此 Jaccard=1.0 证明的是"加载 → 合并 → 去重 → 等级映射 → 序列化"链路无损，
不构成"机器独立发现依赖关系"的能力证据。** 为让判据非平凡，做了两件事：
(1) 破坏性负对照（§3.4）证明判据能抓到漏边/反向边；(2) 实现了独立的证据文本推断通道
（`ledger_evidence_text`），但**当前台账的证据文本从不引用其他能力 id，推断边实测 0 条**
（`edge_source_counts={"declared": 13}`）——推断通道只在合成数据的单测里被验证过。这是
诚实的能力边界：真正的独立边发现需要第三方基准（另仓/论文级依赖声明），本单不具备。

### 5.2 局限二：级联模型是"假设 + 手算自证"，不是"实验标定"

三条链的 0.0% 误差验证的是**实现与模型规格一致**（人工按 §2.2 公式逐跳手算 vs 引擎输出），
不是"模型与真实失效统计一致"。边权 0.75（所有边同权）是**未标定的先验**：物理上不同依赖
强度不同，标定需要历史降级事件数据，当前没有。已有事件样本数为 0 → 无经验似然可用。
因此本模块的定位是**治理可视化与传播假设机**，不是精度预测器。

### 5.3 局限三：`ai.predict` 这类"自身置信度为 0"的节点

该节点自身证据等级 NOT_SUPPORTED（置信度 0），被上游影响时有效置信度仍为 0 → 相对衰减
无定义。实现不去编造数字，而是回 `confidence_decay=null` + `confidence_zero_basis=true`
（测试 `test_cascade_marks_unmeasurable_decay_when_own_confidence_zero` 固化该行为）。这也
顺带暴露台账缺陷：一个 NOT_SUPPORTED 的能力仍被别的能力声明依赖。

### 5.4 局限四：主应用在本沙箱无法整体启动

`backend/main.py` 的导入链依赖 `cryptography`（本环境未装，`backend/tests/test_api.py` 亦因此
预失败，与本单改动无关）。故 API 判据用"独立 FastAPI app + `dependency_overrides` 注入鉴权"
的方式测（`test_api_returns_graph_within_budget` / `test_api_simulate_and_alerts`），
`main.py` 的挂载只做了语法与 try/except 结构保证。真机联调需在装齐依赖的环境复测。

### 5.5 踩过的坑（可复用）

1. **零领域语义扫描不能裸子串匹配 ASCII 短词**：`ice` 会命中 `service`、`ship` 会命中
   `relationship`，第一版扫描当场把自己的注释判红。修法：ASCII 词条用词边界正则
   `(?<![a-z0-9_])tok(?![a-z0-9_])`，CJK 词条沿用子串匹配。这本身是一条可复用的经验
   （见 `docs/` 蒸馏条目）。
2. **"受影响"不等于"等级变了"**：升级事件（STUB→REAL）第一版把事件节点也算进受影响清单，
   导致 `affected_count=1`、severity 误判。修法：受影响集合只收**有效置信度下降**的节点，
   事件方向（`degrade`/`upgrade`/`no_change`）与变更节点单列 `meta.direction`/`changed_ids`。
3. **`tools` 包名在服务进程里会被 `backend/tools` 影子包劫持**：API 端用
   `importlib.util.spec_from_file_location` 私有包名（`tp_capability_graph`）加载，与既有
   `api/capability_semantics.py` 同一套做法。

### 5.6 适用范围

适用：任何"能力条目 + 有向依赖"的台账（本仓 12 仓通用：能力 id 不透明，机制不认域）。
不适用：需要定量预测失效概率的场景（无标定数据）；跨仓真实交付依赖的自动发现（需另建
跨仓交付面扫描，本单 scope 外）。

---

## 6. 误差判定（逐项显式回答）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 依赖图边集 | 人工基准图 13 条（Jaccard） | 1.0（即交集/并集 = 13/13） | 边集误差 0 ≤3% ✔（同源说明见 §5.1） |
| 级联链 C1 逐节点退化度 | 人工手算（13 节点） | 0.0% | 是 ✔ |
| 级联链 C2 逐节点退化度 | 人工手算（6 节点） | 0.0% | 是 ✔ |
| 级联链 C3 逐节点退化度 | 人工手算（4 节点） | 0.0% | 是 ✔ |
| 受影响集合 | 人工清单 | 集合完全相等（0 漏 0 多） | 是 ✔ |
| 预警延迟 | ≤1000 ms 预算 | 1.19 ms（0.12% 预算） | 是 ✔ |
| API 响应 | ≤500 ms 预算 | 0.62 ms（0.12% 预算） | 是 ✔ |
| 零领域语义 | 0 命中 | 0 命中 | 是 ✔ |

**诚实声明**：上述"0.0% 误差"是**实现 vs 手算规格**的一致度，不是与实验/文献的精度对标；
本节点是治理机制（图 + 传播 + 预警），不存在物理场量，故无实验基准可对标（§5.2 已声明）。
Jaccard 的同源局限（§5.1）不粉饰：判据通过，但"独立发现依赖"这件事本单**未达成**。

---

## 7. 审计链

| 环节 | 工件 / 命令 | 说明 |
|---|---|---|
| 配置注入面 | `config/capability_graph.json` | 全部参数/阈值/来源路径 |
| 图快照 | `artifacts/capability_graph/graph_snapshot.json` | 21 节点 / 13 边 / 每边 sources+corroborations / 等级直方图 |
| 判据数字 | `artifacts/capability_graph/cascade_acceptance.json` | 五项判据 + 敏感性负对照 + 环境指纹 + 逐节点误差表 |
| 预警流 | `artifacts/capability_graph/alerts.jsonl` | 12 条 append-only 预警，含 `latency_ms` / `within_budget` / 受影响路径 |
| 人工基准 | `baseline_edges.json` / `manual_chains.json` | 每条边给 rationale；每条链给手算公式与数字 |
| 复算命令 | `python -m tools.capability_graph audit` | 一键重算全部判据与工件（退出码 = all_pass） |
| 单次模拟 | `python -m tools.capability_graph simulate --capability <id> --to-level MOCK` | 人读/机读（`--json`） |
| 测试 | `python -m pytest tests/test_capability_graph.py -q` | 21 passed |
| API | `GET /api/v1/capability-graph` (+ `/edges` `/alerts` `/metrics` `POST /simulate` `/refresh`) | 只读；缓存键=来源 mtime 指纹 |

## 8. 后续（本单未做的部分）

1. 边权标定：需要历史降级事件样本（当前 0 条）；标定前 0.75 是显式先验而非经验值。
2. 独立依赖发现：跨仓交付面扫描（真实 import/调用图）→ 与声明边做三方对照。
3. 预警接通知渠道（alertmanager 已有 webhook 基础设施）——本单只做回路与落盘，不接外呼。
4. `backend/main.py` 真机联调（本沙箱缺 `cryptography`，见 §5.4）。