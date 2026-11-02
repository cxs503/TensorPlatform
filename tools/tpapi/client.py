"""最小 Tensor 平台 API 客户端（登录/取 token/取报告/发报告），仅标准库。"""
import json, os, urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010").rstrip("/")


def _post(path, payload, token=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _get(path, token=None):
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def login():
    d = _post("/auth/login", {"username": os.environ["TENSOR_AGENT_USER"],
                              "password": os.environ["TENSOR_AGENT_PASS"]})
    d = d.get("data", d)
    return d.get("access_token") or d.get("token")


def reports(token, limit=20):
    d = _get(f"/tensor/reports?limit={limit}", token)
    d = d.get("data", d)
    if isinstance(d, dict):
        d = d.get("reports", d.get("items", []))
    return d
