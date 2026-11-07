# tools/worker_portrait —— worker 画像最小面（IN15-O4 · 自回归燃料④：识产能）

**只读分析工具**：按 worker 聚合「领取速度 / 成功率 / 仓分布 / 7200 s 内完成率」，
产出可直接消费的画像 md + JSON。**不做任何调度策略改动**（策略=后续波），
不改平台表结构、不动冻结 API 面、不建新表。

## 组件

| 文件 | 作用 |
|---|---|
| `portrait.py` | 只读导出（单条 SELECT）+ 纯函数聚合 + md 渲染 + 逐条可溯校验 + CLI（含 `--dry-run`） |
| `crosscheck.py` | 独立 SQL 复算同一批量，逐项给相对误差（误差判定基准），含 `--dry-run` |
| `figures.py` | 读 `report.json` 出 4 张判据图，含 `--dry-run` |
| `report.py` | 论文级报告（六要素）生成，数字程序化注入 + 平台发布负载（图压缩到 48 KB/张，避 250 KB 门） |

## 用法

```bash
# 1) 画像（只读 psql；--verify 逐单比对 mission 表；--dry-run 不落盘）
python3 -m tools.worker_portrait --source psql --verify --out-dir artifacts/worker_portraits
python3 -m tools.worker_portrait --dry-run

# 2) 离线快照（无库环境/回归测试）
python3 -m tools.worker_portrait --source json --snapshot artifacts/worker_portraits/report.json --dry-run

# 3) 误差判定（独立 SQL 复算；失败退出码 1）
python3 -m tools.worker_portrait.crosscheck

# 4) 出图 / 报告
python3 -m tools.worker_portrait.figures
python3 -m tools.worker_portrait.report [--dry-run]

# 5) 判据
python3 -m pytest tests/test_worker_portrait.py -q
```

## 口径（definition_version `wp-v1`，与产物 JSON 同源）

* `pickup_s` = `claim_at − created_at`；`claim_at` 取
  `tensor_agent_session_events` 中 `payload->>'subtype'='mission_claimed'` 的**最早**时间戳，
  按 `session_id` 关联（mission 表无 `claimed_at` 列）→ 覆盖度见产物 `coverage.missions_with_claim_event`。
* `wall_s` = `finished_at − claim_at`（缺 claim 时退化为 `finished_at − created_at`）。
* `success_rate` = delivered/(delivered+failed)；
  delivered = {succeeded, awaiting_pr_merge}、failed = {failed, revoked, stopped}；
  running/queued/suspended 在飞态不进分母（单独计数）。
* `completion_7200` = `#(wall_s ≤ 7200)/#(已完结)`，7200 s = `budget.max_wall_seconds` 默认值。
* `repo_distribution` = 该 worker 工单按 `repo` 计数/占比（按原值统计，不归并别名）。

## Fail-closed 语义

空 worker、空归属（`claimed_by` 为空）、活跃 worker 数 < `--min-active-workers`（默认 3）、
空快照 —— 一律拒收（异常 / 退出码 2），**宁报 FAIL 不出空画像**。

## 产物

```
artifacts/worker_portraits/
├── index.md                  # 索引 + 覆盖度 + 口径 + 只读 SQL 附录
├── report.json               # 机器可读画像集合（含 per_mission 逐单明细 + SQL sha256）
├── independent_recompute.json# 独立复算 18 项，逐项相对误差
├── <worker>.md               # 每个活跃 worker 一份画像
└── figs/*.png                # 4 张判据图
```