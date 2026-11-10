"""Cross-validation pipeline for BEM (Neumann-Kelvin) and LBM (two-phase).

Implements the full cross-validation workflow:
1. Run BEM and LBM solvers on identical Wigley hull /工况
2. Compare wave resistance coefficients
3. Perform sensitivity analysis on ≥3 parameters
4. Generate comparison figures
5. Update capability ledger

Acceptance criteria:
  C1: BEM vs LBM wave resistance relative error < 10%
  C2: Sensitivity analysis covers ≥3 parameters
  C3: Capability ledger auto-update success rate 100%
  C4: Cross-solver validation report with ≥5 comparison figures
  C5: pytest all green (≥10 test cases)
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .wigley_bem_nk import NKResult, solve_wigley_nk
from .wigley_lbm_twophase import LBMResult, solve_wigley_lbm, grid_convergence_study


@dataclass
class CrossValidationEntry:
    """Single cross-validation comparison at one工况."""
    Fn: float
    L: float
    B: float
    T: float
    bem_result: NKResult
    lbm_result: LBMResult
    relative_error_pct: float  # |Cw_bem - Cw_lbm| / Cw_bem * 100
    pass_c1: bool  # relative_error < 10%


@dataclass
class SensitivityResult:
    """Sensitivity analysis result for one parameter."""
    parameter: str
    values: List[float]
    bem_cw: List[float]
    lbm_cw: List[float]
    relative_errors: List[float]
    sensitivity_index: float  # d(Cw)/d(param) normalised


@dataclass
class CrossValidationReport:
    """Full cross-validation report."""
    case_id: str
    entries: List[CrossValidationEntry]
    sensitivity_results: List[SensitivityResult]
    figures: List[Dict[str, str]]  # [{name, caption, path}]
    summary: Dict[str, Any]
    digest: str


def run_cross_validation(
    L: float = 1.0,
    B: float = 0.1,
    T: float = 0.0625,
    fn_range: List[float] = None,
    lbm_resolution: Tuple[int, int, int] = (256, 128, 64),
) -> List[CrossValidationEntry]:
    """Run BEM vs LBM cross-validation across Froude number range.

    Args:
        L, B, T: Wigley hull geometry
        fn_range: List of Froude numbers to test
        lbm_resolution: LBM grid resolution (Nx, Ny, Nz)

    Returns:
        List of CrossValidationEntry for each Fn.
    """
    if fn_range is None:
        fn_range = [0.20, 0.25, 0.30, 0.35, 0.40]

    entries = []
    for Fn in fn_range:
        bem = solve_wigley_nk(L=L, B=B, T=T, Fn=Fn)
        lbm = solve_wigley_lbm(
            L=L, B=B, T=T, Fn=Fn,
            Nx=lbm_resolution[0], Ny=lbm_resolution[1], Nz=lbm_resolution[2],
        )

        if bem.Cw > 0:
            rel_err = abs(bem.Cw - lbm.Cw) / bem.Cw * 100.0
        else:
            rel_err = 0.0

        entry = CrossValidationEntry(
            Fn=Fn,
            L=L, B=B, T=T,
            bem_result=bem,
            lbm_result=lbm,
            relative_error_pct=rel_err,
            pass_c1=rel_err < 10.0,
        )
        entries.append(entry)

    return entries


def run_sensitivity_analysis(
    L: float = 1.0,
    B: float = 0.1,
    T: float = 0.0625,
    Fn: float = 0.25,
) -> List[SensitivityResult]:
    """Run sensitivity analysis on 3+ parameters.

    Parameters studied:
    1. Froude number (Fn): 0.20–0.40
    2. Beam-to-Length ratio (B/L): 0.08–0.12
    3. Draft-to-Length ratio (T/L): 0.05–0.075
    4. LBM grid resolution (Nx): 64–512

    Returns:
        List of SensitivityResult for each parameter.
    """
    results = []

    # 1. Froude number sensitivity
    fn_vals = [0.20, 0.25, 0.30, 0.35, 0.40]
    bem_cw_fn = []
    lbm_cw_fn = []
    rel_err_fn = []
    for fn in fn_vals:
        bem = solve_wigley_nk(L=L, B=B, T=T, Fn=fn)
        lbm = solve_wigley_lbm(L=L, B=B, T=T, Fn=fn)
        bem_cw_fn.append(bem.Cw)
        lbm_cw_fn.append(lbm.Cw)
        if bem.Cw > 0:
            rel_err_fn.append(abs(bem.Cw - lbm.Cw) / bem.Cw * 100.0)
        else:
            rel_err_fn.append(0.0)

    # Sensitivity index: normalised derivative
    dCw_dFn = (bem_cw_fn[-1] - bem_cw_fn[0]) / (fn_vals[-1] - fn_vals[0])
    sens_fn = dCw_dFn / max(np.mean(bem_cw_fn), 1e-10)

    results.append(SensitivityResult(
        parameter="Fn",
        values=fn_vals,
        bem_cw=bem_cw_fn,
        lbm_cw=lbm_cw_fn,
        relative_errors=rel_err_fn,
        sensitivity_index=float(sens_fn),
    ))

    # 2. Beam-to-Length ratio sensitivity
    bl_vals = [0.08, 0.09, 0.10, 0.11, 0.12]
    bem_cw_bl = []
    lbm_cw_bl = []
    rel_err_bl = []
    for bl in bl_vals:
        B_test = bl * L
        bem = solve_wigley_nk(L=L, B=B_test, T=T, Fn=Fn)
        lbm = solve_wigley_lbm(L=L, B=B_test, T=T, Fn=Fn)
        bem_cw_bl.append(bem.Cw)
        lbm_cw_bl.append(lbm.Cw)
        if bem.Cw > 0:
            rel_err_bl.append(abs(bem.Cw - lbm.Cw) / bem.Cw * 100.0)
        else:
            rel_err_bl.append(0.0)

    dCw_dBL = (bem_cw_bl[-1] - bem_cw_bl[0]) / (bl_vals[-1] - bl_vals[0])
    sens_bl = dCw_dBL / max(np.mean(bem_cw_bl), 1e-10)

    results.append(SensitivityResult(
        parameter="B/L",
        values=bl_vals,
        bem_cw=bem_cw_bl,
        lbm_cw=lbm_cw_bl,
        relative_errors=rel_err_bl,
        sensitivity_index=float(sens_bl),
    ))

    # 3. Draft-to-Length ratio sensitivity
    tl_vals = [0.050, 0.05625, 0.0625, 0.06875, 0.075]
    bem_cw_tl = []
    lbm_cw_tl = []
    rel_err_tl = []
    for tl in tl_vals:
        T_test = tl * L
        bem = solve_wigley_nk(L=L, B=B, T=T_test, Fn=Fn)
        lbm = solve_wigley_lbm(L=L, B=B, T=T_test, Fn=Fn)
        bem_cw_tl.append(bem.Cw)
        lbm_cw_tl.append(lbm.Cw)
        if bem.Cw > 0:
            rel_err_tl.append(abs(bem.Cw - lbm.Cw) / bem.Cw * 100.0)
        else:
            rel_err_tl.append(0.0)

    dCw_dTL = (bem_cw_tl[-1] - bem_cw_tl[0]) / (tl_vals[-1] - tl_vals[0])
    sens_tl = dCw_dTL / max(np.mean(bem_cw_tl), 1e-10)

    results.append(SensitivityResult(
        parameter="T/L",
        values=tl_vals,
        bem_cw=bem_cw_tl,
        lbm_cw=lbm_cw_tl,
        relative_errors=rel_err_tl,
        sensitivity_index=float(sens_tl),
    ))

    # 4. LBM grid resolution sensitivity
    nx_vals = [64, 128, 256, 512]
    bem_cw_nx = [solve_wigley_nk(L=L, B=B, T=T, Fn=Fn).Cw] * len(nx_vals)
    lbm_cw_nx = []
    rel_err_nx = []
    for nx in nx_vals:
        lbm = solve_wigley_lbm(L=L, B=B, T=T, Fn=Fn, Nx=nx, Ny=nx // 2, Nz=nx // 4)
        lbm_cw_nx.append(lbm.Cw)
        if bem_cw_nx[0] > 0:
            rel_err_nx.append(abs(bem_cw_nx[0] - lbm.Cw) / bem_cw_nx[0] * 100.0)
        else:
            rel_err_nx.append(0.0)

    # Sensitivity: how much does Cw change with resolution?
    dCw_dNx = (lbm_cw_nx[-1] - lbm_cw_nx[0]) / (nx_vals[-1] - nx_vals[0])
    sens_nx = abs(dCw_dNx) / max(np.mean(lbm_cw_nx), 1e-10)

    results.append(SensitivityResult(
        parameter="Nx (LBM resolution)",
        values=[float(v) for v in nx_vals],
        bem_cw=bem_cw_nx,
        lbm_cw=lbm_cw_nx,
        relative_errors=rel_err_nx,
        sensitivity_index=float(sens_nx),
    ))

    return results


def generate_comparison_figures(
    entries: List[CrossValidationEntry],
    sensitivity_results: List[SensitivityResult],
    output_dir: Path,
) -> List[Dict[str, str]]:
    """Generate ≥5 comparison figures for the cross-validation report.

    Figures:
    1. Cw vs Fn: BEM vs LBM comparison
    2. Relative error vs Fn
    3. Wave elevation comparison (centreline)
    4. Sensitivity: Cw vs B/L
    5. Sensitivity: Cw vs T/L
    6. Grid convergence: Cw vs Nx
    7. Wave spectrum comparison
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    figures = []

    # Figure 1: Cw vs Fn (BEM vs LBM)
    fig, ax = plt.subplots(figsize=(8, 5))
    fn_vals = [e.Fn for e in entries]
    bem_cw = [e.bem_result.Cw for e in entries]
    lbm_cw = [e.lbm_result.Cw for e in entries]
    ax.plot(fn_vals, bem_cw, 'bo-', label='BEM (Neumann-Kelvin)', linewidth=2, markersize=8)
    ax.plot(fn_vals, lbm_cw, 'rs--', label='LBM (Two-Phase)', linewidth=2, markersize=8)
    ax.set_xlabel('Froude Number (Fn)', fontsize=12)
    ax.set_ylabel('Wave Resistance Coefficient (Cw)', fontsize=12)
    ax.set_title('Wigley Hull: BEM vs LBM Wave Resistance', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    path1 = output_dir / "fig01_cw_vs_fn.png"
    fig.savefig(path1, dpi=150, bbox_inches='tight')
    plt.close(fig)
    figures.append({
        "name": "Cw_vs_Fn",
        "caption": "Wave resistance coefficient vs Froude number: BEM (Neumann-Kelvin) vs LBM (Two-Phase) for Wigley hull",
        "path": str(path1),
    })

    # Figure 2: Relative error vs Fn
    fig, ax = plt.subplots(figsize=(8, 5))
    rel_errors = [e.relative_error_pct for e in entries]
    ax.bar([str(e.Fn) for e in entries], rel_errors, color='steelblue', alpha=0.7)
    ax.axhline(y=10.0, color='red', linestyle='--', linewidth=2, label='10% threshold')
    ax.set_xlabel('Froude Number (Fn)', fontsize=12)
    ax.set_ylabel('Relative Error (%)', fontsize=12)
    ax.set_title('BEM vs LBM: Relative Error in Cw', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3, axis='y')
    path2 = output_dir / "fig02_relative_error.png"
    fig.savefig(path2, dpi=150, bbox_inches='tight')
    plt.close(fig)
    figures.append({
        "name": "Relative_Error",
        "caption": "Relative error between BEM and LBM wave resistance coefficients across Froude numbers",
        "path": str(path2),
    })

    # Figure 3: Wave elevation comparison (centreline)
    if entries:
        entry = entries[2] if len(entries) > 2 else entries[0]  # Fn=0.30 or first
        fig, ax = plt.subplots(figsize=(10, 4))
        bem_elev = entry.bem_result.wave_elevation
        lbm_elev = entry.lbm_result.wave_elevation
        bem_x = [p[0] for p in bem_elev]
        bem_eta = [p[1] for p in bem_elev]
        lbm_x = [p[0] for p in lbm_elev]
        lbm_eta = [p[1] for p in lbm_elev]
        ax.plot(bem_x, bem_eta, 'b-', label='BEM', linewidth=1.5, alpha=0.8)
        ax.plot(lbm_x, lbm_eta, 'r--', label='LBM', linewidth=1.5, alpha=0.8)
        ax.axvline(x=0.5, color='gray', linestyle=':', alpha=0.5, label='Hull stern')
        ax.axvline(x=-0.5, color='gray', linestyle=':', alpha=0.5, label='Hull bow')
        ax.set_xlabel('x/L', fontsize=12)
        ax.set_ylabel('η/T', fontsize=12)
        ax.set_title(f'Centreline Wave Elevation (Fn={entry.Fn})', fontsize=14)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        path3 = output_dir / "fig03_wave_elevation.png"
        fig.savefig(path3, dpi=150, bbox_inches='tight')
        plt.close(fig)
        figures.append({
            "name": "Wave_Elevation",
            "caption": f"Centreline wave elevation comparison at Fn={entry.Fn}: BEM vs LBM",
            "path": str(path3),
        })

    # Figure 4: Sensitivity - Cw vs B/L
    if len(sensitivity_results) > 1:
        sens_bl = sensitivity_results[1]
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(sens_bl.values, sens_bl.bem_cw, 'bo-', label='BEM', linewidth=2, markersize=8)
        ax.plot(sens_bl.values, sens_bl.lbm_cw, 'rs--', label='LBM', linewidth=2, markersize=8)
        ax.set_xlabel('B/L (Beam-to-Length Ratio)', fontsize=12)
        ax.set_ylabel('Cw', fontsize=12)
        ax.set_title('Sensitivity: Wave Resistance vs B/L', fontsize=14)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        path4 = output_dir / "fig04_sensitivity_BL.png"
        fig.savefig(path4, dpi=150, bbox_inches='tight')
        plt.close(fig)
        figures.append({
            "name": "Sensitivity_BL",
            "caption": "Sensitivity of wave resistance coefficient to beam-to-length ratio",
            "path": str(path4),
        })

    # Figure 5: Sensitivity - Cw vs T/L
    if len(sensitivity_results) > 2:
        sens_tl = sensitivity_results[2]
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(sens_tl.values, sens_tl.bem_cw, 'bo-', label='BEM', linewidth=2, markersize=8)
        ax.plot(sens_tl.values, sens_tl.lbm_cw, 'rs--', label='LBM', linewidth=2, markersize=8)
        ax.set_xlabel('T/L (Draft-to-Length Ratio)', fontsize=12)
        ax.set_ylabel('Cw', fontsize=12)
        ax.set_title('Sensitivity: Wave Resistance vs T/L', fontsize=14)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        path5 = output_dir / "fig05_sensitivity_TL.png"
        fig.savefig(path5, dpi=150, bbox_inches='tight')
        plt.close(fig)
        figures.append({
            "name": "Sensitivity_TL",
            "caption": "Sensitivity of wave resistance coefficient to draft-to-length ratio",
            "path": str(path5),
        })

    # Figure 6: Grid convergence
    if len(sensitivity_results) > 3:
        sens_nx = sensitivity_results[3]
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(sens_nx.values, sens_nx.lbm_cw, 'rs--', label='LBM Cw', linewidth=2, markersize=8)
        ax.axhline(y=sens_nx.bem_cw[0], color='blue', linestyle='-', linewidth=2, label='BEM (reference)')
        ax.set_xlabel('Nx (LBM Grid Resolution)', fontsize=12)
        ax.set_ylabel('Cw', fontsize=12)
        ax.set_title('LBM Grid Convergence Study', fontsize=14)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
        path6 = output_dir / "fig06_grid_convergence.png"
        fig.savefig(path6, dpi=150, bbox_inches='tight')
        plt.close(fig)
        figures.append({
            "name": "Grid_Convergence",
            "caption": "LBM grid convergence: wave resistance coefficient vs streamwise resolution",
            "path": str(path6),
        })

    # Figure 7: Ct vs Fn (total resistance)
    fig, ax = plt.subplots(figsize=(8, 5))
    bem_ct = [e.bem_result.Ct for e in entries]
    lbm_ct = [e.lbm_result.Ct for e in entries]
    ax.plot(fn_vals, bem_ct, 'bo-', label='BEM Ct', linewidth=2, markersize=8)
    ax.plot(fn_vals, lbm_ct, 'rs--', label='LBM Ct', linewidth=2, markersize=8)
    ax.set_xlabel('Froude Number (Fn)', fontsize=12)
    ax.set_ylabel('Total Resistance Coefficient (Ct)', fontsize=12)
    ax.set_title('Wigley Hull: Total Resistance Coefficient', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    path7 = output_dir / "fig07_ct_vs_fn.png"
    fig.savefig(path7, dpi=150, bbox_inches='tight')
    plt.close(fig)
    figures.append({
        "name": "Ct_vs_Fn",
        "caption": "Total resistance coefficient (Ct = Cw + Cf) vs Froude number",
        "path": str(path7),
    })

    return figures


def update_capability_ledger(
    report: CrossValidationReport,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Auto-update capability ledger with cross-validation results.

    Reads the existing ledger, adds/updates the cross-solver consistency card,
    and writes back.

    Returns:
        Update status dict.
    """
    if ledger_path is None:
        ledger_path = Path(__file__).resolve().parents[3] / "artifacts" / "capability_ledger" / "capability_ledger.json"

    # Load existing ledger
    if ledger_path.exists():
        with open(ledger_path, 'r') as f:
            ledger = json.load(f)
    else:
        ledger = {"schema_version": "2.0", "capabilities": [], "evidence_source": {}}

    # Find or create cross-solver consistency card
    cap_id = "cross_solver.bem_lbm_wigley"
    existing = None
    for cap in ledger.get("capabilities", []):
        if cap.get("id") == cap_id:
            existing = cap
            break

    # Compute summary stats
    all_pass = all(e.pass_c1 for e in report.entries)
    max_error = max(e.relative_error_pct for e in report.entries) if report.entries else 0
    mean_error = np.mean([e.relative_error_pct for e in report.entries]) if report.entries else 0

    new_card = {
        "id": cap_id,
        "name": "BEM-LBM Cross-Validation (Wigley Hull)",
        "domain": "resistance",
        "claimed_level": "REAL",
        "description": "Cross-validation of Neumann-Kelvin BEM and two-phase LBM for Wigley hull wave resistance",
        "validation_evidence": {
            "verification_status": "PASS" if all_pass else "HONEST_FAIL",
            "max_relative_error_pct": round(max_error, 2),
            "mean_relative_error_pct": round(mean_error, 2),
            "fn_range_tested": [e.Fn for e in report.entries],
            "sensitivity_parameters": [s.parameter for s in report.sensitivity_results],
            "n_figures": len(report.figures),
            "report_digest": report.digest,
        },
        "evidence": [
            {
                "asserted_level": "REAL",
                "as_of": "2026-11-10",
                "source": "p97w3-n08-bem-lbm-cross-validation",
                "metric": f"max_rel_error={max_error:.2f}%, mean_rel_error={mean_error:.2f}%",
            }
        ],
        "note": f"Cross-validation: BEM (Michell thin-ship) vs LBM (two-phase). Max error: {max_error:.2f}%",
        "action": "mature" if all_pass else "investigate",
    }

    if existing:
        # Update existing card
        idx = ledger["capabilities"].index(existing)
        ledger["capabilities"][idx] = new_card
    else:
        # Add new card
        ledger["capabilities"].append(new_card)

    # Update evidence source
    import datetime
    ledger["evidence_source"] = {
        "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "p97w3-n08-cross-validation",
    }

    # Write back
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, 'w') as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)

    return {
        "status": "success",
        "card_id": cap_id,
        "verification_status": "PASS" if all_pass else "HONEST_FAIL",
        "ledger_path": str(ledger_path),
    }


def build_full_report(
    L: float = 1.0,
    B: float = 0.1,
    T: float = 0.0625,
    fn_range: List[float] = None,
    output_dir: Optional[Path] = None,
) -> CrossValidationReport:
    """Build complete cross-validation report.

    Runs cross-validation, sensitivity analysis, generates figures,
    and updates capability ledger.
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parents[3] / "artifacts" / "cross_validation"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Cross-validation
    entries = run_cross_validation(L=L, B=B, T=T, fn_range=fn_range)

    # 2. Sensitivity analysis
    sensitivity_results = run_sensitivity_analysis(L=L, B=B, T=T)

    # 3. Generate figures
    figures = generate_comparison_figures(entries, sensitivity_results, output_dir)

    # 4. Build report
    import hashlib
    report_data = {
        "entries": len(entries),
        "sensitivity_params": len(sensitivity_results),
        "figures": len(figures),
        "all_pass_c1": all(e.pass_c1 for e in entries),
    }
    digest = hashlib.sha256(json.dumps(report_data, sort_keys=True).encode()).hexdigest()[:16]

    summary = {
        "n_cases": len(entries),
        "all_pass_c1": all(e.pass_c1 for e in entries),
        "max_relative_error_pct": max(e.relative_error_pct for e in entries) if entries else 0,
        "mean_relative_error_pct": float(np.mean([e.relative_error_pct for e in entries])) if entries else 0,
        "n_sensitivity_params": len(sensitivity_results),
        "n_figures": len(figures),
        "geometry": {"L": L, "B": B, "T": T, "Cb": 4.0 / 9.0},
    }

    report = CrossValidationReport(
        case_id="p97w3-n08-wigley-cross-validation",
        entries=entries,
        sensitivity_results=sensitivity_results,
        figures=figures,
        summary=summary,
        digest=digest,
    )

    # 5. Update capability ledger
    ledger_update = update_capability_ledger(report)

    return report
