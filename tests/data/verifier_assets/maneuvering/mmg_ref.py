"""独立 MMG 3-DOF 操纵运动参考实现（KVLCC2 级船型）——操纵性域验证基准。

本模块**不依赖平台 :8001 侧任何代码**，用于清偿"操纵性域验证欠账"：
  1. 时域：MMG 标准型 3-DOF 运动方程 + RK4（回转圈 / 10-10 Z 形）；
  2. 代数：稳态回转 X=Y=N=0 的 Newton 解（与时间推进互为独立校验）；
  3. 解析：sway-yaw 2-DOF 线性化闭式解（稳态增益 K、特征根 λ、course-stability 判据 C）；
  4. 舵力：展弦比 Λ=Hr²/Ar 的 Fujii(1960) 饱和式（大舵角不失真，无量纲量纲自洽）。

实现约定（全量纲状态，无量纲导数）：
    u,v [m/s]，r [rad/s]；导数按 v'=v/U0, r'=rL/U0 无量纲；
    力无量纲因子 F0 = 0.5·ρ·L²·T·U0²，力矩因子 M0 = F0·L。
运动方程（MMG 标准，Yasukawa & Yoshimura 2015）：
    (m+m_x) u̇ - (m+m_y) v r = X
    (m+m_y) v̇ + (m+m_x) u r = Y
    (I_zz+J_zz) ṙ            = N
导数表为**声明式标定假设**（C 类，见 calibrate.py / calibration.json）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import root


KNOT = 0.5144


def kvlcc2_params() -> dict:
    """KVLCC2 主尺度 + MMG 导数表（标定假设，逐项标注来源等级）。"""
    return {
        "ship": {"name": "KVLCC2", "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098,
                 "disp_m3": 312622.0, "rho": 1025.0,
                 "Dp": 9.86, "Ar": 112.5, "Hr": 14.0, "U0_kn": 15.5},
        "added_mass": {"m_x": 0.022, "m_y": 0.223, "J_zz": 0.0140, "k_zz": 0.25},
        "hull_deriv": {
            "R0": 0.0092,
            "X_vv": -0.045, "X_vr": 0.020, "X_rr": 0.005,
            "Y_v": -0.300, "Y_r": 0.050,
            "Y_vvv": -1.500, "Y_vvr": 0.400, "Y_vrr": -0.400, "Y_rrr": 0.050,
            "N_v": -0.120, "N_r": -0.120,
            "N_vvv": 0.050, "N_vvr": -0.200, "N_vrr": -0.050, "N_rrr": -0.020,
        },
        "propeller": {"t": 0.22, "w_P": 0.35, "k0": 0.28, "k1": -0.30, "k2": -0.15},
        "rudder": {"t_R": 0.30, "a_H": 0.18, "x_R": -0.50, "x_H": -0.45,
                   "eps": 0.90, "gamma_R": 0.40},
        "source_notes": {
            "ship": "KVLCC2 公开主尺度；自洽校验 Cb·L·B·T = 312633 m³ vs ∇ = 312622 m³",
            "added_mass": "k_zz = 0.25L 假设 → I'_zz = k'²·m' = 0.0184 自洽",
            "hull_deriv": "C 类标定（calibrate.py）：量级对齐 full-form tanker 导数带，"
                          "标定目标=战术直径≈3L、10/10 首超越≈15°、course-stable",
            "propeller": "B 类：K_T(J) 二次拟合 + 推力减额 t + 伴流 w_P",
            "rudder": "MMG 舵干扰系数 + Fujii(1960) C_N = 6.13Λ/(2.25+Λ)·sinα",
        },
    }


@dataclass
class MMGRef:
    p: dict = field(default_factory=kvlcc2_params)
    rudder_rate_deg_s: float = 2.5

    def __post_init__(self):
        s = self.p["ship"]
        self.L, self.B, self.T = s["L"], s["B"], s["T"]
        self.Cb, self.rho = s["Cb"], s["rho"]
        self.Ar, self.Hr, self.Dp = s["Ar"], s["Hr"], s["Dp"]
        self.U0 = s["U0_kn"] * KNOT
        self.VOL = self.Cb * self.L * self.B * self.T
        # 各因子由"质量-力-力矩"链条自洽确定：Mass0·U0²/L 必须等于力因子
        self.Mass0 = 0.5 * self.rho * self.L ** 2 * self.T               # 质量因子 kg
        self.F0 = self.Mass0 * self.U0 ** 2 / self.L                     # 力因子 N
        self.M0 = self.F0 * self.L                                       # 力矩因子 N·m
        self.Inert0 = self.Mass0 * self.L ** 2                           # 惯量因子 kg·m²
        am = self.p["added_mass"]
        self.m_p = self.VOL * self.rho / self.Mass0                      # m'
        self.m = self.VOL * self.rho                                     # kg
        self.m_x = am["m_x"] * self.Mass0
        self.m_y = am["m_y"] * self.Mass0
        self.I_zz = am["k_zz"] ** 2 * self.m_p * self.Inert0
        self.J_zz = am["J_zz"] * self.Inert0
        self.d, self.pr, self.ru = self.p["hull_deriv"], self.p["propeller"], self.p["rudder"]
        self.Lambda_R = self.Hr ** 2 / self.Ar
        self.n_rps = self._solve_selfprop_n()

    # ------------------------------------------------------------ 螺旋桨/舵
    def thrust(self, u, n=None):
        n = self.n_rps if n is None else n
        J = u * (1 - self.pr["w_P"]) / (n * self.Dp) if n > 0 else 0.0
        kt = self.pr["k0"] + self.pr["k1"] * J + self.pr["k2"] * J * J
        return self.rho * n * n * self.Dp ** 4 * kt, J, kt

    def _solve_selfprop_n(self):
        R = self.d["R0"] * self.F0
        f = lambda z: (1 - self.pr["t"]) * self.thrust(self.U0, z[0])[0] - R
        return root(f, [1.4]).x[0]

    def _rudder_flow(self, u, v, r):
        _, J, kt = self.thrust(u)
        w_P = self.pr["w_P"]
        race = 1.0 + 8.0 * kt / (math.pi * J * J) if J > 1e-6 and kt > 0 else 1.0
        u_R = self.ru["eps"] * u * (1 - w_P) * math.sqrt(max(race, 1.0))
        v_R = self.ru["gamma_R"] * (v + self.ru["x_R"] * self.L * r)
        return u_R, v_R

    def rudder_force(self, u, v, r, delta):
        """舵法向力 F_N [N] 与有效攻角 α [rad]（Fujii 展弦比饱和式）。"""
        u_R, v_R = self._rudder_flow(u, v, r)
        UR = math.hypot(u_R, v_R)
        if UR < 1e-6:
            return 0.0, 0.0
        alpha = delta - math.atan2(v_R, u_R)
        alpha = max(min(alpha, math.pi / 2), -math.pi / 2)
        C_N = 6.13 * self.Lambda_R / (2.25 + self.Lambda_R) * math.sin(alpha)
        return 0.5 * self.rho * self.Ar * UR * UR * C_N, alpha

    # ------------------------------------------------------------ 运动方程
    def forces(self, u, v, r, delta):
        d, ru = self.d, self.ru
        vp, rp = v / self.U0, r * self.L / self.U0
        # 阻力随合速度平方变化（R0 由 U0 标定），否则减速时阻力被人为冻结
        q = (u * u + v * v) / self.U0 ** 2
        XH = self.F0 * (-d["R0"] * q + d["X_vv"] * vp ** 2 + d["X_vr"] * vp * rp + d["X_rr"] * rp ** 2)
        YH = self.F0 * (d["Y_v"] * vp + d["Y_r"] * rp + d["Y_vvv"] * vp ** 3
                        + d["Y_vvr"] * vp * vp * rp + d["Y_vrr"] * vp * rp ** 2 + d["Y_rrr"] * rp ** 3)
        NH = self.M0 * (d["N_v"] * vp + d["N_r"] * rp + d["N_vvv"] * vp ** 3
                        + d["N_vvr"] * vp * vp * rp + d["N_vrr"] * vp * rp ** 2 + d["N_rrr"] * rp ** 3)
        XP = (1 - self.pr["t"]) * self.thrust(u)[0]
        F_N, _ = self.rudder_force(u, v, r, delta)
        s, c = math.sin(delta), math.cos(delta)
        XR = -(1 - ru["t_R"]) * F_N * s
        YR = -(1 + ru["a_H"]) * F_N * c
        NR = -(ru["x_R"] + ru["a_H"] * ru["x_H"]) * self.L * F_N * c
        return XH + XP + XR, YH + YR, NH + NR

    def rhs(self, t, x, delta):
        u, v, r = x
        X, Y, N = self.forces(u, v, r, delta)
        mx_tot, my_tot = self.m + self.m_x, self.m + self.m_y
        Iz = self.I_zz + self.J_zz
        return np.array([(X + my_tot * v * r) / mx_tot,
                         (Y - mx_tot * u * r) / my_tot,
                         N / Iz])

    # ------------------------------------------------------------ 时域积分
    def run(self, delta_deg=35.0, t_end_s=900.0, dt_s=0.25, u0=None,
            rudder_rate=None, mode="turning", heading_deg=None):
        rate = self.rudder_rate_deg_s if rudder_rate is None else rudder_rate
        dTgt = math.radians(delta_deg)
        head = math.radians(heading_deg if heading_deg is not None else delta_deg)
        x = np.array([self.U0 if u0 is None else u0, 0.0, 0.0])
        psi = xp = yp = 0.0
        delta = 0.0
        n_steps = int(t_end_s / dt_s) + 1
        keys = ("t", "u", "v", "r", "psi", "x", "y", "delta")
        out = {k: np.empty(n_steps) for k in keys}
        side = 1.0
        for i in range(n_steps):
            t = i * dt_s
            if mode == "turning":
                delta = math.copysign(min(abs(dTgt), math.radians(rate) * t), dTgt) if rate > 0 else dTgt
            else:  # zigzag
                if delta == 0.0:
                    delta = side * dTgt
                if side > 0 and psi >= head:
                    side, delta = -1.0, -dTgt
                elif side < 0 and psi <= -head:
                    side, delta = 1.0, dTgt
            k1 = self.rhs(t, x, delta)
            k2 = self.rhs(t, x + 0.5 * dt_s * k1, delta)
            k3 = self.rhs(t, x + 0.5 * dt_s * k2, delta)
            k4 = self.rhs(t, x + dt_s * k3, delta)
            x = x + dt_s * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
            u, v, r = x
            xp += (u * math.cos(psi) - v * math.sin(psi)) * dt_s
            yp += (u * math.sin(psi) + v * math.cos(psi)) * dt_s
            psi += r * dt_s
            out["t"][i], out["psi"][i] = t, psi
            out["x"][i], out["y"][i] = xp, yp
            out["u"][i], out["v"][i], out["r"][i], out["delta"][i] = u, v, r, delta
        return out

    # ------------------------------------------------------------ 稳态代数解
    def steady_state(self, delta_deg, guess=(0.9, -0.6, 0.02)):
        delta = math.radians(delta_deg)
        sol = root(lambda z: self.rhs(0.0, z, delta), np.array(guess, float), tol=1e-11)
        u, v, r = sol.x
        return {"u": u, "v": v, "r": r, "ok": bool(sol.success),
                "r_deg_s": math.degrees(r), "beta_deg": math.degrees(math.atan2(-v, u)),
                "u_prime": u / self.U0, "r_prime": r * self.L / self.U0,
                "residual": float(np.linalg.norm(self.rhs(0.0, sol.x, delta)))}

    # ------------------------------------------------------------ 线性闭式
    def linear_model(self):
        """sway-yaw 2-DOF 线性化：A、b、稳态增益 K、特征根、course-stability 判据。"""
        d, ru = self.d, self.ru
        u0 = self.U0
        C_Na = (6.13 * self.Lambda_R / (2.25 + self.Lambda_R)) * 0.5 * self.rho * self.Ar \
            * self._rudder_flow(u0, 0, 0)[0] ** 2          # ∂F_N/∂α [N/rad]
        sgn = -1.0                                          # Y_R = -(1+a_H) F_N cosδ
        Y_d = sgn * (1 + ru["a_H"]) * C_Na
        N_d = -(ru["x_R"] + ru["a_H"] * ru["x_H"]) * self.L * C_Na
        kv = ru["gamma_R"]
        uR = self._rudder_flow(u0, 0.0, 0.0)[0]             # 舵处合流速度 [m/s]
        # α = δ − v_R/u_R，v_R = γ_R(v + x_R L r) → ∂α/∂v = −γ_R/u_R（量纲 1/(m/s)）
        dyv = sgn * (1 + ru["a_H"]) * C_Na * (-kv / uR)
        dnv = -(ru["x_R"] + ru["a_H"] * ru["x_H"]) * self.L * C_Na * (-kv / uR)
        Y_v = self.F0 * d["Y_v"] / self.U0 + dyv
        Y_r = self.F0 * d["Y_r"] * self.L / self.U0 + dyv * ru["x_R"] * self.L
        N_v = self.M0 * d["N_v"] / self.U0 + dnv
        N_r = self.M0 * d["N_r"] * self.L / self.U0 + dnv * ru["x_R"] * self.L
        mx_tot, my_tot = self.m + self.m_x, self.m + self.m_y
        Iz = self.I_zz + self.J_zz
        A = np.array([[Y_v / my_tot, (Y_r - mx_tot * u0) / my_tot],
                      [N_v / Iz, N_r / Iz]])
        b = np.array([Y_d / my_tot, N_d / Iz])
        x_ss = -np.linalg.solve(A, b)                       # δ=1 rad 时的稳态 (v,r)
        lam = np.linalg.eigvals(A)
        K = x_ss[1]                                         # r/δ [1/(s·rad)]
        D = Y_v * N_r - N_v * (Y_r - mx_tot * u0)           # 特征方程常数项 × my·Iz
        return {"A": A, "b": b, "Y_v": Y_v, "N_v": N_v, "Y_r": Y_r, "N_r": N_r,
                "Y_delta": Y_d, "N_delta": N_d, "C_Na": C_Na,
                "v_ss_prime": x_ss[0] / u0, "r_ss_prime": x_ss[1] * self.L / u0,
                "K_1s": K, "eig": lam, "D_stability": float(D),
                "C_positive": bool(D > 0),
                "T_dom_s": float(-1.0 / np.real(lam[np.argmax(np.real(lam))]))}

    # ------------------------------------------------------------ 指标
    def turning_indices(self, run):
        psi, x, y, t = np.degrees(run["psi"]), run["x"], run["y"], run["t"]
        res = {"advance_L": float("nan"), "transfer_L": float("nan"),
               "tactical_diameter_L": float("nan")}
        i90 = np.where(psi >= 90)[0]
        if len(i90):
            res["advance_L"], res["transfer_L"] = x[i90[0]] / self.L, abs(y[i90[0]]) / self.L
        i180 = np.where(psi >= 180)[0]
        if len(i180):
            res["tactical_diameter_L"] = 2 * abs(y[i180[0]]) / self.L
        res.update({"steady_r_deg_s": math.degrees(abs(run["r"][-1])),
                    "steady_u_kn": run["u"][-1] / KNOT,
                    "D_over_L_180": None})
        if len(i180):
            res["D_over_L_180"] = float(np.hypot(x[i180[0]], y[i180[0]]) / self.L)
        return res

    def zigzag_indices(self, run, heading_deg=10.0):
        psi, t, dl = np.degrees(run["psi"]), run["t"], run["delta"]
        i1 = np.where(psi >= heading_deg)[0]
        if not len(i1):
            return {"first_overshoot_deg": float("nan"), "period_s": float("nan")}
        i1 = int(i1[0])
        seg = psi[i1:]
        ipk = i1 + int(np.argmax(seg))
        zc = np.where(psi[ipk:] <= 0.0)[0]
        i2 = ipk + int(zc[0]) if len(zc) else None
        i3 = None
        if i2 is not None:
            zp = np.where(psi[i2:] >= heading_deg)[0]
            i3 = i2 + int(zp[0]) if len(zp) else None
        return {"first_overshoot_deg": float(psi[ipk] - heading_deg),
                "reach_heading_s": float(t[i1]),
                "period_s": float(t[i3] - t[i1]) if i3 else float("nan"),
                "second_zero_s": float(t[i2]) if i2 else float("nan")}


if __name__ == "__main__":
    m = MMGRef()
    lin = m.linear_model()
    print("U0=%.3f m/s  m=%.3e kg  I_zz=%.3e  n=%.4f rps  m'=%.4f"
          % (m.U0, m.m, m.I_zz, m.n_rps, m.m_p))
    print("D_stability=%.4e stable=%s  eig=%s  K=%.5f 1/s  T_dom=%.1f s"
          % (lin["D_stability"], lin["C_positive"], np.round(lin["eig"], 3),
             lin["K_1s"], lin["T_dom_s"]))
    ss = m.steady_state(35)
    print("steady 35deg:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ss.items()})
    print("turning 35deg:", {k: (round(v, 3) if isinstance(v, float) else v)
                             for k, v in m.turning_indices(m.run(35.0)).items()})
    print("zigzag 10/10:", m.zigzag_indices(m.run(10.0, mode="zigzag"), 10.0))