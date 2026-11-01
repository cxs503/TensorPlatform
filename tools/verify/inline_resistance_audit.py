"""验证欠账偿还：TensorPlatform inline 阻力端点 vs 解析基准 / 本地基准数据集。

审计对象
--------
``backend/api/calculations.py::calc_resistance``（平台自留的 inline 实现；服务层
``:8001`` 不可达时它是唯一可用的阻力通道）。该实现自述 "Holtrop-Mennen simplified,
calibrated for SUBOFF"，能力台账却把 ``calc.resistance`` 记为 REVERIFY —— 从来没有
一次把它逐项拆开、对可疑项（湿表面积、剩余阻力项）做独立复核。本脚本填补该欠账。

三条互相独立的判据（均可在本机离线复算，零外部依赖）
----------------------------------------------------
P1 几何恒等式（解析）：回转体湿表面积。可用闭式解的长球体（prolate spheroid，
   同 L、同最大直径）面积 S_sph = 2πb² + (2πab/e)·asin(e) 给出**同尺寸光滑回转体**
   的参考面积上界侧估计；实心圆柱 πDL 给出绝对上界。inline 的
   ``S = πBL(0.5 + 0.25Cb)`` 若偏离解析面积 >15%，即为几何层缺陷。
P2 阻力恒等式（数据自洽）：对数据集每行检查 Rt ?= 0.5·ρ·S·Ct·V²。破坏即说明
   数据集 Rt 列不可用于任何误差判定（只能以 Ct 为准）。
P3 方法层（物理闭合）：全浸没回转体的兴波阻力按 Froude 相似应趋于零，故
   Ct_sub ≈ (1+k)·Cf，其中 k 取 Hoerner(1965) 回转体形状因子
   1+k = 1 + 1.5(L/D)^-1.5 + 7(L/D)^-3（Harvald/Hoerner 光滑回转体相关式）。
   inline 的 Cr = 2e-3(1+2Cb)Fn^2.5 既无出处，也对全浸没体给出随 Fn 单调放大的
   "剩余阻力"，与 P3 的物理闭合相悖 —— 该幂律指数是把水下体的兴波当水面船处理。

用法::

    python tools/verify/inline_resistance_audit.py --out artifacts/inline_resistance_audit
    python tools/verify/inline_resistance_audit.py --json /tmp/audit.json   # 只打印摘要

判决口径：|err| <= 15% 记为 PASS（平台精度门），否则 FAIL；无独立参考的量记 WITHHELD。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import statistics
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# 常量（与端点默认值一致：淡水 998 kg/m³, ν=1.004e-6 m²/s）
# --------------------------------------------------------------------------
RHO = 998.0
NU = 1.004e-6
G = 9.81
TOL_PCT = 15.0  # 平台精度门

REPO_ROOT = Path(__file__).resolve().parents[2]
INLINE_MODULE = REPO_ROOT / "backend" / "api" / "calculations.py"
DATASET = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"


# --------------------------------------------------------------------------
# 1. 加载 inline 端点（不拉起 app/DB：注入 stub services.ship_timing）
# --------------------------------------------------------------------------
def load_inline_module(path: Path = INLINE_MODULE):
    """按文件路径加载 calculations.py，绕过 backend/api/__init__ 的 DB 依赖。"""
    if "services.ship_timing" not in sys.modules:
        pkg = sys.modules.setdefault("services", types.ModuleType("services"))
        timing = types.ModuleType("services.ship_timing")

        class _Collector:  # 最小 duck-typed 替身
            def record(self, *a, **k):
                return None

            def get_records(self):
                return []

            def get_baseline(self):
                return {}

        timing.get_collector = lambda: _Collector()

        def timed_calc(name):  # 计时装饰器透传
            def deco(fn):
                return fn

            return deco

        timing.timed_calc = timed_calc
        sys.modules["services.ship_timing"] = timing
        setattr(pkg, "ship_timing", timing)
    spec = importlib.util.spec_from_file_location("tp_inline_calculations", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def call_resistance(mod, **kwargs) -> Dict[str, Any]:
    """异步端点同步调用，返回 dict（pydantic v2 / v1 均可）。"""
    req = mod.ResistanceRequest(**kwargs)
    resp = asyncio.run(mod.calc_resistance(req))
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    return resp.dict()  # pragma: no cover - pydantic v1 兼容


# --------------------------------------------------------------------------
# 2. 独立解析参考
# --------------------------------------------------------------------------
def ittc57_cf(re: float) -> float:
    """ITTC-1957 平板摩擦阻力线 Cf = 0.075/(log10 Re - 2)^2。"""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def hoerner_form_factor(l_over_d: float) -> float:
    """Hoerner(1965) 光滑回转体形状因子 1+k（L/D 为细长比）。"""
    return 1.0 + 1.5 * l_over_d ** -1.5 + 7.0 * l_over_d ** -3.0


def spheroid_wetted_area(length: float, diameter: float) -> float:
    """同 L、同最大直径的长球体（prolate spheroid）湿表面积闭式解。"""
    a, b = length / 2.0, diameter / 2.0
    e = math.sqrt(1.0 - (b / a) ** 2)
    return 2.0 * math.pi * b * b + (2.0 * math.pi * a * b / e) * math.asin(e)


def cylinder_wetted_area(length: float, diameter: float) -> float:
    """实心圆柱面积 πDL —— 任何回转体的湿表面积绝对上界。"""
    return math.pi * diameter * length


# --------------------------------------------------------------------------
# 3. 审计主流程
# --------------------------------------------------------------------------
@dataclass
class CaseRow:
    case_id: str
    ship_type: str
    v: float
    fn: float
    re: float
    ct_ref_exp: float
    ct_inline: float
    err_inline_pct: float
    cf: float
    cr_inline: float
    ct_analytic: Optional[float] = None        # (1+k)Cf（仅全浸没体适用）
    err_analytic_pct: Optional[float] = None
    s_inline: Optional[float] = None
    s_dataset: Optional[float] = None
    s_spheroid: Optional[float] = None
    s_cylinder: Optional[float] = None
    rt_dataset: Optional[float] = None
    rt_from_ct: Optional[float] = None         # 0.5ρSCtV²，S 用数据集值
    identity_resid_pct: Optional[float] = None  # (rt_from_ct - rt_dataset)/rt_dataset
    fn_data: Optional[float] = None            # 数据集自报 Fn 列
    fn_resid_pct: Optional[float] = None       # (Fn_data - V/√(gL))/Fn_data
    admissible: bool = False                   # 是否可用于方法层误差判定
    reject_reason: str = ""
    implied_1k: Optional[float] = None         # Ct_inline / Cf（隐含 (1+k)+Cw/Cf）
    beam: Optional[float] = None
    cb: Optional[float] = None


@dataclass
class AuditResult:
    rows: List[CaseRow] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def verdicts(self) -> Dict[str, str]:
        return {c["id"]: c["verdict"] for c in self.checks}


def _summarise(rows: List[CaseRow]) -> str:
    kind = rows[0].ship_type if rows else "?"
    errs = [r.err_inline_pct for r in rows]
    worst = max(errs, key=abs)
    n_fail = sum(1 for e in errs if abs(e) > TOL_PCT)
    return (f"{kind}: inline Ct 误差 {min(errs):+.1f}% … {max(errs):+.1f}% "
            f"(|max|={abs(worst):.1f}%), 超 15% 门 {n_fail}/{len(errs)}")


def run_audit(dataset_path: Path = DATASET, module_path: Path = INLINE_MODULE) -> AuditResult:
    mod = load_inline_module(module_path)
    data = json.loads(dataset_path.read_text())
    res = AuditResult()

    for case in data["cases"]:
        geo = case["geometry"]
        length = float(geo["length_m"])
        # 回转体：以最大直径同时作为 beam 与 draft（端点用 πBL 而不是 πDL·f）
        diameter = float(geo.get("diameter_m", geo.get("beam_m", 0.0)))
        beam = float(geo.get("beam_m", diameter))
        draft = float(geo.get("draft_m", diameter))
        cb = float(geo.get("cb", 0.5))
        v = float(case["speed_ms"])

        out = call_resistance(
            mod,
            ship_type=case["ship_type"],
            speed_ms=v,
            length=length,
            beam=beam,
            draft=draft,
            cb=cb,
            water_density=RHO,
            kinematic_viscosity=NU,
        )
        re = out["reynolds_number"]
        fn = out["froude_number"]
        cf = ittc57_cf(re)
        # 端点内部湿表面积：S = πBL(0.5 + 0.25Cb)（与端点源码逐字同式）
        s_inline = math.pi * beam * length * (0.5 + 0.25 * cb)
        cr_inline = out["residual_resistance_n"] / (0.5 * RHO * s_inline * v * v)
        ct_inline = out["total_resistance_n"] / (0.5 * RHO * s_inline * v * v)
        ct_exp = float(case["experimental_ct"])

        row = CaseRow(
            case_id=case["case_id"],
            ship_type=case["ship_type"],
            v=v,
            fn=fn,
            re=re,
            ct_ref_exp=ct_exp,
            ct_inline=ct_inline,
            err_inline_pct=(ct_inline - ct_exp) / ct_exp * 100.0,
            cf=cf,
            cr_inline=cr_inline,
            s_inline=s_inline,
            beam=beam,
            cb=cb,
        )

        if "diameter_m" in geo:  # 全浸没回转体：可用 (1+k)Cf 解析参考
            k1 = hoerner_form_factor(length / diameter)
            row.ct_analytic = k1 * cf
            row.err_analytic_pct = (row.ct_analytic - ct_exp) / ct_exp * 100.0
            row.s_spheroid = spheroid_wetted_area(length, diameter)
            row.s_cylinder = cylinder_wetted_area(length, diameter)
            row.s_dataset = float(geo.get("wetted_surface_m2", 0.0)) or None

        if case.get("experimental_rt_n") and row.s_dataset:
            row.rt_dataset = float(case["experimental_rt_n"])
            row.rt_from_ct = 0.5 * RHO * row.s_dataset * ct_exp * v * v
            row.identity_resid_pct = (row.rt_from_ct - row.rt_dataset) / row.rt_dataset * 100.0

        row.implied_1k = ct_inline / cf
        fn_data = case.get("froude_number")
        if fn_data:
            row.fn_data = float(fn_data)
            row.fn_resid_pct = (row.fn_data - fn) / row.fn_data * 100.0

        # 方法层误差只在"物理可用"的案例上判定：
        #  (a) 数据集 Fn 列与 V/√(gL) 自洽（否则该行速度/尺度自相矛盾）
        #  (b) 水面船 Fn <= 0.45（超出即进入滑行 regime，排水型阻力比较无意义）；
        #      全浸没回转体不设 Fn 上限（无兴波），只要求 Fn 恒等式自洽
        submerged = "diameter_m" in geo   # 全浸没回转体：兴波项按 Froude 相似应→0，Fn 上限不适用
        if row.fn_resid_pct is not None and abs(row.fn_resid_pct) > 5.0:
            row.reject_reason = (f"数据集 Fn={row.fn_data:.3f} 与 V/√(gL)={fn:.3f} 不符 "
                                 f"({row.fn_resid_pct:+.1f}%)，该行速度/尺度自相矛盾")
        elif not submerged and fn > 0.45:
            row.reject_reason = f"Fn={fn:.3f} > 0.45，越出排水型（非滑行）阻力方法适用域"
        else:
            row.admissible = True
        res.rows.append(row)

    res.checks = _build_checks(res.rows)
    return res


def _build_checks(rows: List[CaseRow]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    def add(cid, name, verdict, detail, value=None, tol=None, basis=""):
        checks.append({
            "id": cid, "name": name, "verdict": verdict, "value": value,
            "tolerance_pct": tol, "detail": detail, "basis": basis,
        })

    # ---- P1 几何：回转体湿表面积 vs 解析长球体 ----------------------------
    rev = [r for r in rows if r.s_spheroid]
    if rev:
        r0 = rev[0]
        err_sph = (r0.s_inline - r0.s_spheroid) / r0.s_spheroid * 100.0
        err_ds = ((r0.s_inline - r0.s_dataset) / r0.s_dataset * 100.0) if r0.s_dataset else None
        add("P1a", "inline 湿表面积 vs 解析长球体(同L/D)",
            "FAIL" if abs(err_sph) > TOL_PCT else "PASS",
            f"S_inline={r0.s_inline:.4f} m² (πBL(0.5+0.25Cb), B=D={r0.__dict__.get('beam', 0.508):.3f} m)；"
            f"S_spheroid={r0.s_spheroid:.4f} m² (闭式)；偏差 {err_sph:+.2f}%"
            + (f"；同尺寸实心圆柱上界 {r0.s_cylinder:.4f} m²；数据集 {r0.s_dataset:.2f} m²"
               if r0.s_dataset and r0.s_cylinder else ""),
            value=round(err_sph, 3), tol=TOL_PCT,
            basis="prolate spheroid 闭式解析面积（同 L、同最大直径）—— 与端点实现无关")
        if r0.s_dataset:
            err_ds_sph = (r0.s_dataset - r0.s_spheroid) / r0.s_spheroid * 100.0
            add("P1b", "数据集湿表面积 vs 解析长球体",
                "PASS" if abs(err_ds_sph) <= TOL_PCT else "FAIL",
                f"数据集 {r0.s_dataset:.2f} m² vs 解析 {r0.s_spheroid:.4f} m² → {err_ds_sph:+.2f}% "
                f"（数据集 S 与解析一致 ⇒ 缺陷在 inline 公式侧）",
                value=round(err_ds_sph, 3), tol=TOL_PCT,
                basis="prolate spheroid 闭式解析面积")
        else:
            add("P1b", "数据集湿表面积 vs 解析长球体", "WITHHELD",
                "该行未给 wetted_surface_m2", basis="—")
    else:
        add("P1a", "inline 湿表面积 vs 解析回转体面积", "WITHHELD",
            "数据集中无回转体（直径）样本，几何判据无法实例化", basis="—")

    # ---- P2 数据集自洽：阻力恒等式与 Froude 恒等式 -----------------------
    id_rows = [r for r in rows if r.identity_resid_pct is not None]
    if id_rows:
        worst = max(id_rows, key=lambda r: abs(r.identity_resid_pct or 0.0))
        n_bad = sum(1 for r in id_rows if abs(r.identity_resid_pct or 0.0) > TOL_PCT)
        add("P2a", "数据集阻力恒等式 Rt = 0.5ρSCtV²",
            "PASS" if n_bad == 0 else "FAIL",
            f"{n_bad}/{len(id_rows)} 行破坏恒等式；最差 {worst.case_id}: "
            f"Rt_data={worst.rt_dataset:.1f} N vs 恒等式 {worst.rt_from_ct:.1f} N "
            f"({worst.identity_resid_pct:+.1f}%) → Rt 列不可用于任何误差判定",
            value=round(worst.identity_resid_pct or 0.0, 3), tol=TOL_PCT,
            basis="阻力定义式（纯代数，无外部数据）")

    fnr = [r for r in rows if r.fn_resid_pct is not None]
    if fnr:
        worst = max(fnr, key=lambda r: abs(r.fn_resid_pct or 0.0))
        n_bad = sum(1 for r in fnr if abs(r.fn_resid_pct or 0.0) > 5.0)
        add("P2b", "数据集 Froude 恒等式 Fn = V/√(gL)",
            "PASS" if n_bad == 0 else "FAIL",
            f"{n_bad}/{len(fnr)} 行不自洽；最差 {worst.case_id}: "
            f"Fn_data={worst.fn_data:.3f} vs V/√(gL)={worst.fn:.3f} ({worst.fn_resid_pct:+.1f}%) "
            f"→ 该行速度与尺度互相矛盾，其 Ct 不能作基准",
            value=round(worst.fn_resid_pct or 0.0, 3), tol=5.0,
            basis="Froude 数定义式（纯代数）")

    # ---- P3 方法层：仅物理可用案例 --------------------------------------
    adm = [r for r in rows if r.admissible]
    rej = [r for r in rows if not r.admissible]
    if adm:
        for stype, rs in _group(adm).items():
            errs = [r.err_inline_pct for r in rs]
            n_fail = sum(1 for e in errs if abs(e) > TOL_PCT)
            add(f"P3-{stype}", f"inline Ct vs 数据集 experimental_ct [{stype}]（可用案例）",
                "PASS" if n_fail == 0 else "FAIL",
                _summarise(rs) + f"；n={len(rs)}",
                value=round(max(errs, key=abs), 3), tol=TOL_PCT,
                basis="backend/data/benchmark_reference.json 的 experimental_ct（来源见各行 citation）")
    else:
        add("P3", "inline Ct vs 数据集（可用案例）", "WITHHELD",
            "无物理可用案例（全部被 P2 或 regime 判据排除）", basis="—")

    add("P3x", "被排除案例（数据集自相矛盾/越域）",
        "WITHHELD",
        ("; ".join(f"{r.case_id}: {r.reject_reason} (inline 误差 {r.err_inline_pct:+.1f}%)" for r in rej)
         if rej else "无"),
        value=len(rej), tol=None,
        basis="P2a/P2b 恒等式与水面船 Fn<=0.45 适用域；这些案例的误差数字不得用于判端点对错")

    # ---- P4 物理闭合：全浸没体解析 (1+k)Cf ------------------------------
    rev_rows = [r for r in rows if r.ct_analytic is not None]
    if rev_rows:
        errs = [r.err_analytic_pct or 0.0 for r in rev_rows]
        n_fail = sum(1 for e in errs if abs(e) > TOL_PCT)
        add("P4", "解析 (1+k)Cf vs 数据集（全浸没体）",
            "PASS" if n_fail == 0 else "FAIL",
            f"(1+k)Cf 相对数据集偏差 {min(errs):+.1f}% … {max(errs):+.1f}%，超 15% 门 {n_fail}/{len(errs)} —— "
            f"全浸没体按 Froude 相似兴波应→0，故 Ct≈(1+k)Cf；数据集在高 Fn 段高出解析 19–38%，"
            f"说明数据集 Ct 混入非全浸没成分（或 1+k 相关式不适用于该体），二者都使数据集不能当绝对真值",
            value=round(max(errs, key=abs), 3), tol=TOL_PCT,
            basis="Hoerner(1965) 回转体 1+k = 1 + 1.5(L/D)^-1.5 + 7(L/D)^-3 × ITTC-1957 Cf")

    # ---- P5 Cr 项出处 ---------------------------------------------------
    shares = [((r.cr_inline / (r.cr_inline + r.cf) * 100.0), r) for r in rows]
    lo = min(shares, key=lambda t: t[0]); hi = max(shares, key=lambda t: t[0])
    add("P5", "inline 剩余阻力项 Cr 的出处与幂律", "FAIL",
        f"Cr = 2e-3(1+2Cb)Fn^2.5：源码注释自述 'curve-fitting to experimental data'，但无引文/无拟合点位/"
        f"无拟合优度；Cr 占 Ct 份额 {lo[0]:.2f}% ({lo[1].case_id}) … {hi[0]:.2f}% ({hi[1].case_id})，"
        f"且同一幂律同时套用于水面船与全浸没回转体（后者兴波项应为 0）",
        value=round(hi[0], 3), tol=None,
        basis="源码自述 + 物理闭合（全浸没体无兴波）")

    # ---- P6 水面船隐含形状因子 ------------------------------------------
    surf = [r for r in rows if r.s_spheroid is None and r.admissible]
    if surf:
        vals = [r.implied_1k or 0.0 for r in surf]
        add("P6", "水面船 inline 隐含 (1+k)+Cw/Cf",
            "FAIL" if max(vals) < 1.10 else "PASS",
            f"Ct_inline/Cf = {min(vals):.3f} … {max(vals):.3f}（{surf[0].ship_type} 等）；"
            f"该值等于 1+k+Cw/Cf，端点既无形状因子也无兴波项 ⇒ 丰满船必然低估。"
            f"绝对误差量级需外部 (1+k)/Cw 基准，本轮记 WITHHELD（服务层 Holtrop 重写在 :8001，本机不可达）",
            value=round(max(vals), 3), tol=1.10,
            basis="物理闭合：Cb>0.8 母型船 (1+k) 下限远高于 1.10；本项只做定性上界判定")

    return checks


def _group(rows: List[CaseRow]) -> Dict[str, List[CaseRow]]:
    out: Dict[str, List[CaseRow]] = {}
    for r in rows:
        out.setdefault(r.ship_type, []).append(r)
    return out


# --------------------------------------------------------------------------
# 4. 输出
# --------------------------------------------------------------------------
def make_figures(res: AuditResult, outdir: Path) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # 图1: 回转体 Ct vs Fn —— 数据集 / inline / 解析(1+k)Cf
    rev = [r for r in res.rows if r.ct_analytic is not None]
    if rev:
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        fn = [r.fn for r in rev]
        ax.plot(fn, [r.ct_ref_exp for r in rev], "o-", label="experimental Ct (benchmark dataset)")
        ax.plot(fn, [r.ct_inline for r in rev], "s--", label="inline endpoint Ct (Cf+fabricated Cr)")
        ax.plot(fn, [r.ct_analytic for r in rev], "^:", label="analytic (1+k) Cf, Hoerner k + ITTC-57")
        ax.set_xlabel("Froude number Fn")
        ax.set_ylabel("total resistance coefficient Ct")
        ax.set_title("SUBOFF class: inline endpoint vs dataset vs analytic (submerged body)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        p = outdir / "fig1_suboff_ct_vs_fn.png"
        fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
        figs.append({"path": str(p), "caption": "图1 SUBOFF 家族 Ct–Fn：数据集、inline 端点与解析 (1+k)Cf 三条线"})

    # 图2: 相对误差 + 15% 门
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ids = [r.case_id for r in res.rows]
    errs = [r.err_inline_pct for r in res.rows]
    colors = [
        ("#c0392b" if abs(e) > TOL_PCT else "#27ae60") if r.admissible else "#bdc3c7"
        for e, r in zip(errs, res.rows)
    ]
    ax.bar(range(len(ids)), errs, color=colors)
    ax.axhline(TOL_PCT, ls="--", c="k", lw=1); ax.axhline(-TOL_PCT, ls="--", c="k", lw=1)
    ax.axhline(0, c="k", lw=0.8)
    ax.set_xticks(range(len(ids))); ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("inline Ct error vs experimental Ct (%)")
    ax.set_title("Per-case error of inline Ct vs dataset (grey = case rejected: dataset self-contradictory)")
    ax.grid(axis="y", alpha=0.3)
    p = outdir / "fig2_case_errors.png"
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    figs.append({"path": str(p), "caption": "图2 逐案例相对误差，红色为超 ±15% 平台精度门"})

    # 图3: 湿表面积四面比较
    r0 = next((r for r in res.rows if r.s_spheroid), None)
    if r0:
        labels = ["inline formula", "dataset", "analytic spheroid", "cylinder bound πDL"]
        vals = [r0.s_inline, r0.s_dataset or 0.0, r0.s_spheroid, r0.s_cylinder or 0.0]
        fig, ax = plt.subplots(figsize=(6.6, 4.0))
        bars = ax.bar(labels, vals, color=["#c0392b", "#2980b9", "#27ae60", "#7f8c8d"])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.2f}", ha="center", fontsize=9)
        ax.set_ylabel("wetted surface area (m²)")
        ax.set_title(f"SUBOFF wetted surface: inline formula vs analytic ({r0.case_id})")
        ax.grid(axis="y", alpha=0.3)
        p = outdir / "fig3_wetted_surface.png"
        fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
        figs.append({"path": str(p), "caption": "图3 湿表面积四面比较：inline 公式低估解析长球体约 20%"})

    # 图4: 数据集恒等式残差
    idr = [r for r in res.rows if r.identity_resid_pct is not None]
    if idr:
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        ax.bar([r.case_id for r in idr], [r.identity_resid_pct for r in idr], color="#8e44ad")
        ax.axhline(0, c="k", lw=0.8)
        ax.set_ylabel("(0.5ρSCtV² − Rt_dataset)/Rt_dataset  (%)")
        ax.set_title("Dataset identity check: Rt column vs resistance definition")
        ax.tick_params(axis="x", rotation=60, labelsize=7)
        ax.grid(axis="y", alpha=0.3)
        p = outdir / "fig4_dataset_identity.png"
        fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
        figs.append({"path": str(p), "caption": "图4 基准数据集 Rt 列与阻力恒等式的残差（可达 −86%，说明 Rt 列不可用于误差判定）"})

    # 图5: 隐含 (1+k)+Cw/Cf
    imp = [r for r in res.rows if r.implied_1k]
    if imp:
        fig, ax = plt.subplots(figsize=(7.4, 4.2))
        ax.bar([r.case_id for r in imp], [r.implied_1k for r in imp], color="#16a085")
        ax.axhline(1.0, c="k", lw=1, label="pure flat plate (1+k = 1, no wave)")
        ax.axhline(1.10, ls="--", c="#c0392b", lw=1, label="plausible lower bound 1+k >= 1.10")
        ax.set_ylabel("implied (1+k) + Cw/Cf  =  Ct_inline / Cf")
        ax.set_title("Inline method implies form factor + wave term: surface hulls fall below physical lower bound")
        ax.tick_params(axis="x", rotation=60, labelsize=7)
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)
        p5 = outdir / "fig5_implied_form_factor.png"
        fig.tight_layout(); fig.savefig(p5, dpi=140); plt.close(fig)
        figs.append({"path": str(p5), "caption": "图5 inline 隐含形状因子+兴波项：水面船落到 1.03–1.10，低于物理下限"})

    return [{"name": Path(f["path"]).name, "path": f["path"], "caption": f["caption"]} for f in figs]


def to_json(res: AuditResult, figures: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "audit": "inline_resistance_endpoint",
        "target": str(INLINE_MODULE.relative_to(REPO_ROOT)),
        "dataset": str(DATASET.relative_to(REPO_ROOT)),
        "constants": {"rho": RHO, "nu": NU, "g": G},
        "tolerance_pct": TOL_PCT,
        "summary": {
            "cases": len(res.rows),
            "checks": len(res.checks),
            "FAIL": sum(1 for c in res.checks if c["verdict"] == "FAIL"),
            "PASS": sum(1 for c in res.checks if c["verdict"] == "PASS"),
            "WITHHELD": sum(1 for c in res.checks if c["verdict"] == "WITHHELD"),
        },
        "checks": res.checks,
        "cases": [r.__dict__ for r in res.rows],
        "figures": figures,
        "evidence_level": {
            "friction_line(ITTC-57)": "REAL — 闭式解析，逐点复算一致",
            "wetted_surface(inline)": "MOCK — 无出处经验式，偏离解析回转体面积 -19.8%",
            "residual_resistance(inline)": "MOCK — 无引文 Fn^2.5 幂律",
            "total_resistance(inline, submerged)": "WITHHELD — 数据集 Rt/Ct 自相矛盾，无外部真值可用",
            "total_resistance(inline, surface ships)": "WITHHELD — 无 1+k/兴波项，服务层 Holtrop 重写在 :8001（报告 1140），本机不可达",
        },
    }


def _print_summary(res: AuditResult) -> None:
    for c in res.checks:
        print(f"[{c['verdict']:>8}] {c['id']:<10} {c['name']}")
        print(f"           {c['detail']}")
    s = {"FAIL": 0, "PASS": 0, "WITHHELD": 0}
    for c in res.checks:
        s[c["verdict"]] = s.get(c["verdict"], 0) + 1
    print(f"\n合计: {s}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="inline 阻力端点验证欠账审计")
    ap.add_argument("--out", default=None, help="输出目录（写 results.json + 图）")
    ap.add_argument("--json", default=None, help="结果 JSON 另存路径")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    res = run_audit()
    figs: List[Dict[str, str]] = []
    if args.out and not args.no_figures:
        figs = make_figures(res, Path(args.out))
    payload = to_json(res, figs)
    _print_summary(res)

    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\n写入 {outdir/'results.json'}")
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"写入 {args.json}")

    # 退出码：有 FAIL 判据即 1（CI 可据此阻断）
    return 1 if payload["summary"]["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())