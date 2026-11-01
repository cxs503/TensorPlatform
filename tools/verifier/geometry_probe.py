"""Read-only probe: capture geometry evidence from ship platform :8001 into artifacts/."""
import json, os, time, urllib.request, urllib.error

B = "http://127.0.0.1:8001/api/v1"
OUT = os.path.join(os.path.dirname(__file__), "artifacts", "geometry_audit")
os.makedirs(OUT, exist_ok=True)


def get(p, t=90):
    t0 = time.time()
    try:
        with urllib.request.urlopen(B + p, timeout=t) as r:
            return {"ok": True, "data": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        return {"ok": False, "err": f"HTTP {e.code}", "body": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


names = ["KCS", "KVLCC2", "DTMB5415", "Series60", "Wigley", "barge"]
dump = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "probes": {}}
for n in names:
    dump["probes"][n] = {
        "geometry": get(f"/geometry/{n}"),
        "verify": get(f"/geometry/verify/{n}"),
        "offsets": get(f"/geometry/offsets/{n}"),
    }
# draft sweep on KCS + KVLCC2
draft_sweep = {}
for n in ["KCS", "KVLCC2"]:
    draft_sweep[n] = {}
    for T in (2.0, 4.0, 6.0, 8.0, 10.8, 12.0):
        draft_sweep[n][str(T)] = get(f"/geometry/hydro-at/{n}?draft={T}")
dump["draft_sweep"] = draft_sweep

json.dump(dump, open(os.path.join(OUT, "raw_probe.json"), "w"), ensure_ascii=False, indent=1)

for n in names:
    g = dump["probes"][n]["geometry"]
    v = dump["probes"][n]["verify"]
    o = dump["probes"][n]["offsets"]
    def brief(d):
        if not d["ok"]:
            return f"ERR {d['err']}"
        dd = d["data"]
        if isinstance(dd, dict):
            geo = dd.get("geometry") or {}
            keys = [k for k in ("L", "B", "T", "Cb", "Cm", "Cp") if k in geo]
            return f"keys={list(dd.keys())[:6]} geo={ {k: geo[k] for k in keys} }"
        return str(dd)[:120]
    print(f"[{n}] geom: {brief(g)}")
    print(f"      verify: {json.dumps(v['data'], ensure_ascii=False)[:160] if v['ok'] else v['err']}")
    if o["ok"]:
        tb = o["data"].get("table") or []
        print(f"      offsets rows={len(tb)} cols={len(tb[0]) if tb else 0} max={max(max(r[1:]) for r in tb if len(r)>1) if tb else None}")
    else:
        print(f"      offsets: {o['err']}")
print("\ndraft sweep KCS:", {k: (v["data"]["hydrostatics"]["Cb"] if v["ok"] else v["err"]) for k, v in draft_sweep["KCS"].items()})
print("draft sweep KVLCC2:", {k: (v["data"]["hydrostatics"]["Cb"] if v["ok"] else v["err"]) for k, v in draft_sweep["KVLCC2"].items()})