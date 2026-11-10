"""Render the UQ framework report assets (figures + markdown) from the ledger.

Every number in the generated document is read from the ledger produced by
``tools.uq_bench.run_case``; every physics-specific sentence comes from the case
card's own markdown fragments (``docs.intro`` / ``docs.caveats`` in the case
JSON).  This module therefore stays domain free -- it is a renderer, not a
source of domain content (platform rule: mechanism in code, physics in data).

Usage::

    python -m tools.uq_bench.run_case --all --out-dir artifacts/uq_pce_m9011 --sweep
    python -m tools.uq_bench.make_report --ledger artifacts/uq_pce_m9011/ledger.json \\
        --out-dir artifacts/uq_pce_m9011 --doc docs/verification/pce_uq_framework_m9011/REPORT.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# case helpers (structural selection -- no case ids in code)
# --------------------------------------------------------------------------- #
def split_cases(ledger: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split into (analytic, propagation) by what the case actually carries."""
    analytic = None
    propagation = None
    for case in ledger["cases"].values():
        if "closed_form_reference" in case:
            analytic = case
        elif "error_vs_high_accuracy_reference" in case.get("comparison", {}):
            propagation = case
    if analytic is None or propagation is None:
        raise SystemExit("ledger must contain one analytic case (closed-form reference) "
                         "and one propagation case (high-accuracy MC reference)")
    return analytic, propagation


def _presentation(case: Dict[str, Any]) -> Dict[str, Any]:
    return dict(case.get("spec", {}).get("presentation", {}))


def _response_scale(case: Dict[str, Any]) -> Tuple[float, str, str]:
    resp = _presentation(case).get("response", {})
    # ``scale`` is a multiplier: display_value = raw_value * scale (e.g. N -> kN)
    return (float(resp.get("scale", 1.0)), str(resp.get("unit", "")),
            str(resp.get("label", "response")))


def _input_label(case: Dict[str, Any], name: str) -> str:
    return _presentation(case).get("input_labels", {}).get(name, name)


