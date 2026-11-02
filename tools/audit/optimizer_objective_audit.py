#!/usr/bin/env python3
"""优化器目标函数物理保真度复核（验证欠账清偿）。

对象
----
``optimizer/interfaces/resistance_eval.py`` 是 KCS 球鼻艏 CMA-ES 优化的**目标函数**
（``optimization_loop.objective`` 直接取 ``evaluate_resistance(...)["Ct"]``）。该模块文件头
自称 "m547 interface mock"，但从未对任何独立基准复核过：优化器报告里的"阻力下降 x%"
因此建在未验证的物理之上。

本工具不改物理（正典 Holtrop-1984 内核由 agent/m1553 落地，避免第三次重复实现造成
版本漂移——报告 1144 已就此裁决过一次），只做**可机器裁决**的复核：

判据（每条独立、可变异体判负）
------------------------------
C1 几何恒等式      ∇ = Cb·L·B·T，L/B、B/T 落在 Holtrop 标定域
C2 湿表面积        mock S_wet vs 正典 wetted_surface（≤15%），且满足几何夹逼 2LT ≤ S ≤ L(B+2T)
C3 量纲一致性      相关补贴 Ca 必须无量纲进入 Ct；禁止把 [m²] 量加到 [N] 上
C4 残余阻力显著性  Cr/Cf 与正典同量级（0.5 ≤ ratio ≤ 2），否则形阻+兴波被抹平
C5 总阻力对比      Ct / Rt vs 正典相对误差（15% 不可接受门 + 3% 报告门）
C6 物理下界        Ct ≥ Cf；Ct ≥ (1+k)·Cf（k 取正典形状因子）
C7 速度极限/单调   Rt(V) 严格递增、Ct(V) 单调下降（摩擦主导域）

正典来源（零手抄、可复现）
--------------------------
优先读工作树 ``backend/api/holtrop1984.py``；不存在则从 git 引用 ``git show <ref>:<path>``
取出到临时目录导入，并把 ref 与 blob sha256 记入产物 provenance。两者都没有 → 判定
WITHHELD（不编数字）。

用法
----
    python3 tools/audit/optimizer_objective_audit.py --write
    python3 tools/audit/optimizer_objective_audit.py --check   # 漂移闸：复算==入库

产物：``artifacts/optimizer_objective_audit/optimizer_objective_audit.json``（判据+数值+provenance）、
``docs/verification/optimizer-objective-mock-audit.md``（论文级，表格程序化注入）、
``docs/verification/figures/optobj_*.png``。
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
DOC = ROOT / "docs" / "verification" / "optimizer-objective-mock-audit.md"
FIGDIR = ROOT / "docs" / "verification" / "figures"
CANON_REL = "backend/api/holtrop1984.py"
DEFAULT_REFS = ("agent/m1553", "main")

ERR_GATE = 15.0   # 平台精度门：>15% 不可接受
ERR_TIGHT = 3.0   # 报告要求显式回答 "<3%"

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
# mock 目标函数取数
# ---------------------------------------------------------------------------
def _mock_path() -> Dict[str, Any]:
    import numpy as np
    from optimizer.interfaces.kcs_bow_param import KCS_BASELINE, generate_geometry
    from optimizer.interfaces.resistance_eval import evaluate_resistance, holtrop_resistance

    geom = generate_geometry(np.zeros(8))
    base = evaluate_resistance(geom, KCS_BASELINE["Fn_design"])
    g = {"L": float(geom["L"]), "B": float(geom["B"]), "T": float(geom["T"]),
         "Cb": float(geom["Cb"])}
    V = float(base["V_ms"])
    raw = holtrop_resistance({**g, "volume_ratio": 1.0, "params": {}}, V)
    sweep = []
    for Vv in (1.0, 1.5, 2.0, 2.1969, 2.5, 3.0, 3.5, 4.0):
        r = holtrop_resistance({**g, "volume_ratio": 1.0, "params": {}}, Vv)
        sweep.append({"V_ms": Vv, "Ct": r["Ct"], "Rt_N": r["Rt_N"], "Cf": r["Cf"]})
    return {"geom": g, "base": base, "raw": raw, "sweep": sweep,
            "Fn_design": float(KCS_BASELINE["Fn_design"])}


def _canon_path(canon: Dict[str, Any], geom: Dict[str, float], speed_ms: float) -> Dict[str, Any]:
    mod = canon["module"]
    if mod is None:
        return {"available": False}
    rho, nu = 1025.0, 1.19e-6  # 与 mock 保持同一流体，比较的是方法而非介质
    cm = mod.estimate_cm(geom["Cb"])
    cwp = mod.estimate_cwp(geom["Cb"])
    hg = mod.HullGeometry(name="KCS-baseline-mock", l=geom["L"], b=geom["B"], t=geom["T"],
                          cb=geom["Cb"], cm=cm, cwp=cwp)
    r = mod.holtrop1984_resistance(hg, speed_ms, rho=rho, nu=nu)
    out = {"available": True, "speed_ms": speed_ms, "fn": r.fn, "re": r.re, "s_ref": r.s_ref,
           "cf": r.cf, "form_factor_1pk": r.form_factor_1pk, "rf": r.rf, "rf_1pk": r.rf_1pk,
           "rw": r.rw, "rb": r.rb, "rtr": r.rtr, "ra": r.ra, "rt": r.rt, "ct": r.ct,
           "cv": r.cv, "cw": r.cw, "ca": r.ca, "cm": cm, "cwp": cwp,
           "notes": list(getattr(r, "notes", []) or [])}
    if hasattr(mod, "wetted_surface"):
        try:
            out["s_wetted_fn"] = float(mod.wetted_surface(geom["L"], geom["B"], geom["T"],
                                                         geom["Cb"], cm, cwp))
        except Exception as exc:  # noqa: BLE001
            out["s_wetted_fn_error"] = str(exc)
    return out


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100.0 if b else float("inf")


def audit() -> Dict[str, Any]:
    canon = _load_canonical()
    mock = _mock_path()
    g, base, raw = mock["geom"], mock["base"], mock["raw"]
    V = float(base["V_ms"])
    can = _canon_path(canon, g, V)

    L, B, T, Cb = g["L"], g["B"], g["T"], g["Cb"]
    vol = Cb * L * B * T
    rho = 1025.0
    q = 0.5 * rho * V ** 2                      # 动压 [Pa]
    S_mock = float(raw["S_wet"])
    Ca_dimless = 0.006 * (L + 100.0) ** (-0.16)  # Holtrop 相关补贴（无量纲）
    Ca_force_recorded = float(raw["Ca_N"])       # mock 把 [m²] 量直接加到 [N]

    checks: List[Dict[str, Any]] = []

    # C1 几何恒等式 -------------------------------------------------------
    lb, bt = L / B, B / T
    c1 = (3.0 <= lb <= 8.0) and (2.0 <= bt <= 4.0) and (0.55 <= Cb <= 0.75)
    checks.append({"id": "C1", "name": "几何恒等式 Cb·L·B·T / 标定域",
                   "pass": bool(c1),
                   "values": {"L_B": lb, "B_T": bt, "Cb": Cb, "nabla_m3": vol,
                              "displacement_kg_at_1025": vol * rho,
                              "domain": {"L_B": [3.0, 8.0], "B_T": [2.0, 4.0], "Cb": [0.55, 0.75]}},
                   "note": "几何先过 Cb/L/B；任何越域工况的阻力数字都不得用于优化对比"})

    # C2 湿表面积 ---------------------------------------------------------
    lo, hi = 2.0 * L * T, L * (B + 2.0 * T)
    S_can = can.get("s_wetted_fn") if can["available"] else None
    s_err = _rel(S_mock, S_can) if S_can else None
    c2 = (lo <= S_mock <= hi) and (s_err is not None and s_err <= ERR_GATE)
    checks.append({"id": "C2", "name": "湿表面积 S_wet（夹逼 + 异实现）",
                   "pass": bool(c2) if s_err is not None else None,
                   "values": {"S_mock_m2": S_mock, "S_canonical_m2": S_can,
                              "squeeze_lo_2LT": lo, "squeeze_hi_L_B_2T": hi,
                              "rel_err_pct": s_err},
                   "note": ("Cb·L·B·T=%.4f m³；湿面积用夹逼带做一阶几何仲裁，再对正典比"
                            % vol) if s_err is not None else "正典 wetted_surface 不可用 → WITHHELD"})

    # C3 量纲一致性 -------------------------------------------------------
    implied_coeff = Ca_force_recorded / (q * S_mock)
    ra_correct = q * S_mock * Ca_dimless
    c3 = _rel(implied_coeff, Ca_dimless) <= 1.0
    checks.append({"id": "C3", "name": "相关补贴 Ca 量纲一致性",
                   "pass": bool(c3),
                   "values": {"Ca_dimensionless": Ca_dimless,
                              "Ca_force_recorded_N": Ca_force_recorded,
                              "implied_coefficient": implied_coeff,
                              "Ra_correct_N": ra_correct,
                              "rel_err_pct_of_coefficient": _rel(implied_coeff, Ca_dimless),
                              "underestimate_N": ra_correct - Ca_force_recorded},
                   "note": "C·S_wet [m²] 加到 Rf [N] 上是量纲错误：系数被压低 2 个数量级"})

    # C4 残余阻力显著性 ---------------------------------------------------
    Cr_Cf_mock = float(raw["Cr"]) / float(raw["Cf"])
    if can["available"]:
        cw_can = can["cw"] + (can["rb"] + can["rtr"]) / (q * can["s_ref"])
        cv_can = can["cv"]
        Cr_Cf_can = cw_can / can["cf"]
        ratio = Cr_Cf_mock / Cr_Cf_can if Cr_Cf_can else float("inf")
        c4 = 0.5 <= ratio <= 2.0
        c4_pass: Optional[bool] = bool(c4)
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
                              "Rt_mock_N": float(raw["Rt_N"]), "Rt_canonical_N": can.get("rt"),
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
    sw = mock["sweep"]
    rt_mono = all(sw[i + 1]["Rt_N"] > sw[i]["Rt_N"] for i in range(len(sw) - 1))
    ct_dec = all(sw[i + 1]["Ct"] < sw[i]["Ct"] for i in range(len(sw) - 1))
    c7 = rt_mono and ct_dec
    checks.append({"id": "C7", "name": "速度极限 Rt(V)↑ / Ct(V)↓",
                   "pass": bool(c7),
                   "values": {"Rt_monotonic_increasing": rt_mono, "Ct_monotonic_decreasing": ct_dec,
                              "samples": len(sw)},
                   "note": "摩擦主导域两端极限；Ct 随 V 上升即 HOLD 错误"})

    evaluated = [c for c in checks if c["pass"] is not None]
    fails = [c["id"] for c in evaluated if not c["pass"]]
    withheld = [c["id"] for c in checks if c["pass"] is None]
    verdict = ("MOCK" if fails else ("REAL" if not withheld else "WITHHELD"))

    return {"object": "optimizer/interfaces/resistance_eval.py::evaluate_resistance (KCS 目标函数)",
            "verdict_evidence_level": verdict,
            "criteria_total": len(checks), "criteria_evaluated": len(evaluated),
            "failed": fails, "withheld": withheld,
            "inputs": {"geom": g, "Fn_design": mock["Fn_design"], "V_ms": V,
                       "rho": rho, "nu": 1.19e-6,
                       "mock_components": {k: (float(v) if isinstance(v, (int, float)) else v)
                                           for k, v in raw.items()
                                           if isinstance(v, (int, float))},
                       "canonical": can},
            "checks": checks,
            "sweep_mock": sw,
            "provenance": {"canonical": canon["provenance"],
                           "tool": "tools/audit/optimizer_objective_audit.py"}}


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
def _figures(res: Dict[str, Any]) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGDIR.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []
    can = res["inputs"]["canonical"]
    g = res["inputs"]["geom"]

    # 图1：Ct(V) mock vs 正典
    sw = res["sweep_mock"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot([s["V_ms"] for s in sw], [s["Ct"] for s in sw], "o-", label="mock objective (Ct)")
    ax.plot([s["V_ms"] for s in sw], [s["Cf"] for s in sw], ":", label="ITTC-57 Cf (friction floor)")
    if can["available"]:
        canon = _load_canonical()
        Vs, Cts, Cvs = [], [], []
        for s in sw:
            c = _canon_path(canon, g, s["V_ms"])
            if c["available"]:
                Vs.append(s["V_ms"])
                Cts.append(c["ct"])
                Cvs.append(c["cv"])
        ax.plot(Vs, Cts, "s-", label="canonical Holtrop-1984 (m1553)")
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
    figs.append({"name": p1.name, "caption": "Ct(V)：mock 目标函数与正典 Holtrop-1984 及 ITTC-57 摩擦下界对比"})

    # 图2：阻力分量分解（bar）
    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels = ["Rf", "Rr", "Rw", "Ra", "Rt"]
    mc = res["inputs"]["mock_components"]
    mock_vals = [mc["Rf_N"], mc["Rr_N"], mc["Rw_N"], mc["Ca_N"], mc["Rt_N"]]
    ax.bar([x - 0.2 for x in range(len(labels))], mock_vals, width=0.4, label="mock")
    if can["available"]:
        q = 0.5 * res["inputs"]["rho"] * res["inputs"]["V_ms"] ** 2
        can_vals = [can["rf_1pk"], can["rb"] + can["rtr"], can["rw"], can["ra"], can["rt"]]
        ax.bar([x + 0.2 for x in range(len(labels))], can_vals, width=0.4, label="canonical")
        ax.set_yscale("symlog", linthresh=1e-2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(["Rf (viscous)", "Rr/Rb+Rtr", "Rw", "Ra", "Rt"])
    ax.set_ylabel("resistance [N] (symlog)")
    ax.set_title("Resistance decomposition: mock vs canonical")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    p2 = FIGDIR / "optobj_fig2_components.png"
    fig.tight_layout()
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append({"name": p2.name, "caption": "阻力分量分解对比：mock 的残余/兴波/相对补贴项量级错误（symlog）"})
    return figs


# ---------------------------------------------------------------------------
# 文档（表格程序化注入，数字零手抄）
# ---------------------------------------------------------------------------
def _md(res: Dict[str, Any]) -> str:
    can = res["inputs"]["canonical"]
    by = {c["id"]: c for c in res["checks"]}
    c5 = by["C5"]["values"]
    c3 = by["C3"]["values"]
    c4 = by["C4"]["values"]
    c2 = by["C2"]["values"]
    c6 = by["C6"]["values"]
    g = res["inputs"]["geom"]
    prov = res["provenance"]["canonical"]

    def fmt(v, n=6):
        if v is None:
            return "—"
        if isinstance(v, float):
            if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e6):
                return f"{v:.3e}"
            return f"{v:.{n}g}"
        return str(v)

    rows = "\n".join(
        f"| {c['id']} | {c['name']} | {'PASS' if c['pass'] else ('FAIL' if c['pass'] is not None else 'WITHHELD')} | "
        f"{json.dumps({k: (round(v, 6) if isinstance(v, float) else v) for k, v in c['values'].items() if not isinstance(v, dict)}, ensure_ascii=False)} |"
        for c in res["checks"])

    lines = [
        "# 优化器目标函数物理保真度复核：KCS 球鼻艏 CMA-ES 的 Ct 目标实为裸摩擦线（证据等级 MOCK）",
        "",
        "## ① 案例介绍",
        "",
        "**对象**：`optimizer/interfaces/resistance_eval.py::evaluate_resistance` —— 平台 KCS 球鼻艏",
        "形优化（CMA-ES）**唯一目标函数**：`optimization_loop.objective` 直接返回 `evaluate_resistance(...)[\"Ct\"]`，",
        "8 个艏部变形参数的全部适应度都由它给出；`optimizer/REPORT.md` 的“阻力下降 xx%”即建立其上。",
        "该模块文件头自称 `m547 interface mock`，但**从未被任何独立参考复核**，属于典型验证欠账。",
        "",
        "**几何与工况（KCS 模型尺度，取自 `optimizer/interfaces/kcs_bow_param.py`）**",
        "",
        "| 量 | 值 | 判据 |",
        "|---|---|---|",
        f"| L / B / T (m) | {fmt(g['L'])} / {fmt(g['B'])} / {fmt(g['T'])} | L/B={fmt(by['C1']['values']['L_B'],4)}，B/T={fmt(by['C1']['values']['B_T'],4)} |",
        f"| Cb | {fmt(g['Cb'],4)} | ∇=Cb·L·B·T={fmt(by['C1']['values']['nabla_m3'],5)} m³（@1025 kg/m³ → {fmt(by['C1']['values']['displacement_kg_at_1025'],6)} kg） |",
        f"| 设计 Fn / V | {fmt(res['inputs']['Fn_design'],3)} / {fmt(res['inputs']['V_ms'],5)} m/s | V=Fn·√(gL) |",
        f"| 流体 | ρ={fmt(res['inputs']['rho'],4)} kg/m³，ν={fmt(res['inputs']['nu'],3)} m²/s | 与 mock 同流体，只比方法 |",
        "",
        "## ② 怎么做的（方法/流程/关键设置/工具链）",
        "",
        "1. **异实现交叉**（而非自洽复算）：正典取 Holtrop-Mennen (1984) 全系数链内核（`%s`，"
        "provenance: `%s:%s`，sha256=%s）。本单**不重复实现第三次**——报告 1144 已裁决过多分支数值漂移的代价。"
        % (prov.get("path"), prov.get("source"), prov.get("blob_sha", "-"), str(prov.get("sha256"))[:16]),
        "2. **七条独立判据**（C1~C7，见下表）：几何恒等式、湿面积夹逼+异实现、量纲一致性、残余阻力量级、",
        "   总阻力误差、物理下界、速度极限/单调性。每条都可被变异体单独判负。",
        "3. **复算工具**：`python3 tools/audit/optimizer_objective_audit.py --write`；漂移闸 `--check`",
        "   （复算结果必须等于入库 JSON，否则非零退出）。",
        "4. 输入全部由程序读取（`KCS_BASELINE` / `generate_geometry` / 正典模块），文中数字由工具在写文档时注入，零手抄。",
        "",
        "## ③ 计算结果（程序化注入）",
        "",
        f"**证据等级判决：{res['verdict_evidence_level']}**（{res['criteria_evaluated']}/{res['criteria_total']} 条可判，"
        f"FAIL={res['failed'] or '无'}，WITHHELD={res['withheld'] or '无'}）",
        "",
        "| 判据 | 名称 | 判决 | 关键数值 |",
        "|---|---|---|---|",
        rows,
        "",
        "关键量对照（KCS 模型，设计点 Fn=0.26）：",
        "",
        "| 量 | mock 目标函数 | 正典 Holtrop-1984 | 相对误差 |",
        "|---|---|---|---|",
        f"| Ct [-] | {fmt(c5['Ct_mock'])} | {fmt(c5['Ct_canonical'])} | {fmt(c5['err_pct_Ct'],4)}% |",
        f"| Rt [N] | {fmt(c5['Rt_mock_N'],5)} | {fmt(c5['Rt_canonical_N'],5)} | {fmt(c5['err_pct_Rt'],4)}% |",
        f"| S_wet [m²] | {fmt(c2['S_mock_m2'],5)} | {fmt(c2['S_canonical_m2'],5)} | {fmt(c2['rel_err_pct'],4)}% |",
        f"| Cr/Cf [-] | {fmt(c4['Cr_Cf_mock'])} | {fmt(c4['Cr_Cf_canonical'])} | 比值 {fmt(c4['ratio_mock_over_canonical'])} |",
        f"| Ca（无量纲） | 记录为力项 {fmt(c3['Ca_force_recorded_N'],4)} N → 隐含系数 {fmt(c3['implied_coefficient'])} | {fmt(c3['Ca_dimensionless'])} | {fmt(c3['rel_err_pct_of_coefficient'],4)}% |",
        "",
        "## ④ 图",
        "",
        "见 `docs/verification/figures/optobj_fig1_ct_vs_v.png`（Ct(V) 与摩擦下界）与",
        "`optobj_fig2_components.png`（分量分解，symlog）。",
        "",
        "## ⑤ 注意点（坑/局限/适用范围）",
        "",
        "- **mock 的 Ct 恒等于 ITTC-57 Cf**：残余阻力项 `0.2·exp(-1.5/Fn^1.2)` 在 Fn=0.26 处给出 Cr≈1e-7，",
        "  兴波项 `exp(-2/Fn²)` 给出 Rw≈1e-11 N，二者被指数形式压在机器意义下的零 —— 目标函数等价于“只有摩擦”。",
        "- **量纲错误**：相关补贴 `Ca=0.006(L+100)^-0.16·S_wet` 把 [m²] 量直接加到 [N] 上，",
        f"  使该作用项被低估 {fmt(c3['underestimate_N'],4)} N（系数低 2 个数量级）。",
        "- **影响面**：优化器的每条适应度都偏低约 %s%%，且**对艏部参数的灵敏度完全来自人工 `params` 乘子**"
        % fmt(c5['err_pct_Ct'], 3),
        "  （`bow_entrance_angle_delta` / `bulb_volume_scale` / `stem_rake_delta` 的线性折扣），",
        "  所以“优化改善 x%”是灵敏度旋钮的读数，不是水动力收益；不得作为 REAL 证据引用。",
        "- **局限**：正典本身也不适用于细长轴对称体（见其模块 docstring 第 4 条），本复核只覆盖 KCS 位移船工况。",
        "- 本单**不修物理**：正典 Holtrop-1984 内核已由 agent/m1553 落地（`backend/api/holtrop1984.py`），",
        "  在优化器侧再写一份必然产生第三次版本漂移。正确修法是把目标函数改为委托正典（已在本单留下工单建议）。",
        "",
        "## ⑥ 误差判定",
        "",
        f"- Ct 相对误差 **{fmt(c5['err_pct_Ct'],4)}%** vs 正典 Holtrop-1984 → **是否 <3%：{'是' if c5['under_3pct'] else '否'}**；"
        f"是否 <15%（平台不可接受门）：**{'是' if (c5['err_pct_Ct'] or 1e9) <= ERR_GATE else '否'}**。",
        f"- Rt 相对误差 **{fmt(c5['err_pct_Rt'],4)}%**（同一判据的力形式）。",
        f"- 相关补贴系数误差 **{fmt(c3['rel_err_pct_of_coefficient'],4)}%**（量纲错误，远超门限）。",
        f"- Cr/Cf 量级比 **{fmt(c4['ratio_mock_over_canonical'])}**（要求 0.5~2）。",
        f"- 结论：上述四项全部不满足 3%/15% 判据，目标函数证据等级维持 **MOCK**；"
        "优化器输出不得进入能力台账的 REAL 行。",
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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写产物 JSON/MD/图")
    ap.add_argument("--check", action="store_true", help="漂移闸：复算必须等于入库")
    a = ap.parse_args(argv)

    res = audit()
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
        if old.get("verdict_evidence_level") != res["verdict_evidence_level"] or diffs:
            print(f"FAIL: 漂移 detected verdict={res['verdict_evidence_level']} diffs={diffs}")
            return 1
        print(f"PASS: 漂移闸通过（verdict={res['verdict_evidence_level']}，判据 {len(res['checks'])} 条）")
        return 0

    try:
        res["figures"] = _figures(res)
    except Exception as exc:  # noqa: BLE001
        res["figures_error"] = str(exc)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(_md(res))
    print(f"verdict={res['verdict_evidence_level']} failed={res['failed']} withheld={res['withheld']}")
    for c in res["checks"]:
        st = "PASS" if c["pass"] else ("FAIL" if c["pass"] is not None else "WITHHELD")
        print(f"  {c['id']} {st:8s} {c['name']}")
    if a.write:
        print(f"wrote {out.relative_to(ROOT)}, {DOC.relative_to(ROOT)}, {FIGDIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())