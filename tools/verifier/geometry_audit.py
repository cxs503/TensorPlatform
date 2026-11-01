"""几何/静水力域验证欠账核销工具 (geometry & hydrostatics verification audit).

背景
----
平台规则「几何必须先验证 Cb/L/B」；能力台账（报告 1148）里 6 项 geometry.* 能力全部
UNVERIFIED。本工具把该域从「自述 REAL」变成「可复算的门禁判决」：

  1) 解析自检 (analytic self-benchmarks)：箱型驳 (Cb=Cm=Cp=Cw=1) 与 Wigley 抛物型船
     (Cb=4/9, Cp=2/3, Cw=2/3) 的闭式值 —— 先证明「我们自己的积分器是对的」，
     再拿它去判平台。
  2) 独立复算：不信任平台的 Cb 汇总值，直接取 /geometry/offsets/{name} 的型值表
     (21 站 x 11 水线，半宽按 B/2 归一) 用二维 Simpson 自行积分，得
     Cb / Cm / Cp / Cw / LCB，与平台 /geometry/hydro-at 的回报逐项比对。
  3) 文献基准：KCS Cb=0.6505 (KRISO)、KVLCC2 Cb=0.8098 (MOERI)、
     Wigley Cb=4/9=0.4444 (解析)。文献项只登记 Cb（主尺度 L/B/T 同源），
     Cm/Cp 文献值本单不臆造 → 记 WITHHELD。
  4) 六道门禁 G1..G6（见 GATES），任一核心门未过即 exit 1。

用法
----
    python3 tools/verifier/geometry_audit.py            # 实时探 :8001
    python3 tools/verifier/geometry_audit.py --offline  # 只用抓好的快照 (可复现)
    python3 tools/verifier/geometry_audit.py --write     # 落盘 JSON 工件

产物: artifacts/geometry_audit/geometry_audit.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "artifacts", "geometry_audit")
SNAPSHOT = os.path.join(OUTDIR, "raw_probe.json")
API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")

# 平台自定精度门: 关键量对基准误差 >15% 不可接受; 严档 3% 用于「同源一致性」
TOL_HARD = 0.15
TOL_TIGHT = 0.03

# 文献/解析基准 (只登记有出处的量; 未登记的量为 None = WITHHELD, 不臆造)
LITERATURE = {
    "KCS": {"L": 230.0, "B": 32.2, "T": 10.8, "Cb": 0.6505,
            "src": "KRISO KCS (SIMMAN/T2015 官方主尺度), Cb=0.6505"},
    "KVLCC2": {"L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098,
               "src": "MOERI KVLCC2 (SIMMAN 2008 官方主尺度), Cb=0.8098"},
    "Wigley": {"L": 1.0, "B": 0.1, "T": 0.0625, "Cb": 4.0 / 9.0,
               "src": "Wigley paraboloid 解析: y=(B/2)(1-(2x/L)^2)(1-(z/T)^2) → Cb=4/9"},
}


# ─────────────────────────── 数值积分内核 ───────────────────────────
def simpson(y, h):
    """复合 Simpson 1/3；点数偶数时末段退化为梯形。精确到三次多项式。"""
    n = len(y)
    if n < 2:
        return 0.0
    if n % 2 == 1:
        s = y[0] + y[-1] + 4.0 * sum(y[1:-1:2]) + 2.0 * sum(y[2:-2:2])
        return s * h / 3.0
    s = simpson(y[:-1], h)
    return s + 0.5 * (y[-2] + y[-1]) * h


def integrate_table(table):
    """对归一化型值表 (ns x nw, u = 半宽/(B/2), ξ=站, ζ=水线) 做二维积分。

    返回 dict:
      Cb   = ∫∫ u dζ dξ                       (= ∇/(L·B·T), 全宽由 2·(u·B/2) 抵消)
      Cw   = ∫ u(ξ, ζ=1) dξ                    (= Awp/(L·B))
      Cm   = ∫ u(ξ=0.5, ζ) dζ                  (中横剖面系数)
      Cp   = Cb/Cm
      xi_bar = ∫∫ ξ·u / ∫∫ u                   (浮心相对站位, 自 ξ=0 端起算)
      A_stations = 各站 A/(B·T)                (用于画邦戎曲线/剖面面积曲线)
    """
    ns, nw = len(table), len(table[0])
    if ns < 3 or nw < 3:
        raise ValueError("型值表至少 3x3 才能 Simpson 积分")
    hx, hz = 1.0 / (ns - 1), 1.0 / (nw - 1)

    cb = simpson([simpson(row, hz) for row in table], hx)
    cw = simpson([table[i][-1] for i in range(ns)], hx)
    mid = table[ns // 2]
    cm = simpson(mid, hz)
    mom = simpson([simpson([table[i][j] for j in range(nw)], hz) * (i * hx) for i in range(ns)], hx)
    areas = [simpson(row, hz) for row in table]
    return {
        "Cb": cb, "Cw": cw, "Cm": cm, "Cp": cb / cm if cm else float("nan"),
        "xi_bar": mom / cb if cb else float("nan"),
        "A_stations": areas,
    }


# ─────────────────────────── 解析自检 ───────────────────────────
def _grid(ns=21, nw=11, fn=None):
    return [[fn(i / (ns - 1), j / (nw - 1)) for j in range(nw)] for i in range(ns)]


def analytic_selfcheck():
    """积分器必须先过闭式解，才允许它去判平台。"""
    cases = []

    box = integrate_table(_grid(fn=lambda xi, ze: 1.0))
    cases.append(("箱型驳 Cb", box["Cb"], 1.0))
    cases.append(("箱型驳 Cw", box["Cw"], 1.0))
    cases.append(("箱型驳 Cm", box["Cm"], 1.0))

    # Wigley 型 (非退化: 水线在 ζ=1 处仍有宽度): u=(1-(2ξ-1)^2)·(1-ζ²/4)
    wig = integrate_table(_grid(fn=lambda xi, ze: (1 - (2 * xi - 1) ** 2) * (1 - ze ** 2 / 4.0)))
    cases.append(("Wigley Cb", wig["Cb"], 4.0 / 9.0 * (11.0 / 12.0) * (3.0 / 2.0)))
    # 上式即 (2/3)·(11/12)=0.6111: 纵向 2/3, 垂向 1-1/12=11/12
    cases.append(("Wigley Cp", wig["Cp"], 2.0 / 3.0))
    cases.append(("Wigley Cw", wig["Cw"], (2.0 / 3.0) * (3.0 / 4.0)))
    cases.append(("Wigley xi_bar", wig["xi_bar"], 0.5))

    # 棱柱驳 + 抛物线首尾: 纯纵向三次以内 → Simpson 精确
    prism = integrate_table(_grid(fn=lambda xi, ze: 1.0 - (2 * xi - 1) ** 2))
    cases.append(("棱柱驳 Cb", prism["Cb"], 2.0 / 3.0))

    out, ok = [], True
    for name, got, want in cases:
        err = abs(got - want) / abs(want) if want else abs(got)
        passed = err < 1e-9
        ok &= passed
        out.append({"case": name, "got": got, "want": want, "rel_err": err, "pass": passed})
    return {"pass": ok, "cases": out}


# ─────────────────────────── 平台探测 ───────────────────────────
def _get(path, timeout=60):
    try:
        req = urllib.request.Request(API + path)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "data": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        return {"ok": False, "err": f"HTTP {e.code}", "body": e.read().decode()[:300]}
    except Exception as e:  # 超时/拒连 → 记 WITHHELD，不编数
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


MODELS = ["KCS", "KVLCC2", "Wigley"]
DRAFT_FRACS = [0.2, 0.4, 0.6, 0.8, 1.0, 1.1]  # × 各型设计吃水


def probe_live():
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "api": API, "probes": {}, "draft_sweep": {}}
    for n in MODELS:
        out["probes"][n] = {
            "geometry": _get(f"/geometry/{n}"),
            "verify": _get(f"/geometry/verify/{n}"),
            "offsets": _get(f"/geometry/offsets/{n}"),
        }
        T = LITERATURE[n]["T"]
        out["draft_sweep"][n] = {str(round(f * T, 4)): _get(f"/geometry/hydro-at/{n}?draft={f * T}")
                                for f in DRAFT_FRACS}
    return out


def load_snapshot():
    with open(SNAPSHOT) as f:
        return json.load(f)


# ─────────────────────────── 门禁 ───────────────────────────
def evaluate(snap):
    rows, gates = [], {}

    # ── 每型: 独立积分 vs 平台回报 vs 文献 ──
    for name in MODELS:
        p = snap["probes"].get(name, {})
        geo = (p.get("geometry") or {}).get("data") or {}
        g = geo.get("geometry") or {}
        off = (p.get("offsets") or {}).get("data") or {}
        table = off.get("table") or []
        lit = LITERATURE.get(name, {})
        rec = {"model": name,
               "declared": {k: g.get(k) for k in ("L", "B", "T", "Cb")},
               "literature_Cb": lit.get("Cb"), "literature_src": lit.get("src")}
        if table and len(table) >= 3:
            ind = integrate_table(table)
            rec["independent"] = {k: ind[k] for k in ("Cb", "Cm", "Cp", "Cw", "xi_bar")}
            rec["offsets_shape"] = [len(table), len(table[0])]
            rec["zeta0_all_zero"] = all(abs(row[0]) < 1e-12 for row in table)
            rec["zeta1_max"] = max(row[-1] for row in table)
        hydro = ((snap["draft_sweep"].get(name) or {}).get(str(round(lit.get("T", 10.8), 4))) or {}).get("data")
        if hydro:
            hs = hydro["hydrostatics"]
            rec["api_hydro"] = {k: hs.get(k) for k in
                                ("Cb", "Awp", "LCB", "LCB_from_fp", "VCB", "displacement_volume", "draft")}
            rec["api_hydro"]["Cb_norm"] = hydro["hydrostatics"]["Cb"]
        rows.append(rec)

    # G1 物理可容许性: 0 < Cb <= 1 (任何吃水) —— 唯一的硬物理约束
    bad = []
    for name in MODELS:
        for d, v in (snap["draft_sweep"].get(name) or {}).items():
            if v.get("ok"):
                cb = v["data"]["hydrostatics"]["Cb"]
                if not (0.0 < cb <= 1.0):
                    bad.append({"model": name, "draft": float(d), "Cb": cb})
    gates["G1_Cb_physical"] = {"pass": not bad, "violations": bad,
                               "rule": "0 < Cb <= 1.0（Cb>1 即几何/积分仿射错误）"}

    # G2 声明 Cb vs 文献 Cb
    g2 = []
    for r in rows:
        if r["literature_Cb"] is None or not r["declared"]["Cb"]:
            continue
        err = abs(r["declared"]["Cb"] - r["literature_Cb"]) / r["literature_Cb"]
        g2.append({"model": r["model"], "declared": r["declared"]["Cb"],
                   "lit": r["literature_Cb"], "rel_err": err, "pass": err <= TOL_HARD})
    gates["G2_declared_vs_literature"] = {"pass": all(x["pass"] for x in g2), "items": g2,
                                         "rule": f"|Cb_declared-Cb_lit|/Cb_lit <= {TOL_HARD:.0%}"}

    # G3 服务端 Cb(hydro-at, 设计吃水) vs 文献
    g3 = []
    for r in rows:
        api = r.get("api_hydro")
        if not api or r["literature_Cb"] is None:
            continue
        err = abs(api["Cb"] - r["literature_Cb"]) / r["literature_Cb"]
        g3.append({"model": r["model"], "api": api["Cb"], "lit": r["literature_Cb"],
                   "rel_err": err, "pass": err <= TOL_HARD})
    gates["G3_served_Cb_vs_literature"] = {"pass": all(x["pass"] for x in g3), "items": g3,
                                           "rule": f"served Cb 对文献误差 <= {TOL_HARD:.0%}"}

    # G4 吃水参数有效性: hydro-at 必须随 draft 变化 (死参数 = 静默失效)
    g4 = []
    for name in MODELS:
        sw = snap["draft_sweep"].get(name) or {}
        vols = [(float(d), v["data"]["hydrostatics"]["displacement_volume"])
                for d, v in sw.items() if v.get("ok")]
        if len(vols) < 2:
            continue
        vols.sort()
        distinct = len({round(v, 3) for _, v in vols})
        monotone = all(vols[i][1] <= vols[i + 1][1] + 1e-6 for i in range(len(vols) - 1))
        g4.append({"model": name, "distinct_volumes": distinct, "monotone": monotone,
                   "pass": distinct >= 3 and monotone,
                   "volumes": {d: round(v, 1) for d, v in vols}})
    gates["G4_draft_parameter_live"] = {"pass": all(x["pass"] for x in g4) if g4 else None,
                                        "items": g4,
                                        "rule": "排水体积随吃水单调变化且至少 3 个不同值"}

    # G5 恒等式 Cb = Cm·Cp (在同一型值表上), 用独立积分值
    g5 = []
    for r in rows:
        ind = r.get("independent")
        if not ind:
            continue
        err = abs(ind["Cb"] - ind["Cm"] * ind["Cp"]) / ind["Cb"]
        g5.append({"model": r["model"], "Cb": ind["Cb"], "Cm*Cp": ind["Cm"] * ind["Cp"],
                   "rel_err": err, "pass": err <= TOL_TIGHT})
    gates["G5_identity_Cb_eq_Cm_Cp"] = {"pass": all(x["pass"] for x in g5) if g5 else None,
                                        "items": g5, "rule": "|Cb-Cm·Cp|/Cb <= 3% (独立性自检)"}

    # G6 平台自带 verify 端点必须非空且判据收紧
    g6 = []
    for name in MODELS:
        v = snap["probes"].get(name, {}).get("verify")
        if v is None:
            continue      # 该型未探 → 不判 (未探 ≠ 空转)
        if not v.get("ok"):
            g6.append({"model": name, "vacuous": True, "pass": False, "note": v.get("err")})
            continue
        d = v["data"]
        checked = d.get("stations_checked") or 0
        mx = d.get("max_error_pct")
        g6.append({"model": name, "stations_checked": checked, "max_error_pct": mx,
                   "vacuous": checked == 0,
                   "pass": checked > 0 and mx is not None and mx / 100.0 <= TOL_HARD})
    gates["G6_self_verify_nonvacuous"] = {"pass": all(x["pass"] for x in g6) if g6 else None,
                                          "items": g6,
                                          "rule": "stations_checked>0 且 max_error<=15%；空结果=无效自检"}

    # 独立积分 vs 服务的 Cb (同源一致性, 非独立基准, 仅记录)
    consistency = []
    for r in rows:
        if r.get("independent") and r.get("api_hydro"):
            a, b = r["api_hydro"]["Cb"], r["independent"]["Cb"]
            consistency.append({"model": r["model"], "api_Cb": a, "independent_Cb": b,
                                "rel_err": abs(a - b) / b if b else None})

    # G7 同一几何两条路径一致性 (offsets 积分 vs hydro-at 截面生成)
    g7 = []
    for r in rows:
        if r.get("independent") and r.get("api_hydro"):
            a, b = r["api_hydro"]["Cb"], r["independent"]["Cb"]
            err = abs(a - b) / b if b else None
            g7.append({"model": r["model"], "api_Cb": a, "independent_Cb": b,
                       "rel_err": err, "pass": err is not None and err <= TOL_TIGHT})
    gates["G7_offsets_vs_served_Cb"] = {"pass": all(x["pass"] for x in g7) if g7 else None,
                                        "items": g7,
                                        "rule": "型值表独立积分 vs 服务端 Cb 收敛 <= 3% (同一几何不得两说)"}

    self_check = analytic_selfcheck()
    hard_fail = [k for k, v in gates.items() if v["pass"] is False]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": snap.get("api", API), "probe_ts": snap.get("ts"),
        "analytic_selfcheck": self_check,
        "models": rows,
        "gates": gates,
        "api_vs_independent_Cb": consistency,
        "tolerances": {"hard": TOL_HARD, "tight": TOL_TIGHT},
        "verdict": {
            "gates_total": len(gates),
            "gates_failed": hard_fail,
            "all_pass": self_check["pass"] and not hard_fail,
            "evidence_level": "MOCK" if hard_fail else "REAL",
            "reason": ("物理不可容许输出(Cb>1)/死参数/自检空转 → 端点证据等级降为 MOCK"
                       if hard_fail else "解析自检+文献+恒等式全过"),
        },
    }


def print_report(res):
    sc = res["analytic_selfcheck"]
    print("==" * 34)
    print("① 解析自检（积分器必须先过闭式解）")
    for c in sc["cases"]:
        print(f"  {'PASS' if c['pass'] else 'FAIL'} {c['case']:14s} got={c['got']:.10f} want={c['want']:.10f}")
    print(f"  → selfcheck {'PASS' if sc['pass'] else 'FAIL'}")

    print("\n② 平台回报 vs 文献 vs 独立积分")
    hdr = f"  {'model':9s} {'decl_Cb':>8s} {'lit_Cb':>8s} {'api_Cb':>8s} {'ind_Cb':>8s} {'ind_Cm':>7s} {'ind_Cp':>7s} {'ind_Cw':>7s}"
    print(hdr)
    for r in res["models"]:
        ind = r.get("independent") or {}
        api = r.get("api_hydro") or {}
        lit = r.get("literature_Cb")
        print(f"  {r['model']:9s} {str(r['declared']['Cb']):>8s} "
              f"{(f'{lit:.4f}' if lit else 'n/a'):>8s} "
              f"{(f'{api.get(chr(67)+chr(98)):.4f}' if api else 'n/a'):>8s} "
              f"{(f'{ind.get(chr(67)+chr(98)):.4f}' if ind else 'n/a'):>8s} "
              f"{(f'{ind.get(chr(67)+chr(109)):.4f}' if ind else 'n/a'):>7s} "
              f"{(f'{ind.get(chr(67)+chr(112)):.4f}' if ind else 'n/a'):>7s} "
              f"{(f'{ind.get(chr(67)+chr(119)):.4f}' if ind else 'n/a'):>7s}")

    print("\n③ 门禁")
    for k, v in res["gates"].items():
        state = {True: "PASS", False: "FAIL", None: "WITHHELD"}[v["pass"]]
        print(f"  {state:8s} {k:30s} {v['rule']}")

    print("\n④ 判定:", res["verdict"]["evidence_level"], "| failed gates:", res["verdict"]["gates_failed"])
    print("==" * 34)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="只用快照, 不探活")
    ap.add_argument("--write", action="store_true", help="落盘 JSON 工件")
    args = ap.parse_args(argv)

    os.makedirs(OUTDIR, exist_ok=True)
    snap = None
    if not args.offline:
        snap = probe_live()
        with open(SNAPSHOT, "w") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
    if snap is None:
        snap = load_snapshot()
    res = evaluate(snap)
    print_report(res)
    if args.write:
        path = os.path.join(OUTDIR, "geometry_audit.json")
        with open(path, "w") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("工件:", os.path.relpath(path, ROOT))
    return 0 if res["verdict"]["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())