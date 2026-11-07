"""六维评价表与多臂对照门（通用层，零领域语义）。

六维：正确性 / 精度 / 稳定性 / 计算效率 / 可复现性 / 适用范围。
对照门只做**结构**裁决：某维在 ≥2 个臂上都有同单位的数值 + 来源，才算「可比维」。
比可比维数量达标更重要的是：每个数字都必须带 ``source``（工件路径/命令），
否则该维不入账——防「空话打分」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DIMENSIONS = (
    "correctness",
    "accuracy",
    "stability",
    "efficiency",
    "reproducibility",
    "applicability",
)

DIMENSION_LABELS = {
    "correctness": "正确性（是否满足守恒/解析极限/物理约束）",
    "accuracy": "精度（与基准的相对误差）",
    "stability": "稳定性（时间步/能量/收敛是否受控）",
    "efficiency": "计算效率（墙钟/步数）",
    "reproducibility": "可复现性（同输入两次运行差异）",
    "applicability": "适用范围（外推边界与失效模式登记）",
}


def build_table(arms: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    """规范化多臂六维表。

    ``arms`` = {arm_name: {dimension: {"value": float, "unit": str, "source": str}}}
    返回 {arms, dimensions, cells, n_numeric_by_dim}。非法维名/非数值 value 直接记入 errors。
    """
    errors: List[str] = []
    cells: List[Dict[str, Any]] = []
    for arm, dims in sorted(arms.items()):
        for dim, cell in sorted((dims or {}).items()):
            if dim not in DIMENSIONS:
                errors.append(f"{arm}: 未知维度 {dim}")
                continue
            value = (cell or {}).get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{arm}.{dim}: value 非数值（{value!r}）")
                continue
            source = str((cell or {}).get("source") or "").strip()
            if not source:
                errors.append(f"{arm}.{dim}: 缺 source（数字无出处不入账）")
                continue
            cells.append({
                "arm": arm,
                "dimension": dim,
                "value": float(value),
                "unit": str((cell or {}).get("unit") or ""),
                "source": source,
            })
    by_dim: Dict[str, int] = {d: 0 for d in DIMENSIONS}
    for c in cells:
        by_dim[c["dimension"]] += 1
    return {
        "arms": sorted(arms),
        "dimensions": list(DIMENSIONS),
        "cells": cells,
        "n_numeric_by_dim": by_dim,
        "errors": errors,
    }


def comparison_gate(
    table: Dict[str, Any],
    *,
    arms: Optional[List[str]] = None,
    min_dims: int = 2,
    require_units_match: bool = True,
) -> Dict[str, Any]:
    """多臂对照门：返回可比维清单与是否通过。

    可比维 = 该维在全部指定臂上都有数值，且单位一致（``require_units_match``）。
    """
    wanted = list(arms) if arms else list(table.get("arms") or [])
    comparable: List[Dict[str, Any]] = []
    insufficient: List[Dict[str, Any]] = []
    for dim in DIMENSIONS:
        cells = {c["arm"]: c for c in table.get("cells", []) if c["dimension"] == dim}
        missing = [a for a in wanted if a not in cells]
        if missing:
            if cells:
                insufficient.append({"dimension": dim, "missing_arms": missing})
            continue
        units = {cells[a]["unit"] for a in wanted}
        if require_units_match and len(units) > 1:
            insufficient.append({"dimension": dim, "reason": "单位不一致", "units": sorted(units)})
            continue
        comparable.append({
            "dimension": dim,
            "values": {a: cells[a]["value"] for a in wanted},
            "unit": sorted(units)[0] if units else "",
            "sources": {a: cells[a]["source"] for a in wanted},
        })
    return {
        "arms": wanted,
        "comparable": comparable,
        "n_comparable": len(comparable),
        "min_dims": int(min_dims),
        "insufficient": insufficient,
        "pass": len(comparable) >= int(min_dims),
    }