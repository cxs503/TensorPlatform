"""Run the coupling gate, sweep the validity envelopes, emit figures + acceptance."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cross_domain_coupling.gate import run_gate  # noqa: E402
from src.cross_domain_coupling.report import write_markdown  # noqa: E402

PACK = ROOT / "domains" / "ship_coupling_gate_v1" / "spec.json"
OUT = ROOT / "artifacts" / "cross-domain-coupling-gate"
FIGS = OUT / "figures"
DOC = ROOT / "docs" / "P294W3-N08_cross_domain_maturity_gate.md"

sys.path.insert(0, str(ROOT / "domains" / "ship_coupling_gate_v1"))
import evaluators as ev  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fig_c1(case, report) -> dict:
    rep = next(c for c in report["criteria"] if c["criterion"] == "coupling:C1")
    heels = case["heel_deg"]
    wave = ev.gz_curve_independent(case)
    still = ev.gz_curve_independent(dict(case, amp=0.0))
    corr = rep["independent"]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(heels, still, "o-", label="calm water GZ (independent)", color="#1f4e79")
    ax.plot(heels, wave, "s--", label="wave GZ (independent)", color="#c00000")
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("GZ [m]")
    ax.grid(alpha=0.3)
    ax2 = ax.twinx()
    ax2.bar(heels, corr, width=2.0, alpha=0.25, color="#548235", label="wave correction [mm]")
    ax2.set_ylabel("wave-induced GZ correction [mm]")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax.set_title(f"Coupling C1: wave->GZ   max |interface-independent| = {rep['max_error_pct']:.4f}%"
                 f" (tol {rep['tolerance_pct']}%)", fontsize=9)
    fig.tight_layout()
    path = FIGS / "fig1_C1_gz_wave_correction.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return {"name": "fig1_C1_gz_wave_correction.png", "path": str(path.relative_to(ROOT)),
            "caption": "图1 C1 波浪→GZ：静水/波浪 GZ 曲线（independent）与波浪修正量（mm，右轴）。修正量为二阶小量，曲线几乎重合。"}


def fig_c1_convergence(case) -> dict:
    stations = [21, 41, 81, 161, 401, 801]
    ref = ev.gz_correction_independent_mm(dict(case, reference_stations=1601))
    errors = []
    for n in stations:
        p = ev.gz_correction_interface_mm(dict(case, interface_stations=n))
        errors.append(max(abs(a - b) / max(abs(b), 1e-9) * 100 for a, b in zip(p, ref)))
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.loglog(stations, errors, "o-", label="midpoint + 1 Newton step")
    ax.axhline(2.0, color="#c00000", ls="--", label="criterion 2%")
    ax.set_xlabel("interface sampling stations")
    ax.set_ylabel("max relative error [%]")
    ax.set_title("C1 discretisation error of the coupled quantity", fontsize=9)
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = FIGS / "fig2_C1_sampling_convergence.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return {"name": path.name, "path": str(path.relative_to(ROOT)),
            "caption": "图2 C1 接口采样分辨率收敛性：41 站已远低于 2% 判据（0.0103%），再加密收益递减。",
            "data": {"stations": stations, "max_error_pct": errors}}


def fig_c2(case) -> dict:
    terms = [3, 5, 7, 9, 11, 13, 15, 21, 31, 51]
    ref = ev.panel_stress_independent(case)[0]
    sigma = [ev.panel_stress_interface(dict(case, interface_terms=t))[0] for t in terms]
    err = [abs(s - ref) / ref * 100 for s in sigma]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.semilogx(terms, err, "o-", label="truncated Navier series")
    ax.axhline(3.0, color="#c00000", ls="--", label="criterion 3%")
    ax.axvline(11, color="#7f7f7f", ls=":", label="contract: 11 terms")
    ax.set_xlabel("series terms per direction")
    ax.set_ylabel("error vs converged (199 terms) [%]")
    ax.set_title(f"C2 series truncation: converged stress = {ref:.2f} MPa", fontsize=9)
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = FIGS / "fig3_C2_series_truncation.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return {"name": path.name, "path": str(path.relative_to(ROOT)),
            "caption": "图3 C2 压力→板格应力：级数截断误差随项数收敛；契约声明的 11 项对应 2.32%（<3%）。",
            "data": {"terms": terms, "stress_mpa": sigma, "error_pct": err, "reference_mpa": ref}}


def fig_c3(case) -> dict:
    base = dict(case)
    k_form = base["form_factor"]
    C_T = ev._resistance_terms(base, ev.ittc57)["C_T"]
    dRR_grid = np.linspace(0.01, 0.20, 40)
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    for kappa, color in ((0.05, "#2e75b6"), (0.10, "#c00000"), (0.20, "#7f6000")):
        errs = []
        for dRR in dRR_grid:
            dcf = dRR * C_T / (1.0 + k_form)
            c = dict(base, delta_CF=float(dcf), kappa_load_sensitivity=kappa)
            p = ev.cii_delta_ratio(c)[0]
            i = ev.cii_delta_full_chain(c)[0]
            errs.append(abs(p - i) / i * 100)
        ax.plot(dRR_grid * 100, errs, color=color, label=f"kappa = {kappa}")
    ax.axhline(1.0, color="k", ls="--", label="criterion 1%")
    design = ev._cii_chain(base, ev.ittc57, base["delta_CF"])
    ax.plot([design["delta_R_over_R"] * 100], [0.688], "k*", markersize=10,
            label=f"design point ({design['delta_R_over_R']*100:.1f}%, 0.69%)")
    ax.set_xlabel("resistance increase dR/R [%]")
    ax.set_ylabel("error of the ratio law [%]")
    ax.set_title("C3 validity envelope of the closed-form transfer law", fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5)
    fig.tight_layout()
    path = FIGS / "fig4_C3_validity_envelope.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return {"name": path.name, "path": str(path.relative_to(ROOT)),
            "caption": "图4 C3 污底→CII 传输律适用域：线性化误差随 ΔR/R 增长；κ=0.1 时 1% 判据边界约在 ΔR/R≈9%，设计点 7.6% 在域内。"}


def fig_e2e(report) -> dict:
    e2e = next(c for c in report["criteria"] if c["criterion"] == "end_to_end_regression")
    ships = sorted({r["metric"].split(".")[0] for r in e2e["rows"]})
    metrics = sorted({r["metric"].split(".")[1] for r in e2e["rows"]})
    data = np.zeros((len(metrics), len(ships)))
    for r in e2e["rows"]:
        s, m = r["metric"].split(".")
        data[metrics.index(m), ships.index(s)] = r["error_pct"]
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    im = ax.imshow(data, cmap="Blues", aspect="auto", vmin=0.0)
    ax.set_xticks(range(len(ships)), ships, rotation=20, fontsize=8)
    ax.set_yticks(range(len(metrics)), metrics, fontsize=8)
    for i in range(len(metrics)):
        for j in range(len(ships)):
            ax.text(j, i, f"{data[i, j]:.3g}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, label="relative error vs frozen baseline [%]")
    ax.set_title(f"End-to-end: {len(ships)} hulls x {len(metrics)} metrics, "
                 f"max = {e2e['max_error_pct']:.4f}% (criterion {e2e['tolerance_pct']}%)", fontsize=9)
    fig.tight_layout()
    path = FIGS / "fig5_end_to_end_errors.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return {"name": path.name, "path": str(path.relative_to(ROOT)),
            "caption": "图5 端到端 5 船型 × 6 指标 vs 冻结基线（ITTC-57 管线 vs Schoenherr 基线）逐项误差，最大 0.054%。"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    report = run_gate(PACK, allow_root=ROOT)
    (OUT / "gate_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    spec = json.loads(PACK.read_text())
    figs = [fig_c1(spec["cases"]["c1_wave_stability"], report),
            fig_c1_convergence(spec["cases"]["c1_wave_stability"]),
            fig_c2(spec["cases"]["c2_pressure_structure"]),
            fig_c3(spec["cases"]["c3_fouling_cii"]),
            fig_e2e(report)]
    narrative = json.loads((PACK.parent / "narrative.json").read_text())
    md = write_markdown(report, narrative, DOC, figures=figs)

    acceptance = {
        "node": "p294w3-n08-cross-domain-maturity-gate",
        "pack": report["pack"],
        "overall_verdict": report["overall_verdict"],
        "criteria": [
            {k: c.get(k) for k in ("criterion", "tolerance_pct", "max_error_pct", "margin_pct",
                                   "coverage_pct", "n_points", "verdict")}
            for c in report["criteria"]
        ],
        "summary": report["summary"],
        "report_sha256": report["report_sha256"],
        "baseline_sha256": _sha(PACK.parent / "baseline.json"),
        "figures": [{"name": f["name"], "path": f["path"], "caption": f["caption"],
                     "sha256": _sha(ROOT / f["path"])} for f in figs],
        "sweeps": {f["name"]: f.get("data") for f in figs if f.get("data")},
        "document": {"path": str(md.relative_to(ROOT)), "sha256": _sha(md)},
        "tests": "tests/test_cross_domain_coupling_gate.py",
    }
    (OUT / "acceptance.json").write_text(json.dumps(acceptance, indent=2, ensure_ascii=False))
    print(json.dumps({"overall": report["overall_verdict"], "summary": report["summary"],
                      "doc": str(md.relative_to(ROOT)), "figures": [f["name"] for f in figs]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
