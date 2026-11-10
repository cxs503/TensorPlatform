"""Acceptance tests: hull-form three-objective (R, GM, V_cargo) Pareto framework.

Structure
---------
* physics unit tests (geometry identity, hydrostatics limits, cargo integral,
  Holtrop component sanity) — fast, no optimisation;
* acceptance tests against the mission criteria (a)-(f), driven by *one*
  full-specification NSGA-II run (population 100 x 200 generations) shared
  through session-scoped fixtures.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from domains.hull_form_pareto.hull import (
    DesignSpace,
    G,
    RHO_SEAWATER,
    bm_estimate,
    build_hull,
    hold_volume_integral,
    kb_estimate,
    load_design_space,
    resistance,
    sectional_fullness_profile,
)
from domains.hull_form_pareto.objectives import (
    constraints_of,
    evaluate,
    objective_vector,
)
from domains.hull_form_pareto.pareto import (
    negative_control_single_objective,
    run_hull_pareto,
    save_artifacts,
    verify_zdt1,
    zdt1_reference_front,
)

POP = 100
GEN = 200
SEED = 42


# --------------------------------------------------------------------------
# design space / geometry
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def space() -> DesignSpace:
    return load_design_space()


def _default_design(space: DesignSpace) -> np.ndarray:
    """A mid-range reference design built from the JSON bounds."""
    return (space.lower + space.upper) / 2.0


def test_design_space_has_10_to_15_variables(space):
    assert 10 <= space.n_var <= 15
    assert space.n_var == len(space.names) == 13
    assert np.all(space.lower < space.upper)
    for name in ("l_over_b", "b_over_t", "cb", "cm", "lcb_pct", "speed_kn"):
        assert name in space.names
    # Holtrop regression domain must be respected by the space itself
    lb = (space.lower[space.names.index("l_over_b")],
          space.upper[space.names.index("l_over_b")])
    bt = (space.lower[space.names.index("b_over_t")],
          space.upper[space.names.index("b_over_t")])
    assert 3.9 <= lb[0] and lb[1] <= 9.5
    assert 2.1 <= bt[0] and bt[1] <= 4.0


def test_section_distribution_integrates_to_cp():
    for cp in (0.60, 0.65, 0.72, 0.80, 0.90):
        x, s = sectional_fullness_profile(cp)
        assert np.all(s >= 0.0) and np.all(s <= 1.0 + 1e-12)
        integral = float(np.trapezoid(s, x))
        assert integral == pytest.approx(cp, abs=1e-3)
        # parallel body fraction quoted by the model
        assert max(0.0, 2.0 * cp - 1.0) <= 1.0


def test_geometry_identity_cb_equals_cp_times_cm(space):
    rng = np.random.RandomState(7)
    for _ in range(25):
        x = rng.uniform(space.lower, space.upper)
        hull = build_hull(x, space)
        assert hull.cp == pytest.approx(hull.cb / hull.cm, rel=1e-12)
        # volume recomputed from the parametric section model == Cb L B T
        integral = float(np.trapezoid(hull.section_fullness, hull.stations))
        nabla_sections = hull.cm * hull.beam * hull.draft * hull.length * integral
        assert nabla_sections == pytest.approx(hull.nabla, rel=1e-3)
        assert hull.displacement_t == pytest.approx(
            hull.nabla * RHO_SEAWATER / 1000.0, rel=1e-12)


def test_kb_box_limit_and_monotonicity():
    # box hull: Cb = Cwp = 1  ->  KB = T/2 exactly
    assert kb_estimate(1.0, 1.0, 12.0) == pytest.approx(6.0, rel=1e-12)
    # fuller hulls have a lower centre of buoyancy
    kb_slender = kb_estimate(0.62, 0.80, 10.0)
    kb_full = kb_estimate(0.85, 0.90, 10.0)
    assert 0.5 * 10.0 < kb_slender < 0.65 * 10.0
    assert kb_full < kb_slender


def test_bm_matches_analytic_waterplane_inertia(space):
    hull = build_hull(_default_design(space), space)
    expected = (hull.cwp * hull.length * hull.beam ** 3 / 12.0) / hull.nabla
    assert bm_estimate(hull.cwp, hull.length, hull.beam, hull.nabla) == \
        pytest.approx(expected, rel=1e-12)
    assert hull.bm == pytest.approx(expected, rel=1e-12)


def test_gm_and_roll_period_physics(space):
    """Higher Cb at fixed L, B, T must reduce GM (BM ∝ Cwp/Cb)."""
    base = _default_design(space)
    i_cb = space.names.index("cb")
    low, high = base.copy(), base.copy()
    low[i_cb] = 0.62
    high[i_cb] = 0.84
    h_low, h_high = build_hull(low, space), build_hull(high, space)
    assert h_low.beam == pytest.approx(h_high.beam, rel=1e-12)
    assert h_high.gm < h_low.gm
    # GM rises -> stiffer ship -> shorter natural roll period
    assert h_low.gm > h_high.gm
    assert h_low.roll_period < h_high.roll_period
    assert h_low.roll_period == pytest.approx(
        2.0 * math.pi * 0.35 * h_low.beam / math.sqrt(G * h_low.gm), rel=1e-12)


def test_cargo_volume_is_a_longitudinal_integral(space):
    base = _default_design(space)
    hull = build_hull(base, space)
    diag = hold_volume_integral(hull)
    t_side = max(0.012 * hull.beam, 0.8)
    manual = (0.92 * hull.cm * (hull.beam - 2.0 * t_side)
              * (hull.depth - hull.double_bottom) * hull.length
              * diag["contained_section_area"])
    assert hull.cargo_volume == pytest.approx(manual, rel=1e-12)
    # longer hold window and more depth must both increase capacity
    i_hold = space.names.index("hold_len_frac")
    i_dt = space.names.index("d_over_t")
    longer = base.copy()
    longer[i_hold] = min(space.upper[i_hold], base[i_hold] + 0.05)
    deeper = base.copy()
    deeper[i_dt] = min(space.upper[i_dt], base[i_dt] + 0.05)
    assert build_hull(longer, space).cargo_volume > hull.cargo_volume
    assert build_hull(deeper, space).cargo_volume > hull.cargo_volume


def test_resistance_components_and_domain_guard(space):
    hull = build_hull(_default_design(space), space)
    res = resistance(hull)
    parts = res["rf_n"] + res["rw_n"] + res["rbulb_n"] + res["rtr_n"] + res["ra_n"]
    assert res["rt_n"] == pytest.approx(parts, rel=1e-9)
    assert res["rt_n"] > 0.0
    assert 1.0 < res["form_factor"] < 2.0
    assert 1e8 <= res["reynolds"] <= 3e9
    # friction must dominate at a moderate Froude number
    assert res["rf_n"] > res["rw_n"]
    assert res["warnings"] == [] or isinstance(res["warnings"], list)
    # outside the Cb regression domain the implementation must warn (evidence)
    import copy
    from domains.hull_form_pareto.hull import load_holtrop_module
    h2 = copy.copy(hull)
    h2.cb = 0.92
    flagged = load_holtrop_module().holtrop_resistance(
        h2.length, h2.beam, h2.draft, h2.cb, h2.speed_ms,
        cm=h2.cm, cwp=h2.cwp, lcb_frac=h2.lcb_frac)
    assert any("Cb" in w for w in flagged.warnings)


def test_constraints_flag_infeasible_designs(space):
    ok = evaluate(_default_design(space), space)
    assert ok.feasible and ok.violation == 0.0
    # design at the top of the Cb range violates the Holtrop regression domain
    x = _default_design(space).copy()
    x[space.names.index("cb")] = space.upper[space.names.index("cb")]
    x[space.names.index("cm")] = space.lower[space.names.index("cm")]
    weak = evaluate(x, space)
    assert weak.constraints["cb_max"] > 0.0
    assert weak.constraints["cp_max"] > 0.0
    assert weak.violation > 0.0 and not weak.feasible
    assert set(constraints_of(weak.hull)) == set(weak.constraints)


def test_objective_vector_sign_convention(space):
    ev = evaluate(_default_design(space), space)
    assert ev.objectives[0] == pytest.approx(ev.resistance["rt_kn"])
    assert ev.objectives[1] == pytest.approx(-ev.hull.gm)
    assert ev.objectives[2] == pytest.approx(-ev.hull.cargo_volume / 1000.0)
    assert np.allclose(objective_vector(ev.x, space), ev.objectives)


# --------------------------------------------------------------------------
# acceptance: one full-specification NSGA-II run
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def hull_run():
    return run_hull_pareto(pop_size=POP, n_gen=GEN, seed=SEED)


@pytest.fixture(scope="session")
def zdt1_run():
    return verify_zdt1(pop_size=POP, n_gen=GEN, seed=SEED)


@pytest.fixture(scope="session")
def negative_run():
    return negative_control_single_objective(pop_size=POP, n_gen=GEN, seed=SEED,
                                             n_random=6000)


def test_criterion_a_zdt1_hausdorff(hull_run, zdt1_run):
    """(a) NSGA-II front vs analytic ZDT1 front: relative Hausdorff < 5 %."""
    assert zdt1_run["n_gen"] == GEN and zdt1_run["pop_size"] == POP
    assert "n_var=10" in zdt1_run["problem"]
    assert zdt1_run["hausdorff_relative"] < 0.05, zdt1_run["hausdorff"]


def test_criterion_b_hypervolume_convergence(hull_run):
    """(b) hypervolume change over the closing generations < 1 %."""
    conv = hull_run["summary"]["hypervolume_convergence"]
    assert conv["basis"].startswith("external archive")
    assert conv["finite"] is True
    assert conv["delta"] < 0.01, conv


def test_criterion_c_spacing(hull_run):
    """(c) Pareto set is evenly spread in objective space: spacing < 0.1."""
    assert hull_run["summary"]["spacing_normalised"] < 0.10, \
        hull_run["summary"]["spacing_normalised"]
    assert hull_run["summary"]["n_front"] >= 20


def test_criterion_d_physical_r_gm_tradeoff(hull_run):
    """(d) 阻力↓ ⇒ 稳性↓ — verified two ways.

    (i) controlled experiment: sweep slenderness L/B at fixed L and B/T.  A
        narrower hull has less wetted surface (lower R_T) and less waterplane
        inertia (lower GM): R and GM must move together (Spearman = +1).
    (ii) at equal displacement on the Pareto front, buying GM costs specific
        resistance: Spearman(R/Δ^(2/3), GM) < -0.2.
    """
    s = hull_run["summary"]
    ctrl = s["controlled_tradeoff"]
    assert ctrl["n_feasible"] >= 10
    assert ctrl["spearman_R_vs_GM"] > 0.9
    assert ctrl["dR_per_m_beam_kn"] > 0.0 and ctrl["dGM_per_m_beam"] > 0.0
    assert s["spearman_specific_R_GM"] < -0.2


def test_criterion_d_front_is_physically_admissible(hull_run, space):
    front = hull_run["front"]
    assert len(front) >= 20
    for row in front:
        assert row["feasible"] is True
        hull = row["hull"]
        # IMO intact-stability floor and roll-period floor
        assert hull["gm"] >= 0.15
        assert row["diagnostics"]["roll_period_s"] >= 10.0 - 1e-9
        # parametric model identity survives the search
        assert hull["cp"] == pytest.approx(hull["cb"] / hull["cm"], rel=1e-9)
        assert 0.55 <= hull["cp"] <= 0.87
        assert hull["cb"] <= 0.85 + 1e-12
        # resistance ordering: R = R_F + R_W + R_B + R_TR + R_A
        res = row["resistance"]
        parts = res["rf_n"] + res["rw_n"] + res["rbulb_n"] + res["rtr_n"] + res["ra_n"]
        assert res["rt_n"] == pytest.approx(parts, rel=1e-9)
    # front members are mutually non-dominated
    assert hull_run["summary"]["n_dominated_in_front"] == 0


def test_criterion_e_negative_control(negative_run):
    """(e) single-objective optimisation degenerates to the global optimum.

    Strict check: on a 3-variable sub-space the optimum is obtained by exhaustive
    grid enumeration, and the single-objective GA must reach it (< 2 %).
    Large-scale check: on the full 13-D space the GA must be at least as good as
    a 6 000-point Latin-hypercube random search.
    """
    sub = negative_run["subproblem"]
    assert sub is not None and sub["pass"] is True, sub
    assert abs(sub["relative_gap"]) < 0.02, sub
    assert negative_run["ga_best_feasible"] is True
    assert negative_run["ga_best_R_kn"] <= negative_run["random_search_best_R_kn"] * 1.01


def test_criterion_f_reproducible_and_artifacted(hull_run, zdt1_run, negative_run,
                                                 tmp_path):
    """(f) deterministic results + machine-readable acceptance artefacts."""
    assert hull_run["summary"]["input_hash"]
    paths = save_artifacts(hull_run, zdt1_run, negative_run, out_dir=tmp_path)
    for key in ("pareto_result", "zdt1_verification", "negative_control",
                "acceptance", "manifest"):
        assert paths[key].exists()
    acceptance = json.loads(paths["acceptance"].read_text())
    assert acceptance["criterion_a_zdt1_hausdorff_lt_5pct"]["pass"] is True
    assert acceptance["criterion_c_spacing_lt_0_1"]["pass"] is True
    assert acceptance["criterion_e_negative_control_lt_1pct"]["pass"] is True
    assert acceptance["criterion_d_physical_r_gm_tradeoff"]["pass"] is True
    manifest = json.loads(paths["manifest"].read_text())
    assert len(manifest["sha256"]) >= 4
    # small reproducible sub-run: same seed -> identical objectives
    a = run_hull_pareto(pop_size=12, n_gen=6, seed=5)
    b = run_hull_pareto(pop_size=12, n_gen=6, seed=5)
    assert a["summary"]["input_hash"] == b["summary"]["input_hash"]
    assert [r["objectives"] for r in a["front"]] == [r["objectives"] for r in b["front"]]