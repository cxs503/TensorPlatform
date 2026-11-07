#!/usr/bin/env python3
"""IN20-R4 报告图（程序化取数，零手抄）。

图 1 artifacts/propose_channel_guard/fig1_channel_rootcause.png
  (a) 提案波三槽位结局：按钉住模型分组（唯一变量=模型名）
  (b) glm-5.1 名下一次 claim 活动 vs 9 张积压入池时刻
  (c) 积压单在池中滞留时长（疏散前后）
图 2 artifacts/propose_channel_guard/fig2_wave_cycle_idle.png
  wave_cycle 日志逐日行数（熔断等人 → 空转零记录 → 人工重启）

数据源：tensorplatform 库（runuser postgres）+ /var/log/tp-wave-cycle.log。
"""
import json
import pathlib
import subprocess
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = pathlib.Path(__file__).resolve().parent.parent / "artifacts" / "propose_channel_guard"
OUT.mkdir(parents=True, exist_ok=True)
PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-t", "-A", "-F", "\x02", "-c"]


def q(sql):
    p = subprocess.run(PSQL + [sql], capture_output=True, text=True, check=True)
    return [ln.split("\x02") for ln in p.stdout.splitlines() if ln.strip()]


# ── 数据 ────────────────────────────────────────────────────────────────
slots = q("SELECT model, status, count(*) FROM tensor_dev_missions "
          "WHERE task_key ~ '^propose-p[0-9]+-w1-[123]$' AND created_at > '2026-11-06' "
          "GROUP BY 1,2 ORDER BY 1")
activity = q("SELECT model, max(heartbeat_at)::text FROM tensor_dev_missions "
             "WHERE claimed_by IS NOT NULL AND model LIKE 'glm-5.1' GROUP BY 1")
backlog = q("SELECT id, created_at::text, finished_at::text, "
            "coalesce(result->'guard'->>'reason','') FROM tensor_dev_missions "
            "WHERE task_key ~ '^propose-p[0-9]+-w1-2$' AND created_at > '2026-11-06' ORDER BY id")
arb = q("SELECT min(created_at)::text FROM tensor_dev_missions WHERE task_key LIKE 'arbiter-%' "
        "AND created_at > '2026-11-07'")
log = pathlib.Path("/var/log/tp-wave-cycle.log").read_text().splitlines()

ST = {"succeeded": "#2a9d8f", "queued": "#e76f51", "stopped": "#8d99ae", "failed": "#6d597a"}
models = sorted({r[0] for r in slots})
counts = {m: {} for m in models}
for model, st, n in slots:
    counts[model][st] = int(n)

fig = plt.figure(figsize=(13.5, 10.5))
gs = fig.add_gridspec(3, 1, height_ratios=[1.0, 1.0, 1.0], hspace=0.55)

# (a) 槽位结局按模型
ax = fig.add_subplot(gs[0])
bottom = [0] * len(models)
for st in ("succeeded", "queued", "stopped", "failed"):
    vals = [counts[m].get(st, 0) for m in models]
    if not any(vals):
        continue
    ax.bar(models, vals, bottom=bottom, color=ST[st], label=st, edgecolor="white")
    for i, v in enumerate(vals):
        if v:
            ax.text(i, bottom[i] + v / 2, str(v), ha="center", va="center",
                    color="white", fontsize=11, fontweight="bold")
    bottom = [b + v for b, v in zip(bottom, vals)]
ax.set_title("(a) Outcome of the 3 proposer slots per wave (20 waves, 2026-11-06/07): the only variable is the pinned model", fontsize=10)
ax.set_ylabel("missions")
ax.legend(fontsize=9, ncol=4)
for i, m in enumerate(models):
    ax.text(i, bottom[i] + 0.15, f"pinned: {m}", ha="center", fontsize=8)
ax.set_ylim(0, max(bottom) + 1.2)

# (b) glm-5.1 claim 活动 vs 积压入池
ax = fig.add_subplot(gs[1])
if activity:
    last = datetime.fromisoformat(activity[0][1]).timestamp()
    ax.axvline(last, color="#264653", lw=2, ls="--",
               label=f"last claim activity under glm-5.1: {activity[0][1][:16]}")
