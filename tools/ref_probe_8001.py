#!/usr/bin/env python3
"""Probe the live ship-performance platform (:8001) against the domain anchors.

The probe is deliberately *anchor driven*: it loads the three anchor documents
under artifacts/ref/anchors/ and turns every platform answer into a machine
readable verdict record

    {subsystem, status_code, quantity, value, anchor, rel_err, verdict, ts, ...}

with verdict in {PASS, FAIL, ERROR}.  A HTTP 5xx / traceback is recorded as
FAIL while keeping status_code, because "the endpoint crashed" is a result the
regression gate must see (not an excuse to skip the probe).

Emitted artifact: artifacts/ref/probe_verdicts_r1.json

Usage:
    python3 tools/ref_probe_8001.py
    python3 tools/ref_probe_8001.py --base-url http://127.0.0.1:8001 --out x.json
Exit status is 0 whenever the verdict file was written (the verdicts themselves
carry the pass/fail information); pass --strict to exit 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ANCHOR_DIR = os.path.join(ROOT, "artifacts", "ref", "anchors")
DEFAULT_OUT = os.path.join(ROOT, "artifacts", "ref", "probe_verdicts_r1.json")
DEFAULT_BASE = "http://127.0.0.1:8001"

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"
# domain evidence level (defect verdicts); WITHHELD = platform unreachable, never PASS
WITHHELD = "WITHHELD"

# platform payloads used for the three subsystems (all within anchor validity)
KVLCC2_REQUEST = {
    "length": 320.0, "beam": 58.0, "draft": 20.8,
    "displacement": 312622.0, "cb": 0.8098, "speed": 15.5,
    "water_density": 1025.0, "water_viscosity": 1.19e-6,
}
ITTCT8_REQUEST = {"Lwl": 320.0, "B": 58.0, "T": 20.8, "displacement": 312622.0,
                  "Cb": 0.8098, "Cm": 0.9950, "wetted_surface": 27194.0,
                  "speed": 15.5, "form_factor": 1.15}
SPECTRUM_REQUEST = {"Hs": 5.0, "Tp": 9.5, "spectrum_type": "ITTC", "N": 3}


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------
def http_request(path, body=None, method=None, base_url=DEFAULT_BASE, timeout=30):
    """Return (status_code, text).  Transport failures come back as (None, msg)."""
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # connection refused, timeout, DNS, ...
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _load_anchor(name):
    with open(os.path.join(ANCHOR_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)["payload"]


def _entries_by_id(payload):
    return {e["id"]: e for e in payload["entries"] if not e.get("divergent")}


def _record(subsystem, status_code, quantity, value, anchor, rel_err, verdict,
            probe_path, **extra):
    rec = {
        "subsystem": subsystem,
        "status_code": status_code,
        "quantity": quantity,
        "value": value,
        "anchor": anchor,
        "rel_err": rel_err,
        "verdict": verdict,
        "probe_path": probe_path,
        "ts": _now(),
    }
    rec.update(extra)
    return rec


def _rel_err(value, anchor):
    if value is None or anchor in (None, 0):
        return None
    try:
        return abs(float(value) - float(anchor)) / abs(float(anchor))
    except (TypeError, ValueError):
        return None


def _find_first(obj, keys):
    """Depth-first search for the first numeric value under any of ``keys``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        for v in obj.values():
            got = _find_first(v, keys)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_first(v, keys)
            if got is not None:
                return got
    return None


def _find_pairs(obj, xkeys, ykeys):
    """Extract (x, y) sample pairs from a spectrum-like JSON structure."""
    pairs = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and v and all(isinstance(i, (int, float)) for i in v):
                lk = k.lower()
                if lk in xkeys or ("freq" in lk) or ("omega" in lk) or lk in ("w", "f"):
                    pairs.append(("x", [float(i) for i in v]))
                elif lk in ykeys or ("densit" in lk) or ("spect" in lk) or lk in ("s", "s_w"):
                    pairs.append(("y", [float(i) for i in v]))
            elif isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
                xs = [i.get("omega", i.get("w", i.get("frequency", i.get("f")))) for i in v]
                ys = [i.get("S", i.get("density", i.get("S_w", i.get("spectrum")))) for i in v]
                if all(isinstance(i, (int, float)) for i in xs) and \
                        all(isinstance(i, (int, float)) for i in ys):
                    pairs.append(("xy", list(zip([float(i) for i in xs], [float(i) for i in ys]))))
            else:
                pairs.extend(_find_pairs(v, xkeys, ykeys))
    elif isinstance(obj, list):
        for v in obj:
            pairs.extend(_find_pairs(v, xkeys, ykeys))
    return pairs


