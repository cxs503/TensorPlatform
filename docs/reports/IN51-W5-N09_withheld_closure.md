# WITHHELD 欠账闭环成熟化：登记库↔门禁双向绑定 · 数字化不确定度预算 · 缺口自动派单

**节点** IN51-W5-N09 · **仓** TensorPlatform · **域** 平台共性机制（零领域语义）
**合成场景** SYN-* 虚拟域（SYN-Repo-A/B/C、SYN-DS0xx、SYN-QUANTITY-ALPHA、5 天战役时钟）
**产物** `tools/vv/withheld_closure.py`（1799 行）· `backend/migrations/114_withheld_closure_binding.sql`
· `tests/vv/test_withheld_closure.py`（32 用例）· `artifacts/withheld_closure/`（9 JSON + 5 PNG）
**上游** IN50-W4-N09 `tools/vv/withheld_gates.py`（三态清偿 + 契约哈希 + 审计链 + KPI + 粗派单）

---

## ① 案例介绍（问题设定与参数表）

平台正典把验收判决分三态 `PASS < WITHHELD < WARN < FAIL`。IN50 已把 WITHHELD 做成**可清偿债务**
（债权人=门、标的=参考数据集、账龄=天），但留了三处**成熟化缺口**，本节点逐条闭合：

| # | IN50 缺口 | 后果（可观测） | 本节点机制 |
|---|---|---|---|
| A | 只有门→数据集**单向边**，登记册不知道自己被谁引用 | 数据集重登/改名后旧门静默漏绑；门工件被删后登记册挂幽灵引用；无法回答"这张表支撑哪几门" | 绑定索引一等对象 + 双向边集合 + 三类漂移分类 + 定向修复 |
| B | 不确定度带只有 `u_exp、u_dig` 两项，且**允许不确定度把超差结果判 PASS** | "测不准"被洗成"通过"：本例 6 门 `E=0.20 > tol=0.15` 却因 `k·u_c/D=0.60` 变 PASS | 四分量预算（`u_dig/u_exp/u_num/u_interp`，可带相关系数）+ 双路复算 + 分辨力判据 |
| C | 派单只识别 4 类 reason_code，且篡改态**根本进不了派单表** | 43 个真实缺口只派 27 张工单（准确率 70.9%），漏派分辨力/过期/篡改/孤儿 16 项 | 9 类缺口决策表（上游优先）+ 幂等工单 + 标注语料评测 |

**为什么落在 TensorPlatform**：三件事都是"证据链机制"（绑定、不确定度核算、派单），与船型/物理量无关。
本文件与测试**不含任何学科词条**：域名一律 `SYN-*`，真实名称由调用方工件注入（零领域语义铁律）。

### 场景参数表（全部可程序化复算）

| 项 | 设定 |
|---|---|
| 战役时钟 | `T0 = 2026-11-01T00:00:00Z`；门声明 T0；数据集登记 T0+1d；预算重判 T0+2d；过期重登记 T0+3d；评测 T0+5d |
| 门 | 53 门，分布 SYN-Repo-A/B/C，`quantity=SYN-QUANTITY-ALPHA`，`tol=0.15`、`k=2.0`（篡改 3 门基线 `tol=0.10`） |
| 数据集 | 47 张（44 张被引用 + 2 张孤儿 + 1 张模板复用），参考表 6 点线性 `y=1+x`（`x∈[0,1]`），`at=0.5 ⇒ r=1.5` |
| 不确定度分量 | `u_exp=0.01`（绝对）、`u_dig=1%`（相对，弱精度模板 30%）、`u_num=1%`、`u_interp=0.2%` |
| 预算评测语料 | 48 例，2–4 分量，量级 `10^-3..10^-0.5` log-uniform，ρ∈{∅, +0.3, −0.4, +0.8} |
| 派单标注语料 | 55 例（53 门 + 2 孤儿数据集），9 类缺口 + "无缺口"（含 4 例**诚实 FAIL**，必须不派单） |
| 漂移自检 | 注入 UNBOUND/GHOST/STALE 各 1 条 → 测覆盖率 → `reconcile_bindings()` → 再测 |

