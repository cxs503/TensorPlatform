"""图表：混淆矩阵 + 三把刀剖面（判据数字与阈值同图，便于肉眼复核）。"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import knives  # noqa: E402


def confusion_matrix_figure(matrix: dict, path: str, title: str = "claimed vs falsified") -> str:
    rows, cols = matrix["grades_rows"], matrix["grades_cols"]
    data = np.array([[matrix["matrix"].get(r, {}).get(c, 0) for c in cols] for r in rows])
    fig, ax = plt.subplots(figsize=(7.6, 5.6))
    im = ax.imshow(data, cmap="Blues", vmin=0, vmax=max(1, data.max()))
    ax.set_xticks(range(len(cols)), cols, rotation=30, ha="right")
    ax.set_yticks(range(len(rows)), rows)
    ax.set_xlabel("falsified grade (knife verdicts)")
    ax.set_ylabel("claimed grade (audit_capabilities ledger)")
    ax.set_title(f"{title}\n(claimed REAL={matrix['claimed_real']}, "
                 f"unconfirmed rate={matrix['overclaim_rate']:.2f}, "
                 f"refuted rate={matrix['refuted_rate']:.2f})", fontsize=10)
    for i in range(len(rows)):
        for j in range(len(cols)):
            v = data[i, j]
            ax.text(j, i, str(v), ha="center", va="center",
                    color="white" if v > data.max() * 0.6 else "black",
                    fontsize=12, fontweight="bold" if v else "normal")
    fig.colorbar(im, ax=ax, label="capabilities")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def knife_profiles_figure(probe: dict, path: str) -> str:
    """左：刀 A 各驱动量的最大相对变化（log 轴，1e-12 阈值线）；
    右：刀 B 各解析/恒等式检查的相对误差（log 轴，15% 阈值线）。"""
    a_rows, b_rows, c_rows = [], [], []
    # ---- 刀 A
    for tr in probe["capabilities"]:
        for kr in tr.get("knife_results") or []:
            if kr["knife"] != "A":
                continue
            for key, st in (kr.get("per_key") or {}).items():
                if st.get("max_rel_change") is None:
                    continue
                a_rows.append((f"{tr['capability']}\n[{kr.get('driver')}:{key}]",
                               max(st["max_rel_change"], 1e-16), kr["verdict"]))
    # ---- 刀 B
    for tr in probe["capabilities"]:
        for kr in tr.get("knife_results") or []:
            if kr["knife"] != "B":
                continue
            for chk in kr.get("checks") or []:
                if chk.get("rel_error") is None:
                    continue
                b_rows.append((f"{tr['capability']}\n[{chk['id']}]",
                               max(chk["rel_error"], 1e-16), chk["verdict"]))
    # ---- 刀 C
    for tr in probe["capabilities"]:
        for kr in tr.get("knife_results") or []:
            if kr["knife"] != "C":
                continue
            worst = min((s["signed_rel_step"] for s in kr.get("steps") or []), default=0.0)
            c_rows.append((f"{tr['capability']}\n[{kr.get('series')}]",
                           worst, kr["verdict"]))

    fig, axes = plt.subplots(1, 3, figsize=(15.5, max(4.2, 0.42 * max(
        len(a_rows), len(b_rows), len(c_rows)))))
    panels = [
        (axes[0], a_rows, knives.INVARIANCE_TOL, "knife A: max relative change",
         "rel. change vs perturbation (log)"),
        (axes[1], b_rows, knives.ANALYTIC_TOL, "knife B: relative error",
         "|obs-ref|/|ref| (log)"),
    ]
    for ax, rows, thr, title, xlabel in panels:
        labels = [r[0] for r in rows][::-1]
        vals = [r[1] for r in rows][::-1]
        verdicts = [r[2] for r in rows][::-1]
        colors = ["#c0392b" if v == "KILL" else ("#27ae60" if v == "PASS" else "#f39c12")
                  for v in verdicts]
        ax.barh(range(len(vals)), vals, color=colors)
        ax.set_yticks(range(len(vals)), labels, fontsize=6.5)
        ax.set_xscale("log")
        ax.axvline(thr, color="black", ls="--", lw=1)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel(xlabel, fontsize=8)
        ax.tick_params(axis="x", labelsize=7)
    # 刀 C：有符号步长（负值即违反方向）
    ax = axes[2]
    labels = [r[0] for r in c_rows][::-1]
    vals = [r[1] for r in c_rows][::-1]
    colors = ["#c0392b" if r[2] == "KILL" else "#27ae60" for r in c_rows][::-1]
    ax.barh(range(len(vals)), vals, color=colors)
    ax.set_yticks(range(len(vals)), labels, fontsize=6.5)
    ax.axvline(0.0, color="black", lw=1)
    ax.set_title("knife C: worst signed rel. step\n(<0 violates physical monotonicity)", fontsize=9)
    ax.set_xlabel("signed rel. step", fontsize=8)
    ax.tick_params(axis="x", labelsize=7)
    fig.suptitle("Falsification knife profiles (red = KILL, green = PASS, orange = inconclusive)",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def mutant_figure(mutants: dict, path: str) -> str:
    rows = mutants["rows"]
    labels = [f"{r['mutant']}\nexpect {r['expected']} → got {r['got']}" for r in rows]
    ok = [1 if (r["expected"] == r["got"]) else 0 for r in rows]
    colors = ["#27ae60" if v else "#c0392b" for v in ok]
    fig, ax = plt.subplots(figsize=(8.4, 0.5 * len(rows) + 1.6))
    ax.barh(range(len(rows)), ok, color=colors)
    ax.set_yticks(range(len(rows)), labels, fontsize=7.5)
    ax.set_xlim(0, 1.2)
    ax.set_xlabel("1 = verdict matches pre-registered expectation")
    ax.set_title(f"instrument self-test: detection={mutants['detection_rate']:.0%} "
                 f"({mutants['n_detected']}/{mutants['n_expected_flagged']}), "
                 f"false alarm={mutants['false_alarm_rate']:.0%} "
                 f"({mutants['n_control_false_alarm']}/{mutants['n_controls']})", fontsize=9)
    for i, r in enumerate(rows):
        ax.text(1.03, i, "killed by " + "+".join(r["killed_by"]) if r["killed_by"] else "survived",
                va="center", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path