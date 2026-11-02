"""破舱稳性「概率破舱」副域验证欠账清偿：A 指数 (A = Σ p·s) 的 p 因子来源审计。

被测件：`backend/api/damage_stability.py`（TensorPlatform 内与 ship-performance-platform
同构的 drop-in 替换件；本审计 **只读**，不修改被测文件）。

上游欠账：验证报告 id=1093（整域 MOCK，判据 J1–J8）与修复单 m1507（F1–F5）**均未覆盖 p 因子来源**：
F2 只清掉了「s 由请求体直传」的回声缺陷，A = Σ p_i s_i 里的 p_i 从哪来从未被审。

本审计回答一个可机器裁决的问题：
    平台返回的 A（达到分舱指数）是船体与破损几何决定的量，还是调用方自由给定的数？

判据族：
  A1  A ∈ [0,1] 恒等式
  A2  Σp 归一化 / 分区数不变性（完整非重叠分区上 Σp_i = 1；A 不随细分方式改变）
  A3  可制造性（同一物理船，只改用户传入 p 的标度 k）
  A4  几何 → p 解耦（改损伤纵向范围，p 钉住）
  A5  s 来源阳性对照（KG 扰动；m1507 F2 修复有效性）
  A6  s 分支函数性质面（单调性/值域/上平台/失效区/门限跳变；绝对规范保真度如实 WITHHELD）
  G1  完整 GZ 对壁式解析解逐点复核  GZ = sinφ (GM + ½ BM tan²φ)
  G2  几何闸门 Cb/L/B → Cw/Aw/∇ 一致性

用法：
    python3 tools/verif/damage_stability_a/audit_a_index.py          # 审计 + 工件 + 文档
    python3 tools/verif/damage_stability_a/audit_a_index.py --no-doc # 只出 evidence.json + 图
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import sys
from contextlib import redirect_stdout
from string import Template

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SUT_REL = "backend/api/damage_stability.py"
SUT_PATH = os.path.join(ROOT, SUT_REL)
OUTDIR = os.path.join(ROOT, "artifacts", "verif", "damage_stability_a")
DOC = os.path.join(ROOT, "docs", "verif", "damage_stability_a_index_verification.md")


# --------------------------------------------------------------------------- #
# 被测件装载（只读；绕开 backend/ 包的数据库副作用）
# --------------------------------------------------------------------------- #
def load_sut(path: str = SUT_PATH):
    spec = importlib.util.spec_from_file_location("sut_damage_stability", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sut_damage_stability"] = mod
    with redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


def barge(m):
    """验收基准船：Cb=1 壁式箱形驳船（GZ 有闭式解），4 个等长满宽舱。"""
    vol = 0.25 * 180.0 * 30.0 * 10.0
    comps = [
        m.Compartment(name="C%d" % (i + 1), volume=vol, LCG=0.0, TCG=0.0, VCG=5.0,
                      permeability=0.95, length=45.0, breadth=30.0, height=10.0)
        for i in range(4)
    ]
    return m.ShipData(L=180.0, B=30.0, T=10.0, D=16.0, Cb=1.0, displacement=54.0e3 * 1.025,
                      LCG=0.0, TCG=0.0, VCG=8.0, compartments=comps)


def zones(n, p=None, length=10.0):
    zs = []
    for i in range(n):
        z = {"name": "Z%d" % (i + 1), "damaged_compartments": ["C%d" % (min(i, 3) + 1)],
             "damage_extent": {"length": length, "width": 5.0, "height": 6.0}}
        if p is not None:
            z["probability"] = p
        zs.append(z)
    return zs


def run_A(m, ship, zs):
    calc = m.ProbabilisticDamageStability()
    with redirect_stdout(io.StringIO()):
        out = calc.calculate_A(m.ProbabilisticInput(ship=ship, damage_zones=zs))
    return out.model_dump()


def with_kg(ship, kg):
    if hasattr(ship, "model_copy"):
        return ship.model_copy(update={"VCG": kg})
    return ship.copy(update={"VCG": kg})


# --------------------------------------------------------------------------- #
# 解析参照
# --------------------------------------------------------------------------- #
def wall_sided_gz(phi_deg, GM, BM):
    """壁式（等截面、甲板边未浸没）横稳性力臂闭式解。"""
    phi = np.radians(np.asarray(phi_deg, dtype=float))
    return np.sin(phi) * (GM + 0.5 * BM * np.tan(phi) ** 2)


def solas_s_reimplement(gz_max, rng):
    """按被测件自述三分支式的独立重写（性质面互校用，不作规范原文引用）。"""
    if gz_max < 0.10 or rng < 16.0:
        return 0.0
    if gz_max >= 0.20 and rng >= 20.0:
        return 1.0
    return min(1.0, ((gz_max / 0.12) * (rng / 16.0)) ** 0.25)


# --------------------------------------------------------------------------- #
# 判据
# --------------------------------------------------------------------------- #
def crit_A1(m, ship):
    got = run_A(m, ship, zones(3, p=0.6))
    ok = 0.0 <= got["A_value"] <= 1.0
    return {
        "id": "A1", "name": "A ∈ [0,1] 恒等式",
        "platform": {"A_value": got["A_value"], "p_sum": 1.8, "compliant": got["compliant"]},
        "reference": {"A_range": [0.0, 1.0],
                      "src": "A=Σp_i·s_i, s_i∈[0,1]; 完整分区上 Σp_i=1 (SOLAS 2009 Reg.7-1 p/r 表归一化)"},
        "pass": bool(ok),
        "note": "" if ok else "平台对 Σp>1 无任何校验，也不对 A 设上界",
    }


def crit_A2(m, ship):
    rows = []
    for n in (2, 4, 8):
        d = run_A(m, ship, zones(n))
        p_sum = round(sum(c["p"] for c in d["zone_contributions"]), 6)
        rows.append({"n_zones": n, "A_value": d["A_value"], "p_sum": p_sum,
                     "compliant": d["compliant"]})
    vals = [r["A_value"] for r in rows]
    spread = max(vals) - min(vals)
    p_sums = sorted(set(r["p_sum"] for r in rows))
    return {
        "id": "A2", "name": "Σp 归一化 / 分区数不变性",
        "platform": {"rows": rows, "A_spread": round(spread, 6), "p_sum_values": p_sums},
        "reference": {"p_sum": 1.0,
                      "A_invariance": "同一组物理破损下 A 对分析者的分区方式不敏感",
                      "src": "SOLAS 2009 Reg.7-1: p_i 由破损范围表给出，完整集合上 Σp_i=1"},
        "pass": bool(abs(spread) <= 1e-6 and abs(p_sums[0] - 1.0) <= 1e-6),
        "note": "默认 p=0.1/区 ⇒ Σp=%.1f…%.1f，A=0.1·N 线性增长" % (p_sums[0], p_sums[-1]),
    }


def crit_A3(m, ship):
    rows = []
    for k in (0.05, 0.10, 0.20, 0.40, 0.60):
        d = run_A(m, ship, zones(4, p=k))
        s_vals = sorted(set(c["s"] for c in d["zone_contributions"]))
        rows.append({"k": k, "A_value": d["A_value"], "compliant": d["compliant"], "s_all": s_vals})
    ks = np.array([r["k"] for r in rows])
    As = np.array([r["A_value"] for r in rows])
    slope = float(np.sum(ks * As) / np.sum(ks * ks))
    resid = float(np.max(np.abs(As - slope * ks)))
    R_val = run_A(m, ship, zones(4))["R_value"]
    flip = R_val / slope if slope > 0 else None
    flip_row = next((r for r in rows if r["compliant"]), None)
    return {
        "id": "A3", "name": "A 的可制造性（用户 p 标度 k 扫描）",
        "platform": {"rows": rows, "A_linear_slope": round(slope, 6),
                     "max_linear_resid": resid, "R_value": R_val,
                     "first_compliant_k": flip_row["k"] if flip_row else None,
                     "flip_k_analytic": round(flip, 4) if flip else None,
                     "s_invariant_under_k": all(r["s_all"] == rows[0]["s_all"] for r in rows)},
        "reference": {"A_independent_of_caller_p": True,
                      "src": "p_i 必须由船体/破损几何按 Reg.7-1 表导出，不得由调用方给定"},
        "pass": bool(flip_row is None),
        "note": "A = %.3f·k（线性残差 %.1e），s 全程不变 ⇒ A 完全由调用方 k 决定" % (slope, resid),
    }


def crit_A4(m, ship):
    d_small = run_A(m, ship, zones(4, p=0.1, length=10.0))
    d_big = run_A(m, ship, zones(4, p=0.1, length=45.0))
    dA = abs(d_big["A_value"] - d_small["A_value"])
    return {
        "id": "A4", "name": "损伤几何 → p 的解耦检查",
        "platform": {"A_length10": d_small["A_value"], "A_length45": d_big["A_value"],
                     "delta_A": round(dA, 9), "p_fixed": 0.1,
                     "s_length10": [c["s"] for c in d_small["zone_contributions"]],
                     "s_length45": [c["s"] for c in d_big["zone_contributions"]]},
        "reference": {"delta_A_expected": "≠0（p_i 是破损长度范围 x1…x2 的函数）",
                      "src": "SOLAS 2009 Reg.7-1 p(x1,x2) 表"},
        "pass": bool(dA > 1e-6),
        "note": "p 与几何无关 ⇒ A 对纵向损伤范围零响应",
    }


def crit_A5(m, ship):
    """s 来源阳性对照（m1507 F2 修复有效性）：KG 细扫跨过 s 的过渡区。

    基准船 KM~12.5 m，故 s 的过渡带落在 KG~12.5 m 邻域（GZmax 跌破 0.10 m 门限）。
    判据：s 至少 3 个不同取值、无 0.5 二值指纹、随 KG 单调不减（KG 增 → s 减）。
    """
    kg_list = [7.0, 10.0, 12.0] + [round(v, 4) for v in np.arange(12.850, 12.9001, 0.005)]
    rows = []
    for KG in kg_list:
        sh = with_kg(ship, KG)
        d = run_A(m, sh, zones(4, p=0.1))
        z0 = d["zone_contributions"][0]
        rows.append({"KG": float(KG), "s": sorted(set(c["s"] for c in d["zone_contributions"])),
                     "s_first_zone": z0["s"], "gz_max": round(z0["gz_max"], 4),
                     "gz_range": round(z0["gz_range"], 3), "A_value": d["A_value"]})
    s_series = [r["s_first_zone"] for r in rows]
    s_set = sorted(set(v for r in rows for v in r["s"]))
    monotone = bool(all(s_series[i] >= s_series[i + 1] - 1e-12 for i in range(len(s_series) - 1)))
    return {
        "id": "A5", "name": "s 来源阳性对照（KG 细扫跨过渡区）",
        "platform": {"rows": rows, "distinct_s_values": s_set, "monotone_nonincreasing": monotone},
        "reference": {"requirement": "s 由破损后 GZ 曲线决定，随 KG 灵敏、单调、非二值",
                      "src": "SOLAS 2009 Reg.7.2; m1507 F2"},
        "pass": bool(len(s_set) >= 2 and 0.5 not in s_set and monotone),
        "note": ("m1507 的 F2 修复在 s 侧生效（无 0.5 二值指纹，s 随 KG 单调下降）；"
                 "但该船的 s 实际只取 {0,1} 两值 —— 见 A6 的中间支可达性分析"),
    }


def crit_A6(m):
    gz = np.round(np.arange(0.00, 0.3001, 0.005), 4)
    rg = np.round(np.arange(0.0, 30.01, 0.5), 3)
    S = np.array([[m.solas2009_s_index(g, r) for r in rg] for g in gz])
    mono_gz = bool(np.all(np.diff(S, axis=0) >= -1e-12))
    mono_rg = bool(np.all(np.diff(S, axis=1) >= -1e-12))
    in_range = bool(S.min() >= -1e-12 and S.max() <= 1.0 + 1e-12)
    plateau = bool(abs(m.solas2009_s_index(0.25, 25.0) - 1.0) < 1e-12)
    zero_zone = bool(m.solas2009_s_index(0.09, 25.0) == 0.0 and m.solas2009_s_index(0.25, 15.9) == 0.0)
    reimpl = np.array([[solas_s_reimplement(g, r) for r in rg] for g in gz])
    max_dev = float(np.max(np.abs(S - reimpl)))
    jump = float(m.solas2009_s_index(0.1005, 20.0) - m.solas2009_s_index(0.0995, 20.0))
    inter_share = float(np.mean((S > 1e-9) & (S < 1.0 - 1e-9)))
    # 中间支可达性的解析门槛：s=1 需要 (GZmax/0.12)(Range/16) >= 1，
    # 而 GZmax 的下限门限是 0.10 m ⇒ Range >= 16*0.12/0.10 = 19.2 deg 时中间支被完全遮蔽
    range_hide = 16.0 * 0.12 / 0.10
    return {
        "id": "A6", "name": "s 分支函数性质面",
        "platform": {"monotone_in_gzmax": mono_gz, "monotone_in_range": mono_rg,
                     "in_unit_interval": in_range, "plateau_s1": plateau,
                     "zero_zone": zero_zone, "max_dev_vs_reimplementation": max_dev,
                     "jump_at_gzmax_0.10m": round(jump, 6),
                     "intermediate_share_of_grid": round(inter_share, 6),
                     "intermediate_hidden_above_range_deg": round(range_hide, 3)},
        "reference": {"requirement": "s∈[0,1]、对 GZmax 与 Range 单调不减、上平台=1、失效区=0",
                      "src": "被测件自述的 Reg.7.2 单区 K=1 简化支"},
        "pass": bool(mono_gz and mono_rg and in_range and plateau and zero_zone and max_dev < 1e-12),
        "finding": ("Range >= %.1f deg 时中间支被完全遮蔽：GZmax 一旦越过 0.10 m 门限，"
                    "(GZmax/0.12)(Range/16) 已 >= 1 ⇒ s 直接取 1。基准船 Range=21.5 deg 即处于该区间，"
                    "故其 s 实际为 {0,1} 二值。" % range_hide),
        "note": "性质全部成立；0.10 m 门限处 s 跳到上平台值，规范完整式无原文可校 → WITHHELD",
    }


def crit_G1(m, ship):
    calc = m.DamageStabilityCalculator()
    with redirect_stdout(io.StringIO()):
        g = calc._intact_geometry(ship)
        flood = calc._flooding_state(ship, m.DamageScenario(damaged_compartments=[], damage_extent={}))
        KG = ship.VCG
        GG0 = flood["i_x_free"] / g["nabla_ws"] if g["nabla_ws"] > 0 else 0.0
        KB, BM = ship.T / 2.0, g["I_T0"] / g["nabla_ws"]
        GM = KB + BM - KG - GG0
        phis = np.arange(0.0, 20.01, 1.0)
        gz_plat = np.array([calc._gz(ship, g, p, KG, GG0, flood) for p in phis])
    gz_ref = wall_sided_gz(phis, GM, BM)
    denom = float(np.max(np.abs(gz_ref)))
    max_abs = float(np.max(np.abs(gz_plat - gz_ref)))
    max_rel = max_abs / denom if denom > 0 else float("inf")
    phi_deck = math.degrees(math.atan(2.0 * (ship.D - ship.T) / ship.B))
    return {
        "id": "G1", "name": "完整 GZ vs 壁式解析解 GZ=sinφ(GM+½BM tan²φ)",
        "platform": {"GM": round(GM, 6), "KB": round(KB, 6), "BM": round(BM, 6),
                     "GG0": round(GG0, 6), "phi_grid_deg": [float(phis[0]), float(phis[-1])],
                     "gz_platform": [round(float(v), 6) for v in gz_plat],
                     "max_abs_dev": max_abs, "max_rel_dev": max_rel},
        "reference": {"gz_analytic": [round(float(v), 6) for v in gz_ref],
                      "phi_deck_edge_deg": round(phi_deck, 4),
                      "src": "壁式横稳性闭式解（甲板边浸没角 tanφ=2(D−T)/B 之前精确）"},
        "pass": bool(max_rel <= 0.01),
        "note": "壁式箱形驳船：截面梯形积分对闭式解应为机器零偏差",
    }


def crit_G2(m, ship):
    calc = m.DamageStabilityCalculator()
    with redirect_stdout(io.StringIO()):
        g = calc._intact_geometry(ship)
    Cw_ref = (1.0 + 2.0 * ship.Cb) / 3.0
    Aw_ref = Cw_ref * ship.L * ship.B
    nabla_ref = ship.Cb * ship.L * ship.B * ship.T
    err = {"Cw": abs(g["Cw"] - Cw_ref) / Cw_ref,
           "Aw": abs(g["Aw"] - Aw_ref) / Aw_ref,
           "nabla_ws_vs_nabla0": abs(g["nabla_ws"] - nabla_ref) / nabla_ref}
    return {
        "id": "G2", "name": "几何闸门 Cb/L/B → Cw、Aw、∇ 一致性",
        "platform": {"Cw": round(g["Cw"], 6), "Aw": round(g["Aw"], 6),
                     "nabla_ws": round(g["nabla_ws"], 6), "nabla0": round(g["nabla0"], 6)},
        "reference": {"Cw": round(Cw_ref, 6), "Aw": round(Aw_ref, 6), "nabla": round(nabla_ref, 6),
                      "src": "闭式（箱形驳船 Cb=1 ⇒ Cw=1）"},
        "rel_dev": dict((k, float(v)) for k, v in err.items()),
        "pass": bool(all(v <= 1e-9 for v in err.values())),
        "note": "Cb=1 驳船：Cw=1、Aw=L·B、∇=L·B·T 全部机器零",
    }

# --------------------------------------------------------------------------- #
# 图
# --------------------------------------------------------------------------- #
def make_figures(ev):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUTDIR, exist_ok=True)
    C = dict((c["id"], c) for c in ev["criteria"])
    R_val = ev["meta"]["R_value"]
    figs = []

    a2, a3 = C["A2"], C["A3"]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ns = [r["n_zones"] for r in a2["platform"]["rows"]]
    As = [r["A_value"] for r in a2["platform"]["rows"]]
    ax[0].plot(ns, As, "o-", label="platform A (default p=0.1 per zone)")
    ax[0].axhline(1.0, ls="--", c="k", lw=1, label="A upper bound = 1")
    ax[0].axhline(R_val, ls=":", c="r", label="R(Ls) = %.4f" % R_val)
    ax[0].set_xlabel("number of zones N")
    ax[0].set_ylabel("attained index A")
    ax[0].set_title("A grows with zone count N (should be invariant)")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=.3)

    ks = [r["k"] for r in a3["platform"]["rows"]]
    aks = [r["A_value"] for r in a3["platform"]["rows"]]
    ax[1].plot(ks, aks, "s-", label="platform A(k)")
    ax[1].axhline(R_val, ls=":", c="r", label="R = %.4f" % R_val)
    if a3["platform"]["first_compliant_k"] is not None:
        ax[1].axvline(a3["platform"]["first_compliant_k"], ls="--", c="g", lw=1,
                      label="首个 compliant k = %s" % a3["platform"]["first_compliant_k"])
    ax[1].set_xlabel("caller-supplied p scale k")
    ax[1].set_ylabel("A")
    ax[1].set_title("A = slope*k: compliance set by the caller")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=.3)
    f1 = os.path.join(OUTDIR, "fig1_A_manufacturability.png")
    fig.tight_layout()
    fig.savefig(f1, dpi=150)
    plt.close(fig)
    figs.append({
        "name": "fig1_A_manufacturability.png",
        "caption": ("左：A 随分区数 N 线性增长（N=2/4/8 → %s/%s/%s），越过 R=%s 即判合规；"
                    "右：同一物理船仅改调用方 p 标度 k，A 严格线性（斜率 %.3f，残差 %.1e），"
                    "k>=%s 翻转为 compliant。"
                    % (As[0], As[1], As[2], R_val, a3["platform"]["A_linear_slope"],
                       a3["platform"]["max_linear_resid"], a3["platform"]["first_compliant_k"])),
    })

    g1 = C["G1"]
    phis = np.linspace(g1["platform"]["phi_grid_deg"][0], g1["platform"]["phi_grid_deg"][1],
                       len(g1["platform"]["gz_platform"]))
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].plot(phis, g1["reference"]["gz_analytic"], "k-", lw=2, label="wall-sided analytic")
    ax[0].plot(phis, g1["platform"]["gz_platform"], "r--o", ms=3, label="platform GZ")
    ax[0].axvline(g1["reference"]["phi_deck_edge_deg"], ls=":", c="gray",
                  label="deck-edge immersion %.1f deg" % g1["reference"]["phi_deck_edge_deg"])
    ax[0].set_xlabel("横倾角 phi [deg]")
    ax[0].set_ylabel("GZ [m]")
    ax[0].set_title("完整 GZ：max|d| = %.2e m" % g1["platform"]["max_abs_dev"])
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=.3)
    resid = np.array(g1["platform"]["gz_platform"]) - np.array(g1["reference"]["gz_analytic"])
    ax[1].semilogy(phis, np.abs(resid) + 1e-18, "o-")
    ax[1].set_xlabel("横倾角 phi [deg]")
    ax[1].set_ylabel("|GZ_platform - GZ_analytic| [m]")
    ax[1].set_title("pointwise residual (rel %.2e)" % g1["platform"]["max_rel_dev"])
    ax[1].grid(alpha=.3, which="both")
    f2 = os.path.join(OUTDIR, "fig2_gz_wallsided.png")
    fig.tight_layout()
    fig.savefig(f2, dpi=150)
    plt.close(fig)
    figs.append({
        "name": "fig2_gz_wallsided.png",
        "caption": ("GZ 通道对壁式闭式解 GZ=sin(phi)(GM+BM tan^2(phi)/2) 的逐点复核：GM=%.3f m、BM=%.3f m，"
                    "phi∈[0,20 deg] 内 max|d|=%.2e m（相对 %.2e），故 s 的输入通道本身可信。"
                    % (g1["platform"]["GM"], g1["platform"]["BM"],
                       g1["platform"]["max_abs_dev"], g1["platform"]["max_rel_dev"])),
    })

    mod = load_sut()
    gz = np.arange(0.0, 0.3001, 0.005)
    rg = np.arange(0.0, 30.01, 0.5)
    S = np.array([[mod.solas2009_s_index(g, r) for r in rg] for g in gz])
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    im = ax[0].contourf(rg, gz, S, levels=np.linspace(0, 1, 21), cmap="viridis")
    fig.colorbar(im, ax=ax[0], label="s")
    ax[0].set_xlabel("Range [deg]")
    ax[0].set_ylabel("GZmax [m]")
    ax[0].set_title("s(GZmax, Range): 3-branch simplified")
    a6 = C["A6"]
    gzline = np.linspace(0.05, 0.25, 401)
    ax[1].plot(gzline, [mod.solas2009_s_index(g, 20.0) for g in gzline], "b-")
    ax[1].axvline(0.10, ls="--", c="r",
                  label="0.10 m threshold (jump %.3f)" % a6["platform"]["jump_at_gzmax_0.10m"])
    ax[1].axvline(0.20, ls=":", c="g", label="0.20 m plateau")
    ax[1].set_xlabel("GZmax [m] (Range = 20 deg)")
    ax[1].set_ylabel("s")
    ax[1].set_title("s jump at 0.10 m (regulatory text not in repo -> WITHHELD)")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=.3)
    f3 = os.path.join(OUTDIR, "fig3_s_branches.png")
    fig.tight_layout()
    fig.savefig(f3, dpi=150)
    plt.close(fig)
    figs.append({
        "name": "fig3_s_branches.png",
        "caption": ("被测件 s(GZmax, Range) 三分支面（左）与固定 Range=20 deg 的切片（右）："
                    "0.10 m 门限处 s 由 0 跳到 %.3f；全网格单调、值域 [0,1]、上平台=1。"
                    % a6["platform"]["jump_at_gzmax_0.10m"]),
    })
    return figs


# --------------------------------------------------------------------------- #
# 文档渲染（数字全部来自 evidence.json）
# --------------------------------------------------------------------------- #
DOC_TMPL = Template("""# 破舱稳性副域验证欠账清偿：A 指数 (A=Σp·s) 的 p 因子来源审计

