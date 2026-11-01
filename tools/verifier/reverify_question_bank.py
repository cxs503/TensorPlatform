#!/usr/bin/env python3
"""
Question-bank reference-integrity audit and migration.

Purpose
-------
Pay down a verification debt: the bank's Holtrop-Mennen questions used to carry
reference values produced by the *same* private expression the pilot candidate
implemented, so the verification was circular.  This tool

  1. audits every committed question for independent re-derivability
     (criteria C1-C5, see `tools/verifier/reference/integrity.py`),
  2. rewrites any question whose reference is not reproducible from the
     published formula, preserving its id / created_at and bumping its version,
  3. re-audits and writes a machine-readable before/after artifact.

Usage
-----
    python3 tools/verifier/reverify_question_bank.py [--dry-run] [--out DIR]

Exit code is 0 when the post-migration audit passes all criteria, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.generate_questions import generate_all_questions  # noqa: E402
from verifier.question_bank import DEFAULT_QBANK_PATH  # noqa: E402
from verifier.reference import integrity  # noqa: E402

FORMS = ("specification", "expected_result", "tolerances", "provenance", "tags")


def load_rows(db_path: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT id, domain, difficulty, title, specification, expected_result,"
            " tolerances, provenance, tags, version, created_at, committed, commit_hash"
            " FROM questions ORDER BY title"
        )
        rows = []
        for r in cur.fetchall():
            rows.append({
                "id": r[0], "domain": r[1], "difficulty": r[2], "title": r[3],
                "specification": json.loads(r[4]),
                "expected_result": json.loads(r[5]),
                "tolerances": json.loads(r[6]),
                "provenance": json.loads(r[7]),
                "tags": json.loads(r[8]),
                "version": r[9], "created_at": r[10], "committed": bool(r[11]),
                "commit_hash": r[12],
            })
    finally:
        conn.close()
    return rows


def canonical_by_title() -> Dict[str, Any]:
    """Corrected question content keyed by title (the generator is authoritative)."""
    return {q.title: q for q in generate_all_questions()}


def migrate(rows: List[Dict[str, Any]], db_path: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Rewrite rows whose reference value cannot be re-derived from the published form."""
    canon = canonical_by_title()
    changed = []
    conn = None if dry_run else sqlite3.connect(db_path)
    try:
        for row in rows:
            verdict = integrity.check_rederivation(row)
            if verdict.passed:
                continue
            target = canon.get(row["title"])
            if target is None:
                changed.append({
                    "id": row["id"], "title": row["title"],
                    "action": "unfixed_no_canonical_source",
                    "reason": verdict.detail,
                })
                continue
            target.id = row["id"]
            target.created_at = row["created_at"]
            target.version = int(row["version"]) + 1
            target.freeze()
            record = {
                "id": row["id"],
                "title": row["title"],
                "action": "superseded",
                "old_expected": row["expected_result"],
                "new_expected": target.expected_result,
                "old_version": row["version"],
                "new_version": target.version,
                "reason": verdict.detail,
            }
            changed.append(record)
            if conn is not None:
                conn.execute(
                    "UPDATE questions SET specification=?, expected_result=?, tolerances=?,"
                    " provenance=?, tags=?, difficulty=?, version=?, committed=1, commit_hash=?"
                    " WHERE id=?",
                    (
                        json.dumps(target.specification, ensure_ascii=False, sort_keys=True),
                        json.dumps(target.expected_result, ensure_ascii=False, sort_keys=True),
                        json.dumps(target.tolerances, ensure_ascii=False, sort_keys=True),
                        json.dumps(target.provenance, ensure_ascii=False, sort_keys=True),
                        json.dumps(target.tags, ensure_ascii=False),
                        target.difficulty,
                        target.version,
                        target.commit_hash,
                        row["id"],
                    ),
                )
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qbank", default=DEFAULT_QBANK_PATH)
    ap.add_argument("--out", default="artifacts/verifier_reference_integrity")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    before_rows = load_rows(args.qbank)
    before = integrity.audit_bank(before_rows)

    changed = migrate(before_rows, args.qbank, args.dry_run)

    after_rows = load_rows(args.qbank) if not args.dry_run else before_rows
    after = integrity.audit_bank(after_rows) if not args.dry_run else before

    artifact = {
        "tool": "tools/verifier/reverify_question_bank.py",
        "qbank": args.qbank,
        "dry_run": args.dry_run,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "migrated_questions": changed,
        "before": before,
        "after": after,
    }

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "reference_integrity_audit.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False)

    print(f"questions audited : {before['n_questions']}")
    print(f"questions migrated: {len([c for c in changed if c['action'] == 'superseded'])}")
    print(f"before all_passed : {before['all_passed']}")
    print(f"after  all_passed : {after['all_passed']}")
    for c in after["criteria"]:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['criterion']}: {c['detail']}")
    print(f"artifact          : {path}")
    return 0 if after["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())