def parse_spectrum_m0(text):
    """Best-effort m0 extraction from a wave-spectrum response body."""
    try:
        body = json.loads(text)
    except Exception:
        return None
    direct = _find_first(body, {"m0", "m_0", "area", "variance"})
    if direct is not None:
        return direct
    pairs = _find_pairs(body, {"omega", "w", "freq", "frequency"}, {"s", "s_w"})
    xs, ys = [], []
    for kind, val in pairs:
        if kind == "x" and not xs:
            xs = val
        elif kind == "y" and not ys:
            ys = val
        elif kind == "xy" and not xs:
            xs = [p[0] for p in val]
            ys = [p[1] for p in val]
    if len(xs) >= 3 and len(xs) == len(ys):
        m0 = 0.0
        for i in range(len(xs) - 1):
            m0 += 0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i])
        return m0
    return None


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------
def probe_spectrum(fetch):
    """Subsystem 1: seakeeping / wave spectrum (m0 against the ITTC-78 anchor)."""
    payload = _load_anchor("spectra_ittc_issc.json")
    entries = _entries_by_id(payload)
    anchor = entries["ittc78_hs5_t1_9p5_m0"]
    recs = []
    for path in ("/api/v1/wave-spectrum/calculate",
                 "/api/v1/seakeeping-advanced/wave-spectrum"):
        status, text = fetch(path, SPECTRUM_REQUEST)
        subsystem = "seakeeping/wave-spectrum" if "advanced" not in path else \
            "seakeeping-advanced/wave-spectrum"
        if status is None:
            recs.append(_record(subsystem, None, "m0", None, anchor["value"], None, ERROR,
                                path, error=text[:400]))
            continue
        if status >= 500:
            recs.append(_record(subsystem, status, "m0", None, anchor["value"], None, FAIL,
                                path, error=f"http_{status}: {text[:200]}"))
            continue
        if status >= 400:
            recs.append(_record(subsystem, status, "m0", None, anchor["value"], None, FAIL,
                                path, error=f"http_{status}: {text[:200]}"))
            continue
        m0 = parse_spectrum_m0(text)
        rel = _rel_err(m0, anchor["value"])
        verdict = PASS if (rel is not None and rel <= anchor["tolerance_rel"]) else FAIL
        recs.append(_record(subsystem, status, "m0", m0, anchor["value"], rel, verdict, path,
                            tolerance_rel=anchor["tolerance_rel"],
                            error=None if m0 is not None else "m0_not_parseable"))
    return recs


def probe_propeller(fetch):
    """Subsystem 2: Wageningen B4-70 open water (KT / 10KQ / eta0 vs anchor table)."""
    payload = _load_anchor("propeller_wageningen_b4_70.json")
    entries = _entries_by_id(payload)
    geom = payload["geometry"]
    recs = []
    path = "/api/v1/propeller-design/wageningen-b"
    for J in [0.1 * i for i in range(1, 9)]:
        status, text = fetch(path, {"J": round(J, 1), "P_D": geom["P_D"],
                                    "Z": int(geom["Z"]), "Ae_Ao": geom["Ae_Ao"]})
        tag = f"b4_70_J{J:.1f}"
        if status is None or status >= 400:
            recs.append(_record("propulsion/wageningen-b", status, "KT", None,
                                entries[f"{tag}_KT"]["value"], None,
                                ERROR if status is None else FAIL, path,
                                J=round(J, 1), error=(text or "")[:200]))
            continue
        try:
            body = json.loads(text)
        except Exception:
            recs.append(_record("propulsion/wageningen-b", status, "KT", None,
                                entries[f"{tag}_KT"]["value"], None, FAIL, path,
                                J=round(J, 1), error="body_not_json"))
            continue
        kt = body.get("KT", body.get("kt"))
        kq = body.get("KQ", body.get("kq"))
        tenkq = body.get("10KQ", 10.0 * kq if isinstance(kq, (int, float)) else None)
        eta = body.get("eta0", body.get("eta"))
        for qty, value in (("KT", kt), ("10KQ", tenkq), ("eta0", eta)):
            anchor = entries[f"{tag}_{qty}"]["value"]
            rel = _rel_err(value, anchor)
            verdict = PASS if (rel is not None and rel <= entries[f"{tag}_{qty}"]["tolerance_rel"]) else FAIL
            recs.append(_record("propulsion/wageningen-b", status, qty,
                                None if value is None else float(value), anchor, rel, verdict,
                                path, J=round(J, 1),
                                tolerance_rel=entries[f"{tag}_{qty}"]["tolerance_rel"]))
        # placeholder detector: a constant KQ/KT ratio across J is not the 1975 series
        if isinstance(kt, (int, float)) and isinstance(kq, (int, float)) and kt:
            recs[-1]["kq_over_kt"] = kq / kt
    ratios = [r["kq_over_kt"] for r in recs if "kq_over_kt" in r]
    if ratios and max(ratios) - min(ratios) <= 1e-9:
        for r in recs:
            r["placeholder_flag"] = "KQ/KT constant across J"
    return recs


