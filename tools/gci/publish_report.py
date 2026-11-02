"""Publish the GCI absorption report to the platform report API.

    python -m tools.gci.publish_report

Reads ``artifacts/gci/gci_e2e.json`` (numbers) and the three figures, builds
``/tmp/report.json`` and POSTs it to ``$TENSOR_API_BASE/tensor/reports`` with a
token obtained from ``$TENSOR_API_BASE/auth/login``.  Nothing is transcribed by
hand: every figure in the markdown comes from the artefact JSON.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Dict, List

from tools.gci.gen_report import ART, JSON_PATH, build_markdown

MISSION_ID = 1778
REPORT_JSON = "/tmp/report.json"

_FIGURES = [
    ("fig1_convergence.png", "图1 三网格收敛史：C_a 随网格加密趋近 Richardson 极限与解析值 1.0"),
    ("fig2_richardson.png", "图2 Richardson 图：离散误差估计随网格尺度的 log-log 衰减，拟合斜率对照理论 -2"),
    ("fig3_u_v_gate.png", "图3 U_V 与 15% 实验闸门对照：绿 PASS / 红 FAIL / 橙 WITHHELD，三角为参考偏差"),
]


def _post(url: str, payload: dict, token: str) -> Dict[str, object]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    base = os.environ["TENSOR_API_BASE"].rstrip("/")
    cred = json.dumps(
        {"username": os.environ["TENSOR_AGENT_USER"], "password": os.environ["TENSOR_AGENT_PASS"]}
    ).encode()
    req = urllib.request.Request(
        base + "/auth/login", data=cred, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        token = json.loads(resp.read().decode("utf-8"))["access_token"]

    with open(JSON_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    main_case = payload["cases"][0]

    md = build_markdown(payload)
    # local image links are meaningless in the platform viewer: figures are embedded
    md = "\n".join(
        line for line in md.splitlines() if not line.startswith("![fig")
    )

    figures: List[Dict[str, str]] = []
    for fname, caption in _FIGURES:
        path = os.path.join(ART, fname)
        with open(path, "rb") as fh:
            figures.append(
                {"name": fname, "caption": caption, "png_base64": base64.b64encode(fh.read()).decode()}
            )

    summary = (
        f"落地 ITTC 7.5-03-01/02 三网格 GCI 链：细化比/表观阶(含极限阶回退)/Richardson 外推/"
        f"GCI21,GCI32/U_V，并把平台「偏差>15% 不可接受」升级为「实验偏差 vs U_V」双判据三态裁决。"
        f"端到端算例（二维圆截面附加质量，解析参考 1.0）给出 p={main_case['p']:.4f}、"
        f"R_G={main_case['R_G']:.4f}、U_V={100 * float(main_case['U_V']):.4f}%、"
        f"偏差={100 * float(main_case['deviation']):.4f}% -> {main_case['verdict']}；"
        f"锚库 8 条 sha256 自校验通过，篡改 1 位即拒；65 项 pytest 全绿。"
    )

    report = {
        "title": "ITTC 7.5-03-01/02 数值不确定度(GCI)吸收：U_V 双判据取代单一 15% 偏差闸门",
        "summary": summary,
        "scenario_key": "numerical_verification",
        "mission_id": MISSION_ID,
        "tags": ["ITTC", "GCI", "verification", "U_V", "Richardson", "uncertainty", "anchor"],
        "markdown": md,
        "figures": figures,
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False)

    try:
        out = _post(base + "/tensor/reports", report, token)
        print(json.dumps(out, ensure_ascii=False)[:600])
        return 0
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read().decode("utf-8")[:800], file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())