#!/usr/bin/env python3
"""Surrogate / prediction endpoint non-degeneracy audit (mission m1752, tp-infra-1).

Why this tool exists
--------------------
The ship-performance platform (:8001) exposes ~20 "AI / surrogate prediction"
endpoints next to the physics engines.  ``audit_capabilities`` reports
``ai.predict = NOT_SUPPORTED`` while 24 of those endpoints *self-label* ``[MOCK]``
in their OpenAPI summary and ~8 carry **no label at all**.  Neither the platform
registry nor any prior audit says whether the unlabelled ones are real models,
closed-form fallbacks, or output that is invariant to / independent of its input.

Accuracy audits (resistance vs ITTC, stability vs closed form, ...) cannot answer
that question: they judge *one* operating point.  A degenerate endpoint can be
"accidentally right" at the calibration point and meaningless everywhere else.

This tool applies four **model-free, machine-adjudicable** criteria that any
honest prediction endpoint must satisfy, and that require no reference value:

  D1 determinism ......... identical request twice -> identical numeric answer
                           (a hidden ``random()`` is disqualifying)
  D2 sensitivity ......... output must depend on the physical input (speed)
                           (invariance == information destruction)
  D3 physical scaling .... R(V) must be increasing with an effective exponent
                           inside the admissible band for ship resistance
                           (1.5 <= n_eff <= 8; friction ~V^1.8, wave ~V^4+)
  D4 cross-engine ........ agreement with the platform's own physics engine
                           (/calculations/resistance) within the platform's 15%
                           acceptance band -- reported, not required, because a
                           surrogate is allowed to differ *predictably*.

Plus a label-honesty criterion:

  L1 label ............... does the OpenAPI summary declare ``[MOCK]`` /
                           ``[STUB]``?  An undocumented degenerate endpoint is an
                           OVERCLAIM-by-exposure; a documented one is honest.

Positive controls make the detector non-vacuous: two endpoints that *are* known
MOCK (``/ai-models/predict/resistance``, ``/pinn/resistance``) must be caught by
D1/D2, and the REAL physics engine (``/calculations/resistance``) must pass
D1-D3.  A detector that cannot pass its controls is worthless, so the test suite
asserts on the controls, not just on the verdicts.

Usage
-----
    python3 tools/audit/surrogate_nondegeneracy_m1752.py --probe     # live :8001 -> snapshot
    python3 tools/audit/surrogate_nondegeneracy_m1752.py --check     # snapshot -> verdicts + artifacts
    python3 tools/audit/surrogate_nondegeneracy_m1752.py --figure    # verdicts -> png
    python3 tools/audit/surrogate_nondegeneracy_m1752.py --probe --check --figure

The verdict layer is a *pure* function of the snapshot dict, so pytest can replay
every verdict offline (no network) -- the same discipline as the m1523 sea/ma
ledger: a verdict that cannot be replayed cannot be trusted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

API_DEFAULT = "http://127.0.0.1:8001/api/v1"
ART_DIR = os.path.join("artifacts", "m1752")
SNAPSHOT = os.path.join(ART_DIR, "surrogate_probe_snapshot.json")
VERDICTS = os.path.join(ART_DIR, "surrogate_verdicts.json")
VERDICTS_MD = os.path.join(ART_DIR, "surrogate_verdicts.md")
FIGURE = os.path.join(ART_DIR, "fig_surrogate_nondegeneracy.png")

# --- criteria constants (single source of truth, imported by the tests) -----
TOL_DETERMINISM = 1e-9       # relative: identical request must not move the answer
TOL_SENSITIVITY = 1e-6       # relative: speed change must move the answer
EXPONENT_BAND = (1.5, 8.0)   # admissible effective exponent d lnR / d lnV
CROSS_ENGINE_BAND = 0.15     # platform acceptance band (>15% unacceptable)

# ---------------------------------------------------------------------------
# Request battery.  Every endpoint gets a base case and one *speed* mutation on
# the same hull; results are compared by numeric-leaf fingerprint so that the
# criterion does not depend on response field names.
# ---------------------------------------------------------------------------

#: reference hull: a mid-size tanker-like form (L/B/T/Cb physically consistent)
REF_HULL = dict(L=180.0, B=32.0, T=11.0, cb=0.82, disp=52000.0)

ENDPOINTS: List[Dict[str, Any]] = [
    # --- controls: documented MOCK (must be caught) ------------------------
    {
        "id": "ctl_mock_ai_resistance", "path": "ai-models/predict/resistance",
        "role": "control_mock", "label_expected": "MOCK",
        "base": {"length": 180.0, "beam": 32.0, "draft": 11.0,
                 "displacement": 52000.0, "speed": 7.0, "cb": 0.82},
        "speed_key": "speed", "speed_values": [7.0, 9.9],
    },
    {
        "id": "ctl_mock_pinn_resistance", "path": "pinn/resistance",
        "role": "control_mock", "label_expected": "MOCK",
        "base": {"L": 180.0, "B": 32.0, "T": 11.0, "Cb": 0.82,
                 "displacement": 52000.0, "speed": 7.0},
        "speed_key": "speed", "speed_values": [7.0, 9.9],
    },
    # --- control: REAL physics engine (must pass D1-D3) --------------------
    # ResistanceInput: displacement is a VOLUME [m3] that must sit within 5% of
    # L*B*T*Cb = 51955 m3; speed defaults to knots, so 13.6 kn == 7.0 m/s.
    {
        "id": "ctl_real_resistance_engine", "path": "calculations/resistance",
        "role": "control_real", "label_expected": None,
        "base": {"length": 180.0, "beam": 32.0, "draft": 11.0,
                 "displacement": 51955.0, "speed": 13.6, "cb": 0.82},
        "speed_key": "speed", "speed_values": [13.6, 19.2],   # 7.00 / 9.88 m/s
    },
    # --- subjects: unlabelled prediction endpoints ------------------------
    {
        "id": "resistance_predict", "path": "resistance/predict",
        "role": "subject", "label_expected": None,
        "base": {"Lwl": 182.0, "Lpp": 180.0, "B": 32.0, "T": 11.0,
                 "displacement": 52000.0, "Cb": 0.82, "Cm": 0.99, "Cwp": 0.87,
                 "speeds": [7.0]},
        "speed_key": "speeds", "speed_values": [[7.0], [9.9]],
    },
    {
        "id": "propulsion_predict", "path": "propulsion/predict",
        "role": "subject", "label_expected": None,
        "base": {"L": 180.0, "B": 32.0, "T": 11.0, "displacement": 52000.0,
                 "Cb": 0.82, "speed": 7.0, "Dp": 6.5},
        "speed_key": "speed", "speed_values": [7.0, 9.9],
    },
    {
        "id": "lbm_predict", "path": "lbm/predict",
        "role": "subject", "label_expected": None,
        "base": {"L": 180.0, "B": 32.0, "T": 11.0, "Cb": 0.82, "V": 7.0},
        "speed_key": "V", "speed_values": [7.0, 9.9],
    },
    {
        "id": "transformer_resistance", "path": "transformer/resistance",
        "role": "subject", "label_expected": None,
        "base": {"ship_params": [{"L": 180.0, "B": 32.0, "T": 11.0,
                                  "Cb": 0.82, "displacement": 52000.0}],
                 "speed_range": [7.0, 7.0]},
        "speed_key": "speed_range", "speed_values": [[7.0, 7.0], [9.9, 9.9]],
    },
    {
        # no speed field at all: the only lever is the requested quantity, so
        # D2 is applied topically (resistance vs stability must differ).
        "id": "gnn_predict", "path": "gnn/predict",
        "role": "subject", "label_expected": None,
        "base": {"ship_graph": {"node_features": [[180.0, 32.0, 11.0, 0.82]],
                                "edge_index": [[0], [0]]},
                 "prediction_type": "resistance"},
        "speed_key": None, "speed_values": [None, None],
        "topic_key": "prediction_type", "topic_values": ["resistance", "stability"],
        # fairness guard: a "degenerate" verdict must not be an artefact of a
        # single-node graph, so also post a 5-node graph + another topic.
        "extra": [
            {"name": "contrast_rich_graph", "set": {
                "ship_graph": {"node_features": [[180.0, 32.0, 11.0, 0.82],
                                                 [175.0, 30.0, 10.0, 0.80],
                                                 [185.0, 34.0, 12.0, 0.84],
                                                 [170.0, 28.0, 9.5, 0.78],
                                                 [190.0, 36.0, 13.0, 0.86]],
                               "edge_index": [[0, 1, 2, 3], [1, 2, 3, 4]]},
                "prediction_type": "stability"}},
        ],
    },
    {
        "id": "digital_twin_predict", "path": "digital-twin/predict",
        "role": "subject", "label_expected": None,
        "base": {"ship_model": {"ship_id": "m1752", "ship_name": "ref-tanker",
                                "ship_type": "tanker", "L": 180.0, "B": 32.0,
                                "T": 11.0, "D": 18.0, "displacement": 52000.0,
                                "Cb": 0.82, "Cm": 0.99, "Cwp": 0.87,
                                "main_engine_power": 9000.0,
                                "propeller_diameter": 6.5, "propeller_rpm": 90.0,
                                "created_at": "2026-11-01T00:00:00Z"},
                 "current_state": {"speed": 7.0, "draft": 11.0},
                 "prediction_horizon": 6, "prediction_type": "performance"},
        "speed_key": "current_state.speed", "speed_values": [7.0, 9.9],
        # fairness guard: also change the requested quantity and the horizon.
        "extra": [
            {"name": "contrast_type_horizon", "set": {
                "prediction_type": "resistance", "prediction_horizon": 12,
                "current_state": {"speed": 9.9, "draft": 13.5}}},
        ],
    },
]

#: Explicit output target per endpoint.  Declaring the adjudicated quantity in
#: the battery (rather than guessing "the biggest number") keeps the verdict
#: accountable; the resolver still tolerates nesting/suffix differences because
#: the platform's response envelopes differ (list vs dict, kN vs N, Rt vs rt).
OUTPUT_KEYS: Dict[str, str] = {
    "ctl_mock_ai_resistance": "predicted_rt",
    "ctl_mock_pinn_resistance": "resistance",
    "ctl_real_resistance_engine": "rt",
    "resistance_predict": "total_resistance",
    "propulsion_predict": "power",
    "lbm_predict": "Rt",
    "transformer_resistance": "resistance",
    "gnn_predict": "prediction",
    "digital_twin_predict": "predicted_value",
}

#: outputs that carry a FORCE (resistance) and may be compared to the engine's R_T
RESISTANCE_OUTPUTS = {"rt", "Rt", "total_resistance", "resistance", "predicted_rt"}

#: endpoints whose interface cannot be driven by the JSON battery (honest gaps,
#: recorded in the snapshot instead of being silently dropped).
INTERFACE_NOTES: List[Dict[str, str]] = [
    {"path": "performance/predict",
     "reason": "multipart/form-data file upload (Body_predict_performance_api_v1...), "
               "no JSON body; not driven by this battery -> NOT_PROBED"},
    {"path": "vibration/predict",
     "reason": "takes ship_id as a REQUIRED query parameter and no body; the answer is "
               "looked up from stored ship data, so an input-sensitivity criterion has "
               "no lever -> NOT_PROBED"},
]

#: cross-engine comparison: these subjects claim to predict *resistance*, so they
#: may be compared against the platform's own physics engine result.
CROSS_ENGINE = {
    "resistance_predict": "kt",
    "lbm_predict": "N",
    "transformer_resistance": "N",
    "ctl_mock_ai_resistance": "unspecified",
    "ctl_mock_pinn_resistance": "unspecified",
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _post(api: str, path: str, payload: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    url = f"{api.rstrip('/')}/{path.lstrip('/')}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            status = r.getcode()
    except urllib.error.HTTPError as exc:  # 4xx/5xx are data, not crashes
        body = exc.read().decode()
        status = exc.code
    except Exception as exc:  # connection refused / timeout
        body = json.dumps({"__transport_error__": f"{type(exc).__name__}: {exc}"})
        status = 0
    elapsed = (time.perf_counter() - t0) * 1000.0
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = {"__non_json__": body[:2000]}
    return {"status": status, "elapsed_ms": round(elapsed, 3), "body": parsed}


def _mutate(base: Dict[str, Any], key: Optional[str], value: Any) -> Dict[str, Any]:
    """Deep-copy ``base`` and set ``key`` (dotted path supported) to ``value``."""
    out = json.loads(json.dumps(base))
    if key:
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return out


def _at(payload: Dict[str, Any], key: Optional[str]) -> Any:
    if not key:
        return None
    node: Any = payload
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def probe(api: str = API_DEFAULT) -> Dict[str, Any]:
    """Hit every endpoint in the battery and dump a replayable snapshot."""
    records: List[Dict[str, Any]] = []
    for spec in ENDPOINTS:
        rec: Dict[str, Any] = {
            "id": spec["id"], "path": spec["path"], "role": spec["role"],
            "label_expected": spec.get("label_expected"),
            "speed_key": spec.get("speed_key"),
            "output_key": OUTPUT_KEYS.get(spec["id"]),
            "openapi_tags": _openapi_tags(api, spec["path"]),
            "probes": {},
        }
        v0, v1 = spec["speed_values"]
        p0 = _mutate(spec["base"], spec["speed_key"], v0) if v0 is not None else spec["base"]
        p1 = _mutate(spec["base"], spec["speed_key"], v1) if v1 is not None else spec["base"]
        rec["probes"]["base"] = {"payload": p0, **_post(api, spec["path"], p0)}
        rec["probes"]["base_repeat"] = {"payload": p0, **_post(api, spec["path"], p0)}
        rec["probes"]["speed_up"] = {"payload": p1, **_post(api, spec["path"], p1)}
        for extra in spec.get("extra", []):
            pe = json.loads(json.dumps(spec["base"]))
            for path_k, val in extra["set"].items():
                pe = _mutate(pe, path_k, val)
            rec["probes"][extra["name"]] = {"payload": pe, **_post(api, spec["path"], pe)}
        if spec.get("topic_key"):
            pt = _mutate(spec["base"], spec["topic_key"], spec["topic_values"][1])
            rec["probes"]["topic_alt"] = {"payload": pt, **_post(api, spec["path"], pt)}
        records.append(rec)

    snap = {
        "mission": 1752,
        "schema": "surrogate_nondegeneracy/1",
        "api": api,
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "constants": {
            "tol_determinism": TOL_DETERMINISM,
            "tol_sensitivity": TOL_SENSITIVITY,
            "exponent_band": list(EXPONENT_BAND),
            "cross_engine_band": CROSS_ENGINE_BAND,
        },
        "records": records,
        "interface_notes": INTERFACE_NOTES,
    }
    return snap


def _openapi_tags(api: str, path: str) -> Optional[str]:
    """Fetch the OpenAPI summary of ``path`` (label honesty: [MOCK]/[STUB])."""
    url = api.rstrip("/")
    url = url[: url.rfind("/api/")] if "/api/" in url else url
    try:
        with urllib.request.urlopen(url + "/openapi.json", timeout=30) as r:
            spec = json.load(r)
    except Exception:
        return None
    full = "/api/v1/" + path.lstrip("/")
    node = spec.get("paths", {}).get(full, {})
    for _method, op in node.items():
        return op.get("summary")
    return None


# ---------------------------------------------------------------------------
# Numeric fingerprinting: criterion must not depend on response field names
# ---------------------------------------------------------------------------
def numeric_leaves(obj: Any, prefix: str = "") -> Dict[str, float]:
    """Flatten a JSON body into {dotted.path: float} (bools excluded)."""
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(numeric_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(numeric_leaves(v, f"{prefix}[{i}]"))
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out[prefix] = float(obj)
    return out


def _rel(a: float, b: float) -> float:
    """Relative difference, guarded at zero."""
    scale = max(abs(a), abs(b))
    if scale < 1e-12:
        return 0.0
    return abs(a - b) / scale


def fingerprint_diff(body_a: Any, body_b: Any) -> Dict[str, Any]:
    """Compare two bodies by their numeric leaves.

    Returns ``changed`` (number of leaves that moved beyond tol), ``max_rel``,
    ``common`` (number of leaves present in both) and per-leaf details.
    """
    fa, fb = numeric_leaves(body_a), numeric_leaves(body_b)
    common = sorted(set(fa) & set(fb))
    diffs = []
    for k in common:
        r = _rel(fa[k], fb[k])
        diffs.append({"key": k, "a": fa[k], "b": fb[k], "rel": r})
    changed = [d for d in diffs if d["rel"] > TOL_DETERMINISM]
    return {
        "common_keys": len(common),
        "only_a": len(set(fa) - set(fb)),
        "only_b": len(set(fb) - set(fa)),
        "changed": len(changed),
        "max_rel": max([d["rel"] for d in diffs], default=0.0),
        "details": sorted(diffs, key=lambda d: -d["rel"])[:6],
    }


def resolve_output(body: Any, key: Optional[str]) -> Optional[float]:
    """Resolve the declared output quantity in ``body``.

    Order: exact dotted path -> exact dotted leaf -> suffix match on the leaf
    name (so ``total_resistance`` finds ``[0].total_resistance``).  Among suffix
    candidates the largest magnitude wins, because envelope metadata such as
    ``friction_resistance`` would otherwise shadow the total.
    """
    if not key:
        return None
    exact = _at(body, key)
    if isinstance(exact, (int, float)) and not isinstance(exact, bool):
        return float(exact)
    leaves = numeric_leaves(body)
    if key in leaves:
        return leaves[key]
    cands = [(k, v) for k, v in leaves.items()
             if k.split(".")[-1].endswith(key) or k.endswith(key)]
    if not cands:
        return None
    return max(cands, key=lambda kv: abs(kv[1]))[1]


def primary_scalar(body: Any, key: Optional[str] = None) -> Optional[float]:
    """Declared output when known, else the largest non-diagnostic leaf."""
    if key:
        return resolve_output(body, key)
    leaves = numeric_leaves(body)
    leaves = {k: v for k, v in leaves.items()
              if not any(t in k.lower() for t in
                         ("elapsed", "time", "ms", "confidence", "re", "fn",
                          "loss", "importance", "id"))}
    if not leaves:
        return None
    key = max(leaves, key=lambda k: abs(leaves[k]))
    return leaves[key]


# ---------------------------------------------------------------------------
# Verdict rules (pure functions of the snapshot)
# ---------------------------------------------------------------------------
def judge_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Apply D1/D2/D3/L1 to one endpoint record."""
    probes = rec["probes"]
    base, rep, up = probes["base"], probes["base_repeat"], probes["speed_up"]
    verdict: Dict[str, Any] = {
        "id": rec["id"], "path": rec["path"], "role": rec["role"],
        "status_base": base["status"], "status_speed_up": up["status"],
        "label_expected": rec.get("label_expected"),
        "openapi_summary": rec.get("openapi_tags"),
        "criteria": {}, "failures": [],
    }

    transport_ok = base["status"] == 200 and up["status"] == 200
    verdict["reachable"] = transport_ok
    if not transport_ok:
        verdict["classification"] = "UNREACHABLE"
        verdict["criteria"] = {"D1": None, "D2": None, "D3": None, "L1": None}
        verdict["failures"] = [f"HTTP {base['status']}/{up['status']}"]
        return verdict

    # D1 determinism ------------------------------------------------------
    det = fingerprint_diff(base["body"], rep["body"])
    d1 = det["changed"] == 0
    verdict["criteria"]["D1"] = {"pass": d1, **{k: det[k] for k in
                                                ("changed", "max_rel", "common_keys")}}
    if not d1:
        verdict["failures"].append(f"D1 stochastic: {det['changed']} leaves moved "
                                   f"(max rel {det['max_rel']:.3e})")

    # D2 sensitivity ------------------------------------------------------
    # Primary lever is a physical input (speed).  For endpoints that expose no
    # physical input at all, fall back to a *topical* lever (the requested
    # quantity) and record which basis was used -- the criterion stays honest.
    topic = rec["probes"].get("topic_alt")
    extras = [k for k in rec["probes"] if k.startswith("contrast_")]
    if rec.get("speed_key"):
        sens = fingerprint_diff(base["body"], up["body"])
        same_payload = base["payload"] == up["payload"]
        basis = "physical:ship_speed"
    elif topic is not None:
        sens = fingerprint_diff(base["body"], topic["body"])
        same_payload = base["payload"] == topic["payload"]
        basis = "topical:prediction_type"
    else:
        sens = None
        same_payload = True
        basis = "none"
    # A degenerate verdict must survive every available lever, not just one.
    contrast: List[Dict[str, Any]] = []
    for name in (["speed_up", "topic_alt"] + extras):
        if name not in rec["probes"]:
            continue
        d = fingerprint_diff(base["body"], rec["probes"][name]["body"])
        contrast.append({"probe": name, "changed": d["changed"], "max_rel": d["max_rel"]})
    best_changed = max([c["changed"] for c in contrast], default=0)
    d2 = bool(best_changed > 0) if sens is not None else (best_changed > 0 if contrast else None)
    verdict["criteria"]["D2"] = {
        "pass": d2 if sens is not None else None,
        "basis": basis,
        "payload_identical": same_payload,
        "changed": sens["changed"] if sens else None,
        "max_rel": sens["max_rel"] if sens else None,
        "common_keys": sens["common_keys"] if sens else None,
        "contrast_probes": contrast,
        "best_changed": best_changed,
    }
    if not d2:
        verdict["failures"].append(
            f"D2 input-invariant: all {len(contrast)} levers moved 0 numeric leaves "
            f"({basis} + {[c['probe'] for c in contrast]})")

    # D3 physical scaling -------------------------------------------------
    out_key = rec.get("output_key")
    s0, s1 = resolve_output(base["body"], out_key), resolve_output(up["body"], out_key)
    d3: Dict[str, Any] = {"pass": None, "output_key": out_key,
                          "scalar_a": s0, "scalar_b": s1}
    v0 = _speed_of(rec, "base")
    v1 = _speed_of(rec, "speed_up")
    if s0 is None or s1 is None or v0 is None or v1 is None or s0 <= 0 or s1 <= 0 or v0 <= 0 or v1 <= 0:
        d3["reason"] = "declared output missing/non-positive or no speed lever"
        verdict["criteria"]["D3"] = d3
    else:
        n_eff = math.log(s1 / s0) / math.log(v1 / v0)
        lo, hi = EXPONENT_BAND
        d3.update({"exponent": n_eff, "band": [lo, hi]})
        d3["pass"] = bool(lo <= n_eff <= hi)
        verdict["criteria"]["D3"] = d3
        if not d3["pass"]:
            verdict["failures"].append(
                f"D3 non-physical scaling: d lnR/d lnV = {n_eff:.3f} outside [{lo}, {hi}]")

    # L1 label honesty ----------------------------------------------------
    summary = (rec.get("openapi_tags") or "")
    declared = None
    for tag in ("MOCK", "STUB"):
        if tag in summary.upper():
            declared = tag
            break
    degenerate = (verdict["criteria"]["D1"]["pass"] is False
                  or verdict["criteria"]["D2"]["pass"] is False)
    l1: Dict[str, Any] = {"declared": declared, "degenerate": degenerate}
    if degenerate and declared is None:
        l1["pass"] = False
        l1["finding"] = "UNDECLARED_DEGENERATE"
    else:
        l1["pass"] = True
        l1["finding"] = "OK"
    verdict["criteria"]["L1"] = l1
    if not l1["pass"]:
        verdict["failures"].append("L1 label: degenerate behaviour is not declared in OpenAPI summary")

    verdict["classification"] = _classify(verdict, rec)
    return verdict


