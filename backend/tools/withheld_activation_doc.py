#!/usr/bin/env python3
"""Render the numbers sections of the withheld-activation document.

Every number in the published document is injected here from
``artifacts/withheld_activation/acceptance.json`` and the audit log, never typed by
hand (owner rule: 零手抄).  The document keeps three markers and this script
rewrites the blocks between them::

    python backend/tools/withheld_activation_doc.py \
        --doc docs/WITHHELD_REFERENCE_ACTIVATION_MATURITY.md \
        --artifacts artifacts/withheld_activation

Exit code 0 on success; 1 when the document or the artifact is missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CRITERIA_TITLES = {
    "A": "positive activation (WITHHELD -> PASS inside the budget)",
    "B": "negative control (no truth -> stays WITHHELD, exit 0, no audit)",
    "C": "withdrawal (truth removed -> falls back to WITHHELD, audited)",
    "D": "counter recompute gate (forged counter refused, delta reported)",
    "E": "zero domain vocabulary in new code / fixtures / artifacts",
    "F": "idempotence and concurrency (no audit inflation)",
    "G": "figure artifact written",
}


def _md_table(header, rows) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return "`" + json.dumps(value, ensure_ascii=False) + "`"
    return str(value)


def _block(marker: str, body: str) -> str:
    return (f"<!-- BEGIN:{marker} -->\n{body}\n<!-- END:{marker} -->")


def subjects_table(report: dict) -> str:
    ledger = report["main_ledger"]
    rows = []
    for subject in sorted(ledger["verdicts"]):
        rows.append([f"`{subject}`",
                     _fmt(ledger["verdicts"][subject]),
                     _fmt(ledger["error_pct"].get(subject)),
                     _fmt(ledger["tolerance_pct"].get(subject))])
    return _md_table(["subject", "verdict", "error_pct", "tolerance_pct"], rows)


def criteria_table(report: dict) -> str:
    rows = []
    for key in sorted(report["criteria"]):
        item = report["criteria"][key]
        numbers = item.get("numbers", {})
        highlight = ", ".join(f"{k}={_fmt(v)}" for k, v in numbers.items()
                             if k in {"latency_s", "audit_before", "audit_after",
                                      "audit_entries", "exit_codes", "gate_exit",
                                      "scan_exit", "repair_exit", "deltas", "hit_count",
                                      "scanned_files", "sequential_counts",
                                      "after_concurrent", "png_bytes", "budget_s",
                                      "reference_sha256", "new_verdict"})
        rows.append([key, CRITERIA_TITLES.get(key, "extra"), "PASS" if item["passed"] else "FAIL",
                     highlight or "-"])
    return _md_table(["criterion", "meaning", "result", "key numbers"], rows)


def audit_table(state_dir: Path) -> str:
    path = Path(state_dir) / "audit.jsonl"
    if not path.exists():
        return "_no audit records_"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for record in records:
        rows.append([record.get("seq"), record.get("trigger"), record.get("subject_id"),
                     record.get("old_verdict"), record.get("new_verdict"),
                     _fmt(record.get("error_pct")), record.get("actor"),
                     (record.get("sha256") or "-")[:16] + "..."])
    return _md_table(["seq", "trigger", "subject", "old", "new", "error_pct", "actor", "sha256 (16 hex)"], rows)


def error_verdict_block(report: dict) -> str:
    """Explicit per-subject answer to the "is it below 3 %?" question."""
    ledger = report["main_ledger"]
    lines = []
    for subject in sorted(ledger["verdicts"]):
        error = ledger["error_pct"].get(subject)
        tolerance = ledger["tolerance_pct"].get(subject)
        verdict = ledger["verdicts"][subject]
        if error is None or tolerance is None:
            lines.append(f"* `{subject}`: no reference truth on file -> error undefined, "
                         f"verdict `{verdict}` (nothing to compare, so the question is not answerable "
                         f"for this subject and is **not** reported as passing).")
            continue
        below_three = "yes" if error < 3.0 else "no"
        within = "yes" if error <= tolerance else "no"
        lines.append(f"* `{subject}`: relative error `{error:.6g} %` vs declared tolerance "
                     f"`{tolerance:.6g} %` -> within tolerance: **{within}**; "
                     f"below the platform's 3 % bar: **{below_three}**; verdict `{verdict}`.")
    return "\n".join(lines)


def render(doc_path: Path, artifacts: Path) -> int:
    acceptance = Path(artifacts) / "acceptance.json"
    if not acceptance.exists():
        print(f"missing {acceptance}; run the acceptance first", file=sys.stderr)
        return 1
    if not Path(doc_path).exists():
        print(f"missing {doc_path}", file=sys.stderr)
        return 1
    report = json.loads(acceptance.read_text(encoding="utf-8"))
    text = Path(doc_path).read_text(encoding="utf-8")
    blocks = {
        "SUBJECTS": subjects_table(report),
        "ERROR_VERDICT": error_verdict_block(report),
        "CRITERIA": criteria_table(report),
        "AUDIT": audit_table(Path(report["main_ledger"]["state_dir"])),
        "AUDIT_WITHDRAWAL": audit_table(Path(report["out_dir"]) / "withdrawal"),
    }
    for marker, body in blocks.items():
        start = f"<!-- BEGIN:{marker} -->"
        end = f"<!-- END:{marker} -->"
        if start not in text or end not in text:
            print(f"marker {marker} missing in {doc_path}", file=sys.stderr)
            return 1
        head, rest = text.split(start, 1)
        _, tail = rest.split(end, 1)
        text = head + _block(marker, body) + tail
    Path(doc_path).write_text(text, encoding="utf-8")
    print(f"rendered {len(blocks)} blocks into {doc_path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc", default=str(ROOT / "docs" / "WITHHELD_REFERENCE_ACTIVATION_MATURITY.md"))
    parser.add_argument("--artifacts", default=str(ROOT / "artifacts" / "withheld_activation"))
    args = parser.parse_args(argv)
    return render(Path(args.doc), Path(args.artifacts))


if __name__ == "__main__":
    raise SystemExit(main())