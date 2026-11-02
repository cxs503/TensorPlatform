"""Structure-domain verification debt: plate buckling vs exact plate theory.

Two groups of tests:

*Reference tests* (offline, must pass) lock the independent reference solution in
``docs/verification_debt/structure_plate_buckling/refs_plate.py``: the closed-form
von Mises/Timoshenko coefficient, its numerical Galerkin re-derivation, the
Timoshenko shear coefficient and its Galerkin cross-check, and the kN conversion
for metre-based geometry.  If any of these drift, the audit's verdicts drift with
them, so they are the contract of the whole report.

*Platform gates* talk to the ship platform (``:8001``) and are skipped when it is
unreachable.  Three of them are marked ``xfail(strict=False)`` on purpose: they
encode the defects found by mission m1852 (frozen compression coefficient 4.0,
``critical_load`` six orders of magnitude low, unsupported boundary tokens
silently mapped to the clamped branch).  When the platform is fixed those tests
report ``XPASS``; the gate has flipped, not broken.  The remaining gates lock
behaviour that is already correct (shear coefficient, scaling law) so a future
refactor cannot silently regress it.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEBT = os.path.join(REPO, "docs", "verification_debt", "structure_plate_buckling")
sys.path.insert(0, DEBT)

import refs_plate as R  # noqa: E402

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
B, T, E, NU, SY = 1.0, 0.012, 210000.0, 0.3, 235.0


# --------------------------------------------------------------- reference core


def test_plate_factor_matches_hand_calculation():
    # pi^2 * (210000 * 0.012^3 / (12*(1-0.09))) / (1.0^2 * 0.012)
    D = 210000.0 * 0.012**3 / (12 * (1 - 0.3**2))
    assert R.plate_factor(E, NU, T, B) == pytest.approx(3.141592653589793**2 * D / (B * B * T))
    assert R.plate_factor(E, NU, T, B) == pytest.approx(27.33121218763207, rel=1e-12)


@pytest.mark.parametrize("alpha,k_ref,m_ref", [
    (0.5, 6.25, 1),            # (1/0.5 + 0.5)^2
    (1.0, 4.00, 1),            # classic square-plate value
    (1.5, 4.34027777777777, 2),
    (2.0, 4.00, 2),
    (2.5, 4.134444444444444, 3),
    (4.0, 4.00, 4),
])
def test_exact_simply_supported_coefficient(alpha, k_ref, m_ref):
    k, m = R.k_ss_exact(alpha)
    assert k == pytest.approx(k_ref, rel=1e-12)
    assert m == m_ref


def test_exact_coefficient_is_never_below_the_long_plate_limit():
    # k >= 4 for every aspect ratio; equality only at integer alpha
    for i in range(1, 400):
        alpha = i / 10.0 * 0.1 * 10  # 0.1 .. 40
        k, _ = R.k_ss_exact(alpha)
        assert k >= 4.0 - 1e-12
    for alpha in (1.0, 2.0, 3.0, 4.0):
        assert R.k_ss_exact(alpha)[0] == pytest.approx(4.0, rel=1e-12)


@pytest.mark.parametrize("alpha", [0.3, 0.5, 0.8, 1.0, 1.37, 2.0, 2.5, 3.0, 4.0, 6.0])
def test_galerkin_rederivation_agrees_with_closed_form(alpha):
    """The two independent routes must agree to machine precision."""
    k_exact, _ = R.k_ss_exact(alpha)
    assert R.k_ss_ritz(alpha) == pytest.approx(k_exact, rel=1e-12)


@pytest.mark.parametrize("alpha,k_ref", [(0.5, 25.36), (1.0, 9.34), (2.0, 6.34), (3.0, 5.7844)])
def test_timoshenko_shear_coefficient(alpha, k_ref):
    assert R.k_shear_ss_exact(alpha) == pytest.approx(k_ref, abs=1e-3)


@pytest.mark.parametrize("alpha", [1.0, 1.5, 2.0, 3.0])
def test_shear_coefficient_reproduced_by_galerkin(alpha):
    """8x8 sine basis; documented accuracy is a few percent at alpha=2."""
    got = R.k_shear_ss_numeric(alpha)
    ref = R.k_shear_ss_exact(alpha)
    assert abs(got / ref - 1.0) < 0.05


def test_critical_stress_and_coefficient_round_trip():
    k = R.k_ss_exact(1.5)[0]
    sigma = R.sigma_from_k(k, E, NU, T, B)
    assert sigma == pytest.approx(4.34027777777777 * 27.33121218763207, rel=1e-10)
    assert R.k_from_sigma(sigma, E, NU, T, B) == pytest.approx(k, rel=1e-12)


def test_critical_load_conversion_is_kilonewtons_for_metre_geometry():
    sigma = 109.325  # MPa
    P = R.P_cr_kN(sigma, B, T)
    assert P == pytest.approx(sigma * 1e6 * B * T / 1e3, rel=1e-12)
    assert P == pytest.approx(1311.9, rel=1e-3)
    # linear in thickness and in plate width
    assert R.P_cr_kN(sigma, B, 2 * T) == pytest.approx(2 * P, rel=1e-12)
    assert R.P_cr_kN(sigma, 2 * B, T) == pytest.approx(2 * P, rel=1e-12)


# ------------------------------------------------------------------ platform I/O


def _post(path, body, timeout=15.0):
    req = urllib.request.Request(
        f"{BASE}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:200]
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"platform unreachable at {BASE}: {type(exc).__name__}")


def _plate(**over):
    body = {"a": B, "b": B, "t": T, "E": E, "nu": NU, "yield_stress": SY,
            "load_type": "compression", "boundary": "simply_supported"}
    body.update(over)
    status, payload = _post("fea/plate-buckling", body)
    assert status == 200, payload
    return payload


@pytest.mark.xfail(strict=False, reason="m1852 defect A1: compression coefficient frozen at k=4.0, "
                                        "exact k(0.5)=6.25 (-36%), k(1.5)=4.34 (-7.8%)")
@pytest.mark.parametrize("alpha", [0.5, 0.667, 0.8, 1.5, 2.5])
def test_gate_platform_compression_coefficient_matches_exact(alpha):
    sigma = _plate(a=alpha * B)["critical_stress"]
    k_platform = R.k_from_sigma(sigma, E, NU, T, B)
    k_exact = R.k_ss_exact(alpha)[0]
    assert abs(k_platform / k_exact - 1.0) <= 0.03


@pytest.mark.xfail(strict=False, reason="m1852 defect A4: half_waves is reported but never used - "
                                        "the coefficient is alpha-independent")
def test_gate_reported_half_wave_count_drives_the_coefficient():
    k_short = R.k_from_sigma(_plate(a=0.5 * B)["critical_stress"], E, NU, T, B)
    k_square = R.k_from_sigma(_plate(a=1.0 * B)["critical_stress"], E, NU, T, B)
    assert k_short / k_square == pytest.approx(4.2025 / 4.0 * (6.25 / 4.2025), rel=0.01)


@pytest.mark.xfail(strict=False, reason="m1852 defect A3: critical_load = sigma*b*t/1000 with metre "
                                        "geometry -> six orders of magnitude low")
def test_gate_critical_load_matches_reported_stress():
    payload = _plate()
    P_ref = R.P_cr_kN(payload["critical_stress"], B, T)
    assert abs(payload["critical_load"] / P_ref - 1.0) <= 0.03


@pytest.mark.xfail(strict=False, reason="m1852 defect A6: 'free' and unknown boundary tokens are "
                                        "silently mapped onto the clamped branch")
def test_gate_unknown_boundary_token_is_rejected():
    status, payload = _post("fea/plate-buckling",
                            {"a": B, "b": B, "t": T, "E": E, "nu": NU,
                             "yield_stress": SY, "boundary": "totally_unknown_token"})
    assert status == 422, payload


def test_gate_shear_coefficient_is_real():
    """Already correct: locks the Timoshenko k_s = 5.34 + 4/alpha^2 behaviour."""
    for alpha in (1.0, 1.5, 2.0, 3.0):
        sigma = _plate(a=alpha * B, load_type="shear")["critical_stress"]
        k = R.k_from_sigma(sigma, E, NU, T, B)
        assert abs(k / R.k_shear_ss_exact(alpha) - 1.0) <= 0.03


def test_gate_plate_factor_scaling_is_real():
    """Already correct: sigma ~ t^2, ~ 1/b^2, ~ E."""
    ref = _plate()["critical_stress"]
    assert _plate(t=0.024)["critical_stress"] / ref == pytest.approx(4.0, rel=0.01)
    assert _plate(b=0.5, a=0.5)["critical_stress"] / ref == pytest.approx(4.0, rel=0.01)
    assert _plate(E=105000)["critical_stress"] / ref == pytest.approx(0.5, rel=0.01)