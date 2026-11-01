"""Verification-debt tests for the platform's Wageningen B-series open-water paths.

Two classes of assertions:
  * self-checks of the clean-room reference (must be green);
  * characterisation of the audited platform paths, asserting the *measured*
    deviation bands so that the audit finding cannot silently regress — and so
    that a future fix (which would drop the deviation below the band) breaks
    these tests loudly and forces the band to be tightened rather than ignored.

The platform tree is read-only and external; tests skip when it is absent.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

AUDIT = REPO_ROOT / "tools/verify/propeller_open_water/audit_open_water.py"
PLATFORM_ROOT = Path(
    __import__("os").environ.get(
        "SHIP_PLATFORM_ROOT",
        "/srv/tensor/ship-workspace/ship-performance-platform"))
HAVE_PLATFORM = PLATFORM_ROOT.exists()
needs_platform = pytest.mark.skipif(not HAVE_PLATFORM,
                                    reason=f"platform tree absent: {PLATFORM_ROOT}")


# --------------------------------------------------------------------------- #
# reference implementation self-checks
# --------------------------------------------------------------------------- #
def test_reference_table_sizes():
    """Published fit: 39 KT terms and 47 KQ terms."""
    assert len(ref.KT_COEFFICIENTS) == 39
    assert len(ref.KQ_COEFFICIENTS) == 47


def test_reference_geometry_ordering():
    """KT(0) must grow with P/D and with Ae/A0 at fixed Z (B-series behaviour)."""
    z = 4
    kt_low = ref.open_water_coefficients(0.0, 0.8, 0.55, z)["KT"]
    kt_high = ref.open_water_coefficients(0.0, 1.2, 0.55, z)["KT"]
    assert kt_high > kt_low
    kt_small_area = ref.open_water_coefficients(0.0, 1.0, 0.40, z)["KT"]
    kt_big_area = ref.open_water_coefficients(0.0, 1.0, 0.70, z)["KT"]
    assert kt_big_area > kt_small_area


def test_reference_monotone_decay_and_zero_crossing():
    """KT decreases monotonically in J and crosses zero inside the validity box."""
    Js = [0.05 * i for i in range(25)]
    kts = [ref.open_water_coefficients(J, 1.0, 0.70, 4)["KT"] for J in Js]
    assert all(b < a for a, b in zip(kts, kts[1:]))
    J_zero = ref.zero_thrust_J(1.0, 0.70, 4)
    assert 0.9 < J_zero < 1.2
    assert abs(ref.open_water_coefficients(J_zero, 1.0, 0.70, 4)["KT"]) < 1e-6


def test_reference_efficiency_below_actuator_disc_bound():
    """eta_0 must stay below the ideal (actuator-disc) efficiency for J > 0."""
    for J in (0.3, 0.5, 0.7, 0.9):
        r = ref.blasius_itcc_kt(1.0, 0.70, 4, J)
        assert 0.0 < r["ratio"] < 1.0, r


def test_reference_peak_efficiency_physically_plausible():
    peak = ref.peak_efficiency(1.0, 0.70, 4)
    assert 0.55 < peak["eta_0"] < 0.80
    assert 0.6 < peak["J"] < 1.0


def test_reference_matches_platform_official_coefficient_file():
    """Term-by-term equality with the platform's shipped official coefficients."""
    ref.check = None
    dat = PLATFORM_ROOT / "backend/data/official/Wageningen_Bseries_coeffs.dat"
    if not dat.exists():
        pytest.skip(f"coefficient file absent: {dat}")
    lines = [l.split() for l in dat.read_text().splitlines()[1:] if l.strip()]
    ct_dat = [(float(p[5]), int(p[6]), int(p[7]), int(p[8]), int(p[9])) for p in lines]
    cq_dat = [(float(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4])) for p in lines]
    assert [t for t in ct_dat if t[0] != 0.0] == ref.KT_COEFFICIENTS
    assert cq_dat == ref.KQ_COEFFICIENTS


# --------------------------------------------------------------------------- #
# audited platform paths
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def impls():
    if not HAVE_PLATFORM:
        pytest.skip("platform tree absent")
    from tools.verify.propeller_open_water import audit_open_water as audit
    return audit.load_platform_modules()


