"""Publish the m8299 waLBerla OSS-inventory report to TensorPlatform.

Payload (markdown + base64 figure) is produced by docs/oss_inventory/build_report.py
into /tmp/report_m8299.json; this script only authenticates and POSTs it.
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PWD = os.environ["TENSOR_AGENT_PASS"]
PAYLOAD = "/tmp/report_m8299.json"


def post(path, payload, token=None, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    tok = post("/auth/login", {"username": USER, "password": PWD})
    token = (tok.get("access_token") or tok.get("token")
             or (tok.get("data") or {}).get("access_token"))
    if not token:
        raise SystemExit(f"login failed keys={list(tok)}")

    payload = json.loads(open(PAYLOAD).read())
    print(f"posting: title={payload['title'][:40]}... "
          f"md={len(payload['markdown'])} chars, figures={len(payload['figures'])}")
    try:
        res = post("/tensor/reports", payload, token)
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:2000])
        raise
    print(json.dumps({k: res[k] for k in res if k in
                      ("id", "report_id", "status", "qa_status", "message", "title")}, ensure_ascii=False))
    return res


if __name__ == "__main__":
    main()