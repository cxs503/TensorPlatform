"""Reference data & vessel particulars for wind/current/ice coupled manoeuvring.

Provenance policy
-----------------
Every numeric constant here carries an explicit provenance note so an auditor can
separate

* **physics-defined** constants (density, gravity, ITTC-57 friction line),
* **geometry-derived** constants (projected / wetted areas from principal
  dimensions),
* **literature-shaped** constants (OCIMF-style angle dependence, MMG
  hydrodynamic derivatives), whose *exact reproduction of the original tables* is
  NOT verified offline - see ``docs/m795/LIMITATIONS.md`` and the mission report.

Reference hull
--------------
``VESSEL_VLCC`` is a VLCC-form reference hull built on the KVLCC2 particulars
(L_pp = 320 m, B = 58 m, T = 20.8 m, C_b = 0.8098) already used by the platform
benchmark file ``backend/data/benchmark_reference.json`` (SIMMAN 2008 / ITTC
workshop ship family).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Physical constants (physics-defined)
# ---------------------------------------------------------------------------
RHO_WATER = 1025.0        # kg/m^3, sea water
RHO_AIR = 1.225           # kg/m^3, ISA sea level
GRAVITY = 9.80665         # m/s^2
NU_WATER = 1.1892e-6      # m^2/s, sea water at 15 degC
KNOT = 0.514444           # m/s per knot


# ---------------------------------------------------------------------------
# Vessel particulars
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Vessel:
    """Reference hull particulars and derived geometric quantities."""

    name: str
    L: float               # length between perpendiculars [m]
    B: float               # breadth moulded [m]
    T: float               # mean draft [m]
    Cb: float              # block coefficient [-]
    depth: float           # depth moulded [m]
    A_L: float             # lateral above-water projected area [m^2]
    A_F: float             # frontal above-water projected area [m^2]
    A_R: float             # rudder area [m^2]
    D_prop: float          # propeller diameter [m]
    b_R: float             # rudder span [m]
    k_zz_ratio: float = 0.25   # radius of gyration / L [-]

    @property
    def volume(self) -> float:
        return self.Cb * self.L * self.B * self.T

    @property
    def mass(self) -> float:
        return RHO_WATER * self.volume

    @property
    def S_wetted(self) -> float:
        """Holtrop (1984) simplified wetted-surface estimate [m^2]."""
        cm = 0.98
        cwp = (1.0 + 2.0 * self.Cb) / 3.0
        return (
            self.L
            * (2.0 * self.T + self.B)
            * math.sqrt(cm)
            * (
                0.453
                + 0.4425 * self.Cb
                - 0.2862 * cm
                - 0.003467 * self.B / self.T
                + 0.3696 * cwp
            )
        )

    @property
    def A_MS(self) -> float:
        """Immersed midship section area [m^2]."""
        return self.B * self.T * 0.98

    @property
    def A_LS(self) -> float:
        """Lateral wetted area - reference area of the current model [m^2]."""
        return self.L * self.T * 0.85

    @property
    def I_z(self) -> float:
        """Yaw moment of inertia about midship [kg m^2]."""
        k = self.k_zz_ratio * self.L
        return self.mass * k * k

    def particulars(self) -> Dict[str, float]:
        return {
            "L": self.L, "B": self.B, "T": self.T, "Cb": self.Cb,
            "volume": self.volume, "mass": self.mass,
            "S_wetted": self.S_wetted, "A_MS": self.A_MS,
            "A_LS": self.A_LS, "A_L": self.A_L, "A_F": self.A_F,
            "A_R": self.A_R, "I_z": self.I_z,
        }


VESSEL_VLCC = Vessel(
    name="VLCC-320 reference hull (KVLCC2 particulars)",
    L=320.0,
    B=58.0,
    T=20.8,
    Cb=0.8098,
    depth=30.0,
    A_L=1500.0,
    A_F=500.0,
    A_R=0.0117 * 320.0 * 20.8,   # A_R/(L T) = 0.0117, typical VLCC
    D_prop=8.5,
    b_R=8.3,
)


# ---------------------------------------------------------------------------
# Wind: OCIMF-style non-dimensional coefficients
# ---------------------------------------------------------------------------
# Convention (OCIMF 1994, "Prediction of Wind and Current Loads on VLCCs"):
#   X_wind = 0.5 rho_air V_R^2 C_X(psi) A_L
#   Y_wind = 0.5 rho_air V_R^2 C_Y(psi) A_L
#   N_wind = 0.5 rho_air V_R^2 C_N(psi) A_L L
# psi = relative wind angle, 0 deg = wind from ahead, 90 deg = from starboard.
#
# Anchor values: the shape follows the OCIMF loaded-tanker curves and is pinned
# on two independently documented values
#   * psi = 90 deg: 0.5 rho_air C_Y(90) = 0.504 (IMO IS Code 2008 beam-wind
#     pressure P = 0.504 V^2 Pa on the lateral above-water area), i.e. C_Y(90) =
#     0.823 for rho_air = 1.225;
#   * psi = 0 deg: C_X(0) A_L = C_AF A_F with the flat-plate frontal drag
#     coefficient C_AF = 0.80 (bluff flat plate, Re > 1e5).
# A Fourier series is least-squares fitted to these anchors at import time, so
# the symmetry invariants C_X(psi)=C_X(-psi) (even) and C_Y, C_N odd hold
# exactly.
WIND_ANCHORS: Tuple[Tuple[float, float, float, float], ...] = (
    # psi_deg, C_X, C_Y, C_N
    (0.0, 0.270, 0.000, 0.000),
    (30.0, 0.205, 0.320, 0.038),
    (45.0, 0.140, 0.440, 0.053),
    (60.0, 0.080, 0.570, 0.069),
    (90.0, 0.020, 0.823, 0.099),
    (135.0, -0.140, 0.440, 0.050),
    (180.0, -0.220, 0.000, 0.000),
)

WIND_ANCHOR_PROVENANCE = (
    "psi=90 deg pinned on IMO IS Code 2008 beam-wind pressure P = 0.504 V^2; "
    "psi=0 deg pinned on flat-plate frontal drag C_AF = 0.80 acting on A_F; "
    "intermediate anchors follow the OCIMF 1994 loaded-tanker curve shape "
    "(not machine-verified against the guide tables - see LIMITATIONS)"
)

# Flat-plate drag coefficients of the *independent* projected-area wind model
# used as validation reference (textbook values).
C_D_FRONTAL = 0.80        # blunt frontal face / deckhouse front
C_D_LATERAL = 0.85        # lateral above-water area (hull side + deckhouse)
X_CE_RATIO = -0.12        # centre of windage relative to midship /L (aft < 0)
IMO_WIND_PRESSURE_COEF = 0.504   # Pa per (m/s)^2, IMO IS Code 2008


# ---------------------------------------------------------------------------
# Current
# ---------------------------------------------------------------------------
# Convention:
#   X_cur = 0.5 rho_water V_c^2 C_XC(psi_c) A_MS
#   Y_cur = 0.5 rho_water V_c^2 C_YC(psi_c) A_LS
#   N_cur = 0.5 rho_water V_c^2 C_NC(psi_c) A_LS L
C_D_CROSSFLOW = 1.00      # 2D cross-flow drag of a ship section (lit. 1.0-1.2)
X_CP_RATIO = -0.10        # centre of lateral current force /L (aft < 0)
CURRENT_ANCHORS: Tuple[Tuple[float, float, float, float], ...] = (
    # psi_deg, C_XC, C_YC, C_NC
    (0.0, -0.040, 0.000, 0.000),
    (30.0, -0.030, 0.238, 0.024),
    (45.0, -0.020, 0.475, 0.048),
    (60.0, -0.010, 0.713, 0.071),
    (90.0, 0.000, 0.950, 0.095),
    (135.0, 0.020, 0.475, 0.048),
    (180.0, 0.040, 0.000, 0.000),
)
CURRENT_ANCHOR_PROVENANCE = (
    "pinned on strip cross-flow drag (C_D = 1.1 on the lateral wetted area) and "
    "ITTC-57 friction for the axial component; OCIMF current-curve shape; not "
    "machine-verified against the guide tables - see LIMITATIONS"
)


def ittc57_cf(reynolds: float) -> float:
    """ITTC-57 friction coefficient (physics-defined)."""
    if reynolds <= 0:
        raise ValueError("Reynolds number must be positive")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


# ---------------------------------------------------------------------------
# Ice: level-ice condition + Riska-type coefficient set
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IceCondition:
    """Level-ice condition for the Riska-type resistance model."""

    thickness: float                 # h [m]
    sigma_flexural: float = 0.5e6    # flexural strength [Pa] (0.3-0.7 MPa)
    sigma_crushing: float = 3.0e6    # crushing strength [Pa] (1.9-5.6 MPa)
    rho_ice: float = 900.0           # kg/m^3
    mu: float = 0.15                 # ship-ice friction (0.05-0.3)
    stem_angle: float = 30.0         # waterline stem angle [deg]

    @property
    def density_deficit(self) -> float:
        """(rho_w - rho_i)/rho_w - buoyancy deficit driving submergence."""
        return (RHO_WATER - self.rho_ice) / RHO_WATER

    def as_dict(self) -> Dict[str, float]:
        return {
            "thickness": self.thickness,
            "sigma_flexural": self.sigma_flexural,
            "sigma_crushing": self.sigma_crushing,
            "rho_ice": self.rho_ice,
            "mu": self.mu,
            "stem_angle": self.stem_angle,
        }


# Riska-type semi-empirical decomposition coefficient set.
#   R_i = (C1 + C2 V) rho_i g h^2 B + C3 sigma_f h^2 + C4 mu rho_i g h^2 B
# Scaling laws (verified by tests): R ~ h^2, R linear in sigma_f through C3,
# R linear in B, monotone increasing in V and mu.
RISKA_COEFFICIENTS = {
    "C1": 0.15,     # velocity-independent crushing/breaking part [-]
    "C2": 0.30,     # velocity-dependent part [s/m]
    "C3": 0.60,     # flexural-strength dominated breaking part [-]
    "C4": 0.10,     # friction / sliding part [-]
    "provenance": (
        "Riska (1989)-type crushing + breaking + submergence + friction "
        "decomposition; coefficient magnitudes selected inside the published "
        "engineering ranges to reproduce 0.3-1.5 MN for h = 0.5-1.5 m at "
        "0-2 m/s for a 320 m hull. Not a coefficient-exact reproduction of the "
        "original paper (paper unavailable offline) - see LIMITATIONS"
    ),
}

# Independent cross-check model (Lindqvist-type decomposition, different
# structure and constants) used to bound the ice-resistance uncertainty.
LINDQVIST_LIKE_COEFFICIENTS = {
    "k_break": 0.62,     # sigma_f h^2 (tan(alpha) + mu/cos(alpha))
    "k_sub": 0.42,       # rho_i g h^2 B buoyancy deficit
    "k_fric": 0.55,      # mu rho_i g h B T / (1 + ...) sliding part
}