---

## ② 怎么做的（方法 / 流程 / 工具链）

### 2.1 机制流程

```
门工件 ──declare──> 门账本(WITHHELD gates, 唯一真相) ──INSERT 触发器──> 绑定索引
   │                     │                                              │
   │                     └──── 参考登记册(DOI/sha256/method/u_dig) <────┘ 数据集登记 → PENDING→BOUND
   │                                    │
   └──────────────> 不确定度预算 u_c=sqrt(Σu²+2Σρuu), U=k·u_c, band=max(tol,U/D)
                                        │
                            三态判决（分辨力不足 ⇒ WITHHELD，不得 PASS）
                                        │
                        缺口分类（上游优先 9 类）→ 幂等派单 WO-hash(repo|class|target)
                                        │
                判据 A 覆盖率 100% · 判据 B 双路复算 <1% · 判据 C 准确率 >95%
```
（流程图见 `figures/fig1_mechanism_flow.png`）

### 2.2 双向绑定：边集合与三类漂移

两侧各自独立成边，**覆盖率按对称差计算**，杜绝"只看一侧就宣称 100%"：

```
forward = {(gate_id, dataset_id, artifact_sha256)}      取自门账本（门工件为准）
reverse = {(gate_id, dataset_id, artifact_sha256)}      取自绑定索引（登记册反查视图）
coverage = |forward ∩ reverse| / |forward ∪ reverse|
drift    = UNBOUND(门有索引无) / GHOST(索引有门无) / STALE(同门但数据集或指纹不同)
```
结构层兜底：门 INSERT 即建边、数据集登记/撤销即 `PENDING↔BOUND`、门 DELETE 即撤边（DDL 触发器，
SQLite 与 PG 同构）；API 侧 `reconcile_bindings()` 幂等修复。`registration_coverage` 与
`binding_coverage` **分开计**——绑定绿不得用来掩盖"数据集还没登记"。

### 2.3 数字化不确定度预算与"不得洗成 PASS"

| 分量 | 出处 | 口径 |
|---|---|---|
| `u_dig` | 登记册 `digitisation.uncertainty.u_dig`（或逐点 σ 插值） | 相对→绝对按 `|r|` 折算 |
| `u_exp` | 门工件 `measurement.u_exp / u_exp_rel` | 绝对优先 |
| `u_num` | 门工件 `budget.components.u_num`（数值离散/迭代） | 相对或绝对 |
| `u_interp` | 登记册 `digitisation.interp_uncertainty` | 相对 |
| 附加 + ρ | 门工件 `budget.components[] / budget.correlations[]` | ρ∈[−1,1]，越界即报错 |

合成：`u_c = sqrt(Σu_i² + 2Σ_{i<j} ρ_ij u_i u_j)`，`U=k·u_c`，`band_unc=U/max(|r|,floor)`。
**双路复算**：`auto`（浮点 `math.fsum` + 成对项显式展开）vs `manual`（`Decimal` 手算台账，
分量记 3 位有效数字、按名排序固定顺序折叠），另加 GUM Supplement 1 的 Monte-Carlo 第三方核对
（特征分解采样，报告 MC 相对标准误）。

判决纪律（相对 IN50 的**收紧**）：`|E|≤tol ⇒ PASS`；`band_unc>tol 且 |E|≤band_unc ⇒
WITHHELD/INSUFFICIENT_RESOLUTION`（试验无分辨力，既不能通过也不能失败）；其余 `FAIL`。
IN50 的 `WITHIN_UNCERTAINTY ⇒ PASS` 规则**废止**（见 ③ 的 6 门对照）。

### 2.4 缺口决策表与幂等派单

上游优先（先补债的源头，再复算下游）：`TAMPER(1) → NO_DATASET(2) → PROVENANCE(3) → MEASUREMENT(4)
→ RANGE(5) → RESOLUTION(6) → STALE(7) → BINDING(8) → ORPHAN(9)`。
工单号 `WO-sha256(repo|gap_class|target)[:12]`：跨波、跨进程可复算，重跑不产生重复工单。
篡改态**从审计链结构判定**（★★★★ 见 ⑤ 坑 1），并新增 `clear_tamper()` 走 `TAMPER_RESET` 审计行。

