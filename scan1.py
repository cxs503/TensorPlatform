import json, os, urllib.request, urllib.error

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")

def req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:
        return -1, {"err": str(e)}

st, d = req("POST", "/auth/login", {"username": USER, "password": PASS})
print("login", st)
tok = d.get("access_token", "")
print("token_len", len(tok))

st, d = req("GET", "/tensor/reports?limit=20", token=tok)
print("reports", st)
items = d if isinstance(d, list) else (d.get("items") or d.get("reports") or d.get("data") or [])
print("count", len(items))
for r in items[:20]:
    print(r.get("id"), "|", str(r.get("title", ""))[:90], "|", r.get("scenario_key", ""), "|", r.get("created_at", ""))
json.dump({"token": tok, "reports": items}, open("/tmp/scan1.json", "w"))
