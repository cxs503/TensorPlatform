# IN19-Q3 · 质量门 E 级强制与四类审查接入（TensorPlatform）

MISSION: m5025（战役 DAG 节点 IN19-Q3）· 分支 `agent/m5025`
SPEC 依据：`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md`
§6.1（四类审查）、§6.2（E0–E4）、§6.3（证据原则）、§7.1-8/-9（S×E 合法性）。
工件（本报告全部数字由 `tools/ops/m5025_outcome_review_audit.py` 程序化计算后注入）：
`artifacts/m5025/outcome_review_gate_audit.json`、`artifacts/m5025/fig_se_legality_and_ticket_lifecycle.png`。

## 1. 做了什么（方法 / 关键设置 / 工具链）

本单把 SPEC v1.2 的收紧条款落成两个**零领域语义**的机器门：

1. **成果登记质量门** `tools/ops/outcome_evidence_gate.py`（新增）
   —— 6 条判据 G1..G6，任一 FAIL 即整体 FAIL（阻断登记，非降级警告）：

| 判据 | 内容 | SPEC |
|---|---|---|
| G1 | 任何等级登记须 E≥1 且有可检查制品 | §7.1-8 + §6.2 |
| G2 | 申报 S5 须 E≥3 | §7.1-9（v1.2 收紧） |
| G3 | 等级须附理由（≥12 字符） | §7.1-1/-4 |
| G4 | S4 申报已确认须 E≥2+对照基线，否则只能候选 | §7.1-9 |
| G5 | 申报 S5 须声明新颖性检索范围 | §7.1 S5 最低评价依据 |
| G6 | 否定性成果 S-N 登记须 E≥2 | §7.1 否定性成果门槛 |

2. **四类审查工单台账** `tools/ops/review_ticket_ledger.py`（新增）
   —— Execution Audit / Technical Review / **Outcome Assessment** / **Innovation Review**；
   Outcome 与 Innovation 接入 **R3 验证专员线**（受理角色固定 `verifier`），
   生命周期 `open → assign → close`，事件史 append-only。

| 审查类型 | 审查对象 | 受理角色 | 必填载荷字段 |
|---|---|---|---|
| execution_audit | 是否真实执行、记录完整、资源与预算符合授权 | `operator` | evidence_refs |
| technical_review | 实现/设计/接口/测试/安全/可维护性是否满足要求 | `reviewer` | diff_ref, test_evidence |
| outcome_assessment | 结果是否达到目标、是否产生价值、局限是否披露 | `verifier` | claimed_grade, evidence_level, acceptance_refs |
| innovation_review | 新方法/新能力/新假设是否有对照实验与证据支持 | `verifier` | novelty_search_scope, hypothesis |

Innovation Review 的 `novelty_search_scope` 支持字符串（≥8 字符）或
`{"sources": [...], "period": "..."}` 结构；缺失/空集合 = 开单被拒。

3. **杀伤性测试面**（QUALITY_BAR R1 作用于门自身）：`tools/ops/test_outcome_evidence_gate.py`
   与 `tools/ops/test_review_ticket_ledger.py`，`@pytest.mark.mutation` 标记的应拒探针
   **17 个**。

## 2. 判据数字（机器验收）

| 验收项 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 二判据 E≥1 / S5⇒E≥3 落地 | 强制 | G1、G2 常驻 | PASS |
| 二门各带 ≥2 负例 MUST FAIL | ≥2 | G 门 7 条负例、其中 7 条如期变红；工单门 5 条负例、其中 5 条如期拒收 | PASS |
| 四类工单可开可结 | 4/4 | 4/4 关单（approve） | PASS |
| pytest | ≥8 | 42 passed / 0 failed（rc=0） | PASS |
| 零领域语义 | 域词禁硬编码 | 模块源码域词扫描为空，夹具 `synthetic-alpha` | PASS |

### 2.1 成果登记门负例（注入应拒样例 → MUST 红）

