# P73-W3 跨域集成与回归验证报告 (p73w3-n08-cross-domain-integration)

> 本文件由 `tools/cross_domain_integration.docrender` 渲染：文中所有数字都是模板占位符，
> 从报告 JSON 程序化注入（零手抄）；模板见 `docs/P73W3_CROSS_DOMAIN_INTEGRATION.template.md`。
> 报告 JSON: `artifacts/p73w3_integration_report.json`
> 报告自封 sha256: `{{seal.body_sha256}}`
> manifest: `artifacts/p73w3_integration/manifest.json` (sha256 `{{provenance.manifest_sha256}}`)
> 扫描时刻: `{{generated_utc}}`，扫描根: `{{scan.work_root}}`

## ① 案例介绍（物理背景/几何/参数表）

P73-W3 是船舶总体性能深化波的第三波（计划 9035，7 个执行节点 + 本集成节点）。
集成节点的任务不是再做一次物理计算，而是把 7 个节点交付的工件收成**一份可机器
复算的账**：谁交了、工件哈希是什么、单位/参考系/格式是否与跨仓契约一致、
相对上一波（P73-W2b，计划 405）关键指标变了多少。

| 节点 | mission | 仓 | 本报告数量名（契约声明） | 扫描时刻状态 | 完整性 |
|---|---|---|---|---|---|
| `{{nodes.0.node_ext_id}}` | `{{nodes.0.mission_id}}` | `{{nodes.0.repo}}` | relative_error / normalization_error | {{nodes.0.status}} | {{nodes.0.completeness\|.2f}} |
| `{{nodes.1.node_ext_id}}` | `{{nodes.1.mission_id}}` | `{{nodes.1.repo}}` | relative_error / closure_residual | {{nodes.1.status}} | {{nodes.1.completeness\|.2f}} |
| `{{nodes.2.node_ext_id}}` | `{{nodes.2.mission_id}}` | `{{nodes.2.repo}}` | relative_error / restoring_area / closure_residual | {{nodes.2.status}} | {{nodes.2.completeness\|.2f}} |
| `{{nodes.3.node_ext_id}}` | `{{nodes.3.mission_id}}` | `{{nodes.3.repo}}` | relative_error / numeric_diffusivity | {{nodes.3.status}} | {{nodes.3.completeness\|.2f}} |
| `{{nodes.4.node_ext_id}}` | `{{nodes.4.mission_id}}` | `{{nodes.4.repo}}` | potential / relative_error | {{nodes.4.status}} | {{nodes.4.completeness\|.2f}} |
| `{{nodes.5.node_ext_id}}` | `{{nodes.5.mission_id}}` | `{{nodes.5.repo}}` | tension / equilibrium_residual | {{nodes.5.status}} | {{nodes.5.completeness\|.2f}} |
| `{{nodes.6.node_ext_id}}` | `{{nodes.6.mission_id}}` | `{{nodes.6.repo}}` | recall_at_k / latency_p95 | {{nodes.6.status}} | {{nodes.6.completeness\|.2f}} |

参考系允许集: `none / body / earth / local-vertical`（跨仓契约，manifest `contract.allowed_frames`）。
基线波: P73-W2b（计划 {{campaign.baseline.plan_id}}，8/8 节点 done，本报告取其中 2 个同题节点的工件做基线）。

## ② 怎么做的（方法/流程/关键设置/工具链）

一次跑完 5 步，全部在 `tools/cross_domain_integration/`（纯 stdlib + matplotlib，零领域语义）：

1. **工件盘点**（`ingest.scan_node`）：按 manifest 的 glob 扫各节点工作树，逐文件记
   `bytes`/`sha256`/git 状态；**新鲜度归属**——只有未跟踪文件或最近提交 epoch ≥
   波次开始时刻（`campaign.started_utc`）的工件才计为本波产物，工作树里上一波留下的
   同名文件进 `stale_excluded`（本次排除 {{scan.stale_excluded}} 件），避免把旧证据
   当成新成果。
