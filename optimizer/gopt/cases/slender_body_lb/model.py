"""Case model: slender-body (prolate spheroid) family at fixed displacement.

Domain content lives **here**, not in the G-OPT kernels: the kernels only see
``Objective`` / ``AdjointSolver`` objects built by this module.

Chain
-----
``lambda = L/D``  ->  ``(L, D, S)`` at fixed enclosed volume  ->  resistance
coefficients  ->  objective.

The resistance *value* is taken from the platform solver
``backend/api/domain/resistance_core.py`` (ITTC-57 friction + SUBOFF-calibrated
Froude fit) so there is exactly one source of physics.  The same two formulas are
mirrored locally in a complex-safe form solely to obtain **machine-precision
derivatives by complex-step differentiation**; a regression test pins the mirror
to the platform solver to 1e-12 across the whole design sweep, so the mirror can
not drift.

Geometry family
---------------
Prolate spheroid of revolution, enclosed volume held fixed::

    V   = (pi/6) L D^2                    (fixed)
    L   = (6 V lambda^2 / pi)^(1/3)
    S   = 2 pi b^2 (1 + (a/(b e)) asin(e)),  a = L/2, b = D/2, e = sqrt(1-(b/a)^2)

The family passes through the reference hull's principal dimensions at
``lambda0 = 4.356/0.508 = 8.5748``, where its wetted area matches the published
5.42 m^2 to 1.4 % and its cylindrical block coefficient is exactly 2/3 (above the
Cauchy-Schwarz lower bound of 0.608 implied by the published (L, D, S) triple --
i.e. the family is geometrically admissible, not a free parameter fit).

Form-factor variant
-------------------
The platform solver carries no viscous form factor, so a second objective adds
Hoerner's bodies-of-revolution form factor ``1 + k = 1 + 1.5 (D/L)^1.5 + 7 (D/L)^3``
(Hoerner 1965, ch. 6).  This term is *case level*, flagged as a literature
correlation, and never enters the platform solver.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# platform solver (single source of the physics)
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_PLATFORM_DOMAIN = os.path.join(_REPO_ROOT, "backend", "api", "domain")
if _PLATFORM_DOMAIN not in sys.path:
    sys.path.insert(0, _PLATFORM_DOMAIN)

try:  # pragma: no cover - import path guard
    from resistance_core import solve_resistance as _solve_resistance  # type: ignore
except Exception as exc:  # pragma: no cover
    _solve_resistance = None
    _IMPORT_ERROR = exc

CASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CASE_DIR, "config.json")


def case_artifact_dir() -> str:
    """Worktree-local artifact directory for this case."""
    path = os.path.join(_REPO_ROOT, "artifacts", "gopt", "slender_body_lb")
    os.makedirs(path, exist_ok=True)
    return path


def load_config(path: str = CONFIG_PATH) -> Dict:
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# closed-form geometry family
# ---------------------------------------------------------------------------
def spheroid_wetted_area(L, D):
    """Exact wetted area of a prolate spheroid (complex-safe for ODE/CSD use)."""
    a = L / 2.0
    b = D / 2.0
    e = np.sqrt(1.0 - (b / a) ** 2)
    return 2.0 * np.pi * b ** 2 * (1.0 + (a / (b * e)) * np.arcsin(e))


def length_from_volume(volume: float, lam):
    """L(lambda) for a prolate spheroid at fixed enclosed volume."""
    return (6.0 * volume * lam ** 2 / np.pi) ** (1.0 / 3.0)


@dataclass
class SlenderBodyFamily:
    volume_m3: float
    rho: float = 1025.0
    nu: float = 1.004e-6
    g: float = 9.81
    speed: float = 3.0
    hull_key: str = "SUBOFF"

    # -- geometry ------------------------------------------------------------
    def geometry(self, lam):
        L = length_from_volume(self.volume_m3, lam)
        D = L / lam
        S = spheroid_wetted_area(L, D)
        return L, D, S

    def datum_lambda(self) -> float:
        return 4.356 / 0.508

    def volume_from_datum(self) -> float:
        L, D = 4.356, 0.508
        return float(np.pi / 6.0 * L * D ** 2)

    # -- resistance (platform formulas, complex-safe mirror) ------------------
    def coefficients(self, lam):
        L, D, S = self.geometry(lam)
        re = self.speed * L / self.nu
        cf = 0.075 / (np.log10(re) - 2.0) ** 2
        fn = self.speed / np.sqrt(self.g * L)
        cb = 2.0 / 3.0
        cr = 0.002 * (1.0 + 2.0 * cb) * fn ** 2.5
        ct = cf + cr
        q = 0.5 * self.rho * S * self.speed ** 2
        return {
            "L": L, "D": D, "S": S, "Re": re, "Fn": fn, "Cb": cb,
            "Cf": cf, "Cr": cr, "Ct": ct, "q": q,
            "Rt": q * ct, "Rvis": q * cf, "Rwave": q * cr,
        }

    def form_factor(self, lam):
        r = 1.0 / lam
        return 1.0 + 1.5 * r ** 1.5 + 7.0 * r ** 3.0

    # -- platform cross-check -------------------------------------------------
    def platform_resistance(self, lam: float) -> Dict:
        if _solve_resistance is None:  # pragma: no cover
            raise RuntimeError(f"platform resistance core unavailable: {_IMPORT_ERROR}")
        L, D, S = (float(v) for v in self.geometry(lam))
        return _solve_resistance(
            self.hull_key,
            self.speed,
            overrides={
                "length": L,
                "diameter": D,
                "wetted_surface_m2": S,
                "cb": 2.0 / 3.0,
            },
            rho=self.rho,
            nu=self.nu,
        )

    # -- objectives -----------------------------------------------------------
    def objective_value(self, key: str, lam):
        c = self.coefficients(lam)
        if key == "CD_wetted":
            return c["Ct"]
        if key == "Rt_platform":
            return c["Rt"]
        if key == "Rt_form_factor":
            return c["q"] * (c["Cf"] * self.form_factor(lam) + c["Cr"])
        if key == "Rv_viscous_only":
            return c["q"] * c["Cf"] * self.form_factor(lam)
        raise KeyError(f"unknown objective {key!r}")

    def objective_gradient(self, key: str, lam: float, h: float = 1e-30) -> float:
        """Complex-step derivative (machine precision, no subtractive cancellation)."""
        z = complex(lam, h)
        dz = self.objective_value(key, z)
        return float(np.imag(dz) / h)

    def make_objective(self, key: str) -> Callable[[np.ndarray], float]:
        return lambda x: float(self.objective_value(key, float(np.asarray(x).reshape(-1)[0])))

    def make_gradient(self, key: str) -> Callable[[np.ndarray], np.ndarray]:
        return lambda x: np.array([self.objective_gradient(key, float(np.asarray(x).reshape(-1)[0]))])


def family_from_config(cfg: Optional[Dict] = None) -> SlenderBodyFamily:
    cfg = cfg or load_config()
    datum = cfg["geometry_family"]["datum"]
    vol = float(np.pi / 6.0 * datum["length_m"] * datum["diameter_m"] ** 2)
    design = cfg["design"]
    return SlenderBodyFamily(
        volume_m3=vol,
        rho=float(design["rho_kg_m3"]),
        nu=float(design["nu_m2_s"]),
        g=float(design["g_m_s2"]),
        speed=float(design["speed_ms"]),
    )


# ---------------------------------------------------------------------------
# published reference dataset (canonical platform source, not duplicated here)
# ---------------------------------------------------------------------------
def load_reference_conditions(cfg: Optional[Dict] = None) -> Dict:
    cfg = cfg or load_config()
    ref = cfg["references"]
    path = os.path.join(_REPO_ROOT, ref["source"])
    with open(path) as fh:
        data = json.load(fh)
    wanted = set(ref["case_ids"])
    cases = [c for c in data["cases"] if c.get("case_id") in wanted]
    return {"source_path": ref["source"], "dataset_version": data.get("version"),
            "citation": ref["citation"], "cases": cases}