def _speed_of(rec: Dict[str, Any], which: str) -> Optional[float]:
    key = rec.get("speed_key")
    if not key:
        return None
    val = _at(rec["probes"][which]["payload"], key)
    if isinstance(val, list):
        return float(val[0]) if val else None
    return float(val) if isinstance(val, (int, float)) else None


def _classify(verdict: Dict[str, Any], rec: Dict[str, Any]) -> str:
    c = verdict["criteria"]
    if verdict.get("classification") == "UNREACHABLE":
        return "UNREACHABLE"
    d1 = c["D1"]["pass"] if c["D1"] else None
    d2 = c["D2"]["pass"] if c["D2"] else (None if c["D2"].get("pass") is None else c["D2"]["pass"])
    if d2 is None:
        return "NOT_JUDGED"
    if d1 is False:
        return "MOCK_STOCHASTIC"
    if d2 is False:
        return "MOCK_DEGENERATE"
    d3 = c["D3"].get("pass")
    declared = (c["L1"] or {}).get("declared")
    if d3 is False:
        return "SUSPECT_SCALING"
    if declared == "MOCK":
        # deterministic + input-sensitive + physically scaled, yet shipped as a
        # documented mock: a closed-form stand-in, not a trained model.
        return "HEURISTIC_FORMULA_DECLARED_MOCK"
    return "BEHAVES_AS_MODEL"


