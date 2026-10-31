"""Physical constants and IMO regulatory tables used by the polar package.

References
----------
* ITTC-1957 model-ship correlation line (friction coefficient).
* Holtrop & Mennen (1982) "An approximate power prediction method",
  International Shipbuilding Progress 29(335).
* IMO MEPC.353(78)  -- 2022 Guidelines on the CII reference lines (G1).
* IMO MEPC.354(78)  -- 2022 Guidelines on the operational carbon intensity
  rating of ships (G4), rating boundaries d1..d4.
* IMO MEPC.364(79)  -- 2022 Guidelines on the method of calculation of the
  attained EEXI; Annex contains the C_F carbon factors used here.
* IMO MEPC.203(62)  -- EEDI reference lines and phase reduction factors.

All quantities are SI unless the name says otherwise.
"""

from __future__ import annotations

# --- physical constants -----------------------------------------------------
RHO_WATER = 1025.0        # sea water density [kg/m3] (15 C, 35 ppt)
RHO_ICE = 917.0           # sea ice density [kg/m3]
G = 9.81                  # gravitational acceleration [m/s2]
NU_WATER = 1.19e-6        # kinematic viscosity of sea water [m2/s]
ICE_YOUNG = 5.0e9         # sea-ice elastic modulus [Pa] (0.5-10 GPa range)
ICE_POISSON = 0.3         # sea-ice Poisson ratio
ICE_LATENT_HEAT = 3.33e5  # latent heat of fusion of ice [J/kg]
ICE_CONDUCTIVITY = 2.2    # thermal conductivity of ice [W/(m K)]
KNOT = 0.514444           # 1 kn in m/s
NM_M = 1852.0             # 1 nautical mile in m

# --- CII (MEPC.353(78) G1 / MEPC.354(78) G4) --------------------------------
#: Reference-line coefficients: CII_ref = a * capacity^c   [g CO2 / (capacity nm)]
#: capacity = deadweight (DWT) for bulk/tanker/container/general cargo.
CII_REFERENCE_LINES = {
    "bulk_carrier": {"a": 4745.0, "c": -0.622},
    "tanker": {"a": 5247.0, "c": -0.610},
    "container_ship": {"a": 1984.0, "c": -0.489},
    "general_cargo": {"a": 31948.0, "c": -1.430},
    "refrigerated_cargo": {"a": 4600.0, "c": -0.557},
    "combination_carrier": {"a": 5119.0, "c": -0.622},
    "gas_carrier_large": {"a": 14405e7, "c": -2.071},
    "gas_carrier_small": {"a": 8104.0, "c": -0.639},
}

#: Annual CII reduction factor Z [%] relative to the 2019 reference line.
CII_REDUCTION_FACTOR = {2023: 5.0, 2024: 7.0, 2025: 9.0, 2026: 11.0, 2030: 11.0}

#: Rating boundaries as a multiplier of the *required* CII (MEPC.354(78) G4).
CII_RATING_BOUNDARIES = {
    "d1": 0.86,   # superior boundary: A if attained <= d1 * required
    "d2": 0.94,   # lower boundary   : B if attained <= d2 * required
    "d3": 1.06,   # upper boundary   : C if attained <= d3 * required
    "d4": 1.18,   # inferior boundary: D if attained <= d4 * required, else E
}

#: Carbon factors C_F [t CO2 / t fuel] (MEPC.364(79) Annex).
CARBON_FACTORS = {
    "diesel_gas_oil": 3.206,
    "light_fuel_oil": 3.151,
    "heavy_fuel_oil": 3.114,
    "lpg_propane": 3.000,
    "lpg_butane": 3.030,
    "lng": 2.750,
    "methanol": 1.375,
}

# --- EEDI (MEPC.203(62)) ----------------------------------------------------
#: EEDI reference line: EEDI_ref = a * DWT^(-c)   [g CO2 / (t nm)]
EEDI_REFERENCE_LINES = {
    "bulk_carrier": {"a": 961.79, "c": 0.477},
    "tanker": {"a": 1218.80, "c": 0.488},
    "container_ship": {"a": 186.51, "c": 0.200},
    "general_cargo": {"a": 107.48, "c": 0.216},
}

#: EEDI phase reduction factors [%] w.r.t. the reference line.
EEDI_PHASE_REDUCTION = {0: 0.0, 1: 10.0, 2: 20.0, 3: 30.0}

# --- numerical tolerances ---------------------------------------------------
REL_TOL_SMALL = 1e-9