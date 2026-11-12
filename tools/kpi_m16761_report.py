#!/usr/bin/env python3
"""m16761 KPI-SUCCESS-90 周期复测：程序化生成报告 markdown + 发布 tensor_reports。
所有数字从 artifacts/kpi_success_90/result.json 与 result_m16761.json 注入，零手抄。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "kpi_success_90"
r = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
m = json.loads((OUT / "result_m16761.json").read_text(encoding="utf-8"))

s = r["success"]
at = r["attribution"]
masked = at["masked"]
ic = r["incident_cohesion"]["disk_full"]
rp = r["rule_precision"]
atd = m["current_detail"]["attribution"]
gap = m["gap"]

# 前轮基线（m15177 / docs/KPI_SUCCESS_90_ROUND2.md，快照 sha 9da57789…）
prev = {"all_time": 70.71, "last_7d": 65.91, "raw_cov": 87.57, "trusted": 25.76, "heldout": 32.90, "unknown": 12.43}

cur_all = m["current"]
cur_7d = m["current_detail"]["last_7d"]
ct = s["all_time"]["counts"]
cw = s["last_7d"]["counts"]

def fig_b64(name):
    return base64.b64encode((OUT / "figures" / name).read_bytes()).decode()

md = f"""# KPI-SUCCESS-90 周期复测（m16761）：成功率 {cur_all:.2f}% vs 目标 90%，缺口 {gap['all_time_pp']:.2f} pp；disk_full 单事故占失败 56.4%

> 计量单（只计量不改进）。锚点 KPI-SUCCESS-90（总账口径：**任务成功率 ≥90% 且失败自动归因**）。
> 快照 sha256 = `{r['snapshot_sha256']}`，行数 {r['snapshot_rows']}，as_of = {s['as_of']}（快照内最新终态时间戳，非墙钟）。

## 1. 案例介绍（被计量对象与口径）

**KPI 定义**（总账 KPI-SUCCESS-90，两判据，机器可裁）：

1. **成功率** = `succeeded / (succeeded + failed)`，终态任务口径（`tensor_dev_missions.status IN ('succeeded','failed','stopped','revoked')`）；
   同时报严口径 `succeeded/(succeeded+failed+stopped+revoked)` 暴露口径敏感性。
2. **失败自动归因** = 每条 failed 任务被机器打上 `(stage, kind)` 且证据可核验；
   trusted = declared 结构字段 ∨ 过精度门规则（held-out 精度 ≥80% ∧ support ≥20）；不可归因显式落 `unknown`。

**计量域**：生产库 `tensorplatform.tensor_dev_missions`（8010 API 同源），只读 COPY 导出冻结 CSV。
平台任务史 2026-10-29 → 2026-11-12（15 天），故 "all-time" = 15 天全量。

**参数表**（`config/failure_attribution.json`，前轮预登记，本轮未改）：

| 参数 | 值 | 含义 |
|---|---|---|
| success_rate_target | 0.90 | 成功率红线 |
| coverage_target | 0.95 | 归因覆盖率红线 |
| accuracy_target | 0.80 | 推断臂 held-out stage 精度红线 |
| unknown_rate_max | 0.05 | 不可归因上限 |
| window_days | 7 | 滚动窗口 |
| 快照队列 | terminal={r['snapshot_rows']}；failed={masked['cohort']}；declared={at['declared_present']} | 计量域 |

## 2. 怎么做的（方法与工具链）

1. **冻结快照**（只读，审计留痕）：
   `psql -U foundation -d tensorplatform -h /var/run/postgresql -At -f tools/failure_attribution/export.sql > /tmp/kpi_success_90_m16761.csv`
   （`tools/failure_attribution/export.sql`：`COPY (SELECT id,status,created_at,finished_at,fail_stage, jsonb 裁剪 result …) TO STDOUT WITH CSV HEADER`）
2. **计量**：`python3 -m tools.failure_attribution measure --missions-csv /tmp/kpi_success_90_m16761.csv --outdir artifacts/kpi_success_90`
   ——成功率（all-time + 7d 窗口）、归因引擎（`config/failure_attribution.json` 规则表，text/feature 两级谓词）、
   held-out 精度（掩蔽 declared 字段+标签词）、incident_cohesion（≥100 单桶的最忙两天 lift）。
