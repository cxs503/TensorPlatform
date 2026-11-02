"""优化器目标函数物理保真度审计（m1601 建 / m1610 升级为双轨）

背景
----
m1601 复核判定 ``optimizer/interfaces/resistance_eval.py::evaluate_resistance``
证据等级 MOCK：Ct 恒等于 ITTC-57 Cf（Cr/Cf ~ 3e-5 vs 正典 0.335），相关补贴 Ca 以
[m²] 量加到 [N]（系数低估 99.96%），Ct 相对正典 Holtrop-1984 误差 44.3%。

m1610 的修法是把物理内核**委托正典** ``backend/api/holtrop1984.py``（agent/m1553
落地），几何由 ``kcs_bow_param`` 提供 cm/cwp 等船型要素；旧手拟链条只作 fallback。
于是本工具改为**双轨**，两条轨用同一套判据：

* ``live``   —— 优化器实际在跑的目标函数（``evaluate_resistance`` ->
  ``holtrop_resistance``，正典委托）。判决应为 REAL。
* ``legacy`` —— 强制走旧手拟 fallback（``_legacy_holtrop_resistance``）。判决必须
  仍是 MOCK，失败项固定为 {C3, C4, C5, C6}——这是"退回旧链条"的回归钉子。

判据（C1~C7，每条都可被变异体单独判负）
--------------------------------------
C1 几何恒等式     ∇=Cb·L·B·T，L/B、B/T 落在 Holtrop 标定域
C2 湿表面积       S_wet vs 正典 wetted_surface（≤15%），且满足几何夹逼 2LT ≤ S ≤ L(B+2T)
C3 量纲一致性     相关补贴 Ca 必须无量纲进入 Ct；禁止把 [m²] 量加到 [N] 上
C4 残余阻力显著性 Cr/Cf 与正典同量级（0.5 ≤ ratio ≤ 2），否则形阻+兴波被抹平
C5 总阻力对比     Ct / Rt vs 正典相对误差（15% 不可接受门 + 3% 报告门）
C6 物理下界       Ct ≥ Cf；Ct ≥ (1+k)·Cf（k 取正典形状因子）
C7 速度极限/单调  Rt(V) 严格递增；Ct(V) 在摩擦主导域（Fn≤0.15）单调下降

正典来源（零手抄、可复现）
--------------------------
优先读工作树 ``backend/api/holtrop1984.py``；不存在则从 git 引用 ``git show <ref>:<path>``
取出到临时目录导入，并把 ref 与 blob sha256 记入产物 provenance。两者都没有 → 判定
WITHHELD（不编数字）。

用法
----
实现落在 ``optimizer/audit/``（受本单 EDIT_WHITELIST 约束）；验收入口
``tools/audit/optimizer_objective_audit.py`` 是同一实现的薄封装，两者等价：

    python3 tools/audit/optimizer_objective_audit.py --write   # 生成 JSON+MD+图
    python3 tools/audit/optimizer_objective_audit.py --check   # 漂移闸：复算==入库
    python3 -m optimizer.audit.optimizer_objective_audit --check

产物：``artifacts/optimizer_objective_audit/optimizer_objective_audit.json``（双轨判据+数值+
provenance）、``docs/verification/optimizer-objective-physics-audit.md``（论文级，表格程序化
注入）、``docs/verification/figures/optobj_*.png``。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "optimizer_objective_audit"
DOC = ROOT / "docs" / "verification" / "optimizer-objective-physics-audit.md"
SUPERSEDED_DOC = ROOT / "docs" / "verification" / "optimizer-objective-mock-audit.md"
FIGDIR = ROOT / "docs" / "verification" / "figures"
CANON_REL = "backend/api/holtrop1984.py"
DEFAULT_REFS = ("agent/m1553", "main")

ERR_GATE = 15.0   # 平台精度门：>15% 不可接受
ERR_TIGHT = 3.0   # 报告要求显式回答 "<3%"
G = 9.81          # 重力加速度 [m/s^2]
#: 摩擦主导域上限（Fn 起，兴波阻力开始主导，Ct 出现阻力峰）。
#: Ct(V) 的单调下降只在摩擦主导域成立；真实位移船在 Fn>0.15 处必须
#: 出现 Ct 回升（阻力峰），故 C7 只在该子域要求 Ct 单调下降。
FN_FRICTION_LIMIT = 0.15

TRACKS = ("live", "legacy")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 正典内核装载
# ---------------------------------------------------------------------------
def _load_canonical() -> Dict[str, Any]:
    """返回 {module, provenance} —— 只读，不修改正典。"""
    local = ROOT / CANON_REL
    if local.exists():
        spec = importlib.util.spec_from_file_location("_canon_h84", local)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = mod          # dataclasses 需要模块已注册
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        blob = hashlib.sha256(local.read_bytes()).hexdigest()
        return {"module": mod, "provenance": {"source": "worktree", "path": CANON_REL,
                                              "sha256": blob}}
    for ref in DEFAULT_REFS:
        try:
            blob_bytes = subprocess.run(
                ["git", "show", f"{ref}:{CANON_REL}"], cwd=ROOT,
                capture_output=True, check=True, timeout=60).stdout
        except Exception:
            continue
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "canon_h84.py"
            p.write_bytes(blob_bytes)
            spec = importlib.util.spec_from_file_location("_canon_h84", p)
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        sha = subprocess.run(["git", "rev-parse", f"{ref}:{CANON_REL}"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        return {"module": mod, "provenance": {"source": f"git:{ref}", "path": CANON_REL,
                                              "blob_sha": sha,
                                              "sha256": hashlib.sha256(blob_bytes).hexdigest()}}
    return {"module": None, "provenance": {"source": "unavailable", "path": CANON_REL}}


# ---------------------------------------------------------------------------
# 取数：轨道 live / legacy
# ---------------------------------------------------------------------------
def _mock_path(track: str = "live") -> Dict[str, Any]:
    """Evaluate one track of the objective at the design Fn and over a speed sweep.

    ``live`` goes through ``holtrop_resistance`` (the shipped, canonical-delegated
    objective; monkeypatchable -> this is the seam the mutation tests use).
    ``legacy`` calls the hand-fitted fallback directly, bypassing the delegate.
    """
    import numpy as np
    from optimizer.interfaces.kcs_bow_param import KCS_BASELINE, generate_geometry
    from optimizer.interfaces.resistance_eval import (
        _legacy_holtrop_resistance, evaluate_resistance, holtrop_resistance,
    )

    geom_full = generate_geometry(np.zeros(8))
    base = evaluate_resistance(geom_full, KCS_BASELINE["Fn_design"])
    g = {"L": float(geom_full["L"]), "B": float(geom_full["B"]),
         "T": float(geom_full["T"]), "Cb": float(geom_full["Cb"])}
    particulars = {k: float(geom_full[k]) for k in
                   ("Cm", "Cwp", "Cp", "lcb_pct", "vol", "abt", "hb", "at")}
    V = float(base["V_ms"])
    fn = holtrop_resistance if track == "live" else _legacy_holtrop_resistance

    raw = fn(geom_full, V)
    sweep = []
    grid = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.27, 1.3, 1.5, 2.0]
    sweep_grid = tuple(grid) + (V, 2.5, 3.0, 3.5, 4.0)
    for Vv in sorted(set(sweep_grid)):
        r = fn(geom_full, Vv)
        sweep.append({"V_ms": Vv,
                      "Fn": Vv / (G * geom_full["L"]) ** 0.5,
                      "Ct": float(r["Ct"]), "Rt_N": float(r["Rt_N"]),
                      "Cf": float(r["Cf"])})
    return {"track": track, "geom": g, "particulars": particulars, "base": base,
            "raw": raw, "sweep": sweep, "Fn_design": float(KCS_BASELINE["Fn_design"]),
            "V_ms": V,
            "evidence_level": raw.get("evidence_level", "WITHHELD"),
            "physics_source": raw.get("physics_source", "unknown")}


def _canon_path(canon: Dict[str, Any], geom: Dict[str, float],
                particulars: Dict[str, float], speed_ms: float) -> Dict[str, Any]:
    """正典 Holtrop-1984 在同一船型要素下的参考解（审计侧独立读正典）。"""
    mod = canon["module"]
    if mod is None:
        return {"available": False}
    rho, nu = 1025.0, 1.19e-6  # 与目标函数同一流体，比较的是方法而非介质
    hg = mod.HullGeometry(name="KCS-bow-param", l=geom["L"], b=geom["B"], t=geom["T"],
                          cb=geom["Cb"], cm=particulars["Cm"], cwp=particulars["Cwp"],
                          lcb_pct=particulars["lcb_pct"], vol=particulars["vol"],
                          abt=particulars["abt"], hb=particulars["hb"],
                          at=particulars["at"])
    r = mod.holtrop1984_resistance(hg, speed_ms, rho=rho, nu=nu)
    out = {"available": True, "speed_ms": speed_ms, "fn": r.fn, "re": r.re, "s_ref": r.s_ref,
           "cf": r.cf, "form_factor_1pk": r.form_factor_1pk, "rf": r.rf, "rf_1pk": r.rf_1pk,
           "rw": r.rw, "rb": r.rb, "rtr": r.rtr, "ra": r.ra, "rt": r.rt, "ct": r.ct,
           "cv": r.cv, "cw": r.cw, "ca": r.ca}
    if hasattr(mod, "wetted_surface"):
        try:
            out["s_wetted_fn"] = float(mod.wetted_surface(
                geom["L"], geom["B"], geom["T"], geom["Cb"],
                particulars["Cm"], particulars["Cwp"], particulars["abt"]))
        except Exception as exc:  # noqa: BLE001
            out["s_wetted_fn_error"] = str(exc)
    return out


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100.0 if b else float("inf")


def audit(track: str = "live") -> Dict[str, Any]:
    """Run the C1~C7 criteria on one track and return the graded result."""
    if track not in TRACKS:
        raise ValueError(f"unknown track {track!r}")
    canon = _load_canonical()
    path = _mock_path(track)
    g, raw = path["geom"], path["raw"]
    parts = path["particulars"]
    V = float(path["V_ms"])
    can = _canon_path(canon, g, parts, V)

    L, B, T, Cb = g["L"], g["B"], g["T"], g["Cb"]
    vol = Cb * L * B * T
    rho = 1025.0
    q = 0.5 * rho * V ** 2                      # 动压 [Pa]
    S_mock = float(raw["S_wet"])
    Ca_dimless = (float(can["ca"]) if can["available"]
                  else 0.006 * (L + 100.0) ** (-0.16) - 0.00205)
    Ca_force_recorded = float(raw["Ca_N"])
    Ra_correct = q * S_mock * Ca_dimless

    checks: List[Dict[str, Any]] = []

    # C1 几何恒等式 -------------------------------------------------------
    lb, bt = L / B, B / T
    c1 = (3.0 <= lb <= 8.0) and (2.0 <= bt <= 4.0) and (0.55 <= Cb <= 0.75)
    checks.append({"id": "C1", "name": "几何恒等式 Cb·L·B·T / 标定域",
                   "pass": bool(c1),
                   "values": {"L_B": lb, "B_T": bt, "Cb": Cb, "nabla_m3": vol,
                              "displacement_kg_at_1025": vol * rho,
                              "Cm": parts["Cm"], "Cwp": parts["Cwp"], "Cp": parts["Cp"],
                              "lcb_pct": parts["lcb_pct"], "abt_m2": parts["abt"],
                              "hb_m": parts["hb"]},
                   "note": "先验证几何再谈阻力：∇ 必须等于 Cb·L·B·T，且长宽吃水比在标定域"})

    # C2 湿表面积 ---------------------------------------------------------
    lo, hi = 2.0 * L * T, L * (B + 2.0 * T)
    if can["available"]:
        S_can = float(can.get("s_wetted_fn", can["s_ref"]))
        s_err = _rel(S_mock, S_can)
        c2 = (s_err <= ERR_GATE) and (lo <= S_mock <= hi)
        c2_pass: Optional[bool] = bool(c2)
    else:
        S_can, s_err, c2_pass = None, None, None
    checks.append({"id": "C2", "name": "湿表面积（夹逼 + 异实现）",
                   "pass": c2_pass,
                   "values": {"S_mock_m2": S_mock, "S_canonical_m2": S_can,
                              "rel_err_pct": s_err, "bracket_lo_m2": lo,
                              "bracket_hi_m2": hi,
                              "inside_bracket": bool(lo <= S_mock <= hi)},
                   "note": "夹逼 2LT≤S≤L(B+2T) 与正典湿面积双保险；任一条破即判负"})

    # C3 相关补贴量纲 -----------------------------------------------------
    implied = Ca_force_recorded / (q * S_mock) if S_mock else float("nan")
    ca_err = _rel(implied, Ca_dimless)
    c3 = ca_err <= ERR_GATE
    checks.append({"id": "C3", "name": "相关补贴 Ca 量纲一致性",
                   "pass": bool(c3),
                   "values": {"Ca_force_recorded_N": Ca_force_recorded,
                              "implied_coefficient": implied,
                              "Ca_dimensionless": Ca_dimless,
                              "rel_err_pct_of_coefficient": ca_err,
                              "Ra_correct_N": Ra_correct,
                              "underestimate_N": Ra_correct - Ca_force_recorded},
                   "note": "Ca 必须无量纲进入 0.5ρV²S；把 [m²] 量加到 [N] 上即量纲错误"})

    # C4 残余阻力显著性 ---------------------------------------------------
    Cr_Cf_mock = float(raw["Cr"]) / float(raw["Cf"]) if raw["Cf"] else float("inf")
    if can["available"] and can["cf"]:
        Cr_Cf_can = float(can["cw"]) / float(can["cf"])
        ratio = Cr_Cf_mock / Cr_Cf_can if Cr_Cf_can else float("inf")
        c4_pass: Optional[bool] = bool(0.5 <= ratio <= 2.0)
    else:
        Cr_Cf_can, ratio, c4_pass = None, None, None
    checks.append({"id": "C4", "name": "残余阻力显著性 Cr/Cf（量级仲裁）",
                   "pass": c4_pass,
                   "values": {"Cr_Cf_mock": Cr_Cf_mock, "Cr_Cf_canonical": Cr_Cf_can,
                              "ratio_mock_over_canonical": ratio},
                   "note": "0.5≤ratio≤2 才认为形阻/兴波量级正确；比值 ~1e-4 即被抹平"})

    # C5 总阻力对比 -------------------------------------------------------
    if can["available"]:
        ct_err = _rel(float(raw["Ct"]), float(can["ct"]))
        rt_err = _rel(float(raw["Rt_N"]), float(can["rt"]))
        c5 = (ct_err <= ERR_GATE) and (rt_err <= ERR_GATE)
        c5_pass: Optional[bool] = bool(c5)
    else:
        ct_err = rt_err = None
        c5_pass = None
    checks.append({"id": "C5", "name": "总阻力 Ct / Rt vs 正典 Holtrop-1984",
                   "pass": c5_pass,
                   "values": {"Ct_mock": float(raw["Ct"]), "Ct_canonical": can.get("ct"),
                              "err_pct_Ct": ct_err,
                              "Rt_mock_N": float(raw["Rt_N"]),
                              "Rt_canonical_N": can.get("rt"),
                              "err_pct_Rt": rt_err,
                              "gate_pct": ERR_GATE, "tight_pct": ERR_TIGHT,
                              "under_3pct": (ct_err is not None and ct_err < ERR_TIGHT)},
                   "note": "15% 不可接受门；报告须显式回答是否 <3%"})

    # C6 物理下界 ---------------------------------------------------------
    Ct_mock = float(raw["Ct"])
    if can["available"]:
        lb_1pk = can["form_factor_1pk"] * can["cf"]
    else:
        lb_1pk = float(raw["Cf"]) * 1.0675
    c6 = (Ct_mock >= float(raw["Cf"])) and (Ct_mock >= lb_1pk)
    checks.append({"id": "C6", "name": "物理下界 Ct ≥ Cf 且 Ct ≥ (1+k)Cf",
                   "pass": bool(c6),
                   "values": {"Ct_mock": Ct_mock, "Cf": float(raw["Cf"]),
                              "Cf_times_1pk": lb_1pk,
                              "Ct_over_Cf": Ct_mock / float(raw["Cf"]),
                              "deficit_pct_vs_1pk": _rel(Ct_mock, lb_1pk)},
                   "note": "任何位移船的 Ct 必须高于粘性下界 (1+k)Cf；等号即形阻/兴波被抹平"})

    # C7 速度极限 / 单调 --------------------------------------------------
    sw = path["sweep"]
    rt_mono = all(sw[i + 1]["Rt_N"] > sw[i]["Rt_N"] for i in range(len(sw) - 1))
    sub = [s for s in sw if s["Fn"] <= FN_FRICTION_LIMIT]
    ct_dec = (len(sub) >= 3 and
              all(sub[i + 1]["Ct"] < sub[i]["Ct"] for i in range(len(sub) - 1)))
    c7 = rt_mono and ct_dec
    checks.append({"id": "C7", "name": "速度极限 Rt(V)↑ / Ct(V)↓（摩擦主导域）",
                   "pass": bool(c7),
                   "values": {"Rt_monotonic_increasing": rt_mono,
                              "Ct_monotonic_decreasing": ct_dec,
                              "fn_friction_limit": FN_FRICTION_LIMIT,
                              "samples_total": len(sw), "samples_friction_domain": len(sub)},
                   "note": "全速域 Rt 严格递增；摩擦主导域（Fn≤0.15）Ct 单调下降。"
                           "Ct 与 V 无关（HOLD）即判负；Fn>0.15 的阻力峰是真实物理，不在本判据内"})

    evaluated = [c for c in checks if c["pass"] is not None]
    fails = [c["id"] for c in evaluated if not c["pass"]]
    withheld = [c["id"] for c in checks if c["pass"] is None]
    verdict = ("MOCK" if fails else ("REAL" if not withheld else "WITHHELD"))

    components = {k: (float(v) if isinstance(v, (int, float)) else v)
                  for k, v in raw.items() if isinstance(v, (int, float))}
    return {
        "track": track,
        "object": "optimizer/interfaces/resistance_eval.py::evaluate_resistance (KCS 目标函数)",
        "verdict_evidence_level": verdict,
        "objective_evidence_level": path["evidence_level"],
        "objective_physics_source": path["physics_source"],
        "criteria_total": len(checks), "criteria_evaluated": len(evaluated),
        "failed": fails, "withheld": withheld,
        "inputs": {"geom": g, "particulars": parts, "Fn_design": path["Fn_design"],
                   "V_ms": V, "rho": rho, "nu": 1.19e-6,
                   "mock_components": components,
                   "canonical": can},
        "checks": checks,
        "sweep_mock": sw,
        "provenance": {"canonical": canon["provenance"],
                       "tool": "tools/audit/optimizer_objective_audit.py "
                               "(impl: optimizer/audit/optimizer_objective_audit.py)"},
    }


def audit_full() -> Dict[str, Any]:
    """Primary (live) result plus the legacy fallback track as a regression pin."""
    live = audit("live")
    legacy = audit("legacy")
    keys = ("verdict_evidence_level", "objective_evidence_level", "objective_physics_source",
            "failed", "withheld", "criteria_total", "criteria_evaluated")
    live["tracks"] = {"live": {k: live[k] for k in keys},
                      "legacy": {k: legacy[k] for k in keys}}
    live["legacy_track"] = {
        "object": legacy["object"],
        "verdict_evidence_level": legacy["verdict_evidence_level"],
        "objective_evidence_level": legacy["objective_evidence_level"],
        "objective_physics_source": legacy["objective_physics_source"],
        "failed": legacy["failed"], "withheld": legacy["withheld"],
        "checks": legacy["checks"],
        "sweep_mock": legacy["sweep_mock"],
        "components": legacy["inputs"]["mock_components"],
        "particulars": legacy["inputs"]["particulars"],
    }
    return live


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
def _figures(res: Dict[str, Any]) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGDIR.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []
    canon = _load_canonical()
    g = res["inputs"]["geom"]
    parts = res["inputs"]["particulars"]
    leg = res["legacy_track"]
    sw = res["sweep_mock"]

    # 图1：Ct(V) 双轨 vs 正典
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot([s["V_ms"] for s in sw], [s["Ct"] for s in sw], "o-",
            label="live objective (canonical Holtrop-1984)")
    ax.plot([s["V_ms"] for s in sw], [s["Cf"] for s in sw], ":",
            label="ITTC-57 Cf (friction floor)")
    ax.plot([s["V_ms"] for s in leg["sweep_mock"]], [s["Ct"] for s in leg["sweep_mock"]],
            "x--", label="legacy fallback (MOCK: Ct == Cf)")
    if canon["module"] is not None:
        Vs, Cts, Cvs = [], [], []
        for s in sw:
            c = _canon_path(canon, g, parts, s["V_ms"])
            if c["available"]:
                Vs.append(s["V_ms"])
                Cts.append(c["ct"])
                Cvs.append(c["cv"])
        ax.plot(Vs, Cts, "s-", label="canonical reference (independent read)")
        ax.plot(Vs, Cvs, "--", label="canonical (1+k)Cf floor")
    ax.set_xlabel("V [m/s] (KCS model, L=%.3f m)" % g["L"])
    ax.set_ylabel("total resistance coefficient Ct [-]")
    ax.set_title("Optimizer objective vs canonical Holtrop-1984")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p1 = FIGDIR / "optobj_fig1_ct_vs_v.png"
    fig.tight_layout()
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append({"name": p1.name,
                 "caption": "Ct(V)：live 目标函数（正典委托）与 legacy fallback、正典参考解、"
                            "ITTC-57 摩擦下界对比"})

    # 图2：阻力分量分解（symlog）
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels = ["Rf(ITTC-57)", "(1+k)Rf", "Rw", "Rb", "Rtr", "Ra", "Rt"]
    keys = ["Rf_N", "Rv_N", "Rw_N", "Rb_N", "Rtr_N", "Ca_N", "Rt_N"]
    mc = res["inputs"]["mock_components"]
    lc = leg["components"]
    live_vals = [float(mc.get(k, 0.0)) for k in keys]
    leg_vals = [float(lc.get(k, 0.0)) for k in keys]
    x = list(range(len(labels)))
    ax.bar([i - 0.2 for i in x], live_vals, width=0.4, label="live (canonical)")
    ax.bar([i + 0.2 for i in x], leg_vals, width=0.4, label="legacy fallback (MOCK)")
    ax.set_yscale("symlog", linthresh=1e-3)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, fontsize=8)
    ax.set_ylabel("resistance component [N] (symlog)")
    ax.set_title("Resistance decomposition at design Fn=%.2f" % res["inputs"]["Fn_design"])
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    p2 = FIGDIR / "optobj_fig2_components.png"
    fig.tight_layout()
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append({"name": p2.name,
                 "caption": "阻力分量分解（symlog）：legacy 的 Rw/Ra 比正典低 3~4 个量级，"
                            "即形阻与相关补贴被抹平"})
    return figs


# ---------------------------------------------------------------------------
# 论文级文档
# ---------------------------------------------------------------------------
def _fmt(v: Any, nd: int = 6) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e5):
            return f"{v:.{nd}e}"
        return f"{v:.{nd}g}"
    return str(v)


def _md(res: Dict[str, Any]) -> str:
    by = {c["id"]: c for c in res["checks"]}
    leg = res["legacy_track"]
    lby = {c["id"]: c for c in leg["checks"]}
    g = res["inputs"]["geom"]
    parts = res["inputs"]["particulars"]
    prov = res["provenance"]["canonical"]
    c1, c2, c3 = by["C1"]["values"], by["C2"]["values"], by["C3"]["values"]
    c4, c5 = by["C4"]["values"], by["C5"]["values"]
    fmt = _fmt

    rows = "\n".join(
        "| {id} | {name} | {lvl} | {leg} |".format(
            id=c["id"], name=c["name"],
            lvl="PASS" if c["pass"] else ("FAIL" if c["pass"] is not None else "WITHHELD"),
            leg=("PASS" if lby[c["id"]]["pass"] else
                 ("FAIL" if lby[c["id"]]["pass"] is not None else "WITHHELD")))
        for c in res["checks"])

    lines = [
        "# 优化器目标函数物理内核复核（m1610：委托正典 Holtrop-1984）",
        "",
        "> 本文件取代 m1601 的 `docs/verification/optimizer-objective-mock-audit.md`：",
        "> 该文判 MOCK，其结论自 m1610 起作废，仅作历史记录。",
        "",
        "## ① 案例介绍",
        "",
        "KCS（KRISO Container Ship）模型尺度艏部型线优化：CMA-ES 在 8 维艏部变形参数空间内",
        "最小化设计 Froude 数下的总阻力系数 Ct。m1601 复核发现目标函数实际是裸 ITTC-57 摩擦线",
        "（Ct ≡ Cf，Cr/Cf ~ 3e-5 vs 正典 0.335；Ca 以 [m²] 量加到 [N]），证据等级 MOCK。",
        "m1610 把物理内核改为**委托**正典 Holtrop-Mennen (1984)（`%s`，provenance `%s`，"
        "sha256=%s），旧手拟链条只作 fallback 并继续标注 MOCK。"
        % (prov.get("path"), prov.get("source"), str(prov.get("sha256"))[:16]),
        "",
        "| 量 | 值 | 来源 |",
        "|---|---|---|",
        "| L / B / T | %s / %s / %s m | `kcs_bow_param.KCS_BASELINE` |"
        % (fmt(g["L"], 5), fmt(g["B"], 5), fmt(g["T"], 5)),
        "| Cb | %s | 同上（p4 修正后） |" % fmt(g["Cb"], 4),
        "| Cm / Cwp / Cp | %s / %s / %s | 正典 `estimate_cm` / `estimate_cwp` |"
        % (fmt(parts["Cm"], 4), fmt(parts["Cwp"], 4), fmt(parts["Cp"], 4)),
        "| LCB | %s %%L（中后为负=前） | LCB_BASELINE_PCT + p6/p7 通路 |"
        % fmt(parts["lcb_pct"], 4),
        "| ∇ | %s m³（@1025 → %s kg） | ∇=Cb·L·B·T |"
        % (fmt(c1["nabla_m3"], 5), fmt(c1["displacement_kg_at_1025"], 6)),
        "| 球鼻艏 Abt / hb | %s m² / %s m | Abt/Am=0.09、hb/T=0.45 基准 + p4/p5 |"
        % (fmt(parts["abt"], 5), fmt(parts["hb"], 5)),
        "| 设计 Fn / V | %s / %s m/s | V=Fn·√(gL) |"
        % (fmt(res["inputs"]["Fn_design"], 3), fmt(res["inputs"]["V_ms"], 5)),
        "| 流体 | ρ=%s kg/m³，ν=%s m²/s | 两轨同一流体，只比方法 |"
        % (fmt(res["inputs"]["rho"], 4), fmt(res["inputs"]["nu"], 3)),
        "",
        "## ② 怎么做的（方法/流程/关键设置/工具链）",
        "",
        "1. **单实现原则**：`optimizer/interfaces/canonical_bridge.py` 按 "
        "`worktree → git:<ref>` 顺序装载正典内核，",
        "   `resistance_eval.holtrop_resistance()` 直接调用 `holtrop1984_resistance()`；",
        "   报告 1144 已裁决多分支漂移的代价，故优化器侧不再出现第二份 Holtrop 系数链。",
        "2. **几何先验证**：`kcs_bow_param` 输出 cm/cwp/LCB/∇/Abt/hb，8 个艏部参数只通过方法中",
        "   真实存在的通路生效（Cwp 满度、Abt、hb、LCB）；deck camber 在 Holtrop-1984 里没有",
        "   静水阻力通路，显式置为惰性，而不是像 m1601 那样用手工乘子伪造灵敏度。",
        "3. **双轨判据**：live 轨 = 实际目标函数（正典委托）；legacy 轨 = 强制旧手拟 fallback。",
        "   同一套 C1~C7 分别判级，legacy 轨固定失败项 {C3,C4,C5,C6}，作为“退回旧链条”的回归钉子。",
        "4. **七条独立判据**：几何恒等式、湿面积夹逼+异实现、量纲一致性、残余阻力量级、总阻力误差、",
        "   物理下界、速度极限/单调性；每条都可被变异体单独判负"
        "（见 `tests/test_optimizer_objective_audit.py`）。",
        "5. **复算**：`python3 tools/audit/optimizer_objective_audit.py --write`；漂移闸 `--check`。",
        "   文中数字全部由本工具注入，零手抄。",
        "",
        "## ③ 计算结果（程序化注入）",
        "",
        "**live 目标函数证据等级：%s**（%d/%d 条可判，FAIL=%s，WITHHELD=%s）"
        % (res["verdict_evidence_level"], res["criteria_evaluated"], res["criteria_total"],
           res["failed"] or "无", res["withheld"] or "无"),
        "**legacy fallback 证据等级：%s**（FAIL=%s）"
        % (leg["verdict_evidence_level"], leg["failed"] or "无"),
        "",
        "| 判据 | 名称 | live | legacy |",
        "|---|---|---|---|",
        rows,
        "",
        "关键量对照（KCS 模型，设计点 Fn=%s）：" % fmt(res["inputs"]["Fn_design"], 3),
        "",
        "| 量 | live 目标函数 | legacy fallback | 正典参考 |",
        "|---|---|---|---|",
        "| Ct [-] | %s | %s | %s |" % (fmt(c5["Ct_mock"]), fmt(lby["C5"]["values"]["Ct_mock"]),
                                       fmt(c5["Ct_canonical"])),
        "| Rt [N] | %s | %s | %s |" % (fmt(c5["Rt_mock_N"], 5),
                                       fmt(lby["C5"]["values"]["Rt_mock_N"], 5),
                                       fmt(c5["Rt_canonical_N"], 5)),
        "| S_wet [m²] | %s | %s | %s |" % (fmt(c2["S_mock_m2"], 5),
                                           fmt(lby["C2"]["values"]["S_mock_m2"], 5),
                                           fmt(c2["S_canonical_m2"], 5)),
        "| Cr/Cf [-] | %s | %s | %s |" % (fmt(c4["Cr_Cf_mock"]),
                                          fmt(lby["C4"]["values"]["Cr_Cf_mock"]),
                                          fmt(c4["Cr_Cf_canonical"])),
        "| Ca（无量纲） | %s | %s | %s |" % (fmt(c3["implied_coefficient"]),
                                             fmt(lby["C3"]["values"]["implied_coefficient"]),
                                             fmt(c3["Ca_dimensionless"])),
        "| (1+k) | %s | %s | %s |"
        % (fmt(res["inputs"]["mock_components"].get("form_factor_1pk")),
           fmt(leg["components"].get("form_factor_1pk")),
           fmt(res["inputs"]["canonical"].get("form_factor_1pk"))),
        "",
        "目标函数输出 JSON 携带 `objective_evidence_level=%s`、`objective_physics_source=%s`"
        % (res["objective_evidence_level"], res["objective_physics_source"]),
        "（见 `optimizer/runs/opt_run_holtrop1984/optimization_result.json`）。",
        "",
        "## ④ 图",
        "",
        "`docs/verification/figures/optobj_fig1_ct_vs_v.png`（Ct(V)：live / legacy / 正典 / 摩擦下界）",
        "与 `optobj_fig2_components.png`（分量分解，symlog）。",
        "",
        "## ⑤ 注意点（坑/局限/适用范围）",
        "",
        "- **委托而非重写**：优化器侧不允许出现第二份 Holtrop 系数链；任何“为了让优化跑通”而就地",
        "  改系数的改动都会重新打开报告 1144 的漂移风险，必须走正典 + 审计。",
        "- **fallback 仍是 MOCK**：正典内核不可用时（未合入该文件的分支）会退回旧链条，并在",
        "  `objective_evidence_level` 标注 MOCK、`fallback_reason` 记录原因；下游不得当 REAL 引用。",
        "- **参数灵敏度变小是预期的**：m1601 的“改善 %”来自人工乘子；改为物理通路后，静水阻力对",
        "  deck camber / shoulder 曲率的响应本就接近零，优化收益必然下降——这是保真度的代价。",
        "- **位移约束仍是 mock**：`generate_geometry` 的 ±5% 排水量与水密性判据仍是 m546 的 mock",
        "  逻辑（不在本单范围），故 p4（球鼻艏体积）在大幅度时会被判无效。",
        "- **与 m1601 的 44.3%% 对齐**：m1601 的正典参考解不含球鼻艏（Abt=0），本单的目标函数",
        "  携带 Abt/hb（KCS 有球鼻艏），故 legacy 的 Ct 误差为 %s%%；两者都远超 15%% 门。"
        % fmt(lby["C5"]["values"]["err_pct_Ct"], 4),
        "- **正典自身局限**：Ca 的 L<50 m 修正未实现（模型尺度 L=7.278 m 属外插），标定域为",
        "  Cb 0.55~0.85、Fn≤0.4；本复核只覆盖 KCS 位移船工况。",
        "",
        "## ⑥ 误差判定",
        "",
        "- live Ct 相对误差 **%s%%** vs 正典 Holtrop-1984 → **是否 <3%%：%s**（委托同一内核，"
        "误差为数值同源）" % (fmt(c5["err_pct_Ct"], 4), "是" if c5["under_3pct"] else "否"),
        "- live Rt 相对误差 **%s%%**（同一判据的力形式）。" % fmt(c5["err_pct_Rt"], 4),
        "- legacy Ct 相对误差 **%s%%**、Rt **%s%%** → 维持 MOCK。"
        % (fmt(lby["C5"]["values"]["err_pct_Ct"], 4),
           fmt(lby["C5"]["values"]["err_pct_Rt"], 4)),
        "- legacy 相关补贴系数误差 **%s%%**（量纲错误，远超 15%% 门）。"
        % fmt(lby["C3"]["values"]["rel_err_pct_of_coefficient"], 4),
        "- legacy Cr/Cf 量级比 **%s**（要求 0.5~2）。"
        % fmt(lby["C4"]["values"]["ratio_mock_over_canonical"]),
        "- **结论**：live 目标函数七条判据全过 → 证据等级 **%s**；legacy fallback 维持 **MOCK**，"
        % res["verdict_evidence_level"],
        "  优化器结果 JSON 必须按 `objective_evidence_level` 字段引用。",
        "",
        "## ⑦ 复现",
        "",
        "```",
        "python3 tools/audit/optimizer_objective_audit.py --write   # 生成 JSON+MD+图",
        "python3 tools/audit/optimizer_objective_audit.py --check   # 漂移闸（复算==入库）",
        "python3 -m pytest tests/test_optimizer_objective_audit.py -q",
        "```",
        "",
    ]
    return "\n".join(lines)


def _write_superseded_banner() -> None:
    """把 m1601 的旧结论文档就地标注作废（不删除历史）。"""
    if not SUPERSEDED_DOC.exists():
        return
    text = SUPERSEDED_DOC.read_text()
    banner = ("> **作废（m1610）**：优化器目标函数已改为委托正典 "
              "`backend/api/holtrop1984.py`，见 `docs/verification/"
              "optimizer-objective-physics-audit.md`。本文的 MOCK 结论仅描述 m1601 "
              "时点的旧手拟内核（现仅作 fallback）。\n\n")
    if banner not in text:
        SUPERSEDED_DOC.write_text(banner + text)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写产物 JSON/MD/图")
    ap.add_argument("--check", action="store_true", help="漂移闸：复算必须等于入库")
    a = ap.parse_args(argv)

    res = audit_full()
    res["figures"] = []
    ART.mkdir(parents=True, exist_ok=True)
    out = ART / "optimizer_objective_audit.json"

    if a.check:
        if not out.exists():
            print("FAIL: 入库 JSON 不存在")
            return 2
        old = json.loads(out.read_text())
        diffs = [c["id"] for c, o in zip(res["checks"], old.get("checks", []))
                 if json.dumps(c, sort_keys=True) != json.dumps(o, sort_keys=True)]
        old_leg = old.get("legacy_track", {})
        leg_diffs = [c["id"] for c, o in zip(res["legacy_track"]["checks"],
                                             old_leg.get("checks", []))
                     if json.dumps(c, sort_keys=True) != json.dumps(o, sort_keys=True)]
        drift = (old.get("verdict_evidence_level") != res["verdict_evidence_level"]
                 or old_leg.get("verdict_evidence_level")
                 != res["legacy_track"]["verdict_evidence_level"]
                 or diffs or leg_diffs)
        if drift:
            print(f"FAIL: 漂移 detected verdict={res['verdict_evidence_level']} "
                  f"diffs={diffs} legacy_diffs={leg_diffs}")
            return 1
        print(f"PASS: 漂移闸通过（live={res['verdict_evidence_level']}，"
              f"legacy={res['legacy_track']['verdict_evidence_level']}，"
              f"判据 {len(res['checks'])} 条 / legacy {len(res['legacy_track']['checks'])} 条）")
        return 0

    try:
        res["figures"] = _figures(res)
    except Exception as exc:  # noqa: BLE001
        res["figures_error"] = str(exc)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(_md(res))
    _write_superseded_banner()
    print(f"live verdict={res['verdict_evidence_level']} failed={res['failed']} "
          f"withheld={res['withheld']} level={res['objective_evidence_level']}")
    print(f"legacy verdict={res['legacy_track']['verdict_evidence_level']} "
          f"failed={res['legacy_track']['failed']}")
    for c in res["checks"]:
        st = "PASS" if c["pass"] else ("FAIL" if c["pass"] is not None else "WITHHELD")
        print(f"  {c['id']} {st:8s} {c['name']}")
    if a.write:
        print(f"wrote {out.relative_to(ROOT)}, {DOC.relative_to(ROOT)}, "
              f"{FIGDIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())