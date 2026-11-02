#!/usr/bin/env python3
"""Capability card validation gate (schema + evidence anchor).

mission 1819 rewrote the evidence half of this gate.  Before:

    def validate_report_id_exists(report_id, known_reports=None):
        # "In production, this queries the database"
        valid_ids = {"e2e1", "suboff_sail", "wave10_multiphase",
                     "dtmb5415_geometry", "report_gate_v1", "m597"}

That is a *hardcoded whitelist of the very five labels the cards invented* — the
gate could not distinguish a real platform report from a fabricated one, and it
rejected genuine report ids (e.g. "1375") that were not in the list.  The
red-team demo built on it was tautological for the same reason.

Now every card's evidence must resolve through
`tools/audit/capability_card_evidence.py` against the pinned report snapshot
(E2/E3/E4/E5/E7): a numeric report id that exists in the snapshot, digests
recomputed from the pinned bytes, a verbatim quote that hits, and no PASS on top
of a failure quote.

Usage:
    python3 scripts/validate_capability_card.py <card.json>
    python3 scripts/validate_capability_card.py --batch
    python3 scripts/validate_capability_card.py --red-team
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = WORK_DIR / "docs" / "capability_card.schema.json"
CARDS_DIR = WORK_DIR / "docs" / "capability_registry" / "cards"
RED_TEAM_PATH = WORK_DIR / "docs" / "capability_registry" / "red_team" / "red_team_demo.json"
EVIDENCE_TOOL = WORK_DIR / "tools" / "audit" / "capability_card_evidence.py"
SNAPSHOT = WORK_DIR / "tests" / "fixtures" / "capability_evidence" / "reports_snapshot.json"


def _evidence():
    spec = importlib.util.spec_from_file_location("capability_card_evidence", EVIDENCE_TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capability_card_evidence"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_schema(card, schema):
    """Structural validation (required fields, basic patterns)."""
    import re

    errors = []
    for field in schema.get("required", []):
        if field not in card:
            errors.append(f"Missing required field: {field}")
    if "card_id" in card and not re.match(r"^CAP-[A-Za-z0-9_-]+$", card["card_id"]):
        errors.append(f"Invalid card_id format: {card['card_id']} (expected CAP-XXX)")
    ve = card.get("validation_evidence")
    if isinstance(ve, dict):
        for field in (schema["properties"]["validation_evidence"].get("required") or []):
            if field not in ve:
                errors.append(f"Missing required field: validation_evidence.{field}")
    return errors


def validate_evidence(card, snapshot=None):
    """Evidence-anchor validation (E2-E7 minus the registry-wide E1)."""
    tool = _evidence()
    snap = snapshot if snapshot is not None else json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    cid = card.get("card_id", "CAP-?")
    _, violations = tool.check({cid: card}, {"cards": [], "total_cards": 1}, snap, root=WORK_DIR)
    return [v for v in violations if not v.startswith("E1")]


def validate_card(card_path, snapshot=None):
    card = json.loads(Path(card_path).read_text(encoding="utf-8"))
    errors = validate_schema(card, load_schema())
    if errors:
        return False, f"Schema validation failed: {'; '.join(errors)}"
    violations = validate_evidence(card, snapshot)
    if violations:
        return False, "; ".join(violations)
    return True, "PASS"


def _judge(card, snapshot):
    """Return (accepted: bool, reason: str) for a red-team input card."""
    errors = validate_schema(card, load_schema())
    if errors:
        return False, f"schema: {'; '.join(errors)}"
    violations = validate_evidence(card, snapshot)
    if violations:
        return False, "; ".join(violations)
    return True, "evidence anchor resolves (E2/E3/E4/E5/E7 green)"


def run_red_team(update_file: bool = True):
    demo = json.loads(RED_TEAM_PATH.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    print("=" * 60)
    print("RED TEAM DEMO: Capability Card Validation Gate")
    print("=" * 60)
    all_pass = True
    for test in demo["tests"]:
        print(f"\nTest: {test['test_id']} - {test['name']}")
        accepted, reason = _judge(test["input_card"], snapshot)
        actual = "ACCEPTED" if accepted else "REJECTED"
        test["actual_result"] = actual
        test["rejection_reason_observed"] = reason
        test["verdict"] = "PASS" if actual == test["expected_result"] else "FAIL"
        all_pass &= test["verdict"] == "PASS"
        print(f"  Result: {actual} (expected {test['expected_result']})")
        print(f"  Reason: {reason}")
        print(f"  Verdict: {test['verdict']}")
    if update_file:
        RED_TEAM_PATH.write_text(json.dumps(demo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{'=' * 60}\nOverall: {'ALL PASS' if all_pass else 'SOME FAILED'}\n{'=' * 60}")
    return all_pass


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "--red-team":
        return 0 if run_red_team() else 1
    if argv[0] == "--batch":
        print("Batch validation of all cards in " + str(CARDS_DIR.relative_to(WORK_DIR)))
        print("=" * 60)
        ok_all = True
        for f in sorted(CARDS_DIR.glob("*.json")):
            ok, msg = validate_card(f)
            print(f"  {f.name}: {'PASS' if ok else 'FAIL'} - {msg}")
            ok_all &= ok
        print(f"\nOverall: {'ALL PASS' if ok_all else 'SOME FAILED'}")
        return 0 if ok_all else 1
    p = Path(argv[0])
    if not p.exists():
        print(f"Error: File not found: {p}")
        return 1
    ok, msg = validate_card(p)
    print(f"Validation: {'PASS' if ok else 'FAIL'}")
    print(f"Message: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())