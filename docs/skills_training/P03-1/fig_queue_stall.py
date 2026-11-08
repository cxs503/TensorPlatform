#!/usr/bin/env python3
"""P03-1 训练单：把「入池→收养→终态」链路的现场证据画成图（只读）。

数据来源全部是只读 SQL（runuser -u postgres -- psql -tA），
图里的每个数字都由本脚本从库里现取，不做任何手抄。

Panel A：queued 池按 model 引脚分组 —— 条数 + 最老年龄（分钟），240 min 阈值线。
Panel B：同一条车道上的两个「存活信号」对照 ——
         worker 注册表心跳新鲜度（分钟） vs 该 model 最近一次真实认领证据（分钟）。
         两者的差距就是「心跳新鲜、车道已死」这个洞的可视图。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent
THRESHOLD_MIN = 240.0

PSQL = ["runuser", "-u", "postgres", "--", "psql", "-tA", "-F", "|", "-d", "tensorplatform", "-c"]


def q(sql: str) -> list[list[str]]:
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True, check=True).stdout
    return [ln.split("|") for ln in out.strip().splitlines() if ln.strip()]


def collect() -> dict:
    parked = q("""
        select coalesce(model,'(null)'), count(*),
               round(max(extract(epoch from (now()-created_at))/60))
        from tensor_dev_missions
        where status='queued' and suspended_at is null
        group by 1 order by 2 desc;
    """)
    last_claim = q("""
        select coalesce(model,'(null)'),
               coalesce(round(extract(epoch from (now()-max(coalesce(finished_at,heartbeat_at))))/60)::text,'never')
        from tensor_dev_missions
        where model is not null and status in ('succeeded','failed','claimed','running')
        group by 1;
    """)
    hb = q("""
        select coalesce(model,'(null)'), count(*) filter (where enabled),
               coalesce(round(min(extract(epoch from (now()-heartbeat_at)))/60)::text,'never')
        from factory_resident_workers
        where enabled group by 1;
    """)
    totals = dict(
        (r[0], r[1])
        for r in q("""
            select 'queued', count(*) from tensor_dev_missions where status='queued'
            union all select 'awaiting_acceptance', count(*) from tensor_dev_missions
                where status='awaiting_acceptance'
            union all select 'oldest_queued_min', round(max(extract(epoch from (now()-created_at))/60))
                from tensor_dev_missions where status='queued'
            union all select 'glm51_queued', count(*) from tensor_dev_missions
                where status='queued' and model='glm-5.1';
        """)
    )
    return {
        "queued_by_model": [{"model": m, "count": int(c), "oldest_min": float(a)} for m, c, a in parked],
        "last_claim_min": {m: (None if v == "never" else float(v)) for m, v in last_claim},
        "registrants": {m: {"enabled": int(c), "hb_min": (None if v == "never" else float(v))}
                        for m, c, v in hb},
        "totals": totals,
    }


def draw(d: dict) -> Path:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.6))

    rows = sorted(d["queued_by_model"], key=lambda r: -r["count"])
    labels = [f"{r['model']}\n(n={r['count']})" for r in rows]
    ages = [r["oldest_min"] / 60.0 for r in rows]
    colors = ["#c62828" if a * 60 > THRESHOLD_MIN else "#2e7d32" for a in ages]
    bars = ax1.bar(range(len(rows)), ages, color=colors, width=0.55)
    ax1.axhline(THRESHOLD_MIN / 60.0, ls="--", lw=1.2, color="#37474f",
                label=f"stall threshold {THRESHOLD_MIN:.0f} min")
    for b, a in zip(bars, ages):
        ax1.annotate(f"{a:.1f} h", (b.get_x() + b.get_width() / 2, b.get_height()),
                     ha="center", va="bottom", fontsize=9)
    ax1.set_xticks(range(len(rows)))
    ax1.set_xticklabels(labels, fontsize=9)
    ax1.set_ylabel("oldest adoptable queued age (hours)")
    ax1.set_title("A. queued pool by model pin (live, read-only)")
    ax1.legend(fontsize=8, loc="upper right")

    # Panel B: heartbeat freshness vs real claim evidence, per lane with queued work
    lanes = [r["model"] for r in rows if r["model"] in d["registrants"]]
    hb = [d["registrants"][m]["hb_min"] or 0.0 for m in lanes]
    lc = [d["last_claim_min"].get(m, 0.0) or 0.0 for m in lanes]
    idx = range(len(lanes))
    w = 0.36
    ax2.bar([i - w / 2 for i in idx], hb, width=w, color="#2e7d32", label="registrant heartbeat age")
    ax2.bar([i + w / 2 for i in idx], lc, width=w, color="#c62828", label="last real claim evidence age")
    ax2.set_yscale("symlog", linthresh=10)
    ax2.set_xticks(list(idx))
    ax2.set_xticklabels(lanes, fontsize=9)
    ax2.set_ylabel("age (minutes, symlog)")
    ax2.set_title("B. fresh heartbeat ≠ claimable lane")
    ax2.legend(fontsize=8)
    for i, v in zip(idx, lc):
        ax2.annotate(f"{v:.0f}m", (i + w / 2, v), ha="center", va="bottom", fontsize=8)

    fig.suptitle("P03-1 · queue accepted → never claimed: live signature", fontsize=12)
    fig.tight_layout()
    png = OUT / "fig_queue_stall.png"
    fig.savefig(png, dpi=140)
    plt.close(fig)
    return png


if __name__ == "__main__":
    data = collect()
    png = draw(data)
    (OUT / "queue_stall_evidence.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"png": str(png), "totals": data["totals"]}, ensure_ascii=False))