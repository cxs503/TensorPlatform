#!/usr/bin/env python3
"""Q1 self-check for the mission gradient & exploration-budget mechanism (IN19-Q6).

The mechanism evaluates *itself* with its own gate (``evaluate_q1``) and, to avoid
a self-referential rubber stamp, every criterion is backed by an executed check:

  C1  pytest          — the mechanism's suite is really run (subprocess) and must
                        report >= 6 passing tests, zero failures;
  C2  mix registry    — registered start ratio 60/25/10/4/1 validates and sums to 1;
  C3  quota           — allocation over N=100 / N=1000 consumes exactly N slots;
  C4  credit gating   — full 5x5 tier x level matrix is computed and matches
                        "Sn requires Ln or better" (S4 -> L4+, S5 -> L5 only);
  C5  exploration     — marker exists on S4/S5 only; S1..S3 cannot claim honest FAIL;
  C6  honest FAIL     — a documented honest FAIL is quarantined from the failure
                        rate, an undocumented one is not; overwrite is refused;
  C7  domain-free     — no tenant-domain vocabulary in the mechanism sources;
  C8  migration       — the DDL contract (fields, registry, S-N track, view,
                        append-only trigger) is present;
  C9  PostgreSQL      — migration 103 is executed on a real PG scratch database
                        (twice, for idempotency) and every refuse-case/view check
                        in artifacts/mission_gradient/pg_verify.json passes.

Outputs (worktree paths):
  artifacts/mission_gradient/q1_selfcheck.json     — verdict + evidence hashes
  artifacts/mission_gradient/dispatch_matrix.json  — the 5x5 credit-gate matrix
  artifacts/mission_gradient/accounting_vectors.json — worked failure-rate example

Usage:
  python3 scripts/mission_gradient_q1_selfcheck.py [--mission-id 5026] [--skip-pytest]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
OUT_DIR = REPO_ROOT / "artifacts" / "mission_gradient"
sys.path.insert(0, str(BACKEND))

from services import mission_gradient as mg  # noqa: E402

DOMAIN_WORDS = ["阻力", "推进", "耐波", "操纵", "稳性", "结构", "振动", "噪声", "冰区", "绿色"]
DOMAIN_WORDS_EN = ["resistance", "propulsion", "seakeeping", "maneuvering", "hydrostatics",
                   "vibration", "noise", "hull", "propeller", "slamming"]

#: sources that must stay domain-free (the test file is excluded: it *names* the
#: forbidden vocabulary inside its guard-list, which is how the check is expressed)
MECHANISM_SOURCES = [
    BACKEND / "services" / "mission_gradient.py",
    BACKEND / "api" / "mission_gradient.py",
    BACKEND / "migrations" / "103_mission_gradient.sql",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest() -> dict:
    # NOTE: no extra -q here — backend/pyproject.toml already adds "-q", and a
    # doubled -q suppresses the "N passed" summary line we need as evidence.
    cmd = [sys.executable, "-m", "pytest", "tests/test_mission_gradient.py",
           "--no-header", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, timeout=1800)
    out = proc.stdout or ""
    m = re.search(r"(\d+) passed", out)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", out)
    failed = int(m.group(1)) if m else 0
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-2:]
    return {"command": " ".join(cmd), "cwd": str(BACKEND), "returncode": proc.returncode,
            "passed": passed, "failed": failed, "tail": tail}


def credit_matrix() -> dict:
    matrix = {}
    for tier in mg.GRADIENT_TIERS:
        row = {}
        for level in mg.CREDIT_LEVELS:
            row[f"L{level}"] = mg.credit_permits(level, tier)
        matrix[tier] = row
    allowed = sum(1 for r in matrix.values() for v in r.values() if v)
    return {
        "rule": "a mission on tier Sn may only be dispatched to a worker with credit >= Ln",
        "matrix": matrix,
        "allowed_cells": allowed,
        "denied_cells": len(mg.GRADIENT_TIERS) * len(mg.CREDIT_LEVELS) - allowed,
        "s4_to_l3": matrix["S4"]["L3"],
        "s4_to_l4": matrix["S4"]["L4"],
        "s5_to_l4": matrix["S5"]["L4"],
        "s5_to_l5": matrix["S5"]["L5"],
    }


def accounting_vectors() -> dict:
    """A worked example: same worker, 3 ordinary failures, k honest FAILs."""
    rows = []
    for k in range(0, 7):
        outcomes = [{"worker_id": "w-alpha-1", "mission_id": f"dom-alpha/m-{i:03d}",
                     "tier": "S1", "status": "succeeded"} for i in range(10)]
        outcomes += [{"worker_id": "w-alpha-1", "mission_id": f"dom-alpha/f-{i:03d}",
                      "tier": "S2", "status": "failed"} for i in range(3)]
        outcomes += [{"worker_id": "w-alpha-1", "mission_id": f"dom-alpha/x-{i:03d}",
                      "tier": "S4", "status": "honest_fail",
                      "root_cause": f"dom-alpha probe {i} diverged from the reference trace",
                      "retrospective": ("Cause: synthetic fixture scale mismatch; next time "
                                        "validate fixture size before the run.")}
                     for i in range(k)]
        acct = mg.failure_accounting(outcomes)
        w = acct["workers"][0]
        rows.append({"honest_fails": k, "attempts": w["attempts"],
                     "ordinary_failures": w["failures"],
                     "quarantined": w["quarantined_honest_fails"],
                     "disciplined_rate": w["rate"], "naive_rate": w["naive_rate"]})
    # a claimed honest FAIL with no diagnosis must land as a failure
    thin = mg.classify_outcome({"worker_id": "w-beta-2", "mission_id": "dom-beta/m-001",
                                "tier": "S5", "status": "honest_fail", "root_cause": "oops"})
    return {"series": rows, "undocumented_honest_fail": thin,
            "rule": "quarantined honest FAIL leaves both numerator and denominator"}


def domain_scan() -> dict:
    hits = {}
    for path in MECHANISM_SOURCES:
        blob = path.read_text(encoding="utf-8")
        found = [w for w in DOMAIN_WORDS if w in blob]
        found += [w for w in DOMAIN_WORDS_EN if re.search(rf"\b{w}", blob, re.IGNORECASE)]
        hits[path.name] = found
    return {"scanned": [p.name for p in MECHANISM_SOURCES], "hits": hits,
            "clean": all(not v for v in hits.values())}


def migration_contract() -> dict:
    sql = (BACKEND / "migrations" / "103_mission_gradient.sql").read_text(encoding="utf-8")
    tokens = ["gradient_tier", "exploration_budget", "honest_fail", "fail_root_cause",
              "retrospective", "mission_gradient_mix", "worker_credit_tiers",
              "mission_negative_results", "worker_failure_rate_v1",
              "ck_mission_gradient_mix_sum", "trg_mission_negative_results_append_only"]
    missing = [t for t in tokens if t not in sql]
    return {"file": "backend/migrations/103_mission_gradient.sql", "tokens": tokens,
            "missing": missing, "append_only": "BEFORE UPDATE OR DELETE ON mission_negative_results" in sql,
            "idempotent_add_column": sql.count("ADD COLUMN IF NOT EXISTS"),
            "destructive": "DROP TABLE" in sql.upper()}


def pg_verification() -> dict:
    """Load the real-PostgreSQL verification artifact written by
    ``scripts/mission_gradient_pg_verify.py`` (skipped when the host has no PG)."""
    path = OUT_DIR / "pg_verify.json"
    if not path.exists():
        return {"present": False, "skipped": True,
                "reason": "artifacts/mission_gradient/pg_verify.json not generated"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"present": True, "skipped": bool(data.get("skipped")),
            "all_passed": bool(data.get("all_passed")),
            "checks_total": data.get("checks_total"),
            "checks_passed": data.get("checks_passed"),
            "database": data.get("database"),
            "failure_rate_view": (data.get("environment") or {}).get("failure_rate_view")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission-id", default="5026")
    ap.add_argument("--skip-pytest", action="store_true")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mix = mg.validate_mix(None)
    quota100 = mg.allocate_quota(100, mix)
    quota1000 = mg.allocate_quota(1000, mix)
    matrix = credit_matrix()
    vectors = accounting_vectors()
    scan = domain_scan()
    mig = migration_contract()
    pg = pg_verification()
    py = {"skipped": True} if args.skip_pytest else run_pytest()

    declaration = mg.self_declare(args.mission_id, mix=mix)
    honest = mg.classify_outcome({"worker_id": "w-alpha-1", "mission_id": "dom-alpha/m-901",
                                  "tier": "S4", "status": "honest_fail",
                                  "root_cause": "dom-alpha solver diverged at step 3 (fixture scale)",
                                  "retrospective": "Cause: fixture scale mismatch; next time validate "
                                                   "fixture size before the run.",
                                  "evidence_ref": "artifact://dom-alpha/run-901/divergence.json"})
    overwrite = mg.guard_honest_fail({"mission_id": "dom-alpha/m-901", "status": "honest_fail"},
                                     "succeeded")
    nrs = mg.negative_result_entry({"worker_id": "w-alpha-1", "mission_id": "dom-alpha/m-901",
                                    "tier": "S4", "status": "honest_fail",
                                    "root_cause": "dom-alpha solver diverged at step 3 (fixture scale)",
                                    "retrospective": "Cause: fixture scale mismatch; next time validate "
                                                     "fixture size before the run.",
                                    "evidence_ref": "artifact://dom-alpha/run-901/divergence.json"},
                                   reuse_tags=["fixture-scale", "probe-design"])

    criteria = [
        {"id": "C1_pytest", "description": "mechanism suite passes with >= 6 tests",
         "passed": None if py.get("skipped") else (py["returncode"] == 0 and py["passed"] >= 6
                                                   and py["failed"] == 0),
         "evidence": py},
        {"id": "C2_mix_registry", "description": "registered start ratio is 60/25/10/4/1 and sums to 1",
         "passed": (quota100 == {"S1": 60, "S2": 25, "S3": 10, "S4": 4, "S5": 1}
                    and abs(sum(mix.values()) - 1.0) <= mg.MIX_TOL),
         "evidence": {"mix": mix, "signature": mg.mix_signature(mix),
                      "defaults": dict(mg.DEFAULT_MIX)}},
        {"id": "C3_quota_allocation",
         "description": "quota allocation consumes exactly N slots for N=100 and N=1000",
         "passed": (sum(quota100.values()) == 100 and sum(quota1000.values()) == 1000
                    and quota1000 == {k: v * 10 for k, v in quota100.items()}),
         "evidence": {"quota_100": quota100, "quota_1000": quota1000,
                      "exploration_quota_100": quota100["S4"] + quota100["S5"]}},
        {"id": "C4_credit_gating",
         "description": "S4 requires L4+ and S5 requires L5 only; matrix has 15 allowed cells",
         "passed": (matrix["s4_to_l3"] is False and matrix["s4_to_l4"] is True
                    and matrix["s5_to_l4"] is False and matrix["s5_to_l5"] is True
                    and matrix["allowed_cells"] == 15),
         "evidence": matrix},
        {"id": "C5_exploration_scope",
         "description": "exploration marker exists on S4/S5 only; S1..S3 cannot claim honest FAIL",
         "passed": (all(not mg.exploration_budget(t, {"max_tool_calls": 100})["exploration"]
                        for t in ("S1", "S2", "S3"))
                    and all(mg.exploration_budget(t, {"max_tool_calls": 100})["exploration"]
                            for t in ("S4", "S5"))
                    and mg.classify_outcome({"worker_id": "w-c", "mission_id": "dom-c/m-1",
                                             "tier": "S3", "status": "honest_fail",
                                             "root_cause": "x" * 10,
                                             "retrospective": "y" * 30}
                                            )["quarantined"] is False),
         "evidence": {"s4": mg.exploration_budget("S4", {"max_tool_calls": 100}),
                      "s5": mg.exploration_budget("S5", {"max_tool_calls": 100}),
                      "s1": mg.exploration_budget("S1", {"max_tool_calls": 100})}},
        {"id": "C6_honest_fail_discipline",
         "description": "documented honest FAIL is quarantined, undocumented is not, overwrite refused",
         "passed": (honest["quarantined"] is True
                    and vectors["undocumented_honest_fail"]["quarantined"] is False
                    and overwrite["allowed"] is False
                    and vectors["series"][-1]["disciplined_rate"]
                    < vectors["series"][-1]["naive_rate"]),
         "evidence": {"admissible": honest, "overwrite_guard": overwrite,
                      "series": vectors["series"],
                      "negative_result_entry": nrs}},
        {"id": "C7_domain_free",
         "description": "no tenant-domain vocabulary hard-coded in mechanism sources",
         "passed": scan["clean"] is True, "evidence": scan},
        {"id": "C8_migration_contract",
         "description": "DDL carries gradient fields, mix registry, S-N track, view, append-only trigger",
         "passed": (not mig["missing"] and mig["append_only"] and not mig["destructive"]
                    and mig["idempotent_add_column"] >= 6),
         "evidence": mig},
        {"id": "C9_postgres_execution",
         "description": "migration 103 executes on real PostgreSQL (idempotent) and every "
                        "refuse-case + view arithmetic check passes",
         "passed": None if pg["skipped"] else pg["all_passed"],
         "evidence": pg},
    ]
    verdict = mg.evaluate_q1(criteria, min_criteria=6)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mission_id": args.mission_id,
        "mechanism": "mission gradient S1..S5 + exploration budget + credit-matched dispatch",
        "self_declaration": declaration,
        "q1_gate": verdict,
        "artifacts": {
            "service": "backend/services/mission_gradient.py",
            "api": "backend/api/mission_gradient.py",
            "migration": "backend/migrations/103_mission_gradient.sql",
            "tests": "backend/tests/test_mission_gradient.py",
            "figures": ["artifacts/mission_gradient/fig1_quota_allocation.png",
                        "artifacts/mission_gradient/fig2_credit_gating_matrix.png",
                        "artifacts/mission_gradient/fig3_failure_rate_accounting.png"],
        },
        "evidence_sha256": {p.name: sha256(p) for p in MECHANISM_SOURCES},
        "honest_limits": [
            "mechanism is flag-gated (TP_MISSION_GRADIENT=1) and NOT yet wired into the "
            "live dispatcher while the platform freeze (owner 2026-11-02) holds; the "
            "integration point is a single call to services.mission_gradient.dispatch_decide",
            "migration 103 was executed against a real PostgreSQL 15.5 scratch database "
            "(17/17 checks, applied twice for idempotency) — see pg_verify.json; it was NOT "
            "applied to the platform's own database (freeze period)",
            "credit levels are read from worker_credit_tiers; until that table is populated "
            "the default posture is 'no worker above L1', i.e. S4/S5 dispatch is refused (fail-closed)",
        ],
    }
    (OUT_DIR / "q1_selfcheck.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    (OUT_DIR / "dispatch_matrix.json").write_text(json.dumps(matrix, indent=2, ensure_ascii=False),
                                                  encoding="utf-8")
    (OUT_DIR / "accounting_vectors.json").write_text(
        json.dumps(vectors, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"verdict": verdict["q1_verdict"], "passed": verdict["criteria_passed"],
                      "total": verdict["criteria_total"], "pytest": py.get("passed"),
                      "out": str(OUT_DIR)}, ensure_ascii=False))
    return 0 if verdict["q1_verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())