| 样例 | 注入缺陷 | S/E | 期望命中 | 判决 | 是否红 |
|---|---|---|---|---|---|
| `no-evidence-E0` | 无证据：E0 纯声明 | S3/E0 | `G1` | **FAIL** | ✔ 红 |
| `no-artifact-E1` | 无证据：自报 E1 但零制品 | S3/E1 | `G1` | **FAIL** | ✔ 红 |
| `low-evidence-high-grade` | 低报高：S5/E2 | S5/E2 | `G2` | **FAIL** | ✔ 红 |
| `no-reason-grade` | 无理由等级 | S3/E2 | `G3` | **FAIL** | ✔ 红 |
| `s4-confirmed-no-baseline` | S4 已确认缺对照基线 | S4/E2 | `G4` | **FAIL** | ✔ 红 |
| `s5-no-novelty-scope` | S5 缺新颖性检索范围 | S5/E3 | `G5` | **FAIL** | ✔ 红 |
| `s-n-below-e2` | 否定性成果 S-N/E1 | S-N/E1 | `G6` | **FAIL** | ✔ 红 |

### 2.2 成果登记门正例（防恒假）

| 样例 | 说明 | S/E | 判决 / 分类 |
|---|---|---|---|
| `s3-e2-confirmed` | 常规可登记成果 | S3/E2 | **PASS** / CONFIRMED |
| `s5-e3-confirmed` | S5 满证 | S5/E3 | **PASS** / CONFIRMED |
| `s4-e1-candidate` | S4 候选（不计统计） | S4/E1 | **PASS** / CANDIDATE |

统计口径（§7.1-9，非法 S×E 不计入成果分布）：
total=10,
confirmed=2,
candidate=1,
rejected=7,
countable=2。

### 2.3 审查工单负例（注入违例 → MUST 拒收）

| 样例 | 注入缺陷 | 结果 | 是否拒收 |
|---|---|---|---|
| `innovation-no-novelty-scope` | Innovation 审查缺新颖性检索范围声明 | `TicketRejected` | ✔ 拒收 |
| `outcome-wrong-role` | Outcome 审查由非 verifier 角色受理 | `TicketRejected` | ✔ 拒收 |
| `self-review` | 审查者=生产者（独立性违例） | `TicketRejected` | ✔ 拒收 |
| `close-before-assign` | 未派单即结单 | `TicketStateError` | ✔ 拒收 |
| `close-no-rationale` | 结单无理由 | `TicketRejected` | ✔ 拒收 |

### 2.4 四类工单开结记录

| 工单 | 类型 | 受理角色 | 终态 | 裁决 |
|---|---|---|---|---|
| `RT-execution_audit-0002` | execution_audit | `operator` | closed | approve |
| `RT-technical_review-0003` | technical_review | `reviewer` | closed | approve |
| `RT-outcome_assessment-0004` | outcome_assessment | `verifier` | closed | approve |
| `RT-innovation_review-0005` | innovation_review | `verifier` | closed | approve |

## 3. 结果图

![S×E legality matrix and ticket lifecycle](../../artifacts/m5025/fig_se_legality_and_ticket_lifecycle.png)

图 (a)：对 6 个等级 × 5 个证据等级共 30
个组合逐格调用 `evaluate()` 计算得到的合法矩阵（红=FAIL 不可登记、黄=候选不计统计、
绿=已确认可计数）——注意 E0 整列为红（G1），S5 行 E0–E2 为红（G2）。
图 (b)：四类工单 `open→assign→close` 生命周期与受理角色，右侧为本次实测开/结计数。

S×E 矩阵（单元格 = 分类）：

| grade \ E | E0 | E1 | E2 | E3 | E4 |
|---|---|---|---|---|---|
| S1 | REJECTED | CONFIRMED | CONFIRMED | CONFIRMED | CONFIRMED |
| S2 | REJECTED | CONFIRMED | CONFIRMED | CONFIRMED | CONFIRMED |
| S3 | REJECTED | CONFIRMED | CONFIRMED | CONFIRMED | CONFIRMED |
| S4 | REJECTED | CANDIDATE | CANDIDATE | CANDIDATE | CANDIDATE |
| S5 | REJECTED | REJECTED | REJECTED | CONFIRMED | CONFIRMED |
| S-N | REJECTED | REJECTED | CONFIRMED | CONFIRMED | CONFIRMED |

## 4. 注意点（坑 / 局限 / 适用范围）

