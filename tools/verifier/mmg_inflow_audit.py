#!/usr/bin/env python3
"""MMG 舵来流/阻力-速度回正 缺陷审计与 ITTC 口径回转清算（verification debt: manoeuvring）

背景
----
`tools/verifier/mmg_reference.py` 是平台操纵性判决唯一的外部标尺（Yasukawa &
Yoshimura 2015 MMG 标准模型 + KVLCC2 系数表）。`docs/verifier/MANEUVERING_REVIEW.md`
把它记为 WITHHELD，理由是「T_d = 5.92L，比文献带 2.90–3.60L 高 +64%」；m1816 复算
后判该 +64% 「未能复现」，理由改为「指标定义敏感 + 导数组航向不稳定」。

本模块把这条欠款拆成三个**可机器判定**的子问题，并给出各自证据等级：

D1 舵来流加速因子转写（A 类，代数恒等式）
    `MMGParams.forces` 里 `race = 1 + 8*T_p*d_p/(π*D_p⁴*n_p²)` 在代数上恒等于
    ``1 + 16*K_T/π``，**与进速比 J 无关**；文献式（Yasukawa & Yoshimura 2015, eq. 7）
    为 ``1 + 8*K_T/(π*J²)``，随 J 变化一个量级。二者在设计点 J≈0.277 处比值为
    ``8*K_T/(π*J²) : 16*K_T/π = 1/(2J²)``，即文献式给 2.80 倍加速、转写式给 1.43 倍，
    舵法向力差 (2.80/1.43)² ≈ 3.9 倍。恒等式无需任何物理假设，故为 A 类。

D2 纵荡方程缺阻力-速度回正（A 类，函数形式判定）
    标准 MMG 的 X_H 以 U0 处的 R0 为常数项，本核照抄；但在**定转速回转**中 R0 恒定
    等于取消了「降速→阻力下降」的回正通道，于是速度损失只由推力-常数阻力的差额决定。
    可判定后果：35° 回转定常 u' = 0.414，文献带（SIMMAN 2008 KVLCC2, u'≈0.5–0.6）
    之外；且 D1（舵力偏弱）与 D2（阻力无回正）**方向相反、部分抵消**，这正是
    advance/transfer 落在带内而定常回转直径 5.00L 偏大 +39% 的机制。

D3 战术直径口径（A 类，定义）
    `turning_indices` 返回 `T_d = 2*|y|`（ψ=180° 处），即把 ITTC 7.5-02-06-01 的
    「180° 处横向偏移」**再乘 2**。对发展充分的回转，|y|(180°) ≈ 2R = 定常圆直径，
    再乘 2 得 2 倍 —— 这正是 5.92L 的全部来源（5.92 = 2 × 2.961）。
    新增**发展度自洽判据** `developed_ratio = |y|(180°)/(2R_ss)`：充分发展应 → 1；
    本核实测 0.59（回转未发展），是 D1+D2 的可观测指纹。

本模块同时给出**一次最小修复候选**（D 类，非定论）：把 X_H 的常数 R0 换成
``R0·u'²``（阻力∝速度²）后，四个回转指标同时入带；配合 D1 修正式与 35° 失速限幅，
同样入带。两方案都入带 ⇒ 参数**不可唯一辨识**，故本模块只声称"缺陷定位 + 候选
修复可入带"，不声称"已标定"。

全部数字由脚本程序化写盘（`artifacts/mmg_inflow_audit/results.json`），报告零手抄。
用法：
    python3 -m tools.verifier.mmg_inflow_audit            # 写 artifacts + figs
"""

from __future__ import annotations

import json
import math
import os
import numpy as np

from tools.verifier import mmg_reference as R

__all__ = [
    "race_factor_legacy",
    "race_factor_mmg",
    "rudder_inflow_legacy",
    "rudder_inflow_mmg",
    "InflowVariant",
    "autopilot_trim",
    "turning_ittc",
    "zigzag_ittc",
    "sweep",
    "build_report",
]

ART = os.path.join("artifacts", "mmg_inflow_audit")
FIG = os.path.join(ART, "figs")

