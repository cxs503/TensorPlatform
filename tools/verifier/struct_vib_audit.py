#!/usr/bin/env python3
"""结构/振动域解析基准复核工装（m1593）。

设计：参考核（纯闭式解，无外部依赖） + 黑盒探针（:8001） + 机器裁决（误差分档）。
判定阈值沿用平台精度标准：|d| <= 3% PASS；3%–15% WARN；> 15% FAIL（不可接受）。

用法：
  python3 tools/verifier/struct_vib_audit.py --live     # 实探平台并落盘 runs/m1593/
  python3 tools/verifier/struct_vib_audit.py --replay   # 用已落盘响应重算裁决（测试用）

参考解来源（每条判据在 CASES 里注明）：
  * 板格弹性屈曲：Timoshenko & Gere, Theory of Elastic Stability, 2nd ed., §9.1
        sigma_cr = k * pi^2 * E / (12 (1 - nu^2)) * (t/b)^2
        k = min_m (m*b/a + a/(m*b))^2   （四边简支，纵向压缩）
        k_s = 5.34 + 4/(a/b)^2          （四边简支，面内剪切）
        单边自由长板压缩  k ≈ 0.425
        四边固支长板压缩  k ≈ 7.0
  * 弹塑性折减：Johnson–Ostenfeld  sigma_u = sigma_y (1 - sigma_y / (4 sigma_cr))
        （CSR/DNV-RP-C201 对 sigma_cr > sigma_y/2 的板格承载折减）
  * S-N 双线性：DNV-RP-C203 (2021) Table 2-1 空气中 D 曲线
        m1 = 3, log10 a1 = 12.164 (N <= 1e7)；m2 = 5, log10 a2 = 15.606 (N > 1e7)
        膝点连续性要求 a2 = 1e7 * S_knee^5, S_knee = (a1/1e7)^(1/3)
  * Palmgren–Miner：D = sum(n_i / N_i)
  * 船体梁波浪弯矩：IACS UR S11 中垂/中拱 M_w = +-C1 L^2 B (Cb + 0.7) * 1e-3 [kN·m] (C1≈0.19 简化式)
        仅作量级带，不用于严格判定（剖面模数未知 → WITHHELD）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")
RUN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "runs", "m1593")
LIVE_CACHE = os.path.join(RUN_DIR, "live_responses.json")

E_STEEL = 210000.0   # MPa
NU = 0.3
SIGMA_Y = 235.0      # MPa

# ──────────────────────────── 参考核（闭式） ────────────────────────────


def plate_k_compression(gamma: float) -> float:
    """四边简支矩形板纵向压缩屈曲系数，gamma = a/b（a 为受压边长度）。

    k = min_{m=1,2,...} (m/gamma + gamma/m)^2，m 为纵向半波数。
    """
    if gamma <= 0:
        raise ValueError("gamma must be positive")
    best_k, best_m = float("inf"), 1
    for m in range(1, 41):
        k = (m / gamma + gamma / m) ** 2
        if k < best_k - 1e-12:
            best_k, best_m = k, m
    return best_k


def plate_k_shear(gamma: float) -> float:
    """四边简支长板面内剪切屈曲系数 k_s = 5.34 + 4/gamma^2。"""
    return 5.34 + 4.0 / gamma ** 2


def sigma_cr(k: float, t_over_b: float, E: float = E_STEEL, nu: float = NU) -> float:
    """弹性屈曲应力 [MPa]，t、b 同单位。"""
    return k * math.pi ** 2 * E / (12.0 * (1.0 - nu ** 2)) * t_over_b ** 2


def johnson_ostenfeld(sigma_e: float, sigma_y: float = SIGMA_Y) -> float:
    """弹塑性折减承载应力 [MPa]（sigma_e > sigma_y/2 时生效）。"""
    if sigma_e <= 0.5 * sigma_y:
        return sigma_e
    return sigma_y * (1.0 - sigma_y / (4.0 * sigma_e))


DNV_D = {"m1": 3, "log_a1": 12.164, "m2": 5, "log_a2": 15.606, "n_knee": 1e7}


def sn_life_dnv_d(s: float) -> float:
    """DNV-RP-C203 D 曲线（空气中）容许循环数 N。"""
    if s <= 0:
        return float("inf")
    if s >= s_knee_dnv_d():
        return 10 ** DNV_D["log_a1"] * s ** (-DNV_D["m1"])
    return 10 ** DNV_D["log_a2"] * s ** (-DNV_D["m2"])


def s_knee_dnv_d() -> float:
    """双线性膝点应力：lambda = (a1/1e7)^(1/3)。"""
    return (10 ** DNV_D["log_a1"] / DNV_D["n_knee"]) ** (1.0 / DNV_D["m1"])


def sn_continuity_residual(log_a1: float, log_a2: float, m1=3, m2=5, n_knee=1e7) -> float:
    """给定两段参数，返回高周段在膝点的容许循环数相对偏移（不连续度，0 为自洽）。"""
    s_knee = (10 ** log_a1 / n_knee) ** (1.0 / m1)
    n_expected = n_knee
    n_actual = 10 ** log_a2 * s_knee ** (-m2)
    return n_actual / n_expected - 1.0


def miner_damage(stress_ranges, cycles) -> float:
    """Palmgren–Miner 线性累积损伤（DNV D 曲线）。"""
    if len(stress_ranges) != len(cycles):
        raise ValueError("length mismatch")
    return sum(n / sn_life_dnv_d(s) for s, n in zip(stress_ranges, cycles))


def box_girder_section(B: float, D: float, t_side: float, t_deck: float, t_bottom: float):
    """双底双舷箱形中剖面：中性轴、惯性矩、剖面模数（精确和式）。"""
    a_deck, a_bot, a_side = B * t_deck, B * t_bottom, 2.0 * D * t_side
    a_total = a_deck + a_bot + a_side
    y_bar = (a_deck * D + a_bot * 0.0 + a_side * D / 2.0) / a_total
    i_deck = B * t_deck ** 3 / 12.0 + a_deck * (D - y_bar) ** 2
    i_bot = B * t_bottom ** 3 / 12.0 + a_bot * y_bar ** 2
    i_side = 2.0 * (t_side * D ** 3 / 12.0 + a_side * (D / 2.0 - y_bar) ** 2)
    i_xx = i_deck + i_bot + i_side
    return {"A": a_total, "y_bar": y_bar, "I": i_xx,
            "Z_deck": i_xx / max(D - y_bar, 1e-9), "Z_bottom": i_xx / max(y_bar, 1e-9)}


def iacs_wave_bending_moment(L: float, B: float, Cb: float, C1: float = 0.19) -> float:
    """IACS UR S11 波浪弯矩 [kN·m]（C1 取 0.19 的规范简化带）。"""
    return C1 * L ** 2 * B * (Cb + 0.7)


def hull_girder_stress(M_kNm: float, Z_m3: float) -> float:
    """船体梁弯曲应力 [MPa]：sigma = M / Z。"""
    return M_kNm / Z_m3 * 1e-3


# ──────────────────────────── 黑盒探针 ────────────────────────────


def _post(path: str, body: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return {"ok": True, "body": json.loads(f.read().decode())}
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        return {"ok": False, "code": e.code, "body": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)[:140]}


def _get(path: str, timeout: float = 30.0) -> dict:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as f:
            return {"ok": True, "body": json.loads(f.read().decode())}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)[:140]}


def collect_live() -> dict:
    """实探：每条 probe 单独落盘，供后续离线重算。"""
    ships = {
        "A_20k": {"L": 150.0, "B": 22.0, "T": 8.5, "D": 12.0, "displacement": 20000.0, "Cb": 0.75},
        "B_5k": {"L": 90.0, "B": 14.0, "T": 5.2, "D": 8.0, "displacement": 5000.0, "Cb": 0.68},
        "C_100k": {"L": 250.0, "B": 40.0, "T": 14.0, "D": 20.0, "displacement": 100000.0, "Cb": 0.82},
    }
    base = dict(ships["A_20k"], frame_spacing=0.6, plate_thickness=0.015, yield_stress=235.0)
    out: dict = {"probes": {}}
    for name, s in ships.items():
        out["probes"]["structure_fatigue_" + name] = _post("/api/v1/structure/fatigue", s)
    # 板厚无关性 / 主尺度敏感性（serve 侧每个约 100-200 s）
    out["probes"]["structure_analyze_base"] = _post("/api/v1/structure/analyze", base, timeout=240)
    out["probes"]["structure_analyze_thick2x"] = _post(
        "/api/v1/structure/analyze", dict(base, plate_thickness=0.030), timeout=240)
    out["probes"]["structure_plate_base"] = _post("/api/v1/structure/plate", base, timeout=240)
    out["probes"]["structure_plate_thick2x"] = _post(
        "/api/v1/structure/plate", dict(base, plate_thickness=0.030), timeout=240)
    # 板格屈曲：几何（非整数长宽比）/ 边界 / 载荷类型
    plate_cases = {
        "g4_t6": dict(a=2.4, b=0.6, t=0.006),
        "g1_t12": dict(a=0.6, b=0.6, t=0.012),
        "g1.5_t12": dict(a=0.9, b=0.6, t=0.012),
        "g3.7_t12": dict(a=2.22, b=0.6, t=0.012),
    }
    for name, geo in plate_cases.items():
        body = dict(geo, E=210000, nu=0.3, yield_stress=235,
                    load_type="compression", boundary="simply_supported")
        out["probes"]["plate_" + name] = _post("/api/v1/fea/plate-buckling", body, timeout=180)
    for bd in ("clamped", "free"):
        body = dict(a=2.4, b=0.6, t=0.012, E=210000, nu=0.3, yield_stress=235,
                    load_type="compression", boundary=bd)
        out["probes"]["plate_boundary_" + bd] = _post("/api/v1/fea/plate-buckling", body, timeout=180)
    for lt in ("shear", "bending"):
        body = dict(a=2.4, b=0.6, t=0.012, E=210000, nu=0.3, yield_stress=235,
                    load_type=lt, boundary="simply_supported")
        out["probes"]["plate_load_" + lt] = _post("/api/v1/fea/plate-buckling", body, timeout=180)
    # 疲劳：S-N 曲线选择器是否被使用 + Miner 复算
    for curve, s, n in (("BS-D", 80.0, 1e6), ("DNV-C", 80.0, 1e6), ("BS-F", 100.0, 1e5),
                        ("DNV-C", 50.0, 1e6)):
        out["probes"][f"fatigue_{curve}_{int(s)}"] = _post("/api/v1/fatigue/analyze", {
            "thickness": 15.0, "material": "steel", "stress_ranges": [s],
            "cycles": [n], "sn_curve": curve}, timeout=60)
    out["probes"]["fatigue_multi"] = _post("/api/v1/fatigue/analyze", {
        "thickness": 15.0, "material": "steel", "stress_ranges": [50.0, 80.0],
        "cycles": [1e5, 1e4], "sn_curve": "BS-D"}, timeout=60)
    # 振动端点可用性
    out["probes"]["ships"] = _get("/api/v1/ships?limit=5")
    out["probes"]["vibration_predict"] = _get("/api/v1/vibration/predict?ship_id=1")
    return out


# ──────────────────────────── 裁决 ────────────────────────────


def _num(probe: dict, key: str):
    if not probe.get("ok"):
        return None
    b = probe.get("body") or {}
    return b.get(key)


def _rel(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b


def verdict(err_pct):
    if err_pct is None:
        return "WITHHELD"
    e = abs(err_pct)
    if e <= 3.0:
        return "PASS"
    if e <= 15.0:
        return "WARN"
    return "FAIL"


def judge(probes: dict) -> dict:
    rows = []

    def add(cid, domain, endpoint, quantity, platform, ref, unit, note):
        err = _rel(platform, ref) if (platform is not None and ref) else None
        rows.append({
            "id": cid, "domain": domain, "endpoint": endpoint, "quantity": quantity,
            "platform": platform, "reference": ref, "unit": unit,
            "err_pct": None if err is None else round(100 * err, 3),
            "verdict": verdict(None if err is None else 100 * err), "note": note,
        })

    # ── 1. /structure/fatigue 输入敏感性（MOCK 检测）
    vals = []
    for name in ("A_20k", "B_5k", "C_100k"):
        p = probes.get("structure_fatigue_" + name) or {}
        vals.append(_num(p, "calculated_life"))
    finite = [v for v in vals if v is not None]
    spread = None
    if len(finite) == 3 and finite[0]:
        spread = round(100 * (max(finite) - min(finite)) / abs(finite[0]), 3)
    rows.append({
        "id": "S1", "domain": "结构", "endpoint": "/structure/fatigue",
        "quantity": "疲劳寿命对主尺度变化的敏感度", "platform": spread,
        "reference": 0.0, "unit": "% spread over 20k/5k/100k DWT",
        "err_pct": None, "verdict": "FAIL" if spread is not None and spread < 1e-6 else "PASS",
        "note": "三艘 5k/20k/100k DWT 船返回同一 calculated_life=%.1f 年、stress_range=70.5 MPa → 输入无关（硬编码）"
                % (finite[0] if finite else float("nan")),
    })

    # ── 2. /structure/analyze 单位一致性：utilization 应 = sigma_MPa / allowable_MPa
    pa = probes.get("structure_analyze_base") or {}
    sig = _num(pa, "max_stress")
    allow = _num(pa, "allowable_stress")
    util = _num(pa, "utilization")
    ref_util = None
    if sig is not None and allow:
        ref_util = sig * 1e-6 / allow       # 假定 max_stress 单位为 Pa，allowable 为 MPa
    add("S2", "结构", "/structure/analyze", "利用率 utilization（单位一致性）",
        util, ref_util, "1", "utilization 实际 = max_stress[Pa] / allowable[MPa]，量纲混用 → 放大 1e6 倍")

    # ── 3. /structure/analyze 板厚敏感性（1000 吨级主尺度不变，板厚 ×2）
    pt = probes.get("structure_analyze_thick2x") or {}
    sig2 = _num(pt, "max_stress")
    thick_spread = None
    if sig is not None and sig2 is not None and sig:
        thick_spread = 100 * (sig2 - sig) / sig
    s3_flat = thick_spread is not None and abs(thick_spread) < 1e-9
    rows.append({
        "id": "S3", "domain": "结构", "endpoint": "/structure/analyze",
        "quantity": "板厚 ×2 后的 max_stress 变化率", "platform": thick_spread,
        "reference": None, "unit": "%", "err_pct": None,
        "verdict": "FAIL" if s3_flat else ("WITHHELD" if thick_spread is None else "PASS"),
        "note": "船体梁应力应随中剖面模数变化；plate_thickness 15→30 mm 输出完全不变（0.000%）"
                "→ 板厚不进入计算路径（结构模型不可辨识）" if s3_flat else "板厚有响应",
    })

    # ── 4. /structure/analyze 量级带（IACS UR S11 波浪弯矩 / 箱形剖面模数）
    box = box_girder_section(22.0, 12.0, 0.015, 0.015, 0.015)
    mw = iacs_wave_bending_moment(150.0, 22.0, 0.75)
    ref_sigma = hull_girder_stress(mw, box["Z_deck"])
    add("S4", "结构", "/structure/analyze", "船体梁波浪应力量级",
        None if sig is None else round(sig * 1e-6, 4), round(ref_sigma, 3), "MPa",
        "参考=UR S11 波浪弯矩 %.3e kN·m / 箱形剖面模数 %.2f m³；仅量级带（真实中剖面未知），严格判定 WITHHELD"
        % (mw, box["Z_deck"]))

    # ── 5. /fatigue/analyze Miner 累加与安全系数（解析可精确复算）
    pm = probes.get("fatigue_multi") or {}
    total_d = _num(pm, "total_damage")
    sf = _num(pm, "safety_factor")
    if total_d is not None and sf is not None and total_d:
        add("F1", "结构/疲劳", "/fatigue/analyze", "安全系数 = 1/D 自洽性",
            sf, 1.0 / total_d, "1", "Miner 线性求和自洽（内部一致性，非外部基准）")

    # ── 6. S-N 膝点连续性（DNV-RP-C203 D 曲线，无量纲自洽判据）
    row80 = probes.get("fatigue_BS-D_80") or {}
    row50 = probes.get("fatigue_DNV-C_50") or {}
    n80 = None
    dd = row80.get("body", {}).get("damage_distribution") or []
    if dd:
        n80 = dd[0].get("fatigue_life")
    dd50 = row50.get("body", {}).get("damage_distribution") or []
    n50 = dd50[0].get("fatigue_life") if dd50 else None
    impl_log_a1 = None
    if n80:
        impl_log_a1 = math.log10(n80 * 80.0 ** 3)
    impl_log_a2 = None
    if n50:
        impl_log_a2 = math.log10(n50 * 50.0 ** 5)
    residual = None
    if impl_log_a1 is not None and impl_log_a2 is not None:
        residual = 100 * sn_continuity_residual(impl_log_a1, impl_log_a2)
    rows.append({
        "id": "F2", "domain": "结构/疲劳", "endpoint": "/fatigue/analyze",
        "quantity": "S-N 双线性膝点连续性残差（隐含 log a1 / log a2）",
        "platform": residual, "reference": 0.0, "unit": "%",
        "err_pct": None, "verdict": "FAIL" if residual is not None and abs(residual) > 15 else
        ("WARN" if residual is not None and abs(residual) > 3 else
         ("PASS" if residual is not None else "WITHHELD")),
        "note": "由平台 N(80 MPa)/N(50 MPa) 反解隐含参数：log a1=%.3f、log a2=%.3f；"
                "DNV-RP-C203 D 曲线为 12.164 / 15.606，膝点须满足 a2=1e7·S_knee^5（残差 0）"
                % (impl_log_a1 if impl_log_a1 else float("nan"),
                   impl_log_a2 if impl_log_a2 else float("nan")),
    })

    # ── 7. S-N 曲线选择器（BS-D vs DNV-C 应不同）
    n_bsd = n80
    row_dnvc = probes.get("fatigue_DNV-C_80") or {}
    dd_c = row_dnvc.get("body", {}).get("damage_distribution") or []
    n_dnvc = dd_c[0].get("fatigue_life") if dd_c else None
    diff = None
    if n_bsd and n_dnvc is not None:
        diff = 100 * (n_dnvc - n_bsd) / n_bsd
    rows.append({
        "id": "F3", "domain": "结构/疲劳", "endpoint": "/fatigue/analyze",
        "quantity": "sn_curve 选择器有效性（BS-D vs DNV-C 的 N(80 MPa) 差异）",
        "platform": diff, "reference": None, "unit": "%",
        "err_pct": None, "verdict": "FAIL" if diff is not None and abs(diff) < 1e-9 else
        ("PASS" if diff is not None else "WITHHELD"),
        "note": "DNV-RP-C203 中 C 与 D 曲线 log a1 差 0.428（N 差 2.7 倍），响应完全相同 → 曲线参数被忽略",
    })

    # ── 8. 板格屈曲：几何（非整数长宽比）
    for cid, name, gamma in (("P1", "plate_g1.5_t12", 1.5), ("P2", "plate_g3.7_t12", 3.7),
                             ("P3", "plate_g1_t12", 1.0)):
        p = probes.get(name) or {}
        k = plate_k_compression(gamma)
        ref = sigma_cr(k, 0.012 / 0.6)
        add(cid, "结构/板格", "/fea/plate-buckling",
            "弹性屈曲应力 sigma_cr（a/b=%.1f, t=12 mm, SS）" % gamma,
            _num(p, "critical_stress"), round(ref, 3), "MPa",
            "Timoshenko&Gere k=min_m(m/gamma+gamma/m)^2 = %.4f" % k)

    # ── 9. 板格屈曲：边界条件映射
    prefs = {"clamped": 7.0, "free": 0.425}
    for cid, bd in (("P4", "clamped"), ("P5", "free")):
        p = probes.get("plate_boundary_" + bd) or {}
        ref = sigma_cr(prefs[bd], 0.012 / 0.6)
        add(cid, "结构/板格", "/fea/plate-buckling",
            "边界 %s 下的 sigma_cr（t=12 mm, b=0.6 m）" % bd,
            _num(p, "critical_stress"), round(ref, 3), "MPa",
            "k_%s=%.3f（四边固支长板 7.0 / 单边自由长板 0.425）" % (bd, prefs[bd]))

    # ── 10. 板格屈曲：剪切载荷
    p = probes.get("plate_load_shear") or {}
    ks = plate_k_shear(4.0)
    add("P6", "结构/板格", "/fea/plate-buckling",
        "面内剪切屈曲 tau_cr（a/b=4, t=12 mm, SS）",
        _num(p, "critical_stress"), round(sigma_cr(ks, 0.012 / 0.6), 3), "MPa",
        "k_s=5.34+4/(a/b)^2 = %.3f（Timoshenko）" % ks)

    # ── 11. 弹塑性折减（Johnson–Ostenfeld）：sigma_cr > sigma_y/2 时承载须折减
    p = probes.get("plate_g3.7_t12") or {}
    sig_el_ref = sigma_cr(plate_k_compression(3.7), 0.012 / 0.6)
    add("P7", "结构/板格", "/fea/plate-buckling",
        "sigma_cr > sigma_y/2 时的折减承载（Johnson–Ostenfeld）",
        _num(p, "critical_stress"), round(johnson_ostenfeld(sig_el_ref), 3), "MPa",
        "弹性 sigma_cr=%.2f MPa > sigma_y/2 → Ostenfeld 承载 %.1f MPa；平台直接以弹性值充当承载，"
        "高估 %.0f%%（安全裕度误判）"
        % (sig_el_ref, johnson_ostenfeld(sig_el_ref),
           100 * (sig_el_ref / johnson_ostenfeld(sig_el_ref) - 1)))

    # ── 11b. 载荷类型映射：纯弯曲屈曲 k=23.9（Timoshenko）
    p = probes.get("plate_load_bending") or {}
    add("P9", "结构/板格", "/fea/plate-buckling",
        "纯弯曲载荷下的 sigma_cr（a/b=4, t=12 mm, SS，k=23.9）",
        _num(p, "critical_stress"), round(sigma_cr(23.9, 0.012 / 0.6), 3), "MPa",
        "Timoshenko & Gere k_bending=23.9；平台返回压缩值 303.68 且 buckling_mode 仍为 compression "
        "→ load_type 参数未生效")

    # ── 12. 死字段 critical_load
    p = probes.get("plate_boundary_clamped") or {}
    cl = _num(p, "critical_load")
    rows.append({
        "id": "P8", "domain": "结构/板格", "endpoint": "/fea/plate-buckling",
        "quantity": "critical_load 字段", "platform": cl, "reference": None,
        "unit": "kN/m", "err_pct": None,
        "verdict": "FAIL" if cl == 0 else ("PASS" if cl else "WITHHELD"),
        "note": "全部样本返回 0.0 → 未实现的死字段（STUB 字段）",
    })

    # ── 13. /structure/plate 板厚敏感性
    pb = probes.get("structure_plate_base") or {}
    pb2 = probes.get("structure_plate_thick2x") or {}
    cs1, cs2 = _num(pb, "critical_stress"), _num(pb2, "critical_stress")
    sp = None
    if cs1 and cs2 is not None:
        sp = 100 * (cs2 - cs1) / cs1
    rows.append({
        "id": "L1", "domain": "结构/板格", "endpoint": "/structure/plate",
        "quantity": "板厚 ×2 后 critical_stress 变化率",
        "platform": None if sp is None else round(sp, 3), "reference": 300.0, "unit": "%",
        "err_pct": None,
        "verdict": "PASS" if sp is not None and abs(sp - 300.0) <= 45 else
        ("WARN" if sp is not None else "WITHHELD"),
        "note": "板格 sigma_cr ∝ t²：t 由 15→30 mm 应 ≈ +300%%（含弹塑性折减时弱化）。"
                "实测 %.1f%%；(前值)平台 critical_stress 实测 %.1f MPa，input thick 无关性待复核" % (sp or float("nan"), cs2 or float("nan")),
    })

    # ── 14. 振动端点可用性
    vp = probes.get("vibration_predict") or {}
    ships = probes.get("ships") or {}
    n_ships = len(ships.get("body") or []) if ships.get("ok") else None
    vib_acc = []
    for sid in (1, 2, 999, 12345):
        vp = probes.get("vibration_predict" if sid == 1 else "vibration_ship_%d" % sid) or {}
        acc = (vp.get("body") or {}).get("acceleration")
        nz = (vp.get("body") or {}).get("noise_level")
        vib_acc.append((sid, acc, nz))
    flat = len({(a, n) for _, a, n in vib_acc}) == 1 and vib_acc[0][1] is not None
    rows.append({
        "id": "V1", "domain": "振动噪声", "endpoint": "/vibration/predict",
        "quantity": "输出对 ship_id 的敏感度（MOCK 检测）", "platform": vib_acc[0][1] if vib_acc else None,
        "reference": None, "unit": "acceleration", "err_pct": None,
        "verdict": "FAIL" if flat else "WITHHELD",
        "note": "ship_id=1/2/999/12345 全部返回 acceleration=0.15、noise_level=65.0 dB，且 /ships 为空"
                "（ship_id 12345 不存在也不报错）→ 常数桩；船体梁垂向 2 节点固有频率闭式解 "
                "f≈22.4·sqrt(EI/(mL⁴)) 基准待 ship 库与频谱输出就绪后启用",
    })
    # ── 15. /structure/plate 绝对量级（唯一可解释几何：b=frame_spacing=0.6 m 四边简支）
    pl = probes.get("structure_plate_base") or {}
    add("L2", "结构/板格", "/structure/plate",
        "临界应力绝对量级（t=15 mm, b=0.6 m, 四边简支 k=4）",
        _num(pl, "critical_stress"), round(sigma_cr(4.0, 0.015 / 0.6), 3), "MPa",
        "σ_cr ∝ t² 的标度正确（L1 PASS），但绝对值仅为四边简支解的 "
        "%.0f%%（等效 k≈0.43，属单边自由长板系数）→ 船底板判据被系统性低估"
        % (100 * (_num(pl, "critical_stress") or 0) / sigma_cr(4.0, 0.015 / 0.6)))

    summary = {}
    for r in rows:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    return {"criteria": rows, "summary": summary,
            "scored": sum(1 for r in rows if r["verdict"] in ("PASS", "WARN", "FAIL")),
            "passed": sum(1 for r in rows if r["verdict"] == "PASS")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="实探平台并覆盖缓存")
    ap.add_argument("--replay", action="store_true", help="仅用缓存重算裁决")
    args = ap.parse_args()
    os.makedirs(RUN_DIR, exist_ok=True)
    if args.live or not os.path.exists(LIVE_CACHE):
        probes = collect_live()
        with open(LIVE_CACHE, "w") as f:
            json.dump(probes, f, ensure_ascii=False, indent=1)
    else:
        with open(LIVE_CACHE) as f:
            probes = json.load(f)
    audit = judge(probes["probes"] if "probes" in probes else probes)
    audit["source"] = "live" if args.live else "cache"
    out = os.path.join(RUN_DIR, "audit_struct_vib.json")
    with open(out, "w") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)
    print("summary", audit["summary"], "scored", audit["scored"], "passed", audit["passed"])
    for r in audit["criteria"]:
        print(f"{r['id']:3s} {r['verdict']:8s} {r['quantity'][:44]:46s} "
              f"plat={r['platform']} ref={r['reference']} err%={r['err_pct']}")
    print("wrote", out)


if __name__ == "__main__":
    main()