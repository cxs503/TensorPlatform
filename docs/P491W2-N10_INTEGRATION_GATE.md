# 战役集成与验收门（campaign-p491w2_aggregated_seeding）

- `gate_schema_version`: `1.0`
- `input_digest`: `f8d4df1189d587385120c73d6315557f983f7add1460677419bb0c55538878c4`（证据规格 + 全部被读证据文档 sha256 的 sha256）
- `snapshot_digest`: `5b6e761adceba3cfed19b8499c251f7e56419dad255b6779fc63583d2de40b45`（注册表快照，fetched_at=2026-11-11T06:11:23+00:00）
- `sources_config_digest`: `b4f879205affc9486f1f755dc2f9d00e7347d9676640cb9e6c3b480736594989`
- 战役计划：`p491w2_aggregated_seeding`（plan_id=9380，状态 active）
- **两层裁定**：证据层（物理判据可复算性）= **WITHHELD**；交付层（战役验收门 a–f）= **FAIL**

## ① 案例介绍

本节点的物理目标是把上游 N01–N09 **各自验证过**的多域成果集成成一份战役级交付，并在战役验收门上逐项裁决。难点不在算一个数，而在「凭什么相信上游的数」：报告里的形容词、绿色的测试、并进去的 PR 都不能直接当证据。故本文的案例对象是**集成链本身**——它的输入、它的证据门、它的裁定与它的成熟化单：

| 环节 | 输入 | 输出 | 机器判据 |
|---|---|---|---|
| 注册表 | 战役 DAG 快照（API → 落盘） | 节点×mission×验收清单 | 快照 sha256 入审计链 |
| 收集 | 节点工作树规范交付位 + 报告索引 | presence 文档（实测交付态） | 缺就写缺，不补值 |
| 证据层裁定 | 证据文档 + 证据规格 | PASS/FAIL/WITHHELD 三态 | 无证据不 PASS |
| 契约层 | 跨节点接口契约 + 注册表自洽 | 0 违反 / 违反明细 / 未检 | 任一侧无证据即 WITHHELD |
| 成熟化 | 判定体 + presence | feature_ledger INSERT + rollback | 四道准入才 REAL |

参与本战役的上游节点（全部数字来自快照与 presence 文档，非手抄）：

| 节点 | 仓库 | mission | 任务状态 | 实测 artifact | 报告 | 证据等级 | 判据通过率 |
|---|---|---|---|---|---|---|---|
| `p491w2-n01-ledger-liveness-maturity` | TensorPlatform | 13928 | in-flight | 0 | 0 | WITHHELD | 0 |
| `p491w2-n02-axisymmetric-scalar` | TensorLBM | 13246 | blocked | 0 | 0 | WITHHELD | 0 |
| `p491w2-n03-channel-memory` | TensorDEM | 13248 | blocked | 0 | 0 | WITHHELD | 0 |
| `p491w2-n04-bed-conductivity-gen2` | TensorDEM | 13249 | blocked | 0 | 0 | WITHHELD | 0 |
| `p491w2-n05-ice-wave-ultimate` | TensorPhysics | 13250 | blocked | 0 | 0 | WITHHELD | 0 |
| `p491w2-n06-propeller-ice` | ship-performance-platform | 13926 | done | 2 | 1 | PARTIAL | 0 |
| `p491w2-n07-propeller-quadrant` | ship-performance-platform | 13927 | in-flight | 0 | 0 | WITHHELD | 0 |
| `p491w2-n08-sn-knee-continuity` | ship-performance-platform | 13925 | in-flight | 1 | 1 | PARTIAL | 0 |
| `p491w2-n09-quasi-hydrostatic-domain` | ship-performance-platform | 13254 | blocked | 0 | 0 | WITHHELD | 0 |

战役规模：节点 9 个，实测交付 2 个（覆盖率 0.222222），artifact 总数 3（其中可解析 JSON 3 份），报告条目 2；任务状态分布 {'blocked': 5, 'done': 1, 'in-flight': 3}，其中基础设施阻塞 5 个。

**证据协议符合性**：0/9 个节点交付了协议要求的证据文档（无）。不符合协议的 artifact 只能证明『有东西』，不能证明『数可复算』——故它们的判据仍停在 WITHHELD，这是刻意的不给分。

## ② 怎么做的

