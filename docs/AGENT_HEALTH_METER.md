# 席位健康度仪表（agent_health_meter）—— 定义、首跑快照、红队与部署

锚点：**FEAT-AGENT-HARNESS**（owner 2026-11-12 定谳：agent 能力偏弱主因之一 = 个体记忆层空壳 +
健康度无保障）。本件**只计量不裁决**：落数字、打标记、留证据；处置（门诊/换班/重派）归协调线。

| 交付 | 落点 |
|---|---|
| 台账表 | `backend/migrations/123_agent_health_meter.sql` → `tensor_agent_health` |
| 周报面（SQL 视图） | 同迁移 → `v_agent_health_seat_latest` / `v_agent_health_weekly` |
| 守护 | `tools/agent/agent_health_meter.py`（日轮询 / flock 幂等 / 日志 / chronicle 全动作） |
| 验收测试（含红队） | `tests/test_agent_health_meter.py`（13 passed） |
| 首跑证据 | `runs/agent_health_m15950/`（`snapshot_summary.json` / `seat_examples.json` / `fleet_rollup.txt` / `agent_health_weekly_2026-11-12.md` / `meter.log` / `redteam_pytest.log`） |

---

## §1 计量对象与口径来源（先认证列，再算数）

开工三查结论（产库真列，非推测）：

| 事实 | 证据 |
|---|---|
| 席位标识列 = `tensor_dev_missions.claimed_by` | 7d 窗口 11,266 单中 `agent_session_id` **全为 NULL**，`claimed_by` 有值 10,829 单；累计 14,865 单核对同结论 |
| `claimed_by` ↔ `factory_resident_workers.worker_id` 同名可 join | 93 个 resident worker，与 `claimed_by` 取值同名 |
| `tensor_dev_missions.fail_stage` **列全空** | `select count(fail_stage)` = 0 / 14,865 |
| 真实拦路阶段在 `result->>'fail_stage'` | 窗口内 478 行有值：`budget_exhausted+forced_finish` / `no_progress+forced_finish` / `wall_exhausted+forced_finish` |
| **强制终结后仍可能 `succeeded`** | 478 条有 `fail_stage` 的行里 **460 条最终 status='succeeded'**；另有 3,426 条 failed **无** `fail_stage` |
| 记忆源 `factory_memory_events` **零记录** | 全表 0 行；`factory_resident_workers.memory` 存量上限 12 条且无时间戳 |

→ 由此确定三条口径（写进代码注释与 `source_sql`）：

1. **席位**：`btrim(claimed_by)`；空值进 `unattributed` 桶（窗口内 420 单）。该桶**只计量、不判定**——
   否则 420 条无主失败会把三类标记全部打爆成假阳性。
2. **问题单** `failures_total` = `status <> 'succeeded'` **或** 有 `fail_stage`。若按「最终状态」取交集，
   96% 的强制终结事件会被漏掉（460/478），RECUR_FAIL 直接打哑。
3. **强制终结** `forced_finish_count` = 有 `fail_stage` **或** `result ? 'reaped_by'` **或**
   `result ? 'stop_condition'` **或** `status in ('revoked','stopped')`。

窗口 = 快照日 D 的 `[D+1d-7d, D+1d)`（左闭右开，UTC）；`fail_stage_dist` 每单只进一个桶
（有记录进该阶段，无记录的失败单进 `(unspecified)`）。

## §2 健康分数学定义（可复算）

四个 [0,1] 分量，缺失分量**整体剔除并重归一化**（不补默认值——「看不见」≠「0 分」）：

```
S = 成功率           = missions_succeeded / missions_total            w=0.50
C = 可控率           = 1 - forced_finish_count / missions_total       w=0.20
A = 失败可归因率     = stage_attributed / failures_total              w=0.10   （无问题单记 1.0）
M = 记忆活力         = min(1, (lessons_injected + memory_hits)/total) w=0.20   （源不可用→缺省）

raw   = 100 × Σ(wᵢ·cᵢ) / Σ(wᵢ | cᵢ ≠ None)          （分母 = weights_renormalized）
score = raw × Π penaltyₖ,   penalty: RECUR_FAIL 0.65 / LOW_SUCCESS 0.60 / MEMORY_STARVED 0.75
```

- **乘性罚分而非硬上限**：同一病组内保留排序信息（协调线要知道「52 个 RECUR_FAIL 里谁最差」），
  硬 cap 会把它们拉平成并列。
- **无任务（total=0）→ score = NULL**（无定义），不按 0 处理：94 席里有 4 席窗口内无单（score=NULL；均分/中位数按 90 席算）。
- 落库 `score_components` 存 `components` / `weights_used` / `weights_renormalized` /
  `penalties_applied`，测试 `test_score_components_recomputable` 与
  `test_redteam_...` 均把落库分量**重算一遍**并断言等于落库分（复算校验，非口头声明）。

## §3 三类病席位标记（阈值先于动手既定）

