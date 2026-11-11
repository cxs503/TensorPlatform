# 复用规则：能力证据「比例超标」必须判 FAIL，并把缺口拆成可执行欠账

**规则（一句话）**：能力台账审计里，任何**比例型健康判据**（如 WITHHELD 占比）超标就是未达标——
逐因分析只能解释缺口，不能翻判为通过；同时必须把该比例拆成「逐条提升条件 + 可量测缺口」的欠账台账，
且量测值只能来自**真跑**的独立验证脚本（命令/sha256/退出码可复核），不得手抄或填默认值。

## 适用范围（context）

- 适用：平台侧「能力声明 → 交付 → 证据」治理流水线（`tools/capability_semantics/`），
  以及任何用**比例/覆盖率类判据**做验收门的审计模块（能力台账、证据治理、覆盖审计）。
- 不适用：物理量精度判定本身（那由领域仓的验证工件按 3%/15% 两档承担）；
  本规则只约束「治理口径判据不得被解释豁免」与「缺口必须可量测」。

## 证据与出处（provenance）

| 项 | 值 |
|---|---|
| 任务 | P266W2-N10（mission 11203，TensorPlatform，分支 `agent/m11203`） |
| 触发事实 | attempt-1 报告用 `waived_by_analysis=True` 把 WITHHELD 33.33%（阈值 <30%）判成 `ok=True`；且 `artifacts/capability_semantics/` 未随分支提交 → 服务侧端点恒 503 |
| 修复后工件 | `artifacts/capability_semantics/acceptance.json`（`ok_basis=honest_fail_ratio_above_max`, `honest_fail.criteria=[c_evidence_audit]`）、`verification_debt.json`（24 条欠账，1 条已量测） |
| 量测样例 | `stdmodels.lookup`：R1 文献锚点最差相对误差 1.35%（≤3% 通过）、R3 隐含密度最差相对误差 102602.7%（>15% 不可接受）——两者均由 `tools/validation/verify_stdmodels_lookup.py` 真跑得到，输出 sha256 入账 |
| 报告 | `/tensor/reports` id 12544（mission 11203，4 图） |
| 最后核验日 | 2026-11-10（`sha256sum -c` 双跑一致：`semantic_layers.json` / `acceptance.json` 逐字节稳定） |

## 机制（mechanism）

1. **判据诚实化**：`criteria.<key>.ok` 只由指标与阈值决定；`waived_by_analysis` 一律为 False。
   超标时额外输出**口径敏感性**（`all_graded` / `ledger_only` / `excluding_out_of_repo`），
   并在 `acceptance.honest_fail` 里点名失败判据——禁止用「换分母」宣布通过。
2. **缺口可量测**：`debt.py` 把每条非 REAL 条目映射为
   `理由码 → 提升条件 → 量测缺口 → 状态（OPEN / MEASURED / BLOCKED_OUT_OF_REPO）`；
   配置 `verification_runs` 声明脚本与指标抽取路径，流水线 `--run-verifiers` 真跑，
   失败路径显式记 `SKIPPED_MISSING_SCRIPT` / `EXEC_FAILED` / `PARSE_INCOMPLETE`。
3. **债务不参与改判**：欠账台账只解释缺口，等级仍由证据级联决定——避免「为关账而改判」的自证循环。
4. **证据边界**：实现位于扫描边界外仓的能力标 `BLOCKED_OUT_OF_REPO`，本仓不假造证据。

## 推荐流程（procedure）

1. 判据定义阶段就写清「分母是什么」，并预置至少两种口径的敏感性输出。
2. 判据不通过时：先落 `honest_fail`（含判据名与实测值），再写逐因分解，最后给欠账条目。
3. 需要「量测缺口」时：找**已有的独立验证脚本**（含参考值与容差），在配置里声明并真跑；
   脚本缺失就保持 `OPEN`，不要用人工填数补位。
4. 关账动作（提升等级）只走「补独立验证工件 + 复扫」路径，不动规范台账本体（只出补丁）。

## 反例/局限（counterexamples & limitations）

- **反例**：把「已附原因分析」当作达标依据（attempt-1 的 `waived_by_analysis`）——指标没变，
  只是措辞变了，属自欺。
- **反例**：为了压低比例改判为 STUB/MOCK 而不修证据——等级降了、缺口还在，且丢失可执行信息。
- **局限 1**：扫描关键词门控保守会产生假阴性（`NO_CODE_DELIVERY_WITH_TEST_REFERENCE`），
  这类条目按 WITHHELD 处理是**诚实**的，但不能据此宣称「无实现」。
- **局限 2**：API 模块的行覆盖率在 coverage 纯 Python tracer + anyio/TestClient 线程下会崩
  （`pop from empty list`），只能退化为端点级黑盒断言；不要把「端点 200」写成「行覆盖率」。

## 回归护栏（regression guard）

| 护栏 | 位置 |
|---|---|
| 比例超标必须 `ok=False` 且 `waived_by_analysis=False` | `tools/capability_semantics/tests/test_pipeline.py::test_pipeline_withheld_ratio_above_max_is_honest_fail` |
| 三口径敏感性键必须齐备 | 同上（断言 `all_graded/ledger_only/excluding_out_of_repo`） |
| 欠账量测必须来自真跑且失败路径不填默认值 | `tools/capability_semantics/tests/test_debt.py`（6 例失败/缺失路径） |
| 工件必须随分支提交、端点真实 200 | `backend/tests/test_capability_semantics_api.py::test_committed_artifacts_serve_all_endpoints` |
| 工件链可复算、逐字节稳定 | `python3 -m tools.capability_semantics … && sha256sum -c`（见报告复现命令） |

## 复核状态

- 代码评审：仓内单测 84 例全绿（含 12 例 API 黑盒）；引擎分支覆盖 93%（`--cov-branch`）。
- 未完成：判据 (c) 仍为 honest-FAIL（WITHHELD 33.33%），消解路径见
  `artifacts/capability_semantics/verification_debt.json`（24 条，P0 7 条）。