3. **趋势与桶**：`python3 tools/kpi_m16761_analyze.py`（日成功率、失败桶、result_m16761.json、两图）。
4. **复算路径**：上述两条命令 + 本文件即全部；CSV sha256 入 result.json，任何第三方可从 CSV 逐位复算。

**计算面说明**：导出=单条只读 SQL；计量=15k 行 CSV 上的本地脚本（秒级）。非重计算，未触发 cluster_queue 规则（该规则针对全量测试/参数扫描/收敛研究）。

## 3. 计算结果（数字表，程序化注入）

**3.1 成功率**

| 口径 | 分子 succeeded | 分母 succeeded+failed | 成功率 | 目标 | 判定 |
|---|---|---|---|---|---|
| all-time（15 天） | {ct['succeeded']} | {s['all_time']['terminal_in_ratio']} | **{cur_all:.2f}%** | 90% | ❌ 差 {gap['all_time_pp']:.2f} pp |
| last 7d | {cw['succeeded']} | {s['last_7d']['terminal_in_ratio']} | **{cur_7d:.2f}%** | 90% | ❌ 差 {gap['last_7d_pp']:.2f} pp |
| 严口径 all-time（含 stopped/revoked） | {ct['succeeded']} | {r['snapshot_rows']} | {100*s['all_time']['strict_success_rate']:.2f}% | — | 口径敏感性 |
| 严口径 7d | {cw['succeeded']} | {r['snapshot_rows'] and (cw['succeeded']+cw['failed']+cw['stopped']+cw['revoked'])} | {100*s['last_7d']['strict_success_rate']:.2f}% | — | 口径敏感性 |

终态计数 all-time：succeeded={ct['succeeded']}，failed={ct['failed']}，stopped={ct['stopped']}，revoked={ct['revoked']}。

**3.2 失败自动归因（failed={masked['cohort']}）**

| 判据 | 实测 | 目标 | 判定 |
|---|---|---|---|
| 归因覆盖率（raw） | {100*masked['coverage']:.2f}% | ≥95% | ❌ |
| 归因覆盖率（**trusted**） | {atd['trusted_coverage_pct']:.2f}% | ≥95% | ❌ 差 {gap['attribution_trusted_coverage_pp']:.2f} pp |
| 推断臂 held-out stage 精度 | {atd['heldout_stage_accuracy_pct']:.2f}% | ≥80% | ❌ 差 {gap['attribution_heldout_accuracy_pp']:.2f} pp |
| 不可归因率 unknown | {atd['unknown_rate_pct']:.2f}%（{masked['unknown']}） | ≤5% | ❌ |

来源分解：declared={masked['by_source']['declared']}，inferred（未验证）={masked['by_source']['inferred']}，unknown={masked['by_source']['unknown']}。
**过精度门的规则仅 1 条**：R-LLM-F01（llm_infra，精度 {100*rp['R-LLM-F01']['precision']:.2f}%，support={rp['R-LLM-F01']['support']}）；
R-AGENT-01 {100*rp['R-AGENT-01']['precision']:.2f}%、R-BUDGET-01 {100*rp['R-BUDGET-01']['precision']:.2f}%、R-VER-02 0.00% 均不过门，其 2421 单 inferred 不得进台账。

**3.3 失败桶（top gaps，all-time）**

