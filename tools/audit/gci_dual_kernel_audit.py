#!/usr/bin/env python3
"""双 GCI 内核分歧审计 + 「渐近性判据恒真」缺陷定级（m2033）。

动机
----
平台里有两套**互不相干**的网格收敛指数（GCI）实现，都被当作"离散误差不确定性"
的真源：

* 内核 A —— ``src/verification/gci.py``（命令行 ``tools/gci_report.py`` 用它出
  ``verification_status: PASS/UNVERIFIED`` **和退出码**，直接当 CI 门禁用）；
* 内核 B —— ``backend/services/vv/gci.py``（``assess_convergence``，带
  ``asymptotic_ratio`` / ``asymptotic_ok`` / ``exact`` 覆盖率判定）。

两者算的不是同一个"渐近范围检验"。本工具用**可机器裁决的电池**回答三个问题：

1. 两个内核在能算 p 的时候数值是否一致？（若不一致，平台同一份三网格数据会给出
   两个不同的不确定度带。）
2. 两侧的"渐近范围指示量"是否携带信息？（代数上可证）
3. 现有 ``PASS`` 门禁会不会给出**假绿**？（给出反例 + 修复后的判决）

结论（可复现）
------------
* 数值一致：sweep 内 ``p`` 与 ``GCI`` 的最大相对偏差 ~1e-12（双内核同公式、不同解法）。
* ``asym_ratio = e32/(r21**p * e21)`` 在 ``p`` 由同一组三点估计时**恒等于 1**
  （代入 Eq. A 即得 ``r21**p / r21**p``）。所以内核 B 的 ``asymptotic_ok`` 恒真、
  内核 A 的 ``asymptotic`` 实际只是"细化比 >= 1.3"的代理量 —— **两侧都没有做
  可伪证的渐近范围检验**。
* 假绿反例：``phi = 1 + 4h^2 + 30h^3``（r=2，形式二阶）→ 估计阶 2.4367
  （偏差 21.8%），修复前 ``asymptotic=True`` / CLI 退出码 0 / ``PASS``。
  修复后（``p_expected`` 判据）判 ``UNVERIFIED`` + 退出码 1。

用法::

    python3 tools/audit/gci_dual_kernel_audit.py --out artifacts/gci_dual_kernel \
        [--sweep 3000] [--figures]

产物
----
``<out>/audit.json``、``<out>/audit.md``、``--figures`` 时三张 png。

纪律
----
纯只读、离线、零网络；sweep 用固定 seed（可复算）；随机电池只用来证明**恒等式**
与**一致性**，反例本身是显式常量的确定性数据。
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import random
import sys
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SEED = 2033
ORDER_TOL = 0.10


def load_kernels():
    """Import both kernels *as modules* (the packages re-export functions)."""
    import src.verification  # noqa: F401  (ensure package import path)
    import services.vv  # noqa: F401
    A = importlib.import_module("src.verification.gci")
    B = importlib.import_module("services.vv.gci")
    return A, B


def synth(phi_ext: float, terms: Sequence[Tuple[float, float]], h: float) -> float:
    """``phi(h) = phi_ext + sum(c * h**k)`` -- multi-term truncation model."""
    return phi_ext + sum(c * h ** k for c, k in terms)


def deterministic_battery() -> List[Dict]:
    """Explicit, hand-written cases (no randomness).  Every case is citable."""
    cases: List[Dict] = []

    # (1) 文献算例：Celik, Ghia, Roache et al. (2008) 三网格工作示例数据。
    cases.append({
        "name": "celik2008_worked_example",
        "note": "Celik et al. 2008 JFE 130:078001 worked example (r=2 uniform)",
        "h": [1.0, 2.0, 4.0],
        "phi": [0.97050, 0.96854, 0.96178],
        "p_expected": None,
        "ref": {"p": 1.786, "gci_pct": 0.103, "phi_ext": None},
    })

    # (2) 纯单项式序列：p 必须被精确复原（p=1/2/3，均匀 r=2）。
    for p_true in (1.0, 2.0, 3.0):
        h = [1 / 80.0, 1 / 40.0, 1 / 20.0]
        cases.append({
            "name": f"mms_single_term_p{p_true:g}",
            "note": "pure truncation term, order must be recovered exactly",
            "h": h, "phi": [1.0 + 0.7 * hi ** p_true for hi in h],
            "p_expected": p_true, "ref": None,
        })

    # (3) 非均匀细化比（教科书的 ln|eps32/eps21|/ln r 在此失效）。
    h = [1 / 90.0, 1 / 60.0, 1 / 37.5]  # r21 = 1.5, r32 = 1.6
    cases.append({
        "name": "mms_nonuniform_r15_r16",
        "note": "non-uniform refinement ratios (r21=1.5, r32=1.6), p_true=3",
        "h": h, "phi": [2.5 - 0.4 * hi ** 3.0 for hi in h],
        "p_expected": 3.0, "ref": None,
    })

    # (4) **假绿反例**：两项截断 4h^2 + 30h^3，形式二阶，r=2。
    #     修复前 asymptotic=True（只查 r>=1.3）-> CLI 退出码 0 / PASS。
    h = [1 / 32.0, 1 / 16.0, 1 / 8.0]
    cases.append({
        "name": "preasymptotic_false_green",
        "note": "phi = 1 + 4h^2 + 30h^3 (formal order 2): observed order drifts 21.8%",
        "h": h, "phi": [synth(1.0, [(4.0, 2.0), (30.0, 3.0)], hi) for hi in h],
        "p_expected": 2.0, "ref": None,
    })

    # (5) 振荡收敛（R < 0）：两项内核必须都不"照单全收"。
    h = [1 / 8.0, 1 / 4.0, 1 / 2.0]
    cases.append({
        "name": "oscillatory_R_negative",
        "note": "alternating solution changes (R<0)",
        "h": h, "phi": [1.00, 1.06, 1.03], "p_expected": 2.0, "ref": None,
    })

    # (6) 发散细化（R > 1）。
    cases.append({
        "name": "divergent_R_gt_1",
        "note": "changes grow under refinement (R>1)",
        "h": [1 / 8.0, 1 / 4.0, 1 / 2.0], "phi": [1.0, 3.0, 4.5],
        "p_expected": 2.0, "ref": None,
    })

    # (7) 舍入极限（最细两网格到 5 位有效数字已无变化）。
    cases.append({
        "name": "roundoff_degenerate",
        "note": "two finest solutions agree to round-off (d21 == 0)",
        "h": [0.0136, 0.0184, 0.0352], "phi": [0.90338, 0.90338, 0.90336],
        "p_expected": 2.0, "ref": None,
    })

    # (8) 细化比不足（r=1.1 < 1.3，Celik 建议下限）。
    h = [1.0, 1.1, 1.21]
    cases.append({
        "name": "small_refinement_ratio",
        "note": "r = 1.1 < 1.3: round-off can swamp the solution changes",
        "h": h, "phi": [1.0 + 0.5 * hi ** 2.0 for hi in h],
        "p_expected": 2.0, "ref": None,
    })
    return cases


def run_case(A, B, case: Dict) -> Dict:
    """Evaluate one triplet through both kernels and diff the verdicts."""
    h, phi = case["h"], case["phi"]
    rec: Dict = {"name": case["name"], "note": case["note"],
                 "h": list(h), "phi": [float(v) for v in phi],
                 "p_expected": case["p_expected"]}

    try:
        ra = A.gci(h, phi, p_expected=case["p_expected"], order_tol=ORDER_TOL)
        rec["A"] = {
            "regime": ra.regime, "p": ra.p, "phi_ext": ra.phi_ext,
            "gci_fine": ra.gci_fine, "gci_fine_pct": ra.gci_fine_pct,
            "asym_ratio": ra.asym_ratio,
            "asym_ratio_tautological": ra.asym_ratio_tautological,
            "order_rel_dev": ra.order_rel_dev, "order_ok": ra.order_ok,
            "asymptotic": ra.asymptotic, "notes": ra.notes,
        }
    except Exception as exc:                       # pragma: no cover - defensive
        rec["A"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        rb = B.assess_convergence(
            [B.Level(h=float(h[i]), value=float(phi[i])) for i in range(len(h))],
            expected_order=case["p_expected"],
        )
        rec["B"] = {
            "verdict": rb.verdict, "observed_order": rb.observed_order,
            "extrapolated_value": rb.extrapolated_value,
            "gci_fine": rb.gci_fine, "asym_ratio": rb.asymptotic_ratio,
            "asym_ok": rb.asymptotic_ok, "passed": rb.passed,
            "warnings": list(rb.warnings),
        }
    except Exception as exc:
        rec["B"] = {"error": f"{type(exc).__name__}: {exc}"}

    a, b = rec["A"], rec["B"]
    if "error" not in a and "error" not in b:
        if a["p"] is not None and b["observed_order"] is not None:
            rec["rel_dp"] = abs(a["p"] - b["observed_order"]) / max(abs(b["observed_order"]), 1e-300)
        if a["gci_fine"] and b["gci_fine"]:
            rec["rel_dgci"] = abs(a["gci_fine"] - b["gci_fine"]) / abs(b["gci_fine"])
        rec["regime_agree"] = (
            (a["regime"] == "monotone") == (b["verdict"] == "monotone_convergence")
        )
        rec["gate_agree"] = bool(a["asymptotic"]) == bool(b["passed"])
    return rec


def randomized_sweep(A, B, n: int = 3000, seed: int = SEED) -> Dict:
    """Multi-term truncation + rounded output, mildly non-uniform ratios.

    Purpose: prove the two things that a hand-picked battery cannot prove --
    (i) the two kernels are numerically the same estimator, and (ii) the
    classical range indicator is an identity, not a test.
    """
    rng = random.Random(seed)
    n_used = n_p = n_cls = 0
    max_dp = max_dg = 0.0
    max_identity = 0.0
    asym_ratios: List[float] = []
    dgci: List[float] = []
    dp: List[float] = []
    class_pairs: Dict[str, int] = {}

    for _ in range(n):
        h1 = rng.uniform(0.01, 0.05)
        r21, r32 = rng.uniform(1.3, 2.2), rng.uniform(1.3, 2.2)
        h = [h1, h1 * r21, h1 * r21 * r32]
        p = rng.choice([1.0, 1.5, 2.0, 3.0])
        q = p + rng.choice([0.5, 1.0])
        ext = rng.uniform(0.5, 2.0)
        a = rng.choice([-1, 1]) * rng.uniform(0.2, 1.0)
        b = rng.choice([-1, 1]) * rng.uniform(0.1, 0.8)
        phi = [round(ext + a * hi ** p + b * hi ** q, 5) for hi in h]
        try:
            ra = A.gci(h, phi)
            rb = B.assess_convergence([B.Level(h=h[i], value=phi[i]) for i in range(3)])
        except Exception:
            continue
        n_used += 1
        if ra.asym_ratio is not None:
            asym_ratios.append(ra.asym_ratio)
            max_identity = max(max_identity, abs(ra.asym_ratio - 1.0))
        if ra.p is not None and rb.observed_order is not None:
            n_p += 1
            rel = abs(ra.p - rb.observed_order) / max(abs(rb.observed_order), 1e-300)
            dp.append(rel)
            max_dp = max(max_dp, rel)
            if ra.gci_fine and rb.gci_fine:
                relg = abs(ra.gci_fine - rb.gci_fine) / abs(rb.gci_fine)
                dgci.append(relg)
                max_dg = max(max_dg, relg)
        if (ra.regime == "monotone") != (rb.verdict == "monotone_convergence"):
            n_cls += 1
            key = f"{ra.regime}|{rb.verdict}"
            class_pairs[key] = class_pairs.get(key, 0) + 1

    return {
        "seed": seed, "trials_requested": n, "trials_used": n_used,
        "n_p_compared": n_p,
        "max_rel_dp": max_dp, "max_rel_dgci": max_dg,
        "max_abs_asym_ratio_minus_1": max_identity,
        "asym_ratio_min": min(asym_ratios) if asym_ratios else None,
        "asym_ratio_max": max(asym_ratios) if asym_ratios else None,
        "n_regime_disagree": n_cls,
        "regime_disagree_pairs": class_pairs,
        "_dp": dp, "_dgci": dgci, "_asym": asym_ratios,
    }


def cellik_reference_check(res: Dict) -> Optional[Dict]:
    """Judge the literature anchor against published p and GCI (rounded)."""
    for rec in res["battery"]:
        if rec["name"] != "celik2008_worked_example":
            continue
        a, b = rec["A"], rec["B"]
        if "error" in a or "error" in b:
            return None
        return {
            "p_published": 1.786, "p_A": a["p"], "p_B": b["observed_order"],
            "rel_err_p_A": abs(a["p"] - 1.786) / 1.786,
            "rel_err_p_B": abs(b["observed_order"] - 1.786) / 1.786,
            "gci_pct_published": 0.103, "gci_pct_A": a["gci_fine_pct"],
            "rel_err_gci_A": abs(a["gci_fine_pct"] - 0.103) / 0.103,
        }
    return None


def figures(res: Dict, outdir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: List[str] = []
    sw = res["sweep"]

    # fig 1 -- the identity: asym_ratio == 1 to machine precision, i.e. no test.
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    dev = [abs(v - 1.0) for v in sw["_asym"]]
    ax.hist(dev, bins=50, color="#3b6ea5")
    ax.set_xscale("log")
    ax.set_xlabel("|asym_ratio - 1|   (algebraic identity would be exactly 0)")
    ax.set_ylabel("cases")
    ax.set_title(
        "Range indicator e32/(r21^p e21) is identically 1 when p is estimated\n"
        f"from the same triplet ({sw['trials_used']} random pre-asymptotic cases, "
        f"max |dev| = {sw['max_abs_asym_ratio_minus_1']:.1e})"
    )
    fig.tight_layout()
    p1 = os.path.join(outdir, "fig1_asym_identity.png")
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    paths.append(p1)

    # fig 2 -- observed vs formal order on the deterministic battery.
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    for rec in res["battery"]:
        a = rec["A"]
        if "error" in a or rec["p_expected"] is None or a["p"] is None:
            continue
        ok = bool(a["order_ok"])
        ax.scatter(rec["p_expected"], a["p"], s=70,
                   color="#2e8b57" if ok else "#c0392b",
                   marker="o" if ok else "X", zorder=3)
        ax.annotate(rec["name"], (rec["p_expected"], a["p"]),
                    textcoords="offset points", xytext=(6, -10), fontsize=7)
    xs = [0.5, 3.5]
    ax.plot(xs, xs, "k-", lw=1, label="p_obs = p_formal")
    ax.fill_between(xs, [x * (1 - ORDER_TOL) for x in xs],
                    [x * (1 + ORDER_TOL) for x in xs], color="grey", alpha=0.2,
                    label=f"+-{int(ORDER_TOL*100)}% order band")
    ax.set_xlabel("formal order of accuracy  p_expected")
    ax.set_ylabel("observed order  p")
    ax.set_title("Only the order-of-accuracy test is falsifiable\n"
                 "(red X = pre-asymptotic: GCI band must not be certified)")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    p2 = os.path.join(outdir, "fig2_order_check.png")
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    paths.append(p2)

    # fig 3 -- dual-kernel numerical agreement (where both estimate p).
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    vals = [max(v, 1e-16) for v in sw["_dgci"]]
    ax.hist(vals, bins=50, color="#8e6ba8")
    ax.set_xscale("log")
    ax.set_xlabel("|GCI_A - GCI_B| / GCI_B")
    ax.set_ylabel("cases")
    ax.set_title(f"Two kernels are the same estimator ({sw['n_p_compared']} cases,\n"
                 f"max rel. diff = {sw['max_rel_dgci']:.1e})")
    fig.tight_layout()
    p3 = os.path.join(outdir, "fig3_kernel_agreement.png")
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    paths.append(p3)
    return paths


def _fmt(v, spec="%.6g"):
    return "n/a" if v is None else spec % v


def to_markdown(res: Dict) -> str:
    L: List[str] = []
    sw = res["sweep"]
    L.append("# 双 GCI 内核分歧审计 / 渐近性判据恒真缺陷（m2033）\n")
    L.append(f"* 电池用例：**{len(res['battery'])}**（确定性、写死在脚本里）")
    L.append(f"* 随机 sweep：**{sw['trials_used']}** 例（seed={sw['seed']}，多项截断 + 5 位舍入 + 非均匀细化比）")
    L.append(f"* p 可比较例数：{sw['n_p_compared']}；max rel. Δp = {sw['max_rel_dp']:.2e}；"
             f"max rel. ΔGCI = {sw['max_rel_dgci']:.2e}")
    L.append(f"* asym_ratio 恒等偏差：max |ratio-1| = {sw['max_abs_asym_ratio_minus_1']:.2e}"
             f"（区间 [{_fmt(sw['asym_ratio_min'])} , {_fmt(sw['asym_ratio_max'])}]）")
    L.append(f"* 收敛类判决不一致：{sw['n_regime_disagree']} 例 → {sw['regime_disagree_pairs']}\n")
    L.append("| case | regime A | p A | p B | Δp rel | GCI A % | GCI B | order dev | "
             "A asymptotic | B passed |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for rec in res["battery"]:
        a, b = rec["A"], rec["B"]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            rec["name"],
            a.get("regime", a.get("error", "?")), _fmt(a.get("p"), "%.4f"),
            _fmt(b.get("observed_order"), "%.4f"),
            _fmt(rec.get("rel_dp"), "%.1e"),
            _fmt(a.get("gci_fine_pct"), "%.4f"), _fmt(b.get("gci_fine"), "%.6e"),
            _fmt(a.get("order_rel_dev"), "%.3f"),
            a.get("asymptotic"), b.get("passed"),
        ))
    ref = res.get("celik_reference")
    if ref:
        L.append("\n**文献锚（Celik et al. 2008 工作示例）**：发布 p=1.786 / GCI=0.103%；"
                 f"内核 A p={ref['p_A']:.6f}（相对偏差 {100*ref['rel_err_p_A']:.4f}%），"
                 f"内核 B p={ref['p_B']:.6f}（{100*ref['rel_err_p_B']:.4f}%），"
                 f"A 的 GCI={ref['gci_pct_A']:.6f}%（相对偏差 {100*ref['rel_err_gci_A']:.4f}%）。")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(_REPO_ROOT, "artifacts", "gci_dual_kernel"))
    ap.add_argument("--sweep", type=int, default=3000)
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args(argv)

    A, B = load_kernels()
    res: Dict = {
        "kernel_A": "src/verification/gci.py:gci()  (CLI tools/gci_report.py gate)",
        "kernel_B": "backend/services/vv/gci.py:assess_convergence()",
        "order_tol": ORDER_TOL,
        "battery": [run_case(A, B, c) for c in deterministic_battery()],
    }
    res["celik_reference"] = cellik_reference_check(res)
    res["sweep"] = randomized_sweep(A, B, n=args.sweep)

    os.makedirs(args.out, exist_ok=True)
    fig_paths: List[str] = []
    if args.figures:
        fig_paths = figures(res, args.out)
    res["figures"] = [os.path.relpath(p, _REPO_ROOT) for p in fig_paths]

    sw_out = {k: v for k, v in res["sweep"].items() if not k.startswith("_")}
    res["sweep"] = sw_out
    with open(os.path.join(args.out, "audit.json"), "w") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "audit.md"), "w") as fh:
        fh.write(to_markdown({**res, "sweep": sw_out}))
    print(to_markdown({**res, "sweep": sw_out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())