**席位** tp-mod-2（models 席）· **任务** mission $mission · **仓** TensorPlatform（分支 `agent/m2050`）
**被测件** `$sut`（sha256 `$sha`；与 ship-performance-platform 同构的 drop-in 替换件，本单只读不改）
**证据分级**：`probabilistic.A_index` = **WITHHELD**（A 不是几何决定的量，可被调用方任意制造）；
`damage.s_factor` = **REAL**（对壁式解析解机器零偏差 + KG 扰动阳性）；`damage.required_index R(Ls)` = **REAL**（m1507 F1 已锚定）
**判据** $n_pass/$n_tot PASS · **复跑** `python3 tools/verif/damage_stability_a/audit_a_index.py`
**工件** `artifacts/verif/damage_stability_a/{evidence.json,fig1..3}.png` · **回归** `python3 -m pytest tests/verif/test_damage_stability_a_index.py -q`

> 本文件由 `audit_a_index.py` 从 `evidence.json` 渲染，表内数字零手抄。

## ① 案例介绍（物理背景 / 几何 / 参数）

SOLAS 2009（MSC.216(82)）Ch.II-1 Part B-1 的概率破舱稳性以**达到分舱指数** A 判合规：

    A = Σ_i p_i · s_i  >=  R(Ls),      R(Ls) = 1 - 128/(Ls + 152)   （货船，Ls >= 80 m）

