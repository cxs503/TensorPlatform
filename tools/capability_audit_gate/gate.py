"""回归防护：把上一次审计冻结成基线，任何**等级下降**都要显式 ack，否则红。

防的是这类事故：为了让 CI 变绿，把某能力的证据悄悄删掉/注释掉 — 等级下降 →
CI 失败；并且降级必须留下 ack 文件（id + 理由 + 责任人），写进报告，不可静默。

与既有 ``capability_reconcile --fail-on-overclaim`` 的边界：那个防"自述高于证据"，
本模块防"**相对基线**的能力退化"（含 REAL→WITHHELD 这类无正面反证但能力退步的情况），
两者互补，可同跑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import taxonomy

BASELINE_SCHEMA = "capability_audit_gate.baseline.v1"


class BaselineError(ValueError):
    """基线文件非法（CLI 退出码 4）。"""


def baseline_from_report(report: dict) -> dict:
    return {
        "schema_version": BASELINE_SCHEMA,
        "ruleset_version": report["ruleset_version"],
        "spec_sha256": report["inputs"]["spec_sha256"],
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "levels": {v["capability"]: v["level"] for v in report["capabilities"]},
    }


def load_baseline(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise BaselineError(f"baseline not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline is not valid JSON: {exc}") from exc
    if data.get("schema_version") != BASELINE_SCHEMA or not isinstance(data.get("levels"), dict):
        raise BaselineError("baseline schema mismatch (expected "
                            f"{BASELINE_SCHEMA} with 'levels' map)")
    return data


def load_ack(path: str | Path | None) -> dict:
    """ack 文件：``{"ruleset": "reason", "levels": {"cap.x": "reason", "*": "reason"}}``。"""
    if not path:
        return {"ruleset": None, "levels": {}}
    p = Path(path)
    if not p.is_file():
        raise BaselineError(f"ack file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BaselineError("ack file must be a JSON object")
    return {"ruleset": data.get("ruleset"), "levels": dict(data.get("levels") or {})}


def compare(report: dict, baseline: dict, ack: dict | None = None) -> dict:
    ack = ack or {"ruleset": None, "levels": {}}
    current = {v["capability"]: v["level"] for v in report["capabilities"]}
    stable, upgrades, violations, acked = [], [], [], []
    for cid, new in current.items():
        old = baseline["levels"].get(cid)
        if old is None:
            stable.append({"id": cid, "level": new, "note": "NEW_CAPABILITY"})
            continue
        change = taxonomy.classify_change(old, new)
        row = {"id": cid, "baseline": old, "current": new, "change": change}
        if change == "STABLE":
            stable.append({"id": cid, "level": new})
        elif change == "UPGRADE":
            upgrades.append(row)
        else:
            reason = ack["levels"].get(cid) or ack["levels"].get("*")
            if reason:
                row["ack_reason"] = reason
                acked.append(row)
            else:
                violations.append(row)
    disappeared = [{"id": cid, "baseline": lv, "current": "ABSENT",
                    "change": "CAPABILITY_DROPPED"}
                   for cid, lv in baseline["levels"].items() if cid not in current]
    violations.extend(disappeared)

    ruleset_changed = report["ruleset_version"] != baseline.get("ruleset_version")
    ruleset_ok = (not ruleset_changed) or bool(ack["ruleset"])
    hard = [v for v in violations if v["change"] in {"REFUTATION", "CAPABILITY_DROPPED"}]
    result = {
        "baseline_ruleset": baseline.get("ruleset_version"),
        "current_ruleset": report["ruleset_version"],
        "ruleset_changed": ruleset_changed,
        "ruleset_acked": bool(ack["ruleset"]),
        "counts": {"stable": len(stable), "upgrades": len(upgrades),
                   "violations": len(violations), "acked": len(acked),
                   "refutations": len([v for v in violations if v["change"] == "REFUTATION"]),
                   "dropped": len(disappeared)},
        "violations": violations,
        "refutations": hard,
        "upgrades": upgrades,
        "acked": acked,
        "fail": bool(violations) or (ruleset_changed and not ruleset_ok),
        "exit_code": 1 if (violations or (ruleset_changed and not ruleset_ok)) else 0,
    }
    return result