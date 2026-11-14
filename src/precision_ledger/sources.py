"""Scan the repo for every machine-readable "computed vs baseline" pair.

Two layers of normalisation:

1. :func:`scan_generic` — walks every ``*.json`` under the scan root and picks up
   dict records that carry a case id plus a known computed/reference key alias
   (``calc_ct``/``ref_ct``, ``Ct_calc``/``Ct_exp``, ``calc_rt_n``/``ref_rt_n`` …).
   This is what makes the ledger cover the whole repo instead of a hand-picked
   list of files.
2. :func:`scan_special` — targeted adapters for shapes the generic rule cannot
   read: verification-oracle reports, propeller BEM-vs-regression tables,
   friction-line cross-checks and batch runs that recorded no baseline at all.

Both layers emit :class:`RawRecord`; baseline adjudication happens later in
``ledger.py`` so that *one* policy governs every entry.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .model import Baseline

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "artifacts/precision_ledger",
}
SKIP_PATH_PARTS = ("/fixtures/", "/golden/", "/node_modules/", "/.git/")
MAX_JSON_BYTES = 16 * 1024 * 1024

CASE_ID_KEYS = ("case_id", "id", "case", "tag", "name")

QUANTITY_ALIASES: Dict[str, Dict[str, tuple]] = {
    "Ct": {
        "computed": ("calc_ct", "Ct_calc", "ct_calc", "computed_ct", "ct_computed"),
        "ref": ("ref_ct", "Ct_exp", "exp_ct", "ct_exp", "experimental_ct"),
    },
    "Rt": {
        "computed": ("calc_rt_n", "Rt_calc", "rt_calc_n", "computed_rt_n", "rt_calc"),
        "ref": ("ref_rt_n", "Rt_exp", "rt_exp_n", "experimental_rt_n", "rt_exp"),
    },
}
FR_KEYS = ("froude_number", "Fn", "fn", "Fr", "froude")


@dataclass
class RawRecord:
    source_file: str
    adapter: str
    family: str
    case_id: str
    quantity: str
    computed: Optional[float]
    ref_recorded: Optional[float] = None
    method: str = ""
    note: str = ""
    fr: Optional[float] = None
    ship_type: str = ""
    unit: str = "-"
    self_referential: bool = False
    forced_grade: Optional[str] = None
    forced_baseline: Optional[Baseline] = None
    forced_withheld_reason: str = ""
    extra: Dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _num(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first(d: dict, keys: Iterable[str]) -> Optional[object]:
    for k in keys:
        if k in d:
            return d[k]
    return None


def family_of(rel_path: str) -> str:
    parts = rel_path.split("/")
    if len(parts) >= 2 and parts[0] in ("docs", "artifacts", "runs"):
        return "/".join(parts[:2])
    if parts[0] == "backend" or parts[0] == "data":
        return parts[0]
    return parts[0]


def iter_json_files(root: Path) -> List[Path]:
    out: List[Path] = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and f"{rel_dir}/{d}".strip("/") not in SKIP_DIRS
        )
        for fn in sorted(filenames):
            if not fn.endswith(".json"):
                continue
            p = Path(dirpath) / fn
            rel = str(p.relative_to(root)).replace(os.sep, "/")
            if any(part in f"/{rel}" for part in SKIP_PATH_PARTS):
                continue
            if rel.startswith("artifacts/precision_ledger/"):
                continue
            try:
                if p.stat().st_size > MAX_JSON_BYTES:
                    continue
            except OSError:
                continue
            out.append(p)
    return out


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return None


def _walk_dicts(node) -> Iterable[dict]:
    """Yield every dict inside a JSON document (deterministic order)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_dicts(v)


# --------------------------------------------------------------------------
# layer 1: generic alias-based scan
# --------------------------------------------------------------------------
def record_from_dict(d: dict, rel_path: str, root: Path) -> List[RawRecord]:
    case_id = _first(d, CASE_ID_KEYS)
    if not isinstance(case_id, str) or not case_id.strip():
        return []
    case_id = case_id.strip()
    out: List[RawRecord] = []
    fr = None
    for k in FR_KEYS:
        if k in d and _num(d[k]) is not None:
            fr = _num(d[k])
            break
    ship_type = d.get("ship_type") if isinstance(d.get("ship_type"), str) else ""
    method = d.get("method") if isinstance(d.get("method"), str) else ""
    note = d.get("note") if isinstance(d.get("note"), str) else ""
    for qty, alias in QUANTITY_ALIASES.items():
        comp_keys, ref_keys = alias["computed"], alias["ref"]
        has_comp = any(k in d for k in comp_keys)
        has_ref = any(k in d for k in ref_keys)
        if not (has_comp or has_ref):
            continue
        computed = _num(_first(d, comp_keys))
        ref = _num(_first(d, ref_keys))
        if not has_comp or computed is None:
            # a bare reference declaration (or a null result) is not a comparison;
            # dedicated adapters below cover runs that recorded no number at all.
            continue
        identical = computed is not None and ref is not None and computed == ref
        out.append(RawRecord(
            source_file=rel_path,
            adapter="generic_alias",
            family=family_of(rel_path),
            case_id=case_id,
            quantity=qty,
            computed=computed,
            ref_recorded=ref,
            method=method,
            note=note,
            fr=fr,
            ship_type=ship_type,
            unit="-" if qty == "Ct" else "N",
            self_referential=identical,
            forced_grade="MOCK" if identical else None,
            extra={"identical_to_reference": identical},
        ))
    return out