1. **门可以失败，但门也会误伤**：G3（理由 ≥12 字符）与 G5（检索范围 ≥8 字符）是
   形式判据，只能拦住"空理由"，不能判定理由是否真实对应成果——真实性仍靠
   Outcome/Innovation 独立审查（工单门要求证据引用 + 审查者≠生产者）。
2. **S5 收紧的代价**：v1.2 把 S5 硬化为 E≥3，原 §7.1-9 的「S5/E0–E1 候选」通道
   因此关闭（本单按 mission 判据从严实现）。候选通道仍由 S4/E<2 承载（
   `declared_confirmed=False` 时 PASS 但 `countable=False`）。若治理侧要求保留
   S5 候选通道，只需改 G2 的适用条件——这是**唯一**需要重新裁决的分支。
3. **域中立是刻意约束**：平台不定义证据等级的领域含义（SPEC §6.3）。领域验证器
   需自行给出 E2/E3 的领域判据（例如数值域约定 E2=回归+收敛、E3=对独立锚误差
   在容差内），本模块只承载结构与记录。
4. **未接入的部分（如实披露）**：本次未改动 `tools/ops/quality_gate.py`（R1–R6 PR 门）
   与 `backend/api/tensor_missions.py` 的 complete 路径——理由：平台冻结期禁动运行
   底座，且本单判据只要求门与工单接入 machine-checkable 模块。接续点：把
   `summary.quality_gate` 的审计扩展为调用 `outcome_evidence_gate.evaluate()`，
   并在 `complete` 受理前用 `ReviewTicketLedger` 检查 S3+ 是否已闭环四类审查。
5. **基线红记账（QUALITY_BAR R4）**：`tools/ops/benchmark/test_benchmark_dispatch.py::test_backlog_backward_step_l4_is_gpu`
   在本分支与 main 上均为红（与本次改动无关，已用移动 `tools/ops/pytest.ini` 前后对照复验：
   1 failed / 33 passed 两次一致）。按 R4 只记录不修改，本次不新增红：`tools/ops` 全目录
   116 passed / 1 failed（该既有红），本单新增两套件全绿。
6. **工单台账未落数据库**：当前为 JSON 快照 + append-only 事件史（`save/load`），
   便于审计与回放；并发场景需加文件锁或迁移到 DB（未做）。

## 5. 误差判定（本单为后端/治理任务，无数值基准）

| 关键量 | 基准 | 实测 | 是否 <3% |
|---|---|---|---|
| 成果登记门分类正确率 | 7 条注入样例的期望判决（手工设计判据） | 7/7 负例如期红、3/3 正例如期绿（离散一致，误差=0） | 是（离散 100% 一致） |
| 工单拒收正确率 | 5 条违例注入 | 5/5 如期拒收（离散一致） | 是 |
| pytest 通过率 | 全绿 | 42 passed / 0 failed | 是 |

无物理量基准：本单不产生数值仿真结果，故无场量/曲线误差可比；上表以离散一致性
代替连续误差判据，并显式声明理由（无物理基准，不适用 3% 门槛）。

## 5.1 复现指令

```bash
python -m pytest tools/ops/test_outcome_evidence_gate.py tools/ops/test_review_ticket_ledger.py -q   # 42 passed
python tools/ops/m5025_outcome_review_audit.py    # 重算全部数字 + 重画图 + 重写本报告
python tools/ops/outcome_evidence_gate.py --rules # 判据清册
REVIEW_TICKET_STORE=/tmp/rt.json python tools/ops/review_ticket_ledger.py types | list | stats
```

## 6. 审计链

- SPEC：`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md` §6.1–§7.1
- 实现：`tools/ops/outcome_evidence_gate.py`、`tools/ops/review_ticket_ledger.py`
- 测试：`tools/ops/test_outcome_evidence_gate.py`、`tools/ops/test_review_ticket_ledger.py`
- 工件：`artifacts/m5025/outcome_review_gate_audit.json`（含逐例判决、矩阵、pytest 计数、台账快照）
- 图：`artifacts/m5025/fig_se_legality_and_ticket_lifecycle.png`
- 生成器：`tools/ops/m5025_outcome_review_audit.py`（重跑即复现全部数字）
