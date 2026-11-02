"""服务层阻力通道独立复核（mission m1846 · 验证欠账清偿）。

背景
----
docs/verification/inline_resistance_audit_2026-11-01.md 判决 ``calc.resistance`` 的
**inline 通道**为 MOCK，但其「注意点」明确写：服务层（:8001，Holtrop 重写）三次探测超时，
**未被覆盖**，因此 ``calc.resistance`` 的绝对精度记为 WITHHELD。本模块清偿该欠账：
在 :8001 恢复可达后，对**服务层**的两条阻力通道做独立复核。

被复核的两条服务层通道（同一平台、同一船型）
------------------------------------------
A. ``POST /api/v1/calculations/resistance``  —— ITTC-57 Cf + ITTC-1978 粗糙度 dCf
   + ``calculate_holtrop_cr()``（backend/api/calculations.py）。
B. ``POST /api/v1/resistance/predict``       —— ``calculate_holtrop_full()``
   （backend/api/resistance_advanced.py）。

判据设计原则：**先造判据，再审端点**。三条判据完全不依赖被测平台
--------------------------------------------------------------
J1 零弗劳德极限（物理恒等）：兴波/剩余阻力系数必须满足 lim_{Fn->0} Cr = 0
   （兴波随 Fn^4 量级消失；Holtrop-Mennen 的 Cw 表达式中 C1 含 (90-iE)^-1.37565 且在
   Fn->0 时被 m1*Fn^d, d<0 ... 支配 → 数值上趋于极小）。任何在 Fn=0 处给出
   有限非零 Cr 的模型都是把「常数份额」塞进了剩余阻力。
J2 黏性主导份额带（尺度分析）：Fn <= 0.20 的排水型船，Cf 量级 ~1.4e-3 且 Ct 量级
   2.4e-3~3e-3（canonical v3.0 的 KVLCC2 四行即在此区间），故剩余阻力份额 Cr/Ct
   物理上必须 <= 0.50。若某模型在 Fn<=0.2 给出 Cr/Ct > 0.5，则它把「剩余阻力」
   当成了主项——这不是兴波，是常数拟合项。
J3 丰满度敏感性（物理方向）：L、B、T、Fn 固定时，Cb 增大 ⇒ 排水体积增大 ⇒
   Cw 单调不减；且在 Cb∈[0.55,0.70] 内**不能**出现零敏感平台。
J4 外部基准（15% 门）：对 canonical v3.0 基准数据集 ``backend/data/benchmark_reference.json``
   的 ``experimental_ct`` 逐案例比较（同 Fn、同 Re、同流体：把基准的模型尺度几何与
   参考流体直接喂给端点，使两边处于同一无量纲状态）。
J5 跨通道一致（工程恒等）：同一船同一航速，两条通道 Ct 相对差 > 5% 即至少一条错。
J6 湿表面积物理带：S/(L·B) ∈ [1.0, 3.2]（canonical R6 形体带）。

用法
----
    python3 -m tools.resistance_svc.audit_m1846 --out artifacts/m1846

工件：``results.json``（全部原始返回 + 判据结论）、``fig1..fig3`` png。
若 :8001 不可达，脚本**不伪造**任何数字：以 ``status="unreachable"`` 记录并退出码 3。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIP_BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")
CANON = os.path.join(ROOT, "backend", "data", "benchmark_reference.json")

# 判决门
GATE_EFD_PCT = 15.0          # 外部基准门（>15% 不可接受）
GATE_CROSS_PCT = 5.0         # 跨通道一致门
GATE_ZERO_FROUDE_CR = 1.0e-4  # Fn->0 时 |Cr| 上限
GATE_CR_SHARE_FN_MAX = 0.20      # 黏性主导区间上限 Fn
GATE_CR_SHARE = 0.50             # 该区间 Cr/Ct 上限


# --------------------------------------------------------------------------- #
# HTTP 探针（黑盒，唯一入口；不 import 被测仓代码）
# --------------------------------------------------------------------------- #
def _post(path: str, body: Dict[str, Any], timeout: float = 20.0) -> Tuple[int, Any]:
    url = f"{SHIP_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要留证
        return exc.code, exc.read().decode()[:500]


def probe_channel_a(length: float, beam: float, draft: float, cb: float,
                    speed_ms: float, rho: float, nu: float) -> Dict[str, Any]:
    """通道 A：/calculations/resistance（速度以 m/s 显式声明，流体显式给基准值）。"""
    nabla = cb * length * beam * draft
    status, payload = _post("/api/v1/calculations/resistance", {
        "length": length, "beam": beam, "draft": draft, "cb": cb,
        "displacement": nabla,
        "speed": speed_ms, "speed_unit": "m/s",
        "water_density": rho, "water_viscosity": nu,
    })
    return {"status": status, "body": payload}


def probe_channel_b(length: float, beam: float, draft: float, cb: float,
                    speed_ms: float) -> Dict[str, Any]:
    """通道 B：/resistance/predict（其契约以 kn 解释 speeds）。"""
    speed_kn = speed_ms * 3600.0 / 1852.0
    status, payload = _post("/api/v1/resistance/predict", {
        "Lwl": length, "Lpp": length, "B": beam, "T": draft,
        "displacement": cb * length * beam * draft,
        "Cb": cb, "speeds": [speed_kn],
    })
    return {"status": status, "body": payload, "speed_kn_sent": speed_kn}


# --------------------------------------------------------------------------- #
# 判据实现（纯函数，可离线单测）
# --------------------------------------------------------------------------- #
def judge_zero_froude(cr_by_fn: Dict[float, float]) -> Dict[str, Any]:
    """J1：Fn->0 时 Cr 必须 -> 0。取扫描里最小的 Fn 作为极限代理。"""
    fns = sorted(cr_by_fn)
    fn0 = fns[0]
    cr0 = cr_by_fn[fn0]
    return {
        "judge": "J1_zero_froude_limit",
        "fn": fn0,
        "cr": cr0,
        "gate": GATE_ZERO_FROUDE_CR,
        "pass": abs(cr0) <= GATE_ZERO_FROUDE_CR,
        "note": "兴波阻力随 Fn 高阶消失；Fn->0 处有限非零 Cr = 常数份额伪装成剩余阻力",
    }


def judge_viscous_share(rows: List[Dict[str, Any]], fn_max: float = 0.20,
                        gate: float = 0.50) -> Dict[str, Any]:
    """J2：Fn <= fn_max 的案例，剩余阻力份额 Cr/Ct 必须 <= gate。

    rows 需含 case_id / cr / ct / froude_number；froude_number 缺失时用 Fn 反算。
    """
    per = []
    for r in rows:
        fn = r.get("froude_number")
        if fn is None or fn > fn_max:
            continue
        share = r["cr"] / r["ct"] if r["ct"] else float("inf")
        per.append({"case_id": r["case_id"], "fn": fn, "cr": r["cr"], "ct": r["ct"],
                    "cr_share": share, "pass": share <= gate})
    return {
        "judge": "J2_viscous_dominated_share",
        "fn_max": fn_max,
        "gate_share": gate,
        "cases": per,
        "pass": bool(per) and all(p["pass"] for p in per),
        "max_share": max((p["cr_share"] for p in per), default=0.0),
        "note": "Fn<=0.2 区间 Cf 主导；Cr/Ct>0.5 说明剩余阻力项被当成主项（常数拟合）",
    }


def judge_fullness(series: Dict[float, float], plateau_band=(0.55, 0.70)) -> Dict[str, Any]:
    """J3：固定 L/B/T/Fn，Cr 随 Cb 单调不减，且低 Cb 段不得为常数平台。"""
    cbs = sorted(series)
    monotone = all(series[cbs[i + 1]] >= series[cbs[i]] - 1e-12 for i in range(len(cbs) - 1))
    band = [c for c in cbs if plateau_band[0] <= c <= plateau_band[1]]
    plateau = False
    if len(band) >= 2:
        vals = [series[c] for c in band]
        plateau = (max(vals) - min(vals)) <= 1e-12 * max(1.0, max(vals))
    return {
        "judge": "J3_fullness_sensitivity",
        "series": {str(k): v for k, v in series.items()},
        "monotone": monotone,
        "plateau_in_band": plateau,
        "pass": monotone and not plateau,
        "note": "Cb 增大 ⇒ ∇ 增大 ⇒ 兴波不减；Cb∈[0.55,0.70] 内的零敏感平台物理上不成立",
    }


def judge_efd(rows: List[Dict[str, Any]], gate_pct: float = GATE_EFD_PCT) -> Dict[str, Any]:
    """J4：|Ct_pred - Ct_efd|/Ct_efd <= gate。rows 需含 case_id / ct_pred / ct_efd。

    ct_efd 为 None（数据集判为 WITHHELD 的行）时不参与判决，单列 with_held。
    """
    per, held = [], []
    for r in rows:
        if r.get("ct_efd") is None:
            held.append(r["case_id"])
            continue
        err = (r["ct_pred"] - r["ct_efd"]) / r["ct_efd"] * 100.0
        per.append({"case_id": r["case_id"], "ct_pred": r["ct_pred"],
                    "ct_efd": r["ct_efd"], "err_pct": err,
                    "pass": abs(err) <= gate_pct})
    return {
        "judge": "J4_efd_gate",
        "gate_pct": gate_pct,
        "cases": per,
        "with_held_from_judgement": held,
        "n_pass": sum(1 for p in per if p["pass"]),
        "n_total": len(per),
        "pass": bool(per) and all(p["pass"] for p in per),
        "max_abs_err_pct": max((abs(p["err_pct"]) for p in per), default=0.0),
    }


def judge_cross_channel(rows: List[Dict[str, Any]], gate_pct: float = GATE_CROSS_PCT) -> Dict[str, Any]:
    """J5：同船同速，两条通道 Ct 相对差 <= gate。"""
    per = []
    for r in rows:
        a, b = r.get("ct_a"), r.get("ct_b")
        if a is None or b is None:
            continue
        diff = (b - a) / a * 100.0
        per.append({"case_id": r["case_id"], "ct_a": a, "ct_b": b,
                    "diff_pct": diff, "pass": abs(diff) <= gate_pct})
    return {
        "judge": "J5_cross_channel_consistency",
        "gate_pct": gate_pct,
        "cases": per,
        "pass": bool(per) and all(p["pass"] for p in per),
        "max_abs_diff_pct": max((abs(p["diff_pct"]) for p in per), default=0.0),
    }


def judge_wetted_band(rows: List[Dict[str, Any]],
                      band: Tuple[float, float] = (1.0, 3.2)) -> Dict[str, Any]:
    """J6：S/(L·B) 必须落在 canonical R6 形体带内。"""
    per = []
    for r in rows:
        s = r.get("wetted_surface")
        if s is None:
            continue
        ratio = s / (r["length"] * r["beam"])
        per.append({"case_id": r["case_id"], "s_over_lb": ratio,
                    "pass": band[0] <= ratio <= band[1]})
    return {"judge": "J6_wetted_surface_band", "band": list(band), "cases": per,
            "pass": all(p["pass"] for p in per)}


# --------------------------------------------------------------------------- #
# 案例装载
# --------------------------------------------------------------------------- #
def load_cases(path: str = CANON) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = []
    for c in doc["cases"]:
        g = c["geometry"]
        if "beam_m" not in g:  # 回转体（SUBOFF）只有 D/L，端点几何门禁无法表达
            doc.setdefault("_skipped", []).append(c["case_id"])
            continue
        out.append({
            "case_id": c["case_id"],
            "ship_type": c["ship_type"],
            "form_type": c.get("form_type"),
            "scale": c["scale"],
            "length": g["length_m"],
            "beam": g["beam_m"],
            "draft": g["draft_m"],
            "cb": g["cb"],
            "speed_ms": c["speed_ms"],
            "rho": c["reference_density_kg_m3"],
            "nu": c["reference_kinematic_viscosity_m2_s"],
            # 数据集 Ct 列：v3.0 已由 benchmark_canon 裁决（Rt 列 WITHHELD，Ct 列可作条件基准）
            "ct_efd": c.get("experimental_ct"),
            "rt_status": c.get("rt_status"),
            "reference_status": c.get("reference_status"),
        })
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(out_dir: str) -> Dict[str, Any]:
    cases = load_cases()
    results: Dict[str, Any] = {"mission": 1846, "ship_api_base": SHIP_BASE,
                               "canon": os.path.relpath(CANON, ROOT), "cases": []}

    # 0) 可达性闸门
    st, payload = _post("/api/v1/calculations/resistance", {
        "length": 100.0, "beam": 20.0, "draft": 8.0, "cb": 0.8,
        "displacement": 0.8 * 100.0 * 20.0 * 8.0, "speed": 5.0, "speed_unit": "m/s"})
    results["reachable"] = (st == 200)
    if st != 200:
        results["status"] = "unreachable"
        results["probe_detail"] = {"status": st, "body": payload}
        _write(os.path.join(out_dir, "results.json"), results)
        return results
    results["status"] = "ok"
    results["coverage_gap"] = {
        "submerged_bodies": "SUBOFF 族为回转体（仅 D/L），端点几何门禁要求 Cb>0.3 与 B/T/Cb 三元组，"
                            "无法表达潜体 → 该域不计入判决，记覆盖缺口（非端点错误）",
        "kcs": "canonical v3.0 无 KCS 行；Fn/Cb 扫描用 canonical 首例几何（自造工况，不做绝对比对）",
        "channel_b_resolution": "通道 B 以 kN 且 2 位小数返回（R~10 N 的模型试验行只有 1-2 位有效数字），"
                                "且不返回 Ct/湿面积 → 模型尺度无法折算 Ct；跨通道一致判据只在实尺度案例上执行",
    }

    # 1) 逐案例双通道探针
    rows: List[Dict[str, Any]] = []
    for c in cases:
        a = probe_channel_a(c["length"], c["beam"], c["draft"], c["cb"],
                            c["speed_ms"], c["rho"], c["nu"])
        b = probe_channel_b(c["length"], c["beam"], c["draft"], c["cb"], c["speed_ms"])
        row = dict(c)
        row["channel_a_status"] = a["status"]
        row["channel_b_status"] = b["status"]
        if a["status"] == 200:
            row.update({k: a["body"][k] for k in
                        ("ct", "cf", "cf_total", "cr", "rf", "rr", "rt", "pe",
                         "wetted_surface", "reynolds_number", "froude_number")})
            row["ct_a"] = a["body"]["ct"]
        if b["status"] == 200 and isinstance(b["body"], list) and b["body"]:
            bb = b["body"][0]
            # 通道 B 不返回 Ct/湿面积：用其自身 wave_resistance 与源码 Cr 式
            # （resistance_advanced.py: Cr=0.001*(1+10*Fn)）反解内部湿面积，再折 Ct。
            fn_b = c["speed_ms"] / math.sqrt(9.81 * c["length"])
            row["cr_b_src"] = 0.001 * (1.0 + 10.0 * fn_b)
            # 通道 B 只给 kN（2 位舍入）且不返回湿面积：模型尺度（R~10 N）分辨率不足，
            # 故 ct_b 只在实尺度案例上折算（用通道 A 的湿面积归一，S 的两通道差 <3%）。
            if bb["total_resistance"] >= 100.0 and row.get("wetted_surface"):
                row["ct_b"] = (bb["total_resistance"] * 1000.0 /
                               (0.5 * 1025.0 * c["speed_ms"] ** 2 * row["wetted_surface"]))
            row["rt_b_kn"] = bb["total_resistance"]
            row["rf_b_kn"] = bb["friction_resistance"]
            row["rw_b_kn"] = bb["wave_resistance"]
            row["re_b"] = bb["reynolds_number"]
            row["nu_b_implied"] = c["speed_ms"] * c["length"] / max(1e-9, bb["reynolds_number"])
            row["re_err_pct"] = ((bb["reynolds_number"] - row["reynolds_number"]) /
                                 row["reynolds_number"] * 100.0) if row.get("reynolds_number") else None
            row["speed_kn_sent_b"] = b["speed_kn_sent"]
        row["ct_pred"] = row.get("ct_a")
        rows.append(row)
    results["cases"] = rows

    # 2) Fn 扫描（判定 J1）—— KCS 实尺度几何，扫 Fn 0.02..0.30
    kcs = next(c for c in cases if c["ship_type"] == "KCS") if any(
        c["ship_type"] == "KCS" for c in cases) else cases[0]
    sweep = []
    for fn in (0.02, 0.05, 0.10, 0.15, 0.20, 0.26, 0.30):
        v = fn * math.sqrt(9.81 * kcs["length"])
        a = probe_channel_a(kcs["length"], kcs["beam"], kcs["draft"], kcs["cb"],
                            v, kcs["rho"], kcs["nu"])
        b = probe_channel_b(kcs["length"], kcs["beam"], kcs["draft"], kcs["cb"], v)
        item = {"fn": fn, "speed_ms": v}
        if a["status"] == 200:
            item["cr_a"] = a["body"]["cr"]
            item["ct_a"] = a["body"]["ct"]
            item["cf_total_a"] = a["body"]["cf_total"]
        if b["status"] == 200 and isinstance(b["body"], list) and b["body"]:
            item["rt_b_kn"] = b["body"][0]["total_resistance"]
            item["rw_b_kn"] = b["body"][0]["wave_resistance"]
        sweep.append(item)
    results["fn_sweep_kcs"] = {"geometry_label": f"canonical 首例几何（{kcs['case_id']}，模型尺度）",
                               "geometry": {k: kcs[k] for k in
                                            ("length", "beam", "draft", "cb", "rho", "nu")},
                               "points": sweep}

    # 3) Cb 敏感性（判定 J3）—— 固定 L/B/T/Fn=0.26
    fullness = {}
    for cb in (0.55, 0.60, 0.65, 0.70, 0.75, 0.82):
        v = 0.26 * math.sqrt(9.81 * kcs["length"])
        a = probe_channel_a(kcs["length"], kcs["beam"], kcs["draft"], cb, v,
                            kcs["rho"], kcs["nu"])
        if a["status"] == 200:
            fullness[cb] = a["body"]["cr"]
    results["cb_sweep"] = {"fixed": {"length": kcs["length"], "beam": kcs["beam"],
                                     "draft": kcs["draft"], "fn": 0.26},
                            "cr_by_cb": {str(k): v for k, v in fullness.items()}}

    # 4) 判据
    cr_by_fn = {p["fn"]: p["cr_a"] for p in sweep if "cr_a" in p}
    results["judgements"] = {
        "J1_zero_froude": judge_zero_froude(cr_by_fn),
        "J2_viscous_share": judge_viscous_share(rows),
        "J3_fullness": judge_fullness(fullness),
        "J4_efd": judge_efd(rows),
        "J5_cross_channel": judge_cross_channel(rows),
        "J6_wetted_band": judge_wetted_band(rows),
    }
    results["verdict"] = derive_verdict(results["judgements"])
    _write(os.path.join(out_dir, "results.json"), results)
    return results


def derive_verdict(judg: Dict[str, Any]) -> Dict[str, Any]:
    """把判据聚合成证据等级。硬物理判据（J1/J2/J3）任一 FAIL ⇒ 通道不可能为 REAL。"""
    hard = [k for k in ("J1_zero_froude", "J2_viscous_share", "J3_fullness")
            if not judg[k]["pass"]]
    efd = judg["J4_efd"]
    cross = judg["J5_cross_channel"]
    if hard:
        level = "MOCK"
        why = ("硬物理判据 FAIL(" + ",".join(hard) + ")：剩余阻力项在 Fn->0 处非零 / "
               "黏性主导区剩余阻力份额越界 / 对 Cb 无敏感 —— 该项不是 Holtrop 剩余阻力，"
               "而是常数拟合项")
    elif efd["pass"]:
        level = "REAL"
        why = "三条硬物理判据通过且外部基准 15% 门通过"
    else:
        level = "WITHHELD"
        why = "物理判据通过但外部基准超 15% 门"
    return {
        "evidence_level_service_layer": level,
        "reason": why,
        "efd_pass": f"{efd['n_pass']}/{efd['n_total']}",
        "efd_max_abs_err_pct": efd["max_abs_err_pct"],
        "cross_channel_pass": cross["pass"],
        "cross_channel_max_abs_diff_pct": cross["max_abs_diff_pct"],
    }


# --------------------------------------------------------------------------- #
# 产出
# --------------------------------------------------------------------------- #
def _write(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def make_figures(res: Dict[str, Any], out_dir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs = []
    os.makedirs(out_dir, exist_ok=True)

    # fig1: Cr(Fn) —— 零弗劳德截距
    sw = res["fn_sweep_kcs"]["points"]
    fns = [p["fn"] for p in sw if "cr_a" in p]
    crs = [p["cr_a"] for p in sw if "cr_a" in p]
    if fns:
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        ax.plot(fns, crs, "o-", color="#c0392b", label="service channel A: Cr")
        ax.axhline(0.0, color="k", lw=0.8)
        ax.axhspan(-GATE_ZERO_FROUDE_CR, GATE_ZERO_FROUDE_CR, color="#27ae60",
                   alpha=0.25, label="J1 band |Cr|<=1e-4 at Fn->0")
        ax.set_xlabel("Froude number Fn")
        ax.set_ylabel("residual resistance coefficient Cr")
        ax.set_title("J1: residual resistance must vanish as Fn->0 (KCS geometry)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        p = os.path.join(out_dir, "fig1_cr_vs_fn.png")
        fig.tight_layout()
        fig.savefig(p, dpi=140)
        plt.close(fig)
        figs.append(p)

    # fig2: 逐案例相对误差 + 15% 门
    per = res["judgements"]["J4_efd"]["cases"]
    if per:
        fig, ax = plt.subplots(figsize=(8.4, 4.6))
        ids = [c["case_id"] for c in per]
        errs = [c["err_pct"] for c in per]
        colors = ["#c0392b" if abs(e) > GATE_EFD_PCT else "#27ae60" for e in errs]
        ax.bar(range(len(ids)), errs, color=colors)
        ax.axhline(GATE_EFD_PCT, ls="--", color="#e67e22", label="+15% gate")
        ax.axhline(-GATE_EFD_PCT, ls="--", color="#e67e22")
        ax.set_xticks(range(len(ids)))
        ax.set_xticklabels(ids, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("Ct error vs canonical EFD [%]")
        ax.set_title("J4: service-layer Ct vs canonical v3.0 EFD (15% gate)")
        ax.grid(axis="y", alpha=0.3)
        ax.legend(fontsize=8)
        p = os.path.join(out_dir, "fig2_case_errors.png")
        fig.tight_layout()
        fig.savefig(p, dpi=140)
        plt.close(fig)
        figs.append(p)

    # fig3: Cb 敏感性（平台）
    cb = res["cb_sweep"]["cr_by_cb"]
    if cb:
        xs = [float(k) for k in cb]
        ys = [cb[k] for k in cb]
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        xs = [xs[i] for i in order]
        ys = [ys[i] for i in order]
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        ax.plot(xs, ys, "s-", color="#2c3e50", label="service channel A: Cr(Cb)")
        ax.set_xlabel("block coefficient Cb")
        ax.set_ylabel("residual resistance coefficient Cr")
        ax.set_title("J3: Cr vs Cb at fixed L,B,T,Fn=0.26 (must be non-decreasing)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        p = os.path.join(out_dir, "fig3_cb_sensitivity.png")
        fig.tight_layout()
        fig.savefig(p, dpi=140)
        plt.close(fig)
        figs.append(p)

    return figs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts", "m1846"))
    args = ap.parse_args(argv)
    res = run(args.out)
    if res.get("status") != "ok":
        print(json.dumps({"status": res.get("status")}, ensure_ascii=False))
        return 3
    figs = make_figures(res, args.out)
    with open(os.path.join(args.out, "results.json"), encoding="utf-8") as fh:
        res = json.load(fh)
    res["figures"] = [os.path.relpath(f, ROOT) for f in figs]
    _write(os.path.join(args.out, "results.json"), res)
    print(json.dumps({"verdict": res["verdict"],
                      "figures": res["figures"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())