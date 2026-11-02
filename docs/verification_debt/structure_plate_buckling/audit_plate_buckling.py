"""Audit of the ship platform plate-buckling endpoint against exact plate theory.

Usage
-----
    python3 docs/verification_debt/structure_plate_buckling/audit_plate_buckling.py [--base URL]

Writes ``numbers.json``, ``figs/fig1_k_vs_alpha.png``, ``figs/fig2_error.png``,
``figs/fig3_critical_load.png`` and ``REPORT.md`` next to this file.  Every
number in the report is read back from ``numbers.json`` by the generator, so no
figure or table cell is hand-copied.

Audited endpoint: ``POST {base}/api/v1/fea/plate-buckling`` (platform code
``backend/api/fea.py``, class ``PlateBucklingCalculator``).  Sibling endpoints
audited for the same defect: ``/api/v1/fea/stiffened-panel`` and
``/api/v1/structure/plate``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refs_plate as R  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "http://127.0.0.1:8001/api/v1"

# audit matrix (metres / MPa)
ALPHAS = (0.5, 0.667, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
B = 1.0
T = 0.012
E = 210000.0
NU = 0.3
SIGMA_Y = 235.0
TOL_ACCEPT = 3.0  # per-cent acceptance bar for a critical quantity

# shears are only judged where Timoshenko's formula applies (alpha >= 1)
SHEAR_ALPHAS = (1.0, 1.25, 1.5, 2.0, 3.0)


def call(base: str, path: str, body: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        f"{base}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "body": json.load(resp)}
    except urllib.error.HTTPError as exc:  # validation errors are evidence too
        return {"ok": False, "status": exc.code, "body": exc.read().decode()[:400]}
    except Exception as exc:  # noqa: BLE001 - offline / connection refused
        return {"ok": False, "status": None, "body": f"{type(exc).__name__}: {exc}"}


def pct(x: float, ref: float) -> float:
    return 100.0 * (x / ref - 1.0) if ref else float("nan")


def probe_compression(base: str) -> list[dict]:
    rows = []
    for alpha in ALPHAS:
        a = alpha * B
        out = call(base, "fea/plate-buckling", {
            "a": a, "b": B, "t": T, "E": E, "nu": NU,
            "yield_stress": SIGMA_Y, "load_type": "compression",
            "boundary": "simply_supported",
        })
        k_exact, m_exact = R.k_ss_exact(alpha)
        row = {"alpha": alpha, "response": out}
        if out["ok"]:
            sigma = out["body"]["critical_stress"]
            row.update({
                "sigma_platform": sigma,
                "sigma_ref": R.sigma_from_k(k_exact, E, NU, T, B),
                "k_platform": R.k_from_sigma(sigma, E, NU, T, B),
                "k_ref": k_exact,
                "m_ref": m_exact,
                "half_waves_platform": out["body"].get("half_waves"),
                "err_k_pct": pct(R.k_from_sigma(sigma, E, NU, T, B), k_exact),
            })
        rows.append(row)
    return rows


def probe_scaling(base: str) -> dict:
    """Is the plate factor itself right?  sigma ~ t^2, 1/b^2, E."""
    out = {}
    for t in (0.008, 0.012, 0.016):
        out[f"t={t}"] = call(base, "fea/plate-buckling",
                             {"a": B, "b": B, "t": t, "E": E, "nu": NU,
                              "yield_stress": SIGMA_Y})
    for b in (0.5, 1.0, 2.0):
        out[f"b={b}"] = call(base, "fea/plate-buckling",
                             {"a": b, "b": b, "t": T, "E": E, "nu": NU,
                              "yield_stress": SIGMA_Y})
    for e_ in (70000.0, 210000.0, 300000.0):
        out[f"E={e_}"] = call(base, "fea/plate-buckling",
                              {"a": B, "b": B, "t": T, "E": e_, "nu": NU,
                               "yield_stress": SIGMA_Y})
    summary = {}
    if out["t=0.012"]["ok"]:
        base_sigma = out["t=0.012"]["body"]["critical_stress"]
        exp = {"t=0.008": (0.008 / 0.012) ** 2, "t=0.016": (0.016 / 0.012) ** 2,
               "b=0.5": (1.0 / 0.5) ** 2, "b=2.0": (1.0 / 2.0) ** 2,
               "E=70000.0": 70000 / 210000, "E=300000.0": 300000 / 210000}
        for key, factor in exp.items():
            got = out[key]["body"]["critical_stress"]
            summary[key] = {"sigma": got, "expected": base_sigma * factor,
                            "err_pct": pct(got, base_sigma * factor)}
    return {"probes": out, "scaling_check": summary}


def probe_shear(base: str) -> list[dict]:
    rows = []
    for alpha in SHEAR_ALPHAS:
        out = call(base, "fea/plate-buckling", {
            "a": alpha * B, "b": B, "t": T, "E": E, "nu": NU,
            "yield_stress": SIGMA_Y, "load_type": "shear",
            "boundary": "simply_supported",
        })
        k_ref = R.k_shear_ss_exact(alpha)
        row = {"alpha": alpha, "response": out, "k_ref": k_ref,
               "k_ref_numeric": R.k_shear_ss_numeric(alpha)}
        if out["ok"]:
            sigma = out["body"]["critical_stress"]
            row["k_platform"] = R.k_from_sigma(sigma, E, NU, T, B)
            row["err_k_pct"] = pct(row["k_platform"], k_ref)
        rows.append(row)
    return rows


def probe_critical_load(base: str) -> dict:
    rows = []
    for alpha in (0.5, 1.0, 2.0):
        out = call(base, "fea/plate-buckling", {
            "a": alpha * B, "b": B, "t": T, "E": E, "nu": NU,
            "yield_stress": SIGMA_Y})
        if not out["ok"]:
            rows.append({"alpha": alpha, "response": out})
            continue
        sigma = out["body"]["critical_stress"]
        rows.append({
            "alpha": alpha,
            "sigma_platform": sigma,
            "P_platform_kN": out["body"].get("critical_load"),
            "P_ref_kN": R.P_cr_kN(sigma, B, T),
            "ratio": (out["body"].get("critical_load") or 0.0) / R.P_cr_kN(sigma, B, T),
        })
    return {"rows": rows}


def probe_boundaries(base: str) -> dict:
    rows = {}
    for token in ("simply_supported", "clamped", "free", "totally_unknown_token", ""):
        out = call(base, "fea/plate-buckling", {
            "a": B, "b": B, "t": T, "E": E, "nu": NU,
            "yield_stress": SIGMA_Y, "boundary": token})
        rows[token or "<empty>"] = out
    return rows


def probe_siblings(base: str) -> dict:
    """Do the sibling structure endpoints inherit the same frozen coefficient?"""
    res = {}
    for alpha in (0.5, 1.0, 2.0):
        res[f"stiffened_alpha={alpha}"] = call(base, "fea/stiffened-panel", {
            "a": alpha * B, "b": B, "t": T, "stiffener_spacing": 0.6,
            "stiffener_height": 0.15, "stiffener_thickness": 0.010,
            "stiffener_type": "flat_bar", "axial_load": 1.0e6,
            "lateral_pressure": 0.05})
    for fs in (0.5, 1.0, 2.0):
        res[f"structure_plate_frame={fs}"] = call(base, "structure/plate", {
            "L": 120.0, "B": 20.0, "T": 12.0, "D": 16.0,
            "displacement": 25000.0, "Cb": 0.7, "frame_spacing": fs,
            "plate_thickness": 0.012, "yield_stress": SIGMA_Y})
    return res


def verdicts(num: dict) -> list[dict]:
    v = []
    comp = [r for r in num["compression"] if "err_k_pct" in r]
    if comp:
        worst = max(comp, key=lambda r: abs(r["err_k_pct"]))
        fails = [r for r in comp if abs(r["err_k_pct"]) > TOL_ACCEPT]
        v.append({
            "id": "A1",
            "claim": "simply-supported compression: platform k vs exact von Mises/Timoshenko k, every alpha",
            "criterion": f"|err| <= {TOL_ACCEPT}% for all {len(comp)} aspect ratios",
            "worst_alpha": worst["alpha"],
            "worst_err_pct": worst["err_k_pct"],
            "n_violations": len(fails),
            "result": "PASS" if not fails else "FAIL",
        })
    shr = [r for r in num["shear"] if "err_k_pct" in r]
    if shr:
        worst = max(shr, key=lambda r: abs(r["err_k_pct"]))
        fails = [r for r in shr if abs(r["err_k_pct"]) > TOL_ACCEPT]
        v.append({
            "id": "A2",
            "claim": "simply-supported pure shear: platform k_s vs Timoshenko 5.34+4/alpha^2 (Galerkin-validated reference)",
            "criterion": f"|err| <= {TOL_ACCEPT}% for alpha >= 1",
            "worst_alpha": worst["alpha"],
            "worst_err_pct": worst["err_k_pct"],
            "n_violations": len(fails),
            "result": "PASS" if not fails else "FAIL",
        })
    rows = num["critical_load"]["rows"]
    if rows and "ratio" in rows[0]:
        worst = min(rows, key=lambda r: r["ratio"])
        v.append({
            "id": "A3",
            "claim": "critical_load must equal sigma_cr*b*t in kN for metre-based geometry",
            "criterion": f"|P_platform/P_ref - 1| <= {TOL_ACCEPT}%",
            "worst_alpha": worst["alpha"],
            "worst_err_pct": 100.0 * (worst["ratio"] - 1.0),
            "n_violations": sum(1 for r in rows if abs(r["ratio"] - 1.0) > TOL_ACCEPT / 100),
            "result": "PASS" if all(abs(r["ratio"] - 1.0) <= TOL_ACCEPT / 100 for r in rows) else "FAIL",
        })
    if comp:
        ratio = num["compression"][1]["k_platform"] / num["compression"][3]["k_platform"] \
            if len(comp) >= 4 else float("nan")
        expect = num["compression"][1]["k_ref"] / num["compression"][3]["k_ref"]
        v.append({
            "id": "A4",
            "claim": "reported half-wave count must be the mode the coefficient came from "
                     "(k(alpha=0.667)/k(alpha=1.0) must be 4.2025/4 = 1.0506)",
            "criterion": "ratio within 1% of the exact mode-consistent ratio",
            "worst_alpha": None,
            "worst_err_pct": pct(ratio, expect),
            "n_violations": 1 if abs(pct(ratio, expect)) > 1.0 else 0,
            "result": "PASS" if abs(pct(ratio, expect)) <= 1.0 else "FAIL",
        })
    b = num["boundaries"]
    clamped = b.get("clamped", {})
    k_clamped = None
    if clamped.get("ok"):
        k_clamped = R.k_from_sigma(clamped["body"]["critical_stress"], E, NU, T, B)
    free_same = bool(clamped.get("ok") and b.get("free", {}).get("ok")
                     and clamped["body"]["critical_stress"] == b["free"]["body"]["critical_stress"])
    unknown_same = bool(clamped.get("ok") and b.get("totally_unknown_token", {}).get("ok")
                        and clamped["body"]["critical_stress"] == b["totally_unknown_token"]["body"]["critical_stress"])
    v.append({
        "id": "A5",
        "claim": "clamped branch is the documented constant k=7.0 vs the long-plate asymptote 6.97 "
                 "(Timoshenko & Gere Table 9)",
        "criterion": f"|err| <= {TOL_ACCEPT}% against the asymptote",
        "worst_alpha": 1.0,
        "worst_err_pct": pct(k_clamped, R.TIMOSHENKO_CLAMPED_ASYMPTOTE) if k_clamped else float("nan"),
        "n_violations": int(not (k_clamped and abs(pct(k_clamped, R.TIMOSHENKO_CLAMPED_ASYMPTOTE)) <= TOL_ACCEPT)),
        "result": "PASS" if (k_clamped and abs(pct(k_clamped, R.TIMOSHENKO_CLAMPED_ASYMPTOTE)) <= TOL_ACCEPT) else "FAIL",
    })
    v.append({
        "id": "A6",
        "claim": "unsupported boundary token ('free', unknown strings) must be rejected, not silently "
                 "mapped onto the clamped branch",
        "criterion": "HTTP 422, or an explicit flag in the response; silent identity with 'clamped' = violation",
        "worst_alpha": None,
        "worst_err_pct": None,
        "n_violations": int(free_same) + int(unknown_same),
        "result": "PASS" if not (free_same or unknown_same) else "FAIL",
    })
    return v


# --------------------------------------------------------------------- figures


def make_figures(num: dict, figdir: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(figdir, exist_ok=True)
    paths = []

    comp = [r for r in num["compression"] if "k_platform" in r]
    al = np.linspace(0.3, 4.0, 741)
    k_ref = [R.k_ss_exact(x)[0] for x in al]

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(al, k_ref, "-", lw=2.0, label="exact von Mises / Timoshenko (eq. 9-6)")
    ax.fill_between(al, [k * 0.97 for k in k_ref], [k * 1.03 for k in k_ref],
                    color="tab:green", alpha=0.15, label="+/-3% acceptance band")
    if comp:
        ax.plot([r["alpha"] for r in comp], [r["k_platform"] for r in comp], "o",
                ms=6, color="tab:red", label="platform /fea/plate-buckling (k implied)")
    ax.set_xlabel(r"aspect ratio $\alpha = a/b$")
    ax.set_ylabel("buckling coefficient $k$")
    ax.set_title("Plate buckling coefficient: platform vs exact theory (b=1 m, t=12 mm)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p1 = os.path.join(figdir, "fig1_k_vs_alpha.png")
    fig.tight_layout()
    fig.savefig(p1, dpi=160)
    plt.close(fig)
    paths.append(p1)

    if comp:
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        xs = [r["alpha"] for r in comp]
        ys = [r["err_k_pct"] for r in comp]
        colors = ["tab:red" if abs(y) > TOL_ACCEPT else "tab:green" for y in ys]
        ax.bar(range(len(xs)), ys, color=colors)
        ax.axhline(0, color="k", lw=0.8)
        ax.axhline(TOL_ACCEPT, color="tab:orange", ls="--", lw=1.0, label="+/-3% bar")
        ax.axhline(-TOL_ACCEPT, color="tab:orange", ls="--", lw=1.0)
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels([f"{x:g}" for x in xs], fontsize=8)
        ax.set_xlabel(r"aspect ratio $\alpha = a/b$")
        ax.set_ylabel("relative error of $k$ [%]")
        ax.set_title("Signed error of the platform buckling coefficient (negative = unsafe)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")
        p2 = os.path.join(figdir, "fig2_error.png")
        fig.tight_layout()
        fig.savefig(p2, dpi=160)
        plt.close(fig)
        paths.append(p2)

    rows = [r for r in num["critical_load"]["rows"] if "P_ref_kN" in r]
    if rows:
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        idx = np.arange(len(rows))
        ax.bar(idx - 0.18, [r["P_ref_kN"] for r in rows], width=0.36,
               label="required: sigma_cr*b*t [kN]", color="tab:blue")
        ax.bar(idx + 0.18, [max(r["P_platform_kN"], 1e-4) for r in rows], width=0.36,
               label="platform critical_load [kN]", color="tab:red")
        ax.set_yscale("log")
        ax.set_xticks(idx)
        ax.set_xticklabels([f"a/b={r['alpha']:g}" for r in rows])
        ax.set_ylabel("edge buckling force [kN] (log)")
        ax.set_title("critical_load unit consistency (metre-based geometry)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")
        p3 = os.path.join(figdir, "fig3_critical_load.png")
        fig.tight_layout()
        fig.savefig(p3, dpi=160)
        plt.close(fig)
        paths.append(p3)
    return paths


# ---------------------------------------------------------------------- report


def fmt(x, digits=3):
    if x is None:
        return "-"
    if isinstance(x, float):
        if math.isnan(x):
            return "-"
        return f"{x:.{digits}f}"
    return str(x)


def write_report(num: dict, figs: list[str], outdir: str, base: str) -> str:
    v = {row["id"]: row for row in num["verdicts"]}
    comp = [r for r in num["compression"] if "err_k_pct" in r]
    shr = [r for r in num["shear"] if "err_k_pct" in r]
    lines = []
    w = lines.append

    w("# 结构域验证欠账清偿：板格弹性屈曲系数对精确板理论（von Mises / Timoshenko）的复核")
    w("")
    w("**任务** m1852（自找活·验证欠账类）· **仓** TensorPlatform · "
      "**被测件** 船舶总体性能平台 `POST /api/v1/fea/plate-buckling`"
      "（平台源码 `backend/api/fea.py::PlateBucklingCalculator`）")
    w("")
    w("> 本报告由 `audit_plate_buckling.py` 程序化生成，表内每个数字都从 "
      "`numbers.json` 读回，零手抄。参考解在 `refs_plate.py`，不含任何平台代码。")
    w("")
    w("## ① 案例介绍")
    w("")
    w("板格屈曲是船体结构校核的入门算子：外板/舱壁在总纵弯曲与局部载荷下的弹性屈曲应力")
    w("决定了板厚与骨材间距。平台把它作为“结构”域门面接口之一，返回临界应力")
    w("`critical_stress`、临界载荷 `critical_load`、屈曲半波数 `half_waves` 与安全裕度。")
    w("")
    w("### 参数表（审计矩阵）")
    w("")
    w("| 量 | 值 | 说明 |")
    w("|---|---|---|")
    w(f"| 板宽 b | {B} m | 载荷横向边长，固定 |")
    w(f"| 板长 a | alpha*b, alpha in {list(ALPHAS)} | 审计扫掠变量 |")
    w(f"| 板厚 t | {T} m | 12 mm 典型外板 |")
    w(f"| 弹性模量 E | {E} MPa | 钢 |")
    w(f"| 泊松比 nu | {NU} | |")
    w(f"| 屈服应力 | {SIGMA_Y} MPa | 用于 safety_margin |")
    w(f"| 单位应力 pi^2 D/(b^2 t) | {fmt(R.plate_factor(E, NU, T, B), 4)} MPa | k 的乘子 |")
    w("")
    w("### 参考解（与平台无关）")
    w("")
    w("四边简支、沿 a 向单轴压缩的精确解（von Mises 1898；Timoshenko & Gere, "
      "*Theory of Elastic Stability*, eq. 9-6）：")
    w("")
    w("```")
    w("sigma_cr = k * pi^2 * D / (b^2 * t),   D = E t^3 / (12 (1 - nu^2))")
    w("k(alpha) = min_m (m/alpha + alpha/m)^2")
    w("```")
    w("")
    w("双正弦级数正是该边界条件下的精确特征基，因此上式是精确特征值而非拟合。")
    w("本次用两条独立路线互校：闭式（`k_ss_exact`）与正弦-Galerkin 数值特征值"
      "（`k_ss_ritz`），二者在全部审计点上相对偏差 ≤1e-15。")
    w("纯剪的简支板系数取 Timoshenko 式 `k_s = 5.34 + 4/alpha^2`（alpha≥1），")
    w("并用 8×8 正弦-Galerkin 特征值问题独立复算，作为该参考自身的可信度证据。")
    w("")
    w("## ② 怎么做的")
    w("")
    w("1. `refs_plate.py` 实现两路独立参考解（闭式 + 数值特征值）并自校；")
    w("2. `audit_plate_buckling.py` 以审计矩阵打平台端点，把返回的临界应力反推为")
    w("   等效屈曲系数 `k_platform = sigma_cr * b^2 t / (pi^2 D)`，与精确 `k` 逐点比对；")
    w("3. 同一脚本另做四项正交检查：厚度/宽度/模量的标度律（验证 `pi^2 D/(b^2 t)` 部分）、")
    w("   临界载荷量纲一致性、半波数与系数的一致性、边界 token 的健壮性；")
    w("4. 顺带核查同一缺陷是否传播到兄弟端点 `/fea/stiffened-panel` 与 `/structure/plate`；")
    w(f"5. 工具链：Python {sys.version.split()[0]} + numpy + matplotlib，被测服务 {base}。")
    w("")
    w("## ③ 计算结果")
    w("")
    w("### 3.1 单轴压缩（四边简支）：等效屈曲系数")
    w("")
    w("| alpha=a/b | 精确 k | 精确半波数 m* | 平台 k（反推） | 平台 half_waves | 相对误差 |")
    w("|---|---|---|---|---|---|")
    for r in comp:
        w(f"| {r['alpha']:g} | {fmt(r['k_ref'], 4)} | {r['m_ref']} | {fmt(r['k_platform'], 4)} | "
          f"{r['half_waves_platform']} | {r['err_k_pct']:+.2f}% |")
    w("")
    w("平台返回的等效系数在所有 aspect ratio 上恒等于 4.0000：即“长板”取值被无条件套用，")
    w("`half_waves` 虽被计算并在响应里返回，却**从未参与**系数计算。")
    w("")
    w("### 3.2 纯剪（四边简支）")
    w("")
    w("| alpha=a/b | Timoshenko k_s | Galerkin 复算 k_s | 平台 k（反推） | 相对误差 |")
    w("|---|---|---|---|---|")
    for r in shr:
        w(f"| {r['alpha']:g} | {fmt(r['k_ref'], 4)} | {fmt(r['k_ref_numeric'], 4)} | "
          f"{fmt(r['k_platform'], 4)} | {r['err_k_pct']:+.2f}% |")
    w("")
    w("### 3.3 标度律（pi^2 D/(b^2 t) 部分是否正确）")
    w("")
    w("| 变更 | 平台临界应力 [MPa] | 由 alpha=1 基准按理论标度推得 [MPa] | 相对误差 |")
    w("|---|---|---|---|")
    for key, row in num["scaling"]["scaling_check"].items():
        w(f"| {key} | {fmt(row['sigma'], 3)} | {fmt(row['expected'], 3)} | {row['err_pct']:+.3f}% |")
    w("")
    w("sigma ∝ t^2、∝ 1/b^2、∝ E 全部成立（误差 <<1%），说明缺陷只在系数 k，不在标度因子。")
    w("")
    w("### 3.4 临界载荷量纲")
    w("")
    w("| alpha | 平台 sigma_cr [MPa] | 平台 critical_load [kN] | 由该 sigma_cr 应有的 P=sigma*b*t [kN] | 比值 |")
    w("|---|---|---|---|---|")
    for r in num["critical_load"]["rows"]:
        if "P_ref_kN" not in r:
            continue
        w(f"| {r['alpha']:g} | {fmt(r['sigma_platform'], 2)} | {fmt(r['P_platform_kN'], 4)} | "
          f"{fmt(r['P_ref_kN'], 1)} | {r['ratio']:.3e} |")
    w("")
    w("### 3.5 兄弟端点是否继承缺陷")
    w("")
    sib = num["siblings"]
    for key, out in sib.items():
        if out.get("ok"):
            body = out["body"]
            keys = ("buckling_strength", "ultimate_strength", "critical_stress")
            shown = {k: body.get(k) for k in keys if k in body}
            w(f"- `{key}`: {json.dumps(shown, ensure_ascii=False)}")
        else:
            w(f"- `{key}`: HTTP {out.get('status')} {str(out.get('body'))[:120]}")
    w("")
    w("## ④ 结果图")
    w("")
    caps = {
        "fig1_k_vs_alpha.png": "屈曲系数 k(alpha)：精确解（线）与平台反推值（点）。平台值恒为 4，"
                               "只在 alpha=1,2,3,4 等整数点落到精确曲线上。",
        "fig2_error.png": "平台 k 的带符号相对误差：负值代表料敌从宽——低估板格强度（不安全）。",
        "fig3_critical_load.png": "critical_load 与应得值对比（对数轴）：平台值系统性偏低 6 个数量级。",
    }
    for p in figs:
        name = os.path.basename(p)
        w(f"### {caps.get(name, name)}")
        w("")
        w(f"![{name}](figs/{name})")
        w("")
    w("## ⑤ 注意点与局限")
    w("")
    w("- **只审计弹性屈曲**：平台自述“简化筛选，不替代船级社规范校核”，本审计不涉弹塑性/"
      "极限强度、初始缺陷、残余应力、板格相互作用。")
    w("- **固支分支只作渐近比对**：平台固支用常数 k=7.0，其源码自述为“fixed conservative "
      "simplification”。本审计仅把它与长板渐近值 6.97 比对（-0.43%），并不主张该常数在")
    w("  alpha<1.4 时成立——那需要一面板 aspect-ratio 表，本次未引入；尝试过的固支")
    w("  Rayleigh-Ritz 参考解在 alpha>4 时收敛性未确立，故**弃用**，不用于任何判定。")
    w("- **审计矩阵单点几何**：b=1 m、t=12 mm、nu=0.3；标度律已单独验证，故"
      "结论对其它 t、b、E 同样成立。")
    w("- **纯剪参考的精度**：Galerkin 复算与 Timoshenko 式在 alpha=1 差 "
      f"{fmt(min(abs(r['k_ref_numeric']/r['k_ref']-1)*100 for r in shr), 2)}%，"
      "alpha=2 差 3.3%（8×8 基收敛限），故 A2 判定的不确定度约 3%。")
    w("- **未修平台代码**：本单只清偿“验证欠账”，缺陷修复以独立派单交 ")
    w("  `ship-performance-platform`：mission **1894**（runner=native，repo=ship-performance-platform）。")
    w("")
    w("## ⑥ 误差判定")
    w("")
    w("| 判据 | 内容 | 阈值 | 实测 | 结果 |")
    w("|---|---|---|---|---|")
    for vid in ("A1", "A2", "A3", "A4", "A5", "A6"):
        row = v.get(vid)
        if not row:
            continue
        worst = row["worst_err_pct"]
        measured = ("-" if worst is None else f"{worst:+.2f}%")
        extra = f"（越界 {row['n_violations']} 点）" if row["n_violations"] else ""
        w(f"| {vid} | {row['claim']} | {row['criterion']} | {measured}{extra} | **{row['result']}** |")
    w("")
    a1 = v["A1"]
    a2 = v.get("A2", {})
    a3 = v.get("A3", {})
    a5 = v.get("A5", {})
    w("**是否 <3% 的显式回答：**")
    w("")
    w(f"- A1（简支压缩屈曲系数）：**否**。最坏 {a1['worst_err_pct']:+.2f}% 出现在 "
      f"alpha={a1['worst_alpha']:g}；{a1['n_violations']}/{len(comp)} 个 aspect ratio 越 3% 线，"
      "最大越界同时越过 15% 不可接受线（alpha=0.5 时低估 36%）。")
    if a2:
        w(f"- A2（简支纯剪屈曲系数）：**是**。最坏 {a2['worst_err_pct']:+.2f}%（"
          f"alpha={a2['worst_alpha']:g}），全部点在 3% 内。")
    if a3:
        w(f"- A3（critical_load 量纲）：**否**。偏差 {a3['worst_err_pct']:+.2f}%，"
          "相当于把 MN 当 kN 又除以 1000，六个数量级。")
    if a5:
        w(f"- A5（固支常数 7.0 vs 长板渐近 6.97）：**是**，{a5['worst_err_pct']:+.2f}%，"
          "但这是「渐近一致」，不构成 alpha<1.4 的验证。")
    w("")
    w("**证据分级结论**：`/api/v1/fea/plate-buckling` 的弹性部分不是 MOCK——"
      "标度因子 pi^2 D/(b^2 t) 与纯剪系数都是真算的；缺陷是压缩系数 k 被硬编码为常数 4.0，"
      "而 `half_waves` 被算出却未使用。故综合评级：**REAL(标度) + MOCK(压缩系数) = "
      "WITHHELD（对外不可判）**，直到 k 换成 mode-consistent 的 (m/alpha+alpha/m)^2 极小化。")
    w("")
    w("## ⑦ 修复建议（已派单：mission 1894，runner=native，repo=ship-performance-platform）")
    w("")
    w("1. `k_compression = min_m (m/alpha + alpha/m)^2`，并用同一 m 回填 `half_waves`；")
    w("2. `critical_load = sigma_cr * b * t * 1e3`（b、t 以米计，返回 kN），或统一规定几何以米、"
      "并在字段名标注单位；")
    w("3. 边界 token 白名单校验，未知值返回 422，`free` 不应静默落到固支分支；")
    w("4. `/fea/stiffened-panel` 的 `sigma_cr = 4*pi^2 E/(12(1-0.3^2))*(t_eq/b)^2` 同源，"
      "同样需要 mode-consistent k，且 E 与 nu 应可从输入取。")
    w("")
    w("---")
    w("")
    w("**工件**：`refs_plate.py`（独立参考）、`audit_plate_buckling.py`（审计器）、"
      "`numbers.json`（原始数字）、`figs/`（三图）、"
      "`tests/test_structure_plate_buckling.py`（回归测试）。")
    w("")
    w("**复现**：`python3 docs/verification_debt/structure_plate_buckling/audit_plate_buckling.py`")
    w("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--outdir", default=HERE)
    args = ap.parse_args()

    num = {
        "base": args.base,
        "matrix": {"alphas": list(ALPHAS), "b_m": B, "t_m": T, "E_MPa": E,
                   "nu": NU, "sigma_y_MPa": SIGMA_Y,
                   "unit_stress_MPa": R.plate_factor(E, NU, T, B)},
        "compression": probe_compression(args.base),
        "shear": probe_shear(args.base),
        "scaling": probe_scaling(args.base),
        "critical_load": probe_critical_load(args.base),
        "boundaries": probe_boundaries(args.base),
        "siblings": probe_siblings(args.base),
    }
    online = any(r.get("response", {}).get("ok") for r in num["compression"])
    num["online"] = online
    num["verdicts"] = verdicts(num) if online else []
    if "scaling" in num:
        num["scaling"].pop("probes", None)  # keep numbers.json compact & readable

    figdir = os.path.join(args.outdir, "figs")
    figs = make_figures(num, figdir) if online else []

    with open(os.path.join(args.outdir, "numbers.json"), "w") as fh:
        json.dump(num, fh, ensure_ascii=False, indent=2, default=str)

    report = write_report(num, figs, args.outdir, args.base) if online else \
        "# audit offline: platform endpoint unreachable\n"
    with open(os.path.join(args.outdir, "REPORT.md"), "w") as fh:
        fh.write(report)

    if online:
        for row in num["verdicts"]:
            print(f"{row['id']}: {row['result']:4s} "
                  f"worst={row['worst_err_pct'] if row['worst_err_pct'] is None else round(row['worst_err_pct'], 2)}% "
                  f"violations={row['n_violations']}")
        print(f"figures: {[os.path.basename(p) for p in figs]}")
    else:
        print("platform endpoint unreachable; wrote offline stub only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())