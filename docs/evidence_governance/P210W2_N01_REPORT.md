# P210W2-N01: WITHHELD→REAL 自动升级管线与证据欠账编译器（统一证据治理能力）

- **节点**: P210W2-N01（战役 DAG，物理域，成熟化节点）
- **仓库 / 分支**: TensorPlatform · `agent/m9967`
- **落库**: `tools/evidence_governance/`（代码 10 文件 + 测试 6 文件）、
  `artifacts/evidence_governance/`（工件 + 图）、本报告
- **判决**: 判据 (1)(2)(4) 达成；(3) 管线达成但**真实库闭环率为 0/19（honest FAIL）**，闭环能力本身在合成回路上证成（3/8）

---

## ① 案例介绍（物理背景 / 输入 / 参数表）

本节点不是物理仿真算例，而是**平台证据治理机制**的成熟化：把「WITHHELD→REAL 自动升级管线」
（prop-7982#4）与「证据欠账→补数据提案编译器」（prop-7984#15）合并为单一平台能力。

治理对象是本仓已入库的能力证据台账：

| 项 | 值 |
|---|---|
| 被扫描库 | `artifacts/capability_ledger/capability_ledger_resolved.json`（21 行） |
| WITHHELD 条目 | **19** 行（扫描覆盖 19/21，未筛样） |
| 其中需补证据的能力行 | 5 行（物理能力） |
| 非证据承载行（平台/开发端点） | 14 行（经注入数据 `inputs/capability_class.json` 标记，路由到 P3 队列外） |
| 声明语料 | `tests/fixtures/capability_ledger/reports_corpus.json`（在线报告 + 仓内文档） |
| 合成回路库 | `tools/evidence_governance/fixtures/library_snapshot.json`（8 行 WITHHELD，全部 `synth.*` 域名） |

判据参数（全部在 `config/governance.v1.json`，代码内无任何域词条）：

| 参数 | 值 | 作用 |
|---|---|---|
| `magnitude.weights` | evidence_gap 0.45 / claim_gap 0.35 / corroboration_gap 0.2 | 缺失量级三因子加权 |
| `magnitude.evidence_count_saturation` | 3 | 证据条数饱和点 |
| `priority.weights` | magnitude 0.5 / criticality 0.3 / overclaim 0.2 | 优先级评分 |
| `priority.bands` | P0 ≥ 0.75，P1 ≥ 0.5，P2 ≥ 0，P3=非证据行 | 分档 |
| `classification.accuracy_threshold` | 0.95 | 判据 (1) 门限 |
| `verification.min_independent_refs` | 2 | 升级门：独立证据源下限 |
| `verification.require_content_hashes` | `true` | 升级门：证据须带内容哈希 |

## ② 怎么做的（方法 / 流程 / 工具链）

单一命令跑完整条回路：

```
scan(全库) → classify(基准类型) → proposal(量级+优先级+验收规格)
           → upgrade gate(结构门，不看 status 字段) → closure KPI → sha256 工件清单
```

1. **全库扫描** (`scanner.py`)：来源、字段映射、`WITHHELD` 判定值全部来自配置；支持主台账 +
   辅助台账按 id 拼接注记 + 报告语料句子归并。输出按 `(source, id)` 排序，逐行内容摘要。
2. **分类** (`classifier.py`)：复用 `tools/evidence_debt_compiler` 的规则机（合并点 1），
   规则集外置为配置；同一条声明命中多个基准信号时按**声明的优先序**消歧；同规则内重叠模式
   （如「实测」与「实测数据」）折叠为最具体者，避免冗长模式表虚增得分。
3. **提案编译** (`proposal.py`)：每个 WITHHELD 行输出
   `baseline_type`（分类）+ `missing_magnitude`（0–1 缺失量级：证据密度/声明落差/旁证广度加权）
   + `priority_score/band` + 可机读的 `required_evidence`/`acceptance_criteria`（复用欠账编译器的
   任务模板，合并点 2）。
4. **升级门** (`upgrade.py`)：`pass` 声明必须同时满足「≥2 个**不同**证据源」
   「验收检查非空且全过」「每条证据带内容哈希」，否则记 `claim_rejected` 且条目留在 WITHHELD ——
   伪造 pass 无法推动 KPI。
