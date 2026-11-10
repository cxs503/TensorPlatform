#!/usr/bin/env python3
"""发布 IN68W2-N10（验收门证据契约成熟化）进展报告到 tensor 平台。"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

login = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login,
                             headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
print(f"login ok: {token[:16]}...")

md = (ROOT / "docs" / "gate_contract_hardening.md").read_text(encoding="utf-8")
fig_dir = ROOT / "artifacts" / "gate_contract" / "figures"
meta = json.loads((fig_dir / "figures_metadata.json").read_text(encoding="utf-8"))
figs = []
for item in meta["figures"]:
    blob = (fig_dir / item["file"]).read_bytes()
    figs.append({"name": item["file"].replace(".png", ""),
                 "caption": item["caption"],
                 "png_base64": base64.b64encode(blob).decode()})

summary = json.loads((ROOT / "artifacts" / "gate_contract" / "summary.json").read_text(encoding="utf-8"))
nc = json.loads((ROOT / "artifacts" / "gate_contract" / "negcontrol_summary.json").read_text(encoding="utf-8"))
cross = json.loads((ROOT / "artifacts" / "gate_contract" / "crossarch.json").read_text(encoding="utf-8"))

report = {
    "title": ("验收门证据契约成熟化：gate.json 强制化 + 缺失/不符即 WITHHELD + "
              "注入式负控把假通过率压到 0（plan 258 九节点回填）"),
    "summary": (
        "gate.json 契约从 13 条判据扩到 19 条（新增 6 条 fail-closed：权威登记表在位/指纹锚定、"
        "判据集合完整、判据不重复、工件不越出门口、判定对象唯一），dev_acceptance 前门强制复算；"
        f"九类伪证据 {nc['n_cases']} 例注入式负控假通过 {nc['false_pass']} 例（率 {nc['false_pass_rate']:.1%}），"
        f"阳性对照 {nc['positive_pass']} 例 PASS；plan 258 九节点回填覆盖率 "
        f"{summary['C1_backfill']['coverage']:.0%}（{summary['C1_backfill']['grades']}）；"
        f"跨宿主一致性 A={cross['A_heterogeneous_fingerprint']['consistency']:.0%}"
        f"（4 套异构指纹 × 9 门）、libc 往返坏 {cross['C_libc_roundtrip']['n_bad']}/{cross['C_libc_roundtrip']['n_values']}；"
        "复现：python3 tools/gate_contract/plan258_audit.py。"
        "诚实局限：x86 物理机/CI 工件本轮未接入，跨架构为构造性外推（已装 x86 CI 比对作业待落件）。"
    ),
    "scenario_key": "in68w2-n10",
    "mission_id": 9737,
    "tags": ["gate-contract", "evidence", "withheld", "negative-control", "fail-closed",
             "cross-arch", "plan258", "TensorPlatform", "audit", "dev-acceptance"],
    "markdown": md,
    "figures": figs,
}

body = json.dumps(report).encode()
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp = urllib.request.urlopen(req2, timeout=60)
    print("posted:", resp.read().decode()[:400])
except Exception as exc:  # noqa: BLE001
    print(f"post failed: {exc}", file=sys.stderr)
    if hasattr(exc, "read"):
        print(exc.read().decode()[:800], file=sys.stderr)
    sys.exit(1)
