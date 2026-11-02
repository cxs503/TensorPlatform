"""Verification debt: in-repo BEM open-water kernel (m1393) vs O&O (1975) B-series.

Context
-------
``backend/services/propeller_open_water.py`` (mission m1393) is a first-principles
blade-element / momentum kernel.  Its published verification
(``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md``) is *analytic-bound +
internal-consistency only* because "no literature point data was reachable
offline".  Mission m1585 subsequently landed the canonical Oosterveld & van
Oossanen (1975) B-series regression in
``tools/verification/propulsion/b_series_reference.py`` (REAL, 39+47 terms).

This module closes that debt: it replays the BEM kernel against the O&O
reference for the standard Wageningen B-series propellers and quantifies the
gap.  It is a *reference-side* audit (read-only on both kernels); it does not
modify either implementation.

Run standalone (generates ``outputs/bem_vs_oosterveld/results.json`` + figures)::

    python3 tests/test_bem_propeller_vs_oosterveld.py

Run as tests::

    python3 -m pytest tests/test_bem_propeller_vs_oosterveld.py --noconftest -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_BEM = ROOT / "backend" / "services" / "propeller_open_water.py"
_REF = ROOT / "tools" / "verification" / "propulsion" / "b_series_reference.py"
OUT = ROOT / "outputs" / "bem_vs_oosterveld"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass resolution needs the module registered
    spec.loader.exec_module(mod)
    return mod


bem = _load(_BEM, "bem_kernel_under_test")
ref = _load(_REF, "oosterveld_reference")

# Standard Wageningen B-series propellers (name -> P/D, Ae/Ao, Z).
B_SERIES = {
    "B4-70": (1.0, 0.70, 4),
    "B4-40": (1.0, 0.40, 4),
    "B4-55": (0.8, 0.55, 4),
    "B3-80": (1.0, 0.80, 3),
    "B5-79": (1.0, 0.79, 5),
}
HUB_RATIO = 0.20  # O&O 1975 B-series hub ratio
JS = [round(0.05 * i, 2) for i in range(1, 25)]  # 0.05 .. 1.20


def _bem_geom(p_d: float, ae_ao: float, z: int):
    return bem.PropellerGeometry(
        diameter_m=5.0,
        pitch_ratio=p_d,
        blade_area_ratio=ae_ao,
        blade_count=z,
        hub_ratio=HUB_RATIO,
        sections=40,
    )


def _rel(a: float, b: float) -> float:
    """Relative error of ``a`` vs reference ``b`` (percent); None if b ~ 0."""
    if abs(b) < 1e-12:
        return None
    return (a - b) / b * 100.0


def compare_propeller(tag: str, p_d: float, ae_ao: float, z: int):
    """Return per-J comparison rows + summary for one propeller."""
    geom = _bem_geom(p_d, ae_ao, z)
    rows = []
    for J in JS:
        bp = bem.open_water_point(geom, J, rho=1025.0)
        rp = ref.open_water(J, p_d, ae_ao, z)
        rows.append({
            "J": J,
            "KT_bem": bp["KT"], "KT_ref": rp.Kt,
            "KQ_bem": bp["KQ"], "KQ_ref": rp.Kq,
            "eta0_bem": bp["eta0"], "eta0_ref": rp.eta0,
            "eta_ideal_ref": rp.eta_ideal,
            "dKT_pct": _rel(bp["KT"], rp.Kt),
            "dKQ_pct": _rel(bp["KQ"], rp.Kq),
            "deta_pct": _rel(bp["eta0"], rp.eta0) if rp.eta0 else None,
        })
    # zero-thrust advance ratio
    j0_bem = bem.solve_zero_thrust_advance_ratio(geom, rho=1025.0)
    j0_ref = ref.zero_thrust_J(p_d, ae_ao, z)
    # best efficiency point
    be_bem = bem.best_efficiency_point(geom, rho=1025.0)
    # O&O best efficiency by scan
    best = max((ref.open_water(J, p_d, ae_ao, z) for J in JS),
               key=lambda p: (p.eta0 if p.eta0 else -1.0))
    summary = {
        "tag": tag, "P_D": p_d, "Ae_Ao": ae_ao, "Z": z,
        "J0_bem": j0_bem, "J0_ref": j0_ref,
        "dJ0_pct": _rel(j0_bem, j0_ref),
        "eta0_max_bem": be_bem["eta0"], "J_opt_bem": be_bem["J"],
        "eta0_max_ref": best.eta0, "J_opt_ref": best.J,
        "deta_max_pct": _rel(be_bem["eta0"], best.eta0) if best.eta0 else None,
    }
    return rows, summary


def _max_abs(rows, key):
    vals = [abs(r[key]) for r in rows if r.get(key) is not None]
    return max(vals) if vals else None


# ---------------------------------------------------------------------------
# pytest: structural + bounded-gap gates (fast subset)
# ---------------------------------------------------------------------------
def test_reference_is_real_oosterveld():
    assert len(ref.CT_TERMS) == 39 and len(ref.CQ_TERMS) == 47
    assert ref.provenance()["reference"].startswith("Oosterveld")


def test_bem_kernel_respects_momentum_bound():
    """BEM eta0 must stay below the Rankine-Froude disc bound (its own invariant)."""
    geom = _bem_geom(1.0, 0.70, 4)
    for J in (0.2, 0.4, 0.6, 0.8):
        p = bem.open_water_point(geom, J, rho=1025.0)
        assert p["eta0"] <= p["eta_ideal"] + 1e-9


def test_bem_kt_monotone_decreasing():
    geom = _bem_geom(1.0, 0.70, 4)
    kts = [bem.open_water_point(geom, 0.05 * i, rho=1025.0)["KT"] for i in range(1, 13)]
    assert all(kts[i] > kts[i + 1] for i in range(len(kts) - 1))


def test_bem_vs_oosterveld_b470_bounded_gap():
    """The BEM kernel is a first-principles model, not the B-series regression;
    we gate that the gap is *bounded and physical* (same sign of slope, same
    zero-thrust region), not that it reproduces the regression point-for-point."""
    rows, summary = compare_propeller("B4-70", 1.0, 0.70, 4)
    # zero-thrust advance ratio within 25% (both models agree on the operating window)
    assert summary["dJ0_pct"] is not None
    assert abs(summary["dJ0_pct"]) < 25.0, f"J0 gap {summary['dJ0_pct']}%"
    # KT at mid-advance within a factor of 2 (order-of-magnitude agreement)
    mid = [r for r in rows if abs(r["J"] - 0.6) < 1e-9][0]
    assert 0.5 < mid["KT_bem"] / mid["KT_ref"] < 2.0


if __name__ == "__main__":  # standalone artifact generator
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.mkdir(parents=True, exist_ok=True)
    all_rows, all_sum = {}, {}
    for tag, (p_d, ae, z) in B_SERIES.items():
        rows, summary = compare_propeller(tag, p_d, ae, z)
        all_rows[tag] = rows
        all_sum[tag] = summary
        summary["max_abs_dKT_pct"] = _max_abs(rows, "dKT_pct")
        summary["max_abs_dKQ_pct"] = _max_abs(rows, "dKQ_pct")
        summary["max_abs_deta_pct"] = _max_abs(rows, "deta_pct")

    payload = {
        "provenance": {
            "bem_kernel": str(_BEM.relative_to(ROOT)),
            "oosterveld_ref": str(_REF.relative_to(ROOT)),
            "hub_ratio": HUB_RATIO,
            "J_grid": [JS[0], JS[-1], len(JS)],
            "note": ("BEM kernel is first-principles (sin^0.55 chord, Cl=2pi sin a cap 1.10, "
                     "Cd=0.008+a^2, Prandtl tip/hub loss); O&O is the 39+47-term regression. "
                     "Gap is expected; this audit quantifies it."),
        },
        "propellers": all_sum,
        "rows": all_rows,
    }
    (OUT / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    # Figure 1: KT(K) curves, BEM vs O&O, all propellers
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    for tag, (p_d, ae, z) in B_SERIES.items():
        rs = all_rows[tag]
        axes[0].plot([r["J"] for r in rs], [r["KT_ref"] for r in rs], "-", label=f"{tag} O&O")
        axes[0].plot([r["J"] for r in rs], [r["KT_bem"] for r in rs], "--", label=f"{tag} BEM")
        axes[1].plot([r["J"] for r in rs], [r["KQ_ref"] for r in rs], "-", label=f"{tag} O&O")
        axes[1].plot([r["J"] for r in rs], [r["KQ_bem"] for r in rs], "--", label=f"{tag} BEM")
    axes[0].set_xlabel("J"); axes[0].set_ylabel("K_T"); axes[0].set_title("Thrust coefficient")
    axes[1].set_xlabel("J"); axes[1].set_ylabel("K_Q"); axes[1].set_title("Torque coefficient")
    for ax in axes:
        ax.grid(alpha=0.3); ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_kt_kq_bem_vs_oosterveld.png", dpi=140)
    plt.close(fig)

    # Figure 2: eta0 curves + relative error for B4-70
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    rs = all_rows["B4-70"]
    ej_ref = [r["J"] for r in rs if r["eta0_ref"]]
    axes[0].plot(ej_ref, [r["eta0_ref"] for r in rs if r["eta0_ref"]], "-", label="O&O eta0")
    axes[0].plot([r["J"] for r in rs], [r["eta0_bem"] for r in rs], "--", label="BEM eta0")
    axes[0].set_xlabel("J"); axes[0].set_ylabel(r"$\eta_0$"); axes[0].set_title("B4-70 open-water efficiency")
    axes[0].grid(alpha=0.3); axes[0].legend()
    ej = [r["J"] for r in rs if r["deta_pct"] is not None]
    ev = [r["deta_pct"] for r in rs if r["deta_pct"] is not None]
    axes[1].plot(ej, ev, "o-", ms=3)
    axes[1].axhline(0, color="k", lw=0.6); axes[1].axhline(15, color="r", ls=":", lw=0.8)
    axes[1].axhline(-15, color="r", ls=":", lw=0.8)
    axes[1].set_xlabel("J"); axes[1].set_ylabel(r"$\Delta\eta_0$ [%]"); axes[1].set_title("B4-70 eta0 error (BEM vs O&O)")
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig2_eta0_b470.png", dpi=140)
    plt.close(fig)

    print(json.dumps(all_sum, indent=2, ensure_ascii=False))
    print(f"artifacts -> {OUT}")
