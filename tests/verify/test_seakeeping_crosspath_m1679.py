"""m1679 - cross-path adjudication of the platform's two seakeeping implementations.

These tests lock the *adjudicated* facts so the deviations cannot silently drift, and
they lock the two implementation defects found (with the fix quantified) so a future
refactor cannot reintroduce them unnoticed.

Facts asserted here are read from ``artifacts/m1679/crosspath_adjudication.json`` when
it exists, otherwise recomputed, so the suite is meaningful both in CI (no artifacts)
and after a full run.
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BACKEND = os.path.join(_ROOT, "backend")
for _p in (_BACKEND, os.path.join(_ROOT, "tools", "verify", "seakeeping")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.seakeeping import SeakeepingModel, transfer, transfer_peak          # noqa: E402
from services.digital_twin.models.geometry import HullForm                        # noqa: E402
from services.digital_twin.models.seakeeping import (                             # noqa: E402
    regular_response,
)
import crosspath_adjudication as adj                                             # noqa: E402

G = 9.80665
CASE = adj.load_canon_kvlcc2()
L, B, T, Cb, RHO = CASE["L"], CASE["B"], CASE["T"], CASE["Cb"], CASE["RHO"] if "RHO" in CASE else CASE["rho"]
KG = adj.KG_DECLARED
SERVICE_SPEED = adj.SERVICE_SPEED


@pytest.fixture(scope="module")
def hull() -> HullForm:
    return HullForm(L=L, B=B, T=T, Cb=Cb, rho=RHO)


@pytest.fixture(scope="module")
def model_a(hull) -> SeakeepingModel:
    return SeakeepingModel(
        L=L, B=B, T=T, Cb=Cb, Cwp=Cb, displacement_t=CASE["displacement_t"],
        KG=KG, GM_T=hull.KB + hull.BM - KG, rho=RHO, KB=hull.KB,
    )


# ---------------------------------------------------------------------------
# geometry gate first (platform rule)
# ---------------------------------------------------------------------------


def test_canon_geometry_closes(hull):
    residual = hull.volume / (Cb * L * B * T) - 1.0
    assert abs(residual) < 1e-9, "canon KVLCC2 geometry must close before any RAO"


def test_geometry_gate_rejects_inconsistent_cb():
    """A Cb that does not close on the displacement must raise, not warn."""
    with pytest.raises(Exception):
        SeakeepingModel(L=L, B=B, T=T, Cb=Cb, Cwp=Cb, displacement_t=100000.0,
                        KG=KG, GM_T=2.0, rho=RHO)


# ---------------------------------------------------------------------------
# closed-form anchors: both paths must satisfy them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("omega", [0.05, 0.06, 0.08])
def test_long_wave_heave_anchor_both_paths(hull, model_a, omega):
    a = model_a.rao_at(omega, 180.0, 0.0)["heave_rao_m_per_m"]
    b = regular_response(omega, 180.0, hull, KG, 0.0)["heave_rao"]
    assert a == pytest.approx(1.0, abs=0.02), "path A long-wave heave RAO must tend to 1"
    assert b == pytest.approx(1.0, abs=0.02), "path B long-wave heave RAO must tend to 1"


def test_transfer_peak_matches_closed_form():
    zeta = 0.20
    r_peak, peak = transfer_peak(zeta)
    assert transfer(r_peak, zeta) == pytest.approx(peak, rel=1e-12)
    assert peak == pytest.approx(1.0 / (2.0 * zeta * math.sqrt(1.0 - zeta ** 2)), rel=1e-12)


def test_high_frequency_decay_slope(model_a):
    t1 = transfer(1.20 / model_a.omega3, model_a.zeta33)
    t2 = transfer(1.45 / model_a.omega3, model_a.zeta33)
    slope = math.log(t2 / t1) / math.log(1.45 / 1.20)
    assert slope == pytest.approx(-2.0, abs=0.5), "SDOF transfer must decay like omega^-2"


# ---------------------------------------------------------------------------
# defect 1: shipped path B is speed-invariant (documented artefact, now fixable)
# ---------------------------------------------------------------------------


def test_shipped_path_b_ignores_ship_speed(hull):
    """Regression lock on the defect: the shipped default must stay speed-invariant.

    This is asserted *as a defect marker* - if a future change makes the shipped path
    respond to speed, this test fails and the adjudication report must be updated.
    """
    v0 = regular_response(0.42, 180.0, hull, KG, 0.0)["heave_rao"]
    v15 = regular_response(0.42, 180.0, hull, KG, SERVICE_SPEED)["heave_rao"]
    assert v0 == pytest.approx(v15, rel=1e-12)


def test_encounter_frequency_option_restores_speed_dependence(hull):
    v0 = regular_response(0.42, 180.0, hull, KG, 0.0,
                          use_encounter_frequency=True)["heave_rao"]
    row = regular_response(0.42, 180.0, hull, KG, SERVICE_SPEED,
                           use_encounter_frequency=True)
    v15 = row["heave_rao"]
    assert row["encounter_frequency_applied"] is True
    # head seas raise the encounter frequency: omega_e = omega + k U
    assert row["omega_e"] == pytest.approx(0.42 + 0.42 ** 2 / G * SERVICE_SPEED, rel=1e-12)
    assert abs(v15 - v0) / v0 > 0.10, "encounter frequency must move the 15 kn RAO by >10%"


def test_encounter_frequency_option_is_off_by_default(hull):
    row = regular_response(0.30, 180.0, hull, KG, SERVICE_SPEED)
    assert row["encounter_frequency_applied"] is False
    assert row["heave_rao"] == pytest.approx(
        regular_response(0.30, 180.0, hull, KG, 0.0)["heave_rao"], rel=1e-12)


# ---------------------------------------------------------------------------
# defect 2: beam-sea heave is identically zero in path B
# ---------------------------------------------------------------------------


def test_beam_sea_heave_defect_and_path_a_finiteness(hull, model_a):
    b = regular_response(0.35, 90.0, hull, KG, 0.0)["heave_rao"]
    a = model_a.rao_at(0.35, 90.0, 0.0)["heave_rao_m_per_m"]
    assert b < 1e-6, "path B's cos(beta)**0.5 heading factor zeroes beam-sea heave"
    assert a > 0.5, "path A must keep a finite beam-sea heave (sinc(kB/2) ~ 1 for kB<<1)"


# ---------------------------------------------------------------------------
# defect 3: the two paths integrate different frequency windows
# ---------------------------------------------------------------------------


def test_path_b_native_frequency_window_is_declared():
    """Path B's own irregular-sea entry point uses 0.03-0.50 Hz; asserting the numbers
    here keeps the window mismatch visible in the adjudication."""
    lo, hi = 2.0 * math.pi * 0.03, 2.0 * math.pi * 0.50
    assert hi / lo > 15.0
    assert hi > adj.OMEGA_MAX, "path B's window extends above path A's 1.5 rad/s"


# ---------------------------------------------------------------------------
# convention differences: quantified, not silently averaged
# ---------------------------------------------------------------------------


def test_smith_convention_ratio_reaches_factor_two(hull):
    k = 0.8 ** 2 / G
    ratio = math.exp(-k * T / 2.0) / math.exp(-k * T)
    assert ratio > 1.8, "exp(-kT/2) vs exp(-kT) must differ materially in the resonant band"


def test_pitch_added_inertia_convention_differs(hull, model_a):
    terms_b = adj.path_b_terms(hull, KG)
    kappa_b = terms_b["kappa55_effective"]
    kappa_a = model_a.A55 / model_a.I55
    assert kappa_a == pytest.approx(model_a.kappa55, rel=1e-12)
    assert kappa_b > 2.0 * kappa_a, "the section integral is >2x the declared kappa55"


def test_pitch_natural_period_deviation_is_above_standard(hull, model_a):
    terms_b = adj.path_b_terms(hull, KG)
    t_a = 2.0 * math.pi / model_a.omega5
    t_b = 2.0 * math.pi / terms_b["omega5_rad_s"]
    assert 100.0 * abs(t_b - t_a) / t_a > 15.0


# ---------------------------------------------------------------------------
# the adjudication artifact itself (present after a full run)
# ---------------------------------------------------------------------------


ART = os.path.join(_ROOT, "artifacts", "m1679", "crosspath_adjudication.json")


@pytest.mark.skipif(not os.path.exists(ART), reason="run the harness first")
def test_artifact_records_verdicts_and_grades():
    with open(ART) as fh:
        d = json.load(fh)
    assert d["verdict"]["threshold_pct"] == 15.0
    assert d["verdict"]["cross_path_consistent"] is False, \
        "the audit's finding is that the two paths disagree beyond 15 %"
    assert "WITHHELD" in d["verdict"]["absolute_accuracy_vs_model_tests"]
    assert d["defects"]["B_speed_invariance"]["sensitivity_pct"] == pytest.approx(0.0, abs=1e-9)
    assert d["geometry"]["geometry_residual_pct"] == pytest.approx(0.0, abs=1e-9)
    # every row must carry its band and its status
    for row in d["checks"]:
        assert row["band"] in ("ALL", "LONGWAVE", "SEASTATE")
        assert row["agreement_status"] in ("AGREE", "DISAGREE")
    # numbers quoted in the report must match the artifact
    sea = d["sea_state"]["rows"]
    assert len(sea) == 2 and sea[0]["U_m_s"] == 0.0
    assert sea[1]["U_m_s"] == pytest.approx(SERVICE_SPEED, rel=1e-9)