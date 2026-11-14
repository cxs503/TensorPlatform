"""Machine gate for ``ledger.json`` (acceptance item B).

Checks, per entry:
  * required fields present and typed;
  * ``rel_err.{low,mid,high}`` re-derives from ``computed`` / ``baseline`` within 1e-6;
  * ``pass_15pct`` / ``pass_3pct`` re-derive from the declared gate basis;
  * WITHHELD / STUB / NOT_SUPPORTED entries never carry ``pass_*pct = true``;
  * WITHHELD entries must state a reason.
Summary-level checks: counts agree with the entry list, gate thresholds match the
owner rule, gate basis is the declared one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from .model import (ADOPTED_GATE_BASIS, GRADES, REL_ERR_TOL, TOL_HARD,
                    TOL_TARGET, within)

REQUIRED_ENTRY_FIELDS = (
    "entry_id", "case_id", "quantity", "computed", "baseline", "rel_err",
    "pass_15pct", "pass_3pct", "evidence_grade", "source_file", "gate_basis",
)
REQUIRED_BASELINE_FIELDS = ("value", "low", "high", "source", "evidence_grade")
NON_JUDGEABLE = ("WITHHELD", "STUB", "NOT_SUPPORTED")


def _rel(computed, ref):
    if computed is None or ref is None:
        return None
    ref = float(ref)
    if ref == 0.0:
        return None
    return (float(computed) - ref) / ref


def _cmp(stored, recomputed) -> bool:
    if stored is None and recomputed is None:
        return True
    if stored is None or recomputed is None:
        return False
    return abs(float(stored) - float(recomputed)) <= REL_ERR_TOL


def check_entry(e: dict) -> List[str]:
    errs: List[str] = []
    eid = e.get("entry_id", "<no entry_id>")
    for f in REQUIRED_ENTRY_FIELDS:
        if f not in e:
            errs.append(f"{eid}: missing field {f!r}")
    if errs:
        return errs
    b = e["baseline"]
    if not isinstance(b, dict):
        return [f"{eid}: baseline is not an object"]
    for f in REQUIRED_BASELINE_FIELDS:
        if f not in b:
            errs.append(f"{eid}: baseline missing field {f!r}")
    if b.get("evidence_grade") not in GRADES:
        errs.append(f"{eid}: bad baseline.evidence_grade {b.get('evidence_grade')!r}")
    if e.get("evidence_grade") not in GRADES:
        errs.append(f"{eid}: bad evidence_grade {e.get('evidence_grade')!r}")
    if e.get("gate_basis") != ADOPTED_GATE_BASIS:
        errs.append(f"{eid}: gate_basis {e.get('gate_basis')!r} != declared {ADOPTED_GATE_BASIS!r}")

    rel = e["rel_err"]
    if not isinstance(rel, dict) or set(rel) != {"low", "mid", "high"}:
        errs.append(f"{eid}: rel_err must have exactly low/mid/high")
        return errs
    recomp = {
        "low": _rel(e["computed"], b.get("low")),
        "mid": _rel(e["computed"], b.get("value")),
        "high": _rel(e["computed"], b.get("high")),
    }
    for k in ("low", "mid", "high"):
        if not _cmp(rel[k], recomp[k]):
            errs.append(
                f"{eid}: rel_err.{k} {rel[k]!r} != recomputed {recomp[k]!r} "
                f"(tolerance {REL_ERR_TOL})"
            )
    # gate re-derivation
    grade = e["evidence_grade"]
    judgeable = grade in ("REAL", "MOCK") and e["computed"] is not None and b.get("value") is not None
    exp15 = bool(judgeable and within(recomp["mid"], TOL_HARD))
    exp3 = bool(judgeable and within(recomp["mid"], TOL_TARGET))
    if bool(e["pass_15pct"]) != exp15:
        errs.append(f"{eid}: pass_15pct {e['pass_15pct']} != recomputed {exp15}")
    if bool(e["pass_3pct"]) != exp3:
        errs.append(f"{eid}: pass_3pct {e['pass_3pct']} != recomputed {exp3}")
    # withholder invariants
    if grade in NON_JUDGEABLE:
        if e["pass_15pct"] or e["pass_3pct"]:
            errs.append(f"{eid}: {grade} entry must not carry a PASS verdict")
        if grade == "WITHHELD" and not (e.get("withheld_reason") or "").strip():
            errs.append(f"{eid}: WITHHELD entry must state withheld_reason")
        if e["computed"] is not None and grade == "WITHHELD" and b.get("value") is None:
            pass  # allowed: computed exists, baseline withheld
    if grade == "REAL" and b.get("value") is None and e["pass_15pct"]:
        errs.append(f"{eid}: REAL entry with no baseline value cannot pass")
    return errs


def check_ledger(ledger: dict) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return False, ["ledger has no 'entries' list"]
    if len(entries) < 15:
        errs.append(f"entries = {len(entries)} < 15 (acceptance floor)")
    gate = ledger.get("gate") or {}
    if gate.get("basis") != ADOPTED_GATE_BASIS:
        errs.append(f"gate.basis {gate.get('basis')!r} != {ADOPTED_GATE_BASIS!r}")
    if gate.get("tol_hard") != TOL_HARD:
        errs.append(f"gate.tol_hard {gate.get('tol_hard')!r} != {TOL_HARD}")
    if gate.get("tol_target") != TOL_TARGET:
        errs.append(f"gate.tol_target {gate.get('tol_target')!r} != {TOL_TARGET}")
    ids = set()
    for e in entries:
        errs.extend(check_entry(e))
        eid = e.get("entry_id")
        if eid in ids:
            errs.append(f"duplicate entry_id {eid!r}")
        ids.add(eid)
    # summary consistency
    totals = ledger.get("totals") or {}
    if totals.get("entries") != len(entries):
        errs.append(f"totals.entries {totals.get('entries')} != len(entries) {len(entries)}")
    by_grade = totals.get("by_grade") or {}
    actual: dict = {}
    for e in entries:
        actual[e["evidence_grade"]] = actual.get(e["evidence_grade"], 0) + 1
    for g in set(list(by_grade) + list(actual)):
        if by_grade.get(g, 0) != actual.get(g, 0):
            errs.append(f"totals.by_grade[{g}] {by_grade.get(g, 0)} != counted {actual.get(g, 0)}")
    res = ledger.get("gate_results") or {}
    n_pass15 = sum(1 for e in entries if e["pass_15pct"])
    n_pass3 = sum(1 for e in entries if e["pass_3pct"])
    if res.get("pass_15pct") != n_pass15:
        errs.append(f"gate_results.pass_15pct {res.get('pass_15pct')} != counted {n_pass15}")
    if res.get("pass_3pct") != n_pass3:
        errs.append(f"gate_results.pass_3pct {res.get('pass_3pct')} != counted {n_pass3}")
    adj = ledger.get("gate_adjudication") or {}
    if adj.get("adopted") != ADOPTED_GATE_BASIS:
        errs.append("gate_adjudication.adopted missing/!= declared basis")
    for k in ("endpoints", "mid", "containment"):
        if k not in (adj.get("options") or {}):
            errs.append(f"gate_adjudication missing option {k!r}")
    return (not errs), errs


def check_file(path: Path) -> Tuple[bool, List[str]]:
    path = Path(path)
    if not path.exists():
        return False, [f"ledger file not found: {path}"]
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, [f"invalid JSON: {exc}"]
    return check_ledger(ledger)