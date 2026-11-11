# 可复用知识：执行型证据采集的"能证到哪一级"判据

- **规则（一句话）**：自动化证据采集只能把等级顶到"证据类型实际支持的上限"——**契约通过只证可用、不变量只证不自相矛盾、只有外部参考值（误差 ≤ 3%）才证"算对了"**；采集不到就写 WITHHELD，不许用"测试全绿"代替"算对了"。
- **适用范围**：任何"能力台账 + 等级自述"的平台（物理域求解器 / 经验公式端点 / 服务端 API）。用于新增或改造证据自动化管线。
- **不适用**：纯代码质量审计（那看静态规则）；纯文档生产（无机器判据可锚）。
- **证据与出处**：mission `12475`（P9263-N08），提交 `bfb49df4e`（`tools/evidence_collection/`、`config/evidence_collection/`、`tests/evidence_collection/`），工件 `artifacts/evidence_collection/`，报告 `docs/evidence_collection/P9263_N08_report.md`（平台报告 12958）。
- **机制**：能力 → 面（在线 OpenAPI）/ 探针（请求 + 判据）/ 参考工件 / 测试的**显式映射存 JSON**；采集器只做 HTTP、算术、子进程执行与文件摘要；判定器按冻结规则集级联；工件自摘要 + 等级可由存储信号重放。
- **推荐流程**：
  1. 先给"参考值"找落点（实验/解析/基线工件）；找不到就明确该项**不可能**拿到 REAL。
  2. 每条判据标类别：`contract` / `invariant` / `reference`；只有 `reference` 能升级。
  3. 服务不可达、模块缺失、超时一律 `NOT_EXECUTED`/`MISSING`，**不算通过**。
  4. 参考误差自行复算，并与工件自称误差列比对；不一致即作废（防手抄）。
  5. 定级后写密封工件 + 判据表 + 台账块（不覆盖人审字段），块间用摘要成链。
  6. 回归门只拦"**无正面反证**的降级"；有反证（不变量违反/参考超限/面消失）的降级放行但必须留理由码，无理由码则在 ack 文件逐条申报。
  7. CI 只做离线复核（快照摘要 + 等级重放 + 判据），在线采集留给部署侧定时任务。
- **反例（踩过的坑）**：
  - 契约全绿 + 测试全绿就判 REAL → 自动盖章（合成用例 `syn-contract-only` 已钉死为 WITHHELD）。
  - 端点 404 就判 NOT_SUPPORTED → 过度断言（多为改名/迁移）；正确是 WITHHELD + `SURFACE_ABSENT`，并把"台账过期"作为结论上报（本轮 12/21 命中）。
  - 绑定测试红就判 MOCK → 混淆"回归/缺陷"与"造假"；红测试应回落 WITHHELD。
  - 把报告渲染脚本放进扫描器包 → 域词从报告正文漏进"零领域语义"面（本轮 C5 实测命中 16 次，已把渲染器移到 `scripts/`）。
- **回归守卫（机器可查）**：
  - `tests/evidence_collection/test_gate_and_e2e.py::test_shipped_calibration_corpus_meets_accuracy`（一致率 ≥ 0.9，规则漂移即红）；
  - `test_committed_acceptance_artifacts_are_verifiable`（快照摘要 + 等级重放）；
  - `test_gate_c4_flags_silent_downgrade_and_accepts_acked`（静默降级必红）；
  - CI job `evidence-collection`（`.github/workflows/capability-audit.yml`）。
- **审查状态**：自审 + 机器判据 C1–C7 全绿（一致率 0.9333、50 用例、C4 违例 0）；未做外部独立复核，读者如需引用请复核 `artifacts/evidence_collection/n08_acceptance.json` 原始数字。
- **最后验证日期**：2026-11-11（在线服务 v2.0.0，OpenAPI sha256 `c39b9640a6a0d44a…`）。