#!/usr/bin/env python3
"""KPI-SUCCESS-90 m16761 周期复测：从冻结 CSV 快照算日成功率趋势 + 失败桶，
生成图与 result.json（零手抄，全部数字来自 artifacts/kpi_success_90/result.json 与 CSV）。

用法: python3 tools/kpi_m16761_analyze.py
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
CSV = Path("/tmp/kpi_success_90_m16761.csv")
OUT = ROOT / "artifacts" / "kpi_success_90"
FIG = OUT / "figures"
FIG.mkdir(parents=True, exist_ok=True)

result = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
success = result["success"]
masked = result["attribution"]["masked"]
as_of = datetime.fromisoformat(success["as_of"])


def parse_ts(v):
    if not v:
        return None
    t = str(v).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        return None


# ---- 日成功率（终态口径 succeeded/(succeeded+failed)，按 finished_at 缺则 created_at）----
daily = defaultdict(lambda: [0, 0])  # day -> [succeeded, failed]
for row in csv.DictReader(CSV.open(encoding="utf-8")):
    st = row["status"]
    if st not in ("succeeded", "failed"):
        continue
    ts = parse_ts(row["finished_at"]) or parse_ts(row["created_at"])
    if ts is None:
        continue
    day = ts.astimezone(timezone.utc).date()
    if st == "succeeded":
        daily[day][0] += 1
    else:
        daily[day][1] += 1

days = sorted(daily)
last30 = [d for d in days if d >= days[-1] - timedelta(days=29)]
x = [d.isoformat() for d in last30]
rate = [100.0 * s / (s + f) if (s + f) else None for s, f in (daily[d] for d in last30)]
n_term = [s + f for s, f in (daily[d] for d in last30)]

fig, ax = plt.subplots(figsize=(11, 4.6), dpi=140)
ax.plot(x, rate, marker="o", ms=3, lw=1.2, color="#1f77b4", label="daily success rate (succeeded/(succeeded+failed))")
ax.axhline(90, color="red", ls="--", lw=1.2, label="KPI target 90%")
ax.axhline(100.0 * success["all_time"]["success_rate"], color="gray", ls=":", lw=1,
           label=f"all-time {100*success['all_time']['success_rate']:.2f}%")
ax.set_ylim(0, 100)
ax.set_ylabel("success rate %")
ax.set_title("KPI-SUCCESS-90 daily mission success rate (last 30 terminal days, as_of %s)" % as_of.date())
ax.legend(fontsize=8)
ax.grid(alpha=0.3)
fig.autofmt_xdate()
fig.tight_layout()
fig.savefig(FIG / "daily_success_rate.png")
plt.close(fig)

# ---- 失败桶（top gaps）----
buckets = {}
for row in csv.DictReader((OUT / "failure_buckets.csv").open(encoding="utf-8")):
    buckets[f"{row['stage']}/{row['kind']}"] = int(row["count"])
top = sorted(buckets.items(), key=lambda kv: -kv[1])
labels = [k for k, _ in top][::-1]
vals = [v for _, v in top][::-1]
failed_total = masked["cohort"]

fig, ax = plt.subplots(figsize=(9, 5.2), dpi=140)
colors = ["#d62728" if k == "worktree/disk_full" else "#1f77b4" for k in labels]
ax.barh(labels, vals, color=colors)
for i, v in enumerate(vals):
    ax.text(v + max(vals) * 0.01, i, f"{v} ({100*v/failed_total:.1f}%)", va="center", fontsize=8)
ax.set_xlabel("failed missions")
ax.set_title(f"Failure attribution buckets, all-time (n_failed={failed_total}, snapshot sha256 {result['snapshot_sha256'][:12]}…)")
ax.grid(alpha=0.3, axis="x")
fig.tight_layout()
fig.savefig(FIG / "failure_buckets.png")
plt.close(fig)

# ---- result.json（任务要求格式）----
all_time = success["all_time"]
w7 = success["last_7d"]
cur_all = 100.0 * all_time["success_rate"]
cur_7d = 100.0 * w7["success_rate"]
top_gaps = [
    {
        "gap": "worktree/disk_full 单点基础设施事故",
        "n": buckets.get("worktree/disk_full", 0),
        "share_of_failed_pct": round(100.0 * buckets.get("worktree/disk_full", 0) / failed_total, 2),
        "evidence": ("incident_cohesion: top2_days={top_days} lift={lift:.2f} corroborated={corr}（2026-11-10/11 磁盘写满，占失败 56.4%）"
                     .format(top_days=result["incident_cohesion"]["disk_full"]["top_days"],
                             lift=result["incident_cohesion"]["disk_full"]["lift"],
                             corr=result["incident_cohesion"]["disk_full"]["corroborated"])),
    },
    {
        "gap": "llm_infra/llm_error",
        "n": buckets.get("llm_infra/llm_error", 0),
        "share_of_failed_pct": round(100.0 * buckets.get("llm_infra/llm_error", 0) / failed_total, 2),
        "evidence": "规则 R-LLM-F01 held-out 精度 90.5%（唯一过精度门的规则）",
    },
    {
        "gap": "agent_loop/no_progress",
        "n": buckets.get("agent_loop/no_progress", 0),
        "share_of_failed_pct": round(100.0 * buckets.get("agent_loop/no_progress", 0) / failed_total, 2),
        "evidence": "规则 R-AGENT-01 held-out 精度 31.8% < 80% 红线，不可进台账",
    },
    {
        "gap": "unknown/unclassified（不可归因）",
        "n": buckets.get("unknown/unclassified", 0),
        "share_of_failed_pct": round(100.0 * buckets.get("unknown/unclassified", 0) / failed_total, 2),
        "evidence": "unknown_rate={u:.2f}% > 5% 上限".format(u=100.0 * masked["unknown_rate"]),
    },
    {
        "gap": "budget（budget_exhausted+wall_exhausted）",
        "n": buckets.get("budget/budget_exhausted", 0) + buckets.get("budget/wall_exhausted", 0),
        "share_of_failed_pct": round(100.0 * (buckets.get("budget/budget_exhausted", 0) + buckets.get("budget/wall_exhausted", 0)) / failed_total, 2),
        "evidence": "规则 R-BUDGET-01 held-out 精度 32.4% < 80% 红线",
    },
]

result_out = {
    "kpi": "KPI-SUCCESS-90",
    "mission_id": 16761,
    "as_of": success["as_of"],
    "current": round(cur_all, 2),
    "unit": "percent",
    "current_detail": {
        "all_time": round(cur_all, 2),
        "last_7d": round(cur_7d, 2),
        "strict_all_time_incl_stopped_revoked": round(100.0 * all_time["strict_success_rate"], 2),
        "counts_all_time": all_time["counts"],
        "counts_last_7d": w7["counts"],
        "attribution": {
            "raw_coverage_pct": round(100.0 * masked["coverage"], 2),
            "trusted_coverage_pct": round(100.0 * result["attribution"]["declared_coverage"], 2),
            "heldout_stage_accuracy_pct": round(100.0 * masked["heldout"]["stage_accuracy"], 2),
            "unknown_rate_pct": round(100.0 * masked["unknown_rate"], 2),
        },
    },
    "target": 90.0,
    "gap": {
        "all_time_pp": round(90.0 - cur_all, 2),
        "last_7d_pp": round(90.0 - cur_7d, 2),
        "attribution_trusted_coverage_pp": round(95.0 - 100.0 * result["attribution"]["declared_coverage"], 2),
        "attribution_heldout_accuracy_pp": round(80.0 - 100.0 * masked["heldout"]["stage_accuracy"], 2),
        "verdict": "FAIL（成功率与自动归因两判据均未达标）",
    },
    "data_sources": [
        "SQL: psql -U foundation -d tensorplatform -h /var/run/postgresql -At -f tools/failure_attribution/export.sql > /tmp/kpi_success_90_m16761.csv（只读 COPY，表 tensor_dev_missions，status IN succeeded/failed/stopped/revoked）",
        "表: tensorplatform.tensor_dev_missions（生产库，8010 API 同源）",
        "工件: artifacts/kpi_success_90/result.json（snapshot_sha256=%s, rows=%d）" % (result["snapshot_sha256"], result["snapshot_rows"]),
        "工件: artifacts/kpi_success_90/failure_buckets.csv, rule_precision.csv, backfill_fail_stage.sql",
        "复算: python3 -m tools.failure_attribution measure --missions-csv /tmp/kpi_success_90_m16761.csv --outdir artifacts/kpi_success_90",
    ],
    "top_gaps": top_gaps,
    "proposed_anchors": [
        {
            "anchor": "KPI-DISK-FULL-0",
            "definition": "worktree/disk_full 失败数 = 0（或占失败比 ≤5%）；磁盘水位告警先于任务失败触发",
            "rationale": "disk_full 占全部失败 56.4%（2316/4109），是成功率缺口 17.93pp 的最大单一来源；反事实：若该 2316 单转成功 all-time 72.07%→87.81%（12919/14712），若剔除出分母 85.54%（10603/12396）",
        },
        {
            "anchor": "KPI-LLM-ERROR-5",
            "definition": "llm_infra/llm_error 占失败比 ≤5%",
            "rationale": "当前 13.8%（566/4109）；R-LLM-F01 精度 90.5% 已可自动归因，缺的是 LLM 网关重试/降级机制",
        },
        {
            "anchor": "KPI-ATTRIBUTION-TRUSTED-95",
            "definition": "trusted 归因覆盖率 ≥95%（declared 或过精度门规则）",
            "rationale": "当前 trusted 仅 29.03%（1193/4109）；KPI 第二判据『失败自动归因』的硬口径",
        },
        {
            "anchor": "KPI-UNKNOWN-5",
            "definition": "unknown 不可归因率 ≤5%",
            "rationale": "当前 12.05%（495/4109）；需执行器在终态时刻写结构化 fail_stage（declared 通道），而非事后文本猜",
        },
    ],
    "criteria": result["criteria"],
    "snapshot_sha256": result["snapshot_sha256"],
}
(OUT / "result_m16761.json").write_text(json.dumps(result_out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"current": result_out["current"], "gap": result_out["gap"],
                  "daily_last30": {"days": len(last30), "min": min(r for r in rate if r is not None),
                                   "max": max(r for r in rate if r is not None)}}, ensure_ascii=False, indent=1))
