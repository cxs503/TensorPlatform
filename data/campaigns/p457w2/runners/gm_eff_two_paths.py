#!/usr/bin/env python3
"""Trace runner: post-damage ``GM_eff`` from TWO independent code paths of the node.

同一个量在节点内部有两条实现路径：
  A ``manual_gm_eff``        — 手工装配式（KB+BM-KG-Σi/∇）
  B ``damaged_hydrostatics`` — added-weight 累积式（逐舱体积/形心/自由液面）
闸把两条路径当两侧互裁：若 A≠B，说明节点自证里的 1e-9 级「互证」只在它自己
选的点成立，跨点不可复现 —— 这正是集成门要抓的东西。

输出 ``meta`` 带闸要核的标量（SOLAS 泛函两侧、质量账本残差），供 metric 判据读取。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

SIBLING_ROOT = Path(os.environ.get("TP_SIBLING_ROOT", "/root/tp-agent/work"))
WORKTREE = SIBLING_ROOT / "m13986"
MODULES = WORKTREE / "backend" / "modules"

# 与被测节点自证工件（artifacts/damage_stability_flooding/criteria.json）同一条
# 场景：驳船 100x20x14 m，初始吃水 6 m，KG0 7 m，两舱 15x10x8 m，渗透率 0.95。
SCENARIO = {"length": 100.0, "breadth": 20.0, "depth": 14.0, "draft0": 6.0, "kg0": 7.0}
COMPS = [("C1", 15.0, 10.0, 8.0, 0.95), ("C2", 15.0, 10.0, 8.0, 0.95)]
LEVELS = [0.0, 2.0, 4.0, 6.0, 8.0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    sys.path.insert(0, str(MODULES))
    from damage_stability_flooding import geometry, hydrostatics  # noqa: E402

    comps = [geometry.compartment_from_dict(
        {"name": n, "length": l, "breadth": b, "height": h, "permeability": mu})
        for n, l, b, h, mu in COMPS]
    rows = []
    worst = {"level": None, "abs_dev": 0.0}
    for h in LEVELS:
        levels = {c.name: h for c in comps}
        state = hydrostatics.damaged_hydrostatics(
            SCENARIO["length"], SCENARIO["breadth"], SCENARIO["draft0"],
            SCENARIO["kg0"], comps, levels)
        fs = [c.free_surface_inertia(h) for c in comps]
        # 路径 A：手工装配 KB+BM-KG-Σi/∇（heavy_free 由 added-weight 路径反解，
        # 使两条路径比的是「自由液面/装配」而不是「KG 抬升」）
        gm_manual = hydrostatics.manual_gm_eff(
            kb=state.kb, bm=state.bm, kg=state.kg, fs_inertias=fs, nabla=state.nabla)
        # 路径 B：added-weight 累积式
        gm_added = state.gm_eff
        dev = abs(gm_manual - gm_added)
        rows.append({"param": h, "value": float(gm_added)})
        if dev >= worst["abs_dev"]:
            worst = {"level": h, "abs_dev": dev}
    # 质量/体积账本残差：Σ体积（几何闭式）vs 舱容积分量
    v_geom = sum(geometry.rectangular_volume(l, b, h, mu) for _, l, b, h, mu in COMPS)
    v_lib = sum(c.volume(c.height) for c in comps)
    meta = {
        "gm_eff_abs_dev_max_m": float(worst["abs_dev"]),
        "gm_eff_abs_dev_at_level": float(worst["level"]),
        "volume_rel_dev": float(abs(v_geom - v_lib) / v_geom),
        "level_min": float(LEVELS[0]),
        "level_max": float(LEVELS[-1]),
        "module_sha256": sha256(MODULES / "damage_stability_flooding" / "hydrostatics.py"),
        "geometry_sha256": sha256(MODULES / "damage_stability_flooding" / "geometry.py"),
        "worktree": str(WORKTREE),
        "n_points": float(len(LEVELS)),
        "gm_eff_max": float(max(r["value"] for r in rows)),
        "gm_eff_min": float(min(r["value"] for r in rows)),
    }
    print(json.dumps({"rows": rows, "meta": meta}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())