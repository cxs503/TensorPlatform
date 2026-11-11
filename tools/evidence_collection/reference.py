"""参考值误差复算（reference collector）。

纪律：**零手抄**。工件里若同时给了"实算值 / 参考值"，本模块一律用自己的口径
``|computed - reference| / max(|reference|, eps) * 100`` 复算误差，再与工件中
自称的误差列比对；两者不一致（超 ``declaration_tolerance_pct``）→ 判据
``declaration_mismatch`` 置位，该证据不可用于顶 REAL。

支持的 ``value_path`` 语法：点号路径 + ``*`` 通配（展开为列表后再聚合），
例如 ``results.*.error_pct``、``cases.*.max_abs_rel_err_pct_inside.canon``。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .runtime import FAIL, PASS

REFERENCE_PASS = "PASS"


def _flatten(payload: Any, dotted: str) -> list[Any]:
    parts = [p for p in dotted.split(".") if p]
    cur: list[Any] = [payload]
    for part in parts:
        nxt: list[Any] = []
        for item in cur:
            if part == "*":
                if isinstance(item, list):
                    nxt.extend(item)
                elif isinstance(item, dict):
                    nxt.extend(item.values())
            elif isinstance(item, dict) and part in item:
                nxt.append(item[part])
            elif isinstance(item, list) and part.lstrip("-").isdigit():
                idx = int(part)
                if -len(item) <= idx < len(item):
                    nxt.append(item[idx])
        cur = nxt
    return cur


def _numbers(payload: Any, dotted: str) -> list[float]:
    out = []
    for item in _flatten(payload, dotted):
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            out.append(float(item))
        elif isinstance(item, str):
            try:
                out.append(float(item))
            except ValueError:
                continue
    return out


def _aggregate(values: list[float], how: str) -> float | None:
    if not values:
        return None
    if how == "best":
        return min(values)
    if how == "mean":
        return sum(values) / len(values)
    return max(values)


def evaluate_reference(entry: dict, root: str | Path = ".") -> dict:
    """按 spec 的 ``reference`` 段复算参考误差。"""
    path = Path(root) / entry["path"]
    result: dict[str, Any] = {
        "path": str(entry["path"]),
        "metric": entry.get("metric", ""),
        "value_path": entry["value_path"],
        "aggregate": entry.get("aggregate", "worst"),
        "tolerance_pct": float(entry.get("tolerance_pct", 3.0)),
        "status": "MISSING",
        "worst_pct": None,
        "declared_worst_pct": None,
        "declaration_mismatch": None,
        "n_values": 0,
        "samples": [],
        "sha256": None,
    }
    if not path.exists():
        result["detail"] = f"artifact not found: {entry['path']}"
        return result

    raw = path.read_bytes()
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)

    values = _numbers(payload, entry["value_path"])
    result["n_values"] = len(values)
    recompute = entry.get("recompute")
    if recompute:
        computed = _numbers(payload, recompute["computed_path"])
        reference = _numbers(payload, recompute["reference_path"])
        if len(computed) != len(reference) or not computed:
            result["status"] = FAIL
            result["detail"] = (f"recompute length mismatch: computed={len(computed)} "
                                f"reference={len(reference)}")
            return result
        recomputed = [
            abs(c - r) / max(abs(r), 1e-12) * 100.0 for c, r in zip(computed, reference)
        ]
        declared = _numbers(payload, recompute.get("declared_error_path", entry["value_path"])) \
            if recompute.get("declared_error_path", entry["value_path"]) else []
        mismatch = None
        if declared and len(declared) == len(recomputed):
            worst_delta = max(abs(d - v) for d, v in zip(declared, recomputed))
            mismatch = worst_delta > float(recompute.get("declaration_tolerance_pct", 0.01))
            result["declared_worst_pct"] = _aggregate(declared, entry.get("aggregate", "worst"))
            result["declaration_max_delta_pct"] = worst_delta
            result["declaration_mismatch"] = mismatch
        values = recomputed
        result["samples"] = [round(v, 6) for v in recomputed[:5]]
    else:
        result["samples"] = [round(v, 6) for v in values[:5]]

    worst = _aggregate(values, entry.get("aggregate", "worst"))
    result["worst_pct"] = None if worst is None else round(worst, 6)
    result["declaration_mismatch"] = result["declaration_mismatch"] or bool(result.get("declaration_mismatch"))
    if worst is None:
        result["status"] = FAIL
        result["detail"] = f"no numeric values at value_path={entry['value_path']!r}"
        return result
    if result["declaration_mismatch"]:
        result["status"] = FAIL
        result["detail"] = "declared error column disagrees with recomputed error"
        return result
    result["status"] = PASS if worst <= result["tolerance_pct"] else FAIL
    return result