### 2.5 工具链

`python3`（stdlib + sqlite3；`numpy`/`matplotlib` 仅用于 MC 与出图，模块本身不硬依赖）
· `pytest tests/vv/test_withheld_closure.py`（32 用例）· 复跑命令：

```bash
python3 tools/vv/withheld_closure.py --demo --out artifacts/withheld_closure
python3 -m pytest tests/vv/test_withheld_closure.py -q
```

---

## ③ 计算结果（程序化注入，零手抄）

三判据汇总（`artifacts/withheld_closure/closure_summary.json`）：

| 判据 | 目标 | 实测 | 判定 |
|---|---|---|---|
| A 登记库↔门禁双向绑定覆盖率 | = 100% | **100.00%**（53 边，`|F∩R|=|F∪R|=53`，三类漂移各 0） | PASS |
| B 预算自动核算 vs 手工台账 | < 1% | **0.4463%**（48 例最大；均值 0.0993%；精确手算 4.42×10⁻¹⁵） | PASS |
| C 缺口派单准确率 | > 95% | **100.00%**（55/55，误派 0） | PASS |
| D 对照 IN50 基线 | 三项均严格更优 | 派单 70.9%→100%；洗白 PASS 6→0；反向查 无→有 | PASS |

### 3.1 绑定覆盖率与漂移自检

| 指标 | 值 |
|---|---|
| 门数 / 登记数据集 | 53 / 47 |
| forward / reverse / 双向绑定 / 登记册反向视图 | 100% / 100% / **100%** / 100% |
| 数据集登记完成度（37/53 门可用） | 69.81%（与绑定覆盖率分开计，未混算） |
| 漂移注入后 | 覆盖率 **94.44%**，UNBOUND/GHOST/STALE = 1/1/1，`bidirectional_ok=false` |
| `reconcile_bindings()` 后 | 3 条定向动作（BIND/REBIND/DROP_GHOST），覆盖率回到 **100%**，三类漂移归零 |

### 3.2 不确定度预算（48 例）

| 量 | 值 |
|---|---|
| 自动 vs 手工台账 相对误差 | 最大 **0.4463%**，均值 0.0993%（判据 1%，余量 2.2×） |
| 自动 vs 精确手算（15 位 Decimal） | 最大 **4.42×10⁻¹⁵**（浮点噪声级，证明同一公式） |
| 解析 vs Monte-Carlo（n=6×10⁴） | 最大相对差 **0.548%**，MC 相对标准误最大 0.290% ⇒ 48/48 落在 3σ 内 |
| 预算台账行数 / 分辨力不足门数 | 23 / **6**（`k·u_c/D=0.6005 > tol=0.15`） |
| 相关分量反例 | ρ=+1、`u1=u2=1 ⇒ u_c=2.000`（若 `(a,b)/(b,a)` 双计会得 √6=2.449） |

### 3.3 判决与缺口（53 门 / 43 缺口 / 43 张工单）

| verdict | 门数 | 其中带缺口 |
|---|---|---|
| PASS | 16 | 5 过期（G_STALE）+ 3 篡改未裁决（G_TAMPER） |
| WITHHELD | 33 | 8 未登记 + 8 出处不全 + 5 缺实测 + 6 量程不足 + 6 分辨力不足 |
| FAIL | 4 | 0（诚实 FAIL 不是欠账，**不得派单**） |

**可采信 PASS（当前）= 8 门**：16 个 PASS 里 5 门参考数据已更新（须重判）、3 门处于
`TAMPER_BLOCKED` 审计态（IN50 判决列仍写 PASS）。这是本节点暴露的真实风险面——**表面通过率
30.2%（16/53），真实可采信率 15.1%（8/53）**。

工单（43 张，幂等复跑 `order_id` 序列完全一致）：`NO_DATASET 8 · PROVENANCE 8 · RANGE 6 ·
RESOLUTION 6 · MEASUREMENT 5 · STALE 5 · TAMPER 3 · ORPHAN 2`；优先级=上游序号
（P1 篡改 3 · P2 未登记 8 · P3 出处 8 · P4 缺实测 5 · P5 量程 6 · P6 分辨力 6 · P7 过期 5 ·
P9 孤儿 2），账龄 4.0/5.0 天（按 T0+5d 时钟）。
审计链自检：**248 行 / 53 条链 / 断裂 0**。

