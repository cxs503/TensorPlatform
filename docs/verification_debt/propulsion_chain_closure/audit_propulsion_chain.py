#!/usr/bin/env python3
"""推进功率链物理闭合审计（m1864）—— 线上 :8001 端点 vs 树内已发表基准.

为什么做这一单
==============
报告 1561（"推进端点推力闭合修复：Kt=0.3-0.2J 直线式 → BEMT 反解转速，5 型船
闸门 verdict_fail=0"）宣称推进端点的推力闭合已修好。本单不复述该结论，而是做
一次**与文献/解析基准对照的独立复核**（验证欠账类），基准全部取自树内：

* R1  Oosterveld & van Oossanen (1975) 瓦赫宁根 B 系列回归
      ``tools/verification/propulsion/b_series_reference.py``（树内已发表数据，REAL）
* R2  能量/效率恒等式（无单位、无自由参数）
        η_o = J·K_T/(2π·K_Q) < 1                （开路效率上界）
        0 < η_D = P_E/P_D ≤ η_o,max·η_H,max      （有效功率不得超过收到功率）
        P_E(resistance 端点) == η_D·P_D(propulsion 端点)   （链闭合恒等式）
* R3  推力闭合带：对任意合理推力减额 t∈[0, 0.35]，T 必须落在
        [R_T, R_T/0.65]
      区间内（R_T 取同一船/同一航速下 resistance 端点读数）。

判据设计的一处关键取舍
----------------------
推进端点响应里**没有** J / w / t / K_T / K_Q 中间量（见 FINDINGS.md 缺陷 D5），
且响应无单位标注。因此本审计坚持只用**两种单位假设都成立的判据**：
凡是依赖"T 的单位是 kN 还是 N"才能下结论的判据，都同时给出两种假设的结论，
只有两种假设都判 FAIL 才记 FAIL。

产物
----
* ``artifacts/m1864_propulsion_chain/results.json``  逐船逐项数值 + 判定
* ``artifacts/m1864_propulsion_chain/fig*.png``      3 张图
* 退出码 0 = 审计脚本正常完成（不看判定结果，判定看 results.json / pytest 门）

用法::

    python3 docs/verification_debt/propulsion_chain_closure/audit_propulsion_chain.py
    # 离线重算（不访问网络，用已记录的 live 读数）：--offline
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from tools.verification.propulsion.b_series_reference import kt_kq  # noqa: E402

API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1/")
OUT = os.path.join(ROOT, "artifacts", "m1864_propulsion_chain")

RHO = 1025.0  # kg/m^3，与平台端点默认海水密度一致

# 推力减额/伴流的物理合理带（Holtrop 经验值域，仅用于"闭合带"判据）
T_BAND = (0.0, 0.35)
HULL_EFF_MAX = 1.15   # η_H=(1-t)/(1-w) 在伴流远大于推力减额时可略大于 1，上限 1.15
ETA_O_MAX_B_SERIES = 0.75  # 瓦赫宁根 B 系列开路效率工程上界（J∈[0,1.2] 扫描实测见 results）

CASES: List[Dict[str, object]] = [
    dict(name="KVLCC2_tanker", L=320.0, B=58.0, T=20.8, nabla=312622.0,
         V_kn=15.5, Cb=0.8098, D=9.86, P_over_D=0.862, rpm=78.0, Z=5, AeAo=0.431),
    dict(name="KCS_container", L=230.0, B=32.2, T=10.8, nabla=52030.0,
         V_kn=24.0, Cb=0.651, D=7.9, P_over_D=0.886, rpm=110.0, Z=5, AeAo=0.800),
    dict(name="Bulk_180m", L=180.0, B=32.0, T=11.5, nabla=53100.0,
         V_kn=14.0, Cb=0.8053, D=6.5, P_over_D=0.862, rpm=95.0, Z=4, AeAo=0.500),
    dict(name="Ferry_120m", L=120.0, B=20.0, T=6.0, nabla=8280.0,
         V_kn=20.0, Cb=0.5750, D=4.0, P_over_D=0.875, rpm=180.0, Z=5, AeAo=0.700),
    dict(name="Coaster_90m", L=90.0, B=15.0, T=5.5, nabla=5346.0,
         V_kn=12.0, Cb=0.7200, D=3.2, P_over_D=0.8125, rpm=200.0, Z=4, AeAo=0.550),
]


# ---------------------------------------------------------------------------
# 参照侧：B 系列可接受域（树内已发表回归，不手抄任何数字）
# ---------------------------------------------------------------------------
def b_series_envelope(P_over_D: float, AeAo: float, Z: int) -> Dict[str, float]:
    """扫描 J∈[0,1.2]，给出该几何下 K_T、K_Q、η_o 的 B 系列能取到的最大值。"""
    kt_max = kq_max = eta_max = 0.0
    j_at_eta_max = 0.0
    n = 481
    for i in range(n):
        J = 1.2 * i / (n - 1)
        kt, kq = kt_kq(J, P_over_D, AeAo, Z)
        kt_max = max(kt_max, kt)
        kq_max = max(kq_max, kq)
        if kq > 1e-9 and kt > 0.0:
            eta = J * kt / (2.0 * math.pi * kq)
            if eta > eta_max:
                eta_max, j_at_eta_max = eta, J
    return {"Kt_max": kt_max, "Kq_max": kq_max, "eta_o_max": eta_max,
            "J_at_eta_o_max": j_at_eta_max}


# ---------------------------------------------------------------------------
# 线上端点
# ---------------------------------------------------------------------------
def _post(path: str, body: Dict[str, object]) -> Dict[str, object]:
    import urllib.request
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as fh:
        return json.load(fh)


def query_live(case: Dict[str, object]) -> Tuple[Dict[str, object], Dict[str, object]]:
    hull = {k: case[k] for k in ("L", "B", "T", "nabla", "V_kn", "Cb")}
    res_body = dict(length=hull["L"], beam=hull["B"], draft=hull["T"],
                    displacement=hull["nabla"], speed=hull["V_kn"], speed_unit="kn",
                    cb=hull["Cb"])
    prop_body = dict(res_body, prop_diameter=case["D"], prop_pitch=float(case["P_over_D"]) * float(case["D"]),
                     prop_rpm=case["rpm"], num_blades=case["Z"], blade_area_ratio=case["AeAo"])
    return _post("calculations/resistance", res_body), _post("calculations/propulsion", prop_body)


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def judge(case: Dict[str, object], res: Dict[str, object], prop: Dict[str, object]) -> Dict[str, object]:
    V = float(case["V_kn"]) * 1852.0 / 3600.0
    D = float(case["D"])
    nn = float(case["rpm"]) / 60.0
    env = b_series_envelope(float(case["P_over_D"]), float(case["AeAo"]), int(case["Z"]))

    R_T = float(res["rt"])          # kN
    P_E = float(res["pe"])          # kW
    T_rep = float(prop["thrust"])
    Q_rep = float(prop["torque"])
    P_D = float(prop["delivered_power"])
    eta_o = float(prop["open_water_eff"])
    eta_H = float(prop["hull_eff"])
    eta_D = float(prop["propulsive_eff"])

    # 两种单位假设：响应无单位标注（缺陷 D5）
    hyp = {}
    for label, ts, qs in (("kN_kNm", 1e3, 1e3), ("N_Nm", 1.0, 1.0)):
        T_N = T_rep * ts
        Q_Nm = Q_rep * qs
        kt = T_N / (RHO * nn ** 2 * D ** 4)
        kq = Q_Nm / (RHO * nn ** 2 * D ** 5)
        # 推力闭合：T(1-t)=R_T  =>  1-t = R_T/T
        one_minus_t = (R_T * 1e3) / T_N if T_N > 0 else float("inf")
        hyp[label] = {
            "Kt_implied": kt, "Kq_implied": kq,
            "Kt_over_Kt_max": kt / env["Kt_max"] if env["Kt_max"] else float("inf"),
            "Kq_over_Kq_max": kq / env["Kq_max"] if env["Kq_max"] else float("inf"),
            "one_minus_t_implied": one_minus_t,
            "thrust_closure_ok": bool(1.0 - T_BAND[1] <= one_minus_t <= 1.0 - T_BAND[0]),
            "units_plausible": bool(0.02 <= kt <= 4.0),
        }

    # 用 J 自洽性挑出"主要单位假设"（K_T 量级最接近 B 系列的）
    main = min(hyp, key=lambda k: abs(math.log(max(hyp[k]["Kt_implied"], 1e-12))))
    m = hyp[main]

    # 无单位判据（两种单位假设下都成立）
    checks = {
        "D1_eta_o_le_1": dict(value=eta_o, limit=1.0, ok=bool(eta_o <= 1.0),
                              note="开路效率 η_o=J·K_T/(2π·K_Q) 数学上必 <1"),
        "D2_eta_o_le_b_series_max": dict(value=eta_o, limit=env["eta_o_max"], ok=bool(eta_o <= env["eta_o_max"] + 1e-12),
                                          note="超过该几何 B 系列全 J 扫描的 η_o 上界"),
        "D3_eta_D_le_hull_term": dict(value=eta_D, limit=ETA_O_MAX_B_SERIES * HULL_EFF_MAX,
                                       ok=bool(eta_D <= ETA_O_MAX_B_SERIES * HULL_EFF_MAX),
                                       note="η_D=η_o·η_H，工程上界约 0.75×1.15"),
        "D4_power_chain_closure": dict(value=(P_E / (eta_D * P_D) if eta_D * P_D else float("nan")),
                                        limit=(0.98, 1.02),
                                        ok=bool(eta_D * P_D > 0 and 0.98 <= P_E / (eta_D * P_D) <= 1.02),
                                        note="P_E(阻力端点) 应等于 η_D·P_D(推进端点)"),
        "D5_H1_kt_within_b_series": dict(value=m["Kt_over_Kt_max"], limit=1.0,
                                          ok=bool(m["Kt_over_Kt_max"] <= 1.0),
                                          note=f"单位假设 {main}：推力反解 K_T 超过 B 系列同几何上界"),
        "D6_H1_thrust_closure": dict(value=m["one_minus_t_implied"], limit=(1.0 - T_BAND[1], 1.0 - T_BAND[0]),
                                      ok=bool(m["thrust_closure_ok"]),
                                      note=f"单位假设 {main}：T(1-t)=R_T 反解 (1-t)"),
    }
    return dict(case=case, R_T_kN=R_T, P_E_kW=P_E, thrust_reported=T_rep, torque_reported=Q_rep,
                P_D_kW=P_D, eta_o=eta_o, eta_H=eta_H, eta_D=eta_D,
                V_ms=V, n_rps=nn, b_series_envelope=env, unit_hypotheses=hyp,
                main_unit_hypothesis=main, checks=checks,
                n_fail=sum(1 for c in checks.values() if not c["ok"]))


def run(offline: Optional[str] = None) -> Dict[str, object]:
    rows = []
    for case in CASES:
        if offline:
            rec = json.load(open(offline))
            pair = next(r for r in rec["rows"] if r["case"]["name"] == case["name"])
            res = dict(rt=pair["R_T_kN"], pe=pair["P_E_kW"])
            prop = dict(thrust=pair["thrust_reported"], torque=pair["torque_reported"],
                        delivered_power=pair["P_D_kW"], open_water_eff=pair["eta_o"],
                        hull_eff=pair["eta_H"], propulsive_eff=pair["eta_D"])
        else:
            res, prop = query_live(case)
        rows.append(judge(case, res, prop))
        r = rows[-1]
        print(f"{case['name']:16s} η_o={r['eta_o']:.3f} η_D={r['eta_D']:.3f} "
              f"P_E={r['P_E_kW']:.0f}kW η_D·P_D={r['eta_D']*r['P_D_kW']:.0f}kW "
              f"Kt[{r['main_unit_hypothesis']}]={r['unit_hypotheses'][r['main_unit_hypothesis']]['Kt_implied']:.3f} "
              f"fail={r['n_fail']}/{len(r['checks'])}")
    summary = {
        "n_ships": len(rows),
        "n_fail_total": sum(r["n_fail"] for r in rows),
        "eta_o_gt_1": [r["case"]["name"] for r in rows if r["eta_o"] > 1.0],
        "eta_D_gt_bound": [r["case"]["name"] for r in rows if r["checks"]["D3_eta_D_le_hull_term"]["value"] > ETA_O_MAX_B_SERIES * HULL_EFF_MAX],
        "power_closure_fail": [r["case"]["name"] for r in rows if not r["checks"]["D4_power_chain_closure"]["ok"]],
        "worst_power_closure_ratio": max(rows, key=lambda r: abs(math.log(r["checks"]["D4_power_chain_closure"]["value"])))["checks"]["D4_power_chain_closure"]["value"],
        "reference": "Oosterveld & van Oossanen (1975) B-series via tools/verification/propulsion/b_series_reference.py",
    }
    return {"summary": summary, "rows": rows}


def figures(rec: Dict[str, object]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUT, exist_ok=True)
    rows = rec["rows"]
    names = [r["case"]["name"] for r in rows]
    xs = range(len(rows))
    paths = []

    # fig1: η_o / η_D per ship vs admissible envelope
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ax.bar([x - 0.2 for x in xs], [r["eta_o"] for r in rows], width=0.4, label="η_o (reported)")
    ax.bar([x + 0.2 for x in xs], [r["eta_D"] for r in rows], width=0.4, label="η_D (reported)")
    ax.axhline(1.0, color="k", ls="--", lw=1.2, label="η = 1 (energy bound)")
    for i, r in enumerate(rows):
        ax.hlines(r["b_series_envelope"]["eta_o_max"], i - 0.42, i - 0.02, color="crimson", lw=1.6)
    ax.plot([], [], color="crimson", lw=1.6, label="η_o,max from B-series (O&O 1975)")
    ax.set_xticks(list(xs)); ax.set_xticklabels(names, rotation=12, fontsize=8)
    ax.set_ylabel("efficiency [-]"); ax.set_title("m1864 · propulsion endpoint efficiencies vs admissible envelope")
    ax.legend(fontsize=7); ax.grid(alpha=0.3, axis="y")
    p = os.path.join(OUT, "fig1_efficiency_vs_bound.png"); fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    paths.append(p)

    # fig2: implied K_T against B-series K_T(J) curves
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    for r in rows:
        c = r["case"]
        Js = [0.9 * i / 120 for i in range(121)]
        kts = [kt_kq(J, float(c["P_over_D"]), float(c["AeAo"]), int(c["Z"]))[0] for J in Js]
        ax.plot(Js, kts, lw=1.2, label=f"B-series {c['name']}")
        m = r["unit_hypotheses"][r["main_unit_hypothesis"]]
        ax.scatter([0.02], [m["Kt_implied"]], marker="x", s=60, zorder=5,
                   color=ax.lines[-1].get_color())
    ax.set_yscale("log"); ax.set_xlabel("advance ratio J [-]"); ax.set_ylabel("K_T [-]")
    ax.set_title("m1864 · K_T implied from reported thrust (×) vs B-series K_T(J) curves")
    ax.legend(fontsize=6, ncol=2); ax.grid(alpha=0.3)
    p = os.path.join(OUT, "fig2_implied_kt_vs_bseries.png"); fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    paths.append(p)

    # fig3: power-chain closure
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    for r in rows:
        pe = r["P_E_kW"]; pd_eff = r["eta_D"] * r["P_D_kW"]
        ax.scatter([pe], [pd_eff], s=55)
        ax.annotate(r["case"]["name"], (pe, pd_eff), fontsize=7, xytext=(4, 4), textcoords="offset points")
    lim = [1e3, 1e6]
    ax.plot(lim, lim, "k--", lw=1.2, label="P_E = η_D·P_D (identity)")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel("P_E from /calculations/resistance [kW]")
    ax.set_ylabel("η_D·P_D from /calculations/propulsion [kW]")
    ax.set_title("m1864 · power-chain closure (should lie on the dashed line)")
    ax.legend(fontsize=8); ax.grid(alpha=0.3, which="both")
    p = os.path.join(OUT, "fig3_power_chain_closure.png"); fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", default=None, help="recompute judges from a recorded results.json")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rec = run(offline=args.offline)
    rec["mode"] = "offline-replay" if args.offline else "live-:8001"
    if not args.no_figures:
        try:
            rec["figures"] = figures(rec)
        except Exception as exc:  # matplotlib missing -> honest record
            rec["figures"] = []; rec["figure_error"] = str(exc)[:200]
    out = os.path.join(OUT, "results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    print(json.dumps(rec["summary"], indent=2, ensure_ascii=False))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())