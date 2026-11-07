#!/usr/bin/env python3
"""报告 gate 的台账消费者（消费者 #4）。

报告 gate（``backend/api/tensor_reports.py::_check_report_quality``）此前与能力台账
完全脱钩：一份报告的正文可以写「``propeller.open_water`` 证据等级 REAL」，而台账
对账结果是 MOCK，gate 照放行——平台对外同时存在两个证据等级，且没有红灯。

本模块提供三件事（纯 stdlib，可离线跑、可被 CI 直接调用）：

1. :func:`ledger_ref` —— 契约访问器（四消费者之一）；
2. :func:`ledger_gate` —— 扫报告 markdown 里「能力 id + 等级」的断言，
   若断言强于台账 ``reconciled_level``（无对账则用 ``claimed_level``）→ 违规；
3. :func:`gate_summary` —— 给报告 gate 返回体的机读摘要（含台账 sha256）。

纪律：台账降级 ≠ 放行。降级时 ``ok=False`` 且 ``degraded=True``，由调用方决定
是软告警还是阻断（生产 gate 当前软告警，不改变既有发布路径的可用性）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402

#: 台账里的等级序（与 artifacts/capability_ledger/capability_ledger.json 的 level_rank 一致）
LEVEL_RANK = {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4}

GRADE_CLAIM = re.compile(
    r"`?(?P<cap>[A-Za-z][A-Za-z0-9_.\-]{2,40})`?[^\n]{0,40}?"
    r"(?:证据等级|evidence\s*grade|claimed|等级)\s*[:：=为]?\s*[*_`]*"
    r"(?P<grade>REAL|MOCK|STUB|WITHHELD|NOT_SUPPORTED)\b",
    re.I,
)


def ledger_ref() -> dict:
    """消费者契约访问器：台账身份（path/sha256/status）。"""
    return LP.resolve_ledger()


def _ledger_levels(ref: dict) -> dict:
    """从台账读出每个能力 id 的 (reconciled_level, claimed_level)。"""
    out: dict[str, dict] = {}
    path = ref.get("path")
    if not path:
        return out
    try:
        import json
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return out
    caps = payload.get("capabilities")
    if isinstance(caps, list):
        for c in caps:
            if isinstance(c, dict) and c.get("id"):
                out[str(c["id"])] = {"reconciled_level": c.get("reconciled_level"),
                                     "claimed_level": c.get("claimed_level")}
    return out


def ledger_gate(markdown: str, ref: dict | None = None) -> dict:
    """把报告正文里的能力等级断言与台账对账（强于台账 → 违规）。"""
    ref = ref or ledger_ref()
    levels = _ledger_levels(ref)
    degraded = ref.get("status") != LP.STATUS_OK
    violations: list[dict] = []
    unregistered: list[str] = []
    for m in GRADE_CLAIM.finditer(markdown or ""):
        cap = m.group("cap")
        grade = m.group("grade").upper()
        if cap not in levels:
            # 可能是公式/单位误捕（如 "CT REAL"），只在 id 形似台账命名空间时登记
            if "." in cap:
                unregistered.append(cap)
            continue
        basis = levels[cap].get("reconciled_level") or levels[cap].get("claimed_level")
        if not basis:
            continue
        allowed = LEVEL_RANK.get(str(basis).upper(), -1)
        if LEVEL_RANK.get(grade, -1) > allowed:
            violations.append({"capability": cap, "asserted": grade, "ledger_level": basis,
                               "rule": "REPORT_OVERCLAIMS_LEDGER"})
    ok = not violations and not degraded
    return {
        "ok": ok,
        "degraded": degraded,
        "violations": violations,
        "unregistered_ids": sorted(set(unregistered)),
        "ledger": LP.ledger_view(ref),
    }


def gate_summary(markdown: str) -> dict:
    """报告 gate 返回体里可内嵌的摘要（机读，含台账 sha256 前缀）。"""
    res = ledger_gate(markdown)
    led = res["ledger"]
    return {
        "ok": res["ok"],
        "degraded": res["degraded"],
        "violations": res["violations"][:20],
        "ledger_path": led.get("path"),
        "ledger_sha256": led.get("sha256"),
        "ledger_status": led.get("status"),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="报告 gate × 能力台账 一致性检查")
    ap.add_argument("markdown", help="报告 markdown 路径")
    args = ap.parse_args(argv)
    text = Path(args.markdown).read_text(encoding="utf-8")
    res = ledger_gate(text)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())