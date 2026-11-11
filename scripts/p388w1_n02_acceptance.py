#!/usr/bin/env python3
"""P388W1-N02 acceptance runner: audit + evidence verify + pytest, one JSON output.

Every number in ``artifacts/invariant_audit/run_meta.json`` is produced here by
actually executing the tools (no hand-copied values), so the report can cite the
file and a reviewer can re-run this script in a fresh checkout.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.invariant_audit.bundle import verify_bundle, write_bundle  # noqa: E402
from tools.invariant_audit.engine import load_plan_file, run_audit  # noqa: E402

PLAN = REPO / "config" / "invariant_audit" / "plans" / "synthetic_solver_matrix.json"
OUT = REPO / "artifacts" / "invariant_audit" / "run_matrix"
META = REPO / "artifacts" / "invariant_audit" / "run_meta.json"


def _pytest_summary() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/invariant_audit", "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True)
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    summary = tail[-1] if tail else ""
    passed = re.search(r"(\d+) passed", summary)
    failed = re.search(r"(\d+) failed", summary)
    skipped = re.search(r"(\d+) skipped", summary)
    return {
        "command": "python3 -m pytest tests/invariant_audit -q",
        "exit_code": proc.returncode,
        "summary": summary,
        "passed": int(passed.group(1)) if passed else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "skipped": int(skipped.group(1)) if skipped else 0,
    }


def _neutrality_scan() -> dict:
    vocab = json.loads((REPO / "config" / "invariant_audit" / "neutrality_vocab.json")
                       .read_text(encoding="utf-8"))
    tokens = vocab["forbidden_in_mechanism_code"]
    files = [REPO / rel for rel in vocab["scope"]["paths"]]
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if re.search(rf"\b{re.escape(token)}\b", text, flags=re.IGNORECASE):
                hits.append(f"{path.name}:{token}")
    return {"mechanism_files_scanned": len(files), "forbidden_tokens": len(tokens),
            "hits": hits}


def main() -> int:
    plan = load_plan_file(PLAN)
    report = run_audit(plan)
    manifest = write_bundle(report, plan, OUT)
    verdict = verify_bundle(OUT)

    pairs = report.pair_counts()
    targets = report.counts()
    meta = {
        "node": "p388w1-n02-solver-invariant-audit",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "plan": str(PLAN.relative_to(REPO)),
        "plan_digest": report.plan_digest,
        "bundle": str(OUT.relative_to(REPO)),
        "bundle_root_digest": manifest["root_digest"],
        "bundle_entries": len(manifest["entries"]),
        "bundle_verify": verdict,
        "counts_targets": targets,
        "counts_pairs": pairs,
        "invariant_pairs": len(report.pairs()),
        "targets": len(report.targets),
        "kinds": sorted({inv.kind.value for t in report.targets for inv in t.invariants}),
        "pytest": _pytest_summary(),
        "neutrality": _neutrality_scan(),
        "acceptance": {
            "A1_zero_domain_semantics": None,   # filled below
            "A2_invariant_families": None,
            "A3_sensitivity_all_planted_defects_flagged": None,
            "A4_specificity_no_false_fail": None,
            "A5_withheld_not_a_pass": None,
            "A6_evidence_chain_verifies": verdict["ok"],
            "A7_pytest_green": None,
        },
    }
    neutrality = meta["neutrality"]
    meta["acceptance"]["A1_zero_domain_semantics"] = (not neutrality["hits"]
                                                     and neutrality["mechanism_files_scanned"] > 0)
    meta["acceptance"]["A2_invariant_families"] = len(meta["kinds"]) >= 4
    planted = {"synth-grid-interpolating", "synth-response-grid-aligned",
               "synth-boost-heuristic", "synth-postproc-unit-bug",
               "synth-postproc-formula-bug"}
    flagged = {t.target_id for t in report.targets if t.state.value == "FAIL"}
    meta["acceptance"]["A3_sensitivity_all_planted_defects_flagged"] = planted <= flagged
    meta["acceptance"]["A4_specificity_no_false_fail"] = flagged == planted
    withheld = [t for t in report.targets if t.state.value == "WITHHELD"]
    meta["acceptance"]["A5_withheld_not_a_pass"] = bool(withheld) and all(
        inv.residual is None for t in withheld for inv in t.invariants
        if inv.state.value == "WITHHELD")
    meta["acceptance"]["A7_pytest_green"] = (meta["pytest"]["exit_code"] == 0
                                             and meta["pytest"]["failed"] == 0)

    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(meta["acceptance"], sort_keys=True))
    print(json.dumps({"pair_counts": pairs, "target_counts": targets,
                      "root_digest": manifest["root_digest"],
                      "pytest": meta["pytest"]["summary"]}, sort_keys=True))
    all_ok = all(bool(v) for v in meta["acceptance"].values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())