def probe_resistance(fetch):
    """Subsystem 3+4: total resistance vs the KVLCC2 / KCS full-scale anchors."""
    payload = _load_anchor("resistance_kvlcc2_kcs.json")
    entries = _entries_by_id(payload)
    anchor_rt = entries["KVLCC2_RT_full"]
    recs = []
    targets = (
        ("resistance/calculations-resistance", "/api/v1/calculations/resistance",
         KVLCC2_REQUEST, anchor_rt, 1.0),      # answers in kN
        ("resistance/ittc78-advanced", "/api/v1/resistance-advanced/ittc78",
         ITTCT8_REQUEST, anchor_rt, 1.0),      # answers in kN
        ("resistance/predict", "/api/v1/resistance/predict",
         {"Lwl": 320.0, "Lpp": 320.0, "B": 58.0, "T": 20.8,
          "displacement": 312622.0, "Cb": 0.8098, "speeds": [15.5]},
         anchor_rt, 1.0),
    )
    keys = {"rt", "r_t", "total_resistance", "totalresistance", "resistance", "rt_kn"}
    for subsystem, path, body, anchor, scale in targets:
        status, text = fetch(path, body)
        if status is None:
            recs.append(_record(subsystem, None, "RT", None, anchor["value"], None, ERROR,
                                path, error=text[:300]))
            continue
        if status >= 400:
            recs.append(_record(subsystem, status, "RT", None, anchor["value"], None, FAIL,
                                path, error=f"http_{status}: {text[:200]}"))
            continue
        try:
            parsed = json.loads(text)
        except Exception:
            recs.append(_record(subsystem, status, "RT", None, anchor["value"], None, FAIL,
                                path, error="body_not_json"))
            continue
        rt = _find_first(parsed, keys)
        if rt is None:
            recs.append(_record(subsystem, status, "RT", None, anchor["value"], None, FAIL,
                                path, error="RT_not_parseable"))
            continue
        rt_n = rt * scale * 1.0e3 if abs(rt) < 1.0e5 else rt * scale  # kN or N heuristics
        if abs(rt) < 1000.0:  # MN
            rt_n = rt * 1.0e6
        elif abs(rt) < 1.0e5:  # kN
            rt_n = rt * 1.0e3
        rel = _rel_err(rt_n, anchor["value"])
        verdict = PASS if (rel is not None and rel <= anchor["tolerance_rel"]) else FAIL
        recs.append(_record(subsystem, status, "RT", rt_n, anchor["value"], rel, verdict, path,
                            tolerance_rel=anchor["tolerance_rel"], raw_value=rt))
    return recs


# ---------------------------------------------------------------------------
# domain-defect probes (m1780): maneuvering MMG contract + advanced/bem RAO physics
#
# Both defects were measured on the live platform before any repair, so the probe
# doubles as the *machine adjudicator*: every verdict below is recomputed from the
# evidence it carries (see reevaluate_defect_record), which is what makes the
# committed artifact tamper-evident instead of self-reported.
# ---------------------------------------------------------------------------
MANEUVERING_BASE = {"L": 320.0, "B": 58.0, "T": 20.8, "displacement": 320000.0,
                    "Cb": 0.82, "Dp": 9.86, "n": 80.0, "Ar": 120.0, "V0": 7.7}
TURNING_REQUEST = dict(MANEUVERING_BASE, rudder_angle=35.0)
ZIGZAG_REQUEST = dict(MANEUVERING_BASE, rudder_angle=10.0, heading_angle=10.0)
MISSING_PARAM_FIELD = "displacement"
MISSING_PARAM_REQUEST = {k: v for k, v in TURNING_REQUEST.items()
                         if k != MISSING_PARAM_FIELD}
TURNING_PATH = "/api/v1/maneuvering-mmg/turning"
ZIGZAG_PATH = "/api/v1/maneuvering-mmg/zigzag"

BEM_PATH = "/api/v1/advanced/bem"
BEM_HULL_A = {"length": 320.0, "beam": 58.0, "draft": 20.8, "speed": 15.5, "cb": 0.82}
BEM_HULL_B = {"length": 230.0, "beam": 32.2, "draft": 10.8, "speed": 12.0, "cb": 0.651}