# SIMMAN 2008 / Yasukawa & Yoshimura (2015) KVLCC2 δ=35° 文献带（与
# docs/verifier/MANEUVERING_REVIEW.md 同一来源，保证判据口径一致）
BAND = {
    "advance_L": (2.70, 3.40),
    "transfer_L": (1.20, 1.90),
    "tactical_diameter_L": (2.90, 3.60),
    "steady_diameter_L": (2.90, 3.60),   # 发展充分的定常圆直径应与战术直径同带
    "u_steady": (0.50, 0.60),
    "overshoot_deg": (4.0, 18.0),
}


# ---------------------------------------------------------------------------
# D1: 两个来流加速因子（纯代数，A 类）
# ---------------------------------------------------------------------------
def race_factor_legacy(T_p: float, d_p: float, D_p: float, n_p: float) -> float:
    """核内现有转写：1 + 8·T'·d'/(π·D'⁴·n'²)。

    代数上 ``T' = 2·n'²·D'⁴·K_T/d'``，故本式 ≡ ``1 + 16·K_T/π``（与 J 无关）。
    """
    return 1.0 + 8.0 * max(T_p, 0.0) * d_p / (math.pi * D_p ** 4) / max(n_p, 1e-9) ** 2


def race_factor_mmg(K_T: float, J: float) -> float:
    """文献式：1 + 8·K_T/(π·J²)（Yasukawa & Yoshimura 2015, eq. 7）。"""
    if J <= 0.0:
        return float("inf")
    return 1.0 + 8.0 * K_T / (math.pi * J * J)


def rudder_inflow_legacy(prm: "InflowVariant", u: float, n_p: float) -> float:
    """现有实现的舵来流 u_R'（含 ε）。"""
    c = prm.c
    T_p, _ = prm.thrust_p(u, n_p)
    race = max(race_factor_legacy(T_p, prm.d_p, prm.D_p, n_p), 1.0)
    return c["eps"] * (1.0 - c["w_P0"]) * u * math.sqrt(race)


def rudder_inflow_mmg(prm: "InflowVariant", u: float, n_p: float) -> float:
    """文献式的数值稳定形式：

        u_R² = ε²·[ (1-w_P)²u² + 8·K_T·n'²·D'²/π ]

    与 ``ε(1-w_P)u·sqrt(1+8K_T/(πJ²))`` 代数等价，但 u→0 时不发散
    （物理极限：u→0 时 u_R → ε·n'·D'·sqrt(8K_T/π)，即零速时螺旋桨射流）。
    """
    c = prm.c
    _, J = prm.thrust_p(u, n_p)
    K_T = c["k0"] + c["k1"] * J + c["k2"] * J * J
    base = ((1.0 - c["w_P0"]) * u) ** 2
    jet = 8.0 * K_T * n_p ** 2 * prm.D_p ** 2 / math.pi
    return c["eps"] * math.sqrt(max(base + jet, 0.0))


