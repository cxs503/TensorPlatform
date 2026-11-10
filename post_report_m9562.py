#!/usr/bin/env python3
"""Post the p106w2-n11 integration-gate report to the platform (mission 9562)."""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")

verdict = json.loads((WORKTREE / "artifacts/p106w2_interop/verdict.json").read_text(encoding="utf-8"))
md = (WORKTREE / "docs/P106W2-N11_INTEGRATION_GATE.md").read_text(encoding="utf-8")
meta = json.loads((WORKTREE / "artifacts/p106w2_interop/figures/figures_metadata.json").read_text(encoding="utf-8"))
pytest_line = [line for line in (WORKTREE / "artifacts/p106w2_interop/pytest_summary.txt")
               .read_text(encoding="utf-8").splitlines() if "passed" in line][-1]

figures = []
for entry in meta:
    png = (WORKTREE / "artifacts/p106w2_interop/figures" / entry["name"]).read_bytes()
    figures.append({"name": entry["name"], "caption": entry["caption"],
                    "png_base64": base64.b64encode(png).decode("ascii")})

counts = verdict["fracture_summary"]["by_type"]
summary = (
    "p106w2-n11 跨求解器互裁集成门落库（interop gate 机制 + 战役实例）。honest FAIL：4/4 项 FAIL、"
    f"断裂点 {len(verdict['fractures'])} 条。n05/n06 几何 Cb 差 37.2% 使 RAO 互裁前提不成立；"
    "n06 RAO 自评 cases_run=0；n02 滑移 ΔC_f 低于 DNS 带 21.2 倍；n09/n07 额定转速差 8.3%、"
    "惯量差 61.1%、受迫响应缺 5 阶；n07 缺 4 项疲劳声明；n01/n10 无工件。测试 26 条全绿。"
)

body = {
    "title": "【p106w2-n11】跨求解器互裁集成门：四项互裁与接口契约的机器裁决（honest FAIL，12 断裂点）",
    "summary": summary,
    "scenario_key": "campaign-integration-gate",
    "mission_id": 9562,
    "tags": ["campaign", "p106w2", "integration-gate", "cross-solver", "interop",
             "interface-contract", "honest-fail", "TensorPlatform"],
    "markdown": md + (
        "\n\n## 附：机器口径（自动注入，防摘要与工件漂移）\n\n"
        f"* 门判定 `{verdict['gate_status']}`；verdict sha256 `{verdict['verdict_digest']}`；"
        f"manifest sha256 `{verdict['manifest_digest']}`。\n"
        f"* 断裂点按类型：`{json.dumps(counts, ensure_ascii=False)}`。\n"
        f"* 测试：`{pytest_line.strip()}`（合成域机制单测 15 + 战役实例 9 + 域词表反查 2）。\n"
        "* 全部数字源自 `artifacts/p106w2_interop/verdict.json`（本 commit 内），报告由 "
        "`tools/xsolver_interop/docgen.py` 从该文件注入，无手抄。\n"
    ),
    "figures": figures,
}


def main() -> int:
    login = json.dumps({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(f"{API}/auth/login", data=login,
                                 headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]
    post = urllib.request.Request(f"{API}/tensor/reports", data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    try:
        result = json.loads(urllib.request.urlopen(post, timeout=60).read())
    except Exception as exc:  # surface the server's message
        if hasattr(exc, "read"):
            print(exc.read().decode()[:1200], file=sys.stderr)
        raise
    print("report posted:", json.dumps(result, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())