import json, sys, urllib.request

BASE = "http://127.0.0.1:8010/api/v1"
TOKEN = open("/tmp/tok.txt").read().strip()


def get(path, raw=False):
    req = urllib.request.Request(BASE + "/" + path.lstrip("/"))
    req.add_header("Authorization", "Bearer " + TOKEN)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    return body if raw else json.loads(body)


def post(path, payload):
    req = urllib.request.Request(BASE + "/" + path.lstrip("/"),
                                 data=json.dumps(payload).encode(),
                                 method="POST")
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "get":
        out = get(sys.argv[2], raw=("-r" in sys.argv))
        print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=1)[:8000])