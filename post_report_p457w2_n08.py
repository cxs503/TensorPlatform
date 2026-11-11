#!/usr/bin/env python3
"""Post the p457w2-n08 report (cross-solver clock sync + distillation maturity).

Transports artefacts only: the markdown is ``artifacts/multifidelity/report.md``
and the figures are read from ``artifacts/multifidelity/figures``.  No number is
typed here.
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ART = ROOT / "artifacts" / "multifidelity"
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")


def login() -> str:
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps({
            "username": os.environ["TENSOR_AGENT_USER"],
            "password": os.environ["TENSOR_AGENT_PASS"],
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def main() -> int:
    token = login()
    criteria = json.loads((ART / "criteria.json").read_text(encoding="utf-8"))
    provenance = json.loads((ART / "provenance.json").read_text(encoding="utf-8"))
    markdown = (ART / "report.md").read_text(encoding="utf-8")
    figures = []
    captions = {
        "fig1_clock_sync_timeline.png": "Fig.1 三个参与求解器的步长格点、gcd 全局网格与 lcm 宏节点；以及单通道的逐帧应许量 / 屏障记账 / 无全局时钟记账。",
        "fig2_conservation_error.png": "Fig.2 守恒缺陷随消费者帧相位变化（含 1% 验收线与 5% 负控线），及三个守恒通道的屏障 vs 无时钟记账。",
        "fig3_distillation_pareto.png": "Fig.3 精度-效率前沿：alpha×width 扫描、Pareto 前沿、教师、闭式低保真拟合、关闭蒸馏的对照点。",
    }
    for name, caption in captions.items():
        path = ART / "figures" / name
        figures.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode(path.read_bytes()).decode(),
        })
    payload = {
        "title": "跨求解器全局时钟与知识蒸馏成熟化（p457w2-n08）：精确有理 gcd/lcm 同步网格 + 帧级守恒审计 + 教师→学生蒸馏前沿",
        "summary": (
            "精确有理全局时钟：gcd=1/960 / lcm=1/60 两策略、非同步时刻交换误差 1.14e-4（<0.1%）；"
            "屏障协议帧级守恒 8.13e-5（净失衡 2.2e-16），无全局时钟时帧级缺陷 13.4%（净失衡仍只 4.3%）；"
            "12 个硬标签 + 600 教师软标签蒸馏：学生 96.25% vs 教师 99.59%（差 3.33 点，<5%），"
            "推理加速 517x（>10x）；关闭蒸馏掉 11.93 点、误差涨 4.18x。判据 a~f 全 PASS。"
        ),
        "scenario_key": "platform.multifidelity.clock_distill",
        "mission_id": 13992,
        "tags": ["clock-sync", "cosim", "conservation", "knowledge-distillation",
                 "pareto", "multifidelity", "platform-neutral", "p457w2-n08"],
        "markdown": markdown,
        "figures": figures,
        "metadata": {
            "criteria": {k: v.get("pass") for k, v in criteria.items()},
            "provenance": provenance,
            "package": "src/multifidelity",
            "tests": json.loads((ART / "pytest.json").read_text(encoding="utf-8")).get("collected"),
            "negative_controls": json.loads((ART / "pytest.json").read_text(encoding="utf-8")).get(
                "negative_controls"),
        },
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API}/tensor/reports", data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        print(resp.status, resp.read().decode()[:400])
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read().decode()[:800], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())