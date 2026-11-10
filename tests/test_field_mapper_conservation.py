"""Conservation, correction and accuracy of the field map (measured, not asserted)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tools.field_mapper import (  # noqa: E402
    CellSet,
    ConservativeMap,
    Correction,
    MapError,
    MapLaw,
    conserved_total,
    l2_relative_error,
)


def _uniform(n: int, name: str | None = None) -> CellSet:
    edges = np.linspace(0.0, 1.0, n + 1)
    lo = np.array([[x, y] for x in edges[:-1] for y in edges[:-1]])
    hi = np.array([[x, y] for x in edges[1:] for y in edges[1:]])
    return CellSet.from_boxes(lo, hi, name=name or f"u{n}")


def _field(cells: CellSet) -> np.ndarray:
    c = cells.centers
    return 2.0 + np.sin(np.pi * c[:, 0]) * np.cos(np.pi * c[:, 1])


@pytest.mark.parametrize("ratio", [1, 2, 3, 4, 8, 10])
def test_density_total_is_conserved_for_every_grid_ratio(ratio: int):
    src = _uniform(12)
    dst = _uniform(12 * ratio)
    mapper = ConservativeMap(src, dst)
    values = _field(src)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    audit = mapper.audit(values, mapped, law=MapLaw.DENSITY)
    assert audit.total_rel_error <= 1e-14
    assert audit.covered_fraction_min == pytest.approx(1.0)
    assert audit.overshoot_ratio <= 1.0 + 1e-12


def test_identity_map_on_a_coincident_mesh_is_exact():
    cells = _uniform(9)
    values = _field(cells)
    mapper = ConservativeMap(cells, cells)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    assert l2_relative_error(mapped, values, cells.measure) <= 1e-15
    off_diagonal = mapper.matrix - np.diag(np.diag(mapper.matrix))
    assert np.allclose(off_diagonal, 0.0)


def test_extensive_and_density_laws_differ_for_non_unit_measures():
    src = _uniform(4)  # cell measure 1/16
    dst = _uniform(8)  # cell measure 1/64
    mapper = ConservativeMap(src, dst)
    density = np.full(src.n_cells, 3.0)
    as_density = mapper.apply(density, law=MapLaw.DENSITY)
    as_extensive = mapper.apply(density, law=MapLaw.EXTENSIVE)
    # density law reproduces the value, extensive law spreads the cell total
    np.testing.assert_allclose(as_density, 3.0, rtol=1e-14)
    np.testing.assert_allclose(as_extensive, 0.75, rtol=1e-14)
    assert conserved_total(as_density, dst.measure, MapLaw.DENSITY) == pytest.approx(
        conserved_total(density, src.measure, MapLaw.DENSITY)
    )
    assert conserved_total(as_extensive, dst.measure, MapLaw.EXTENSIVE) == pytest.approx(
        conserved_total(density, src.measure, MapLaw.EXTENSIVE)
    )


def test_momentum_vector_is_conserved_componentwise():
    src = _uniform(10)
    dst = _uniform(31)
    mapper = ConservativeMap(src, dst)
    rho = _field(src)
    velocity = np.stack([np.cos(src.centers[:, 0]), np.sin(src.centers[:, 1])], axis=1)
    momentum = rho[:, None] * velocity
    mapped = mapper.apply_vector(momentum, law=MapLaw.DENSITY)
    for k in range(2):
        before = float((momentum[:, k] * src.measure).sum())
        after = float((mapped[:, k] * dst.measure).sum())
        assert abs(after - before) / abs(before) <= 1e-14


def test_nearest_control_is_visibly_non_conservative_and_correction_closes_the_budget():
    src = _uniform(12)
    dst = _uniform(48)
    values = _field(src)
    raw_mapper = ConservativeMap(src, dst, operator="nearest")
    raw = raw_mapper.apply(values, law=MapLaw.DENSITY)
    raw_audit = raw_mapper.audit(values, raw, law=MapLaw.DENSITY)
    assert raw_audit.total_rel_error > 1e-3  # the control must actually be wrong

    corrected = raw_mapper.apply(values, law=MapLaw.DENSITY, correction=Correction.GLOBAL)
    fixed = raw_mapper.audit(values, corrected, law=MapLaw.DENSITY)
    assert fixed.total_rel_error <= 1e-14
    # ... but correcting the total does not buy accuracy
    assert l2_relative_error(corrected, values.mean() * np.ones(dst.n_cells), dst.measure) > 0.0
    assert fixed.l2_vs_reference is None or np.isfinite(fixed.l2_vs_reference)


def test_bounded_correction_keeps_positivity():
    src = _uniform(8)
    dst = _uniform(16)
    mapper = ConservativeMap(src, dst, operator="nearest")
    values = _field(src)
    corrected = mapper.apply(
        values, law=MapLaw.DENSITY, correction=Correction.BOUNDED, bounds=(0.0, float(values.max()))
    )
    assert corrected.min() >= 0.0
    assert corrected.max() <= values.max() + 1e-12


def test_scalar_law_is_not_a_conservation_claim_but_stays_bounded():
    src = _uniform(6)
    dst = _uniform(11)
    mapper = ConservativeMap(src, dst)
    values = _field(src)  # a smoothly varying point quantity
    mapped = mapper.apply(values, law=MapLaw.SCALAR)
    assert mapped.min() >= values.min() - 1e-12
    assert mapped.max() <= values.max() + 1e-12
    assert MapLaw.SCALAR.conserves_total is False
    assert MapLaw.DENSITY.conserves_total is True


def test_accuracy_improves_with_source_resolution():
    """The differential test: a finer source mesh must project more accurately."""
    dst = _uniform(40)
    reference = _field(dst)  # dense evaluation of the analytic-ish field
    errors = []
    for n in (8, 16, 32):
        src = _uniform(n)
        mapper = ConservativeMap(src, dst)
        mapped = mapper.apply(_field(src), law=MapLaw.DENSITY)
        errors.append(l2_relative_error(mapped, reference, dst.measure))
    assert errors[0] > errors[1] > errors[2]
    assert errors[2] < 0.2 * errors[0]


def test_partial_coverage_is_reported_not_silently_lost():
    src = _uniform(4)
    stage = CellSet.from_boxes(
        np.array([[0.0, 0.0], [1.0, 1.0]]),
        np.array([[0.5, 0.5], [2.0, 2.0]]),
        name="partial",  # covers a quarter of the source plus one disconnected cell
    )
    dst = stage
    mapper = ConservativeMap(src, dst)
    values = _field(src)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    audit = mapper.audit(values, mapped, law=MapLaw.DENSITY)
    assert audit.n_uncovered_dst == 1
    assert audit.covered_fraction_min == pytest.approx(0.0)
    assert audit.total_rel_error > 0.0


def test_dimension_and_shape_guards_fail_closed():
    with pytest.raises(MapError):
        ConservativeMap(_uniform(4), CellSet.from_boxes(np.zeros((1, 3)), np.ones((1, 3))))
    mapper = ConservativeMap(_uniform(4), _uniform(8))
    with pytest.raises(MapError):
        mapper.apply(np.zeros(5), law=MapLaw.DENSITY)
    with pytest.raises(MapError):
        ConservativeMap(_uniform(4), _uniform(8), operator="magic")