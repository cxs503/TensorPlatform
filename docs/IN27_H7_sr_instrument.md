# IN27-H7 · SR 继承就绪仪表（SR1–SR4）

> 模块：`tools/autonomy/sr_readiness.py`（度量核）+ `tools/autonomy/sr_dashboard.py`（CLI/渲染）
> 测试：`tests/test_sr_readiness.py`（34 例，库无关，`FakeRunner` 驱动）
> 产物：`artifacts/sr/sr_baseline.json` · `docs/reports/IN27_H7_sr_inheritance_readiness.md` · `artifacts/sr/sr_baseline.png`
> chronicle 落点：`chronicle_snapshots.payload->'s8_harness'->'sr_inheritance'`（时钟槽 **s8 = harness**）

## 1. 四个指标怎么算（每个数字一条 SQL，见 artifact 的 `sql` 数组）

| 指标 | 定义 | 数据源 | 目标 |
|---|---|---|---|
| SR1 影子分歧率 | 影子协调官 `would_do[]` 提议的处置动作，在 look-ahead 窗口内**没有被平台实际执行**的占比 | `coordinator_shadow.verdict->'would_do'` × 处置流（任务 goal / `campaign_events` 派单与升级 / `alerts` / 手打决策备注） | < 20% |
| SR2 协调官自主天数 | 距上一次**手打**（非守护通道）协调者决策的天数续航 | `tensor_agent_session_events`（`kind='approval_decided'` 且备注不以 `[auto-approve]` 开头且非空） | ≥ 7 d |
| SR3 红线决策包完整正确率 | 呈 owner 的决策包抽检：结论头 / 验证叙述 / 交付物指针 / 量化数字 / 可追溯 / 结论-终态一致，六项全过才算合格 | `tensor_approval_requests.evidence` + `mission.status` + `decision_note` | = 100%（抽检） |
| SR4 owner 非红线干预/周 | 周窗内**手打**且不属于红线（`stage_promotion`/`stop_condition`/`write_off`/`suspend`/…）的决策，折算成每周次数 | 同 SR2 的通道，按 kind 分流 | = 0 |

要点：

1. **人机分流靠显式标记，不靠散文。** 无人值守放行器在自己拍的决策备注上盖 `[auto-approve]`；
   没有该标记且备注非空的决策 = 手打通道（owner 或协调者）。这个口径与 `tools/autonomy/metrics.py`
   的 M3「放门」机制判据同源。
2. **未到期绝不算分歧。** SR1 的单条提议在 look-ahead 未走完且尚无处置证据时记 `pending`，
   不进分子也不进分母；只有窗口到期（或早期出现处置证据）才落 `matched`/`diverged`。
   因此仪表不会因为「刚主张完还没来得及执行」而虚报分歧。
3. **多视界读数。** SR1 同时发布 0.5 h / 1 h / 6 h / 24 h 四个 look-ahead 的分歧率、判定数与 `settled` 标志；
   **首个已有判定条目的视界**即首月基线读数（`baseline_horizon_hours` / `baseline_value`），
   该读数的样本量就是同行 `decided` 列——不能把 `pending` 当分歧，也不能因为影子官按 30 min 一拍
   不断产生新条目就永远等「全部到期」（那样基线永远不落定）。0.5 h = 一拍，是最早能出数的运营视界；
   24 h 是主读数的宽限视界。
4. **抽检可复算。** SR3 的样本按 `md5("IN27-H7:" + pack_id)` 定序取前 N（默认 200），
   与行序、时区、进程无关；`audited_ids` 落在 artifact 里，换机器能取到同一批包。
5. **证据分级。** 查询失败或窗口内无成熟样本 → `WITHHELD`（值为 `null`），绝不填 0 冒充测量。

## 2. 运行

```sh
# 单次：算四指标 + 落 artifact/markdown/图 + 追加 chronicle s8 快照
TP_DSN="dbname=tensorplatform user=postgres host=/var/run/postgresql" \
python3 -m tools.autonomy.sr_dashboard snapshot \
  --window-days 30 --sample-size 200 \
  --out artifacts/sr/sr_baseline.json \
  --markdown docs/reports/IN27_H7_sr_inheritance_readiness.md \
  --figure artifacts/sr/sr_baseline.png --chronicle

# 连续产出（仪表不能只跑一次）：默认 1 h 一拍
ops/sr_instrument_loop.sh

# 读回趋势 / 检验连续产出
python3 -m tools.autonomy.sr_dashboard trend --limit 30
python3 -m tools.autonomy.sr_dashboard continuity --days 14
```

`continuity` 返回近 N 天「有 SR 快照的天数 / 缺口天 / 当前连续产出天数」，是「仪表连续产出」这条
验收判据的机器证据面。

## 3. 已知边界（诚实清单）

* **SR1 的样本量在仪表出生时极小。** 影子协调官（`coordinator_shadow`）2026-11-07 09:55 才落第一拍，
  首日只有 1 回合 / 4 条提议。仪表按小时连续产出，样本随影子官逐拍增长；`rounds`/`items`/`counts`
  三个字段随每次快照落在 artifact 与 chronicle 里，任何人可查「这个数是多少条提议算出来的」。
* **处置匹配是词面匹配。** `would_do[].target` 是器官键（如 `s5_outcomes`、`queued_stuck`），
  处置证据用大小写无关子串匹配到任务 goal / 事件 detail / 告警正文 / 手打备注。若平台对器官的处置
  在文本里完全不提器官名，会被判成「未处置」。这是保守方向（宁可多报分歧），但确实会高估分歧率。
* **SR2/SR4 用通道标记而非账号身份。** 平台在册账号（`eco_admin`/`eco_web`/…）都是服务账号，
  按 `started_by`/`decided_by` 分不出「人」；因此用「备注是否带守护标记」当人手痕迹。
  一旦有真实人类账号接入，可在 `measure_sr4` 里加 `decided_by IN (...)` 收窄。
* **SR3 的六项判据是「可读、可核、可追」的必要条件，不是充分条件。** 它证明决策包里有结论、有验证叙述、
  有交付物指针、有量化数字、有任务与备注、结论与终态一致；它**不**判断数字本身对不对（那要重跑实验）。
* **SR3 会误判 `checks` 为空的旧包。** 平台早期 `evidence.checks` 是 `{}`（对象），
  内容全在 `summary.checks` 文本里；审核器优先读 `summary.checks`，两者都空才判不合格。