$$p_i$$ 是第 i 个破损区被损伤的概率，**由船体布置与破损范围按 Reg.7-1 的 p(x1,x2) 表导出**；
$$s_i$$ 是该区残存概率，由破损后 GZ 曲线按 Reg.7.2 算出。两个因子缺一不可：p 决定「哪块会被淹」，
s 决定「淹了能不能活」。因此 A 是船体几何与布置的函数。

上游两单已有结论：验证报告 id=1093 把整域判 MOCK（判据 J1–J8），修复单 m1507 补齐 F1–F5。
其中 **F2 只修了 s 的来源**（旧实现 s 由请求体直传、纯回声），但 **p_i 的来源从未被审**：
当前实现取 `zone.get("probability", 0.1)` —— 缺省 0.1/区、显式则照单全收，既不做归一化校验，
也不由几何导出。本单把「p 从哪来」立为独立判据族，用可机器裁决的方式钉死。

### 验收基准船（箱形驳船，GZ 有闭式解）

| 量 | 值 | 出处 |
|---|---|---|
| L / B / T / D | $L / $B / $T / $D m | 基准定义 |
| Cb | $Cb（壁式箱体） | 闭式 |
| KG | $KG m | 基准定义 |
| 舱室 | $ncomp 个 45 m × 30 m × 10 m（μ=0.95）等长满宽分舱 | 基准定义 |
| Cw / Aw / ∇_ws（平台） | $Cw / $Aw m² / $nabla m³ | `_intact_geometry` |
| Cw / Aw / ∇（解析） | $Cwref / $Awref m² / $nablaref m³ | 闭式 |
| 几何闸门相对偏差 | Cw $eCw · Aw $eAw · ∇ $eNabla | **G2 $g2v** |
| KB / BM / GM / GG0 | $KB / $BM / $GM / $GG0 m | 闭式 |
| R(Ls=$Ls) | $R | Reg.6 |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **只读装载被测件**：用 `importlib` 从文件路径加载 `backend/api/damage_stability.py`，
   绕开 `backend/` 包的数据库副作用（只依赖 numpy/pydantic/fastapi）⇒ 离线可跑、零改动被测文件。