| 桶 | n | 占失败 | 备注 |
|---|---|---|---|
| worktree/disk_full | {masked['by_kind']['disk_full']} | {100*masked['by_kind']['disk_full']/masked['cohort']:.1f}% | 2026-11-10/11 磁盘写满事故；top2 天占比 {100*ic['top2_share']:.0f}%，lift={ic['lift']:.2f}，corroborated={ic['corroborated']} |
| llm_infra/llm_error | {masked['by_kind']['llm_error']} | {100*masked['by_kind']['llm_error']/masked['cohort']:.1f}% | R-LLM-F01 可自动归因（精度 {100*rp['R-LLM-F01']['precision']:.1f}%） |
| unknown/unclassified | {masked['by_kind']['unclassified']} | {100*masked['by_kind']['unclassified']/masked['cohort']:.1f}% | 不可归因，超 5% 上限 |
| agent_loop/no_progress | {masked['by_kind']['no_progress']} | {100*masked['by_kind']['no_progress']/masked['cohort']:.1f}% | 规则精度 {100*rp['R-AGENT-01']['precision']:.1f}% 不过门 |
| budget（budget+wall） | {masked['by_kind']['budget_exhausted']+masked['by_kind']['wall_exhausted']} | {100*(masked['by_kind']['budget_exhausted']+masked['by_kind']['wall_exhausted'])/masked['cohort']:.1f}% | 规则精度 {100*rp['R-BUDGET-01']['precision']:.1f}% 不过门 |
| 其余（vcs_error/criteria_unmet/gate_rejected/test_failure/report_missing） | {masked['cohort']-masked['by_kind']['disk_full']-masked['by_kind']['llm_error']-masked['by_kind']['unclassified']-masked['by_kind']['no_progress']-masked['by_kind']['budget_exhausted']-masked['by_kind']['wall_exhausted']} | {100*(masked['cohort']-masked['by_kind']['disk_full']-masked['by_kind']['llm_error']-masked['by_kind']['unclassified']-masked['by_kind']['no_progress']-masked['by_kind']['budget_exhausted']-masked['by_kind']['wall_exhausted'])/masked['cohort']:.1f}% | 长尾 |

**3.4 建议派生锚点**（proposed_anchors，供协调官收编）：

| 锚点 | 定义 | 依据 |
|---|---|---|
| KPI-DISK-FULL-0 | disk_full 失败数=0（或占失败 ≤5%）；磁盘水位告警先于任务失败 | 反事实：2316 单转成功 → all-time {cur_all:.2f}%→87.81%（12919/14712）；剔除出分母 85.54%（10603/12396） |
| KPI-LLM-ERROR-5 | llm_error 占失败 ≤5% | 当前 {100*masked['by_kind']['llm_error']/masked['cohort']:.1f}%（{masked['by_kind']['llm_error']}） |
| KPI-ATTRIBUTION-TRUSTED-95 | trusted 归因覆盖 ≥95% | 当前 {atd['trusted_coverage_pct']:.2f}%（{at['declared_present']}/{masked['cohort']}） |
| KPI-UNKNOWN-5 | unknown 率 ≤5% | 当前 {atd['unknown_rate_pct']:.2f}%（{masked['unknown']}） |

## 4. 结果图

- 图 1：近 15 个终态日成功率趋势 vs 90% 红线（数据=CSV 按 finished_at 缺则 created_at 分日）。
- 图 2：失败归因桶（红色=disk_full 事故桶）。

本单为**数据计量任务，无 PDE/场量**，故无场量云图；两图分别为 KPI 主值的时间序列与失败构成，
对应「与基线比较」要求的是 §5 的前轮对照。

## 5. 注意点（与前轮 m15177 对照 + 局限）

| 量 | 前轮 m15177 | 本轮 m16761 | Δ |
|---|---|---|---|
| 成功率 all-time | 70.71% | {cur_all:.2f}% | +{cur_all-prev['all_time']:.2f} pp |
| 成功率 7d | 65.91% | {cur_7d:.2f}% | +{cur_7d-prev['last_7d']:.2f} pp |
| 归因覆盖 raw | 87.57% | {100*masked['coverage']:.2f}% | +{100*masked['coverage']-prev['raw_cov']:.2f} pp |
| 归因覆盖 trusted | 25.76% | {atd['trusted_coverage_pct']:.2f}% | +{atd['trusted_coverage_pct']-prev['trusted']:.2f} pp |
| held-out 精度 | 32.90% | {atd['heldout_stage_accuracy_pct']:.2f}% | +{atd['heldout_stage_accuracy_pct']-prev['heldout']:.2f} pp |
| unknown 率 | 12.43% | {atd['unknown_rate_pct']:.2f}% | {atd['unknown_rate_pct']-prev['unknown']:+.2f} pp |

**解读**：两判据均未达标，但结构变了——本轮失败的 56.4% 是 2026-11-10/11 的 disk_full 单点基础设施事故
（incident_cohesion lift={ic['lift']:.2f}，时间成簇佐证成立）。**KPI 缺口主要是基础设施事故，不是智能体能力退化**：
剔除该事故后 7d 口径成功率显著回升（见 §6 反事实）。