5. **KPI 与工件** (`model.governance ledger` / `artifacts.py`)：`closure_rate = n_upgraded / n_withheld_total`，
   序列化时校验 `upgraded + failed + remaining == total`；工件无墙钟时间、canonical JSON、
   逐文件 sha256 + `combined_sha256`。

## ③ 计算结果（程序化注入，零手抄）

### 3.1 真实库（19 行 WITHHELD）

| 指标 | 值 |
|---|---|
| WITHHELD 总数 | **19** |
| 生成提案数 | **19**（覆盖率 1.000） |
| 升级数 / 失败数 / 仍开放 | **0 / 0 / 19** |
| **升级闭环率 KPI** | **0.000**（0/19，已写入 `upgrade_ledger.json` + `kpi.json`） |
| 不变量 | `n_upgraded + n_failed + n_remaining == n_withheld_total` → `True` |
| 优先级分档 | P0 4 / P1 1 / P2 0 / P3 14 |
| 基准类型分布 | experimental 2, unknown 17 |
| 平均缺失量级 | 0.9105；待补证据单元合计 11 |
| 真实库 outcome 形态 | no_outcome（无任何已登记验证结果） |

P0 队列（需补证据的能力行）：

| 能力行 | 档 | 优先级得分 | 基准类型 | 缺失量级 | 缺失单元 | 建议首选阶梯 |
|---|---|---|---|---|---|---|
| `shiphull.generate` | P0 | 0.985 | unknown | 1.000 | 2 | physical_consistency |
| `geometry.generate` | P0 | 0.970 | unknown | 1.000 | 2 | physical_consistency |
| `geometry.hydro_curves` | P0 | 0.970 | unknown | 1.000 | 2 | physical_consistency |
| `geometry.verify_official` | P0 | 0.940 | experimental | 1.000 | 3 | experimental |
| `calc.resistance` | P1 | 0.675 | experimental | 0.350 | 2 | experimental |

### 3.2 分类判据（判据 1）

| 指标 | 值 |
|---|---|
| 标注基准 | `synthetic-labeled-declarations-v1`，48 条（6 类各 8 条，`synth.*` 域名） |
| 正确数 / 准确率 | **48/48 = 1.0000** |
| 门限 | 0.95 → `threshold_met=True` |
| 逐类召回 | analytical_solution 1.000(n=8), cross_validation 1.000(n=8), experimental 1.000(n=8), mesh_convergence 1.000(n=8), physical_consistency 1.000(n=8), unknown 1.000(n=8) |
| 误判明细 | 无 |
| 修复对照（重叠模式折叠前/后） | 0.9792 (47/48) → **1.0000**（+2.08 pp，见 fig3 右图，程序化重算） |

### 3.3 合成回路（判据 2/3 的端到端证明）

| 指标 | 值 |
|---|---|
| WITHHELD 总数 | 8 |
| 提案 | 8（6 类齐全：analytical_solution, cross_validation, experimental, mesh_convergence, physical_consistency, unknown） |
| 升级 / 失败 / 仍开放 | 3 / 1 / 4 |
| **闭环率** | **0.375**（3/8） |
| 被门拒绝的伪造 pass | 3 条：synth.delta — pass claim rejected: only 1 independent evidence source(s) (< 2); synth.epsilon — pass claim rejected: acceptance checks failed: n_cases; synth.zeta — pass claim rejected: evidence without content hash: src-f2 |

### 3.4 幂等复现（判据 4）

| 指标 | 真实库 | 合成回路 |
|---|---|---|
| `combined_sha256` | `b37ee990549308d78661c385085ec0bb10121de3e3dcbd341b2d1473079a3a31` | `05dbf1391f5d2019727217aa9aef484de894c10601fb9ea1b16edb0f88177628` |
| 两次运行同输入 | 相同（`tests/test_idempotency.py`） | 相同（`--verify-idempotent` 实测） |
| 工件数 | 5 | 5 |

### 3.5 测试与纪律门

