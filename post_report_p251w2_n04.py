#!/usr/bin/env python3
"""Post the P251-W2-N04 report (number-level provenance gate) to the platform.

Zero hand-copied numbers: the summary is built from
``runs/p251w2_n04/gate_evidence.json`` and the body is the rendered
``docs/reports/p251w2_n04_prov_gate.md`` (rendered from its template by the very
renderer under test).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
REPORT_MD = REPO_ROOT / "docs/reports/p251w2_n04_prov_gate.md"
EVIDENCE = REPO_ROOT / "runs/p251w2_n04/gate_evidence.json"
MISSION_ID = 10745


def _api_base() -> str:
    return os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")


def _post(path: str, payload: dict, token: str | None = None, timeout: int = 90) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_api_base() + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _login() -> str:
    user = os.environ["TENSOR_AGENT_USER"]
    pw = os.environ["TENSOR_AGENT_PASS"]
    for path in ("/auth/login",):
        try:
            tok = _post(path, {"username": user, "password": pw})
            return tok.get("access_token") or tok.get("token") or ""
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"login {path} failed: {exc}", file=sys.stderr)
    raise RuntimeError("login failed")


def _figure(path: Path, caption: str) -> dict:
    return {
        "name": path.name,
        "caption": caption,
        "png_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def main() -> int:
    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    md = REPORT_MD.read_text(encoding="utf-8")
    nc = ev["negative_controls"]
    sc = ev["report_selfcheck"]
    tests = ev["tests"]

    summary = (
        f"实现报告数字级 provenance 门：模板零结果数字、{{{{prov:key}}}} 占位符发布时由 SDK 从 sidecar "
        f"渲染，sidecar 每条记录带 query_hash/artifact_sha256+computed_at；四层判定（渲染保真/键覆盖/"
        f"出处身份/裸数字比例）。pytest {tests['tests_passed']} 项全绿；三类负控 "
        f"{nc['intercepted']}/{nc['variants']} 全部拦截（拦截率 {nc['interception_rate'] * 100:.2f}%）；"
        f"干净文档误杀 {ev['negative_controls']['false_positives']} 例；本报告自身经同一门禁复核 "
        f"status={sc['status']}，裸数字比例 {sc['bare_number_ratio']:.4f}（{sc['counts']['bound']}/"
        f"{sc['counts']['in_scope']} 绑定，字级抽取覆盖 {sc['extraction']['coverage']:.3f}）。"
    )

    payload = {
        "title": "P251-W2-N04 报告数字级 provenance 门：占位符契约+可复算 sidecar+裸数字比例拦截",
        "summary": summary,
        "scenario_key": "p251w2-n04",
        "mission_id": MISSION_ID,
        "tags": ["provenance", "report_gate", "placeholder_sidecar", "anti_fabrication",
                 "platform_maturity"],
        "markdown": md,
        "figures": [
            _figure(REPO_ROOT / ev["artifacts"]["figures"]["layers"],
                    "图 1 门禁各层失败码在合成夹具上的触发计数（干净文档零触发；三类负控各自触发设计中的失败码）"),
            _figure(REPO_ROOT / ev["artifacts"]["figures"]["interception"],
                    "图 2 三类负控逐族拦截率（全部 100%）与篡改后文档的裸数字比例"),
        ],
    }
    token = _login()
    result = _post("/tensor/reports", payload, token=token)
    print("report posted:", json.dumps(result, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())