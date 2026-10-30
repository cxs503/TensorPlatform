import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")

def req(method, path, body=None, token=None, raw=False):
    url = BASE + "/" + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode()
            return resp.status, (txt if raw else json.loads(txt))
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt

def login():
    st, d = req("POST", "auth/login", {"username": os.environ["TENSOR_AGENT_USER"], "password": os.environ["TENSOR_AGENT_PASS"]})
    if st not in (200, 201):
        raise SystemExit(f"login failed {st}: {d}")
    tok = d.get("access_token") or d.get("token")
    if not tok:
        raise SystemExit(f"no token in login resp: {d}")
    return tok

if __name__ == "__main__":
    tok = login()
    open("/srv/tensor/agent/work/m43/agent_work/m43_shift/token.txt", "w").write(tok)
    print("login ok, token len", len(tok))
