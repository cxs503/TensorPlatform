# 复核带裁决通道：near_duplicate 提案的可处置出口（守住 fail-closed）

**落地仓**：TensorPlatform（平台共性机制，零领域语义）
**代码**：`tools/proposal_gate/gate.py`（`review_band` / `adjudicate` / `adopt` 守卫）+
`backend/api/proposal_gate.py`（`GET /proposals/gate/review`、`POST /proposals/gate/review/resolve`）+
`config/proposal_gate.json`（`review` 段）
**证据**：`artifacts/proposal_gate/review_band/evidence.json` + `review_band_lifecycle.png`（sha256 入证据）

## 1. 问题：供给链上有一段「无人区」

提案门已成三道机器门（契约 / 跨提案去重 / 播种覆盖率账本，见
`docs/P128W3_N08_proposal_gate_maturity.md`）。但去重门的判定是三档而不是两档：

| 相似度区间（配置） | 判定 | 落态 |
|---|---|---|
| `score ≥ dup_threshold` | `duplicate` / `twin` | `dedup_rejected` |
| `review_threshold ≤ score < dup_threshold` | `near_duplicate` | `quarantined`（**复核带**）|
| 否则 | `accepted` | `proposal` |

中间这一档的设计意图是「不自动拒、也不入池」。落地时它缺了一个必要的机器件：
**出带通道**。后果有两个，都是治理缺口：

1. **凭空蒸发**：带上的行没有队列视图、没有裁决入口，永远是 `quarantined`——
   在四态统计里它和「契约不合格」混在一起，重复率 KPI 也看不出有多少是被搁置的；
2. **纪律可绕**：`POST /gate/adopt` 此前只校验「行存在」，因此一行**未裁决的带行**
   （甚至一行契约失败行）可以被 adopt 一步推到 `adopted`——「不进仲裁」的 fail-closed
   语义被一条普通写接口绕过。

本改动补上出带通道，并把两条绕过路径堵死（守卫在模型层，不在 API 层，避免旁路）。

## 2. 设计：裁决是一个带留痕的状态机动作

### 2.1 队列视图

`ProposalGate.review_band()` / `GET /proposals/gate/review`

* 队列成员由配置双条件决定：`state ∈ review.queue_states`（当前 `quarantined`）
  **且** `verdict ∈ review.queue_verdicts`（当前 `near_duplicate`）。
  因此**契约失败行（verdict=`quarantined`）不进队列**——它的正确出路是重新提交合规载荷，不是复核放行；
* 排序按 `score` 降序（最像重复的先看），素材全部取自账本列，不重算相似度：
  队列视图与判定口径同源，不做二次解释。

### 2.2 显式裁决

`ProposalGate.adjudicate()` / `POST /proposals/gate/review/resolve`

| 决策 | 目标 state | 目标 verdict | 语义 |
|---|---|---|---|
| `admit` | `proposal` | `accepted` | 复核认定为不同立项 → 入池，可被采纳 |
| `reject` | `dedup_rejected` | `near_duplicate`（保留） | 复核认定为同义改写 → 拒 |

留痕：`reasons` 追加 `review_decision=… reviewer=… at=… prev_state=… prev_verdict=… note=…`，
同时写事件 `proposal_review_adjudicated`（含前态、决策、复核人、score）。裁决理由长度下限
`review.min_note_length`（当前 8）来自配置，`reviewer` 非空必填——**没有匿名裁决**。

### 2.3 守卫（全部 fail-closed，原因码机器可裁）

| 原因码 | 触发条件 | HTTP |
|---|---|---|
| `adopt_requires_state` | adopt 的行 state 不在 `review.adopt_allowed_states`（当前仅 `proposal`） | 409 |
| `not_in_review_band` | 要裁决的行不在带上（含契约失败行、已出带的行） | 409 |
| `unknown_decision` | 决策名未在 `review.decisions` 登记 | 409 |
| `reviewer_required` / `note_too_short` | 复核人为空 / 理由短于配置下限 | 409 |
| `ledger_row_not_found` | 账本无此行 | 404 |

裁决端点比其它写端点更严：只对 `ADMIN` / `MANAGER` 开放（`evaluate`/`adopt` 是
`ADMIN`/`MANAGER`/`ENGINEER`）——出带是治理动作，不是工程默认权限。

## 3. 计算结果（真跑，非手抄）

场景（合成域名 foo/bar/widget，`tools/proposal_gate/review_band_evidence.py`，一次跑完落证据）：

