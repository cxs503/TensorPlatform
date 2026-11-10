"""回归基线: 基线读数、当前值比对、契约严格度增量 (纯 stdlib)。

三件事:

1. ``read_baseline``: 从上一波工件里按 JSON pointer 读基线值，并记 sha256
   (基线值不是手抄的，来自工件本身；manifest 里可选声明值，二者不一致 =
   基线漂移，报违约);
2. ``compare``: 当前值 vs 基线值，相对偏差与容差给 PASS/FAIL；当前值缺失
   一律 ``WITHHELD`` (缺证据不许算通过);
3. ``bound_delta``: 从两波的判据文本里抽取数值界，给"契约收紧/放宽"的
   可量化增量 (测量值缺失时，这是唯一诚实的改进量化口径)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .ingest import IngestError, load_json, resolve_pointer
from .provenance import sha256_file

BOUND_RE = re.compile(
    r"(<=|>=|≤|≥|<|>|＝|=)\s*"
    r"([0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)"
    r"\s*(%|‰|m·rad|rad|deg|m|s|kN|N|kg|t)?")
_OP_NORM = {"<=": "<=", "≤": "<=", "<": "<", ">=": ">=", "≥": ">=", ">": ">",
            "=": "==", "＝": "=="}
# 全局界里"越小越严"的算子；其余算子在收紧方向相反。
_LOWER_BETTER = ("<=", "<", "==")


def read_baseline(q: dict[str, Any], work_root: str | Path) -> dict[str, Any]:
    spec = q.get("baseline")
    if not spec:
        return {"declared": False}
    path = Path(work_root) / spec["worktree"] / spec["artifact"]
    out: dict[str, Any] = {
        "declared": True,
        "worktree": spec["worktree"],
        "artifact": spec["artifact"],
        "pointer": spec["pointer"],
        "unit": spec.get("unit", q["unit"]),
        "frame": spec.get("frame", q["frame"]),
        "declared_value": spec.get("value"),
        "artifact_present": path.is_file(),
        "artifact_sha256": None,
        "value": None,
        "drift": None,
        "error": None,
    }
    if not path.is_file():
        out["error"] = f"baseline artifact missing: {path}"
        return out
    out["artifact_sha256"] = sha256_file(path)
    try:
        doc = load_json(path)
        out["value"] = float(resolve_pointer(doc, spec["pointer"]))
    except (IngestError, TypeError, ValueError) as exc:
        out["error"] = str(exc)
        return out
    declared = spec.get("value")
    if declared is not None:
        scale = max(abs(float(declared)), 1e-12)
        rel = abs(out["value"] - float(declared)) / scale
        out["drift"] = {"declared_value": float(declared),
                        "artifact_value": out["value"],
                        "rel": round(rel, 12),
                        "consistent": rel <= 1e-9}
    return out


def compare(baseline: dict[str, Any], current: dict[str, Any] | None,
            tolerance_rel: float | None) -> dict[str, Any]:
    """当前值 vs 基线值: 相对偏差、方向(改进/退化)、容差判定。"""
    if current is None or current.get("value") is None:
        return {"verdict": "WITHHELD", "delta_rel": None, "direction": None,
                "reason": "current value absent (artifact not produced at scan time)"}
    cv = float(current["value"])
    if baseline.get("value") is None:
        return {"verdict": "WITHHELD", "delta_rel": None, "direction": None,
                "current_value": cv, "reason": "baseline value absent"}
    bv = float(baseline["value"])
    scale = max(abs(bv), 1e-12)
    delta = (cv - bv) / scale
    direction = "improved" if abs(cv) < abs(bv) else (
        "degraded" if abs(cv) > abs(bv) else "unchanged")
    if tolerance_rel is None:
        verdict = "NA"
        reason = "no tolerance declared for this quantity"
    else:
        verdict = "PASS" if abs(delta) <= tolerance_rel else "FAIL"
        reason = (f"|delta|={abs(delta):.6g} vs tolerance {tolerance_rel:.6g}"
                  if verdict == "FAIL" else
                  f"|delta|={abs(delta):.6g} within tolerance {tolerance_rel:.6g}")
    return {"verdict": verdict, "delta_rel": round(delta, 12),
            "delta_abs": round(cv - bv, 12),
            "current_value": cv, "baseline_value": bv,
            "direction": direction, "reason": reason}


def extract_bounds(texts: list[str]) -> list[dict[str, Any]]:
    """从判据文本里抽数值界: ``<3%`` → {op:'<', value:3.0, unit:'%'}。"""
    out: list[dict[str, Any]] = []
    for text in texts or []:
        for m in BOUND_RE.finditer(str(text)):
            op = _OP_NORM[m.group(1)]
            try:
                value = float(m.group(2))
            except ValueError:
                continue
            ctx = str(text)[max(0, m.start() - 12):m.start()].strip()
            out.append({"op": op, "value": value, "unit": m.group(3) or "1",
                        "raw": m.group(0).strip(), "context": ctx,
                        "source": str(text)[:120]})
    return out


def bound_delta(baseline_texts: list[str], current_texts: list[str]) -> dict[str, Any]:
    """两波判据文本的数值界增量: 收紧/放宽/新增/移除 逐条给出。"""
    w2 = extract_bounds(baseline_texts)
    w3 = extract_bounds(current_texts)
    if not w2 or not w3:
        return {"w2_bounds": len(w2), "w3_bounds": len(w3),
                "comparable": False,
                "reason": "one side has no numeric bound in criteria text",
                "tightened": [], "loosened": [], "added": len(w3), "removed": len(w2)}
    tightened, loosened, same = [], [], []
    used = set()
    for b in w2:
        for i, c in enumerate(w3):
            if i in used or c["op"] != b["op"] or c["unit"] != b["unit"]:
                continue
            used.add(i)
            rel = (c["value"] - b["value"]) / max(abs(b["value"]), 1e-12)
            item = {"op": b["op"], "unit": b["unit"], "w2": b["value"],
                    "w3": c["value"], "rel_change": round(rel, 6),
                    "w2_context": b["context"], "w3_context": c["context"],
                    "context_same": b["context"] == c["context"]}
            if abs(rel) <= 1e-12:
                same.append(item)
            elif (b["op"] in _LOWER_BETTER and c["value"] < b["value"]) or \
                 (b["op"] not in _LOWER_BETTER and c["value"] > b["value"]):
                tightened.append(item)
            else:
                loosened.append(item)
            break
    return {"w2_bounds": len(w2), "w3_bounds": len(w3), "comparable": True,
            "tightened": tightened, "loosened": loosened, "unchanged": len(same),
            "added": len(w3) - len(used), "removed": len(w2) - len(used),
            "pairs_with_context_mismatch": sum(
                1 for it in tightened + loosened if not it["context_same"]),
            "note": ("bound-level comparison of acceptance criteria; pairs are matched by "
                     "operator+unit token only, so context_same=False pairs are indicative "
                     "of strictness change but may refer to different metrics; added/"
                     "removed counts are robust to that")}