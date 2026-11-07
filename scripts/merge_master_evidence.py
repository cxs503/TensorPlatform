#!/usr/bin/env python3
"""把合并口的运行产物聚成一份「可引用」的证据摘要（数字零手抄）。

    python3 scripts/merge_master_evidence.py --pytest-passed 17

读 artifacts/merge_master/*.json + audit.jsonl，输出 evidence_summary.json：
每轮判决计数、每张 PR 的处置与理由、门判决统计、审计链校验、边界自检。
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(HERE, "artifacts", "merge_master")
RUNS = [("plan_live.json", "plan_live_baseline"),
        ("rehearsal.json", "rehearsal_execute_path"),
        ("ablation_no_boundary.json", "ablation_boundary_disabled")]
TOOL_FILES = [
    "tools/merge_master/__init__.py", "tools/merge_master/model.py",
    "tools/merge_master/audit.py", "tools/merge_master/gates.py",
    "tools/merge_master/engine.py", "tools/merge_master/providers.py",
    "tools/merge_master/cli.py", "tools/merge_master/rollback.py",
    "tools/merge_master/rehearsal.py", "tests/test_merge_master.py",
    "ops/merge_master/daemon.sh", "deploy/merge_master/compose.yml",
]


def load(p):
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def selfscan():
    cmd = [sys.executable, "tools/boundary/pr_gate_scan.py", "paths",
           *TOOL_FILES, "--format", "json"]
    p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    try:
        rep = json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "error": p.stderr[-300:]}
    return {"ok": rep.get("verdict") == "PASS",
            "violations": len(rep.get("violations") or []),
            "totals": rep.get("totals") or {},
            "lexicon": rep.get("lexicon", {}).get("sha256", "")[:12]}


def audit():
    path = os.path.join(ART, "audit.jsonl")
    kinds = collections.Counter()
    if os.path.exists(path):
        for line in open(path, "r", encoding="utf-8"):
            line = line.strip()
            if line:
                kinds[json.loads(line)["kind"]] += 1
    p = subprocess.run([sys.executable, "-m", "tools.merge_master.cli",
                        "verify-audit", "--audit", path],
                       cwd=HERE, capture_output=True, text=True)
    try:
        v = json.loads(p.stdout or "{}")
    except ValueError:
        v = {"ok": False, "error": p.stderr[-300:]}
    return {"kind_counts": dict(kinds), "total_records": sum(kinds.values()),
            "chain_verified": v.get("ok"), "head": (v.get("head") or "")[:16]}


def main() -> int:
    pytest_passed = 0
    if "--pytest-passed" in sys.argv:
        pytest_passed = int(sys.argv[sys.argv.index("--pytest-passed") + 1])
    out = {"schema": "tp.merge_master.evidence/v1",
           "pytest_passed": pytest_passed, "audit": audit(),
           "selfscan": selfscan(), "runs": {}}
    gate_tally = collections.Counter()
    gate_fail = collections.Counter()
    pr_rows = {}
    for fname, label in RUNS:
        path = os.path.join(ART, fname)
        if not os.path.exists(path):
            continue
        rep = load(path)
        rows = []
        for dec in rep["decisions"]:
            pr = "%s#%d" % (dec["pr"]["repo"], dec["pr"]["number"])
            rows.append({"pr": pr, "action": dec["action"],
                         "reasons": dec["reasons"],
                         "mission_id": dec["mission_id"],
                         "mission_status": dec["mission_status"],
                         "merge_commit": dec["merge_commit"],
                         "rollback": bool(dec.get("rollback"))})
            pr_rows.setdefault(pr, {})[label] = dec["action"]
            for g in dec["gates"]:
                gate_tally[g["gate"]] += 1
                if not g["ok"]:
                    gate_fail[g["gate"]] += 1
        out["runs"][label] = {"file": fname, "counts": rep["counts"],
                              "budget": rep["budget"],
                              "audit_seq": (rep.get("audit") or {}).get("seq"),
                              "decisions": rows}
    out["gates"] = {g: {"evaluated": gate_tally[g], "failed": gate_fail[g]}
                    for g in sorted(gate_tally)}
    out["pr_disposition"] = pr_rows
    path = os.path.join(ART, "evidence_summary.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"out": path, "runs": {k: v["counts"]
                                            for k, v in out["runs"].items()},
                      "gates": out["gates"], "audit": out["audit"],
                      "selfscan": {k: out["selfscan"][k]
                                   for k in ("ok", "violations")}},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())