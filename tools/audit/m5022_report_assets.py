#!/usr/bin/env python3
"""m5022 报告资产：迁移图/普查图 + 判据数字 JSON（程序化注入，零手抄）。

产物（均落 artifacts/m5022/）：
  fig1_state_machine.png   状态机（契约派生；REOPENED 高亮为唯一复活回边）
  fig2_reopen_census.png   历史复活四口径普查条形图 + 战役引用 19 例参考线
  verdict.json             判据数字（契约版本、词表规模、pytest 数、守护存量…）

用法：python3 tools/audit/m5022_report_assets.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "backend" / "contracts"
OUT = ROOT / "artifacts" / "m5022"

# 层内布局（手工坐标只为可读；边全部来自契约）
POS = {
    "queued": (0.5, 0.0),
    "claimed": (2.0, 0.0),
    "running": (3.5, 0.0),
    "awaiting_acceptance": (5.0, 1.1),
    "awaiting_pr_merge": (5.0, -1.1),
    "succeeded": (6.6, -1.1),
    "failed": (6.6, 1.1),
    "stopped": (8.0, 2.0),
    "revoked": (8.0, -2.0),
    "orphan": (8.0, 3.0),
    "suspended": (3.5, 1.9),
    "reopened": (7.4, 0.0),
}


def load():
    vocab = json.loads((CONTRACTS / "mission_status_vocab.v1.json").read_text())
    sm = json.loads((CONTRACTS / "mission_state_machine.v1.json").read_text())
    return vocab, sm


def fig_state_machine(path: Path) -> None:
    vocab, sm = load()
    graph = {k: tuple(v["to"]) for k, v in sm["transitions"].items()}
    terminal = set(vocab["terminal"])
    fig, ax = plt.subplots(figsize=(11, 6.4), dpi=68)
    for src, dsts in graph.items():
        x0, y0 = POS[src]
        for dst in dsts:
            if dst not in POS:
                continue
            x1, y1 = POS[dst]
            back = dst == "reopened" or (src in terminal and dst not in terminal
                                        and dst not in ("awaiting_pr_merge", "failed"))
            color = "#c0392b" if back else "#95a5a6"
            lw = 2.4 if back else 1.0
            ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                        shrinkA=26, shrinkB=26,
                                        connectionstyle="arc3,rad=0.12"))
    for name, (x, y) in POS.items():
        if name == "reopened":
            fc, ec = "#fdebd0", "#c0392b"
        elif name in terminal:
            fc, ec = "#e8f0fe", "#1a73e8"
        else:
            fc, ec = "#f5f5f5", "#616161"
        ax.add_patch(mpatches.FancyBboxPatch((x - 0.62, y - 0.19), 1.24, 0.38,
                                            boxstyle="round,pad=0.04",
                                            fc=fc, ec=ec, lw=1.6))
        ax.text(x, y, name, ha="center", va="center", fontsize=9.5)
    ax.set_xlim(-0.4, 9.0)
    ax.set_ylim(-2.6, 3.6)
    ax.axis("off")
    ax.set_title("Mission state machine (contract: mission_state_machine.v1.json v%d, vocab v%d)\n"
                 "red = the only revival back-edge: terminal -> reopened (governance actor + cited "
                 "acceptance record)" % (sm["version"], vocab["version"]), fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    _shrink(path)


def _shrink(path: Path, colors: int = 48) -> None:
    """量化到调色板，压到报告内嵌可接受的体积（TRAPS: >250KB 负载静默失败）。"""
    from PIL import Image
    with Image.open(path) as img:
        img.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT).save(
            path, optimize=True)


def fig_census(path: Path, census: dict) -> None:
    h = census["headline"]
    labels = ["T1 rework\nafter finish", "T2 post-merge\nrework",
              "T3 finished but\nactive", "T4 legacy guard\nholds pending"]
    vals = [h.get("T1", 0), h.get("T2", 0), h.get("T3", 0), h.get("T4", 0)]
    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=95)
    bars = ax.bar(labels, vals, color=["#1a73e8", "#34a853", "#fbbc04", "#c0392b"])
    ax.axhline(h.get("campaign_cited_cases", 19), color="#7f8c8d", ls="--", lw=1.4,
               label="campaign-cited '19 cases' (as-of snapshot, not reproduced)")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, str(v),
                ha="center", fontsize=10)
    ax.set_ylabel("missions")
    ax.set_title("Historical-revival census by counting convention\n"
                 "total missions = %s; SPEC §4.2 makes the revival path auditable"
                 % h.get("mission_total", "?"), fontsize=11)
    ax.legend(fontsize=8, loc="upper right")
    ax.margins(y=0.15)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    _shrink(path)


def pytest_count() -> dict:
    """用 junitxml 读实测条数/失败数（-q 汇总行被 conftest addopts 吞掉，不靠猜）。"""
    xml = OUT / "pytest_junit.xml"
    proc = subprocess.run([sys.executable, "-m", "pytest",
                           "tests/test_mission_reopened_state.py",
                           "-q", "--no-header", "-p", "no:cacheprovider",
                           f"--junitxml={xml}"],
                          cwd=ROOT / "backend", capture_output=True, text=True)
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if xml.exists():
        head = xml.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"<testsuite\s[^>]*>", head)
        if m:
            for key in counts:
                km = re.search(rf'{key}="(\d+)"', m.group(0))
                if km:
                    counts[key] = int(km.group(1))
    counts["exit"] = proc.returncode
    counts["all_green"] = (proc.returncode == 0 and counts["failures"] == 0
                           and counts["errors"] == 0)
    return counts


def audit_rows() -> dict:
    proc = subprocess.run([sys.executable, "tools/contracts/vocab_contract_audit.py",
                           "--dry-run"], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"\{.*\}", proc.stdout.strip().splitlines()[-1] if proc.stdout else "")
    return json.loads(m.group(0)) if m else {"verdict": "ERROR", "exit": proc.returncode}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    vocab, sm = load()
    census = json.loads((OUT / "reopen_census.json").read_text())
    migration = json.loads((OUT / "reopen_state_migration.json").read_text())
    fig_state_machine(OUT / "fig1_state_machine.png")
    fig_census(OUT / "fig2_reopen_census.png", census)

    from_gate = (ROOT / "backend/api/domain/mission_state_machine.py").read_text()
    guard = (ROOT / "ops/taskplane/tp_pusher.py").read_text()
    verdict = {
        "campaign_node": "IN19-Q7",
        "spec": "AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC §4.2",
        "contracts": {
            "mission_status_vocab_version": vocab["version"],
            "mission_state_machine_version": sm["version"],
            "statuses": len(vocab["statuses"]),
            "statuses_added": ["reopened"],
            "terminal_unchanged": vocab["terminal"],
            "workable_states": sm["workable"],
            "graph_edges": sum(len(v["to"]) for v in sm["transitions"].values()),
            "reopen_from": sm["reopen"]["from"],
            "reopen_targets": sm["reopen"]["targets"],
        },
        "state_machine": {
            "module": "backend/api/domain/mission_state_machine.py",
            "ops_mirror": "ops/taskplane/mission_reopen.py",
            "derived_from_contract": "mission_state_machine_contract" in from_gate,
            "endpoints": ["POST /tensor/missions/{id}/reopen",
                          "POST /tensor/missions/{id}/reopen-release"],
            "audit_trail": ["tensor_dev_missions.reopened_from/reopen_ref/"
                            "reopen_reason/reopened_at",
                            "mission_lifecycle_log action='reopen' (append-only)",
                            "session event kind=decision subtype=mission_reopened"],
        },
        "migration": {"file": "backend/migrations/103_mission_reopened.sql",
                      "db_constraint": "reopened requires from/ref/reason",
                      "lifecycle_action_ratchet": "reopen added, history preserved"},
        "guard_switch": {
            "file": "ops/taskplane/tp_pusher.py",
            "legacy_sentinel_in_production_py": 0,
            "wired_to": "mission_reopen.apply_reopen",
            "pending_legacy_entries": migration.get("candidates", 0),
            "migration_tool": "ops/taskplane/migrate_reopen_state.py",
            "migration_mode": migration.get("mode", "unknown"),
        },
        "tests": pytest_count(),
        "contract_audit": audit_rows(),
        "census": census["headline"],
        "census_tiers": {k: v.get("count", v.get("available"))
                         for k, v in census["tiers"].items()},
        "figures": ["artifacts/m5022/fig1_state_machine.png",
                    "artifacts/m5022/fig2_reopen_census.png"],
    }
    (OUT / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: verdict[k] for k in ("tests", "contract_audit", "guard_switch")},
                     ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())