| 标记 | 判据 | 罚分 |
|---|---|---|
| `RECUR_FAIL` | 同一**已识别** `fail_stage` ≥3 次 / 7d（`(unspecified)` 不计——无法判定「同阶段」，记入数据质量缺口） | ×0.65 |
| `LOW_SUCCESS` | 单数 ≥5 且成功率 < 地板 0.30（产库席位中位数 0.64，地板约为其一半以下） | ×0.60 |
| `MEMORY_STARVED` | 单数 ≥10 且 `memory_hits = 0`（**仅记忆源可用时可判**；源空 → `withheld`，不打标） | ×0.75 |

`markers_meta` 逐步记「为什么没打标」（`withheld: insufficient_missions` /
`stage_unspecified` / `memory_source_unavailable`），避免「看不见」被读成「健康」。

## §4 首跑真实快照（2026-11-12，SQL 原文可复算）

执行：`python3 tools/agent/agent_health_meter.py --once --report-dir runs/agent_health_m15950`
→ 95 行（94 worker + 1 unattributed），`rows_written=95`，`source_sql` 列内嵌当次聚合 SQL 原文 + 参数 + 窗口边界。

| 指标 | 值 |
|---|---|
| 席位（worker） | 94 |
| 有病标记席位 | **61 / 94** |
| `RECUR_FAIL` | 52 |
| `LOW_SUCCESS` | 11 |
| `MEMORY_STARVED` | 0 触发 / **94 withheld**（记忆源全表 0 行） |
| 无标记席位 | 33 |
| 均分 / 中位数 / 最低 | 47.42 / 45.00 / 8.44 |
| 窗口内任务 | 10,197 单，成功 6,570（64.4%） |

三个真实席位得分示例（`runs/agent_health_m15950/seat_examples.json` 为整行 jsonb，含分量）：

| 席位 | 单数 | 成功率 | 强制终结率 | 重复阶段（次） | 记忆存量 | 分 | 标记 |
|---|---|---|---|---|---|---|---|
| `tp-ds31-1` | 88 | 0.1023 | 0.0682 | — | 12 | 18.00 | LOW_SUCCESS |
| `tp-gen-1` | 295 | 0.6373 | 0.1356 | no_progress+forced_finish (17) | 12 | 41.44 | RECUR_FAIL |
| `tp-doc-1` | 247 | 0.7449 | 0.0567 | budget_exhausted+forced_finish (4) | 12 | 46.19 | RECUR_FAIL |
| `tp-ds31-7` | 8 | 0.1250 | 0.7500 | — | 1 | 8.44 | LOW_SUCCESS |

**读法（诚实版）**：`tp-gen-1` 成功率 64% 却仍被打 RECUR_FAIL——因为它 7d 内同一阶段
（no_progress+forced_finish）强制终结 17 次；分项里 S=0.637 看得见，标记说明「学不到东西」。
`tp-ds31-1` 88 单只有 9 单成功（10.2%），是真正的产能黑洞。这两类病需要不同门诊处方。

## §5 红队证明（标记必须能亮，也必须能灭）

靶场：独立 PG schema `rt_health`（与生产零接触），源表结构 = 产库真列的精简版，
**DDL 直接用生产迁移文件本身**（不复制，杜绝靶场口径漂移）。
四类假席位 + 一个无主席位，全部断言（`tests/test_agent_health_meter.py`，13 passed）：

| 假席位 | 构造 | 期望 | 实测 |
|---|---|---|---|
| `rt-recur` | 6 单，4 条同阶段 `no_progress+forced_finish` | 只 RECUR_FAIL | ✅ `['RECUR_FAIL']`, recur_stage/no_progress, count=4 |
| `rt-low` | 6 单 1 成，失败阶段各不相同 | 只 LOW_SUCCESS | ✅ `['LOW_SUCCESS']`（sr=0.1667） |
| `rt-starved` | 12 单 10 成，记忆源可用但该席位 0 命中 | 只 MEMORY_STARVED | ✅ `['MEMORY_STARVED']` |
| `rt-healthy` | 12 单 10 成 + 1 条记忆命中 | 阴性对照：无标记 | ✅ `[]`, memory_hits=1 |
| `unattributed` | 1 条无 `claimed_by` 的失败单 | 不判定 | ✅ `markers=[]`, `suppressed=unattributed_bucket` |

**反向（红队可失败）**：删掉 3 条同阶段强制终结（余 1）、把 rt-low 的失败改成成功、删掉
rt-starved 全部任务、清空记忆源 → 重跑同日快照：三个标记全部归 0；`rt-starved` 整行被
「先清后写」剪除（陈旧标记不残留）；`rt-healthy` 的 `MEMORY_STARVED` 由触发态转
`withheld: memory_source_unavailable`，`memory_attribution.canonical_status` 由 `ok` 转 `table_empty`
——即「源没了」不会被静默当成「命中为 0」，也不会被静默当成「通过」。

另外三条护栏测试：同日重跑**行数不变、值不变**（幂等）、改数据后**覆盖更新不新增**、`--dry-run` 零写入、
`source_schema` 注入串（`public; DROP TABLE x`）被标识符白名单拒、`flock` 第二实例立即 `LockBusy` 让位。

## §6 记忆层归因缺口（如实登记，不粉饰）