### 3.4 派单混淆矩阵（55 例，`figures/fig4_dispatch_confusion.png`）

| 金标 \ 预测 | 8 类缺口 | NONE |
|---|---|---|
| 每类缺口（8 类，47 例） | 对角全中（recall = precision = 1.00） | 0 |
| NONE（12 例：8 健康 + 4 诚实 FAIL） | **0（零误派）** | 12 |

### 3.5 与 IN50 基线对照（同一批场景重放）

| 指标 | IN50 基线 | 本节点 | Δ |
|---|---|---|---|
| 登记册反向查门能力 | 无（表结构不存在） | 有（索引 + 视图） | 新增能力 |
| 派单准确率 | 70.91%（27/55） | **100%**（55/55） | **+29.09 pp** |
| 派单缺口数 / 真实缺口数 | 27 / 43 | 43 / 43 | 漏派 16 → 0 |
| 被不确定度洗成 PASS 的门 | **6** | **0** | 消除 |
| 判决列 PASS 数（含不可采信） | 22 | 16 | 挤掉 6 个假绿 |
| 可采信 PASS（无在案缺口） | 22（未做审慎核验） | **8** | 暴露 14 门欠账 |

---

## ④ 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| 机制流程图 | `figures/fig1_mechanism_flow.png` | 证据链两侧 + 双向绑定索引 + 预算判决 → 缺口分类 → 幂等派单；底部三项判据 |
| 双向绑定覆盖率 | `figures/fig2_binding_coverage.png` | forward/reverse/双向/登记完成度 + 三类漂移 检测→修复 前后对比 |
| 预算双路复算 | `figures/fig3_budget_parity.png` | 左：自动 vs 手工台账 1:1（±1% 判据带）；右：自动↔手工、解析↔MC 误差分布（对数轴） |
| 派单混淆矩阵 | `figures/fig4_dispatch_confusion.png` | 8 类缺口 × NONE，对角全中、零误派 |
| 分辨力判据 | `figures/fig5_resolution_vs_tolerance.png` | 逐门 `k·u_c/D` vs `tol`，红色 6 门分辨力不足（只能 WITHHELD） |

---

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

**坑 1（最重要）：IN50 的篡改判定在判决列上是隐形的。** `withheld_gates.declare_gate()` 拒绝
放宽 `tol` 时只写 `TAMPER_BLOCKED` 审计行，**不覆盖 `verdict` 列**——本例 3 门判决列仍是 PASS。
若按判决列判定缺口，这 3 门永远不会被派单（IN50 基线正是如此）。本节点改为**审计链结构判定**
（出现 `TAMPER_BLOCKED` 且未 `TAMPER_RESET` 即粘性篡改态），并让自动重判**跳过**篡改门，
防止"一次自动重判就把篡改痕迹洗白"。

**坑 2：过期检测会被自动重判抹掉。** 首版在全链路末尾又跑了一次预算重判，`last_judged_at`
被刷新到 T0+5d，于是 T0+3d 的重登记不再构成"过期"——5 门 G_STALE 全部消失。改为使用
T0+2d 的判决快照后才恢复。

**坑 3：相关分量在自动路径被双计。** `_normalise_correlations` 为便于反查同时存了 `(a,b)` 与
`(b,a)`，自动路径遍历字典时把同一对加了两次（ρ=0.3 时 u_c 偏大 8%）。用 ρ=+1、`u1=u2=1`
的解析反例（必须等于 2 而不是 √6）把它钉成回归测试。

**坑 4：MC 标准误绝对/相对混用**，导致 35/48 例被误判"超 3σ"。改为比较相对误差 vs 相对标准误。

**坑 5：触发器会改变 `reconcile_bindings()` 的动作计数**（边已由触发器建好，reconcile 只剩
状态推进）。测试因此断言**覆盖率与漂移计数**，不断言动作条数。

