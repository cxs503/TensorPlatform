"""C4: conservative mapping conserves; standard mapping buys accuracy; the gate can fail.

The three claims under test, in the order they matter:

1. **conservative mode** (volume- or surface-weighted averaging) keeps the
   conserved total to round-off on every admissible mesh pair, well inside the
   protocol tolerance of ``1e-3`` (0.1 %);
2. **standard mode** (second-order reconstruction) is strictly more accurate on
   a smooth/linear field, at no cost to the total on nested meshes — the
   precision/conservation trade-off is measured, not asserted;
3. **the gate can fail**: a deliberately lossy exchange (``nearest_scaled``
   without the conservation correction, non-matching cell volumes) must exceed
   the tolerance and be reported ``passed=False``.  Without this negative
   control the 0.1 % threshold would be decoration.

Admissibility rule proved here: a *flux* quantity may only be exchanged between
meshes that share the discretisation along the flux normal; refining along the
normal changes the surface measure itself, so the conserved total is no longer
comparable and the mapping fails closed (``RemapDomainError`` → ``MappingError``).
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.coupling.mesh import BoxMesh
from tools.solver_adapter import (
    SOLVER_IDS,
    DimensionalError,
    MappingMode,
    MappingSpec,
    ExchangeRequest,
    build_adapter,
    map_field,
)

N = 4
TOL = 1e-3


def _ready(solver_id: str, n_cells: int = N):
    adapter = build_adapter(solver_id, n_cells=n_cells)
    adapter.init()
    return adapter


def _request(source: str, target: str, field: str, mode: MappingMode,
             operator: str | None = None, sum_tol: float = TOL) -> ExchangeRequest:
    default = "overlap_l2" if mode is MappingMode.CONSERVATIVE else "overlap_p1"
    return ExchangeRequest(
        solver_id=source,
        version="1.0.0",
        target_solver=target,
        field_name=field,
        mapping=MappingSpec(mode=mode, operator=operator or default, sum_tol=sum_tol),
    )


# ------------------------------------------------------------------ C4 core
@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_conservative_density_mapping_into_refined_mesh(solver_id):
    """Coarse -> nested fine (volume measure identical): total to round-off."""
    source = _ready(solver_id)
    fine = source.mesh.refined(2)
    result = map_field(
        source.export_field("density"),
        source._spec("density"),
        source._spec("density"),
        fine,
        _request(solver_id, "dem" if solver_id != "dem" else "lbm", "density",
                 MappingMode.CONSERVATIVE),
    )
    assert result.metrics.sum_rel_residual < TOL
    assert result.passed is True


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_conservative_density_mapping_onto_non_nested_mesh(solver_id):
    """Non-nested destination (5^3 against 4^3): still conservative to round-off."""
    source = _ready(solver_id)
    target_mesh = BoxMesh.uniform((0, 0, 0), (1, 1, 1), (5, 5, 5))
    result = map_field(
        source.export_field("density"),
        source._spec("density"),
        source._spec("density"),
        target_mesh,
        _request(solver_id, "sph" if solver_id != "sph" else "lbm", "density",
                 MappingMode.CONSERVATIVE),
    )
    assert result.metrics.sum_rel_residual < TOL
    assert result.passed is True


@pytest.mark.parametrize("solver_id,other", [("fvm", "dg"), ("dg", "bem"), ("bem", "fvm")])
def test_conservative_flux_mapping_with_tangential_refinement(solver_id, other):
    """Flux totals are surface integrals: refine tangentially, keep the normal grid."""
    source = _ready(solver_id)
    target_mesh = source.mesh.refined((1, 2, 2))
    result = map_field(
        source.export_field("flux_x"),
        source._spec("flux_x"),
        source._spec("flux_x"),
        target_mesh,
        _request(solver_id, other, "flux_x", MappingMode.CONSERVATIVE),
    )
    assert result.metrics.sum_rel_residual < TOL
    assert result.metrics.sum_src != 0.0
    assert result.passed is True


@pytest.mark.parametrize("solver_id", ["fvm", "dg", "bem"])
def test_flux_mapping_refining_along_the_normal_fails_closed(solver_id):
    """Normal refinement multiplies the surface measure: refuse, never fudge."""
    from tools.solver_adapter import MappingError

    source = _ready(solver_id)
    target_mesh = source.mesh.refined(2)
    with pytest.raises(MappingError):
        map_field(
            source.export_field("flux_x"),
            source._spec("flux_x"),
            source._spec("flux_x"),
            target_mesh,
            _request(solver_id, "fvm" if solver_id != "fvm" else "dg", "flux_x",
                     MappingMode.CONSERVATIVE),
        )


# ------------------------------------------------- precision / mode trade-off
def test_standard_mode_is_exact_on_a_linear_field_conservative_is_not():
    source = _ready("lbm")
    centers = source.mesh.centers[:, 0]
    exact = 1.0 + 2.0 * centers
    source._fields["density"] = exact.copy()
    fine = source.mesh.refined(2)
    spec = source._spec("density")

    conservative = map_field(source.export_field("density"), spec, spec, fine,
                             _request("lbm", "dem", "density", MappingMode.CONSERVATIVE))
    standard = map_field(source.export_field("density"), spec, spec, fine,
                         _request("lbm", "dem", "density", MappingMode.STANDARD))

    exact_fine = 1.0 + 2.0 * fine.centers[:, 0]
    err_conservative = float(np.max(np.abs(conservative.field.values - exact_fine)))
    err_standard = float(np.max(np.abs(standard.field.values - exact_fine)))
    assert err_standard < 1e-12
    assert err_conservative > 10 * err_standard
    # both keep the total: conservative by construction, standard by projection
    assert conservative.metrics.sum_rel_residual < TOL
    assert standard.metrics.sum_rel_residual < TOL


def test_operators_are_reported_in_the_audit_record():
    source = _ready("lbm")
    fine = source.mesh.refined(2)
    spec = source._spec("density")
    conservative = map_field(source.export_field("density"), spec, spec, fine,
                             _request("lbm", "dem", "density", MappingMode.CONSERVATIVE))
    standard = map_field(source.export_field("density"), spec, spec, fine,
                         _request("lbm", "dem", "density", MappingMode.STANDARD))
    assert conservative.record.operator == "overlap_l2"
    assert standard.record.operator == "overlap_p1"
    assert conservative.record.digest() != standard.record.digest()


# --------------------------------------------------------- negative controls
def test_lossy_nearest_without_correction_breaks_the_tolerance():
    """Falsification: the 0.1 % gate must be able to fail."""
    source = _ready("lbm")
    target_mesh = BoxMesh.uniform((0, 0, 0), (1, 1, 1), (5, 5, 5))
    spec = source._spec("density")
    result = map_field(
        source.export_field("density"), spec, spec, target_mesh,
        _request("lbm", "dem", "density", MappingMode.STANDARD,
                 operator="nearest_scaled"),
        apply_conservation_correction=False,
    )
    assert result.metrics.sum_rel_residual > TOL
    assert result.passed is False


def test_the_same_lossy_operator_with_correction_passes():
    """The correction is what restores the total: same operator, correction on."""
    source = _ready("lbm")
    target_mesh = BoxMesh.uniform((0, 0, 0), (1, 1, 1), (5, 5, 5))
    spec = source._spec("density")
    result = map_field(
        source.export_field("density"), spec, spec, target_mesh,
        _request("lbm", "dem", "density", MappingMode.STANDARD,
                 operator="nearest_scaled"),
        apply_conservation_correction=True,
    )
    assert result.metrics.sum_rel_residual < TOL
    assert result.passed is True


def test_partial_overlap_is_refused_not_zero_filled():
    from tools.solver_adapter import MappingError

    source = _ready("lbm")
    spec = source._spec("density")
    shifted = BoxMesh.uniform((0.5, 0.0, 0.0), (1.5, 1.0, 1.0), (8, 8, 8))
    with pytest.raises(MappingError):
        map_field(source.export_field("density"), spec, spec, shifted,
                  _request("lbm", "dem", "density", MappingMode.CONSERVATIVE))


# ------------------------------------------------------------ input validation
def test_dimension_mismatch_is_refused():
    source = _ready("lbm")
    spec = source._spec("density")
    other = build_adapter("dem")._spec("density")   # extensive vs density kind
    with pytest.raises(DimensionalError):
        map_field(source.export_field("density"), spec, other, source.mesh,
                  _request("lbm", "dem", "density", MappingMode.CONSERVATIVE))


def test_dimensional_exponents_must_match():
    from tools.solver_adapter import FieldSpec

    source = _ready("lbm")
    spec = source._spec("density")
    wrong_units = FieldSpec(name="density", kind="density", dim_vector=(1, -2, 0, 0))
    with pytest.raises(DimensionalError):
        map_field(source.export_field("density"), spec, wrong_units, source.mesh,
                  _request("lbm", "dem", "density", MappingMode.CONSERVATIVE))


def test_non_finite_payload_is_refused():
    from tools.solver_adapter import AdapterError

    source = _ready("lbm")
    spec = source._spec("density")
    source._fields["density"] = source._fields["density"].copy()
    source._fields["density"][3] = np.nan
    with pytest.raises(AdapterError):
        map_field(source.export_field("density"), spec, spec, source.mesh,
                  _request("lbm", "dem", "density", MappingMode.CONSERVATIVE))


def test_request_naming_another_field_is_refused():
    from tools.solver_adapter import MappingError

    source = _ready("lbm")
    spec = source._spec("density")
    request = ExchangeRequest(solver_id="lbm", version="1.0.0", target_solver="dem",
                              field_name="velocity_x")
    with pytest.raises(MappingError):
        map_field(source.export_field("density"), spec, spec, source.mesh, request)


def test_operator_for_mode_refuses_a_silent_downgrade():
    from tools.solver_adapter import MappingError
    from tools.solver_adapter.mapping import operator_for_mode

    assert operator_for_mode(MappingMode.CONSERVATIVE) == "overlap_l2"
    assert operator_for_mode(MappingMode.STANDARD) == "overlap_p1"
    with pytest.raises(MappingError):
        operator_for_mode(MappingMode.CONSERVATIVE, "nearest_scaled")
    with pytest.raises(MappingError):
        operator_for_mode(MappingMode.CONSERVATIVE, "overlap_p1")


@pytest.mark.parametrize("n_src,n_dst", [(4, 8), (4, 6), (4, 5), (8, 4), (3, 5)])
def test_conservation_holds_across_cell_count_combinations(n_src, n_dst):
    source = _ready("fvm", n_cells=n_src)
    target_mesh = BoxMesh.uniform((0, 0, 0), (1, 1, 1), (n_dst, n_dst, n_dst))
    spec = source._spec("density")
    result = map_field(source.export_field("density"), spec, spec, target_mesh,
                       _request("fvm", "dg", "density", MappingMode.CONSERVATIVE),
                       apply_conservation_correction=True)
    assert result.metrics.sum_rel_residual < TOL


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_kernel_is_conservative_on_its_own_mesh(solver_id):
    """The synthetic kernel must not create or destroy the conserved total."""
    adapter = _ready(solver_id)
    before = {name: adapter.export_field(name).total
              for name in adapter.manifest().field_names()}
    for _ in range(3):
        adapter.step(0.002)
    after = {name: adapter.export_field(name).total
             for name in adapter.manifest().field_names()}
    for name in before:
        scale = max(abs(before[name]), 1e-30)
        assert abs(after[name] - before[name]) / scale < 1e-12