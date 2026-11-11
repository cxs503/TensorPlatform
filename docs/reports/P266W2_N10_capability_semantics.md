# P266W2-N10 能力台账语义三元分层成熟化（capability / delivery / evidence）

> 本文件由 `tools/capability_semantics` 程序化生成（零手抄）：所有数字来自
> `artifacts/capability_semantics/acceptance.json`（sha256 链见 `audit_log.json`）。

## 1. 案例介绍（问题背景与输入）

能力台账此前是**扁平结构**：一条能力一行，只有 `id / domain / 声明等级 / 自由文本证据`。
扁平结构无法回答三个治理问题：①「能做什么」与「交付了什么」混为一谈；
②「有实现代码」被当成「有证据」；③ 对外报 `NOT_SUPPORTED` 的能力可能早已有在线端点。
本节点把它重构成语义三元分层，并给误用与证据建立机器判据。

| 输入项 | 值 |
|---|---|
| 台账条目（capability 层） | 21 |
| 分层后能力条目 | 21 |
| 交付物节点（delivery 层） | 1589 |
| 证据节点（evidence 层） | 285 |
| 端点模块扫描数（delivery 粒度） | 11 |
| 其中孤立（账上无条目） | 6 |
| 配置文件 | config/capability_semantics.json |
| 配置 sha256 | 755a7af0623e65844600bb6b467383b7a65e1c89baaac743c3d61259b53f16ca |
| 台账 sha256 | 618d37ff38a162da385c05e84e23c04c9f761fc3f8355f4e37d5b4316b58ac89 |

## 2. 怎么做的（方法 / 流程 / 工具链）

流水线：**声明源归一 → 交付物扫描 → 三层装配 → 证据等级级联判定 → 误用扫描 →
补丁改判 → 复扫验证 → 依赖图 → 验收判据落盘**。

证据等级判定级联（顺序即优先级，见 `tools/capability_semantics/evidence.py`）：

| 序 | 条件 | 派生等级 |
|---|---|---|
| 1 | 有 `stance=contradicts` 的独立审计裁决 | 取最弱裁决等级（CONTRADICTED_BY_AUDIT） |
| 2 | 无代码交付、无证据 | NOT_SUPPORTED（NO_DELIVERY_NO_EVIDENCE） |
| 3 | 无代码交付、有证据/验证工件 | WITHHELD（NO_CODE_DELIVERY_WITH_EVIDENCE） |
| 4 | 有代码 + 数值通过工件（误差 ≤ 3%） | REAL（NUMERIC_PASS_VERIFIED） |
| 5 | 有代码 + 含数值断言的测试 | WITHHELD（UNVERIFIED_IMPLEMENTATION） |
| 6 | 有代码 + 无数值断言的测试 | WITHHELD（TEST_WITHOUT_NUMERIC_ASSERT） |
| 7 | 只有代码 | STUB（CODE_ONLY） |

误用治理三分类（`misuse.py`）：`UNDERCLAIM_NOT_SUPPORTED`（声明不支持但仓内有交付）、
`UNREGISTERED_DELIVERY`（端点模块在飞而账上无条目）、`CLAIM_WITHOUT_DELIVERY`（声明有等级但扫描零交付，软信号）。
改判只写成**补丁工件**并复扫验证，不改写规范台账本体。

顺带修复（接线缺陷）：仓内 `backend/api/capability_audit.py` 一直存在但 **main.py 从未
include**，线上 `/api/v1/tensor/capabilities/audit` 恒 404（探针实测：带 token 打 :8010
返回 404；本节点已接线并在单测里做源码级接线回归）。

### 2.1 attempt-2 修正（本版相对上一版的实质变更）

