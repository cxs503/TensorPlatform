# 任务梯度与探索预算机制（IN19-Q6）

> 战役节点 IN19-Q6 · 落库 TensorPlatform · 风险等级：机制类（无领域语义）
> 交付物：`backend/services/mission_gradient.py`、`backend/api/mission_gradient.py`、
> `backend/migrations/103_mission_gradient.sql`、`backend/tests/test_mission_gradient.py`、
> `scripts/mission_gradient_q1_selfcheck.py`、`scripts/mission_gradient_figures.py`、
> `artifacts/mission_gradient/`

## 1. 问题与设计

任务池此前是"平的"：所有 mission 同一档预算、同一套失败统计、同一套派发资格。
后果有三个可观测病灶：

1. **探索型工作无法诚实失败**。S4/S5（突破/创造）单本质上是"结果未知"的投入，
   若与执行型单共用同一套失败率统计，理性策略就是只领确定性高的活 → 探索被饿死。
2. **派发不看资历**。高不确定性的单被派给信用档位低的 worker，失败率高且无学习价值。
3. **失败无沉淀**。失败只留一个 `failed` 状态，根因与复盘随 worktree 一起被回收。

机制给出四条规则（全部可机器判定）：

| 规则 | 实现 |
|---|---|
| 任务梯度 S1..S5 | `GRADIENT_TIERS` / `TIER_LABELS`（执行/改进/建设/突破/创造） |
| 播种配比登记，起步 60/25/10/4/1，可调 | `mission_gradient_mix` 表 + `validate_mix`（非负、和为 1±1e-6、版本化、`mix_signature`） |
| 配额精确分配 | `allocate_quota`（Hamilton 最大余数法，确定性 tie-break → 可复算/可重放） |
| S4/S5 单带探索预算标记 | `exploration_budget`（仅 S4/S5 有 `exploration=True`、`failure_quarantined=True`、`reserve_tool_calls`） |
| honest FAIL 不计入失败率 | `classify_outcome` + `failure_accounting`：可采纳 honest FAIL 同时从分子与分母移出；`naive_rate` 并列给出，差额可审计 |
| 失败必须留根因 + 复盘 | 根因 ≥8 字符、复盘 ≥20 字符，缺一项即**降级为普通失败**（照样计数） |
| 否定性成果轨 S-N | `negative_result_entry` → `mission_negative_results` 表（append-only 触发器，禁 UPDATE/DELETE） |
| 派发按信用档位匹配 | `dispatch_decide`：tier `Sn` 要求信用 `Ln` 及以上 → **S4 需 L4+，S5 仅 L5**；理由码 `ok / invalid_tier / tier_share_exhausted / credit_gate_blocked / no_available_worker` |
| honest FAIL 不可覆盖 | `guard_honest_fail`：已记录的 honest FAIL 不能被改写为 succeeded/failed，只能追加新行 |

## 2. 落库形态

迁移 `backend/migrations/103_mission_gradient.sql`（幂等，全 `IF NOT EXISTS` / `DO $$ … EXCEPTION`）：

- `tensor_dev_missions` 增列：`gradient_tier`(S1..S5, CHECK)、`exploration_budget`(JSONB，CHECK 仅 S4/S5 可非空)、
  `gradient_self_declared`、`honest_fail`、`fail_root_cause`、`retrospective`、`negative_result_ref`；
  并加 CHECK `ck_tensor_dev_missions_honest_fail_evidence`：标 `honest_fail=TRUE` 必须同时具备根因(≥8)与复盘(≥20)。
- `mission_gradient_mix`：版本化配比登记表，`ck_mission_gradient_mix_sum` 在库侧再校验和为 1，
  每组织仅一条 `active`。
- `worker_credit_tiers`：worker → L1..L5（`ck_worker_credit_tiers_level`）。
- `mission_negative_results`：S-N 否定性成果轨；触发器 `trg_mission_negative_results_append_only`
  拒绝 UPDATE/DELETE——**可编辑的否定性成果等于没有**。
- 视图 `worker_failure_rate_v1`：`disciplined_failure_rate`（排除 quarantined honest FAIL）与
  `naive_failure_rate`（平计数）并列，两者差值即"探索税"。

字段落地证据：`artifacts/mission_gradient/q1_selfcheck.json` 的 `C8_migration_contract` 项。

## 3. 派发集成点（一行）

```python
from services.mission_gradient import dispatch_decide

decision = dispatch_decide({"mission_id": mid, "tier": tier}, candidate_workers,
                           quota_remaining=quota_left)
if not decision["allowed"]:
    ...  # reason 字段直接可入 event/审计：credit_gate_blocked / tier_share_exhausted / ...
```