命令：`python3 -m tools.campaign_integration.cli`。工具链：`tools/campaign_integration/`（sources → contracts → maturity → report 四模块）+ 复用 `tools/maturity_gate/`判据引擎（spec/evidence/gate/ledger/contracts/report）+ pytest。

关键设置：

1. **缺就写缺**：节点零 artifact 也照落 presence 文档（`delivery.delivered=false`），让「没交付」变成可复算事实；证据文档不存在则判据 WITHHELD——『在飞』绝不写成『通过』。
2. **判据是分母**：每节点 3 条判据（交付 / 测试 / 验收覆盖），通过率按判据算；少声明一条不会让通过率虚高。
3. **反空真**：跨节点接口契约任一侧无证据即 WITHHELD——「两边都没交付所以没矛盾」不算 0 违反；总体契约判定只在全部契约真被检查过时才可能 PASS。
4. **互裁**：证据文档可声明上游自述（`claim.*`）；自述为真而本引擎复算为假 → `claim_mismatch`，一票入账且不改判（造假嫌疑必须显形）。
5. **成熟化 fail-closed**：只有『有 artifact + 判据过绿线 + 无 FAIL + 无 claim_mismatch』四道全过才写 feature_ledger，且必附 evidence 路径与 sha256；WITHHELD 不写 INSERT。
6. **零领域语义**：本包代码内无学科词条；节点名/仓库/判据文字/接口名/单位全由`data/campaign_integration/*.json` 工件注入（域数据经配置注入，不进代码）。

## ③ 计算结果

### 3.1 证据层（判据可复算性）

| 量 | 值 |
|---|---|
| campaign.nodes_total | 9 |
| campaign.nodes_delivered | 0 |
| campaign.nodes_absent | ['p491w2-n01-ledger-liveness-maturity', 'p491w2-n02-axisymmetric-scalar', 'p491w2-n03-channel-memory', 'p491w2-n04-bed-conductivity-gen2', 'p491w2-n05-ice-wave-ultimate', 'p491w2-n06-propeller-ice', 'p491w2-n07-propeller-quadrant', 'p491w2-n08-sn-knee-continuity', 'p491w2-n09-quasi-hydrostatic-domain'] |
| campaign.nodes_green | [] |
| campaign.nodes_red | [] |
| campaign.min_pass_ratio | — |
| campaign.mean_pass_ratio | — |
| campaign.criteria_pass_threshold | 0.8 |
| campaign.claim_mismatch_total | 0 |
| campaign.verdict | WITHHELD |

### 3.2 交付层（战役验收判据 a–f）

| 判据 | 判决 | 实测/阈值 |
|---|---|---|
| a artifact coverage | FAIL | delivered 2/9 (coverage 0.2222), artifact_total 3 |
| b cross-node interface contracts 0 violations | FAIL | verdict FAIL; checked 1/5 (interfaces 0/4), violations 1, withheld 4 |
| c per-node acceptance pass-or-honest-record | PASS | passed 0, honest-absent 7 (withheld criteria 27), unrecorded 0, claim_mismatch 0 |
| d campaign report six elements | PASS | all present |
| e capability ledger REAL registrations >= 5 | FAIL | REAL(upstream) 0, REAL(all) 1 / required 5 (deferred 9) |
| f maturity order SQL + rollback | PASS | insert True, rollback True, rollback_covers True, inserted_entries 1, smoke=True (first=1, second=1, after_rollback=0) |

交付层裁定：**FAIL**；硬失败项 ['a', 'b', 'e']。

### 3.3 契约层

契约总数 5（接口契约 4 条，其中真被检查 0 条），WITHHELD 4 条，违反 1 条，裁定 **FAIL**。

| 契约 | 类型 | 接口 | 检查数 | 违反 | 状态 | 原因 |
|---|---|---|---|---|---|---|
| `registry-consistency` | registry | — | 53 | 1 | FAIL | — |
| `C1-ice-load-to-channel-resistance` | interface | ice-load-timeseries | 0 | 0 | WITHHELD | interface_absent:producer:ice-load-timeseries,consumer:ice-load-timeseries |
| `C2-quadrant-kt-to-accretion` | interface | propeller-kt-vs-j | 0 | 0 | WITHHELD | interface_absent:producer:propeller-kt-vs-j,consumer:propeller-kt-vs-j |
| `C3-sn-branch-to-ultimate` | interface | sn-curve-branches | 0 | 0 | WITHHELD | interface_absent:producer:sn-curve-branches,consumer:sn-curve-branches |
| `C4-ledger-verdict-to-registration` | interface | capability-evidence-verdict | 0 | 0 | WITHHELD | interface_absent:producer:capability-evidence-verdict,consumer:capability-evidence-verdict |

