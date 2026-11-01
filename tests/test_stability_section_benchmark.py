"""Analytic benchmark for intact stability on *real* hull coefficients (m1747).

Motivation (verification debt)
------------------------------
The intact-stability benchmark (m1532 / ``tools/analytic_bench/reference.py``)
only knew rectangular box barges, i.e. ``Cb = 1`` -- a shape no ship has.
Every real-hull verdict was therefore technically WITHHELD, and in particular
the *corrected* KB kernel shipped in ``src/ship_perf/hydrostatics.py`` (m1473,
``KB = T (1 + 1/(2 Cb))/3``) had no independent adjudicator: for a box it is
indistinguishable from ``T/2`` by construction, so a wrong Cb dependence would
have slipped through.

This module extends the family to a prismatic hull with a sloped bilge of rise
``k*T`` and vertical topsides, parametrised by ``(Cb, k)``, and proves with
three mutually independent routes:

  * ``kb``      closed-form first moment of the trapezoidal section;
  * ``kb_numeric`` shoelace centroid of the immersed polygon;
  * wall-sided closed form vs polygon-clipping/bisection GZ solver -- an exact
    identity (to 1e-12) inside the validity domain found *numerically*, which
    is what makes the box validation carry over to ``Cb < 1``.

No platform code is imported, so a formula error on the platform side cannot
leak into these assertions.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.analytic_bench import reference as R  # noqa: E402

# (Cb, bilge rise fraction) pairs; feasibility requires Cb >= 1 - k/2.
CASES = [
    R.SlopedBilgeHull(100.0, 20.0, 8.0, 14.0, 6.0, cb=0.90, bilge_rise_frac=0.25,
                      name="sloped-cb090"),
    R.SlopedBilgeHull(100.0, 20.0, 8.0, 14.0, 6.0, cb=0.80, bilge_rise_frac=0.45,
                      name="sloped-cb080"),
    R.SlopedBilgeHull(100.0, 20.0, 8.0, 14.0, 6.0, cb=0.70, bilge_rise_frac=0.62,
                      name="sloped-cb070"),
]
BOX = R.BoxBarge(length=100.0, beam=20.0, draft=8.0, depth=14.0, kg=6.0)


# ---------------------------------------------------------------- geometry
@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_section_area_reproduces_block_coefficient(hull):
    """Geometric prior: the *immersed* section carries exactly Cb*B*T of area.

    (The full section polygon also encloses the freeboard volume up to the
    deck, so the clip at the waterline is what makes Cb well defined.)
    """
    immersed = R._clip_halfplane(hull.section(), 0.0, 1.0, hull.draft)
    assert R._polygon_area(immersed) == pytest.approx(
        hull.cb * hull.beam * hull.draft, rel=1e-12)
    # freeboard part is a plain rectangle B x (D - T)
    full = R._polygon_area(hull.section())
    assert full - hull.cb * hull.beam * hull.draft == pytest.approx(
        hull.beam * (hull.depth - hull.draft), rel=1e-12)


@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_kb_closed_form_equals_independent_centroid(hull):
    """Analytic trapezoid KB vs shoelace centroid of the immersed section."""
    assert hull.kb == pytest.approx(hull.kb_numeric(), rel=1e-10)


@pytest.mark.parametrize("cb", [0.62, 0.70, 0.85, 0.95])
def test_kb_reduces_to_shipped_kernel_formula_at_k1(cb):
    """k = 1 (slant reaches the waterline) must give the m1473 kernel value.

    This is the link between the reference and the platform-side fix
    ``src/ship_perf/hydrostatics.py: KB = T (1 + 1/(2 Cb))/3``.
    """
    hull = R.SlopedBilgeHull(80.0, 24.0, 6.0, 12.0, 5.0, cb=cb, bilge_rise_frac=1.0)
    assert hull.kb == pytest.approx(hull.draft * (1.0 + 1.0 / (2.0 * cb)) / 3.0,
                                    rel=1e-12)
    assert hull.kb == pytest.approx(hull.kb_numeric(), rel=1e-10)


def test_infeasible_section_is_rejected():
    """Cb = 0.6 cannot be built from a bilge rise of 0.4 T -- must raise."""
    with pytest.raises(ValueError):
        R.section_polygon(20.0, 8.0, 14.0, cb=0.6, bilge_rise_frac=0.4)


# ------------------------------------------------- Cb = 1 degenerates to box
def test_cb_one_reproduces_box_barge():
    """The new family must collapse onto the m1532 box reference exactly."""
    box = R.SlopedBilgeHull(100.0, 20.0, 8.0, 14.0, 6.0, cb=1.0, bilge_rise_frac=0.5)
    assert box.kb == pytest.approx(BOX.kb, rel=1e-12)
    assert box.bm == pytest.approx(BOX.bm, rel=1e-12)
    assert box.gm == pytest.approx(BOX.gm, rel=1e-12)
    assert box.wall_sided_limit_deg == pytest.approx(BOX.wall_sided_limit_deg, abs=1e-6)
    for phi in range(0, 90, 5):
        assert box.gz_numeric(phi) == pytest.approx(BOX.gz_numeric(phi), abs=1e-11)


# ------------------------------------------ wall-sided identity, Cb < 1
@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_wall_sided_formula_is_exact_inside_numeric_validity_domain(hull):
    """Closed-form GZ == independently integrated GZ up to the numeric limit.

    The limit itself is found by bisection on "waterplane still full breadth
    and deck still dry", so this test also validates the *domain* claim and not
    just the values.
    """
    limit = hull.wall_sided_limit_deg
    assert 5.0 < limit < 45.0
    for i in range(0, 13):
        phi = limit * i / 12.0
        assert hull.gz_wall_sided(phi) == pytest.approx(hull.gz_numeric(phi),
                                                       abs=1e-9, rel=1e-9)
        assert hull.wall_sided_exact(phi)


@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_validity_limit_is_the_first_failure(hull):
    """Just past the limit the closed form must stop being the identity."""
    limit = hull.wall_sided_limit_deg
    assert not hull.wall_sided_exact(limit + 1.0)


@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_small_angle_slope_equals_metacentric_height(hull):
    """dGZ/dphi at the origin == GM = KB + BM - KG (BM carries the Cb factor)."""
    eps = 1e-4
    slope = (hull.gz_numeric(eps) - hull.gz_numeric(0.0)) / math.radians(eps)
    assert slope == pytest.approx(hull.gm, rel=1e-5)


@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_bm_includes_displacement_scaling(hull):
    """BM = I/nabla = B^2/(12 Cb T); dropping Cb inflates GZ by up to +20 %."""
    assert hull.bm == pytest.approx(hull.beam**2 / (12.0 * hull.cb * hull.draft), rel=1e-12)
    assert hull.bm > hull.beam**2 / (12.0 * hull.draft)  # Cb < 1 -> larger BM


@pytest.mark.parametrize("hull", CASES, ids=lambda h: h.name)
def test_gz_zero_upright_and_positive_restoring(hull):
    assert hull.gz_numeric(0.0) == pytest.approx(0.0, abs=1e-12)
    assert all(hull.gz_numeric(p) > 0.0 for p in (5.0, 10.0, 15.0))


def test_closed_form_diverges_past_the_limit():
    """Beyond the limit tan^2 grows without bound while the true GZ flattens."""
    hull = CASES[0]
    limit = hull.wall_sided_limit_deg
    at_limit = hull.gz_numeric(limit)
    far = hull.gz_numeric(80.0)
    assert hull.gz_wall_sided(80.0) > 3.0 * at_limit      # unphysical blow-up
    assert far < at_limit                                  # true GZ already decaying


def test_error_envelope_rows_are_self_consistent():
    rows = R.gz_error_envelope_section(CASES[1], [0.0, 10.0, 20.0, 30.0, 45.0])
    limit = CASES[1].wall_sided_limit_deg
    for r in rows:
        assert r["abs_err_m"] == pytest.approx(
            r["gz_wall_sided_m"] - r["gz_numeric_m"], abs=1e-12)
        assert r["wall_sided_exact"] == (r["heel_deg"] <= limit + 1e-9)
        if r["wall_sided_exact"] and abs(r["gz_numeric_m"]) > 1e-9:
            assert abs(r["rel_err_pct"]) < 1e-6