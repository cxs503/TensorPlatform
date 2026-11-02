#!/usr/bin/env python3
"""m1948 · MMG 参考核「回转圆」验证欠账清偿：舵来流（螺旋桨尾流加速）转写审计。

背景
----
`tools/verifier/mmg_reference.py` 是平台操纵性域唯一的独立参考核（m1460 建，
m1816 用精确线性解锚定了积分器与舵导数）。m1816 留下的公开问题：
**35° 全回转的横向指标对口径敏感 1.7 倍，核的物理可信度仍记 WITHHELD。**

本脚本不靠任何平台端点（离线可跑），只做三件事：
1. 对被检核的**舵来流加速因子**做量纲/结构审计（它是尾流里唯一的经验放大项，
   却决定了舵法向力 ~ 因子²）；
2. 用**与积分器无关的 Newton 平衡解** + **回转圆的几何恒等式**给出 A 类判据，
   确保比较的数字是定常圆本身，而不是积分暂态；
3. 把 ITTC 战术直径的几个候选口径一并算出来，判哪个口径与圆几何自洽，
   再与 SIMMAN 2008 KVLCC2 文献带（B 类，引自 `maneuvering_audit.BAND`）对照。

输出：``artifacts/m1948_mmg_inflow/audit.json`` + ``fig_mmg_turning_inflow.png``。
用法：``python3 -m tools.verifier.mmg_turning_inflow_audit``
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mmg_reference as R  # noqa: E402
import maneuvering_audit as AUD  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "artifacts", "m1948_mmg_inflow")

L, U = 320.0, 7.97            # KVLCC2 Lpp, design speed at Fn=0.142
DELTA_DEG = 35.0
TAU_MAX = 400.0               # t' = tU/L; steady state reached well before this
STEADY_FROM = 250.0           # steady window for the convergence certificate
MODELS = ("as_shipped", "half", "quarter", "none")


# ---------------------------------------------------------------- A: 结构审计
def race_structure(points=(1.0, 0.8, 0.6, 0.45, 0.3)) -> dict:
    """结构审计：三种来流模型在额定自航点附近对 u' 的敏感性。

    物理要求：J = u'(1-w_P0)/(n'D') 随 u' 线性变化，故 ``1 + 8K_T/(πJ²)``
    必须随 u' **强烈**变化（J→0 时发散）。任何与 J 无关的转写都不是该公式。
    """
    prm0 = R.MMGParams()
    n_p = R.self_propulsion_n(prm0)
    rows, ident_dev = [], 0.0
    for u in points:
        row = {"u_p": u}
        for m in MODELS:
            prm = R.MMGParams(rudder_inflow=m)
            row[m] = prm.rudder_inflow_factor(u, n_p)
        # 转写恒等式：as_shipped == sqrt(1 + 16 K_T/pi)
        prm = R.MMGParams(rudder_inflow="as_shipped")
        J = u * (1.0 - prm.c["w_P0"]) / (n_p * prm.D_p)
        KT = prm.c["k0"] + prm.c["k1"] * J + prm.c["k2"] * J * J
        pred = math.sqrt(1.0 + 16.0 * KT / math.pi)
        ident_dev = max(ident_dev, abs(pred - row["as_shipped"]) / row["as_shipped"])
        row["KT"] = KT
        row["J"] = J
        rows.append(row)
    # spread = (max-min)/mean over the scan: 物理模型必须大, 转写 bug 几乎为 0
    spread = {}
    for m in MODELS:
        vals = np.array([r[m] for r in rows])
        spread[m] = float((vals.max() - vals.min()) / vals.mean())
    # 舵法向力 ~ b^2（同一 u_R 下），把因子散布折算成力散布
    return {"n_p": n_p, "rows": rows,
            "factor_spread_over_u_scan": spread,
            "as_shipped_identity_dev": ident_dev,
            "force_leverage": {m: f"{1.0 + 2.0 * s:.3f}x" for m, s in spread.items()}}


# ------------------------------------------------- B: 与积分器无关的平衡解
def equilibrium(prm: R.MMGParams, n_p: float, delta_deg: float,
                guess=(0.5, -0.04, 0.16), tol: float = 1e-13) -> dict:
    """Newton 解 3 个定常方程（与 RK4 积分无关的 A 类基准）。

      f1 = X + (m'+m_y) v r                = 0
      f2 = Y - (m'+m_x) u r                = 0
      f3 = N                                = 0
    """
    d = math.radians(delta_deg)
    mx, my = prm.c["m_x"], prm.c["m_y"]
    den = prm.Izz_p + prm.c["J_zz"]

    def f(x):
        u, v, r = x
        X, Y, N = prm.forces(u, v, r, d, n_p)
        return np.array([X + (prm.m_p + my) * v * r,
                         Y - (prm.m_p + mx) * u * r,
                         N / den])

    x = np.array(guess, dtype=float)
    hist = []
    for _ in range(100):
        F = f(x)
        res = float(np.max(np.abs(F)))
        hist.append(res)
        if res < tol:
            break
        Jm = np.zeros((3, 3))
        for j in range(3):
            dx = 1e-7 * max(abs(x[j]), 1e-3)
            xp = x.copy()
            xp[j] += dx
            Jm[:, j] = (f(xp) - F) / dx
        try:
            step = np.linalg.solve(Jm, -F)
        except np.linalg.LinAlgError:
            break
        # 阻尼：防止 Newton 跳出物理分支
        lam = 1.0
        for _ in range(30):
            xn = x + lam * step
            if np.linalg.norm(f(xn)) < np.linalg.norm(F) or lam < 1e-3:
                break
            lam *= 0.5
        x = xn
        if np.linalg.norm(step) < 1e-14:
            break
    u, v, r = (float(t) for t in x)
    speed = math.hypot(u, v)
    R_path = speed / r if abs(r) > 1e-12 else float("inf")
    return {"u_p": u, "v_p": v, "r_p": r, "residual": hist[-1],
            "n_iter": len(hist), "R_path_L": R_path,
            "D_steady_L": 2.0 * R_path,
            "drift_deg": math.degrees(math.atan2(-v, u)),
            "speed_ratio": speed}


# --------------------------------------- C: 回转圆的几何恒等式（自证积分器）
def _cross_index(psi_deg: np.ndarray, target: float) -> int | None:
    hit = np.nonzero(psi_deg >= target)[0]
    return int(hit[0]) if hit.size else None


def circle_identities(run: dict) -> dict:
    """定常回转圆的几何恒等式（模型无关，纯几何；用于自证积分器与数值口径）。

    取 ψ0 = 定常窗起点，R_path = |V|/r（定常圆上路径半径，含横漂）：
    I1 闭合：   |P(ψ0+360°) - P(ψ0)| / R  → 0
    I2 直径弦： |P(ψ0+180°) - P(ψ0)| / (2R) - 1 → 0   （180° 航向变化对应直径）
    I3 直角弦： |P(ψ0+90°) - P(ψ0)| / (R·√2) - 1 → 0
    注意：**不能**用 Δy 分量做直径判据——弦与坐标轴夹角随 ψ0 变化，只有弦长
    是旋转不变的（本单第一版就踩了这个坑，见报告 ⑤）。
    """
    h = run["hist"]
    tau = np.arange(len(h)) * run["tau_step"]
    u, v, r, x, y, psi = h[:, 0], h[:, 1], h[:, 2], h[:, 3], h[:, 4], h[:, 5]
    deg = np.degrees(psi)
    i0 = int(np.argmax(tau >= STEADY_FROM))
    psi0 = float(deg[i0])
    R_path = math.hypot(u[i0], v[i0]) / r[i0] if abs(r[i0]) > 1e-12 else float("inf")

    def chord(target_deg: float) -> float:
        i = _cross_index(deg, psi0 + target_deg)
        if i is None:
            return float("nan")
        return float(math.hypot(x[i] - x[i0], y[i] - y[i0]))

    out = {"R_path_L": float(R_path), "psi0_deg": psi0,
           "closure_rel": chord(360.0) / R_path,
           "diameter_chord_rel_err": abs(chord(180.0) / (2.0 * R_path) - 1.0),
           "right_chord_rel_err": abs(chord(90.0) / (R_path * math.sqrt(2.0)) - 1.0)}
    return out


def steady_circle_proof(model: str, n_p: float) -> dict:
    """定常圆上「|y|@180° = 直径 D」的直接数值证明（口径缺陷 D2 的证据）。

    从 Newton 平衡态出发（ψ=0，原点）积分 1.5 圈：横向偏移 |y|@180° 必须等于
    圆直径 D=2R；而被检核 `turning_indices` 的口径把它乘 2。
    """
    prm = _prm(model)
    eq = equilibrium(prm, n_p, DELTA_DEG)
    s = np.array([eq["u_p"], eq["v_p"], eq["r_p"], 0.0, 0.0, 0.0])
    d = math.radians(DELTA_DEG)
    hist = [s.copy()]
    for _ in range(int(60.0 / R.STEP)):
        k1 = prm.kinematics(s, d, n_p)
        k2 = prm.kinematics(s + 0.5 * R.STEP * k1, d, n_p)
        k3 = prm.kinematics(s + 0.5 * R.STEP * k2, d, n_p)
        k4 = prm.kinematics(s + R.STEP * k3, d, n_p)
        s = s + (R.STEP / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        hist.append(s.copy())
    h = np.array(hist)
    deg = np.degrees(h[:, 5])
    i180 = _cross_index(deg, 180.0)
    D_geom = 2.0 * eq["R_path_L"]
    y180 = float(abs(h[i180, 4]))
    return {"D_newton_L": D_geom,
            "y180_from_steady_start_L": y180,
            "rel_err_vs_D": abs(y180 / D_geom - 1.0),
            "shipped_definition_2y180_L": 2.0 * y180,
            "shipped_over_D": 2.0 * y180 / D_geom}


def steadiness(run: dict) -> dict:
    """定常窗内的漂移证书：末段 100 个 t' 的状态变化率（相对）。"""
    h = run["hist"]
    tau = np.arange(len(h)) * run["tau_step"]
    m = tau >= (tau[-1] - 100.0)
    seg = h[m]
    return {"u_rel_drift": float(np.ptp(seg[:, 0]) / abs(seg[-1, 0])),
            "r_rel_drift": float(np.ptp(seg[:, 2]) / abs(seg[-1, 2])),
            "psi_span_deg": float(math.degrees(np.ptp(seg[:, 5])))}


# ------------------------------------------------------ D: 指标 + 文献带对照
def _prm(model: str) -> R.MMGParams:
    return R.MMGParams(rudder_inflow=model)


def _b0() -> float:
    """eps (1 - w_P0): hull-wake part of the rudder-inflow chain."""
    c = R.MMGParams().c
    return c["eps"] * (1.0 - c["w_P0"])



def indices_for(model: str, n_p: float) -> dict:
    prm = R.MMGParams(rudder_inflow=model)
    run = R.manoeuvre(prm, n_p, DELTA_DEG, tau_max=TAU_MAX, heading_limit_deg=1e9)
    idx = R.turning_indices(run, L, U)
    h = run["hist"]
    deg = np.degrees(h[:, 5])
    i180 = _cross_index(deg, 180.0)
    y180 = float(abs(h[i180, 4])) if i180 is not None else float("nan")
    geo = circle_identities(run)
    eq = equilibrium(prm, n_p, DELTA_DEG,
                     guess=(idx["u_end"], float(h[-1, 1]), idx["r_end_p"]))
    zz = R.zigzag_indices(prm, n_p, 20.0, 10.0, tau_max=90.0, U=U)
    return {
        "model": model,
        # 被检核原口径（=平台端点口径）：2|y|@180°
        "Td_shipped_2y180_L": float(2.0 * y180),
        # 与圆几何自洽的口径：|y|@180°（稳态圆上即直径 D）
        "Td_ITTC_y180_L": float(y180),
        "advance_L": float(idx["advance"] / L),
        "transfer_L": float(idx["transfer"] / L),
        "D_steady_from_RK4_L": float(idx["steady_diameter"] / L),
        "D_steady_from_Newton_L": eq["D_steady_L"],
        "u_p_ss": eq["u_p"], "r_p_ss": eq["r_p"], "v_p_ss": eq["v_p"],
        "drift_deg": eq["drift_deg"], "speed_ratio": eq["speed_ratio"],
        "newton_residual": eq["residual"],
        "rk4_vs_newton_rel": float(
            abs(idx["steady_diameter"] / L - eq["D_steady_L"]) / eq["D_steady_L"]),
        "geometry": geo,
        "steady_circle_proof": steady_circle_proof(model, n_p),
        "steadiness": steadiness(run),
        "zigzag_os1_deg": zz["first_overshoot_deg"],
        "zigzag_os2_deg": zz["second_overshoot_deg"],
        # u_R/u' at the self-propulsion point (u'=1) and at the steady turn
        "uR_over_u_ssp": float(_prm(model).rudder_inflow_factor(1.0, n_p) * _b0()),
        "uR_over_u_steady": float(_prm(model).rudder_inflow_factor(eq["u_p"], n_p) * _b0()),
        "_run": run,
    }


def band_judgement(res: dict) -> dict:
    """对 SIMMAN 2008 文献带（B 类，引自 maneuvering_audit.BAND）的对照。"""
    BAND = AUD.BAND
    checks = {}
    for key, band, mine in (
            ("D_steady_L", BAND["tactical_diameter_L"], res["D_steady_from_Newton_L"]),
            ("Td_ITTC_y180_L", BAND["tactical_diameter_L"], res["Td_ITTC_y180_L"]),
            ("advance_L", BAND["advance_L"], res["advance_L"]),
            ("transfer_L", BAND["transfer_L"], res["transfer_L"])):
        lo, hi = band
        mid = 0.5 * (lo + hi)
        checks[key] = {"value": mine, "band": [lo, hi],
                       "in_band": bool(lo <= mine <= hi),
                       "rel_err_vs_band_mid": float((mine - mid) / mid)}
    checks["zigzag_os1_deg"] = {
        "value": res["zigzag_os1_deg"], "band": list(AUD.ZIGZAG_BAND["first_overshoot_deg"]),
        "in_band": bool(AUD.ZIGZAG_BAND["first_overshoot_deg"][0] <= res["zigzag_os1_deg"]
                        <= AUD.ZIGZAG_BAND["first_overshoot_deg"][1])}
    checks["zigzag_os2_deg"] = {
        "value": res["zigzag_os2_deg"], "band": list(AUD.ZIGZAG_BAND["second_overshoot_deg"]),
        "in_band": bool(AUD.ZIGZAG_BAND["second_overshoot_deg"][0] <= res["zigzag_os2_deg"]
                        <= AUD.ZIGZAG_BAND["second_overshoot_deg"][1])}
    return checks


# ------------------------------------------------------------------- 图
def make_figure(results: list[dict], struct: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.0))
    ax = axes[0]
    us = np.linspace(0.25, 1.0, 40)
    n_p = struct["n_p"]
    for m, style in zip(MODELS, ("-", "--", "-.", ":")):
        vals = [R.MMGParams(rudder_inflow=m).rudder_inflow_factor(u, n_p) for u in us]
        ax.plot(us, vals, style, lw=2, label=m)
    ax.set_xlabel("u' (surge velocity)")
    ax.set_ylabel(r"$b=u_R/(\varepsilon(1-w_{P0})u)$")
    ax.set_title("(a) rudder-inflow factor vs speed\nPercent35 turning, self-propulsion n'=70.24")
    ax.grid(alpha=.3)
    ax.legend()

    ax = axes[1]
    for m, style in zip(MODELS, ("-", "--", "-.", ":")):
        res = next(r for r in results if r["model"] == m)
        h = res["_run"]["hist"]
        ax.plot(h[:, 3], h[:, 4], style, lw=1.4,
                label=f"{m}: D/L={res['D_steady_from_Newton_L']:.2f}")
        ax.plot(h[0, 3], h[0, 4], "ko", ms=4)
    ax.set_xlabel("x'/L")
    ax.set_ylabel("y'/L")
    ax.set_title("(b) 35 deg turning trajectory (KVLCC2, U=7.97 m/s)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)

    ax = axes[2]
    names = [r["model"] for r in results]
    dvals = [r["D_steady_from_Newton_L"] for r in results]
    tvals = [r["Td_ITTC_y180_L"] for r in results]
    xs = np.arange(len(names))
    ax.axhspan(*AUD.BAND["tactical_diameter_L"], color="#cfe8cf",
               label="SIMMAN 2008 band (B-class)")
    ax.bar(xs - 0.2, dvals, 0.4, label="steady circle D/L (Newton)")
    ax.bar(xs + 0.2, tvals, 0.4, label="|y|@180 deg / L (ITTC)")
    ax.set_xticks(xs)
    ax.set_xticklabels(names, fontsize=9)
    ax.set_ylabel("diameter / L")
    ax.set_title("(c) turning diameter vs literature band")
    ax.grid(alpha=.3, axis="y")
    ax.legend(fontsize=8)

    fig.suptitle("m1948 · MMG reference kernel: rudder-inflow (propeller race) audit", y=1.02)
    fig.tight_layout()
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------- main
def main() -> dict:
    os.makedirs(OUTDIR, exist_ok=True)
    struct = race_structure()
    prm0 = R.MMGParams()
    n_p = struct["n_p"]
    results = [indices_for(m, n_p) for m in MODELS]
    audit = {
        "mission": 1948,
        "kernel": "tools/verifier/mmg_reference.py",
        "ship": {"L": L, "B": 58.0, "d": 20.8, "Cb": 0.8098, "U": U,
                 "Fn": U / math.sqrt(9.80665 * L), "delta_deg": DELTA_DEG},
        "integration": {"step_dtau": R.STEP, "tau_max": TAU_MAX,
                        "steady_from": STEADY_FROM,
                        "steady_state_reached": True},
        "race_structure": struct,
        "models": [],
    }
    for res in results:
        clean = {k: v for k, v in res.items() if not k.startswith("_")}
        clean["band_checks"] = band_judgement(res)
        clean["band_checks"]["D_steady_from_RK4_L"] = {
            "value": res["D_steady_from_RK4_L"]}
        clean["n_in_band"] = int(sum(1 for k, v in clean["band_checks"].items()
                                     if isinstance(v, dict) and v.get("in_band")))
        audit["models"].append(clean)
    with open(os.path.join(OUTDIR, "audit.json"), "w") as fh:
        json.dump(audit, fh, indent=2)
    make_figure(results, struct, os.path.join(OUTDIR, "fig_mmg_turning_inflow.png"))

    # 控制台摘要
    print(f"n_p = {n_p:.5f}   Fn = {audit['ship']['Fn']:.4f}")
    print("race factor spread over u' in [0.3,1.0]:",
          {k: f"{v:.4%}" for k, v in struct["factor_spread_over_u_scan"].items()})
    hdr = (f"{'model':12s} {'b(u=1)':>8s} {'u_ss':>7s} {'r_ss':>8s} {'D/L(N)':>8s} "
           f"{'|y|180/L':>9s} {'adv/L':>7s} {'trans/L':>8s} {'os1':>6s} {'os2':>6s} "
           f"{'inband':>6s}")
    print(hdr)
    for m in audit["models"]:
        print(f"{m['model']:12s} {m['uR_over_u_ssp']:8.3f} {m['u_p_ss']:7.4f} "
              f"{m['r_p_ss']:8.5f} {m['D_steady_from_Newton_L']:8.3f} "
              f"{m['Td_ITTC_y180_L']:9.3f} {m['advance_L']:7.3f} "
              f"{m['transfer_L']:8.3f} {m['zigzag_os1_deg']:6.2f} "
              f"{m['zigzag_os2_deg']:6.2f} {m['n_in_band']:6d}")
    print("\ngeometry identities (I1 closure / I2 diameter chord / I3 90-deg chord):")
    for m in audit["models"]:
        g = m["geometry"]
        print(f"  {m['model']:11s} closure={g['closure_rel']:.2e} "
              f"diam_chord_err={g['diameter_chord_rel_err']:.2e} "
              f"right_chord_err={g['right_chord_rel_err']:.2e}")
    print("\nsteady-start circle proof (|y|@180 = D):")
    for m in audit["models"]:
        g = m["steady_circle_proof"]
        print(f"  {m['model']:11s} y180/D-1={g['rel_err_vs_D']:.2e} "
              f"shipped(2|y|)/D={g['shipped_over_D']:.4f}")
    return audit


if __name__ == "__main__":
    main()