# the hardcoded table shipped by backend/api/advanced_theory.py:364-366
KNOWN_BEM_RAO_TABLE = {
    "frequencies": [0.3, 0.5, 0.7, 0.9, 1.1, 1.3],
    "heave": [0.8, 1.2, 1.5, 1.3, 1.0, 0.8],
    "pitch": [0.5, 0.8, 1.2, 1.0, 0.7, 0.5],
}
BEM_RAO_MIN_POINTS = 15
BEM_RAO_MIN_POINTWISE_REL = 0.05
BEM_RAO_MAX_CORRELATION = 0.99
BEM_RAO_MIN_PEAK_SHIFT_REL = 0.05

DOMAIN_VERDICTS = (PASS, FAIL, "REAL", "MOCK", "STUB", "WITHHELD")
DEFECT_SUBSYSTEMS = ("maneuvering-mmg/contract", "advanced-bem/rao")
DEFECT_IDS = {
    "maneuvering-mmg/contract": "SHIP-MAN-001",
    "advanced-bem/rao": "SHIP-SEA-001",
}
DEFAULT_DEFECT_OUT = os.path.join(ROOT, "artifacts", "ship_endpoint_verdicts.json")
PREFIX_BASELINE_OUT = os.path.join(ROOT, "artifacts",
                                   "ship_endpoint_verdicts_prefix_baseline.json")

_LEGACY_VERDICT = {PASS: PASS, "REAL": PASS, FAIL: FAIL, "MOCK": FAIL,
                   "STUB": FAIL, "WITHHELD": ERROR}


def _domain_record(subsystem, endpoint, domain_verdict, criterion_id, evidence,
                   expected_after_fix, aggregates=None):
    """A defect-probe record: legacy PASS/FAIL/ERROR plus the domain vocabulary."""
    status = evidence.get("http_status")
    rec = _record(subsystem, status, "-", None, None, None,
                  _LEGACY_VERDICT[domain_verdict], endpoint)
    rec.update({
        "http_status": status,
        "endpoint": endpoint,
        "domain_verdict": domain_verdict,
        "criterion_id": criterion_id,
        "evidence": evidence,
        "expected_after_fix": expected_after_fix,
    })
    if aggregates:
        rec["aggregates"] = list(aggregates)
    return rec


def _http_verdict(status, ok):
    """Unreachable is WITHHELD, never PASS."""
    if status is None:
        return WITHHELD
    return PASS if ok else FAIL


def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = [float(x) for x in a[:n]], [float(x) for x in b[:n]]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0.0 or vb <= 0.0:
        return 1.0 if a == b else 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (va ** 0.5 * vb ** 0.5)


def _max_pointwise_rel(a, b):
    n = min(len(a), len(b))
    worst = 0.0
    for x, y in zip([float(v) for v in a[:n]], [float(v) for v in b[:n]]):
        denom = max(abs(x), abs(y), 1e-12)
        worst = max(worst, abs(x - y) / denom)
    return worst


def _peak_frequency(freqs, series):
    """Frequency of the maximum of a response curve (the resonance peak)."""
    if not series or not freqs or len(freqs) != len(series):
        return None
    pairs = list(zip([float(f) for f in freqs], [float(v) for v in series]))
    return max(pairs, key=lambda p: p[1])[0]


def _peak_shift_rel(freqs, series_a, series_b):
    fa, fb = _peak_frequency(freqs, series_a), _peak_frequency(freqs, series_b)
    if fa in (None, 0) or fb is None:
        return None
    return abs(fb - fa) / abs(fa)


def rao_dependence_ok(dependence):
    """Hull/speed/frequency dependence of the RAO.

    Physics gate: the pointwise difference must exceed 5% AND the curve must either
    decorrelate (Pearson < 0.99) *or* move its resonance peak by > 5% — a genuine
    solved response changes with Cb/L, while a hardcoded table changes in neither.
    A platform that reacts to ``wave_frequency`` satisfies the gate outright.
    """
    dep = dependence or {}
    if dep.get("omega_changed"):
        return True
    corr = dep.get("hull_corr")
    rel = dep.get("hull_max_rel") or 0.0
    shift = dep.get("peak_shift_rel") or 0.0
    if corr is None:
        return False
    return rel > BEM_RAO_MIN_POINTWISE_REL and (
        corr < BEM_RAO_MAX_CORRELATION or shift > BEM_RAO_MIN_PEAK_SHIFT_REL)