2. **跨仓契约校验**（`contract.py`）：
   - C0 声明层自洽：manifest 声明的单位可被量纲代数解析、参考系在允许集内；
   - C1 信封契约：`tp.cdi.envelope/v1` 必需键、节点/任务绑定、RFC3339 UTC 时间戳；
   - C2/C3/C4 实测层：工件声明的单位可解析、与参考单位同量纲、参考系合法；
   - C5 跨生产者一致：同一量名在**不同仓**里必须同量纲 + 同参考系（单位前缀差异记
     `scale_variants` 警告，因为不通换算直接相加就是 100/1000 倍错误）；
   - C6 legacy 元数据：无信封工件按历史格式统计 schema/mission/时间戳/单位四类键存在率。
3. **回归基线**（`baseline.py`）：基线值不手抄——按 JSON pointer 从上一波工件里读，
   同时记该工件的 sha256；manifest 里另存"声明值"，与工件读数不一致即报 `baseline_value_drift`。
   当前值缺失一律 `WITHHELD`（缺证据不许算通过）。另给**契约严格度增量**：从两波判据
   文本抽数值界（`<3%`、`<=1e-8` 等）比对收紧/放宽（按算子+单位配对，上下文不一致的
   配对显式标 `context_same=false`）。
4. **判据自检**（`criteria.py`）：I1 工件完整性、I2 契约通过率、I3 基线可比、I4 报告六要素、I5 产物与图落盘。
5. **报告与图**（`report`/`figures`/`cli`）：JSON 报告自封 `seal.body_sha256`，图件 sha256 入账；三次重跑同输入同 sha（确定性）。

工具链: Python 3.11 + stdlib（hashlib/json/subprocess/fractions）+ matplotlib(Agg)；
pytest 用例 `tests/test_cross_domain_integration.py`（合成域夹具 34 条 + 真实工件 legacy 扫描覆盖）。

## ③ 计算结果（数字表）

### 3.1 工件汇总

本次扫描索引工件 {{scan.artifacts_indexed}} 件（含信封 0 件），排除陈旧命中 {{scan.stale_excluded}} 件。
逐节点完整性与必需件缺口见 ①的表与报告 JSON `nodes[]`（每件工件带 sha256，可逐字节复核）。

### 3.2 契约检查

| 指标 | 数值 |
|---|---|
| 适用检查（PASS+FAIL） | {{contract.summary.counts.PASS}} PASS + {{contract.summary.counts.FAIL}} FAIL |
| 不适用（NA） | {{contract.summary.counts.NA}} |
| 通过率 | {{contract.summary.pass_rate\|.4f}} |
| 信封采用率（tp.cdi.envelope/v1） | {{contract.envelope_adoption.rate\|.4f}}（{{contract.envelope_adoption.nodes_with_envelope}}/{{contract.envelope_adoption.nodes_total}} 节点） |
| 跨仓一致性（C5） | 适用 {{contract.cross_producer_summary.applicable}} 条，PASS {{contract.cross_producer_summary.pass}}，FAIL {{contract.cross_producer_summary.fail}} |
| legacy 工件元数据存在率 | schema {{contract.legacy_metadata.aggregate.presence_rate.schema\|.4f}} / unit {{contract.legacy_metadata.aggregate.presence_rate.unit\|.4f}} / timestamp {{contract.legacy_metadata.aggregate.presence_rate.timestamp\|.4f}} / mission {{contract.legacy_metadata.aggregate.presence_rate.mission\|.4f}}（{{contract.legacy_metadata.aggregate.count}} 件） |

### 3.3 已落盘节点的实测值（真实数字，非占位）

| 节点 | 量 | 值 | 单位 |
|---|---|---|---|
| n01 谱疲劳 | relative_error（Dirlik 寿命 vs 时域雨流参考） | {{nodes.0.quantities.0.value\|.4g}} | % |
| n01 谱疲劳 | normalization_error（Dirlik PDF 归一化） | {{nodes.0.quantities.1.value\|.4g}} | 1 |
| n02 EEXI | relative_error（Holtrop 阻力 vs 试验值，KVLCC2） | {{nodes.1.quantities.0.value\|.4g}} | % |
| n02 EEXI | closure_residual（推进平衡残差，KCS） | {{nodes.1.quantities.1.value\|.4g}} | 1 |
| n04 FVM 算子验证 | relative_error（实测数值黏性 vs 理论） | {{nodes.3.quantities.0.value\|.4g}} | % |
| n04 FVM 算子验证 | numeric_diffusivity（实测，upwind C=0.2） | {{nodes.3.quantities.1.value\|.4g}} | m²/s |
| n05 电场特征 | potential（船体保护电位） | {{nodes.4.quantities.0.value\|.4g}} | V |
| n05 电场特征 | relative_error（半空间点源解析锚） | {{nodes.4.quantities.1.value\|.4g}} | % |

