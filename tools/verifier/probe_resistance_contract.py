"""Black-box probe for `POST /api/v1/calculations/resistance` (calc.resistance).

Refreshes `artifacts/verifier/resistance_contract/captures.json`: one record per
POST, holding the request verbatim, the HTTP status, and either the parsed JSON
body (200) or the raw error body (4xx/5xx).  Nothing is edited by hand -- the
gate (`audit_resistance_contract.py`) and the report renderer only ever read
this file.

The OpenAPI fragment of the endpoint is captured too, because the unit contract
(D1) is a *declaration* defect: the fix is only verifiable if the schema itself
says what `speed` means.

Cases (mission 1611):

    boxbarge_5/10/20kn        power-law sweep (C12)
    midcb_10kn                Cb=0.8, self-consistent nabla (extra 200 case)
    kcs_like_15p5kn           realistic hull form, speed in knots
    kcs_like_15p5kn_ms        same physical speed declared as "m/s"  -> C14
    boxbarge_10kn_disp8000    nabla off by 100 %                     -> C9  (422)
    invalid_cb_1p2            Cb>1, physically impossible            -> C10 (422)
    zero_speed                V=0                                    -> C11 (422)
    negative_speed            V<0                                    -> C11b(422)

Usage:
    python3 tools/verifier/probe_resistance_contract.py \
        [--base http://127.0.0.1:8001] \
        [--out artifacts/verifier/resistance_contract/captures.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

PATH = "/api/v1/calculations/resistance"
KNOTS = 1852.0 / 3600.0

BARGE = {"length": 100.0, "beam": 20.0, "draft": 8.0, "displacement": 16400.0,
         "cb": 1.0, "speed": 10.0}
KCS = {"length": 230.0, "beam": 32.2, "draft": 10.8, "displacement": 52030.0,
       "cb": 0.651, "speed": 15.5}


def _cases() -> List[Dict[str, Any]]:
    c = lambda cid, **kw: {"case_id": cid, "request": kw}
    return [
        c("boxbarge_5kn", **{**BARGE, "speed": 5.0}),
        c("boxbarge_10kn", **BARGE),
        c("boxbarge_20kn", **{**BARGE, "speed": 20.0}),
        # Cb=0.8 with nabla = L*B*T*Cb = 12800 m^3 exactly -> must be accepted
        c("midcb_10kn", **{**BARGE, "cb": 0.8, "displacement": 12800.0}),
        c("kcs_like_15p5kn", **KCS),
        # D1 verification: identical physical speed, declared as SI m/s
        c("kcs_like_15p5kn_ms", **{**KCS, "speed": round(15.5 * KNOTS, 9),
                                   "speed_unit": "m/s"}),
        # D4 verification: nabla inconsistent with L*B*T*Cb -> 422
        c("boxbarge_10kn_disp8000", **{**BARGE, "displacement": 8000.0}),
        # D3 verification: impossible geometry -> 422
        c("invalid_cb_1p2", **{**BARGE, "cb": 1.2}),
        # D5 verification: V=0 / V<0 -> 4xx, never 5xx
        c("zero_speed", **{**BARGE, "speed": 0.0}),
        c("negative_speed", **{**BARGE, "speed": -3.0}),
    ]


def _post(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return {"http_status": r.status, "response": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err: Any = json.loads(raw)
        except json.JSONDecodeError:
            err = raw
        return {"http_status": e.code, "error_body": err}


def _openapi(base: str, path: str) -> Dict[str, Any]:
    with urllib.request.urlopen(base + "/openapi.json", timeout=60) as r:
        spec = json.loads(r.read())
    op = spec.get("paths", {}).get(path, {}).get("post", {})
    ref = (op.get("requestBody", {}).get("content", {})
             .get("application/json", {}).get("schema", {}).get("$ref", ""))
    name = ref.rsplit("/", 1)[-1]
    schema = spec.get("components", {}).get("schemas", {}).get(name, {})
    resp_ref = (op.get("responses", {}).get("200", {}).get("content", {})
                  .get("application/json", {}).get("schema", {}).get("$ref", ""))
    resp_schema = spec.get("components", {}).get("schemas", {}).get(
        resp_ref.rsplit("/", 1)[-1], {})
    return {
        "request_model": name,
        "request_properties": schema.get("properties", {}),
        "request_required": schema.get("required", []),
        "endpoint_summary": op.get("summary", ""),
        "endpoint_description": op.get("description", ""),
        "response_model": resp_ref.rsplit("/", 1)[-1],
        "response_properties": sorted(resp_schema.get("properties", {})),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--out", default=str(
        root / "artifacts/verifier/resistance_contract/captures.json"))
    args = ap.parse_args()

    url = args.base.rstrip("/") + PATH
    cases = []
    for spec in _cases():
        rec = {"case_id": spec["case_id"], "request": spec["request"]}
        rec.update(_post(url, spec["request"]))
        cases.append(rec)
        tail = ""
        if rec["http_status"] == 200:
            r = rec["response"]
            tail = f'Rt={r["rt"]} kN  ct={r["ct"]}  cf_total={r.get("cf_total")}'
        print(f'{spec["case_id"]:<24} HTTP {rec["http_status"]}  {tail}')

    schema = _openapi(args.base.rstrip("/"), PATH)
    caps = {
        "meta": {
            "endpoint": f"POST {url}",
            "captured_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mission": 1611,
            "probe": "tools/verifier/probe_resistance_contract.py",
            "note": ("Verbatim platform responses, unedited. Rows with http_status!=200 "
                     "record transport-level observations only (error body as returned)."),
            "request_schema_found": sorted(schema["request_properties"]),
            "openapi": schema,
        },
        "reference_chain": {
            "knots_to_ms": KNOTS,
            "knots_to_ms_before_fix": 0.5144,
            "g": 9.81,
            "rho_seawater": 1025.0,
            "nu_seawater_15C": 1.19e-6,
            "ittc1957_cf": "0.075/(log10(Re)-2)^2",
            "ittc1978_roughness_delta_cf": 0.0004,
        },
        "cases": cases,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(caps, indent=2, ensure_ascii=False))
    print(f"captures -> {out} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