def constant_table_fingerprint(series, reference=None, tol=1e-9):
    """Detect a hardcoded constant table standing in for a computed RAO series.

    A *hit* means the values are bit-for-bit the known hardcoded table (same
    length, same entries), i.e. the answer cannot depend on hull, speed or
    frequency.  A genuine >=15-point curve never hits because its length differs.
    """
    ref = list(reference if reference is not None else KNOWN_BEM_RAO_TABLE["heave"])
    values = [float(v) for v in (series or [])]
    hit = len(values) == len(ref) and all(abs(v - r) <= tol
                                          for v, r in zip(values, ref))
    return {"hit": hit, "n_points": len(values), "reference_points": len(ref),
            "reference": ref, "tolerance": tol}


def adjudicate_rao_from_criteria(c4, c5, c6):
    """Evidence level of an RAO answer, derived only from the criterion verdicts."""
    verdicts = (c4, c5, c6)
    if WITHHELD in verdicts:
        return WITHHELD
    if c6 == FAIL:      # known hardcoded constant table -> MOCK
        return "MOCK"
    if c4 == FAIL:      # too few points to be a solved response curve -> STUB
        return "STUB"
    if c5 == FAIL:      # independent of hull / speed / frequency -> MOCK
        return "MOCK"
    return "REAL"


def adjudicate_contract_from_criteria(c1, c2, c3):
    """Contract verdict from the three contract criteria."""
    verdicts = (c1, c2, c3)
    if WITHHELD in verdicts:
        return WITHHELD
    return PASS if all(v == PASS for v in verdicts) else FAIL


def probe_maneuvering_contract(fetch):
    """Contract probe: a legal MMG payload must answer 200, a missing field must 422."""
    recs = []
    for cid, path, body, expected in (
            ("C1", TURNING_PATH, TURNING_REQUEST, "HTTP 200 + 回转参数(K/T/战术直径)"),
            ("C2", ZIGZAG_PATH, ZIGZAG_REQUEST, "HTTP 200 + Z形参数(首向角/超调)")):
        status, text = fetch(path, body)
        evidence = {"http_status": status, "request": body,
                    "response_excerpt": (text or "")[:240]}
        recs.append(_domain_record("maneuvering-mmg/contract", path,
                                   _http_verdict(status, status == 200),
                                   cid, evidence, expected))
    status, text = fetch(TURNING_PATH, MISSING_PARAM_REQUEST)
    missing_fields = []
    try:
        detail = json.loads(text).get("detail", [])
        for item in detail:
            if isinstance(item, dict):
                loc = item.get("loc") or []
                if loc:
                    missing_fields.append(loc[-1])
    except Exception:
        pass
    ok = status == 422 and MISSING_PARAM_FIELD in missing_fields
    evidence = {"http_status": status, "request": MISSING_PARAM_REQUEST,
                "missing_fields": missing_fields,
                "expected_missing_field": MISSING_PARAM_FIELD,
                "response_excerpt": (text or "")[:240]}
    recs.append(_domain_record("maneuvering-mmg/contract", TURNING_PATH,
                               _http_verdict(status, ok), "C3", evidence,
                               "HTTP 422 且 detail.loc 指名 displacement"))
    sub = {r["criterion_id"]: r["domain_verdict"] for r in recs}
    recs.append(_domain_record(
        "maneuvering-mmg/contract", TURNING_PATH + "|" + ZIGZAG_PATH,
        adjudicate_contract_from_criteria(sub["C1"], sub["C2"], sub["C3"]),
        "L1", {"http_status": recs[0]["http_status"], "sub_criteria": sub},
        "契约修复后 C1/C2/C3 全 PASS", aggregates=("C1", "C2", "C3")))
    return recs


def _parse_rao(text):
    doc = json.loads(text)
    rao = doc.get("rao") or {}
    return {"frequencies": rao.get("frequencies"),
            "heave": rao.get("heave"), "pitch": rao.get("pitch")}


