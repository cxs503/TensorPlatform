"""Acceptance tests for conservative exchange and clock semantics (synthetic only).

Every fixture here is synthetic (blobs, vectors, ramps) and every mesh is a
box mesh: the mechanism is domain-neutral by construction, so a domain word in
this file is a defect (C8 scans it).

The *negative controls* are the important half of the suite: each one shows that
the corresponding gate can actually fail, so a green run means "the property was
checked", not "nothing was checked".
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.coupling.clock import ClockContract, ClockContractViolation, ClockMode, TimeSeriesExchange
from tools.coupling.exchange_gate import (
    LOOP_TOL,
    MOMENT_TOL,
    PATCH_TOL,
    TOTAL_TOL,
    TORQUE_TOL,
    clock_mode_table,
    coupled_loop,
    criterion_c1,
    criterion_c3,
    criterion_c7,
)
from tools.coupling.mesh import BoxMesh, Field, QuantityKind, face_areas
from tools.coupling.precision import (
    QUANTITY_LAWS,
    DegenerateMeasureError,
    VectorField,
    area_overlap_matrix,
    law_for,
    moment_projection,
)
from tools.coupling.remap import (
    RemapDomainError,
    RemapError,
    RemapOperator,
    RemapSpec,
    remap,
    remap_vector,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- fixtures
def bulk(n=(6, 6), m=(9, 9)):
    lo, hi = [0.0, 0.0], [2.0, 2.0]
    return (BoxMesh.uniform(lo, hi, list(n), name="f-src"),
            BoxMesh.uniform(lo, hi, list(m), name="f-dst"))


def blob(mesh, centre=(0.9, 1.1), width=0.45, amplitude=3.0):
    d2 = ((mesh.centers - np.asarray(centre, dtype=float)) ** 2).sum(axis=1)
    return amplitude * np.exp(-d2 / (2.0 * width**2))


def ramp(mesh, gradient=(0.7, -1.3), offset=2.5):
    return offset + mesh.centers @ np.asarray(gradient, dtype=float)


def surface_pair(n_x=6, slabs=3):
    lo, hi = [0.0, 0.0], [2.0, 2.0]
    return (BoxMesh.uniform(lo, hi, [n_x, slabs], name="s-src"),
            BoxMesh.uniform(lo, hi, [n_x + 3, slabs], name="s-dst"))


# ------------------------------------------------------------------ law contract
def test_four_quantity_laws_are_declared_with_distinct_semantics():
    """The law table must cover the four classes and differ where it matters."""
    assert set(QUANTITY_LAWS) == {"density", "flux", "extensive", "scalar"}
    assert law_for("density").positive and law_for("density").first_moment
    assert law_for("flux").measure == "surface" and law_for("flux").signed
    assert law_for("scalar").conserved is False
    assert law_for(QuantityKind.FLUX).measure == "surface"


def test_measure_matches_declared_law():
    mesh, _ = bulk((4, 3), (5, 5))
    assert np.allclose(Field(np.ones(mesh.n_cells), mesh, QuantityKind.DENSITY).measure,
                       mesh.volume)
    assert np.allclose(Field(np.ones(mesh.n_cells), mesh, QuantityKind.FLUX).measure,
                       face_areas(mesh))
    assert np.allclose(Field(np.ones(mesh.n_cells), mesh, QuantityKind.EXTENSIVE).measure, 1.0)


# --------------------------------------------------------------- amount exchange
def test_density_total_and_first_moment_conserved_on_shifted_mesh():
    src, dst = bulk()
    field = Field(blob(src) + 0.2, src, QuantityKind.DENSITY, name="d")
    out = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.OVERLAP_P1, moment_exact=True))
    assert out.report.rel_residual <= TOTAL_TOL
    mom = np.max(np.abs(out.field.first_moment() - field.first_moment())) / np.max(
        np.abs(field.first_moment()))
    assert mom <= MOMENT_TOL


def test_extensive_total_conserved_with_moment_projection():
    src, dst = bulk(n=(5, 5), m=(8, 8))
    field = Field(blob(src) * src.volume, src, QuantityKind.EXTENSIVE, name="e")
    out = remap(field, src, dst, "extensive",
                spec=RemapSpec(operator=RemapOperator.RBF_CONSERVATIVE, moment_exact=True))
    assert out.report.rel_residual <= TOTAL_TOL
    assert out.report.moment_first_residual <= MOMENT_TOL


def test_flux_is_area_conservative_on_a_surface_pair():
    src, dst = surface_pair()
    field = Field(blob(src) - 1.0, src, QuantityKind.FLUX, name="fl")
    out = remap(field, src, dst, "flux", spec=RemapSpec(quantity_kind=QuantityKind.FLUX))
    assert out.report.covered_fraction == pytest.approx(1.0)
    assert out.report.rel_residual <= TOTAL_TOL


def test_scalar_law_is_precision_not_a_sum():
    src, dst = bulk()
    field = Field(ramp(src), src, QuantityKind.SCALAR, name="s")
    out = remap(field, src, dst, "scalar",
                spec=RemapSpec(operator=RemapOperator.OVERLAP_P1,
                               quantity_kind=QuantityKind.SCALAR))
    assert out.report.sum_conserved is False
    assert np.max(np.abs(out.field.values - ramp(dst))) <= TOTAL_TOL * np.max(np.abs(ramp(dst)))


def test_vector_total_first_moment_and_torque_conserved():
    src, dst = bulk()
    rng = np.random.default_rng(4)
    vec = VectorField(rng.normal(size=(src.n_cells, 2)), src, QuantityKind.DENSITY, name="v")
    out = remap_vector(vec, src, dst, "density",
                       spec=RemapSpec(quantity_kind=QuantityKind.DENSITY, moment_exact=True))
    assert out.total_residual <= TOTAL_TOL
    assert out.first_moment_residual <= MOMENT_TOL
    assert out.torque_residual <= TORQUE_TOL


# ------------------------------------------------------------------- patch test
def test_patch_test_translation_is_exact():
    out = criterion_c3()
    translations = [r for r in out["rows"] if r["motion"] == "translation"]
    assert max(r["errors"]["overlap_p1"] for r in translations) <= PATCH_TOL


def test_patch_test_rotation_is_exact():
    out = criterion_c3()
    rotations = [r for r in out["rows"] if r["motion"] == "rotation"]
    assert max(r["errors"]["overlap_p1"] for r in rotations) <= PATCH_TOL


# ------------------------------------------------------------- bounds / positivity
def test_positivity_and_no_overshoot_under_bounded_projection():
    src, dst = bulk(n=(6, 6), m=(11, 11))
    density = blob(src, width=0.35, amplitude=5.0)
    field = Field(density, src, QuantityKind.DENSITY, name="pos")
    out = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.OVERLAP_P1, positivity=True,
                               enforce_bounds=True))
    assert out.field.values.min() >= -1e-14
    assert out.field.values.max() <= density.max() * (1.0 + 1e-12)
    assert out.report.rel_residual <= TOTAL_TOL


# ------------------------------------------------------------------ coupled loop
def test_coupled_loop_conserves_amount_momentum_and_energy():
    out = coupled_loop(RemapOperator.OVERLAP_P1, cycles=8, moment_exact=True)
    assert out["residual"]["amount"] <= LOOP_TOL
    assert out["residual"]["momentum"] <= LOOP_TOL
    assert out["residual"]["energy"] <= LOOP_TOL


def test_coupled_loop_passes_for_all_three_mappings():
    for op in (RemapOperator.OVERLAP_L2, RemapOperator.OVERLAP_P1,
               RemapOperator.RBF_CONSERVATIVE):
        out = coupled_loop(op, cycles=6, moment_exact=True)
        assert out["worst_residual"] <= LOOP_TOL, op


# ------------------------------------------------------------------------ clock
def test_clock_non_integer_ratio_is_refused():
    with pytest.raises(ClockContractViolation):
        ClockContract(dt_master=1.0, dt_sub=0.3)


def test_clock_request_outside_sampled_window_is_refused():
    contract = ClockContract(dt_master=1.0, dt_sub=0.25, mode=ClockMode.LINEAR_INTERPOLATION)
    exchange = TimeSeriesExchange(contract)
    for t in (0.0, 0.25, 0.5):
        exchange.store(t, np.array([t]))
    with pytest.raises(ClockContractViolation):
        exchange.request(9.0)


def test_clock_extrapolation_beyond_declared_horizon_is_refused():
    contract = ClockContract(dt_master=1.0, dt_sub=0.25,
                             mode=ClockMode.ONE_STEP_EXTRAPOLATION, max_extrapolation_steps=1)
    exchange = TimeSeriesExchange(contract)
    for t in (0.0, 0.25, 0.5):
        exchange.store(t, np.array([t]))
    with pytest.raises(ClockContractViolation):
        exchange.request(0.5 + 3 * 0.25)


def test_clock_sync_mode_refuses_an_off_grid_request():
    contract = ClockContract(dt_master=1.0, dt_sub=0.25, mode=ClockMode.SYNC)
    exchange = TimeSeriesExchange(contract)
    for t in (0.0, 0.25, 0.5):
        exchange.store(t, np.array([t]))
    with pytest.raises(ClockContractViolation):
        exchange.request(0.375)


def test_clock_modes_declare_precondition_order_and_out_of_contract_rule():
    table = {row["mode"]: row for row in clock_mode_table()}
    assert set(table) == {m.value for m in ClockMode}
    for row in table.values():
        assert row["precondition"] and row["off_grid_rule"] and row["out_of_range_rule"]
        assert isinstance(row["declared_order"], int)


def test_clock_linear_interpolation_is_second_order():
    errors = []
    for n in (16, 32, 64):
        dt = 1.0 / n
        contract = ClockContract(dt_master=1.0, dt_sub=dt, mode=ClockMode.LINEAR_INTERPOLATION)
        exchange = TimeSeriesExchange(contract)
        f = lambda t: np.cos(1.7 * t) + 0.3 * t
        for t in np.arange(n + 1) * dt:
            exchange.store(float(t), np.array([f(float(t))]))
        probe = 0.5 - 0.5 * dt
        errors.append(abs(float(exchange.request(probe).values[0]) - f(probe)))
    slopes = np.diff(np.log(errors)) / np.diff(np.log([1 / 16, 1 / 32, 1 / 64]))
    assert np.mean(slopes) == pytest.approx(2.0, abs=0.25)


# ------------------------------------------------------- projection unit behaviour
def test_moment_projection_is_closed_form_and_round_off_exact():
    rng = np.random.default_rng(3)
    centres = rng.random((40, 3))
    measure = rng.random(40)
    values = rng.normal(size=40)
    target_total, target_moment = 2.5, rng.normal(size=3)
    projected, report = moment_projection(values, measure, centres, target_total, target_moment)
    assert report.total_residual <= MOMENT_TOL
    assert report.first_moment_residual <= MOMENT_TOL
    assert abs(float((projected * measure).sum()) - target_total) <= TOTAL_TOL * target_total
    assert report.condition_number < 1e3


def test_moment_projection_refuses_a_destination_that_cannot_span_the_axes():
    lo = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    mesh = BoxMesh(lo=lo, hi=lo + np.array([1.0, 0.05]))
    with pytest.raises(DegenerateMeasureError):
        moment_projection(np.ones(3), mesh.volume, mesh.centers, 3.0, np.array([1.0, 0.0]))


def test_flux_needs_two_dimensions():
    mesh = BoxMesh.uniform([0.0], [1.0], [4])
    with pytest.raises(DegenerateMeasureError):
        area_overlap_matrix(mesh, mesh)


def test_flux_between_different_surfaces_is_refused():
    src = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [6, 3])
    dst = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [6, 7])
    field = Field(np.ones(src.n_cells), src, QuantityKind.FLUX, name="fl")
    with pytest.raises(RemapDomainError):
        remap(field, src, dst, "flux")


def test_second_order_operator_refuses_a_surface_quantity():
    src, _ = surface_pair()
    field = Field(np.ones(src.n_cells), src, QuantityKind.FLUX, name="fl")
    with pytest.raises(RemapError):
        remap(field, src, src.refined(2), "flux",
              spec=RemapSpec(operator=RemapOperator.OVERLAP_P1, quantity_kind=QuantityKind.FLUX))


def test_moment_constraint_on_a_precision_law_is_refused():
    src, dst = bulk()
    field = Field(ramp(src), src, QuantityKind.SCALAR, name="s")
    with pytest.raises(RemapError):
        remap(field, src, dst, "scalar",
              spec=RemapSpec(quantity_kind=QuantityKind.SCALAR, moment_exact=True))


# ------------------------------------------------------------- reproducibility
def test_remap_is_idempotent_and_writes_a_mapping_manifest():
    out = criterion_c7()
    assert out["same_field_digest"] and out["same_remap_digest"]
    manifest_path = ROOT / out["manifest_path"]
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema"] == "coupling.mapping_manifest/1"
    record = manifest["records"][0]
    for key in ("spec_digest", "src_digest", "dst_digest", "remap_digest", "field_sha256"):
        assert isinstance(record[key], str) and len(record[key]) == 64
    assert manifest["manifest_digest"] == out["manifest_digest"]


# ----------------------------------------------------------- NEGATIVE CONTROLS
def test_negative_control_first_order_operator_fails_the_patch_test():
    """The patch-test gate must reject the first-order operator (>= 1 % error)."""
    out = criterion_c3()
    assert out["first_order_baseline_error"] >= 1e-2
    assert out["first_order_baseline_detected"] is True


def test_negative_control_nearest_without_correction_drifts_beyond_one_percent():
    src, dst = bulk(n=(4, 4), m=(13, 13))
    field = Field(blob(src, width=0.25, amplitude=5.0) + 0.01, src, QuantityKind.DENSITY)
    off = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED,
                               apply_conservation_correction=False))
    on = remap(field, src, dst, "density",
               spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED))
    assert off.report.raw_rel_residual >= 1e-2
    assert on.report.rel_residual <= TOTAL_TOL


def test_negative_control_drift_is_detected_in_every_trial():
    src, dst = bulk(n=(4, 4), m=(13, 13))
    rng = np.random.default_rng(0)
    drifts = []
    for _ in range(8):
        shift = rng.uniform(0.2, 0.8, size=2)
        field = Field(blob(src, centre=shift, width=0.25, amplitude=5.0) + 0.01,
                      src, QuantityKind.DENSITY)
        out = remap(field, src, dst, "density",
                    spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED,
                                   apply_conservation_correction=False))
        drifts.append(float(out.report.raw_rel_residual))
    assert min(drifts) >= 1e-2


def test_negative_control_mass_only_correction_leaves_torque_unconserved():
    """Without the first-moment projection the torque gate must trip."""
    src, dst = bulk()
    rng = np.random.default_rng(5)
    vec = VectorField(rng.normal(size=(src.n_cells, 2)), src, QuantityKind.DENSITY)
    on = remap_vector(vec, src, dst, "density",
                      spec=RemapSpec(quantity_kind=QuantityKind.DENSITY, moment_exact=True))
    off = remap_vector(vec, src, dst, "density",
                       spec=RemapSpec(quantity_kind=QuantityKind.DENSITY, moment_exact=False))
    assert on.torque_residual <= TORQUE_TOL
    assert off.torque_residual >= 1e-3          # detected, far above the gate


def test_negative_control_vocabulary_scanner_catches_an_injected_word(tmp_path):
    """The C8 scanner must actually report a hit when one is planted."""
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    lexicon = load_lexicon()
    word = str(np.atleast_1d(lexicon["ascii_terms"])[0])
    planted = tmp_path / "planted.py"
    planted.write_text(f'"""module that mentions {word} once"""\n')
    hits = scan_paths([str(planted)], lexicon=lexicon, root=tmp_path)["hits"]
    assert hits and hits[0]["term"] == word


def test_negative_control_loop_gate_trips_without_correction():
    """The end-to-end gate is not vacuous: without correction it must fail."""
    out = criterion_c1()
    assert out["passed"] is True                       # the real configuration is green
    lossy = coupled_loop(RemapOperator.NEAREST_SCALED, cycles=4, correct=False)
    assert lossy["worst_residual"] >= LOOP_TOL          # the gate would catch this