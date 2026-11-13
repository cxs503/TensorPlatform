"""m17134 证据生成：红队实跑 + 机门/审计帽实测图（可复跑）。

用法::

    python docs/demand_intake/m17134/make_evidence.py

产物（同目录）：
    redteam_result.json    五条红队场景的真实返回（一次性 schema，跑完即弃）
    gate_metrics.json      机门/审计帽关键量（图上数字的唯一来源）
    gate_metrics.png       双面板柱图：铸造机门裁决分布 / 审计帽盖与缺口
    pytest_result.txt      pytest 输出（真库用例失败=证据失效）

诚实边界：图里的数字全部来自本次真库执行；无 psql/无库时脚本直接失败，不生成图。
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from tools.demand_intake import redteam  # noqa: E402
from tools.demand_intake.store import PsqlTransport, drop_schema  # noqa: E402


def main() -> int:
    t = PsqlTransport()
    schema = "m17134_evidence"
    try:
        res = redteam.run_all(t, schema, bootstrap=True)
    finally:
        drop_schema(t, schema)
    (HERE / "redteam_result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    by = {r["scenario"]: r for r in res["results"]}
    metrics = {
        "all_ok": res["all_ok"],
        "concurrent_same_slug": {
            "threads": by["concurrent_same_slug"]["threads"],
            "minted": by["concurrent_same_slug"]["minted"],
            "duplicate": by["concurrent_same_slug"]["duplicates"],
            "mission_rows": by["concurrent_same_slug"]["mission_rows"],
        },
        "cap_exceeded": {
            "cap": by["cap_exceeded"]["cap"],
            "minted": by["cap_exceeded"]["minted"],
            "cap_exceeded": by["cap_exceeded"]["cap_exceeded"],
        },
        "strike_downgrade": {
            "baseline_cap": by["strike_downgrade"]["baseline_cap"],
            "cap_after_strikes": by["strike_downgrade"]["cap_after_strikes"],
            "fresh_agent_first": by["strike_downgrade"]["fresh_agent_first"],
            "fresh_agent_second": by["strike_downgrade"]["fresh_agent_second"],
            "credit_levels": by["strike_downgrade"]["credit_levels"],
        },
        "audit_daily_cap": {
            "sampled": by["audit_daily_cap"]["sampled"],
            "uncovered": by["audit_daily_cap"]["uncovered"],
            "second_run_sampled": by["audit_daily_cap"]["second_run_sampled"],
        },
        "audit_fail_revokes": by["audit_fail_revokes"],
    }
    (HERE / "gate_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_demand_intake_v2.py",
         "tests/test_demand_intake_dispatch_client.py", "-q"],
        cwd=str(ROOT), capture_output=True, text=True)
    (HERE / "pytest_result.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")

    _figure(metrics, HERE / "gate_metrics.png")
    print(json.dumps({"all_ok": res["all_ok"], "pytest_rc": proc.returncode},
                     ensure_ascii=False))
    return 0 if res["all_ok"] and proc.returncode == 0 else 1


def _figure(metrics: dict, path: pathlib.Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    labels = ["same slug\n(4 threads)", "cap overflow\n(11 mints)", "post-strike\n(downgraded)"]
    minted = [metrics["concurrent_same_slug"]["minted"],
              metrics["cap_exceeded"]["minted"],
              int(metrics["strike_downgrade"]["fresh_agent_first"] == "minted")]
    blocked = [metrics["concurrent_same_slug"]["duplicate"],
               metrics["cap_exceeded"]["cap_exceeded"],
               int(metrics["strike_downgrade"]["fresh_agent_second"] == "cap_exceeded")]
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], minted, width=0.4, label="minted (anchor open)", color="#2b7a3d")
    ax.bar([i + 0.2 for i in x], blocked, width=0.4, label="blocked by gate", color="#b23b3b")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("count")
    ax.set_title("Machine gate rulings (real DB: concurrency / quota / strike)", fontsize=10)
    ax.legend(fontsize=8)
    for i, (m, b) in enumerate(zip(minted, blocked)):
        ax.text(i - 0.2, m + 0.1, str(m), ha="center", fontsize=9)
        ax.text(i + 0.2, b + 0.1, str(b), ha="center", fontsize=9)

    ax = axes[1]
    sampled = metrics["audit_daily_cap"]["sampled"]
    uncovered = metrics["audit_daily_cap"]["uncovered"]
    ax.bar(["audited today", "uncovered backlog"], [sampled, uncovered],
           color=["#2b5d8a", "#c98b1f"], width=0.5)
    ax.set_ylabel("anchors")
    ax.set_title("Post-hoc audit cap 20/day vs backlog (25 pending)", fontsize=10)
    for i, v in enumerate([sampled, uncovered]):
        ax.text(i, v + 0.2, str(v), ha="center", fontsize=10)
    ax.set_ylim(0, max(sampled, uncovered) * 1.25 + 1)

    fig.suptitle("FEAT-DEMAND-AUTOINTAKE v2: proposal-equals-mission + post-hoc audit (m17134)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())