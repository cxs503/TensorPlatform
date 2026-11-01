"""操纵性证据分级审计：探针 → 判据 → artifacts（JSON + 图）.

用法::

    python3 tools/maneuver_judge/run_audit.py \
        --out artifacts/m1456_maneuver [--base http://127.0.0.1:8001]

产出：
    probe_matrix.json     原始探针（含 5xx/超时原文）
    verdicts.json         每个端点的证据等级与判据明细
    summary.md            人读摘要
    fig1_turning_geom.png 回转几何：平台输出 vs 运动学恒等式
    fig2_rudder_law.png   舵角响应律：平台 vs Nomoto D∝1/δ
    fig3_zigzag_band.png  Z 形超越角 vs 平台自带 KCS 语料区间
    fig4_evidence_matrix.png 证据矩阵
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.maneuver_judge import checks, conformance, nomoto  # noqa: E402


# ------------------------------------------------------------------ 判据装配
def _select(rows: List[Dict], endpoint: str) -> List[Dict]:
    return [r for r in rows if r["endpoint"] == endpoint]


def _n_broken(rows: List[Dict]) -> int:
    return sum(1 for r in rows if r.get("status") is None or r.get("status") >= 500)


def audit(base: str, outdir: str, timeout: float = 60.0) -> Dict:
    os.makedirs(outdir, exist_ok=True)
    hp = conformance.health(base)

    parts = {"KCS": conformance.KCS_PARTICULARS,
             "KVLCC2": conformance.KVLCC2_PARTICULARS}

    turning_rows: List[Dict] = []
    zigzag_rows: List[Dict] = []
    kt_rows: List[Dict] = []
    for name, p in parts.items():
        turning_rows += conformance.run_matrix(
            base, conformance.build_turning_matrix(p), timeout=timeout)
        zigzag_rows += conformance.run_matrix(
            base, conformance.build_zigzag_matrix(p), timeout=timeout)
        krows = conformance.kt_probe(base, p)
        for r in krows:
            r["ship"] = name
        kt_rows += krows
        for r in turning_rows + zigzag_rows:
            r.setdefault("ship", name)
        # 修正 ship 标注（run_matrix 会覆盖，这里重新按批标注）
    # 重新标注 ship（矩阵是按船顺序拼接的）
    n_t = len(conformance.build_turning_matrix(conformance.KCS_PARTICULARS))
    for i, r in enumerate(turning_rows):
        r["ship"] = "KCS" if i < n_t else "KVLCC2"
    n_z = len(conformance.build_zigzag_matrix(conformance.KCS_PARTICULARS))
    for i, r in enumerate(zigzag_rows):
        r["ship"] = "KCS" if i < n_z else "KVLCC2"

    # ---- 判据 --------------------------------------------------------
    verdicts: Dict[str, Dict] = {}

    def _groups(rows: List[Dict]):
        seen = []
        for r in rows:
            k = (r["endpoint"], r.get("ship", "?"))
            if k not in seen:
                seen.append(k)
        for k in seen:
            yield k, [r for r in rows
                      if r["endpoint"] == k[0] and r.get("ship", "?") == k[1]]

    for (ep, ship), rows in _groups(turning_rows):
        L = rows[0]["input"]["L"] if rows else 320.0
        v = checks.grade_turning(L, rows, n_broken=_n_broken(rows))
        v.update(ship=ship, n_probes=len(rows), n_broken=_n_broken(rows))
        verdicts[f"{ep}|{ship}"] = v
    for (ep, ship), rows in _groups(zigzag_rows):
        v = checks.grade_zigzag(rows, n_broken=_n_broken(rows))
        v.update(ship=ship, n_probes=len(rows), n_broken=_n_broken(rows))
        # 语料区间比对（KCS 20/20 首超越角 20~30°, typical 25）
        ok = [r for r in rows if r.get("result") and r["label"].startswith("zigzag_20")]
        if ok:
            v["detail"]["corpus"] = checks.corpus_band_check(
                ok[0]["result"]["overshoot_1"],
                {"min": 20.0, "max": 30.0, "typical": 25.0})
        verdicts[f"{ep}|{ship}"] = v

    # K-T 端点
    for (ep, ship), rows in _groups(kt_rows):
        ok = [r for r in rows if r.get("result")]
        pr = parts.get(ship, parts["KCS"])
        L, U = pr["L"], pr["speed_kn"] * 0.5144
        detail: Dict = dict(band_reference=dict(K_prime=[0.3, 3.0], T_prime=[0.3, 3.0],
                                                note="文献常见带，非精确基准"),
                            sensitivities=[], samples=[])
        for r in ok:
            K = r["result"].get("K")
            T = r["result"].get("T")
            if K is None or T is None:
                continue
            detail["samples"].append(dict(label=r["label"], K=K, T=T,
                                          K_prime=K * L / U, T_prime=T * U / L,
                                          KT=K * T))
        # 响应性：K 必须随舵面积/船型变化
        for key in ("Ar", "Cb"):
            sub = [(r["input"][key], r["result"]["K"]) for r in ok
                   if key in r["input"] and r["result"].get("K") is not None]
            if len({round(t[0], 6) for t in sub}) >= 2:
                sub.sort()
                detail["sensitivities"].append(checks.loglog_sensitivity(
                    key, [t[0] for t in sub], [t[1] for t in sub],
                    expected_sign="+").as_dict())
        no_resp = [s.get("param") for s in detail["sensitivities"] if not s["respond"]]
        nbr = _n_broken(rows)
        if nbr == len(rows):
            grade, reason = checks.Evidence.BROKEN.value, "全部调用 5xx"
        elif no_resp:
            s0 = detail["samples"][0] if detail["samples"] else {}
            grade = checks.Evidence.MOCK.value
            reason = (f"K 对 {', '.join(no_resp)} 完全无响应（K 只由 L/speed 决定）；"
                      f"且 K'={s0.get('K_prime', float('nan')):.2f}、"
                      f"T'={s0.get('T_prime', float('nan')):.2f} 超出文献带 0.3~3")
        else:
            grade, reason = checks.Evidence.WITHHELD.value, "有响应但缺精确基准"
        verdicts[f"{ep}|{ship}"] = dict(grade=grade, reason=reason, ship=ship,
                                        n_probes=len(rows), n_broken=nbr,
                                        detail=detail)

    # ---- 落盘 --------------------------------------------------------
    payload = dict(
        generated_at=__import__("datetime").datetime.utcnow().isoformat() + "Z",
        base=base, health=hp,
        particulars=parts,
        probe_matrix=dict(turning=turning_rows, zigzag=zigzag_rows, kt=kt_rows),
        verdicts=verdicts,
    )
    with open(os.path.join(outdir, "probe_matrix.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, "verdicts.json"), "w", encoding="utf-8") as f:
        json.dump(verdicts, f, ensure_ascii=False, indent=2)

    _write_summary(outdir, payload)
    _make_figures(outdir, payload)
    return payload


# ------------------------------------------------------------------ 摘要
def _write_summary(outdir: str, payload: Dict) -> None:
    lines = ["# 操纵性端点证据分级审计摘要", "",
             f"- 平台: {payload['base']}  reachable={payload['health'].get('reachable')}",
             "", "| 端点 | 等级 | 探针 | 5xx | 理由 |", "|---|---|---|---|---|"]
    for ep, v in payload["verdicts"].items():
        lines.append(f"| `{ep}` | **{v['grade']}** | {v.get('n_probes','-')} | "
                     f"{v.get('n_broken','-')} | {v['reason'][:110]} |")
    with open(os.path.join(outdir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ 图件
def _make_figures(outdir: str, payload: Dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tk = [r for r in payload["probe_matrix"]["turning"]
          if "maneuvering/turning" in r["endpoint"] and r.get("result")
          and r["ship"] == "KCS"]
    base_row = next((r for r in tk if r["label"] == "rudder_35deg"), None)

    # ---- Fig1: 回转几何 vs 运动学恒等式 --------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.6))
    if base_row:
        r = base_row["result"]
        L = base_row["input"]["L"]
        V = r["final_speed"]
        w = math.radians(r["turning_rate"])
        R_kin = V / w if w > 0 else float("nan")
        R_geom = 0.5 * r["tactical_diameter"]
        K_impl = w / math.radians(base_row["input"]["rudder_angle"])
        resp = nomoto.NomotoResponse(K_impl, 60.0)
        tr = resp.trajectory(math.radians(base_row["input"]["rudder_angle"]), V,
                             t_end=400.0, dt=0.05)
        ax[0].plot(tr["x"], tr["y"], lw=1.6, label="Nomoto K-T path (closed form)")
        ax[0].add_patch(plt.Circle((0, 0), R_kin, fill=False, ls="--", lw=1.0,
                                   color="tab:red"))
        ax[0].add_patch(plt.Circle((0, 0), R_geom, fill=False, ls=":", lw=1.2,
                                   color="tab:green"))
        ax[0].set_title(f"KCS D_T={r['tactical_diameter']:.0f} m -> R_geom={R_geom:.0f} m\n"
                        f"kinematics r=V/R gives R={R_kin:.0f} m", fontsize=9)
        ax[0].set_xlabel("x [m]"); ax[0].set_ylabel("y [m]"); ax[0].axis("equal")
        ax[0].grid(alpha=0.3); ax[0].legend(fontsize=7)

        bars = [R_geom, R_kin]
        ax[1].bar(["R from reported\nD_T/2", "R from reported\nr=V/R"], bars,
                  color=["tab:green", "tab:red"])
        for i, b in enumerate(bars):
            ax[1].text(i, b, f"{b:.0f}", ha="center", va="bottom", fontsize=9)
        rel = abs(bars[0] - bars[1]) / bars[0] * 100
        ax[1].set_ylabel("implied steady radius R [m]")
        ax[1].set_title(f"Identity A1 self-inconsistency: {rel:.0f}% (>15% fails)", fontsize=9)
        ax[1].grid(alpha=0.3, axis="y")
    fig.suptitle("Fig.1 Turning geometry: platform output vs kinematic identity", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig1_turning_geom.png"), dpi=140)
    plt.close(fig)

    # ---- Fig2: 舵角响应律 -------------------------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for ep, style in (("/api/v1/maneuvering/turning", "o-"),
                      ("/api/v1/maneuvering-mmg/turning", "s--")):
        rows = [r for r in payload["probe_matrix"]["turning"]
                if r["endpoint"] == ep and r["ship"] == "KCS"
                and r["label"].startswith("rudder_")]
        ang = [r["input"]["rudder_angle"] for r in rows if r.get("result")]
        dia = [r["result"]["tactical_diameter"] for r in rows if r.get("result")]
        if ang:
            ax[0].plot(ang, dia, style, label=ep.split("/api/v1/")[-1])
    angs = np.linspace(5, 35, 60)
    if base_row:
        d35 = base_row["result"]["tactical_diameter"]
        ax[0].plot(angs, d35 * 35.0 / angs, "k:", lw=1.4,
                   label="Nomoto law $D_T\\propto 1/\\delta$ (anchored @35°)")
    ax[0].set_xlabel("rudder angle $\\delta$ [deg]"); ax[0].set_ylabel("tactical diameter [m]")
    ax[0].set_title("Turning diameter vs rudder angle", fontsize=9)
    ax[0].legend(fontsize=7); ax[0].grid(alpha=0.3)

    labels, vals = [], []
    for s in (payload["verdicts"].get("/api/v1/maneuvering/turning|KCS", {})
              .get("detail", {}).get("sensitivities", [])):
        if isinstance(s, dict) and "loglog_slope" in s:
            labels.append(s["param"]); vals.append(s["loglog_slope"] or 0.0)
    if vals:
        ax[1].barh(labels, vals, color="tab:purple")
        ax[1].axvline(0, color="k", lw=0.8)
        ax[1].set_xlabel("log-log slope  d ln(out)/d ln(param)")
        ax[1].set_title("Sensitivity: 0 = hard-coded (no physics)", fontsize=9)
        ax[1].grid(alpha=0.3, axis="x")
    fig.suptitle("Fig.2 Falsification of rudder/parameter response", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig2_rudder_law.png"), dpi=140)
    plt.close(fig)

    # ---- Fig3: Z 形超越角 vs 语料区间 ------------------------------------
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    corpus = {"10/10": dict(first=(8, 12, 10), second=(15, 25, 20)),
              "20/20": dict(first=(20, 30, 25), second=(25, 40, 32))}
    xs = [0.0, 1.0]
    plat_1, plat_2 = [], []
    for tag in ("zigzag_10_10", "zigzag_20_20"):
        row = next((r for r in payload["probe_matrix"]["zigzag"]
                    if "maneuvering/zigzag" in r["endpoint"] and r["label"] == tag
                    and r["ship"] == "KCS" and r.get("result")), None)
        plat_1.append(row["result"]["overshoot_1"] if row else float("nan"))
        plat_2.append(row["result"]["overshoot_2"] if row else float("nan"))
    for i, tag in enumerate(("10/10", "20/20")):
        c = corpus[tag]
        for j, (key, col) in enumerate((("first", "tab:blue"), ("second", "tab:orange"))):
            lo, hi, typ = c[key]
            x = xs[i] + (j - 0.5) * 0.32
            ax.errorbar([x], [typ], yerr=[[typ - lo], [hi - typ]], fmt="s", color=col,
                        capsize=5, label=f"KCS corpus {key} overshoot" if i == 0 else None)
        ax.plot([xs[i] - 0.16], [plat_1[i]], "v", color="tab:red", ms=9,
                label="platform first overshoot" if i == 0 else None)
        ax.plot([xs[i] + 0.16], [plat_2[i]], "^", color="tab:red", ms=9,
                label="platform second overshoot" if i == 0 else None)
    ax.set_xticks(xs); ax.set_xticklabels(["10°/10° zigzag", "20°/20° zigzag"])
    ax.set_ylabel("overshoot angle [deg]")
    ax.set_title("Fig.3 Zigzag overshoot vs platform's own KCS corpus (SIMMAN 2008/2014)", fontsize=9)
    ax.legend(fontsize=7); ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig3_zigzag_band.png"), dpi=140)
    plt.close(fig)

    # ---- Fig4: 证据矩阵 ---------------------------------------------------
    fig, ax = plt.subplots(figsize=(9.0, 3.4))
    eps = list(payload["verdicts"])
    grades = [payload["verdicts"][e]["grade"] for e in eps]
    color_map = {"REAL": 3, "WITHHELD": 2, "MOCK": 1, "STUB": 0, "BROKEN": -1,
                 "NOT_SUPPORTED": -2}
    vals = np.array([[color_map.get(g, -2) for g in grades]], dtype=float)
    im = ax.imshow(vals, cmap="RdYlGn", aspect="auto", vmin=-2, vmax=3)
    ax.set_xticks(range(len(eps)))
    ax.set_xticklabels([e.replace("/api/v1/", "") for e in eps], rotation=18, ha="right",
                       fontsize=8)
    ax.set_yticks([])
    for i, g in enumerate(grades):
        ax.text(i, 0, g, ha="center", va="center", fontsize=9, fontweight="bold")
    ax.set_title("Fig.4 Evidence grade per manoeuvring endpoint", fontsize=10)
    fig.colorbar(im, ax=ax, ticks=[-2, -1, 0, 1, 2, 3],
                 label="NOT_SUP / BROKEN / STUB / MOCK / WITHHELD / REAL")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig4_evidence_matrix.png"), dpi=140)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="manoeuvring evidence audit")
    ap.add_argument("--base", default=conformance.DEFAULT_BASE)
    ap.add_argument("--out", default="artifacts/m1456_maneuver")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args(argv)
    payload = audit(a.base, a.out, timeout=a.timeout)
    for ep, v in payload["verdicts"].items():
        print(f"{ep:45s} {v['grade']:10s} {v['reason'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())