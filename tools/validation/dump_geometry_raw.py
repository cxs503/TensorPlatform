#!/usr/bin/env python3
"""Dump live hydrostatics/geometry responses from the ship platform (:8001).

Writes raw JSON to artifacts/geometry_hydro_raw/ and prints a compact digest so the
anchor evaluator (tools/validation/probe_geometry_hydrostatics.py) can be written
against the real schema.  READ-ONLY: only GET requests.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "artifacts", "geometry_hydro_raw")


def get(path: str, timeout: int = 60):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # noqa: PERF203
        return e.code, e.read().decode()[:400]
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    os.makedirs(OUT, exist_ok=True)
    st, cats = get("/geometry/categories")
    print("categories", st, json.dumps(cats, ensure_ascii=False)[:400])
    names = []
    for c in (cats if isinstance(cats, list) else cats.get("categories", []) if isinstance(cats, dict) else []):
        key = c if isinstance(c, str) else (c.get("key") or c.get("name") or c.get("id"))
        st, models = get(f"/geometry/models/{key}")
        print("models", key, st, json.dumps(models, ensure_ascii=False)[:600])
        ml = models if isinstance(models, list) else (models.get("models") or models.get("ships") or []) if isinstance(models, dict) else []
        for m in ml:
            if isinstance(m, str):
                names.append(m)
            else:
                n = m.get("name") or m.get("ship_name") or m.get("id") or m.get("key")
                if n:
                    names.append(n)
    print("NAMES", names)
    for n in names:
        for tag, path in (("curves", f"/geometry/hydro-curves/{n}"), ("verify", f"/geometry/verify/{n}")):
            st, body = get(path)
            fn = os.path.join(OUT, f"{n.replace('/', '_')}__{tag}.json")
            json.dump({"status": st, "body": body}, open(fn, "w"), ensure_ascii=False, indent=1)
            s = json.dumps(body, ensure_ascii=False)
            print(f"--- {n} {tag} http={st} len={len(s)}")
            print(s[:900])


if __name__ == "__main__":
    main()