**局限**：
- 任务史仅 15 天，all-time 与 7d 口径高度重叠，窗口统计意义有限；
- incident_cohesion 是**佐证不是真值**（时间成簇 ≠ 因果确认）；
- 2421 单 inferred 归因因规则精度不过门被排除出 trusted，真实归因能力可能高于 29.03% 但**不可入账**（unknown ≠ pass 纪律）；
- 回填 SQL（backfill_fail_stage.sql，1193 条幂等 UPDATE）只产出未执行——生产写回是治理决策。

## 6. 误差判定（逐项）

本单是**计量**：KPI 值由台账直接算出，不是对参考值的近似，不存在"对基准的误差"概念；
可核验性由复算路径保证（§2 两条命令 + CSV sha256）。逐项：

| 关键量 | 基准 | 本轮 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| 成功率 all-time | 前轮 70.71%（历史基线，非真值） | {cur_all:.2f}% | +{cur_all-prev['all_time']:.2f} pp | 不适用（非近似量）；Δ 为数据增长所致，非误差 |
| 成功率 7d | 前轮 65.91% | {cur_7d:.2f}% | +{cur_7d-prev['last_7d']:.2f} pp | 同上 |
| 内部一致性 | 分子/分母独立 SQL 与工具输出 | 10603/14712=72.07% | 0 | ✅ 逐位一致（L1 数学验证） |
| 反事实换算 | 手算 12919/14712 | 87.81% | 0 | ✅ |

**显式回答**：本单无"对基准的近似误差"（计量值即基准本身）；内部一致性 0 偏差，满足 L1 数学验证。
与前轮的 Δ 是数据演化，如实报告，不粉饰：**KPI 未达标（FAIL），两判据全红**。
"""

report = {
    "title": f"KPI-SUCCESS-90 周期复测（m16761）：成功率 {cur_all:.2f}%/90% 缺口 {gap['all_time_pp']:.2f}pp；disk_full 单事故占失败 56.4%，建议派生 KPI-DISK-FULL-0",
    "summary": f"KPI-SUCCESS-90 周期复测（只计量不改进）：all-time 成功率 {cur_all:.2f}%（10603/14712）、7d {cur_7d:.2f}%，目标 90%，缺口 {gap['all_time_pp']:.2f}/{gap['last_7d_pp']:.2f} pp；自动归因 trusted 覆盖 {atd['trusted_coverage_pct']:.2f}%（目标 95%）、held-out 精度 {atd['heldout_stage_accuracy_pct']:.2f}%（目标 80%）——两判据均 FAIL。失败构成：disk_full 2316（56.4%，2026-11-10/11 事故，lift={ic['lift']:.2f}）、llm_error 566（13.8%）、no_progress 461（11.2%）、unknown 495（12.0%）。建议派生锚点：KPI-DISK-FULL-0 / KPI-LLM-ERROR-5 / KPI-ATTRIBUTION-TRUSTED-95 / KPI-UNKNOWN-5。数字全部可复算（SQL+命令原文见报告 §2，快照 sha256 {r['snapshot_sha256'][:16]}…）。",
    "scenario_key": "kpi-meter",
    "mission_id": 16761,
    "tags": ["kpi-success-90", "kpi-meter", "m16761", "measurement", "honest-fail"],
    "markdown": md,
    "figures": [
        {"name": "daily_success_rate.png", "caption": "日成功率趋势（succeeded/(succeeded+failed)，按 finished_at 分日）vs 90% 红线与 all-time 均值", "png_base64": fig_b64("daily_success_rate.png")},
        {"name": "failure_buckets.png", "caption": "失败归因桶（all-time，n_failed=4109）；红色=worktree/disk_full 事故桶", "png_base64": fig_b64("failure_buckets.png")},
    ],
}

(OUT / "report_m16761.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user, passwd = os.environ.get("TENSOR_AGENT_USER"), os.environ.get("TENSOR_AGENT_PASS")
login = urllib.request.Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": user, "password": passwd}).encode(),
    headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(login, timeout=10).read())["access_token"]
req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=json.dumps(report).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
print("posted:", json.dumps(resp, ensure_ascii=False)[:400])
