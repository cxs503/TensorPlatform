"""Probe plan + HTTP/cache layer for the structure-domain audit.

The same probe plan is used for the live platform endpoints and for the analytic
positive-control candidate, so a criterion cannot accidentally depend on which
"server" produced the numbers.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .criteria import Record

API_PREFIX = "/api/v1"

EP_PB = "/fea/plate-buckling"
EP_SA = "/structure/analyze"
EP_SP = "/structure/plate"
EP_SF = "/structure/fatigue"

KCS = {"L": 230.0, "B": 32.2, "T": 10.8, "D": 19.0,
       "displacement": 52030.0, "Cb": 0.651,
       "frame_spacing": 0.6, "plate_thickness": 0.015, "yield_stress": 235.0}
BULKER = {"L": 180.0, "B": 32.0, "T": 11.0, "D": 17.0,
          "displacement": 48000.0, "Cb": 0.82,
          "frame_spacing": 0.75, "plate_thickness": 0.018, "yield_stress": 315.0}

_HULL_CASES = [
    ("sa_base", KCS),
    ("sa_L150", {**KCS, "L": 150.0}),
    ("sa_L320", {**KCS, "L": 320.0}),
    ("sa_t12", {**KCS, "plate_thickness": 0.012}),
    ("sa_t30", {**KCS, "plate_thickness": 0.030}),
    ("sa_ys355", {**KCS, "yield_stress": 355.0}),
    ("sa_bulker", BULKER),
]

_PB_BASE = {"E": 210000.0, "nu": 0.3, "yield_stress": 235.0,
            "load_type": "compression", "boundary": "simply_supported"}

PROBE_PLAN: dict[str, list[tuple[str, dict]]] = {
    EP_PB: [
        ("pb_r0.4", {**_PB_BASE, "a": 0.48, "b": 1.2, "t": 0.012}),
        ("pb_r1.0", {**_PB_BASE, "a": 1.20, "b": 1.2, "t": 0.012}),
        ("pb_r1.5", {**_PB_BASE, "a": 1.80, "b": 1.2, "t": 0.012}),
        ("pb_r2.5", {**_PB_BASE, "a": 3.00, "b": 1.2, "t": 0.012}),
        ("pb_r4.0", {**_PB_BASE, "a": 4.80, "b": 1.2, "t": 0.012}),
        ("pb_t_lo", {**_PB_BASE, "a": 3.00, "b": 1.2, "t": 0.012}),
        ("pb_t_hi", {**_PB_BASE, "a": 3.00, "b": 1.2, "t": 0.024}),
        ("pb_b_double", {**_PB_BASE, "a": 6.00, "b": 2.4, "t": 0.012}),
        ("pb_E_half", {**_PB_BASE, "a": 3.00, "b": 1.2, "t": 0.012, "E": 105000.0}),
    ],
    EP_SA: list(_HULL_CASES),
    EP_SP: [
        ("sp_base", KCS),
        ("sp_t6", {**KCS, "plate_thickness": 0.006, "frame_spacing": 1.2}),
        ("sp_t12", {**KCS, "plate_thickness": 0.012, "frame_spacing": 1.2}),
        ("sp_ys355", {**KCS, "yield_stress": 355.0}),
    ],
    EP_SF: list(_HULL_CASES),
}

#: cases that a criterion compares against each other, used by the pair-based tests
REFERENCE_POINT = "pb_r2.5"


def _post(base_url: str, path: str, payload: dict, timeout: float) -> tuple[dict, float, str]:
    url = base_url.rstrip("/") + API_PREFIX + path
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        return body, time.time() - t0, "ok"
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "detail": exc.read().decode()[:400]}, \
               time.time() - t0, f"http_{exc.code}"
    except Exception as exc:  # timeout, connection reset...
        return {"error": repr(exc)}, time.time() - t0, "error"


def probe_live(base_url: str, timeout: float = 90.0,
               plan: dict | None = None) -> tuple[list[Record], list[dict]]:
    """Call the live endpoints following PROBE_PLAN; return records + call log."""
    plan = plan or PROBE_PLAN
    records, log = [], []
    for endpoint, cases in plan.items():
        for case, payload in cases:
            body, dt, status = _post(base_url, endpoint, payload, timeout)
            log.append({"endpoint": endpoint, "case": case, "status": status,
                        "seconds": round(dt, 3)})
            records.append(Record(case=case, endpoint=endpoint,
                                  inputs=payload, output=body))
    return records, log


def probe_reference(plan: dict | None = None) -> list[Record]:
    """Evaluate the analytic positive control on exactly the same probe plan."""
    from . import reference_candidate as rc
    plan = plan or PROBE_PLAN
    records = []
    for endpoint, cases in plan.items():
        for case, payload in cases:
            records.append(Record(case=case, endpoint=endpoint,
                                  inputs=payload, output=rc.respond(endpoint, payload)))
    return records


def save_cache(path: str, records: list[Record], log: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "n_records": len(records), "call_log": log,
               "records": [r.__dict__ for r in records]}
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_cache(path: str) -> tuple[list[Record], list[dict]]:
    with open(path) as fh:
        payload = json.load(fh)
    records = [Record(**r) for r in payload["records"]]
    return records, payload.get("call_log", [])