| 步骤 | verdict | state | score | 说明 |
|---|---|---|---|---|
| submit_A | `accepted` | `proposal` | 0.0000 | 首次入池 |
| submit_B_twin | `near_duplicate` | `quarantined` | **0.6364** | 同义改写，落进 `[0.55, 0.85)` 复核带 |
| submit_bad_contract | `quarantined` | `quarantined` | — | 判据无量化阈值 → 契约门 fail-closed |
| submit_dup_of_A | `duplicate` | `dedup_rejected` | 1.0000 | 逐字重复 → 自动拒 |

守卫实测（均被拦下）：

| 尝试 | 结果 |
|---|---|
| 未裁决直接 adopt 带行 | 拒绝，`adopt_requires_state` |
| 裁决一行契约失败提案（admit） | 拒绝，`not_in_review_band` |

四态计数（账本 SQL 计数，before → after `admit(twin)`）：

| state | before | after |
|---|---|---|
| `proposal` | 1 | **2** |
| `adopted` | 0 | 0 |
| `dedup_rejected` | 1 | 1 |
| `quarantined` | 2 | **1** |

即：带上 1 行经显式裁决入池，`quarantined` 里剩下的正好是那行契约失败提案（未被放行）。

误差判定：本改动是状态机/治理语义，关键量是**离散判定与计数**，不存在数值误差；
全部计数由账本 SQL 直接统计（`LedgerStore.stats()`），与上图右panel 同源，逐项一致。
配置边界（`0.55` / `0.85` / `min_note_length=8`）不写进代码，`tests/test_proposal_gate_review_band.py`
中 `test_note_min_length_comes_from_config` 用「把下限抬到 64 → 同一段理由变不够长」证明阈值确实配置驱动。

## 4. 测试与回归

| 命令 | 基线（HEAD 干净检出） | 本改动 |
|---|---|---|
| `pytest tests/test_proposal_gate_*.py tests/test_threshold_registry.py tests/test_campaign_ledger_synthetic_domain.py` | 159 passed | **174 passed**（+15 新用例，无失败） |
| `pytest tests/test_gate_wiring_audit.py test_governance_proposal_dedup.py test_tenancy_gate*.py test_autonomy_acceptance_gate.py test_semantic_gate.py test_boundary_gate*.py test_write_gate.py` | 291 passed, 11 skipped | **291 passed, 11 skipped**（逐项相同） |
| `python -m tools.criteria.threshold_registry validate/drift` | 绿 | 绿（源文件摘要随 `config/proposal_gate.json` 变更重盖章，见下） |
| `python -m pytest tests/test_threshold_registry.py` | 50 passed + 2 failed（摘要漂移） | **52 passed** |

新用例覆盖：带上可见性（契约失败行不入队）、admit/reject 迁移与理由留痕、事件流、
未裁决 adopt 被拒、契约失败既不可裁也不可采纳、未知决策、复核人/理由必填、
配置驱动下限、API 五类错误码（404/409×4）、治理角色 403、config 端点暴露 review 参数。

## 5. 注意点与局限

* **治理耦合**：`config/proposal_gate.json` 被 `data/criteria/threshold_registry.json` 以文件摘要钉住，
  改配置会让判据漂移门变红。本次按仓内既定纪律**只重盖该文件的摘要**（4 条 `source_ref`），
  未重跑整表 harvest——因为 harness 抽样含大量与本改动无关的轮换（会引入数十条无关 diff）。
  若后续维护者要动门阈值，仍应走 `make criteria-harvest` 全量入册。
* **不回头改索引**：admit 只是把行从带上挪进池，不会把该提案补进当前进程的去重索引
  （索引按次构建）；下一次 evaluate 前重建索引即可生效，语义上不丢（账本 blob 已在）。
* **reject 保留原 verdict**：`reject` 后 verdict 仍是 `near_duplicate`（state 转 `dedup_rejected`），
  这样重复率 KPI 仍把它算作「相似提案被拒」；若需要区分「自动拒 / 复核拒」，
  看 `reasons` 里的 `review_decision=` 与事件流。
* **单次裁决不可撤销**：裁决是单向状态迁移（出带即不再进队列）；需要推翻时应以新提案重提，
  而不是回滚账本——这符合审计留痕优先于便利的原则。
* **未做**：带上行的 SLA/超时自动上报（谁在带里待久了）未实现；批量裁决（一个请求裁多行）未实现。