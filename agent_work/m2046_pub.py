#!/usr/bin/env python3
"""m2046 通用平台客户端：登录 + GET/POST（凭据只从环境变量读，不落盘不回显）。"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PWD = os.environ.get("TENSOR_AGENT_PASS", "")


def login():
    d = json.dumps({"username": USER, "password": PWD}).encode()
    r = urllib.request.Request(BASE + "/auth/login", data=d,
                               headers={"Content-Type": "application/json"})
    j = json.load(urllib.request.urlopen(r, timeout=20))
    return j.get("access_token") or j.get("token") or j.get("data", {}).get("access_token")


def call(method, path, body=None, tok=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    hd = {"Content-Type": "application/json"}
    if tok:
        hd["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(url, data=data, headers=hd, method=method)
    try:
        return json.load(urllib.request.urlopen(r, timeout=60))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:500]}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "get":
        print(json.dumps(call("GET", sys.argv[2], tok=login()), ensure_ascii=False)[:int(sys.argv[3]) if len(sys.argv) > 3 else 4000])
    elif cmd == "post":
        body = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {}
        print(json.dumps(call("POST", sys.argv[2], body, tok=login()), ensure_ascii=False)[:4000])