import json, os, sys
sys.path.insert(0, ".")
from tphttp import req
tok = open("token.txt").read().strip()
st, d = req("GET", "tensor/missions?limit=50", token=tok)
print("status", st)
json.dump(d, open("missions_raw.json", "w"), ensure_ascii=False, indent=1)
if isinstance(d, dict):
    print("keys:", list(d.keys()))
    items = d.get("items") or d.get("missions") or d.get("data") or []
else:
    items = d
print("count:", len(items))
if items:
    print("item keys:", sorted(items[0].keys()))