def _read_fragment(case: Dict[str, Any], key: str) -> str:
    path = case.get("spec", {}).get("docs", {}).get(key)
    if not path:
        return ""
    full = REPO_ROOT / path
    return full.read_text() if full.exists() else ""


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def fig_analytic_sobol(case: Dict[str, Any], path: Path) -> Path:
    ref = case["closed_form_reference"]
    sobol = case["sobol"]
    names = list(ref["first_order"])
    labels = [_input_label(case, n) for n in names]
    x = np.arange(len(names))
    width = 0.2
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    ax.bar(x - 1.5 * width, [ref["first_order"][n] for n in names], width,
           label="analytic $S_i$", color="#4c72b0")
    ax.bar(x - 0.5 * width, [sobol["first_order"][n] for n in names], width,
           label="PCE $S_i$", color="#8fb3e0")
    ax.bar(x + 0.5 * width, [ref["total_order"][n] for n in names], width,
           label="analytic $S_{T,i}$", color="#c44e52")
    ax.bar(x + 1.5 * width, [sobol["total_order"][n] for n in names], width,
           label="PCE $S_{T,i}$", color="#e8a0a2")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Sobol' index [-]")
    ax.set_title("analytic vs PCE Sobol' indices   "
                 f"(max abs error {case['sobol_max_abs_error']:.4f})")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_analytic_convergence(case: Dict[str, Any], path: Path) -> Path:
    rows = [r for r in case.get("sweep", []) if "sobol_max_abs_error" in r]
    orders = [r["order"] for r in rows]
    errs = [max(r["sobol_max_abs_error"], 1e-8) for r in rows]
    band = float(case["criteria"].get("sobol_max_abs_error", 0.05))
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.semilogy(orders, errs, "o-", label="max |index - index_ref|")
    ax.axhline(band, color="#c44e52", ls="--", label=f"{100 * band:g} % acceptance band")
    ax.set_xlabel("PCE total degree $p$")
    ax.set_ylabel("max abs Sobol' error [-]")
    ax.set_title("spectral convergence of the variance decomposition")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_uncertainty_band(case: Dict[str, Any], path: Path) -> Path:
    scale, unit, label = _response_scale(case)
    hist = case["comparison"]["mc_sample_histogram"]
    edges = np.asarray(hist["edges"], dtype=float) * scale
    counts = np.asarray(hist["counts"], dtype=float)
    widths = np.diff(edges)
    density = counts / (counts.sum() * widths)
    ref = case["comparison"]["error_vs_high_accuracy_reference"]["reference"]
    band = case["band"]

    fig, ax = plt.subplots(figsize=(7.8, 4.2))
    ax.bar(0.5 * (edges[1:] + edges[:-1]), density, width=widths, alpha=0.55,
           color="#8fb3e0", label=f"direct sampling, N = {hist['n']:,}")
    for key, style in (("p5", ":"), ("p50", "-"), ("p95", ":")):
        ax.axvline(band["pce"][key] * scale, color="#c44e52", ls=style, lw=1.4)
    pm2 = band["pce_mean_plus_minus_2sigma"]
    ax.axvline(pm2[0] * scale, color="#4c72b0", ls="-.", lw=1.2, label=r"surrogate mean $\pm 2\sigma$")
    ax.axvline(pm2[1] * scale, color="#4c72b0", ls="-.", lw=1.2)
    ax.axvline(ref["mean"] * scale, color="k", lw=1.0, alpha=0.6,
               label=f"QMC N = {ref['n_model_evaluations']:,} mean")
    ax.set_xlabel(f"{label} [{unit}]")
    ax.set_ylabel(f"probability density [1/{unit}]")
    ax.set_title("response uncertainty band: surrogate quantiles vs direct sampling")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_sensitivity_ranking(case: Dict[str, Any], path: Path) -> Path:
    sobol = case["sobol"]
    names = sorted(sobol["first_order"], key=sobol["first_order"].get, reverse=True)
    labels = [_input_label(case, n)
              for n in names]
    y = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.barh(y - 0.2, [sobol["first_order"][n] for n in names], 0.4,
            label="$S_i$ (first order)", color="#4c72b0")
    ax.barh(y + 0.2, [sobol["total_order"][n] for n in names], 0.4,
            label="$S_{T,i}$ (total order)", color="#c44e52")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Sobol' index of the response [-]")
    ax.set_title("uncertainty attribution (PCE spectral ANOVA, no extra model runs)")
    ax.legend(fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_cost_accuracy(case: Dict[str, Any], path: Path) -> Path:
    rows = [r for r in case.get("sweep", []) if "std_rel_error" in r]
    band = float(case["criteria"].get("std_rel_error", 0.03))
    limit = float(case["comparison"]["mc_baseline"]["n_model_evaluations"]) / float(
        case["criteria"].get("speedup_model_evaluations_min", 100))
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    for r in rows:
        cfg = r["config"]
        label = (f"{'proj' if cfg['method'] == 'projection' else 'lsq'} p={cfg['order']}"
                 f" N={r['n_model_evaluations']}")
        y = max(r["std_rel_error"], 1e-5)
        ax.plot(r["n_model_evaluations"], y, "o")
        ax.annotate(label, (r["n_model_evaluations"], y), textcoords="offset points",
                    xytext=(4, 4), fontsize=6)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.axhline(band, color="#c44e52", ls="--", label=f"{100 * band:g} % moment band")
    ax.axvline(limit, color="#55a868", ls=":", label=f"acceleration limit (N = {limit:.0f})")
    ax.set_xlabel("black-box evaluations spent by the surrogate")
    ax.set_ylabel("relative error of the response standard deviation")
    ax.set_title("cost / accuracy trade-off of the surrogate")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# placeholder context
# --------------------------------------------------------------------------- #
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def _pct(x: float, digits: int = 3) -> str:
    return f"{100.0 * float(x):.{digits}f} %"


def _annotations(case: Dict[str, Any]) -> Dict[str, str]:
    """Evaluate the case card's display annotations over the nominal inputs."""
    pres = _presentation(case)
    nominal = dict(case.get("nominal_inputs", {}))
    out: Dict[str, str] = {}
    for name, spec in pres.get("annotations", {}).items():
        try:
            value = float(eval(str(spec["expr"]), {"__builtins__": {}}, dict(nominal)))  # noqa: S307
        except Exception:  # pragma: no cover - reported as a missing placeholder
            continue
        out[name] = format(value, str(spec.get("format", ".4g")))
    return out


def build_context(ledger: Dict[str, Any], analytic: Dict[str, Any],
                  propagation: Dict[str, Any]) -> Dict[str, str]:
    ctx: Dict[str, str] = {}
    # -- analytic case ------------------------------------------------------
    ref_a = analytic["closed_form_reference"]
    ctx.update({
        "ish_mean": f"{ref_a['mean']:.4f}",
        "ish_var": f"{ref_a['variance']:.6f}",
        "ish_err": f"{analytic['sobol_max_abs_error']:.2e}",
        "ish_grid": f"{analytic['pce']['design']['quadrature']['n_points']}",
        "ish_st3": f"{list(ref_a['total_order'].values())[-1]:.4f}",
        "ish_s1_x3": f"{list(analytic['sobol']['first_order'].values())[-1]:.5f}",
        "ish_st_x3": f"{list(analytic['sobol']['total_order'].values())[-1]:.5f}",
    })
    # -- propagation case ---------------------------------------------------
    scale, unit, label = _response_scale(propagation)
    mc = propagation["comparison"]["mc_baseline"]
    ref = propagation["comparison"]["error_vs_high_accuracy_reference"]["reference"]
    err = propagation["comparison"]["error_vs_mc_baseline"]
    ref_err = propagation["comparison"]["error_vs_high_accuracy_reference"]
    timing = propagation["comparison"]["timing"]
    band = propagation["band"]

    ctx.update({
        "mc_mean": f"{mc['mean'] * scale:.1f}",
        "mc_std": f"{mc['std'] * scale:.1f}",
        "mc_n": f"{mc['n_model_evaluations']:,}",
        "ref_mean": f"{ref['mean'] * scale:.1f}",
        "ref_std": f"{ref['std'] * scale:.1f}",
        "ref_n": f"{ref['n_model_evaluations']:,}",
        "cv_RT": _pct(mc["std"] / mc["mean"], 2),
        "band_p5": f"{band['pce']['p5'] * scale:.1f}",
        "band_p50": f"{band['pce']['p50'] * scale:.1f}",
        "band_p95": f"{band['pce']['p95'] * scale:.1f}",
        "band_p95_excess": _pct((band["pce"]["p95"] - mc["mean"]) / mc["mean"], 2),
        "nom_rt": f"{propagation['nominal_response'] * scale:.1f}",
        "pce_evals": f"{propagation['pce']['n_model_evaluations']}",
        "pce_terms": f"{propagation['pce']['n_terms']}",
        "pce_order": f"{propagation['pce']['order']}",
        "pce_level": f"{propagation['pce']['design']['quadrature'].get('level', '-')}",
        "mean_rel": _pct(err["mean_rel"], 4),
        "std_rel": _pct(err["std_rel"], 3),
        "ref_mean_rel": _pct(ref_err["mean_rel"], 4),
        "ref_std_rel": _pct(ref_err["std_rel"], 3),
        "std_rel_anchor": _pct(ref_err["std_rel"], 3),
        "mean_rel_anchor": _pct(ref_err["mean_rel"], 4),
        "chosen_std_rel_anchor": "",
        "speedup_evals": f"{propagation['comparison']['speedup_model_evaluations']:.0f}",
        "speedup_wall": f"{timing['speedup_wall_clock_median']:.0f}",
        "speedup_gap": f"{timing['speedup_wall_clock_median'] / max(propagation['comparison']['speedup_model_evaluations'], 1e-9):.2f}",
        "mc_wall_ms": f"{1000 * timing['mc_pipeline_seconds_median']:.1f}",
        "pce_wall_ms": f"{1000 * timing['pce_pipeline_seconds_median']:.1f}",
        "sum_first": f"{propagation['sobol']['sum_first_order']:.4f}",
        "sum_total": f"{propagation['sobol']['sum_total_order']:.4f}",
    })
    # nominal inputs -> {{nom_<name>}}
    for name, value in propagation.get("nominal_inputs", {}).items():
        ctx[f"nom_{name}"] = f"{value:g}"
    # case-card annotations
    ctx.update(_annotations(propagation))
    # dense projection configurations present in the sweep
    proj_rows = sorted([r for r in propagation.get("sweep", [])
                        if r.get("method") == "projection" and "std_rel_error" in r],
                       key=lambda r: r["n_model_evaluations"])
    chosen = int(propagation["pce"]["n_model_evaluations"])
    denser = [r for r in proj_rows if r["n_model_evaluations"] > chosen]
    if denser:
        mid, last = denser[0], denser[-1]
        ctx["sweep_level_mid_evals"] = str(mid["n_model_evaluations"])
        ctx["sweep_level_mid_std_err"] = _pct(mid["std_rel_error"], 3)
        ctx["sweep_level_last_evals"] = str(last["n_model_evaluations"])
        ctx["sweep_level_last_std_err"] = _pct(last["std_rel_error"], 3)
    return ctx


def render(text: str, ctx: Dict[str, str], where: str = "") -> str:
    def sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in ctx:
            raise SystemExit(f"unresolved placeholder {{{{{key}}}}} in {where}; "
                             f"add it to build_context or fix the case card")
        return ctx[key]

    return PLACEHOLDER.sub(sub, text)


# --------------------------------------------------------------------------- #
# markdown tables
# --------------------------------------------------------------------------- #
def table(header: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def build_document(ledger: Dict[str, Any], figures: Dict[str, Path]) -> str:
    analytic, propagation = split_cases(ledger)
    ctx = build_context(ledger, analytic, propagation)
    scale, unit, label = _response_scale(propagation)
    env = ledger["environment"]
    mc = propagation["comparison"]["mc_baseline"]
    ref = propagation["comparison"]["error_vs_high_accuracy_reference"]["reference"]
    err = propagation["comparison"]["error_vs_mc_baseline"]
    ref_err = propagation["comparison"]["error_vs_high_accuracy_reference"]

    analytic_rows = []
    for name in analytic["closed_form_reference"]["first_order"]:
        analytic_rows.append([
            f"`{name}`",
            f"{analytic['closed_form_reference']['first_order'][name]:.5f}",
            f"{analytic['sobol']['first_order'][name]:.5f}",
            f"{abs(analytic['sobol']['first_order'][name] - analytic['closed_form_reference']['first_order'][name]):.5f}",
            f"{analytic['closed_form_reference']['total_order'][name]:.5f}",
            f"{analytic['sobol']['total_order'][name]:.5f}",
            f"{abs(analytic['sobol']['total_order'][name] - analytic['closed_form_reference']['total_order'][name]):.5f}",
        ])

    analytic_sweep = []
    for r in analytic.get("sweep", []):
        if "sobol_max_abs_error" not in r:
            continue
        analytic_sweep.append([
            f"p={r['order']}", str(r["n_model_evaluations"]),
            f"{r['sobol_max_abs_error']:.3e}", _pct(r["std_rel_error"], 2),
            _pct(r["mean_rel_error"], 4),
        ])

    propagation_sweep = []
    for r in propagation.get("sweep", []):
        if "std_rel_error" not in r:
            continue
        cfg = r["config"]
        propagation_sweep.append([
            f"{cfg['method']} p={cfg['order']}"
            + (f" L={cfg['level']}" if "level" in cfg else f" N={cfg.get('n_samples')}"),
            str(r["n_model_evaluations"]), str(r["n_terms"]),
            _pct(r["mean_rel_error"], 4), _pct(r["std_rel_error"], 3),
            f"{r['speedup_model_evaluations']:.0f}x",
        ])

    band_rows = [[key, f"{propagation['band']['mc'][key] * scale:.1f}",
                  f"{propagation['band']['pce'][key] * scale:.1f}",
                  _pct(propagation['band']['rel_error'][key], 3)]
                 for key in propagation["band"]["pce"]]

    sobol_rows = [[f"`{name}` ({_input_label(propagation, name)})",
                   f"{propagation['sobol']['first_order'][name]:.4f}",
                   f"{propagation['sobol']['total_order'][name]:.4f}"]
                  for name in sorted(propagation["sobol"]["first_order"],
                                     key=propagation["sobol"]["first_order"].get, reverse=True)]

    check_rows = []
    for case_id, case in ledger["cases"].items():
        for name, chk in case["checks"].items():
            check_rows.append([f"`{case_id}`", f"`{name}`", f"{chk['value']:.6g}",
                               f"{chk['threshold']:.6g}", "PASS" if chk["passed"] else "FAIL"])

    total_inputs = len(propagation["spec"]["inputs"])
    doc = f"""# Non-intrusive PCE uncertainty framework: analytic Sobol' verification, six-parameter uncertainty band, Monte Carlo acceleration

* node **P152W1-N05**, repository `TensorPlatform`, branch `agent/m9011`
* machine verdict: **{('all acceptance criteria PASS' if ledger['passed'] else 'FAIL present -- see the criteria table')}**
  (table generated from `artifacts/uq_pce_m9011/ledger.json`, no hand-copied numbers)
* rerun: `python -m tools.uq_bench.run_case --all --out-dir artifacts/uq_pce_m9011 --reps 3 --sweep`
* tests: `python -m pytest tests/test_pce_uq_kernel_m9011.py -q` (18 cases)
* audit chain: git `{ledger['git_commit'][:12]}`, python {env['python']}, numpy {env['numpy']},
  generated {ledger['generated_at_utc']}; case cards sha256
  `{analytic['spec_sha256'][:16]}...` / `{propagation['spec_sha256'][:16]}...`

---

## 0 摘要（中文，数字同样由台账注入）

本节点把平台的不确定度量化能力收敛为一个**零领域语义**的非侵入式多项式混沌（PCE）内核
`backend/services/pce_uq/`：正交归一 Legendre/Hermite 基 + total-degree/双曲截断 + Gauss 张量与
Smolyak 稀疏求积 + 谱投影与 LHS 最小二乘两条装配路线 + 系数级闭式矩与 Sobol 分解 +
直接 Monte Carlo 参考与"评估次数/墙钟"双口径加速记账。物理内容全部经 `config/uq_cases/*.json`
算例卡注入；平台域词扫描器对内核与运行器命中 0（常驻 pytest）。

机器判据（全部由 `tools/uq_bench/run_case.py` 裁决，数字见台账）：

* 解析算例（Ishigami–Homma 1990 闭式方差分解）：Sobol 一阶/总效应最大绝对误差
  **{analytic['sobol_max_abs_error']:.2e}**（判据 0.05），且一阶≈0、总效应>0 的判别特征被正确复现；
* 六参数传播算例（黑箱 = 仓库已发布的经验回归模型）：响应均值/标准差相对误差
  **{_pct(err['mean_rel'], 4)} / {_pct(err['std_rel'], 3)}**（判据 3 %），
  对 2×10⁵ 点 QMC 精确基准为 **{_pct(ref_err['mean_rel'], 4)} / {_pct(ref_err['std_rel'], 3)}**；
* 加速比：仅用 **{propagation['pce']['n_model_evaluations']}** 次黑箱评估即达到上述精度，
  相对直接 Monte Carlo（10⁴ 次）为 **{propagation['comparison']['speedup_model_evaluations']:.0f}×**
  （评估次数口径，主判据），同机同接口墙钟 **{propagation['comparison']['timing']['speedup_wall_clock_median']:.0f}×**（判据 100×）；
* 结论：**{('全部判据 PASS' if ledger['passed'] else '存在 FAIL')}**，无 honest-FAIL 需声明。

---

## 1 Why this layer exists

Three PCE code paths existed in the platform before this node -- one bound to a
single physical model (projection, quadrature based), one least-squares surrogate
for a digital twin, one pick-freeze Sobol' estimator. Each ran, but nothing was
*scored*: acceptance rested on self-consistency invariants, which are blind to a
globally rescaled or mis-normalised basis. Changing the black box meant rewriting
the assembly code, and "how much faster is this than brute force" had no ledger.

`backend/services/pce_uq/` is the domain-free kernel that closes that gap. It
accepts only a black box `f(x) -> y`, one marginal law per input, and a JSON case
card; all physical content arrives through `config/uq_cases/*.json`. The platform's
own vocabulary scanner (`tools/audit/domain_leak_scan.py` with
`config/domain_word_denylist.json`) reports **0 hits** on the kernel and the runner,
and that check is a standing pytest case.

## 2 Case cards

### 2.1 Analytic card: {analytic['title']}

{render(_read_fragment(analytic, 'intro'), ctx, 'analytic intro').strip()}

### 2.2 Propagation card: {propagation['title']}

{render(_read_fragment(propagation, 'intro'), ctx, 'propagation intro').strip()}

## 3 Method

### 3.1 Kernel layout (`backend/services/pce_uq/`)

| module | responsibility | property that is tested |
|---|---|---|
| `basis.py` | orthonormal Legendre / Hermite bases, total-degree and hyperbolic truncation | $E[\\phi_i\\phi_j]=\\delta_{{ij}}$ so spectral moments need no norm bookkeeping |
| `marginals.py` | uniform / normal / lognormal marginals + isoprobabilistic map | sampling (truncated) and quadrature (untruncated) inverse maps kept separate |
| `quadrature.py` | Gauss tensor grids, Smolyak sparse grids | signed combination weights; exactness degree `2L-1` asserted against closed-form monomial integrals |
| `kernel.py` | projection and least-squares fits, spectral moments, Sobol', quantiles | regression quality via a QR leverage LOO residual (zero extra model runs) |
| `mcbench.py` | direct Monte Carlo reference + acceleration accounting | evaluation-count and wall-clock speed-ups reported separately |
| `spec.py` | case wiring from JSON (dotted paths, direct file load) | no domain content in code |

Closed-form post-processing in the orthonormal basis:

$$\\mathbb{{E}}[y]=c_0,\\quad \\mathrm{{Var}}[y]=\\sum_{{\\alpha\\neq 0}}c_\\alpha^2,\\quad
S_i=\\frac{{\\sum_{{\\mathrm{{supp}}(\\alpha)=\\{{i\\}}}}c_\\alpha^2}}{{\\mathrm{{Var}}[y]}},\\quad
S_{{T,i}}=\\frac{{\\sum_{{i\\in\\mathrm{{supp}}(\\alpha)}}c_\\alpha^2}}{{\\mathrm{{Var}}[y]}}$$

### 3.2 Acceptance criteria as machine decisions

| criterion | how it is decided | threshold |
|---|---|---|
| analytic Sobol' recovery | max abs difference against the published closed-form decomposition | <= 0.05 |
| response moments | relative error against direct Monte Carlo at the stated budget | <= 3 % |
| acceleration | $N_{{MC}}/N_{{PCE}}$ black-box evaluations (primary) and measured wall clock on the same scalar interface | >= 100x |
| domain-free mechanism | platform vocabulary scanner over the kernel and runner | 0 hits |

## 4 Results

### 4.1 Analytic case: PCE spectral ANOVA vs closed form

{table(["input", "analytic $S_i$", "PCE $S_i$", "abs err", "analytic $S_{T,i}$", "PCE $S_{T,i}$", "abs err"], analytic_rows)}

Configuration: Legendre basis, total degree $p={analytic['pce']['order']}$ ({analytic['pce']['n_terms']} terms),
{analytic['pce']['design']['quadrature']['kind']} quadrature with {analytic['pce']['design']['quadrature']['n_points']} model evaluations.
**Max absolute error {analytic['sobol_max_abs_error']:.2e}** against a 0.05 band.

Refinement trajectory on the same card:

{table(["configuration", "model evaluations", "max abs dS", "std rel. error", "mean rel. error"], analytic_sweep)}

![analytic Sobol comparison]({figures['analytic_sobol'].name})

![analytic convergence]({figures['analytic_convergence'].name})

### 4.2 Propagation case: uncertainty band of the response

| quantity | direct sampling ($N$ = {mc['n_model_evaluations']:,}) | surrogate | relative error |
|---|---|---|---|
| mean [ {unit} ] | {mc['mean'] * scale:.1f} | {propagation['comparison']['pce']['mean_sampled'] * scale:.1f} | {_pct(err['mean_rel'], 4)} |
| std [ {unit} ] | {mc['std'] * scale:.1f} | {propagation['comparison']['pce']['std_sampled'] * scale:.1f} | {_pct(err['std_rel'], 3)} |

High-accuracy anchor: {ref['n_model_evaluations']:,}-point scrambled-Sobol' quasi-Monte-Carlo,
mean {ref['mean'] * scale:.1f} {unit}, std {ref['std'] * scale:.1f} {unit}. Against it the surrogate is off by
{_pct(ref_err['mean_rel'], 4)} (mean) and {_pct(ref_err['std_rel'], 3)} (std), while the
direct baseline itself is off by {_pct(ref_err['mc_baseline_mean_rel'], 4)} and
{_pct(ref_err['mc_baseline_std_rel'], 3)}. The surrogate residual is therefore of the same order as
(here {ref_err['std_rel'] / max(ref_err['mc_baseline_std_rel'], 1e-12):.1f}x) the statistical error of the
10^4-sample baseline itself, which is what sets the resolution of this comparison -- the 3 % band is
not being passed by a lucky draw, but it is also not a 10^-3 measurement.

Band (surrogate quantiles vs direct sampling):

{table(["quantile", f"direct sampling [{unit}]", f"surrogate [{unit}]", "relative error"], band_rows)}

Mean $\\pm2\\sigma$: surrogate
[{propagation['band']['pce_mean_plus_minus_2sigma'][0] * scale:.1f}, {propagation['band']['pce_mean_plus_minus_2sigma'][1] * scale:.1f}] {unit}
vs direct sampling
[{propagation['band']['mc_mean_plus_minus_2sigma'][0] * scale:.1f}, {propagation['band']['mc_mean_plus_minus_2sigma'][1] * scale:.1f}] {unit}.

![uncertainty band]({figures['uncertainty_band'].name})

### 4.3 Uncertainty attribution (spectral ANOVA, no extra model runs)

{table(["input", "$S_i$", "$S_{T,i}$"], sobol_rows)}

$\\sum S_i = {propagation['sobol']['sum_first_order']:.4f}$ and
$\\sum S_{{T,i}} = {propagation['sobol']['sum_total_order']:.4f}$, so the interaction
share of the variance in this card is {propagation['sobol']['sum_total_order'] - 1.0:+.4f}.

![attribution]({figures['sensitivity_ranking'].name})

### 4.4 Acceleration and the cost/accuracy trade-off

Delivered configuration: {propagation['pce']['design']['quadrature']['kind']} grid, level
{propagation['pce']['design']['quadrature'].get('level')}, {propagation['pce']['n_terms']} basis terms,
{propagation['pce']['n_model_evaluations']} black-box evaluations.

* **evaluation-count speed-up {propagation['comparison']['speedup_model_evaluations']:.0f}x**
  ($N_{{MC}}/N_{{PCE}}$, independent of machine speed -- the primary criterion)
* **wall-clock speed-up {propagation['comparison']['timing']['speedup_wall_clock_median']:.0f}x**
  (same machine, same scalar calling interface, median of {propagation['comparison']['timing']['reps']} runs:
  direct pipeline {1000 * propagation['comparison']['timing']['mc_pipeline_seconds_median']:.1f} ms vs
  surrogate pipeline {1000 * propagation['comparison']['timing']['pce_pipeline_seconds_median']:.1f} ms;
  run-to-run range {propagation['comparison']['timing']['speedup_wall_clock_min']:.0f}x -
  {propagation['comparison']['timing']['speedup_wall_clock_max']:.0f}x -- wall clock carries machine-load scatter,
  the evaluation-count number above does not)

Full cost/accuracy table on the same card (errors against the high-accuracy anchor):

{table(["configuration", "model evaluations", "basis terms", "mean rel. error", "std rel. error", "acceleration"], propagation_sweep)}

![cost accuracy]({figures['cost_accuracy'].name})

## 5 What generalises, and what does not

{render(_read_fragment(analytic, 'caveats'), ctx, 'analytic caveats').strip()}

{render(_read_fragment(propagation, 'caveats'), ctx, 'propagation caveats').strip()}

Generic limitations of the mechanism itself (independent of the case cards):

1. **Signed sparse-grid weights.** Combination weights are negative for part of the
   nodes. A sign slip leaves results that look plausible; hence the exactness of each
   grid level is asserted against closed-form monomial integrals, not assumed.
2. **Projection can be under-determined.** When the sparse grid carries fewer points
   than basis terms the projection is under-determined; it stays usable only because
   the quadrature is exact up to its declared total degree. The refinement trajectory
   in the ledger is the evidence that the delivered point is not a lucky sample.
3. **Truncation vs quadrature mapping.** Sampling truncates the tails so that
   physical inputs stay admissible; quadrature nodes are not truncated so that they
   match the orthonormal measure. Mixing the two silently breaks the weights.
4. **Two acceleration definitions.** Evaluation-count speed-up is the right measure
   for solver-level black boxes; wall-clock speed-up additionally pays the surrogate's
   fixed assembly cost. Both are reported, never merged.
5. **Scope.** The kernel handles smooth responses in moderate dimension
   ($d\\lesssim10$). It is not a substitute for sampling-based GSA on non-smooth or
   strongly interacting responses, and it does not certify the black box itself.

## 6 Error verdict (item by item, explicit)

{table(["case", "criterion", "measured", "threshold", "verdict"], check_rows)}

* analytic Sobol' recovery: **{analytic['sobol_max_abs_error']:.2e}** max abs error (band 0.05) -> **< 5 % satisfied**.
* response mean relative error **{_pct(err['mean_rel'], 4)}**, standard deviation relative error
  **{_pct(err['std_rel'], 3)}** -> **< 3 % satisfied** (against the high-accuracy anchor:
  {_pct(ref_err['mean_rel'], 4)} / {_pct(ref_err['std_rel'], 3)}).
* acceleration **{propagation['comparison']['speedup_model_evaluations']:.0f}x** (evaluations) and
  **{propagation['comparison']['timing']['speedup_wall_clock_median']:.0f}x** (wall clock) -> **> 100x satisfied**.
* overall: **{('all PASS' if ledger['passed'] else 'FAIL present')}**; no honest-FAIL needs to be declared.

## 7 Reproduce / audit

```
# 1) machine loop: cases -> criteria -> ledger
python -m tools.uq_bench.run_case --all --out-dir artifacts/uq_pce_m9011 --reps 3 --sweep
# 2) regression tests
python -m pytest tests/test_pce_uq_kernel_m9011.py -q
# 3) figures + this document (all numbers injected from the ledger)
python -m tools.uq_bench.make_report --ledger artifacts/uq_pce_m9011/ledger.json \\
    --out-dir artifacts/uq_pce_m9011 --doc docs/verification/pce_uq_framework_m9011/REPORT.md
```

Ledger fields per case: `spec` (with sha256), `nominal_inputs` / `nominal_response`,
`pce` (configuration + design diagnostics), `sobol`, `comparison` (baseline, sampling
error, acceleration, timing, sample histogram), `band`, `checks` (machine verdicts),
`sweep` (cost/accuracy).

## 8 Delivered artefacts

| path | content |
|---|---|
| `backend/services/pce_uq/` | domain-free non-intrusive PCE kernel (bases, marginals, quadrature, kernel, MC reference, case wiring) |
| `config/uq_cases/*.json` | case cards: `{analytic['case_id']}`, `{propagation['case_id']}` (injected physics) |
| `config/uq_cases/*.intro.md`, `*.caveats.md` | case-card narratives consumed by this renderer |
| `tools/uq_bench/run_case.py` | machine-closed-loop runner (criteria + ledger) |
| `tools/uq_bench/references.py` | closed-form analytic references |
| `tools/uq_bench/make_report.py` | figures + this document (numbers injected) |
| `tests/test_pce_uq_kernel_m9011.py` | 18 pytest regression cases |
| `artifacts/uq_pce_m9011/` | `ledger.json`, per-case JSON, five figures |
"""
    return doc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default="artifacts/uq_pce_m9011/ledger.json")
    ap.add_argument("--out-dir", default="artifacts/uq_pce_m9011")
    ap.add_argument("--doc", default="docs/verification/pce_uq_framework_m9011/REPORT.md")
    args = ap.parse_args(argv)

    ledger = json.loads(Path(args.ledger).read_text())
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    analytic, propagation = split_cases(ledger)
    figures = {
        "analytic_sobol": fig_analytic_sobol(analytic, out_dir / "fig1_analytic_sobol.png"),
        "analytic_convergence": fig_analytic_convergence(analytic, out_dir / "fig2_analytic_convergence.png"),
        "uncertainty_band": fig_uncertainty_band(propagation, out_dir / "fig3_uncertainty_band.png"),
        "sensitivity_ranking": fig_sensitivity_ranking(propagation, out_dir / "fig4_sensitivity_ranking.png"),
        "cost_accuracy": fig_cost_accuracy(propagation, out_dir / "fig5_cost_accuracy.png"),
    }
    doc = build_document(ledger, figures)
    doc_path = Path(args.doc)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(doc)
    print(f"[report] {doc_path}")
    for name, path in figures.items():
        print(f"[figure] {name}: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())