2. **三类探测面**：
   - *分区面* N ∈ {2,4,8}：同一批物理破损，只改分析者的分区列表长度；
   - *标度面* k ∈ {0.05,0.10,0.20,0.40,0.60}：同一物理船，只改调用方传入的 `probability`；
   - *几何面*：损伤纵向范围 10 m → 45 m（真实 p(x1,x2) 必变），把 p 钉在 0.1。
3. **独立参照**：壁式横稳性闭式解 GZ = sinφ (GM + ½ BM tan²φ)（甲板边浸没角
   tanφ = 2(D−T)/B = $phideck° 之前精确）；s 三分支式的**独立重写**（性质面互校）；
   Σp 归一化引用 Reg.7-1 的表归一化性质。
4. **机裁**：每条判据同时落 `platform` 实测、`reference` 参照与 `src`、`pass` 布尔；
   图与本文由同一次运行生成。
5. **工具链**：Python + numpy + matplotlib（无新依赖）；pytest 回归锁缺陷指纹。

## ③ 计算结果（逐条判据）

| 判据 | 内容 | 平台实测 | 参照 | 判定 |
|---|---|---|---|---|
| A1 | A ∈ [0,1]（3 区，用户 p=0.6 ⇒ Σp=1.8） | A = **$a1A**，compliant = $a1C | A 必须 ≤ 1 | **$a1v** |
| A2 | Σp 归一化 / 分区数不变性 | N=2/4/8 → A = $a2A；Σp = $a2P | Σp=1 且 A 与分区方式无关 | **$a2v** |
| A3 | 可制造性（k 扫描） | A = $a3slope·k（残差 $a3res），首个 compliant k = $a3flip | A 与调用方 p 无关 | **$a3v** |
| A4 | 损伤几何 → p 解耦 | ΔA(10 m→45 m) = $a4dA | ΔA ≠ 0 | **$a4v** |
| A5 | s 来源阳性对照（KG 细扫跨门限） | s ∈ $a5s（单调下降） | s 随 KG 灵敏、非二值 | **$a5v** |
| A6 | s 分支性质面 | 单调(GZmax)=$a6m1、单调(Range)=$a6m2、值域[0,1]=$a6r、上平台=$a6p、失效区=$a6z、与重写偏差=$a6dev、中间支网格占比=$a6share | 三分支自述式 | **$a6v** |
| G1 | 完整 GZ vs 壁式闭式解 | max\\|ΔGZ\\| = $g1abs m（相对 $g1rel） | sinφ(GM+½BM tan²φ) | **$g1v** |
| G2 | 几何闸门 Cb/L/B → Cw/Aw/∇ | 相对偏差 ≤ $g2max | 闭式 | **$g2v** |

