# 历史「复活」口径说明（REOPENED 正式化配套 / 战役 IN19-Q7）

任务书要求「历史复活 19 例口径作说明文档」。本文做两件事：**把口径定义写死**，
并给出**可复算**的实测数字。结论先说：**任何单一口径都复现不出 19**——因此按
「口径必须先定义、数字必须可复跑」的纪律，本文把四种候选口径各自算清并全部入档，
而不是把 19 反推成某个统计量。

复算命令：

```bash
python3 tools/audit/reopen_census.py            # 写 artifacts/m5022/reopen_census.json（只读 SELECT）
python3 tools/audit/reopen_census.py --no-db    # 只验契约不变量（离线可跑）
```

普查时间：2026-11-07（mission 总数 4670）。JSON 里每条明细都带其 SQL，可逐条复跑。

## 1. 为什么要分口径

「复活」在口语里至少有四种互相不覆盖的含义，混在一起统计必然失真：

| 口径 | 定义（机器可判） | 语义 |
|---|---|---|
| **T1** `engine_rework_after_finish` | `finished_at` 存在且 `heartbeat_at > finished_at + 60s` | 单已落终态，之后又被 worker 真干活（心跳只能由 worker 写）——库外守护/人工把单拉回来重做的最硬 footprint |
| **T2** `post_merge_rework` | `result.pr_gate.history` 先出现 `merged`、之后又出现 `open` | 合并后回归被发现、需要重开的典型形态（本状态要解决的主场景） |
| **T3** `terminal_to_active` | `finished_at` 非空而当前状态**非终态** | 只有「重开」能造成这个组合（返工入队后 `finished_at` 未清） |
| **T4** `guard_pending` | `tp_pusher` 状态文件里仍带 `main_red_hold` 私有旗标的单 | 未走任何正式状态的库外挂起存量（= 本次切换的迁移待办） |

## 2. 实测（2026-11-07，只读普查）

| 口径 | 例数 | 构成 |
|---|---|---|
| T1 | **33** | `failed` 30、`succeeded` 2、`stopped` 1 |
| T2 | **7** | mission 4929/4933/4950/4951/4953/4955/4956 |
| T3 | **13** | 全部 `awaiting_pr_merge`（finish 后被 pr-gate 拉回在飞） |
| T4 | **117** | pusher 私有旗标存量（逐单可解析 `acceptance_ref`，见 `reopen_state_migration.json`） |
| T1∪T2∪T3 | **53** | T1∩T3 = 0，T1∩T2 = 0（互不覆盖，故不能相加当「总数」） |
| 战役引用 | 19 | — |

`cited_19_reproduced = false`（四口径均非 19；两两/三三并集亦非 19：33 / 46 / 53 / 7 / 13 / 117）。

## 3. 判读（诚实结论）

1. **T1 是语义上最贴「库外复活」的口径**（终态落定后仍被真干活），今天实测 33；
   它随时间单调增长（普查时刻仍在涨），因此 19 极可能是**播种时点的 T1 快照**，
   而不是另一个口径。这一点无法从库内证据证实（平台没有逐次状态迁移的
   append-only 流水，见下条局限），故按诚实纪律记 **WITHHELD**，不做反推。
2. **T2 是本次状态正式化最该覆盖的场景，但只有 7 例**——数量小并不能说明问题
   不重要：7 例每一例都是「已合并的成果在合并后才出问题」，正是 SPEC §4.2 的靶心。
3. **T4 = 117** 说明真正的「库外复活」主力不是偶发人工，而是守护逻辑本身
   （继承红挂起）；这也是本单把 `tp_pusher` 切正式状态的直接动因。切换后这类挂起
   会以 `reopened` 落库并带审计行，下一轮普查 T4 应归零、T3 的构成会开始出现
   `reopened`。
4. **口径落地即为验收的一部分**：`tools/audit/reopen_census.py` 已入仓，任何人可在
   任意时点用同一命令复算四口径；数字与口径同时进 `artifacts/m5022/reopen_census.json`。

## 4. 局限（为什么 19 无法被证实）

* 平台**没有**逐次状态迁移的 append-only 流水：`audit_logs` 无 mission 条目
  （全表按 `entity_type ILIKE '%mission%'` 计 0 行），`mission_lifecycle_log` 只记
  suspend/resume/write_off/pr_gate/custody_requeue/reopen 六类动作，**不记每次
  status 写入**。因此「历史上某单从终态被改回活跃态」只能靠心跳/时间戳等侧面
  footprint 推断，无法逐事件回放。
* 本次正式化正好补上这个缺口：**从今往后每一次重开都有 DB 约束 + 审计行 +
  事件流三份证据**，下一轮普查的 T-reopen 类口径将是精确计数而非推断。
* 建议（未在本单实施，避免越界改派发面）：把 mission status 的每次写入也纳入
  `mission_lifecycle_log`（或等价 append-only 流水），则 T1/T3 可由事件回放精确
  判定，口径争议消失。