1. **真缺陷修复：工件未随分支提交。** 上一版只提交了代码与报告，`artifacts/capability_semantics/` 未入分支 ⇒ 服务侧 `_load` 恒 503，「成熟化 API」形同虚设。本版把 10 个工件（含 sha256 审计链）随分支提交，并加机器证明测试 `test_committed_artifacts_serve_all_endpoints`：用仓内**真实**工件打 7 个只读端点，全 200 才算过。
2. **判据 (c) 改为诚实口径。** 上一版当 WITHHELD 比例超标时按「已给逐因分析」判 `ok=True`（`waived_by_analysis=True`）——这等于让解释替代达标。本版删除豁免：超标即 `ok=False`，并额外给**三口径敏感性**（all_graded 33.33% / ledger_only 42.86% / excluding_out_of_repo 33.33%），防止挑分母宣布通过。
3. **新增验证欠账台账**（`verification_debt.json`，24 条）：把比例拆成「优先级 + 提升条件 + 当前量测缺口」，并**真跑**配置声明的独立验证脚本（1 个执行成功），记录仓内命令/sha256/退出码/输出 sha256——量测值不是手抄，重跑即得。
4. **新增只读端点** `/api/v1/tensor/capabilities/semantics/debt`（支持 priority/status 过滤），供审计脚本机器复核。
5. **覆盖率口径如实披露**：判据 (e) 的 93.0% 是 `tools/capability_semantics` 的分支覆盖（pytest --cov-branch，见第 6 节复现命令）；API 模块因 coverage 纯 Python tracer 与 anyio/TestClient 线程冲突（`pop from empty list`）无法同进程测量，按「端点级黑盒断言」覆盖（12 例），不冒充行覆盖率。

## 3. 计算结果（判据 (a)–(f)）

| 判据 | 规模 | 指标 | 阈值 | 结论 |
|---|---|---|---|---|
| (a) 三层分层 | 21 | 归类 100% / 实质覆盖 90.48% | 丢失 0 条（0.00%） | True |
| (b) 误用治理 | 修复前 7 → 后 0 | fixed=7 | 阈值 ≥3 | True |
| (c) 证据审计 | 27 | WITHHELD 33.33%（honest-FAIL：附逐因分解 + 欠账台账） | 阈值 <30% | False |
| (d) 依赖图 | 21 | 环 0 / 孤立 0.00% | 阈值 <5% | True |
| (e) 成熟化交付 | 7 | 覆盖率 93.0 | 阈值 ≥80.0% | True |
| (f) NaN/None 安全 | 2 | — | — | True |

证据等级分布（补丁后，共 27 条）：

| 等级 | 条目数 |
|---|---|
| NOT_SUPPORTED | 4 |
| STUB | 7 |
| MOCK | 4 |
| WITHHELD | 9 |
| REAL | 3 |

WITHHELD 逐因分解（判据 c 要求 >30% 时给原因；本次 withheld_ok=False, waived_by_analysis=False, ok_basis=honest_fail_ratio_above_max）：

| 理由码 | 条目数 | 占 WITHHELD | 释义 |
|---|---|---|---|
| NO_CODE_DELIVERY_WITH_TEST_REFERENCE | 5 | 55.6% | 只有测试引用、扫描未定位到实现文件 → 未验证 |
| TEST_WITHOUT_NUMERIC_ASSERT | 1 | 11.1% | 有测试但无数值断言，不能作为精度证据 → 未验证 |
| UNVERIFIED_IMPLEMENTATION | 3 | 33.3% | 有代码与数值断言测试，但缺独立验证工件 → 未验证 |

逐因受影响的条目（根因分析）：

| 理由码 | 条目数 | 受影响能力 | 根因 |
|---|---|---|---|
| NO_CODE_DELIVERY_WITH_TEST_REFERENCE | 5 | dev.git、geometry.generate、geometry.verify_official、misc.stubs、stdmodels.lookup | 扫描关键词门控保守：有测试引用但未把实现文件绑定到该能力 → 交付绑定缺口（非必然无实现） |
| TEST_WITHOUT_NUMERIC_ASSERT | 1 | dev.nginx_audit | 测试无数值断言，不能作为精度证据 → 测试质量缺口 |
| UNVERIFIED_IMPLEMENTATION | 3 | geometry.list_models、gov.stats、shiphull.generate | 有代码与数值断言测试，但缺独立的数值通过工件（基准/实验对标）→ 平台级验证供给不足 |