def probe_bem_rao(fetch):
    """Physics probe: is the BEM RAO a solved response or a hardcoded 6-point table?"""
    bodies = (("hull_a", BEM_HULL_A), ("hull_b", BEM_HULL_B),
              ("hull_a_w03", dict(BEM_HULL_A, wave_frequency=0.3)),
              ("hull_a_w13", dict(BEM_HULL_A, wave_frequency=1.3)))
    parsed, statuses, excerpts = {}, {}, {}
    for tag, body in bodies:
        status, text = fetch(BEM_PATH, body)
        statuses[tag] = status
        excerpts[tag] = (text or "")[:240]
        if status == 200:
            try:
                parsed[tag] = _parse_rao(text)
            except Exception:
                excerpts[tag] = "body_not_json: " + excerpts[tag]
    main_status = statuses.get("hull_a")

    # C4: series resolution
    lens = {}
    for motion in ("heave", "pitch"):
        series = (parsed.get("hull_a") or {}).get(motion) or []
        lens[motion] = len(series)
    freq_n = len((parsed.get("hull_a") or {}).get("frequencies") or [])
    c4 = _http_verdict(main_status,
                       bool(lens) and freq_n >= BEM_RAO_MIN_POINTS
                       and min(lens.values()) >= BEM_RAO_MIN_POINTS)
    recs = [_domain_record(
        "advanced-bem/rao", BEM_PATH, c4, "C4",
        {"http_status": main_status, "lengths": lens, "n_frequencies": freq_n,
         "min_points_required": BEM_RAO_MIN_POINTS,
         "response_excerpt": excerpts.get("hull_a", "")},
        f"frequencies/rao 长度 >= {BEM_RAO_MIN_POINTS}")]

    # C5: dependence on hull (Cb/L) and on wave frequency
    dependence = {"hull_corr": None, "hull_max_rel": None, "peak_shift_rel": None,
                  "omega_changed": None,
                  "hull_pair": ["hull_a:L320/Cb0.82/V15.5", "hull_b:L230/Cb0.651/V12"],
                  "omega_pair": [0.3, 1.3]}
    a, b = parsed.get("hull_a"), parsed.get("hull_b")
    if a and b:
        corrs, rels, shifts = [], [], []
        freqs_a = a.get("frequencies") or []
        for motion in ("heave", "pitch"):
            sa, sb = a.get(motion) or [], b.get(motion) or []
            if sa and sb:
                corrs.append(_pearson(sa, sb))
                rels.append(_max_pointwise_rel(sa, sb))
                shift = _peak_shift_rel(freqs_a, sa, sb)
                if shift is not None:
                    shifts.append(shift)
        if corrs and rels:
            dependence["hull_corr"] = min(corrs)   # most-changed motion
            dependence["hull_max_rel"] = max(rels)
        if shifts:
            dependence["peak_shift_rel"] = max(shifts)
    w0, w1 = parsed.get("hull_a_w03"), parsed.get("hull_a_w13")
    if w0 and w1:
        changed = any((w0.get(m) or []) != (w1.get(m) or []) for m in ("heave", "pitch"))
        dependence["omega_changed"] = changed
    c5 = _http_verdict(main_status, rao_dependence_ok(dependence))
    recs.append(_domain_record(
        "advanced-bem/rao", BEM_PATH, c5, "C5",
        {"http_status": main_status, "dependence": dependence,
         "max_correlation_allowed": BEM_RAO_MAX_CORRELATION,
         "min_pointwise_rel_required": BEM_RAO_MIN_POINTWISE_REL,
         "response_excerpt": excerpts.get("hull_a", "")},
        "RAO 随 Cb/L/ω 变化：逐点差 > 5% 且（相关性 < 0.99 或共振峰移位 > 5%）"))

    # C6: hardcoded-table fingerprint
    fingerprints = {}
    for motion in ("heave", "pitch"):
        series = (parsed.get("hull_a") or {}).get(motion) or []
        fp = constant_table_fingerprint(series, KNOWN_BEM_RAO_TABLE[motion])
        fp["reference_name"] = f"backend/api/advanced_theory.py:364-366 {motion}"
        fingerprints[motion] = fp
    hit = any(fp["hit"] for fp in fingerprints.values())
    c6 = _http_verdict(main_status, not hit)
    recs.append(_domain_record(
        "advanced-bem/rao", BEM_PATH, c6, "C6",
        {"http_status": main_status, "fingerprint": fingerprints,
         "response_excerpt": excerpts.get("hull_a", "")},
        "不得等于 6 点常数表（表长=6 且值逐点相等即判 MOCK）"))

    sub = {r["criterion_id"]: r["domain_verdict"] for r in recs}
    recs.append(_domain_record(
        "advanced-bem/rao", BEM_PATH + "|rao",
        adjudicate_rao_from_criteria(sub["C4"], sub["C5"], sub["C6"]),
        "L2", {"http_status": main_status,
               "lengths": lens, "dependence": dependence,
               "fingerprint_hits": {m: fingerprints[m]["hit"] for m in fingerprints},
               "sub_criteria": sub},
        "修复后 RAO 为频率分辨的物理解算 -> REAL", aggregates=("C4", "C5", "C6")))
    return recs


