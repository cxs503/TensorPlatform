import json, os, urllib.request

base = os.environ["TENSOR_API_BASE"]
req = urllib.request.Request(
    base + "/auth/login",
    data=json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                     "password": os.environ["TENSOR_AGENT_PASS"]}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
tok = json.load(urllib.request.urlopen(req))["access_token"]
open("/tmp/tok.txt", "w").write(tok)
print("TOKEN_LEN", len(tok))
