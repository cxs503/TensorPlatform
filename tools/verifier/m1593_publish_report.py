#!/usr/bin/env python3
"""发布 m1593 进展报告到 Tensor 平台（凭据取自环境变量，不含秘密）。"""
import base64
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def call(path, body=None, token=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.loads(f.read().decode())


tok = None
for p in ("/auth/login", "/tensor/auth/login"):
    try:
        d = call(p, {"username": USER, "password": PASS})
        tok = d.get("access_token") or d.get("token") or (d.get("data") or {}).get("access_token")
        if tok:
            break
    except Exception as e:  # noqa: BLE001
        print("login", p, "fail", str(e)[:80])
assert tok, "no token"

with open(os.path.join(ROOT, "docs/verifier/m1593_struct_vib_audit.md")) as f:
    md = f.read()
with open(os.path.join(ROOT, "runs/m1593/figures.json")) as f:
    figs_meta = json.load(f)
figs = []
for fg in figs_meta:
    with open(fg["path"], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    figs.append({"name": fg["name"], "caption": fg["caption"], "png_base64": b64})
print("figures", [(f["name"], len(f["png_base64"])) for f in figs])

payload = {
    "title": "结构/振动噪声域验证欠账清算 v1（m1593）：19 条闭式判据，6 PASS / 1 WARN / 12 FAIL",
    "summary": (
        "首次审计平台结构/振动两域（22 条路径中 5 个可算端点），以 Timoshenko 板格屈曲、"
        "Johnson-Ostenfeld、DNV-RP-C203 D 双线性 S-N、Miner、箱形剖面+IACS UR S11 为闭式基准："
        "19 条判据 6 PASS/1 WARN/12 FAIL。/structure/fatigue 与 /vibration/predict 为常数桩(MOCK)；"
        "/structure/analyze 量纲混用放大 1e6 且板厚不入模型；S-N 膝点不连续 +62.3%；"
        "plate-buckling 基础 k=4/7/5.59 精确，但 bending/free 映射错误、缺 Ostenfeld 折减(高估 60%)。"
    ),
    "scenario_key": "struct_vib_audit",
    "mission_id": 1593,
    "tags": ["verify-debt", "structure", "vibration", "fatigue", "plate-buckling", "m1593"],
    "markdown": md,
    "figures": figs,
}
try:
    d = call("/tensor/reports", payload, token=tok)
    print("report resp:", json.dumps(d, ensure_ascii=False)[:400])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:1200])