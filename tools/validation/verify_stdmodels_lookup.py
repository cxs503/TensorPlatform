#!/usr/bin/env python3
"""Independent verification of the ``stdmodels.lookup`` capability (mission m1876).

The platform's ``audit_capabilities`` grades ``stdmodels.lookup`` as REAL, yet the
validation-debt ledger (``docs/research/validation_debt_ledger.md``, m1705) attributes
**29 hard debts** (STUB 3 / MOCK 1 / WITHHELD 25) to that capability and lists it as
"identified but untouched".  This tool closes that gap with an *outside* yardstick:

* **R1 literature anchors** — benchmark hulls/propellers compared field by field
  against published values (``references_stdmodels.py``), never against the platform.
* **R2 hull-form identity** — ``Cb == Cm * Cp`` (definition of the block coefficient).
* **R3 displacement / density consistency** — ``displacement / (Lpp*B*T*Cb)`` must be a
  physically possible mass density (1.025 t/m^3 sea water, 1.000 t/m^3 model tank).
* **R4 registry integrity** — referential integrity against ``standard-models/types``,
  cross-category contamination, geometric sanity, duplicate names.
* **R5 auxiliary registries** — spectrum ids / regulation codes must be ones that exist
  in the published literature and rule sets.
* **R6 propeller schema** — the propeller registry must expose propeller particulars
  (D, P/D, A_E/A_O); hull columns repurposed as propeller data are not a schema.

Read-only and offline-first: the default input is a frozen snapshot
(``tests/fixtures/stdmodels/stdmodels_snapshot.json``) so the verdict is reproducible
without a live service; ``--live`` re-fetches from the API.

Exit codes: 0 = PASS, 1 = FAIL, 2 = WITHHELD (input unavailable).

Usage
-----
    python3 tools/validation/verify_stdmodels_lookup.py                 # snapshot
    python3 tools/validation/verify_stdmodels_lookup.py --live          # :8001
    python3 tools/validation/verify_stdmodels_lookup.py --json out.json --md out.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from references_stdmodels import (  # noqa: E402
    KNOWN_REGULATION_CODES,
    KNOWN_SPECTRA,
    PROPELLER_ANCHORS,
    PROPELLER_COLUMNS,
    SHIP_ANCHORS,
    TOL_CB_ABS,
    TOL_DENSITY_REL,
    TOL_DENSITY_WARN,
    TOL_PASS,
    TOL_REJECT,
    reference_density,
    rel_error,
    unit_scale_diagnosis,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_SNAPSHOT = os.path.join(REPO, "tests", "fixtures", "stdmodels",
                                "stdmodels_snapshot.json")
DEFAULT_API = "http://127.0.0.1:8001/api/v1"

LIST_ENDPOINTS = {
    "ships": "standard-models/ships",
    "propellers": "standard-models/propellers",
    "platforms": "standard-models/platforms",
    "types": "standard-models/types",
    "regulations": "standard-models/regulations",
    "spectrums": "standard-models/wave-spectrums",
}

GRADE_ORDER = {"PASS": 0, "WITHHELD": 1, "FAIL": 2}

SUBMARINE_HINTS = ("submarine", "suboff", "type 209", "virginia class", "drea",
                   "joubert", "bb2")


# --------------------------------------------------------------------------- input
def fetch_live(base: str, timeout: float = 20.0) -> dict:
    """Fetch every registry endpoint and return a snapshot-shaped dict."""
    snap = {"fetched_utc": _dt.datetime.utcnow().isoformat() + "Z", "base": base,
            "endpoints": {}}
    for key, path in LIST_ENDPOINTS.items():
        url = base.rstrip("/") + "/" + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                snap["endpoints"][key] = {"status": r.status, "path": path,
                                          "body": json.load(r)}
        except (urllib.error.URLError, OSError, ValueError) as exc:  # pragma: no cover
            snap["endpoints"][key] = {"status": 0, "path": path, "error": str(exc)}
    return snap


def load_snapshot(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _rows(snap: dict, key: str) -> list:
    """Rows of the first (and only) list in an endpoint body."""
    ep = snap.get("endpoints", {}).get(key, {})
    body = ep.get("body") or {}
    if isinstance(body, list):
        return body
    for value in body.values():
        if isinstance(value, list):
            return value
    return []


def _missing_endpoints(snap: dict) -> list:
    return [k for k in LIST_ENDPOINTS
            if snap.get("endpoints", {}).get(k, {}).get("status") != 200]


def _unique(rows: list) -> list:
    """Drop exact duplicate rows (the FPSO/platform entries live in two lists)."""
    seen, out = set(), []
    for r in rows:
        key = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


# -------------------------------------------------------------------------- checks
def _grade(err: float) -> str:
    err = abs(err)
    if err <= TOL_PASS:
        return "PASS"
    return "FAIL" if err > TOL_REJECT else "WITHHELD"


def check_literature_anchors(ships: list, propellers: list) -> dict:
    """R1 — published benchmark particulars vs registry values."""
    by_name = {r.get("name"): r for r in ships}
    fields = []
    for name, spec in SHIP_ANCHORS.items():
        row = by_name.get(name)
        if row is None:
            fields.append({"case": name, "field": "*", "reference": None, "value": None,
                           "rel_error": None, "verdict": "WITHHELD",
                           "source": next(iter(spec.values()))[1],
                           "note": "benchmark hull absent from registry"})
            continue
        for field, (ref, source) in spec.items():
            if field == "displacement_volume":
                disp = float(row.get("displacement") or 0.0)
                rho = reference_density(float(row.get("Lpp") or 0.0))
                value = disp / rho if rho else 0.0
                label = "displacement/rho"
            else:
                raw = row.get(field)
                value = None if raw is None else float(raw)
                label = field
            if value is None:
                fields.append({"case": name, "field": label, "reference": ref,
                               "value": None, "rel_error": None,
                               "verdict": "WITHHELD", "source": source,
                               "note": "field missing"})
                continue
            err = rel_error(value, ref)
            fields.append({"case": name, "field": label, "reference": ref,
                           "value": value, "rel_error": err, "verdict": _grade(err),
                           "source": source, "note": ""})

    prop_by_name = {r.get("name"): r for r in propellers}
    for name, spec in PROPELLER_ANCHORS.items():
        row = prop_by_name.get(name)
        for field, (ref, source) in spec.items():
            if row is None or row.get(field) is None:
                fields.append({"case": name, "field": field, "reference": ref,
                               "value": None, "rel_error": None,
                               "verdict": "WITHHELD", "source": source,
                               "note": "propeller field absent from registry"})
                continue
            err = rel_error(float(row[field]), ref)
            fields.append({"case": name, "field": field, "reference": ref,
                           "value": float(row[field]), "rel_error": err,
                           "verdict": _grade(err), "source": source, "note": ""})

    graded = [f for f in fields if f["verdict"] != "WITHHELD"]
    verdict = "WITHHELD"
    if graded:
        verdict = "PASS" if all(f["verdict"] == "PASS" for f in graded) else "FAIL"
    return {"id": "R1", "name": "literature anchors", "verdict": verdict,
            "fields": fields,
            "worst_abs_rel_error": max((abs(f["rel_error"]) for f in graded),
                                       default=None),
            "n_pass": sum(1 for f in fields if f["verdict"] == "PASS"),
            "n_fields": len(fields)}


def check_cb_identity(rows: list) -> dict:
    """R2 — ``Cb == Cm * Cp`` for every hull entry."""
    rows = _unique(rows)
    bad = []
    for r in rows:
        try:
            cb, cm, cp = float(r["Cb"]), float(r["Cm"]), float(r["Cp"])
        except (KeyError, TypeError, ValueError):
            bad.append({"name": r.get("name"), "reason": "Cb/Cm/Cp not numeric"})
            continue
        if abs(cb - cm * cp) > TOL_CB_ABS:
            bad.append({"name": r.get("name"), "Cb": cb, "Cm": cm, "Cp": cp,
                        "Cm_times_Cp": cm * cp, "abs_diff": abs(cb - cm * cp)})
    return {"id": "R2", "name": "Cb = Cm*Cp identity",
            "verdict": "PASS" if not bad else "FAIL",
            "n_checked": len(rows), "n_violations": len(bad), "violations": bad}


def _classify(row: dict) -> str:
    name = str(row.get("name", "")).lower()
    typ = str(row.get("type", "")).lower()
    if "submarine" in typ or any(h in name for h in SUBMARINE_HINTS):
        return "submarine"
    if float(row.get("Lpp") or 0.0) <= 10.0:
        return "model_scale"
    return "surface"


def check_displacement_density(rows: list) -> dict:
    """R3 — implied mass density must be physically possible.

    Two tiers, aligned with the owner standard: a deviation beyond 15 % is a hard
    FAIL; 5–15 % is a soft WARN (documented, not fatal).
    """
    rows = _unique(rows)
    checked, hard, soft = [], [], []
    for r in rows:
        try:
            lpp, b, t = float(r["Lpp"]), float(r["B"]), float(r["T"])
            cb = float(r["Cb"])
            disp = float(r.get("displacement") or 0.0)
        except (KeyError, TypeError, ValueError):
            hard.append({"name": r.get("name"), "reason": "non-numeric geometry"})
            continue
        vol = lpp * b * t * cb
        if disp <= 0.0 or vol <= 0.0:
            continue  # no displacement claim -> nothing to adjudicate
        implied = disp / vol  # t/m^3
        expect = reference_density(lpp)
        err = rel_error(implied, expect)
        rec = {"name": r.get("name"), "category": _classify(r),
               "volume_m3": vol, "displacement": disp, "implied_density": implied,
               "expected_density": expect, "rel_error": err,
               "unit_scale": unit_scale_diagnosis(implied / expect)}
        checked.append(rec)
        if abs(err) > TOL_DENSITY_REL:
            hard.append(rec)
        elif abs(err) > TOL_DENSITY_WARN:
            soft.append(rec)
    return {"id": "R3", "name": "displacement/density consistency",
            "verdict": "PASS" if not hard else "FAIL",
            "n_checked": len(checked), "n_violations": len(hard),
            "n_warnings": len(soft),
            "violations": sorted(hard, key=lambda x: -abs(x.get("rel_error") or 0.0)),
            "warnings": sorted(soft, key=lambda x: -abs(x.get("rel_error") or 0.0)),
            "max_abs_rel_error": max((abs(v.get("rel_error") or 0.0) for v in hard),
                                     default=0.0)}


def check_registry_integrity(snap: dict, ships: list, propellers: list,
                            platforms: list) -> dict:
    """R4 — referential integrity, cross-category contamination, geometric sanity."""
    known_types = {str(t) for t in _rows(snap, "types")}
    prop_names = {r.get("name") for r in propellers}
    ship_names = {r.get("name") for r in ships}

    unknown_type, missing_source, nonphysical, dup_within = [], [], [], []
    contam = sorted(prop_names & ship_names)

    for cat, rows in (("ships", ships), ("propellers", propellers),
                      ("platforms", platforms)):
        local = {}
        for r in rows:
            name = r.get("name")
            local[name] = local.get(name, 0) + 1
            if known_types and r.get("type") not in known_types:
                unknown_type.append({"category": cat, "name": name,
                                     "type": r.get("type")})
            if not r.get("source"):
                missing_source.append({"category": cat, "name": name})
            if cat == "ships" and name in prop_names:
                continue  # contamination is reported once, under its own heading
            try:
                lpp, b, t = float(r["Lpp"]), float(r["B"]), float(r["T"])
                cb, cm, cp = float(r["Cb"]), float(r["Cm"]), float(r["Cp"])
            except (KeyError, TypeError, ValueError):
                nonphysical.append({"category": cat, "name": name,
                                    "reason": "non-numeric geometry"})
                continue
            reasons = []
            if not (0.0 < cb <= 1.0):
                reasons.append(f"Cb={cb}")
            if not (0.0 < cm <= 1.0):
                reasons.append(f"Cm={cm}")
            if not (0.0 < cp <= 1.0):
                reasons.append(f"Cp={cp}")
            if lpp <= 0.0:
                reasons.append(f"Lpp={lpp}")
            if b <= 0.0 or b > lpp:
                reasons.append(f"B={b} vs Lpp={lpp}")
            # T < B is *not* required (a spar buoy legitimately draws more than its
            # diameter), and a deep-draft offshore unit may also draw more than its
            # length: the T <= Lpp rule therefore applies to conventional hulls only.
            deep_draft = ("offshore" in str(r.get("type", "")).lower()
                          or "spar" in str(r.get("name", "")).lower())
            if t <= 0.0 or (t > lpp and not deep_draft):
                reasons.append(f"T={t} vs Lpp={lpp}")
            if reasons:
                nonphysical.append({"category": cat, "name": name, "reasons": reasons})
        dup_within.extend({"category": cat, "name": n, "count": c}
                          for n, c in local.items() if c > 1)

    seen = {}
    for cat, rows in (("ships", ships), ("propellers", propellers),
                      ("platforms", platforms)):
        for r in rows:
            seen.setdefault(r.get("name"), []).append(cat)
    cross_dup = [{"name": n, "categories": sorted(set(c))}
                 for n, c in sorted(seen.items()) if len(set(c)) > 1]

    violations = {"propeller_in_ship_list": contam, "geometry_nonphysical": nonphysical,
                  "duplicate_name_within_list": dup_within, "unknown_type": unknown_type}
    n_hard = sum(len(v) for v in violations.values())
    return {"id": "R4", "name": "registry integrity",
            "verdict": "PASS" if n_hard == 0 else "FAIL",
            "n_hard_violations": n_hard, "violations": violations,
            "soft": {"cross_category_duplicate": cross_dup,
                     "missing_source": missing_source}}


def check_auxiliary_registries(snap: dict) -> dict:
    """R5 — spectra / regulations registries vs published names."""
    spectra = [str(s.get("id")) for s in _rows(snap, "spectrums")]
    regs = [str(r.get("code")) for r in _rows(snap, "regulations")]
    unknown_spectra = [s for s in spectra if s not in KNOWN_SPECTRA]
    unknown_regs = [c for c in regs if c not in KNOWN_REGULATION_CODES]
    dup = sorted({s for s in spectra if spectra.count(s) > 1})
    ok = not unknown_spectra and not unknown_regs and not dup
    return {"id": "R5", "name": "auxiliary registries",
            "verdict": "PASS" if ok else "FAIL",
            "spectra": spectra, "regulations": regs,
            "unknown_spectra": unknown_spectra,
            "unknown_regulation_codes": unknown_regs, "duplicate_spectra": dup}


def check_propeller_schema(propellers: list) -> dict:
    """R6 — does the propeller registry carry propeller particulars?"""
    if not propellers:
        return {"id": "R6", "name": "propeller schema", "verdict": "WITHHELD",
                "reason": "propeller registry empty", "columns_present": []}
    present = sorted({c for r in propellers for c in PROPELLER_COLUMNS if c in r})
    core = [c for c in ("diameter", "D", "P_over_D", "EAR") if c in present]
    return {"id": "R6", "name": "propeller schema",
            "verdict": "PASS" if core else "FAIL",
            "columns_present": present, "core_columns_found": core,
            "has_diameter": any(c in present for c in ("diameter", "D")),
            "has_pitch_ratio": "P_over_D" in present,
            "has_expanded_area_ratio": any(c in present for c in ("EAR", "A_E_A_O")),
            "example_columns": sorted(propellers[0].keys())}


# ------------------------------------------------------------------------ driver
def run(snap: dict) -> dict:
    """Run every check and aggregate the verdicts."""
    ships = _rows(snap, "ships")
    propellers = _rows(snap, "propellers")
    platforms = _rows(snap, "platforms")
    missing = _missing_endpoints(snap)

    checks = [
        check_literature_anchors(ships, propellers),
        check_cb_identity(ships + platforms),
        check_displacement_density(ships + platforms),
        check_registry_integrity(snap, ships, propellers, platforms),
        check_auxiliary_registries(snap),
        check_propeller_schema(propellers),
    ]
    if missing:
        checks.insert(0, {"id": "R0", "name": "endpoint reachability",
                          "verdict": "WITHHELD", "missing": missing})

    worst = max((c["verdict"] for c in checks), key=lambda v: GRADE_ORDER[v])
    return {
        "mission": "m1876",
        "capability": "stdmodels.lookup",
        "generated_utc": _dt.datetime.utcnow().isoformat() + "Z",
        "snapshot_utc": snap.get("fetched_utc"),
        "source": snap.get("base") or "snapshot",
        "counts": {"ships": len(ships), "propellers": len(propellers),
                   "platforms": len(platforms),
                   "types": len(_rows(snap, "types")),
                   "regulations": len(_rows(snap, "regulations")),
                   "spectrums": len(_rows(snap, "spectrums"))},
        "checks": checks,
        "verdict": worst,
        "tolerances": {"pass_rel": TOL_PASS, "reject_rel": TOL_REJECT,
                       "cb_identity_abs": TOL_CB_ABS,
                       "density_warn_rel": TOL_DENSITY_WARN,
                       "density_fail_rel": TOL_DENSITY_REL},
    }


def render_markdown(report: dict) -> str:
    c = report["counts"]
    lines = [
        "# stdmodels.lookup 独立复核（m1876）", "",
        f"- verdict: **{report['verdict']}**",
        f"- snapshot: `{report['snapshot_utc']}` ← `{report['source']}`",
        f"- registry 规模: ships {c['ships']} / propellers {c['propellers']} / "
        f"platforms {c['platforms']} / types {c['types']}", "",
        "| 判据 | 内容 | 判决 | 明细 |", "|---|---|---|---|",
    ]
    for chk in report["checks"]:
        detail = ""
        if chk["id"] == "R1":
            worst = chk["worst_abs_rel_error"]
            wtext = "n/a" if worst is None else f"{worst:.4%}"
            detail = f"最差 |误差| {wtext}；PASS {chk['n_pass']}/{chk['n_fields']}"
        elif chk["id"] == "R2":
            detail = f"{chk['n_checked']} 条，违反 {chk['n_violations']}"
        elif chk["id"] == "R3":
            detail = (f"{chk['n_checked']} 条可判，硬违反 {chk['n_violations']}，"
                      f"警告 {chk['n_warnings']}，最差 {chk['max_abs_rel_error']:.2%}")
        elif chk["id"] == "R4":
            detail = f"硬违反 {chk['n_hard_violations']}"
        elif chk["id"] == "R5":
            detail = (f"未知谱 {chk['unknown_spectra']} / "
                      f"未知规则码 {chk['unknown_regulation_codes']}")
        elif chk["id"] == "R6":
            detail = f"核心列 {chk.get('core_columns_found')}"
        else:
            detail = f"缺失端点 {chk.get('missing')}"
        lines.append(f"| {chk['id']} | {chk['name']} | {chk['verdict']} | {detail} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true",
                    help="fetch from the live API instead of the frozen snapshot")
    ap.add_argument("--base", default=DEFAULT_API)
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--json", default=None, help="write the report JSON here")
    ap.add_argument("--md", default=None, help="write the markdown summary here")
    args = ap.parse_args(argv)

    if args.live:
        snap = fetch_live(args.base)
    else:
        try:
            snap = load_snapshot(args.snapshot)
        except OSError as exc:
            print(f"WITHHELD: cannot read snapshot ({exc})", file=sys.stderr)
            return 2

    report = run(snap)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))

    print(json.dumps({"verdict": report["verdict"],
                      "checks": {c["id"]: c["verdict"] for c in report["checks"]}},
                     ensure_ascii=False))
    return {"PASS": 0, "FAIL": 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())