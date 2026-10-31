"""
Ice Breaking Force Time Series Model

Physics-based model for ice breaking forces on marine structures.
Based on established ice mechanics theories:
- Flexural failure mode (dominant for sloped structures)
- Compressive failure mode (dominant for vertical structures)
- Mixed-mode failure with realistic variability

Key references:
- Matlock et al. (1971), "Random fatigue model for ice forces"
- Chatterjee et al. (1976), "Dynamic ice forces on offshore structures"
- Croasdale (1974), "Ice forces on fixed and floating structures"
- Nevel (1971), "Characteristic length in ice flexure"
"""

import numpy as np
from typing import Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class IceProperties:
    """Ice mechanical properties"""
    thickness: float  # m
    flexural_strength: float  # Pa (typical 0.5-1.5 MPa)
    compressive_strength: float  # Pa (typical 2-10 MPa)
    elastic_modulus: float  # Pa (typical 3-7 GPa)
    density: float  # kg/m^3 (typical 900-920)
    poisson_ratio: float = 0.33


@dataclass
class StructureProperties:
    """Structure geometry and properties"""
    width: float  # m (characteristic width)
    slope_angle: float = 0.0  # rad (0 = vertical, >0 = sloped)
    shape_factor: float = 1.0  # accounts for non-rectangular shapes


@dataclass
class ShipProperties:
    """Ship motion properties"""
    velocity: float  # m/s
    heading: float = 0.0  # rad (0 = head-on)