@needs_platform
def test_path_C_official_dataset_reproduces_reference(impls):
    """api/open_water.py uses the official table: must agree to round-off."""
    for J in (0.1, 0.3, 0.6, 0.9):
        kt, kq = impls["C_api_official_dat"](J, 1.0, 0.70, 4)
        c = ref.open_water_coefficients(J, 1.0, 0.70, 4)
        assert abs(kt - c["KT"]) < 1e-9
        assert abs(kq - c["KQ"]) < 1e-9


@needs_platform
def test_path_B_claimed_verified_fails_15pct_band(impls):
    """wageningen_b.py advertises validation_status=verified but is a local
    quadratic: KT error must stay above the 15 % acceptance band (documented
    finding).  A genuine fix would fail this test — that is intended."""
    from tools.verify.propeller_open_water.audit_open_water import rel_err
    errs = []
    for J in [round(0.1 * i, 1) for i in range(1, 10)]:  # J = 0.1 .. 0.9
        kt, _ = impls["B_shared_regression"](J, 1.0, 0.70, 4)
        errs.append(rel_err(kt, ref.open_water_coefficients(J, 1.0, 0.70, 4)["KT"]))
    mean_err = sum(errs) / len(errs)
    assert mean_err > 15.0, f"deviation dropped to {mean_err:.1f}% — re-audit and tighten the claim"
    # Structural defect: the local quadratic keeps producing thrust far beyond the
    # canonical zero-thrust advance ratio (regression has no physical crossing).
    J_zero_ref = ref.zero_thrust_J(1.0, 0.70, 4)
    assert impls["B_shared_regression"](J_zero_ref + 0.15, 1.0, 0.70, 4)[0] > 0.0


@needs_platform
def test_path_A_claimed_oosterveld_is_not_the_published_fit(impls):
    """backend/methods/propulsion/wageningen.py cites Oosterveld & van Oossanen but
    its coefficients are not the published ones: KT collapses to 0 over most of
    the operating range."""
    kt0, _ = impls["A_backend_methods"](0.0, 1.0, 0.70, 4)
    kt_ref0 = ref.open_water_coefficients(0.0, 1.0, 0.70, 4)["KT"]
    assert abs(kt0 - kt_ref0) / kt_ref0 > 0.15
    # thrust vanishes far too early: no positive KT at J=0.5
    assert impls["A_backend_methods"](0.5, 1.0, 0.70, 4)[0] == 0.0


@needs_platform
def test_bundled_reference_dataset_is_not_canonical():
    """The shipped B4-70 dataset (declared Oosterveld & van Oossanen, 1975) puts
    KT(0)=0.40 and zero thrust at J=1.2, while the canonical polynomial gives
    KT(0)~0.455 and J(KT=0)~1.06."""
    path = PLATFORM_ROOT / "backend/data/test_data/propulsion_wageningen.json"
    if not path.exists():
        pytest.skip("bundled dataset absent")
    data = json.loads(path.read_text())
    dp = data["data_points"]
    kt0_ref = ref.open_water_coefficients(0.0, 1.0, 0.70, 4)["KT"]
    err = abs(dp["KT"][0] - kt0_ref) / kt0_ref
    assert err > 0.10, f"dataset KT(0)={dp['KT'][0]} vs canonical {kt0_ref:.4f}"
    J_zero_ds = next(J for J, kt in zip(dp["J"], dp["KT"]) if kt == 0.0)
    J_zero_ref = ref.zero_thrust_J(1.0, 0.70, 4)
    assert abs(J_zero_ds - J_zero_ref) > 0.10


def test_audit_json_is_reproducible():
    """The committed audit artefact must exist and carry the key findings."""
    audit_json = REPO_ROOT / "artifacts/m1403/propeller_open_water/audit.json"
    if not audit_json.exists():
        pytest.skip("audit artefact not generated yet (run the audit module)")
    d = json.loads(audit_json.read_text())
    assert d["coefficient_table_check"]["identical"] is True
    assert d["implementations"]["C_api_official_dat"]["overall_mean_errKT_pct"] < 1e-6
    assert d["implementations"]["B_shared_regression"]["overall_mean_errKT_pct"] > 10.0
    assert d["bundled_dataset_check"]["max_errKT_pct"] > 15.0
    assert math.isfinite(d["literature_anchor_kp505"]["summary"]["mean_errKT_pct"])