**局限（如实申报）**

1. **语料是合成的，金标与决策表同源**：55 例金标按规格表书写、分类器从库状态推断，100% 只证明
   "实现与规格一致 + 零误派"，**不等于**现场准确率；真实战场需接入实际门工件后重测（接口已留）。
2. **PG 端只做到结构 parity**（迁移列/触发器/视图与 SQLite DDL 逐列对齐 + 测试断言），未做
   PG 实库端到端跑批（本环境无可用 PG 写权限）。SQLite 路径全链路可复现。
3. **Monte-Carlo 依赖 numpy 的特征分解**；无 numpy 时退化为对角近似（代码里有 `mode` 标注），
   相关情形下的第三方核对能力下降。
4. **适用范围**：任何"证据不足 ⇒ 暂不可判"的门禁体系；不适用于需要外推的参考表（本模块与 IN50
   一样**禁止外推**，超覆盖一律 WITHHELD）。
5. 预算分量只覆盖可数字化的四类来源；模型形式不确定度（model-form）需要调用方以附加分量注入，
   本模块不臆造。

---

## ⑥ 误差判定（逐项，显式回答）

本节点是**平台机制**任务，无物理场量、无几何（故无 Cb/L/B 几何校验），也无实验/文献基准可对标——
下面的"基准"是任务书给定的机器判据与独立复算路径，逐项列出：

| 关键量 | 基准 / 判据 | 实测 | 相对偏差 | 是否 < 3% |
|---|---|---|---|---|
| 双向绑定覆盖率 | 判据 = 100% | 100.00% | 0.00% | **是（0%）** |
| 预算自动核算 vs 手工台账 | 判据 < 1% | 最大 0.4463% | 0.4463% | **是** |
| 预算自动核算 vs 精确手算 | 判据 < 1% | 4.42×10⁻¹⁵ | ~0% | **是** |
| 解析 vs Monte-Carlo（u_c） | 3σ（σ_rel≤0.290%） | 最大 0.548% | 1.89σ | **是（<3σ）** |
| 缺口派单准确率 | 判据 > 95% | 100.00%（55/55） | 误派率 0% | **是（准确率类，非误差类）** |
| 工单幂等性 | 复跑序列完全一致 | 一致（43/43） | 0 | **是** |
| 审计链完整性 | 断裂 0 | 248 行/53 链/断裂 0 | 0 | **是** |

**未达 3% 的项目：无。** 需强调：上表全部是**机制一致性**指标（同一真相两条独立实现互校），
不是物理精度指标；本节点未产生任何"某船型阻力/耐波误差 X%"式的物理结论，也不应被引用为物理证据。

---

## ⑦ 复现与产物清单

```
artifacts/withheld_closure/
├── closure_summary.json     # 三判据 + 基线对照 + 判决/缺口/工单汇总（判据数字的唯一来源）
├── binding_coverage.json    # 双向边集合、覆盖率、三类漂移
├── drift_selftest.json      # 注入→检测→修复 三段证据（94.44% → 100%）
├── budget_parity.json       # 48 例逐例 u_c（auto/manual/exact/MC）+ 误差统计
├── dispatch_accuracy.json   # 混淆矩阵、逐类 recall/precision、误派清单
├── dispatch_orders.json     # 43 张幂等工单（order_id/优先级/账龄/证据）
├── gaps.json                # 43 个缺口（类别/证据/动作）
├── corpus.json              # 55 例标注语料（金标 expected_class）
├── budget_rows.json         # 23 个判决的预算快照（含 resolution_ok）
└── figures/fig1..fig5.png   # 机制流程图 + 三判据图 + 分辨力图
```

回归守卫：`tests/vv/test_withheld_closure.py`（32 用例）覆盖
双向绑定建边/状态推进/三类漂移/修复/反查、预算公式与相关性反例、手算台账 <1%、
MC 3σ、分辨力不足不得 PASS、预算台账 append-only、缺口优先级、篡改审计链判定、
过期检测、孤儿数据集、幂等派单、语料准确率 >95%、IN50 基线严格更差、迁移列 parity、CLI 端到端。