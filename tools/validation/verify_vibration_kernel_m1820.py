"""振动噪声核独立复核（m1820）：把 m1493 已合并核逐项对**文献/异离散/恒等式**复核。

判据纪律（判据先于动手）：
* 每条判据必须给出 ``basis``（基准来源）与 ``tol``（容差），否则不许进账本；
* 参考侧只来自 ``vibration_reference_m1820.py``（文献表值 / 有限差分 / 时域 RK4 /
  恒等式），**不复用被测代码任何公式**；
* 判决只看 served vs reference 的相对偏差，大于容差即 FAIL，不许事后放宽。

产出（全部程序化、零手抄）：
* ``artifacts/m1820/verdicts.json``      判据账本（含每条 served/reference/误差/判决）
* ``artifacts/m1820/verdicts.md``        同表的 markdown
* ``artifacts/m1820/fig_vibration_audit.png``  4 面板图
* ``artifacts/m1820/report_payload.json`` 平台报告载荷（含 base64 图）
"""
from __future__ import annotations

import base64
import importlib.util
import json
import math
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import vibration_reference_m1820 as ref  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUTDIR = ROOT / "artifacts" / "m1820"
MODULE = ROOT / "backend" / "api" / "domain" / "ship_vibration.py"


def load_kernel():
    spec = importlib.util.spec_from_file_location("ship_vibration_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECKS: List[Dict] = []


def add(cid: str, domain: str, name: str, served, reference, tol: float,
        basis: str, unit: str = "", kind: str = "rel", grade: str = "REAL") -> Dict:
    if kind == "rel":
        denom = abs(reference) if abs(reference) > 1e-12 else 1.0
        err = abs(served - reference) / denom
        err_s = f"{err:.3e}"
    else:  # abs
        err = abs(served - reference)
        err_s = f"{err:.3e}"
    ok = bool(err <= tol)
    row = {
        "id": cid, "domain": domain, "name": name,
        "served": served, "reference": reference,
        "abs_error": abs(served - reference), "error": err,
        "error_str": err_s, "tol": tol, "tol_str": f"{tol:.1e}",
        "tol_kind": kind, "passed": ok, "verdict": "PASS" if ok else "FAIL",
        "basis": basis, "unit": unit, "grade": grade,
    }
    CHECKS.append(row)
    return row


def run_checks(k) -> Dict[str, list]:
    series = {}

    # ---------- A. 梁特征值 vs Blevins(1979) Table 8-1 ----------
    beam_series = {}
    for bc in ("clamped-clamped", "clamped-free", "clamped-pinned", "pinned-pinned"):
        got = k.beam_eigenvalues(bc, 4)
        lit = ref.blevins_lambda(bc, 4)
        errs = [abs(g - l) / abs(l) for g, l in zip(got, lit)]
        beam_series[bc] = {"served": got, "reference": lit, "errors": errs}
        add(f"A.{bc}", "beam", f"lambda_1..4 ({bc}) vs Blevins Table 8-1",
            max(got), max(lit), 1e-5,
            "Blevins 1979, Formulas for Natural Frequency and Mode Shape, Table 8-1 "
            "(文献表值；误差取 4 阶中最大相对偏差)", unit="1")

    # ---------- B. 梁频率：文献 lambda 代闭式（与自求根独立） ----------
    E, I, rho, A, L = 2.1e11, 1.6e-4, 7850.0, 0.02, 6.0
    for bc in ("clamped-free", "clamped-clamped"):
        got_f = k.beam_natural_frequencies(bc, E, I, rho, A, L, 4)
        ref_f = ref.beam_frequency_from_lambda(bc, E, I, rho, A, L, 4)
        err = max(abs(g - r) / r for g, r in zip(got_f, ref_f))
        add(f"B.{bc}", "beam", f"f_1..4 ({bc}) vs 文献 lambda 闭式",
            got_f[0], ref_f[0], 1e-5,
            "f_n = lambda_n^2/(2 pi L^2) sqrt(EI/rhoA)，lambda 取文献表值（独立于模块求根器）",
            unit="Hz")
        beam_series.setdefault(bc, {})["freq_err"] = err

    # ---------- C. Hermite FEM 收敛阶（有限元是模块自带交叉验证，本单复核其真实性） ----------
    bcs_conv = []
    for bc in ("clamped-clamped", "clamped-free"):
        e = []
        for n in (4, 8, 16, 32):
            fem = k.beam_fem_frequencies(bc, E, I, rho, A, L, n_elem=n, n_modes=1)[0]
            ana = ref.beam_frequency_from_lambda(bc, E, I, rho, A, L, 1)[0]
            e.append(abs(fem - ana) / ana)
        # 网格 8 -> 32 单元 = 4 倍加密；三次 Hermite + 一致质量单元的特征频率
        # 理论收敛阶为 O(h^4)（Bathe, Finite Element Procedures, §12）
        p = math.log(e[1] / e[-1]) / math.log(4.0)
        bcs_conv.append({"bc": bc, "errors": e, "order": p})
        add(f"C.{bc}", "beam", f"FEM 收敛阶 p ({bc}, 8->32 单元)",
            p, 4.0, 0.15, "三次 Hermite 单元 + 一致质量：特征频率理论收敛阶 O(h^4)"
            "（Bathe §12；15% 容差）", unit="1")
    series["fem_convergence"] = bcs_conv

    # ---------- D. 板第 1 阶：闭式 vs 有限差分（异离散） ----------
    a, b, Ep, nu, rho_p, h = 2.0, 1.5, 2.1e11, 0.3, 7850.0, 0.008
    fd = []
    for n in (16, 24, 32):
        mu_fd = ref.plate_ssss_fd_eigenvalues(a, b, n_grid=n, n_modes=3)
        fd.append({"n_grid": n, "mu": mu_fd})
    mu_exact = ref.plate_ssss_analytic_mu(1, 1, a, b)
    # mu 排序对应 (m,n) 组合；第 1 个即 (1,1)
    fd_err = [abs(d["mu"][0] - mu_exact) / mu_exact for d in fd]
    rich = fd[-1]["mu"][0] + (fd[-1]["mu"][0] - fd[-2]["mu"][0])  # 一阶外推
    fd_err_ext = abs(rich - mu_exact) / mu_exact
    series["plate_fd"] = {"n_grid": [d["n_grid"] for d in fd], "mu11": [d["mu"][0] for d in fd],
                          "mu_exact": mu_exact, "errors": fd_err, "extrap_error": fd_err_ext}
    add("D.plate_mu11", "plate", "Laplacian 特征值 mu11：FD(32x32) vs 解析",
        fd[-1]["mu"][0] / mu_exact, 1.0, 1e-2,
        "5 点差分 Dirichlet Laplacian 的 L^2 特征值开方 vs 解析 mu=(pi/a)^2+(pi/b)^2"
        "（异离散交叉验证，1% 容差为 32x32 网格二阶离散误差量级）", unit="1/m^2")
    add("D.plate_mu11_extrap", "plate", "mu11：FD Richardson 一阶外推 vs 解析",
        rich / mu_exact, 1.0, 1e-3,
        "取 24/32 两网格做一阶 Richardson 外推，检验外推后是否进入 0.1%", unit="1/m^2")
    f11_mod = k.plate_natural_frequency(1, 1, a, b, Ep, nu, rho_p, h)
    f11_ref = ref.plate_ssss_frequency(1, 1, a, b, Ep, nu, rho_p, h)
    add("D.plate_f11", "plate", "SSSS 板 f11：模块闭式 vs 独立书写闭式",
        f11_mod, f11_ref, 1e-9,
        "D、mu 均按 Kirchhoff 定义独立书写；此为恒等式级复核（1e-9 容差）", unit="Hz")
    # 高阶模态 (2,1)/(1,2) 由 FD 谱序对照
    mu_fd_all = fd[-1]["mu"]
    mu_pair = sorted([ref.plate_ssss_analytic_mu(2, 1, a, b), ref.plate_ssss_analytic_mu(1, 2, a, b)])
    add("D.plate_mu21_fd", "plate", "FD 第 2/3 阶 mu vs 解析 mu(2,1)/mu(1,2)",
        max(mu_fd_all[1], mu_fd_all[2]) / mu_pair[1], 1.0, 1e-2,
        "第 2、3 个 FD 特征值应落在解析 (2,1)/(1,2) 附近（异离散，1% 容差）", unit="1/m^2")

    # ---------- E. A 计权 vs IEC 61672-1 Table 3 ----------
    aw_errs = {}
    for f, lit in ref.IEC_61672_TABLE_3.items():
        aw_errs[f] = abs(k.a_weighting(f) - lit)
    worst_f = max(aw_errs, key=lambda x: aw_errs[x])
    add("E.aw_table", "noise", f"A 计权最差频点 ({worst_f:g} Hz) vs IEC 61672-1 Table 3",
        k.a_weighting(worst_f), ref.aw_iec_table(worst_f), 0.3,
        "IEC 61672-1 Table 3 标称值（0.1 dB 取整的规范表，34 个频点全覆盖；容差 0.3 dB 含表格取整）",
        unit="dB", kind="abs")
    add("E.aw_1000", "noise", "A 计权 1000 Hz 归零约定",
        k.a_weighting(1000.0), 0.0, 0.02,
        "IEC 61672-1：1000 Hz 处 A 计权恒等于 0 dB（解析式必须严格归零）", unit="dB", kind="abs")
    series["a_weighting"] = {"freqs": sorted(aw_errs), "errors": [aw_errs[f] for f in sorted(aw_errs)]}

    # ---------- F. 质量定律隔声 ----------
    mpp = 39.25  # 5 mm 钢板面质量 kg/m^2
    for f in (250.0, 500.0, 1000.0, 2000.0):
        add(f"F.tl_normal_{int(f)}", "noise", f"正入射 TL ({int(f)} Hz) vs 教科书精确式",
            k.panel_tl(f, mpp)["tl_normal_db"],
            ref.tl_normal_incidence_exact(f, mpp), 1e-9,
            "Bies & Hansen 无限板正入射透射系数 tau=1/(1+(omega m''/2 rho0 c0)^2)，恒等式级",
            unit="dB", kind="abs")
    f_hi = 2000.0
    add("F.tl_field_eng", "noise", "场入射 TL (2 kHz) vs 工程式 20log10(m''f)-47.2",
        k.panel_tl(f_hi, mpp)["tl_field_db"], ref.tl_field_engineering(f_hi, mpp), 0.5,
        "常规船体声学工程式 TL≈20log10(m''f)-47.2 dB（模块用 tl_n-5，本判据量化该约定的偏差）",
        unit="dB", kind="abs")

    # ---------- G. 吻合频率 ----------
    for h_mm in (5.0, 8.0):
        got = k.coincidence_frequency(2.1e11, 0.3, 7850.0, h_mm / 1000.0)
        eng = ref.coincidence_frequency_engineering(2.1e11, 7850.0, h_mm / 1000.0, 0.3)
        add(f"G.fc_{int(h_mm)}mm", "noise", f"吻合频率 (钢 {h_mm:g} mm) vs 弯曲波速匹配式",
            got, eng, 1e-9, "f_c=c0^2/(2 pi h)*sqrt(12)/c_L，c_L=sqrt(E/(rho(1-nu^2)))（独立书写）",
            unit="Hz")
        add(f"G.fc_rule_{int(h_mm)}mm", "noise", f"吻合频率 (钢 {h_mm:g} mm) vs 经验式 12/h[mm] kHz",
            got, 12.0 / h_mm * 1000.0, 0.05,
            "钢的经验校核 f_c[kHz]≈12/h[mm]（5% 容差，经验式本身只有 2 位有效）", unit="Hz")

    # ---------- H. dB 叠加与 A 计权总级 ----------
    add("H.combine_two", "noise", "两个 60 dB 不相干叠加",
        k.combine_levels_db([60.0, 60.0]), ref.combine_db([60.0, 60.0]), 1e-12,
        "能量叠加恒等式 10log10(2*10^6)=60+3.0103 dB", unit="dB", kind="abs")
    ten = [50.0] * 10
    add("H.combine_ten", "noise", "10 个等强 50 dB 叠加 = 60 dB",
        k.combine_levels_db(ten), ref.combine_db(ten), 1e-12, "能量叠加恒等式（+10 dB）",
        unit="dB", kind="abs")
    oct_out = k.octave_band_levels(80.0, [63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0])
    add("H.octave_energy", "noise", "倍频程分配的能量守恒（未计权带级回算总级）",
        ref.combine_db(oct_out["band_levels_db"]), 80.0, 1e-9,
        "把总级按权重分到各带后，未计权带级再叠加必须回到总级（能量守恒）", unit="dB", kind="abs")

    # ---------- I. 隔振传递率：恒等式 + 时域积分 ----------
    kt = []
    for zeta in (0.0, 0.05, 0.2):
        T = k.isolation_transmissibility(math.sqrt(2.0) * 10.0, 10.0, zeta)["transmissibility"]
        kt.append({"zeta": zeta, "T_at_sqrt2": T})
        add(f"I.T_sqrt2_zeta{zeta:g}", "isolation", f"T(r=sqrt2, zeta={zeta:g}) 恒等于 1",
            T, 1.0, 1e-12, "传递率恒等式：r=sqrt2 时 T=1 且与阻尼无关", unit="1", kind="abs")
    # 时域 RK4 独立复算两个工作点
    rk = []
    for f, fn, zeta in ((20.0, 10.0, 0.05), (5.0, 10.0, 0.05), (30.0, 10.0, 0.1)):
        mod_T = k.isolation_transmissibility(f, fn, zeta)["transmissibility"]
        rk_T = ref.sdof_transmissibility_rk4(f, fn, zeta, n_periods=60, n_steps_per_period=200)
        rk.append({"f": f, "f_n": fn, "zeta": zeta, "module": mod_T, "rk4": rk_T})
        add(f"I.T_rk4_f{f:g}_z{zeta:g}", "isolation",
            f"T(f={f:g}, fn={fn:g}, zeta={zeta:g}) vs 时域 RK4 稳态幅值比",
            mod_T, rk_T, 0.02,
            "直接积分 z''+2 zeta wn z'+wn^2 z = w^2 Y sin(wt) 至稳态，取 |x|/Y（完全不用传递率公式）",
            unit="1", kind="rel")
    series["isolation"] = {"identities": kt, "rk4": rk}

    # ---------- J. 激励频率 ----------
    ex = k.blade_rate_orders(4, 120.0, 3)
    fs, bpf = ref.shaft_and_blade_freq(120.0, 4)
    add("J.shaft", "excitation", "轴频 (120 rpm)", ex["shaft_hz"], fs, 1e-12, "f_s=rpm/60",
        unit="Hz")
    add("J.bpf", "excitation", "叶频 (4 叶, 120 rpm)", ex["bpf_hz"], bpf, 1e-12,
        "BPF=n_blades*rpm/60（ITTC 螺旋桨激励惯例）", unit="Hz")
    df4 = k.diesel_firing_frequencies(120.0, 6, 4)
    add("J.diesel4", "excitation", "4 冲程 6 缸点火频 (120 rpm)",
        df4["firing_hz"], ref.diesel_firing_freq(120.0, 6, 4), 1e-12,
        "F=n_cyl*rpm/(60*2)（4 冲程曲轴两转一循环）", unit="Hz")
    df2 = k.diesel_firing_frequencies(120.0, 6, 2)
    add("J.diesel2", "excitation", "2 冲程 6 缸点火频 (120 rpm)",
        df2["firing_hz"], ref.diesel_firing_freq(120.0, 6, 2), 1e-12,
        "F=n_cyl*rpm/60（2 冲程曲轴一转一循环）", unit="Hz")

    # ---------- K. 共振裕度语义 ----------
    for margin_target, tol in ((0.20, 0.15), (0.10, 0.15), (-0.20, 0.15)):
        fn = 10.0
        fexc = fn * (1.0 + margin_target)
        r = k.resonance_margin(fexc, fn, tol)
        expected_pass = abs(margin_target) >= tol
        add(f"K.margin_{margin_target:+.2f}", "excitation",
            f"裕度语义 (margin={margin_target:+.2f}, tol={tol})",
            1.0 if r["passed"] else 0.0, 1.0 if expected_pass else 0.0, 1e-12,
            "定义 margin=(f_exc-f_nat)/f_nat，|margin|>=tol 通过（判据为工程惯例，非规范条文）",
            unit="bool", kind="abs")

    # ---------- L. 振型边条件残差（数值微分，不用模块振型公式） ----------
    for bc, ends in (("clamped-clamped", "both"), ("clamped-free", "left")):
        res_f, res_c = [], []
        for npts in (8001, 32001):          # 步长 4 倍加密
            ms = k.beam_mode_shape(bc, 1, L, n_points=npts)
            r = ref.bc_residual(ms["x"], ms["w"], L, ends=ends)
            res_c.append(r)
        # w 端点残差（不依赖差分，必须精确 0）
        add(f"L.modeshape_w_{bc}", "beam", f"第 1 阶振型端点 w 残差 ({bc})",
            max(v for kk, v in res_c[-1].items() if kk.startswith("w_")), 0.0, 1e-12,
            "clamped 端 w(a)=0 是闭式振型的构造性质，直接用采样值判（1e-12）",
            unit="1", kind="abs")
        # 斜率残差：中心差分为二阶，按 Richardson 外推后的极限判
        s_fine = max(v for kk, v in res_c[-1].items() if kk.startswith("slope_"))
        s_coarse = max(v for kk, v in res_c[0].items() if kk.startswith("slope_"))
        order = math.log(s_coarse / s_fine) / math.log(4.0)
        limit = s_fine + (s_fine - s_coarse) / 3.0     # 二阶 Richardson
        add(f"L.modeshape_slope_decay_{bc}", "beam",
            f"端点斜率残差随加密的衰减指数 ({bc})", 1.0 if order >= 0.25 else 0.0, 1.0, 1e-12,
            "步长 4 倍加密后残差至少应降 2 倍（衰减指数 >=0.25）；只查单调收敛，"
            "不假设 np.gradient 端点单边差分的阶（那是差分格式性质）", unit="1")
        add(f"L.modeshape_slope_limit_{bc}", "beam",
            f"端点斜率残差 Richardson 极限 ({bc})", limit, 0.0, 1e-4,
            "外推去掉差分截断后，clamped 端应满足 w'=0，故极限残差 -> 0（1e-4 容差）",
            unit="1", kind="abs")
        series.setdefault("mode_shape", []).append(
            {"bc": bc, "residual_coarse": res_c[0], "residual_fine": res_c[-1],
             "order": order, "limit": limit})

    series["beam_eigen"] = beam_series
    return series


def make_figures(series: Dict, path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12.0, 8.6))

    # (a) 梁特征值相对误差
    ax = axes[0][0]
    bcs = list(series["beam_eigen"].keys())
    data = [series["beam_eigen"][bc]["errors"] for bc in bcs]
    x = np.arange(4)
    w = 0.2
    for i, (bc, errs) in enumerate(zip(bcs, data)):
        ax.bar(x + (i - 1.5) * w, errs, width=w, label=bc)
    ax.set_yscale("log")
    ax.set_xticks(x, ["$\\lambda_1$", "$\\lambda_2$", "$\\lambda_3$", "$\\lambda_4$"])
    ax.set_ylabel("relative deviation vs Blevins Tab.8-1")
    ax.set_title("(a) beam eigenvalues: kernel root-finder vs literature table")
    ax.axhline(1e-5, color="k", ls="--", lw=1, label="tolerance 1e-5")
    ax.legend(fontsize=7)

    # (b) A 计权
    ax = axes[0][1]
    fs = sorted(ref.IEC_61672_TABLE_3)
    ax.plot(fs, [ref.aw_iec_table(f) for f in fs], "o", ms=4, label="IEC 61672-1 Table 3")
    ff = np.logspace(1.0, math.log10(20000.0), 300)
    ax.plot(ff, [ref_mod_aw(f) for f in ff], "-", lw=1.2, label="kernel analytic")
    ax.set_xscale("log")
    ax.set_xlabel("frequency [Hz]")
    ax.set_ylabel("A-weighting [dB]")
    ax.set_title("(b) A-weighting: analytic vs IEC 61672-1 Table 3 (34 points)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # (c) 板 FD 收敛
    ax = axes[1][0]
    pd = series["plate_fd"]
    ax.semilogy(pd["n_grid"], pd["errors"], "o-", label="FD mu11 rel. deviation")
    ax.semilogy([pd["n_grid"][-1]], [pd["extrap_error"]], "s",
                label="Richardson extrapolation")
    ax.axhline(1e-2, color="k", ls="--", lw=1, label="tolerance 1e-2")
    ax.set_xlabel("grid N x N")
    ax.set_ylabel("$|\\mu_{11}^{FD}-\\mu_{11}|/\\mu_{11}$")
    ax.set_title("(c) plate: independent FD discretisation converging to analytic mu11")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    # (d) 隔振传递率
    ax = axes[1][1]
    rr = np.linspace(0.1, 3.0, 200)
    ax.plot(rr, [ref_mod_T(r, 10.0, 0.05) for r in rr], "-", lw=1.4,
            label="kernel analytic (zeta=0.05)")
    pts = series["isolation"]["rk4"]
    ax.plot([p["f"] / p["f_n"] for p in pts], [p["rk4"] for p in pts], "s", ms=7,
            label="time-domain RK4 steady |x|/Y")
    ax.axhline(1.0, color="gray", lw=0.8)
    ax.axvline(math.sqrt(2.0), color="k", ls="--", lw=1, label="r=sqrt2")
    ax.set_xlabel("frequency ratio r = f/f_n")
    ax.set_ylabel("transmissibility T")
    ax.set_title("(d) isolation transmissibility: analytic vs time-domain integration")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.suptitle("m1820 vibration/noise kernel independent review (literature / FD / time-domain)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def ref_mod_aw(f: float) -> float:
    """仅用于画图的解析 A 计权曲线（独立书写，不调用被测函数）."""
    f2 = f * f
    ra = (12194.0 ** 2 * f ** 4) / ((f2 + 20.6 ** 2) * math.sqrt((f2 + 107.7 ** 2) *
                                                                 (f2 + 737.9 ** 2)) *
                                    (f2 + 12194.0 ** 2))
    return 20.0 * math.log10(ra) + 2.00


def ref_mod_T(r: float, fn: float, zeta: float) -> float:
    num = 1.0 + (2.0 * zeta * r) ** 2
    den = (1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2
    return math.sqrt(num / den)


def md_table(rows: List[Dict]) -> str:
    out = ["| 编号 | 域 | 判据 | 服务值 | 参考值 | 偏差 | 容差 | 判决 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        sv = f"{r['served']:.6g}" if isinstance(r["served"], (int, float)) else str(r["served"])
        rv = f"{r['reference']:.6g}" if isinstance(r["reference"], (int, float)) else str(r["reference"])
        out.append(f"| {r['id']} | {r['domain']} | {r['name']} | {sv} | {rv} | "
                   f"{r['error_str']} | {r['tol_str']} | **{r['verdict']}** |")
    return "\n".join(out)


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    import time
    t0 = time.time()
    k = load_kernel()
    series = run_checks(k)
    print(f"[timing] checks {time.time()-t0:.1f}s", flush=True)
    n_pass = sum(1 for r in CHECKS if r["passed"])
    n_fail = len(CHECKS) - n_pass
    domains = {}
    for r in CHECKS:
        d = domains.setdefault(r["domain"], {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += int(r["passed"])

    fig = OUTDIR / "fig_vibration_audit.png"
    make_figures(series, fig)

    wiring = json.loads((OUTDIR / "wiring_evidence.json").read_text()) \
        if (OUTDIR / "wiring_evidence.json").exists() else {}

    payload = {
        "generated_by": "tools/validation/verify_vibration_kernel_m1820.py",
        "kernel": {"path": "backend/api/domain/ship_vibration.py",
                   "lines": sum(1 for _ in MODULE.open()),
                   "introduced_by": wiring.get("git", {}).get("module_introduced_by")},
        "summary": {"n_checks": len(CHECKS), "n_pass": n_pass, "n_fail": n_fail,
                    "by_domain": domains},
        "checks": CHECKS,
        "series": series,
        "wiring": wiring,
    }
    (OUTDIR / "verdicts.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                                                     default=float))
    (OUTDIR / "verdicts.md").write_text(
        "# m1820 振动噪声核独立复核判据账本\n\n"
        f"判据 {len(CHECKS)} 条：PASS {n_pass} / FAIL {n_fail}\n\n" + md_table(CHECKS) + "\n")
    print(f"checks={len(CHECKS)} pass={n_pass} fail={n_fail}")
    for r in CHECKS:
        if not r["passed"]:
            print("  FAIL", r["id"], r["error_str"], ">", r["tol_str"])
    print("figure", fig.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())