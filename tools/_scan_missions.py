import json, os, urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
tok = open("/tmp/tok.txt").read().strip()


def get(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


d = get("/tensor/missions?limit=40")
ms = d.get("items") or d.get("missions") or d
for m in ms[:40]:
    print(m.get("id"), m.get("status"), (m.get("goal") or "")[:90].replace("\n", " "))