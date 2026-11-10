#!/usr/bin/env python3
"""Tenant isolation v1 acceptance harness (machine-checkable criteria C1-C7).

Runs the synthetic case batteries against the real mechanism
(``backend/services/tenant_isolation``), writes an evidence JSON with every case
decision, and (optionally) renders the summary figures used by the report.

Usage
-----
    python3 tools/audit/tenant_isolation_validation.py                       # full run
    python3 tools/audit/tenant_isolation_validation.py --skip-regression     # mechanism only
    python3 tools/audit/tenant_isolation_validation.py --json out.json --figures

Exit code 0 iff every criterion passes; 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.tenant_isolation import DomainTextGate, IsolationEngine  # noqa: E402
from services.tenant_isolation.registry import default_registry_path  # noqa: E402

MECHANISM_DIR = ROOT / "backend" / "services" / "tenant_isolation"
MECHANISM_TEST = ROOT / "backend" / "tests" / "test_tenant_isolation_v2.py"
MECHANISM_API = ROOT / "backend" / "api" / "tenant_isolation.py"
MECHANISM_TOOL = ROOT / "tools" / "audit" / "tenant_isolation_validation.py"
DENYLIST = ROOT / "config" / "domain_word_denylist.json"
DEFAULT_OUT = ROOT / "artifacts" / "tenant_isolation" / "p337w1_n02_validation.json"
FIG_DIR = ROOT / "artifacts" / "tenant_isolation" / "figures"

REGRESSION_TARGETS = [
    "backend/tests/test_tenant_isolation.py",
    "backend/tests/test_mission_gradient.py",
    "backend/tests/test_mission_reopened_state.py",
    "backend/tests/test_missions_api_boundaries.py",
    "backend/tests/test_report_gate.py",
    "backend/tests/test_report_gate_standalone.py",
    "backend/tests/test_report_reconcile.py",
]


def _cases_quota(engine: IsolationEngine, tenant: str) -> List[Dict[str, Any]]:
    rows = [
        ("cpu_core_hours", 1e9, "quota_exceeded"),
        ("memory_gb_hours", 1e9, "quota_exceeded"),
        ("storage_gb", 1e9, "quota_exceeded"),
        ("concurrent_tasks", 1e6, "quota_exceeded"),
        ("periodic_tasks", 1e6, "quota_exceeded"),
        ("cpu_core_hours", -5, "invalid_amount"),
        ("cpu_core_hours", 0, "invalid_amount"),
        ("cpu_core_hours", float("nan"), "invalid_amount"),
        ("resource_not_declared", 1, "unknown_resource"),
        ("concurrent_tasks", 1.5, "fractional_gauge"),
    ]
    out = []
    for i, (res, amount, expected) in enumerate(rows, 1):
        d = engine.budget.claim(tenant, res, amount, cycle="acceptance")
        out.append(
            {
                "case_id": f"C2-{i:02d}",
                "tenant": tenant,
                "resource": res,
                "amount": None if amount != amount else amount,
                "expected_reason": expected,
                "observed_reason": d.reason,
                "rejected": not d.accepted,
                "pass": (not d.accepted) and d.reason == expected,
            }
        )
    return out


def _cases_write(engine: IsolationEngine, tenant: str, other: str, ws: Path) -> List[Dict[str, Any]]:
    repo_t, repo_o = engine.registry.repo_of(tenant), engine.registry.repo_of(other)
    (ws / repo_t).mkdir(parents=True, exist_ok=True)
    (ws / repo_o).mkdir(parents=True, exist_ok=True)
    (ws / "shared").mkdir(parents=True, exist_ok=True)
    (ws / repo_t / "jump_to_other").symlink_to(ws / repo_o)
    (ws / repo_t / "jump_out").symlink_to(ws.parent)
    rows = [
        (f"{repo_o}/pkg/mod.py", "cross_tenant_write"),
        (f"{repo_t}/../{repo_o}/pkg/mod.py", "path_traversal"),
        (str(ws / repo_o / "deep" / "x.py"), "cross_tenant_write"),
        (str(ws.parent / "elsewhere" / "x.py"), "outside_tenant_namespace"),
        ("shared/x.py", "shared_root_read_only"),
        (f"{repo_t}/jump_to_other/x.py", "cross_tenant_write"),
        (f"{repo_t}/jump_out/x.py", "outside_tenant_namespace"),
        (f"{repo_o}/./x.py", "cross_tenant_write"),
        (f"{repo_o}/", "cross_tenant_write"),
        (f"../{repo_o}/x.py", "path_traversal"),
    ]
    out = []
    for i, (path, expected) in enumerate(rows, 1):
        d = engine.paths.check_write(tenant, path)
        out.append(
            {
                "case_id": f"C3-{i:02d}",
                "tenant": tenant,
                "target": path,
                "owner": d.owner,
                "expected_reason": expected,
                "observed_reason": d.reason,
                "rejected": not d.allowed,
                "pass": (not d.allowed) and d.reason == expected,
            }
        )
    return out


def _cases_domain(engine: IsolationEngine) -> List[Dict[str, Any]]:
    lex = json.loads(DENYLIST.read_text(encoding="utf-8"))
    ascii_words, cjk_words = lex["ascii_words"], lex["cjk_words"]
    payloads = {
        "standalone_token": f"LABEL = '{ascii_words[0]}'\n",
        "mixed_case": f"note = \"{ascii_words[1].upper()} suite\"\n",
        "json_value": json.dumps({"tag": ascii_words[2]}),
        "comment_only": f"# {ascii_words[3]} handled elsewhere\ndef g():\n    return 2\n",
        "cjk_embedded": f"TITLE = '关于{cjk_words[0]}的说明'\n",
    }
    gate = engine.domain_gate()
    out = []
    for i, (label, text) in enumerate(payloads.items(), 1):
        v = gate.check_submission({f"backend/pkg/{label}.py": text})
        out.append(
            {
                "case_id": f"C4-{i:02d}",
                "label": label,
                "hits": v.total_hits,
                "verdict_allowed": v.allowed,
                "rejected": not v.allowed,
                "pass": (not v.allowed) and v.total_hits > 0,
            }
        )
    return out


def _cases_data(engine: IsolationEngine, tenant: str, other: str) -> List[Dict[str, Any]]:
    engine.artifacts.write(tenant, f"{tenant}/runs/out.json", "{}")
    engine.reports.write(tenant, f"{tenant}/r1/report.md", "# r1")
    private_event = engine.events.publish(tenant, "run.finished", {"k": 1})
    rows = [
        ("artifact_read", engine.artifacts.read(other, f"{tenant}/runs/out.json")),
        ("artifact_traversal", engine.artifacts.read(other, f"../{tenant}/runs/out.json")),
        ("report_read", engine.reports.read(other, f"{tenant}/r1/report.md")),
        ("event_read", engine.events.read_event(other, private_event)),
        ("artifact_missing_after_deny", engine.artifacts.read(other, f"{tenant}/runs/absent.json")),
    ]
    out = []
    for i, (label, d) in enumerate(rows, 1):
        out.append(
            {
                "case_id": f"C5-{i:02d}",
                "label": label,
                "target": d.target,
                "owner": d.owner,
                "observed_reason": d.reason,
                "rejected": not d.allowed,
                "pass": not d.allowed,
            }
        )
    return out


def _rate(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(cases)
    rejected = sum(1 for c in cases if c["rejected"])
    return {
        "cases": total,
        "rejected": rejected,
        "rejection_rate": (rejected / total) if total else 0.0,
        "all_pass": all(c["pass"] for c in cases) if cases else False,
    }


def _zero_domain() -> Dict[str, Any]:
    gate = DomainTextGate.from_config()
    files = sorted(MECHANISM_DIR.glob("*.py")) + [MECHANISM_TEST, MECHANISM_API, MECHANISM_TOOL]
    rel = [str(f.relative_to(ROOT)) for f in files]
    v = gate.check_paths(rel)
    return {
        "files": rel,
        "files_checked": v.files_checked,
        "hits": v.total_hits,
        "violations": v.violations[:10],
        "pass": v.total_hits == 0 and v.files_checked >= len(files),
    }


def _regression() -> Dict[str, Any]:
    """Run the existing mission/event/report suites; counts come from junit-xml."""
    import xml.etree.ElementTree as ET

    with tempfile.TemporaryDirectory(prefix="regression_") as tmp:
        xml_path = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", *REGRESSION_TARGETS,
               "-q", "--tb=no", "-p", "no:cacheprovider", f"--junit-xml={xml_path}"]
        started = time.time()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        duration = round(time.time() - started, 2)
        counts = {"tests": 0, "failed": 0, "error": 0, "skipped": 0}
        if xml_path.is_file():
            tree = ET.parse(xml_path)
            for suite in tree.iter("testsuite"):
                counts["tests"] += int(suite.get("tests") or 0)
                counts["failed"] += int(suite.get("failures") or 0)
                counts["error"] += int(suite.get("errors") or 0)
                counts["skipped"] += int(suite.get("skipped") or 0)
        counts["passed"] = counts["tests"] - counts["failed"] - counts["error"] - counts["skipped"]
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-3:]
    return {
        "command": " ".join(cmd),
        "targets": REGRESSION_TARGETS,
        "exit_code": proc.returncode,
        "stdout_tail": tail,
        "counts": counts,
        "duration_s": duration,
        "pass": proc.returncode == 0
        and counts["tests"] > 0
        and counts["failed"] == 0
        and counts["error"] == 0,
    }


def _figures(result: Dict[str, Any]) -> List[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"[figures] matplotlib unavailable: {exc}", file=sys.stderr)
        return []
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    labels, rates = [], []
    for cid, block in result["criteria"].items():
        if "rejection_rate" in block:
            labels.append(cid)
            rates.append(block["rejection_rate"] * 100.0)
    paths = []
    fig1 = FIG_DIR / "rejection_rates.png"
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    bars = ax.bar(labels, rates, color="#2f6f9f")
    ax.axhline(100.0, color="#c0392b", ls="--", lw=1, label="target 100%")
    ax.set_ylim(0, 112)
    ax.set_ylabel("rejection rate [%]")
    ax.set_title("Tenant isolation v1 acceptance (synthetic cases)")
    for b, r in zip(bars, rates):
        ax.text(b.get_x() + b.get_width() / 2, r + 2, f"{r:.0f}%", ha="center", fontsize=9)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(fig1, dpi=160)
    plt.close(fig)
    paths.append(str(fig1.relative_to(ROOT)))

    claims = result.get("claim_breakdown", [])
    if claims:
        fig2 = FIG_DIR / "claim_decisions.png"
        resources = [c["resource"] for c in claims]
        accepted = [c["accepted"] for c in claims]
        rejected = [c["rejected"] for c in claims]
        fig, ax = plt.subplots(figsize=(6.8, 3.8))
        ax.bar(resources, accepted, label="accepted", color="#2e8b57")
        ax.bar(resources, rejected, bottom=accepted, label="rejected", color="#c0392b")
        ax.set_ylabel("claims [count]")
        ax.set_title("Budget admission outcomes by resource (acceptance cycle)")
        ax.legend(fontsize=8)
        plt.setp(ax.get_xticklabels(), rotation=20, ha="right", fontsize=8)
        fig.tight_layout()
        fig.savefig(fig2, dpi=160)
        plt.close(fig)
        paths.append(str(fig2.relative_to(ROOT)))
    return paths


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(DEFAULT_OUT))
    ap.add_argument("--skip-regression", action="store_true")
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args(argv)

    started = time.time()
    with tempfile.TemporaryDirectory(prefix="tenant_isolation_") as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir()
        engine = IsolationEngine(workspace_root=ws)
        names = engine.tenant_names()
        tenant, other = names[0], names[1]

        # control (must be accepted) so a blanket-deny implementation cannot pass
        control_quota = engine.budget.claim(tenant, "cpu_core_hours", 1.0, cycle="control")
        control_write = engine.paths.check_write(tenant, f"{engine.registry.repo_of(tenant)}/ok.py")
        control_read = engine.paths.check_read(tenant, f"{engine.registry.repo_of(tenant)}/ok.py")
        control_domain = engine.domain_gate().check_submission({"backend/pkg/ok.py": "def h():\n    return 3\n"})
        control_artifact = engine.artifacts.write(tenant, f"{tenant}/ctrl/ok.txt", "x")
        control_artifact_read = engine.artifacts.read(tenant, f"{tenant}/ctrl/ok.txt")

        c2 = _cases_quota(engine, tenant)
        c3 = _cases_write(engine, tenant, other, ws)
        c4 = _cases_domain(engine)
        c5 = _cases_data(engine, tenant, other)
        c7 = _zero_domain()
        completeness = engine.registry.completeness()

        result: Dict[str, Any] = {
            "node": "p337w1-n02-tenant-isolation",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "registry_path": str(default_registry_path().relative_to(ROOT)),
            "mechanism_module": "backend/services/tenant_isolation",
            "tenant_pair_used": [tenant, other],
            "criteria": {},
            "controls": {
                "quota_accept": control_quota.to_dict(),
                "write_own_repo": control_write.to_dict(),
                "read_own_repo": control_read.to_dict(),
                "domain_clean": control_domain.to_dict(),
                "artifact_write": control_artifact.to_dict(),
                "artifact_read": control_artifact_read.to_dict(),
            },
            "cases": {"C2": c2, "C3": c3, "C4": c4, "C5": c5},
        }

        result["criteria"]["C1"] = {
            "tenant_count": completeness["tenant_count"],
            "complete": completeness["complete"],
            "ledger_entries": len(engine.store.ledger_query(item_type="tenant_registration")),
            "pass": completeness["complete"]
            and completeness["tenant_count"] == 12
            and len(engine.store.ledger_query(item_type="tenant_registration")) == 12,
        }
        result["criteria"]["C2"] = _rate(c2)
        result["criteria"]["C2"]["pass"] = result["criteria"]["C2"]["all_pass"] and control_quota.accepted
        result["criteria"]["C3"] = _rate(c3)
        result["criteria"]["C3"]["pass"] = result["criteria"]["C3"]["all_pass"] and control_write.allowed
        result["criteria"]["C4"] = _rate(c4)
        result["criteria"]["C4"]["pass"] = result["criteria"]["C4"]["all_pass"] and control_domain.allowed
        result["criteria"]["C5"] = _rate(c5)
        result["criteria"]["C5"]["pass"] = (
            result["criteria"]["C5"]["all_pass"] and control_artifact_read.allowed
        )
        result["criteria"]["C7"] = c7

        if args.skip_regression:
            result["criteria"]["C6"] = {"skipped": True, "pass": None}
        else:
            result["criteria"]["C6"] = _regression()

        # claim outcome mix for the figure + audit
        rows = engine.store.query(
            "SELECT resource, decision, COUNT(*) AS n FROM tenant_claim GROUP BY resource, decision"
        )
        mix: Dict[str, Dict[str, int]] = {}
        for r in rows:
            mix.setdefault(r["resource"], {"accepted": 0, "rejected": 0})
            key = "accepted" if r["decision"] == "ACCEPT" else "rejected"
            mix[r["resource"]][key] += int(r["n"])
        result["claim_breakdown"] = [{"resource": k, **v} for k, v in sorted(mix.items())]
        result["budget_rejection_rate"] = engine.budget.rejection_rate()
        result["access_log_entries"] = len(engine.store.access_entries())
        result["duration_s"] = round(time.time() - started, 2)

        passed = all(
            (b.get("pass") is True) for b in result["criteria"].values() if b.get("pass") is not None
        )
        result["all_criteria_pass"] = bool(passed) and not result["criteria"]["C6"].get("skipped", False)
        engine.store.close()

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.figures:
        result["figures"] = _figures(result)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({k: (v if k != "cases" else {kk: len(vv) for kk, vv in v.items()})
                      for k, v in result.items() if k in ("criteria", "controls", "all_criteria_pass", "cases")},
                     indent=2, ensure_ascii=False, default=str))
    print(f"[written] {out}")
    return 0 if result["all_criteria_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())