#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布 mission 1756 的论文级进展报告（markdown 来自工件，图内嵌 base64）。"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010").rstrip("/")
U, P = os.environ["TENSOR_AGENT_USER"], os.environ["TENSOR_AGENT_PASS"]
MISSION = 1756

DOC = os.path.join(ROOT, "docs", "verif", "weather_criterion_verification.md")
ART = os.path.join(HERE, "artifacts", "weather_probe.json")


def post(path, body, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    return json.load(urllib.request.urlopen(req, timeout=60))


tok = None
for ep in ("/auth/login", "/tensor/auth/login"):
    try:
        r = post(ep, {"username": U, "password": P})
        tok = r.get("access_token") or r.get("token") or (r.get("data") or {}).get("access_token")
        if tok:
            break
    except Exception:  # noqa: BLE001
        pass
if not tok:
    sys.exit("login failed")

md = open(DOC, encoding="utf-8").read()
art = json.load(open(ART, encoding="utf-8"))

figures = []
caps = {
    "fig_gz_areas.png": ("算例 1 的 GZ 曲线、lw1/lw2 力臂与 a/b 区域定义：绿色为规范 b 区（θC→θ2 间 GZ−lw2），"
                         "灰底为平台 area_b 的实际积分域（0→末采样角），两者几何域明显不同。"),
    "fig_ratio.png": ("左：b/a 随横摇回摆角 θ1；右：b/a 随受风面积。参考判据单调可翻转，"
                      "平台比值系统性偏于宽松侧——边界船因此被误判为满足。"),
}
for name, cap in caps.items():
    p = os.path.join(HERE, "artifacts", name)
    if os.path.exists(p):
        figures.append({"name": name, "caption": cap,
                        "png_base64": base64.b64encode(open(p, "rb").read()).decode()})

r2 = art["results"][1]
rep2, pf2 = r2["reference_representative_theta1_15"], r2["platform_formula_replica"]

summary = (
    "把平台自称「IMO天气衡准」的 /api/v1/dynamic-stability/weather-criterion 对 IS Code 2008 §2.3 逐条复核："
    "缺 1.5 阵风臂、缺横摇回摆角 θ1、θ2 硬编码 50°、a/b 积分域错配；"
    f"边界船 b/a 规范 {rep2['ratio']:.3f} vs 平台 {pf2['ratio']:.3f}（{100*(pf2['ratio']-rep2['ratio'])/rep2['ratio']:+.0f}%）"
    "导致判决从不满足翻成满足（false PASS）；另发现 schema 合法键名触发 500。10 条 pytest 全绿，能力等级判 WITHHELD。")

md_report = md + f"""

---

### 附：本单工件指针

| 工件 | 路径 |
|---|---|
| 独立参考实现 | `tools/verif/weather/imo_weather.py` |
| 探针（API + 双数值路径 + 出图） | `tools/verif/weather/probe_weather.py` |
| 数字工件 | `tools/verif/weather/artifacts/weather_probe.json` |
| 图 | `tools/verif/weather/artifacts/{'`, `'.join(f[0] for f in [(k, 0) for k in caps])}` |
| 文档（本报告正文） | `docs/verif/weather_criterion_verification.md` |
| 回归测试 | `tests/test_weather_criterion_reference.py`（10 passed） |

平台实测样本（程序化写入）：b/a_plat = `{pf2['ratio']:.3f}`，合规 = `{pf2['compliant']}`；
规范样本：b/a_ref = `{rep2['ratio']:.3f}`（PCHIP 路径 `{r2['reference_representative_pchip']['ratio']:.3f}`），合规 = `{rep2['compliant']}`。
"""

payload = {
    "title": ("IMO 天气衡准复核（IS Code 2008 §2.3）：平台缺 1.5 阵风臂/θ1、θ2 硬编码 50°，"
              "边界船 b/a 误判导致 false PASS + 合法键名 500"),
    "summary": summary,
    "scenario_key": "ship/stability/weather-criterion",
    "mission_id": MISSION,
    "tags": ["verification-debt", "stability", "IMO", "weather-criterion",
             "IS-Code-2008", "false-pass", "pytest"],
    "markdown": md_report,
    "figures": figures,
}
res = post("/tensor/reports", payload, tok)
print(json.dumps(res, ensure_ascii=False)[:400])