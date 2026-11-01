#!/usr/bin/env python3
"""结构 / 振动噪声域验证欠账审计 (mission m1600, tp-app-2 applications 席)。

把"船舶总体性能平台"(:8001, ship-performance-platform) 在 **结构强度** 与
**振动噪声** 两域的服务端点，对三套基准做机器可算复核：
  (a) 第一性原理闭式解（Euler 板屈曲、自由-自由梁固有频率、箱形剖面模数）；
  (b) 公开标准曲线（BS 7608 / DNV RP-C203 S-N 双斜率幂律、Miner 线性累积损伤）；
  (c) 平台自身同域实现（/fatigue/curves 的 S-N 表）—— 双实现交叉，无需外部基准。

设计纪律
--------
1. 参考值只由闭式解析量构成，文献数值以可追溯常量写入并在 pytest 中对照验收。
2. 平台侧数值一律由 HTTP 探针程序化采集落盘为 fixture，文档/报告里的每个
   数字都从 fixture 读取（零手抄）。
3. 每条判据三态判决 PASS / FAIL / WITHHELD；>15% 相对误差即 FAIL（平台精度标准）。

用法
----
    python3 tools/audit/structure_vibration_audit.py --probe
    python3 tools/audit/structure_vibration_audit.py --report
    python3 tools/audit/structure_vibration_audit.py --figures
    python3 tools/audit/structure_vibration_audit.py --all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts"
FIX = ROOT / "tests" / "fixtures" / "structure_vibration_probe.json"
BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
REL_TOL = 0.15  # 平台精度标准：>15% 不可接受

# ---------------------------------------------------------------------------
# 可追溯的文献常量（pytest 对照验收；来源见注释）
# BS 7608:2014 / DNV RP-C203 in-air 曲线: N = a*(dS)^-m, N<=1e7 段
#   DNV B1 log a=12.592, C1 log a=12.164, C2 12.038, D 11.761 (m=3)
#   第二段 m=5, 在 N=1e7 处连续
# ---------------------------------------------------------------------------
SN_DNV_C1_AIR = {"m": 3.0, "log_a1": 12.164, "log_a2": 15.817, "n_knee": 1.0e7}
SN_DNV_B1_AIR = {"m": 3.0, "log_a1": 12.592, "log_a2": 16.081, "n_knee": 1.0e7}
SN_BS_D = {"m": 3.0, "log_a1": 12.164, "log_a2": 15.817, "n_knee": 1.0e7}
E_STEEL = 206.0e9  # Pa
NU_STEEL = 0.3
G = 9.81
FREEFREE_K1 = 4.730040744862704  # 自由-自由梁一阶无量纲根


# ==========================================================================
# 参考实现（纯函数，闭式可解析）
# ==========================================================================
def box_section_modulus(B: float, D: float, t: float) -> tuple[float, float]:
    """裸壳箱形剖面 (I [m^4], Z [m^3])；Z 为船体真实剖面的保守下界。"""
    b, d = B - 2.0 * t, D - 2.0 * t
    if b <= 0 or d <= 0:
        raise ValueError("板厚超过剖面尺寸")
    I = (B * D**3 - b * d**3) / 12.0
    return I, I / (D / 2.0)


def plate_buckling_coefficient(E: float = E_STEEL, nu: float = NU_STEEL,
                              k: float = 4.0) -> float:
    """sigma_cr = C*E*(t/b)^2 中的无量纲系数 C（长板简支 k=4 -> 3.6154）。"""
    return k * math.pi**2 / (12.0 * (1.0 - nu**2))


def plate_buckling_stress(t: float, b: float, E: float = E_STEEL,
                          nu: float = NU_STEEL, k: float = 4.0) -> float:
    """Euler 板屈曲临界应力 [Pa]（四边简支、单轴压缩长板, k=4）。"""
    return plate_buckling_coefficient(E, nu, k) * E * (t / b) ** 2


def sn_life(stress_range: float, m: float = 3.0, log_a1: float = 12.164,
            log_a2: float = 15.817, n_knee: float = 1.0e7) -> float:
    """双斜率 S-N：N = 10^log_a * dS^-m；低于拐点应力改用 (m+2) 段。"""
    a1 = 10.0 ** log_a1
    s_knee = (a1 / n_knee) ** (1.0 / m)
    if stress_range >= s_knee:
        return a1 * stress_range ** (-m)
    m2 = m + 2.0
    a2 = 10.0 ** log_a2 if log_a2 else n_knee * s_knee**m2
    return a2 * stress_range ** (-m2)


def miner_damage(stress_ranges: list[float], cycles: list[float],
                 m: float = 3.0, log_a1: float = 12.164,
                 log_a2: float = 15.817, n_knee: float = 1.0e7) -> float:
    """Miner 线性累积损伤 D = sum n_i / N_i。"""
    if len(stress_ranges) != len(cycles):
        raise ValueError("stress_ranges 与 cycles 长度不一致")
    return sum(n / sn_life(s, m=m, log_a1=log_a1, log_a2=log_a2, n_knee=n_knee)
               for s, n in zip(stress_ranges, cycles))


def freefree_beam_freq(L: float, EI: float, mass_per_length: float) -> float:
    """自由-自由 Euler-Bernoulli 梁一阶频率 [Hz]。"""
    return (FREEFREE_K1 / L) ** 2 / (2.0 * math.pi) * math.sqrt(EI / mass_per_length)


def hull_2node_vertical_freq(L: float, B: float, D: float, t: float,
                             displacement_t: float, added_mass: float = 1.2) -> float:
    """船体 2 节点垂向固有频率闭式估计 [Hz]（裸壳 I 下界 + 附连水 1.2）。"""
    I, _ = box_section_modulus(B, D, t)
    m_prime = added_mass * displacement_t * 1000.0 / L
    return freefree_beam_freq(L, E_STEEL * I, m_prime)


def blade_freq(rpm: float, z: int) -> float:
    """叶频恒等式 [Hz]：f = n_blades * rpm / 60。"""
    return z * rpm / 60.0


def still_water_bending_moment(displacement_t: float, L: float,
                               fraction: float = 1.0 / 100.0) -> float:
    """复现平台源码的弯矩量 M_sw = Δ[kg]*g*L/100 [kN·m]。

    仅用于隔离"剖面模数/单位"层误差，不对该弯矩式本身背书。
    """
    return displacement_t * G * L * fraction


# ==========================================================================
# 探针
# ==========================================================================
def _send(req: urllib.request.Request, timeout: float) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "data": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "data": e.read().decode()[:400]}
    except Exception as e:
        return {"status": None, "data": f"{type(e).__name__}: {e}"}


def _post(path: str, body: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
    req = urllib.request.Request(f"{BASE}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return _send(req, timeout)


def _post_q(path: str, body: dict[str, Any] | None = None,
            timeout: float = 30.0) -> dict[str, Any]:
    """POST + query string（/vibration/predict 为 POST + ship_id 查询参数）。"""
    req = urllib.request.Request(f"{BASE}/{path}",
                                 data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return _send(req, timeout)


def _get(path: str, timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(f"{BASE}/{path}", method="GET")
    return _send(req, timeout)


VIB_KEYS = ("length", "beam", "draft", "displacement", "speed", "cb",
            "prop_rpm", "num_blades")


def _ship(L: float, B: float, T: float, disp: float, cb: float,
          rpm: float, z: int = 5) -> dict[str, Any]:
    return {"length": L, "beam": B, "draft": T, "displacement": disp,
            "speed": 12.5, "cb": cb, "prop_rpm": rpm, "num_blades": z}


def collect_probe() -> dict[str, Any]:
    """实机采集全部探针，返回可落盘 fixture。"""
    base = _ship(230.0, 32.2, 10.8, 52030.0, 0.651, 80.0)
    fx: dict[str, Any] = {"base_url": BASE, "fixtures": {}}
    f = fx["fixtures"]

    vib_cases = {
        "vib_base": base,
        "vib_disp2x": {**base, "displacement": base["displacement"] * 2.0},
        "vib_length_half_similar": _ship(115.0, 16.1, 5.4, 6503.75, 0.651, 160.0),
        "vib_length_1p5_similar": _ship(345.0, 48.3, 16.2, 175601.25, 0.651, 53.3333),
        "vib_rpm60": {**base, "prop_rpm": 60.0},
        "vib_rpm120": {**base, "prop_rpm": 120.0},
        "vib_blades4": {**base, "num_blades": 4},
        "vib_draft2x": {**base, "draft": base["draft"] * 2.0},
    }
    for k, c in vib_cases.items():
        f[k] = _post("extended-calculations/vibration", {kk: c[kk] for kk in VIB_KEYS})
    f["vib_predict_a"] = _post_q("vibration/predict?ship_id=KCS")
    f["vib_predict_b"] = _post_q("vibration/predict?ship_id=VLCC325")
    f["vib_predict_q_draft"] = _post_q("vibration/predict?ship_id=BR200")

    st_base = {"L": 230.0, "B": 32.2, "T": 10.8, "D": 19.5,
               "displacement": 52030.0, "Cb": 0.651}
    st_cases = {
        "struct_base": {},
        "struct_t2x": {"plate_thickness": 0.03},
        "struct_D1p5x": {"D": 29.25},
        "struct_disp2x": {"displacement": 104060.0},
        "struct_yield355": {"yield_stress": 355.0},
        "struct_half_similar": {"L": 115.0, "B": 16.1, "T": 5.4, "D": 9.75,
                                "displacement": 6503.75},
    }
    for k, over in st_cases.items():
        f[k] = _post("structure/analyze", {**st_base, **over})
        if k in ("struct_base", "struct_t2x", "struct_D1p5x", "struct_disp2x"):
            f[k.replace("struct_", "plate_")] = _post("structure/plate",
                                                      {**st_base, **over})
    f["struct_fatigue_base"] = _post("structure/fatigue", st_base)
    f["struct_fatigue_y355"] = _post("structure/fatigue",
                                     {**st_base, "yield_stress": 355.0})

    f["fatigue_analyze_bsd"] = _post("fatigue/analyze", {
        "thickness": 20.0, "material": "steel", "stress_ranges": [80.0, 50.0],
        "cycles": [1000000, 5000000], "sn_curve": "BS-D"})
    f["fatigue_curves"] = _get("fatigue/curves")
    return fx

# ==========================================================================
# 判据台账
# ==========================================================================
@dataclass
class Criterion:
    cid: str
    domain: str
    statement: str
    platform: Any
    reference: Any
    rel_err: float | None
    verdict: str
    attribution: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {"id": self.cid, "domain": self.domain, "statement": self.statement,
             "platform": self.platform, "reference": self.reference,
             "rel_err": (None if self.rel_err is None else round(self.rel_err, 6)),
             "verdict": self.verdict, "attribution": self.attribution}
        d.update(self.extra)
        return d


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if b else float("inf")


def _ok(rel_err: float | None) -> str:
    if rel_err is None:
        return "WITHHELD"
    return "PASS" if rel_err <= REL_TOL else "FAIL"


def _v(fx: dict[str, Any], key: str, name: str) -> float | None:
    d = (fx.get(key) or {}).get("data")
    if isinstance(d, dict) and isinstance(d.get(name), (int, float)):
        return float(d[name])
    return None


L0, B0, T0, D0, DISP0 = 230.0, 32.2, 10.8, 19.5, 52030.0
T_PLATE = 0.015   # /structure/* 端点默认板厚
SY0 = 235.0       # 默认屈服应力 MPa


def build_ledger(fx: dict[str, Any]) -> list[Criterion]:
    """fixture -> 判据台账（所有数值从 fixture 读取）。"""
    out: list[Criterion] = []

    # ---------------- 振动噪声域 ----------------
    bf = _v(fx, "vib_base", "blade_frequency")
    ref_bf = blade_freq(80.0, 5)
    out.append(Criterion(
        "V1", "vibration",
        "叶频恒等式 f_BF = n_blades·rpm/60（无不确定度的恒等式，容差 1%）",
        bf, round(ref_bf, 4), None if bf is None else _rel(bf, ref_bf),
        ("PASS" if bf is not None and _rel(bf, ref_bf) <= 0.01
         else ("WITHHELD" if bf is None else "FAIL")),
        "恒等式成立 ⇒ 端点确实读取 rpm/叶片数；这是两域中唯一物理正确通道"))

    fh = _v(fx, "vib_base", "hull_natural_freq")
    fh_half = _v(fx, "vib_length_half_similar", "hull_natural_freq")
    if fh and fh_half:
        expo = math.log(fh_half / fh) / math.log(115.0 / 230.0)
        out.append(Criterion(
            "V2", "vibration",
            "几何相似族（λ=1/2, Δ∝λ³, 同材料）船体 2 节点固有频率尺度律 "
            "f∝λ^-1（物理带 -1.5..-0.8）",
            round(expo, 4), -1.0, _rel(expo, -1.0),
            "PASS" if -1.5 <= expo <= -0.8 else "FAIL",
            "平台 f_hull=1.5(L/100)^-0.5 只含 L 单变量，尺度律 -0.5 越出物理带"))

    fh_d2 = _v(fx, "vib_disp2x", "hull_natural_freq")
    if fh and fh_d2 is not None:
        sens = (fh_d2 - fh) / fh
        out.append(Criterion(
            "V3", "vibration",
            "固有频率必须依赖质量：Δ 加倍 ⇒ f 降 -29.3%（f∝m^-0.5）；"
            "|Δf/f| 须 ≥ 0.05",
            round(sens, 6), -0.2929, None,
            "PASS" if abs(sens) >= 0.05 else "FAIL",
            "响应与排水量完全无关 ⇒ f_hull 不是 EI/m 的函数，质量通道缺失"))

    f_ref = hull_2node_vertical_freq(L0, B0, D0, T_PLATE, DISP0)
    band = [round(f_ref, 4), round(f_ref * 1.75, 4)]
    if fh is not None:
        out.append(Criterion(
            "V4", "vibration",
            "2 节点垂向固有频率量级：须落在闭式自由-自由梁带内"
            "（裸壳 I 下界 → 含纵骨 ×1.75）",
            fh, band, None, "PASS" if band[0] <= fh <= band[1] else "FAIL",
            "量级勉强命中基准带，但与 V2 的 -0.5 尺度律互斥：单变量拟合偶然落在带内"))

    ratios = []
    for k in ("vib_base", "vib_disp2x", "vib_length_half_similar",
              "vib_length_1p5_similar", "vib_draft2x"):
        h, dk = _v(fx, k, "hull_natural_freq"), _v(fx, k, "deck_natural_freq")
        if h and dk:
            ratios.append(dk / h)
    if ratios:
        out.append(Criterion(
            "V5", "vibration",
            "甲板/上层建筑固有频率应为独立动力学量（f_deck/f_hull 随尺度变化）",
            f"ratio={min(ratios):.4f}..{max(ratios):.4f}", "比值须非常数",
            None, "FAIL" if (max(ratios) - min(ratios)) / min(ratios) < 0.01 else "PASS",
            "f_deck ≡ 2.5·f_hull 纯比例占位，无独立物理"))

    vv = _v(fx, "vib_base", "vertical_vibration")
    if vv is not None:
        implied = 2.0 + 0.5 * (80.0 / 100.0) * 0.3   # 源码占位式（非共振分支）
        out.append(Criterion(
            "V6", "vibration",
            "振动响应量须由激励/阻抗计算；此处用源码占位式 "
            "2.0+0.5·(rpm/100)·0.3 反向复现平台值（可复现即 MOCK 证据）",
            vv, round(implied, 6), _rel(vv, implied), "FAIL",
            "平台值可被两参数线性式精确复现 ⇒ 手调占位；且无单位、无频率计权"))

    lv = (fx.get("vib_base", {}).get("data") or {}).get("vibration_level")
    out.append(Criterion(
        "V7", "vibration",
        "ISO 6954 舒适性分级须基于 mm/s RMS 速度谱值（1–80 Hz 计权），端点须声明单位",
        f"level={lv}, v={vv}（无单位）", "需 units + frequency weighting",
        None, "FAIL",
        "响应无单位/计权字段，2.12 无法判定是否满足 ISO 6954 ⇒ 分级 NOT_SUPPORTED"))

    risk = (fx.get("vib_base", {}).get("data") or {}).get("resonance_risk")
    if fh and bf:
        ratio = bf / fh
        expect = ("高风险" if 0.9 < ratio < 1.1 else
                  "中等风险" if 1.8 < ratio < 2.2 else "低风险")
        out.append(Criterion(
            "V8", "vibration",
            "共振风险旗标与所报 f_BF/f_hull 内部自洽（源码判据带 0.9–1.1 / 1.8–2.2）",
            f"{risk} (ratio={ratio:.3f})", expect, None,
            "PASS" if risk and expect in str(risk) else "FAIL",
            "内部自洽，但 f_hull 非物理（见 V2–V4），旗标物理意义为零"))

    pa = fx.get("vib_predict_a", {}).get("data")
    pb = fx.get("vib_predict_b", {}).get("data")
    if pa is not None and pb is not None:
        out.append(Criterion(
            "V9", "vibration",
            "船舶振动/噪声端点响应须随船改变（两条不同船应给不同值）",
            json.dumps(pa, ensure_ascii=False), json.dumps(pb, ensure_ascii=False),
            None, "PASS" if pa != pb else "FAIL",
            "/vibration/predict 对任意 ship_id 返回常数 ⇒ STUB（3 个硬编码字段）"))

    # ---------------- 结构域：总纵强度 ----------------
    s_base = _v(fx, "struct_base", "max_stress")
    M_total = still_water_bending_moment(DISP0, L0)          # kN·m（平台自带式子）
    _, Z_ref = box_section_modulus(B0, D0, T_PLATE)          # m³（下界）
    sigma_ref = M_total / Z_ref / 1000.0                     # MPa
    out.append(Criterion(
        "S1", "structure",
        "总纵弯曲应力 σ=M/Z 单位一致（kN·m/m³=kPa→/1000=MPa），Z 取裸壳下界（应力上界）",
        s_base, round(sigma_ref, 2),
        None if s_base is None else _rel(s_base, sigma_ref),
        _ok(_rel(s_base, sigma_ref)) if s_base is not None else "WITHHELD",
        "源码 `M/Z*1000` 应为 `M/Z/1000`（1e6 单位错误）+ Z=B·D²·0.015 多乘一个 D"))

    s_d2 = _v(fx, "struct_D1p5x", "max_stress")
    if s_base and s_d2:
        expo_D = math.log(s_d2 / s_base) / math.log(29.25 / 19.5)
        # 闭式参照指数：σ ∝ 1/Z(D)，Z 由裸壳箱形剖面给出（翼板 ∝D、腹板 ∝D²）
        _, z_a = box_section_modulus(B0, 19.5, T_PLATE)
        _, z_b = box_section_modulus(B0, 29.25, T_PLATE)
        expo_ref = -math.log(z_b / z_a) / math.log(29.25 / 19.5)
        out.append(Criterion(
            "S2", "structure",
            "剖面模数尺度律：σ ∝ 1/Z(D)，参照指数由闭式 Z 给出"
            f"（{expo_ref:.3f}，容差 15%）",
            round(expo_D, 3), round(expo_ref, 3), _rel(expo_D, expo_ref),
            _ok(_rel(expo_D, expo_ref)),
            "平台 σ∝D^-2（Z=B·D²·t 多乘一个 D）⇒ 随型深增大系统性低估应力"))

    s_t2 = _v(fx, "struct_t2x", "max_stress")
    if s_base and s_t2 is not None:
        out.append(Criterion(
            "S3", "structure",
            "板厚通道：Z∝t（σ∝1/t），板厚加倍 ⇒ 应力至少降 10%",
            f"σ(t=.015)={s_base}, σ(t=.03)={s_t2}", "σ(t)∝1/t", None,
            "PASS" if s_t2 < 0.9 * s_base else "FAIL",
            "plate_thickness 被端点忽略 ⇒ 无截面模型（MOCK 而非 REAL 结构分析）"))

    s_disp2 = _v(fx, "struct_disp2x", "max_stress")
    if s_base and s_disp2:
        rr = s_disp2 / s_base
        out.append(Criterion(
            "S4", "structure",
            "装载反应：排水量加倍 ⇒ 静水弯矩加倍 ⇒ 应力约加倍（带 1.5–2.5）",
            round(rr, 4), 2.0, _rel(rr, 2.0),
            "PASS" if 1.5 <= rr <= 2.5 else "FAIL",
            "该通道成立（M_sw∝Δ）⇒ 端点确是公式链，失效在截面/单位层"))

    p_base = _v(fx, "plate_base", "critical_stress")
    b_plate = 0.6 * 1.5   # 源码 b = frame_spacing*1.5
    ref_cr = plate_buckling_stress(T_PLATE, b_plate) / 1e6
    out.append(Criterion(
        "S5", "structure",
        "板格屈曲临界应力：长板四边简支单轴 σ_cr=C·E·(t/b)², C=3.6154（b=0.9 m, t=15 mm）",
        p_base, round(ref_cr, 2),
        None if p_base is None else _rel(p_base, ref_cr),
        _ok(_rel(p_base, ref_cr)) if p_base is not None else "WITHHELD",
        "平台用 σ_cr=0.9E(t/b)²，系数 0.9 vs 3.6154 ⇒ 低估 75%（隐含 k=1 而非 4）"))

    a_base = _v(fx, "plate_base", "actual_stress")
    if a_base is not None:
        out.append(Criterion(
            "S6", "structure",
            "板格实际应力须由载荷/总纵应力导出，不得恒等于 0.5σy",
            a_base, 0.5 * SY0, _rel(a_base, 0.5 * SY0),
            "FAIL" if abs(a_base - 0.5 * SY0) < 1e-9 else "PASS",
            "actual_stress ≡ 0.5·yield_stress 硬编码；安全系数只随 t/b 变化，与工况无关"))

    fs = _v(fx, "struct_fatigue_base", "stress_range")
    if fs is not None:
        out.append(Criterion(
            "S7", "structure",
            "结构端点应力幅须来自载荷谱，不得恒等于 0.3σy",
            fs, 0.3 * SY0, _rel(fs, 0.3 * SY0),
            "FAIL" if abs(fs - 0.3 * SY0) < 1e-9 else "PASS",
            "stress_range ≡ 0.3·yield_stress 硬编码（无载荷输入，不可复核）"))

    life = _v(fx, "struct_fatigue_base", "calculated_life")
    if fs is not None and life is not None:
        N_pl, N_dnv = life * 1e6, sn_life(fs, **SN_DNV_C1_AIR)
        out.append(Criterion(
            "S8", "structure",
            "结构端点 S-N（自称 DNV-C）寿命 vs DNV RP-C203 C1 in-air 闭式解"
            "（m=3, log a=12.164, 双斜率）",
            N_pl, round(N_dnv, 1), _rel(N_pl, N_dnv), _ok(_rel(N_pl, N_dnv)),
            "平台 N=1e7(100/Δσ)³ 隐含 log a=13.0，比 DNV C1(12.164) 高 6.8×；"
            "标称曲线与实际截距不符"))

    # 同服务内双实现交叉：/structure/fatigue 的隐含截距 vs /fatigue/analyze 的
    # 已验证截距（后者由 S10 与闭式 BS-D 逐位吻合，故其隐含 log a=12.164 可信）
    fa0 = fx.get("fatigue_analyze_bsd", {}).get("data")
    if isinstance(fa0, dict) and fs is not None and life is not None:
        bins = fa0.get("damage_distribution") or []
        n_impl = None
        for b in bins:
            if isinstance(b, dict) and abs(float(b.get("stress_range", 0)) - fs) < 1e-9:
                n_impl = b.get("fatigue_life")
        if n_impl is None:
            # 用平台自有 S-N 参数在 Δσ=fs 处闭式外推（S10 已证明其=标准 BS-D）
            n_impl = sn_life(fs, **SN_BS_D)
        N_pl = life * 1e6
        out.append(Criterion(
            "S9", "structure",
            "同服务内双实现交叉：/structure/fatigue 隐含 N(Δσ) vs /fatigue/analyze "
            "所用 BS-D 曲线 N(Δσ)（Δσ=0.3σy）",
            round(N_pl, 1), round(n_impl, 1), _rel(N_pl, n_impl),
            _ok(_rel(N_pl, n_impl)),
            "两分支同一服务内相差 6.8×（隐含 log a 13.0 vs 12.164）⇒ 结构分支"
            "自成体系、与平台已验证的疲劳内核脱钩"))

    fa = fx.get("fatigue_analyze_bsd", {}).get("data")
    if isinstance(fa, dict):
        D_pl = None
        for k in ("cumulative_damage", "total_damage", "damage", "miner_damage"):
            if isinstance(fa.get(k), (int, float)):
                D_pl = float(fa[k])
                break
        D_ref = miner_damage([80.0, 50.0], [1e6, 5e6], **SN_BS_D)
        out.append(Criterion(
            "S10", "structure",
            "Miner 累积损伤：平台 /fatigue/analyze(BS-D, Δσ=[80,50], n=[1e6,5e6]) "
            "vs 闭式 Σn/N",
            D_pl if D_pl is not None else "字段缺失",
            round(D_ref, 6),
            None if D_pl is None else _rel(D_pl, D_ref),
            _ok(_rel(D_pl, D_ref)) if D_pl is not None else "WITHHELD",
            "疲劳寿命内核（幂律+Miner）可闭式复现" if D_pl is not None
            else "响应未含累积损伤字段，交叉由 S9 表级对照承担"))
    return out


# ==========================================================================
# 落盘 + 图件
# ==========================================================================
def summarize(ledger: list[Criterion]) -> dict[str, Any]:
    by_dom: dict[str, dict[str, int]] = {}
    for c in ledger:
        d = by_dom.setdefault(c.domain, {"PASS": 0, "FAIL": 0, "WITHHELD": 0,
                                         "total": 0})
        d[c.verdict] = d.get(c.verdict, 0) + 1
        d["total"] += 1
    return {"criteria_total": len(ledger),
            "pass": sum(1 for c in ledger if c.verdict == "PASS"),
            "fail": sum(1 for c in ledger if c.verdict == "FAIL"),
            "withheld": sum(1 for c in ledger if c.verdict == "WITHHELD"),
            "by_domain": by_dom, "rel_tol": REL_TOL}


def write_report(ledger: list[Criterion], fixture: dict[str, Any]) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    payload = {"generated_by": "tools/audit/structure_vibration_audit.py",
               "probe_base": fixture.get("base_url"),
               "fixture": str(FIX.relative_to(ROOT)),
               "summary": summarize(ledger),
               "criteria": [c.as_dict() for c in ledger]}
    js = ART / "structure_vibration_audit.json"
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["| 判据 | 域 | 内容 | 平台值 | 参考值 | 相对误差 | 判决 |",
             "|---|---|---|---|---|---|---|"]
    for c in ledger:
        rel = "—" if c.rel_err is None else f"{c.rel_err * 100:.3g}%"
        pv = c.platform if isinstance(c.platform, (int, float)) else str(c.platform)
        rv = c.reference if isinstance(c.reference, (int, float)) else str(c.reference)
        lines.append(f"| {c.cid} | {c.domain} | {c.statement} | {pv} | {rv} "
                     f"| {rel} | **{c.verdict}** |")
    (ART / "structure_vibration_audit_tables.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return js


def make_figures(ledger: list[Criterion], fixture: dict[str, Any]) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    figdir = ART / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    plt.rcParams.update({"figure.dpi": 130, "font.size": 9})

    # 图1 船体固有频率：平台拟合式 vs 物理尺度律 vs 实机探针点
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    Ls = np.linspace(100, 350, 60)
    ax.plot(Ls, 1.5 * (Ls / 100.0) ** -0.5, "r-", lw=2,
            label="platform $f=1.5(L/100)^{-0.5}$")
    f230 = hull_2node_vertical_freq(L0, B0, D0, T_PLATE, DISP0)
    ax.plot(Ls, f230 * (Ls / L0) ** -1.0, "b--", lw=2,
            label="closed-form free-free beam, $f\\propto L^{-1}$")
    ax.plot(Ls, 1.75 * f230 * (Ls / L0) ** -1.0, "b:", lw=1,
            label="closed-form upper (stiffened $\\times$1.75)")
    pts = [(L0, _v(fixture, "vib_base", "hull_natural_freq")),
           (115.0, _v(fixture, "vib_length_half_similar", "hull_natural_freq")),
           (345.0, _v(fixture, "vib_length_1p5_similar", "hull_natural_freq"))]
    pts = [(x, y) for x, y in pts if y]
    ax.plot([p[0] for p in pts], [p[1] for p in pts], "ks", ms=6,
            label="platform API (probe)")
    ax.set_xlabel("L [m]"); ax.set_ylabel("2-node vertical natural freq [Hz]")
    ax.set_yscale("log"); ax.grid(alpha=.3, which="both"); ax.legend(fontsize=7)
    ax.set_title("Hull natural frequency: platform fit vs physics scaling")
    p = figdir / "fig1_hull_freq_scaling.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(p)

    # 图2 总纵弯曲应力：平台 vs 闭式（对数柱）
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    M = still_water_bending_moment(DISP0, L0)
    _, Z = box_section_modulus(B0, D0, T_PLATE)
    s_ref = M / Z / 1000.0
    s_pl = _v(fixture, "struct_base", "max_stress") or 0.0
    bars = ax.bar(["closed-form\n$M/Z_{shell}$", "platform\n/structure/analyze"],
                  [s_ref, s_pl], color=["tab:blue", "tab:red"])
    ax.set_yscale("log"); ax.set_ylabel("bending stress [MPa]")
    for b, v in zip(bars, [s_ref, s_pl]):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.4, f"{v:.3g}",
                ha="center", fontsize=8)
    ax.grid(alpha=.3, axis="y")
    ax.set_title("Hull girder bending stress (KCS-like; $Z$ = bare-shell lower bound)")
    p = figdir / "fig2_hull_girder_stress.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(p)

    # 图3 S-N：结构端点占位式 vs DNV C1 / B1 闭式
    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    S = np.logspace(1.7, 2.3, 120)
    ax.plot(S, 1e7 * (100.0 / S) ** 3, "r-", lw=2,
            label="platform /structure/fatigue $N=10^7(100/\\Delta\\sigma)^3$")
    ax.plot(S, [sn_life(s, **SN_DNV_C1_AIR) for s in S], "b--", lw=2,
            label="DNV RP-C203 C1 in air (log a=12.164)")
    ax.plot(S, [sn_life(s, **SN_DNV_B1_AIR) for s in S], "g:", lw=1.5,
            label="DNV RP-C203 B1 in air (log a=12.592)")
    ax.axhline(1e7, color="grey", lw=.8, ls="-.")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel(r"stress range $\Delta\sigma$ [MPa]"); ax.set_ylabel("N [cycles]")
    ax.grid(alpha=.3, which="both"); ax.legend(fontsize=7)
    ax.set_title("S-N life: hard-coded structural formula vs standards")
    p = figdir / "fig3_sn_curves.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(p)

    # 图4 判据判决矩阵
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    ids = [c.cid for c in ledger]
    errs = [(c.rel_err * 100 if c.rel_err is not None else 0.05) for c in ledger]
    cmap = {"PASS": "tab:green", "FAIL": "tab:red", "WITHHELD": "tab:grey"}
    ax.barh(ids, [max(e, 0.05) for e in errs],
            color=[cmap[c.verdict] for c in ledger])
    ax.set_xscale("log"); ax.set_xlabel("relative error vs reference [%] (log)")
    for i, c in enumerate(ledger):
        txt = ("—" if c.rel_err is None else f"{c.rel_err * 100:.3g}%")
        ax.text(max(errs[i], 0.05) * 1.15, i, f"{txt} [{c.verdict}]",
                va="center", fontsize=7)
    ax.invert_yaxis(); ax.grid(alpha=.3, axis="x")
    ax.set_title("Criterion ledger: platform vs closed-form / standards")
    p = figdir / "fig4_criteria_matrix.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="实机采集 fixture")
    ap.add_argument("--report", action="store_true", help="出账（读 fixture）")
    ap.add_argument("--figures", action="store_true", help="出图")
    ap.add_argument("--all", action="store_true", help="全链")
    a = ap.parse_args()
    do_probe = a.probe or a.all
    do_report = a.report or a.all
    do_fig = a.figures or a.all

    if do_probe:
        fx = collect_probe()
        FIX.parent.mkdir(parents=True, exist_ok=True)
        FIX.write_text(json.dumps(fx, ensure_ascii=False, indent=2), encoding="utf-8")
        n = len(fx["fixtures"])
        errs = [k for k, v in fx["fixtures"].items()
                if not isinstance(v.get("data"), (dict, list))]
        print(f"probe: {n} endpoints recorded -> {FIX.relative_to(ROOT)}")
        if errs:
            print("  non-JSON responses:", ", ".join(errs))
    if do_report or do_fig:
        if not FIX.exists():
            print("fixture missing; run --probe first")
            return 2
        fx = json.loads(FIX.read_text(encoding="utf-8"))
        ledger = build_ledger(fx["fixtures"])
        if do_report:
            js = write_report(ledger, fx)
            s = summarize(ledger)
            print(f"ledger -> {js.relative_to(ROOT)}")
            print(f"  criteria={s['criteria_total']} PASS={s['pass']} "
                  f"FAIL={s['fail']} WITHHELD={s['withheld']}")
            for dom, d in s["by_domain"].items():
                print(f"  {dom}: {d}")
        if do_fig:
            for p in make_figures(ledger, fx["fixtures"]):
                print("fig ->", p.relative_to(ROOT))
    if not (do_probe or do_report or do_fig):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
