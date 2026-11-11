# P266W2-N10 能力台账语义三元分层成熟化（attempt-2）：诚实判据 + 验证欠账台账

> 数字全部由 `post_report_m11203.py` 从 `artifacts/capability_semantics/*.json` 读出注入（零手抄）；sha256 链见 `audit_log.json`。

## ① 案例介绍（背景/对象/参数）

能力台账原先是**扁平结构**：一条能力一行（`id / domain / 声明等级 / 自由文本证据`）。扁平结构回答不了三个治理问题：①「能做什么」与「交付了什么」混为一谈；②「有代码」被当成「有证据」；③ 对外报 `NOT_SUPPORTED` 的能力可能早已有在线实现。本节点把台账重构为**语义三元分层**（capability / delivery / evidence），并给「证据等级」「NOT_SUPPORTED 误用」建立机器判据；attempt-2 修两处真缺陷（工件未提交、判据可被解释豁免），并新增**验证欠账台账**把比例拆成可执行整改项。

| 输入项 | 值 |
|---|---|
| 台账条目（capability 层） | 21 |
| 交付物节点（delivery 层） | 1589 |
| 证据节点（evidence 层） | 285 |
| 证据审计条目（判据 c 分母） | 27 |
| 配置 sha256 | 755a7af0623e6584… |
| 台账 sha256 | 618d37ff38a162da… |
| 判据口径 | 治理口径（条目丢失率/比例/环），非物理量精度 |

## ② 怎么做的（方法/流程/工具链）

流水线：**声明源归一 → 交付物扫描 → 三层装配 → 证据等级级联 → 误用扫描 → 补丁改判 → 复扫验证 → 依赖图 → 欠账台账（真跑验证脚本）→ 验收判据落盘**。

- 证据等级级联（`tools/capability_semantics/evidence.py`）：有反向审计裁决 → 取最弱裁决；无 code 且无证据 → `NOT_SUPPORTED`；无 code 有测试/证据 → `WITHHELD`；有 code + 数值通过工件（误差 ≤3%）→ `REAL`；有 code + 数值断言测试 → `WITHHELD`；只有 code → `STUB`。**代码存在 ≠ 证据存在**。
- 误用治理（`misuse.py`）：`UNDERCLAIM_NOT_SUPPORTED` / `UNREGISTERED_DELIVERY` / `CLAIM_WITHOUT_DELIVERY` 三类；改判只写补丁工件（带 `applies_to_ledger_sha256` 与逐条 `basis_sha256`），并做补丁后复扫验证，不改写台账本体。
- **验证欠账台账（新增，`debt.py`）**：每条非 REAL 条目 → `理由码 → 提升条件 → 当前量测缺口 → 状态`；配置声明的独立验证脚本被**真跑**，记录命令/脚本 sha256/退出码/输出 sha256/耗时/UTC 时间。缺脚本、执行失败、字段缺失分别记 `SKIPPED_MISSING_SCRIPT` / `EXEC_FAILED` / `PARSE_INCOMPLETE`，**从不填默认值**。
- **判据 (c) 改为诚实口径**：删除 `waived_by_analysis`（曾把「已给原因分析」当达标），超标即 `ok=False`，并同时输出三口径敏感性，禁止挑分母宣布通过。
- 工具链：`python3 -m tools.capability_semantics --run-verifiers --figures --doc … --coverage-pct 93.0`；单测 `pytest tools/capability_semantics/tests backend/tests/test_capability_semantics_api.py`；覆盖率 `pytest --cov-branch`。

## ③ 计算结果（判据逐条 + 关键分布）

| 判据 | 规模 | 指标 | 阈值 | 结论 |
|---|---|---|---|---|
| (a) 三层分层 | 21 | 归类 100% / 实质覆盖 90.48% | 丢失 0（0.00%） | True |
| (b) 误用治理 | 前 7 → 后 0 | fixed=7 | 阈值 ≥3 | True |
| (c) 证据审计 | 27 | WITHHELD 33.33% | 阈值 <30% | False |
| (d) 依赖图 | 21 | 环 0 / 孤立 0.00% | 阈值 <5% | True |
| (e) 成熟化交付 | 7 | 分支覆盖率 93.0% | 阈值 ≥80.0% | True |
| (f) NaN/None 安全 | 2 | — | — | True |

