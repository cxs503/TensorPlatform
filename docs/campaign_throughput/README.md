# 战役波次吞吐报表（campaign_throughput）

自回归燃料③「看增长」的仪表：把**按 campaign plan 聚合**的 mission 吞吐量做成可复算产物。
工具：`tools/campaign_throughput.py`（只读，无 schema 变更）　·　测试：`tests/test_campaign_throughput.py`

## 产物

| 文件 | 内容 |
|---|---|
| `docs/campaign_throughput/THROUGHPUT_REPORT.md` | 首份真实报表：plan 11/12/13 主表 + 回流率 + 增长数据源 + Python↔SQL 对拍 |
| `docs/campaign_throughput/THROUGHPUT_BACKFILL_ALL_PLANS.md` | 全战役回填（p2–p18）：含失败样本的 7 个 plan，回流率有真实分母 |
| `artifacts/campaign_throughput/report_plans_11_13.json` | 机器可验报表（含 `definitions`、`crosscheck.rows`、`growth.points`） |
| `artifacts/campaign_throughput/report_all_plans.json` | 同上，覆盖全部可见 plan |
| `artifacts/campaign_throughput/fig1_growth_curve.png` | 累计终态单增长曲线（逐 plan） |
| `artifacts/campaign_throughput/fig2_latency_p50_p95.png` | 领取时延 vs 执行墙钟 P50/P95 |

## 口径（代码即口径，与报表 §7 同源）

- **plan 归属**：`task_key = ^p<plan_id>-<node_id>-a<attempt>$`（战役派发器写入口径，
  `plan_id` 即 `campaign_plans.id`）。无该前缀的 mission **不计入任何 plan**，不猜归属。
- **终态**：`status ∈ backend/contracts/mission_status_vocab.v1.json` 的 terminal 集合
  （G-A 单一契约源，本工具不复制词表；兜底常量与契约一致性由 pytest 断言）。
- **成功率** = `#succeeded / #terminal`（在飞单不进分母）。
- **领取时延**（worker claim latency）= 首个 `mission_claimed` 会话事件 − `created_at`；
  **墙钟** = `finished_at − claimed`，缺证据时回落 `created_at → finished_at` 并计入
  `wall_fallback_n`；**在制** = `finished_at − created_at`。
- **失败回流率** = 同 `plan+node` 存在更晚 attempt 的 failed 单 / failed 单；分母为 0 → `null`
  （**不写 0%**）。另有平台级通道 `mission_lifecycle_log.action='custody_requeue'` 计数。
- **百分位**：线性插值 `h=(n−1)q`，与 PostgreSQL `percentile_cont` 同构，故对拍容差可取 1e-9。
  统计层**不取整**（取整只在渲染层），否则「先舍入再统计」与 SQL 全精度结果永远差 ~1e-6。

## 用法

```bash
# 打印（不落盘），重点看 stderr 的 [crosscheck]
python3 tools/campaign_throughput.py --plans 11,12,13 --crosscheck --dry-run

# 落盘：JSON + markdown + 图
python3 tools/campaign_throughput.py --plans 11,12,13 --crosscheck \
    --out-json artifacts/campaign_throughput/report_plans_11_13.json \
    --out-md   docs/campaign_throughput/THROUGHPUT_REPORT.md \
    --figure-dir artifacts/campaign_throughput

# 全战役回填
python3 tools/campaign_throughput.py --plans all --crosscheck \
    --out-json artifacts/campaign_throughput/report_all_plans.json \
    --out-md   docs/campaign_throughput/THROUGHPUT_BACKFILL_ALL_PLANS.md

# 离线（无 DB）：fixture 源，供 CI/评审复现
python3 tools/campaign_throughput.py --source fixture --fixture tests/fixtures/campaign_throughput_sample.json --dry-run
```

取数传输：优先 `TP_PG_PASSWORD`（TCP，`TP_PG_HOST/USER/DB`），否则回落
`su postgres -c psql`（本机 peer 认证）。SQL 一律只读 `SELECT`。

## 对拍机制

`--crosscheck` 用**独立 SQL**（`FILTER` + `percentile_cont` + `EXISTS` 半连接，见报表 §5 折叠块
原文）重算 15 项关键量；与 Python 侧逐项比对，`rel_err ≤ 1e-9`（时间戳比到 1e-6 s）为 `pass`。
判定：任一 `FAIL` / `one_null` / `sql_missing` → `ok=false`，进程退出码 1（可作 CI 门）。
`both_null`（两侧都无定义，如无失败样本时的回流率）单独计数、不算通过也不算失败。

## 局限（详见报表 §6）

- 逐 plan 样本量小（plan 11/12/13 终态 9/3/3 张）→ P95 属指示性，不作 SLA 承诺。
- 领取证据依赖会话事件 `note/mission_claimed`，只对 2026-11 之后的 mission 完整；更早的
  mission 墙钟走回落口径。
- 自启单（`selfstart-*`、空 `task_key`）无稳定 node 身份，回流率对其为 NULL。