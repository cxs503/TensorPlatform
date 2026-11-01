"""Capture raw geometry-capability API responses to disk (provenance for the audit).

Every number in the audit report is computed from the JSON files this script writes;
nothing is typed by hand.  Filenames are stable so the pytest suite can run offline.

Usage:
    python3 tools/verifier/geometry/capture_api.py --out artifacts/verifier/geometry/api_captures
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE = os.environ.get("TP_SHIP_API", "http://127.0.0.1:8001/api/v1")

SHIPS = {
    # name -> (L, B, T, Cb) as published in the platform's own standard-model registry
    "KCS": (230.0, 32.2, 10.8, 0.651),
    "KVLCC2": (320.0, 58.0, 20.8, 0.81),
}

CURVE_DRAFTS = (2.0, 4.0, 6.0, 8.0, 10.8, 12.0)  # includes d > T (clamp probe) and d = T


def _call(base: str, path: str, body=None, timeout: float = 300.0):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        payload = json.load(urllib.request.urlopen(req, timeout=timeout))
        return {"ok": True, "path": path, "method": "POST" if body is not None else "GET",
                "elapsed_s": round(time.time() - t0, 2), "request": body, "response": payload}
    except urllib.error.HTTPError as e:  # keep the status code: it is itself evidence
        return {"ok": False, "path": path, "method": "POST" if body is not None else "GET",
                "elapsed_s": round(time.time() - t0, 2), "request": body,
                "http_status": e.code, "error": str(e)[:200]}
    except Exception as e:  # noqa: BLE001 - timeout / connection is evidence too
        return {"ok": False, "path": path, "method": "POST" if body is not None else "GET",
                "elapsed_s": round(time.time() - t0, 2), "request": body, "error": str(e)[:200]}


def capture(base: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    index = {"base": base, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": {}}

    def save(name: str, rec: dict):
        with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
        index["files"][name] = {"ok": rec["ok"], "path": rec["path"],
                                "http_status": rec.get("http_status"), "elapsed_s": rec["elapsed_s"]}

    save("standard_models_ships", _call(base, "/standard-models/ships"))
    for name, (L, B, T, cb) in SHIPS.items():
        save(f"offsets_{name}", _call(base, f"/geometry/offsets/{name}"))
        save(f"hydro_curves_{name}", _call(base, f"/geometry/hydro-curves/{name}?n_drafts=6"))
        save(f"hydro_at_{name}_design", _call(base, f"/geometry/hydro-at/{name}?draft={T}"))
        save(f"verify_{name}", _call(base, f"/geometry/verify/{name}"))
        save(f"generate_{name}", _call(base, "/geometry/generate", {
            "model_id": name, "category": "container" if name == "KCS" else "tanker",
            "params": {"Lpp": L, "B": B, "T": T, "Cb": cb}}))
    # draft sweep on one hull -> draft-dependence (or not) of the hydrostatic shape
    kL, kB, kT, _ = SHIPS["KCS"]
    for d in CURVE_DRAFTS:
        tag = int(d) if float(d).is_integer() else d
        save(f"hydro_at_KCS_d{tag}", _call(base, f"/geometry/hydro-at/KCS?draft={d}"))
    # negative controls: do the endpoints fail closed on unknown input?
    save("verify_nosuchship", _call(base, "/geometry/verify/NOSUCHSHIP"))
    save("hydro_at_nosuchship", _call(base, "/geometry/hydro-at/NOSUCHSHIP?draft=5"))
    save("generate_missing_model", _call(base, "/geometry/generate", {"params": {"Lpp": kL, "B": kB, "T": kT}}))

    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    return index


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default="artifacts/verifier/geometry/api_captures")
    args = ap.parse_args()
    idx = capture(args.base, args.out)
    for name, meta in idx["files"].items():
        print(f"{name:28s} ok={meta['ok']!s:5s} status={meta['http_status']} t={meta['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())