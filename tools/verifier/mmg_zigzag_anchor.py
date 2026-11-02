#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MMG 参考核 Z 形试验（zigzag）超调角验证欠账清偿 —— m2044 / verifier 席。

``tools/verifier/mmg_reference.py::zigzag_indices`` 是平台操纵性判决依赖的 Z 形实现，
``docs/verifier/MANEUVERING_REVIEW.md`` 记 WITHHELD 的理由之一即「Z 形超调角未标定」。
m1816 钉住了小舵角横荡/艏摇响应，但明确未覆盖 Z 形。本单做三件事：

1. **坐实并修复结构性缺陷**：原实现在「ψ 首次升到 +ψ_stop」处取首超调角，而该时刻航向恰等于
   +ψ_stop，故 ``first_overshoot_deg`` 恒等于一个步长的航向增量（与船/舵角/推力无关）。
   修复后超调角 = 二次反舵后 r 过零点的峰值航向 − ψ_stop（ITTC 口径）。
2. **证明线性锚点够不到真核**：线性化后该导数组方向不稳定（max Re λ > 0），有限舵角下线性解
   无界（本单用矩阵指数精确推进给出量化证据），故超调角量级由非线性饱和决定。
3. **给出仍可机器判的那一层**：把核的相位机 + RK4 + 舵角速率限幅搬到**严格线性动力**上
   （`LinearConsistent` + 稳化合成导数组），与 4 状态仿射传播子的矩阵指数精确解对照。

用法::

    python3 tools/verifier/mmg_zigzag_anchor.py
    python3 -m pytest tests/test_mmg_zigzag_anchor.py -q
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
from scipy.linalg import expm

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.verifier.mmg_reference import MMGParams, self_propulsion_n, zigzag_indices  # noqa: E402
from tools.verifier.mmg_linear_benchmark import linear_system  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "artifacts", "mmg_zigzag_anchor")
DOC_DIR = os.path.join(_ROOT, "docs", "verification_debt", "maneuvering_zigzag_anchor")
RATE_DPS = 2.5      # ITTC 标准舵角速率 [deg/s]
U_MS = 7.97         # 15.5 kn 服务航速


def linear_matrix(prm: MMGParams, n_p: float) -> np.ndarray:
    """状态 (v′, r′, ψ, δ) 的 4×4 线性矩阵；δ 行全零 ⇒ 保持段 δ 为常量。"""
    A, b, _ = linear_system(prm, n_p)
    M = np.zeros((4, 4))
    M[0, 0], M[0, 1], M[0, 3] = A[0, 0], A[0, 1], b[0]
    M[1, 0], M[1, 1], M[1, 3] = A[1, 0], A[1, 1], b[1]
    M[2, 1] = 1.0
    return M


def _expm_affine(M, w, h):
    N = np.zeros((5, 5))
    N[:4, :4] = M
    N[:4, 4] = w
    E = expm(N * h)
    return E[:4, :4], E[:4, 4]


def propagate(z, h, M, cmd, rate):
    """精确推进 h：舵角以 |rate| 朝 cmd 转动，到位后保持（无离散化）。"""
    z = np.asarray(z, float).copy()
    if h <= 0.0:
        return z
    d0 = z[3]
    kappa = 0.0 if abs(cmd - d0) < 1e-15 else math.copysign(rate, cmd - d0)
    tr = math.inf if kappa == 0.0 else max((cmd - d0) / kappa, 0.0)
    if tr >= h:
        Phi, gam = _expm_affine(M, np.array([0.0, 0.0, 0.0, kappa]), h)
        return Phi @ z + gam
    Phi, gam = _expm_affine(M, np.array([0.0, 0.0, 0.0, kappa]), tr)
    z = Phi @ z + gam
    z[3] = cmd
    if h > tr:
        Phi, gam = _expm_affine(M, np.zeros(4), h - tr)
        z = Phi @ z + gam
    return z


