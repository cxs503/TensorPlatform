"""m1868 · 阻力端点残余阻力锚点审计（live :8001 vs 仓内 Holtrop-1984 参考实现 vs 基准门注册表）

三路对撞，全部机器可裁、离线可复跑（毫秒级，无网格/无算力负载）：

  L) 生产端点  POST http://127.0.0.1:8001/api/v1/calculations/resistance
  R) 仓内参考实现 backend/api/holtrop1984.py::holtrop1984_resistance（纯 Python，论文系数链）
  G) 平台基准门注册表 backend/services/ship_proxy.py::BENCHMARK_REFERENCE["resistance"]["kvlcc2_full"]

产出：
  artifacts/m1868/resistance_anchor.json      （全部原始读数 + 判定，零手抄来源）
  artifacts/m1868/resistance_anchor.png       （图）
  docs/audit/m1868_residual_resistance_anchor.md（由本脚本渲染，数字程序化注入）

用法：
  python3 -m tools.audit.resistance_anchor_m1868            # live 复跑并重新裁决
  python3 -m tools.audit.resistance_anchor_m1868 --offline   # 只读既有 ledger 做裁决/渲染
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "m1868"
LEDGER = ART / "resistance_anchor.json"
API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")

RHO_SEA = 1025.0
G = 9.81
NU_SEA = 1.1883e-6  # m^2/s, 15 degC

# KVLCC2 主尺度（与注册表 kvlcc2_full 的 params 逐字段一致）
SHIP = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098, displacement=320437.0, speed=15.0)
SPEEDS_KN = [5.0, 8.0, 11.0, 13.0, 14.0, 15.0, 15.5, 16.0, 17.0, 18.0]


# ── 基准门注册表 ─────────────────────────────────────
def load_gate_registry() -> dict:
    """读平台自己的基准门注册表（唯一真值声明处）。"""
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from services.ship_proxy import BENCHMARK_REFERENCE  # type: ignore
        return BENCHMARK_REFERENCE["resistance"]["kvlcc2_full"]
    finally:
        sys.path.pop(0)


# ── 仓内参考实现 ─────────────────────────────────────
def load_reference_kernel():
    """按文件路径装载 backend/api/holtrop1984.py，绕开 api 包的 DB 依赖。"""
    path = ROOT / "backend" / "api" / "holtrop1984.py"
    spec = importlib.util.spec_from_file_location("holtrop1984_ref", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["holtrop1984_ref"] = mod
    spec.loader.exec_module(mod)
    return mod


def build_geom(mod, ship: dict):
    """构造 HullGeometry：主尺度给定，Cm/Cwp 由实现自身回归派生（不喂外部数字）。"""
    HG = mod.HullGeometry
    names = set(inspect.signature(HG).parameters)
    vol = ship["displacement"] * 1000.0 / RHO_SEA
    cm = mod.estimate_cm(ship["cb"])
    cwp = mod.estimate_cwp(ship["cb"])
    cand = dict(name="KVLCC2", l=ship["length"], b=ship["beam"], t=ship["draft"],
                cb=ship["cb"], cm=cm, cwp=cwp, vol=vol,
                displacement=ship["displacement"])
    kwargs = {k: v for k, v in cand.items() if k in names}
    return HG(**kwargs), names


def reference_resistance(mod, geom, speed_ms: float) -> dict:
    r = mod.holtrop1984_resistance(geom, speed_ms)
    out = {}
    for key in ("rt", "rf", "rf_1pk", "rw", "rb", "rtr", "rr", "ra", "cf", "ct", "cv",
                "cw", "ca", "s", "s_ref", "form_factor", "form_factor_1pk", "re", "fn",
                "iE", "m1", "m4", "lam"):
        if hasattr(r, key):
            v = getattr(r, key)
            out[key] = float(v() if callable(v) else v)
    # dataclass 兜底
    if "rt" not in out and hasattr(r, "__dict__"):
        for k, v in vars(r).items():
            if isinstance(v, (int, float)):
                out.setdefault(k, float(v))
    out["_raw_keys"] = [k for k in dir(r) if not k.startswith("_")][:40]
    return out


# ── 生产端点 ─────────────────────────────────────────
def live_resistance(speed_kn: float, ship: dict) -> dict:
    body = dict(length=ship["length"], beam=ship["beam"], draft=ship["draft"],
                cb=ship["cb"], displacement=ship["displacement"], speed=speed_kn)
    req = urllib.request.Request(
        API + "/calculations/resistance",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as fh:
        return json.load(fh)


# ── 判据（每条都独立于被测实现）─────────────────────
def cf_ittc57(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


def anchor_cf(live: dict, ship: dict) -> dict:
    """A1 ITTC-57 摩擦系数恒等式：CF 只允许由 Re 决定。"""
    speed_ms = live["speed_ms"]
    re = speed_ms * ship["length"] / NU_SEA
    cf_expect = cf_ittc57(re)
    rel = abs(live["cf"] - cf_expect) / cf_expect
    return dict(anchor="A1_cf_ittc57", re=re, cf_reported=live["cf"],
                cf_expected=cf_expect, rel_err=rel, tol=0.02, passed=rel <= 0.02)


def anchor_additivity(live: dict) -> dict:
    """A2 阻力分解可加性：rt = rf + rr（同一算例内部恒等式）。"""
    s = live["rf"] + live["rr"]
    rel = abs(live["rt"] - s) / max(abs(s), 1e-12)
    return dict(anchor="A2_rt_rf_plus_rr", rt=live["rt"], rf_plus_rr=s,
                rel_err=rel, tol=1e-6, passed=rel <= 1e-6)


def anchor_coeff_scale(live: dict) -> dict:
    """A3 系数-力换算恒等式：R = c · ½ρV²S（三个分量逐一验）。"""
    qs = 0.5 * RHO_SEA * live["speed_ms"] ** 2 * live["wetted_surface"]
    rows = {}
    for c_key, r_key in (("cf_total", "rf"), ("cr", "rr"), ("ct", "rt")):
        if c_key in live:
            exp_kN = live[c_key] * qs / 1e3  # live 端点的力以 kN 返回
            rows[c_key] = dict(c=live[c_key], r_kN=live[r_key], r_expected_kN=exp_kN,
                               rel_err=abs(live[r_key] - exp_kN) / max(abs(live[r_key]), 1e-12))
    worst = max((v["rel_err"] for v in rows.values()), default=0.0)
    return dict(anchor="A3_R_equals_c_half_rho_V2_S", qs=qs, rows=rows,
                worst_rel_err=worst, tol=0.01, passed=worst <= 0.01)


def anchor_roughness(live: dict) -> dict:
    """A4 摩擦线由 CF+ΔCF 构成：rf = ½ρV²S(cf+dcf)。"""
    qs = 0.5 * RHO_SEA * live["speed_ms"] ** 2 * live["wetted_surface"]
    expect = qs * (live["cf"] + live.get("dcf", 0.0)) / 1e3  # kN
    rel = abs(live["rf"] - expect) / max(abs(live["rf"]), 1e-12)
    return dict(anchor="A4_rf_from_cf_plus_dcf", rf=live["rf"], expected=expect,
                rel_err=rel, tol=0.02, passed=rel <= 0.02)


def anchor_form_factor(live: dict) -> dict:
    """A5 隐含形状因子：由 (rt-rr)/(½ρV²S·cf) 反解 (1+k)。

    排水型船体回归带 1.25–1.55（Holtrop-1984 form factor 对肥大型船体区间）；
    超出该带说明粘性/剩余分解失衡（不是形状因子真值声明）。
    """
    qs = 0.5 * RHO_SEA * live["speed_ms"] ** 2 * live["wetted_surface"]
    # live 的力以 kN 给出，qs 是 N -> 归一到 kN 后反解 (1+k)
    one_plus_k = (live["rt"] - live["rr"]) / max(qs * live["cf"] / 1e3, 1e-12)
    return dict(anchor="A5_implied_form_factor", one_plus_k=one_plus_k,
                band=[1.25, 1.55], passed=1.25 <= one_plus_k <= 1.55)


def anchor_three_way(live: dict, ref: dict, gate: dict) -> dict:
    """A6 三路对撞：生产端点 / 仓内参考实现 / 基准门注册值。"""
    g_rt = float(gate["reference_rt"])
    tol = float(gate.get("tolerance_pct", 15.0))
    d_live = abs(live["rt"] - g_rt) / g_rt * 100.0
    d_ref = abs(ref["rt"] / 1e3 - g_rt) / g_rt * 100.0
    d_lr = abs(live["rt"] - ref["rt"] / 1e3) / (ref["rt"] / 1e3) * 100.0
    return dict(anchor="A6_three_way_rt", rt_live_kN=live["rt"], rt_ref_kN=ref["rt"] / 1e3,
                rt_gate_kN=g_rt, tol_pct=tol,
                err_live_vs_gate_pct=d_live, err_ref_vs_gate_pct=d_ref,
                err_live_vs_ref_pct=d_lr,
                passed_live=d_live <= tol, passed_ref=d_ref <= tol,
                three_way_agree=d_live <= tol and d_ref <= tol)


def anchor_split(live: dict, ref: dict) -> dict:
    """A7 分量拆分对撞：总阻力系数与「剩余项占比」两路比。

    live 的 cr 是 CT−CF；ref kernel 的同类量是 CT−CF（cw+ca+(1+k−1)cf 部分进 rf_1pk）。
    """
    ref_ct = ref["ct"]
    ref_cr_resid = ref["ct"] - ref["cf"]
    ref_res_share = (ref["rt"] - ref["rf_1pk"]) / ref["rt"]
    live_res_share = live["rr"] / live["rt"]
    return dict(anchor="A7_residual_split",
                live_ct=live["ct"], ref_ct=ref_ct,
                ct_ratio=live["ct"] / ref_ct,
                live_cr=live["cr"], ref_cr_resid=ref_cr_resid,
                cr_ratio=live["cr"] / max(ref_cr_resid, 1e-12),
                live_rr_rf=live["rr"] / live["rf"],
                ref_rw_rf=ref["rw"] / ref["rf_1pk"],
                live_res_share=live_res_share, ref_res_share=ref_res_share,
                ref_1pk=ref["form_factor_1pk"])


def anchor_low_froude(sweep: list, ref_sweep: list) -> dict:
    """A8 低 Fr 消失性：Fr→0 时「剩余项/粘性项」必须塌缩（兴波阻力物理上消失）。

    纯无量纲判据，不用任何文献常数：把 Fr<0.07 的比值与设计 Fr≈0.138 的比值相比，
    真实现（含兴波）应塌缩 ≥5×；若几乎不变，则所谓「剩余阻力」不是造波项。
    """
    def ratio_curve(rows, key_low, key_high):
        pts = [(r["fn"], r[key_low] / max(r[key_high], 1e-12))
               for r in rows if "error" not in r and r.get(key_high)]
        return pts

    def collapse(pts):
        if not pts:
            return None
        low = [v for f, v in pts if f < 0.07]
        hi = [v for f, v in pts if abs(f - 0.1377) < 0.008]
        if not low or not hi:
            return None
        return dict(low_max=max(low), design_max=max(hi),
                    collapse=max(hi) / max(max(low), 1e-12))

    live_pts = ratio_curve(sweep, "rr", "rf")
    ref_pts = ratio_curve(ref_sweep, "rw", "rf_1pk")
    out = {"anchor": "A8_low_froude_vanishing",
           "live": collapse(live_pts), "ref": collapse(ref_pts),
           "live_curve": live_pts, "ref_curve": ref_pts, "threshold": 5.0}
    if out.get("live"):
        out["passed"] = out["live"]["collapse"] >= 5.0
    if out.get("ref"):
        out["passed_ref"] = out["ref"]["collapse"] >= 5.0
    return out


# ── 采集 ────────────────────────────────────────────
def collect() -> dict:
    gate = load_gate_registry()
    mod = load_reference_kernel()
    geom, geom_fields = build_geom(mod, SHIP)
    sweep, ref_sweep = [], []
    for kn in SPEEDS_KN:
        speed_ms = kn * 0.514444
        try:
            live = live_resistance(kn, SHIP)
        except Exception as exc:  # 端点不可用时不静默：如实记录
            live = {"error": f"{type(exc).__name__}: {exc}"}
        if "error" not in live:
            cwp = SHIP["length"] * SHIP["beam"] * SHIP["draft"] * SHIP["cb"]
            live["fn"] = live["speed_ms"] / math.sqrt(G * SHIP["length"])
            live["vol_check"] = cwp
            sweep.append(dict(speed_kn=kn, speed_ms=live["speed_ms"], fn=live["fn"],
                              rt=live["rt"], rf=live["rf"], rr=live["rr"],
                              cf=live["cf"], cr=live["cr"], ct=live["ct"],
                              s=live["wetted_surface"], re=live["reynolds_number"],
                              rr_rf=live["rr"] / live["rf"]))
            if kn == 15.0:
                live_design = live
        else:
            live_design = None
            sweep.append(dict(speed_kn=kn, **live))
        try:
            r = reference_resistance(mod, geom, speed_ms)
            row = {k: v for k, v in r.items() if isinstance(v, (int, float))}
            row["fn"] = r.get("fn", speed_ms / math.sqrt(G * SHIP["length"]))
            row["rt_kN"] = r["rt"] / 1e3
            ref_sweep.append(dict(speed_kn=kn, speed_ms=speed_ms, **row))
        except Exception as exc:
            ref_sweep.append(dict(speed_kn=kn, error=f"{type(exc).__name__}: {exc}"))

    data = dict(ship=SHIP, gate=gate, speeds_kn=SPEEDS_KN, sweep=sweep,
                ref_sweep=ref_sweep, geom_fields=sorted(geom_fields),
                nu=NU_SEA, rho=RHO_SEA, api=API)
    if live_design:
        ref_design = ref_sweep[[s["speed_kn"] for s in ref_sweep].index(15.0)]
        data["design"] = dict(
            A1=anchor_cf(live_design, SHIP),
            A2=anchor_additivity(live_design),
            A3=anchor_coeff_scale(live_design),
            A4=anchor_roughness(live_design),
            A5=anchor_form_factor(live_design),
            A6=anchor_three_way(live_design, ref_design, gate),
            A7=anchor_split(live_design, ref_design),
        )
        data["A8"] = anchor_low_froude(sweep, ref_sweep)
        # 参考实现自身恒等式（证明参考实现不是随便凑的）
        data["ref_selfcheck"] = dict(
            cf_rel_err=abs(ref_design["cf"] - cf_ittc57(ref_design["re"])) / cf_ittc57(ref_design["re"]),
            rt_additivity_rel_err=abs(ref_design["rt"] - (ref_design["rf_1pk"] + ref_design["rw"]
                                    + ref_design.get("rb", 0.0) + ref_design.get("rtr", 0.0)
                                    + ref_design.get("ra", 0.0))) / ref_design["rt"],
            keys=sorted(ref_design),
        )
    return data


# ── 裁决 ────────────────────────────────────────────
def adjudicate(data: dict) -> dict:
    d = data.get("design") or {}
    verdicts = {}
    for key in ("A1", "A2", "A3", "A4", "A5"):
        if key in d:
            verdicts[key] = {"passed": bool(d[key].get("passed")), "detail": d[key]}
    if "A6" in d:
        verdicts["A6"] = {"passed": bool(d["A6"]["passed_live"]),
                          "passed_reference_impl": bool(d["A6"]["passed_ref"]),
                          "three_way_agree": bool(d["A6"]["three_way_agree"]),
                          "detail": d["A6"]}
    a8 = data.get("A8") or {}
    if a8:
        verdicts["A8"] = {"passed": bool(a8.get("passed")),
                          "passed_reference_impl": bool(a8.get("passed_ref")),
                          "detail": a8}

    attribution = None
    if "A6" in d and "A7" in d and a8:
        a6, a7 = d["A6"], d["A7"]
        live_ok = verdicts.get("A8", {}).get("passed")
        ref_ok = verdicts.get("A8", {}).get("passed_reference_impl")
        if not a6["three_way_agree"]:
            attribution = dict(
                verdict="THREE_WAY_DIVERGENCE",
                locus=("live endpoint residual term (Rr/Cr)" if not live_ok and ref_ok
                       else "unresolved: both internal paths deviate"),
                evidence=dict(
                    rt_live_kN=a6["rt_live_kN"], rt_ref_kN=a6["rt_ref_kN"], rt_gate_kN=a6["rt_gate_kN"],
                    err_live_pct=a6["err_live_vs_gate_pct"], err_ref_pct=a6["err_ref_vs_gate_pct"],
                    err_live_vs_ref_pct=a6["err_live_vs_ref_pct"],
                    ct_live=a7["live_ct"], ct_ref=a7["ref_ct"], ct_ratio=a7["ct_ratio"],
                    live_res_share=a7["live_res_share"], ref_res_share=a7["ref_res_share"]),
                exonerated=[k for k in ("A1", "A2", "A3", "A4") if verdicts.get(k, {}).get("passed")],
                live_low_froude_collapse=(a8.get("live") or {}).get("collapse"),
                ref_low_froude_collapse=(a8.get("ref") or {}).get("collapse"),
            )
    return dict(verdicts=verdicts, attribution=attribution,
                all_passed=all(v["passed"] for v in verdicts.values()))


# ── 图 ──────────────────────────────────────────────
def render_figure(data: dict, out: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sweep = [s for s in data["sweep"] if "error" not in s]
    refs = [s for s in data["ref_sweep"] if "error" not in s]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.8))

    ax = axes[0]
    if sweep:
        ax.plot([s["speed_kn"] for s in sweep], [s["rt"] for s in sweep], "o-", lw=2,
                color="#c0392b", label="live POST /calculations/resistance")
    if refs:
        ax.plot([s["speed_kn"] for s in refs], [s["rt"] / 1e3 for s in refs], "s--", lw=2,
                color="#1f6feb", label="in-repo Holtrop-1984 reference kernel")
    g_rt = float(data["gate"]["reference_rt"])
    tol = float(data["gate"].get("tolerance_pct", 15.0))
    ax.axhline(g_rt, color="k", lw=1.2, label=f"gate reference {g_rt:.0f} kN")
    ax.fill_between([4, 19], g_rt * (1 - tol / 100), g_rt * (1 + tol / 100),
                    color="k", alpha=0.12, label=f"±{tol:.0f}% acceptance band")
    ax.set_xlabel("ship speed [kn]"); ax.set_ylabel("total resistance RT [kN]")
    ax.set_title("RT(V): live endpoint vs reference kernel vs gate")
    ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="upper left")

    ax = axes[1]
    if sweep:
        ax.plot([s["fn"] for s in sweep], [s["cr"] for s in sweep], "o-", color="#c0392b",
                label="live Cr = Rr /(½ρV²S)")
    if refs:
        ax.plot([s["speed_ms"] / math.sqrt(G * data["ship"]["length"]) for s in refs],
                [s.get("cr", (s.get("rw", 0.0) / max(s.get("s", 1.0), 1e-9) / (0.5 * RHO_SEA * s["speed_ms"] ** 2)))
                 for s in refs], "s--", color="#1f6feb", label="reference kernel Cr")
    ax.set_xlabel("Froude number Fn"); ax.set_ylabel("residual resistance coefficient Cr")
    ax.set_title("Cr(Fn): residual term localization")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)

    fig.suptitle("m1868 · KVLCC2 residual-resistance anchor audit", fontsize=11)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    return str(out)


# ── 文档渲染（数字全部从 ledger 读，零手抄）───────
def render_docs(data: dict, adj: dict) -> str:
    """委托 tools/audit/m1868_render.py（数字一律从台账注入）。"""
    from tools.audit.m1868_render import render_docs as _render
    return _render(data, adj)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="复用既有 ledger，不重新探活")
    ap.add_argument("--no-docs", action="store_true")
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    if args.offline:
        data = json.loads(LEDGER.read_text())
    else:
        data = collect()
        adj = adjudicate(data)
        data["adjudication"] = adj
        fig = render_figure(data, ART / "resistance_anchor.png")
        data["figure"] = fig
    small = {k: v for k, v in data.items() if k not in ("sweep", "ref_sweep")}
    small["sweep"] = data.get("sweep", [])
    small["ref_sweep"] = data.get("ref_sweep", [])
    LEDGER.write_text(json.dumps(small, indent=2, ensure_ascii=False))
    if not args.no_docs:
        doc = render_docs(data, data.get("adjudication", {}))
        (ROOT / "docs" / "audit").mkdir(parents=True, exist_ok=True)
        path = ROOT / "docs" / "audit" / "m1868_residual_resistance_anchor.md"
        path.write_text(doc)
        print("docs ->", path)
    print(json.dumps(small.get("adjudication", {}), ensure_ascii=False, indent=2)[:2500])
    print("design:", json.dumps(small.get("design", {}), ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())