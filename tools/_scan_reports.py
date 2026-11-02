import json, os, urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
U = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
P = os.environ.get("TENSOR_AGENT_PASS", "")


def post(path, body, tok=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get(path, tok=None):
    req = urllib.request.Request(BASE + path)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


tok = post("/auth/login", {"username": U, "password": P})
token = tok.get("access_token") or tok.get("token")
open("/tmp/tok.txt", "w").write(token or "")
print("token ok:", bool(token))
d = get("/tensor/reports?limit=10", token)
rs = d.get("items") or d.get("reports") or d
for r in rs[:10]:
    md = r.get("markdown") or ""
    i = max(md.find("局限"), md.find("待办"))
    print("---", r.get("id"), "m", r.get("mission_id"), (r.get("title") or "")[:70])
    print("   ", md[i:i+260].replace("\n", " ") if i > 0 else "NONE")