**总判定：`all_ok=False`；honest-FAIL 判据 = c_evidence_audit。** 判据 (c) 是如实未通过，不粉饰、不豁免。

证据等级分布（补丁后，共 27 条）：

| 等级 | 条目数 |
|---|---|
| MOCK | 4 |
| NOT_SUPPORTED | 4 |
| REAL | 3 |
| STUB | 7 |
| WITHHELD | 9 |

WITHHELD 口径敏感性（同一审计、三种总体定义；任一口径达标不得单独宣布通过）：

| 口径 | WITHHELD | 评定条目 | 比例 | 低于阈值 |
|---|---|---|---|---|
| all_graded | 9 | 27 | 33.33% | 否 |
| excluding_out_of_repo | 7 | 21 | 33.33% | 否 |
| ledger_only | 9 | 21 | 42.86% | 否 |

误用治理明细（扫描面 9 条 finding）：
| kind | capability | declared | suggested |
|---|---|---|---|
| UNDERCLAIM_NOT_SUPPORTED | ai.predict | NOT_SUPPORTED | MOCK |
| UNREGISTERED_DELIVERY | unregistered.coupling_clock_sync | NOT_SUPPORTED | STUB |
| UNREGISTERED_DELIVERY | unregistered.ice_uq | NOT_SUPPORTED | STUB |
| UNREGISTERED_DELIVERY | unregistered.ship_batch | NOT_SUPPORTED | STUB |
| UNREGISTERED_DELIVERY | unregistered.ship_green | NOT_SUPPORTED | STUB |
| UNREGISTERED_DELIVERY | unregistered.ship_samples | NOT_SUPPORTED | STUB |
| UNREGISTERED_DELIVERY | unregistered.ship_vibration | NOT_SUPPORTED | STUB |
| CLAIM_WITHOUT_DELIVERY | dev.probe | REAL | NOT_SUPPORTED |
| CLAIM_WITHOUT_DELIVERY | dev.services | REAL | NOT_SUPPORTED |

验证脚本真跑量测（命令/脚本 sha256/退出码/输出 sha256 可复核）：

| 能力 | 脚本 | 状态 | 判决 | 退出码 | 脚本 sha256 | 输出 sha256 | 耗时(s) |
|---|---|---|---|---|---|---|---|
| stdmodels.lookup | stdmodels_lookup_independent_audit | EXECUTED | FAIL | 1 | 1fa6fc251c818334… | 722ec2e2328dc46f… | 0.182 |

欠账台账汇总（`verification_debt.json`）：{"by_priority": {"P0": 7, "P1": 8, "P2": 8, "P3": 1}, "by_reason": {"CODE_ONLY": 7, "CONTRADICTED_BY_AUDIT": 3, "NO_CODE_DELIVERY_WITH_TEST_REFERENCE": 5, "NO_DELIVERY_NO_EVIDENCE": 4, "PATCHED": 1, "TEST_WITHOUT_NUMERIC_ASSERT": 1, "UNVERIFIED_IMPLEMENTATION": 3}, "by_status": {"BLOCKED_OUT_OF_REPO": 6, "MEASURED": 1, "OPEN": 17}, "debt_entries": 24, "measured_ratio": 0.0417, "out_of_repo_count": 6, "verifier_execution_enabled": true, "verifiers_declared": 1, "verifiers_executed": 1, "verifiers_failed": 0}

## ④ 结果图

图1 声明 vs 评定等级分布（左：一眼看清 overclaim）；图2 三层覆盖率与误用治理前后；图3 能力依赖图（环 0）；图4 **判据 (c) 口径敏感性 + 欠账台账优先级×状态**（本节点新增图，用来直接拒绝「挑一个达标口径」）。见随报 4 张 png（base64 内嵌）。