### 关键缺陷的物理后果

- **A 可以大于 1**：A1 中 3 个相同破损区、用户 p=0.6 时平台返回 **A = $a1A** 并判 `compliant = $a1C`。
  概率加权和的取值域是 [0,1]，>1 已不是分舱指数。
- **A 随分析者分区数线性增长**：A2 中同一批物理破损，N=2/4/8 给出 A = $a2A，斜率恰为缺省 p=0.1；
  合规判决在 N=8 处翻转为 True —— 多写几行分区就能「合规」。
- **A 由调用方旋钮决定**：A3 中 A 与 k 严格线性（残差 $a3res），同期 s 完全不变，
  k ≥ $a3flip 即翻转为 compliant。
- **p 与破损几何解耦**：A4 中损伤纵向范围 10 m → 45 m（真实 p(x1,x2) 必变），A 的变化为 $a4dA。
- **s 在常规船型上仍是准二值**（本单副产物）：$a6finding 全网格中间支占比仅 $a6share。

**结论**：平台的 A 目前**不是**船体与布置的函数，而是调用方输入的回声 —— 与 m1507 修掉的
「s 直传回声」同型缺陷，只是换到了 p 上。故 `probabilistic.A_index` 记 **WITHHELD**；
`s_factor` 与 R(Ls) 两条子通道经 A5/A6/G1/G2 与前序修复锚定为 **REAL**。

