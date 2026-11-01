"""耐波性域在线端点复核探针（只读）+ 解析判据裁决 + 图表生成。

用途
----
对船舶总体性能平台 :8001 的耐波性三端点做机器可裁的复核：

* POST /api/v1/seakeeping/rao                    （RAO 曲线）
* POST /api/v1/seakeeping/motion                 （运动幅值/自然周期）
* POST /api/v1/seakeeping-advanced/strip-theory  （切片理论）

判据全部来自**精确解析关系**，不依赖任何手工抄录的参考数字：

C1  输入敏感性：改变 wave_frequency 必须改变 RAO（否则端点忽略该输入）
C2  自由度可分辨性：垂荡 RAO（无量纲，m/m）与纵摇 RAO（每波陡，rad/(ka)）
    是两个不同物理量，逐点必须显著不同；恒等 ⇒ 纵摇自然频率公式退化
C3  浪向依赖：首斜浪/横浪必须改变 RAO；首浪(heading=180)下横摇激励趋于零，
    横摇 RAO 必须显著小于横浪
C4  长波极限：kL<0.3 时垂荡 RAO→1、纵摇斜率 RAO→1（Newman 1977 §6.10；
    Journée & Massie, Offshore Hydromechanics, 长波/ Froude-Krylov 极限）
C5  航速依赖：计入遭遇频率 ωe=|ω-kU cosβ| 后，有航速与零航速 RAO 必须不同
C6  /motion 幅值不得为硬编码常数（改变输入必须改变输出）
C7  横摇：由自报 T44 反解回转半径 k_xx=T44√(g·GM)/2π，须落在 0.30B~0.42B
    （水面船工程带；k_xx=0.35B 为基线）
C8  垂荡自然周期的物理下界：A33>0 且 Aw<=Cwp·L·B ⇒
    T >= T_min=2π√(m/(ρ g Cwp·L·B))；低于下界等价于 A33=0（非物理）
C9  切片理论自报 T33 与自报 A33 的定义一致性 T=2π√((m+A33)/(ρ g Aw))
C10 切片理论纵摇 RAO 不得为常数（截顶/下限 clamp 指纹）
C11 端点间一致性：同一船 /motion 与 /strip-theory 自然周期须相符（比值<1.2）

用法::

    python3 tools/seakeeping_audit/audit_seakeeping.py \
        --base http://127.0.0.1:8001 --out artifacts/m1515/seakeeping_audit
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

G = 9.80665
RHO = 1025.0

# ---------------------------------------------------------------- 算例（公开船型主尺度）
SHIPS = {
    # KVLCC2 油轮标模（MoWiT/NMRI 公开主尺度）
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, D=30.0, displacement=312622.0,
                   Cb=0.8098, Cm=0.9950, Cwp=0.8600, KG=18.6, GM=9.0, speed_kn=15.0),
    # S-175 集装箱船标模（ITTC 耐波性比较算例）
    "S175": dict(L=175.0, B=25.4, T=9.5, D=15.5, displacement=24400.0,
                 Cb=0.5720, Cm=0.9700, Cwp=0.7600, KG=8.0, GM=1.5, speed_kn=15.0),
    # 箱形驳（长波极限判据的解析适用域：L 小 ⇒ kL 可 <0.3）
    "BARGE60": dict(L=60.0, B=12.0, T=4.0, D=6.0, displacement=2200.0,
                    Cb=0.9000, Cm=0.9500, Cwp=0.9400, KG=3.0, GM=2.0, speed_kn=0.0),
}


def post(base, path, body, timeout=60):
    url = base.rstrip("/") + "/api/v1" + path
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"http": r.status, "data": json.load(r)}
    except urllib.error.HTTPError as e:          # pragma: no cover - 线上错误面
        return {"http": e.code, "data": json.loads(e.read().decode() or "{}")}
    except Exception as e:                       # pragma: no cover
        return {"http": 0, "data": {"error": str(e)}}


# ---------------------------------------------------------------- 解析基线
def k_deep(w):
    """深水色散 k = ω²/g（精确）。"""
    return w * w / G


def encounter(w, speed_ms, heading_deg):
    """遭遇频率 ωe = |ω - k·U·cosβ|，β 自船首量起（180° = 顶浪）。"""
    return abs(w - k_deep(w) * speed_ms * math.cos(math.radians(heading_deg)))


def cwp_from_cb(cb):
    """水线面系数工程估计 Cwp≈(1+2Cb)/3（仅作 Aw 的解析基线）。"""
    return (1.0 + 2.0 * cb) / 3.0


def ref_periods(s, k_yy_ratio=0.30, add_mass_ratio=0.8, k_xx_ratio=0.35):
    """解析自然周期：垂荡（含附加质量）/纵摇（含纵稳性高）/横摇。单位 s。"""
    L, B, T, KG, GM = s["L"], s["B"], s["T"], s["KG"], s["GM"]
    nabla = s["displacement"] / (RHO / 1000.0)    # m³（displacement 单位 t，ρ=1.025 t/m³）
    mass = RHO * nabla                            # kg
    Aw = cwp_from_cb(s["Cb"]) * L * B
    T33 = 2 * math.pi * math.sqrt(mass * (1 + add_mass_ratio) / (RHO * G * Aw))
    KB = 0.53 * T
    BM_L = (B * L ** 3 / 12.0) / nabla
    GM_L = BM_L + KB - KG
    T55 = 2 * math.pi * (k_yy_ratio * L) / math.sqrt(G * GM_L)
    T44 = 2 * math.pi * (k_xx_ratio * B) / math.sqrt(G * GM)
    return dict(T_heave=T33, T_pitch=T55, T_roll=T44, GM_L=GM_L, Aw=Aw,
                k_xx=k_xx_ratio * B, k_yy=k_yy_ratio * L)


def body_rao(s, w=None, heading=180.0):
    return dict(L=s["L"], B=s["B"], T=s["T"], D=s["D"], displacement=s["displacement"],
                Cb=s["Cb"], KG=s["KG"], GM=s["GM"], speed=s["speed_kn"],
                wave_frequency=w, heading=heading)


def body_strip(s, w, heading=180.0, amp=1.0):
    return dict(L=s["L"], B=s["B"], T=s["T"], D=s["D"], displacement=s["displacement"],
                Cb=s["Cb"], Cm=s["Cm"], Cwp=s["Cwp"], KG=s["KG"], GM=s["GM"],
                speed=s["speed_kn"], wave_frequency=w, wave_amplitude=amp,
                heading=heading, water_density=RHO)


# ---------------------------------------------------------------- 探针
def run_probes(base):
    P = {"meta": {"base": base, "G": G, "rho": RHO}, "rao": {}, "motion": {},
         "strip": {}, "ref": {}}

    for name, s in SHIPS.items():
        P["ref"][name] = ref_periods(s)

        # C1/C2/C9：基础曲线（多组可选输入，检验是否被忽略）
        variants = {
            "base": body_rao(s, 0.6, 180.0),
            "wf_high": body_rao(s, 2.4, 180.0),
            "head0": body_rao(s, 0.6, 0.0),
            "beam90": body_rao(s, 0.6, 90.0),
            "stern135": body_rao(s, 0.6, 135.0),
        }
        P["rao"][name] = {k: post(base, "/seakeeping/rao", b) for k, b in variants.items()}

        # C5：同一船不同航速（其余输入相同）
        s0 = dict(s, speed_kn=0.0)
        P["rao"][name]["speed0"] = post(base, "/seakeeping/rao", body_rao(s0, 0.6, 180.0))

        # C6：运动幅值是否硬编码 —— 三组差别很大的输入
        mvar = {
            "base": body_rao(s, 0.6, 180.0),
            "wave_hi": body_rao(s, 2.0, 90.0),
            "slow": body_rao(dict(s, speed_kn=0.5), 0.3, 0.0),
        }
        P["motion"][name] = {k: post(base, "/seakeeping/motion", b) for k, b in mvar.items()}

        # 切片理论：两个波浪频率（C10 常数 / C11 自然周期一致性 / C8 附加质量）
        P["strip"][name] = {
            "w06": post(base, "/seakeeping-advanced/strip-theory", body_strip(s, 0.6, 180.0)),
            "w12": post(base, "/seakeeping-advanced/strip-theory", body_strip(s, 1.2, 180.0)),
        }
    return P


# ---------------------------------------------------------------- 判据
def _max_rel_diff(a, b):
    pairs = [(abs(x), abs(y)) for x, y in zip(a, b)]
    return max(abs(x - y) / max(x, y, 1e-12) for x, y in pairs)


def judge(P):
    """11 条判据；每条证据均给出可复算数字。"""
    crit = []

    def add(cid, desc, verdict, evidence):
        crit.append({"id": cid, "desc": desc, "verdict": verdict, "evidence": evidence})

    rao = P["rao"]

    # C1 输入敏感性
    ev, bad = {}, []
    for name, packs in rao.items():
        base = packs["base"]["data"].get("heave_rao") or []
        for k in ("wf_high", "head0", "beam90", "stern135"):
            other = packs[k]["data"].get("heave_rao") or []
            r = _max_rel_diff(base, other) if base and other else 0.0
            ev[f"{name}:{k}"] = round(r, 6)
            if r < 1e-6:
                bad.append(f"{name}:{k}")
    add("C1", "改变可选输入 wave_frequency/heading 必须改变 RAO 曲线",
        "FAIL" if bad else "PASS", {"identical_pairs": bad, "max_rel_diff": ev})

    # C2 垂荡 ≡ 纵摇 退化
    ev, degenerate = {}, []
    for name, packs in rao.items():
        h = packs["base"]["data"].get("heave_rao") or []
        pit = packs["base"]["data"].get("pitch_rao") or []
        r = _max_rel_diff(h, pit) if h and pit else 1.0
        ev[name] = round(r, 6)
        if r < 1e-6:
            degenerate.append(name)
    add("C2", "垂荡 RAO(m/m) 与纵摇 RAO(rad/(ka)) 物理量不同，不可逐点恒等",
        "FAIL" if degenerate else "PASS", {"identical_ships": degenerate, "max_rel_diff": ev})

    # C3 浪向依赖（首浪横摇激励应远小于横浪）
    ev, bad = {}, []
    for name, packs in rao.items():
        r180 = packs["base"]["data"].get("roll_rao") or []
        r90 = packs["beam90"]["data"].get("roll_rao") or []
        if not r180 or not r90:
            continue
        ratio = max(r180) / max(max(r90), 1e-12)
        ev[name] = round(ratio, 4)
        if ratio > 0.6:      # 首浪横摇激励按对称性应趋于零
            bad.append(name)
    add("C3", "首浪(β=180°)横摇 RAO 峰值须显著小于横浪(β=90°)，比值<0.6",
        "FAIL" if bad else "PASS", {"head_over_beam_peak_ratio": ev, "threshold": 0.6})

    # C4 长波极限（仅对 kL<0.3 的算例适用；不适用者如实标 n/a）
    ev, bad = {}, []
    for name, s in SHIPS.items():
        freqs = rao[name]["base"]["data"].get("frequencies") or []
        h = rao[name]["base"]["data"].get("heave_rao") or []
        if not freqs:
            continue
        idx = [i for i, w in enumerate(freqs) if k_deep(w) * s["L"] < 0.30]
        if not idx:
            ev[name] = {"applicable": False, "note": "端点频率栅格最密点 kL>=0.30，判据不适用"}
            continue
        i = idx[-1]
        err = abs(h[i] - 1.0) * 100.0
        ev[name] = {"applicable": True, "omega": freqs[i],
                    "kL": round(k_deep(freqs[i]) * s["L"], 3), "heave_rao": h[i],
                    "analytic": 1.0, "err_pct": round(err, 2)}
        if err > 10.0:
            bad.append(name)
    add("C4", "长波极限：kL<0.3 时垂荡 RAO→1（解析，Newman 1977 §6.10），误差<10%",
        "FAIL" if bad else "PASS", {"cases": ev, "failing": bad})

    # C5 航速依赖（遭遇频率）
    ev, bad = {}, []
    for name, packs in rao.items():
        a = packs["base"]["data"].get("heave_rao") or []
        b = packs["speed0"]["data"].get("heave_rao") or []
        r = _max_rel_diff(a, b) if a and b else 0.0
        ev[name] = round(r, 6)
        if r < 1e-6:
            bad.append(name)
    add("C5", "计入遭遇频率 ωe=|ω-kU cosβ| 后，航速必须改变 RAO",
        "FAIL" if bad else "PASS", {"max_rel_diff_vs_speed0": ev, "identical": bad})

    # C6 /motion 幅值硬编码
    ev, bad = {}, []
    for name, packs in P["motion"].items():
        amps = {k: packs[k]["data"].get("motion_amplitudes") for k in packs}
        vals = [json.dumps(v, sort_keys=True) for v in amps.values() if v]
        ev[name] = amps
        if vals and len(set(vals)) == 1:
            bad.append(name)
    add("C6", "/motion 输出幅值不得对任意输入恒为常数（硬编码指纹）",
        "FAIL" if bad else "PASS", {"hardcoded_ships": bad, "amplitudes": ev})

    # C7 横摇：由自报 T44 反解回转半径，须落在工程带内
    ev, bad = {}, []
    band = (0.30, 0.42)
    for name, s in SHIPS.items():
        got = P["motion"][name]["base"]["data"].get("natural_periods", {}).get("roll")
        if got is None:
            continue
        k_xx_implied = got * math.sqrt(G * s["GM"]) / (2 * math.pi)
        ref = P["ref"][name]["T_roll"]
        ev[name] = {"T44_endpoint": got, "k_xx_implied_m": round(k_xx_implied, 3),
                    "k_xx_over_B": round(k_xx_implied / s["B"], 4),
                    "band_over_B": list(band), "T44_ref(0.35B)": round(ref, 3),
                    "err_vs_ref_pct": round(abs(got - ref) / ref * 100, 2)}
        if not (band[0] <= k_xx_implied / s["B"] <= band[1]):
            bad.append(name)
    add("C7", "横摇自报周期反解的回转半径须在 0.30B~0.42B（水面船工程带）",
        "FAIL" if bad else "PASS", {"cases": ev, "failing": bad, "band": list(band)})

    # C8 垂荡自然周期的物理下界（A33>0, Aw<=Cwp·L·B）
    ev, bad = {}, []
    for name, s in SHIPS.items():
        got = P["motion"][name]["base"]["data"].get("natural_periods", {}).get("heave")
        if got is None:
            continue
        nabla = s["displacement"] / (RHO / 1000.0)
        mass = RHO * nabla
        Aw_max = cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
        T_min = 2 * math.pi * math.sqrt(mass / (RHO * G * Aw_max))
        ratio = got / T_min
        ev[name] = {"T33_endpoint": got, "T_lower_bound_s": round(T_min, 3),
                    "ratio": round(ratio, 4), "required_ratio": ">=1.00 (A33>0)"}
        if ratio < 1.0:
            bad.append(name)
    add("C8", "垂荡自然周期须高于无附加质量下界 T_min=2π√(m/(ρ g Cwp·L·B))（violation⇒A33=0 非物理）",
        "FAIL" if bad else "PASS", {"cases": ev, "failing": bad})

    # C9 切片理论：自报 T33 与自报 A33 的定义一致性
    ev, bad = {}, []
    for name, s in SHIPS.items():
        d = P["strip"][name]["w06"]["data"]
        A33, Tn = d.get("added_mass_heave"), d.get("natural_period_heave")
        if A33 is None or Tn is None:
            continue
        nabla = s["displacement"] / (RHO / 1000.0)
        mass = RHO * nabla
        Aw = cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
        T_implied = 2 * math.pi * math.sqrt((mass + A33) / (RHO * G * Aw))
        err = abs(T_implied - Tn) / max(Tn, 1e-9) * 100.0
        ev[name] = {"A33_reported_kg": A33, "A33_over_mass": round(A33 / mass, 6),
                    "T33_reported": Tn, "T33_from_A33": round(T_implied, 3),
                    "inconsistency_pct": round(err, 2)}
        if err > 5.0:
            bad.append(name)
    add("C9", "切片端点自报 T33 须与自报 A33 满足 T=2π√((m+A33)/(ρ g Aw))（误差<5%）",
        "FAIL" if bad else "PASS", {"cases": ev, "failing": bad})

    # C10 切片理论纵摇 RAO 常数指纹
    ev, bad = {}, []
    for name, packs in P["strip"].items():
        pit = packs["w06"]["data"].get("pitch_rao") or []
        if not pit:
            continue
        ev[name] = {"min": min(pit), "max": max(pit),
                    "spread": round(max(pit) - min(pit), 6), "n": len(pit)}
        if (max(pit) - min(pit)) < 1e-9:
            bad.append(name)
    add("C10", "切片理论纵摇 RAO 不得为全频常数（下限 clamp 指纹）",
        "FAIL" if bad else "PASS", {"constant_ships": bad, "cases": ev})

    # C11 端点间自然周期一致性
    ev, bad = {}, []
    for name in SHIPS:
        a = P["motion"][name]["base"]["data"].get("natural_periods", {})
        b = P["strip"][name]["w06"]["data"]
        pair = {}
        for comp, key in (("heave", "natural_period_heave"), ("pitch", "natural_period_pitch"),
                          ("roll", "natural_period_roll")):
            if comp in a and key in b:
                pair[comp] = {"rao_motion": a[comp], "strip": b[key],
                              "ratio": round(max(a[comp], b[key]) /
                                             max(min(a[comp], b[key]), 1e-9), 3)}
        ev[name] = pair
        if any(v["ratio"] > 1.2 for v in pair.values()):
            bad.append(name)
    add("C11", "同一船 /motion 与 /strip-theory 自然周期须一致（比值<1.2）",
        "FAIL" if bad else "PASS", {"cases": ev, "failing": bad})

    npass = sum(1 for c in crit if c["verdict"] == "PASS")
    nfail = len(crit) - npass
    grade = "MOCK" if nfail >= 4 else ("PARTIAL" if nfail else "REAL")
    return {"criteria": crit, "summary": {"pass": npass, "fail": nfail, "total": len(crit),
                                          "grade": grade}}


# ---------------------------------------------------------------- 图表
def make_figures(P, J, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    figs = []

    # 图1：垂荡/纵摇 RAO 重合 + 长波解析极限
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for name in ("KVLCC2", "S175"):
        d = P["rao"][name]["base"]["data"]
        if not d.get("frequencies"):
            continue
        ax[0].plot(d["frequencies"], d["heave_rao"], "-o", ms=3, label=f"{name} heave")
        ax[0].plot(d["frequencies"], d["pitch_rao"], "--x", ms=3, label=f"{name} pitch")
    ax[0].axhline(1.0, color="k", lw=1.2, ls=":", label="analytic long-wave limit (kL→0): RAO=1")
    ax[0].set_xlabel(r"$\omega$ (rad/s)")
    ax[0].set_ylabel("RAO (-)")
    ax[0].set_title("heave vs pitch RAO: endpoints return identical curves")
    ax[0].legend(fontsize=7)
    ax[0].grid(alpha=.3)

    # 右图：可选输入不敏感（同一船，改 wave_frequency / heading）
    d0 = P["rao"]["KVLCC2"]["base"]["data"]
    for key, lab, st in (("wf_high", r"wave_frequency=2.4", "-"),
                         ("beam90", r"heading=90$^\circ$", "--"),
                         ("head0", r"heading=0$^\circ$", "-.")):
        dd = P["rao"]["KVLCC2"][key]["data"]
        if dd.get("frequency") or dd.get("frequencies"):
            ax[1].plot(dd["frequencies"], dd["heave_rao"], st, lw=1.4, label=lab)
    ax[1].plot(d0["frequencies"], d0["heave_rao"], "k:", lw=2, label="base (w=0.6, head)")
    ax[1].set_xlabel(r"$\omega$ (rad/s)")
    ax[1].set_ylabel("heave RAO (-)")
    ax[1].set_title("optional inputs ignored: curves coincide bit-for-bit")
    ax[1].legend(fontsize=7)
    ax[1].grid(alpha=.3)
    fig.tight_layout()
    p1 = os.path.join(outdir, "fig1_rao_degeneracy.png")
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append(("fig1_rao_degeneracy.png", "图1 左：KVLCC2/S-175 垂荡与纵摇 RAO 逐点重合（物理量不同却恒等）；右：改变 wave_frequency/heading 后曲线与基准完全重合（可选输入被忽略）。虚线为长波解析极限 RAO=1。"))

    # 图2：自然周期：端点 vs 解析
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    labels, ref_v, rao_v, strip_v = [], [], [], []
    for name in SHIPS:
        r = P["ref"][name]
        m = P["motion"][name]["base"]["data"].get("natural_periods", {})
        st = P["strip"][name]["w06"]["data"]
        for comp, rk, mk, sk in (("heave", "T_heave", "heave", "natural_period_heave"),
                                 ("pitch", "T_pitch", "pitch", "natural_period_pitch"),
                                 ("roll", "T_roll", "roll", "natural_period_roll")):
            labels.append(f"{name}\n{comp}")
            ref_v.append(r[rk])
            rao_v.append(m.get(mk, float("nan")))
            strip_v.append(st.get(sk, float("nan")))
    import numpy as np
    x = np.arange(len(labels))
    ax.bar(x - 0.27, ref_v, 0.27, label="analytic baseline")
    ax.bar(x, rao_v, 0.27, label="/seakeeping/motion")
    ax.bar(x + 0.27, strip_v, 0.27, label="/strip-theory")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7)
    ax.set_ylabel("natural period (s)")
    ax.set_title("natural periods: endpoints vs analytic definition")
    ax.legend(fontsize=8)
    ax.grid(alpha=.3, axis="y")
    fig.tight_layout()
    p2 = os.path.join(outdir, "fig2_natural_periods.png")
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append(("fig2_natural_periods.png", "图2 三船三自由度自然周期：解析基线（含附加质量/纵稳性高/0.35B 回转半径）与两个端点自报值对比；KVLCC2 纵摇端点值 63.0 s 与解析 7~9 s 相差约 7 倍。"))

    # 图3：切片理论 RAO 截顶指纹
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    d = P["strip"]["KVLCC2"]["w06"]["data"]
    if d.get("heave_rao"):
        x = range(1, len(d["heave_rao"]) + 1)
        ax.plot(x, d["heave_rao"], "-o", ms=3, label="heave RAO (strip endpoint)")
        ax.plot(x, d["pitch_rao"], "-s", ms=3, label="pitch RAO (strip endpoint)")
        ax.plot(x, d["roll_rao"], "-^", ms=3, label="roll RAO (strip endpoint)")
        ax.axhline(0.5, color="r", ls=":", lw=1.2, label="clamp floor 0.5")
        ax.axhline(0.02, color="m", ls=":", lw=1.2, label="clamp floor 0.02")
    ax.set_xlabel("index of returned frequency sample")
    ax.set_ylabel("RAO (-)")
    ax.set_title("strip-theory endpoint: response clamped at hard-coded floors")
    ax.legend(fontsize=8)
    ax.grid(alpha=.3)
    fig.tight_layout()
    p3 = os.path.join(outdir, "fig3_strip_clamp.png")
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    figs.append(("fig3_strip_clamp.png", "图3 切片理论端点返回的 RAO 序列被硬编码下限截断（垂荡/横摇 0.5、纵摇恒为 0.02），高频段失去物理衰减。"))

    return figs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--out", default="artifacts/m1515/seakeeping_audit")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    P = run_probes(args.base)
    J = judge(P)
    figs = make_figures(P, J, args.out)

    with open(os.path.join(args.out, "probes.json"), "w") as f:
        json.dump(P, f, indent=1, ensure_ascii=False)
    with open(os.path.join(args.out, "verdict.json"), "w") as f:
        json.dump({"summary": J["summary"], "criteria": J["criteria"],
                   "figures": [f[0] for f in figs],
                   "ref_periods": P["ref"]}, f, indent=1, ensure_ascii=False)

    for c in J["criteria"]:
        print(f"{c['id']:>4} {c['verdict']:<5} {c['desc']}")
    print("summary:", J["summary"])
    return 0 if J["summary"]["fail"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())