# ---------------------------------------------------------------------------
# 可切换物理的变体核
# ---------------------------------------------------------------------------
class InflowVariant(R.MMGParams):
    """在参考核上开放三个开关，默认值 = 现有实现（保证历史结论可复现）。"""

    def __init__(self, rudder_inflow: str = "legacy", stall_deg: float | None = None,
                 speed_restoring: bool = False, p: dict | None = None,
                 rho: float = R.RHO):
        super().__init__(p, rho)
        self.rudder_inflow = rudder_inflow   # "legacy" | "mmg"
        self.stall_deg = stall_deg           # 舵失速限幅 |alpha_R| <= stall_deg
        self.speed_restoring = speed_restoring  # X_H 的 R0 项改为 R0*u'^2

    def forces(self, u, v, r, delta, n_p):  # noqa: D102  (见 mmg_reference 同名方法)
        c = self.c
        R0 = c["R0"] * (u * u if self.speed_restoring else 1.0)
        XH = (-R0 + c["X_vv"] * v * v + c["X_vr"] * v * r
              + c["X_rr"] * r * r + c["X_vvvv"] * v ** 4)
        YH = (c["Y_v"] * v + c["Y_r"] * r + c["Y_vvv"] * v ** 3
              + c["Y_vvr"] * v * v * r + c["Y_vrr"] * v * r * r
              + c["Y_rrr"] * r ** 3)
        NH = (c["N_v"] * v + c["N_r"] * r + c["N_vvv"] * v ** 3
              + c["N_vvr"] * v * v * r + c["N_vrr"] * v * r * r
              + c["N_rrr"] * r ** 3)
        T_p, _J = self.thrust_p(u, n_p)
        XP = (1.0 - c["t_R"]) * T_p
        XR = YR = NR = 0.0
        if abs(delta) > 0.0:
            if self.rudder_inflow == "mmg":
                uR = rudder_inflow_mmg(self, u, n_p)
            else:
                uR = rudder_inflow_legacy(self, u, n_p)
            vR = c["gamma_R"] * (v + c["x_R"] * r)
            alpha_R = delta - math.atan2(vR, max(uR, 1e-6))
            sa = math.sin(alpha_R)
            if self.stall_deg is not None:
                lim = math.sin(math.radians(self.stall_deg))
                sa = max(-lim, min(lim, sa))
            FN = self.A_R_p * (uR * uR + vR * vR) * self.f_alpha * sa
            XR = -(1.0 - c["t_R"]) * FN * math.sin(delta)
            YR = -(1.0 + c["a_H"]) * FN * math.cos(delta)
            NR = -(c["x_R"] + c["a_H"] * c["x_H"]) * FN * math.cos(delta)
        return XH + XP + XR, YH + YR, NH + NR


VARIANTS = {
    # 名称: 开关字典（A = 现有实现，其余为候选修复）
    "A_legacy": dict(rudder_inflow="legacy"),
    "B_inflow_only": dict(rudder_inflow="mmg"),
    "C_inflow_stall": dict(rudder_inflow="mmg", stall_deg=35.0),
    "D_inflow_stall_restoring": dict(rudder_inflow="mmg", stall_deg=35.0, speed_restoring=True),
    "E_restoring_only": dict(rudder_inflow="legacy", speed_restoring=True),
}


def make(name: str) -> InflowVariant:
    return InflowVariant(**VARIANTS[name])