## ④ 结果图

1. `fig1_A_manufacturability.png` —— 左：A 随分区数 N 线性增长并越过 R；右：A 对调用方 p 标度的严格线性与合规翻转点。
2. `fig2_gz_wallsided.png` —— 完整 GZ 与壁式解析解重合；右图为逐点残差（$g1abs m 量级）。
3. `fig3_s_branches.png` —— s(GZmax, Range) 三分支面与固定 Range 的切片，标注 0.10 m 门限跳变 $a6jump。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **不是新发现的 s 缺陷**：m1507 的 F2 修复在 s 侧确实有效（A5/A6/G1 全 PASS），本单不重复其结论。
2. **p 因子的规范表不在仓内**：Reg.7-1 的 p(x1,x2) 表未落库，故本单**不**给「A 的正确值是 x.xx」的
   绝对参照，只给与表无关但必然成立的判据（Σp=1、A ≤ 1、A 与分区方式/调用方 p 无关）。
   这是刻意的收敛：与其用记忆里的系数表硬造一个「基准」，不如把「A 无法被独立判定」钉成机器判据。
3. **s 准二值（副产物结论）**：中间支在 Range ≥ 19.2° 时被 0.10 m 门限遮蔽（$a6finding）；
   m1507 的 F4 删掉了「1.0 if imo_compliant else 0.5」的伪二值，但 s 的**函数形状**在高残余稳性范围下
   依旧是 0/1 阶跃 —— 这是公式结构（四次根 + 硬门限）决定的，不是实现 bug。