## ⑤ 注意点（坑/局限/适用范围）

1. **工件不进分支 = 端点形同虚设（本单真缺陷）**：上一版代码在、工件不在 → 服务侧恒 503；本版把工件与 sha256 链随分支提交，并用 `test_committed_artifacts_serve_all_endpoints` 拿**真实**工件打 7 个只读端点（全 200）做机器证明。
2. **解释不能替代达标**：判据 (c) 比例超标时给逐因分析只是解释，不是达标；本版判 FAIL 并给三口径敏感性，避免「口径挑数字」。
3. **扫描关键词是假阴性来源**：`NO_CODE_DELIVERY_WITH_TEST_REFERENCE` 多为「有测试引用但没把实现文件绑到该能力」（关键词门控保守），不等于无实现；`CLAIM_WITHOUT_DELIVERY` 同理，只作 low 信号 + `REVIEW_SCAN_KEYWORDS` 人工动作。
4. **外仓实现不算本仓缺口**：6 条 `dev.*` 能力的实现在扫描边界外的仓 ⇒ 欠账状态 `BLOCKED_OUT_OF_REPO`，本仓不假造证据；敏感性里单列排除口径。
5. **自指证据陷阱**：本节点自己的报告/工件必须从扫描里排除，否则「报告里写了 ≤3%」会被当成该能力的验证证据（循环论证）；排除项入账 warnings 可复核。
6. **覆盖率口径限制**：93.0% 是 `tools/capability_semantics` 的分支覆盖；API 模块因 coverage 纯 Python tracer 与 anyio/TestClient 线程冲突（`pop from empty list`）无法同进程测量，按端点级黑盒断言（12 例）覆盖，不冒充行覆盖率。
7. **部署滞后（实测）**：对运行中的 `:8010` 带 token 探针 `/api/v1/tensor/capabilities/semantics`、`/semantics/debt`、`/tensor/capabilities/audit` 全返回 404 ⇒ 线上服务跑的是旧检出；API 可用性证据以仓内真实工件的 TestClient 断言为准，重新部署后才会对外生效。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否<3%」）

| 关键量 | 实测 | 基准/阈值 | 口径 | 是否达标 |
|---|---|---|---|---|
| 条目丢失率 | 0.00% | 0（停止规则 5%） | 走阈值 | 是 |
| 三层归类率 | 100% | 100% | 走阈值 | 是 |
| 实质覆盖（交付或证据非空） | 90.48% | 停止规则 90% | 走阈值 | 是 |
| WITHHELD 比例 | 33.33% | <30% | 走阈值 | **否（honest-FAIL）** |
| 循环依赖 | 0 | 0 | 走阈值 | 是 |
| 孤立节点率（domain） | 0.00% | <5% | 走阈值 | 是 |
| 误用修复数 | 7 | ≥3 | 走阈值 | 是 |
| stdmodels.lookup R1 文献锚点最差相对误差 | 1.3519% | 3%（平台通过档） | 物理量 | 是 |
| stdmodels.lookup R3 隐含密度最差相对误差 | 102602.7% | 15%（不可接受档） | 物理量 | **否（>15%，平台真实缺陷）** |

注：判据 (a)–(f) 判的是**治理口径**（比例/覆盖率/环），不是物理量精度；物理精度的两个真实量测来自标准模型库独立验证脚本（R1 通过、R3 失败），两者都是**重跑即得**的数字，不是转述。

### 复现命令

```bash
python3 -m tools.capability_semantics --run-verifiers --figures \
    --doc docs/reports/P266W2_N10_capability_semantics.md --coverage-pct 93.0
python3 -m pytest tools/capability_semantics/tests \
    backend/tests/test_capability_semantics_api.py -q
python3 -m pytest tools/capability_semantics/tests -q \
    --cov=tools/capability_semantics --cov-branch
```