误用扫描明细（前 20 条）：

| kind | capability / module | declared | suggested | 修复动作 |
|---|---|---|---|---|
| UNDERCLAIM_NOT_SUPPORTED | ai.predict | NOT_SUPPORTED | MOCK | ADD_INDEPENDENT_BENCHMARK,SET_DECLARED_LEVEL,LEDGER_UNCHANGED_THIS_NODE |
| UNREGISTERED_DELIVERY | unregistered.coupling_clock_sync | NOT_SUPPORTED | STUB | REGISTER_CAPABILITY_ENTRY,ADD_NUMERIC_ASSERT_TEST,LEDGER_UNCHANGED_THIS_NODE |
| UNREGISTERED_DELIVERY | unregistered.ice_uq | NOT_SUPPORTED | STUB | REGISTER_CAPABILITY_ENTRY,ADD_NUMERIC_ASSERT_TEST,LEDGER_UNCHANGED_THIS_NODE |
| UNREGISTERED_DELIVERY | unregistered.ship_batch | NOT_SUPPORTED | STUB | REGISTER_CAPABILITY_ENTRY,ADD_NUMERIC_ASSERT_TEST,LEDGER_UNCHANGED_THIS_NODE |
| UNREGISTERED_DELIVERY | unregistered.ship_green | NOT_SUPPORTED | STUB | REGISTER_CAPABILITY_ENTRY,ADD_NUMERIC_ASSERT_TEST,LEDGER_UNCHANGED_THIS_NODE |
| UNREGISTERED_DELIVERY | unregistered.ship_samples | NOT_SUPPORTED | STUB | REGISTER_CAPABILITY_ENTRY,ADD_NUMERIC_ASSERT_TEST,LEDGER_UNCHANGED_THIS_NODE |
| UNREGISTERED_DELIVERY | unregistered.ship_vibration | NOT_SUPPORTED | STUB | REGISTER_CAPABILITY_ENTRY,ADD_NUMERIC_ASSERT_TEST,LEDGER_UNCHANGED_THIS_NODE |
| CLAIM_WITHOUT_DELIVERY | dev.probe | REAL | NOT_SUPPORTED | REVIEW_SCAN_KEYWORDS |
| CLAIM_WITHOUT_DELIVERY | dev.services | REAL | NOT_SUPPORTED | REVIEW_SCAN_KEYWORDS |

REAL 评定归因可靠性（subject_backed = 验证工件自身主题命中该能力；keyword_only = 仅靠关键词共现，需人工复核）：

| 口径 | 能力 |
|---|---|
| REAL 条目 | dev.gate_5415、geometry.hydro_curves、knowledge.docs |
| subject_backed | geometry.hydro_curves |
| keyword_only（需复核） | dev.gate_5415、knowledge.docs |

补丁改判与独立评定冲突（取保守值对外声明，冲突单列）：

| 能力 | 独立评定等级 | 对外声明等级（保守） |
|---|---|---|
| ai.predict | REAL | MOCK |

精度维（与等级维正交，`accuracy_flag`）：

| accuracy_flag | 条目数 |
|---|---|
| no_numeric | 12 |
| within_pass | 15 |

## 4. 结果图

![声明等级与流水线评定等级分布对比](figures/fig1_level_distribution.png)

*声明等级与流水线评定等级分布对比*

![三层覆盖率与误用治理修复前后对照](figures/fig2_tier_coverage_and_misuse.png)

*三层覆盖率与误用治理修复前后对照*

![能力依赖图（灰=工具类节点，蓝=领域节点）](figures/fig3_dependency_graph.png)

*能力依赖图（灰=工具类节点，蓝=领域节点）*

![WITHHELD 口径敏感性（三种总体定义）与验证欠账台账（优先级×状态）](figures/fig4_withheld_sensitivity_debt.png)

*WITHHELD 口径敏感性（三种总体定义）与验证欠账台账（优先级×状态）*


## 5. 注意点（坑 / 局限 / 适用范围）