def scan_generic(root: Path) -> List[RawRecord]:
    root = Path(root)
    out: List[RawRecord] = []
    for path in iter_json_files(root):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        doc = _load(path)
        if doc is None:
            continue
        seen = set()
        for d in _walk_dicts(doc):
            for rec in record_from_dict(d, rel, root):
                key = (rec.case_id, rec.quantity, rec.computed, rec.ref_recorded)
                if key in seen:
                    continue
                seen.add(key)
                out.append(rec)
    return out


# --------------------------------------------------------------------------
# layer 2: special adapters
# --------------------------------------------------------------------------
def scan_verification_oracle(root: Path) -> List[RawRecord]:
    """``*verification_report.json``: expected_result vs actual_result.

    These reports compare the platform against an ``expected_result`` that is
    stored next to it.  When the two are bit-identical the comparison carries no
    independent information -> graded MOCK (self-referential oracle).
    """
    root = Path(root)
    out: List[RawRecord] = []
    for path in iter_json_files(root):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        if "verification_report" not in path.name and "pilot_verification" not in path.name:
            continue
        doc = _load(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
            continue
        for i, r in enumerate(doc["results"]):
            if not isinstance(r, dict):
                continue
            actual = r.get("actual_result") or {}
            expected = r.get("expected_result") or {}
            if not isinstance(actual, dict) or not isinstance(expected, dict):
                continue
            qid = str(r.get("question_id") or r.get("question_title") or f"q{i}")
            title = str(r.get("question_title") or "")
            for qty, exp in expected.items():
                if qty not in actual:
                    continue
                a, e = _num(actual[qty]), _num(exp)
                if a is None and e is None:
                    continue
                identical = a is not None and e is not None and a == e
                out.append(RawRecord(
                    source_file=rel,
                    adapter="verification_oracle",
                    family=family_of(rel),
                    case_id=f"{qid}",
                    quantity=str(qty),
                    computed=a,
                    ref_recorded=e,
                    method="platform candidate vs stored expected_result",
                    note=title,
                    self_referential=identical,
                    forced_grade="MOCK" if identical else None,
                    extra={"identical_to_expected": identical},
                ))
    return out


def scan_propeller_tables(root: Path) -> List[RawRecord]:
    """Propeller BEM kernel vs Wageningen B-series regression (m1753 shape).

    Peak efficiency and zero-thrust advance ratio are the same physical quantity
    on both sides -> judgeable.  Pointwise KT/KQ are recorded as NOT_SUPPORTED:
    the first-principles BEM kernel is not a B-series implementation, so its
    pointwise gap is a model-family statement, not an accuracy measurement.
    """
    root = Path(root)
    out: List[RawRecord] = []
    for path in iter_json_files(root):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        doc = _load(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("propellers"), dict):
            continue
        prov = doc.get("provenance") or {}
        ref_src = str(prov.get("oosterveld_ref") or "Wageningen B-series regression")
        for tag, p in doc["propellers"].items():
            if not isinstance(p, dict):
                continue
            pairs = [
                ("eta0_max", "eta0_max_bem", "eta0_max_ref", "-"),
                ("J0", "J0_bem", "J0_ref", "-"),
            ]
            for qty, ck, rk, unit in pairs:
                if ck not in p or rk not in p:
                    continue
                out.append(RawRecord(
                    source_file=rel,
                    adapter="propeller_table",
                    family=family_of(rel),
                    case_id=f"{tag}",
                    quantity=qty,
                    computed=_num(p.get(ck)),
                    ref_recorded=_num(p.get(rk)),
                    method="first-principles BEM kernel vs Oosterveld-van Oossanen regression",
                    note=str(prov.get("note") or "")[:200],
                    ship_type="propeller",
                    unit=unit,
                    forced_baseline=Baseline(
                        value=_num(p.get(rk)), low=None, high=None,
                        source=ref_src,
                        evidence_grade="REAL", kind="regression",
                        citation="Oosterveld & van Oossanen (1975) 39+47-term polynomials",
                        note="regression baseline (not experimental)",
                    ),
                ))
            pointwise = {k: _num(p.get(k)) for k in
                         ("max_abs_dKT_pct", "max_abs_dKQ_pct", "max_abs_deta_pct")}
            pointwise = {k: v for k, v in pointwise.items() if v is not None}
            if pointwise:
                k_max = max(pointwise, key=lambda k: pointwise[k] if k != "max_abs_deta_pct"
                            else -1.0)
                qty = "KT_KQ_pointwise_max_abs_dev"
                out.append(RawRecord(
                    source_file=rel,
                    adapter="propeller_table",
                    family=family_of(rel),
                    case_id=f"{tag}",
                    quantity=qty,
                    computed=pointwise[k_max],
                    ref_recorded=0.0,
                    method="BEM vs B-series regression, pointwise max |deviation| (%) "
                           f"(KT={pointwise.get('max_abs_dKT_pct')}, "
                           f"KQ={pointwise.get('max_abs_dKQ_pct')}, "
                           f"eta={pointwise.get('max_abs_deta_pct')})",
                    note="model-family gap, no accuracy claim",
                    ship_type="propeller",
                    unit="%",
                    forced_grade="NOT_SUPPORTED",
                    forced_withheld_reason=(
                        "not_supported: BEM kernel is a first-principles blade-element "
                        "implementation, not a B-series code; pointwise KT/KQ deviation "
                        "(>2000%) is a model-family statement, not an accuracy measurement"
                    ),
                ))
    return out


def scan_friction_crosscheck(root: Path) -> List[RawRecord]:
    """ITTC-1957 vs Schoenherr friction lines at matched Re."""
    root = Path(root)
    out: List[RawRecord] = []
    for path in iter_json_files(root):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        if "friction" not in path.name.lower():
            continue
        doc = _load(path)
        if not isinstance(doc, list):
            continue
        for row in doc:
            if not isinstance(row, dict) or "ittc57" not in row or "schoenherr" not in row:
                continue
            re = _num(row.get("re"))
            out.append(RawRecord(
                source_file=rel,
                adapter="friction_crosscheck",
                family=family_of(rel),
                case_id=f"Cf@Re={re:.3g}" if re else "Cf@unknown",
                quantity="Cf",
                computed=_num(row.get("ittc57")),
                ref_recorded=_num(row.get("schoenherr")),
                method="ITTC-1957 line vs Schoenherr line",
                note="empirical-line cross-check (independent formulations)",
                unit="-",
                forced_baseline=Baseline(
                    value=_num(row.get("schoenherr")), low=None, high=None,
                    source="Schoenherr friction line (independent empirical formulation)",
                    evidence_grade="REAL", kind="regression",
                    citation="Schoenherr (1932)",
                    note="cross-check between two empirical lines, not an experiment",
                ),
            ))
    return out


def scan_baselineless_runs(root: Path) -> List[RawRecord]:
    """Batch sweeps whose rows recorded a result but no reference value.

    Each such row is a *plausibility bound* check (``criteria_detail``), not a
    benchmark comparison, so it must appear as WITHHELD and never as PASS.  Rows
    are aggregated per (file, category) — one aggregate entry states how many
    rows lacked a baseline instead of inflating the ledger with N identical
    "no baseline" statements.
    """
    root = Path(root)
    out: List[RawRecord] = []
    for path in iter_json_files(root):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        if not path.name.startswith("batch_report"):
            continue
        doc = _load(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("cases"), list):
            continue
        buckets: Dict[tuple, Dict[str, int]] = {}
        for row in doc["cases"]:
            if not isinstance(row, dict):
                continue
            cat = str(row.get("category") or "unknown")
            status = str(row.get("status") or "")
            key = (cat, "stub" if status != "success" else "withheld")
            b = buckets.setdefault(key, {"n": 0, "no_result": 0})
            b["n"] += 1
            if status != "success":
                b["no_result"] += 1
        for (cat, kind), b in sorted(buckets.items()):
            if kind == "stub":
                out.append(RawRecord(
                    source_file=rel,
                    adapter="baselineless_run",
                    family=family_of(rel),
                    case_id=f"batch/{cat}/failed_rows",
                    quantity=f"{cat}_batch_result",
                    computed=None,
                    method="batch sweep: failed rows",
                    note=f"{b['no_result']} of {b['n']} {cat} cases produced no result",
                    unit="-",
                    forced_grade="STUB",
                    forced_withheld_reason=(
                        f"stub: {b['no_result']} batch rows in category {cat!r} ended without a "
                        "numeric result -> nothing to compare"
                    ),
                ))
            else:
                out.append(RawRecord(
                    source_file=rel,
                    adapter="baselineless_run",
                    family=family_of(rel),
                    case_id=f"batch/{cat}/no_baseline_rows",
                    quantity=f"{cat}_batch_result",
                    computed=None,
                    method="batch sweep: bound check only",
                    note=(f"{b['n']} {cat} cases; criteria_detail carries plausibility bounds "
                          "only, no independent reference"),
                    unit="-",
                    forced_grade="WITHHELD",
                    forced_withheld_reason=(
                        f"no independent baseline: {b['n']} {cat!r} batch rows carry only "
                        "plausibility bounds (criteria_detail), which are not a benchmark reference"
                    ),
                ))
    return out



def _walk_with_case_ctx(node, ctx=None):
    """Yield (dict, nearest enclosing case-like dict)."""
    if isinstance(node, dict):
        new_ctx = ctx
        if any(k in node for k in ("case_id", "hull", "case")):
            new_ctx = node
        yield node, new_ctx
        for v in node.values():
            yield from _walk_with_case_ctx(v, new_ctx)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_with_case_ctx(v, ctx)


def scan_capability_audit_rows(root: Path) -> List[RawRecord]:
    """Capability audits that compare the platform against an independent stack.

    Row shape: ``{quantity, platform, reference, reference_basis, rel_err_pct}``
    nested in a case dict carrying ``hull`` / ``speed_kn``.

    Scope decision (declared, see REPORT §5): only the **outcome** quantities are
    recorded — total-resistance coefficient and total resistance force.  Derived
    components (RF/RR/PE) and definition re-computations (Re, Fn, Cb) are skipped:
    the components inherit the same defect and the definitions are identities, so
    recording them would multiply one defect into eight "evidence points".
    Rows the source itself labels *informational* are skipped as well.
    """
    root = Path(root)
    out: List[RawRecord] = []
    for path in iter_json_files(root):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        doc = _load(path)
        if doc is None:
            continue
        for row, ctx in _walk_with_case_ctx(doc):
            if not isinstance(row.get("quantity"), str):
                continue
            if "platform" not in row or "reference" not in row:
                continue
            qtxt = row["quantity"].lower()
            if "informational" in qtxt or "self-consistency" in str(row.get("reference_basis", "")).lower():
                continue
            if "total resistance coefficient" in qtxt:
                qty = "Ct"
            elif "total resistance" in qtxt:
                qty = "RT"
            else:
                continue
            plat = _num(row.get("platform"))
            ref = _num(row.get("reference"))
            if plat is None or ref is None:
                continue
            ctx = ctx or {}
            cid = (ctx.get("case_id") or
                   (f"{ctx.get('hull')}@{ctx.get('speed_kn')}kn"
                    if ctx.get("hull") and ctx.get("speed_kn") is not None else "unnamed"))
            basis = str(row.get("reference_basis") or "")
            note_bits = [f"reference_basis: {basis}" if basis else ""]
            rec_pct = _num(row.get("rel_err_pct"))
            if rec_pct is not None:
                mine = 100.0 * (plat - ref) / ref if ref else None
                if mine is not None and abs(mine - rec_pct) > 0.05:
                    note_bits.append(
                        f"source rel_err_pct={rec_pct:+.3f}% vs recomputed {mine:+.3f}% (discrepancy kept)")
                else:
                    note_bits.append(f"source rel_err_pct={rec_pct:+.3f}% agrees with recomputation")
            out.append(RawRecord(
                source_file=rel,
                adapter="capability_audit",
                family=family_of(rel),
                case_id=cid,
                quantity=qty,
                computed=plat,
                ref_recorded=ref,
                method=str((ctx.get("platform_method") or "")[:120]),
                note="; ".join(b for b in note_bits if b),
                ship_type=str(ctx.get("hull") or ""),
                unit="-" if qty == "Ct" else "N",
                forced_baseline=Baseline(
                    value=ref, low=None, high=None,
                    source=basis or "independent reference implementation",
                    evidence_grade="REAL", kind="reference_implementation",
                    note="verification reference stack (independent implementation), not an experiment",
                ),
            ))
    return out


SPECIAL_SCANNERS = (
    scan_verification_oracle,
    scan_capability_audit_rows,
    scan_propeller_tables,
    scan_friction_crosscheck,
    scan_baselineless_runs,
)


def scan_all(root: Path) -> List[RawRecord]:
    """Run every adapter, de-duplicating on (file, case, quantity, computed, ref)."""
    root = Path(root)
    records: List[RawRecord] = []
    seen = set()
    for fn in (scan_generic,) + SPECIAL_SCANNERS:
        for rec in fn(root):
            key = (rec.source_file, rec.case_id, rec.quantity, rec.computed, rec.ref_recorded)
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
    records.sort(key=lambda r: (r.source_file, r.case_id, r.quantity,
                                -1 if r.computed is None else r.computed))
    return records