| 检查 | 结果 |
|---|---|
| `pytest tools/evidence_governance/tests` | **36 passed / 0 failed**（rc=0） |
| `tools/evidence_domain_scan.py`（零域语义门） | 扫描 44 文件 / 37 词条 / **hits=0** |
| 新增扫描范围 | `tools/evidence_governance`、`tools/evidence_debt_compiler` 已登记进 lexicon `targets`（否则本门会空转通过） |

## ④ 图

| 图 | 文件 | 说明 |
|---|---|---|
| 图 1 | `artifacts/evidence_governance/figures/fig1_acquisition_queue.png` | 真实库 19 行按优先级排序；颜色=分档，标注=基准类型 + 缺失量级 |
| 图 2 | `artifacts/evidence_governance/figures/fig2_closure_funnel.png` | 扫描→提案→升级漏斗（真实库 vs 合成回路）+ 闭环账目（不变量） |
| 图 3 | `artifacts/evidence_governance/figures/fig3_classification_accuracy.png` | 逐类召回 + 重叠模式折叠前后准确率对照（重算非手抄） |

（本节点为后端/机制类任务，无场量云图；图 1–3 是判据量的直接可视化，脚本
`tools/evidence_governance/gen_figures.py` 从工件 JSON 注入，避免手抄。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **"有报告"≠"有可复算证据"**：真实库 19 行里有 14 行是平台/开发端点，
   其 REAL 语义是「可用」而非「物理正确」。若不区分就把它们放进补证据队列，
   队列会被噪声淹没（首版即如此，P3 分档是修复）。
2. **重叠正则双计**：首版规则里「实测」与「实测数据」同时命中同一短语被记 2 分，
   把 `analytical` 判定压成 `experimental`（实测准确率 0.9792）。折叠为最具体模式后 1.0。
   这也是本节点唯一一次「先有基线、后有改进」的可对照修复。
3. **manifest 可以造假**：仅凭 `status=pass` 升级等于把台账交给填写人；本实现把门做成结构性的
   （独立源数 / 检查非空 / 内容哈希），并专门用 3 条伪造 pass 做负控。
4. **局限 1**：分类基准与规则同一作者编写，测的是**规则-标注一致性与消歧行为**，
   不等于分布外泛化能力；真实库 19 行无标注，故只报覆盖率（19/19），
   不报准确率。
5. **局限 2**：`missing_magnitude` 是**结构亏欠的归一化指数**（0–1），不是物理量；
   只在同一配置内可比，跨配置不可比。
6. **局限 3（honest FAIL）**：真实库闭环率 **0.0**。库内没有任何已登记的验证结果，
   升级门正确地拒绝了「无证据升级」。因此「闭环能力已建成」由合成回路（0.375）
   与负控证明，而**不是**由真实库改善证明；补齐 `inputs/verification_manifest.json` 后无需改代码即可关闭欠账。

## ⑥ 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 关键量 | 本次值 | 基准 / 来源 | 误差 | <3%？ |
|---|---|---|---|---|
| 分类准确率 | 1.0000（48/48） | 标注基准 `synthetic-labeled-declarations-v1` | 0.00 pp | **是**（0.00%） |
| 逐类召回（最低一类） | 1.0000 | 同上 | 0.00 pp | **是** |
| 闭环率（合成回路） | 0.3750 | 手工账目期望 3/8 = 0.375 | 0.00 pp | **是**（0.00%） |
| 闭环率（真实库） | 0.0000 | **无外部基准**（治理 KPI 非物理量；库内无验证结果 → 期望值 0） | 0.00 pp | **是**（与期望账目一致 0.00%） |
| 工件 sha256 复现 | `b37ee990549308d7…` | 同输入第二遍运行 | 位完全一致 | **是**（0 bit 差） |

> **诚实声明**：判据 (3) 的「升级闭环率」在真实库上为 0.000，属**如实 FAIL**（管线正确、库存无料），
> 不以合成回路的 0.375 冒充真实库成绩；本节点的能力增量是「扫描-提案-门-KPI-可复现」这条链路本身
> 及其负控，而非学到了物理精度。