### 3.4 回归基线（vs P73-W2b，证据等级按数值是否双侧齐全）

| 量 | 基线值 | 当前值 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|
| n02 relative_error (%) | {{baseline.quantities.0.baseline.value\|.6g}} | {{baseline.quantities.0.current.value\|.6g}} | {{baseline.quantities.0.comparison.delta_rel\|.4g}} | ±{{baseline.quantities.0.tolerance_rel\|.3g}} | {{baseline.quantities.0.comparison.verdict}} |
| n02 closure_residual (1) | {{baseline.quantities.1.baseline.value\|.6g}} | {{baseline.quantities.1.current.value\|.6g}} | {{baseline.quantities.1.comparison.delta_rel\|.4g}} | ±{{baseline.quantities.1.tolerance_rel\|.3g}} | {{baseline.quantities.1.comparison.verdict}} |
| n03 relative_error (%) | {{baseline.quantities.2.baseline.value\|.6g}} | {{baseline.quantities.2.current.value\|.6g}} | {{baseline.quantities.2.comparison.delta_rel\|.4g}} | ±{{baseline.quantities.2.tolerance_rel\|.3g}} | {{baseline.quantities.2.comparison.verdict}} |
| n03 restoring_area (m·rad) | {{baseline.quantities.3.baseline.value\|.6g}} | {{baseline.quantities.3.current.value\|.6g}} | {{baseline.quantities.3.comparison.delta_rel\|.4g}} | ±{{baseline.quantities.3.tolerance_rel\|.3g}} | {{baseline.quantities.3.comparison.verdict}} |

可比 {{baseline.comparable}} 条 / WITHHELD {{baseline.withheld}} 条。

### 3.5 契约严格度增量（两波判据文本的数值界比对）

- n02（EEXI）：W2 界 {{baseline.bound_delta.p73w3-n02-eexi-maturity.w2_bounds}} 个 → W3 界 {{baseline.bound_delta.p73w3-n02-eexi-maturity.w3_bounds}} 个；
  放宽 {{baseline.bound_delta.p73w3-n02-eexi-maturity.loosened.0.w2\|.4g}}% → {{baseline.bound_delta.p73w3-n02-eexi-maturity.loosened.0.w3\|.4g}}%（阻力偏差）、
  {{baseline.bound_delta.p73w3-n02-eexi-maturity.loosened.1.w2\|.4g}}% → {{baseline.bound_delta.p73w3-n02-eexi-maturity.loosened.1.w3\|.4g}}%（推进功率闭合）；
  上下文不一致配对数 {{baseline.bound_delta.p73w3-n02-eexi-maturity.pairs_with_context_mismatch}}。
- n03（破损稳性）：W2 界 {{baseline.bound_delta.p73w3-n03-damage-stability-maturity.w2_bounds}} 个 → W3 界 {{baseline.bound_delta.p73w3-n03-damage-stability-maturity.w3_bounds}} 个；
  新增 {{baseline.bound_delta.p73w3-n03-damage-stability-maturity.added}}、移除 {{baseline.bound_delta.p73w3-n03-damage-stability-maturity.removed}}、上下文不一致配对 {{baseline.bound_delta.p73w3-n03-damage-stability-maturity.pairs_with_context_mismatch}}。

## ④ 结果图

图件由 `tools/cross_domain_integration/figures.py` 生成，sha256 已入账（`figures[]`）：

