# P474W1-N02 判据漂移检测器（proposal → node acceptance report）

**结论**：新增平台共性能力 `tools/criteria_drift/`——把"提案里写的验收判据"与"验收报告里实际采用的判据"
逐条对齐、量化漂移、生成审计链，并在漂移超限时把 `campaign_nodes` 置为 `blocked` + `block_reason`，
作为战役 DAG 的**静默降标防线**。

**实测（`artifacts/criteria_drift/metrics.json`，零手抄）**

| 判据 | 目标 | 实测 |
|---|---|---|
| 解析器覆盖判据模式 | ≥5 | 5（误差/收敛阶/守恒/p 值/覆盖率；另有计数/标量/定性共 8 种） |
| 漂移检测准确率（合成 240 篇） | ≥95% | 文档级 0.9958、判据级 0.9975 |
| 误报率 | ≤5% | 文档级 0.0000、判据级 0.0000 |
| 审计报告要素 | 漂移度+影响+建议 | `audit_demo.json`（含 `max_relaxation`/`impact`/`recommended_actions`/`audit_sha256`） |
| pytest | ≥15 | 30（`tests/test_criteria_drift.py`） |
| campaign_nodes 集成 | 超限 block | `in-flight -> blocked`，`block_reason=criteria_drift:...`；干净节点 `-> done` |

真实文本外部效度：`data/campaigns/p106w2/nodes.json`（105 条人工判据）+ `docs/*.md` 判据段，
构造式变异 753 例，准确率 1.0000、误报率 0.0000（真值来自构造，不代表线上已发生事故）。

**产物**

* `tools/criteria_drift/`（model / parser / lexicon.json / config.json / matching / compare /
  campaign_integration / corpus_synth / real_corpus / evaluate / run_audit / README）
* `tests/test_criteria_drift.py`（30 例）
* `artifacts/criteria_drift/{metrics.json, report.md, audit_demo.json, campaign_gate_demo.json,
  real_eval.json, synthetic_rows.json, report_payload.json, figures/fig1..fig4}`

**与相邻机制的分界**：`tools/criteria_governance` 管"阈值出处是否被篡改"（CI 钉桩），
`tools/capability_drift` 管能力度量随时间漂移，`tools/node_acceptance` 从工件聚合机器判决；
本包只做"一次判决内、提案与报告两侧判据的缩窄审计"，不重复它们的职责。

**接入方式**（3 行）：`compare.audit(proposal, report)` → `if audit.block:` →
`campaign_integration.apply_sqlite/apply_via_api(...)`；PG 表 `campaign_nodes.block_reason`
已存在（migration 095），无需迁移。

**已知局限**：同族同阈值的"度量替换"与"度量改名"不可区分（需提案 `metric` 字段作第二锚点）；
定性判据删条只进人工复核；真实语料为构造式变异。