class IceBreakingForceModel:
    """
    Physics-based ice breaking force time series generator.
    
    Implements a stochastic model that captures:
    1. Periodic ice breaking events with realistic variability
    2. Force buildup and release phases
    3. Spectral characteristics matching experimental observations
    4. Statistical properties (peak, mean, std) matching model tests
    """
    
    def __init__(
        self,
        ice_props: IceProperties,
        struct_props: StructureProperties,
        ship_props: ShipProperties,
        failure_mode: str = 'mixed'  # 'flexural', 'compressive', 'mixed'
    ):
        self.ice = ice_props
        self.struct = struct_props
        self.ship = ship_props
        self.failure_mode = failure_mode
        
        # Derived parameters
        self._compute_derived_parameters()
    
    def _compute_derived_parameters(self):
        """Compute derived physical parameters"""
        # Characteristic length for flexural failure (Nevel, 1971)
        # L_c = (E*h^3 / (12*(1-nu^2)*rho_w*g))^(1/4)
        rho_w = 1025.0  # water density
        g = 9.81
        E = self.ice.elastic_modulus
        h = self.ice.thickness
        nu = self.ice.poisson_ratio
        
        self.characteristic_length = (
            E * h**3 / (12 * (1 - nu**2) * rho_w * g)
        ) ** 0.25
        
        # Characteristic ice breaking length (empirical, typically 2-5 * L_c)
        self.breaking_length = 3.0 * self.characteristic_length
        
        # Ice breaking frequency (f = V / L_break)
        V = self.ship.velocity
        if V > 0:
            self.breaking_frequency = V / self.breaking_length
        else:
            self.breaking_frequency = 0.0
        
        # Peak force calculation
        # For flexural failure: F_peak ~ C_f * sigma_f * h^2 * D
        # For compressive failure: F_peak ~ C_c * sigma_c * h * D
        D = self.struct.width
        h = self.ice.thickness
        
        # Shape and slope correction factors
        slope_factor = 1.0 / np.cos(self.struct.slope_angle) if self.struct.slope_angle < np.pi/4 else 2.0
        
        if self.failure_mode == 'flexural':
            self.peak_force_coeff = 2.5  # empirical coefficient
            self.base_peak_force = (
                self.peak_force_coeff * 
                self.ice.flexural_strength * 
                h**2 * 
                D * 
                slope_factor *
                self.struct.shape_factor
            )
        elif self.failure_mode == 'compressive':
            self.peak_force_coeff = 1.5
            self.base_peak_force = (
                self.peak_force_coeff * 
                self.ice.compressive_strength * 
                h * 
                D * 
                slope_factor *
                self.struct.shape_factor
            )
        else:  # mixed mode
            # Weighted average based on typical mixed-mode behavior
            flexural_force = 2.5 * self.ice.flexural_strength * h**2 * D * slope_factor
            compressive_force = 1.5 * self.ice.compressive_strength * h * D * slope_factor
            self.base_peak_force = 0.6 * flexural_force + 0.4 * compressive_force
            self.base_peak_force *= self.struct.shape_factor
        
        # Mean force (typically 0.4-0.6 of peak for cyclic loading)
        self.mean_force_ratio = 0.5
        self.base_mean_force = self.mean_force_ratio * self.base_peak_force
    
    def generate_time_series(
        self,
        duration: float,
        dt: float,
        seed: Optional[int] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generate ice breaking force time series.
        
        Parameters
        ----------
        duration : float
            Total simulation time (s)
        dt : float
            Time step (s)
        seed : int, optional
            Random seed for reproducibility
        
        Returns
        -------
        time : ndarray
            Time array (s)
        force : ndarray
            Ice breaking force time series (N)
        """
        if seed is not None:
            np.random.seed(seed)
        
        n_steps = int(duration / dt)
        time = np.arange(n_steps) * dt
        
        # Generate force time series using stochastic model
        force = self._generate_stochastic_force(time, dt)
        
        return time, force
    
    def _generate_stochastic_force(self, time: np.ndarray, dt: float) -> np.ndarray:
        """
        Generate stochastic ice breaking force with realistic characteristics.
        
        The model captures:
        1. Periodic breaking events with random variability
        2. Force buildup (elastic) and release (failure) phases
        3. High-frequency content from ice fragmentation
        4. Low-frequency modulation from varying ice conditions
        """
        n_steps = len(time)
        force = np.zeros(n_steps)
        
        # Breaking period with variability
        if self.breaking_frequency > 0:
            mean_period = 1.0 / self.breaking_frequency
        else:
            mean_period = 10.0  # default 10s if no velocity
        
        # Generate breaking events with random timing
        # Use gamma distribution for more realistic variability than exponential
        shape_param = 3.0  # controls variability (higher = more regular)
        scale_param = mean_period / shape_param
        
        # Generate breaking times
        breaking_times = []
        t_current = 0.0
        while t_current < time[-1]:
            breaking_times.append(t_current)
            # Random period with gamma distribution
            period = np.random.gamma(shape_param, scale_param)
            t_current += period
        
        # Generate force for each breaking cycle
        for i, t_break in enumerate(breaking_times):
            # Find indices for this cycle
            if i < len(breaking_times) - 1:
                t_next = breaking_times[i + 1]
            else:
                t_next = time[-1]
            
            mask = (time >= t_break) & (time < t_next)
            if not np.any(mask):
                continue
            
            cycle_time = time[mask] - t_break
            cycle_duration = t_next - t_break
            
            # Random peak force for this cycle (lognormal distribution)
            # Coefficient of variation ~0.2-0.3 typical for ice forces
            peak_variation = np.random.lognormal(0.0, 0.25)
            peak_force = self.base_peak_force * peak_variation
            
            # Force buildup phase (typically 60-80% of cycle)
            buildup_fraction = 0.7 + 0.1 * np.random.randn()
            buildup_fraction = np.clip(buildup_fraction, 0.5, 0.9)
            buildup_duration = buildup_fraction * cycle_duration
            
            # Buildup phase: approximately linear with some nonlinearity
            buildup_mask = cycle_time <= buildup_duration
            if np.any(buildup_mask):
                t_buildup = cycle_time[buildup_mask] / buildup_duration
                # Nonlinear buildup (power law)
                power = 1.2 + 0.3 * np.random.randn()
                power = np.clip(power, 0.8, 1.8)
                force_buildup = peak_force * t_buildup ** power
                
                # Add high-frequency content (ice cracking)
                hf_amplitude = 0.05 * peak_force
                hf_freq = 5.0 + 2.0 * np.random.randn()
                hf_freq = max(2.0, hf_freq)
                hf_noise = hf_amplitude * np.sin(2 * np.pi * hf_freq * t_buildup * buildup_duration)
                
                force[mask][buildup_mask] = force_buildup + hf_noise
            
            # Release phase: rapid drop
            release_mask = cycle_time > buildup_duration
            if np.any(release_mask):
                t_release = (cycle_time[release_mask] - buildup_duration) / (cycle_duration - buildup_duration)
                # Exponential decay
                decay_rate = 3.0 + 1.0 * np.random.randn()
                decay_rate = max(1.5, decay_rate)
                force_release = peak_force * np.exp(-decay_rate * t_release)
                
                # Add fragmentation noise
                frag_amplitude = 0.1 * peak_force
                frag_noise = frag_amplitude * np.random.randn(len(t_release))
                frag_noise = np.convolve(frag_noise, np.ones(5)/5, mode='same')  # smooth
                
                force[mask][release_mask] = force_release + frag_noise
        
        # Add low-frequency modulation (ice thickness variation, etc.)
        lf_freq = 0.05  # Hz
        lf_amplitude = 0.1 * self.base_mean_force
        lf_modulation = lf_amplitude * np.sin(2 * np.pi * lf_freq * time)
        force += lf_modulation
        
        # Ensure non-negative forces
        force = np.maximum(force, 0.0)
        
        return force
    
    def compute_statistics(self, force: np.ndarray) -> Dict[str, float]:
        """
        Compute statistical properties of force time series.
        
        Returns
        -------
        stats : dict
            Dictionary containing:
            - peak_force: maximum force (N)
            - mean_force: mean force (N)
            - std_force: standard deviation (N)
            - rms_force: RMS force (N)
            - peak_to_mean_ratio: ratio of peak to mean
        """
        stats = {
            'peak_force': float(np.max(force)),
            'mean_force': float(np.mean(force)),
            'std_force': float(np.std(force)),
            'rms_force': float(np.sqrt(np.mean(force**2))),
            'peak_to_mean_ratio': float(np.max(force) / np.mean(force)) if np.mean(force) > 0 else 0.0
        }
        return stats
    
    def compute_spectrum(self, time: np.ndarray, force: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute power spectral density of force time series.
        
        Returns
        -------
        freq : ndarray
            Frequency array (Hz)
        psd : ndarray
            Power spectral density (N^2/Hz)
        """
        dt = time[1] - time[0]
        n = len(force)
        
        # Remove mean
        force_detrended = force - np.mean(force)
        
        # Apply Hanning window
        window = np.hanning(n)
        force_windowed = force_detrended * window
        
        # FFT
        fft_vals = np.fft.rfft(force_windowed)
        psd = 2.0 * np.abs(fft_vals)**2 / (n * (1.0/dt))
        
        # Frequency array
        freq = np.fft.rfftfreq(n, dt)
        
        return freq, psd
    
    def find_spectral_peak(self, freq: np.ndarray, psd: np.ndarray) -> float:
        """
        Find the peak frequency in the power spectrum.
        
        Returns
        -------
        peak_freq : float
            Peak frequency (Hz)
        """
        # Exclude DC component
        if len(freq) > 1:
            idx_peak = np.argmax(psd[1:]) + 1
            peak_freq = freq[idx_peak]
        else:
            peak_freq = 0.0
        
        return float(peak_freq)
