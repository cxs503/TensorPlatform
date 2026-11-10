"""Post the P269W2-N03 report to the platform report API (six-element, with figures).

The markdown and the figure bytes are read from the generated archive artefacts, so
nothing is re-typed here::

    python3 -m tools.benchmark_gen.post_report --mission-id 10827 \\
        --archive artifacts/benchmark_gen

Credentials come from ``TENSOR_AGENT_USER`` / ``TENSOR_AGENT_PASS`` and the base URL
from ``TENSOR_API_BASE`` (never from this file).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


def _request(url: str, payload: Optional[Dict[str, Any]] = None,
             token: Optional[str] = None, timeout: int = 120) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:  # pragma: no cover - non-JSON response
        return {"raw": body}


def login(base: str) -> str:
    user = os.environ.get("TENSOR_AGENT_USER")
    password = os.environ.get("TENSOR_AGENT_PASS")
    if not user or not password:
        raise RuntimeError("TENSOR_AGENT_USER / TENSOR_AGENT_PASS are not set")
    last_error: Optional[Exception] = None
    for path in ("/tensor/auth/login", "/auth/login", "/tensor/login"):
        try:
            result = _request(base + path, {"username": user, "password": password})
        except urllib.error.HTTPError as exc:  # pragma: no cover - path probing
            last_error = exc
            continue
        for key in ("access_token", "token", "accessToken"):
            if isinstance(result, dict) and result.get(key):
                return str(result[key])
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            for key in ("access_token", "token"):
                if result["data"].get(key):
                    return str(result["data"][key])
    raise RuntimeError(f"login failed: {last_error}")


def build_payload(archive: Path, mission_id: int) -> Dict[str, Any]:
    report = (archive / "report.md").read_text(encoding="utf-8")
    metrics = json.loads((archive / "metrics.json").read_text(encoding="utf-8"))
    acceptance = metrics["acceptance"]
    figures = []
    for figure in metrics["figures"]:
        blob = base64.b64encode((archive / "figures" / figure["name"]).read_bytes()).decode()
        figures.append({"name": figure["name"], "caption": figure["caption"],
                        "png_base64": blob})
    summary = (
        f"可追溯基准数据集生成管线工具包落地 tools/benchmark_gen："
        f"{acceptance['A1_bodies']} 个配置注入合成船型 × {acceptance['A1_conditions']} 个工况 = "
        f"{acceptance['A1_datasets']} 个数据集；数值网格积分 vs Beta 函数闭式解最大相对偏差 "
        f"{acceptance['A3_max_relative_deviation']:.2e}（门槛 {acceptance['A3_tolerance']:g}），"
        f"湿表面积 vs 高阶求积 {acceptance['A3_surface_max_deviation']:.2e}，"
        f"种子控制 Monte-Carlo 体积交叉校验 ≤{acceptance['mc_max_sigmas']:.2f}σ；"
        f"dataset_id 唯一且可复算={acceptance['A2_unique_dataset_ids']}，"
        f"归档校验通过={acceptance['A2_archive_verified']}；pytest {acceptance['A5_pytest_cases']} 例。"
    )
    return {
        "title": ("P269W2-N03 基准数据集生成管线：配置注入船型 × 工况 × sha256 溯源"
                  f"（解析解锚定 {acceptance['A3_max_relative_deviation']:.1e}）"),
        "summary": summary,
        "scenario_key": "benchmark_dataset_generation",
        "mission_id": int(mission_id),
        "tags": ["benchmark-dataset", "provenance", "sha256", "analytic-verification",
                 "P269W2-N03"],
        "markdown": report,
        "figures": figures,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Post the benchmark-generator report")
    parser.add_argument("--archive", default="artifacts/benchmark_gen")
    parser.add_argument("--mission-id", type=int, default=10827)
    parser.add_argument("--dump", default=None, help="write the payload JSON here as well")
    args = parser.parse_args(argv)

    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    payload = build_payload(Path(args.archive), args.mission_id)
    if args.dump:
        Path(args.dump).write_text(json.dumps(payload), encoding="utf-8")
    token = login(base)
    result = _request(base + "/tensor/reports", payload, token=token)
    print(json.dumps({k: v for k, v in result.items() if k != "markdown"}, ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())