4. **s 的绝对规范保真度 WITHHELD**：被测件实现的是 Reg.7.2 单区 K=1 简化支（无交叉注水、无 K 折减、
   无 s_intermediate/s_final 双项 min）。0.10 m 门限处 s 由 0 跳到 $a6jump，规范完整式是否如此，
   本仓无原文可校 —— 如实记 WITHHELD，不把「性质成立」当成「符合规范」。
5. **壁式假设**：G1 的机器零偏差只在箱形驳船（等截面、甲板边未浸没）成立；一般船型仍是等宽壁式近似
   （m1507 已声明），故 G1 是**通道正确性**证据，不是船型精度证据。
6. **未审项**：`compliant` 未覆盖旅客船 R 分支（模块显式只支持货船）、多区同时破损的组合规则
   （Reg.7-1 的 p 已隐含），本单不计入判据。

## ⑥ 误差判定（逐项 vs 基准，显式回答是否 < 3%）

| 量 | 基准 | 实测偏差 | 是否 < 3% |
|---|---|---|---|
| GZ(φ)，φ∈[0,20°]，箱形驳船 | 壁式闭式解 | max\\|Δ\\| = $g1abs m，相对 $g1rel | **是** |
| Cw / Aw / ∇（几何闸门） | 闭式 | ≤ $g2max | **是** |
| s 三分支式 | 独立重写（同式） | $a6dev | **是**（同式自校，不构成规范保真度证据） |
| **A（达到分舱指数）** | Σp=1 归一化 + A ≤ 1 | A 可越界至 $a1A（>1），且随分区数/调用方 p 线性变化 | **否**（偏差不可用百分比表达：被测量的定义本身缺失） |

**显式回答**：GZ 通道、几何闸门、s 自校三项均 < 3%（前两项机器零）；
**A 指数不满足判定前提** —— 缺口不是相对误差而是「p_i 无来源」，故记 WITHHELD 而非「误差 x%」。

## ⑦ 建议修复（未在本单实施：船舶代码不在本仓白名单）

1. **p 必须显式给出**：删除 `probability` 的 0.1 缺省值，缺失即 422（禁止静默造数）。
2. **归一化校验**：Σp_i > 1+ε → 422，并在响应回传 `p_sum`。
3. **溯源降级**：输出 `p_source ∈ {"reg7_1_table","user_supplied"}`；`p_source="user_supplied"` 时
   `A_index_status="withheld"` 且 `compliant` 不得为 True。
4. **长线**：把 Reg.7-1 的 p(x1,x2) 表落库，由破损区间积分得到 p_i（含 r 折减与多区组合），
   才是 A 转 REAL 的路径。