def judge_all(snap: Dict[str, Any]) -> Dict[str, Any]:
    verdicts = [judge_record(r) for r in snap["records"]]
    counts: Dict[str, int] = {}
    for v in verdicts:
        counts[v["classification"]] = counts.get(v["classification"], 0) + 1

    controls = [v for v in verdicts if v["role"].startswith("control")]
    ctl_mock = [v for v in controls if v["role"] == "control_mock"]
    ctl_real = [v for v in controls if v["role"] == "control_real"]
    control_ok = {
        # POSITIVE control: a stochastic mock (documented [MOCK] + noise) must
        # land exactly on MOCK_STOCHASTIC, i.e. D1 must fire.
        "stochastic_mock_caught": any(
            v["classification"] == "MOCK_STOCHASTIC" for v in ctl_mock),
        # NEGATIVE control: the REAL physics engine must pass D1, D2 and D3.
        "real_engine_passes": all(
            (v["criteria"]["D1"] or {}).get("pass") is True
            and (v["criteria"]["D2"] or {}).get("pass") is True
            and (v["criteria"]["D3"] or {}).get("pass") is True
            for v in ctl_real) and bool(ctl_real),
    }
    control_ok["detector_non_vacuous"] = (
        control_ok["stochastic_mock_caught"] and control_ok["real_engine_passes"])

    # Cross-engine diagnostic: every resistance-family answer vs the platform's
    # own physics engine at the same hull/speed.  Reported as a RATIO, not a
    # verdict, because the responses do not declare force units (finding in
    # itself) -- a ratio of 6x is unambiguous, 1.02x is not proof of agreement.
    engine = next((v for v in verdicts if v["id"] == "ctl_real_resistance_engine"), None)
    engine_rec = next((r for r in snap["records"] if r["id"] == "ctl_real_resistance_engine"), None)
    eng_val = None
    if engine and engine_rec:
        eng_val = resolve_output(engine_rec["probes"]["base"]["body"], engine_rec.get("output_key"))
    cross: List[Dict[str, Any]] = []
    if eng_val:
        for v in verdicts:
            if v["role"] != "subject" and v["id"] != "ctl_mock_pinn_resistance":
                continue
            rec = next(r for r in snap["records"] if r["id"] == v["id"])
            # only force-like answers may be compared with the engine's R_T;
            # comparing a shipped POWER with a resistance would be unsound.
            if rec.get("output_key") not in RESISTANCE_OUTPUTS:
                continue
            val = resolve_output(rec["probes"]["base"]["body"], rec.get("output_key"))
            if val is None or val <= 0:
                continue
            cross.append({
                "endpoint": v["path"], "output_key": rec.get("output_key"),
                "value": val, "engine_rt": eng_val, "ratio": val / eng_val,
                "deviation": abs(val / eng_val - 1.0),
                "note": "ratio only: force units are not declared in the response",
            })

    return {
        "mission": snap["mission"],
        "snapshot_utc": snap["captured_utc"],
        "api": snap["api"],
        "constants": snap["constants"],
        "verdicts": verdicts,
        "counts": counts,
        "controls": control_ok,
        "cross_engine": cross,
        "engine_rt": eng_val,
        "subjects_unlabelled_degenerate": [
            v["path"] for v in verdicts
            if v["role"] == "subject" and not v["criteria"]["L1"].get("pass", True)],
    }


