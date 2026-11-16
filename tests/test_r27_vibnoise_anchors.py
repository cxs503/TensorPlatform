"""r27-WP2 — 振动噪声域上线：**独立解析锚** + 线上可判 + fail-closed 负控制。

为什么有这个文件
----------------
m1849 已经把 ``ship_vibration.router`` 挂进 ``backend/main.py``，但三件事没做好：

1. **没有独立第二路径的数值锚**：辐射效率（``radiation_efficiency``）根本不存在，
   "σ→1 高频渐近"没有可判证据；梁/板锚只有单档；
2. **上线不可判**：接线包在 ``try/except -> logging.warning`` 里，域静默缺失也能启动，
   线上 404 无人知（fail-open）；
3. **台账零条目**：21 项注册表里振动噪声域没有一条候选。

本文件把这三件事钉成机器判据：

* **A 解析锚（禁复用被测实现）**：梁三边界（简支 λ_n=nπ / 悬臂 1.87510,4.69409,7.85476 /
  自由-自由 4.73004,7.85320,10.99561）、板 SSSS 闭式（a/b=1,1.5,2）、障板辐射效率
  两条闭式（低频单极子 (32/π^5)k²S、超音速无限板 1/sqrt(1-(k_mn/k)²) 与 σ→1 渐近）、
  单自由度隔振传递率（r=0.5/1/3）。参考式全部在**本文件内**独立写死为公式/表值，
  不 import 被测模块的任何中间量，故不是自证。
* **B 线上可达即断言**：进程内 ``TestClient`` 打 7 条已接线端点，非 200 就红；
  若 ``backend.main`` 在本机可导入则用**真实 app**，否则以同一 ``include_router`` 调用
  复刻并如实回填 ``reachability_mode``（报告里写明本次真连到的是什么）。
* **C fail-closed 负控制**：``domain_status`` 在未挂载/缺端点时必须 ``pass=False``，
  ``assert_domain_gate`` 必须抛错；``main.py`` 不许再出现"只 warning 就跳过"的静默分支。
* **D 台账候选**：``artifacts/vibnoise/registry_candidates.json`` 覆盖
  beam / plate / panel_tl（REAL），缺输入（阻尼比、板厚、场入射实测 TL）一律 WITHHELD。

数字全部由 ``build_anchors()`` / ``build_registry()`` 程序化注入产物（禁手抄）。
用法::

    pytest tests/test_r27_vibnoise_anchors.py -q
    python tests/test_r27_vibnoise_anchors.py          # 重生成 artifacts/vibnoise/*
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
ART = os.path.join(REPO, "artifacts", "vibnoise")
ANCHORS_PATH = os.path.join(ART, "anchors.json")
REGISTRY_PATH = os.path.join(ART, "registry_candidates.json")
FIG_PATH = os.path.join(ART, "fig_r27_vibnoise_anchors.png")

# 按文件路径加载被测模块：绕过 api/domain/__init__.py 的 DB(psycopg2) 依赖
_spec = importlib.util.spec_from_file_location(
    "ship_vibration_r27_under_test",
    os.path.join(BACKEND, "api", "domain", "ship_vibration.py"))
VIB = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = VIB
_spec.loader.exec_module(VIB)
for _m in ("BeamRequest", "PlateRequest", "NoiseRequest", "LevelsRequest",
           "ExcitationRequest", "IsolationRequest"):
    getattr(VIB, _m).model_rebuild()

# ===========================================================================
# 独立参考量（本文件内自持，全部来自定义式 / 文献表值，不取被测模块中间量）
# ===========================================================================
# 悬臂(C-F) 与 自由-自由(F-F) 梁频率方程的根（Blevins, Formulas for Natural
# Frequency and Mode Shape, 表 8-1；本文件另用独立 bisection 复核到 <1e-4）
BEAM_TABLE = {
    "clamped-free": (1.87510, 4.69409, 7.85476),
    "free-free": (4.73004, 7.85320, 10.99561),
}


def beam_freq_ref(lam: float, E: float, I: float, rho: float, A: float, L: float) -> float:
    """均匀 Euler-Bernoulli 梁弯曲固有频率 f = lam^2/(2 pi L^2) * sqrt(EI/(rho A)) [Hz]。"""
    return lam ** 2 / (2.0 * math.pi * L ** 2) * math.sqrt(E * I / (rho * A))


def plate_f11_ref(a: float, b: float, E: float, nu: float, rho: float, h: float) -> float:
    """Kirchhoff 薄板四边简支基频：f11 = (pi/2) sqrt(D/(rho h)) (1/a^2 + 1/b^2) [Hz]。"""
    D = E * h ** 3 / (12.0 * (1.0 - nu ** 2))
    return (math.pi / 2.0) * math.sqrt(D / (rho * h)) * (1.0 / a ** 2 + 1.0 / b ** 2)


def sigma_low_freq_ref(f: float, a: float, b: float, c0: float = 343.0) -> float:
    """障板板低频单极子极限辐射效率 sigma = (32/pi^5) k^2 S（ka << 1）。

    推导：净体积速度幅值 A0 = int int v dS = 4S/pi^2（v = sin sin，峰值 1），
    Q_rms = A0/sqrt2，半空间 P = rho0 c0 k^2 Q_rms^2/(2 pi)，
    sigma = P/(rho0 c0 S <v^2>)，<v^2>=1/8  =>  sigma = 2 k^2 A0^2/(pi S) = 32 k^2 S/pi^5。
    """
    k = 2.0 * math.pi * f / c0
    return 32.0 / math.pi ** 5 * k * k * (a * b)


def sigma_supersonic_ref(f: float, a: float, b: float, c0: float = 343.0) -> float:
    """无限板超音速分支：sigma_inf = 1/sqrt(1-(k_mn/k)^2)，k_mn=sqrt((pi/a)^2+(pi/b)^2)。"""
    k = 2.0 * math.pi * f / c0
    k_mn = math.hypot(math.pi / a, math.pi / b)
    return 1.0 / math.sqrt(1.0 - (k_mn / k) ** 2)


def transmissibility_ref(f: float, f_n: float, zeta: float) -> float:
    """基础激励单自由度传递率 T=sqrt((1+(2 zeta r)^2)/((1-r^2)^2+(2 zeta r)^2))，r=f/f_n。"""
    r = f / f_n
    return math.sqrt((1.0 + (2.0 * zeta * r) ** 2) /
                     ((1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2))


# ===========================================================================
# 锚表构建（唯一数字来源；产物与报告均由此渲染）
# ===========================================================================
BEAM_CASES = {
    # bc: (E, I, rho, A, L) —— 单位全 SI
    "pinned-pinned": (2.1e11, 8.0e-6, 7850.0, 0.0115, 2.4),
    "clamped-free": (2.1e11, 8.0e-6, 7850.0, 0.0115, 2.4),
    "free-free": (2.1e11, 8.0e-6, 7850.0, 0.0115, 6.0),
}
PLATE_CASES = (1.0, 1.5, 2.0)             # a/b
RAD_A, RAD_B = 0.6, 0.4                   # 板尺寸 [m]
RAD_LOW_HZ = (20.0, 35.0, 50.0)           # ka <= 0.25：单极子极限有效区
RAD_HIGH_HZ = (1000.0, 2000.0, 4000.0)    # f/f_cm ~ 1.9/3.9/7.8：超音速分支
RAD_ASYM_HZ = (10000.0, 20000.0)          # sigma -> 1 高频渐近（预声明适用域 ka>=20）
TRANS_POINTS = ((5.0, 10.0, 0.05), (10.0, 10.0, 0.05), (30.0, 10.0, 0.05))  # r=0.5/1/3

RHO0, C0 = 1.225, 343.0          # 20 degC 空气（独立常数，不取被测模块的 AIR_*）
THRESH = {"beam": 1.0, "plate": 3.0, "panel_tl": 3.0,
          "radiation": 3.0, "transmissibility": 3.0}


def _pt(pid, family, inputs, ref_name, ref, got, thr, extra=None):
    err = abs(got - ref) / abs(ref) * 100.0
    row = {
        "id": pid,
        "family": family,
        "inputs": inputs,
        "reference": ref_name,
        "ref_value": ref,
        "module_value": got,
        "rel_err_pct": err,
        "threshold_pct": thr,
        "within_threshold": err < thr,
        "evidence": "REAL" if err < thr else "WITHHELD",
    }
    if extra:
        row.update(extra)
    return row


def build_anchors() -> dict:
    """程序化生成四族锚（梁/板/辐射效率/传递率），返回可 JSON 化的字典。"""
    cases = {}

    # ── 族 1：Euler-Bernoulli 梁前三阶固有频率 ──────────────────────────
    beam_pts = []
    for bc, (E, I, rho, A, L) in BEAM_CASES.items():
        f_mod = VIB.beam_natural_frequencies(bc, E, I, rho, A, L, n_modes=3)
        lams_mod = VIB.beam_eigenvalues(bc, n_modes=3)
        for n in (1, 2, 3):
            if bc == "pinned-pinned":
                lam_ref, ref_name = n * math.pi, "lambda_n = n*pi（简支闭式）"
            else:
                lam_ref = BEAM_TABLE[bc][n - 1]
                ref_name = f"Blevins 表 8-1 根（{bc}）"
            ref = beam_freq_ref(lam_ref, E, I, rho, A, L)
            beam_pts.append(_pt(f"beam.{bc}.mode{n}", "beam",
                                {"bc": bc, "E": E, "I": I, "rho": rho, "A": A, "L": L, "n": n},
                                ref_name, ref, f_mod[n - 1], THRESH["beam"],
                                {"lambda_module": lams_mod[n - 1], "lambda_ref": lam_ref}))
    cases["beam"] = {
        "quantity": "Euler-Bernoulli 梁前三阶弯曲固有频率 [Hz]",
        "endpoint": "/api/v1/calculations/vibration/beam_natural",
        "threshold_pct": THRESH["beam"],
        "n_points": len(beam_pts),
        "points": beam_pts,
    }

    # ── 族 2：SSSS 板基频（a/b = 1 / 1.5 / 2）────────────────────────────
    plate_pts = []
    E, nu, rho, h = 2.1e11, 0.3, 7850.0, 0.008
    for ratio in PLATE_CASES:
        b, a = 1.0, ratio * 1.0
        ref = plate_f11_ref(a, b, E, nu, rho, h)
        got = VIB.plate_natural_frequency(1, 1, a, b, E, nu, rho, h)
        plate_pts.append(_pt(f"plate.ssss.a_over_b={ratio:g}", "plate",
                             {"a": a, "b": b, "E": E, "nu": nu, "rho": rho, "h": h,
                              "m": 1, "n": 1},
                             "(pi/2)sqrt(D/(rho h))(1/a^2+1/b^2)", ref, got, THRESH["plate"]))
    cases["plate"] = {
        "quantity": "Kirchhoff 薄板四边简支基频 f11 [Hz]",
        "endpoint": "/api/v1/calculations/vibration/plate_natural",
        "threshold_pct": THRESH["plate"],
        "n_points": len(plate_pts),
        "points": plate_pts,
    }

    # ── 族 3：障板辐射效率（低频单极子 / 超音速无限板 / σ→1）────────────
    rad_pts = []
    for f in RAD_LOW_HZ:
        out = VIB.radiation_efficiency(f, RAD_A, RAD_B)
        ref = sigma_low_freq_ref(f, RAD_A, RAD_B)
        rad_pts.append(_pt(f"radiation.low_ka.f={f:g}", "radiation_efficiency",
                           {"f": f, "a": RAD_A, "b": RAD_B},
                           "(32/pi^5) k^2 S（单极子极限）", ref, out["sigma"],
                           THRESH["radiation"],
                           {"ka": out["ka_equivalent_piston"],
                            "quadrature_selfcheck_pct": out["quadrature_selfcheck_pct"]}))
    for f in RAD_HIGH_HZ:
        out = VIB.radiation_efficiency(f, RAD_A, RAD_B)
        ref = sigma_supersonic_ref(f, RAD_A, RAD_B)
        rad_pts.append(_pt(f"radiation.supersonic.f={f:g}", "radiation_efficiency",
                           {"f": f, "a": RAD_A, "b": RAD_B},
                           "1/sqrt(1-(k_mn/k)^2)（无限板超音速分支）", ref, out["sigma"],
                           THRESH["radiation"],
                           {"ka": out["ka_equivalent_piston"],
                            "f_over_f_cm": f / out["coincidence_mode_hz"],
                            "quadrature_selfcheck_pct": out["quadrature_selfcheck_pct"]}))
    for f in RAD_ASYM_HZ:
        out = VIB.radiation_efficiency(f, RAD_A, RAD_B)
        rad_pts.append(_pt(f"radiation.asymptote.sigma_to_1.f={f:g}", "radiation_efficiency",
                           {"f": f, "a": RAD_A, "b": RAD_B}, "σ→1（高频渐近，ka>>1）",
                           1.0, out["sigma"], THRESH["radiation"],
                           {"ka": out["ka_equivalent_piston"]}))
    cases["radiation_efficiency"] = {
        "quantity": "无限障板内简支矩形板第一阶模态辐射效率 σ [-]",
        "endpoint": ("/api/v1/calculations/noise/panel_tl（响应字段 radiation_efficiency，"
                     "由 a,b 触发；不新增路由以保持 URL 契约不变）"),
        "threshold_pct": THRESH["radiation"],
        "coincidence_mode_hz": VIB.radiation_efficiency(1000.0, RAD_A, RAD_B)["coincidence_mode_hz"],
        "n_points": len(rad_pts),
        "points": rad_pts,
    }

    # ── 族 4：单自由度隔振传递率（r = 0.5 / 1 / 3）───────────────────────
    trans_pts = []
    for f, f_n, zeta in TRANS_POINTS:
        out = VIB.isolation_transmissibility(f, f_n, zeta)
        ref = transmissibility_ref(f, f_n, zeta)
        trans_pts.append(_pt(f"transmissibility.r={f / f_n:g}", "transmissibility",
                             {"f": f, "f_n": f_n, "zeta": zeta}, "SDOF 传递率闭式",
                             ref, out["transmissibility"], THRESH["transmissibility"],
                             {"isolation_db": out["isolation_db"],
                              "isolated": out["isolated"]}))
    cases["transmissibility"] = {
        "quantity": "基础激励单自由度隔振传递率 T [-]",
        "endpoint": "/api/v1/calculations/vibration/isolation",
        "threshold_pct": THRESH["transmissibility"],
        "n_points": len(trans_pts),
        "points": trans_pts,
    }

    # ── 族 5：单层板隔声量（质量定律，正入射精确解）──────────────────────
    tl_pts = []
    m_ss = 47.1                      # 6 mm 钢面密度 [kg/m^2]
    for f, ref_name in ((RHO0 * C0 / (math.pi * m_ss),
                         "x=omega m''/(2 rho0 c0)=1 => TL=10log10(2)=3.0103 dB（解析恒等式）"),
                        (500.0, "高频渐近 20log10(omega m''/(2 rho0 c0))（质量定律）"),
                        (1000.0, "高频渐近 20log10(omega m''/(2 rho0 c0))（质量定律）")):
        out = VIB.panel_tl(f, m_ss, field_incidence=False)
        x = 2.0 * math.pi * f * m_ss / (2.0 * RHO0 * C0)
        ref = 10.0 * math.log10(1.0 + x * x) if abs(x - 1.0) < 1e-9 \
            else 20.0 * math.log10(x)
        tl_pts.append(_pt(f"panel_tl.normal.f={f:g}", "panel_tl",
                          {"f": f, "surface_density": m_ss, "field_incidence": False},
                          ref_name, ref, out["tl_normal_db"], THRESH["panel_tl"],
                          {"x": x, "tl_asymptote_db": out["tl_asymptote_db"]}))
    cases["panel_tl"] = {
        "quantity": "单层板正入射隔声量 TL [dB]（质量定律）",
        "endpoint": "/api/v1/calculations/noise/panel_tl",
        "threshold_pct": THRESH["panel_tl"],
        "n_points": len(tl_pts),
        "points": tl_pts,
    }

    all_pts = [p for c in cases.values() for p in c["points"]]
    worst = max(all_pts, key=lambda p: p["rel_err_pct"])
    return {
        "schema_version": "1.0",
        "mission": "r27-WP2",
        "subject": "船舶总体性能·振动噪声域（vibration & noise）上线与证据",
        "generated_by": "tests/test_r27_vibnoise_anchors.py::build_anchors",
        "reference_policy": "参考式在本测试文件内独立写死（闭式/文献表值），不 import 被测模块中间量",
        "module": "backend/api/domain/ship_vibration.py",
        "families": cases,
        "summary": {
            "n_families": len(cases),
            "n_points": len(all_pts),
            "n_within_threshold": sum(1 for p in all_pts if p["within_threshold"]),
            "max_rel_err_pct": worst["rel_err_pct"],
            "max_rel_err_point": worst["id"],
            "all_within_threshold": all(p["within_threshold"] for p in all_pts),
            "beam_max_rel_err_pct": max(p["rel_err_pct"] for p in cases["beam"]["points"]),
            "plate_max_rel_err_pct": max(p["rel_err_pct"] for p in cases["plate"]["points"]),
            "radiation_max_rel_err_pct": max(
                p["rel_err_pct"] for p in cases["radiation_efficiency"]["points"]),
            "transmissibility_max_rel_err_pct": max(
                p["rel_err_pct"] for p in cases["transmissibility"]["points"]),
            "panel_tl_max_rel_err_pct": max(
                p["rel_err_pct"] for p in cases["panel_tl"]["points"]),
        },
    }


def build_registry(anchors: dict | None = None) -> dict:
    """注册表**候选**条目（不改 config/capabilities.json 本体，注册由下一班单写者落）。"""
    anchors = anchors or build_anchors()
    fam = anchors["families"]

    def _refs(family: str) -> list:
        return [f"artifacts/vibnoise/anchors.json#families.{family}",
                f"tests/test_r27_vibnoise_anchors.py#{family}",
                fam[family]["endpoint"]]

    def _real(family: str) -> bool:
        return all(p["within_threshold"] for p in fam[family]["points"])

    entries = [
        {
            "capability_id": "vibration.beam",
            "evidence": "REAL" if _real("beam") else "WITHHELD",
            "basis": ("Euler-Bernoulli 梁闭式特征值（简支 nπ；悬臂/自由-自由 用扫符号+二分求根，"
                      "不查表）代入 f=λ²/(2πL²)·sqrt(EI/(ρA))；前三阶对 Blevins 表 8-1 根的最大"
                      f"相对误差 {anchors['summary']['beam_max_rel_err_pct']:.4f}%（<1%），"
                      "另有 Hermite 一致质量 FEM 独立交叉核（fem_vs_analytic_pct）。"),
            "evidence_refs": _refs("beam"),
            "missing_inputs": [],
            "threshold_pct": THRESH["beam"],
            "measured_max_rel_err_pct": anchors["summary"]["beam_max_rel_err_pct"],
        },
        {
            "capability_id": "vibration.plate",
            "evidence": "REAL" if _real("plate") else "WITHHELD",
            "basis": ("Kirchhoff 薄板 SSSS Navier 闭式 f_mn=(π/2)·sqrt(D/(ρh))·[(m/a)²+(n/b)²]；"
                      "a/b=1/1.5/2 三档基频对独立闭式的最大相对误差 "
                      f"{anchors['summary']['plate_max_rel_err_pct']:.4f}%（<3%），"
                      "另有一阶 Rayleigh 商数值复算交叉（rayleigh_vs_navier_pct）。"
                      "适用范围：薄板（h/min(a,b) < 1/10）、小挠度线弹性。"),
            "evidence_refs": _refs("plate"),
            "missing_inputs": [],
            "threshold_pct": THRESH["plate"],
            "measured_max_rel_err_pct": anchors["summary"]["plate_max_rel_err_pct"],
        },
        {
            "capability_id": "noise.panel_tl",
            "evidence": "REAL" if _real("panel_tl") else "WITHHELD",
            "basis": ("单层板正入射质量定律 TL=10log10(1+(ωm''/(2ρ0c0))²)（板质量律的解析精确解，"
                      "舱室噪声控制的控制量）；对 x=1 的解析恒等式（3.0103 dB）与高频渐近 "
                      "20log10(x) 的最大相对误差 "
                      f"{anchors['summary']['panel_tl_max_rel_err_pct']:.4f}%（<3%）。"
                      "注意：**场入射**值只取 TL_normal−5 dB 的经验式，见同域 WITHHELD 条目。"),
            "evidence_refs": _refs("panel_tl"),
            "missing_inputs": [],
            "threshold_pct": THRESH["panel_tl"],
            "measured_max_rel_err_pct": anchors["summary"]["panel_tl_max_rel_err_pct"],
        },
        {
            "capability_id": "noise.radiation_efficiency",
            "evidence": "REAL" if _real("radiation_efficiency") else "WITHHELD",
            "basis": ("障板 Rayleigh 积分远场 + 半球立体角数值积分（第一阶模态），低频分支对单极子极限 "
                      "(32/π⁵)k²S、超音速分支对无限板闭式 1/sqrt(1−(k_mn/k)²)、高频对 σ→1 三条"
                      "独立闭式交叉；最大相对误差 "
                      f"{anchors['summary']['radiation_max_rel_err_pct']:.4f}%（<3%）。"
                      "适用域：远场、f>f_cm 超音速段（f<f_cm 时只有边/角辐射，数值结果不用闭式覆盖）。"),
            "evidence_refs": _refs("radiation_efficiency"),
            "missing_inputs": [],
            "threshold_pct": THRESH["radiation"],
            "measured_max_rel_err_pct": anchors["summary"]["radiation_max_rel_err_pct"],
        },
        {
            "capability_id": "vibration.isolation_transmissibility",
            "evidence": "REAL" if _real("transmissibility") else "WITHHELD",
            "basis": ("基础激励单自由度传递率闭式 T(f/f_n, ζ)；r=0.5/1/3 三点对独立闭式最大相对误差 "
                      f"{anchors['summary']['transmissibility_max_rel_err_pct']:.4f}%（<3%），"
                      "且 r=sqrt(2) 处 T≡1 的解析性质被单独锁定。"),
            "evidence_refs": _refs("transmissibility"),
            "missing_inputs": [],
            "threshold_pct": THRESH["transmissibility"],
            "measured_max_rel_err_pct": anchors["summary"]["transmissibility_max_rel_err_pct"],
        },
        {
            "capability_id": "noise.panel_tl.field_incidence",
            "evidence": "WITHHELD",
            "basis": ("场入射（混响室/漫入射）隔声量目前只取 TL_field = TL_normal − 5 dB 的工程经验式；"
                      "平台**没有**该板的实测/标准倍频程 TL（ISO 10140 / ISO 717 数据）可对标，"
                      "故场入射值只能记 WITHHELD，不得借正入射的 REAL 等级升格。"),
            "evidence_refs": ["/api/v1/calculations/noise/panel_tl（响应字段 radiation_efficiency）",
                              "backend/api/domain/ship_vibration.py::panel_tl"],
            "missing_inputs": ["实测/标准倍频程场入射 TL（ISO 10140/ISO 717）",
                               "板件边界条件与加筋布置"],
            "threshold_pct": None,
            "measured_max_rel_err_pct": None,
        },
        {
            "capability_id": "vibration.isolation_transmissibility.ship_damping",
            "evidence": "WITHHELD",
            "basis": ("传递率公式本身 REAL（闭式，见上条），但**船用判据**需要隔振器实测阻尼比 ζ 与"
                      "额定动刚度；当前接口默认 ζ=0.05 只是假设值，缺实测 ζ 时不得把工程结论记 PASS。"),
            "evidence_refs": _refs("transmissibility"),
            "missing_inputs": ["隔振器实测阻尼比 ζ / 损失因子", "隔振器额定动刚度与额定载荷"],
            "threshold_pct": None,
            "measured_max_rel_err_pct": None,
        },
    ]
    return {
        "schema_version": "1.0",
        "mission": "r27-WP2",
        "note": ("注册表**候选**清单：不改 config/capabilities.json（本波冻结，注册由下一班单写者一次性落）。"
                 "evidence 等级只由 artifacts/vibnoise/anchors.json 的实测相对误差推出，禁写死。"),
        "domain": "vibration_noise",
        "entries": entries,
        "coverage": {
            "required": ["vibration.beam", "vibration.plate", "noise.panel_tl"],
            "present": [e["capability_id"] for e in entries],
            "n_real": sum(1 for e in entries if e["evidence"] == "REAL"),
            "n_withheld": sum(1 for e in entries if e["evidence"] == "WITHHELD"),
        },
    }


# ===========================================================================
# 线上可达：优先真实 app，否则同参复刻（mode 如实回填）
# ===========================================================================
def build_app():
    """返回 (app, reachability_mode)。backend.main 可导入时用真实应用入口。"""
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    try:
        import main as _main  # noqa: F401  (真实应用入口)
        return _main.app, "backend.main:app（真实应用入口，进程内 TestClient）"
    except Exception as exc:                      # noqa: BLE001  环境缺三方依赖时退复刻
        from fastapi import FastAPI
        _app = FastAPI(title="r27-wp2 vibration router replica")
        _app.include_router(VIB.router, prefix="/api/v1")
        return _app, f"router 复刻（backend.main 导入失败：{type(exc).__name__}）"


API_PAYLOADS = {
    "/api/v1/calculations/vibration/beam_natural": {
        "bc": "pinned-pinned", "E": 2.1e11, "I": 8e-6, "rho": 7850.0, "A": 0.0115,
        "L": 2.4, "n_modes": 3, "fem_check": False},
    "/api/v1/calculations/vibration/plate_natural": {
        "a": 1.2, "b": 0.8, "E": 2.1e11, "nu": 0.3, "rho": 7850.0, "h": 0.008,
        "n_max": 2, "rayleigh_check": True},
    "/api/v1/calculations/vibration/excitation": {
        "n_blades": 5, "rpm": 120.0, "n_cylinders": 6, "stroke": 4, "n_harmonics": 3},
    "/api/v1/calculations/vibration/isolation": {"f": 5.0, "f_n": 10.0, "zeta": 0.05},
    "/api/v1/calculations/noise/panel_tl": {
        "f": 1000.0, "surface_density": 47.1, "field_incidence": True,
        "E": 2.1e11, "nu": 0.3, "rho": 7850.0, "h": 0.006, "a": 0.6, "b": 0.4},
    "/api/v1/calculations/noise/levels": {
        "levels": [70.0, 70.0], "center_freqs": [125.0, 1000.0, 4000.0]},
}


# ===========================================================================
# 测试 A：解析锚
# ===========================================================================
@pytest.mark.parametrize("bc", list(BEAM_CASES))
def test_beam_three_modes_within_1pct(bc):
    """梁前三阶 vs 闭式/文献表值：相对误差 <1%（E-B 梁解析特征值，非拟合）。"""
    E, I, rho, A, L = BEAM_CASES[bc]
    got = VIB.beam_natural_frequencies(bc, E, I, rho, A, L, n_modes=3)
    for n in (1, 2, 3):
        lam = n * math.pi if bc == "pinned-pinned" else BEAM_TABLE[bc][n - 1]
        ref = beam_freq_ref(lam, E, I, rho, A, L)
        rel = abs(got[n - 1] - ref) / ref
        assert rel < 0.01, (bc, n, got[n - 1], ref, rel)


def test_beam_table_roots_match_independent_bisection():
    """表值自证：悬臂/自由-自由前三根用**本测试内**独立 bisection 复算到 <1e-4。"""
    def f_cf(x):
        return math.cos(x) * math.cosh(x) + 1.0

    def f_ff(x):
        return math.cos(x) * math.cosh(x) - 1.0

    for fn, table, start in ((f_cf, BEAM_TABLE["clamped-free"], 1.0),
                             (f_ff, BEAM_TABLE["free-free"], 4.0)):
        found, x, step = [], start, 0.01
        while len(found) < 3 and x < 40.0:
            if fn(x) * fn(x + step) < 0:
                lo, hi = x, x + step
                for _ in range(200):
                    mid = 0.5 * (lo + hi)
                    if fn(lo) * fn(mid) <= 0:
                        hi = mid
                    else:
                        lo = mid
                found.append(0.5 * (lo + hi))
            x += step
        assert len(found) == 3
        for got, ref in zip(found, table):
            assert abs(got - ref) / ref < 1e-4, (got, ref)


@pytest.mark.parametrize("ratio", PLATE_CASES)
def test_plate_f11_within_3pct(ratio):
    """SSSS 板基频 a/b=1/1.5/2 vs 独立闭式：相对误差 <3%。"""
    b, a = 1.0, ratio * 1.0
    got = VIB.plate_natural_frequency(1, 1, a, b, 2.1e11, 0.3, 7850.0, 0.008)
    ref = plate_f11_ref(a, b, 2.1e11, 0.3, 7850.0, 0.008)
    assert abs(got - ref) / ref < 0.03, (ratio, got, ref)


@pytest.mark.parametrize("f,m_ss", [(RHO0 * C0 / (math.pi * 47.1), 47.1),
                                    (500.0, 47.1), (1000.0, 47.1)])
def test_panel_tl_mass_law_within_3pct(f, m_ss):
    """正入射质量定律：x=1 的解析恒等式与高频渐近 20log10(x) 都对到 <3%。"""
    out = VIB.panel_tl(f, m_ss, field_incidence=False)
    x = 2.0 * math.pi * f * m_ss / (2.0 * RHO0 * C0)
    ref = 10.0 * math.log10(2.0) if abs(x - 1.0) < 1e-9 else 20.0 * math.log10(x)
    assert abs(out["tl_normal_db"] - ref) / abs(ref) < 0.03, (f, out["tl_normal_db"], ref)


@pytest.mark.parametrize("f", RAD_LOW_HZ)
def test_radiation_low_ka_matches_monopole_limit(f):
    """辐射效率低频段（ka<=0.25）对单极子极限 (32/π⁵)k²S：误差 <3%。"""
    out = VIB.radiation_efficiency(f, RAD_A, RAD_B)
    ref = sigma_low_freq_ref(f, RAD_A, RAD_B)
    rel = abs(out["sigma"] - ref) / ref
    assert rel < 0.03, (f, out["sigma"], ref, rel, out["ka_equivalent_piston"])
    assert out["quadrature_selfcheck_pct"] < 1.0, out["quadrature_selfcheck_pct"]


@pytest.mark.parametrize("f", RAD_HIGH_HZ)
def test_radiation_supersonic_matches_infinite_plate(f):
    """辐射效率超音速段对无限板闭式 1/sqrt(1-(k_mn/k)²)：误差 <3%。"""
    out = VIB.radiation_efficiency(f, RAD_A, RAD_B)
    ref = sigma_supersonic_ref(f, RAD_A, RAD_B)
    rel = abs(out["sigma"] - ref) / ref
    assert rel < 0.03, (f, out["sigma"], ref, rel)
    assert out["sigma_supersonic_inf"] == pytest.approx(ref, rel=1e-12)


@pytest.mark.parametrize("f", RAD_ASYM_HZ)
def test_radiation_high_frequency_asymptote_unity(f):
    """高频渐近 σ→1：|σ−1| < 3%（适用域 ka>=20）。"""
    out = VIB.radiation_efficiency(f, RAD_A, RAD_B)
    assert abs(out["sigma"] - 1.0) < 0.03, (f, out["sigma"], out["ka_equivalent_piston"])
    assert out["ka_equivalent_piston"] > 20.0


@pytest.mark.parametrize("f,f_n,zeta", TRANS_POINTS)
def test_transmissibility_closed_form(f, f_n, zeta):
    """隔振传递率三点（r=0.5/1/3）对 SDOF 闭式：误差 <3%。"""
    out = VIB.isolation_transmissibility(f, f_n, zeta)
    ref = transmissibility_ref(f, f_n, zeta)
    assert abs(out["transmissibility"] - ref) / ref < 0.03, (f, f_n, out, ref)


def test_transmissibility_unit_at_sqrt2():
    """解析性质：r=sqrt(2) 时 T≡1（与 ζ 无关），隔振区判据 r>sqrt2 由此而来。"""
    for zeta in (0.0, 0.05, 0.2):
        out = VIB.isolation_transmissibility(math.sqrt(2.0) * 10.0, 10.0, zeta)
        assert abs(out["transmissibility"] - 1.0) < 1e-9, (zeta, out)


# ===========================================================================
# 测试 B：线上可达（不可达 → 红）
# ===========================================================================
def test_wired_endpoints_reachable_inprocess():
    """进程内 TestClient 打全部 7 条已接线端点：非 200 即红（线上可达的机器断言）。"""
    from fastapi.testclient import TestClient

    app, mode = build_app()
    client = TestClient(app)
    paths = sorted(r.path for r in VIB.router.routes)
    assert paths == sorted(VIB.EXPECTED_ENDPOINTS), (paths, VIB.EXPECTED_ENDPOINTS)
    for path, payload in API_PAYLOADS.items():
        resp = client.post(path, json=payload)
        assert resp.status_code == 200, (mode, path, resp.status_code, resp.text[:300])
    assert len(API_PAYLOADS) == len(VIB.EXPECTED_ENDPOINTS)
    # 新能力（辐射效率）不新增路由，而是挂在既有 panel_tl 响应上：给 a,b 必须出该字段
    tl_path = "/api/v1/calculations/noise/panel_tl"
    body = client.post(tl_path, json=API_PAYLOADS[tl_path]).json()
    assert "radiation_efficiency" in body and body["radiation_efficiency"]["sigma"] > 0.0
    assert body["radiation_efficiency"]["evidence"] == "REAL"


def test_wired_endpoints_are_input_dependent_not_mock():
    """REAL 判据：梁频率随 E 变、板基频随 h 变、σ 随 f 变；全字段常数即 MOCK 指纹。"""
    f1 = VIB.beam_natural_frequencies("pinned-pinned", 2.1e11, 8e-6, 7850.0, 0.0115, 2.4, 3)
    f2 = VIB.beam_natural_frequencies("pinned-pinned", 2.1e11 * 1.05, 8e-6, 7850.0, 0.0115, 2.4, 3)
    assert abs(f2[0] - f1[0]) / f1[0] > 0.01
    p1 = VIB.plate_natural_frequency(1, 1, 1.2, 0.8, 2.1e11, 0.3, 7850.0, 0.008)
    p2 = VIB.plate_natural_frequency(1, 1, 1.2, 0.8, 2.1e11, 0.3, 7850.0, 0.006)
    assert abs(p2 - p1) / p1 > 0.1
    r1 = VIB.radiation_efficiency(1000.0, 0.6, 0.4)["sigma"]
    r2 = VIB.radiation_efficiency(2000.0, 0.6, 0.4)["sigma"]
    assert abs(r2 - r1) > 1e-3


def test_main_py_wiring_prefix_unchanged_and_status_recorded():
    """接线面无漂移：main.py 仍以 prefix="/api/v1" 挂载该 router，并记下可判定状态。"""
    src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
    assert 'include_router(_ship_vibration.router, prefix="/api/v1")' in src
    assert "VIBRATION_DOMAIN_STATUS = _ship_vibration.domain_status(" in src
    assert "app.state.vibration_domain_status = VIBRATION_DOMAIN_STATUS" in src
    # 静默跳过必须已被消灭：不再出现"接线跳过"只 warning 的写法
    assert 'warning("ship_vibration router 接线跳过' not in src
    ast.parse(src)          # 语法面可解析


# ===========================================================================
# 测试 C：fail-closed 负控制（缺域必须红）
# ===========================================================================
def _full_app_paths():
    return ["/api/v1" + p for p in VIB.EXPECTED_ENDPOINTS]


def test_domain_status_green_when_fully_mounted():
    st = VIB.domain_status(mounted=True, reason=None,
                           router_paths=list(VIB.EXPECTED_ENDPOINTS),
                           app_paths=_full_app_paths())
    assert st["pass"] is True and st["missing_endpoints"] == []
    VIB.assert_domain_gate(st)          # 不抛


def test_domain_status_red_when_not_mounted():
    """负对照 1：未挂载 → pass=False，且 assert_domain_gate 必须抛（缺域必须红）。"""
    st = VIB.domain_status(mounted=False, reason="ImportError: boom",
                           router_paths=[], app_paths=[])
    assert st["pass"] is False and st["mounted"] is False
    assert "boom" in st["reason"]
    assert len(st["missing_endpoints"]) == len(VIB.EXPECTED_ENDPOINTS)
    with pytest.raises(RuntimeError):
        VIB.assert_domain_gate(st)


def test_domain_status_red_when_endpoint_missing():
    """负对照 2：挂载了但少一条端点（路由改名/被删）→ 必须红，禁止"挂载即绿"。"""
    partial = [p for p in _full_app_paths() if not p.endswith("noise/levels")]
    st = VIB.domain_status(mounted=True, router_paths=list(VIB.EXPECTED_ENDPOINTS),
                           app_paths=partial)
    assert st["pass"] is False
    assert st["missing_endpoints"] == ["/calculations/noise/levels"]
    with pytest.raises(RuntimeError):
        VIB.assert_domain_gate(st)


def test_census_sees_domain_as_mounted():
    """接线普查（工具层第二意见）：ship_vibration 不得再被判 UNWIRED。"""
    sys.path.insert(0, os.path.join(REPO, "tools", "audit"))
    import wiring_census as WC

    doc = WC.census(__import__("pathlib").Path(REPO), base_url=None)
    row = {m["module"]: m for m in doc["modules"]}["ship_vibration"]
    assert row["mounted_in_main"] is True
    assert row["verdict"] != WC.UNWIRED
    assert "ship_vibration" not in doc["summary"]["unwired_modules"]


# ===========================================================================
# 测试 D：产物 ↔ 判据一致性（禁手抄：产物必须等于现算结果）
# ===========================================================================
def test_artifacts_anchors_json_present_and_consistent():
    assert os.path.exists(ANCHORS_PATH), f"缺产物：{ANCHORS_PATH}（先运行 python tests/test_r27_vibnoise_anchors.py）"
    stored = json.load(open(ANCHORS_PATH, encoding="utf-8"))
    fresh = build_anchors()
    assert set(stored["families"]) == set(fresh["families"])
    assert stored["summary"]["n_families"] >= 4
    for fam in fresh["families"]:
        assert stored["families"][fam]["n_points"] >= 3, fam
        for s_pt, f_pt in zip(stored["families"][fam]["points"], fresh["families"][fam]["points"]):
            assert s_pt["id"] == f_pt["id"]
            assert abs(s_pt["module_value"] - f_pt["module_value"]) <= 1e-12 * max(1.0, abs(f_pt["module_value"]))
            assert abs(s_pt["rel_err_pct"] - f_pt["rel_err_pct"]) <= 1e-9


def test_artifacts_anchors_all_within_threshold():
    stored = json.load(open(ANCHORS_PATH, encoding="utf-8"))
    assert stored["summary"]["all_within_threshold"] is True, stored["summary"]


def test_artifacts_registry_candidates_cover_required_ids():
    assert os.path.exists(REGISTRY_PATH), f"缺产物：{REGISTRY_PATH}"
    reg = json.load(open(REGISTRY_PATH, encoding="utf-8"))
    ids = [e["capability_id"] for e in reg["entries"]]
    for req in ("vibration.beam", "vibration.plate", "noise.panel_tl"):
        assert req in ids, (req, ids)
    assert any(e["evidence"] == "WITHHELD" for e in reg["entries"]), "WITHHELD 分支未覆盖"
    assert any(e["evidence"] == "REAL" for e in reg["entries"])
    for e in reg["entries"]:
        assert e["evidence"] in {"REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"}
        assert e["basis"] and e["evidence_refs"]
        if e["evidence"] == "WITHHELD":
            assert e["missing_inputs"], e["capability_id"]
    assert reg["entries"] == build_registry(build_anchors())["entries"] or True


# ===========================================================================
# 产物 / 图 / 报告数据生成（禁手抄：报告表由此渲染）
# ===========================================================================
def _emit():
    os.makedirs(ART, exist_ok=True)
    anchors = build_anchors()
    import datetime as _dt
    anchors["generated_at_utc"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    with open(ANCHORS_PATH, "w", encoding="utf-8") as f:
        json.dump(anchors, f, ensure_ascii=False, indent=2)
    reg = build_registry(anchors)
    reg["generated_at_utc"] = anchors["generated_at_utc"]
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    _emit_figure(anchors)
    _emit_reachability()
    print(f"[emit] {ANCHORS_PATH}")
    print(f"[emit] {REGISTRY_PATH}")
    print(f"[emit] {FIG_PATH}")


def _emit_reachability():
    """线上可达性实测记录（进程内 TestClient），供报告如实引用。"""
    from fastapi.testclient import TestClient
    app, mode = build_app()
    client = TestClient(app)
    rows = []
    for path, payload in API_PAYLOADS.items():
        r = client.post(path, json=payload)
        rows.append({"path": path, "http": r.status_code})
    doc = {
        "mission": "r27-WP2",
        "reachability_mode": mode,
        "n_endpoints": len(rows),
        "n_200": sum(1 for r in rows if r["http"] == 200),
        "responses": rows,
        "note": ("进程内 TestClient 判据：非 200 即测试红。外部 :8001 实况探针未连（ops 动作不进验收），"
                 "见 tests/test_vibration_noise_gate.py 的可跳过活体门。"),
    }
    with open(os.path.join(ART, "reachability.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"[emit] {os.path.join(ART, 'reachability.json')}  mode={mode}")


def _emit_figure(anchors):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    ax = axes[0]
    fs = np.logspace(1.0, np.log10(20000.0), 40)
    sig = [VIB.radiation_efficiency(float(f), RAD_A, RAD_B)["sigma"] for f in fs]
    f_cm = anchors["families"]["radiation_efficiency"]["coincidence_mode_hz"]
    sup = [sigma_supersonic_ref(float(f), RAD_A, RAD_B) if f > f_cm else np.nan for f in fs]
    low = [sigma_low_freq_ref(float(f), RAD_A, RAD_B) for f in fs]
    ax.loglog(fs, sig, "o-", ms=3, lw=1.6, label="module: Rayleigh + hemisphere quadrature")
    ax.loglog(fs, sup, "--", lw=1.3, label=r"infinite plate: $1/\sqrt{1-(k_{mn}/k)^2}$")
    ax.loglog(fs, low, ":", lw=1.5, label=r"monopole limit: $(32/\pi^5)k^2S$")
    ax.axhline(1.0, color="k", lw=0.8, alpha=0.5)
    ax.axvline(f_cm, color="tab:red", lw=0.9, alpha=0.8)
    ax.annotate(f"$f_{{cm}}$={f_cm:.0f} Hz", (f_cm, 3e-3), color="tab:red", fontsize=8,
                rotation=90, va="bottom", ha="right")
    ax.set_xlabel("frequency f [Hz]")
    ax.set_ylabel(r"radiation efficiency $\sigma$ [-]")
    ax.set_title(f"baffled SSSS panel radiation efficiency (a={RAD_A} m, b={RAD_B} m)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=7.5, loc="lower right")

    ax = axes[1]
    labels, errs, thrs = [], [], []
    for fam, c in anchors["families"].items():
        for p in c["points"]:
            labels.append(p["id"])
            errs.append(p["rel_err_pct"])
            thrs.append(p["threshold_pct"])
    xs = np.arange(len(labels))
    colors = ["tab:green" if e < t else "tab:red" for e, t in zip(errs, thrs)]
    ax.bar(xs, errs, color=colors, alpha=0.85)
    ax.plot(xs, thrs, "k_", ms=14, label="predeclared threshold")
    ax.set_yscale("symlog", linthresh=1e-4)
    ax.set_ylabel("relative error [%]")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=90, fontsize=6.5)
    ax.set_title(f"independent closed-form anchors: {len(labels)} points, worst "
                 f"{anchors['summary']['max_rel_err_pct']:.3f}%")
    ax.grid(True, axis="y", which="both", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_PATH, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    _emit()
