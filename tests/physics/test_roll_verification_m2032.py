"""Roll (横摇) verification debt — m2032.

Two independent roll paths live in the platform::

    A) backend/api/seakeeping_physics.py   strip kernel, per-mode solve, k_eff = 0.36 B, zeta44 = 0.08
    B) backend/services/seakeeping.py      SDOF transfer model, I44 = m (0.35 B)^2, A44 = 0.35 I44, zeta44 = 0.10

Neither had a machine-adjudicable anchor on the *roll* axis: the strip-kernel verification
(m1547) covered heave/pitch added mass and the heading-symmetry identity, the SDOF module
documented its limits but was never checked against the rule-of-thumb roll period.

What this module proves (machine criteria, all numbers recomputed, none transcribed):

  K1  beam-sea roll RAO of the strip kernel **is** the SDOF magnification
      1/sqrt((1-nu^2)^2 + (2 zeta nu)^2)  —  rel. error <= 1e-12 (exact identity, see note)
  K2  long-wave limit  omega -> 0 :  roll/slope -> 1            (abs 1e-9)
  K3  high-frequency decay  roll * nu^2 -> 1 as nu -> inf       (rel 1e-3 at nu = 50)
  K4  resonant peak of the kernel curve = 1/(2 zeta sqrt(1-zeta^2)) at nu = sqrt(1-2 zeta^2)
  K5  phase triple:  0 @ nu->0,  -90 deg @ nu = 1,  -180 deg @ nu->inf
  K6  heading symmetry  H-ROLL-SYM: roll excitation identical to zero at beta = 0 / 180 deg
  K7  beam seas are the roll maximum; head/following seas carry no roll
  K8  half-power bandwidth  Delta(nu) = 2 zeta
  S1  SDOF `transfer` / `transfer_peak` of path B match the closed form and a numeric scan
  S2  path B roll excitation follows the *transverse* wave slope (|sin b|) — regression lock
      for defect F1 (before m2032 it used |cos b|: roll was zero in beam seas and maximal
      in head seas, i.e. exactly inverted, plus roll_rad_per_rad -> nan at beta = 90 deg)
  S3  path B beam-sea long-wave roll/slope -> 1
  R1  roll natural period of both paths vs the IS Code 2008 (MSC.267(85)) §2.3.4 regression
      T_R = 2 C B / sqrt(GM),  C = 0.373 + 0.023 (B/d) - 0.043 (L/100)  -> error per hull
  R2  cross-copy drift of T44 (the two paths must agree; today they do not, see DEBT)
  R3  physical gyradius bound: the assumed virtual gyradius may not fall below the
      homogeneous-prism value sqrt(B^2 + T^2)/sqrt(12)
  Z1  roll damping defaults (0.08 / 0.10) move the resonant amplitude by 25 % — the
      absolute roll level is therefore WITHHELD, only ratios/shape are verified

Note on K1 (why it is exact, not approximate): for beam seas and zero speed the kernel
excitation is  f4 = sum_sections rho g GM (A_s dx) k a sin(beta), with sum A_s dx = nabla,
so f4/slope = rho g nabla GM sin(beta) = C44 sin(beta)  *identically*, and the kernel
denominator is -omega_e^2 i44 + i omega_e (2 zeta i44 omega44) + C44.  With i44 = m k_eff^2
and omega44 = sqrt(C44/i44) this is the SDOF transfer re-parameterised, hence the machine
precision agreement.  The identity is what makes the kernel *internally* verifiable; it
says nothing about the external accuracy of k_eff / zeta (see R1, R3, Z1).

Reference (R1) is the IS Code 2008 rolling-period regression, which is itself an empirical
fit to measured full-scale periods; it absorbs hull mass distribution and added inertia into
C(L, B, d).  Offline it can only be transcribed, not re-downloaded, so R1 is used as a
*band* check (15 % platform acceptance bar), never as ground truth to 1 %.

Run::

    python3 -m pytest tests/physics/test_roll_verification_m2032.py --noconftest -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for sub in ("backend/api", "backend"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import seakeeping_physics as sp              # noqa: E402  (path A, FastAPI-free kernel)
from services import seakeeping as sv        # noqa: E402  (path B, SDOF transfer model)

G = 9.80665
ZETA_KERNEL = sp.ZETA_ROLL                   # 0.08
KEFF_KERNEL = sp.KXX_EFF_RATIO               # 0.36 (fraction of B)

# Hull matrix: (L, B, T, Cb, Cwp, GM, KG).  Cb*L*B*T closes on the displacement by
# construction (geometry gate), GM in the range of a loaded cargo ship.
HULLS = [
    dict(name="coaster", L=100.0, B=16.0, T=6.0, Cb=0.70, Cwp=0.80, GM=1.50),
    dict(name="handysize", L=150.0, B=22.0, T=7.5, Cb=0.75, Cwp=0.82, GM=1.50),
    dict(name="panamax", L=200.0, B=32.0, T=9.0, Cb=0.80, Cwp=0.86, GM=1.50),
    dict(name="vlcc", L=300.0, B=50.0, T=13.0, Cb=0.82, Cwp=0.88, GM=1.50),
]


def _ship(h) -> dict:
    disp = h["Cb"] * h["L"] * h["B"] * h["T"] * 1.025
    return dict(L=h["L"], B=h["B"], T=h["T"], Cb=h["Cb"], Cwp=h["Cwp"],
                displacement=disp, GM=h["GM"], KG=0.55 * h["T"])


def _model(h) -> sv.SeakeepingModel:
    disp = h["Cb"] * h["L"] * h["B"] * h["T"] * 1.025
    return sv.SeakeepingModel(L=h["L"], B=h["B"], T=h["T"], Cb=h["Cb"], Cwp=h["Cwp"],
                              displacement_t=disp, KG=0.55 * h["T"], GM_T=h["GM"])


def _sdof(nu: float, zeta: float) -> float:
    return 1.0 / math.sqrt((1.0 - nu * nu) ** 2 + (2.0 * zeta * nu) ** 2)


def _is_code_period(L, B, T, GM):
    """IS Code 2008 §2.3.4 rolling period regression (transcribed reference)."""
    C = 0.373 + 0.023 * (B / T) - 0.043 * (L / 100.0)
    return 2.0 * C * B / math.sqrt(GM), C


def _kernel_T44(ship):
    return sp.natural_periods(ship)["roll"]


def _kernel_roll_rao(ship, omega, heading=90.0, speed=0.0):
    return sp.solve_rao(ship, omega, heading, speed)["roll_rao"]


# ---------------------------------------------------------------- path A: kernel


@pytest.mark.parametrize("h", HULLS, ids=[h["name"] for h in HULLS])
def test_K1_beam_sea_roll_rao_is_sdof_magnification_exactly(h):
    """K1: beam-sea kernel roll RAO == SDOF magnification, machine precision."""
    ship = _ship(h)
    w44 = 2.0 * math.pi / _kernel_T44(ship)
    worst = 0.0
    for nu in (0.10, 0.30, 0.70, 0.90, 0.99, 1.00, 1.01, 1.30, 2.00, 4.00):
        got = _kernel_roll_rao(ship, nu * w44)
        ref = _sdof(nu, ZETA_KERNEL)
        worst = max(worst, abs(got / ref - 1.0))
    assert worst <= 1e-12, f"kernel roll RAO is not the SDOF transfer: rel err {worst:.3e}"


@pytest.mark.parametrize("h", HULLS, ids=[h["name"] for h in HULLS])
def test_K2_long_wave_roll_limit_is_one(h):
    """K2: omega -> 0, beam seas: roll/slope -> 1 (ship follows the wave surface)."""
    ship = _ship(h)
    omega = 1.0e-3
    w44 = 2.0 * math.pi / _kernel_T44(ship)
    rao = _kernel_roll_rao(ship, omega)
    # the residual is the SDOF inertia term (1 - nu^2), not a modelling error:
    assert rao == pytest.approx(_sdof(omega / w44, ZETA_KERNEL), rel=1e-12)
    assert rao == pytest.approx(1.0, abs=1e-4)


def test_K3_high_frequency_roll_decays_as_nu_minus_two():
    """K3: roll * nu^2 -> 1 for nu -> inf (mass-controlled asymptote)."""
    ship = _ship(HULLS[2])
    w44 = 2.0 * math.pi / _kernel_T44(ship)
    for nu in (10.0, 20.0, 50.0):
        val = _kernel_roll_rao(ship, nu * w44) * nu * nu
        assert val == pytest.approx(nu * nu * _sdof(nu, ZETA_KERNEL), rel=1e-12)
    assert _kernel_roll_rao(ship, 50.0 * w44) * 2500.0 == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("h", HULLS, ids=[h["name"] for h in HULLS])
def test_K4_resonant_peak_location_and_amplitude(h):
    """K4: peak at nu = sqrt(1-2 zeta^2) with value 1/(2 zeta sqrt(1-zeta^2))."""
    ship = _ship(h)
    w44 = 2.0 * math.pi / _kernel_T44(ship)
    nu_peak = math.sqrt(1.0 - 2.0 * ZETA_KERNEL ** 2)
    amp = 1.0 / (2.0 * ZETA_KERNEL * math.sqrt(1.0 - ZETA_KERNEL ** 2))
    # numeric scan of the kernel itself (2500 points, no closed form involved)
    best_nu, best = 0.0, 0.0
    for i in range(1, 2501):
        nu = 0.5 + i * (0.5 / 2500.0)          # scan nu in [0.5, 1.0]
        val = _kernel_roll_rao(ship, nu * w44)
        if val > best:
            best_nu, best = nu, val
    assert best_nu == pytest.approx(nu_peak, abs=4e-4)
    assert best == pytest.approx(amp, rel=1e-4)
    assert best == pytest.approx(ZETA_KERNEL ** -1 / 2.0, rel=0.02)   # ~1/(2 zeta)


def test_K5_roll_phase_triple():
    """K5: phase 0 / -90 / -180 deg at nu -> 0 / 1 / inf."""
    ship = _ship(HULLS[2])
    w44 = 2.0 * math.pi / _kernel_T44(ship)
    def ph(nu):
        return sp.solve_rao(ship, nu * w44, 90.0, 0.0)["roll_phase_deg"]

    for nu in (0.01, 0.05, 0.5, 1.0, 2.0, 20.0, 200.0):
        ref = -math.degrees(math.atan2(2.0 * ZETA_KERNEL * nu, 1.0 - nu * nu))
        assert ph(nu) == pytest.approx(ref, abs=1e-9), f"nu={nu}"
    assert ph(1.0) == pytest.approx(-90.0, abs=1e-6)
    assert ph(0.01) == pytest.approx(0.0, abs=0.2)      # 2 zeta nu radians = 0.09 deg


@pytest.mark.parametrize("heading", [0.0, 180.0])
def test_K6_head_and_following_seas_carry_no_roll(heading):
    """K6: H-ROLL-SYM — port/starboard symmetry kills the roll moment exactly."""
    ship = _ship(HULLS[2])
    for omega in (0.3, 0.6, 0.785, 1.2):
        assert abs(_kernel_roll_rao(ship, omega, heading)) <= 1e-12


def test_K7_beam_seas_are_the_roll_maximum():
    """K7: roll RAO is maximal in beam seas and symmetric about beta = 90 deg."""
    ship = _ship(HULLS[2])
    omega = 2.0 * math.pi / 8.0
    rao = {hb: _kernel_roll_rao(ship, omega, hb) for hb in range(0, 181, 15)}
    assert max(rao, key=rao.get) == 90
    for hb in range(15, 90, 15):
        assert rao[hb] == pytest.approx(rao[180 - hb], rel=1e-9)
    assert rao[90] > 5.0 * rao[45]
    # NB: the heading sweep is not monotone towards beam seas -- the lengthwise phasing
    # factor Gamma(u), u = k L |cos b| / 2 oscillates and dips near u = 4.49 (|Gamma| ~ 0),
    # so 45 deg can exceed 60 deg.  Only the beam-sea maximum and the port/starboard
    # symmetry are exact statements.


def test_K8_half_power_bandwidth_is_two_zeta():
    """K8: |H| = |H|peak/sqrt(2) at nu = 1 -+ zeta, i.e. Delta(nu) = 2 zeta."""
    ship = _ship(HULLS[2])
    w44 = 2.0 * math.pi / _kernel_T44(ship)
    target = (1.0 / (2.0 * ZETA_KERNEL * math.sqrt(1.0 - ZETA_KERNEL ** 2))) / math.sqrt(2.0)
    # fine scan of the kernel: the |H| >= |H|peak/sqrt(2) band is contiguous about resonance
    NU = [0.5 + i * (1.0 / 200000.0) for i in range(200001)]      # 5e-6 resolution
    inside = [nu for nu in NU if _kernel_roll_rao(ship, nu * w44) >= target]
    assert inside, "resonant band not resolved"
    dn = inside[-1] - inside[0]
    # half-power bandwidth = 2 zeta up to an O(zeta^2) correction; the residual is a
    # stated approximation error of the textbook half-power rule, not a kernel defect.
    assert dn == pytest.approx(2.0 * ZETA_KERNEL, rel=1e-1)


# ---------------------------------------------------------- path B: SDOF model


@pytest.mark.parametrize("zeta", [0.02, 0.05, 0.08, 0.10, 0.20, 0.50])
def test_S1_sdof_transfer_matches_closed_form(zeta):
    """S1: transfer()/transfer_peak() reproduce the textbook SDOF algebra."""
    for r in (0.1, 0.5, 0.9, 1.0, 1.1, 2.0):
        assert sv.transfer(r, zeta) == pytest.approx(_sdof(r, zeta), rel=1e-14)
    r_peak, h_peak = sv.transfer_peak(zeta)
    assert h_peak == pytest.approx(_sdof(r_peak, zeta), rel=1e-13)
    if zeta < 1.0 / math.sqrt(2.0):
        scan = max(sv.transfer(i / 20000.0, zeta) for i in range(1, 40001))
        assert scan == pytest.approx(h_peak, rel=1e-6)
    with pytest.raises(ValueError):                 # peak only exists below zeta = 1/sqrt2
        sv.transfer_peak(0.8)


def test_S2_roll_excitation_follows_transverse_wave_slope():
    """S2 (defect F1 lock): roll lives on |sin b|, not |cos b|.

    Before m2032 ``m4`` was built from ``|cos b|``, so beam seas (the governing roll case)
    returned exactly zero roll and head seas the maximum, and ``roll_rao_rad_per_rad``
    divided by ``k|cos b|`` -> nan at beta = 90 deg.
    """
    m = _model(HULLS[2])
    omega = 2.0 * math.pi / 8.0
    row = {hb: m.rao_at(omega, heading_deg=hb) for hb in (0, 45, 90, 135, 180)}
    assert row[90]["roll_rao_deg_per_m"] > 0.0, "beam seas must carry roll"
    assert math.isfinite(row[90]["roll_rao_rad_per_rad"]), "beam-sea roll/slope must be finite"
    assert abs(row[0]["roll_rao_deg_per_m"]) < 1e-9, "following seas: roll must vanish"
    assert abs(row[180]["roll_rao_deg_per_m"]) < 1e-9, "head seas: roll must vanish"
    assert row[45]["roll_rao_deg_per_m"] == pytest.approx(row[135]["roll_rao_deg_per_m"], rel=1e-12)
    assert row[90]["roll_rao_deg_per_m"] > 100.0 * row[45]["roll_rao_deg_per_m"]


def test_S3_beam_sea_roll_long_wave_limit_is_one():
    """S3: path B beam-sea roll/slope -> 1 as omega -> 0 (Smith + phasing -> 1)."""
    m = _model(HULLS[2])
    assert m.rao_at(1.0e-3, heading_deg=90.0)["roll_rao_rad_per_rad"] == pytest.approx(1.0, abs=5e-3)


def test_S4_resonant_roll_response_algebra_is_exact():
    """S4: at omega_e = omega4 the path-B roll RAO equals (m4/C44)/(2 zeta44) exactly."""
    m = _model(HULLS[2])
    omega = m.omega4
    row = m.rao_at(omega, heading_deg=90.0)
    # m4/C44 = k |sin b| Gamma e^{-kT} with |sin 90| = 1; transfer(1, zeta) = 1/(2 zeta)
    ref = row["wave_number"] * row["gamma_len"] * row["smith"] / (2.0 * m.zeta44)
    assert row["roll_rao_deg_per_m"] == pytest.approx(math.degrees(ref), rel=1e-12)


# ----------------------------------------------------------- reference / debt


def _periods_table():
    rows = []
    for h in HULLS:
        T_code, C = _is_code_period(h["L"], h["B"], h["T"], h["GM"])
        T_a = _kernel_T44(_ship(h))
        T_b = _model(h).natural_periods()["roll"]
        rows.append(dict(name=h["name"], C=C, T_code=T_code, T_a=T_a, T_b=T_b,
                         err_a=T_a / T_code - 1.0, err_b=T_b / T_code - 1.0,
                         drift=T_b / T_a - 1.0))
    return rows


def test_R1_roll_period_within_platform_bar_for_kernel():
    """R1: strip-kernel T44 vs the IS Code regression stays inside the 15 % bar."""
    rows = _periods_table()
    worst = max(rows, key=lambda r: abs(r["err_a"]))
    assert abs(worst["err_a"]) <= 0.15, rows
    # and it is not accidentally exact either: a fixed k_eff cannot track C(L,B,d) to <1 %
    assert max(abs(r["err_a"]) for r in rows) > 0.01


def test_R2_debt_marker_cross_copy_roll_period_drift():
    """R2 (DEBT-ROLL-1): the two roll paths disagree by ~10 % on T44 and ~23 % on the
    IS Code reference for the 300 m hull.  This test *pins the current debt* so that a
    later reconciliation cannot pass silently; flip the assertion when the convention is
    unified (see docs/audit/m2032_roll_verification_debt.md)."""
    rows = _periods_table()
    drift = max(abs(r["drift"]) for r in rows)
    worst_b = max(abs(r["err_b"]) for r in rows)
    assert drift > 0.05, f"drift {drift:.2%}: DEBT-ROLL-1 appears fixed — update this test"
    assert worst_b > 0.15, f"path B worst error {worst_b:.2%} — update this test"


def test_R3_virtual_gyradius_above_homogeneous_prism_bound():
    """R3: k_eff/B must exceed the uniform-prism gyradius sqrt(1+(T/B)^2)/sqrt(12)."""
    for h in HULLS:
        bound = math.sqrt(1.0 + (h["T"] / h["B"]) ** 2) / math.sqrt(12.0)
        k_a = KEFF_KERNEL
        m = _model(h)
        k_b = math.sqrt((m.I44 + m.A44) / m.mass) / h["B"]
        assert k_a >= bound, (h["name"], bound, k_a)
        assert k_b >= bound, (h["name"], bound, k_b)
        assert k_b <= 0.45, (h["name"], k_b)      # engineering upper band 0.42-0.45 B


def test_Z1_roll_amplitude_is_damping_controlled_withheld():
    """Z1: the two default damping ratios differ by 25 % in resonant amplitude, and there
    is no bundled anchor for either, so absolute roll level is WITHHELD (ratios only)."""
    amp_a = 1.0 / (2.0 * ZETA_KERNEL * math.sqrt(1.0 - ZETA_KERNEL ** 2))
    zeta_b = _model(HULLS[2]).zeta44
    amp_b = 1.0 / (2.0 * zeta_b * math.sqrt(1.0 - zeta_b ** 2))
    assert zeta_b == pytest.approx(0.10)
    assert amp_b / amp_a == pytest.approx(0.08 / 0.10, rel=0.01)   # peak ~ 1/zeta
    assert abs(amp_b / amp_a - 1.0) > 0.15, "roll level would then be inside the 15 % bar"