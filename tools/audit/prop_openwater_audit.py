#!/usr/bin/env python3
"""推进域敞水性能审计（判据化）：把「Wageningen B 系列端点是否物理可信」变成可机器裁决的判据集。

设计原则
--------
* 判据只依赖两类依据，避免循环论证：
  (a) **定义级恒等式**（η0 ≡ J·KT/(2π·KQ)、J→0 时 η0→0），由敞水系数的定义直接给出，
      与被审计实现无关；
  (b) **文献基准表**（docs/audit/data/propulsion_wageningen_ref.json，B4-70 / P/D=1.0 /
      Ae/Ao=0.70，出处 Oosterveld & van Oossanen 1975 系数据的平台内副本，含哈希溯源）。
* 每条判据输出 measured（实测量）、limit（阈值）、verdict（PASS/FAIL），并写进裁决 JSON，
  报告里的数字全部由本脚本产物程序化注入，不手抄。

用法
----
    python3 tools/audit/prop_openwater_audit.py \
        --probe artifacts/audit/prop_openwater_probe.json \
        --ref docs/audit/data/propulsion_wageningen_ref.json \
        --out-json artifacts/audit/prop_openwater_verdict.json \
        --out-md artifacts/audit/prop_openwater_verdict.md \
        --fig-dir docs/audit/figures
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
from collections import defaultdict

TWO_PI = 2.0 * math.pi


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_err(a: float, b: float) -> float:
    """相对误差（以 b 为基准）；b=0 时返回绝对值并标记。"""
    if b == 0:
        return float("inf") if a != 0 else 0.0
    return abs(a - b) / abs(b)


def eta0_def(j: float, kt: float, kq: float) -> float:
    """敞水效率定义式。KQ<=0 视为未定义，返回 0。"""
    if kq <= 0 or j <= 0:
        return 0.0
    return j * kt / (TWO_PI * kq)


def interp(x: float, xs: list, ys: list) -> float:
    """线性插值（xs 递增，超出范围时按端点线性外推）。"""
    if x <= xs[0]:
        i = 0
    elif x >= xs[-1]:
        i = len(xs) - 2
    else:
        i = max(k for k in range(len(xs) - 1) if xs[k] <= x)
    x0, x1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def slice_curve(samples: list, z: int, pd: float, ae: float) -> dict:
    """按 (Z, P/D, Ae/Ao) 取一条 KT/KQ/eta0 vs J 曲线，按 J 升序。"""
    rows = [s for s in samples
            if s.get("KT") is not None and s["Z"] == z
            and abs(s["P_D"] - pd) < 1e-9 and abs(s["Ae_Ao"] - ae) < 1e-9]
    rows.sort(key=lambda s: s["J"])
    return {
        "J": [r["J"] for r in rows],
        "KT": [r["KT"] for r in rows],
        "KQ": [r["KQ"] for r in rows],
        "eta0": [r["eta0"] for r in rows],
    }


# --------------------------------------------------------------------------- #
# 判据集
# --------------------------------------------------------------------------- #
def check_g1_eta_identity(samples: list) -> dict:
    """G1 定义级恒等式 η0 = J·KT/(2π·KQ) 是否在端点内部成立。

    FAIL 意味着端点连自己的三个输出量都对不上（不是"精度差"，是"不一致"）。
    """
    worst, n = 0.0, 0
    worst_at = None
    for s in samples:
        j, kt, kq, e = s["J"], s.get("KT"), s.get("KQ"), s.get("eta0")
        if None in (kt, kq, e) or j <= 0.05 or kq <= 0:
            continue
        ref = eta0_def(j, kt, kq)
        # 端点对 eta0 做了 0.85 截断，恒等式只在未截断处可比
        if abs(e - 0.85) < 1e-9:
            continue
        n += 1
        err = rel_err(e, ref)
        if err > worst:
            worst, worst_at = err, {"Z": s["Z"], "P_D": s["P_D"], "Ae_Ao": s["Ae_Ao"], "J": j,
                                   "eta0_api": e, "eta0_def": round(ref, 6)}
    return {
        "id": "G1", "name": "敞水效率定义式自洽 η0=J·KT/(2π·KQ)",
        "measured": round(worst, 6), "limit": 0.005, "unit": "-",
        "verdict": "PASS" if worst <= 0.005 else "FAIL",
        "n_samples": n, "worst_at": worst_at,
        "note": "0.85 截断样本不参与比较（截断本身另由 G7 判定）",
    }


def check_g2_eta_at_zero_j(samples: list) -> dict:
    """G2 J→0 时 η0 必须 →0（定义式分子含 J）。"""
    vals = [s["eta0"] for s in samples if s["J"] == 0.0 and s.get("eta0") is not None]
    mx = max(vals) if vals else float("nan")
    return {
        "id": "G2", "name": "J=0 时 η0 必须为 0", "measured": mx, "limit": 1e-6,
        "unit": "-", "verdict": "PASS" if (vals and mx <= 1e-6) else "FAIL", "n_samples": len(vals),
    }


def check_g3_eta_geometry_sensitivity(samples: list, j_probe: float = 0.6) -> dict:
    """G3 η0 必须随 P/D 变化：真实 B 系列在 J 固定时 η0 对不同 P/D 有明显差异。"""
    by_pd = defaultdict(list)
    for s in samples:
        if abs(s["J"] - j_probe) < 1e-9 and s["Ae_Ao"] == 0.70 and s.get("eta0") is not None:
            by_pd[s["P_D"]].append(s["eta0"])
    means = {pd: sum(v) / len(v) for pd, v in by_pd.items() if v}
    if not means:
        return {"id": "G3", "name": "η0 对 P/D 的敏感性", "measured": None,
                "limit": 0.01, "verdict": "FAIL", "n_samples": 0}
    lo, hi = min(means.values()), max(means.values())
    spread = (hi - lo) / hi if hi > 0 else 0.0
    return {
        "id": "G3", "name": f"η0 对 P/D 的敏感性（J={j_probe}, Ae/Ao=0.70）",
        "measured": round(spread, 6), "limit": 0.01, "unit": "-",
        "verdict": "PASS" if spread >= 0.01 else "FAIL",
        "n_samples": len(means), "detail": {f"P/D={k}": round(v, 6) for k, v in sorted(means.items())},
    }


def check_g4_kqkt_ratio(samples: list) -> dict:
    """G4 KQ/KT 之比必须随工况变化；恒定比值说明 KQ 是 KT 的线性复制。"""
    ratios = [s["KQ"] / s["KT"] for s in samples if s.get("KT") and s["KT"] > 1e-9 and s.get("KQ")]
    if not ratios:
        return {"id": "G4", "name": "KQ/KT 比值随工况变化", "measured": None, "limit": 0.05,
                "verdict": "FAIL", "n_samples": 0}
    mean = sum(ratios) / len(ratios)
    var = sum((r - mean) ** 2 for r in ratios) / len(ratios)
    cv = math.sqrt(var) / mean if mean else 0.0
    return {
        "id": "G4", "name": "KQ/KT 比值随工况变化（变异系数）",
        "measured": round(cv, 8), "limit": 0.05, "unit": "-",
        "verdict": "PASS" if cv >= 0.05 else "FAIL", "n_samples": len(ratios),
        "detail": {"mean_KQ_over_KT": round(mean, 8),
                   "min": round(min(ratios), 8), "max": round(max(ratios), 8)},
    }


def check_g5_monotonic(samples: list) -> dict:
    """G5 固定几何下 KT、KQ 必须随 J 单调下降（B 系列在适用域内的基本行为）。"""
    bad = []
    for z in {s["Z"] for s in samples}:
        for pd in {s["P_D"] for s in samples}:
            for ae in {s["Ae_Ao"] for s in samples}:
                c = slice_curve(samples, z, pd, ae)
                for key in ("KT", "KQ"):
                    for k in range(len(c[key]) - 1):
                        if c[key][k + 1] > c[key][k] + 1e-12:
                            bad.append({"Z": z, "P_D": pd, "Ae_Ao": ae, "key": key,
                                        "J_from": c["J"][k], "J_to": c["J"][k + 1],
                                        "from": c[key][k], "to": c[key][k + 1]})
                            break
    return {
        "id": "G5", "name": "KT、KQ 对 J 单调下降", "measured": len(bad), "limit": 0, "unit": "处",
        "verdict": "PASS" if not bad else "FAIL", "violations": bad[:10],
    }


def check_g6_zero_thrust(samples: list) -> dict:
    """G6 必须存在零推力点 J0：适用域内 KT 应降到 0（或负），而不是被下限截住。"""
    rows = []
    for pd in sorted({s["P_D"] for s in samples}):
        for z in sorted({s["Z"] for s in samples}):
            c = slice_curve(samples, z, pd, 0.70)
            if not c["J"]:
                continue
            rows.append({"Z": z, "P_D": pd, "KT_at_Jmax": c["KT"][-1], "Jmax": c["J"][-1],
                         "KT_min": min(c["KT"])})
    # 判定：对 P/D>=0.8，J=1.4 处 KT 仍 > 0.02 → 无零推力点
    bad = [r for r in rows if r["P_D"] >= 0.8 and r["KT_at_Jmax"] > 0.02]
    return {
        "id": "G6", "name": "存在零推力点 J0（J=1.4 处 KT≤0.02）",
        "measured": len(bad), "limit": 0, "unit": "条曲线",
        "verdict": "PASS" if not bad else "FAIL",
        "detail": rows, "violations": bad[:6],
    }


def check_g7_saturation(samples: list) -> dict:
    """G7 效率截断饱和率：η0 大量取同一上界说明结果被 clamp 而非计算。"""
    vals = [s["eta0"] for s in samples if s.get("eta0") is not None]
    if not vals:
        return {"id": "G7", "name": "η0 截断饱和率", "measured": None, "limit": 0.05,
                "verdict": "FAIL", "n_samples": 0}
    cap = max(vals)
    n_cap = sum(1 for v in vals if abs(v - cap) < 1e-9)
    frac = n_cap / len(vals)
    return {
        "id": "G7", "name": "η0 取上界的样本占比（clamp 证据）",
        "measured": round(frac, 6), "limit": 0.05, "unit": "-",
        "verdict": "PASS" if frac <= 0.05 else "FAIL",
        "n_samples": len(vals), "detail": {"cap_value": cap, "n_at_cap": n_cap},
    }


def check_g8_area_ratio_insensitivity(samples: list, j_probe: float = 0.6) -> dict:
    """G8 KT 对盘面比 Ae/Ao 的敏感度：真实 B 系列在 Ae/Ao 0.4→1.0 变化量约 <15%。"""
    d = {}
    for ae in {s["Ae_Ao"] for s in samples}:
        rows = [s for s in samples if abs(s["J"] - j_probe) < 1e-9 and s["Ae_Ao"] == ae
                and s["Z"] == 4 and abs(s["P_D"] - 1.0) < 1e-9]
        if rows:
            d[ae] = rows[0]["KT"]
    if len(d) < 2:
        return {"id": "G8", "name": "KT 对 Ae/Ao 的敏感度", "measured": None, "limit": 0.15,
                "verdict": "FAIL", "n_samples": len(d)}
    lo, hi = min(d), max(d)
    if d[lo] <= 0:
        sens = float("inf")
    else:
        sens = d[hi] / d[lo] - 1.0
    return {
        "id": "G8", "name": f"KT 对 Ae/Ao 的敏感度（J={j_probe}, B4-70, P/D=1.0）",
        "measured": round(sens, 6), "limit": 0.15, "unit": "-",
        "verdict": "PASS" if abs(sens) <= 0.15 else "FAIL", "n_samples": len(d),
        "detail": {f"Ae/Ao={k}": v for k, v in sorted(d.items())},
    }


def check_g9_reference_agreement(samples: list, ref: dict) -> dict:
    """G9 对文献基准表（B4-70, P/D=1.0, Ae/Ao=0.70）的逐点相对误差 ≤15%。"""
    dp = ref["data_points"]
    xs = dp["J"]
    rows = []
    for j, kt_ref, kq_ref, eta_ref in zip(xs, dp["KT"], dp["KQ"], dp["eta_0"]):
        hit = [s for s in samples if abs(s["J"] - j) < 1e-9 and s["Z"] == 4
               and abs(s["P_D"] - 1.0) < 1e-9 and abs(s["Ae_Ao"] - 0.70) < 1e-9]
        if not hit:
            continue
        s = hit[0]
        rows.append({
            "J": j,
            "KT_api": s.get("KT"), "KT_ref": kt_ref, "KT_err": round(rel_err(s.get("KT") or 0, kt_ref), 6),
            "KQ_api": s.get("KQ"), "KQ_ref": kq_ref, "KQ_err": round(rel_err(s.get("KQ") or 0, kq_ref), 6),
            "eta0_api": s.get("eta0"), "eta0_ref_table": eta_ref,
            "eta0_ref_def": round(eta0_def(j, kt_ref, kq_ref), 6),
        })
    # 效率基准用定义式复算值（基准表 eta_0 列自身有算术不一致，见 G10）
    for r in rows:
        r["eta0_err"] = round(rel_err(r["eta0_api"] or 0, r["eta0_ref_def"]), 6) if r["eta0_ref_def"] else None
    # J=0 处 eta0 定义式恒为 0，不参与误差统计
    eff_rows = [r for r in rows if r["eta0_ref_def"] > 0]
    worst_kt = max((r["KT_err"] for r in rows), default=None)
    worst_kq = max((r["KQ_err"] for r in rows), default=None)
    worst_eta = max((r["eta0_err"] for r in eff_rows), default=None)
    worst = max(x for x in (worst_kt, worst_kq, worst_eta) if x is not None) if rows else None
    return {
        "id": "G9", "name": "对照文献基准表 B4-70 的逐点相对误差（≤15%）",
        "measured": round(worst, 6) if worst is not None else None,
        "limit": 0.15, "unit": "-",
        "verdict": "PASS" if (worst is not None and worst <= 0.15) else "FAIL",
        "n_points": len(rows),
        "detail": {"worst_KT_err": worst_kt, "worst_KQ_err": worst_kq, "worst_eta0_err": worst_eta},
        "rows": rows,
        "ref_provenance": {k: ref.get(k) for k in ("source", "ship_name")},
    }


def check_g10_reference_self_consistency(ref: dict) -> dict:
    """G10 基准表自身自洽：eta_0 列必须等于定义式复算值（否则基准表不可直接引用）。"""
    dp = ref["data_points"]
    rows = []
    for j, kt, kq, e in zip(dp["J"], dp["KT"], dp["KQ"], dp["eta_0"]):
        ref_e = eta0_def(j, kt, kq)
        rows.append({"J": j, "eta_table": e, "eta_def": round(ref_e, 6),
                     "err": round(rel_err(e, ref_e), 6) if ref_e > 0 else (0.0 if e == 0 else None)})
    eff = [r for r in rows if r["eta_def"] > 0]
    worst = max((r["err"] for r in eff if r["err"] is not None), default=None)
    return {
        "id": "G10", "name": "文献基准表 eta_0 列 vs 定义式复算（≤1%）",
        "measured": worst, "limit": 0.01, "unit": "-",
        "verdict": "PASS" if (worst is not None and worst <= 0.01) else "FAIL",
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# 参照实现（判据之外的独立复算，用于量化下游影响）
# --------------------------------------------------------------------------- #
class RefBSeries:
    """由文献基准表驱动的参照敞水模型（双线性查表 + 定义式 η0）。

    定位：**不是** 声称精确的 B 系列回归实现，而是「现有端点若换成可溯源数据表」能拿到
    什么量级的对照值。适用范围严格限制在基准表覆盖的 P/D=1.0, Ae/Ao=0.7 单系列，
    跨几何只做 J 方向插值 + 不做外推。
    """

    def __init__(self, ref: dict):
        dp = ref["data_points"]
        self.J = list(dp["J"])
        self.KT = list(dp["KT"])
        self.KQ = list(dp["KQ"])
        self.j0 = self.J[-1]

    def kt(self, j: float) -> float:
        return interp(j, self.J, self.KT)

    def kq(self, j: float) -> float:
        return interp(j, self.J, self.KQ)

    def eta0(self, j: float) -> float:
        return eta0_def(j, self.kt(j), self.kq(j))


# --------------------------------------------------------------------------- #
# 报告输出
# --------------------------------------------------------------------------- #
def to_markdown(verdict_doc: dict) -> str:
    L = []
    L.append(f"# 推进域敞水性能审计裁决（{verdict_doc['captured_at']}）\n")
    L.append(f"- 被审端点：`{verdict_doc['endpoint']}`（base `{verdict_doc['base']}`）")
    L.append(f"- 探针样本：{verdict_doc['coverage']['ok']}/{verdict_doc['coverage']['total']} HTTP 200")
    L.append(f"- 文献基准：`{verdict_doc['ref']['path']}` sha256=`{verdict_doc['ref']['sha256'][:16]}…`")
    L.append(f"- **总判定：{verdict_doc['verdict']}**（PASS {verdict_doc['n_pass']} / FAIL {verdict_doc['n_fail']}）\n")
    L.append("| 判据 | 名称 | 实测 | 阈值 | 判定 |")
    L.append("|---|---|---|---|---|")
    for c in verdict_doc["criteria"]:
        meas = c["measured"]
        meas_s = "—" if meas is None else (f"{meas:.4g}" if isinstance(meas, (int, float)) else str(meas))
        L.append(f"| {c['id']} | {c['name']} | {meas_s} | {c['limit']} | {c['verdict']} |")
    L.append("")
    g9 = next(c for c in verdict_doc["criteria"] if c["id"] == "G9")
    L.append("## G9 逐点对照（B4-70, P/D=1.0, Ae/Ao=0.70）\n")
    L.append("| J | KT_api | KT_ref | KT_err | KQ_api | KQ_ref | KQ_err | eta0_api | eta0_ref | eta0_err |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in g9["rows"]:
        f = lambda v: "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
        L.append(f"| {r['J']} | {f(r['KT_api'])} | {r['KT_ref']} | {f(r['KT_err'])} | "
                 f"{f(r['KQ_api'])} | {r['KQ_ref']} | {f(r['KQ_err'])} | "
                 f"{f(r['eta0_api'])} | {f(r['eta0_ref_def'])} | {f(r['eta0_err'])} |")
    L.append("")
    g10 = next(c for c in verdict_doc["criteria"] if c["id"] == "G10")
    L.append("## G10 基准表自洽（eta_0 列 vs 定义式）\n")
    L.append("| J | eta_table | eta_def | 相对偏差 |")
    L.append("|---|---|---|---|")
    for r in g10["rows"]:
        L.append(f"| {r['J']} | {r['eta_table']} | {r['eta_def']} | "
                 f"{'—' if r['err'] is None else r['err']} |")
    L.append("")
    return "\n".join(L)


def make_figures(probe: dict, ref: dict, verdict_doc: dict, fig_dir: str) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(fig_dir, exist_ok=True)
    figs = []
    samples = probe["samples"]
    dp = ref["data_points"]
    R = RefBSeries(ref)

    # 图1：B4-70 P/D=1.0 端点曲线 vs 文献基准（KT / KQ / eta0 三联）
    c = slice_curve(samples, 4, 1.0, 0.70)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, (key, ref_key, ylab) in zip(axes, [("KT", "KT", "$K_T$"), ("KQ", "KQ", "$K_Q$"),
                                               ("eta0", "eta_0", "$\\eta_0$")]):
        ax.plot(c["J"], c[key], "o-", color="#c0392b", label="platform endpoint", ms=4)
        if key == "eta0":
            ax.plot(dp["J"], [eta0_def(j, a, b) for j, a, b in zip(dp["J"], dp["KT"], dp["KQ"])],
                    "s--", color="#1f77b4", label="literature table (def. recomputed)", ms=5)
            ax.plot(dp["J"], dp["eta_0"], "x:", color="#7f8c8d", label="literature table (as filed)", ms=6)
            ax.axhline(0.85, color="#f39c12", ls="-.", lw=1, label="0.85 clamp")
        else:
            ax.plot(dp["J"], dp[ref_key], "s--", color="#1f77b4", label="literature table", ms=5)
        ax.set_xlabel("J"); ax.set_ylabel(ylab); ax.grid(alpha=.3); ax.legend(fontsize=8)
    axes[0].set_title("B4-70 P/D=1.0 Ae/Ao=0.70")
    fig.suptitle("Open-water characteristics: platform endpoint vs literature baseline")
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig1_openwater_compare.png"); fig.savefig(p, dpi=140); plt.close(fig)
    figs.append(p)

    # 图2：eta0 对 P/D 的"零敏感性"证据（J 方向多条曲线重合）
    fig, ax = plt.subplots(figsize=(7, 4.4))
    for pd, color in zip(sorted({s["P_D"] for s in samples}), ["#c0392b", "#e67e22", "#16a085", "#2980b9", "#8e44ad"]):
        cc = slice_curve(samples, 4, pd, 0.70)
        ax.plot(cc["J"], cc["eta0"], "-", color=color, label=f"endpoint P/D={pd}")
    ax.plot(dp["J"], [eta0_def(j, a, b) for j, a, b in zip(dp["J"], dp["KT"], dp["KQ"])],
            "k--", lw=1.6, label="literature (P/D=1.0)")
    ax.set_xlabel("J"); ax.set_ylabel("$\\eta_0$")
    ax.set_title("All P/D curves collapse: endpoint $\\eta_0$ is geometry-independent")
    ax.grid(alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig2_eta0_geometry_invariance.png"); fig.savefig(p, dpi=140); plt.close(fig)
    figs.append(p)

    # 图3：KQ/KT 恒定比值 + KT 对 Ae/Ao 的异常敏感度
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    ratios = [round(s["KQ"] / s["KT"], 9) for s in samples if s.get("KT")]
    uniq = sorted(set(ratios))
    if len(uniq) == 1:
        ax.bar([f"{uniq[0]:.4f}"], [len(ratios)], width=0.5, color="#c0392b", alpha=.85)
        ax.text(0.5, 0.5, f"all {len(ratios)} samples share\n$K_Q/K_T$ = {uniq[0]:.6f}",
                transform=ax.transAxes, ha="center", va="center", fontsize=11,
                bbox=dict(fc="white", ec="#c0392b"))
    else:
        ax.hist(ratios, bins=min(40, max(2, len(uniq))), color="#c0392b", alpha=.8)
    ax.set_xlabel("$K_Q/K_T$"); ax.set_ylabel("samples")
    ax.set_title(f"$K_Q/K_T$ distribution (CV={next(x for x in verdict_doc['criteria'] if x['id']=='G4')['measured']:.2e})")
    ax = axes[1]
    aes = sorted({s["Ae_Ao"] for s in samples})
    kts = []
    for ae in aes:
        hit = [s for s in samples if s["Ae_Ao"] == ae and abs(s["J"] - 0.6) < 1e-9
               and s["Z"] == 4 and abs(s["P_D"] - 1.0) < 1e-9]
        kts.append(hit[0]["KT"] if hit else float("nan"))
    ax.bar([str(a) for a in aes], kts, color="#2980b9", alpha=.85)
    ax.set_xlabel("$A_e/A_0$"); ax.set_ylabel("$K_T$ at J=0.6")
    ax.set_title("endpoint $K_T$ grows ~linearly with $A_e/A_0$ (unphysical)")
    ax.grid(alpha=.3, axis="y")
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig3_kqkt_and_arearatio.png"); fig.savefig(p, dpi=140); plt.close(fig)
    figs.append(p)

    # 图4：下游影响 —— 同一 J 下端点与参照的扭矩/功率偏差
    fig, ax = plt.subplots(figsize=(7, 4.2))
    js = [j for j in c["J"] if 0.1 <= j <= 1.1 and R.kq(j) > 1e-4]
    api = [interp(j, c["J"], c["KQ"]) for j in js]
    refv = [R.kq(j) for j in js]
    ax.plot(js, [a / r - 1 for a, r in zip(api, refv)], "o-", color="#c0392b")
    ax.axhline(0.15, color="#e67e22", ls="--", lw=1, label="+15% 不可接受线")
    ax.axhline(-0.15, color="#e67e22", ls="--", lw=1)
    ax.set_xlabel("J"); ax.set_ylabel("$K_Q$ relative error vs baseline")
    ax.set_title("Torque (hence delivered power) error at fixed J")
    ax.grid(alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig4_downstream_power_error.png"); fig.savefig(p, dpi=140); plt.close(fig)
    figs.append(p)
    return figs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", default="artifacts/audit/prop_openwater_probe.json")
    ap.add_argument("--ref", default="docs/audit/data/propulsion_wageningen_ref.json")
    ap.add_argument("--out-json", default="artifacts/audit/prop_openwater_verdict.json")
    ap.add_argument("--out-md", default="artifacts/audit/prop_openwater_verdict.md")
    ap.add_argument("--fig-dir", default="docs/audit/figures")
    ap.add_argument("--no-fig", action="store_true")
    args = ap.parse_args()

    probe = json.load(open(args.probe, encoding="utf-8"))
    ref = json.load(open(args.ref, encoding="utf-8"))
    samples = probe["samples"]
    ok = sum(1 for s in samples if s.get("http_status") == 200)
    usable = [s for s in samples if s.get("http_status") == 200 and s.get("KT") is not None]

    criteria = [
        check_g1_eta_identity(usable),
        check_g2_eta_at_zero_j(usable),
        check_g3_eta_geometry_sensitivity(usable),
        check_g4_kqkt_ratio(usable),
        check_g5_monotonic(usable),
        check_g6_zero_thrust(usable),
        check_g7_saturation(usable),
        check_g8_area_ratio_insensitivity(usable),
        check_g9_reference_agreement(usable, ref),
        check_g10_reference_self_consistency(ref),
    ]
    n_pass = sum(1 for c in criteria if c["verdict"] == "PASS")
    n_fail = sum(1 for c in criteria if c["verdict"] == "FAIL")

    # 端点级（被审对象）判定不含 G10（G10 是关于基准表质量的判定）
    endpoint_fails = [c["id"] for c in criteria if c["id"] != "G10" and c["verdict"] == "FAIL"]
    grade = "MOCK" if len(endpoint_fails) >= 3 else ("REAL" if not endpoint_fails else "WITHHELD")

    doc = {
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "base": probe["base"], "endpoint": probe["endpoint"],
        "coverage": {"ok": ok, "total": len(samples), "usable": len(usable)},
        "ref": {"path": args.ref, "sha256": sha256_of(args.ref),
                "source": ref.get("source"), "ship_name": ref.get("ship_name")},
        "criteria": criteria,
        "n_pass": n_pass, "n_fail": n_fail,
        "endpoint_fails": endpoint_fails,
        "verdict": grade,
        "summary": {
            "eta0_independent_of_geometry": next(c for c in criteria if c["id"] == "G3")["verdict"] == "FAIL",
            "kq_is_kt_scaled": next(c for c in criteria if c["id"] == "G4")["verdict"] == "FAIL",
            "worst_ref_error": next(c for c in criteria if c["id"] == "G9")["measured"],
        },
    }
    os.makedirs(os.path.dirname(args.out_json) or ".", exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(to_markdown(doc))
    figs = [] if args.no_fig else make_figures(probe, ref, doc, args.fig_dir)
    print(f"[audit] verdict={grade} PASS={n_pass} FAIL={n_fail} fails={endpoint_fails}")
    print(f"[audit] -> {args.out_json}, {args.out_md}")
    for p in figs:
        print(f"[fig] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())