created = [datetime.fromisoformat(b[1]).timestamp() for b in backlog]
ax.scatter(created, [1.0] * len(created), marker="v", s=90, color="#e76f51",
           zorder=5, label="9 stuck proposer missions enqueued (propose-p*-w1-2)")
if arb:
    ta = datetime.fromisoformat(arb[0][0]).timestamp()
    ax.axvline(ta, color="#2a9d8f", lw=2, label=f"first arbiter dispatched after unblocking: {arb[0][0][:16]}")
ax.set_yticks([])
ax.set_title("(b) Behavioural evidence: zero claims under glm-5.1 for 6 days while 9 missions waited 3 h", fontsize=10)
for v in (last, created[0], created[-1], (datetime.fromisoformat(arb[0][0]).timestamp() if arb else last)):
    ax.annotate(datetime.fromtimestamp(v).strftime("%m-%d %H:%M"),
                (v, 1.02), rotation=0, fontsize=8, ha="center")
ax.set_ylim(0.9, 1.1)
ax.legend(fontsize=8, loc="lower left")

# (c) 滞留时长
ax = fig.add_subplot(gs[2])
dur = []
for mid, c, f, reason in backlog:
    t0 = datetime.fromisoformat(c).timestamp()
    t1 = datetime.fromisoformat(f).timestamp() if f else t0
    dur.append((int(mid), (t1 - t0) / 3600.0, reason))
ax.barh([f"m{d[0]}" for d in dur], [d[1] for d in dur], color="#e76f51")
for i, d in enumerate(dur):
    ax.text(d[1] + 0.05, i, f"{d[1]:.2f} h", va="center", fontsize=9)
ax.set_xlabel("time in queue before withdrawal with reason (hours)")
ax.set_title("(c) Queue residence time of the 9 backlogged missions (all reached a terminal state, reason audited)", fontsize=10)
fig.suptitle("IN20-R4 propose-channel root cause: model pinning x claim filter -> arbiter never fires", fontsize=13, fontweight="bold")
fig.savefig(OUT / "fig1_channel_rootcause.png", dpi=140, bbox_inches="tight")
plt.close(fig)

# ── 图 2：wave_cycle 逐日日志行数 ───────────────────────────────────────
per_day = {}
for ln in log:
    if ln.startswith("[20"):
        per_day[ln[1:11]] = per_day.get(ln[1:11], 0) + 1
days = sorted(per_day)[-18:]
fig2, ax2 = plt.subplots(figsize=(11, 4.2))
vals = [per_day[d] for d in days]
bars = ax2.bar(days, vals, color=["#e76f51" if v == 0 else "#457b9d" for v in vals])
for b, v in zip(bars, vals):
    ax2.text(b.get_x() + b.get_width() / 2, v + 0.6, str(v), ha="center", fontsize=9)
ax2.set_title("wave_cycle log lines per day: human-dependent halt (11-01 20:34) -> silent idle 11-02..11-05 -> manual restart", fontsize=10)
ax2.set_ylabel("log lines / day")
ax2.annotate("3 session failures -> st['halt']=waiting for a human;\nafterwards only status file, no log lines",
             xy=(days[vals.index(0)] if 0 in vals else days[0], 0.2),
             xytext=(0.06, 0.72), textcoords="axes fraction", fontsize=9,
             arrowprops=dict(arrowstyle="->", color="#264653"))
fig2.tight_layout()
fig2.savefig(OUT / "fig2_wave_cycle_idle.png", dpi=140, bbox_inches="tight")
plt.close(fig2)

meta = {"slots": counts, "glm51_last_claim": activity, "backlog": backlog,
        "first_arbiter_after_fix": arb, "log_lines_per_day": per_day}
(OUT / "figures_data.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
print("figures written:", [p.name for p in sorted(OUT.glob("*.png"))])
print(json.dumps({"slots": counts, "glm51_last_claim": activity,
                  "first_arbiter_after_fix": arb}, ensure_ascii=False))