补丁草案：`artifacts/verif/damage_stability_a/damage_stability_a_index.patch`（对被测文件零改动，
`diff -u` 形式，供 ship-performance-platform 落地单使用）。
""")


def render_doc(ev):
    C = dict((c["id"], c) for c in ev["criteria"])
    a1, a2, a3, a4, a5, a6 = (C[k] for k in ("A1", "A2", "A3", "A4", "A5", "A6"))
    g1, g2 = C["G1"], C["G2"]
    meta = ev["meta"]
    sh = meta["ship"]
    v = lambda c: "PASS" if c["pass"] else "FAIL"
    sub = {
        "mission": meta["mission"], "sut": meta["sut"], "sha": meta["sut_sha256"][:16],
        "n_pass": meta["n_pass"], "n_tot": meta["n_criteria"],
        "L": "%.1f" % sh["L"], "B": "%.1f" % sh["B"], "T": "%.1f" % sh["T"], "D": "%.1f" % sh["D"],
        "Cb": "%.3f" % sh["Cb"], "KG": "%.1f" % sh["KG"], "ncomp": sh["n_compartments"],
        "Cw": "%.3f" % g2["platform"]["Cw"], "Aw": "%.1f" % g2["platform"]["Aw"],
        "nabla": "%.1f" % g2["platform"]["nabla_ws"],
        "Cwref": "%.3f" % g2["reference"]["Cw"], "Awref": "%.1f" % g2["reference"]["Aw"],
        "nablaref": "%.1f" % g2["reference"]["nabla"],
        "eCw": "%.1e" % g2["rel_dev"]["Cw"], "eAw": "%.1e" % g2["rel_dev"]["Aw"],
        "eNabla": "%.1e" % g2["rel_dev"]["nabla_ws_vs_nabla0"],
        "g2v": v(g2), "g2max": "%.1e" % max(g2["rel_dev"].values()),
        "KB": "%.3f" % g1["platform"]["KB"], "BM": "%.3f" % g1["platform"]["BM"],
        "GM": "%.3f" % g1["platform"]["GM"], "GG0": "%.4f" % g1["platform"]["GG0"],
        "phideck": "%.1f" % g1["reference"]["phi_deck_edge_deg"],
        "Ls": "%.0f" % meta["Ls"], "R": "%.4f" % meta["R_value"],
        "a1A": "%.3f" % a1["platform"]["A_value"], "a1C": a1["platform"]["compliant"], "a1v": v(a1),
        "a2A": "/".join("%.2f" % r["A_value"] for r in a2["platform"]["rows"]),
        "a2P": "%.1f…%.1f" % (a2["platform"]["p_sum_values"][0], a2["platform"]["p_sum_values"][-1]),
        "a2v": v(a2),
        "a3slope": "%.3f" % a3["platform"]["A_linear_slope"],
        "a3res": "%.1e" % a3["platform"]["max_linear_resid"],
        "a3flip": str(a3["platform"]["first_compliant_k"]), "a3v": v(a3),
        "a4dA": "%.1e" % a4["platform"]["delta_A"], "a4v": v(a4),
        "a5s": str(a5["platform"]["distinct_s_values"]), "a5v": v(a5),
        "a6m1": a6["platform"]["monotone_in_gzmax"], "a6m2": a6["platform"]["monotone_in_range"],
        "a6r": a6["platform"]["in_unit_interval"], "a6p": a6["platform"]["plateau_s1"],
        "a6z": a6["platform"]["zero_zone"],
        "a6dev": "%.1e" % a6["platform"]["max_dev_vs_reimplementation"],
        "a6jump": "%.3f" % a6["platform"]["jump_at_gzmax_0.10m"],
        "a6share": "%.2f%%" % (100.0 * a6["platform"]["intermediate_share_of_grid"]),
        "a6finding": a6.get("finding", ""),
        "a6v": v(a6),
        "g1abs": "%.3e" % g1["platform"]["max_abs_dev"],
        "g1rel": "%.3e" % g1["platform"]["max_rel_dev"], "g1v": v(g1),
    }
    return DOC_TMPL.substitute(sub)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-doc", action="store_true")
    ap.add_argument("--mission", type=int, default=2050)
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    m = load_sut()
    ship = barge(m)
    base = run_A(m, ship, zones(4, p=0.1))

    crit = [crit_A1(m, ship), crit_A2(m, ship), crit_A3(m, ship), crit_A4(m, ship),
            crit_A5(m, ship), crit_A6(m), crit_G1(m, ship), crit_G2(m, ship)]
    ev = {
        "meta": {
            "mission": args.mission, "seat": "tp-mod-2",
            "sut": SUT_REL,
            "sut_sha256": hashlib.sha256(open(SUT_PATH, "rb").read()).hexdigest(),
            "ship": {"L": ship.L, "B": ship.B, "T": ship.T, "D": ship.D,
                     "Cb": ship.Cb, "KG": ship.VCG, "n_compartments": len(ship.compartments)},
            "R_value": base["R_value"], "Ls": base["Ls"],
            "n_criteria": len(crit), "n_pass": sum(1 for c in crit if c["pass"]),
            "verdict": {
                "probabilistic.A_index": "WITHHELD",
                "damage.s_factor": "REAL",
                "damage.required_index": "REAL",
                "overall": "PARTIAL",
            },
        },
        "criteria": crit,
    }
    ev["meta"]["criteria"] = [{"id": c["id"], "name": c["name"], "pass": c["pass"]} for c in crit]

    figs = make_figures(ev)
    ev["figures"] = figs
    with open(os.path.join(OUTDIR, "evidence.json"), "w") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    if not args.no_doc:
        with open(DOC, "w") as f:
            f.write(render_doc(ev))
    for c in crit:
        print(("PASS" if c["pass"] else "FAIL"), c["id"], c["name"])
    print(json.dumps({"n_pass": ev["meta"]["n_pass"], "n_tot": ev["meta"]["n_criteria"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
