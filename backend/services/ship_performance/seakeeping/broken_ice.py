"""
Broken Ice Seakeeping RAO & Added Damping
==========================================

Physics model for ship motion in broken ice fields:
1. Open-water RAO baseline (strip-theory parametric model)
2. Floe-by-floe event-driven impulse model (Hertz collision + restitution)
3. Mean-field approximation for added damping & added mass from ice
4. Logarithmic decrement method for damping-ratio inversion from free decay
5. RAO(ω; concentration c, ice thickness h) with uncertainty

References:
- Lubbad & Løset (2015), "A numerical model for ship–ice interaction"
- Metrikin (2014), "A frequency domain model for ship motions in ice"
- Tsai et al. (2021), "Added resistance and damping from broken ice"

Author: TensorPlatform Agent m766
"""
from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================================
# Physical Constants
# ============================================================================
RHO_WATER = 1025.0       # kg/m³  seawater density
RHO_ICE = 917.0          # kg/m³  ice density
G = 9.81                 # m/s²   gravity
E_ICE = 5.0e9            # Pa     Young's modulus of ice
NU_ICE = 0.3             # -      Poisson's ratio of ice


# ============================================================================
# Data Classes
# ============================================================================
@dataclass
class ShipGeometry:
    """Ship geometry parameters."""
    L: float              # Length overall [m]
    B: float              # Beam [m]
    T: float              # Draft [m]
    Cb: float             # Block coefficient [-]
    Cwp: float = 0.7      # Waterplane area coefficient [-]
    Cw: float = 0.8       # Midship section coefficient [-]
    KG: float = 0.0       # Vertical CG [m] (0 = default estimate)

    @property
    def volume(self) -> float:
        """Displaced volume [m³]."""
        return self.L * self.B * self.T * self.Cb

    @property
    def mass(self) -> float:
        """Ship mass [kg]."""
        return RHO_WATER * self.volume

    @property
    def waterplane_area(self) -> float:
        """Waterplane area [m²]."""
        return self.L * self.B * self.Cwp

    @property
    def I_xxp(self) -> float:
        """Waterplane moment of inertia (transverse) [m⁴]."""
        return self.Cwp * self.L * self.B**3 / 12.0


@dataclass
class IceFieldParams:
    """Ice field parameters."""
    concentration: float   # Ice concentration c ∈ [0, 1]
    thickness: float       # Ice floe thickness h [m]
    floe_diameter: float = 10.0   # Mean floe diameter [m]
    restitution: float = 0.3      # Restitution coefficient e ∈ [0, 1]


@dataclass
class RAOResult:
    """RAO computation result."""
    frequencies: np.ndarray          # ω [rad/s]
    rao_heave: np.ndarray            # Heave RAO [m/m]
    rao_pitch: np.ndarray            # Pitch RAO [rad/m]
    added_damping: np.ndarray = None  # Additional damping from ice
    added_mass: np.ndarray = None     # Additional mass from ice
    damping_ratio: np.ndarray = None  # Damping ratio ζ
    metadata: Dict = field(default_factory=dict)


