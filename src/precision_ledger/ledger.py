"""Build the precision ledger: raw records -> adjudicated entries -> summary."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Dict, List, Optional

from .baselines import CanonicalRegistry, load_registry, ship_type_of
from .model import (ADOPTED_GATE_BASIS, GATE_BASIS_OPTIONS, GRADES, TOL_HARD,
                    TOL_TARGET, Baseline, Entry, within)
from .sources import RawRecord, scan_all

SCHEMA = "precision-ledger/1.0"

#: Owner anchor used for the band-gate adjudication (KPI-GATE-100 §3).
#: 0.003393 is the SUBOFF S0 Ct computed by the ITTC-1957 + axisymmetric-residual
#: path; ``artifacts/m1388/chain_results.json`` records 0.003392 for SUBOFF-01
#: (same value to 4 significant figures).
ANCHOR = {
    "case_id": "SUBOFF-S0",
    "quantity": "Ct",
    "computed": 0.003393,
    "band": [0.0035, 0.0040],
    "provenance": (
        "computed: artifacts/m1388/chain_results.json SUBOFF-01 calc_ct=0.003392 "
        "(ITTC-1957 friction + axisymmetric-body empirical residual), 0.003393 to 4 s.f.; "
        "band: Roddy 1990 DTRC-90/012 SUBOFF bare-hull Ct 0.0035-0.0040"
    ),
}

#: Automatic attribution of >15% violations.  First matching rule wins.
ATTRIBUTION_RULES = (
    ("RE_PHYSICS_INCONSISTENT", (
        "raw source computed Cf/Cr at a Reynolds number inconsistent with the canonical "
        "model scale (docs/m1444 run: Re=5.65e6 vs 1.31e7) and with an out-of-family "
        "residual model -> Ct inflated 2-3x")),
    ("SIMPLIFIED_HOLTROP_VARIANT", (
        "platform uses the simplified Cr = 0.002*(1+2Cb)*Fn^2.5 while the reference stack "
        "is a faithful Holtrop-1984 ((1+k)Cf+Cw+Ca) implementation -> total resistance "
        "off by ~2-3x on full-form hulls (KVLCC2/KCS/AFRAMAX)")),
    ("RESIDUAL_FN_BLOWUP", (
        "empirical residual term (Cr ~ Fn^2.5) evaluated far outside its fitted Froude "
        "range (Fr>0.8) -> Ct grows monotonically and diverges from the measured plateau")),
    ("HOLTROP_OUT_OF_DOMAIN", (
        "Holtrop-type method applied outside its validity box (submarine/axisymmetric hull "
        "or Fr>0.4); the method's residual term has no calibrated coefficient there")),
    ("WETTED_SURFACE_FORMULA", (
        "wet-surface estimate S = pi*B*L*(0.5+0.25*Cb) under-predicts full hulls "
        "(KVLCC2/DTMB-5415), so Ct/Rt are systematically low")),
    ("RESISTANCE_FORCE_BASIS", (
        "Rt compared against a force baseline whose rho/S basis differs from the run "
        "(legacy 12.5 N row vs canonical R1-derived force)")),
    ("PROPULSION_MODEL_FAMILY", (
        "propeller BEM kernel vs B-series regression: different model families, "
        "peak-efficiency gap")),
    ("SELF_REFERENTIAL_ORACLE", (
        "expected_result stored next to the run is bit-identical to actual_result -> "
        "verification oracle carries no independent information")),
)


def _family_of_path(p: str) -> str:
    parts = p.split("/")
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def attribute(entry: Entry) -> str:
    """Rule-based cause code for an entry (violations get a real cause code)."""
    if entry.evidence_grade == "WITHHELD":
        return "WITHHELD"
    if entry.evidence_grade == "STUB":
        return "STUB_NO_RESULT"
    if entry.evidence_grade == "NOT_SUPPORTED":
        return "NOT_SUPPORTED"
    if not entry.judged or entry.gate(TOL_HARD):
        return "BELOW_GATE"
    if entry.self_referential:
        return "SELF_REFERENTIAL_ORACLE"
    fam = entry.family
    ship = entry.ship_type
    fr = entry.fr
    qty = entry.quantity
    if fam.startswith("docs/m1444"):
        return "RE_PHYSICS_INCONSISTENT"
    if entry.adapter == "capability_audit":
        return "SIMPLIFIED_HOLTROP_VARIANT"
    if qty in ("Ct", "Rt"):
        if fr is not None and fr > 0.80:
            return "RESIDUAL_FN_BLOWUP"
        if ship in ("SUBOFF",) and fr is not None and fr > 0.40:
            return "HOLTROP_OUT_OF_DOMAIN"
        if ship in ("KVLCC2", "DTMB-5415"):
            return "WETTED_SURFACE_FORMULA"
        if qty == "Rt":
            return "RESISTANCE_FORCE_BASIS"
        return "HOLTROP_OUT_OF_DOMAIN"
    if ship == "propeller" or "eta0" in qty or qty in ("J0", "KT", "KQ", "eta"):
        return "PROPULSION_MODEL_FAMILY"
    return "UNATTRIBUTED"


def _resolve_baseline(rec: RawRecord, reg: CanonicalRegistry) -> Baseline:
    if rec.forced_baseline is not None:
        return rec.forced_baseline
    ship = rec.ship_type or ship_type_of(rec.case_id)
    if rec.quantity == "Ct":
        return reg.ct_baseline(rec.case_id, ship, rec.ref_recorded)
    if rec.quantity == "Rt":
        return reg.rt_baseline(rec.case_id, rec.ref_recorded)
    if rec.ref_recorded is None:
        return Baseline(
            value=None, low=None, high=None, source="none",
            evidence_grade="WITHHELD", kind="none",
            note="quantity has no registered baseline source",
        )
    return Baseline(
        value=rec.ref_recorded, low=None, high=None,
        source="ref value recorded in the source file",
        evidence_grade="REAL", kind="experiment",
        note="quantity-specific baseline, taken from the source record",
    )


def _grade(rec: RawRecord, baseline: Baseline) -> str:
    # an adapter-declared grade wins: it knows things the baseline alone cannot say
    # (self-referential oracle, out-of-scope comparison, aggregate row without a result)
    if rec.forced_grade in ("NOT_SUPPORTED", "WITHHELD", "STUB", "MOCK"):
        return rec.forced_grade
    if rec.computed is None:
        return "STUB"
    if rec.self_referential:
        return "MOCK"
    if baseline.value is None:
        return "WITHHELD"
    if baseline.evidence_grade != "REAL":
        return baseline.evidence_grade
    return "REAL"


PREFERRED_RESULT_NAMES = (
    "validation_results.json", "chain_results.json", "results.json",
    "benchmark_validation.json", "resistance_benchmark_validation.json",
    "batch_report.json", "verification_report.json", "verification_results.json",
)


def _path_rank(path: str):
    """Preference order for collapsing mirror copies of the same comparison."""
    name = path.rsplit("/", 1)[-1]
    raw_result = 0 if name in PREFERRED_RESULT_NAMES else 1
    derived = 1 if name in ("report_data.json", "report.json", "summary.json") else 0
    return (raw_result + derived, len(path), path)


def _evidence_identity(e: Entry):
    def q(v):
        return None if v is None else float(f"{float(v):.12g}")
    return (
        e.case_id, e.quantity, q(e.computed),
        q(e.baseline.value), q(e.baseline.low), q(e.baseline.high),
        e.evidence_grade,
    )


def dedupe_mirrors(entries: List[Entry]) -> List[Entry]:
    """Collapse byte-identical copies of one comparison into a single entry.

    Several missions keep evidence *copies* of a run (``report_data.json`` next
    to ``validation_results.json``, ``docs/board_audit/.../evidence/artifacts/``
    mirrors, ``backend/artifacts/`` duplicate).  Identical evidence must not be
    counted twice, otherwise the pass rate becomes a function of how many copies
    a mission happened to write.  The preferred path wins, the others are kept in
    ``entry.mirrors`` for provenance.
    """
    groups: Dict[tuple, List[Entry]] = {}
    for e in entries:
        groups.setdefault(_evidence_identity(e), []).append(e)
    out: List[Entry] = []
    for _, group in groups.items():
        group.sort(key=lambda e: _path_rank(e.source_file))
        keep = group[0]
        keep.mirrors = sorted(e.source_file for e in group[1:])
        out.append(keep)
    out.sort(key=lambda x: (x.family, x.case_id, x.quantity))
    return out


def build_entries(root: Path, records: Optional[List[RawRecord]] = None) -> List[Entry]:
    root = Path(root)
    reg = load_registry(root)
    if records is None:
        records = scan_all(root)
    entries: List[Entry] = []
    used_ids = set()
    for rec in records:
        baseline = _resolve_baseline(rec, reg)
        grade = _grade(rec, baseline)
        ship = rec.ship_type or ship_type_of(rec.case_id)
        base_id = f"{rec.source_file}|{rec.case_id}|{rec.quantity}"
        entry_id = base_id
        n = 2
        while entry_id in used_ids:
            entry_id = f"{base_id}#{n}"
            n += 1
        used_ids.add(entry_id)
        reason = rec.forced_withheld_reason
        if grade == "WITHHELD" and not reason:
            reason = baseline.note or "no verifiable baseline"
        if grade == "STUB" and not reason:
            reason = "no numeric computed value in the source record"
        e = Entry(
            entry_id=entry_id,
            case_id=rec.case_id,
            quantity=rec.quantity,
            computed=rec.computed,
            baseline=baseline,
            evidence_grade=grade,
            source_file=rec.source_file,
            adapter=rec.adapter,
            family=rec.family or _family_of_path(rec.source_file),
            unit=rec.unit,
            method=rec.method,
            note=rec.note,
            withheld_reason=reason,
            ship_type=ship,
            fr=rec.fr,
            ref_as_recorded=rec.ref_recorded,
            self_referential=rec.self_referential,
        )
        e.attribution = attribute(e)
        entries.append(e)
    return dedupe_mirrors(entries)


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 2) if d else 0.0


def gate_adjudication() -> dict:
    """Worked example: SUBOFF S0 Ct=0.003393 against the 0.0035-0.0040 band."""
    c = ANCHOR["computed"]
    lo, hi = ANCHOR["band"]
    mid = 0.5 * (lo + hi)
    rel_low = (c - lo) / lo
    rel_mid = (c - mid) / mid
    rel_high = (c - hi) / hi
    options = {}
    options["endpoints"] = {
        "definition": GATE_BASIS_OPTIONS["endpoints"],
        "rel_err_low": rel_low,
        "rel_err_high": rel_high,
        "statistic": max(abs(rel_low), abs(rel_high)),
        "pass_15pct": within(max(abs(rel_low), abs(rel_high)), TOL_HARD),
        "pass_3pct": within(max(abs(rel_low), abs(rel_high)), TOL_TARGET),
        "issue": "判据随带宽放大：带越宽越难/越易取决于选哪个端点，且端点选择任意",
    }
    options["mid"] = {
        "definition": GATE_BASIS_OPTIONS["mid"],
        "rel_err_mid": rel_mid,
        "statistic": abs(rel_mid),
        "pass_15pct": within(rel_mid, TOL_HARD),
        "pass_3pct": within(rel_mid, TOL_TARGET),
        "issue": "带宽信息不进判据（仍以 rel_err_low/high + band_contained 逐条披露）",
    }
    contained = lo <= c <= hi
    options["containment"] = {
        "definition": GATE_BASIS_OPTIONS["containment"],
        "contained": contained,
        "statistic": 0.0 if contained else min(abs(rel_low), abs(rel_high)),
        "pass_15pct": contained,
        "pass_3pct": contained,
        "issue": "带宽被吸收进判据：带内 +9.5% 也判过，无法表达 3% 目标，跨 case 不可聚合",
    }
    return {
        "anchor": {
            "case_id": ANCHOR["case_id"],
            "quantity": ANCHOR["quantity"],
            "computed": c,
            "baseline_low": lo,
            "baseline_high": hi,
            "baseline_mid": mid,
            "provenance": ANCHOR["provenance"],
        },
        "options": options,
        "adopted": ADOPTED_GATE_BASIS,
        "rationale": (
            "采用中值口径：(1) 15%/3% 阈值本身以单一参照值定义，相对误差必须有唯一参照点；"
            "(2) 端点口径的难度随带宽与端点选择漂移（同一 9.5% 偏差在不同带下判决相反）；"
            "(3) 带内包含口径把带宽吸收为免费额度，带内 9.5% 与带外 0.1% 同判，无法表达 3% 目标；"
            "(4) 中值口径与带宽解耦、可跨 case 聚合，同时 rel_err_low/high 与 band_contained 仍逐条记录，"
            "带宽信息不丢失。band_contained 作为辅助字段披露但不参与判决。"
        ),
        "auxiliary": "band_contained / rel_err_low / rel_err_high 逐条记录，辅助（非约束）判决字段 aux_pass_15pct_endpoints / aux_pass_15pct_containment",
    }


def summarize(entries: List[Entry], root: Path) -> dict:
    reg = load_registry(Path(root))
    by_grade = {g: 0 for g in GRADES}
    for e in entries:
        by_grade[e.evidence_grade] = by_grade.get(e.evidence_grade, 0) + 1
    judged = [e for e in entries if e.judged]
    judged_real = [e for e in judged if e.evidence_grade == "REAL"]
    p15 = [e for e in judged if e.gate(TOL_HARD)]
    p3 = [e for e in judged if e.gate(TOL_TARGET)]
    p15_real = [e for e in judged_real if e.gate(TOL_HARD)]
    p3_real = [e for e in judged_real if e.gate(TOL_TARGET)]

    by_family: Dict[str, Dict[str, int]] = {}
    for e in entries:
        d = by_family.setdefault(e.family, {"entries": 0, "judged": 0, "pass_15": 0, "pass_3": 0})
        d["entries"] += 1
        if e.judged:
            d["judged"] += 1
            d["pass_15"] += int(e.gate(TOL_HARD))
            d["pass_3"] += int(e.gate(TOL_TARGET))

    by_quantity: Dict[str, Dict[str, int]] = {}
    for e in entries:
        d = by_quantity.setdefault(e.quantity, {"entries": 0, "judged": 0, "pass_15": 0, "pass_3": 0})
        d["entries"] += 1
        if e.judged:
            d["judged"] += 1
            d["pass_15"] += int(e.gate(TOL_HARD))
            d["pass_3"] += int(e.gate(TOL_TARGET))

    # ship-type / family statistics used for the accuracy verdict are computed over
    # REAL-baseline judged entries only: MOCK (self-referential) entries must not
    # influence a statement about accuracy.
    by_ship: Dict[str, Dict[str, int]] = {}
    fam_real: Dict[str, Dict[str, int]] = {}
    for e in judged_real:
        key = e.ship_type or "n/a"
        d = by_ship.setdefault(key, {"judged": 0, "pass_15": 0, "pass_3": 0})
        d["judged"] += 1
        d["pass_15"] += int(e.gate(TOL_HARD))
        d["pass_3"] += int(e.gate(TOL_TARGET))
        f = fam_real.setdefault(e.family, {"judged": 0, "pass_15": 0, "pass_3": 0})
        f["judged"] += 1
        f["pass_15"] += int(e.gate(TOL_HARD))
        f["pass_3"] += int(e.gate(TOL_TARGET))

    def _fam_stats(pick):
        eligible = [(fam, v) for fam, v in fam_real.items() if v["judged"] >= 4]
        if not eligible:
            return None
        fam, v = pick(eligible, key=lambda kv: (kv[1]["pass_15"] / kv[1]["judged"],
                                                kv[1]["pass_3"] / kv[1]["judged"]))
        return {
            "family": fam,
            "judged": v["judged"],
            "pass_15": v["pass_15"],
            "pass_3": v["pass_3"],
            "pass_15_rate": round(100.0 * v["pass_15"] / v["judged"], 2),
            "pass_3_rate": round(100.0 * v["pass_3"] / v["judged"], 2),
        }

    best_family = _fam_stats(max)
    worst_family = _fam_stats(min)

    violations = sorted(
        (e for e in judged if not e.gate(TOL_HARD)),
        key=lambda e: -abs(e.rel_err()["mid"] or 0.0),
    )
    att_counts: Dict[str, int] = {}
    for e in entries:
        att_counts[e.attribution] = att_counts.get(e.attribution, 0) + 1

    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan_root": str(root),
        "gate": {
            "basis": ADOPTED_GATE_BASIS,
            "basis_options": GATE_BASIS_OPTIONS,
            "tol_hard": TOL_HARD,
            "tol_target": TOL_TARGET,
            "pass_rule": "pass iff |rel_err_mid| <= tol (mid = baseline.value)",
            "withheld_rule": "WITHHELD/STUB/NOT_SUPPORTED never carry a PASS verdict",
        },
        "canonical_registry": reg.meta,
        "gate_adjudication": gate_adjudication(),
        "totals": {
            "entries": len(entries),
            "mirror_copies_collapsed": sum(len(e.mirrors) for e in entries),
            "distinct_cases": len({e.case_id for e in judged}),
            "distinct_case_quantity": len({(e.case_id, e.quantity) for e in judged}),
            "judged": len(judged),
            "judged_real": len(judged_real),
            "by_grade": by_grade,
            "by_quantity": by_quantity,
            "by_family": by_family,
            "by_ship_type": by_ship,
            "by_family_real": fam_real,
            "best_real_family": best_family,
            "worst_real_family": worst_family,
        },
        "gate_results": {
            "denominator": len(judged),
            "denominator_real": len(judged_real),
            "pass_15pct": len(p15),
            "pass_15pct_rate": _pct(len(p15), len(judged)),
            "pass_3pct": len(p3),
            "pass_3pct_rate": _pct(len(p3), len(judged)),
            "pass_15pct_real": len(p15_real),
            "pass_15pct_rate_real": _pct(len(p15_real), len(judged_real)),
            "pass_3pct_real": len(p3_real),
            "pass_3pct_rate_real": _pct(len(p3_real), len(judged_real)),
            "violations": len(violations),
        },
        "attribution_counts": dict(sorted(att_counts.items(), key=lambda kv: -kv[1])),
        "violations": [
            {
                "entry_id": e.entry_id,
                "case_id": e.case_id,
                "quantity": e.quantity,
                "ship_type": e.ship_type,
                "computed": e.computed,
                "baseline_value": e.baseline.value,
                "rel_err_mid": e.rel_err()["mid"],
                "abs_rel_err_mid": abs(e.rel_err()["mid"] or 0.0),
                "attribution": e.attribution,
                "source_file": e.source_file,
                "evidence_grade": e.evidence_grade,
            }
            for e in violations
        ],
        "withheld": [
            {
                "entry_id": e.entry_id,
                "case_id": e.case_id,
                "quantity": e.quantity,
                "computed": e.computed,
                "evidence_grade": e.evidence_grade,
                "reason": e.withheld_reason,
                "source_file": e.source_file,
                "mirrors": list(e.mirrors),
            }
            for e in entries if e.evidence_grade in ("WITHHELD", "STUB", "NOT_SUPPORTED")
        ],
        "entries": [e.as_dict() for e in entries],
    }


def build_ledger(root: Path, records: Optional[List[RawRecord]] = None) -> dict:
    root = Path(root)
    entries = build_entries(root, records)
    return summarize(entries, root)


def write_ledger(ledger: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path