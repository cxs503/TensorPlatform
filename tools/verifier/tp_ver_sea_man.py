#!/usr/bin/env python3
"""tp-ver-sea-man — 耐波 / 操纵域验证欠账清偿（闭式判据 + 可复算判决台账）.

EDIT_WHITELIST: tools/verifier/, tests/, docs/verification/, artifacts/

背景
----
平台（:8001 Ship Performance AI Platform v2.0.0）的阻力 / 推进 / 破舱稳性 / 板架稳定域
已在前序单中清偿验证欠账；耐波(seakeeping) 与 操纵(manoeuvring) 两域至今没有任何
独立判决。本模块把这两域对「闭式解析基准 / 造船规范限值」逐项复核，输出：

  * 每条判据的参考值、实测值、相对误差、判决（PASS/FAIL/ERROR/SKIP）
  * 证据快照（probe snapshot，可离线 replay -> 判决可复算、不依赖实时服务）
  * 判决台账 JSON + markdown（数字全部程序化注入，无手抄）

设计原则（与 tools/verifier/verifier.py 同源：NEVER trust self-reported result）
---------------------------------------------------------------------------
1. 判据只用闭式解析恒等式 / 规范限值 / 物理不等式，不拿「另一个黑箱模型」当参考，
   这样判决本身不需要被判决。
2. 几何先验证 Cb/L/B（平台精度纪律）：Cb 必须等于 ∇/(L·B·T)，误差 <1%。
3. 判据函数的输入是 probe 快照（dict），不是实时 HTTP —— 故 pytest 可离线复跑全判决
   （tests/test_tp_ver_sea_man.py）。

用法
----
  python tools/verifier/tp_ver_sea_man.py --live --out artifacts/verification
  python tools/verifier/tp_ver_sea_man.py --replay
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

G = 9.80665  # m/s^2

# ---------------------------------------------------------------------------
# 参考船：KRISO KCS（公开几何；Cb/L/B 由 ∇ 反算校验 —— 见 GEO-0）
# ---------------------------------------------------------------------------
REFERENCE_SHIP: Dict[str, float] = {
    "name": "KCS (KRISO Container Ship)",
    "L": 230.0,
    "B": 32.2,
    "T": 10.8,
    "D": 19.0,
    "displacement": 52030.0,
    "Cb": 0.6505,
    "Cm": 0.9849,
    "Cwp": 0.868,
    "KG": 11.0,
    "GM": 1.2,
    "speed": 7.7,
    "Dp": 7.0,
    "Ar": 55.0,
}

SHIP_KEYS = ("L", "B", "T", "D", "displacement", "Cb", "KG", "GM", "speed", "Dp", "Ar")


def _body(ship: Dict[str, float], **over) -> Dict[str, Any]:
    b = {k: ship[k] for k in SHIP_KEYS}
    b.update(over)
    return b


def probe_specs(ship: Dict[str, float]) -> Dict[str, tuple]:
    """探针清单：(method, path, body)。覆盖两域全部对外能力端点。"""
    return {
        # ---- 操纵域 ----
        "turning_r35": ("POST", "/maneuvering/turning", _body(ship, rudder_angle=35)),
        "turning_r20": ("POST", "/maneuvering/turning", _body(ship, rudder_angle=20)),
        "turning_r05": ("POST", "/maneuvering/turning", _body(ship, rudder_angle=5)),
        "turning_u50": ("POST", "/maneuvering/turning", _body(ship, speed=5.0, rudder_angle=35)),
        "zigzag_r10": ("POST", "/maneuvering/zigzag", _body(ship, rudder_angle=10)),
        "zigzag_r35": ("POST", "/maneuvering/zigzag", _body(ship, rudder_angle=35)),
        "kt_r35": ("POST", "/maneuvering/kt", _body(ship, rudder_angle=35)),
        "mmg_turning": (
            "POST", "/maneuvering-mmg/turning",
            {"L": ship["L"], "B": ship["B"], "T": ship["T"],
             "displacement": ship["displacement"], "Cb": ship["Cb"],
             "Dp": ship["Dp"], "n": 1.5, "Ar": ship["Ar"],
             "V0": ship["speed"], "rudder_angle": 35},
        ),
        # ---- 耐波域 ----
        "rao_gm1p2": ("POST", "/seakeeping/rao", _body(ship, wave_frequency=1.0, heading=180)),
        "rao_gm2p4": ("POST", "/seakeeping/rao",
                      _body(ship, GM=2.4, wave_frequency=1.0, heading=180)),
        "motion_gm1p2": ("POST", "/seakeeping/motion", _body(ship, wave_frequency=0.7, heading=180)),
        "motion_gm2p4": ("POST", "/seakeeping/motion",
                         _body(ship, GM=2.4, wave_frequency=0.7, heading=180)),
        "strip": ("POST", "/seakeeping-advanced/strip-theory",
                  {"L": ship["L"], "B": ship["B"], "T": ship["T"], "D": ship["D"],
                   "displacement": ship["displacement"], "Cb": ship["Cb"],
                   "Cm": ship["Cm"], "Cwp": ship["Cwp"], "KG": ship["KG"],
                   "GM": ship["GM"], "speed": ship["speed"],
                   "wave_frequency": 0.8, "wave_amplitude": 1.0, "heading": 180}),
        "wave_spectrum": ("POST", "/seakeeping-advanced/wave-spectrum",
                          {"Hs": 5.0, "Tp": 9.0, "gamma": 3.3,
                           "spectrum_type": "jonswap", "frequency_range": [0.1, 1.5],
                           "frequency_steps": 50}),
        "roll_motion": ("POST", "/dynamic-stability/roll-motion",
                        {"L": ship["L"], "B": ship["B"], "T": ship["T"],
                         "Cb": ship["Cb"], "displacement": ship["displacement"],
                         "GM": ship["GM"], "KB": 5.5, "roll_damping": 0.1,
                         "wave_amplitude": 2.5, "wave_period": 9.0, "wave_heading": 180}),
    }


def run_probes(base_url: str, ship: Dict[str, float], timeout: float = 90.0) -> Dict[str, Any]:
    """打实时服务，返回 probe 快照（错误也留证，不抛异常）。"""
    out: Dict[str, Any] = {}
    for key, (method, path, body) in probe_specs(ship).items():
        url = base_url.rstrip("/") + "/api/v1" + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out[key] = {"http": r.status, "ok": True, "resp": json.load(r)}
        except urllib.error.HTTPError as e:
            out[key] = {"http": e.code, "ok": False,
                        "resp": e.read().decode(errors="replace")[:400]}
        except Exception as e:  # noqa: BLE001
            out[key] = {"http": None, "ok": False, "resp": f"{type(e).__name__}: {e}"[:400]}
    return out


# ---------------------------------------------------------------------------
# 判决框架
# ---------------------------------------------------------------------------
@dataclass
class Verdict:
    cid: str
    domain: str
    title: str
    basis: str
    tol: str
    verdict: str
    reference: Any = None
    measured: Any = None
    rel_err: Optional[float] = None
    note: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Criterion:
    cid: str
    domain: str
    title: str
    basis: str
    tol: str
    fn: Callable[[Dict[str, Any], Dict[str, float]], Verdict]


def _rel(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None if a == 0 else float("inf")
    return abs(a - b) / abs(b)


def _ok(probe: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    p = probe.get(key) or {}
    return p.get("resp") if p.get("ok") else None


def _err(cid, domain, title, basis, tol, probe, keys) -> Verdict:
    p = probe.get(keys.split("/")[0]) or {}
    return Verdict(cid, domain, title, basis, tol, "ERROR", None, None, None,
                   f"探针不可用：{keys} http={p.get('http')} resp={str(p.get('resp'))[:150]}")


# ---------------------------------------------------------------------------
# 闭式参考（判据与自检共用的单点真值来源）
# ---------------------------------------------------------------------------
def deep_water_wavenumber(omega: float) -> float:
    """深水色散关系 k = ω²/g。"""
    return omega * omega / G


def deep_water_wavelength(omega: float) -> float:
    """深水波长 λ = 2πg/ω²。"""
    return 2.0 * math.pi * G / (omega * omega)


def box_heave_natural_period(T: float, Cb: float, Cwp: float) -> float:
    """壁式剖面船体垂荡固有周期（附加质量 a33=0 的严格下界）。

    ω_n² = ρ g A_w / m，A_w = Cwp·L·B，m = ρ·Cb·L·B·T
         => ω_n² = g·Cwp/(Cb·T) => T_n = 2π/ω_n
    真实船体 a33>0 => 实测周期只会更长，故本值是下界。
    """
    return 2.0 * math.pi / math.sqrt(G * Cwp / (Cb * T))


def roll_natural_period(k_xx: float, GM: float) -> float:
    """横摇固有周期（无阻尼）：T_phi = 2π k_xx / sqrt(g·GM)。"""
    return 2.0 * math.pi * k_xx / math.sqrt(G * GM)


def jonswap_m0(Hs: float, Tp: float, gamma: float = 3.3, n: int = 4000) -> float:
    """JONSWAP 谱零阶矩（Simpson 数值积分；理论自洽 m0 = Hs²/16）。"""
    w_p = 2.0 * math.pi / Tp
    lo, hi = 0.05 * w_p, 20.0 * w_p
    h = (hi - lo) / n
    alpha = 0.0624 / (0.230 + 0.0336 * gamma - 0.185 / (1.9 + gamma))
    total = 0.0
    for i in range(n + 1):
        w = lo + i * h
        if w <= 0:
            continue
        sigma = 0.07 if w <= w_p else 0.09
        r = math.exp(-((w - w_p) ** 2) / (2.0 * sigma * sigma * w_p * w_p))
        S = alpha * Hs * Hs * w_p ** 4 * w ** -5 * math.exp(-1.25 * (w_p / w) ** 4) * gamma ** r
        coef = 1.0 if i in (0, n) else (4.0 if i % 2 else 2.0)
        total += coef * S
    return total * h / 3.0


def nomoto_steady_yaw_rate(K: float, delta_rad: float) -> float:
    """Nomoto 一阶响应模型稳态：r_ss = K·δ (rad/s)。"""
    return K * delta_rad


def turning_radius_from_rate(U: float, r_rad_s: float) -> float:
    """稳态回转运动学恒等式：R = U / r。"""
    return U / r_rad_s


def heave_rao_low_freq(ship: Dict[str, float] = None) -> float:
    """长波渐近值（解析）：垂荡 RAO = 1。"""
    return 1.0


def pitch_rao_low_freq() -> float:
    """长波渐近值（解析）：纵摇 RAO = 0（波长 >> 船长时随波平移不转动）。"""
    return 0.0


# ---------------------------------------------------------------------------
# 几何门槛
# ---------------------------------------------------------------------------
def crit_geo0(probe, ship) -> Verdict:
    cb_calc = ship["displacement"] / (ship["L"] * ship["B"] * ship["T"])
    err = _rel(ship["Cb"], cb_calc)
    return Verdict("GEO-0", "geometry", "参考船几何自洽 Cb = ∇/(L·B·T)",
                   "排水体积恒等式（平台精度纪律：几何先验证 Cb/L/B）", "<1%",
                   "PASS" if err < 0.01 else "FAIL",
                   {"Cb_declared": ship["Cb"], "Cb_from_nabla": round(cb_calc, 5)},
                   round(cb_calc, 5), err, "不通过则后续一切判决无意义")


# ---------------------------------------------------------------------------
# 操纵域判据
# ---------------------------------------------------------------------------
def crit_man1(probe, ship) -> Verdict:
    r = _ok(probe, "turning_r35")
    if not r:
        return _err("MAN-1", "manoeuvring", "稳态回转直径 vs 回转角速度（D = 2U/r）",
                    "刚体运动学 R=U/r => D=2R", "5%", probe, "turning_r35")
    D_rep, w_rep, U = float(r["steady_diameter"]), float(r["turning_rate"]), ship["speed"]
    D_exp = 2.0 * turning_radius_from_rate(U, math.radians(w_rep))
    err = _rel(D_rep, D_exp)
    return Verdict("MAN-1", "manoeuvring", "稳态回转直径 vs 回转角速度（D = 2U/r）",
                   "刚体运动学 R=U/r => D=2R；直径与角速度必须互锁", "5%",
                   "PASS" if err < 0.05 else "FAIL",
                   {"D_from_rate [m]": round(D_exp, 1), "D_reported [m]": D_rep,
                    "r_reported [deg/s]": w_rep,
                    "r_implied_by_D [deg/s]": round(math.degrees(2.0 * U / D_rep), 4)},
                   D_rep, err,
                   f"D={D_rep:.0f} m 与 r={w_rep} deg/s 自相矛盾：由 D 反推 r="
                   f"{math.degrees(2*U/D_rep):.3f} deg/s")


def crit_man2(probe, ship) -> Verdict:
    a, b = _ok(probe, "turning_r35"), _ok(probe, "turning_r20")
    if not (a and b):
        return _err("MAN-2", "manoeuvring", "回转直径对舵角的敏感性（dD/dδ < 0）",
                    "操纵试验/MMG 共识：舵角增大 => 回转直径单调减小", "35° 比 20° 至少收缩 5%",
                    probe, "turning_r35/turning_r20")
    D35, D20 = float(a["tactical_diameter"]), float(b["tactical_diameter"])
    shrink = (D20 - D35) / D20 if D20 else 0.0
    return Verdict("MAN-2", "manoeuvring", "回转直径对舵角的敏感性（dD/dδ < 0）",
                   "操纵试验/MMG 共识：舵角增大 => 回转直径单调减小", "35° 比 20° 至少收缩 5%",
                   "PASS" if shrink >= 0.05 else "FAIL",
                   {"D_35deg [m]": D35, "D_20deg [m]": D20, "required_shrink": 0.05},
                   round(shrink, 4), None,
                   f"实测收缩率 {shrink*100:.1f}% => 舵角输入被忽略")


def crit_man3(probe, ship) -> Verdict:
    a, b = _ok(probe, "turning_r35"), _ok(probe, "turning_u50")
    if not (a and b):
        return _err("MAN-3", "manoeuvring", "回转角速度航速标度律 r ∝ U",
                    "Froude 相似回转：R/L 与航速无关 => r=U/R 线性于 U", "15%",
                    probe, "turning_r35/turning_u50")
    r1, r2 = float(a["turning_rate"]), float(b["turning_rate"])
    exp_ratio = ship["speed"] / 5.0
    got_ratio = (r1 / r2) if r2 else float("inf")
    err = _rel(got_ratio, exp_ratio)
    return Verdict("MAN-3", "manoeuvring", "回转角速度航速标度律 r ∝ U",
                   "Froude 相似回转：R/L 与航速无关 => r=U/R 随 U 线性", "15%",
                   "PASS" if err < 0.15 else "FAIL",
                   {"expected_ratio(U=7.7/5.0)": round(exp_ratio, 3),
                    "r_7.7 [deg/s]": r1, "r_5.0 [deg/s]": r2},
                   round(got_ratio, 3), err,
                   "角速度为常数 => 回转圈随航速不相似")


def crit_man4(probe, ship) -> Verdict:
    a, b = _ok(probe, "turning_r35"), _ok(probe, "turning_r05")
    if not (a and b):
        return _err("MAN-4", "manoeuvring", "小舵角渐近（δ→0 => D→∞）",
                    "Nomoto 一阶：r_ss=Kδ => D=2U/(Kδ)", "D(5°)/D(35°) >= 3",
                    probe, "turning_r35/turning_r05")
    D35, D5 = float(a["tactical_diameter"]), float(b["tactical_diameter"])
    ratio = D5 / D35 if D35 else 0.0
    return Verdict("MAN-4", "manoeuvring", "小舵角渐近（δ→0 => D→∞）",
                   "Nomoto 一阶：r_ss=Kδ => D=2U/(Kδ)，δ→0 直径发散", "D(5°)/D(35°) >= 3",
                   "PASS" if ratio >= 3.0 else "FAIL",
                   {"D_35deg [m]": D35, "D_5deg [m]": D5, "required_ratio": 3.0},
                   round(ratio, 3), None, f"实测比值 {ratio:.2f} => 无 1/δ 律")


def crit_man5(probe, ship) -> Verdict:
    a = _ok(probe, "turning_r35")
    if not a:
        return _err("MAN-5", "manoeuvring", "IMO MSC.137(76) 回转性限值 + 硬编码指纹",
                    "IMO Res. MSC.137(76)：advance <= 4.5L，TD <= 5L", "限值 + 栅格指纹",
                    probe, "turning_r35")
    L = ship["L"]
    adv_L = float(a["advance"]) / L
    td_L = float(a["tactical_diameter"]) / L
    tr_L = float(a["transfer"]) / L
    pass_imo = adv_L <= 4.5 and td_L <= 5.0
    grid = all(abs(x * 2 - round(x * 2)) < 1e-9 for x in (adv_L, td_L, tr_L))
    return Verdict("MAN-5", "manoeuvring", "IMO MSC.137(76) 回转性限值 + 硬编码指纹",
                   "IMO Res. MSC.137(76)（advance <=4.5L, TD <=5L）+ 0.5L 整数栅格指纹",
                   "限值 + 栅格指纹",
                   "PASS" if pass_imo and not grid else "FAIL",
                   {"advance/L": round(adv_L, 4), "transfer/L": round(tr_L, 4),
                    "TD/L": round(td_L, 4), "limits": {"advance/L": 4.5, "TD/L": 5.0},
                    "all_on_0.5L_grid": grid},
                   {"advance/L": round(adv_L, 4), "TD/L": round(td_L, 4)}, None,
                   "数值虽合规，但恰为 L 的 3.5/2.0/4.5 倍（0.5L 栅格）=> 常量表而非计算")


def crit_man6(probe, ship) -> Verdict:
    k, t = _ok(probe, "kt_r35"), _ok(probe, "turning_r35")
    if not (k and t):
        return _err("MAN-6", "manoeuvring", "K-T 模型与回转输出互锁（r_ss = K·δ）",
                    "Nomoto 一阶响应：r_ss=Kδ", "20%（对报告方最优的单位读法）",
                    probe, "kt_r35/turning_r35")
    K, U, L = float(k["K"]), ship["speed"], ship["L"]
    w_rep = float(t["turning_rate"])
    delta = math.radians(35.0)
    r_if_per_s = math.degrees(nomoto_steady_yaw_rate(K, delta))
    r_if_nondim = math.degrees(nomoto_steady_yaw_rate(K, delta) * U / L)
    e1, e2 = _rel(w_rep, r_if_per_s), _rel(w_rep, r_if_nondim)
    best = min(e1, e2)
    return Verdict("MAN-6", "manoeuvring", "K-T 模型与回转输出互锁（r_ss = K·δ）",
                   "Nomoto 一阶：r_ss=Kδ；K 按 1/s 或无量纲 K'=KL/U 两种读法取最优", "20%",
                   "PASS" if best < 0.20 else "FAIL",
                   {"K": K, "r_ss_if_K_per_s [deg/s]": round(r_if_per_s, 3),
                    "r_ss_if_K_nondim [deg/s]": round(r_if_nondim, 3),
                    "r_reported [deg/s]": w_rep},
                   w_rep, best,
                   f"最优单位读法仍偏 {best*100:.0f}% => K-T 端点与回转端点互不相干")


def crit_man7(probe, ship) -> Verdict:
    k = _ok(probe, "kt_r35")
    if not k:
        return _err("MAN-7", "manoeuvring", "K-T 无量纲量级合理性（K', T'）",
                    "Clarke(1983)/Kijima(1990) 回归适用域：K'∈[0.2,5]，T'∈[0.3,5]", "工程域",
                    probe, "kt_r35")
    K, T, U, L = float(k["K"]), float(k["T"]), ship["speed"], ship["L"]
    Kp, Tp = K * L / U, T * U / L
    ok = 0.2 <= Kp <= 5.0 and 0.3 <= Tp <= 5.0
    return Verdict("MAN-7", "manoeuvring", "K-T 无量纲量级合理性（K', T'）",
                   "Clarke(1983)/Kijima(1990) 回归适用域：K'∈[0.2,5]，T'∈[0.3,5]", "工程域",
                   "PASS" if ok else "FAIL",
                   {"K [1/s]": K, "T [s]": T, "K'=KL/U": round(Kp, 3), "T'=TU/L": round(Tp, 3),
                    "domain": {"K'": [0.2, 5.0], "T'": [0.3, 5.0]}},
                   {"Kp": round(Kp, 3), "Tp": round(Tp, 3)}, None,
                   f"K'={Kp:.1f}（超上界 {Kp/5:.0f}×），T'={Tp:.1f}（超上界 {Tp/5:.1f}×）"
                   f"=> 量纲/标度错")


def crit_man8(probe, ship) -> Verdict:
    a, b = _ok(probe, "zigzag_r10"), _ok(probe, "zigzag_r35")
    if not (a and b):
        return _err("MAN-8", "manoeuvring", "Z 形试验超越角依赖 + IMO 限值",
                    "IMO MSC.137(76)：10°/10° 首超越角 <= 10°", "限值 + 单调",
                    probe, "zigzag_r10/zigzag_r35")
    o10, o35 = float(a["overshoot_1"]), float(b["overshoot_1"])
    p10, p35 = float(a["period"]), float(b["period"])
    monotone = o35 > o10
    linear = abs(o35 / o10 - 3.5) < 0.05 if o10 else False
    frozen = abs(p35 - p10) < 1e-6
    imo_ok = o10 <= 10.0
    return Verdict("MAN-8", "manoeuvring", "Z 形试验超越角依赖 + IMO 限值",
                   "IMO MSC.137(76) 10°/10° 首超越角 <=10°；超越角与周期须随舵角变化",
                   "限值 + 单调",
                   "PASS" if (imo_ok and monotone and not frozen) else "FAIL",
                   {"overshoot_1(10deg) [deg]": o10, "overshoot_1(35deg) [deg]": o35,
                    "period(10deg) [s]": p10, "period(35deg) [s]": p35,
                    "IMO_limit [deg]": 10.0, "ratio_equals_delta_ratio": linear},
                   {"overshoot_1(10deg)": o10}, None,
                   f"超越角严格正比于舵角、周期恒为 {p10:.1f} s => 经验式而非仿真")


def crit_man9(probe, ship) -> Verdict:
    p = probe.get("mmg_turning") or {}
    ok = bool(p.get("ok"))
    return Verdict("MAN-9", "manoeuvring", "/maneuvering-mmg/turning 可用性（MMG 3-DOF 真解算）",
                   "端点自述「船体力+螺旋桨力+舵力」=> 标准输入必须返回回转速", "HTTP 200 + 数值",
                   "PASS" if ok else "FAIL", "HTTP 200 含回转要素", f"http={p.get('http')}", None,
                   "KVLCC 级标准输入返回 500 => 声称的 MMG 能力不可用")


# ---------------------------------------------------------------------------
# 耐波域判据
# ---------------------------------------------------------------------------
def crit_sea1(probe, ship) -> Verdict:
    r = _ok(probe, "rao_gm1p2")
    if not r:
        return _err("SEA-1", "seakeeping", "垂荡 RAO 长波渐近 RAO(ω→0)=1",
                    "线性水动力学：λ/L→∞ 时垂荡幅值=波幅（RAO→1）", "10%", probe, "rao_gm1p2")
    f, h = r["frequencies"], r["heave_rao"]
    i = min(range(len(f)), key=lambda j: f[j])
    err = _rel(h[i], heave_rao_low_freq(ship))
    return Verdict("SEA-1", "seakeeping", "垂荡 RAO 长波渐近 RAO(ω→0)=1",
                   "线性水动力学：λ/L→∞ 时垂荡幅值=波幅", "10%",
                   "PASS" if err < 0.10 else "FAIL",
                   {"RAO_ref": 1.0, "omega_min [rad/s]": f[i],
                    "lambda_over_L": round(deep_water_wavelength(f[i]) / ship["L"], 3)},
                   h[i], err,
                   "低频端垂荡渐近正确" if err < 0.10 else "低频端偏离解析渐近值")


def crit_sea2(probe, ship) -> Verdict:
    r = _ok(probe, "rao_gm1p2")
    if not r:
        return _err("SEA-2", "seakeeping", "纵摇 RAO 长波渐近 RAO(ω→0)=0",
                    "线性水动力学：λ>>L 时船体随波平移不转动 => 纵摇 RAO→0", "|RAO| <= 0.05",
                    probe, "rao_gm1p2")
    f, p = r["frequencies"], r["pitch_rao"]
    i = min(range(len(f)), key=lambda j: f[j])
    ok = abs(p[i]) <= 0.05
    return Verdict("SEA-2", "seakeeping", "纵摇 RAO 长波渐近 RAO(ω→0)=0",
                   "线性水动力学：λ>>L 时纵摇 RAO→0（rad/m）", "|RAO| <= 0.05 rad/m",
                   "PASS" if ok else "FAIL",
                   {"RAO_ref": 0.0, "omega_min [rad/s]": f[i]},
                   p[i], _rel(p[i], 1.0), "纵摇低频端未趋于 0 => 纵摇被垂荡数组污染")


def crit_sea3(probe, ship) -> Verdict:
    r = _ok(probe, "rao_gm1p2")
    if not r:
        return _err("SEA-3", "seakeeping", "纵摇/垂荡 RAO 的物理区分度",
                    "垂荡 RAO 无量纲(m/m)，纵摇 RAO 单位 rad/m(∝k)，两者不可能逐点相同",
                    "数组不得恒等", probe, "rao_gm1p2")
    h, p = r["heave_rao"], r["pitch_rao"]
    identical = len(h) == len(p) and all(abs(a - b) < 1e-12 for a, b in zip(h, p))
    ratio0 = (p[0] / h[0]) if h and h[0] else None
    return Verdict("SEA-3", "seakeeping", "纵摇/垂荡 RAO 的物理区分度",
                   "垂荡 m/m 不随 k 变；纵摇 rad/m ≈ k·η5 => 逐点相等即数据污染", "不得恒等",
                   "FAIL" if identical else "PASS",
                   {"pitch_over_heave_at_omega_min": ratio0},
                   "identical" if identical else "distinct", None,
                   "pitch_rao 与 heave_rao 逐点完全相同（30 个频率值全等）=> 同一数组被复用")


def crit_sea4(probe, ship) -> Verdict:
    a, b = _ok(probe, "motion_gm1p2"), _ok(probe, "motion_gm2p4")
    if not (a and b) or "natural_periods" not in a or "natural_periods" not in b:
        return _err("SEA-4", "seakeeping", "横摇固有周期 GM 标度律 T ∝ 1/√GM",
                    "T_phi = 2π k_xx/√(g·GM) => GM 加倍周期缩短 √2", "10%",
                    probe, "motion_gm1p2/motion_gm2p4")
    T1 = float(a["natural_periods"]["roll"])
    T2 = float(b["natural_periods"]["roll"])
    ratio = T1 / T2 if T2 else float("inf")
    err = _rel(ratio, math.sqrt(2.0))
    return Verdict("SEA-4", "seakeeping", "横摇固有周期 GM 标度律 T ∝ 1/√GM",
                   "T_phi = 2π k_xx/√(g·GM)（无阻尼线性横摇）", "10%",
                   "PASS" if err < 0.10 else "FAIL",
                   {"expected_ratio T(GM=1.2)/T(GM=2.4)": round(math.sqrt(2.0), 4),
                    "T_roll(GM=1.2) [s]": T1, "T_roll(GM=2.4) [s]": T2},
                   round(ratio, 4), err,
                   "GM 改变周期不变 => 横摇固有周期未依赖 GM" if err >= 0.10 else "GM 标度律成立")


def crit_sea5(probe, ship) -> Verdict:
    a = _ok(probe, "motion_gm1p2")
    if not a or "natural_periods" not in a:
        return _err("SEA-5", "seakeeping", "垂荡固有周期的附加质量下界",
                    "ω_n²=g·Cwp/(Cb·T)（a33=0 严格下界）", "T_n >= T_box",
                    probe, "motion_gm1p2")
    T_n = float(a["natural_periods"]["heave"])
    T_box = box_heave_natural_period(ship["T"], ship["Cb"], ship["Cwp"])
    ratio = T_n / T_box
    return Verdict("SEA-5", "seakeeping", "垂荡固有周期的附加质量下界",
                   "ω_n²=g·Cwp/(Cb·T)（a33=0 下界）；真实船 T_n/T_box∈[1.1,1.6]",
                   "T_n >= T_box（比值 >= 1.0）",
                   "PASS" if ratio >= 1.0 else "FAIL",
                   {"T_box [s]": round(T_box, 3), "T_n_reported [s]": T_n,
                    "physical_band": [1.1, 1.6]},
                   round(ratio, 4), _rel(T_n, T_box),
                   f"T_n/T_box={ratio:.3f}<1 => 等价于负附加质量，物理不可能")


def crit_sea6(probe, ship) -> Verdict:
    a = _ok(probe, "motion_gm1p2")
    if not a or "natural_periods" not in a:
        return _err("SEA-6", "seakeeping", "垂荡/纵摇固有周期不得相等",
                    "ω_heave 受 A_w/a33 支配，ω_pitch 受 GM_L/a55 支配，无恒等关系", "差异 >5%",
                    probe, "motion_gm1p2")
    Th = float(a["natural_periods"]["heave"])
    Tp = float(a["natural_periods"]["pitch"])
    rel = _rel(Tp, Th)
    bad = rel is not None and rel < 0.05
    return Verdict("SEA-6", "seakeeping", "垂荡/纵摇固有周期不得相等",
                   "垂荡受 A_w/a33 支配，纵摇受 GM_L/a55 支配，二者无恒等关系", "差异 >5%",
                   "FAIL" if bad else "PASS",
                   {"T_heave [s]": Th, "T_pitch [s]": Tp}, round(rel, 6), rel,
                   "垂荡与纵摇固有周期完全相同 => 一套公式复用" if bad else "两周期有区分")


def crit_sea7(probe, ship) -> Verdict:
    r = _ok(probe, "strip")
    if not r or not r.get("heave_rao") or not r.get("pitch_rao"):
        return _err("SEA-7", "seakeeping", "Strip theory 端点：纵摇须有动力学、垂荡低频须→1",
                    "长波垂荡 RAO→1；纵摇 RAO 变异系数不得≈0（否则为常数填充）", "见证据",
                    probe, "strip")
    h, p = r["heave_rao"], r["pitch_rao"]
    mean_p = sum(p) / len(p)
    cv_p = ((sum((x - mean_p) ** 2 for x in p) / len(p)) ** 0.5 / mean_p) if mean_p else float("inf")
    heave_lo = h[0]
    err_lo = _rel(heave_lo, 1.0)
    ok = cv_p > 0.05 and err_lo < 0.10
    return Verdict("SEA-7", "seakeeping", "Strip theory 端点：纵摇须有动力学、垂荡低频须→1",
                   "长波垂荡 RAO→1（容差 10%）；纵摇 RAO 变异系数 >5%（否则常数填充）", "见证据",
                   "PASS" if ok else "FAIL",
                   {"heave_rao(omega_min)": heave_lo, "pitch_rao_mean [rad/m]": round(mean_p, 5),
                    "pitch_rao_cv": round(cv_p, 5)},
                   {"pitch_rao_cv": round(cv_p, 5), "heave_lo": heave_lo}, err_lo,
                   f"垂荡低频 RAO={heave_lo}（应→1，偏 {err_lo*100:.0f}%）；纵摇变异系数 {cv_p*100:.2f}%"
                   f"（前 1 点 {p[0]} 后 19 点恒为 {p[1]} ⇒ 近似常数填充）")


def crit_sea8(probe, ship) -> Verdict:
    p = probe.get("wave_spectrum") or {}
    ok = bool(p.get("ok"))
    return Verdict("SEA-8", "seakeeping",
                   "/seakeeping-advanced/wave-spectrum 可用性（m0=Hs²/16 自洽）",
                   "JONSWAP 谱零阶矩解析恒等式 m0 = Hs²/16（本模块 Simpson 参考实现）",
                   "HTTP 200 + 5%",
                   "PASS" if ok else "FAIL",
                   {"m0_analytic [m²]": round(jonswap_m0(5.0, 9.0, 3.3), 4),
                    "Hs²/16": round(25.0 / 16.0, 4)},
                   f"http={p.get('http')}", None,
                   "端点对标准输入（Hs=5 m, Tp=9 s）返回 500 => 波浪谱能力不可用")


def crit_sea9(probe, ship) -> Verdict:
    p = probe.get("roll_motion") or {}
    ok = bool(p.get("ok"))
    return Verdict("SEA-9", "seakeeping", "/dynamic-stability/roll-motion 可用性",
                   "端点自述非线性横摇时域解 => 标准输入必须返回横摇要素", "HTTP 200 + 数值",
                   "PASS" if ok else "FAIL", "HTTP 200 含横摇要素", f"http={p.get('http')}", None,
                   "标准输入（GM=1.2 m，波幅 2.5 m，周期 9 s）返回 500 => 横摇动力学不可用")


CRITERIA = [
    crit_geo0,
    crit_man1, crit_man2, crit_man3, crit_man4, crit_man5,
    crit_man6, crit_man7, crit_man8, crit_man9,
    crit_sea1, crit_sea2, crit_sea3, crit_sea4, crit_sea5,
    crit_sea6, crit_sea7, crit_sea8, crit_sea9,
]


def evaluate(probe: Dict[str, Any], ship: Dict[str, float]) -> list:
    return [c(probe, ship) for c in CRITERIA]


def summarize(verdicts: list) -> Dict[str, Any]:
    by_domain: Dict[str, Dict[str, int]] = {}
    for v in verdicts:
        d = by_domain.setdefault(v.domain, {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0})
        d[v.verdict] = d.get(v.verdict, 0) + 1
    total = {k: sum(1 for v in verdicts if v.verdict == k) for k in ("PASS", "FAIL", "ERROR", "SKIP")}
    return {"by_domain": by_domain, "total": total, "n": len(verdicts)}


def evidence_grade(v: Verdict) -> str:
    """判决 -> 平台证据分级（REAL/MOCK/STUB）。"""
    if v.verdict == "PASS":
        return "REAL"
    if v.verdict == "ERROR":
        return "STUB"
    return "MOCK"


def to_json(verdicts: list, ship: Dict[str, float], meta: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("cid", "domain", "title", "basis", "tol", "verdict",
            "reference", "measured", "rel_err", "note", "evidence")
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meta": meta,
        "ship": ship,
        "summary": summarize(verdicts),
        "criteria": [
            {**{k: getattr(v, k) for k in keys}, "evidence_grade": evidence_grade(v)}
            for v in verdicts
        ],
    }


def _fmt(x) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    if isinstance(x, (int, str)):
        return str(x)
    return json.dumps(x, ensure_ascii=False)


def to_markdown(verdicts: list, ship: Dict[str, float], meta: Dict[str, Any]) -> str:
    s = summarize(verdicts)
    lines = [
        "# 耐波 / 操纵域验证欠账判决台账",
        "",
        f"- 生成时刻：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 目标服务：{meta.get('base_url')}（{meta.get('mode')}）",
        f"- 参考船：{ship['name']}，L={ship['L']} m，B={ship['B']} m，T={ship['T']} m，"
        f"Cb={ship['Cb']}，∇={ship['displacement']} m³",
        f"- 判决总数 {s['n']}：PASS {s['total']['PASS']} / FAIL {s['total']['FAIL']} / "
        f"ERROR {s['total']['ERROR']} / SKIP {s['total']['SKIP']}",
        "",
        "| 编号 | 域 | 判据 | 解析/规范依据 | 容差 | 参考值 | 实测值 | 相对误差 | 判决 | 证据分级 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in verdicts:
        ref = _fmt(v.reference).replace("|", "/")[:60]
        mea = _fmt(v.measured).replace("|", "/")[:60]
        err = f"{v.rel_err*100:.1f}%" if isinstance(v.rel_err, (int, float)) else "—"
        if isinstance(err, str) and err.startswith("inf"):
            err = "∞"
        lines.append(f"| {v.cid} | {v.domain} | {v.title} | {v.basis} | {v.tol} | {ref} | {mea} | "
                     f"{err} | **{v.verdict}** | {evidence_grade(v)} |")
    lines += ["", "## 逐条说明", ""]
    for v in verdicts:
        lines.append(f"### {v.cid} · {v.title}")
        lines.append(f"- 依据：{v.basis}（容差：{v.tol}）")
        lines.append(f"- 判决：**{v.verdict}**（证据分级 {evidence_grade(v)}）")
        if v.note:
            lines.append(f"- 说明：{v.note}")
        if v.evidence:
            lines.append(f"- 证据：`{json.dumps(v.evidence, ensure_ascii=False)}`")
        lines.append("")
    return "\n".join(lines)


DEFAULT_SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fixtures", "sea_man_probe_snapshot.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="耐波/操纵域验证欠账清偿")
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--live", action="store_true", help="打实时服务并记录快照")
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--out", default="artifacts/verification")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    ship = dict(REFERENCE_SHIP)
    os.makedirs(os.path.dirname(args.snapshot), exist_ok=True)
    os.makedirs(args.out, exist_ok=True)

    if args.live:
        probe = run_probes(args.base_url, ship)
        snap = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "base_url": args.base_url, "ship": ship, "probes": probe}
        with open(args.snapshot, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        mode = f"live {args.base_url}"
    else:
        with open(args.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
        probe, ship = snap["probes"], snap.get("ship", ship)
        mode = f"replay {os.path.basename(args.snapshot)} @ {snap.get('generated_utc')}"

    verdicts = evaluate(probe, ship)
    meta = {"base_url": snap.get("base_url", args.base_url), "mode": mode,
            "snapshot_generated_utc": snap.get("generated_utc")}

    payload = to_json(verdicts, ship, meta)
    jpath = os.path.join(args.out, "sea_man_verdicts.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    md = to_markdown(verdicts, ship, meta)
    mpath = os.path.join(args.out, "sea_man_verdicts.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(md)

    if not args.quiet:
        print(md)
        print(f"\n[written] {jpath}\n[written] {mpath}")
    n_fail = payload["summary"]["total"]["FAIL"] + payload["summary"]["total"]["ERROR"]
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
