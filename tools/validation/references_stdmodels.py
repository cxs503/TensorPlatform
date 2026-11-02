"""Literature reference values for the `stdmodels.lookup` capability audit (m1876).

Every number here is taken from a **published** source, never from the platform under
test.  The point of this module is to be an *independent* yardstick: it is imported by
`tools/validation/verify_stdmodels_lookup.py` and by
`tests/test_stdmodels_lookup_verification.py`, and it must stay free of any value that
was read back from `:8001`.

Sources
-------
[KCS]  MOERI/KRISO container ship KCS — benchmark geometry as distributed for the
       2010 Workshop on CFD in Ship Hydrodynamics (Gothenburg/Tokyo series) and the
       ITTC 7.5-03-02-03 benchmark list.  Lpp = 230.0 m, B = 32.2 m, T = 10.8 m,
       Cb = 0.6505, displacement volume = 52 030.2 m^3, wetted area (no rudder)
       = 9 424.6 m^2.
[KVLCC2] MOERI tanker KVLCC2 — same workshop series.  Lpp = 320.0 m, B = 58.0 m,
       T = 20.8 m, Cb = 0.8098, displacement volume = 312 622 m^3,
       wetted area = 27 194 m^2.
[5415] DTMB 5415 (DDG-51 type, US Navy) — Lpp = 142.0 m, B = 19.06 m, T = 6.15 m,
       Cb = 0.506; the ONR/ITTC benchmark reports a displacement of 8 637 t
       (≈ 8 429 m^3 of displacement volume at rho = 1025 kg/m^3).
[4119] DTMB 4119 three-bladed propeller — D = 0.3048 m (1 ft), P/D = 1.084,
       A_E/A_O = 0.60, r_h/R = 0.20 (Jessup 1989 / ITTC propeller benchmark).

Reference densities
-------------------
Sea water at 15 °C: rho = 1025 kg/m^3 (ITTC recommended value).  Fresh water at 15 °C
(model tanks): rho ≈ 999.1 kg/m^3, rounded to 1000 kg/m^3 for classification purposes.
"""

from __future__ import annotations

RHO_SEAWATER = 1025.0  # kg/m^3, ITTC 15 degC
RHO_FRESHWATER = 1000.0  # kg/m^3, model-tank water

#: Relative-error tolerance for a *pass* on a key quantity (owner standing rule).
TOL_PASS = 0.03
#: Above this relative error a value is not acceptable at all (owner standing rule).
TOL_REJECT = 0.15
#: Absolute tolerance for the Cb = Cm * Cp identity.
TOL_CB_ABS = 5.0e-3
#: Implied mass density: warn band / hard band (relative).
TOL_DENSITY_WARN = 0.05
TOL_DENSITY_REL = TOL_REJECT
#: A hull is treated as model-scale (fresh water) below this length.
MODEL_SCALE_LPP_MAX = 10.0


#: Benchmark ships: field -> (published value, source).  ``displacement_volume`` is the
#: displaced volume of water in m^3 and is compared against ``displacement / rho``.
SHIP_ANCHORS = {
    "KCS": {
        "Lpp": (230.0, "[KCS]"),
        "B": (32.2, "[KCS]"),
        "T": (10.8, "[KCS]"),
        "Cb": (0.6505, "[KCS]"),
        "displacement_volume": (52030.2, "[KCS]"),
    },
    "KVLCC2": {
        "Lpp": (320.0, "[KVLCC2]"),
        "B": (58.0, "[KVLCC2]"),
        "T": (20.8, "[KVLCC2]"),
        "Cb": (0.8098, "[KVLCC2]"),
        "displacement_volume": (312622.0, "[KVLCC2]"),
    },
    "DTMB 5415": {
        "Lpp": (142.0, "[5415]"),
        "B": (19.06, "[5415]"),
        "T": (6.15, "[5415]"),
        "Cb": (0.506, "[5415]"),
        "displacement_volume": (8429.0, "[5415]"),
    },
}

#: Propeller anchors are dimensional, so they are compared against the propeller list
#: (``standard-models/propellers``).  The registry currently exposes no propeller
#: columns at all, so these anchors resolve to WITHHELD by construction.
PROPELLER_ANCHORS = {
    "DTMB 4119": {"diameter": (0.3048, "[4119]"), "P_over_D": (1.084, "[4119]"),
                  "EAR": (0.60, "[4119]")},
}
#: Column names that would carry propeller particulars if the registry had a schema.
PROPELLER_COLUMNS = ("diameter", "D", "P_over_D", "EAR", "A_E_A_O", "blades", "Z")

#: Spectra that a ship-motion solver is expected to know (ITTC 7.5-02-07-01.2 and the
#: classical Ochi-Hubble / Bretschneider formulations).  The registry must be a subset.
KNOWN_SPECTRA = frozenset({"jonswap", "pm", "ittc", "ochi_hubble", "bretschneider"})

#: Regulation *codes* published by IMO / class societies that the registry claims.
KNOWN_REGULATION_CODES = {"IS Code 2008", "SOLAS Ch.II-1", "EEDI/EEXI",
                          "DNV-RU-SHIP", "CCS钢质海船"}


def reference_density(length_m: float) -> float:
    """Expected mass density (t/m^3) for a hull of the given length."""
    if length_m <= MODEL_SCALE_LPP_MAX:
        return RHO_FRESHWATER / 1000.0
    return RHO_SEAWATER / 1000.0


def rel_error(value: float, reference: float) -> float:
    """Signed relative error of ``value`` w.r.t. ``reference`` (0 when both are 0)."""
    if reference == 0.0:
        return 0.0 if value == 0.0 else float("inf")
    return (value - reference) / reference


def unit_scale_diagnosis(ratio: float) -> str | None:
    """Diagnose a density-ratio anomaly as a unit / decimal-point defect.

    ``ratio = implied_density / expected_density``.  A ratio near 1000 means the same
    mass was written in kg instead of tonnes; a ratio near 10^k (k != 0) means a
    decimal-point slip in the stored displacement.
    """
    for k in (3, 2, 1, -1, -2, -3):
        if abs(ratio - 10 ** k) / 10 ** k < 0.05:
            return f"x{10 ** k}"
    return None