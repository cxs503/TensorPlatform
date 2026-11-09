#!/usr/bin/env python3
"""域算例脚本 (p158w2 · n03 侧)：一维纯导热 CHT 工况的剖面与能量账本。

被 ``tools/integration_gate.sampler`` 以 ``kind="script"`` 调用，输出最后一行 JSON：

    {"rows": [{"param": <x_m>, "value": <T_K>}, ...],
     "meta": {"normalized_imbalance": ..., "residual_scaled": ...,
              "boundary_heat_rate_west": ..., "inflow": ..., "outflow": ...,
              "module_sha256": ..., "analytic_heat_rate": ...}}

工况（薄板一维导热，两侧 Dirichlet、上下绝热）：
    nx=6, ny=4, dx=0.1 m, dy=0.05 m, kappa=1e-5 (均匀), T_west=400 K, T_east=300 K
解析锚：稳态线性剖面 T(x) = 400 - 100 x / 0.6；单位深度热流 Q = kappa·ΔT/L·H。
脚本只组装工况、不复制被测离散代数；异常 → 非零退出，让集成门 fail-closed。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

SRC_ROOT = Path("/root/tp-agent/work/m8494/src")
MODULE = "tensorfvm.cht.energy"
FUNC = "solve_steady_energy"

NX, NY = 6, 4
DX, DY = 0.1, 0.05
KAPPA = 1.0e-5
T_WEST, T_EAST = 400.0, 300.0
LENGTH = NX * DX
HEIGHT = NY * DY


def main() -> int:
    sys.path.insert(0, str(SRC_ROOT))
    import importlib
    try:
        mod = importlib.import_module(MODULE)
        fn = getattr(mod, FUNC)
    except Exception as exc:  # fail-closed
        print(f"import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    dx = np.full(NX, DX)
    dy = np.full(NY, DY)
    kappa = np.full((NY, NX), KAPPA)
    flux_e = np.zeros((NY, NX + 1))
    flux_n = np.zeros((NY + 1, NX))
    bc = {"west": {"type": "dirichlet", "values": T_WEST},
          "east": {"type": "dirichlet", "values": T_EAST},
          "south": {"type": "adiabatic"},
          "north": {"type": "adiabatic"}}
    try:
        T, info = fn(dx, dy, kappa, flux_e, flux_n, bc)
        led = info["ledger"].as_dict()
    except Exception as exc:
        print(f"call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    xc = np.cumsum(dx) - dx / 2.0
    rows = [{"param": float(x), "value": float(T[NY // 2, i])}
            for i, x in enumerate(xc)]
    sha = hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()
    print(json.dumps({
        "rows": rows,
        "meta": {
            "module_sha256": sha,
            "module": MODULE,
            "func": FUNC,
            "normalized_imbalance": float(led["normalized_imbalance"]),
            "residual_scaled": float(led["residual_scaled"]),
            "residual_inf": float(led["residual_inf"]),
            "boundary_heat_rate_west": float(led["boundary_heat_rate"]["west"]),
            "boundary_heat_rate_east": float(led["boundary_heat_rate"]["east"]),
            "analytic_heat_rate_west": float(KAPPA * (T_WEST - T_EAST) / LENGTH * HEIGHT),
            "picard_iterations": float(info["picard_iterations"]),
            "kappa_m2_s": KAPPA, "dx_m": DX, "dy_m": DY,
            "T_west_K": T_WEST, "T_east_K": T_EAST,
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())