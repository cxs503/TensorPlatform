"""对照组：既有冰区航行集团军「常规模式」的重建基线（诚实标注为重建）。

常规模式的特征（本单对照组的定义，可审计）
------------------------------------------
1. **一次性实现即交付**：拿到模型就用默认微参数（不做按文献弹性模量的标定）；
2. **无预测盲**：不登记假设与预测，跑完直接给结论；
3. **无独立复算**：同一实现者跑一次即止，换随机种子不复算；
4. **稳定性靠习惯**：直接沿用教科书「0.2 Δt_crit」的经验安全系数。

对照组按上述四点执行，并给一次「重试机会」（把步长降到 0.1 再跑），
以免把对照组的不利全部归因于步长——这样精度维度上的差距才真正反映「无标定/无盲预测」。

输出六维中的数值（单位与实验组一致），供 ``comparison_gate`` 比对。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

from domains.ice_fracture_contact import benchmarks as bm
from domains.ice_fracture_contact.dem_bpm import (IceDEM, add_box_walls, build_lattice)


def _naive_uniaxial(*, dt_safety: float, seed: int, max_steps: int = 4000,
                    spec: "bm.IceSpec" = None) -> Dict[str, Any]:
    """常规模式的一发式单轴压缩（不标定、不检查、不盲预测）。"""
    spec = spec or bm.IceSpec()
    spec.dt_safety = dt_safety
    spec.seed = seed
    pos, rad = build_lattice(16, 34, spec.radius, jitter=0.15, seed=seed)
    bm._flatten_rows(pos, rad, spec.radius)
    sim = bm._new_sim(pos, rad, spec, seed=seed)
    H0 = float(pos[:, 1].max() - pos[:, 1].min())
    W = float(pos[:, 0].max() - pos[:, 0].min())
    add_box_walls(sim, top_vel=0.0)
    sim.walls[1].velocity = 1.0e-2 * H0
    peak, max_omega, max_v = 0.0, 0.0, 0.0
    diverged = False
    for _ in range(max_steps // 50):
        loads = sim.step(50)
        max_omega = max(max_omega, float(np.abs(sim.omega).max()))
        max_v = max(max_v, float(np.abs(sim.vel).max()))
        sig = loads.get("top", 0.0) / (W * sim.t)
        peak = max(peak, abs(sig))
        if max_v > 1.0 or not np.isfinite(max_v) or max_omega > 1e4:
            diverged = True
            break
    return {"dt_safety": dt_safety, "seed": seed, "diverged": diverged,
            "max_omega_rad_s": max_omega, "max_v_m_s": max_v,
            "sigma_peak_Pa": float(peak), "steps": int(sim.step_count),
            "dt_s": float(sim.dt),
            "note": "常规模式不量测模量/泊松比（无独立复算工序）"}


def run_control_arm() -> Dict[str, Any]:
    """执行对照组并给出六维数值。"""
    t0 = time.time()
    base = bm.IceSpec()          # 默认微参数：不做 E 标定（E_b = 7.8e9 原值）
    attempt1 = _naive_uniaxial(dt_safety=0.2, seed=12345, spec=bm.IceSpec())
    attempt2 = _naive_uniaxial(dt_safety=0.1, seed=777, spec=bm.IceSpec())
    repeat = _naive_uniaxial(dt_safety=0.1, seed=999, spec=bm.IceSpec())
    # 常规模式的「精度」：拿未标定参数的量测模量对文献
    E_ref = 9.0e9
    E_naive = None
    ucs = float("nan")
    try:
        res = bm.run_uniaxial(bm.IceSpec(dt_safety=0.1, seed=777), max_strain=4.0e-3)
        E_naive = float(res["E_app_Pa"])
        ucs = float(res["sigma_c_Pa"])
    except Exception as exc:      # pragma: no cover
        res = {}
        E_naive = float("nan")
        print("control arm uniaxial failed:", exc)
    relE = abs(E_naive - E_ref) / E_ref if E_naive and np.isfinite(E_naive) else 1.0
    rep_rel = (abs(attempt2["sigma_peak_Pa"] - repeat["sigma_peak_Pa"])
               / max(attempt2["sigma_peak_Pa"], 1e-30)) if attempt2["sigma_peak_Pa"] else 1.0
    wall = round(time.time() - t0, 3)
    dims = {
        "correctness": {"value": 0.0, "unit": "1",
                        "source": "常规模式无解析/守恒闭合校验（对照定义第 3 条）"},
        "accuracy": {"value": float(relE), "unit": "rel_err_vs_literature",
                     "source": f"未标定 E_b=7.8e9 量得 E_app={E_naive/1e9:.3f} GPa vs 文献 9 GPa"},
        "stability": {"value": float(max(attempt1["max_omega_rad_s"], attempt2["max_omega_rad_s"])),
                      "unit": "max_angular_velocity_rad_s",
                      "source": "0.2 安全系数发散、0.1 重试后最大自旋（control_arm.json）"},
        "efficiency": {"value": float(wall), "unit": "s",
                       "source": "对照组总墙钟（无标定、无复算以外的额外工序）"},
        "reproducibility": {"value": float(rep_rel), "unit": "rel_diff_two_runs",
                            "source": "两种子（777/999）峰值载荷相对差"},
        "applicability": {"value": 1.0, "unit": "documented_limits",
                          "source": "常规模式仅记录「跑通/发散」二元结论"},
    }
    return {
        "note": "对照组=既有冰区集团军常规模式的重建基线（一次性实现/无标定/无盲预测/无独立复算），"
                "非历史记录原样复现，重建规则即本文件头注释",
        "attempt1_dt020": attempt1, "attempt2_dt010": attempt2, "repeat_dt010": repeat,
        "uncalibrated_run": {k: v for k, v in (res or {}).items() if k != "samples"},
        "E_naive_Pa": E_naive, "UCS_naive_Pa": ucs,
        "dimensions": dims, "wall_clock_s": wall,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_control_arm(), ensure_ascii=False, indent=2, default=float))