def reevaluate_defect_record(rec, siblings):
    """Recompute a defect verdict from its own evidence (the tamper detector)."""
    cid = rec.get("criterion_id")
    ev = rec.get("evidence") or {}
    status = ev.get("http_status")
    if cid in ("C1", "C2"):
        return _http_verdict(status, status == 200)
    if cid == "C3":
        return _http_verdict(status,
                             status == 422
                             and MISSING_PARAM_FIELD in (ev.get("missing_fields") or []))
    if cid in ("C4", "C5", "C6"):
        if cid == "C4":
            lens = ev.get("lengths") or {}
            n_freq = ev.get("n_frequencies") or 0
            ok = bool(lens) and n_freq >= BEM_RAO_MIN_POINTS \
                and min(lens.values()) >= BEM_RAO_MIN_POINTS
            return _http_verdict(status, ok)
        if cid == "C5":
            return _http_verdict(status, rao_dependence_ok(ev.get("dependence")))
        fp = ev.get("fingerprint") or {}
        hit = any((v or {}).get("hit") for v in fp.values())
        return _http_verdict(status, not hit)
    aggregates = rec.get("aggregates")
    if aggregates:
        vals = [reevaluate_defect_record(s, siblings) for s in siblings
                if s.get("criterion_id") in aggregates]
        if not vals:
            return WITHHELD
        if cid == "L2":
            by_id = {s.get("criterion_id"): v for s, v in
                     zip([s for s in siblings if s.get("criterion_id") in aggregates], vals)}
            return adjudicate_rao_from_criteria(by_id.get("C4"), by_id.get("C5"),
                                                by_id.get("C6"))
        if WITHHELD in vals:
            return WITHHELD
        return PASS if all(v == PASS for v in vals) else FAIL
    return WITHHELD


def build_defect_document(records, base_url, generated_at=None):
    """Project probe records onto the domain-defect verdict document."""
    defect = [r for r in records if r.get("subsystem") in DEFECT_SUBSYSTEMS
              and "criterion_id" in r]
    reachable = any(r.get("http_status") is not None for r in defect)
    out_records = []
    for r in defect:
        out_records.append({
            "defect_id": DEFECT_IDS[r["subsystem"]],
            "subsystem": r["subsystem"],
            "criterion_id": r["criterion_id"],
            "criterion": r["expected_after_fix"],
            "endpoint": r["endpoint"],
            "http_status": r["http_status"],
            "verdict": r["domain_verdict"],
            "evidence": r["evidence"],
            "expected_after_fix": r["expected_after_fix"],
            "ts": r["ts"],
        })
        if "aggregates" in r:
            out_records[-1]["aggregates"] = list(r["aggregates"])
    summary = {}
    for r in out_records:
        summary.setdefault(r["subsystem"], {})[r["criterion_id"]] = r["verdict"]
    return {
        "probe": "tools/ref_probe_8001.py",
        "task": "m1780 ship-domain endpoint verdicts",
        "platform_base_url": base_url,
        "generated_at": generated_at or _now(),
        "platform_reachable": reachable,
        "verdict_vocabulary": list(DOMAIN_VERDICTS),
        "defect_ids": sorted(set(DEFECT_IDS.values())),
        "prefix_baseline": os.path.relpath(PREFIX_BASELINE_OUT, ROOT),
        "summary": summary,
        "records": out_records,
    }


def verify_defect_document(doc):
    """Return a list of problems; empty list means the document is self-consistent.

    This is the machine gate the pytest suite consumes: every verdict must be
    reproducible from the evidence stored next to it, so a flipped verdict
    (MOCK -> REAL, FAIL -> PASS, ...) is rejected without human review.
    """
    problems = []
    for key in ("platform_base_url", "generated_at", "platform_reachable",
                "verdict_vocabulary", "records"):
        if key not in doc:
            problems.append(f"missing key: {key}")
    if problems:
        return problems
    if doc["verdict_vocabulary"] != list(DOMAIN_VERDICTS):
        problems.append("verdict_vocabulary drift")
    records = doc["records"]
    seen = set()
    for i, rec in enumerate(records):
        for key in ("defect_id", "subsystem", "criterion_id", "endpoint",
                    "http_status", "verdict", "evidence", "expected_after_fix", "ts"):
            if key not in rec:
                problems.append(f"record[{i}] missing {key}")
        if rec.get("verdict") not in DOMAIN_VERDICTS:
            problems.append(f"record[{i}] verdict {rec.get('verdict')!r} outside vocabulary")
            continue
        seen.add(rec.get("criterion_id"))
        if rec.get("verdict") in (PASS, "REAL") and rec.get("http_status") is None:
            problems.append(f"record[{i}] {rec['verdict']} on unreachable endpoint")
        if rec.get("verdict") in ("MOCK", "STUB", "REAL") and not rec.get("evidence"):
            problems.append(f"record[{i}] {rec['verdict']} without evidence")
        recomputed = reevaluate_defect_record(rec, records)
        if recomputed != rec.get("verdict"):
            problems.append(
                f"record[{i}] {rec.get('criterion_id')}: verdict {rec.get('verdict')} "
                f"contradicts evidence (recomputed {recomputed})")
    if not doc["platform_reachable"]:
        if any(r.get("verdict") in (PASS, "REAL") for r in records):
            problems.append("unreachable platform cannot yield PASS/REAL")
    expected = {cid for cid in ("C1", "C2", "C3", "C4", "C5", "C6", "L1", "L2")}
    missing = expected - seen
    if missing:
        problems.append(f"missing criteria: {sorted(missing)}")
    return problems


