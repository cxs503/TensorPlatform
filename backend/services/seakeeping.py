"""Seakeeping (耐波性) reference core — frequency-domain motion response + wave spectra.

Why this module exists
----------------------
The ship-performance service exposes seakeeping endpoints whose physics could not be
verified (see ``docs/audits/seakeeping_verification_m1433.md``): the legacy
``/seakeeping/rao`` curve ignores ``wave_frequency``/``heading``/``speed``, returns an
identical pitch curve to heave, and ``/seakeeping/motion`` returns hard-coded amplitudes.
This module is an independent, *anchored* implementation: every quantity that has a
closed form (dispersion, encounter frequency, long-wave limits, resonant peak,
high-frequency decay, spectral moments) is computed from the closed form or reproduced
by the model within machine/stated accuracy, and the anchors are asserted in
``backend/tests/feat/test_ship_seakeeping.py``.

Model class and its honest limits
---------------------------------
Single-degree-of-freedom linear response per mode with hydrostatic restoring and
*parameterised* radiation coefficients::

    (M + A) * eta'' + B * eta' + C * eta = F(t)

* restoring  C33 = rho*g*A_w,  C55 = rho*g*nabla*GM_L,  C44 = m*g*GM_T   (hydrostatics, exact for small angles)
* added mass  A33 = kappa33*m,  A55 = kappa55*I55,  A44 = kappa44*I44      (inputs, NOT regressed here)
* damping     B = 2*zeta*sqrt(C*(M+A))                                     (inputs: damping ratios)
* excitation  Froude-Krylov + Smith correction e^{-kT} + lengthwise phasing factor
  Gamma(u), u = k*L*|cos(beta)|/2, exact for a parabolic waterplane.

Consequences that make the anchors coefficient-independent:

* long-wave limit  omega -> 0  =>  heave RAO -> 1, pitch/slope -> 1, roll/(slope*cos b) -> 1,
  for *any* kappa/zeta (this is what the legacy service violates by clamping to 0.5);
* high-frequency limit  omega -> inf  =>  RAO ~ C/((M+A) omega_e^2)  (log-log slope -2);
* resonant peak of the SDOF transfer 1/sqrt((1-r^2)^2+(2 zeta r)^2) is exactly
  1/(2 zeta sqrt(1-zeta^2)) at r = sqrt(1-2 zeta^2).

Absolute accuracy against model tests is NOT claimed: added-mass and damping ratios are
switchable inputs with conventional defaults, so the evidence grade of this module is
REAL for the model / WITHHELD for experimental validation. See ``ACCURACY_NOTE``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

try:  # numpy>=2 removed np.trapz; keep a single integration helper
    import numpy as _np

    _trapezoid = getattr(_np, "trapezoid", None) or getattr(_np, "trapz")
except Exception:  # pragma: no cover - numpy is a hard dependency of the platform
    _np = None
    _trapezoid = None

G = 9.80665  # m/s^2, standard gravity

ACCURACY_NOTE = (
    "REAL model / WITHHELD experimental accuracy: linear SDOF frequency-domain response "
    "with hydrostatic restoring from Cb/Cwp/L/B/T (exact for small angles) and "
    "*parameterised* added-mass / damping ratios (defaults are conventional magnitudes, "
    "not fitted to this hull). Analytic anchors (long-wave limit, resonance peak, "
    "high-frequency decay, dispersion, spectral moments) are machine-verified; absolute "
    "RAO accuracy against model tests is NOT verified — no tank data is bundled."
)

# ---------------------------------------------------------------------------
# Wave kinematics (closed forms)
# ---------------------------------------------------------------------------


def wave_number(omega: float) -> float:
    """Deep-water dispersion relation k = omega^2 / g (exact in deep water)."""
    if omega < 0.0:
        raise ValueError("omega must be non-negative")
    return omega * omega / G


def deep_water_wavelength(omega: float) -> float:
    """lambda = 2*pi*g/omega^2."""
    if omega <= 0.0:
        raise ValueError("omega must be positive")
    return 2.0 * math.pi * G / (omega * omega)


def encounter_frequency(omega: float, speed: float, heading_deg: float) -> float:
    """Wave encounter frequency.

    Convention (identical to the ship-performance service): heading 0 deg = following
    seas, 180 deg = head seas.

        omega_e = omega - k*U*cos(beta),      k = omega^2/g

    Head seas therefore raise the encounter frequency. In strong following seas the
    expression can become zero or negative; that branch is outside the validity of the
    model and ``encounter_frequency`` returns the raw value while
    :func:`encounter_frequency_safe` flags it.
    """
    k = wave_number(omega)
    return omega - k * speed * math.cos(math.radians(heading_deg))


def encounter_frequency_safe(omega: float, speed: float, heading_deg: float) -> Tuple[float, bool]:
    """Return ``(omega_e, valid)``; ``valid`` is False when omega_e <= 0."""
    we = encounter_frequency(omega, speed, heading_deg)
    return we, we > 0.0


def waterplane_length_factor(u: float) -> float:
    """Lengthwise phasing factor for a parabolic waterplane.

    For ``b(x) = B_w * (1 - (2x/L)^2)`` on ``x in [-L/2, L/2]``::

        Gamma(u) = (1/A_w) * Int b(x) exp(i k x cos b) dx = 3 (sin u - u cos u) / u^3,
        u = k L |cos b| / 2

    Exact for that waterplane; ``Gamma(0) = 1`` (checked by series in the small-u branch).
    """
    au = abs(u)
    if au < 1.0e-3:
        u2 = au * au
        return 1.0 - u2 / 10.0 + u2 * u2 / 280.0 - u2 * u2 * u2 / 15120.0
    return 3.0 * (math.sin(au) - au * math.cos(au)) / (au ** 3)


def beam_factor(omega: float, beam: float, heading_deg: float) -> float:
    """Across-beam wave-elevation factor sinc(q) = sin(q)/q, q = k B |sin b| / 2.

    Long-wave limit 1 (q -> 0); reduces heave excitation in beam seas where the wave
    elevation varies across the breadth. Exact for a rectangular section waterplane.
    """
    q = 0.5 * wave_number(omega) * beam * abs(math.sin(math.radians(heading_deg)))
    if q < 1.0e-9:
        return 1.0
    return math.sin(q) / q


def smith_factor(omega: float, draft: float) -> float:
    """Smith correction exp(-k*T) (constant-draft approximation)."""
    return math.exp(-wave_number(omega) * draft)


def transfer(r: float, zeta: float) -> float:
    """SDOF magnification |H| = 1/sqrt((1-r^2)^2 + (2 zeta r)^2), r = omega_e/omega_n."""
    return 1.0 / math.sqrt((1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2)


def transfer_peak(zeta: float) -> Tuple[float, float]:
    """(r_peak, |H|_peak) = (sqrt(1-2 zeta^2), 1/(2 zeta sqrt(1-zeta^2))), zeta < 1/sqrt(2)."""
    if not (0.0 < zeta < 1.0 / math.sqrt(2.0)):
        raise ValueError("peak of |H| exists only for 0 < zeta < 1/sqrt(2)")
    r_peak = math.sqrt(1.0 - 2.0 * zeta * zeta)
    return r_peak, 1.0 / (2.0 * zeta * math.sqrt(1.0 - zeta * zeta))


# ---------------------------------------------------------------------------
# Hydrostatics + response model
# ---------------------------------------------------------------------------


@dataclass
class SeakeepingModel:
    """Linear seakeeping model for one hull and one loading condition.

    Geometry is validated first (platform rule: Cb/L/B must close), then hydrostatics
    follow, then the SDOF modal parameters.
    """

    L: float
    B: float
    T: float
    Cb: float
    Cwp: float
    displacement_t: float
    KG: float
    GM_T: float
    rho: float = 1025.0
    KB: Optional[float] = None
    k_yy: float = 0.25          # pitch radius of gyration / L (convention 0.24-0.26)
    k_xx: float = 0.35          # roll radius of gyration / B (convention 0.32-0.40)
    kappa33: float = 0.80       # heave added mass / ship mass
    kappa55: float = 0.25       # pitch added inertia / I55
    kappa44: float = 0.35       # roll added inertia / I44
    zeta33: float = 0.20        # heave damping ratio
    zeta55: float = 0.12        # pitch damping ratio
    zeta44: float = 0.10        # roll damping ratio (<0.1 is typical for bare hulls)
    geometry_tolerance: float = 0.05

    # filled in __post_init__
    mass: float = field(init=False, default=0.0)
    volume: float = field(init=False, default=0.0)
    volume_from_cb: float = field(init=False, default=0.0)
    geometry_residual: float = field(init=False, default=0.0)
    A_w: float = field(init=False, default=0.0)
    I_L: float = field(init=False, default=0.0)
    BM_L: float = field(init=False, default=0.0)
    KB_used: float = field(init=False, default=0.0)
    GM_L: float = field(init=False, default=0.0)
    C33: float = field(init=False, default=0.0)
    C55: float = field(init=False, default=0.0)
    C44: float = field(init=False, default=0.0)
    A33: float = field(init=False, default=0.0)
    A55: float = field(init=False, default=0.0)
    A44: float = field(init=False, default=0.0)
    I55: float = field(init=False, default=0.0)
    I44: float = field(init=False, default=0.0)
    omega3: float = field(init=False, default=0.0)
    omega5: float = field(init=False, default=0.0)
    omega4: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        # --- geometry gate (Cb / L / B / T must close on displacement) ---
        if self.L <= 0 or self.B <= 0 or self.T <= 0:
            raise ValueError("L, B, T must be positive")
        if not (0.20 <= self.Cb <= 1.0):
            raise ValueError(f"Cb={self.Cb} outside physical range [0.20, 1.00]")
        if not (0.40 <= self.Cwp <= 1.0):
            raise ValueError(f"Cwp={self.Cwp} outside physical range [0.40, 1.00]")
        if self.T > self.L / 5.0:
            raise ValueError("draft/length ratio implausible (T > L/5)")
        if self.displacement_t <= 0 or self.rho <= 0 or self.GM_T <= 0:
            raise ValueError("displacement, density and GM must be positive")

        self.mass = self.displacement_t * 1000.0
        self.volume = self.mass / self.rho
        self.volume_from_cb = self.Cb * self.L * self.B * self.T
        self.geometry_residual = self.volume / self.volume_from_cb - 1.0
        if abs(self.geometry_residual) > self.geometry_tolerance:
            raise ValueError(
                "geometry inconsistent: nabla/(Cb*L*B*T) - 1 = "
                f"{self.geometry_residual:+.3%} exceeds {self.geometry_tolerance:.1%}; "
                "fix Cb / L / B / T / displacement before any RAO is produced"
            )

        # --- hydrostatics ---
        self.A_w = self.Cwp * self.L * self.B
        self.I_L = self.Cwp * self.B * self.L ** 3 / 12.0     # transverse waterplane inertia about midship
        self.BM_L = self.I_L / self.volume
        self.KB_used = 0.55 * self.T if self.KB is None else self.KB
        self.GM_L = self.KB_used + self.BM_L - self.KG

        self.C33 = self.rho * G * self.A_w
        self.C55 = self.rho * G * self.volume * self.GM_L
        self.C44 = self.mass * G * self.GM_T
        if self.C55 <= 0.0:
            raise ValueError("negative longitudinal metacentric height: GM_L <= 0 (unstable in pitch)")

        self.I55 = self.mass * (self.k_yy * self.L) ** 2
        self.I44 = self.mass * (self.k_xx * self.B) ** 2
        self.A33 = self.kappa33 * self.mass
        self.A55 = self.kappa55 * self.I55
        self.A44 = self.kappa44 * self.I44

        self.omega3 = math.sqrt(self.C33 / (self.mass + self.A33))
        self.omega5 = math.sqrt(self.C55 / (self.I55 + self.A55))
        self.omega4 = math.sqrt(self.C44 / (self.I44 + self.A44))

    # -- natural periods (s) --
    def natural_periods(self) -> Dict[str, float]:
        return {
            "heave": 2.0 * math.pi / self.omega3,
            "pitch": 2.0 * math.pi / self.omega5,
            "roll": 2.0 * math.pi / self.omega4,
        }

    def hydrostatics(self) -> Dict[str, float]:
        return {
            "displacement_m3": self.volume,
            "displacement_from_cb_m3": self.volume_from_cb,
            "geometry_residual_pct": 100.0 * self.geometry_residual,
            "waterplane_area_m2": self.A_w,
            "transverse_wp_inertia_m4": self.I_L,
            "BM_L_m": self.BM_L,
            "KB_m": self.KB_used,
            "GM_L_m": self.GM_L,
            "GM_T_m": self.GM_T,
        }

    # -- RAO at one encounter condition (per unit wave amplitude) --
    def rao_at(self, omega: float, heading_deg: float = 180.0, speed: float = 0.0) -> Dict[str, float]:
        """Amplitudes per unit wave amplitude a (a = 1 m by construction)."""
        if omega <= 0.0:
            raise ValueError("omega must be positive")
        k = wave_number(omega)
        cosb = math.cos(math.radians(heading_deg))
        u = 0.5 * k * self.L * abs(cosb)
        gamma_len = waterplane_length_factor(u)
        gamma_beam = beam_factor(omega, self.B, heading_deg)
        smith = smith_factor(omega, self.T)
        we, valid = encounter_frequency_safe(omega, speed, heading_deg)

        # Excitation amplitudes, long-wave Froude-Krylov class:
        #   heave  -> C33 * exp(-kT) * sinc(kB sin(b)/2)      (no lengthwise phasing)
        #   pitch  -> C55 * k * exp(-kT) * Gamma(kL cos(b)/2) (slope driven, needs phasing)
        #   roll   -> C44 * k*|cos(b)| * exp(-kT) * Gamma
        # Heave deliberately omits the lengthwise phasing: with FK only (diffraction
        # neglected) that factor over-cancels for lambda <~ L and would drive the
        # resonant heave peak towards zero (a well-known FK-only artefact). Documented
        # validity: quantitative for lambda >~ L, qualitative trend below that.
        f3 = self.C33 * gamma_beam * smith             # N per m of wave amplitude
        m5 = self.C55 * k * gamma_len * smith          # N*m per m
        m4 = self.C44 * k * abs(cosb) * gamma_len * smith

        # (mass + A) * omega_n^2 == C (hydrostatic restoring), so the non-dimensional
        # response is (forcing / C) * |H(r)| and the long-wave limits are transparent:
        #   heave: f3/C33 -> 1 ; pitch: m5/C55 -> k ; roll: m4/C44 -> k*|cos b|
        r3 = we / self.omega3
        r5 = we / self.omega5
        r4 = we / self.omega4
        rao3 = (f3 / self.C33) * transfer(r3, self.zeta33)
        rao5 = (m5 / self.C55) * transfer(r5, self.zeta55)
        rao4 = (m4 / self.C44) * transfer(r4, self.zeta44)

        return {
            "omega": omega,
            "omega_e": we,
            "encounter_valid": valid,
            "wave_number": k,
            "gamma_len": gamma_len,
            "gamma_beam": gamma_beam,
            "excitation_model": "fk_longwave(heave: no lengthwise phasing)",
            "smith": smith,
            "heave_rao_m_per_m": rao3,
            "pitch_rao_deg_per_m": math.degrees(rao5),
            "pitch_rao_rad_per_rad": rao5 / k,          # normalised by wave slope
            "roll_rao_deg_per_m": math.degrees(rao4),
            "roll_rao_rad_per_rad": rao4 / (k * abs(cosb)) if abs(cosb) > 1e-9 else float("nan"),
        }

    def rao_curve(
        self,
        omegas: Sequence[float],
        heading_deg: float = 180.0,
        speed: float = 0.0,
    ) -> Dict[str, List[float]]:
        """RAO curves over a frequency grid (per unit wave amplitude / wave slope)."""
        out: Dict[str, List[float]] = {
            "omega": [],
            "omega_e": [],
            "heave_rao_m_per_m": [],
            "pitch_rao_deg_per_m": [],
            "pitch_rao_rad_per_rad": [],
            "roll_rao_deg_per_m": [],
            "roll_rao_rad_per_rad": [],
        }
        for w in omegas:
            row = self.rao_at(w, heading_deg=heading_deg, speed=speed)
            out["omega"].append(w)
            out["omega_e"].append(row["omega_e"])
            out["heave_rao_m_per_m"].append(row["heave_rao_m_per_m"])
            out["pitch_rao_deg_per_m"].append(row["pitch_rao_deg_per_m"])
            out["pitch_rao_rad_per_rad"].append(row["pitch_rao_rad_per_rad"])
            out["roll_rao_deg_per_m"].append(row["roll_rao_deg_per_m"])
            out["roll_rao_rad_per_rad"].append(row["roll_rao_rad_per_rad"])
        return out

    def peaks(self, omegas: Sequence[float], heading_deg: float = 180.0, speed: float = 0.0) -> Dict[str, Dict[str, float]]:
        """Grid peak of each mode (grid resolution is reported so the reader can judge bias)."""
        curve = self.rao_curve(omegas, heading_deg=heading_deg, speed=speed)
        res: Dict[str, Dict[str, float]] = {}
        for key, mode in (
            ("heave_rao_m_per_m", "heave"),
            ("pitch_rao_rad_per_rad", "pitch_slope"),
            ("roll_rao_rad_per_rad", "roll_slope"),
        ):
            vals = curve[key]
            if not vals:
                continue
            i = max(range(len(vals)), key=lambda j: vals[j])
            res[mode] = {"omega_peak": curve["omega"][i], "peak_value": vals[i]}
        res["grid_resolution_rad_s"] = {"value": (omegas[1] - omegas[0]) if len(omegas) > 1 else 0.0}
        return res


def uniform_grid(omega_min: float, omega_max: float, n: int) -> List[float]:
    """Inclusive uniform frequency grid (n >= 2)."""
    if n < 2:
        raise ValueError("n must be >= 2")
    step = (omega_max - omega_min) / (n - 1)
    return [omega_min + i * step for i in range(n)]


# ---------------------------------------------------------------------------
# Wave spectra (scaled so that 4*sqrt(m0) = Hs exactly by construction)
# ---------------------------------------------------------------------------


def _integrate(y: Sequence[float], x: Sequence[float]) -> float:
    if _trapezoid is not None:
        return float(_trapezoid(list(y), list(x)))
    total = 0.0
    for i in range(1, len(x)):
        total += 0.5 * (y[i] + y[i - 1]) * (x[i] - x[i - 1])
    return total


def ittc_shape(omega: Sequence[float], T1: float) -> List[float]:
    """ITTC/ISSC two-parameter shape S = A omega^-5 exp(-B omega^-4), A=173Hs^2/T1^4, B=691/T1^4.

    Returned *unscaled in Hs* (A carries Hs^2): callers that want an exact target Hs
    should use :func:`ittc_spectrum`, where the A/(4B) = m0 identity is preserved.
    """
    B = 691.0 / T1 ** 4
    return [(173.0 / T1 ** 4) * w ** -5 * math.exp(-B * w ** -4) if w > 0 else 0.0 for w in omega]


def ittc_m0_closed(A: float, B: float) -> float:
    """Closed form m0 = A/(4B) for S = A omega^-5 exp(-B omega^-4)."""
    return A / (4.0 * B)


def ittc_m2_closed(A: float, B: float) -> float:
    """Closed form m2 = A*sqrt(pi)/(4*sqrt(B)) for S = A omega^-5 exp(-B omega^-4)."""
    return A * math.sqrt(math.pi) / (4.0 * math.sqrt(B))


def pm_shape(omega: Sequence[float], Tp: float) -> List[float]:
    """Pierson-Moskowitz shape (unit amplitude factor): g^2 omega^-5 exp(-1.25 (wp/w)^4)."""
    wp = 2.0 * math.pi / Tp
    return [
        G ** 2 * w ** -5 * math.exp(-1.25 * (wp / w) ** 4) if w > 0 else 0.0 for w in omega
    ]


def jonswap_shape(omega: Sequence[float], Tp: float, gamma: float = 3.3) -> List[float]:
    """JONSWAP shape (unit amplitude factor), sigma = 0.07 (w<=wp) / 0.09 (w>wp)."""
    wp = 2.0 * math.pi / Tp
    out = []
    for w in omega:
        if w <= 0:
            out.append(0.0)
            continue
        sigma = 0.07 if w <= wp else 0.09
        r = math.exp(-0.5 * ((w - wp) / (sigma * wp)) ** 2)
        out.append(G ** 2 * w ** -5 * math.exp(-1.25 * (wp / w) ** 4) * gamma ** r)
    return out


def _scale_to_hs(shape: Sequence[float], omega: Sequence[float], Hs: float) -> Tuple[List[float], float]:
    m0_shape = _integrate(shape, omega)
    if m0_shape <= 0.0:
        raise ValueError("degenerate spectral shape over the requested grid")
    alpha = (Hs * Hs / 16.0) / m0_shape
    return [alpha * s for s in shape], alpha


def spectrum(
    kind: str,
    Hs: float,
    Tp: float,
    omega: Sequence[float],
    gamma: float = 3.3,
) -> Dict[str, object]:
    """Spectral density scaled so that 4*sqrt(m0) == Hs (machine-exact up to quadrature)."""
    if Hs <= 0 or Tp <= 0:
        raise ValueError("Hs and Tp must be positive")
    kind = kind.lower()
    if kind in ("pm", "pierson-moskowitz"):
        shape = pm_shape(omega, Tp)
    elif kind in ("jonswap", "jswp"):
        shape = jonswap_shape(omega, Tp, gamma)
    elif kind in ("ittc", "issc"):
        shape = ittc_shape(omega, Tp)
    else:
        raise ValueError(f"unknown spectrum kind {kind!r} (pm | jonswap | ittc)")

    S, alpha = _scale_to_hs(shape, omega, Hs)
    m0 = _integrate(S, omega)
    m2 = _integrate([s * w * w for s, w in zip(S, omega)], omega)
    m1 = _integrate([s * w for s, w in zip(S, omega)], omega)
    return {
        "kind": kind,
        "omega": list(omega),
        "S": S,
        "alpha_scale": alpha,
        "m0": m0,
        "m1": m1,
        "m2": m2,
        "Hs_recovered": 4.0 * math.sqrt(m0),
        "Hs_target": Hs,
        "Tz_s": 2.0 * math.pi * math.sqrt(m0 / m2) if m2 > 0 else float("nan"),
        "Tm01_s": 2.0 * math.pi * m0 / m1 if m1 > 0 else float("nan"),
    }


# ---------------------------------------------------------------------------
# Response statistics (RAO x spectrum)
# ---------------------------------------------------------------------------


def response_statistics(
    model: SeakeepingModel,
    spec: Dict[str, object],
    heading_deg: float = 180.0,
    speed: float = 0.0,
) -> Dict[str, float]:
    """Spectral moments of the response: sigma = sqrt(int RAO^2 S dw), significant = 2 sigma."""
    omegas = spec["omega"]
    S = spec["S"]
    curve = model.rao_curve(omegas, heading_deg=heading_deg, speed=speed)
    out: Dict[str, float] = {}
    for key, name, scale in (
        ("heave_rao_m_per_m", "heave", 1.0),
        ("pitch_rao_rad_per_rad", "pitch", 180.0 / math.pi),
        ("roll_rao_rad_per_rad", "roll", 180.0 / math.pi),
    ):
        dens = [r * r * s for r, s in zip(curve[key], S)]
        m0 = _integrate(dens, omegas)
        m2 = _integrate([d * w * w for d, w in zip(dens, omegas)], omegas)
        sigma = math.sqrt(max(m0, 0.0))
        out[f"sigma_{name}"] = sigma * scale
        out[f"significant_{name}"] = 2.0 * sigma * scale          # 2 sigma = mean of highest 1/3
        out[f"Tz_{name}_s"] = 2.0 * math.pi * math.sqrt(m0 / m2) * 1.0 if m2 > 0 else float("nan")
    # vertical acceleration at the centre of gravity (linear, small-angle)
    dens_acc = [
        (w_e ** 4) * (r * r) * s
        for w_e, r, s in zip(curve["omega_e"], curve["heave_rao_m_per_m"], S)
    ]
    out["sigma_vertical_acc_m_s2"] = math.sqrt(max(_integrate(dens_acc, omegas), 0.0))
    return out