# 计算调度策略 + 计量（FEAT-COMPUTE-POLICY-METER, m17372）

owner 2026-11-13 计算调度令的落地件：「agent 计算提交给调度系统，跑在哪里是调度系统的事」。
本目录交付 **放置策略（可配）+ 按单计量账本 + 看板 + 机器门 + 对照基线**，零域语义
（仓名/算例/物理词条都不进代码，策略数据全部外置到 `config/compute_policy.v1.json`）。

## 1. 组件与边界

| 文件 | 职责 |
|---|---|
| `config/compute_policy.v1.json` | 策略数据：优先级类（返修>主线>自驱）各自的本机 CPU 秒预算与重计算阈值；节点权重/并发帽/负载帽；集群不可用时的行为；本机占比目标 0.20 |
| `tools/compute_meter/policy.py` | **纯函数决策**：`decide()` → `local / cluster / queue` + 节点选择 + 违例；`order_queue()` 出优先级派发序 |
| `tools/compute_meter/meter.py` | 账本：`cpu_seconds × placement × node`；SQL（`compute_ledger`）与 JSONL 双实现，`source_ref` 幂等；`merge_records` 保证回填不抹治理字段；`audit_running` 对孤儿 running 行对账；`estimate_cpu_seconds` 用历史 p90 估本单消耗 |
| `tools/compute_meter/sampler.py` | 控制节点实测：`/proc/<pid>/stat` 进程 CPU、`/proc/stat` 整机 busy，全部可注入假 `/proc` |
| `tools/compute_meter/dashboard.py` | 看板数据 + markdown 块 + 机器门 `evaluate(scope=share|hygiene|all)` |
| `tools/compute_meter/panorama.py` | PANORAMA 标记块幂等注入（半截块拒绝写入） |
| `tools/compute_meter/cli.py` | `compute-meter`：`decide / submit / run-local / ingest / report / gate / inject-panorama / order-queue` |
| `tools/compute_meter/ab_experiment.py` | 预注册对照实验：A=本机强制 vs B=策略路由 |
| `tools/compute_meter/workload.py` | 可校验 CPU 负载（`time.process_time` 实测 + 确定性 checksum） |
| `tools/compute_meter/schema.sql` | `compute_ledger` 表 + `compute_ledger_mission_v`（每单）/`compute_ledger_placement_v`（放置分布）视图 |
| `tools/compute_meter/repro.sh` | 一键复现（单测→回填→看板→门→实验→图→注入 dry-run） |

放置判定序（`policy.decide`，纯函数、可测）：

1. 估算 `est_cpu_seconds` ≥ 该优先级类的 `heavy_threshold_cpu_seconds` → 集群；
2. 否则控制节点 `load/cores ≥ 0.75` → 集群；
3. 否则该优先级类滚动 1h 本机预算 `剩余 < est` → 集群；
4. 否则本机。
5. 集群无容量：`on_unavailable=queue`（默认，HPC 语义：排队等，绝不偷偷回本机）；
   节点选择按 `loadr / weight` 最低，且须满足 `cores 余量`、`running_jobs < max_jobs`、`loadr < load_cap`。
6. `forced_placement`（实验/返修用）与策略建议不一致时**必记违例**；强制本机跑重计算 = blocking。

## 2. 验收对照

| 验收项 | 交付 | 证据 |
|---|---|---|
| ① per-mission 计量：cpu_seconds×placement 入库，可查每单烧了多少、烧在哪 | `compute_ledger`（唯一键 `source_ref`）+ `compute_ledger_mission_v` + `per_mission()` | 实跑回填 347 行 / **576.17 CPU·h**（n330 278.29h、n315 199.46h、shards 98.42h）：`artifacts/compute_meter/report_metrics.json`；mission 17372 单查见 `per_mission[1]` |
| ② 放置策略配置文件（节点权重/本机帽/优先级=返修>主线>自驱） | `config/compute_policy.v1.json` + `load_policy()` 强校验（乱序/重复节点/零权重即报错） | 6 个校验单测 + `test_policy_rejects_*` |
| ③ 看板：队列深度/放置分布/等待分位/违例数，PANORAMA 注入 | `dashboard.metrics` + `render_markdown` + `panorama.inject` | 实跑：队列深度 **565**、等待 p50 **22.0h** / p90 **54.6h** / p99 **57.4h** / max **74.1h**（n=350，最老在队 46.2h）、排空估算 7.3h、违例 `ungoverned_compute`×346(warn) + `stale_running_rows`×15(blocking)；块已注入 `/root/tp-agent/PANORAMA.md`（注入前备份 `PANORAMA.md.bak_computemeter_*`），二次注入字节数不变（235069→235069，只有时间戳行刷新） |
| ④ 对照基线：修前本机占比 vs 修后（目标 <20%） | `ab_experiment.py`（预注册）+ `gate --scope share` | A 臂（本机强制，n=3）**30.023 CPU·s/次**；B 臂（策略路由，n=3）**0.0 CPU·s/次**；`ratio B/A = 0.000` ≤ 0.20 → pass；真实递交作业 `cj1520`（mid=17372）落库为 governed 行 |