1. **代码存在 ≠ 证据存在**：级联第 4 条要求独立验证工件 + 数值判据，否则 WITHHELD；
   这把「实现完整」与「已证实」拆开，避免再用交付物冒充证据。
2. **改判只出补丁**：`registry_patch.json` 带 `applies_to_ledger_sha256` 与逐条
   `basis_sha256`；规范台账本体本节点不改写（需复核），避免自证式修复。
3. **扫描关键词表是假阴性来源**：`CLAIM_WITHOUT_DELIVERY` 类信号既可能是过度声明，
   也可能是关键词未覆盖；因此定级 low 并给 `REVIEW_SCAN_KEYWORDS` 人工动作，不自动降级。
4. **孤立率分母口径**：判据在 `graph_role=domain` 子图上判，全体（含工具类节点）比例
   单列披露为 42.86%；两口径都写进 `acceptance.json`。
5. **自指证据陷阱（本单踩过）**：第一版把本节点自己生成的报告/工件当成扫描输入，报告里写着「误差 ≤3%」→ 扫描器判 pass → 变成该能力的验证证据（循环论证）。现已用 `delivery_discovery.exclude_glob_patterns` 排除本节点产物与模块自身，排除项入账 warnings：excluded_globs: artifacts/capability_semantics/**|docs/reports/P266W2_N10_*|docs/reports/P266W1_N10_*|docs/reports/capability_semantics/**|tools/capability_semantics/**|docs/runbooks/capability_evidence_debt.md。
6. **REAL 的归因可靠性单列披露**：`subject_backed` 表示验证工件自身主题命中该能力（路径词干/文件首段），`keyword_only` 仅靠关键词共现——后者不是判据，只是风险披露，但必须在报告里点名，避免「扫描扫出 REAL」被当成独立复核。
7. **零领域语义**：能力 id / 标签 / 学科 / 依赖边全部来自 `config/capability_semantics.json`，
   代码与单测夹具只用合成域名（`synth.*`）。

## 6. 误差判定（关键量 vs 基准）

| 关键量 | 实测 | 基准/阈值 | 是否达标 |
|---|---|---|---|
| 条目丢失率 | 0.00% | 0（预注册停止规则 5%） | 是 |
| 归类率（结构） | 100.00% | 100% | 是 |
| 实质覆盖（交付或证据非空） | 90.48% | 披露项（无阈值） | — |
| WITHHELD 比例 | 33.33% | <30% | 否（原因见第 3 节逐因分解） |
| 循环依赖 | 0 | 0 | 是 |
| 孤立节点率（domain） | 0.00% | <5% | 是 |
| 误用修复数 | 7 | ≥3 | 是 |

注：判据 (c) 的 WITHHELD 比例 33.33% 不低于阈值 30%；ok_basis=honest_fail_ratio_above_max。 本条按诚实口径判 **FAIL**（原因分析不替代达标）：需消解 9 条 WITHHELD，逐条整改条件见第 3.1 节欠账台账。

口径敏感性（同一审计在不同总体定义下的比例；任一口径达标不得单独宣布通过）：

| 口径 | 评定条目 | WITHHELD | 比例 | 低于阈值 |
|---|---|---|---|---|
| all_graded | 27 | 9 | 33.33% | 否 |
| ledger_only | 21 | 9 | 42.86% | 否 |
| excluding_out_of_repo | 21 | 7 | 33.33% | 否 |

### 3.1 验证欠账台账（比例→可执行整改项，`verification_debt.json`）

| 优先级 | 能力 | 声明 | 实得 | 欠账状态 | 独立验证量测 | 提升条件 |
|---|---|---|---|---|---|---|
| P0 | calc.resistance | REAL | MOCK | OPEN | — | 先修实现缺陷（审计裁决已封顶等级），复验通过后再上 REAL |
| P0 | calc.stability | REAL | MOCK | OPEN | — | 先修实现缺陷（审计裁决已封顶等级），复验通过后再上 REAL |
| P0 | dev.probe | REAL | NOT_SUPPORTED | BLOCKED_OUT_OF_REPO | — | 补实现交付，或把声明降级为 NOT_SUPPORTED（不允许无交付声明 REAL） |
| P0 | dev.restart | REAL | NOT_SUPPORTED | BLOCKED_OUT_OF_REPO | — | 补实现交付，或把声明降级为 NOT_SUPPORTED（不允许无交付声明 REAL） |
| P0 | dev.services | REAL | NOT_SUPPORTED | BLOCKED_OUT_OF_REPO | — | 补实现交付，或把声明降级为 NOT_SUPPORTED（不允许无交付声明 REAL） |
| P0 | methods.computation | NOT_SUPPORTED | NOT_SUPPORTED | OPEN | — | 补实现交付，或把声明降级为 NOT_SUPPORTED（不允许无交付声明 REAL） |
| P0 | propeller.open_water | REAL | MOCK | OPEN | — | 先修实现缺陷（审计裁决已封顶等级），复验通过后再上 REAL |
| P1 | dev.git | REAL | WITHHELD | BLOCKED_OUT_OF_REPO | — | 把测试引用绑到真实实现文件（扫描根/模块路径），或补实现 |
| P1 | geometry.generate | REAL | WITHHELD | OPEN | — | 把测试引用绑到真实实现文件（扫描根/模块路径），或补实现 |
| P1 | geometry.list_models | REAL | WITHHELD | OPEN | — | 补独立数值验证工件（基准/文献对标 + 明确容差），误差 ≤3% 才上 REAL |
| P1 | geometry.verify_official | REAL | WITHHELD | OPEN | — | 把测试引用绑到真实实现文件（扫描根/模块路径），或补实现 |
| P1 | gov.stats | REAL | WITHHELD | OPEN | — | 补独立数值验证工件（基准/文献对标 + 明确容差），误差 ≤3% 才上 REAL |
| P1 | misc.stubs | STUB | WITHHELD | OPEN | — | 把测试引用绑到真实实现文件（扫描根/模块路径），或补实现 |
| P1 | shiphull.generate | REAL | WITHHELD | OPEN | — | 补独立数值验证工件（基准/文献对标 + 明确容差），误差 ≤3% 才上 REAL |
| P1 | stdmodels.lookup | REAL | WITHHELD | MEASURED | R1_literature_worst_rel_error_pct=1.352, R3_density_worst_rel_error_pct=1.026e+05 | 把测试引用绑到真实实现文件（扫描根/模块路径），或补实现 |
| P2 | dev.logs | REAL | STUB | BLOCKED_OUT_OF_REPO | — | 补带数值断言的测试，并补独立验证工件 |
| P2 | dev.nginx_audit | REAL | WITHHELD | BLOCKED_OUT_OF_REPO | — | 给测试补数值断言（基准值 + 容差），无数值断言的测试不算精度证据 |
| P2 | unregistered.coupling_clock_sync | STUB | STUB | OPEN | — | 补带数值断言的测试，并补独立验证工件 |
| P2 | unregistered.ice_uq | STUB | STUB | OPEN | — | 补带数值断言的测试，并补独立验证工件 |
| P2 | unregistered.ship_batch | STUB | STUB | OPEN | — | 补带数值断言的测试，并补独立验证工件 |

验证脚本执行记录（真跑，命令/脚本 sha256/退出码/输出 sha256 可复核）：

| 能力 | 验证脚本 | 状态 | 判决 | 退出码 | 脚本 sha256 | 备注 |
|---|---|---|---|---|---|---|
| stdmodels.lookup | stdmodels_lookup_independent_audit | EXECUTED | FAIL | 1 | 1fa6fc251c81… |  |

欠账汇总：条目 24（P0 7 / P1 8 / P2 8），已有独立量测占比 4.17%，外仓实现 6 条。

判据口径说明：本节点判的是**治理口径的误差（条目丢失率/覆盖率/比例）**，
不是物理量精度——物理精度由各领域仓的验证工件承担，本流水线只按 3%/15% 两档引用其数值判据。

**总判定：存在未通过判据**（all_ok=False）；warnings=1 条。