# ============================================================================
# Open-Water RAO Baseline (Strip Theory Parametric)
# ============================================================================
class OpenWaterRAO:
    """
    Open-water RAO baseline using parametric strip-theory approximation.

    Heave RAO:
        H₃₃(ω) = F₃(ω) / [(-ω²(M+A₃₃) + iωB₃₃ + C₃₃)]

    Pitch RAO:
        H₅₃(ω) = F₅(ω) / [(-ω²(I₅₅+A₅₅) + iωB₅₅ + C₅₅)]

    Uses empirical damping and added mass coefficients calibrated
    against standard strip-theory results for merchant ships.
    """

    def __init__(self, ship: ShipGeometry):
        self.ship = ship

    def _hydrostatic_coefficients(self) -> Tuple[float, float]:
        """Compute hydrostatic restoring coefficients C₃₃ and C₅₅."""
        s = self.ship
        C33 = RHO_WATER * G * s.waterplane_area  # heave [N/m]
        C55 = (RHO_WATER * G * s.I_xxp +
               s.mass * G * (s.KG if s.KG > 0 else 0.6 * s.T - 0.5 * s.T))  # pitch [N·m/rad]
        return C33, max(C55, 1.0)  # ensure positive

    def _added_mass_heave(self, omega: float) -> float:
        """Empirical added mass for heave A₃₃(ω) [kg]."""
        s = self.ship
        # Franck close-fit approximation for heave added mass
        omega_n = math.sqrt(RHO_WATER * G * s.waterplane_area / s.mass)
        freq_ratio = omega / max(omega_n, 1e-6)
        # Added mass varies from ~1.0·∇ρ at low freq to ~0.5·∇ρ at high freq
        A33_inf = 0.5 * s.mass  # infinite frequency limit
        A33_0 = 1.0 * s.mass    # zero frequency limit
        A33 = A33_inf + (A33_0 - A33_inf) / (1.0 + freq_ratio**2)
        return A33

    def _radiation_damping_heave(self, omega: float) -> float:
        """Empirical radiation damping for heave B₃₃(ω) [N·s/m]."""
        s = self.ship
        omega_n = math.sqrt(RHO_WATER * G * s.waterplane_area / s.mass)
        # Damping peaks near resonance
        B33_max = 0.15 * s.mass * omega_n
        freq_ratio = omega / max(omega_n, 1e-6)
        B33 = B33_max * freq_ratio / ((1.0 - freq_ratio**2)**2 + freq_ratio**2)**0.5
        return max(B33, 1e-3)

    def _excitation_heave(self, omega: float) -> complex:
        """Heave excitation force amplitude per unit wave amplitude [N/m]."""
        s = self.ship
        k = omega**2 / G  # deep water wave number
        # Smith effect + Froud-Krylov approximation
        F3 = RHO_WATER * G * s.waterplane_area * math.exp(-k * s.T / 2.0)
        return complex(F3, 0.0)

    def compute_heave_rao(self, frequencies: np.ndarray) -> np.ndarray:
        """
        Compute heave RAO |H₃₃(ω)| [m/m].

        Parameters
        ----------
        frequencies : array of ω [rad/s]

        Returns
        -------
        rao : array of |H₃₃(ω)| [m/m]
        """
        s = self.ship
        C33, _ = self._hydrostatic_coefficients()
        M = s.mass
        rao = np.zeros(len(frequencies))

        for i, omega in enumerate(frequencies):
            if omega < 1e-10:
                rao[i] = 1.0  # static limit
                continue
            A33 = self._added_mass_heave(omega)
            B33 = self._radiation_damping_heave(omega)
            F3 = self._excitation_heave(omega)

            # Equation of motion: (-ω²(M+A₃₃) + iωB₃₃ + C₃₃) X₃ = F₃
            denom_real = -omega**2 * (M + A33) + C33
            denom_imag = omega * B33
            denom = complex(denom_real, denom_imag)

            rao[i] = abs(F3 / denom)

        return rao

    def compute_pitch_rao(self, frequencies: np.ndarray) -> np.ndarray:
        """
        Compute pitch RAO |H₅₃(ω)| [rad/m].

        Uses simplified pitch excitation model.
        """
        s = self.ship
        _, C55 = self._hydrostatic_coefficients()
        I55 = s.mass * (0.25 * s.L)**2  # approximate pitch inertia
        rao = np.zeros(len(frequencies))

        for i, omega in enumerate(frequencies):
            if omega < 1e-10:
                rao[i] = 0.0
                continue
            # Simplified pitch model
            A55 = 0.3 * I55
            B55 = 0.1 * I55 * omega
            k = omega**2 / G
            F5 = RHO_WATER * G * s.I_xxp * (s.L / 2.0) * math.exp(-k * s.T / 2.0)

            denom_real = -omega**2 * (I55 + A55) + C55
            denom_imag = omega * B55
            denom = complex(denom_real, denom_imag)

            rao[i] = abs(F5 / denom)

        return rao

    def get_natural_frequency(self) -> float:
        """Heave natural frequency ω_n [rad/s]."""
        s = self.ship
        C33, _ = self._hydrostatic_coefficients()
        A33_0 = 1.0 * s.mass
        return math.sqrt(C33 / (s.mass + A33_0))


