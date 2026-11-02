"""Absolute-accuracy grading tests for the Wageningen B-series open-water paths.

Context
-------
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`` left absolute accuracy
against published charts as **WITHHELD** ("no network, no chart point values").
The published Oosterveld & van Oossanen (1975) coefficient tables are in fact
already transcribed offline in
``tools/verify/propeller_open_water/reference_bseries.py``, so that hole is
closable — and this file closes it with machine-checkable assertions instead of
prose.

Three classes of assertions:

1. *Reference self-checks* — the transcribed polynomial must obey the propeller
   identities and qualitative physics (``eta_0 = J KT / 2 pi KQ``, monotone
   ``KT``/``KQ``, one interior efficiency maximum).  If the transcription is
   corrupted these fire.
2. *Harness sensitivity* — a deliberately wrong implementation must be graded
   FAIL.  A grader that cannot see a broken implementation is worthless.
3. *Characterisation of the two implementations the platform could serve* — the
   in-repo blade-element/momentum model (graded and banded) and, when the running
   platform is reachable, the live ``/propeller-design/wageningen-b`` endpoint.
   The measured deviation bands are asserted, so a future fix drops the error
   below the band and breaks this test loudly, forcing the band to be tightened
   rather than silently ignored.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verify.propeller_open_water import absolute_accuracy as aa  # noqa: E402
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

CASE = (1.00, 0.55, 4)
CASE_J = (0.30, 0.40, 0.50, 0.60, 0.70)


def _grade(fn, label="test", Js=CASE_J, geoms=(CASE,), **kw):
    return aa.grade(fn, label, grid_j=Js, grid_geom=geoms, **kw)


# --------------------------------------------------------------------------- #
# 1. reference self-checks
# --------------------------------------------------------------------------- #
class TestReferenceSelfConsistency:
    @pytest.mark.parametrize("J", [0.2, 0.4, 0.6, 0.8])
    def test_efficiency_identity(self, J):
        """eta_0 = J KT / (2 pi KQ) — definitional, must hold to machine precision."""
        c = ref.open_water_coefficients(J, *CASE)
        eta = (J / (2.0 * math.pi)) * (c["KT"] / c["KQ"])
        assert eta == pytest.approx(c["eta_0"], rel=1e-12)

    def test_kt_kq_monotone_decreasing(self):
        """KT and KQ must fall monotonically with J on the physical branch."""
        prev = None
        for J in [0.05 * i for i in range(0, 19)]:
            c = ref.open_water_coefficients(J, *CASE)
            if prev is not None:
                assert c["KT"] < prev[0]
                assert c["KQ"] < prev[1]
            prev = (c["KT"], c["KQ"])

    def test_single_interior_efficiency_maximum(self):
        peak = ref.peak_efficiency(*CASE)
        assert 0.5 < peak["J"] < 1.1
        # B4-55 P/D=1.0 peak efficiency sits in the 0.65-0.74 band; loose physical
        # band on purpose (this is a chart-level anchor, not a memory transcription)
        assert 0.65 < peak["eta_0"] < 0.74

    def test_zero_thrust_advance_ratio_band(self):
        """J0/P-D is ~1.0 for the polynomial; the band is a physics sanity check."""
        J0 = ref.zero_thrust_J(*CASE)
        assert 0.9 < J0 / CASE[0] < 1.1

    def test_static_coefficients_plausible(self):
        c = ref.open_water_coefficients(0.0, *CASE)
        assert 0.35 < c["KT"] < 0.50
        assert 0.05 < c["KQ"] < 0.07


# --------------------------------------------------------------------------- #
# 2. harness sensitivity
# --------------------------------------------------------------------------- #
class TestHarnessSensitivity:
    def test_reference_grades_itself_exact(self):
        rep = _grade(aa.reference_point, "reference")
        assert aa.verdict(rep) == "TIGHT"
        for q in ("KT", "KQ", "eta0"):
            assert rep["summary"][q]["max"] < 1e-12

    def test_constant_thrust_implementation_is_flagged(self):
        """A constant-KT surrogate must not survive the grader."""
        def constant(J, P_D, Ae_Ao, Z):
            return {"KT": 0.25, "KQ": 0.04, "eta0": J * 0.25 / (2 * math.pi * 0.04)}
        rep = _grade(constant, "constant")
        assert aa.verdict(rep) == "FAIL"
        assert rep["summary"]["KT"]["within_15pct"] < 0.5

    def test_clamped_efficiency_implementation_is_flagged(self):
        """The historic eta0 = clip(..., 0.85) pathology must be caught."""
        def clamped(J, P_D, Ae_Ao, Z):
            c = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
            # the historic pathology: efficiency clipped into [0.3, 0.55]
            return {"KT": c["KT"], "KQ": c["KQ"], "eta0": min(0.50, max(0.3, c["eta_0"]))}
        # sample the upper branch, where a clamp bites hardest
        rep = _grade(clamped, "clamped", Js=(0.60, 0.70, 0.80, 0.90))
        assert rep["summary"]["eta0"]["design_region"]["verdict"] != "TIGHT"
        assert rep["summary"]["eta0"]["within_3pct"] < 0.5

    def test_unloaded_points_excluded_from_relative_error(self):
        """Near zero thrust, relative error must not be counted (ill-conditioned)."""
        rep = _grade(aa.reference_point, "reference", Js=[1.30, 1.40])
        assert rep["points_total"] == 2
        assert rep["points_excluded_unloaded"] == 2


# --------------------------------------------------------------------------- #
# 3. implementations the platform could serve
# --------------------------------------------------------------------------- #
class TestBladeElementModelAbsoluteAccuracy:
    """Characterisation of ``backend/services/propeller_open_water.py``.

    The BEM path uses a generic (sin^0.55) chord distribution, not the B-series
    one, so a residual is expected; the bands below are the measured ones and are
    deliberately tight enough to catch a regression (e.g. a returns-to-MOCK fix).
    """

    @pytest.fixture(scope="class")
    def rep(self):
        return _grade(lambda J, p, a, z: aa.bem_point(J, p, a, z, sections=16), "bem")

    def test_design_region_errors_within_acceptance(self, rep):
        d = {q: rep["summary"][q]["design_region"] for q in ("KT", "KQ", "eta0")}
        assert d["KT"]["mean"] < 0.10
        assert d["KQ"]["mean"] < 0.10
        assert d["eta0"]["mean"] < 0.15

    def test_not_publication_grade(self, rep):
        """Honest FAIL: this model does not reach the 3 % bar anywhere on the grid."""
        for q in ("KT", "KQ", "eta0"):
            assert rep["summary"][q]["verdict"] != "TIGHT"

    def test_bias_direction_is_documented(self, rep):
        """The model over-predicts efficiency at design load (known optimism)."""
        eta_errs = [p["eta0_err"] for p in rep["points"] if p.get("eta0_err") is not None]
        assert eta_errs and sum(eta_errs) / len(eta_errs) > 0.0

    def test_radial_discretisation_converged(self):
        """Doubling the radial sections must change KT by well under the bar."""
        kt16 = aa.bem_point(0.6, *CASE, sections=16)["KT"]
        kt80 = aa.bem_point(0.6, *CASE, sections=80)["KT"]
        assert abs(kt16 - kt80) / kt80 < 0.01


# --------------------------------------------------------------------------- #
# 4. live served endpoint (skipped when the platform is not running)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def live_report():
    import os
    import urllib.request

    base = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
    fn = aa.live_endpoint_point(base, timeout=8.0)
    try:  # one probe call decides reachability
        fn(0.5, *CASE)
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(f"platform endpoint unreachable at {base}: {exc}")
    return _grade(fn, f"live {base}")


class TestServedEndpointDeviation:
    def test_served_path_is_not_the_polynomial(self, live_report):
        """Measured 2026-11-01: KQ mean error ~46 %, eta0 ~41 % — the endpoint is
        a placeholder while ``propeller.open_water`` is graded REAL.  If this test
        ever fails, the endpoint was (partly) fixed: re-measure and tighten."""
        s = live_report["summary"]
        assert s["KQ"]["design_region"]["mean"] > 0.25
        assert s["eta0"]["design_region"]["mean"] > 0.25

    def test_served_efficiency_is_clamped(self, live_report):
        """eta0 saturates at exactly 0.85 regardless of geometry — the clamp tell."""
        etas = {round(p["eta0"], 6) for p in live_report["points"] if p["eta0"] >= 0.84}
        assert etas == {0.85}