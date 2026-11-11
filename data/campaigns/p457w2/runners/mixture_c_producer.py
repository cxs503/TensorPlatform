#!/usr/bin/env python3
"""Trace runner: mixture sound speed from the producer node implementation.

只读加载兄弟 worktree 里的 ``tensorsph.aeration.mixture_acoustics``（战役 p457w2-n04
的实现），沿 alpha 网格输出 c_m。兄弟 worktree 根目录用环境变量 ``TP_SIBLING_ROOT``
覆盖（默认 /root/tp-agent/work），模块文件 sha256 随 meta 一起输出，供闸记录
「这一侧的数值究竟出自哪个文件」。

输出协议（tools.integration_gate.sampler 的 script 轨迹约定）：
最后一行 stdout 为 JSON ``{"rows": [{"param": ..., "value": ...}], "meta": {...}}``。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

SIBLING_ROOT = Path(os.environ.get("TP_SIBLING_ROOT", "/root/tp-agent/work"))
N04 = SIBLING_ROOT / "m13329" / "src" / "tensorsph" / "aeration"
GRID = [0.0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.45]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    mod_path = N04 / "mixture_acoustics.py"
    if not mod_path.is_file():
        print(json.dumps({"error": f"producer module absent: {mod_path}"}))
        return 1
    sys.path.insert(0, str(N04.parent.parent))  # .../src  → import tensorsph.*
    from tensorsph.aeration import mixture_acoustics as ma  # noqa: E402

    rows = []
    for a in GRID:
        c = ma.wood_sound_speed(a)
        rows.append({"param": a, "value": float(c)})
    alpha_star = ma.alpha_at_min_sound_speed()
    c_min = ma.min_sound_speed()
    meta = {
        "alpha_star": float(alpha_star),
        "c_min": float(c_min),
        "alpha_min_resolvable": float(ma.ALPHA_MIN_RESOLVABLE),
        "alpha_vw_max": float(ma.ALPHA_VW_MAX),
        "module_sha256": sha256(mod_path),
        "module_path": str(mod_path),
        "c_water": float(ma.wood_sound_speed(0.0)),
    }
    print(json.dumps({"rows": rows, "meta": meta}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())