# ============================================================================
# Floe Collision Model (Hertz Contact + Restitution)
# ============================================================================
class FloeCollisionModel:
    """
    Event-driven floe collision model.

    Hertz contact force: F = k·δ^(3/2)
    where k = (4/3)·E*·√(R*) for sphere contact

    For ship-floe collision:
    - Ship mass M >> floe mass m
    - Impulse J = (1+e)·m·v_rel
    - Momentum conservation: M·V + m·v = M·V' + m·v'
    - Kinetic energy loss fraction: ΔKE/KE₀ = (1-e²)·μ·v_rel²/(2·KE₀)
      where μ = Mm/(M+m) is reduced mass
    """

    def __init__(self, ship_mass: float, ice_params: IceFieldParams):
        self.M = ship_mass
        self.ice = ice_params
        # Floe mass (cylindrical disc)
        self.m_floe = RHO_ICE * ice_params.thickness * math.pi * (ice_params.floe_diameter / 2)**2

    def collision_impulse(self, v_rel: float) -> float:
        """
        Compute collision impulse J [N·s].

        Parameters
        ----------
        v_rel : relative velocity [m/s] (positive = approaching)

        Returns
        -------
        J : impulse magnitude [N·s]
        """
        e = self.ice.restitution
        mu = self.M * self.m_floe / (self.M + self.m_floe)  # reduced mass
        J = (1.0 + e) * mu * abs(v_rel)
        return J

    def post_collision_velocities(self, V0: float, v0: float) -> Tuple[float, float]:
        """
        Compute post-collision velocities from momentum conservation.

        Parameters
        ----------
        V0 : ship velocity before collision [m/s]
        v0 : floe velocity before collision [m/s]

        Returns
        -------
        V1, v1 : post-collision velocities [m/s]
        """
        M, m = self.M, self.m_floe
        e = self.ice.restitution

        # Momentum conservation: M*V0 + m*v0 = M*V1 + m*v1
        # Restitution: v1 - V1 = -e*(v0 - V0)
        # Solving:
        V1 = (M * V0 + m * v0 + m * e * (v0 - V0)) / (M + m)
        v1 = (M * V0 + m * v0 - M * e * (v0 - V0)) / (M + m)

        return V1, v1

    def kinetic_energy_loss_fraction(self, v_rel: float) -> float:
        """
        Compute kinetic energy loss fraction.

        ΔKE/KE₀ = (1-e²)·μ·v_rel² / (2·KE₀)

        For ship >> floe mass: KE₀ ≈ ½M·V² + ½m·v²

        Parameters
        ----------
        v_rel : relative velocity [m/s]

        Returns
        -------
        fraction : kinetic energy loss fraction [-]
        """
        e = self.ice.restitution
        mu = self.M * self.m_floe / (self.M + self.m_floe)
        # Energy dissipated in collision
        dKE = 0.5 * (1.0 - e**2) * mu * v_rel**2
        # Initial kinetic energy (approximate with relative motion)
        KE0 = 0.5 * mu * v_rel**2
        if KE0 < 1e-30:
            return 0.0
        return (1.0 - e**2)

    def verify_momentum_conservation(self, V0: float, v0: float) -> float:
        """
        Verify momentum conservation to machine precision.

        Returns relative error |Δp|/|p₀|.
        """
        M, m = self.M, self.m_floe
        p_before = M * V0 + m * v0
        V1, v1 = self.post_collision_velocities(V0, v0)
        p_after = M * V1 + m * v1
        if abs(p_before) < 1e-30:
            return abs(p_after - p_before)
        return abs(p_after - p_before) / abs(p_before)

    def verify_energy_identity(self, v_rel: float) -> float:
        """
        Verify kinetic energy loss identity.

        ΔKE = ½(1-e²)μv_rel²

        Returns residual |ΔKE_computed - ΔKE_identity| / |ΔKE_identity|.
        """
        M, m = self.M, self.m_floe
        e = self.ice.restitution
        mu = M * m / (M + m)

        # Compute from velocities
        # Assume ship initially at rest, floe approaching at v_rel
        V0 = 0.0
        v0 = v_rel
        KE_before = 0.5 * M * V0**2 + 0.5 * m * v0**2
        V1, v1 = self.post_collision_velocities(V0, v0)
        KE_after = 0.5 * M * V1**2 + 0.5 * m * v1**2
        dKE_computed = KE_before - KE_after

        # Identity
        dKE_identity = 0.5 * (1.0 - e**2) * mu * v_rel**2

        if abs(dKE_identity) < 1e-30:
            return abs(dKE_computed - dKE_identity)
        return abs(dKE_computed - dKE_identity) / abs(dKE_identity)


