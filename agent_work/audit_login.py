import json, os, urllib.request

base = os.environ["TENSOR_API_BASE"]
user = os.environ["TENSOR_AGENT_USER"]
pw = os.environ["TENSOR_AGENT_PASS"]

def post(path, body=None, tok=None):
    req = urllib.request.Request(base + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    data = json.dumps(body).encode() if body is not None else b"{}"
    with urllib.request.urlopen(req, data=data, timeout=30) as r:
        return json.load(r)

def get(path, tok=None):
    req = urllib.request.Request(base + path, method="GET")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

tok = post("/auth/login", {"username": user, "password": pw}).get("access_token")
open("/tmp/tok.txt", "w").write(tok)
print("login ok, token len", len(tok))
