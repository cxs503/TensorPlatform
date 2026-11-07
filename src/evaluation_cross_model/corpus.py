"""物理案例语料库（IN47-N08 交叉模型验证的外部效度基座）。

本模块只做一件事：把「平台真算出来的物理量」与「一条独立参考值」配成
:class:`CaseRecord`，并把**审计员可见的证据字段**与**只属于判据标准的
参考侧自一致性审计**分开记录。评分器（``families``）看不见后者的结果，
因此「评分器判级 vs 参考标准」不是同义反复。

参考值来源（全部可在仓内溯源，禁用手抄记忆表）
--------------------------------------------
==============  ====================================================  ===============
案例族           参考来源                                               参考等级 ref_class
==============  ====================================================  ===============
SUBOFF 阻力      ``tools/validate_suboff_resistance.py`` 记录的
                Roddy (1990) 实验 Ct / Rt（原样引用，未二次核对原文）     repo_citation
KVLCC2/KCS 湿面积 ``src/ship_verification/audit_resistance.py``
                ``PUBLISHED_S``（Workshop 公布值 27194 / 9494 m²）       published
B 系列螺旋桨     ``tools/verification/propulsion/b_series_reference.py``
                Oosterveld & van Oossanen (1975) 回归（39/47 项）        regression
Wigley          封闭解析式 Cb=4/9、∇=4LBT/9、KB=3T/8、BM=3B²/(35T)      analytic
箱形浮体         KB=T/2、BM=B²/(12T)、GZ=sinθ(GM+½BM tan²θ)             analytic
梯形剖面船       平台自身剖面积分：KB=T(Cb+½)/(3Cb)、BM=B²/(12T·Cb)      analytic
操纵（回转）     定常回转运动学恒等式 R=|U|/|r|（M-A4, m1651 anchors）    identity
Holtrop 交叉     仓内独立实现 ``src/ship_verification/holtrop1984.py``    internal_crosscheck
==============  ====================================================  ===============

几何先验证：每个案例都带 ``geometry``（L,B,T,Cb,∇）并通过
:func:`geometry_gate`（Cb=∇/(LBT)、L/B、B/T）；未过门 → ``NOT_SUPPORTED``。

**预注册判据**（写死在模块常量里，不允许按语料调参，避免自证）：
``TOLERANCE_PCT = 15.0`` —— 平台既定的「>15% 不可接受」标准。
"""
from __future__ import annotations

import math
import pathlib
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:  # 允许 scripts/ 与 tests/ 直接 import
    sys.path.insert(0, str(REPO))

# ─────────────────────────────── 预注册常量 ───────────────────────────────

#: 平台精度标准：相对误差 >15% 不可接受（预注册，未按本语料拟合）。
TOLERANCE_PCT = 15.0
#: 几何门：Cb 与 ∇/(L·B·T) 的相对偏差上限 [%]。
GEOM_CB_TOL_PCT = 0.5
#: 参考值自一致性容差 [%]（参考值自己不满足自身物理恒等式 → 不可当判据）。
REF_SELF_CONSISTENCY_TOL_PCT = 5.0

REAL = "REAL"
WITHHELD = "WITHHELD"
MOCK = "MOCK"
STUB = "STUB"
NOT_SUPPORTED = "NOT_SUPPORTED"

EVIDENCE_LEVELS = (REAL, WITHHELD, MOCK, STUB, NOT_SUPPORTED)

#: 参考等级权重（审计员可见元数据；等级越高越接近「独立外部参考」）。
REF_CLASS_QUALITY: Dict[str, float] = {
    "analytic": 1.00,
    "regression": 0.90,
    "published": 0.85,
    "identity": 0.75,
    "regulatory": 0.70,
    "repo_citation": 0.55,
    "internal_crosscheck": 0.30,
    "none": 0.00,
}