def verdicts_markdown(result: Dict[str, Any]) -> str:
    lines = [
        "# Surrogate / prediction endpoint non-degeneracy verdicts (mission m1752)",
        "",
        f"- snapshot: `{result['snapshot_utc']}`  ·  API: `{result['api']}`",
        f"- criteria: D1 determinism (tol {result['constants']['tol_determinism']}), "
        f"D2 input sensitivity, D3 admissible resistance exponent "
        f"{result['constants']['exponent_band']}, L1 label honesty",
        f"- controls non-vacuous: **{result['controls']['detector_non_vacuous']}** "
        f"(stochastic mock caught={result['controls']['stochastic_mock_caught']}, "
        f"real engine passes={result['controls']['real_engine_passes']})",
        "",
        "| endpoint | role | class | D1 | D2 | D3 (n_eff) | declared |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in result["verdicts"]:
        c = v["criteria"]
        d1 = "-" if not c["D1"] else ("PASS" if c["D1"]["pass"] else "**FAIL**")
        d2 = "-" if not c["D2"] else ("PASS" if c["D2"]["pass"] else "**FAIL**")
        if c["D3"] is None or c["D3"].get("exponent") is None:
            d3 = "-"
        else:
            d3 = f"{c['D3']['exponent']:.2f} " + ("PASS" if c["D3"]["pass"] else "**FAIL**")
        declared = (c["L1"]["declared"] or "—") if c["L1"] else "—"
        if c["L1"] and not c["L1"].get("pass", True):
            declared += " ⚠️"
        lines.append(f"| `{v['path']}` | {v['role']} | {v['classification']} | "
                     f"{d1} | {d2} | {d3} | {declared} |")
    lines += ["", "## Failures", ""]
    any_f = False
    for v in result["verdicts"]:
        for f in v["failures"]:
            lines.append(f"- `{v['path']}`: {f}")
            any_f = True
    if not any_f:
        lines.append("- none")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
def figure(result: Dict[str, Any], snap: Dict[str, Any], out: str = FIGURE) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(os.path.dirname(out), exist_ok=True)
    by_id = {r["id"]: r for r in snap["records"]}
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.0))

    # left: criterion matrix -------------------------------------------------
    ax = axes[0]
    rows = result["verdicts"]
    cols = ["D1", "D2", "D3", "L1"]
    grid = []
    for v in rows:
        row = []
        for c in cols:
            cell = v["criteria"].get(c)
            if cell is None or cell.get("pass") is None:
                row.append(0.5)
            else:
                row.append(1.0 if cell["pass"] else 0.0)
        grid.append(row)
    ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(["D1 determinism", "D2 sensitivity", "D3 scaling", "L1 label"])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{v['path']}" for v in rows], fontsize=7.5, family="monospace")
    for i, v in enumerate(rows):
        ax.text(len(cols) - 0.35, i, v["classification"].replace("_", " "),
                fontsize=6.6, va="center", ha="left", color="black")
    ax.set_title("Non-degeneracy criteria (green=pass, red=fail, grey=n/a)", fontsize=10)
    ax.set_xlim(-0.5, len(cols) + 2.6)

    # right: response vs speed for endpoints exposing a scalar ---------------
    ax = axes[1]
    for v in rows:
        rec = by_id[v["id"]]
        s0, s1 = primary_scalar(rec["probes"]["base"]["body"]), primary_scalar(rec["probes"]["speed_up"]["body"])
        v0, v1 = _speed_of(rec, "base"), _speed_of(rec, "speed_up")
        if None in (s0, s1, v0, v1) or s0 <= 0 or s1 <= 0:
            continue
        ax.plot([v0, v1], [s0, s1], marker="o", ms=4, lw=1.4,
                label=f"{v['path']} ({v['classification'].lower()})")
        ax.annotate(f"{v0:.0f}→{v1:.0f} m/s  x{(s1/s0):.2f}", (v1, s1),
                    fontsize=6, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("ship speed [m/s]  (same hull, one input moved)")
    ax.set_ylabel("dominant output scalar (platform units, log scale)")
    ax.set_yscale("log")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=6.3, loc="best")
    ax.set_title("Response to a +41% speed step: flat or noisy line = degenerate", fontsize=10)
    fig.suptitle("m1752 · surrogate endpoint non-degeneracy audit (ship performance platform :8001)",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default=os.environ.get("SHIP_API_BASE", API_DEFAULT))
    ap.add_argument("--probe", action="store_true", help="live probe -> snapshot")
    ap.add_argument("--check", action="store_true", help="snapshot -> verdicts (+md)")
    ap.add_argument("--figure", action="store_true", help="verdicts -> png")
    args = ap.parse_args(list(argv) if argv is not None else None)

    os.makedirs(ART_DIR, exist_ok=True)
    if args.probe:
        snap = probe(args.api)
        with open(SNAPSHOT, "w") as fh:
            json.dump(snap, fh, indent=1)
        print(f"probe: {len(snap['records'])} endpoints -> {SNAPSHOT}")
    if args.check:
        snap = json.load(open(SNAPSHOT))
        result = judge_all(snap)
        result["snapshot_sha256"] = hashlib.sha256(
            json.dumps(snap, sort_keys=True).encode()).hexdigest()[:16]
        with open(VERDICTS, "w") as fh:
            json.dump(result, fh, indent=1)
        with open(VERDICTS_MD, "w") as fh:
            fh.write(verdicts_markdown(result))
        print(f"check: {result['counts']} controls={result['controls']}")
        print(f"       -> {VERDICTS} , {VERDICTS_MD}")
    if args.figure:
        result = json.load(open(VERDICTS))
        snap = json.load(open(SNAPSHOT))
        print("figure:", figure(result, snap))
    if not (args.probe or args.check or args.figure):
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())