"""跨端点闭环闸门 v1：阻力 ↔ 推进（PE / T / PD 三条守恒线）。

背景（为什么要这个闸门）
------------------------
平台每个域都有各自的验证记录（阻力 m1536/m1771/m1773、推进 m1498 + PB-009、
几何 tools/verifier/geometry、稳性 m1785、耐波 m1780），但**域间闭合**从无机器
裁判。m1498 在推进域复核时只能「自算 R_T」绕开阻力端点，原因正是两端点从未被
放在一起对账。主计划 M3「全链集成」的门槛因此无从判定。

本模块只做一件事：拿两个端点**自己的回答**互相对撞，判据全部是无需模型常数的
恒等式 + 物理区间，任何一端漂移都会当场自相矛盾。

判据（每条都可机器断言）
------------------------
I1  PE ≡ R_T · V                        阻力端点内部恒等式（零模型假定）
I2  T_required = R_T / (1 - t)          推力平衡；t 只取物理区间 [0.10, 0.30]，
                                        故判据是「平台推力是否落在需求带内」，
                                        不钉单一经验 t（避免把经验公式当基准）
I3  P_D,required = P_E / eta_D          eta_D ∈ [0.50, 0.78] 物理区间（eta0 0.40–0.75
                                        × eta_H 1.10–1.25 × eta_R ≈ 1.0）
I4  交叉：P_E(circ) = T·(1 - t)·V vs P_E(R_T·V)   两端点的有效功率必须同量级
I5  K_T,implied = T / (rho n^2 D^4) ∈ (0.05, 0.6)  —— 与 J = V(1-w)/(nD) 自洽区间
I6  傅汝德标度不变性：几何缩尺 1/2（V/√2, ∇/8）时 C_w 必须不变（theory-exact）

判据形态统一为三态：PASS / FAIL / WITHHELD（缺字段），且**双向可测**：
合法合成闭环必须放行，篡改一位必须拒绝（见 tests/ship/test_chain_gate.py）。

诚实边界
--------
* 端点未返回的字段（湿面积、w、t、Q、eta_D 分项）不猜，一律走区间判据或 WITHHELD。
* 端点 "power" 字段语义未在响应中声明，本闸门按「交付功率 P_D」处理并在台账
  里标 `power_semantics_assumed='delivered'`；若语义不同，结论按 I4 的交叉比
  单独可读，不受影响。
* 本闸门判「两端点是否同一艘船同一工况」，不判绝对精度——绝对精度需要实验/
  解析基准，属各域单点验证的职责。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

KNOT_MS = 0.514444          # kn -> m/s
RHO_SEA = 1025.0            # kg/m^3

T_BAND = (0.10, 0.30)       # 单桨推力减额分数物理区间（Cb 0.6–0.85）
ETA_D_BAND = (0.50, 0.78)   # 准推进系数物理区间
KT_BAND = (0.05, 0.60)      # B 系列工作点 K_T 常见区间
W_BAND = (0.20, 0.40)       # 伴流分数物理区间
D_OVER_T_BAND = (0.40, 0.75)

PE_TOL = 5e-3               # PE 恒等式数值容差
CW_TOL = 5e-2               # 波阻标度不变性容差（代理量 ΔR=Rt-Rf 含非傅汝德分量）


# --------------------------------------------------------------------------- 判据
@dataclass
class Verdict:
    """单条判据结论。interval 为判据允许区间（可空）。"""

    key: str
    state: str                       # PASS / FAIL / WITHHELD
    value: Optional[float] = None
    interval: Optional[Tuple[float, float]] = None
    rel_dev: Optional[float] = None  # 相对最近的允许边界（>0 表示越界）
    note: str = ""

    def as_dict(self) -> Dict[str, object]:
        d = asdict(self)
        if self.interval is not None:
            d["interval"] = list(self.interval)
        return d


def _band_state(value: float, lo: float, hi: float) -> Tuple[str, float]:
    """区间判据。返回 (state, rel_dev)；rel_dev<0 表示距最近边界的富余（相对值）。"""
    if value is None or not math.isfinite(value):
        return "WITHHELD", float("nan")
    if lo <= value <= hi:
        # 相对富余（负数）：到最近边界的距离 / 区间中心
        mid = 0.5 * (lo + hi)
        slack = min(value - lo, hi - value) / mid if mid else 0.0
        return "PASS", -slack
    ref = lo if value < lo else hi
    if ref == 0:
        return "FAIL", float("inf")
    return "FAIL", (value - ref) / ref


def check_pe_identity(rt_kN: float, v_ms: float, pe_kW: float, tol: float = PE_TOL) -> Verdict:
    """I1: P_E = R_T · V（阻力端点内部恒等式）。"""
    if rt_kN is None or pe_kW is None or not v_ms:
        return Verdict("I1_PE_identity", "WITHHELD", note="缺 R_T/P_E/V")
    expect = rt_kN * v_ms                      # kN·m/s = kW
    rel = (pe_kW - expect) / expect if expect else float("inf")
    state = "PASS" if abs(rel) <= tol else "FAIL"
    return Verdict("I1_PE_identity", state, pe_kW,
                   (expect * (1 - tol), expect * (1 + tol)), rel,
                   "P_E 应为 R_T·V [kW]")


def thrust_required_band(rt_kN: float, t_band: Tuple[float, float] = T_BAND) -> Tuple[float, float]:
    """I2: T = R_T/(1-t) 的需求带（t 取区间 -> 区间而非单值）。"""
    lo = rt_kN / (1.0 - t_band[0])
    hi = rt_kN / (1.0 - t_band[1])
    return (min(lo, hi), max(lo, hi))


def check_thrust_closure(rt_kN: float, thrust_kN: Optional[float],
                         t_band: Tuple[float, float] = T_BAND) -> Verdict:
    """I2: 平台推力是否落在 R_T/(1-t) 需求带内。"""
    if rt_kN is None or thrust_kN is None:
        return Verdict("I2_thrust_closure", "WITHHELD", note="缺推力或 R_T")
    lo, hi = thrust_required_band(rt_kN, t_band)
    state, dev = _band_state(thrust_kN, lo, hi)
    return Verdict("I2_thrust_closure", state, thrust_kN, (lo, hi), dev,
                   "T 应落在 R_T/(1-t), t∈[%.2f,%.2f]" % t_band)


def power_required_band(pe_kW: float, eta_band: Tuple[float, float] = ETA_D_BAND) -> Tuple[float, float]:
    """I3: P_D = P_E/eta_D 的需求带。"""
    lo = pe_kW / eta_band[1]     # 大效率 -> 小功率
    hi = pe_kW / eta_band[0]
    return (lo, hi)


def check_power_closure(pe_kW: float, power_kW: Optional[float],
                        eta_band: Tuple[float, float] = ETA_D_BAND) -> Verdict:
    """I3: 平台功率是否落在 P_E/eta_D 需求带内。"""
    if pe_kW is None or power_kW is None:
        return Verdict("I3_power_closure", "WITHHELD", note="缺功率或 P_E")
    lo, hi = power_required_band(pe_kW, eta_band)
    state, dev = _band_state(power_kW, lo, hi)
    return Verdict("I3_power_closure", state, power_kW, (lo, hi), dev,
                   "P_D 应落在 P_E/eta_D, eta_D∈[%.2f,%.2f]" % eta_band)


def check_cross_pe(pe_res_kW: float, thrust_kN: Optional[float], v_ms: float,
                   t_band: Tuple[float, float] = T_BAND) -> Verdict:
    """I4: 交叉有效功率 T·(1-t)·V（推进侧）对 R_T·V（阻力侧）之比。

    比值为 1 才算同一工况。t 取区间 => 比值也成区间，判 1.0 是否落在其内。
    """
    if not pe_res_kW or thrust_kN is None:
        return Verdict("I4_cross_PE", "WITHHELD", note="缺推力或 P_E")
    lo = thrust_kN * (1.0 - t_band[1]) * v_ms      # t 小 -> 比大? T(1-t)V, t=0.30 最小
    hi = thrust_kN * (1.0 - t_band[0]) * v_ms
    lo, hi = min(lo, hi), max(lo, hi)
    ratio_lo, ratio_hi = lo / pe_res_kW, hi / pe_res_kW
    state = "PASS" if ratio_lo <= 1.0 <= ratio_hi else "FAIL"
    ref = ratio_lo if 1.0 < ratio_lo else ratio_hi
    dev = 0.0 if state == "PASS" else (1.0 - ref) / ref
    return Verdict("I4_cross_PE", state, 0.5 * (ratio_lo + ratio_hi), (ratio_lo, ratio_hi), dev,
                   "推进侧 P_E / 阻力侧 P_E 比值（应为 1）")


def check_kt_plausibility(thrust_kN: float, n_rpm: float, D_m: float,
                          rho: float = RHO_SEA, band: Tuple[float, float] = KT_BAND) -> Verdict:
    """I5: 由平台自己的 T/n/D 反演 K_T，必须落在 B 系列工作点区间。"""
    if not thrust_kN or not n_rpm or not D_m:
        return Verdict("I5_KT_implied", "WITHHELD", note="缺 T/n/D")
    n = n_rpm / 60.0
    kt = (thrust_kN * 1e3) / (rho * n * n * D_m ** 4)
    state, dev = _band_state(kt, band[0], band[1])
    return Verdict("I5_KT_implied", state, kt, band, dev,
                   "K_T = T/(rho n^2 D^4)")


def check_diameter_band(D_m: Optional[float], T_m: float,
                        band: Tuple[float, float] = D_OVER_T_BAND) -> Verdict:
    if D_m is None or not T_m:
        return Verdict("I5b_D_over_T", "WITHHELD", note="缺 D/T")
    ratio = D_m / T_m
    state, dev = _band_state(ratio, band[0], band[1])
    return Verdict("I5b_D_over_T", state, ratio, band, dev, "桨径/吃水")


def check_j_band(thrust_kN: float, v_ms: float, n_rpm: float, D_m: float,
                 w_band: Tuple[float, float] = W_BAND) -> Verdict:
    """I5c: 前进比 J = V(1-w)/(nD) 必须落在可工作区间（0.2–1.0）。"""
    if not n_rpm or not D_m or not v_ms:
        return Verdict("I5c_J_implied", "WITHHELD", note="缺 n/D/V")
    n = n_rpm / 60.0
    j_hi = v_ms * (1 - w_band[1]) / (n * D_m)   # w 大 -> J 小
    j_lo = v_ms * (1 - w_band[0]) / (n * D_m)
    j_lo, j_hi = min(j_lo, j_hi), max(j_lo, j_hi)
    state = "PASS" if j_hi >= 0.2 and j_lo <= 1.0 else "FAIL"
    dev = 0.0 if state == "PASS" else (0.2 - j_hi) / 0.2 if j_hi < 0.2 else (j_lo - 1.0)
    return Verdict("I5c_J_implied", state, 0.5 * (j_lo + j_hi), (0.2, 1.0), dev,
                   "J = V(1-w)/(nD), w∈[%.2f,%.2f]" % w_band)


def check_cw_invariance(cw_full: Optional[float], cw_half: Optional[float],
                        tol: float = CW_TOL) -> Verdict:
    """I6: 波阻分量的傅汝德标度不变性（几何缩尺 λ，V√λ，∇λ³）。

    代理量说明：端点不返回湿面积 S，C_w 无法直接反解，故用 ΔR = R_T - R_F 归一化
    到 λ³。ΔR 里仍含粘压阻力/相关津贴等**非**傅汝德分量（不按 λ³ 缩尺），所以这是
    代理判据：容差 ±5%，越界只作「标度可疑」信号，不作单点定罪。
    """
    if cw_full is None or cw_half is None:
        return Verdict("I6_Cw_invariance", "WITHHELD", note="缺缩尺对")
    rel = (cw_half - cw_full) / cw_full if cw_full else float("inf")
    state = "PASS" if abs(rel) <= tol else "FAIL"
    return Verdict("I6_Cw_invariance", state, rel, (-tol, tol), rel,
                   "(C_w,half - C_w,full)/C_w,full")


# --------------------------------------------------------------------------- 组合
def cw_of(rt_kN: float, rf_kN: float, v_ms: float, rho: float, S: Optional[float]) -> Optional[float]:
    """C_w = (R_T - R_F) / (0.5 rho V^2 S)；S 未知则 None（不猜）。"""
    if not S:
        return None
    q = 0.5 * rho * v_ms * v_ms * S
    return (rt_kN - rf_kN) * 1e3 / q if q else None


def evaluate_case(case: Dict[str, object], res_row: Dict[str, object],
                  prop: Dict[str, object]) -> Dict[str, object]:
    """单工况：把阻力行 + 推进应答过一遍 I1–I5c。"""
    v_ms = float(case["speed_kn"]) * KNOT_MS
    rt = float(res_row["total_resistance"])
    rf = float(res_row.get("friction_resistance") or 0.0)
    pe = float(res_row["effective_power"])
    thrust = prop.get("thrust")
    power = prop.get("power")
    n_rpm = prop.get("propeller_rpm")
    D = prop.get("propeller_diameter")

    verdicts = [
        check_pe_identity(rt, v_ms, pe),
        check_thrust_closure(rt, thrust),
        check_power_closure(pe, power),
        check_cross_pe(pe, thrust, v_ms),
        check_kt_plausibility(thrust, n_rpm, D),
        check_diameter_band(D, float(case["T_m"])),
        check_j_band(thrust, v_ms, n_rpm, D),
    ]
    return {
        "case": case,
        "resistance": {"Rt_kN": rt, "Rf_kN": rf, "PE_kW": pe},
        "propulsion": {"thrust_kN": thrust, "power_kW": power,
                       "rpm": n_rpm, "D_m": D,
                       "power_semantics_assumed": "delivered"},
        "verdicts": [v.as_dict() for v in verdicts],
        "n_fail": sum(1 for v in verdicts if v.state == "FAIL"),
        "n_withheld": sum(1 for v in verdicts if v.state == "WITHHELD"),
    }


# --------------------------------------------------------------------------- IO
def _api(base: str, path: str, body: Optional[dict] = None, timeout: float = 30.0):
    url = "%s/%s" % (base.rstrip("/"), path.lstrip("/"))
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:                                   # noqa: BLE001
        return 0, repr(e)


SHIPS: Sequence[Dict[str, object]] = (
    {"name": "KCS", "Lwl": 230.0, "Lpp": 227.0, "B": 32.2, "T": 10.8, "Cb": 0.651,
     "disp_t": 52030.0, "speed_kn": 15.0},
    {"name": "KVLCC2", "Lwl": 320.0, "Lpp": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098,
     "disp_t": 320000.0, "speed_kn": 15.5},
    {"name": "Feeder120", "Lwl": 120.0, "Lpp": 118.0, "B": 19.0, "T": 7.0, "Cb": 0.70,
     "disp_t": 11000.0, "speed_kn": 14.0},
    {"name": "Aframax", "Lwl": 250.0, "Lpp": 245.0, "B": 44.0, "T": 14.5, "Cb": 0.82,
     "disp_t": 115000.0, "speed_kn": 14.5},
    {"name": "Ferry", "Lwl": 160.0, "Lpp": 155.0, "B": 26.0, "T": 6.0, "Cb": 0.62,
     "disp_t": 16000.0, "speed_kn": 20.0},
)


def probe_ship(base: str, ship: Dict[str, object]) -> Dict[str, object]:
    """跑一船的阻力 + 推进，再过一遍判据。"""
    res_body = {"Lwl": ship["Lwl"], "Lpp": ship["Lpp"], "B": ship["B"], "T": ship["T"],
                "displacement": ship["disp_t"], "Cb": ship["Cb"],
                "speeds": [ship["speed_kn"]]}
    prop_body = {"L": ship["Lwl"], "B": ship["B"], "T": ship["T"],
                 "displacement": ship["disp_t"], "Cb": ship["Cb"],
                 "speed": ship["speed_kn"]}
    rs_code, rs = _api(base, "resistance/predict", res_body)
    pr_code, pr = _api(base, "propulsion/predict", prop_body)
    case = {"name": ship["name"], "speed_kn": ship["speed_kn"], "T_m": ship["T"]}
    if rs_code != 200 or pr_code != 200:
        return {"case": case, "error": {"resistance": rs_code, "propulsion": pr_code},
                "raw": [rs, pr], "n_fail": None, "n_withheld": None, "verdicts": []}
    row = rs[0] if isinstance(rs, list) else rs
    return evaluate_case(case, row, pr)


def probe_froude(base: str, ship: Dict[str, object], scale: float = 0.5) -> Dict[str, object]:
    """I6：同一船按几何缩尺 λ 重算，C_w 必须不变。

    S 未知（端点不返回湿面积）——用两端点各自的 R_T、R_F 之差 / 0.5 rho V^2 S，
    由于 S 同比例缩尺（S_half = λ^2 S_full），判据只在知道 S 时可判；这里改用
    等价形式：Cw ∝ (R_T-R_F)/(0.5 rho V^2 S)，S 比 λ^2，故
        Cw_half/Cw_full = [(R_T-R_F)_h/(R_T-R_F)_f] · (V_f^2/V_h^2) · (S_f/S_h)
    其中 V_h = V_f sqrt(λ)，S_f/S_h = 1/λ^2 → 比值 = ΔR_h/ΔR_f · (1/λ) · (1/λ^2)
    """
    lam = scale
    def body(Lwl, Lpp, B, T, d, v):
        return {"Lwl": Lwl, "Lpp": Lpp, "B": B, "T": T,
                "displacement": d, "Cb": ship["Cb"], "speeds": [v]}
    v_kn = float(ship["speed_kn"])
    v_ms = v_kn * KNOT_MS
    full = body(ship["Lwl"], ship["Lpp"], ship["B"], ship["T"], ship["disp_t"], v_kn)
    half = body(ship["Lwl"] * lam, ship["Lpp"] * lam, ship["B"] * lam, ship["T"] * lam,
                ship["disp_t"] * lam ** 3, v_kn * math.sqrt(lam))
    c1, r1 = _api(base, "resistance/predict", full)
    c2, r2 = _api(base, "resistance/predict", half)
    if c1 != 200 or c2 != 200:
        return {"ship": ship["name"], "error": [c1, c2], "raw": [r1, r2],
                "verdict": None}
    a = r1[0] if isinstance(r1, list) else r1
    b = r2[0] if isinstance(r2, list) else r2
    dr_f = float(a["total_resistance"]) - float(a.get("friction_resistance") or 0.0)
    dr_h = float(b["total_resistance"]) - float(b.get("friction_resistance") or 0.0)
    # 标度不变 -> dr_h/dr_f = λ^3；等价归一化后应 = 1
    cw_ratio = (dr_h / dr_f) / lam ** 3 if dr_f else float("nan")
    v = check_cw_invariance(1.0, cw_ratio, tol=CW_TOL)
    return {"ship": ship["name"], "scale": lam, "Fn_full": a.get("froude_number"),
            "Fn_half": b.get("froude_number"), "dr_full_kN": dr_f, "dr_half_kN": dr_h,
            "Cw_ratio_normalised": cw_ratio, "verdict": v.as_dict(),
            "V_ms_full": v_ms, "Rt_full_kN": a["total_resistance"], "Rt_half_kN": b["total_resistance"]}


def run_live(base: str, ships: Iterable[Dict[str, object]] = SHIPS) -> Dict[str, object]:
    rows = [probe_ship(base, s) for s in ships]
    froude = [probe_froude(base, s) for s in ships]
    fails = sum((r.get("n_fail") or 0) for r in rows)
    evaluated = sum(1 for r in rows if r.get("n_fail") is not None)
    froude_fail = sum(1 for f in froude
                      if (f.get("verdict") or {}).get("state") == "FAIL")
    return {
        "api_base": base,
        "n_cases": len(rows),
        "n_evaluated": evaluated,
        "n_verdict_fail": fails,
        "n_froude_fail": froude_fail,
        "cases": rows,
        "froude": froude,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="阻力↔推进跨端点闭环闸门")
    ap.add_argument("--live", action="store_true", help="对 8001 实跑")
    ap.add_argument("--base", default=os.environ.get("SHIP_API_BASE",
                                                    "http://127.0.0.1:8001/api/v1"))
    ap.add_argument("--out", default="artifacts/chain_gate/ledger.json")
    args = ap.parse_args(argv)

    if not args.live:
        ap.print_help()
        return 0
    ledger = run_live(args.base)
    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=1)
    print("cases=%d evaluated=%d verdict_fail=%d froude_fail=%d -> %s"
          % (ledger["n_cases"], ledger["n_evaluated"], ledger["n_verdict_fail"],
             ledger["n_froude_fail"], out))
    for r in ledger["cases"]:
        c = r["case"]
        if r.get("n_fail") is None:
            print("  %-9s %5.1f kn  ERROR %s" % (c["name"], c["speed_kn"], r.get("error")))
            continue
        bad = [v["key"] for v in r["verdicts"] if v["state"] == "FAIL"]
        print("  %-9s %5.1f kn  FAIL=%d %s" % (c["name"], c["speed_kn"], r["n_fail"], ",".join(bad)))
    for f in ledger["froude"]:
        v = f.get("verdict") or {}
        print("  Fn-invariance %-9s ratio=%.4f state=%s"
              % (f["ship"], f.get("Cw_ratio_normalised") or float("nan"), v.get("state")))
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())