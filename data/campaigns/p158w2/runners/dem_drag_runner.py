#!/usr/bin/env python3
"""域算例脚本 (p158w2 · n02 侧)：沿孔隙率扫描 Gidaspow 混合曳力。

本脚本属于**战役数据/算例工件**（data/ 下），被
``tools/integration_gate.sampler`` 以 ``kind="script"`` 调用，输出最后一行 JSON：

    {"rows": [{"param": <voidage>, "value": <force per unit volume>}, ...],
     "meta": {"module_sha256": ..., "model": ...}}

被测模块路径与被测函数都由 ``traces.json`` 决定，脚本只做"同一工况扫一遍"，
不复制被测代数（避免与被测实现同错）。任何导入/调用异常都以非零退出 + stderr
返回，让集成门 fail-closed 成 WITHHELD。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SRC_ROOT = Path("/root/tp-agent/work/m8493/src")
MODULE = "tensordem.two_way_coupling.ergun_sweep"
FUNC = "dem_drag_per_volume"
VOIDAGE = [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.79, 0.8, 0.81, 0.85, 0.9]
U_REL = 0.05          # m/s 相对速度
DIAMETER = 2.0e-3     # m
RHO_FLUID = 1000.0    # kg/m^3
MU = 1.0e-3           # Pa*s


def main() -> int:
    sys.path.insert(0, str(SRC_ROOT))
    import importlib
    try:
        mod = importlib.import_module(MODULE)
        fn = getattr(mod, FUNC)
    except Exception as exc:  # fail-closed
        print(f"import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    rows = []
    for eps in VOIDAGE:
        try:
            v = fn(eps, U_REL, DIAMETER, RHO_FLUID, MU)
        except Exception as exc:
            print(f"call failed at voidage={eps}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        rows.append({"param": float(eps), "value": float(v)})
    sha = hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()
    print(json.dumps({"rows": rows,
                      "meta": {"module_sha256": sha, "module": MODULE, "func": FUNC,
                               "u_rel_m_s": U_REL, "d_m": DIAMETER,
                               "rho_kg_m3": RHO_FLUID, "mu_Pa_s": MU}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())