违反明细（逐条，含节点/规则/证据）：

- `registry-consistency` → `report_mission_mismatch` → {'node': 'p491w2-n08-sn-knee-continuity', 'report_id': 13381, 'claimed_missions': [13845]}

### 3.4 成熟化单（feature_ledger）

条目 10，REAL 1（其中上游 0，集成节点自身机制正控 1），deferred 9，等级分布 {'REAL': 1, 'PARTIAL': 2, 'WITHHELD': 7}。

REAL 条目（已生成 INSERT，证据路径逐条可溯源）：

| 条目 | 等级 | 证据 |
|---|---|---|
| `platform.campaign-integration-gate` | REAL | `tools/campaign_integration/cli.py`@37d9ead64363, `tools/campaign_integration/sources.py`@5b8ad60d747a, `tools/campaign_integration/contracts.py`@e7129856a54a, `tools/campaign_integration/maturity.py`@692737489f52, `tools/campaign_integration/report.py`@05067d57b69e, `tools/campaign_integration/sql_smoke.py`@321cc5d351ba, `tools/campaign_integration/fetch.py`@1702672540f6, `tests/test_campaign_integration_gate.py`@ff7033d97414, `docs/CAMPAIGN_EVIDENCE_PROTOCOL.md`@f6cebd0bb0c4；pytest rc=0 green=True |

deferred 明细（不写 INSERT 的具名原因）：

| 条目 | 原因 |
|---|---|
| `p491w2-n01-ledger-liveness-maturity` | no_artifact_delivered, pass_ratio_below_threshold |
| `p491w2-n02-axisymmetric-scalar` | no_artifact_delivered, pass_ratio_below_threshold |
| `p491w2-n03-channel-memory` | no_artifact_delivered, pass_ratio_below_threshold |
| `p491w2-n04-bed-conductivity-gen2` | no_artifact_delivered, pass_ratio_below_threshold |
| `p491w2-n05-ice-wave-ultimate` | no_artifact_delivered, pass_ratio_below_threshold |
| `p491w2-n06-propeller-ice` | pass_ratio_below_threshold |
| `p491w2-n07-propeller-quadrant` | no_artifact_delivered, pass_ratio_below_threshold |
| `p491w2-n08-sn-knee-continuity` | pass_ratio_below_threshold |
| `p491w2-n09-quasi-hydrostatic-domain` | no_artifact_delivered, pass_ratio_below_threshold |

## ④ 结果图

**fig1_node_evidence.png** — 每节点三态判据数（蓝=PASS，红=FAIL，灰=WITHHELD）+ 该节点实测 artifact 数（art=）。灰柱意味『无从判断』，不是『不过』。

**fig2_delivery_criteria.png** — 战役交付判据 a–f 判决看板；条内文字为程序化注入的实测/阈值。

**fig3_contracts.png** — 契约逐条：注册表自洽（当期可判）与跨节点接口契约（缺证据即灰）。『0 违反』只在真被检查过时才算通过。

## ⑤ 注意点

1. 上游 9 个节点：任务状态分布 {'blocked': 5, 'done': 1, 'in-flight': 3}，其中 5 个被基础设施阻塞（block_reason 含 infra）。本报告不把『在飞/阻塞』写成『通过』：证据层裁定只能是 WITHHELD。
2. 证据协议是**新契约**（`docs/CAMPAIGN_EVIDENCE_PROTOCOL.md`）：节点须把可复算数字写进规范交付位 `artifacts/<evidence_dir>/<node_key>.json`。既有的零散 artifact 字段命名各异，只有显式映射进规格的字段能被取值复算；未映射一律 WITHHELD，不做模糊猜测。当期实测：2/9 个节点有 artifact，其中协议符合 0 个——『有东西』与『数可复算』刻意分开计分。
3. 跨节点接口契约 4 条，当期真被检查 0 条：其余两侧均无接口证据 → WITHHELD。反空真规则刻意让它不产生『0 违反』的假绿。
4. 交付层硬失败项 ['a', 'b', 'e'] 是**实测缺口的直接后果**，不是判定器保守：覆盖率 0.2222、REAL 条目 1（其中上游 0）。FAIL 不可被其它绿项抵消，也不允许被后续报告覆盖。
5. 契约违反 1 条已逐条列明（含报告自述 mission 与索引 mission 不一致的报告链断裂）：这类问题不改变任何物理结论，但会让『按报告计分』的门失效，故报告只作辅助信号、不作判据。
6. 本节点不代跑上游仓库测试：上游测试属于其 own mission 的验收，平台不越权；本节点的 pytest 只覆盖集成机制（合成域夹具 + 真实战役工件重放）。
7. 特征化边界：机制层可复用于任意战役（节点/接口/单位全由工件注入），但不含领域判据推导——领域阈值仍须由各仓节点给出。