| 列 | 首跑值 | 归因状态 | 缺口原因 |
|---|---|---|---|
| `lessons_written` | 0 | `table_empty` | 规范源 `factory_memory_events` **全表 0 行**（建表至今无写入） |
| `lessons_injected` | 0 | `table_empty` | 同上；harness 有 `build_memory_block()` 注入动作，但**没有写回任何计数** |
| `memory_hits` | 0 | `table_empty` | 全生态无「命中」埋点（`payload/provenance` 里无 hit 字段） |

可得的真数（已单独入列，不冒充滚动量）：`memory_lessons_stock` = `worker.memory` jsonb 数组长度
（**上限 12，无时间戳**，故是存量不是 7d 滚动）；`memory_key_missions` = 7d 内 goal 声明
`memory_key=` 的任务数（首跑 **0**，即没有任何任务走常驻记忆路由）。

→ 「记忆层空壳」因此在数据上被量化：93 席 × 存量 ≤12 条、规范记忆台账 0 条、7d 记忆路由 0 次、
命中埋点 0 个。**MEMORY_STARVED 在产库只能 withheld**，红队证明它在源可用时会亮——这是刻意的
诚实取舍：宁可说「看不见」，不给 94 个假阳性。

补埋点建议（不在本单改生产 harness）：在 `tools/agent/tensor_agent.py` 的 `build_memory_block()` 出口
与 `append_memory()` 处各写一条 `factory_memory_events`（`event_type='lesson'`、
`payload={'phase':'inject'|'hit'|'write'}`、`session_id=agent_session_id`），三列即可由 withheld 转实值。

## §7 周报面

- SQL 视图：`v_agent_health_weekly`（周聚合：标记人数 / 均分中位数 / 任务面 / 记忆面）、
  `v_agent_health_seat_latest`（每席最新，`marker_count>0` 即候选）。
- 落地：`--report-dir` 每次日拍自动写 `agent_health_weekly_<D>.md`；已生成
  `runs/agent_health_m15950/agent_health_weekly_2026-11-12.md`。
- 进 `tensor_reports`：周报 markdown 可整篇作为报告正文 POST（本单的报告已按此口径发布）。

## §8 部署三步（部署由协调线执行；**本单不重启任何生产守护**）

```bash
# 第 1 步：迁移（幂等：CREATE TABLE/VIEW 均 IF NOT EXISTS / CREATE OR REPLACE）
psql -d tensorplatform -f backend/migrations/123_agent_health_meter.sql

# 第 2 步：手工验一拍（只读 dry-run 先看数，再落库）
python3 tools/agent/agent_health_meter.py --once --dry-run --dsn "dbname=tensorplatform user=foundation host=/var/run/postgresql"
python3 tools/agent/agent_health_meter.py --once --report-dir /var/lib/tp-agent-health/reports

# 第 3 步：装 systemd 日轮询（槽对齐 86400s；flock 保证单实例；SIGTERM 跑完本拍再退）
cat >/etc/systemd/system/tp-agent-health-meter.service <<'UNIT'
[Unit]
Description=TensorPlatform seat health meter (FEAT-AGENT-HARNESS, measure-only)
After=postgresql.service

[Service]
Type=simple
Environment=TP_DSN=dbname=tensorplatform user=foundation host=/var/run/postgresql
ExecStart=/usr/bin/python3 /opt/tensorplatform/tools/agent/agent_health_meter.py \
    --daemon --interval 86400 --state-dir /var/lib/tp-agent-health \
    --report-dir /var/lib/tp-agent-health/reports --log /var/log/tp-fleet/agent_health_meter.log
Restart=on-failure
RestartSec=300
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now tp-agent-health-meter.service
journalctl -u tp-agent-health-meter -n 20   # 首拍应为 status=ok, rows_written=95
```

回滚：`systemctl disable --now tp-agent-health-meter` + `DROP VIEW v_agent_health_weekly,
v_agent_health_seat_latest; DROP TABLE tensor_agent_health;`（本表不被他表外键引用，可安全回收）。

## §9 已知局限（供对抗方攻击）

1. **记忆三列在产库全 0**：非估算、是空账；分数里 M 分量因此**整体剔除并重归一化**，
   94 席的分数是在 w_sum=0.80 上算的（`weights_renormalized=0.8`，可在 components 里核）。
2. **`RECUR_FAIL` 计的是事件不是终态**：460 条「强制终结但最终 succeeded」的单也计入重复，
   若口径改为终态，52 席会掉到个位数——这是口径选择，已公开，非隐藏。
3. **失败可归因率 A 全面偏低**（窗口内 3,426/3,904 失败单无 `fail_stage`）：A 分量压低的是
   *全体*，不能用于席位间比较强弱；它是数据质量温度计。
4. **窗口 7d 固定**（`--window-days` 可改但视图与阈值假定 7d）；席位数量少时中位数不稳。
5. **`unattributed` 桶不判定**：420 单（7d）无席位标识，等于 3.7% 的产能看不见责任人——
   本身是 harness 的记账缺口，建议派单机在 claim 时强制写 `claimed_by`。
6. 首跑为**单一快照日**：周报面目前只有一周格，趋势要等日轮询攒够两周才可判（不预填假历史）。