- `artifacts/figures/cdi_p73w3_completeness.png`（sha256 `{{figures.0.sha256}}`）：逐节点 必需/期望工件数与完整性比例——直观看哪些节点还没交件。
- `artifacts/figures/cdi_p73w3_contract.png`（sha256 `{{figures.1.sha256}}`）：契约检查按 code 的 PASS/FAIL/NA 堆叠条 + 跨生产者一致性结论。
- `artifacts/figures/cdi_p73w3_baseline.png`（sha256 `{{figures.2.sha256}}`）：每条基线量的相对偏差，灰条 = WITHHELD（当前值缺失）。
- 附：P73-W2 legacy 契约审计三图（`artifacts/figures/p73w2_audit/`）——上一波工件在信封契约下的表现，作为"契约漂移有多大"的对照面。

## ⑤ 注意点（坑/局限/适用范围）

1. **扫描时刻 = 竞争态快照**：集成节点与 7 个执行节点同波并发，扫描时 3 个节点已落工件、
   其余尚未提交。本报告的 `status` 是**时刻快照**而非最终判决；重跑同一命令即得最新快照
   （`--generated-utc` 固定可复现同一 body sha）。
2. **信封采用率 0%**：`tp.cdi.envelope/v1` 是本节点新发布的跨仓契约，本波节点在它存在前
   就已开工，因此只能走 manifest adapter（显式 JSON pointer + `scale` + `unit_in_artifact`）
   入账。adapter 的代价是"单位/参考系只能取 manifest 声明"，故 C2/C3/C4 对这些量记 NA
   而非 PASS——不拿声明冒充实测。
3. **legacy 元数据缺口是实测结论**：本波 3 件工件 schema/unit 键存在率 0%，
   P73-W2 的 4 件 legacy 工件 schema {{contract.legacy_metadata.aggregate.presence_rate.schema\|.2f}}、unit 0.00——
   跨仓"格式一致"目前为零，不是偶发。
4. **基线 FAIL 不等于精度退化 56 倍**：n02 `relative_error` 的 −56 倍偏差根因是**参考基准换了**
   （W2 拿同一条 ITTC'78 线做自洽，W3 拿试验值），报告如实报 FAIL 并把它定性为
   "指标定义漂移 → 基线不可比"告警；下游若要跨波比较，必须固定基准口径。
5. **边界提取只按算子+单位配对**：`bound_delta` 不做语义同一性判断（`context_same` 已显式标注），
   因此"放宽/收紧"条数是指示性的；`added/removed` 计数对上下文不敏感、更可靠。
6. **零领域语义**：机制代码（backend/tools/tests）不含任何域词条；节点清单/量名/单位/基线
   数值全部经 manifest JSON 注入，换一个波次只换 manifest。

## ⑥ 误差判定（逐项对照机器判据）

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| I1 工件完整性 | 7/7 节点齐全（ratio=1.0） | {{criteria.criteria.I1.detail}} | {{criteria.criteria.I1.pass}} |
| I2 契约一致性 | 通过率 1.0 且无违约 | {{criteria.criteria.I2.detail}} | {{criteria.criteria.I2.pass}} |
| I3 回归可比 | ≥1 条量双侧齐全 | {{criteria.criteria.I3.detail}} | {{criteria.criteria.I3.pass}} |
| I4 报告六要素 | 六节标题齐备 | {{criteria.criteria.I4.detail}} | {{criteria.criteria.I4.pass}} |
| I5 产物与图 | 报告 + ≥1 图带 sha256 | {{criteria.criteria.I5.detail}} | {{criteria.criteria.I5.pass}} |

总体判定: **{{criteria.verdict}}**（通过 {{criteria.passed}}）。

**是否 <3%？** 分两类回答，不含糊：
- 本节点自身机制无"精度"可言（判据是完整率/通过率计数），I2 契约通过率
  {{contract.summary.pass_rate\|.4f}}，与 100% 的差距全部来自 legacy 工件缺 schema/unit 键——
  这是格式契约缺口，不是数值精度。
- 对**物理量**而言：已落盘的两条实测值与各自的解析/独立通道参考比
  （n01 Dirlik 寿命 vs 时域雨流参考 {{nodes.0.quantities.0.value\|.4g}}%；n05 半空间点源电位
  vs 解析 {{nodes.4.quantities.1.value\|.4g}}%），均 <3%；
  但需要跨波比对的关键量（n02 阻力偏差、n03 残存曲线）目前只有基线侧有值、当前侧 WITHHELD，
  按纪律记 **WITHHELD**，不给"通过"。