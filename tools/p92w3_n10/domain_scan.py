"""Zero-domain-semantics sweep over every file this node touches.

Uses the platform's single scanner (``api.domain.delivery_state.scan_domain_semantics``)
so the check cannot drift from the classifier's own contract.
Run: python3 tools/p92w3_n10/domain_scan.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api.domain.delivery_state import scan_domain_semantics  # noqa: E402

TARGETS = [
    "backend/api/domain/delivery_state.py",
    "backend/api/kpi_delivery.py",
    "backend/tests/test_p92w3_n10_layering.py",
    "ops/taskplane/mvd_gate.py",
    "ops/taskplane/tp_worker.py",
    "tools/p92w3_n10/evaluate.py",
    "tools/p92w3_n10/adjudicate.py",
    "tools/p92w3_n10/fetch_missions.py",
    "tools/p92w3_n10/shadow_golden.py",
    "tools/p92w3_n10/acceptance.py",
    "tools/p92w3_n10/figures.py",
]


#: Pre-existing domain vocabulary that belongs to the *domain-facing agent prompt*
#: (the tool loop is shared platform machinery; its system prompt is the subject
#: matter layer). For these files the acceptance measure is "no NEW domain term",
#: measured against the file as committed at HEAD.
PROMPT_FILES = {"ops/taskplane/tp_worker.py"}


def _hits_at_head(rel: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
                             capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return scan_domain_semantics(out.stdout)


def _region(source: str) -> str:
    """The gate wiring window inside the worker (what this node added)."""
    marker = "mvd_gate.evaluate_heartbeat("
    if marker not in source:
        return ""
    start = source.index(marker)
    return source[max(0, start - 1200):start + 1200]


def main() -> None:
    report = {"scanner": "api.domain.delivery_state.scan_domain_semantics",
              "files": [], "total_hits": 0, "total_new_hits": 0}
    for rel in TARGETS:
        path = ROOT / rel
        if not path.exists():
            report["files"].append({"path": rel, "hits": ["MISSING"], "exists": False,
                                    "new_hits": ["MISSING"]})
            report["total_hits"] += 1
            report["total_new_hits"] += 1
            continue
        hits = scan_domain_semantics(path.read_text(encoding="utf-8"))
        entry = {"path": rel, "hits": hits, "exists": True}
        if rel in PROMPT_FILES:
            baseline = _hits_at_head(rel)
            new = sorted(set(hits) - set(baseline))
            entry.update({"hits_at_head": baseline, "new_hits": new,
                          "scope": "domain-facing prompt content; measured as new-term delta",
                          "mechanism_region_hits": scan_domain_semantics(
                              _region(path.read_text(encoding="utf-8"))) })
        else:
            entry["new_hits"] = hits
        report["files"].append(entry)
        report["total_hits"] += len(hits)
        report["total_new_hits"] += len(entry["new_hits"]) + len(entry.get("mechanism_region_hits", []))
    out = ROOT / "runs/p92w3-n10/domain_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total_hits": report["total_hits"],
                      "total_new_hits": report["total_new_hits"],
                      "per_file": {f["path"]: f["hits"] for f in report["files"]}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()