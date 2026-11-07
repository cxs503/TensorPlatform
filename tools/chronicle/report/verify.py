#!/usr/bin/env python3
"""Anti-fabrication gate for chronicle reports (IN18-T3, section ③).

The W2 verdict on the paper line was blunt: two drafts were rejected because a
reviewer could not resolve the numbers, and one had figures whose data was
synthesised inside the plotting script. A gate is only a gate if it can say NO.
So this verifier has **two independent ways to FAIL** for every number:

  (a) printed value != recorded value      -> FABRICATED_NUMBER
      the manuscript digit was edited away from the provenance record;
  (b) recorded value != re-executed SQL    -> SOURCE_DRIFT
      the record was re-pointed, or the snapshot no longer says what it said.

plus hash re-computation for the snapshot itself (SNAP_HASH_MISMATCH,
SNAP_SOURCE_DRIFT) and for every figure artifact (FIGURE_HASH_MISMATCH).

FAIL codes
----------
SNAP_HASH_MISMATCH        snapshot sqlite bytes != manifest hash (evidence edited)
SNAP_SOURCE_DRIFT         source artifact changed after snapshot was taken
RECORD_SNAPSHOT_MISMATCH  record bound to a different snapshot than the one under test
SOURCE_DRIFT              re-executed SQL != recorded value
SOURCE_ERROR              recorded SQL fails on the snapshot (record is unverifiable)
FABRICATED_NUMBER         printed number != recorded value
UNMARKED_NUMBER           numeric literal inside an enforced block with no token
DANGLING_TOKEN            token with no provenance record
TOKEN_KIND_MISMATCH       number token bound to a figure record (or vice versa)
FIGURE_MISSING            figure file recorded but absent on disk
FIGURE_HASH_MISMATCH      figure bytes changed after provenance was recorded
UNTERMINATED_PROV_BLOCK   prov:begin without prov:end (enforcement silently disabled)

WARN codes: ORPHAN_RECORD (record never printed), NO_ENFORCED_BLOCK (nothing enforced).

Exit code 0 = PASS, 1 = FAIL, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

from .provenance import (
    BLOCK_BEGIN,
    BLOCK_END,
    ProvenanceStore,
    bare_numbers,
    enforced_blocks,
    extract_figure_tokens,
    extract_tokens,
)
from .snapshot import Snapshot, sha256_file

FAIL = "FAIL"
WARN = "WARN"


def _finding(code: str, severity: str, detail: str, **extra) -> dict:
    return {"code": code, "severity": severity, "detail": detail, **extra}


def _resolve(path: str, root: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (root / p)


def verify_report(
    manuscript: str | Path,
    provenance_path: str | Path,
    snapshot_root: str | Path,
    artifact_root: str | Path | None = None,
) -> dict:
    manuscript = Path(manuscript)
    snapshot_root = Path(snapshot_root)
    artifact_root = Path(artifact_root) if artifact_root else manuscript.parent
    md = manuscript.read_text(encoding="utf-8")
    store = ProvenanceStore.load(provenance_path)
    snap = Snapshot.load(snapshot_root)

    findings: list[dict] = []

    # ---- V1 snapshot integrity -------------------------------------------
    integ = snap.integrity()
    findings.extend(integ["findings"])
    for rec in store.records.values():
        if rec.source.get("snapshot_hash") not in (None, snap.snapshot_hash):
            findings.append(
                _finding(
                    "RECORD_SNAPSHOT_MISMATCH",
                    FAIL,
                    f"{rec.pid}: record pinned to snapshot {str(rec.source.get('snapshot_hash'))[:16]}, "
                    f"under test {snap.snapshot_hash[:16]}",
                    pid=rec.pid,
                )
            )

    # ---- V2 re-execute every recorded query ------------------------------
    recomputed: dict[str, float] = {}
    for rec in store.records.values():
        if rec.kind != "number":
            continue
        sql = rec.source.get("sql") or ""
        params = rec.source.get("params") or []
        try:
            value = snap.scalar(sql, params)
        except Exception as exc:  # unverifiable record is never a PASS
            findings.append(_finding("SOURCE_ERROR", FAIL, f"{rec.pid}: {exc}", pid=rec.pid))
            continue
        recomputed[rec.pid] = value
        if not rec.matches(value):
            findings.append(
                _finding(
                    "SOURCE_DRIFT",
                    FAIL,
                    f"{rec.pid}: recorded {rec.value!r} but re-executed SQL gives {value!r} "
                    f"(budget {rec.allowed_abs_error:g})",
                    pid=rec.pid,
                )
            )

    # ---- V3 printed numbers vs records -----------------------------------
    printed = extract_tokens(md)
    seen: set[str] = set()
    for tok in printed:
        rec = store.records.get(tok["pid"])
        if rec is None:
            findings.append(
                _finding("DANGLING_TOKEN", FAIL, f"line {tok['lineno']}: no record for {tok['pid']}", pid=tok["pid"])
            )
            continue
        seen.add(rec.pid)
        if rec.kind != "number":
            findings.append(
                _finding("TOKEN_KIND_MISMATCH", FAIL, f"line {tok['lineno']}: {rec.pid} is a {rec.kind}",
                         pid=rec.pid)
            )
            continue
        if not rec.matches(tok["printed"]):
            findings.append(
                _finding(
                    "FABRICATED_NUMBER",
                    FAIL,
                    f"line {tok['lineno']}: printed {tok['printed']!r} != recorded {rec.value!r} "
                    f"(budget {rec.allowed_abs_error:g}): {tok['line'][:120]}",
                    pid=rec.pid,
                    printed=tok["printed"],
                    recorded=rec.value,
                )
            )

    # ---- V4 enforced blocks: no unmarked digits --------------------------
    blocks = enforced_blocks(md)
    n_blocks = md.count(BLOCK_BEGIN)
    n_closed = md.count(BLOCK_END)
    if n_blocks != n_closed:
        findings.append(
            _finding("UNTERMINATED_PROV_BLOCK", FAIL, f"{n_blocks} begin / {n_closed} end markers")
        )
    if n_blocks == 0:
        findings.append(_finding("NO_ENFORCED_BLOCK", WARN, "document enforces no provenance block"))
    marked_total = 0
    bare_total = 0
    for bi, block in enumerate(blocks, start=1):
        marked_total += len(TOKEN_RE_find(block))
        for bare in bare_numbers(block):
            bare_total += 1
            findings.append(
                _finding(
                    "UNMARKED_NUMBER",
                    FAIL,
                    f"block {bi} line {bare['lineno']}: literal {bare['literal']!r} has no "
                    f"{{{{prov:...}}}} token: {bare['line']}",
                    block=bi,
                    literal=bare["literal"],
                )
            )

    # ---- V5 figure artifacts --------------------------------------------
    fig_tokens = extract_figure_tokens(md)
    for tok in fig_tokens:
        rec = store.records.get(tok["pid"])
        if rec is None:
            findings.append(
                _finding("DANGLING_TOKEN", FAIL, f"line {tok['lineno']}: no record for {tok['pid']}",
                         pid=tok["pid"])
            )
            continue
        seen.add(rec.pid)
        if rec.kind != "figure":
            findings.append(
                _finding("TOKEN_KIND_MISMATCH", FAIL, f"line {tok['lineno']}: {rec.pid} is a {rec.kind}",
                         pid=rec.pid)
            )
    figure_check: list[dict] = []
    for rec in store.records.values():
        if rec.kind != "figure":
            continue
        for art in rec.artifacts:
            path = _resolve(art["path"], artifact_root)
            if not path.exists():
                findings.append(_finding("FIGURE_MISSING", FAIL, f"{rec.pid}: {path} not on disk", pid=rec.pid))
                continue
            actual = sha256_file(path)
            ok = actual == art["sha256"]
            figure_check.append({"pid": rec.pid, "file": str(path), "ok": ok})
            if not ok:
                findings.append(
                    _finding(
                        "FIGURE_HASH_MISMATCH",
                        FAIL,
                        f"{rec.pid}: {path.name} sha256 {actual[:16]} != recorded {str(art['sha256'])[:16]}",
                        pid=rec.pid,
                    )
                )
        if rec.pid not in {t["pid"] for t in fig_tokens}:
            findings.append(_finding("ORPHAN_RECORD", WARN, f"{rec.pid}: figure never embedded", pid=rec.pid))

    # ---- V6 orphan number records ---------------------------------------
    for rec in store.records.values():
        if rec.kind == "number" and rec.pid not in seen:
            findings.append(
                _finding("ORPHAN_RECORD", WARN, f"{rec.pid}: provenance recorded but never printed", pid=rec.pid)
            )

    fails = [f for f in findings if f["severity"] == FAIL]
    warns = [f for f in findings if f["severity"] == WARN]
    return {
        "schema_version": "chronicle-verify/1",
        "status": FAIL if fails else "PASS",
        "manuscript": str(manuscript),
        "provenance": str(provenance_path),
        "snapshot": {
            "root": str(snapshot_root),
            "hash": snap.snapshot_hash,
            "created_ts": snap.created_ts,
            "integrity_ok": integ["ok"],
            "source": snap.source["path"],
        },
        "counts": {
            "records": len(store.records),
            "number_records": sum(1 for r in store.records.values() if r.kind == "number"),
            "figure_records": sum(1 for r in store.records.values() if r.kind == "figure"),
            "tokens_printed": len(printed),
            "figure_tokens": len(fig_tokens),
            "enforced_blocks": n_blocks,
            "marked_numbers_in_blocks": marked_total,
            "unmarked_numbers_in_blocks": bare_total,
            "queries_reexecuted": len(recomputed),
            "figure_files_checked": len(figure_check),
        },
        "figure_check": figure_check,
        "fail_count": len(fails),
        "warn_count": len(warns),
        "findings": findings,
    }


def TOKEN_RE_find(block: str) -> list[str]:
    return re.findall(r"\{[{]prov:[A-Za-z0-9_.:-]+\}[}]", block)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="verify a chronicle report against its provenance")
    ap.add_argument("--manuscript", required=True)
    ap.add_argument("--provenance", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--artifact-root", default=None)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    result = verify_report(args.manuscript, args.provenance, args.snapshot, args.artifact_root)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"[{result['status']}] fails={result['fail_count']} warns={result['warn_count']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())