## ⑥ 误差判定

**是否<3%**：本任务为**集成与门裁定**，不做场量求解，也没有实验/解析基准量可对标，故『<3%』**不适用**（不是没算准，是没有可对标的连续量）。可对标的只有计数类判据（产物覆盖率、契约违反数、台账条目数），逐项列于下：实测 vs 阈值均为整数计数，不冒充 3% 精度宣称。

| 量 | 实测 | 判据 | 余量口径 |
|---|---|---|---|
| artifact coverage | 0.222222 | >= 1.0 (9/9) | 实测交付 2/9，协议符合 0 个 |
| contract violations | 1 | == 0 | 真被检查 1/5 条契约（接口 0/4） |
| interface contracts checked | 0 | == 4 | 两侧无证据即 WITHHELD（反空真） |
| REAL ledger entries (upstream) | 0 | >= 5 | 四道准入全过才计数；deferred 逐条附具名原因 |
| withheld criteria | 27 | == 0 | 判据因无证据停在 WITHHELD（不知道≠不过），逐条见 gate.json#nodes |

## 审计链

- 证据规格：`/root/tp-agent/work/m13929/data/campaign_integration/p491w2.spec.json`（由注册表快照程序化生成，零手抄）
- 判定体：`/root/tp-agent/work/m13929/artifacts/p491w2_maturity/gate.json`（input_digest 见上）
- 契约明细：`/root/tp-agent/work/m13929/artifacts/p491w2_maturity/contracts.json`；成熟化单：`/root/tp-agent/work/m13929/artifacts/p491w2_maturity/maturity_plan.json`
- SQL：`/root/tp-agent/work/m13929/artifacts/p491w2_maturity/feature_ledger.sql` + `/root/tp-agent/work/m13929/artifacts/p491w2_maturity/feature_ledger_rollback.sql`（回滚覆盖校验 True）
- presence 文档（逐节点 sha256）：
  - `p491w2-n01-ledger-liveness-maturity` → `98ae42a47ac53ec1508a276f4af9d9526f44c8945c4e0fc15e682d2b72ed3795`
  - `p491w2-n02-axisymmetric-scalar` → `55aaad8c5e15378924d7ce725c56f7943e79a3e155cfd74c353832d1f679eaa2`
  - `p491w2-n03-channel-memory` → `c28e74f606586fe785474b495afcb471f5ab68b0a6c75dbcc783fde6e146aef4`
  - `p491w2-n04-bed-conductivity-gen2` → `af4e4178e32efde239374bd773909f058f582d245cf16f91108a0a13c4c965ec`
  - `p491w2-n05-ice-wave-ultimate` → `cfdfe3f28ae1d588715b2cd973b9e393fb58b34969cc9cde2e405eb3ac82b140`
  - `p491w2-n06-propeller-ice` → `3a5d0d2dfc37ac291ece47aa42d2251ddb3dbf4344628236a8912cebbfb5ba87`
  - `p491w2-n07-propeller-quadrant` → `1165061ab1e80405ea2f9ad86ee0eb6f2273156d4a8ffe550452beb40e42d7d9`
  - `p491w2-n08-sn-knee-continuity` → `719211663c9ca613fae4e614413f2d015418c251f5004c9611f4ab13439a8f84`
  - `p491w2-n09-quasi-hydrostatic-domain` → `4bff2804056b6df77dd358404c947736e622475e98b52bd1cb2ddd3f2aebacb9`
  - `p491w2-n10-integration-maturity` → `acc1de0dd4fd28dc0b60b8f7f07d75c3f5cdee3035a1658844788af8ff04d7ab`