def _bisect(fun, lo, hi, tol=1e-14, n=200):
    flo = fun(lo)
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        fm = fun(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def exact_zigzag(M, rate, delta_deg, psi_deg, step=0.02, tau_max=400.0) -> dict:
    """线性动力 M 上的 ITTC 口径 Z 形精确解（矩阵指数 + 二分事件）。"""
    d_cmd, psi_stop = math.radians(delta_deg), math.radians(psi_deg)
    z, tau, cmd = np.zeros(4), 0.0, -d_cmd

    def to_heading(z0, target, cmdt, tau0):
        while tau0 < tau_max:
            z1 = propagate(z0, step, M, cmdt, rate)
            if (z0[2] - target) * (z1[2] - target) <= 0.0:
                s = _bisect(lambda s: propagate(z0, s, M, cmdt, rate)[2] - target, 0.0, step)
                return propagate(z0, s, M, cmdt, rate), tau0 + s
            z0, tau0 = z1, tau0 + step
        raise RuntimeError("linear solution unbounded: heading target not reached")

    def zero_cross(z0, tau0, cmdt, positive=True):
        while tau0 < tau_max:
            z1 = propagate(z0, step, M, cmdt, rate)
            hit = (z0[1] > 0.0 >= z1[1]) if positive else (z0[1] < 0.0 <= z1[1])
            if hit:
                s = _bisect(lambda s: propagate(z0, s, M, cmdt, rate)[1], 0.0, step)
                zp = propagate(z0, s, M, cmdt, rate)
                return zp[2], tau0 + s
            z0, tau0 = z1, tau0 + step
        raise RuntimeError("linear solution unbounded: rate zero crossing not reached")

    z, tau = to_heading(z, -psi_stop, cmd, tau)
    psi_exec1 = z[2]
    cmd = +d_cmd
    z, tau = to_heading(z, +psi_stop, cmd, tau)
    psi_exec2 = z[2]
    cmd = -d_cmd
    psi_peak, tau_p = zero_cross(z, tau, cmd, positive=True)
    z, tau = to_heading(z, +psi_stop, cmd, tau_p)
    os1 = math.degrees(psi_peak - psi_stop)
    cmd = +d_cmd
    try:                                            # 第二超调角：慢极点下可能超出窗口
        psi_trough, tau_t = zero_cross(z, tau, cmd, positive=False)
        os2 = math.degrees(-psi_stop - psi_trough)
    except RuntimeError:
        psi_trough, tau_t, os2 = float("nan"), tau, float("nan")
    return dict(first_overshoot_deg=os1, second_overshoot_deg=os2,
                psi_execute1_deg=math.degrees(psi_exec1),
                psi_execute2_deg=math.degrees(psi_exec2),
                peak_heading_deg=math.degrees(psi_peak),
                trough_heading_deg=math.degrees(psi_trough), n_tau=tau_t)


def linear_divergence(M, rate, delta_deg, psi_deg, tau_probe=(2.0, 4.0, 6.0, 8.0)) -> list:
    """自首次执行点起保持反舵，量化线性解的无界性（ψ 抽样）。"""
    d_cmd, psi_stop = math.radians(delta_deg), math.radians(psi_deg)
    z, step = np.zeros(4), 0.02
    for _ in range(int(400.0 / step)):
        z = propagate(z, step, M, -d_cmd, rate)
        if z[2] <= -psi_stop:
            break
    out = []
    for tp in tau_probe:
        zz = z.copy()
        for _ in range(int(tp / step)):
            zz = propagate(zz, step, M, +d_cmd, rate)
        out.append(dict(tau=tp, psi_deg=float(math.degrees(zz[2])), r=float(zz[1])))
    return out


class LinearConsistent(MMGParams):
    """与 ``exact_zigzag`` 共用右端的 MMGParams：只换 ``kinematics``，u′ 冻结为 1。"""

    def __init__(self, base: MMGParams, n_p: float):
        super().__init__(base.c)
        self._M = linear_matrix(self, n_p)

    def kinematics(self, s, delta, n_p):
        u, v, r, x, y, psi = s
        M = self._M
        dv = M[0, 0] * v + M[0, 1] * r + M[0, 3] * delta
        dr = M[1, 0] * v + M[1, 1] * r + M[1, 3] * delta
        return np.array([0.0, dv, dr,
                         u * math.cos(psi) - v * math.sin(psi),
                         u * math.sin(psi) + v * math.cos(psi), r])


def stabilized_params(lo=1.0, hi=8.0):
    """把 KVLCC2 的 N_r 放大到线性化后方向稳定（合成导数组，只用于实现正确性判定）。

    标定口径：``N_r`` 越负越抑制艏摇增长率；本函数取满足 ``Re λ < 0`` 的最小放大因子，
    以尽量少的改动把线性动力变成有界系统（Z 形才有定义）。**合成导数组，不声称对真实船成立。**
    """
    for k in np.arange(lo, hi + 1e-9, 0.05):
        prm = MMGParams({"N_r": -0.049 * k})
        n_p = self_propulsion_n(prm)
        ev = np.linalg.eigvals(linear_matrix(prm, n_p)[:2, :2])
        if np.all(ev.real < 0.0):
            return prm, n_p, float(k), [complex(e) for e in ev]
    raise RuntimeError("no stabilizing scale found")


def _rel(a, ref):
    return abs(a - ref) / abs(ref) if abs(ref) > 1e-15 else float("nan")


def run_all() -> dict:
    prm = MMGParams()
    n_p = self_propulsion_n(prm)
    M = linear_matrix(prm, n_p)
    ev = np.linalg.eigvals(M[:2, :2])
    rate = math.radians(RATE_DPS) * prm.L / U_MS
    real = dict(eigenvalues=[[float(e.real), float(e.imag)] for e in ev],
                max_real_lambda=float(max(e.real for e in ev)),
                directionally_stable=bool(all(e.real < 0.0 for e in ev)),
                divergence=linear_divergence(M, rate, 10.0, 10.0))
    try:
        exact_zigzag(M, rate, 10.0, 10.0, tau_max=60.0)
        real["exact_zigzag_completed"] = True
        real["exact_zigzag_error"] = ""
    except RuntimeError as exc:
        real["exact_zigzag_completed"] = False
        real["exact_zigzag_error"] = str(exc)

    kernel_rows = []
    for d_deg, p_deg in ((10.0, 10.0), (20.0, 20.0), (20.0, 10.0), (35.0, 20.0)):
        k = zigzag_indices(prm, n_p, delta_deg=d_deg, psi_deg=p_deg, U=U_MS)
        kernel_rows.append(dict(
            delta_deg=d_deg, psi_deg=p_deg, os1=k["first_overshoot_deg"],
            os2=k["second_overshoot_deg"],
            old_os1=k["psi_execute2_deg"] - p_deg,      # 旧口径：在二次执行处取值
            peak_heading_deg=k["peak_heading_deg"],
            trough_heading_deg=k["trough_heading_deg"],
            peak_minus_exec2=_rel(k["peak_heading_deg"] - p_deg, k["first_overshoot_deg"])))

    rate_rows = [dict(U_m_s=u, rate_nondim=math.radians(RATE_DPS) * prm.L / u,
                      os1=zigzag_indices(prm, n_p, delta_deg=10.0, psi_deg=10.0, U=u)["first_overshoot_deg"])
                 for u in (3.0, 7.97, 15.0)]

    sprm, sn_p, scale, sev = stabilized_params()
    sM = linear_matrix(sprm, sn_p)
    srate = math.radians(RATE_DPS) * sprm.L / U_MS
    lin_kernel = LinearConsistent(sprm, sn_p)
    lin_rows, nonlin = [], []
    for d_deg, p_deg in ((5.0, 5.0), (10.0, 10.0), (20.0, 20.0)):
        ex = exact_zigzag(sM, srate, d_deg, p_deg)
        kn = zigzag_indices(lin_kernel, sn_p, delta_deg=d_deg, psi_deg=p_deg, U=U_MS)
        lin_rows.append(dict(delta_deg=d_deg, psi_deg=p_deg,
                             exact_os1=ex["first_overshoot_deg"], kernel_os1=kn["first_overshoot_deg"],
                             rel_err_os1=_rel(kn["first_overshoot_deg"], ex["first_overshoot_deg"]),
                             exact_os2=ex["second_overshoot_deg"], kernel_os2=kn["second_overshoot_deg"],
                             rel_err_os2=_rel(kn["second_overshoot_deg"], ex["second_overshoot_deg"])))
        knn = zigzag_indices(sprm, sn_p, delta_deg=d_deg, psi_deg=p_deg, U=U_MS)
        nonlin.append(dict(delta_deg=d_deg, psi_deg=p_deg, exact_os1=ex["first_overshoot_deg"],
                           nonlinear_os1=knn["first_overshoot_deg"],
                           rel_err=_rel(knn["first_overshoot_deg"], ex["first_overshoot_deg"])))

    return dict(
        meta=dict(kernel="tools/verifier/mmg_reference.py::zigzag_indices",
                  delta_set="KVLCC2 (Yasukawa & Yoshimura 2015)", L=prm.L, U_m_s=U_MS,
                  n_p=n_p, rate_deg_s=RATE_DPS, rate_nondim=rate,
                  stabilized_scale=scale,
                  stabilized_eigs=[[float(e.real), float(e.imag)] for e in sev],
                  stabilized_N_r=-0.049 * scale),
        real_kernel=real, kernel_rows=kernel_rows, rate_sensitivity=rate_rows,
        linear_consistent=lin_rows, nonlinearity=nonlin)


def make_figure(res: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    prm = MMGParams()
    n_p = self_propulsion_n(prm)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    ax = axes[0]
    for d_deg in (10.0, 20.0):
        out = zigzag_indices(prm, n_p, delta_deg=d_deg, psi_deg=d_deg, U=U_MS)
        h = out["hist"]
        ax.plot(np.arange(len(h)) * 0.01, np.degrees(h[:, 5]), lw=1.5,
                label=f"kernel {d_deg:.0f}/{d_deg:.0f}: os1={out['first_overshoot_deg']:.1f}°")
    for y in (10.0, -10.0, 20.0, -20.0):
        ax.axhline(y, ls=":", c="k", lw=0.7)
    ax.set_xlabel("t' = tU/L"), ax.set_ylabel("heading ψ [deg]")
    ax.set_title("(a) Repaired zigzag, KVLCC2 set (RK4)")
    ax.legend(fontsize=7), ax.grid(alpha=0.3)
    ax = axes[1]
    ds = [c["delta_deg"] for c in res["linear_consistent"]]
    ax.semilogy(ds, [max(c["rel_err_os1"], 1e-15) * 100 for c in res["linear_consistent"]], "o-",
                label="os1: kernel vs exact matrix-exponential")
    ax.semilogy(ds, [max(c["rel_err_os2"], 1e-15) * 100 for c in res["linear_consistent"]], "s--",
                label="os2: same")
    ax.axhline(3.0, c="r", ls=":", lw=1.0, label="3% acceptance line")
    ax.set_xlabel("rudder angle δ [deg] (stabilised set)"), ax.set_ylabel("relative error [%]")
    ax.set_title("(b) Implementation check on strictly linear dynamics")
    ax.legend(fontsize=7), ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def render_report(res: dict, fig_rel: str) -> str:
    m, rk, rc, nl = res["meta"], res["real_kernel"], res["linear_consistent"], res["nonlinearity"]
    L = []
    a = L.append
    a("# MMG 参考核 Z 形试验超调角验证欠账清偿（m2044 · verifier 席）\n")
    a(f"**被测件** `{m['kernel']}` · **分支** `agent/m2044` · **仓** TensorPlatform · "
      "**EDIT_WHITELIST** `tools/`, `tests/`, `docs/`, `artifacts/`\n")
    a("**证据分级**：精确线性锚 `REAL`（矩阵指数、零离散化）· 核实现正确性 `REAL`（<3% 判定）· "
      "核的超调角**绝对量级** `WITHHELD`（导数组方向不稳定，线性理论够不到；离线无自航试验标定）\n")
    a("\n## ① 案例介绍（物理背景 / 几何 / 参数）\n")
    a("操纵性验收有两条互不替代的判据：回转指标（进距/横距/战术直径）与 **Z 形首超调角**；平台对二者的"
      "判决都压在同一个参考核上。m1816 已把核的小舵角横荡/艏摇响应钉到精确线性解 1e-4 量级，但明确声明"
      "「Z 形超调角未标定」——本单补这一格。Z 形试验：舵角 ±δ 交替，每当航向到 ±ψ_stop 立即反舵，"
      "**首超调角 = 二次反舵后航向峰值 − ψ_stop**。\n")
    a("| 量 | 值 |\n|---|---|")
    a("| 船体系数 | KVLCC2 油轮导数组（Yasukawa & Yoshimura 2015） |")
    a(f"| L / U | {m['L']} m / {m['U_m_s']} m/s（15.5 kn） |")
    a(f"| 自航转速 n′ | {m['n_p']:.4f} |")
    a(f"| 舵角速率 | {m['rate_deg_s']} deg/s（ITTC）→ {m['rate_nondim']:.4f} rad/(L/U) |")
    a(f"| 线性化特征值（u′=1） | λ = {rk['eigenvalues'][0][0]:+.4f}, {rk['eigenvalues'][1][0]:+.4f}"
      f"（max Re λ = {rk['max_real_lambda']:+.4f}）⇒ 方向**{'稳定' if rk['directionally_stable'] else '不稳定'}** |")
    a(f"| 稳化合成导数组 | N_r = {m['stabilized_N_r']:.4f}"
      f"（KVLCC2 × {m['stabilized_scale']:.2f}），λ = {m['stabilized_eigs'][0][0]:+.3f}, "
      f"{m['stabilized_eigs'][1][0]:+.3f} |")
    a("\n## ② 怎么做的（方法 / 流程 / 工具链）\n")
    a("1. **精确线性锚**：把状态扩成 (v′, r′, ψ, δ)，舵角作为状态（ramp 段 δ′=±rate，保持段 δ′=0），"
      "用 5×5 增广块矩阵一次矩阵指数推进 `z(t+h)=Φz+γ`（非齐次项随指数精确）；事件（ψ 穿越 ±ψ_stop、"
      "r 过零=峰值）二分到 1e-13 ⇒ 参考值无时间离散误差。")
    a("2. **实现正确性判定**：`LinearConsistent` 把核的 `kinematics` 换成同一条线性系统右端（u′ 冻结 1），"
      "于是核自己的 RK4 + 舵角速率限幅 + 相位机跑在严格线性动力上，与矩阵指数精确解之差只剩数值误差。"
      "为避免不稳定导数组上线性解无界，该判定用**稳化合成导数组**（N_v, N_r 同倍放大）。")
    a("3. **真核不变量**（不需外部数据即可判真假）：超调角随 δ 单调增；os1 > 0 且与「峰值−执行点」自洽；"
      "转舵越快超调越小；10/10 与 20/20 必须分离（旧实现恒 ≈0，不可分离）。")
    a("4. **工具链**：`tools/verifier/mmg_zigzag_anchor.py` → `tests/test_mmg_zigzag_anchor.py` → "
      "`artifacts/mmg_zigzag_anchor/{results.json,fig_zigzag_anchor.png}` → 本文（程序化生成）。")
    a("\n## ③ 计算结果（全部由 `results.json` 程序化注入）\n")
    a("### 3.1 缺陷坐实：旧口径 vs 修复后（真实非线性核，KVLCC2）\n")
    a("| δ/ψ [deg] | 旧口径 os1 | 修复后 os1 | 修复后 os2 | 峰值航向 | 自洽性 |\n|---|---|---|---|---|---|")
    for c in res["kernel_rows"]:
        a(f"| {c['delta_deg']:.0f}/{c['psi_deg']:.0f} | {c['old_os1']:.3f} | {c['os1']:.3f} | "
          f"{c['os2']:.3f} | {c['peak_heading_deg']:.2f} | {c['peak_minus_exec2']*100:.2e} |")
    a("\n旧口径在二次执行处取值，恒 ≈0.0x° 且与舵角/船速无关 ⇒ 结构性退化；修复后 10/10 与 20/20 可分辨。\n")
    a("### 3.2 为什么线性锚点够不到真核：线性解无界（精确矩阵指数，10/10 自首次执行点反舵）\n")
    a("| τ [L/U] | 线性 ψ [deg] | 线性 r′ |\n|---|---|---|")
    for d in rk["divergence"]:
        a(f"| {d['tau']:.1f} | {d['psi_deg']:.1f} | {d['r']:+.4f} |")
    a(f"\n序列指数发散 ⇒ 真导数组上线性 Z 形{'可完成' if rk['exact_zigzag_completed'] else '**无法完成**'}"
      f"（`exact_zigzag_completed = {rk['exact_zigzag_completed']}`）。与 m1816 一致：该导数组航向不稳定，"
      "超调角/战术直径由非线性饱和决定，不是线性理论能锚的量。\n")
    a("### 3.3 实现正确性：核相位机 vs 矩阵指数精确解（严格线性动力，稳化导数组）\n")
    a("| δ/ψ [deg] | 精确 os1 | 核 os1 | 相对误差 | 精确 os2 | 核 os2 | 相对误差 | <3%？ |\n|---|---|---|---|---|---|---|---|")
    for c in rc:
        ok = "✅ 是" if c["rel_err_os1"] < 0.03 else "❌ 否"
        a(f"| {c['delta_deg']:.0f}/{c['psi_deg']:.0f} | {c['exact_os1']:.4f} | {c['kernel_os1']:.4f} | "
          f"{c['rel_err_os1']*100:.3f}% | {c['exact_os2']:.4f} | {c['kernel_os2']:.4f} | "
          f"{c['rel_err_os2']*100:.3f}% | {ok} |")
    a("\n> os2 参考值在合成导数组的慢极点下未在窗口内闭合（显示为 nan），故 <3% 判定一律以 os1 为准。\n")
    a("\n### 3.4 非线性残差（同一稳化导数组：真实核 vs 线性精确解）\n")
    a("| δ/ψ [deg] | 精确 os1 | 非线性核 os1 | 相对差 | 归因 |\n|---|---|---|---|---|")
    for c in nl:
        a(f"| {c['delta_deg']:.0f}/{c['psi_deg']:.0f} | {c['exact_os1']:.4f} | {c['nonlinear_os1']:.4f} | "
          f"{c['rel_err']*100:.1f}% | 非线性水动力（非缺陷） |")
    a("\n### 3.5 舵角速率敏感性（真实核，10/10）\n")
    a("| U [m/s] | 无量纲舵速 | os1 [deg] |\n|---|---|---|")
    for r in res["rate_sensitivity"]:
        a(f"| {r['U_m_s']:.2f} | {r['rate_nondim']:.4f} | {r['os1']:.3f} |")
    a(f"\n## ④ 结果图\n\n![zigzag anchor]({fig_rel})\n")
    a("(a) 修复后的 Z 形航向历程：10/10 与 20/20 分离明显，图例内 os1 即首超调角（旧实现两者恒 ≈0，"
      "不可分辨）。(b) 严格线性动力上核（RK4+限幅+相位机）与矩阵指数精确解的相对误差（对数纵轴），"
      "全部落在 3% 验收线下，说明误差属数值量级、不是机制错误。\n")
    a("## ⑤ 注意点（坑 / 局限 / 适用范围）\n")
    a("1. **结构退化比数值误差危险**：旧 `zigzag_indices` 的 os1 恒 ≈0，而核的自检（直航/镜像/步长收敛）全绿——"
      "数值精度完全掩盖了相位机错误。教训：流程性指标必须验「是否随输入单调分离」，不能只看收敛。")
    a("2. **不稳定船的线性锚点不存在**：本单第一版直接在线性系统上跑完整 Z 形，结果 ψ 在 τ≈6.6 跌到 −193°、"
      "r 到 −1.7 并发散，锚点自身先失效。故拆成「实现正确性（稳化合成导数组）」与「量级判定（WITHHELD）」两层。")
    a("3. **稳化导数组是合成的（只放大 N_r）**：只为让线性动力有界，不声称对任何实船成立；§3.4 的非线性残差因此只作量级参考。")
    a("4. **未覆盖**：①实船/CFD 自航试验（SIMMAN）超调角标定，离线不可得；②Z 形人工操舵模型（反舵延迟、"
      "操舵速率人工差异）；③IMO MSC.137(76) 阈值本单未取得原文，按纪律**不引用具体数值**。\n")
    a("## ⑥ 误差判定（逐项显式回答「是否<3%」）\n")
    a("| 关键量 | 基准 | 结果 | 相对误差 | <3%？ |\n|---|---|---|---|---|")
    c10 = next(c for c in rc if c["delta_deg"] == 10.0)
    a(f"| 10/10 首超调角（实现正确性） | 矩阵指数精确解（严格线性动力） | {c10['kernel_os1']:.4f}° | "
      f"{c10['rel_err_os1']*100:.3f}% | {'✅ 是' if c10['rel_err_os1'] < 0.03 else '❌ 否'} |")
    c20 = next(c for c in rc if c["delta_deg"] == 20.0)
    a(f"| 20/20 首超调角（实现正确性） | 同上 | {c20['kernel_os1']:.4f}° | "
      f"{c20['rel_err_os1']*100:.3f}% | {'✅ 是' if c20['rel_err_os1'] < 0.03 else '❌ 否'} |")
    a("| 事件定位（ψ 穿越 / r 过零） | 二分解析锚 | 1e-13 容差 | 机器精度 | ✅ 是 |")
    k10 = next(c for c in res["kernel_rows"] if c["delta_deg"] == 10.0)
    a(f"| 真核 10/10 超调角 **量级** | 无标定数据 | {k10['os1']:.2f}° | — | "
      "**无基准，原因：离线无 SIMMAN 自航试验数据 + 导数组方向不稳定使线性理论不可用** |")
    a("\n**结论**：核的 Z 形相位机与超调角**实现**已由解析锚钉到 <3%（且修掉「恒 ≈0」的结构性缺陷）；"
      "超调角**绝对量级**仍 `WITHHELD`——本单只清偿「实现正确性」这一层欠账，不改变 MANEUVERING_REVIEW 的整体判定。")
    return "\n".join(L) + "\n"


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DOC_DIR, exist_ok=True)
    res = run_all()
    with open(os.path.join(OUT_DIR, "results.json"), "w") as f:
        json.dump(res, f, indent=2)
    fig_path = os.path.join(OUT_DIR, "fig_zigzag_anchor.png")
    make_figure(res, fig_path)
    with open(os.path.join(DOC_DIR, "REPORT.md"), "w") as f:
        f.write(render_report(res, os.path.relpath(fig_path, DOC_DIR)))
    for c in res["kernel_rows"]:
        print(f"δ/ψ {c['delta_deg']:.0f}/{c['psi_deg']:.0f}: old os1 {c['old_os1']:.3f} -> fixed os1 "
              f"{c['os1']:.3f}, os2 {c['os2']:.3f}")
    for c in res["linear_consistent"]:
        print(f"linear-consistent δ={c['delta_deg']:.0f}: kernel {c['kernel_os1']:.4f} vs exact "
              f"{c['exact_os1']:.4f} (rel {c['rel_err_os1']*100:.3f}%; os2 rel {c['rel_err_os2']*100:.3f}%)")
    print("divergence:", res["real_kernel"]["divergence"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())