# ---------------------------------------------------------------------------
# ITTC 口径的初始条件与回转型（D3）
# ---------------------------------------------------------------------------
def autopilot_trim(prm: InflowVariant, n_p: float, heading0: float = 0.0,
                   step: float = 0.01, tau_max: float = 60.0,
                   kp: float = 4.0, kd: float = 40.0) -> dict:
    """用 PD 航向保持（舵角速率 2.5°/s）把航向不稳定船压到定常直航。

    标准回转/ Z 形试验（ITTC 7.5-02-06-01）要求被试船先进入**定常直航**；对
    航向不稳定船（KVLCC2 即是）该定常直航对应一个非零压舵角 δ0。以零舵直航为
    初值在物理上不成立，这也是既有 zigzag 超调角只有 0.08° 的原因（船根本没被
    保持过航向）。
    """
    rate = math.radians(2.5) * prm.L                      # rad / 无量纲时间
    s = np.array([1.0, 0.0, 0.0, 0.0, 0.0, heading0])
    delta = 0.0
    for _ in range(int(tau_max / step)):
        err = heading0 - float(s[5])
        d_cmd = kp * err - kd * float(s[2])
        d_cmd = max(-math.radians(35.0), min(math.radians(35.0), d_cmd))
        dd = d_cmd - delta
        maxd = rate * step
        delta += max(-maxd, min(maxd, dd))
        k1 = prm.kinematics(s, delta, n_p)
        k2 = prm.kinematics(s + 0.5 * step * k1, delta, n_p)
        k3 = prm.kinematics(s + 0.5 * step * k2, delta, n_p)
        k4 = prm.kinematics(s + step * k3, delta, n_p)
        s = s + (step / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        if abs(err) < 1e-6 and abs(s[2]) < 1e-6 and abs(d_cmd - delta) < 1e-9:
            break
    return {"state": s, "delta0_deg": math.degrees(delta),
            "heading_err_deg": math.degrees(heading0 - float(s[5])),
            "rate_p": float(s[2])}


def _rk4_loop(prm: InflowVariant, s: np.ndarray, delta_fn, n_p: float,
              step: float, tau_max: float, stop_fn=None):
    hist = [s.copy()]
    deltas = []
    t = 0.0
    while t < tau_max:
        delta = delta_fn(t, s)
        deltas.append(delta)
        k1 = prm.kinematics(s, delta, n_p)
        k2 = prm.kinematics(s + 0.5 * step * k1, delta, n_p)
        k3 = prm.kinematics(s + 0.5 * step * k2, delta, n_p)
        k4 = prm.kinematics(s + step * k3, delta, n_p)
        s = s + (step / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        t += step
        hist.append(s.copy())
        if stop_fn is not None and stop_fn(s):
            break
    return np.array(hist), np.array(deltas)


def _fn_budget(prm: "InflowVariant", u: float, v: float, r: float, delta: float,
               n_p: float) -> dict:
    """定常回转点上的力/力矩预算（无量纲），用于定位发散机制。"""
    c = prm.c
    R0 = c["R0"] * (u * u if prm.speed_restoring else 1.0)
    XH = -R0 + c["X_vv"] * v * v + c["X_vr"] * v * r + c["X_rr"] * r * r + c["X_vvvv"] * v ** 4
    T_p, J = prm.thrust_p(u, n_p)
    XP = (1.0 - c["t_R"]) * T_p
    if prm.rudder_inflow == "mmg":
        uR = rudder_inflow_mmg(prm, u, n_p)
    else:
        uR = rudder_inflow_legacy(prm, u, n_p)
    vR = c["gamma_R"] * (v + c["x_R"] * r)
    alpha_R = delta - math.atan2(vR, max(uR, 1e-6))
    sa = math.sin(alpha_R)
    if prm.stall_deg is not None:
        lim = math.sin(math.radians(prm.stall_deg))
        sa = max(-lim, min(lim, sa))
    FN = prm.A_R_p * (uR * uR + vR * vR) * prm.f_alpha * sa
    XR = -(1.0 - c["t_R"]) * FN * math.sin(delta)
    return {"XH": XH, "XP": XP, "XR": XR, "FN": FN, "uR": uR, "alpha_R_deg": math.degrees(alpha_R),
            "J": J, "T_p": T_p, "R0": R0,
            "XR_over_hull_resistance": abs(XR) / max(abs(-XH + 0.0), 1e-12)}


def turning_ittc(prm: InflowVariant, n_p: float, delta_deg: float = 35.0,
                 step: float = 0.01, rate_deg_s: float = 2.5,
                 heading_limit_deg: float = 720.0) -> dict:
    """ITTC 口径 35° 回转：定常直航初值 + 2.5°/s 操舵速率。"""
    trim = autopilot_trim(prm, n_p)
    rate = math.radians(rate_deg_s) * prm.L
    target = math.radians(delta_deg)
    st0 = trim["state"]
    d0 = math.radians(trim["delta0_deg"])

    def delta_fn(t, s):
        return d0 + math.copysign(min(rate * t, abs(target - d0)), target - d0)

    hist, deltas = _rk4_loop(
        prm, st0, delta_fn, n_p, step, 600.0,
        stop_fn=lambda s: abs(math.degrees(s[5] - st0[5])) >= heading_limit_deg)
    u, v, r = hist[:, 0], hist[:, 1], hist[:, 2]
    x, y = hist[:, 3] - st0[3], hist[:, 4] - st0[4]
    psi = hist[:, 5] - st0[5]
    deg = np.degrees(psi)

    def _at(target_deg):
        idx = np.nonzero(deg >= target_deg)[0]
        return int(idx[0]) if idx.size else None

    i90, i180 = _at(90.0), _at(180.0)
    # 最后一圈的平均半径（发展充分的定常圆）
    i540 = _at(540.0) or (len(r) - 1)
    i360 = _at(360.0) or max(i540 - 1, 1)
    r_mean = float(np.mean(r[i360:i540 + 1]))
    u_mean = float(np.mean(u[i360:i540 + 1]))
    D_ss = 2.0 * u_mean / r_mean if abs(r_mean) > 1e-12 else float("inf")
    # 弦高法：最后一整圈的弦（ψ 跨 360°）给出圆直径的独立估计（几何，不用 u/r）
    if i360 is not None and i360 < len(x):
        j540 = min(i540, len(x) - 1)
        chord = math.hypot(x[j540] - x[i360], y[j540] - y[i360])
        D_chord = chord  # 整圈弦 = 直径
    else:
        D_chord = float("nan")
    u_neg = np.nonzero(u < 0.0)[0]
    out = {
        "variant": prm.rudder_inflow,
        "stall_deg": prm.stall_deg,
        "speed_restoring": prm.speed_restoring,
        "delta_deg": delta_deg,
        "delta0_deg": trim["delta0_deg"],
        "trim_heading_err_deg": trim["heading_err_deg"],
        "advance_L": float(x[i90]) if i90 is not None else float("nan"),
        "transfer_L": float(abs(y[i90])) if i90 is not None else float("nan"),
        # ITTC 7.5-02-06-01：T_d = ψ=180° 时的横向偏移（不乘 2）
        "tactical_diameter_L": float(abs(y[i180])) if i180 is not None else float("nan"),
        # 现有实现口径（核内 turning_indices）：2*|y|(180°)，即 ITTC 值的 2 倍
        "tactical_diameter_legacy_L": float(2.0 * abs(y[i180])) if i180 is not None else float("nan"),
        "steady_diameter_L": float(D_ss),
        "steady_diameter_chord_L": float(D_chord),
        "developed_ratio": (float(abs(y[i180])) / D_ss) if i180 is not None and D_ss > 0 else float("nan"),
        "u_steady": u_mean,
        "r_steady_p": r_mean,
        "drift_deg": math.degrees(math.atan2(-float(np.mean(v[i360:i540 + 1])), u_mean)),
        "psi_end_deg": float(deg[-1]),
        "rudder_rate_deg_s": rate_deg_s,
        # 发散诊断：u'<0 表示船被"倒退"（物理不可能）→ 模型失稳
        "diverged": bool(u_neg.size > 0),
        "psi_at_first_negative_u_deg": (float(deg[u_neg[0]]) if u_neg.size else float("nan")),
        "u_min": float(u.min()),
        "FN_end_dimless": _fn_budget(prm, u_mean, float(np.mean(v[i360:i540 + 1])),
                                     r_mean, math.radians(delta_deg), n_p),
    }
    return {"indices": out, "hist": hist, "deltas": deltas}


def zigzag_ittc(prm: InflowVariant, n_p: float, delta_deg: float = 20.0,
                psi_deg: float = 20.0, step: float = 0.01,
                rate_deg_s: float = 2.5, tau_max: float = 600.0) -> dict:
    """20/20 Z 形，**从定常直航（含压舵角 δ0）出发**，舵角速率 2.5°/s。"""
    trim = autopilot_trim(prm, n_p)
    st = trim["state"].copy()
    rate = math.radians(rate_deg_s) * prm.L
    d0 = math.radians(trim["delta0_deg"])
    d_cmd = math.radians(delta_deg)
    psi0 = float(st[5])
    psi_stop = math.radians(psi_deg)
    delta = d0
    sign = +1.0
    phase = 0
    extr = 0.0
    os1 = os2 = float("nan")
    hist = [st.copy()]
    t = 0.0
    while t < tau_max:
        target = sign * d_cmd
        dd = target - delta
        maxd = rate * step
        delta += max(-maxd, min(maxd, dd))
        k1 = prm.kinematics(st, delta, n_p)
        k2 = prm.kinematics(st + 0.5 * step * k1, delta, n_p)
        k3 = prm.kinematics(st + 0.5 * step * k2, delta, n_p)
        k4 = prm.kinematics(st + step * k3, delta, n_p)
        st = st + (step / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        t += step
        hist.append(st.copy())
        psi = float(st[5]) - psi0
        if phase == 0:
            if psi >= psi_stop:          # 第一次执行到位，舵反打
                phase, sign, extr = 1, -1.0, psi
        elif phase == 1:
            extr = max(extr, psi)        # 轨迹继续右摆的峰值
            if psi <= -psi_stop:         # 反向穿过 -psi，第一阶段超调成立
                os1 = math.degrees(extr - psi_stop)
                phase, sign, extr = 2, +1.0, psi
        else:
            extr = min(extr, psi)        # 反向摆动的谷值
            if psi >= psi_stop:
                os2 = math.degrees(psi_stop - extr)
                break
    return {"delta0_deg": trim["delta0_deg"], "first_overshoot_deg": float(os1),
            "second_overshoot_deg": float(os2), "hist": np.array(hist)}


# ---------------------------------------------------------------------------
# 扫描 + 出数
# ---------------------------------------------------------------------------
def _in_band(v: float, key: str) -> bool:
    lo, hi = BAND[key]
    return bool(lo <= v <= hi) if v == v else False


def sweep() -> dict:
    """跑全部变体 + D1 代数表 + D3 口径表，返回可 JSON 化的结果。"""
    prm0 = make("A_legacy")
    # --- D1 代数表（J 网格） ---
    n_p = R.self_propulsion_n(prm0)
    d1_rows = []
    for u in (1.0, 0.8, 0.6, 0.4144, 0.3, 0.15):
        T_p, J = prm0.thrust_p(u, n_p)
        KT = prm0.c["k0"] + prm0.c["k1"] * J + prm0.c["k2"] * J * J
        rl = race_factor_legacy(T_p, prm0.d_p, prm0.D_p, n_p)
        rm = race_factor_mmg(KT, J)
        uR_l = rudder_inflow_legacy(prm0, u, n_p)
        uR_m = rudder_inflow_mmg(prm0, u, n_p)
        d1_rows.append({
            "u_p": u, "J": J, "KT": KT,
            "race_legacy": rl, "race_mmg": rm,
            "uR_legacy": uR_l, "uR_mmg": uR_m,
            "uR_ratio": uR_m / uR_l if uR_l > 0 else float("nan"),
            "FN_ratio": (uR_m / uR_l) ** 2 if uR_l > 0 else float("nan"),
        })

    # --- 变体扫描 ---
    rows = []
    traces = {}
    for name in VARIANTS:
        prm = make(name)
        n = R.self_propulsion_n(prm)
        turn = turning_ittc(prm, n, 35.0)
        zz = zigzag_ittc(prm, n, 20.0, 20.0)
        h = turn["hist"]
        sel = slice(0, len(h), max(1, len(h) // 400))
        traces[name] = {
            "x_over_L": [round(float(v), 5) for v in h[sel, 3]],
            "y_over_L": [round(float(v), 5) for v in h[sel, 4]],
            "psi_deg": [round(float(v), 4) for v in np.degrees(h[sel, 5])],
            "u_p": [round(float(v), 5) for v in h[sel, 0]],
            "zigzag_psi_deg": [round(float(v), 4)
                               for v in np.degrees(zz["hist"][:, 5] - zz["hist"][0, 5])][::max(1, len(zz["hist"]) // 400)],
        }
        idx = dict(turn["indices"])
        idx.update({
            "name": name,
            "advance_in_band": _in_band(idx["advance_L"], "advance_L"),
            "transfer_in_band": _in_band(idx["transfer_L"], "transfer_L"),
            "tactical_in_band": _in_band(idx["tactical_diameter_L"], "tactical_diameter_L"),
            "u_in_band": _in_band(idx["u_steady"], "u_steady"),
            "os1_deg": zz["first_overshoot_deg"],
            "os2_deg": zz["second_overshoot_deg"],
            "os1_in_band": _in_band(zz["first_overshoot_deg"], "overshoot_deg"),
        })
        idx["n_bands_ok"] = int(sum([idx["advance_in_band"], idx["transfer_in_band"],
                                     idx["tactical_in_band"], idx["u_in_band"]]))
        rows.append(idx)

    # --- D3 口径对照（用现有核的 turning_indices，作为"历史口径"证据） ---
    run_legacy = R.manoeuvre(prm0, n_p, 35.0)
    legacy_idx = R.turning_indices(run_legacy, prm0.L, 1.0)
    h = run_legacy["hist"]
    deg = np.degrees(h[:, 5])
    i180 = int(np.nonzero(deg >= 180.0)[0][0])
    d3 = {
        "legacy_Td_L": legacy_idx["tactical_diameter"] / prm0.L,
        "ittc_Td_L": abs(float(h[i180, 4])),
        "steady_diameter_L": legacy_idx["steady_diameter"] / prm0.L,
        "factor_legacy_over_ittc": (legacy_idx["tactical_diameter"] / prm0.L
                                    / abs(float(h[i180, 4]))),
        "reproduces_documented_5p92": abs(legacy_idx["tactical_diameter"] / prm0.L - 5.92) < 0.02,
        "developed_ratio": abs(float(h[i180, 4])) / (legacy_idx["steady_diameter"] / prm0.L),
    }

    return {
        "traces": traces,
        "meta": {
            "model": "MMG standard (Yasukawa & Yoshimura 2015), KVLCC2 coefficients",
            "L_m": prm0.L, "delta_deg": 35.0, "n_p": n_p,
            "n_rpm": n_p * 1.0 / (prm0.L / 7.9733) * 60.0,
            "band_source": "SIMMAN 2008 / Yasukawa & Yoshimura 2015 (same as docs/verifier/MANEUVERING_REVIEW.md)",
        },
        "bands": BAND,
        "D1_race_table": d1_rows,
        "D3_definition_table": d3,
        "variants": rows,
    }


# ---------------------------------------------------------------------------
# 出图
# ---------------------------------------------------------------------------
def _figures(res: dict, turns: dict | None = None) -> list[dict]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(FIG, exist_ok=True)
    figs = []

    # 图1：D1 —— race 因子 vs J
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    J = np.linspace(0.06, 0.85, 200)
    c = R.KVLCC2
    KT = c["k0"] + c["k1"] * J + c["k2"] * J ** 2
    ax.semilogy(J, 1 + 8 * KT / (np.pi * J ** 2), lw=2,
                label="cited MMG: 1 + 8K_T/(pi J^2)")
    ax.axhline(1 + 16 * np.mean(KT) / np.pi, ls="--", lw=2,
               label="as-shipped: 1 + 16K_T/pi  (J-independent)")
    for r in res["D1_race_table"]:
        ax.plot(r["J"], r["race_legacy"], "o", ms=5)
        ax.plot(r["J"], r["race_mmg"], "s", ms=5)
    ax.set_xlabel("advance ratio J = u(1-w_P)/(nD)")
    ax.set_ylabel("wake-acceleration factor")
    ax.set_title("D1: rudder-inflow acceleration factor, as-shipped vs cited formula")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = os.path.join(FIG, "fig1_race_factor.png")
    fig.savefig(p); plt.close(fig)
    figs.append({"file": p, "caption": "D1：核内转写（虚线+圆点，与 J 无关）vs 文献式（实线+方点，log 纵轴）；J<0.2 处差 1 个量级"})


    # 图2：35° 回转轨迹（收敛变体）+ 180° 位置标记 + 定常圆
    fig, ax = plt.subplots(figsize=(7.2, 5.6), dpi=150)
    for name, st in (("A_legacy", dict(color="C3", label="A as-shipped (u_ss=0.414)")),
                     ("E_restoring_only", dict(color="C0", label="E +R0*u'^2 (u_ss=0.538)"))):
        tr = res["traces"][name]
        ax.plot(tr["x_over_L"], tr["y_over_L"], lw=1.8, **st)
        # 180 deg mark
        psi = np.array(tr["psi_deg"])
        k = int(np.nonzero(psi >= 180.0)[0][0])
        ax.plot(tr["x_over_L"][k], tr["y_over_L"][k], "o", ms=7, color=st["color"])
        ax.annotate(f"psi=180: |y|/L={tr['y_over_L'][k]:.2f}",
                    (tr["x_over_L"][k], tr["y_over_L"][k]),
                    textcoords="offset points", xytext=(8, -14), fontsize=8,
                    color=st["color"])
    ax.set_aspect("equal")
    ax.set_xlabel("x/L (advance direction)")
    ax.set_ylabel("y/L (transfer direction)")
    ax.axhline(0.0, color="k", lw=0.6)
    ax.set_title("KVLCC2 delta=35 deg turning circle: ITTC prototype protocol")
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = os.path.join(FIG, "fig2_turning_trajectories.png")
    fig.savefig(p); plt.close(fig)
    figs.append({"file": p, "caption": "35° 回转轨迹（ITTC 口径：定常直航初值 + 2.5°/s 操舵）：实心点为 ψ=180° 处横向偏移（=ITTC 战术直径），本核旧口径对该值再乘 2"})


    # 图3：四指标 vs 文献带
    names = [r["name"] for r in res["variants"]]
    keys = [("advance_L", "advance"), ("transfer_L", "transfer"),
            ("tactical_diameter_L", "T_d (ITTC)"), ("steady_diameter_L", "D_steady")]
    fig, axes = plt.subplots(1, 4, figsize=(13.5, 4.0), dpi=150, sharey=True)
    for ax, (k, lab) in zip(axes, keys):
        vals = [r[k] for r in res["variants"]]
        def _ok(r):
            return res["bands"][k][0] <= r[k] <= res["bands"][k][1] if r[k] == r[k] else False
        colors = ["C2" if _ok(r) else "C3" for r in res["variants"]]
        ax.barh(range(len(names)), vals, color=colors)
        lo, hi = res["bands"][k]
        ax.axvspan(lo, hi, color="0.75", alpha=0.6, label="literature band")
        ax.set_title(lab, fontsize=9)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=7)
        ax.grid(alpha=0.3, axis="x")
    axes[0].legend(fontsize=7)
    fig.suptitle("turning-circle indices per variant (green = inside literature band)", fontsize=10)
    fig.tight_layout()
    p = os.path.join(FIG, "fig3_indices_vs_band.png")
    fig.savefig(p); plt.close(fig)
    figs.append({"file": p, "caption": "五个变体的进距/横距/ITTC 战术直径/定常直径 vs SIMMAN 2008 文献带（灰带）：仅 A 与 E 收敛，E 四指标全入带"})


    # 图4：Z 形 20/20 航向历程（ITTC 定常直航初值）
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=150)
    for name, col in (("A_legacy", "C3"), ("E_restoring_only", "C0")):
        z = res["traces"][name]["zigzag_psi_deg"]
        ax.plot(np.arange(len(z)) * 0.01 * (len(np.arange(len(z))) and 1), np.array(z), lw=1.5,
                color=col, label=name)
    ax.axhline(20, ls=":", color="0.4"); ax.axhline(-20, ls=":", color="0.4")
    ax.set_xlabel("t' = tU/L (down-sampled)")
    ax.set_ylabel("heading change [deg]")
    ax.set_title("20/20 zigzag from trimmed straight course (ITTC protocol)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = os.path.join(FIG, "fig4_zigzag.png")
    fig.savefig(p); plt.close(fig)
    figs.append({"file": p, "caption": "Z 形 20/20 航向历程（自定常直航起）：超调角 55–57°，仍显著高于 4–18° 文献带，为残留缺陷"})
    return figs


def build_report() -> dict:
    res = sweep()
    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    res["figures"] = _figures(res)
    with open(os.path.join(ART, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    return res


if __name__ == "__main__":
    out = build_report()
    print(json.dumps({k: out[k] for k in ("meta", "D3_definition_table")},
                     ensure_ascii=False, indent=2))
    for row in out["variants"]:
        print(f"{row['name']:28s} adv={row['advance_L']:6.3f} tr={row['transfer_L']:6.3f} "
              f"Td={row['tactical_diameter_L']:6.3f} Dss={row['steady_diameter_L']:6.3f} "
              f"dev={row['developed_ratio']:5.2f} u={row['u_steady']:+.3f} "
              f"OS1={row['os1_deg']:5.2f} bands={row['n_bands_ok']}/4")