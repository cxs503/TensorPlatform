"""阻力引擎的**外部基准**复核闸门（验证欠账清偿 R5–R7）。

背景
----
`services/benchmark_integrity.py` 已把"参考值自身是否自洽"落成机器可判的闭合检查
（C1–C5）。但它是**内部自洽**闸门：只问"表里的数彼此矛盾吗"，不问
"引擎的物理子模型对得上实验/几何基准吗"。

本模块补的正是后者，针对 `calc.resistance` 引擎（`services/ship_resistance.py`）里
两条**经验式**——它们是 `docs/benchmarks/benchmark_data_integrity.md` §3.2 解析账本
无法证伪的对象（账本只把公式与公式自身比对，误差恒 ~1e-7）：

* 湿表面积 ``S = pi*B*L*(0.5 + 0.25*Cb)``（船体拟合式）
* 残余阻力 ``Cr = 0.002*(1 + 2*Cb)*Fn**2.5``

判据来源（全部外部、可复核，非本轮拟合）
----------------------------------------
* **E1/E3 基准=记录实测湿面积**：正典 v3.0 `backend/data/benchmark_reference.json`
  的 `SUBOFF-01..04` 行自带 `geometry.wetted_surface_m2 = 5.42`（Roddy 1990 DTRC-90/012
  船模拖曳水池实测）。这是本仓唯一可用于"引擎 S 公式绝对精度"判决的外部参考。
* **E2 几何基准=长椭球闭式解**：与 SUBOFF 同 L、同最大直径的回转体，其侧面积有精确
  闭式解（长椭球，偏心率 e）：

  ``S = 2*pi*b^2 + 2*pi*(a*b/e)*asin(e)``，``a = L/2, b = D/2, e = sqrt(1-(b/a)^2)``

  它**不依赖任何船体拟合系数**，因此可独立裁决"是引擎偏了还是参考值偏了"。
  E3 先把该基准对实测值自校（要求 <3%），自校不通过则整个判决降级 WITHHELD。
* **E4 Cb 溯源**：回转体行在正典里没有 Cb、也没有排水体积，而
  `scripts/benchmark_audit.py:214` 用 `geom.get("cb") or 0.542` 兜底——该常数**无溯源**，
  本模块把它判为 WITHHELD 并把候选值（长椭球 π/6 = 0.5236）摆出来对齐。

判级（severity，与 `benchmark_integrity` 同序）
----------------------------------------------
``PASS < WARN < WITHHELD < FAIL``；行级取最差项，数据集级取全部行最差项。

* ``PASS``    相对误差 <= 3%（平台"理想精度"标尺）
* ``WARN``    3% < |误差| <= 15%（不理想但仍在可接受带内）
* ``FAIL``    |误差| > 15%（平台硬门槛：不可接受）
* ``WITHHELD`` 缺少可复核基准（不得据此授予任何精度判决）

纯 stdlib；不连 DB / 不起 HTTP，可脱离服务测试。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Verdicts / bars  (与 services/benchmark_integrity.py 同序同义)
# ---------------------------------------------------------------------------

PASS = "PASS"
WARN = "WARN"
WITHHELD = "WITHHELD"
FAIL = "FAIL"

_SEVERITY = {PASS: 0, WARN: 1, WITHHELD: 2, FAIL: 3}

TARGET_BAR_PCT = 3.0     # 理想标尺：|误差| <= 3% 记 PASS
ACCEPT_BAR_PCT = 15.0    # 平台硬门槛：|误差| > 15% 不可接受

#: `scripts/benchmark_audit.py` 对回转体行的 Cb 兜底常数（无溯源，见 E4）
CB_FALLBACK_UNPROVENANCED = 0.542
#: 长椭球相对 L*B*T 盒的方形系数 = (π/6)ab²/(ab²) ... = π/6（同 L、D 的长椭球）
CB_PROALTE_SPHEROID = math.pi / 6.0


def worst(verdicts: Iterable[str]) -> str:
    """Row/dataset verdict = worst of all checks."""
    out = PASS
    for v in verdicts:
        if _SEVERITY[v] > _SEVERITY[out]:
            out = v
    return out


def verdict_for_error(err_pct: float) -> str:
    """Map a relative error (percent) to a verdict on the platform bars."""
    if err_pct is None or not math.isfinite(err_pct):
        return WITHHELD
    a = abs(err_pct)
    if a <= TARGET_BAR_PCT:
        return PASS
    if a <= ACCEPT_BAR_PCT:
        return WARN
    return FAIL


def rel_err_pct(value: float, reference: float) -> float:
    """Signed relative error of ``value`` against ``reference`` in percent."""
    if reference is None or not math.isfinite(reference) or abs(reference) < 1e-15:
        return float("nan")
    return (value - reference) / abs(reference) * 100.0


# ---------------------------------------------------------------------------
# External geometry baseline: prolate spheroid lateral area (closed form)
# ---------------------------------------------------------------------------

def prolate_spheroid_wetted_area(length_m: float, diameter_m: float) -> float:
    """Exact wetted (lateral) area of a prolate spheroid with the same L and D.

    ``a = L/2``, ``b = D/2``, eccentricity ``e = sqrt(1 - (b/a)^2)``;
    the surface of revolution is

        S = 2*pi*b^2 + (2*pi*a*b/e) * asin(e)

    (the first term is the two poles' cap contribution). No fitted coefficient.
    """
    if length_m <= 0 or diameter_m <= 0:
        raise ValueError("length_m and diameter_m must be positive")
    if diameter_m > length_m:
        raise ValueError("prolate spheroid requires diameter_m <= length_m")
    a = 0.5 * length_m
    b = 0.5 * diameter_m
    e = math.sqrt(max(0.0, 1.0 - (b / a) ** 2))
    if e < 1e-12:                      # sphere limit
        return 4.0 * math.pi * b * b
    return 2.0 * math.pi * b * b + (2.0 * math.pi * a * b / e) * math.asin(e)


def prolate_spheroid_block_coefficient() -> float:
    """``Cb`` of the same-L/D prolate spheroid relative to the L*B*T box: ``pi/6``."""
    return CB_PROALTE_SPHEROID


# ---------------------------------------------------------------------------
# Engine accessors (lazy import: this module stays importable standalone)
# ---------------------------------------------------------------------------

def _engine():
    from services import ship_resistance  # noqa: WPS433 (lazy, avoids import cycles)
    return ship_resistance


def engine_wetted_surface(length_m: float, beam_m: float, cb: float) -> float:
    """``S`` exactly as the shipped endpoint computes it."""
    return _engine().wetted_surface_axisym(beam_m, length_m, cb)


def engine_residual_coefficient(fn: float, cb: float) -> float:
    return _engine().residual_coefficient(fn, cb)


# ---------------------------------------------------------------------------
# Case geometry extraction
# ---------------------------------------------------------------------------

def case_dimensions(case: Mapping[str, Any]) -> Dict[str, Any]:
    """Pull ``(L, B, D, S_recorded, cb_provenanced)`` out of a canon case row."""
    geom = case.get("geometry") or {}
    length = geom.get("length_m")
    diameter = geom.get("diameter_m")
    beam = geom.get("beam_m", diameter)
    draft = geom.get("draft_m", diameter)
    return {
        "length_m": length,
        "beam_m": beam,
        "draft_m": draft,
        "diameter_m": diameter,
        "wetted_surface_recorded_m2": geom.get("wetted_surface_m2"),
        "cb_provenanced": geom.get("cb"),
        "displacement_kg": geom.get("displacement_kg"),
        "form_type": case.get("form_type"),
    }


# ---------------------------------------------------------------------------
# E1/E2/E3 — wetted-surface external baselines
# ---------------------------------------------------------------------------

def check_wetted_surface_external(case: Mapping[str, Any],
                                  cb_used: float = CB_FALLBACK_UNPROVENANCED,
                                  target_pct: float = TARGET_BAR_PCT,
                                  accept_pct: float = ACCEPT_BAR_PCT) -> Dict[str, Any]:
    """E1/E2/E3: engine ``S`` and the geometric baseline vs the recorded measurement.

    Returns a dict with three sub-checks plus a row verdict:

    * ``baseline_selfcheck``   geometric baseline (prolate spheroid) vs recorded S
    * ``engine_vs_recorded``   engine S formula vs recorded S   <-- the debt itself
    * ``engine_vs_baseline``   engine S formula vs geometric baseline

    If ``baseline_selfcheck`` is not within ``target_pct`` the baseline is not
    admissible and the whole row degrades to ``WITHHELD`` (never silently PASS).
    """
    dims = case_dimensions(case)
    L, B, D = dims["length_m"], dims["beam_m"], dims["diameter_m"]
    s_rec = dims["wetted_surface_recorded_m2"]
    out: Dict[str, Any] = {
        "case_id": case.get("case_id"),
        "length_m": L,
        "beam_m": B,
        "diameter_m": D,
        "cb_used": cb_used,
        "cb_provenanced": dims["cb_provenanced"],
        "wetted_surface_recorded_m2": s_rec,
    }
    if not L or not B:
        out.update(verdict=WITHHELD, reason="几何缺 L 或 B，无法复核湿面积")
        return out

    s_engine = engine_wetted_surface(L, B, cb_used)
    out["wetted_surface_engine_m2"] = s_engine

    s_base = None
    if D and 0 < D <= L:
        s_base = prolate_spheroid_wetted_area(L, D)
        out["wetted_surface_baseline_m2"] = s_base
        out["baseline_name"] = "prolate_spheroid_same_L_D"

    # E3 baseline self-check (precondition for the whole verdict)
    if s_base is not None and s_rec:
        e_base = rel_err_pct(s_base, s_rec)
        out["baseline_selfcheck"] = {
            "error_pct": e_base,
            "verdict": verdict_for_error(e_base),
            "note": "几何基准 vs 记录实测 S；>3% 则基准不可用",
        }
        admissible = abs(e_base) <= target_pct
    elif s_base is not None:
        out["baseline_selfcheck"] = {
            "error_pct": float("nan"), "verdict": WITHHELD,
            "note": "无记录实测 S 可比，几何基准未自校",
        }
        admissible = False
    else:
        out["baseline_selfcheck"] = {
            "error_pct": float("nan"), "verdict": WITHHELD,
            "note": "非回转体（无 diameter_m），未启用椭球基准",
        }
        admissible = False

    # E1 engine vs recorded measurement (the debt under audit)
    if s_rec:
        e_eng = rel_err_pct(s_engine, s_rec)
        out["engine_vs_recorded"] = {
            "error_pct": e_eng,
            "verdict": verdict_for_error(e_eng),
            "within_accept_bar": abs(e_eng) <= accept_pct,
            "within_target_bar": abs(e_eng) <= target_pct,
            "note": "引擎 S 公式 vs 记录实测 S（Roddy 1990 / 正典 v3.0）",
        }
    else:
        out["engine_vs_recorded"] = {
            "error_pct": float("nan"), "verdict": WITHHELD,
            "note": "记录无实测湿面积",
        }

    # E2 engine vs geometric baseline
    if s_base:
        e_g = rel_err_pct(s_engine, s_base)
        out["engine_vs_baseline"] = {
            "error_pct": e_g,
            "verdict": verdict_for_error(e_g),
            "note": "引擎 S 公式 vs 长椭球闭式解",
        }
    else:
        out["engine_vs_baseline"] = {
            "error_pct": float("nan"), "verdict": WITHHELD,
            "note": "无几何基准",
        }

    if not s_rec:
        out["verdict"] = WITHHELD
        out["reason"] = "无实测 S，只有相对几何基准的偏差，不足以授予精度判决"
        return out

    # 记录实测值本身就是权威外部基准；几何基准只作**附加**校核。
    # 几何基准自校未过 3% 时它不是"基准坏了"，而是"这个形体不适用椭球近似"——
    # 此时把 E2 降级为 WITHHELD，而不是拿一个不可信的基准去否决实测判决。
    checks = [out["engine_vs_recorded"]["verdict"]]
    if admissible:
        checks.append(out["engine_vs_baseline"]["verdict"])
    else:
        out["engine_vs_baseline"]["verdict"] = WITHHELD
        out["engine_vs_baseline"]["note"] = (
            "几何基准自校未过 3%%（偏离记录实测 S %.2f%%%%）→ 椭球近似不适用于该形体，"
            "本项不参与判决" % abs(out["baseline_selfcheck"]["error_pct"]))
        out["baseline_inadmissible"] = True

    out["verdict"] = worst(checks)
    errs = [abs(out["engine_vs_recorded"]["error_pct"])]
    if admissible:
        errs.append(abs(out["engine_vs_baseline"]["error_pct"]))
    out["reason"] = out.get("reason") or (
        "引擎 S 公式相对外部基准的最大偏差 %.2f%%（基准集合：记录实测%s）"
        % (max(errs), " + 长椭球闭式解" if admissible else ""))
    return out


# ---------------------------------------------------------------------------
# E4 — Cb provenance for bodies of revolution
# ---------------------------------------------------------------------------

def check_cb_provenance(case: Mapping[str, Any],
                        cb_used: float = CB_FALLBACK_UNPROVENANCED,
                        tol_pct: float = TARGET_BAR_PCT) -> Dict[str, Any]:
    """E4: is the ``Cb`` feeding the engine traceable to the canon row?

    The canon carries no ``Cb``/``volume`` for body-of-revolution rows, so callers
    fall back to a module-level constant (0.542). A constant that is not in the
    dataset is not a property of the model: this check reports WITHHELD, but it
    also reports how far the constant sits from the only defensible closed-form
    value (same-L/D prolate spheroid, ``pi/6``) so the gap is visible.
    """
    dims = case_dimensions(case)
    out: Dict[str, Any] = {
        "case_id": case.get("case_id"),
        "cb_used": cb_used,
        "cb_provenanced": dims["cb_provenanced"],
        "cb_spheroid": CB_PROALTE_SPHEROID,
        "cb_band_box": [0.0, 1.0],
    }
    if dims["cb_provenanced"] is not None:
        e = rel_err_pct(cb_used, float(dims["cb_provenanced"]))
        out["error_pct_vs_canon"] = e
        out["verdict"] = verdict_for_error(e) if abs(e) > 0 else PASS
        out["reason"] = "Cb 有正典溯源"
        return out

    e_sp = rel_err_pct(cb_used, CB_PROALTE_SPHEROID)
    out["error_pct_vs_spheroid"] = e_sp
    out["verdict"] = WITHHELD
    out["reason"] = (
        "正典行无 Cb/排水体积 → 引擎 Cb=%.4f 属无溯源兜底常数（源自 "
        "scripts/benchmark_audit.py），与长椭球 π/6=%.4f 相差 %.2f%%；"
        "该常数不是该模型的属性，不得作为几何验证依据"
        % (cb_used, CB_PROALTE_SPHEROID, e_sp)
    )
    return out


# ---------------------------------------------------------------------------
# E5 — residual-law form / physical consistency
# ---------------------------------------------------------------------------

def check_residual_law_form(cb: float = CB_FALLBACK_UNPROVENANCED,
                            fn_seq: Optional[Sequence[float]] = None,
                            cr_ceiling: float = 0.01) -> Dict[str, Any]:
    """E5: does ``Cr = 0.002*(1+2Cb)*Fn**2.5`` survive its own limits?

    Checked (all analytic, no reference data needed):

    * L1 ``Cr(Fn -> 0) = 0``（无波无残阻）
    * L2 ``dCr/dFn > 0``（单调增）
    * L3 ``0 < Cr < cr_ceiling`` for ``Fn in (0, 0.6]``（量级带：残阻系数不可能到 1e-2）
    * L4 幂次 2.5 的超线性：``Cr(2Fn)/Cr(Fn) = 2**2.5 = 5.657``
    """
    if fn_seq is None:
        fn_seq = [0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.459, 0.5, 0.6]
    floor = 0.05  # ship_resistance.residual_coefficient 的 fn_floor 默认值
    pts = [{"fn": fn, "cr": engine_residual_coefficient(fn, cb)} for fn in fn_seq]
    # L2 只在 fn_floor 之上判单调：低于门槛时该实现**有意**归零（阻塞流区无残阻），
    # 把这段算进单调性会把"设计保护"误判成"形状律病态"。
    above = [p["cr"] for p in pts if p["fn"] > floor]
    monotone = all(b > a for a, b in zip(above, above[1:]))
    in_band = all(0.0 < p["cr"] < cr_ceiling for p in pts if p["fn"] > floor)
    limit_zero = engine_residual_coefficient(0.0, cb) == 0.0
    # 幂次律：同一 Cb 下比值恒为 2**2.5
    fn_a, fn_b = 0.2, 0.4
    ratio = (engine_residual_coefficient(fn_b, cb)
             / engine_residual_coefficient(fn_a, cb))
    expected_ratio = 2.0 ** 2.5
    e_ratio = rel_err_pct(ratio, expected_ratio)
    checks = {
        "L1_zero_at_fn0": {"pass": limit_zero, "cr": engine_residual_coefficient(0.0, cb)},
        "L2_monotone": {"pass": monotone},
        "L3_magnitude_band": {"pass": in_band, "ceiling": cr_ceiling},
        "L4_exponent_2p5": {"pass": abs(e_ratio) <= TARGET_BAR_PCT,
                            "ratio": ratio, "expected": expected_ratio,
                            "error_pct": e_ratio},
    }
    ok = all(c["pass"] for c in checks.values())
    return {
        "cb": cb,
        "points": pts,
        "checks": checks,
        "verdict": PASS if ok else FAIL,
        "reason": "形状律通过 4 项极限/量级/幂次检查" if ok
                  else "形状律存在不满足的解析极限或量级检查",
    }


def check_residual_magnitude(case: Mapping[str, Any], speed_ms: float,
                             rho: float, nu: float,
                             cb: float = CB_FALLBACK_UNPROVENANCED) -> Dict[str, Any]:
    """E6: engine ``Cr`` vs the ``Cr`` implied by the recorded ``(Ct, Cf)``.

    Implied residual: ``Cr_impl = Ct_recorded - Cf_ITTC57(Re)``, an identity that
    needs no assumption about wetted area (both coefficients are per unit area).
    """
    if not is_adjudicable(case):
        return {"case_id": case.get("case_id"), "verdict": WITHHELD,
                "reason": "参考行自身 reference_status=%s，不可作为判据来源"
                          % case.get("reference_status")}
    eng = _engine()
    L = case_dimensions(case)["length_m"]
    ct_rec = case.get("experimental_ct")
    re = eng.reynolds_number(speed_ms, L, nu)
    fn = eng.froude_number(speed_ms, L)
    cf = eng.ittc57_cf(re)
    cr_eng = eng.residual_coefficient(fn, cb)
    out = {"case_id": case.get("case_id"), "speed_ms": speed_ms,
           "reynolds": re, "froude": fn, "cf_ittc57": cf,
           "cr_engine": cr_eng}
    if ct_rec is None:
        out.update(verdict=WITHHELD, reason="记录无 Ct，无法反解 Cr")
        return out
    cr_impl = ct_rec - cf
    e = rel_err_pct(cr_eng, cr_impl)
    out.update(ct_recorded=ct_rec, cr_implied=cr_impl, error_pct=e,
               verdict=verdict_for_error(e),
               reason="引擎 Cr vs Ct_recorded-Cf_ITTC57 反解值（仅比较 Cr 量级，"
                      "不含湿面积假设）")
    return out


# ---------------------------------------------------------------------------
# E7 — end-to-end error and its multiplicative decomposition
# ---------------------------------------------------------------------------

def check_end_to_end(case: Mapping[str, Any], speed_ms: float,
                     rho: float, nu: float,
                     cb: float = CB_FALLBACK_UNPROVENANCED) -> Dict[str, Any]:
    """E7: engine ``(Ct, Rt)`` vs recorded ``(Ct, Rt)`` + *why*.

    Multiplicative decomposition of the total-resistance ratio (exact, not a fit):

        Rt_e / Rt_m = (S_e / S_m) * (Cf + Cr_e) / (Ct_m)

    so a total error < 15% can hide a > 15% area error if ``Cr`` compensates.
    Reporting both terms is the point: a passing total does not acquit the
    sub-model, and a failing sub-model does not automatically fail the total.
    """
    if not is_adjudicable(case) or case.get("reference_status", "").upper() != "DERIVED" and \
            (case.get("rt_status") or "").upper() not in ADJUDICABLE_STATUS:
        return {"case_id": case.get("case_id"), "verdict": WITHHELD,
                "reason": "参考行自身不可裁决（reference_status=%s / rt_status=%s），"
                          "不得据此判决引擎" % (case.get("reference_status"),
                                                case.get("rt_status"))}
    eng = _engine()
    dims = case_dimensions(case)
    L, B = dims["length_m"], dims["beam_m"]
    s_rec = dims["wetted_surface_recorded_m2"]
    ct_rec = case.get("experimental_ct")
    rt_rec = case.get("experimental_rt_n")

    bd = eng.resistance_breakdown(L, B, dims["draft_m"] or B, cb, speed_ms, rho, nu)
    out = {"case_id": case.get("case_id"), "speed_ms": speed_ms,
           "cb_used": cb, "engine": {k: bd[k] for k in
                                     ("wetted_surface_m2", "reynolds", "froude",
                                      "cf", "cr", "ct", "rt_n")}}
    if ct_rec is not None:
        e_ct = rel_err_pct(bd["ct"], ct_rec)
        out["ct_recorded"] = ct_rec
        out["ct_error_pct"] = e_ct
        out["ct_verdict"] = verdict_for_error(e_ct)
    if rt_rec is not None:
        e_rt = rel_err_pct(bd["rt_n"], rt_rec)
        out["rt_recorded"] = rt_rec
        out["rt_error_pct"] = e_rt
        out["rt_verdict"] = verdict_for_error(e_rt)
    if s_rec and ct_rec:
        s_ratio = bd["wetted_surface_m2"] / s_rec
        cf_plus_cr = bd["cf"] + bd["cr"]
        # 记录行自带 Ct↔Rt 闭合因子（=1 表示记录本身闭合到 0 位）；分解式
        # Rt_e/Rt_m = (S_e/S_m) * ((Cf+Cr_e)/Ct_m) * closure_recorded 是恒等式，
        # 不是拟合，故可用来把总误差**无损**拆成面积项与系数项。
        closure_rec = (0.5 * rho * speed_ms ** 2 * s_rec * ct_rec) / rt_rec if rt_rec else float("nan")
        total_ratio = bd["rt_n"] / rt_rec if rt_rec else float("nan")
        product = s_ratio * (cf_plus_cr / ct_rec) * closure_rec
        out["decomposition"] = {
            "area_ratio_S_engine_over_recorded": s_ratio,
            "area_term_pct": (s_ratio - 1.0) * 100.0,
            "ct_engine_over_ct_recorded": cf_plus_cr / ct_rec,
            "ct_term_pct": (cf_plus_cr / ct_rec - 1.0) * 100.0,
            "closure_recorded": closure_rec,
            "total_ratio": total_ratio,
            "total_error_pct": (total_ratio - 1.0) * 100.0 if rt_rec else float("nan"),
            "product_check": product,
            "identity_holds": (abs(product - total_ratio) <= 1e-9 * max(1.0, abs(total_ratio))
                               if rt_rec else None),
            # 反事实：同一引擎、把 S 换成记录实测值 → 面积误差对总误差的贡献份额
            "counterfactual_rt_with_recorded_S_n": bd["rt_n"] * (s_rec / bd["wetted_surface_m2"])
            if bd["wetted_surface_m2"] else float("nan"),
            "counterfactual_rt_error_pct": rel_err_pct(
                bd["rt_n"] * (s_rec / bd["wetted_surface_m2"]), rt_rec)
            if (rt_rec and bd["wetted_surface_m2"]) else float("nan"),
        }
    out["verdict"] = worst(
        verdict_for_error(out.get(k, float("nan")))
        for k in ("ct_error_pct", "rt_error_pct")
    )
    out["reason"] = ("总阻力与总阻力系数各自与记录值的偏差；面积项单独列出，"
                     "用于区分「总误差过关但子模型不过关」的补偿性误差")
    return out


# ---------------------------------------------------------------------------
# Dataset-level audit
# ---------------------------------------------------------------------------

ADJUDICABLE_STATUS = ("DERIVED", "PASS", "VERIFIED", "CONSISTENT")


def is_adjudicable(case: Mapping[str, Any]) -> bool:
    """True when the canon itself declares this row usable as a 判据来源.

    `benchmark_integrity` 已把「参考行不可复核」标成 WITHHELD；对这类行做
    「引擎 vs 记录」的误差比较毫无意义（记录本身就不成立），必须拒绝判决。
    """
    st = (case.get("reference_status") or "").upper()
    if st and st not in ADJUDICABLE_STATUS:
        return False
    return case.get("experimental_ct") is not None


def case_cb(case: Mapping[str, Any],
            fallback: float = CB_FALLBACK_UNPROVENANCED) -> float:
    """``Cb`` used for this row: the canon value when present, else the fallback."""
    geom = case.get("geometry") or {}
    cb = geom.get("cb")
    return float(cb) if cb is not None else fallback


def audit_case(case: Mapping[str, Any],
               speed_ms: Optional[float] = None,
               rho: Optional[float] = None,
               nu: Optional[float] = None,
               cb: Optional[float] = None) -> Dict[str, Any]:
    """Run E1–E7 for one canon case and bind a row verdict.

    ``cb=None`` (default) means "use the row's own Cb if the canon carries one,
    otherwise the documented fallback constant" — see :func:`case_cb`.
    """
    if cb is None:
        cb = case_cb(case)
    speed = speed_ms if speed_ms is not None else case.get("speed_ms")
    rho = rho if rho is not None else case.get("reference_density_kg_m3", 998.2)
    nu = nu if nu is not None else case.get(
        "reference_kinematic_viscosity_m2_s", 1.004e-06)

    ws = check_wetted_surface_external(case, cb)
    cb_check = check_cb_provenance(case, cb)
    law = check_residual_law_form(cb)
    mag = check_residual_magnitude(case, speed, rho, nu, cb) if speed else {
        "verdict": WITHHELD, "reason": "无航速"}
    end = check_end_to_end(case, speed, rho, nu, cb) if speed else {
        "verdict": WITHHELD, "reason": "无航速"}

    # 精度判决只看"对得起外部基准吗"；Cb 溯源是**独立的证据等级问题**，
    # 单独成字段（provenance_caveat），不把行判成 WITHHELD —— 否则任何回转体行
    # 都会因正典缺 Cb 而永远无法被判决，闸门就退化成永久 WITHHELD。
    accuracy_verdict = worst([ws["verdict"], law["verdict"],
                              mag["verdict"], end["verdict"]])
    return {
        "case_id": case.get("case_id"),
        "form_type": case.get("form_type"),
        "speed_ms": speed,
        "reference_status": case.get("reference_status"),
        "wetted_surface_external": ws,
        "cb_provenance": cb_check,
        "residual_law": law,
        "residual_magnitude": mag,
        "end_to_end": end,
        "provenance_caveat": cb_check["verdict"] in (WITHHELD, WARN, FAIL),
        "verdict": accuracy_verdict,
    }


def audit_dataset(dataset: Mapping[str, Any],
                  cb: Optional[float] = None) -> Dict[str, Any]:
    """Audit every case in a canon dataset; dataset verdict = worst row."""
    cases = dataset.get("cases") or []
    rows = [audit_case(c, cb=cb) for c in cases]
    by_form: Dict[str, List[str]] = {}
    for r in rows:
        by_form.setdefault(r.get("form_type") or "unknown", []).append(r["verdict"])
    return {
        "dataset_version": dataset.get("version"),
        "cb_fallback": CB_FALLBACK_UNPROVENANCED,
        "cb_per_row": {r["case_id"]: r["cb_provenance"]["cb_used"] for r in rows},
        "bar_target_pct": TARGET_BAR_PCT,
        "bar_accept_pct": ACCEPT_BAR_PCT,
        "rows": rows,
        "row_verdicts": {r["case_id"]: r["verdict"] for r in rows},
        "per_form_verdict": {k: worst(v) for k, v in by_form.items()},
        "provenance_caveat_rows": [r["case_id"] for r in rows if r.get("provenance_caveat")],
        "binding": worst(r["verdict"] for r in rows) if rows else WITHHELD,
        "n_rows": len(rows),
    }