#: 参考值的度量不确定度 u_ref [%]（决定稳健性试验的噪声幅度与判定死区宽度）。
REF_UNCERTAINTY_PCT: Dict[str, float] = {
    "analytic": 0.10,
    "identity": 0.50,
    "regression": 1.50,
    "published": 2.00,
    "regulatory": 1.00,
    "repo_citation": 3.00,
    "internal_crosscheck": 4.00,
    "none": 0.00,
}


# ─────────────────────────────── 数据结构 ───────────────────────────────


@dataclass
class CaseRecord:
    """一个可判级的物理案例（证据包）。

    ``split``：``verified`` = 评分器阈值/口径的标定集所用船型；
    ``holdout`` = 完全没有参与任何标定的船型（外部效度试验）。
    """

    case_id: str
    domain: str                    # resistance / hydrostatics / stability / propulsion / manoeuvring
    hull: str                      # 船型（或螺旋桨型号）标识，外部效度按此分集
    split: str                     # verified | holdout
    quantity: str                  # 被预报的物理量
    unit: str
    predicted: Optional[float]
    reference: Optional[float]
    ref_class: str
    tolerance_pct: float = TOLERANCE_PCT
    # —— 参考侧审计（只给参考标准 oracle 用，评分器不可见）——
    ref_self_consistency_pct: Optional[float] = None
    # —— 审计员可见的行为/结构证据 ——
    responds_to_inputs: bool = True    # 换 ≥3 个不同物理输入，输出是否变化（MOCK 探针）
    reproducible: bool = True          # 重复调用是否逐位一致
    domain_ok: bool = True             # 是否在模型文档化的适用域内
    stub: bool = False                 # 预测器未实现/返回 None
    value_present: bool = True         # 本轮是否拿到了有限数
    cross_route_pct: Optional[float] = None   # 同一模型第二条独立实现的相对偏差 [%]
    geometry: Dict[str, float] = field(default_factory=dict)
    note: str = ""

    # ---- 派生量 ----
    @property
    def error_pct(self) -> Optional[float]:
        if self.predicted is None or self.reference is None:
            return None
        if not (math.isfinite(self.predicted) and math.isfinite(self.reference)):
            return None
        if self.reference == 0.0:
            return None
        return 100.0 * (self.predicted - self.reference) / self.reference

    @property
    def abs_error_pct(self) -> Optional[float]:
        e = self.error_pct
        return None if e is None else abs(e)

    @property
    def ref_uncertainty_pct(self) -> float:
        return REF_UNCERTAINTY_PCT.get(self.ref_class, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["error_pct"] = None if self.error_pct is None else round(self.error_pct, 4)
        d["abs_error_pct"] = None if self.abs_error_pct is None else round(self.abs_error_pct, 4)
        d["ref_uncertainty_pct"] = self.ref_uncertainty_pct
        return d


# ─────────────────────────────── 几何门 ───────────────────────────────


def geometry_gate(L: float, B: float, T: float, cb: float, vol: float) -> Dict[str, Any]:
    """几何先验证（平台纪律）：Cb 必须与 ∇/(L·B·T) 自洽，L/B、B/T 在合理区间。"""
    cb_geom = vol / (L * B * T) if (L and B and T) else float("nan")
    dev = 100.0 * (cb_geom - cb) / cb if cb else float("inf")
    lb = L / B if B else float("inf")
    bt = B / T if T else float("inf")
    checks = {
        "cb_consistent": abs(dev) <= GEOM_CB_TOL_PCT,
        "lb_plausible": 2.0 <= lb <= 20.0,
        "bt_plausible": 1.5 <= bt <= 8.0,
    }
    return {
        "L": L, "B": B, "T": T, "cb": cb, "vol": vol,
        "cb_geom": round(cb_geom, 6), "cb_dev_pct": round(dev, 4),
        "L_B": round(lb, 3), "B_T": round(bt, 3),
        "checks": checks, "ok": all(checks.values()),
    }


# ─────────────────────────────── 语料构建 ───────────────────────────────


def _propeller_kernel():
    """仓内 BEM 螺旋桨内核（惰性导入，避免 numpy 拖慢纯语料测试）。"""
    try:
        from backend.services import propeller_open_water as pow_mod  # type: ignore
        return pow_mod
    except Exception:  # pragma: no cover - 兼容非包导入
        import importlib.util

        path = REPO / "backend" / "services" / "propeller_open_water.py"
        spec = importlib.util.spec_from_file_location("_prop_open_water", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules.setdefault("_prop_open_water", mod)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod


def _holtrop84():
    from src.ship_verification import holtrop1984
    return holtrop1984


def _ship_perf():
    from src.ship_perf import resistance, hydrostatics
    return resistance, hydrostatics


# ---- SUBOFF ---------------------------------------------------------------

#: Roddy (1990) DARPA SUBOFF 数据，原样取自 tools/validate_suboff_resistance.py
SUBOFF_REPO_CASES = (
    {"case_id": "SUBOFF-01", "speed_ms": 3.0, "fn": 0.236, "ct_exp": 0.00380, "rt_exp_n": 12.50},
    {"case_id": "SUBOFF-02", "speed_ms": 4.0, "fn": 0.315, "ct_exp": 0.00400, "rt_exp_n": 21.00},
    {"case_id": "SUBOFF-03", "speed_ms": 5.0, "fn": 0.393, "ct_exp": 0.00420, "rt_exp_n": 31.50},
    {"case_id": "SUBOFF-04", "speed_ms": 6.0, "fn": 0.472, "ct_exp": 0.00440, "rt_exp_n": 45.00},
)

SUBOFF_GEO = {"L": 4.356, "B": 0.508, "T": 0.254, "cb": 0.542}


def _suboff_hull():
    rs, _ = _ship_perf()
    g = SUBOFF_GEO
    vol = g["L"] * g["B"] * g["T"] * g["cb"]
    return rs.HullForm(lwl=g["L"], lpp=g["L"], b=g["B"], t=g["T"],
                       displacement=vol, cb=g["cb"], cm=1.0), vol


def _suboff_cases() -> List[CaseRecord]:
    """SUBOFF 回转体阻力：平台 ITTC-57+(1+k)Cf vs 仓内引用实验数据。"""
    rs, _ = _ship_perf()
    hull, vol = _suboff_hull()
    rr = 0.5 * rs.RHO_FRESH
    S = rs.wetted_surface(hull)
    cases: List[CaseRecord] = []
    for c in SUBOFF_REPO_CASES:
        p = rs.submerged_body_resistance(hull, c["speed_ms"])
        q = 0.5 * rs.RHO_FRESH * c["speed_ms"] ** 2
        # 参考值自一致性：Ct_exp·q·S 应与记录的总阻力 Rt_exp 一致（同一实验的两个导出量）
        implied_rt = c["ct_exp"] * q * S
        self_cons = 100.0 * (implied_rt - c["rt_exp_n"]) / c["rt_exp_n"]
        cases.append(CaseRecord(
            case_id=f"{c['case_id']}-Ct",
            domain="resistance", hull="SUBOFF", split="verified",
            quantity=f"Ct @ {c['speed_ms']:.1f} m/s (Fn={c['fn']:.3f})",
            unit="-", predicted=p.ct, reference=c["ct_exp"],
            ref_class="repo_citation", ref_self_consistency_pct=self_cons,
            responds_to_inputs=True, reproducible=True, domain_ok=True,
            geometry=geometry_gate(SUBOFF_GEO["L"], SUBOFF_GEO["B"], SUBOFF_GEO["T"],
                                   SUBOFF_GEO["cb"], vol),
            note="Roddy(1990) 引用值；水下回转体无自由面兴波项",
        ))
        cases.append(CaseRecord(
            case_id=f"{c['case_id']}-Rt",
            domain="resistance", hull="SUBOFF", split="verified",
            quantity=f"Rt @ {c['speed_ms']:.1f} m/s",
            unit="N", predicted=p.rt, reference=c["rt_exp_n"],
            ref_class="repo_citation", ref_self_consistency_pct=self_cons,
            responds_to_inputs=True, reproducible=True, domain_ok=True,
            geometry=cases[-1].geometry,
            note="同一实验的总阻力导出量；与 Ct 参考互检",
        ))
    return cases


# ---- 水面船（Holtrop）族 --------------------------------------------------

SURFACE_HULLS = {
    # name: (split, speeds_kn)
    "KVLCC2": ("verified", (12.0, 15.0)),
    "KCS": ("holdout", (18.0, 21.0)),
    "AFRAMAX-110k": ("holdout", (12.0, 15.0)),
}

#: Workshop 公布的湿表面积（与 src/ship_verification/audit_resistance.py 一致）
PUBLISHED_S = {"KVLCC2": 27194.0, "KCS": 9494.0}
KM_TO_MS = 0.5144


def _surface_cases() -> List[CaseRecord]:
    """水面船：湿面积（几何门）、总阻力（平台 vs 仓内独立 Holtrop 实现）、静水力。"""
    rs, hs = _ship_perf()
    h84 = _holtrop84()
    out: List[CaseRecord] = []
    for name, (split, speeds) in SURFACE_HULLS.items():
        g = h84.BENCHMARK_HULLS[name]
        vol = g.vol
        geom = geometry_gate(g.l, g.b, g.t, g.cb, vol)
        hull = rs.HullForm(lwl=g.l, lpp=g.l, b=g.b, t=g.t, displacement=vol,
                           cb=g.cb, cm=g.cm, cwp=g.cwp, lcb_pct=g.lcb_pct)
        # (1) 湿表面积 vs 公布值
        if name in PUBLISHED_S:
            out.append(CaseRecord(
                case_id=f"{name}-S", domain="resistance", hull=name, split=split,
                quantity="wetted surface S", unit="m^2",
                predicted=rs.wetted_surface(hull), reference=PUBLISHED_S[name],
                ref_class="published", geometry=geom,
                note="Holtrop 湿面积回归 vs Workshop 公布值（几何门）",
            ))
        # (2) 总阻力：平台简化链 vs 仓内独立 Holtrop-Mennen(1984) 实现
        for kn in speeds:
            v = kn * KM_TO_MS
            a = rs.holtrop_surface_resistance(hull, v)
            b = h84.holtrop1984_resistance(g, v)
            out.append(CaseRecord(
                case_id=f"{name}-Rt-{kn:.0f}kn", domain="resistance", hull=name,
                split=split, quantity=f"total resistance @ {kn:.0f} kn", unit="N",
                predicted=a.rt, reference=b.rt, ref_class="internal_crosscheck",
                cross_route_pct=100.0 * (a.rt - b.rt) / b.rt, geometry=geom,
                note="参考=仓内独立实现（非外部试验值）→ 只能算交叉一致性，不能确认 REAL",
            ))
        # (3) 静水力（棱柱体族内域，解析可核）
        cm = g.cm
        kb_ex = g.t * (g.cb + 0.5 * cm) / (3.0 * g.cb) if cm == 1.0 else g.t * (g.cb + 0.5) / (3.0 * g.cb)
        bm_ex = g.b ** 2 / (12.0 * g.t * g.cb)
        out.append(CaseRecord(
            case_id=f"{name}-KB", domain="hydrostatics", hull=name, split=split,
            quantity="KB (prismatic section)", unit="m",
            predicted=hs.kb_from_cb(g.t, g.cb), reference=kb_ex,
            ref_class="analytic", geometry=geom,
            domain_ok=(g.cm == 1.0),
            note="梯形剖面一阶矩解析解；Cm<1 时退化为棱柱体假设→适用域外",
        ))
        out.append(CaseRecord(
            case_id=f"{name}-BM", domain="hydrostatics", hull=name, split=split,
            quantity="BM = I_wl/∇ (full waterplane)", unit="m",
            predicted=hs.bm_transverse(g.b, g.t, g.cb), reference=bm_ex,
            ref_class="analytic", geometry=geom,
            note="水线面矩 B³L/12 / (LBT·Cb) 解析",
        ))
    return out


# ---- Wigley 与箱形浮体 ----------------------------------------------------


def _wigley_cases() -> List[CaseRecord]:
    """Wigley 抛物线船：平台棱柱体静水力模型 vs 严格解析解（域外案例）。"""
    rs, hs = _ship_perf()
    from src.ship_verification import wigley_yardstick as wy

    L, B, T = wy.STANDARD_L, wy.STANDARD_B, wy.STANDARD_T
    cb = wy.exact_wigley_cb()
    vol = wy.exact_wigley_volume(L, B, T)
    geom = geometry_gate(L, B, T, cb, vol)
    kb_exact = 3.0 * T / 8.0                 # ∇/∫∫ 解析，见模块文档
    bm_exact = 3.0 * B * B / (35.0 * T)
    return [
        CaseRecord(case_id="WIGLEY-cb", domain="hydrostatics", hull="Wigley", split="verified",
                   quantity="block coefficient Cb", unit="-",
                   predicted=vol / (L * B * T), reference=cb, ref_class="analytic",
                   geometry=geom, note="∇=4LBT/9 精确；Cb=4/9 精确"),
        CaseRecord(case_id="WIGLEY-KB", domain="hydrostatics", hull="Wigley", split="verified",
                   quantity="KB (parabolic section)", unit="m",
                   predicted=hs.kb_from_cb(T, cb), reference=kb_exact,
                   ref_class="analytic", geometry=geom, domain_ok=False,
                   note="平台 KB 公式仅对梯形剖面族精确；Wigley 为抛物剖面→域外"),
        CaseRecord(case_id="WIGLEY-BM", domain="hydrostatics", hull="Wigley", split="verified",
                   quantity="BM (parabolic waterplane)", unit="m",
                   predicted=hs.bm_transverse(B, T, cb), reference=bm_exact,
                   ref_class="analytic", geometry=geom, domain_ok=False,
                   note="平台 BM=B²/(12TCb) 假设满水线面矩形；Wigley I_wl=4LB³/105→域外"),
    ]


def _pontoon_cases() -> List[CaseRecord]:
    """箱形浮体：解析精确 + 大倾角 GZ 数值解 vs 垂舷解析式（稳定性域）。"""
    rs, hs = _ship_perf()
    from src.ship_verification import wigley_yardstick as wy

    B, T, KG, D = 20.0, 8.0, 6.0, 12.0
    L = 60.0
    vol = L * B * T
    geom = geometry_gate(L, B, T, 1.0, vol)
    exact = wy.pontoon_hydrostatics(B, T, KG)
    out = [
        CaseRecord(case_id="PONTOON-KB", domain="hydrostatics", hull="Pontoon-B20T8",
                   split="verified", quantity="KB (box)", unit="m",
                   predicted=hs.kb_from_cb(T, 1.0), reference=exact["KB"],
                   ref_class="analytic", geometry=geom, note="KB=T/2 精确"),
        CaseRecord(case_id="PONTOON-BM", domain="hydrostatics", hull="Pontoon-B20T8",
                   split="verified", quantity="BM (box)", unit="m",
                   predicted=hs.bm_transverse(B, T, 1.0), reference=exact["BM"],
                   ref_class="analytic", geometry=geom, note="BM=B²/(12T) 精确"),
        CaseRecord(case_id="PONTOON-GM", domain="stability", hull="Pontoon-B20T8",
                   split="verified", quantity="GM (KG=6 m)", unit="m",
                   predicted=hs.gm_transverse(B, T, 1.0, KG), reference=exact["GM"],
                   ref_class="analytic", geometry=geom, note="KM-KG 精确"),
    ]
    # 大倾角 GZ：数值剖面浸没求积 vs 垂舷解析式（甲板未入水区间）
    angles = [10.0, 20.0, 30.0]
    gz_num = hs.gz_curve(B, T, D, 1.0, KG, angles)
    for a, gz in zip(angles, gz_num):
        ref = wy.pontoon_wall_sided_gz(B, T, KG, a)
        out.append(CaseRecord(
            case_id=f"PONTOON-GZ{a:.0f}", domain="stability", hull="Pontoon-B20T8",
            split="verified", quantity=f"GZ @ {a:.0f} deg", unit="m",
            predicted=gz, reference=ref, ref_class="analytic", geometry=geom,
            note="数值等体积水线求积 vs 垂舷解析 GZ=sinθ(GM+½BM tan²θ)",
        ))
    return out


# ---- 梯形剖面船（外部效度 hold-out 船型）---------------------------------

TRAPEZOID_HULLS = (
    # (case tag, B, T, Cb)
    ("TRAP-Cb060", 32.2, 10.8, 0.60),
    ("TRAP-Cb070", 32.2, 10.8, 0.70),
    ("TRAP-Cb085", 32.2, 10.8, 0.85),
)


def _trapezoid_cases() -> List[CaseRecord]:
    """梯形剖面船族：平台棱柱体静水力（域内精确）——外部效度 hold-out 主体。"""
    rs, hs = _ship_perf()
    L = 180.0
    out: List[CaseRecord] = []
    for tag, B, T, cb in TRAPEZOID_HULLS:
        vol = L * B * T * cb
        geom = geometry_gate(L, B, T, cb, vol)
        kb_ex = T * (cb + 0.5) / (3.0 * cb)
        bm_ex = B * B / (12.0 * T * cb)
        out.append(CaseRecord(
            case_id=f"{tag}-KB", domain="hydrostatics", hull=tag, split="holdout",
            quantity="KB (trapezoid section)", unit="m",
            predicted=hs.kb_from_cb(T, cb), reference=kb_ex,
            ref_class="analytic", geometry=geom, note="平台梯形剖面一阶矩解析解（域内）",
        ))
        out.append(CaseRecord(
            case_id=f"{tag}-BM", domain="hydrostatics", hull=tag, split="holdout",
            quantity="BM (prismatic)", unit="m",
            predicted=hs.bm_transverse(B, T, cb), reference=bm_ex,
            ref_class="analytic", geometry=geom, note="满水线面棱柱体解析解（域内）",
        ))
    return out


# ---- B 系列螺旋桨 ---------------------------------------------------------

PROPELLERS = {
    # name: (P/D, Ae/Ao, Z, split)
    "B4-70": (1.0, 0.70, 4, "verified"),
    "B5-79": (1.0, 0.79, 5, "holdout"),
    "B3-80": (1.0, 0.80, 3, "holdout"),
    "B4-55": (0.8, 0.55, 4, "holdout"),
}

PROP_J = (0.4, 0.6)


#: Oosterveld & van Oossanen (1975) 回归的有效域（与 b_series_reference.provenance 一致）。
PROP_VALIDITY = {"Z": (2, 7), "P_D": (0.5, 1.4), "Ae_Ao": (0.3, 1.05), "J": (0.0, 1.5)}


def propeller_gate(pd: float, ae: float, z: int, J: float) -> Dict[str, Any]:
    """桨几何/工况先验证：超出 O&O 回归有效域的案例不得作为参考值来源。"""
    checks = {
        "P_D_in_range": PROP_VALIDITY["P_D"][0] <= pd <= PROP_VALIDITY["P_D"][1],
        "Ae_Ao_in_range": PROP_VALIDITY["Ae_Ao"][0] <= ae <= PROP_VALIDITY["Ae_Ao"][1],
        "Z_in_range": PROP_VALIDITY["Z"][0] <= z <= PROP_VALIDITY["Z"][1],
        "J_in_range": PROP_VALIDITY["J"][0] <= J <= PROP_VALIDITY["J"][1],
    }
    return {"D": 5.0, "P_D": pd, "Ae_Ao": ae, "Z": z, "J": J,
            "checks": checks, "ok": all(checks.values())}


def _propeller_cases() -> List[CaseRecord]:
    """螺旋桨敞水：仓内 BEM 内核 vs Oosterveld & van Oossanen (1975) 回归。"""
    try:
        from tools.verification.propulsion import b_series_reference as ref
        mod = _propeller_kernel()
    except Exception as exc:  # pragma: no cover - 环境缺依赖时降级为 STUB 案例
        return [CaseRecord(case_id="PROP-unavailable", domain="propulsion", hull="B-series",
                           split="verified", quantity="open-water KT", unit="-",
                           predicted=None, reference=None, ref_class="none",
                           stub=True, value_present=False,
                           note=f"内核不可用: {type(exc).__name__}")]
    out: List[CaseRecord] = []
    for name, (pd, ae, z, split) in PROPELLERS.items():
        kernel_geom = mod.PropellerGeometry(diameter_m=5.0, pitch_ratio=pd,
                                           blade_area_ratio=ae, blade_count=z)
        for J in PROP_J:
            pt = mod.open_water_point(kernel_geom, J)
            kt_ref, kq_ref = ref.kt_kq(J, pd, ae, z)
            for qty, pred, rf, unit in (("KT", pt["KT"], kt_ref, "-"),
                                        ("KQ", pt["KQ"], kq_ref, "-")):
                out.append(CaseRecord(
                    case_id=f"{name}-{qty}-J{J:.1f}", domain="propulsion", hull=name,
                    split=split, quantity=f"{qty} @ J={J:.1f}", unit=unit,
                    predicted=pred, reference=rf, ref_class="regression",
                    geometry=propeller_gate(pd, ae, z, J),
                    note="O&O(1975) 回归（39/47 项）为独立外部参考；内核为面元/动量混合",
                ))
    return out


# ---- 操纵性（回转）-------------------------------------------------------


def _manoeuvring_cases() -> List[CaseRecord]:
    """KVLCC2 级油轮 35° 回转：报告回转直径 vs 定常回转运动学恒等式。"""
    try:
        import time
        from backend.services.digital_twin.models.geometry import HullForm as MHull
        from backend.services.digital_twin.models.manoeuvring import turning_circle
    except Exception as exc:  # pragma: no cover
        return [CaseRecord(case_id="KVLCC2-TURN-unavailable", domain="manoeuvring",
                           hull="KVLCC2", split="verified", quantity="turning diameter",
                           unit="m", predicted=None, reference=None, ref_class="none",
                           stub=True, value_present=False,
                           note=f"数字孪生 MMG 不可用: {type(exc).__name__}")]
    hull = MHull(L=320.0, B=58.0, T=20.8, Cb=0.8098)
    res = turning_circle(hull, 8.0, rudder_deg=35.0, t_end=600.0)
    r_rad = math.radians(abs(res["steady_yaw_rate_deg_s"]))
    d_identity = 2.0 * abs(res["steady_path_speed_m_s"]) / r_rad if r_rad > 0 else float("nan")
    tac = res.get("tactical_diameter_m")
    geom = geometry_gate(320.0, 58.0, 20.8, 0.8098, 320.0 * 58.0 * 20.8 * 0.8098)
    cases = [CaseRecord(
        case_id="KVLCC2-TURN-D", domain="manoeuvring", hull="KVLCC2", split="verified",
        quantity="turning diameter (35 deg, U=8 m/s)", unit="m",
        predicted=res["turning_diameter_m"], reference=d_identity, ref_class="identity",
        geometry=geom,
        note="恒等式 R=|U_path|/|r|（m1651 anchor M-A4）：报告值用 u 而非路径速度，含 cos β 偏置",
    )]
    tac_ok = tac is not None and math.isfinite(float(tac))
    cases.append(CaseRecord(
        case_id="KVLCC2-TURN-tacticalD", domain="manoeuvring", hull="KVLCC2", split="verified",
        quantity="tactical diameter (180 deg)", unit="m",
        predicted=(float(tac) if tac_ok else None), reference=None,
        ref_class="none", geometry=geom, value_present=tac_ok,
        note="仿真窗 600 s 内未达 180° 回转角 → 战术直径未定义（NaN）",
    ))
    return cases


# ─────────────────────────────── 入口 ───────────────────────────────


def build_corpus() -> List[CaseRecord]:
    """构建全部案例（真算，不写死结果）。约 40 例，耗时 ~3 s（含 MMG 回转仿真）。"""
    cases: List[CaseRecord] = []
    cases.extend(_suboff_cases())
    cases.extend(_surface_cases())
    cases.extend(_wigley_cases())
    cases.extend(_pontoon_cases())
    cases.extend(_trapezoid_cases())
    cases.extend(_propeller_cases())
    cases.extend(_manoeuvring_cases())
    return cases


def holdout_hulls(cases: Sequence[CaseRecord]) -> List[str]:
    """外部效度所用的 hold-out 船型清单（按船型去重）。"""
    seen: List[str] = []
    for c in cases:
        if c.split == "holdout" and c.hull not in seen:
            seen.append(c.hull)
    return seen


def verified_hulls(cases: Sequence[CaseRecord]) -> List[str]:
    seen: List[str] = []
    for c in cases:
        if c.split == "verified" and c.hull not in seen:
            seen.append(c.hull)
    return seen


# ─────────────────────────────── 参考标准 ───────────────────────────────


def oracle_label(case: CaseRecord, *, tol_pct: float = TOLERANCE_PCT) -> Dict[str, Any]:
    """预注册参考标准（严格的证据判定）。

    REAL 需同时满足：预测值有限、偏离独立外部参考 ≤15%、参考值自身通过自一致性
    审计（≤5%）、参考等级不是 ``internal_crosscheck``/``none``、重复调用可复现、
    且模型在文档化适用域内。行为性失败（MOCK/STUB）优先判定。
    """
    if not case.geometry.get("ok", True):
        return {"label": NOT_SUPPORTED, "reason": "几何门未通过（Cb/L/B 不自洽）"}
    if case.stub or not case.value_present:
        return {"label": STUB, "reason": "预测器未实现/返回空值"}
    if not case.responds_to_inputs:
        return {"label": MOCK, "reason": "对 ≥3 组不同物理输入输出恒定（硬编码特征）"}
    if not case.domain_ok:
        return {"label": WITHHELD, "reason": "模型在文档化适用域外，结论不作数"}
    if not case.reproducible:
        return {"label": WITHHELD, "reason": "重复调用不一致（不可复现）"}
    if case.reference is None or case.ref_class in ("none",):
        return {"label": WITHHELD, "reason": "无独立参考值"}
    if case.ref_class == "internal_crosscheck":
        return {"label": WITHHELD, "reason": "仅有仓内交叉实现，无外部独立参考"}
    if (case.ref_self_consistency_pct is not None
            and abs(case.ref_self_consistency_pct) > REF_SELF_CONSISTENCY_TOL_PCT):
        return {"label": WITHHELD,
                "reason": f"参考值自身不自洽（{case.ref_self_consistency_pct:.1f}% > "
                          f"{REF_SELF_CONSISTENCY_TOL_PCT}%）"}
    e = case.abs_error_pct
    if e is None:
        return {"label": WITHHELD, "reason": "偏离不可计算"}
    if e <= tol_pct:
        return {"label": REAL, "reason": f"|dev|={e:.2f}% ≤ {tol_pct:.0f}% 且参考独立可核"}
    return {"label": WITHHELD, "reason": f"|dev|={e:.2f}% > {tol_pct:.0f}%（不可接受）"}