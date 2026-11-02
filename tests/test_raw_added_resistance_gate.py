"""Added-resistance / speed-loss gate (mission m1859, verifier seat).

Pins the part of the digital-twin ``R_AW`` audit that must not regress, and pins the
*open* defects as strict xfails so the suite flips the day a period-aware law lands.

Evidence: ``artifacts/verification_debt/added_resistance_speed_loss/results.json``
(regenerate with ``python3 tools/verifier/raw_added_resistance_gate.py``),
narrative: ``docs/verification_debt/added_resistance_speed_loss/REPORT.md``.

Nothing here is copied from the report: every number is recomputed from the live
module (``backend/services/digital_twin/models/seakeeping.py``).
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.seakeeping import (  # noqa: E402
    RAW_LAW,
    added_resistance,
    irregular_seastate,
)
from services.digital_twin.surrogate_registry import get_registry  # noqa: E402

G = 9.80665
SHIPS = [
    ("KVLCC2 tanker", 320.0, 58.0, 20.8, 0.82, 0.137),
    ("KCS container", 230.0, 32.2, 10.8, 0.651, 0.190),
    ("Ferry", 120.0, 20.0, 5.0, 0.60, 0.220),
    ("Trawler", 45.0, 11.0, 3.2, 0.55, 0.280),
]
TPS = [3.0, 5.0, 7.0, 9.0, 11.0, 14.0]


def _declared(Hs, heading_deg, h, U):
    """Independent re-evaluation of ``RAW_LAW['formula']`` from the declaration."""
    Fn = U / math.sqrt(G * h.L)
    head = math.cos(math.radians(0.5 * (180.0 - abs(heading_deg)))) ** 2
    fac = 0.5 + 0.5 * min(Fn / RAW_LAW["fn_ref"], RAW_LAW["fn_cap"])
    return RAW_LAW["c_aw"] * head * fac * h.rho * G * Hs ** 2 * h.B ** 2 / h.L


# ---------------------------------------------------------------- must pass
def test_declaration_reproduces_code():
    """W3: the machine-readable declaration is the single source of truth (drift gate)."""
    worst = 0.0
    for _n, L, B, T, Cb, Fn in SHIPS:
        h = HullForm(L, B, T, Cb)
        for hd in (0.0, 90.0, 135.0, 180.0):
            for fn in (0.0, 0.08, 0.15, 0.25, 0.35):
                U = fn * math.sqrt(G * L)
                for Hs in (1.0, 4.0, 8.0):
                    code = added_resistance(Hs, hd, h, U)
                    den = max(abs(code), 1e-12)
                    worst = max(worst, abs(code - _declared(Hs, hd, h, U)) / den)
    assert worst < 1e-12, f"doc/code drift: worst relative residual {worst:.3e}"


def test_quadratic_law_exact():
    """W2: second-order theory -> R_AW proportional to Hs**2 (slope exactly 2)."""
    h = HullForm(320.0, 58.0, 20.8, 0.82)
    V = 0.137 * math.sqrt(G * h.L)
    hs = np.array([0.5, 1.0, 2.0, 4.0, 8.0])
    raw = np.array([added_resistance(float(x), 180.0, h, V) for x in hs])
    slope = float(np.polyfit(np.log(hs), np.log(raw), 1)[0])
    assert abs(slope - 2.0) < 0.02, f"log-log slope {slope:.4f} deviates from 2"


def test_declaration_honest_about_missing_physics():
    """The declaration must not claim period / literature-method support it lacks."""
    assert RAW_LAW["tp_dependent"] is False
    assert RAW_LAW["relative_motion_dependent"] is False
    assert RAW_LAW["literature_method"] is None
    assert RAW_LAW["absolute_accuracy_claimed"] is False
    assert "Hs" in RAW_LAW["name"] or "hs" in RAW_LAW["name"]


def test_provenance_label_is_not_gerritsma():
    """The registry must not attribute an Hs-only heuristic to Gerritsma-Beukelman."""
    reg = get_registry()
    seakeeping = None
    for name in reg.names():
        s = reg.get(name)
        if name == "seakeeping" or getattr(s, "name", "") == "seakeeping":
            seakeeping = s
    assert seakeeping is not None, "seakeeping surrogate missing from registry"
    label = str(getattr(seakeeping, "reference_model", ""))
    assert "gerritsma" not in label.lower(), f"false provenance label: {label!r}"
    assert "raw_empirical" in label.lower()


def test_irregular_seastate_exposes_evidence_diagnostics():
    """Downstream must be able to read the evidence level off the payload."""
    h = HullForm(320.0, 58.0, 20.8, 0.82)
    r = irregular_seastate(4.0, 8.0, 180.0, h, 12.0, 7.7, n_freq=12, n_sections=21)
    assert r["R_aw_model"] == RAW_LAW["name"]
    assert r["R_aw_tp_dependent"] is False
    assert r["R_aw_absolute_accuracy"] == "WITHHELD"
    assert r["R_aw_kN"] > 0.0


# ------------------------------------------- open defects, pinned as strict xfail
@pytest.mark.xfail(strict=True, reason="m1859 W1: R_AW has no wave-period dependence "
                                       "(spread 0.0 over Tp 3-14 s at Hs=4 m)")
def test_expected_period_sensitivity():
    h = HullForm(320.0, 58.0, 20.8, 0.82)
    V = 0.137 * math.sqrt(G * h.L)
    vals = [added_resistance(4.0, 180.0, h, V) for _ in TPS]
    spread = (max(vals) - min(vals)) / max(vals)
    assert spread > 0.15, f"R_AW still Tp-blind (spread {spread:.3e})"


@pytest.mark.xfail(strict=True, reason="m1859 W4: speed factor keeps 50% of its value "
                                       "at Fn=0 (offset artefact)")
def test_zero_speed_drift_force_not_halved():
    h = HullForm(320.0, 58.0, 20.8, 0.82)
    r0 = added_resistance(4.0, 180.0, h, 0.0)
    rref = added_resistance(4.0, 180.0, h, RAW_LAW["fn_ref"] * math.sqrt(G * h.L))
    assert 0.8 <= r0 / rref <= 1.2, f"ratio {r0 / rref:.3f}"


@pytest.mark.xfail(strict=True, reason="m1859 W5: declared 5-15% band holds only for "
                                       "full-form merchants (Ferry 17.6%, Trawler 75.7%)")
def test_declared_band_holds_for_all_ship_types():
    from services.digital_twin.models.resistance import total_resistance

    bad = []
    for name, L, B, T, Cb, Fn in SHIPS:
        h = HullForm(L, B, T, Cb)
        V = Fn * math.sqrt(G * L)
        rc = float(total_resistance(V, L, B, T, Cb, 0.0, 0.0, 0.0, rho=h.rho)["R_calm_N"])
        share = 100.0 * added_resistance(4.0, 180.0, h, V) / rc
        if not (5.0 <= share <= 15.0):
            bad.append(f"{name}={share:.1f}%")
    assert not bad, "outside declared band: " + ", ".join(bad)


@pytest.mark.xfail(strict=True, reason="m1859 W6: the Tp-blind R_AW makes the implied "
                                       "speed loss identical in a 3 s chop and a 14 s "
                                       "swell (0.3071 kn for both)")
def test_speed_loss_depends_on_peak_period():
    h = HullForm(320.0, 58.0, 20.8, 0.82)
    V = 0.137 * math.sqrt(G * h.L)
    vals = [irregular_seastate(4.0, tp, 180.0, h, 12.0, V, n_freq=12,
                               n_sections=21)["R_aw_N"] for tp in TPS]
    spread = (max(vals) - min(vals)) / max(vals)
    assert spread > 0.15, f"speed loss still Tp-blind (spread {spread:.3e})"