机器门（可红队失败，预注册）：

- `gate --scope share`：本机占比 ≤ 目标 且 无 blocking 的 share 类违例 → 0；超了 → 3；证据不足（总 CPU < 门槛或无 governed 行）→ 4。
- `gate --scope hygiene`：孤儿 running 行 / 队头阻塞 / 野生计算。
- `gate`（默认 all）= share ∪ hygiene。
- 红队面（必须能失败）：`artifacts/compute_meter/redteam_local_heavy.jsonl`（6 条 600s 本机行）
  → `verdict=fail, exit=3`（本机占比 100%、`heavy_compute_on_control_node`×6）；
  同数据只把 placement 改成 cluster 的对照账本 → `pass`（证明门判的是放置而非体量）。

## 3. 实测发现（本单顺带认定的真缺陷，均带证据）

1. **孤儿 running 行封死准入**：`cluster_jobs` 里 20 条 11-09 起的 shard 行状态仍是 `running`，
   叠加 `MAX_PER_NODE=4` 的并发帽 → 双节点 4/4 占满、**565 单排队零期派发**，
   而节点实际 load 只有 0.32/0.44（180 核闲着）。本包 `audit_running()` 把这些行认成
   `stale_running_rows`（超 `timeout×1.5`）并从并发帽里扣掉；口径见 `policy.stale_after_factor`。
2. **并发帽按“作业数”而非“核数”**：128 核节点被 4 条僵尸行按 4 并发算满 → 每节点 `max_jobs`
   从 4 调到 12（`cores` 才是容量口径）。配置改动有单测钉住。
3. **墙钟虚报**：孤儿 running 行若按 `now-started_at` 记账，`cj26` 一条会虚报 1.19e6 CPU·s。
   账本改为按 `timeout×1.1`（未终态）/`timeout+900s`（终态）封顶，`decision_reason` 留
   `wall_capped_by_timeout` 标记（实测 92.16 CPU·h 被封顶，总计 571.83h 口径可审计）。
4. **shard 型作业绕过容量准入**（`cluster_queue.dispatch` 对 shard 不做 `pick_node`）：
   本包未改工作树外代码，仅在文档记录 + 由策略侧对 shard 作业按 `shard_node_share×节点数` 记账核数。
5. **派发是纯 FIFO 且批量固定 8 条**：队头 8 条派不动时后面全饿着。`policy.order_queue()`
   给出「优先级 rank 降序 + 同级 FIFO」的替代派发序，cluster_queue 侧接上即可消队头阻塞（未合入）。

## 4. 用法

```bash
# 递交（agent 唯一入口：只说命令和资源，不说跑哪）
python3 -m tools.compute_meter.cli submit --mission 17372 --repo TensorFEM \
    --cmd "python3 -m pytest tests -q" --cores 8 --priority mainline --est-cpu 1200

# 只看决策（dry-run，不递交）
python3 -m tools.compute_meter.cli submit ... --dry-run

# 回填集群真账 + 出自己的账
python3 -m tools.compute_meter.cli ingest --days 7
python3 -m tools.compute_meter.cli report --days 7 --json out.json --markdown block.md
python3 -m tools.compute_meter.cli gate --days 7 --scope share     # 0/3/4
python3 -m tools.compute_meter.cli inject-panorama --dry-run
sh tools/compute_meter/repro.sh                                    # 全链路一键复现
```

## 5. 局限（诚实申报）

- **「一周」基线尚不完整**：集群侧账本可回溯到 11-09（4.2 天，576.17 CPU·h）；
  本机侧没有历史账，只有本单引入的实测通道。所以「修前本机占比」用 A 臂（强制本机）
  以同样方法实测（100%），「修后」用 B 臂 + 真实集群账本；**完整 7 天对照需账本自然累积**
  （`gate --days 7` 已就绪，届时同一条命令出数）。
- **集群侧 CPU 秒是「租赁核·秒」**（核数×墙钟，含排队外的时间），未做节点侧核占用率采样；
  封顶规则见上，异常值有标记。
- **本机占比 0.0% 是当前窗口的事实**（近 7 天没有本机重计算入账），不是长期保证：
  一旦有人绕过 `submit` 直接本机跑，账本会出现 `ungoverned_compute` 违例（warn → 可按需升 blocking）。
- B 臂只做 1 次真实递交（`cj1520`），因集群 565 单积压未能在实验窗口内跑完 → B 臂的
  「集群侧实际 CPU」需 `ingest` 后续补齐；B 臂当前度量的是**99 侧 CPU 消耗**（这正是本单要压的量）。
- PANORAMA 写入目标在工作树外（`/root/tp-agent/PANORAMA.md`，非 git 文件），已先备份再注入；
  回滚 = 恢复备份文件。