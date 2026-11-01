"""Print a report's markdown (limitations/todo sections) — read only."""
import json
import os
import sys
import urllib.request

base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
tok = open("/tmp/tok").read().strip()
rid = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
req = urllib.request.Request(base + "/tensor/reports/" + rid,
                             headers={"Authorization": "Bearer " + tok})
d = json.load(urllib.request.urlopen(req, timeout=30))
if isinstance(d, dict) and "item" in d:
    d = d["item"]
md = d.get("markdown") or ""
print("TITLE:", d.get("title"))
print("LEN:", len(md))
print(md[:n])