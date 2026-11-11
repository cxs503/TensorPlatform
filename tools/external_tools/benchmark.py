"""Reference-vs-observed benchmark: turn a ledger into a machine-scored table.

Why this module exists
----------------------
A ledger answers "did the smoke call pass its own invariants".  A *benchmark*
answers a sharper question: "how far is the number the tool produced from an
independent reference, and is that error acceptable".  The two are not the same
claim, so they are not derived from the same place:

* the ledger owns PASS/FAIL per invariant (thresholds live in the spec file);
* this module owns the ``ref -> got -> error`` arithmetic, *only* for metrics
  whose spec entry declares a ``ref`` (with ``ref_source`` saying where the
  reference comes from).  A metric without a declared reference is recorded as
  ``unscored`` -- no reference, no accuracy claim, no percentage.

Accuracy bands follow the platform-wide rule, not this module's taste:
``>15%`` relative error is unacceptable (``REL_LIMIT_UNACCEPTABLE``) and the
reporting question "is it below 3%" is answered per row (``REL_LIMIT_STRICT``).
Absolute-error metrics (residuals of an exact identity) have ``rel_err=None``
by construction -- dividing a residual by zero would invent a percentage.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .spec import Invariant, Suite

ANCHOR = "KPI-TOOLS-5"
REL_LIMIT_UNACCEPTABLE = 0.15   # platform accuracy standard: >15% not acceptable
REL_LIMIT_STRICT = 0.03         # "是否 <3%" question, answered per row

__all__ = ["ANCHOR", "REL_LIMIT_UNACCEPTABLE", "REL_LIMIT_STRICT",
           "build", "render_markdown", "write"]


def _case_id(tool_id: str, metric: str) -> str:
    return f"{tool_id}.{metric}"


def _score(inv: Invariant, got: Optional[float]) -> Dict[str, Any]:
    """One row: reference resolution + error arithmetic.  Never guesses a ref."""

    ref: Optional[float] = inv.ref
    ref_source = inv.ref_source
    if ref is None and inv.op == "abs" and inv.target is not None:
        ref = float(inv.target)          # ``abs`` already names its target
        ref_source = ref_source or "invariant target (op=abs)"
    if ref is None:
        return {"ref": None, "ref_source": ref_source or "",
                "abs_err": None, "rel_err": None, "kind": "unscored",
                "within_3pct": None, "within_15pct": None}
    if got is None:
        return {"ref": ref, "ref_source": ref_source, "abs_err": None,
                "rel_err": None, "kind": "value", "within_3pct": None,
                "within_15pct": None}
    abs_err = abs(float(got) - ref)
    rel_err = None if ref == 0.0 else abs_err / abs(ref)
    return {"ref": ref, "ref_source": ref_source, "abs_err": abs_err,
            "rel_err": rel_err, "kind": "value",
            "within_3pct": None if rel_err is None else rel_err <= REL_LIMIT_STRICT,
            "within_15pct": None if rel_err is None else rel_err <= REL_LIMIT_UNACCEPTABLE}


def build(ledger: Mapping[str, Any], suite: Suite) -> Dict[str, Any]:
    """Build the benchmark table from a ledger + the same suite spec."""

    classes = suite.by_id()
    rows: List[Dict[str, Any]] = []
    for rec in ledger.get("classes", []):
        tool_id = str(rec.get("tool_id"))
        cls = classes.get(tool_id)
        if cls is None:
            continue                     # ledger from a different spec: not ours
        metrics = dict(rec.get("metrics") or {})
        inv_by_metric = {inv.metric: inv for inv in cls.invariants}
        for inv_rec in rec.get("invariants", []):
            metric = str(inv_rec.get("metric"))
            inv = inv_by_metric.get(metric)
            if inv is None:
                continue
            got = metrics.get(metric)
            scored = _score(inv, None if got is None else float(got))
            rows.append({
                "case_id": _case_id(tool_id, metric),
                "tool_id": tool_id,
                "category": cls.category,
                "metric": metric,
                "status": rec.get("status"),
                "observed": None if got is None else float(got),
                "tolerance": inv.limit,
                "comparator": inv.op,
                "unit": inv.unit,
                "invariant_ok": bool(inv_rec.get("ok")),
                **scored,
            })

    value_rows = [r for r in rows if r["kind"] == "value"]
    rel_rows = [r for r in value_rows if r["rel_err"] is not None]
    abs_rows = [r for r in value_rows if r["rel_err"] is None and r["abs_err"] is not None]
    n_passed = sum(1 for r in rows if r["invariant_ok"])
    return {
        "anchor_slug": ANCHOR,
        "suite_id": ledger.get("suite_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ledger_generated_at": ledger.get("generated_at"),
        "ledger_digest": ledger.get("digest"),
        "ledger_value_digest": ledger.get("value_digest"),
        "thresholds": {"unacceptable_rel_err": REL_LIMIT_UNACCEPTABLE,
                       "strict_rel_err": REL_LIMIT_STRICT},
        "summary": {
            "n_cases": len(rows),
            "n_value_cases": len(value_rows),
            "n_scored_rel": len(rel_rows),
            "n_scored_abs_only": len(abs_rows),
            "n_unscored": sum(1 for r in rows if r["kind"] == "unscored"),
            "n_passed": n_passed,
            "n_failed": len(rows) - n_passed,
            "n_within_3pct": sum(1 for r in rel_rows if r["rel_err"] <= REL_LIMIT_STRICT),
            "n_within_15pct": sum(1 for r in rel_rows
                                  if r["rel_err"] <= REL_LIMIT_UNACCEPTABLE),
            "max_rel_err": max((r["rel_err"] for r in rel_rows), default=None),
            "all_invariants_ok": n_passed == len(rows) and bool(rows),
        },
        "rows": rows,
    }


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if value == 0.0:
            return "0"
        if abs(value) < 1e-3 or abs(value) >= 1e6:
            return f"{value:.{digits}e}"
        return f"{value:.{digits}g}"
    return str(value)


def render_markdown(bench: Mapping[str, Any]) -> str:
    s = dict(bench.get("summary") or {})
    lines = [
        f"# Benchmark · {bench.get('anchor_slug')} — ref vs got",
        "",
        f"* suite: `{bench.get('suite_id')}`",
        f"* ledger generated_at: {bench.get('ledger_generated_at')}",
        f"* ledger digests: `{bench.get('ledger_digest')}` / `{bench.get('ledger_value_digest')}`",
        f"* cases: {s.get('n_cases')} (value={s.get('n_value_cases')}, "
        f"rel-scored={s.get('n_scored_rel')}, abs-only={s.get('n_scored_abs_only')}, "
        f"unscored={s.get('n_unscored')})",
        f"* passed {s.get('n_passed')} / failed {s.get('n_failed')}; "
        f"within 3% = {s.get('n_within_3pct')}, within 15% = {s.get('n_within_15pct')}; "
        f"max rel_err = {_fmt(s.get('max_rel_err'))}",
        "",
        "| case_id | ref | got | abs_err | rel_err | tol | 判据 | ref_source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in bench.get("rows", []):
        lines.append(
            f"| `{r['case_id']}` | {_fmt(r.get('ref'))} | {_fmt(r.get('observed'))} | "
            f"{_fmt(r.get('abs_err'))} | {_fmt(r.get('rel_err'))} | "
            f"{_fmt(r.get('tolerance'))} | {'✓' if r.get('invariant_ok') else '✗'} | "
            f"{r.get('ref_source') or '—'} |")
    lines += ["",
              "> `rel_err` is blank where the reference is an exact zero "
              "(identity residual): dividing by zero would invent a percentage. "
              "`unscored` rows have no declared reference and make no accuracy claim.", ""]
    return "\n".join(lines)


def write(bench: Mapping[str, Any], out_dir: str | Path,
          stem: str = "benchmark") -> Dict[str, str]:
    """Persist benchmark JSON + markdown; return {kind: path}."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"{stem}.json"
    mp = out / f"{stem}.md"
    jp.write_text(json.dumps(bench, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                  encoding="utf-8")
    mp.write_text(render_markdown(bench), encoding="utf-8")
    return {"json": str(jp), "markdown": str(mp)}