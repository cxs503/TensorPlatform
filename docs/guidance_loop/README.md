# 引导回路（Guidance Loop）——评价牵引派发的接线

> 战役 IN24-E2 · 落库 TensorPlatform · 零领域语义（档位/角色/工件名全部由数据注入）

## 1. 为什么要这一段

平台的评价体系此前只有**记账**面：`credit_ledger` 记终态预算用量、`outcome_evaluations`
记结论，但没有任何机制让「存量评价统计」改变**下一轮派什么活**。评价因此是台账，
不是牵引。本模块把三条牵引通路接成代码：

| 通路 | 评价统计如何改变它 | 实现 | 落库实体 |
|---|---|---|---|
| ① 派发梯度 | 各档位历史产出率 → 份额校准（有界漂移 + 地板） | `gradient.py` | `task_gradient` |
| ② 准入集合 | worker 信用统计 → L1..L5 档位 → 可派档位集合（禁排名） | `admission.py` | （派生自 `guidance_outcome`） |
| ③ 探索预算 | 探索派发消耗 + honest FAIL 退还 + S-N 轨留痕 | `exploration.py` | `exploration_budget` |
| 记账面 | 四类审查 / 豁免 / 创新提案闸门 | `store.py` | `review_budget_ledger`、`innovation_proposals` |

## 2. 数值口径（全部为纯函数，无时钟/随机/IO）

档位 S1..S5 基线 `60/25/10/4/1`（`gradient.BASE_SHARE`）。每轮：

1. **产出率**（经验贝叶斯平滑）：`y = (succ + 4·0.6) / (sampled + 4)`，
   先验强度 4、先验 0.6——小样本档位不会被 1 次结果极端化；
2. **加权均值** `yield_bar = Σ base_t · y_t`；
3. **有界乘子** `m_t = clamp(1 + 1.5·(y_t − yield_bar), 0.75, 1.25)`；
4. **份额** `s_t = normalize(base_t · m_t)`；
5. **地板** `s_t ≥ 0.25·base_t`（缺口按比例从触地档位扣除，迭代至稳定）；
6. **整数化**：最大余数法守恒总数；探索档位（S4/S5）整数保底 1 单（池 ≥1 时），
   缺口从派单数最多的档位扣（确定序，可复现）。

由此得到两条性质，也是验收判据：

- **统计变 → 梯度变**：S4/S5 产出率上行则份额上调、S1 恶化则份额下调；
- **统计不变 → 梯度不漂**：`stats_digest`（规范化统计的 sha256）与 `cycle_id`
  由统计唯一决定，重放逐位相同、落库零新增行。

## 3. 信用档位 → 准入（禁排名）

| 档位 | 绝对阈值（同时满足） | 可派档位 | 探索预算倍数 |
|---|---|---|---|
| L5 | succ ≥ 25, rate ≥ 0.90, 真实失败 ≤ 0 | S1..S5 | 1.5 |
| L4 | succ ≥ 15, rate ≥ 0.85, 真实失败 ≤ 1 | S1..S5 | 1.0 |
| L3 | succ ≥ 8, rate ≥ 0.80, 真实失败 ≤ 3 | S1..S4 | 1.0 |
| L2 | succ ≥ 3, rate ≥ 0.70 | S1..S3 | 0 |
| L1 | 其余（含无样本） | S1..S2 | 0 |

- **绝对阈值、不含同行比较**：`build_admission` 逐 worker 独立计算，输出是以 worker
  为键的**映射**（不是列表），不含任何名次/序位字段；`assert_no_ranking` 做结构自检
  （只检查字段名，不扫数据键——关键词假阳性是本仓已知坑）。
- **honest FAIL 不降档**：`CreditStats.real_rate` 的分母不含 honest FAIL。

## 4. 探索预算与 honest FAIL 豁免

- 每轮开账：`allocated = (S4 6 + S5 2) × 档位倍数`；每次探索派发消耗 1；
- **honest FAIL**（探索档位的「诚实的失败」）：退还该次消耗（净额记账）+ 落 **S-N 轨**
  （`track='S-N'`, `exempt=TRUE`）+ 不进失败率分母；
- 预算耗尽 → `ExplorationExhausted`：该轮不得再派 S4/S5，回落 S1..S3；
- **只增不改**：同一 `mission_key` 重放为幂等 `duplicate`；任何结论改写被拒
  （`OutcomeConflictError`），honest FAIL 尤其不能被后续 `mechanical_fail` 覆盖
  （`HonestFailProtectedError`）——内存账本与持久层同一判据。

## 5. 实体清单（迁移 `backend/migrations/104_guidance_loop.sql`）