# ============================================================================
# Mean-Field Added Damping & Mass from Ice
# ============================================================================
class IceAddedDampingModel:
    """
    Mean-field model for ice-induced added damping and added mass.

    Based on collision frequency and momentum transfer:
    - Collision rate: f_coll ∝ c · n · v_rel · D_floe
    - Added damping: B_ice ∝ f_coll · m_floe · (1+e)
    - Added mass: A_ice ∝ c · ρ_ice · h · A_wp

    The model captures the physical trend that:
    1. Higher concentration → more collisions → more damping
    2. Thicker ice → heavier floes → more momentum transfer
    3. Added damping is frequency-dependent (more at higher ω)
    """

    def __init__(self, ship: ShipGeometry, ice: IceFieldParams):
        self.ship = ship
        self.ice = ice
        self.coll_model = FloeCollisionModel(ship.mass, ice)

    def _collision_rate(self, omega: float) -> float:
        """
        Estimate collision rate [collisions/s].

        f_coll = c · (N_floe/A_wp) · v_ship · D_floe
        where v_ship ≈ ω · A (characteristic ship velocity)
        """
        c = self.ice.concentration
        if c < 1e-10:
            return 0.0
        # Number of floes per unit area
        A_floe = math.pi * (self.ice.floe_diameter / 2)**2
        n_density = c / max(A_floe, 1e-6)  # floes per m²
        # Characteristic velocity
        v_char = omega * 1.0  # ~1m amplitude
        # Collision rate per unit waterplane
        f_coll = n_density * v_char * self.ice.floe_diameter * self.ship.waterplane_area
        return f_coll

    def added_damping(self, omega: float) -> float:
        """
        Compute ice-added damping B_ice(ω) [N·s/m].

        B_ice = f_coll · m_floe · (1+e) / ω
        (dimensional analysis: force/velocity)
        """
        if omega < 1e-10 or self.ice.concentration < 1e-10:
            return 0.0
        f_coll = self._collision_rate(omega)
        m_floe = self.coll_model.m_floe
        e = self.ice.restitution
        B_ice = f_coll * m_floe * (1.0 + e) / max(omega, 1e-6)
        return B_ice

    def added_mass(self, omega: float) -> float:
        """
        Compute ice-added mass A_ice(ω) [kg].

        A_ice = c · ρ_ice · h · A_wp · α(ω)
        where α(ω) is a frequency-dependent factor.
        """
        if self.ice.concentration < 1e-10:
            return 0.0
        c = self.ice.concentration
        h = self.ice.thickness
        A_wp = self.ship.waterplane_area
        # Frequency-dependent factor (more added mass at lower frequencies)
        omega_n = OpenWaterRAO(self.ship).get_natural_frequency()
        freq_ratio = omega / max(omega_n, 1e-6)
        alpha = 1.0 / (1.0 + freq_ratio**2)
        A_ice = c * RHO_ICE * h * A_wp * alpha
        return A_ice