PROBES = (("seakeeping", probe_spectrum),
          ("propulsion", probe_propeller),
          ("resistance", probe_resistance),
          ("maneuvering-contract", probe_maneuvering_contract),
          ("advanced-bem-rao", probe_bem_rao))


def run_probes(fetch):
    """Run every probe with an injected transport; return (records, summary)."""
    records = []
    for name, probe in PROBES:
        try:
            records.extend(probe(fetch))
        except Exception as exc:  # a probe must never abort the whole run
            records.append(_record(name, None, "-", None, None, None, ERROR,
                                   "-", error=f"probe_crash: {type(exc).__name__}: {exc}"))
    return records, summarise(records)


def summarise(records):
    subsystems = {}
    for r in records:
        s = subsystems.setdefault(r["subsystem"], {"PASS": 0, "FAIL": 0, "ERROR": 0})
        s[r["verdict"]] += 1
    overall = FAIL if any(v["FAIL"] for v in subsystems.values()) else (
        ERROR if any(v["ERROR"] for v in subsystems.values()) else PASS)
    return {
        "n_records": len(records),
        "n_subsystems": len(subsystems),
        "subsystems": subsystems,
        "overall_verdict": overall,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("SHIP_API_BASE", DEFAULT_BASE))
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--defect-out", default=DEFAULT_DEFECT_OUT)
    ap.add_argument("--freeze-baseline", action="store_true",
                    help="mark the emitted defect document as the frozen pre-fix baseline")
    ap.add_argument("--verify", metavar="JSON", default=None,
                    help="re-adjudicate an existing defect document and exit non-zero on drift")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    if args.verify:
        with open(args.verify, encoding="utf-8") as fh:
            problems = verify_defect_document(json.load(fh))
        for problem in problems:
            print("PROBLEM:", problem)
        print(f"verified {args.verify}: {'OK' if not problems else str(len(problems)) + ' problem(s)'}")
        return 0 if not problems else 1

    def fetch(path, body=None, method=None):
        return http_request(path, body, method, base_url=args.base_url)

    records, summary = run_probes(fetch)
    doc = {
        "probe": "tools/ref_probe_8001.py",
        "platform_base_url": args.base_url,
        "generated_at": _now(),
        "anchor_dir": os.path.relpath(ANCHOR_DIR, ROOT),
        "summary": summary,
        "verdicts": records,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    defect_doc = build_defect_document(records, args.base_url)
    if args.freeze_baseline:
        defect_doc["frozen"] = True
        defect_doc["frozen_note"] = (
            "pre-repair measurement, kept immutable as the regression evidence: "
            "SHIP-MAN-001 = FAIL (HTTP 500 on legal MMG payload), "
            "SHIP-SEA-001 = MOCK (hardcoded 6-point RAO table)")
    problems = verify_defect_document(defect_doc)
    defect_doc["self_check"] = {"problems": problems, "ok": not problems}
    os.makedirs(os.path.dirname(args.defect_out) or ".", exist_ok=True)
    with open(args.defect_out, "w", encoding="utf-8") as fh:
        json.dump(defect_doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"wrote {os.path.relpath(args.out, ROOT)} "
          f"({summary['n_records']} records / {summary['n_subsystems']} subsystems)")
    for name, counts in sorted(summary["subsystems"].items()):
        print(f"  {name:42s} PASS={counts['PASS']:2d} FAIL={counts['FAIL']:2d} "
              f"ERROR={counts['ERROR']:2d}")
    print(f"overall_verdict={summary['overall_verdict']}")
    print(f"wrote {os.path.relpath(args.defect_out, ROOT)} "
          f"(reachable={defect_doc['platform_reachable']}, "
          f"{len(defect_doc['records'])} records)")
    for subsystem, crit in sorted(defect_doc["summary"].items()):
        row = " ".join(f"{k}={v}" for k, v in sorted(crit.items()))
        print(f"  {subsystem:28s} {row}")
    print(f"defect_self_check={'OK' if not problems else problems}")
    if args.strict and (summary["overall_verdict"] != PASS or problems):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())