| 实体 | 作用 | 关键约束 |
|---|---|---|
| `review_budget_ledger` | 四类审查 / 豁免 / 生产计量 | `seq` 递增 + `prev_hash/entry_hash` sha256 链，只增不改 |
| `innovation_proposals` | 创新提案登记 | `status` 白名单；未过创新审查（`approved*`）不得升级 |
| `task_gradient` | 每轮梯度快照（5 行/轮） | PK `(cycle_id, tier)`；带 `stats_digest` |
| `exploration_budget` | 账户 / 结算 / S-N 轨 | `entry_type ∈ {account, settlement, s_n}`；`exempt` 标记 |
| `guidance_outcome` | 带档位的结论账本（统计基座） | PK `mission_key`，`outcome` 白名单 |
| `guidance_cycle` | 周期快照 | PK `cycle_id`；带统计指纹与公开载荷 |

迁移只使用 Postgres / SQLite 都接受的可移植子集（`IF NOT EXISTS`、
`TEXT/INTEGER/DOUBLE PRECISION/BOOLEAN/TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP`、
标准 `CHECK`），因此单测可以把**同一份迁移文件**当建库脚本执行，
`迁移 ⇔ 代码` 的漂移由测试卡住（`test_migration_creates_all_guidance_entities`）。

## 6. 怎么复现 / 怎么验证

```bash
# 单测（31 例，含负例与链校验）
cd backend && python3 -m pytest tests/test_guidance_loop.py -q

# 部署验证 + 生效实证（写生产库需口令走环境变量；不会打印/落盘凭据）
export PGPASSWORD=***           # 或 TP_PG_PASSWORD；host/user/db 可用 TP_PG_* 覆盖
psql -h 127.0.0.1 -U foundation -d tensorplatform -v ON_ERROR_STOP=1 \
     -f backend/migrations/104_guidance_loop.sql
python3 tools/guidance/evidence.py          # → artifacts/guidance_loop/{evidence.json,evidence.md}
python3 tools/guidance/figures.py           # → artifacts/guidance_loop/fig*.png

# 生产接线：派发侧每轮调一次
python3 tools/guidance/plan_cycle.py --total-pool 40 --cycle-id WAVE-<n> --json-out /tmp/plan.json
```

实证结果（程序化生成，零手抄）见 `artifacts/guidance_loop/evidence.md`：
A→B 轮 S1 份额 −0.0529、S4 +0.0106、S5 +0.0045；w-alpha L3→L2（收回 S4）、
w-beta L2→L4（获准 S4/S5）；honest FAIL 6 条全部豁免、S5 档失败率 0.000、
S-N 轨留痕；负例重放指纹相同、最大份额位移 0.0、新增行 0。

## 7. 局限与未做（诚实披露）

1. **未接入派发主循环本体**：`ops/taskplane/*` 与 `campaign_dispatcher` 的运行时代码
   属平台冻结面（2026-11-02 owner 令：平台冻结期不改运行时代码），本单只提供
   `tools/guidance/plan_cycle.py` 这一**可被派发侧调用的入口**与库内实体，未修改
   dispatcher 的调度顺序；真正把派单打到 worker 上仍需在解冻后接线。
2. **实证用合成评价**：`organization_id=0`、`EV2-` 前缀的结论与周期行是为了可复现地
   演示回路，不是线上真实评价的回放；接入真实数据只需把 `guidance_outcome` 由
   `credit_ledger`/`outcome_evaluations` 的 ETL 填充（`plan_cycle.load_ledger` 已就位）。
3. **阈值未经标定**：L1..L5 的 `succ/rate` 阈值与 `GAIN/DRIFT_CAP/FLOOR_RATIO` 是
   工程先验，尚无历史数据回归标定；策略回放（off-policy 评估）是后续工作。
4. **无 API 面**：本单只做 service + CLI + 库表，未加 FastAPI 路由（避免平台冻结期
   扩张 API 契约）；接入时建议挂在 `backend/api/guidance_loop.py` 并复用同一 store。
5. **幂等键是 `cycle_id`**：缺省由统计指纹派生（同一份统计 → 同一周期 → 零新增行）；
   显式传入 `--cycle-id` 时由调用方负责唯一性，重复传入会多写一组快照行
   （同指纹、同份额，只是新周期名）。生产侧建议按「测量快照」而非按时间窗命名。
6. **审查预算账本与文件版账本并行**：`backend/services/review_budget_ledger.py` 是
   文件 append-only 实现（周聚合在用），本单新增的是**库内**账本；两者字段口径一致，
   但尚未做单向合并（避免破坏在用周报）。