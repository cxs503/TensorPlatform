"""推进域敞水性能验证器 —— Wageningen B 系列三方对账

裁决对象
--------
O1  线上端点  POST /api/v1/propeller-design/wageningen-b   （自述「Wageningen B系列回归公式」）
O2  仓内方法  backend/methods/propulsion/wageningen.py      （自述 Oosterveld 系数表）
O3  基准数据集 backend/data/test_data/propulsion_wageningen.json （自述 "Oosterveld & van Oossanen, 1975"）

参考侧：tools/verification/propulsion/b_series_reference.py（真 Oosterveld 46 项多项式）

判据（每条都可机器裁）
--------------------
C1 输入敏感性      dK_T/d(P/D) ≠ 0 且 dK_T/dZ ≠ 0（真实回归式必然响应 P/D、Z、Ae/Ao）
C2 K_Q/K_T 结构比  比值随 J 变化（真实：J=0 处 ≈6.7，J=1.0 处 ≈11.7）；常数比 = 伪造签名
C3 动量理论界      η0 < η_ideal = 2/(1+√(1+C_T))，C_T = 8K_T/(πJ²)（Rankine–Froude，定理级）
C4 单调/单峰       K_T、K_Q 对 J 单调递减；η0 单峰
C5 零推力点        P/D=1.0 时 J0 = J(K_T=0) ∈ [0.90, 1.20]
C6 数值对账        J=0.4/0.6/0.8 处 K_T、K_Q、η0 相对参考实现误差 < 15%（超 15% 不可接受）

运行：
    python3 tools/verification/propulsion/verify_open_water.py            # 用固化的实测样本
    python3 tools/verification/propulsion/verify_open_water.py --live     # 复探线上端点
产物：
    artifacts/propulsion_verification/propulsion_ow_verification.json
    artifacts/propulsion_verification/fig1_kt_kq_eta.png
    artifacts/propulsion_verification/fig2_error_bars.png
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

import b_series_reference as ref  # noqa: E402

ARTIFACT_DIR = ROOT / "artifacts" / "propulsion_verification"
SERIES_ENDPOINT = "/api/v1/propeller-design/wageningen-b"

# ---------------------------------------------------------------------------
# O1 实测样本（2026-11-01 由验证席对 :8001 线上端点实测，原样固化）
# ---------------------------------------------------------------------------
CAPTURED_AT = "2026-11-01T~07:55Z"
SERVING_SAMPLES: List[Dict] = [
    {"J": 0.0, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.70, "KT": 0.36, "KQ": 0.036, "eta0": 0.5},
    {"J": 0.2, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.70, "KT": 0.312, "KQ": 0.0312, "eta0": 0.3183},
    {"J": 0.4, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.70, "KT": 0.264, "KQ": 0.0264, "eta0": 0.6366},
    {"J": 0.6, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.70, "KT": 0.216, "KQ": 0.0216, "eta0": 0.85},
    {"J": 0.8, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.70, "KT": 0.168, "KQ": 0.0168, "eta0": 0.85},
    # P/D 扫描（J=0.6, Z=4, Ae/Ao=0.70）
    {"J": 0.6, "P_D": 0.6, "Z": 4, "Ae_Ao": 0.70, "KT": 0.072, "KQ": 0.0072, "eta0": 0.85},
    {"J": 0.6, "P_D": 0.8, "Z": 4, "Ae_Ao": 0.70, "KT": 0.144, "KQ": 0.0144, "eta0": 0.85},
    {"J": 0.6, "P_D": 1.2, "Z": 4, "Ae_Ao": 0.70, "KT": 0.288, "KQ": 0.0288, "eta0": 0.85},
    {"J": 0.6, "P_D": 1.4, "Z": 4, "Ae_Ao": 0.70, "KT": 0.36, "KQ": 0.036, "eta0": 0.85},
    # Z / Ae/Ao 扫描（J=0.5, P/D=1.2）
    {"J": 0.5, "P_D": 1.2, "Z": 3, "Ae_Ao": 0.40, "KT": 0.2223, "KQ": 0.02223, "eta0": 0.7958},
    {"J": 0.5, "P_D": 1.2, "Z": 4, "Ae_Ao": 0.70, "KT": 0.312, "KQ": 0.0312, "eta0": 0.7958},
    {"J": 0.5, "P_D": 1.2, "Z": 5, "Ae_Ao": 0.85, "KT": 0.358, "KQ": 0.0358, "eta0": 0.7958},
    {"J": 0.5, "P_D": 1.2, "Z": 6, "Ae_Ao": 1.00, "KT": 0.4056, "KQ": 0.04056, "eta0": 0.7958},
    {"J": 0.5, "P_D": 1.2, "Z": 2, "Ae_Ao": 0.35, "KT": 0.221, "KQ": 0.0221, "eta0": 0.7958},
]

# O2：仓内 backend/methods/propulsion/wageningen.py 的「KT_COEFFICIENTS」前 15 项
# （原样抄录；该表全 39 项中前 15 项 = KQ 表逐项 ×10，其余 24 项为 ±1e-5 填充）
O2_SUSPECT_CT_HEAD = [
    (0.219, 0, 0, 0, 0), (-0.392, 0, 0, 0, 1), (0.156, 0, 0, 1, 0),
    (-0.373, 0, 1, 0, 0), (-0.0585, 1, 0, 0, 0), (0.0125, 0, 0, 0, 2),
    (-0.004, 0, 0, 2, 0), (0.0006, 0, 2, 0, 0), (0.0003, 2, 0, 0, 0),
    (0.0001, 0, 1, 1, 0), (-0.0002, 1, 0, 1, 0), (0.00015, 1, 1, 0, 0),
    (-0.00005, 0, 1, 0, 1), (0.00008, 0, 0, 1, 1), (-0.00003, 1, 0, 0, 1),
]
O2_SUSPECT_CQ_HEAD = [
    (0.0219, 0, 0, 0, 0), (-0.0392, 0, 0, 0, 1), (0.0156, 0, 0, 1, 0),
    (-0.0373, 0, 1, 0, 0), (-0.00585, 1, 0, 0, 0), (0.00125, 0, 0, 0, 2),
    (-0.0004, 0, 0, 2, 0), (0.00006, 0, 2, 0, 0), (0.00003, 2, 0, 0, 0),
    (0.00001, 0, 1, 1, 0), (-0.00002, 1, 0, 1, 0), (0.000015, 1, 1, 0, 0),
    (-0.000005, 0, 1, 0, 1), (0.000008, 0, 0, 1, 1), (-0.000003, 1, 0, 0, 1),
]

# O3：仓内 backend/data/test_data/propulsion_wageningen.json 的 data_points 原样抄录
O3_DATASET = {
    "J": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2],
    "KT": [0.40, 0.35, 0.28, 0.18, 0.10, 0.03, 0.00],
    "KQ": [0.040, 0.038, 0.034, 0.027, 0.018, 0.008, 0.0],
    "eta_0": [0.0, 0.294, 0.524, 0.637, 0.566, 0.597, 0.0],
    "declared": "B4-70, P/D=1.0（Oosterveld & van Oossanen, 1975）",
}

# O4：POST /api/v1/calculations/propulsion 实测样本（2026-11-01，同一船体，仅换桨径/螺距）
PROPULSION_SAMPLES: List[Dict] = [
    {"tag": "D=6.5m P/D=1.0", "D": 6.5, "rpm": 100.0, "V_kn": 15.0,
     "thrust_N": 9042.02, "torque_Nm": 3599.379, "delivered_power_W": 37692.6,
     "open_water_eff": 1.481, "hull_eff": 1.1, "relative_rot_eff": 1.0, "propulsive_eff": 1.629},
    {"tag": "D=8.0m P/D=1.0", "D": 8.0, "rpm": 100.0, "V_kn": 15.0,
     "thrust_N": 26369.59, "torque_Nm": 12413.792, "delivered_power_W": 129996.92,
     "open_water_eff": 1.252, "hull_eff": 1.1, "relative_rot_eff": 1.0, "propulsive_eff": 1.377},
]
RHO, KNOT = 1025.0, 0.514444

CASES = [(0.4, "J0.4"), (0.6, "J0.6"), (0.8, "J0.8")]   # 对账用的无因次工况


def _rel(got: float, want: float) -> float:
    """相对误差（%）；want=0 时返回 nan。"""
    if want == 0 or got is None:
        return float("nan")
    return 100.0 * (got - want) / want


def _live_probe(timeout: float = 20.0) -> Optional[List[Dict]]:
    """复探线上端点（端点不可达时返回 None，退回固化样本）。"""
    import urllib.request

    out: List[Dict] = []
    for s in SERVING_SAMPLES:
        body = json.dumps({"J": s["J"], "P_D": s["P_D"], "Z": s["Z"], "Ae_Ao": s["Ae_Ao"]}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8001" + SERIES_ENDPOINT, data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            r = json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as exc:  # 端点不可达 → 视为无实时证据
            print(f"[live] 探测失败，退回固化样本：{exc}")
            return None
        if not isinstance(r, dict) or "KT" not in r:
            print(f"[live] 返回体不合预期，退回固化样本：{str(r)[:120]}")
            return None
        out.append({**s, "KT": r["KT"], "KQ": r["KQ"], "eta0": r.get("eta0", r.get("eta_0")),
                    "method": r.get("method")})
    return out


# ---------------------------------------------------------------------------
# 判据实现
# ---------------------------------------------------------------------------
def grade_o1(samples: List[Dict]) -> Dict:
    """O1 线上端点逐条判据。"""
    by_case = {(round(s["J"], 3), round(s["P_D"], 3), s["Z"], round(s["Ae_Ao"], 3)): s for s in samples}

    def get(J, P_D=1.0, Z=4, Ae=0.70):
        return by_case.get((round(J, 3), round(P_D, 3), Z, round(Ae, 3)))

    items = []

    # C1 输入敏感性：P/D 从 0.6→1.4，ΔK_T 应显著（真实回归式在 J=0.6 约 +0.15/0.2P/D）
    lo, hi = get(0.6, 0.6), get(0.6, 1.4)
    dkt_dpd = (hi["KT"] - lo["KT"]) / (1.4 - 0.6) if lo and hi else float("nan")
    ref_dkt_dpd = (ref.kt_kq(0.6, 1.4, 0.70, 4)[0] - ref.kt_kq(0.6, 0.6, 0.70, 4)[0]) / 0.8
    items.append({
        "id": "C1a", "name": "dK_T/d(P/D) @J=0.6（输入端是否被使用）",
        "got": round(dkt_dpd, 4), "ref": round(ref_dkt_dpd, 4),
        "rel_err_pct": round(_rel(dkt_dpd, ref_dkt_dpd), 1),
        "pass": abs(_rel(dkt_dpd, ref_dkt_dpd)) <= 15.0,
        "note": "端点确实随 P/D 线性变化，但斜率与回归式不符 → 用了 P/D 但用错方式",
    })
    # Z 敏感性：Z 3→6
    z3, z6 = get(0.5, 1.2, 3, 0.40), get(0.5, 1.2, 6, 1.00)
    dkt_dz = (z6["KT"] - z3["KT"]) / 3.0 if z3 and z6 else float("nan")
    ref_dkt_dz = (ref.kt_kq(0.5, 1.2, 1.00, 6)[0] - ref.kt_kq(0.5, 1.2, 0.40, 3)[0]) / 3.0
    items.append({
        "id": "C1b", "name": "ΔK_T/ΔZ（Z:3→6, Ae/Ao 同时 0.40→1.00）@J=0.5",
        "got": round(dkt_dz, 4), "ref": round(ref_dkt_dz, 4),
        "rel_err_pct": round(_rel(dkt_dz, ref_dkt_dz), 1),
        "pass": abs(_rel(dkt_dz, ref_dkt_dz)) <= 15.0,
        "note": "Z 与 Ae/Ao 混合扫描；数值方向对但幅度不可信",
    })

    # C2 K_Q/K_T 结构比是否随 J 变化
    ratios = []
    for J in (0.0, 0.2, 0.4, 0.6, 0.8):
        s = get(J)
        if s and s["KQ"]:
            ratios.append({"J": J, "KT_over_KQ": round(s["KT"] / s["KQ"], 4)})
    ref_ratios = [{"J": J, "KT_over_KQ": round(ref.kt_kq(J, 1.0, 0.70, 4)[0] / ref.kt_kq(J, 1.0, 0.70, 4)[1], 4)}
                  for J in (0.0, 0.2, 0.4, 0.6, 0.8)]
    spread_got = max(r["KT_over_KQ"] for r in ratios) - min(r["KT_over_KQ"] for r in ratios) if ratios else float("nan")
    items.append({
        "id": "C2", "name": "K_T/K_Q 随 J 的变动幅度（常数=伪造）",
        "got": round(spread_got, 4), "ref": round(max(r['KT_over_KQ'] for r in ref_ratios)
                                                 - min(r['KT_over_KQ'] for r in ref_ratios), 4),
        "pass": spread_got > 1.0,
        "detail": {"serving": ratios, "reference": ref_ratios},
        "note": "参考实现 K_T/K_Q 随 J 单调下降 6.74(J=0)→3.17(J=1.0)；端点恒为 10.00（变动幅度 0）"
                "→ K_Q 由 K_T 直接除以 10 得到，与 B 系列任何真实数据都不相容",
    })

    # C3 动量理论界
    viol = []
    for s in samples:
        if s["J"] <= 0 or s["eta0"] is None:
            continue
        bound = ref.eta_ideal(s["KT"], s["J"])
        if bound is not None and s["eta0"] > bound:
            viol.append({"J": s["J"], "P_D": s["P_D"], "Z": s["Z"], "eta0": s["eta0"],
                         "eta_ideal": round(bound, 4), "excess_pct": round(100 * (s["eta0"] / bound - 1), 1)})
    items.append({
        "id": "C3", "name": "η0 < η_ideal（Rankine–Froude 上界，定理级）",
        "got": len(viol), "ref": 0, "violations": viol,
        "pass": len(viol) == 0,
        "note": "违反数 > 0 即物理不可能 → 输出不可能由任何真实敞水算法产生",
    })

    # C4 单调性 / 单峰
    eta_seq = [get(J) for J in (0.0, 0.2, 0.4, 0.6, 0.8)]
    etas = [s["eta0"] for s in eta_seq if s]
    bounded = sum(1 for e in etas if abs(e - 0.85) < 1e-9)
    items.append({
        "id": "C4", "name": "η0 曲线形状（单峰、无平台段）",
        "got": {"eta0": etas, "n_clamped_at_0.85": bounded}, "ref": "单峰且峰值 < 0.75（B4-70 P/D=1.0）",
        "pass": bounded == 0,
        "note": f"{bounded} 个工况被硬截断到 0.85 → 截断常数暴露",
    })

    # C5 零推力点
    Js = [0.0, 0.2, 0.4, 0.6, 0.8]
    kts = [get(J)["KT"] for J in Js]
    J0, J0_mode = None, "none"
    for i in range(len(Js) - 1):
        if kts[i] > 0 >= kts[i + 1]:
            J0 = Js[i] + (Js[i + 1] - Js[i]) * kts[i] / (kts[i] - kts[i + 1])
            J0_mode = "interpolated"
    if J0 is None and len(kts) >= 2 and kts[-1] > 0:      # 样本内不外推 → 末两点线性外推
        slope = (kts[-1] - kts[-2]) / (Js[-1] - Js[-2])
        if slope < 0:
            J0 = Js[-1] - kts[-1] / slope
            J0_mode = "extrapolated"
    ref_J0 = ref.zero_thrust_J(1.0, 0.70, 4)
    items.append({
        "id": "C5", "name": "零推力进速比 J0（K_T=0），P/D=1.0",
        "got": None if J0 is None else round(J0, 4), "mode": J0_mode, "ref": round(ref_J0, 4),
        "rel_err_pct": None if J0 is None else round(_rel(J0, ref_J0), 1),
        "pass": J0 is not None and abs(_rel(J0, ref_J0)) <= 15.0,
        "note": "端点 K_T 线性斜率 −0.24/J 外推得 J0≈1.50，比参考 1.0618 高 41%"
                if J0_mode == "extrapolated" else "",
    })

    # C6 数值对账（三点 × 三量）
    table = []
    for J, tag in CASES:
        s = get(J)
        if not s:
            continue
        rk, rq = ref.kt_kq(J, 1.0, 0.70, 4)
        rp = ref.open_water(J, 1.0, 0.70, 4)
        row = {"case": tag, "J": J,
               "KT": {"serving": s["KT"], "ref": round(rk, 5), "rel_err_pct": round(_rel(s["KT"], rk), 1)},
               "KQ": {"serving": s["KQ"], "ref": round(rq, 6), "rel_err_pct": round(_rel(s["KQ"], rq), 1)},
               "eta0": {"serving": s["eta0"], "ref": round(rp.eta0, 4), "rel_err_pct": round(_rel(s["eta0"], rp.eta0), 1)},
               "eta_ideal": round(rp.eta_ideal, 4)}
        row["pass"] = all(abs(row[k]["rel_err_pct"]) <= 15.0 for k in ("KT", "KQ", "eta0"))
        table.append(row)
    items.append({
        "id": "C6", "name": "对参考实现的三点对账（K_T/K_Q/η0，判据 15%）",
        "table": table,
        "pass": all(r["pass"] for r in table),
    })

    verdict = "MOCK" if not all(i["pass"] for i in items) else "REAL"
    return {"object": "O1 /api/v1/propeller-design/wageningen-b（线上端点）",
            "claimed": "Wageningen B系列回归公式", "items": items,
            "n_pass": sum(1 for i in items if i["pass"]), "n_items": len(items),
            "verdict": verdict}


def grade_o2() -> Dict:
    """O2 仓内方法的系数表是否为真 Oosterveld 表。"""
    items = []
    # C2' 结构性：CT 表前 15 项 = CQ 表前 15 项 ×10（真表无此关系）
    diffs = [abs(ct[0] / cq[0] - 10.0) for ct, cq in zip(O2_SUSPECT_CT_HEAD, O2_SUSPECT_CQ_HEAD)]
    items.append({
        "id": "T1", "name": "K_T 与 K_Q 系数表是否逐项相差固定因子 10",
        "got": {"max_abs_dev_from_10": max(diffs), "sample_ratio": round(O2_SUSPECT_CT_HEAD[0][0] / O2_SUSPECT_CQ_HEAD[0][0], 6)},
        "pass": max(diffs) > 1e-6,
        "note": "max|CT/CQ − 10| = 0 → 两表由同一模板缩放生成，非独立回归",
    })
    # C6' 数值：用该表在 J=0.6, P/D=1.0, Z=4, Ae/Ao=0.70 处求值
    def ev(terms, J, P_D, Ae, Z):
        return sum(c * J ** s * P_D ** t * Ae ** u * Z ** v for c, s, t, u, v in terms)
    kt_fake = ev(O2_SUSPECT_CT_HEAD, 0.6, 1.0, 0.70, 4)
    kq_fake = ev(O2_SUSPECT_CQ_HEAD, 0.6, 1.0, 0.70, 4)
    rk, rq = ref.kt_kq(0.6, 1.0, 0.70, 4)
    items.append({
        "id": "T2", "name": "该表在 J=0.6/P_D=1.0/Z=4/Ae/Ao=0.70 处的 K_T（前 15 项）",
        "got": round(kt_fake, 4), "ref": round(rk, 5),
        "rel_err_pct": None if kt_fake == 0 else round(_rel(kt_fake, rk), 1),
        "pass": kt_fake > 0 and abs(_rel(kt_fake, rk)) <= 15.0,
        "note": "得负推力（真实 B4-70 该点为正推力 0.2256）→ 符号错误，物理上不可能",
    })
    items.append({
        "id": "T3", "name": "该表 K_Q（前 15 项）",
        "got": round(kq_fake, 6), "ref": round(rq, 6), "pass": kq_fake > 0,
        "note": "K_Q 为负——转矩方向错，不可用于任何功率估算",
    })
    return {"object": "O2 backend/methods/propulsion/wageningen.py（仓内方法）",
            "claimed": "Oosterveld & van Oossanen (1975) 回归系数", "items": items,
            "n_pass": sum(1 for i in items if i["pass"]), "n_items": len(items),
            "verdict": "MOCK" if not all(i["pass"] for i in items) else "REAL"}


def grade_o3() -> Dict:
    """O3 基准数据集是否与所声明文献一致。"""
    items = []
    J = O3_DATASET["J"]
    KT = O3_DATASET["KT"]
    KQ = O3_DATASET["KQ"]
    eta = O3_DATASET["eta_0"]

    ratios = [{"J": j, "KT_over_KQ": round(kt / kq, 3)} for j, kt, kq in zip(J, KT, KQ) if kq]
    spread = max(r["KT_over_KQ"] for r in ratios) - min(r["KT_over_KQ"] for r in ratios)
    items.append({
        "id": "D1", "name": "K_T/K_Q 变动幅度（是否常数比伪造签名）",
        "got": round(spread, 3), "ref": "1.32（参考实现 J=0→0.8 的变动幅度）",
        "pass": spread > 1.0, "detail": ratios,
        "note": "该数据集 K_T/K_Q 随 J 下降（10.0→5.56），不呈现 O1 的常数比签名 → 本项 PASS；"
                "但其 J=0 行恰为 K_T=10K_Q，与 O1 同源痕迹",
    })
    # 列内自洽：η0 必须等于 J/(2π)·K_T/K_Q（定义式，无外部基准即可裁）
    selfcons = []
    for j, kt, kq, e in zip(J, KT, KQ, eta):
        if j <= 0 or kq == 0:
            continue
        want = j / (2.0 * math.pi) * kt / kq
        selfcons.append({"J": j, "eta0_listed": e, "eta0_from_KtKq": round(want, 4),
                         "rel_err_pct": round(_rel(e, want), 1)})
    worst_row = max(selfcons, key=lambda r: abs(r["rel_err_pct"]))
    items.append({
        "id": "D2", "name": "列内自洽 η0 = J/(2π)·K_T/K_Q（定义式）",
        "got": worst_row, "pass": abs(worst_row["rel_err_pct"]) <= 1.0, "detail": selfcons,
        "note": f"J={worst_row['J']} 行 η0 列写 {worst_row['eta0_listed']}，而由其 K_T/K_Q 算得 "
                f"{worst_row['eta0_from_KtKq']}（偏差 {worst_row['rel_err_pct']}%）→ 数据录入错误",
    })
    # C6 数值对账
    table = []
    for j, kt, kq, e in zip(J, KT, KQ, eta):
        if j == 0:
            continue
        rk, rq = ref.kt_kq(j, 1.0, 0.70, 4)
        rp = ref.open_water(j, 1.0, 0.70, 4)
        row = {"J": j,
               "KT": {"dataset": kt, "ref": round(rk, 5), "rel_err_pct": round(_rel(kt, rk), 1)},
               "KQ": {"dataset": kq, "ref": round(rq, 6), "rel_err_pct": round(_rel(kq, rq), 1)},
               "eta0": {"dataset": e, "ref": None if rp.eta0 is None else round(rp.eta0, 4),
                        "rel_err_pct": None if rp.eta0 is None else round(_rel(e, rp.eta0), 1)}}
        row["pass"] = all(abs(row[k]["rel_err_pct"]) <= 15.0 for k in ("KT", "KQ") if row[k]["rel_err_pct"] is not None) \
            and (row["eta0"]["rel_err_pct"] is None or abs(row["eta0"]["rel_err_pct"]) <= 15.0)
        table.append(row)
    items.append({
        "id": "D3", "name": "对参考实现的逐点对账（判据 15%）",
        "table": table, "pass": all(r["pass"] for r in table),
        "note": "K_T 在 J=0 处 0.400 vs 参考 0.4547（−12%）；K_Q 与 η0 偏差更大",
    })
    return {"object": "O3 backend/data/test_data/propulsion_wageningen.json（基准数据集）",
            "claimed": "B4-70 / Oosterveld & van Oossanen (1975)", "items": items,
            "n_pass": sum(1 for i in items if i["pass"]), "n_items": len(items),
            "verdict": "MOCK" if not all(i["pass"] for i in items) else "REAL"}


def grade_o4() -> Dict:
    """O4 /calculations/propulsion（主推进链）判级：只用不变量与量级区间，无需外部基准。"""
    items = []
    rows = []
    for s in PROPULSION_SAMPLES:
        n = s["rpm"] / 60.0
        Va = s["V_kn"] * KNOT * (1.0 - 0.20)          # 名义伴流 w=0.20（仅用于量级估计）
        J = Va / (n * s["D"])
        Kt_implied = s["thrust_N"] / (RHO * n * n * s["D"] ** 4)
        Kq_implied = s["torque_Nm"] / (RHO * n * n * s["D"] ** 5)
        C_T = 8.0 * Kt_implied / (math.pi * J * J) if J > 0 else float("nan")
        eta_ideal = 2.0 / (1.0 + math.sqrt(1.0 + C_T)) if C_T > 0 else 0.0
        rows.append({"tag": s["tag"], "J_nominal": round(J, 3),
                     "Kt_implied": round(Kt_implied, 5), "Kq_implied": round(Kq_implied, 6),
                     "eta0_reported": s["open_water_eff"],
                     "eta_ideal_bound": round(eta_ideal, 4),
                     "etaD_reported": s["propulsive_eff"],
                     "etaD_chain": round(s["open_water_eff"] * s["hull_eff"] * s["relative_rot_eff"], 4),
                     "PD_from_2pinQ": round(2 * math.pi * n * s["torque_Nm"], 1),
                     "PD_reported": s["delivered_power_W"]})
    # C7 η0 ≤ 1 且 ≤ 动量理论上界
    bad = [r for r in rows if not (0 < r["eta0_reported"] <= 1.0)]
    items.append({"id": "P1", "name": "0 < η0 ≤ 1（敞水效率的热力学/动量上限）",
                  "got": [r["eta0_reported"] for r in rows], "pass": len(bad) == 0,
                  "note": "η0=1.481、1.252 均 >1 → 输出功率大于输入轴功率，物理不可能"})
    bad2 = [r for r in rows if r["eta0_reported"] > r["eta_ideal_bound"]]
    items.append({"id": "P2", "name": "η0 < η_ideal（同一 K_T/J 下的作动盘上界）",
                  "got": [(r["eta0_reported"], r["eta_ideal_bound"]) for r in rows], "pass": len(bad2) == 0})
    bad3 = [r for r in rows if not (0 < r["etaD_reported"] <= 1.0)]
    items.append({"id": "P3", "name": "0 < 推进效率 η_D ≤ 1",
                  "got": [r["etaD_reported"] for r in rows], "pass": len(bad3) == 0,
                  "note": "η_D=1.629/1.377 >1，且 η_D 应 = η0·η_H·η_R（自洽值见表）"})
    # C9 K_T/K_Q 量级区间（J≈0.5–0.6 时 B 系列应为 K_T≈0.15–0.35, K_Q≈0.02–0.06）
    bad4 = [r for r in rows if not (0.05 <= r["Kt_implied"] <= 0.6)]
    items.append({"id": "P4", "name": "反算 K_T = T/(ρn²D⁴) ∈ [0.05, 0.6]（J≈0.5–0.6 的 B 系列区间）",
                  "got": [r["Kt_implied"] for r in rows], "pass": len(bad4) == 0,
                  "note": "反算 K_T≈0.002，比 B 系列量级小两个数量级 → 推力模型非 K_T 形式"})
    bad5 = [r for r in rows if not (0.005 <= r["Kq_implied"] <= 0.09)]
    items.append({"id": "P5", "name": "反算 K_Q = Q/(ρn²D⁵) ∈ [0.005, 0.09]",
                  "got": [r["Kq_implied"] for r in rows], "pass": len(bad5) == 0})
    # C10 内部恒等式 P_D = 2πnQ
    maxdev = max(abs(_rel(r["PD_from_2pinQ"], r["PD_reported"])) for r in rows)
    items.append({"id": "P6", "name": "P_D = 2πnQ 恒等式（端点内部自洽）",
                  "got": round(maxdev, 3), "pass": maxdev <= 5.0,
                  "note": "此项 PASS 说明端点内部算术自洽，失效点在推力/转矩的物理模型而非算错乘法"})
    return {"object": "O4 /api/v1/calculations/propulsion（主推进链）",
            "claimed": "螺旋桨理论 + 经验公式", "items": items, "table": rows,
            "n_pass": sum(1 for i in items if i["pass"]), "n_items": len(items),
            "verdict": "MOCK" if not all(i["pass"] for i in items) else "REAL"}


def reference_selfcheck() -> Dict:
    """参考侧自检：不变量 + 文献带（band）校验。"""
    items = []
    # R1 单调性
    kt = [ref.kt_kq(j, 1.0, 0.70, 4)[0] for j in [0.05 * i for i in range(1, 21)]]
    items.append({"id": "R1", "name": "K_T 对 J 单调递减（B4-70 P/D=1.0, J=0.05..1.00）",
                  "pass": all(kt[i] > kt[i + 1] for i in range(len(kt) - 1))})
    # R2 动量理论界
    worst = 0.0
    for j in [0.05 * i for i in range(2, 22)]:
        p = ref.open_water(j, 1.0, 0.70, 4)
        if p.eta0 and p.eta_ideal:
            worst = max(worst, p.eta0 / p.eta_ideal)
    items.append({"id": "R2", "name": "max η0/η_ideal < 1（B4-70 P/D=1.0）",
                  "got": round(worst, 4), "pass": worst < 1.0,
                  "note": "实测 ~0.76–0.80，与文献「B 系列桨效率达理想盘效率 75–80%」量级一致"})
    # R3 文献带（band）锚点：B4-70 P/D=1.0
    anchors = {
        "KT_J0": (ref.kt_kq(0.0, 1.0, 0.70, 4)[0], 0.455, 0.02, "K_T(J=0)≈0.455"),
        "J0": (ref.zero_thrust_J(1.0, 0.70, 4), 1.06, 0.03, "J0≈1.06"),
        "eta_max": (max((ref.open_water(j, 1.0, 0.70, 4).eta0 or 0) for j in [0.02 * i for i in range(1, 60)]),
                    0.690, 0.03, "η0,max≈0.69 @J≈0.80"),
    }
    bands = []
    for k, (got, band, tol, label) in anchors.items():
        rel = _rel(got, band)
        bands.append({"anchor": k, "label": label, "got": round(got, 4), "band_center": band,
                      "tol_pct": round(100 * tol, 1), "rel_err_pct": round(rel, 2),
                      "pass": abs(rel) <= 100 * tol})
    items.append({"id": "R3", "name": "文献带锚点（B4-70 P/D=1.0，容差 2–3%）", "bands": bands,
                  "pass": all(b["pass"] for b in bands),
                  "note": "带值取自 B 系列标准手册叙述量级；精确逐点比对需 MARIN 1975 原表，本单未取得 → 用带校验"})
    return {"object": "REF tools/verification/propulsion/b_series_reference.py",
            "claimed": "Oosterveld & van Oossanen (1975) 多项式", "items": items,
            "n_pass": sum(1 for i in items if i["pass"]), "n_items": len(items),
            "verdict": "REAL" if all(i["pass"] for i in items) else "WITHHELD",
            "provenance": ref.provenance()}


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
def make_figures(o1: Dict, o3: Dict, o4: Dict) -> List[Tuple[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    Js = [0.02 * i for i in range(1, 61)]
    out = []

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    specs = [("Kt", "K_T", (lambda p: p.Kt), ref, 1),
             ("Kq", "K_Q", (lambda p: p.Kq), ref, 1),
             ("eta", "eta_0", (lambda p: p.eta0), ref, 1)]
    for ax, (key, ylab, getter, _r, _s) in zip(axes, specs):
        for pd in (0.8, 1.0, 1.2):
            ys = [getter(ref.open_water(j, pd, 0.70, 4)) for j in Js]
            ax.plot(Js, [0 if y is None else y for y in ys], "-", lw=2,
                    label=f"REAL B4-70 P/D={pd}")
        # 端点（P/D=1.0 的实测 5 点）
        sj = [0.0, 0.2, 0.4, 0.6, 0.8]
        smap = {"Kt": "KT", "Kq": "KQ", "eta": "eta0"}[key]
        sy = [next(s[smap] for s in SERVING_SAMPLES if abs(s["J"] - j) < 1e-9 and s["P_D"] == 1.0) for j in sj]
        ax.plot(sj, sy, "o--", color="crimson", lw=1.6, ms=5,
                label="serving endpoint P/D=1.0 (MOCK)")
        if key == "eta":
            bd = [ref.eta_ideal(next(s["KT"] for s in SERVING_SAMPLES
                                     if abs(s["J"] - j) < 1e-9 and s["P_D"] == 1.0), j) for j in sj[1:]]
            ax.plot(sj[1:], bd, ":", color="black", lw=1.2, label="Rankine-Froude bound (serving KT)")
            ax.axhline(0.85, color="grey", ls="--", lw=0.8)
            ax.annotate("hard clamp 0.85", (0.62, 0.855), fontsize=8)
            ax.set_ylim(0, 1.0)
        ax.set_xlabel("advance ratio J = V_A/(nD)")
        ax.set_ylabel(ylab)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    axes[0].set_title("Thrust coefficient: real regression vs serving endpoint")
    axes[1].set_title("Torque coefficient")
    axes[2].set_title("Open-water efficiency vs ideal-disc bound")
    fig.suptitle("Wageningen B-series open-water audit (B4-70, Ae/Ao=0.70, Z=4)", fontsize=11)
    fig.tight_layout()
    p1 = ARTIFACT_DIR / "fig1_kt_kq_eta.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    out.append((str(p1.relative_to(ROOT)), "K_T/K_Q/η0 曲线：真 B 系列回归（实线，P/D=0.8/1.0/1.2）vs 线上端点实测（红色虚线）；右图虚线为 Rankine–Froude 上界，红点在 J=0.4–0.6 越过上界"))

    # 图 2：对账误差条形图
    rows = []
    for r in next(i for i in o1["items"] if i["id"] == "C6")["table"]:
        rows.append((f"serving {r['case']}", [abs(r["KT"]["rel_err_pct"]), abs(r["KQ"]["rel_err_pct"]),
                                              abs(r["eta0"]["rel_err_pct"])]))
    for r in next(i for i in o3["items"] if i["id"] == "D3")["table"]:
        rows.append((f"dataset J={r['J']:.1f}", [abs(r["KT"]["rel_err_pct"]), abs(r["KQ"]["rel_err_pct"]),
                                                 abs(r["eta0"]["rel_err_pct"] or 0)]))
    import numpy as np
    labels = [r[0] for r in rows]
    arr = np.array([r[1] for r in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(11, 4.6))
    x = np.arange(len(labels))
    w = 0.26
    for k, (nm, c) in enumerate(zip(["K_T", "K_Q", "η0"], ["#1f77b4", "#ff7f0e", "#2ca02c"])):
        ax.bar(x + (k - 1) * w, arr[:, k], w, label=nm, color=c)
    ax.axhline(15, color="crimson", ls="--", lw=1.5)
    ax.annotate("15% acceptability limit", (0.02, 16), color="crimson", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("relative error vs Oosterveld reference [%]")
    ax.set_title("Per-quantity error against the reference B-series regression")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p2 = ARTIFACT_DIR / "fig2_error_bars.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    # 图 3：O4 推进链效率不可能值
    import numpy as np
    tags = [r["tag"] for r in o4["table"]]
    x = np.arange(len(tags))
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    ax.bar(x - 0.2, [r["eta0_reported"] for r in o4["table"]], 0.38, label="reported eta0", color="crimson")
    ax.bar(x + 0.2, [r["etaD_reported"] for r in o4["table"]], 0.38, label="reported eta_D", color="darkorange")
    ax.plot(x - 0.2, [r["eta_ideal_bound"] for r in o4["table"]], "k_", ms=22, label="ideal-disc bound on eta0")
    ax.axhline(1.0, color="black", lw=1.2)
    ax.annotate("physical limit 1.0", (0.6, 1.02), fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(tags, fontsize=8)
    ax.set_ylabel("efficiency [-]")
    ax.set_title("/api/v1/calculations/propulsion: efficiencies above unity")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p3 = ARTIFACT_DIR / "fig3_propulsion_chain_eff.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    _ = o3
    out.append((str(p3.relative_to(ROOT)), "主推进链端点回报的 η0 与 η_D 均大于 1（红/橙柱），黑短横为同工况作动盘上界"))
    out.append((str(p2.relative_to(ROOT)), "逐量误差：线上端点与基准数据集对参考回归式的相对误差（红线=15% 判据），K_Q 与 η0 普遍越界"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="复探线上端点（失败则退回固化样本）")
    args = ap.parse_args()

    samples = SERVING_SAMPLES
    source = "captured"
    if args.live:
        got = _live_probe()
        if got is not None:
            samples, source = got, "live"

    r = reference_selfcheck()
    o1 = grade_o1(samples)
    o2 = grade_o2()
    o3 = grade_o3()
    o4 = grade_o4()
    figs = make_figures(o1, o3, o4)

    summary = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "loadavg": Path("/proc/loadavg").read_text().split()[:3],
        "evidence_source": source,
        "captured_at": CAPTURED_AT,
        "reference": ref.provenance(),
        "verdicts": {k: v["verdict"] for k, v in
                     [("O1_serving_endpoint", o1), ("O2_repo_method", o2),
                      ("O3_benchmark_dataset", o3), ("O4_propulsion_chain", o4),
                      ("REF_reference_impl", r)]},
        "scorecard": {k: f"{v['n_pass']}/{v['n_items']}" for k, v in
                      [("O1_serving_endpoint", o1), ("O2_repo_method", o2),
                       ("O3_benchmark_dataset", o3), ("O4_propulsion_chain", o4),
                      ("REF_reference_impl", r)]},
        "objects": [r, o1, o2, o3, o4],
        "figures": [{"path": p, "caption": c} for p, c in figs],
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / "propulsion_ow_verification.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(json.dumps({k: summary[k] for k in ("verdicts", "scorecard", "evidence_source")},
                     ensure_ascii=False, indent=2))
    print(f"\n产物: {out.relative_to(ROOT)}")
    for p, _ in figs:
        print(f"      {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())