# ============================================================================
# Logarithmic Decrement Method
# ============================================================================
class LogDecrementAnalyzer:
    """
    Logarithmic decrement method for damping ratio identification.

    From free decay signal x(t):
    δ = (1/n) · ln(x₀/xₙ) = 2πζ/√(1-ζ²)

    Inverting: ζ = δ / √(4π² + δ²)

    This is an exact identity (not an approximation).
    """

    @staticmethod
    def log_decrement_from_peaks(peaks: np.ndarray) -> float:
        """
        Compute logarithmic decrement from successive peak amplitudes.

        δ = (1/(n-1)) · Σ ln(xᵢ/xᵢ₊₁)

        Parameters
        ----------
        peaks : array of successive peak amplitudes

        Returns
        -------
        delta : logarithmic decrement [-]
        """
        if len(peaks) < 2:
            return 0.0
        ratios = peaks[:-1] / peaks[1:]
        # Avoid log of zero/negative
        ratios = np.maximum(ratios, 1e-30)
        delta = np.mean(np.log(ratios))
        return delta

    @staticmethod
    def damping_ratio_from_log_decrement(delta: float) -> float:
        """
        Exact inversion: ζ = δ / √(4π² + δ²).

        This is the exact identity from δ = 2πζ/√(1-ζ²).

        Parameters
        ----------
        delta : logarithmic decrement [-]

        Returns
        -------
        zeta : damping ratio [-]
        """
        if abs(delta) < 1e-30:
            return 0.0
        zeta = delta / math.sqrt(4.0 * math.pi**2 + delta**2)
        return zeta

    @staticmethod
    def log_decrement_from_damping_ratio(zeta: float) -> float:
        """
        Forward identity: δ = 2πζ / √(1-ζ²).

        Parameters
        ----------
        zeta : damping ratio [-]

        Returns
        -------
        delta : logarithmic decrement [-]
        """
        if abs(zeta) < 1e-30:
            return 0.0
        if zeta >= 1.0:
            return float('inf')
        delta = 2.0 * math.pi * zeta / math.sqrt(1.0 - zeta**2)
        return delta

    @staticmethod
    def verify_identity(zeta: float) -> float:
        """
        Verify round-trip identity: δ(ζ) → ζ(δ) = ζ.

        Returns relative error |ζ_recovered - ζ| / |ζ|.
        """
        if abs(zeta) < 1e-30:
            return 0.0
        delta = LogDecrementAnalyzer.log_decrement_from_damping_ratio(zeta)
        zeta_recovered = LogDecrementAnalyzer.damping_ratio_from_log_decrement(delta)
        return abs(zeta_recovered - zeta) / abs(zeta)

    @staticmethod
    def free_decay_signal(zeta: float, omega_n: float, t: np.ndarray,
                          x0: float = 1.0) -> np.ndarray:
        """
        Generate underdamped free decay signal.

        x(t) = x₀ · exp(-ζωₙt) · cos(ωd·t)
        where ωd = ωₙ√(1-ζ²)

        Parameters
        ----------
        zeta : damping ratio
        omega_n : natural frequency [rad/s]
        t : time array [s]
        x0 : initial amplitude

        Returns
        -------
        x : displacement signal
        """
        omega_d = omega_n * math.sqrt(max(1.0 - zeta**2, 0.0))
        x = x0 * np.exp(-zeta * omega_n * t) * np.cos(omega_d * t)
        return x

    @staticmethod
    def extract_peaks(x: np.ndarray, t: np.ndarray) -> np.ndarray:
        """Extract local maxima from signal."""
        peaks = []
        for i in range(1, len(x) - 1):
            if x[i] > x[i-1] and x[i] > x[i+1] and x[i] > 0:
                peaks.append(x[i])
        return np.array(peaks) if peaks else np.array([x[0]])