`dispatch_decide` 是纯函数（无 DB/网络），确定性排序：信用高优先 → 在手任务少优先 → worker_id 稳定序，
因此同一输入必然给出同一派发给定，可复算。

## 4. 与平台冻结期的关系（重要局限）

owner 2026-11-02 令：TensorPlatform 处于冻结期，不做平台开发。本节点以**附加、默认关闭**方式落地：

- `backend/main.py` 中的挂载被 `TP_MISSION_GRADIENT=1` 开关包住，**默认不挂载**；
- 未改动 `api/tensor_missions.py`、`api/campaign_plans.py` 等任何既有派发/生命周期路径
  （`test_wiring_is_feature_flagged_so_the_live_dispatch_path_is_untouched` 机器校验）；
- 机制本身以库函数形式可用、可测、可被解冻后的 dispatcher 一行接入。

诚实声明：本节点完成的是"机制 + 判据 + 自证"，**不是**线上派发链路的切换。切换需 owner 解冻决策。

## 5. 自证（Q1 评价）

`python3 scripts/mission_gradient_pg_verify.py`（真库验证）与
`python3 scripts/mission_gradient_q1_selfcheck.py --mission-id 5026`（真的跑测试套件并逐项评估）：

| 判据 | 内容 | 结果 |
|---|---|---|
| C1 | 机制测试套件实跑，≥6 通过且 0 失败 | 36 passed / 0 failed |
| C2 | 起步配比 = 60/25/10/4/1 且和为 1 | pass |
| C3 | N=100、N=1000 配额精确用尽且线性（×10） | pass（100→{60,25,10,4,1}） |
| C4 | 档位匹配矩阵 5×5：S4→L4+、S5→仅 L5，15 admit / 10 refuse | pass |
| C5 | 探索预算标记仅 S4/S5；S1..S3 不能申报 honest FAIL | pass |
| C6 | 有据 honest FAIL 免疫失败率、无据降级为失败、覆盖被拒 | pass |
| C7 | 机制源码零领域词（中英域词正则扫描） | pass |
| C8 | 迁移 DDL 契约（字段/登记表/S-N 轨/视图/append-only 触发器）齐备 | pass |
| C9 | 迁移在**真 PostgreSQL** scratch 库上执行两次（幂等）+ 全部拒绝用例/视图算式 | 17/17 pass |

机制以**自身建设**申报 S3（`self_declare` → `declared_tier=S3`、`exploration=False`、`min_credit_level=3`、
`self_declared=True`）——它不是突破型实验，因此不领探索预算，也不用 honest FAIL 通道。

## 6. 图表

- `fig1_quota_allocation.png`：登记配比 vs 一个合法调整配比；N=100 的精确配额（探索预算 5/100 槽）。
- `fig2_credit_gating_matrix.png`：梯度档 × 信用档 5×5 准入矩阵（15 admit / 10 refuse）。
- `fig3_failure_rate_accounting.png`：同一 worker 3 次普通失败、k 次 honest FAIL 时的
  "纪律化失败率"与"平计数失败率"分叉；以及三类申报的可采纳性。
- 图上数字全部在绘图时由 `services/mission_gradient.py` 现算，不手抄。
- 图上梯度档名用 ASCII（execute/improve/build/breakthrough/create）：构建机无 CJK 字体，
  `figures_metadata.json` 里记录了该取舍，避免豆腐块。

## 7. 局限与未做

1. **未接线上 dispatcher**（冻结期，见 §4）。
2. **迁移只在 scratch 库验证、未上生产库**：`scripts/mission_gradient_pg_verify.py` 在本机
   PostgreSQL 15.5（loong64）上建临时库、建前置最小 schema、**连续执行 `103` 两次**（幂等），
   并探测 17 项契约（含 honest_fail 缺证据拒收、S2 挂探索预算拒收、配比和≠1 拒收、
   S-N 轨 UPDATE/DELETE 拒收、第二个 active 配比拒收、失败率视图算式 0.5000 vs 0.6667），
   全部通过（`artifacts/mission_gradient/pg_verify.json`）；验证后临时库已删除。
   生产库执行仍需 owner 解冻后由迁移 runner 跑一次。
3. **信用档位数据源未定**：`worker_credit_tiers` 目前需人工/治理面写入；未填时默认姿态是
   "无人达到 L4"，即 S4/S5 派发**fail-closed 拒绝**（宁可不派，不可乱派）。
4. **失败率分母口径**：honest FAIL 同时移出分子与分母（"探索不是失败计数的对象"）。若 owner 希望
   保留"探索投入占比"作为独立指标，可再叠一层 `exploration_share`，当前未实现。
5. **配额与派发的簿记**：`quota_remaining` 由调用方（campaign 层）维护并传入；本机制不自行扣减，
   避免在冻结期引入跨模块写竞争。真实扣减需随 dispatcher 切换一起完成。