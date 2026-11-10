"""Cross-library exchange matrix (C3, cross-library part) and field import validation.

The matrix is *not* a rubber stamp: pairs whose quantity laws differ (a per-volume
``density`` against an ``extensive`` per-cell mass) must be refused with a reason,
and the tests assert exactly that, so the compatibility gate is exercised in both
directions.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.solver_adapter import (
    SOLVER_IDS,
    AdapterError,
    CompatibilityError,
    FieldLookupError,
    MappingMode,
    build_adapter,
)

N = 4
TOL = 1e-3


def _ready(solver_id: str, n_cells: int = N):
    adapter = build_adapter(solver_id, n_cells=n_cells)
    adapter.init()
    return adapter


# --------------------------------------------------------------- 8x8 matrix
@pytest.mark.parametrize("source_id", SOLVER_IDS)
@pytest.mark.parametrize("target_id", SOLVER_IDS)
def test_density_exchange_matrix(source_id, target_id):
    """Every ordered pair: either conserves below 0.1 % or is refused with a reason."""
    source = _ready(source_id)
    target = _ready(target_id)
    report = source.check_compatibility(target, "density")
    if source_id == target_id:
        with pytest.raises(CompatibilityError):
            source.map_to(target, "density")     # a boundary needs two libraries
        return
    if not report.compatible:
        assert report.reasons
        with pytest.raises(CompatibilityError):
            source.map_to(target, "density")
        return
    result = source.map_to(target, "density", mode=MappingMode.CONSERVATIVE)
    assert result.metrics.sum_rel_residual < TOL
    assert result.passed is True
    landed = target.export_field("density")
    np.testing.assert_allclose(landed.values, result.field.values, rtol=0, atol=0)


@pytest.mark.parametrize("source_id", SOLVER_IDS)
@pytest.mark.parametrize("target_id", SOLVER_IDS)
def test_compatibility_report_is_symmetric_for_shared_fields(source_id, target_id):
    source, target = _ready(source_id), _ready(target_id)
    forward = source.check_compatibility(target, "density")
    backward = target.check_compatibility(source, "density")
    assert forward.compatible == backward.compatible
    assert forward.shared_modes == backward.shared_modes


def test_density_kind_mismatch_between_volume_and_extensive_is_refused():
    """LBM's per-volume density vs DEM's per-cell mass: same name, different law."""
    report = _ready("lbm").check_compatibility(_ready("dem"), "density")
    assert report.compatible is False
    assert any("kind mismatch" in r for r in report.reasons)


def test_lbm_to_dem_velocity_exchange_conserves():
    source, target = _ready("lbm"), _ready("dem")
    result = source.map_to(target, "velocity_x")
    assert result.metrics.sum_rel_residual < TOL
    assert result.record.request.target_solver == "dem"


def test_fem_to_lbm_offset_exchange_conserves():
    """Mesh-motion slot: the per-cell coordinate offset field crossing FEM -> LBM."""
    source, target = _ready("fem"), _ready("lbm")
    result = source.map_to(target, "offset_x", mode=MappingMode.CONSERVATIVE)
    assert result.metrics.sum_rel_residual < TOL
    assert result.metrics.sum_src != 0.0
    assert np.all(np.isfinite(target.export_field("offset_x").values))


def test_mapping_into_a_refined_target_mesh_conserves():
    coarse = _ready("lbm", n_cells=4)
    fine = build_adapter("dem", n_cells=8)
    fine.init()
    result = coarse.map_to(fine, "velocity_x")
    assert result.metrics.sum_rel_residual < TOL
    assert fine.export_field("velocity_x").values.size == fine.mesh.n_cells


def test_exchange_is_deterministic_across_independent_runs():
    first_src, first_dst = _ready("lbm"), _ready("fvm")
    second_src, second_dst = _ready("lbm"), _ready("fvm")
    one = first_src.map_to(first_dst, "velocity_x")
    two = second_src.map_to(second_dst, "velocity_x")
    assert one.record.src_digest == two.record.src_digest
    assert one.record.dst_digest == two.record.dst_digest
    assert one.record.digest() == two.record.digest()


# ------------------------------------------------------- import validation
def test_import_rejects_wrong_shape():
    target = _ready("lbm")
    foreign = build_adapter("lbm", n_cells=8)
    foreign.init()
    with pytest.raises(AdapterError):
        target.import_field("density", foreign.export_field("density"))


def test_import_rejects_kind_mismatch():
    target = _ready("lbm")
    payload = target.export_field("velocity_x")
    from tools.coupling.mesh import Field

    wrong_kind = Field(values=payload.values, mesh=target.mesh, kind="scalar", name="velocity_x")
    with pytest.raises(AdapterError):
        target.import_field("density", wrong_kind)


def test_import_rejects_non_field():
    with pytest.raises(FieldLookupError):
        _ready("lbm").import_field("density", [1.0, 2.0])  # type: ignore[arg-type]


def test_import_rejects_undeclared_field_name():
    source, target = _ready("lbm"), _ready("cad")
    with pytest.raises(FieldLookupError):
        target.import_field("velocity_x", source.export_field("velocity_x"))


def test_export_rejects_non_finite_payload():
    """A solver that produced NaN/inf must fail loudly, not ship the values."""
    source = _ready("lbm")
    source._fields["velocity_x"] = source._fields["velocity_x"].copy()
    source._fields["velocity_x"][0] = np.inf
    with pytest.raises(AdapterError):
        source.export_field("velocity_x")


def test_import_round_trip_preserves_values():
    source, target = _ready("sph"), _ready("dg")
    payload = source.export_field("density")
    landed = target.import_field("density", payload)
    np.testing.assert_array_equal(landed.values, payload.values)


# --------------------------------------------------- conservation reporting
def test_conservation_report_aggregates_every_exchange():
    source = _ready("lbm")
    for target_id, field in (("dem", "velocity_x"), ("fvm", "velocity_x"), ("sph", "density")):
        source.map_to(_ready(target_id), field)
    report = source.conservation_report()
    assert report["exchanges"] == 3
    assert report["within_tolerance"] is True
    assert 0.0 <= report["worst_sum_rel_residual"] < TOL
    assert len(report["records"]) == 3


def test_conservation_report_is_empty_before_any_exchange():
    report = _ready("lbm").conservation_report()
    assert report["exchanges"] == 0
    assert report["worst_sum_rel_residual"] == 0.0
    assert report["within_tolerance"] is True


def test_target_adapter_records_the_exchange_too():
    source, target = _ready("lbm"), _ready("dem")
    source.map_to(target, "velocity_x")
    assert target.conservation_report()["exchanges"] == 1


@pytest.mark.parametrize("field", ["velocity_y", "offset_y", "velocity_z"])
def test_multi_component_slots_exchange(field):
    source, target = _ready("lbm"), _ready("dem")
    if field not in target.manifest().field_names():
        pytest.skip(f"dem does not declare {field}")
    result = source.map_to(target, field)
    assert result.metrics.sum_rel_residual < TOL