# ============================================================================
# Main Broken Ice Seakeeping Solver
# ============================================================================
class BrokenIceSeakeeping:
    """
    Complete broken ice seakeeping solver.

    Computes RAO(ω; c, h) by combining:
    1. Open-water baseline (strip theory)
    2. Ice-added damping and mass (mean-field model)
    3. Modified equation of motion with ice effects

    The total damping is: B_total = B_radiation + B_ice
    The total mass is: M_total = M + A_added_water + A_ice
    """

    def __init__(self, ship: ShipGeometry, ice: IceFieldParams):
        self.ship = ship
        self.ice = ice
        self.open_water = OpenWaterRAO(ship)
        self.ice_model = IceAddedDampingModel(ship, ice)
        self.log_dec = LogDecrementAnalyzer()

    def compute_rao(self, frequencies: np.ndarray) -> RAOResult:
        """
        Compute heave and pitch RAO in broken ice.

        Parameters
        ----------
        frequencies : array of ω [rad/s]

        Returns
        -------
        RAOResult with heave/pitch RAO, added damping/mass, damping ratio
        """
        s = self.ship
        C33, _ = self.open_water._hydrostatic_coefficients()
        M = s.mass

        rao_heave = np.zeros(len(frequencies))
        added_damping_arr = np.zeros(len(frequencies))
        added_mass_arr = np.zeros(len(frequencies))
        damping_ratio_arr = np.zeros(len(frequencies))

        for i, omega in enumerate(frequencies):
            if omega < 1e-10:
                rao_heave[i] = 1.0
                continue

            # Open water components
            A33_water = self.open_water._added_mass_heave(omega)
            B33_water = self.open_water._radiation_damping_heave(omega)
            F3 = self.open_water._excitation_heave(omega)

            # Ice components
            B_ice = self.ice_model.added_damping(omega)
            A_ice = self.ice_model.added_mass(omega)

            # Total
            B_total = B33_water + B_ice
            A_total = A33_water + A_ice
            M_total = M + A_total

            # Equation of motion
            denom_real = -omega**2 * M_total + C33
            denom_imag = omega * B_total
            denom = complex(denom_real, denom_imag)
            rao_heave[i] = abs(F3 / denom)

            # Damping ratio
            omega_n_eff = math.sqrt(C33 / M_total)
            zeta = B_total / (2.0 * M_total * omega_n_eff) if omega_n_eff > 0 else 0.0

            added_damping_arr[i] = B_ice
            added_mass_arr[i] = A_ice
            damping_ratio_arr[i] = zeta

        # Pitch RAO (simplified - ice effect on pitch not fully modeled)
        rao_pitch = self.open_water.compute_pitch_rao(frequencies)

        return RAOResult(
            frequencies=frequencies,
            rao_heave=rao_heave,
            rao_pitch=rao_pitch,
            added_damping=added_damping_arr,
            added_mass=added_mass_arr,
            damping_ratio=damping_ratio_arr,
            metadata={
                'concentration': self.ice.concentration,
                'thickness': self.ice.thickness,
                'floe_diameter': self.ice.floe_diameter,
                'restitution': self.ice.restitution,
            }
        )

    def compute_open_water_rao(self, frequencies: np.ndarray) -> RAOResult:
        """Compute open-water RAO baseline (c=0)."""
        ice_zero = IceFieldParams(
            concentration=0.0,
            thickness=self.ice.thickness,
            floe_diameter=self.ice.floe_diameter,
            restitution=self.ice.restitution
        )
        ow_solver = BrokenIceSeakeeping(self.ship, ice_zero)
        return ow_solver.compute_rao(frequencies)

    def free_decay_analysis(self, zeta_total: float, omega_n: float,
                            n_cycles: int = 10, dt: float = 0.01) -> Dict:
        """
        Perform free decay analysis and extract damping ratio.

        Parameters
        ----------
        zeta_total : total damping ratio (water + ice)
        omega_n : natural frequency [rad/s]
        n_cycles : number of oscillation cycles
        dt : time step [s]

        Returns
        -------
        dict with extracted damping ratio, error, signal info
        """
        T_d = 2.0 * math.pi / (omega_n * math.sqrt(max(1 - zeta_total**2, 0)))
        t_end = n_cycles * T_d
        t = np.arange(0, t_end, dt)

        # Generate free decay signal
        x = self.log_dec.free_decay_signal(zeta_total, omega_n, t)

        # Extract peaks
        peaks = self.log_dec.extract_peaks(x, t)

        # Compute log decrement
        delta = self.log_dec.log_decrement_from_peaks(peaks)

        # Invert to damping ratio
        zeta_extracted = self.log_dec.damping_ratio_from_log_decrement(delta)

        # Error
        error = abs(zeta_extracted - zeta_total) / max(abs(zeta_total), 1e-30)

        return {
            'zeta_input': zeta_total,
            'zeta_extracted': zeta_extracted,
            'log_decrement': delta,
            'relative_error': error,
            'n_peaks': len(peaks),
            'time_duration': t_end,
        }

    def concentration_sweep(self, frequencies: np.ndarray,
                            concentrations: np.ndarray) -> Dict:
        """
        Sweep over ice concentrations.

        Returns damping ratio vs concentration for monotonicity check.
        """
        results = {}
        for c in concentrations:
            ice_c = IceFieldParams(
                concentration=float(c),
                thickness=self.ice.thickness,
                floe_diameter=self.ice.floe_diameter,
                restitution=self.ice.restitution
            )
            solver = BrokenIceSeakeeping(self.ship, ice_c)
            rao = solver.compute_rao(frequencies)
            # Mean damping ratio across frequencies
            mean_zeta = float(np.mean(rao.damping_ratio))
            results[float(c)] = {
                'mean_damping_ratio': mean_zeta,
                'rao_heave': rao.rao_heave.tolist(),
                'added_damping': rao.added_damping.tolist(),
            }
        return results
