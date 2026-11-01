#!/usr/bin/env python3
"""Publish the m1643 verification report (figures embedded as base64)."""
import base64
import json
import os
import urllib.request
from pathlib import Path

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
U = os.environ["TENSOR_AGENT_USER"]
P = os.environ["TENSOR_AGENT_PASS"]
REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "cii_m1643"
DOC = REPO / "docs" / "verification" / "cii_reference_lines_m1643.md"
VER = json.loads((ART / "cii_reference_verification.json").read_text(encoding="utf-8"))


def call(path, body=None, tok=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + tok} if tok else {})})
    return json.load(urllib.request.urlopen(req, timeout=180))


tok = ""
for p in ("/auth/login", "/auth/token"):
    try:
        r = call(p, {"username": U, "password": P})
        tok = r.get("access_token") or r.get("token") or ""
        if tok:
            break
    except Exception as exc:  # noqa: BLE001
        print("login failed at", p, repr(exc)[:120])
if not tok:
    raise SystemExit("no token")

markdown = DOC.read_text(encoding="utf-8")

figs = [
    ("fig1_cii_reference_lines.png",
     "G1 参考线：公布表（实线）vs 平台取值（空心圆），红色虚线为被否决的旧杂货船指数 c=-1.430"),
    ("fig2_relative_error.png",
     "各类别指数相对误差（修复前/后，symlog 纵轴）：仅 general_cargo 修复前偏离"),
    ("fig3_rating_general_cargo.png",
     "20 000 DWT 杂货船航次在 A-E 评级带上的落点：修复前比值 407.7（超出图轴，E），修复后 0.735（A）"),
]
figures = [{"name": n, "caption": c,
            "png_base64": base64.b64encode((ART / n).read_bytes()).decode()}
           for n, c in figs]

v = VER["verdict"]
body = {
    "title": "CII 参考线（MEPC.353(78) G1）独立复核与缺陷修复（m1643）",
    "summary": (
        "平台绿色航行 CII 链的 G1 参考线系数此前只有自洽检验、无外部锚；本单以 MEPC.353(78) G1 "
        "Annex Table 1 逐行复核 8 个类别 + 24 个容量锚点（最大相对误差 0.0e+00%），并加入三项免文档"
        "不变量。发现并修复 general_cargo 指数 -1.430 → 0.792 的实质缺陷：修复前 20 000 DWT 参考线偏低 "
        "555 倍（相对误差 99.82%），使任何杂货船必然评 E 级（attained/required = 407.7）；修复后同一航次 "
        "比值 0.735、评级 A。未实现的 G1 类别改为显式 NOT_SUPPORTED，不猜系数。"),
    "scenario_key": "cii_reference_lines_verification",
    "mission_id": 1643,
    "tags": ["CII", "MEPC.353(78)", "绿色航行", "验证欠账", "挖掘缺陷", "TensorPlatform"],
    "markdown": markdown,
    "figures": figures,
}
resp = call("/tensor/reports", body, tok)
print("published:", json.dumps(resp, ensure_ascii=False)[:400])
print("verdict:", json.dumps(v, ensure_ascii=False))
