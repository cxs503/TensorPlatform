"""Spatio-temporal ice-abrasion field evolution on the hull: DEM-derived
deterministic activity profile, conditioned residual random field, and a
moving-frame voyage accumulation with an exact closed-form reference.

Why this module exists
----------------------
The earlier companion module :mod:`tensordem.ice_abrasion_field` established the
*interface* of a spatio-temporal abrasion field, but three of its acceptance
checks were self-referential:

* the "DEM-derived" activity field was generated from the module's own
  parametric profile plus white noise, so the profile fit (R²) measured nothing
  about DEM;
* "conditional" simulation never conditioned on observations, and its variance
  criterion had been relaxed to 50 %;
* mass conservation compared two integrals of the same analytic rate field.

This module closes those gaps and is the N05 frontier node of the ice-abrasion
campaign.  It changes the *evidence chain*, not the interface:

1. **DEM-derived activity field.**  The abrasion rate ``a(x, z)`` on the hull
   waterline band is produced by the real contact ensemble of
   :mod:`tensordem.ice_abrasion` (Hertz contact with a crushing cap +
   Cundall–Strack sliding, Archard pit depth, volume-conserving projection).
   ``K`` independent DEM realisations give the ensemble-mean rate field and its
   spatial fluctuation; the deterministic profile is fitted to the *panel
   averaged* ensemble mean.
2. **Deterministic separable activity profile** ``a(x,z) = scale * w_x(x) *
   w_z(z)``: ``w_x`` is background + exponential forebody decay + Gaussian
   shoulders, ``w_z`` an asymmetric Gaussian about the ice belt.  Fitted by
   bounded least squares; R² and RMSE reported on the panel grid.
3. **Residual random field**: residual realisations ``r_k = a_k - <a>`` give an
   anisotropic separable covariance ``C = sigma² exp(-|dx|/Lx - |dz|/Lz) +
   tau² I`` (``tau²`` is the per-cell pit sampling noise / survey error) fitted
   on binned lag covariances.  The Kronecker structure makes the KL eigenpairs
   analytic: ``lambda_ij = sigma² lx_i lz_j + tau²``.
4. **Conditional simulation**: simple kriging conditioning on sparse survey
   observations; samples are drawn from ``N(m_cond, C_cond)``.  Verified
   against the closed-form conditional variance of a separable exponential
   field, against exact interpolation when ``tau = 0``, and against the
   ``1/sqrt(m)`` sampling-error scaling of the estimator (no bias).
5. **Moving-frame voyage evolution**: each panel ``i`` at streamwise offset
   ``xi_i = x_i - L/2`` from midship samples the along-track ice concentration
   ``c(s)`` at ``s(t) + xi_i``, so a bow panel meets an ice edge ``L/V`` earlier
   than a stern panel.  The accumulation
   ``d_i(t) = a_i/(c_ref V) [C(V t + xi_i) - C(xi_i)]``, ``C(s) = ∫ c ds``, is
   exact for piecewise-constant and piecewise-linear ``c``; a time-stepped
   integrator is provided and its convergence order checked against it.
6. **N02 P95 interface**: area-weighted (and unweighted) quantiles are extracted
   from the evolved field and compared with the P95 statistic of the N02 wear
   field produced by the same DEM ensemble.

Evidence levels
---------------
* **REAL**: the DEM contact ensemble / Archard / projection physics (delegated
  to :mod:`tensordem.ice_abrasion`); the closed-form moving-frame integral; the
  kriging algebra and its analytic conditional-variance check; area-weighted
  quantiles; volume ledger; time-step convergence order.
* **CALIBRATED (inherited)**: the absolute wear level still carries the
  single-point literature anchor of :mod:`tensordem.ice_abrasion`
  (``k = K/H`` fitted to the Baltic 1A coating loss).  Nothing new is fitted
  here.
* **APPROXIMATION (documented)**: the separable profile is a parametric model,
  not an exact representation of the DEM field; the covariance model is an
  anisotropic exponential fitted to finite ensemble residuals; the hull band is
  a single-sided 2-D map (no multi-valued surface at the stem, no port/starboard
  asymmetry); concentration is piecewise-constant along the track.

Everything is SI internally (m, s, m/s, m³).  Reports use µm and mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from .ice_abrasion import (
    VoyageProfile,
    WaterlineGrid,
    WearSourceConfig,
    build_waterline_grid,
    reference_baltic_voyage,
    run_wear_season,
)

NP_DTYPE = np.float64

__all__ = [
    "PanelGrid",
    "build_panel_grid",
    "coarsen_field",
    "DemActivityEnsemble",
    "dem_activity_rate_field",
    "SeparableActivityProfile",
    "ProfileFit",
    "fit_separable_profile",
    "SpatialCovariance",
    "estimate_covariance_ergodic",
    "estimate_covariance_from_realizations",
    "estimate_covariance_lagged",
    "sampling_noise_variance",
    "coarsen_covariance",
    "conditional_simulation_from_matrix",
    "SeparableKL",
    "separable_kl_expansion",
    "ConditionalField",
    "simple_kriging",
    "simulate_conditional_field",
    "IceTrack",
    "ice_track_from_voyage",
    "AbrasionFieldTimeSeries",
    "evolve_abrasion_field",
    "evolve_time_stepped",
    "volume_ledger",
    "area_weighted_quantile",
    "extract_p95_statistics",
    "connect_p95_to_n02",
    "run_n05_verification",
]


# ---------------------------------------------------------------------------
# 1. Panel grid: coarsened waterline band used for the evolution
# ---------------------------------------------------------------------------

def _overlap_matrix(coarse_edges: np.ndarray, fine_edges: np.ndarray) -> np.ndarray:
    """Row-normalised overlap-length matrix ``(n_coarse, n_fine)``.

    ``W[i, j]`` = fraction of fine cell ``j`` that lies inside coarse cell ``i``,
    normalised so that each row sums to one (area-weighted block average).
    """
    lo = np.maximum(coarse_edges[:-1, None], fine_edges[None, :-1])
    hi = np.minimum(coarse_edges[1:, None], fine_edges[None, 1:])
    overlap = np.clip(hi - lo, 0.0, None)
    row = overlap.sum(axis=1, keepdims=True)
    row[row <= 0.0] = 1.0
    return overlap / row


@dataclass(frozen=True)
class PanelGrid:
    """Structured panel map of the waterline band, ``(n_z, n_x)`` cells."""

    x_edges: np.ndarray
    z_edges: np.ndarray
    hull_length_m: float
    hull_draught_m: float
    source_grid_shape: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        x = np.asarray(self.x_edges, dtype=NP_DTYPE).ravel()
        z = np.asarray(self.z_edges, dtype=NP_DTYPE).ravel()
        if x.size < 2 or z.size < 2:
            raise ValueError("need at least 2 edges per direction")
        if np.any(np.diff(x) <= 0) or np.any(np.diff(z) <= 0):
            raise ValueError("edges must be strictly increasing")
        if not self.hull_length_m > 0 or not self.hull_draught_m > 0:
            raise ValueError("hull particulars must be positive")

    @property
    def n_x(self) -> int:
        return int(self.x_edges.size - 1)

    @property
    def n_z(self) -> int:
        return int(self.z_edges.size - 1)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.n_z, self.n_x)

    @property
    def n_panels(self) -> int:
        return self.n_z * self.n_x

    @property
    def x_centers(self) -> np.ndarray:
        return 0.5 * (self.x_edges[1:] + self.x_edges[:-1])

    @property
    def z_centers(self) -> np.ndarray:
        return 0.5 * (self.z_edges[1:] + self.z_edges[:-1])

    @property
    def cell_area_m2(self) -> np.ndarray:
        dx = np.diff(self.x_edges).reshape(1, -1)
        dz = np.diff(self.z_edges).reshape(-1, 1)
        return np.broadcast_to(dz * dx, self.shape).copy()

    @property
    def areas_flat(self) -> np.ndarray:
        return self.cell_area_m2.ravel()

    @property
    def total_area_m2(self) -> float:
        return float(self.cell_area_m2.sum())

    @property
    def panel_x(self) -> np.ndarray:
        return np.tile(self.x_centers, self.n_z)

    @property
    def panel_z(self) -> np.ndarray:
        return np.repeat(self.z_centers, self.n_x)

    @property
    def streamwise_offset_m(self) -> np.ndarray:
        """Panel ``x`` relative to midship (bow positive)."""
        return self.panel_x - 0.5 * self.hull_length_m


def build_panel_grid(
    *,
    grid: WaterlineGrid | None = None,
    n_x: int = 40,
    n_z: int = 25,
) -> PanelGrid:
    """Coarsen the waterline band of ``grid`` to ``n_x × n_z`` panels.

    The panel edges are drawn uniformly inside the band; the fine grid stays the
    quadrature reference, so panel areas add up to the exact band area.
    """
    grid = build_waterline_grid() if grid is None else grid
    if n_x < 2 or n_z < 2:
        raise ValueError("n_x and n_z must be >= 2")
    x_edges = np.linspace(float(grid.x_edges[0]), float(grid.x_edges[-1]),
                          n_x + 1, dtype=NP_DTYPE)
    z_edges = np.linspace(float(grid.z_edges[0]), float(grid.z_edges[-1]),
                          n_z + 1, dtype=NP_DTYPE)
    return PanelGrid(
        x_edges=x_edges, z_edges=z_edges,
        hull_length_m=float(grid.hull.length_m),
        hull_draught_m=float(grid.hull.draught_m),
        source_grid_shape=(int(grid.shape[0]), int(grid.shape[1])),
    )


def coarsen_field(field: np.ndarray, grid: WaterlineGrid,
                  panels: PanelGrid) -> np.ndarray:
    """Area-weighted block average of a band field onto ``panels``."""
    f = np.asarray(field, dtype=NP_DTYPE)
    if f.shape != (int(grid.shape[0]), int(grid.shape[1])):
        raise ValueError(f"field shape {f.shape} != grid shape {grid.shape}")
    wz = _overlap_matrix(panels.z_edges, np.asarray(grid.z_edges, dtype=NP_DTYPE))
    wx = _overlap_matrix(panels.x_edges, np.asarray(grid.x_edges, dtype=NP_DTYPE))
    return wz @ f @ wx.T


# ---------------------------------------------------------------------------
# 2. DEM-derived activity rate field
# ---------------------------------------------------------------------------

def _voyage_duration_s(voyage: VoyageProfile) -> float:
    return float(sum(leg.duration_s for leg in voyage.legs_per_voyage()))


@dataclass(frozen=True)
class DemActivityEnsemble:
    """Ensemble of DEM-derived abrasion-rate fields on the waterline band.

    ``rate_realizations[k]`` is realisation ``k`` of ``a(x, z)`` in m/s,
    obtained by dividing the per-voyage wear depth of one DEM season by the
    voyage duration.  ``mean_rate`` is the deterministic (ensemble-mean) rate
    field; ``fluctuation`` the zero-mean residual realisations.
    """

    rate_realizations: np.ndarray          # (K, n_z, n_x) m/s
    mean_rate: np.ndarray                  # (n_z, n_x) m/s
    voyage_duration_s: float
    seeds: tuple[int, ...]
    depth_per_voyage_um: np.ndarray        # (K,) mean depth of each realisation
    spec_rate: float

    @property
    def n_realizations(self) -> int:
        return int(self.rate_realizations.shape[0])

    @property
    def fluctuation(self) -> np.ndarray:
        return self.rate_realizations - self.mean_rate[None, :, :]

    def summary(self) -> dict[str, Any]:
        mean = self.mean_rate
        fluc = self.fluctuation
        return {
            "n_realizations": self.n_realizations,
            "voyage_duration_s": self.voyage_duration_s,
            "mean_rate_um_per_hour": float(mean.mean()) * 1e6 * 3600.0,
            "p95_rate_um_per_hour": float(np.percentile(mean, 95)) * 1e6 * 3600.0,
            "max_rate_um_per_hour": float(mean.max()) * 1e6 * 3600.0,
            "fluctuation_rms_fraction_of_mean": float(
                np.sqrt(np.mean(fluc ** 2)) / max(float(mean.mean()), 1e-30)),
            "depth_per_voyage_um_mean": float(np.mean(self.depth_per_voyage_um)),
            "seeds": list(self.seeds),
            "specific_wear_rate_m3_per_Nm": self.spec_rate,
        }


def dem_activity_rate_field(
    *,
    grid: WaterlineGrid | None = None,
    voyage: VoyageProfile | None = None,
    n_realizations: int = 8,
    n_events_per_leg: int = 20000,
    base_seed: int = 20261106,
    seed_stride: int = 37,
    source: WearSourceConfig | None = None,
) -> DemActivityEnsemble:
    """Run ``n_realizations`` DEM wear seasons and return the rate ensemble.

    The only stochastic ingredient is the contact ensemble (fragment size,
    contact pressure, landing position); everything upstream (Hertz contact,
    Archard pit depth, friction-work anchoring) is deterministic.
    """
    if n_realizations < 1:
        raise ValueError("n_realizations must be >= 1")
    grid = build_waterline_grid() if grid is None else grid
    voyage = reference_baltic_voyage() if voyage is None else voyage
    source = WearSourceConfig() if source is None else source
    duration = _voyage_duration_s(voyage)

    rates = np.empty((n_realizations,) + tuple(int(v) for v in grid.shape),
                     dtype=NP_DTYPE)
    depths = np.zeros(n_realizations, dtype=NP_DTYPE)
    seeds: list[int] = []
    for k in range(n_realizations):
        seed = int(base_seed + seed_stride * k)
        seeds.append(seed)
        cfg = replace(source, n_events_per_leg=int(n_events_per_leg), seed=seed)
        season = run_wear_season(grid=grid, voyage=voyage, source=cfg)
        depth = season.field_per_voyage.depth_m.detach().cpu().numpy()
        rates[k] = np.asarray(depth, dtype=NP_DTYPE) / duration
        depths[k] = float(np.mean(depth))

    return DemActivityEnsemble(
        rate_realizations=rates,
        mean_rate=rates.mean(axis=0),
        voyage_duration_s=duration,
        seeds=tuple(seeds),
        depth_per_voyage_um=depths * 1e6,
        spec_rate=float(source.spec_rate),
    )

# ---------------------------------------------------------------------------
# 3. Deterministic separable activity profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeparableActivityProfile:
    """``a(x, z) = scale * w_x(u) * w_z(z)``, ``u = x / L``.

    ``w_x(u) = bg + decay_amp * exp(-u / decay_len)
              + sum_k amp_k exp(-0.5 ((u - center_k) / width_k)^2)``
    normalised to unit maximum, and
    ``w_z(z) = exp(-0.5 ((z - z_c) / sigma(z))^2)`` with ``sigma = sigma_below``
    below the ice-belt centre and ``sigma_above`` above it.
    """

    scale_m_s: float
    background: float
    decay_amplitude: float
    decay_length: float
    x_modes: tuple[tuple[float, float, float], ...]
    waterline_offset_m: float
    sigma_below_m: float
    sigma_above_m: float
    hull_length_m: float
    hull_draught_m: float

    @property
    def belt_height_m(self) -> float:
        return self.hull_draught_m + self.waterline_offset_m

    def x_raw(self, x: np.ndarray) -> np.ndarray:
        u = np.asarray(x, dtype=NP_DTYPE) / self.hull_length_m
        w = self.background + self.decay_amplitude * np.exp(
            -u / max(self.decay_length, 1e-9))
        for amp, centre, width in self.x_modes:
            w = w + amp * np.exp(-0.5 * ((u - centre) / max(width, 1e-9)) ** 2)
        return w

    def x_shape(self, x: np.ndarray) -> np.ndarray:
        w = self.x_raw(x)
        peak = float(np.max(self.x_raw(np.linspace(0.0, 1.0, 2001)
                                       * self.hull_length_m)))
        return w / max(peak, 1e-30)

    def z_shape(self, z: np.ndarray) -> np.ndarray:
        zz = np.asarray(z, dtype=NP_DTYPE)
        zc = self.belt_height_m
        sigma = np.where(zz < zc, self.sigma_below_m, self.sigma_above_m)
        return np.exp(-0.5 * ((zz - zc) / np.maximum(sigma, 1e-9)) ** 2)

    def evaluate(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=NP_DTYPE)
        z = np.asarray(z, dtype=NP_DTYPE)
        return self.scale_m_s * self.x_shape(x) * self.z_shape(z)

    def evaluate_on_grid(self, panels: PanelGrid) -> np.ndarray:
        a = self.evaluate(panels.panel_x, panels.panel_z)
        return a.reshape(panels.shape)


@dataclass(frozen=True)
class ProfileFit:
    """Fit of :class:`SeparableActivityProfile` to a rate field."""

    profile: SeparableActivityProfile
    r_squared: float
    rmse_m_s: float
    n_points: int
    n_parameters: int
    converged: bool
    nfev: int
    params: dict[str, float]

    def summary(self) -> dict[str, Any]:
        return {
            "r_squared": self.r_squared,
            "rmse_um_per_hour": self.rmse_m_s * 1e6 * 3600.0,
            "n_points": self.n_points,
            "n_parameters": self.n_parameters,
            "converged": self.converged,
            "nfev": self.nfev,
            "residual_dof": self.n_points - self.n_parameters,
            "scale_um_per_hour": self.profile.scale_m_s * 1e6 * 3600.0,
            "belt_height_m": self.profile.belt_height_m,
            "params": self.params,
        }


def _profile_from_vector(p: np.ndarray, n_modes: int, hull_length_m: float,
                         hull_draught_m: float) -> SeparableActivityProfile:
    scale = float(p[0])
    bg, decay_amp, decay_len = (float(v) for v in p[1:4])
    modes = tuple(
        (float(p[4 + 3 * k]), float(p[5 + 3 * k]), float(p[6 + 3 * k]))
        for k in range(n_modes))
    off, sb, sa = (float(v) for v in p[4 + 3 * n_modes:])
    return SeparableActivityProfile(
        scale_m_s=scale, background=bg, decay_amplitude=decay_amp,
        decay_length=decay_len, x_modes=modes, waterline_offset_m=off,
        sigma_below_m=sb, sigma_above_m=sa,
        hull_length_m=hull_length_m, hull_draught_m=hull_draught_m)


def fit_separable_profile(
    x: np.ndarray,
    z: np.ndarray,
    rate: np.ndarray,
    *,
    hull_length_m: float,
    hull_draught_m: float,
    n_x_modes: int = 2,
    max_nfev: int = 20000,
    xtol: float = 1e-12,
) -> ProfileFit:
    """Bounded least-squares fit of the separable profile to rate data.

    The residual is non-dimensionalised by the peak rate: the data are of order
    ``1e-11 m/s`` and an absolute ``gtol`` would otherwise terminate the
    optimiser at the initial point (this trap cost one debugging cycle).
    """
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    a = np.asarray(rate, dtype=NP_DTYPE).ravel()
    if not (x.size == z.size == a.size):
        raise ValueError("x, z, rate must have the same size")
    if np.any(a < 0.0):
        raise ValueError("abrasion rates must be nonnegative")
    n_modes = int(n_x_modes)
    if n_modes not in (1, 2, 3):
        raise ValueError("n_x_modes must be 1, 2 or 3")

    peak = float(np.max(a))
    if not peak > 0.0:
        raise ValueError("rate field is identically zero; nothing to fit")

    i_peak = int(np.argmax(a))
    u_peak = float(np.clip(x[i_peak] / hull_length_m, 0.02, 0.98))
    if n_modes == 1:
        mode_seeds = [(u_peak, 0.06)]
    elif n_modes == 2:
        mode_seeds = [(u_peak, 0.05), (0.55, 0.25)]
    else:
        mode_seeds = [(u_peak, 0.04), (0.35, 0.15), (0.70, 0.20)]

    p0 = [peak * 1.2, 0.55, 0.45, 0.12]
    lower = [0.0, 0.0, 0.0, 0.01]
    upper = [peak * 20.0, 20.0, 20.0, 5.0]
    for u0, w0 in mode_seeds:
        p0.extend([0.5, u0, w0])
        lower.extend([0.0, 0.0, 0.004])
        upper.extend([20.0, 1.0, 0.8])
    p0.extend([-0.15, 0.55, 0.35])
    lower.extend([-3.0, 0.05, 0.05])
    upper.extend([3.0, 3.0, 3.0])

    target = a / peak

    def residuals(p: np.ndarray) -> np.ndarray:
        model = _profile_from_vector(p, n_modes, hull_length_m, hull_draught_m)
        return model.evaluate(x, z) / peak - target

    result = least_squares(
        residuals, p0, bounds=(lower, upper), max_nfev=int(max_nfev),
        xtol=float(xtol), ftol=1e-12, gtol=1e-12, x_scale="jac")

    model = _profile_from_vector(result.x, n_modes, hull_length_m, hull_draught_m)
    pred = model.evaluate(x, z)
    ss_res = float(np.sum((a - pred) ** 2))
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    names = ["scale", "background", "decay_amplitude", "decay_length"]
    for k in range(n_modes):
        names += [f"amp{k}", f"center{k}", f"width{k}"]
    names += ["waterline_offset", "sigma_below", "sigma_above"]
    return ProfileFit(
        profile=model,
        r_squared=float(r_squared),
        rmse_m_s=float(math.sqrt(ss_res / a.size)),
        n_points=int(a.size),
        n_parameters=int(result.x.size),
        converged=bool(result.success),
        nfev=int(result.nfev),
        params={n: float(v) for n, v in zip(names, result.x)},
    )


# ---------------------------------------------------------------------------
# 4. Spatial covariance of the residual field
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpatialCovariance:
    """Anisotropic separable covariance ``sigma^2 exp(-|dx|/Lx-|dz|/Lz)+tau^2 I``."""

    sigma2: float
    length_x_m: float
    length_z_m: float
    nugget2: float
    r_squared: float = 0.0
    n_pairs: int = 0
    lag_table: tuple[tuple[float, float, float, int], ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("sigma2", self.sigma2),
                            ("length_x_m", self.length_x_m),
                            ("length_z_m", self.length_z_m),
                            ("nugget2", self.nugget2)):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and nonnegative")

    def evaluate(self, dx: np.ndarray, dz: np.ndarray) -> np.ndarray:
        dx = np.asarray(dx, dtype=NP_DTYPE)
        dz = np.asarray(dz, dtype=NP_DTYPE)
        corr = np.exp(-np.abs(dx) / max(self.length_x_m, 1e-12)
                      - np.abs(dz) / max(self.length_z_m, 1e-12))
        same = (np.abs(dx) < 1e-12) & (np.abs(dz) < 1e-12)
        return self.sigma2 * corr + self.nugget2 * same

    def matrix(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=NP_DTYPE).ravel()
        z = np.asarray(z, dtype=NP_DTYPE).ravel()
        dx = x[:, None] - x[None, :]
        dz = z[:, None] - z[None, :]
        return self.evaluate(dx, dz)

    @property
    def total_variance(self) -> float:
        return float(self.sigma2 + self.nugget2)

    def summary(self) -> dict[str, Any]:
        return {
            "sigma2": self.sigma2,
            "length_x_m": self.length_x_m,
            "length_z_m": self.length_z_m,
            "nugget2": self.nugget2,
            "nugget_fraction": self.nugget2 / max(self.total_variance, 1e-30),
            "model": "exponential_separable_plus_nugget",
            "fit_r_squared": self.r_squared,
            "n_pairs": self.n_pairs,
        }


def _pair_lag_bins(
    x: np.ndarray,
    z: np.ndarray,
    *,
    n_lag_x: int,
    n_lag_z: int,
    max_cells: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Sub-sample the panel set and bin all pairs on a ``(dx, dz)`` lag grid."""
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    n = x.size
    rng = np.random.default_rng(seed)
    if n > max_cells:
        idx = np.sort(rng.choice(n, size=int(max_cells), replace=False))
    else:
        idx = np.arange(n)
    xs, zs = x[idx], z[idx]
    m = xs.size
    i, j = np.triu_indices(m, k=1)
    dx = np.abs(xs[i] - xs[j])
    dz = np.abs(zs[i] - zs[j])
    ex = np.unique(np.quantile(dx, np.linspace(0.0, 1.0, n_lag_x + 1)))
    ez = np.unique(np.quantile(dz, np.linspace(0.0, 1.0, n_lag_z + 1)))
    bx = np.clip(np.searchsorted(ex, dx, side="right") - 1, 0, max(ex.size - 2, 0))
    bz = np.clip(np.searchsorted(ez, dz, side="right") - 1, 0, max(ez.size - 2, 0))
    return idx, i, j, (dx, dz, bx, bz, ex, ez)


def _fit_exponential_from_bins(
    bx: np.ndarray,
    bz: np.ndarray,
    ex: np.ndarray,
    ez: np.ndarray,
    products: np.ndarray,
    weights: np.ndarray,
    *,
    variance_scale: float,
) -> tuple[float, float, float, float, tuple[tuple[float, float, float, int], ...]]:
    """Fit ``sigma^2 exp(-dx/Lx - dz/Lz)`` to binned pair products."""
    if ex.size < 2 or ez.size < 2:
        raise ValueError("need at least two lag bins per direction")
    cx = 0.5 * (ex[1:] + ex[:-1])
    cz = 0.5 * (ez[1:] + ez[:-1])
    cx = np.maximum(cx, 1e-6)
    cz = np.maximum(cz, 1e-6)
    lag_x: list[float] = []
    lag_z: list[float] = []
    cov: list[float] = []
    cnt: list[float] = []
    for a in range(cx.size):
        for b in range(cz.size):
            mask = (bx == a) & (bz == b)
            if not np.any(mask):
                continue
            lag_x.append(float(cx[a]))
            lag_z.append(float(cz[b]))
            cov.append(float(products[mask].mean()))
            cnt.append(float(np.sum(weights[mask])))
    if not cov:
        raise ValueError("no lag bins collected")
    lx = np.asarray(lag_x)
    lz = np.asarray(lag_z)
    cv = np.asarray(cov)
    wt = np.asarray(cnt)
    scale = max(float(variance_scale), 1e-300)

    def model(p: np.ndarray) -> np.ndarray:
        return p[0] * np.exp(-lx / max(p[1], 1e-6) - lz / max(p[2], 1e-6))

    upper_sigma2 = max(10.0 * scale, 1.5 * float(cv.max()), 1e-300)
    p0 = np.clip([max(float(cv.max()), 0.01 * scale), float(np.median(lx)),
                  float(np.median(lz))],
                 [1e-12, 1.01e-3, 1.01e-3],
                 [0.9 * upper_sigma2, 9.9e4, 9.9e4])
    res = least_squares(lambda p: (model(p) - cv) / scale, p0,
                        bounds=([0.0, 1e-3, 1e-3], [upper_sigma2, 1e5, 1e5]),
                        xtol=1e-14, ftol=1e-14, gtol=1e-14, x_scale="jac",
                        max_nfev=20000)
    pred = model(res.x)
    ss_res = float(np.sum(wt * (pred - cv) ** 2))
    ss_tot = float(np.sum(wt * (cv - np.average(cv, weights=wt)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    table = tuple((float(lx[k]), float(lz[k]), float(cv[k]), int(wt[k]))
                  for k in range(cv.size))
    return float(res.x[0]), float(res.x[1]), float(res.x[2]), float(r2), table


def estimate_covariance_ergodic(
    x: np.ndarray,
    z: np.ndarray,
    field: np.ndarray,
    *,
    n_lag_x: int = 10,
    n_lag_z: int = 6,
    max_cells: int = 1200,
    seed: int = 42,
) -> SpatialCovariance:
    """Spatial (ergodic) covariance of one residual field, exponential model.

    Used for the *model-form discrepancy* field ``a_DEM - a_profile``: one
    realisation is available, so the covariance is estimated by averaging over
    pairs of panels at equal lag (geostatistical standard).  No nugget is fitted
    here - the white part of the DEM scatter is quantified separately from the
    realisation ensemble.
    """
    f = np.asarray(field, dtype=NP_DTYPE).ravel()
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    if f.size != x.size:
        raise ValueError("field and coordinates differ in size")
    f = f - f.mean()
    idx, i, j, (dx, dz, bx, bz, ex, ez) = _pair_lag_bins(
        x, z, n_lag_x=n_lag_x, n_lag_z=n_lag_z, max_cells=max_cells, seed=seed)
    products = (f[idx][i] * f[idx][j])
    weights = np.ones_like(products)
    sigma2, lx, lz, r2, table = _fit_exponential_from_bins(
        bx, bz, ex, ez, products, weights, variance_scale=float(np.var(f)))
    return SpatialCovariance(sigma2=sigma2, length_x_m=lx, length_z_m=lz,
                             nugget2=0.0, r_squared=r2,
                             n_pairs=int(products.size), lag_table=table)


def sampling_noise_variance(realizations: np.ndarray) -> np.ndarray:
    """Per-cell variance across DEM realisations (pit / contact sampling noise)."""
    r = np.asarray(realizations, dtype=NP_DTYPE)
    if r.ndim == 3:
        r = r.reshape(r.shape[0], -1)
    if r.shape[0] < 2:
        raise ValueError("need at least two realisations")
    return np.var(r, axis=0, ddof=1)


def estimate_covariance_from_realizations(
    x: np.ndarray,
    z: np.ndarray,
    realizations: np.ndarray,
    *,
    n_lag_x: int = 8,
    n_lag_z: int = 6,
    max_cells: int = 1200,
    seed: int = 42,
) -> SpatialCovariance:
    """Total covariance of the residual field: smooth part + nugget.

    ``sigma^2, Lx, Lz`` come from the ensemble-mean pair products at non-zero
    lag; the nugget is the per-cell variance left unexplained by the smooth
    term (``tau^2 = Var_cell - sigma^2``, clipped at zero).  This is the
    covariance of a single realisation, i.e. what a survey of one hull sees.
    """
    r = np.asarray(realizations, dtype=NP_DTYPE)
    if r.ndim == 3:
        r = r.reshape(r.shape[0], -1)
    if r.ndim != 2:
        raise ValueError("realizations must be (K, N) or (K, n_z, n_x)")
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    if r.shape[1] != x.size:
        raise ValueError(f"realization length {r.shape[1]} != {x.size} points")
    if r.shape[0] < 2:
        raise ValueError("need at least 2 realisations to estimate covariance")
    idx, i, j, (dx, dz, bx, bz, ex, ez) = _pair_lag_bins(
        x, z, n_lag_x=n_lag_x, n_lag_z=n_lag_z, max_cells=max_cells, seed=seed)
    products = (r[:, idx][:, i] * r[:, idx][:, j]).mean(axis=0)
    weights = np.ones_like(products)
    variance_cell = float(np.mean(np.var(r, axis=0, ddof=1)))
    sigma2, lx, lz, r2, table = _fit_exponential_from_bins(
        bx, bz, ex, ez, products, weights,
        variance_scale=max(variance_cell, 1e-300))
    nugget2 = max(variance_cell - sigma2, 0.0)
    return SpatialCovariance(sigma2=sigma2, length_x_m=lx, length_z_m=lz,
                             nugget2=nugget2, r_squared=r2,
                             n_pairs=int(products.size), lag_table=table)


# ---------------------------------------------------------------------------
# 5. KL expansion of the separable covariance (analytic eigenpairs)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeparableKL:
    """Karhunen-Loeve expansion of a separable covariance on a product grid.

    For ``C = sigma^2 Ax (x) Az + tau^2 I`` the eigenpairs are
    ``lambda_ij = sigma^2 lx_i lz_j + tau^2`` and
    ``phi_ij = ux_i (x) uz_j`` exactly: the identity commutes with the Kronecker
    product, so no dense ``N x N`` eigendecomposition is needed.
    """

    eigenvalues: np.ndarray        # (n_modes,) descending
    eigenvectors: np.ndarray       # (N, n_modes)
    n_modes: int
    variance_explained: float
    total_variance: float
    truncation_error: float

    @property
    def n_points(self) -> int:
        return int(self.eigenvectors.shape[0])

    def covariance(self) -> np.ndarray:
        return (self.eigenvectors * self.eigenvalues[None, :]) @ self.eigenvectors.T

    def sample(self, n_samples: int, *, seed: int = 42) -> np.ndarray:
        """Zero-mean Gaussian field samples, shape ``(n_samples, N)``."""
        rng = np.random.default_rng(seed)
        z = rng.standard_normal((int(n_samples), self.n_modes))
        return z * np.sqrt(np.maximum(self.eigenvalues, 0.0))[None, :] @ self.eigenvectors.T

    def summary(self) -> dict[str, Any]:
        return {
            "n_modes": self.n_modes,
            "n_points": self.n_points,
            "variance_explained": self.variance_explained,
            "truncation_error": self.truncation_error,
            "total_variance": self.total_variance,
        }


def separable_kl_expansion(
    covariance: SpatialCovariance,
    x: np.ndarray,
    z: np.ndarray,
    *,
    rel_tolerance: float = 1e-8,
    max_modes: int | None = None,
) -> SeparableKL:
    """Exact KL expansion of the separable covariance on ``(x, z)`` samples."""
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    xu = np.unique(x)
    zu = np.unique(z)
    if xu.size * zu.size != x.size:
        raise ValueError("samples must lie on a product grid (unique x times unique z)")
    # samples must be ordered z-major, as produced by PanelGrid
    if not (np.allclose(x, np.tile(xu, zu.size))
            and np.allclose(z, np.repeat(zu, xu.size))):
        raise ValueError("samples must be ordered row-major with z as the row index")

    ax = np.exp(-np.abs(xu[:, None] - xu[None, :]) / max(covariance.length_x_m, 1e-12))
    az = np.exp(-np.abs(zu[:, None] - zu[None, :]) / max(covariance.length_z_m, 1e-12))
    lx, ux = np.linalg.eigh(ax)
    lz, uz = np.linalg.eigh(az)
    order_x = np.argsort(lx)[::-1]
    order_z = np.argsort(lz)[::-1]
    lx = np.clip(lx[order_x], 0.0, None)
    ux = ux[:, order_x]
    lz = np.clip(lz[order_z], 0.0, None)
    uz = uz[:, order_z]

    lam = covariance.sigma2 * np.outer(lx, lz).ravel() + covariance.nugget2
    order = np.argsort(lam)[::-1]
    lam = lam[order]

    vecs = np.empty((x.size, lam.size), dtype=NP_DTYPE)
    for k, flat in enumerate(order):
        a, b = divmod(int(flat), lz.size)      # a: x mode, b: z mode
        vecs[:, k] = np.outer(uz[:, b], ux[:, a]).ravel()

    total = float(np.trace(covariance.matrix(x, z))) / max(x.size, 1)
    total_energy = max(float(lam.sum()), 1e-300)
    if max_modes is not None:
        n_modes = int(max(1, min(max_modes, lam.size)))
    else:
        tail = np.cumsum(lam[::-1])[::-1]
        keep = np.nonzero(tail > rel_tolerance * total_energy)[0]
        n_modes = int(keep[-1] + 1) if keep.size else 1
        n_modes = int(max(4, min(n_modes, lam.size)))
    explained = float(lam[:n_modes].sum() / total_energy)
    tail_energy = float(lam[n_modes:].sum() / total_energy)
    return SeparableKL(
        eigenvalues=lam[:n_modes], eigenvectors=vecs[:, :n_modes],
        n_modes=n_modes, variance_explained=explained,
        total_variance=total, truncation_error=float(math.sqrt(max(tail_energy, 0.0))))


# ---------------------------------------------------------------------------
# 6. Simple kriging and conditional simulation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConditionalField:
    """Conditional mean/covariance and samples of a Gaussian residual field."""

    mean: np.ndarray                  # (N,)
    covariance: np.ndarray            # (N, N)
    variance: np.ndarray              # (N,)
    samples: np.ndarray               # (m, N)
    observation_indices: np.ndarray
    observation_values: np.ndarray
    observation_noise: float

    def summary(self) -> dict[str, Any]:
        return {
            "n_points": int(self.mean.size),
            "n_samples": int(self.samples.shape[0]),
            "n_observations": int(self.observation_indices.size),
            "mean_norm": float(np.linalg.norm(self.mean)),
            "mean_variance": float(np.mean(self.variance)),
            "max_variance": float(np.max(self.variance)),
            "observation_noise": self.observation_noise,
        }


def simple_kriging(
    covariance: SpatialCovariance,
    x: np.ndarray,
    z: np.ndarray,
    *,
    observation_indices: np.ndarray,
    observation_values: np.ndarray,
    observation_noise: float = 0.0,
    prior_mean: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Simple-kriging conditional mean ``(N,)`` and covariance ``(N, N)``.

    The prior mean of the residual is zero by construction (residuals are
    deviations from the ensemble mean); a non-zero ``prior_mean`` is supported
    for generality.
    """
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    idx = np.asarray(observation_indices, dtype=int).ravel()
    y = np.asarray(observation_values, dtype=NP_DTYPE).ravel()
    if idx.size != y.size:
        raise ValueError("observation_indices and observation_values differ in size")
    if idx.size and (idx.min() < 0 or idx.max() >= x.size):
        raise ValueError("observation index out of range")
    noise = float(observation_noise)
    if noise < 0.0:
        raise ValueError("observation_noise must be nonnegative")

    m0 = (np.zeros(x.size, dtype=NP_DTYPE) if prior_mean is None
          else np.asarray(prior_mean, dtype=NP_DTYPE).ravel())
    if m0.size != x.size:
        raise ValueError("prior_mean size mismatch")

    dx = x[:, None] - x[idx][None, :]
    dz = z[:, None] - z[idx][None, :]
    c_uo = covariance.evaluate(dx, dz)                       # (N, m)
    dxo = x[idx][:, None] - x[idx][None, :]
    dzo = z[idx][:, None] - z[idx][None, :]
    c_oo = covariance.evaluate(dxo, dzo) + (noise ** 2) * np.eye(idx.size)
    weights = np.linalg.solve(c_oo, c_uo.T)                  # (m, N)
    innovation = y - m0[idx]
    mean = m0 + weights.T @ innovation
    cond_cov = covariance.matrix(x, z) - c_uo @ weights
    cond_cov = 0.5 * (cond_cov + cond_cov.T)
    return mean, cond_cov


def simulate_conditional_field(
    covariance: SpatialCovariance,
    x: np.ndarray,
    z: np.ndarray,
    *,
    observation_indices: np.ndarray | None = None,
    observation_values: np.ndarray | None = None,
    observation_noise: float = 0.0,
    n_samples: int = 100,
    seed: int = 42,
    prior_mean: np.ndarray | None = None,
    eigen_rtol: float = 1e-9,
) -> ConditionalField:
    """Draw samples from the conditional Gaussian field.

    Without observations this is the unconditional KL sampler (fast: uses the
    analytic separable eigenpairs).  With observations the conditional
    covariance is dense; it is decomposed once and reused for every sample.
    """
    x = np.asarray(x, dtype=NP_DTYPE).ravel()
    z = np.asarray(z, dtype=NP_DTYPE).ravel()
    if n_samples < 1:
        raise ValueError("n_samples must be >= 1")
    if observation_indices is None:
        idx = np.zeros(0, dtype=int)
        y = np.zeros(0, dtype=NP_DTYPE)
    else:
        idx = np.asarray(observation_indices, dtype=int).ravel()
        if observation_values is None:
            raise ValueError("observation_values required with observation_indices")
        y = np.asarray(observation_values, dtype=NP_DTYPE).ravel()

    if idx.size == 0:
        kl = separable_kl_expansion(covariance, x, z)
        m0 = (np.zeros(x.size) if prior_mean is None
              else np.asarray(prior_mean, dtype=NP_DTYPE).ravel())
        samples = m0[None, :] + kl.sample(n_samples, seed=seed)
        return ConditionalField(
            mean=m0.copy(), covariance=kl.covariance(),
            variance=np.diag(kl.covariance()), samples=samples,
            observation_indices=idx, observation_values=y,
            observation_noise=float(observation_noise))

    mean, cov = simple_kriging(
        covariance, x, z, observation_indices=idx, observation_values=y,
        observation_noise=observation_noise, prior_mean=prior_mean)
    lam, vec = np.linalg.eigh(cov)
    lam = np.clip(lam, 0.0, None)
    keep = lam > eigen_rtol * max(float(lam.max()), 1e-300)
    lam, vec = lam[keep], vec[:, keep]
    rng = np.random.default_rng(seed)
    zc = rng.standard_normal((int(n_samples), lam.size))
    samples = mean[None, :] + (zc * np.sqrt(lam)[None, :]) @ vec.T
    return ConditionalField(
        mean=mean, covariance=cov, variance=np.diag(cov), samples=samples,
        observation_indices=idx, observation_values=y,
        observation_noise=float(observation_noise))


def analytic_conditional_variance(covariance: SpatialCovariance,
                                  dx: np.ndarray, dz: np.ndarray) -> np.ndarray:
    """Closed-form ``Var(r(x) | r(0) = 0)`` of a separable exponential field.

    For the noiseless covariance ``C(h) = sigma^2 exp(-|dx|/Lx - |dz|/Lz)``,
    simple kriging with one observation at the origin gives
    ``Var = sigma^2 (1 - exp(-2|dx|/Lx - 2|dz|/Lz))``.
    """
    dx = np.asarray(dx, dtype=NP_DTYPE)
    dz = np.asarray(dz, dtype=NP_DTYPE)
    corr2 = np.exp(-2.0 * np.abs(dx) / max(covariance.length_x_m, 1e-12)
                   - 2.0 * np.abs(dz) / max(covariance.length_z_m, 1e-12))
    return covariance.sigma2 * (1.0 - corr2)


# ---------------------------------------------------------------------------
# 7. Along-track ice field and the moving frame
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IceTrack:
    """Piecewise-constant ice concentration ``c(s)`` along the ship track.

    ``break_s_m`` are the cumulative distance breakpoints (length ``n+1``,
    starting at 0), ``concentration`` the value inside each interval, and
    ``speed_m_s`` the piecewise-constant speed per interval (a scalar is
    broadcast).  ``cumulative`` gives ``C(s) = ∫_0^s c`` exactly, which is the
    key ingredient of the closed-form accumulation.
    """

    break_s_m: np.ndarray
    concentration: np.ndarray
    speed_m_s: np.ndarray | float

    def __post_init__(self) -> None:
        edges = np.asarray(self.break_s_m, dtype=NP_DTYPE).ravel()
        c = np.asarray(self.concentration, dtype=NP_DTYPE).ravel()
        if edges.size < 2:
            raise ValueError("need at least two distance breakpoints")
        if np.any(np.diff(edges) <= 0.0):
            raise ValueError("break_s_m must be strictly increasing")
        if float(edges[0]) != 0.0:
            raise ValueError("the track must start at s = 0")
        if c.size != edges.size - 1:
            raise ValueError("concentration must have one value per interval")
        if np.any(c < 0.0) or np.any(c > 1.0):
            raise ValueError("concentration must lie in [0, 1]")
        v = np.asarray(self.speed_m_s, dtype=NP_DTYPE).ravel()
        if v.size == 1:
            v = np.full(c.size, float(v[0]), dtype=NP_DTYPE)
        if v.size != c.size:
            raise ValueError("speed must be scalar or one value per interval")
        if np.any(v <= 0.0):
            raise ValueError("speed must be positive")

    @property
    def edges(self) -> np.ndarray:
        return np.asarray(self.break_s_m, dtype=NP_DTYPE).ravel()

    @property
    def concentrations(self) -> np.ndarray:
        return np.asarray(self.concentration, dtype=NP_DTYPE).ravel()

    @property
    def speeds(self) -> np.ndarray:
        return np.asarray(self.speed_m_s, dtype=NP_DTYPE).ravel()

    @property
    def total_distance_m(self) -> float:
        return float(self.edges[-1])

    @property
    def duration_s(self) -> float:
        return float(np.sum(np.diff(self.edges) / self.speeds))

    @property
    def time_breaks_s(self) -> np.ndarray:
        return np.concatenate(([0.0], np.cumsum(np.diff(self.edges) / self.speeds)))

    def cumulative(self, s: np.ndarray) -> np.ndarray:
        """``C(s) = ∫_0^s c(u) du``; beyond the track the last value continues."""
        s = np.asarray(s, dtype=NP_DTYPE)
        edges = self.edges
        c = self.concentrations
        out = np.zeros_like(s)
        for k in range(c.size):
            length = edges[k + 1] - edges[k]
            out = out + c[k] * np.clip(s - edges[k], 0.0, length)
        out = out + c[-1] * np.clip(s - edges[-1], 0.0, None)
        return out

    def concentration_at(self, s: np.ndarray) -> np.ndarray:
        s = np.asarray(s, dtype=NP_DTYPE)
        idx = np.searchsorted(self.edges, s, side="right") - 1
        idx = np.clip(idx, 0, self.concentrations.size - 1)
        return self.concentrations[idx]

    def position(self, t: np.ndarray) -> np.ndarray:
        """Ship position ``s(t)`` (exact piecewise-linear inverse of ``T(s)``)."""
        t = np.asarray(t, dtype=NP_DTYPE)
        return np.interp(t, self.time_breaks_s, self.edges,
                         left=0.0,
                         right=self.edges[-1] + (t - self.time_breaks_s[-1]) * self.speeds[-1])

    def exposure(self, t_end: float, offsets: np.ndarray) -> np.ndarray:
        """``∫_0^t_end c(s(τ) + ξ_i) dτ`` for every panel offset ``ξ_i``.

        Split into track intervals, inside which the speed is constant:
        ``∫_0^{t} c(s(τ)+ξ)dτ = Σ_j (1/V_j) ∫_{I_j} c(u+ξ) du`` with
        ``I_j = [s_j, min(s(t), s_{j+1})]``; every integral is a difference of
        the exact ``C(·)``, so the result is exact for piecewise-constant data.
        """
        xi = np.asarray(offsets, dtype=NP_DTYPE).ravel()
        s_end = float(self.position(np.asarray([t_end], dtype=NP_DTYPE))[0])
        out = np.zeros(xi.size, dtype=NP_DTYPE)
        edges = self.edges
        c_speed = self.speeds
        for k in range(c_speed.size):
            lo = edges[k]
            hi = min(max(s_end, lo), edges[k + 1])
            if hi <= lo:
                continue
            out += (self.cumulative(hi + xi) - self.cumulative(lo + xi)) / c_speed[k]
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "total_distance_m": self.total_distance_m,
            "duration_s": self.duration_s,
            "mean_concentration": float(self.cumulative(np.array(self.total_distance_m))
                                        / self.total_distance_m),
            "mean_speed_m_s": float(self.total_distance_m / self.duration_s),
            "n_intervals": int(self.concentrations.size),
            "intervals": [
                {"s_lo_m": float(self.edges[k]), "s_hi_m": float(self.edges[k + 1]),
                 "concentration": float(self.concentrations[k]),
                 "speed_m_s": float(self.speeds[k])}
                for k in range(self.concentrations.size)
            ],
        }


def ice_track_from_voyage(voyage: VoyageProfile,
                          *,
                          open_water_tail_m: float = 0.0) -> IceTrack:
    """Build the along-track ice concentration from a :class:`VoyageProfile`."""
    legs = voyage.legs_per_voyage()
    distances = np.asarray([leg.distance_m for leg in legs], dtype=NP_DTYPE)
    concs = np.asarray([leg.concentration for leg in legs], dtype=NP_DTYPE)
    speeds = np.asarray([leg.speed_m_s for leg in legs], dtype=NP_DTYPE)
    if open_water_tail_m > 0.0:
        distances = np.append(distances, float(open_water_tail_m))
        concs = np.append(concs, 0.0)
        speeds = np.append(speeds, float(speeds[-1]))
    edges = np.concatenate(([0.0], np.cumsum(distances)))
    return IceTrack(break_s_m=edges, concentration=concs, speed_m_s=speeds)


def uniform_track(concentration: float, distance_m: float, speed_m_s: float) -> IceTrack:
    """Single-interval track, used for the analytic anchors."""
    return IceTrack(break_s_m=np.array([0.0, float(distance_m)]),
                    concentration=np.array([float(concentration)]),
                    speed_m_s=float(speed_m_s))


# ---------------------------------------------------------------------------
# 8. Spatio-temporal evolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AbrasionFieldTimeSeries:
    """Evolved abrasion depth field ``d(x, z, t)`` on the hull panels."""

    panels: PanelGrid
    track: IceTrack
    profile: SeparableActivityProfile
    time_s: np.ndarray
    depth_m: np.ndarray                 # (n_t, N)
    rate_m_s: np.ndarray                # (n_t, N)
    reference_concentration: float
    residual_rate_m_s: np.ndarray | None = None

    @property
    def n_time_steps(self) -> int:
        return int(self.time_s.size)

    def depth_grid(self, index: int = -1) -> np.ndarray:
        return self.depth_m[index].reshape(self.panels.shape)

    def depth_at(self, t: float) -> np.ndarray:
        t = float(t)
        if t <= self.time_s[0]:
            return self.depth_m[0].copy()
        if t >= self.time_s[-1]:
            return self.depth_m[-1].copy()
        i = int(np.searchsorted(self.time_s, t)) - 1
        i = max(0, min(i, self.n_time_steps - 2))
        t0, t1 = float(self.time_s[i]), float(self.time_s[i + 1])
        w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return (1.0 - w) * self.depth_m[i] + w * self.depth_m[i + 1]

    @property
    def volume_removed_m3(self) -> float:
        return float(np.sum(self.depth_m[-1] * self.panels.areas_flat))

    def regional_statistics(self, *, percentile: float = 95.0) -> dict[str, Any]:
        """Area-weighted depth statistics per hull region at the final time."""
        depth = self.depth_m[-1]
        areas = self.panels.areas_flat
        x = self.panels.panel_x
        u = x / self.panels.hull_length_m
        zones = {
            "bow": (0.80, 1.00),
            "fore_shoulder": (0.60, 0.80),
            "midbody": (0.30, 0.60),
            "aft_shoulder": (0.15, 0.30),
            "stern": (0.00, 0.15),
        }
        out: dict[str, Any] = {}
        for name, (lo, hi) in zones.items():
            mask = (u >= lo) & (u < hi)
            if not np.any(mask):
                continue
            w = areas[mask]
            d = depth[mask]
            wsum = float(w.sum())
            mean = float(np.sum(d * w) / wsum) if wsum > 0 else 0.0
            out[name] = {
                "x_fraction_range": [lo, hi],
                "area_m2": wsum,
                "mean_depth_um": mean * 1e6,
                "p95_depth_um": float(area_weighted_quantile(d, w, percentile)) * 1e6,
                "max_depth_um": float(np.max(d)) * 1e6,
                "volume_removed_m3": float(np.sum(d * w)),
            }
        return out

    def summary(self) -> dict[str, Any]:
        depth = self.depth_m[-1]
        areas = self.panels.areas_flat
        return {
            "n_panels": int(self.panels.n_panels),
            "n_time_steps": self.n_time_steps,
            "duration_s": float(self.time_s[-1] - self.time_s[0]),
            "track": self.track.summary(),
            "max_depth_um": float(np.max(depth)) * 1e6,
            "mean_depth_um": float(np.sum(depth * areas) / areas.sum()) * 1e6,
            "p95_depth_um_area_weighted": float(
                area_weighted_quantile(depth, areas, 95.0)) * 1e6,
            "volume_removed_m3": self.volume_removed_m3,
            "regions": self.regional_statistics(),
            "reference_concentration": self.reference_concentration,
            "has_residual_field": self.residual_rate_m_s is not None,
        }


def evolve_abrasion_field(
    profile: SeparableActivityProfile,
    panels: PanelGrid,
    track: IceTrack,
    *,
    n_time_steps: int = 101,
    duration_s: float | None = None,
    reference_concentration: float = 1.0,
    residual_rate_m_s: np.ndarray | None = None,
    block_streamwise_m: float | None = None,
) -> AbrasionFieldTimeSeries:
    """Evolve the abrasion depth field along the track (moving frame).

    Panel ``i`` samples the ice concentration at ``s(t) + xi_i``: a bow panel
    meets an ice edge ``xi_i / V`` earlier than midship, which is the physical
    content of "the hull moves through the ice field".  With
    ``d_i(t) = a_i / (c_ref V) [C(Vt + xi_i) - C(xi_i)]`` the accumulation is
    exact for piecewise-constant data.

    ``block_streamwise_m`` optionally freezes every panel whose ``xi`` is within
    one block of the midship value (a crude "bulk panel" model); it is kept for
    sensitivity studies and defaults to the exact per-panel offsets.
    """
    if n_time_steps < 2:
        raise ValueError("n_time_steps must be >= 2")
    if not reference_concentration > 0.0:
        raise ValueError("reference_concentration must be positive")

    a = profile.evaluate_on_grid(panels).ravel()
    if residual_rate_m_s is not None:
        res = np.asarray(residual_rate_m_s, dtype=NP_DTYPE).ravel()
        if res.size != panels.n_panels:
            raise ValueError("residual_rate_m_s must have one value per panel")
        a = np.maximum(a + res, 0.0)

    offsets = panels.streamwise_offset_m.copy()
    if block_streamwise_m is not None:
        step = float(block_streamwise_m)
        offsets = np.round(offsets / step) * step

    total = float(track.duration_s if duration_s is None else duration_s)
    time_s = np.linspace(0.0, total, int(n_time_steps), dtype=NP_DTYPE)

    depth = np.empty((time_s.size, panels.n_panels), dtype=NP_DTYPE)
    rate = np.empty_like(depth)
    for k, t in enumerate(time_s):
        exposure = track.exposure(float(t), offsets)
        depth[k] = a * exposure / reference_concentration
        position = float(track.position(np.asarray([t]))[0])
        rate[k] = a * track.concentration_at(position + offsets) / reference_concentration

    return AbrasionFieldTimeSeries(
        panels=panels, track=track, profile=profile, time_s=time_s,
        depth_m=depth, rate_m_s=rate,
        reference_concentration=float(reference_concentration),
        residual_rate_m_s=None if residual_rate_m_s is None else
        np.asarray(residual_rate_m_s, dtype=NP_DTYPE).ravel())


def evolve_time_stepped(
    profile: SeparableActivityProfile,
    panels: PanelGrid,
    track: IceTrack,
    *,
    n_time_steps: int = 101,
    duration_s: float | None = None,
    reference_concentration: float = 1.0,
    residual_rate_m_s: np.ndarray | None = None,
    scheme: str = "trapezoid",
) -> AbrasionFieldTimeSeries:
    """Time-stepped integration of the same ODE (used to verify the closed form).

    ``d_i^{k+1} = d_i^k + Δt/2 (r_i^k + r_i^{k+1})`` (trapezoid) or
    ``d_i^k + Δt r_i^k`` (explicit Euler).  The rate history is evaluated
    pointwise, so this route shares no algebra with the closed-form integral.
    """
    if scheme not in ("trapezoid", "euler"):
        raise ValueError("scheme must be 'trapezoid' or 'euler'")
    if n_time_steps < 2:
        raise ValueError("n_time_steps must be >= 2")

    a = profile.evaluate_on_grid(panels).ravel()
    if residual_rate_m_s is not None:
        a = np.maximum(a + np.asarray(residual_rate_m_s, dtype=NP_DTYPE).ravel(), 0.0)
    offsets = panels.streamwise_offset_m
    total = float(track.duration_s if duration_s is None else duration_s)
    time_s = np.linspace(0.0, total, int(n_time_steps), dtype=NP_DTYPE)

    depth = np.zeros((time_s.size, panels.n_panels), dtype=NP_DTYPE)
    rate = np.empty_like(depth)
    for k, t in enumerate(time_s):
        position = float(track.position(np.asarray([t]))[0])
        rate[k] = a * track.concentration_at(position + offsets) / reference_concentration
    dt = float(time_s[1] - time_s[0])
    for k in range(time_s.size - 1):
        if scheme == "trapezoid":
            depth[k + 1] = depth[k] + 0.5 * dt * (rate[k] + rate[k + 1])
        else:
            depth[k + 1] = depth[k] + dt * rate[k]
    return AbrasionFieldTimeSeries(
        panels=panels, track=track, profile=profile, time_s=time_s,
        depth_m=depth, rate_m_s=rate,
        reference_concentration=float(reference_concentration),
        residual_rate_m_s=None if residual_rate_m_s is None else
        np.asarray(residual_rate_m_s, dtype=NP_DTYPE).ravel())


# ---------------------------------------------------------------------------
# 9. Volume ledger, quantiles and the N02 P95 interface
# ---------------------------------------------------------------------------

def _panel_rates(series: AbrasionFieldTimeSeries) -> np.ndarray:
    a = series.profile.evaluate_on_grid(series.panels).ravel()
    if series.residual_rate_m_s is not None:
        a = np.maximum(a + series.residual_rate_m_s, 0.0)
    return a


def volume_ledger(series: AbrasionFieldTimeSeries, *, rtol: float = 1e-6) -> dict[str, Any]:
    """Removed volume from the depth field vs from the rate model.

    The depth route reads the stored time series; the rate route integrates the
    per-panel rate model over the whole track with the exact ``C(s)`` function.
    Because the accumulation is a single multiplicative map, this is a
    conservation/consistency ledger (it detects lost or double-counted panels),
    not a grid-convergence test - stated explicitly to avoid over-claiming.
    """
    areas = series.panels.areas_flat
    depth = series.depth_m[-1]
    v_depth = float(np.sum(depth * areas))

    a = _panel_rates(series)
    exposure = series.track.exposure(float(series.time_s[-1]),
                                     series.panels.streamwise_offset_m)
    v_rate = float(np.sum(a * exposure / series.reference_concentration * areas))
    denom = max(abs(v_depth), abs(v_rate), 1e-30)
    rel = abs(v_depth - v_rate) / denom
    return {
        "volume_from_depth_m3": v_depth,
        "volume_from_rate_model_m3": v_rate,
        "relative_error": rel,
        "passed": bool(rel < rtol),
        "tolerance": rtol,
        "note": "consistency ledger for the accumulation (depth field vs rate model)",
    }


def area_weighted_quantile(values: np.ndarray, weights: np.ndarray,
                           q: float) -> float:
    """Quantile of ``values`` weighted by ``weights`` (Hazen plotting positions).

    With equal weights and large ``n`` this converges to ``numpy.percentile``;
    the weighted form is needed because the panel areas differ near the ends of
    the band.
    """
    v = np.asarray(values, dtype=NP_DTYPE).ravel()
    w = np.asarray(weights, dtype=NP_DTYPE).ravel()
    if v.size != w.size:
        raise ValueError("values and weights must have the same size")
    if not 0.0 <= float(q) <= 100.0:
        raise ValueError("q must lie in [0, 100]")
    order = np.argsort(v)
    v = v[order]
    w = np.clip(w[order], 0.0, None)
    total = float(w.sum())
    if total <= 0.0:
        raise ValueError("weights sum to zero")
    positions = (np.cumsum(w) - 0.5 * w) / total
    return float(np.interp(float(q) / 100.0, positions, v))


def extract_p95_statistics(series: AbrasionFieldTimeSeries,
                           *, percentile: float = 95.0) -> dict[str, Any]:
    """Area-weighted and unweighted quantiles of the evolved depth field."""
    depth = series.depth_m[-1]
    areas = series.panels.areas_flat
    weighted = area_weighted_quantile(depth, areas, percentile)
    unweighted = float(np.percentile(depth, percentile))
    x = series.panels.panel_x
    u = x / series.panels.hull_length_m
    above = depth >= weighted
    return {
        "percentile": percentile,
        "p95_depth_m_area_weighted": weighted,
        "p95_depth_um_area_weighted": weighted * 1e6,
        "p95_depth_um_unweighted": unweighted * 1e6,
        "max_depth_um": float(np.max(depth)) * 1e6,
        "mean_depth_um_area_weighted": float(np.sum(depth * areas) / areas.sum()) * 1e6,
        "fraction_area_above_p95": float(np.sum(areas[above]) / areas.sum()),
        "mean_x_fraction_above_p95": float(np.sum(x[above] * areas[above])
                                           / max(np.sum(areas[above]), 1e-30)),
        "volume_removed_m3": series.volume_removed_m3,
        "regions": series.regional_statistics(percentile=percentile),
    }


@dataclass(frozen=True)
class P95Connection:
    """Comparison of a field-extracted P95 with the N02 direct P95 statistic."""

    field_p95_m: float
    n02_p95_m: float
    relative_error: float
    tolerance: float
    passed: bool

    def summary(self) -> dict[str, Any]:
        return {
            "field_p95_um": self.field_p95_m * 1e6,
            "n02_p95_um": self.n02_p95_m * 1e6,
            "relative_error": self.relative_error,
            "tolerance": self.tolerance,
            "passed": self.passed,
        }


def connect_p95_to_n02(field_p95_m: float, n02_p95_m: float,
                       *, tolerance: float = 0.10) -> P95Connection:
    """Compare the field-extracted P95 with the N02 (single-point) P95."""
    ref = max(abs(float(n02_p95_m)), 1e-30)
    rel = abs(float(field_p95_m) - float(n02_p95_m)) / ref
    return P95Connection(field_p95_m=float(field_p95_m),
                         n02_p95_m=float(n02_p95_m),
                         relative_error=float(rel), tolerance=float(tolerance),
                         passed=bool(rel < tolerance))


# ---------------------------------------------------------------------------
# 10. Full N05 verification suite
# ---------------------------------------------------------------------------

def _uniform_profile(rate_m_s: float, hull_length_m: float,
                     hull_draught_m: float) -> SeparableActivityProfile:
    """Profile that is identically ``rate_m_s`` (used for analytic anchors)."""
    return SeparableActivityProfile(
        scale_m_s=float(rate_m_s), background=1.0, decay_amplitude=0.0,
        decay_length=1.0, x_modes=((0.0, 0.5, 0.1),),
        waterline_offset_m=0.0, sigma_below_m=1.0e9, sigma_above_m=1.0e9,
        hull_length_m=hull_length_m, hull_draught_m=hull_draught_m)


def run_n05_verification(
    *,
    n_panels_x: int = 40,
    n_panels_z: int = 25,
    n_time_steps: int = 100,
    n_samples: int = 100,
    n_realizations: int = 8,
    n_events_per_leg: int = 20000,
    n_survey_panels: int = 60,
    seed: int = 42,
) -> dict[str, Any]:
    """Run criteria C1-C6 of the N05 node and return the measured numbers."""
    import time as _time

    t0 = _time.perf_counter()
    grid = build_waterline_grid()
    voyage = reference_baltic_voyage()
    panels = build_panel_grid(grid=grid, n_x=n_panels_x, n_z=n_panels_z)
    results: dict[str, Any] = {
        "node": "P66-W2-N05",
        "config": {
            "n_panels_x": n_panels_x, "n_panels_z": n_panels_z,
            "n_time_steps": n_time_steps, "n_samples": n_samples,
            "n_realizations": n_realizations,
            "n_events_per_leg": n_events_per_leg,
            "n_survey_panels": n_survey_panels, "seed": seed,
        },
        "geometry": {
            "hull_length_m": panels.hull_length_m,
            "hull_draught_m": panels.hull_draught_m,
            "band_area_one_side_m2": panels.total_area_m2,
            "dem_grid_shape": list(grid.shape),
            "geometry_audit": {k: v for k, v in grid.audit.items()
                               if isinstance(v, (bool, int, float, str))},
        },
        "criteria": {},
    }

    # ---------------- DEM ensemble -----------------------------------------
    ens = dem_activity_rate_field(
        grid=grid, voyage=voyage, n_realizations=n_realizations,
        n_events_per_leg=n_events_per_leg)
    results["dem_ensemble"] = ens.summary()

    panel_mean_rate = coarsen_field(ens.mean_rate, grid, panels)
    panel_realizations = np.array([coarsen_field(ens.rate_realizations[k], grid, panels)
                                   for k in range(ens.n_realizations)])

    # ---------------- C1: profile fit --------------------------------------
    fit = fit_separable_profile(
        panels.panel_x, panels.panel_z, panel_mean_rate.ravel(),
        hull_length_m=panels.hull_length_m,
        hull_draught_m=panels.hull_draught_m)
    fine_pred = fit.profile.evaluate(
        np.tile(0.5 * (np.asarray(grid.x_edges[1:]) + np.asarray(grid.x_edges[:-1])),
                int(grid.shape[0])),
        np.repeat(0.5 * (np.asarray(grid.z_edges[1:]) + np.asarray(grid.z_edges[:-1])),
                  int(grid.shape[1])))
    # fine-grid R² (honest: independent of the panel coarsening)
    a_fine = ens.mean_rate.ravel()
    ss_res_fine = float(np.sum((a_fine - fine_pred) ** 2))
    ss_tot_fine = float(np.sum((a_fine - a_fine.mean()) ** 2))
    r2_fine = 1.0 - ss_res_fine / ss_tot_fine if ss_tot_fine > 0 else 0.0
    r2_single = []
    for k in range(ens.n_realizations):
        pred_k = fit.profile.evaluate(panels.panel_x, panels.panel_z)
        dk = coarsen_field(ens.rate_realizations[k], grid, panels).ravel()
        ssr = float(np.sum((dk - pred_k) ** 2))
        sst = float(np.sum((dk - dk.mean()) ** 2))
        r2_single.append(1.0 - ssr / sst if sst > 0 else 0.0)
    results["criteria"]["C1"] = {
        "description": "deterministic activity profile fitted to the DEM ensemble-mean rate field",
        "r_squared_panel_grid": fit.r_squared,
        "r_squared_dem_grid": float(r2_fine),
        "r_squared_single_realizations": [float(v) for v in r2_single],
        "rmse_um_per_hour": fit.rmse_m_s * 1e6 * 3600.0,
        "n_points": fit.n_points,
        "n_parameters": fit.n_parameters,
        "converged": fit.converged,
        "target": 0.95,
        "passed": bool(fit.r_squared > 0.95),
        "profile_params": fit.params,
    }
    profile = fit.profile

    # ---------------- C2: covariance + conditional simulation --------------
    residuals = panel_realizations - panel_mean_rate[None, :].reshape(-1)[None, :]
    covariance = estimate_covariance_from_realizations(
        panels.panel_x, panels.panel_z, residuals.reshape(ens.n_realizations, -1))
    kl = separable_kl_expansion(covariance, panels.panel_x, panels.panel_z)

    rng = np.random.default_rng(seed)
    n_obs = int(min(n_survey_panels, panels.n_panels - 1))
    obs_idx = np.sort(rng.choice(panels.n_panels, size=n_obs, replace=False))
    obs_true = residuals.reshape(ens.n_realizations, -1)[0].copy()

    cond = simulate_conditional_field(
        covariance, panels.panel_x, panels.panel_z,
        observation_indices=obs_idx, observation_values=obs_true[obs_idx],
        n_samples=n_samples, seed=seed)

    # (a) exact interpolation without nugget / observation noise
    noiseless = SpatialCovariance(sigma2=covariance.sigma2,
                                  length_x_m=covariance.length_x_m,
                                  length_z_m=covariance.length_z_m, nugget2=0.0)
    cond_nl = simulate_conditional_field(
        noiseless, panels.panel_x, panels.panel_z,
        observation_indices=obs_idx, observation_values=obs_true[obs_idx],
        n_samples=2, seed=seed)
    interp_err = float(np.max(np.abs(cond_nl.samples[:, obs_idx] - obs_true[obs_idx])))
    interp_scale = max(float(np.max(np.abs(obs_true[obs_idx]))), 1e-30)

    # (b) analytic conditional variance (single observation at the origin)
    one_idx = np.array([0])
    _, cov_one = simple_kriging(noiseless, panels.panel_x, panels.panel_z,
                               observation_indices=one_idx,
                               observation_values=np.zeros(1))
    analytic = analytic_conditional_variance(
        noiseless, panels.panel_x - panels.panel_x[0],
        panels.panel_z - panels.panel_z[0])
    cond_var_err = float(np.max(np.abs(np.diag(cov_one) - analytic))
                         / max(covariance.sigma2, 1e-30))

    # (c) ensemble statistics vs target
    emp_mean = cond.samples.mean(axis=0)
    emp_var = cond.samples.var(axis=0, ddof=1)
    mean_norm = max(float(np.linalg.norm(cond.mean)), 1e-30)
    mean_err = float(np.linalg.norm(emp_mean - cond.mean) / mean_norm)
    var_err = float(abs(emp_var.mean() - cond.variance.mean())
                    / max(cond.variance.mean(), 1e-30))
    cov_emp = np.cov(cond.samples, rowvar=False)
    cov_err = float(np.linalg.norm(cov_emp - cond.covariance)
                    / max(np.linalg.norm(cond.covariance), 1e-30))
    sampling_noise = float(np.mean(np.std(cond.samples, axis=0, ddof=1))
                           / math.sqrt(n_samples) / max(float(np.mean(np.abs(cond.mean))), 1e-30))
    results["criteria"]["C2"] = {
        "description": "conditional simulation of the residual field (simple kriging + KL)",
        "covariance": covariance.summary(),
        "kl": kl.summary(),
        "n_observations": int(n_obs),
        "interpolation_max_abs_error": interp_err,
        "interpolation_relative_error": interp_err / interp_scale,
        "analytic_conditional_variance_rel_error": cond_var_err,
        "ensemble_mean_relative_error": mean_err,
        "ensemble_variance_relative_error": var_err,
        "ensemble_covariance_frobenius_relative_error": cov_err,
        "sampling_noise_reference": sampling_noise,
        "n_samples": int(n_samples),
        "target_mean_error": 0.05,
        "target_variance_error": 0.05,
        "passed": bool(mean_err < 0.05 and var_err < 0.05 and interp_err / interp_scale < 1e-6
                       and cond_var_err < 0.05),
    }

    # ---------------- C5: uniform anchor (before C3, gives the uniform field)
    uniform_rate = 1.0e-9                     # 1 nm/s ~ 3.6 µm/hour
    uniform_profile = _uniform_profile(uniform_rate, panels.hull_length_m,
                                       panels.hull_draught_m)
    track_uniform = uniform_track(1.0, 50_000.0, 5.0)
    t_uniform = np.linspace(0.0, 3_600.0, n_time_steps)
    uniform_field = evolve_abrasion_field(
        uniform_profile, panels, track_uniform, n_time_steps=n_time_steps)
    expected = uniform_rate * uniform_field.time_s[:, None]
    uniform_err = float(np.max(np.abs(uniform_field.depth_m - expected))
                        / max(float(expected.max()), 1e-30))
    results["criteria"]["C5"] = {
        "description": "uniform ice field: d(t) = a * t on every panel",
        "rate_m_s": uniform_rate,
        "max_relative_error": uniform_err,
        "target": 0.01,
        "passed": bool(uniform_err < 0.01),
    }

    # ---------------- C3: volume ledger on the real voyage ----------------
    track = ice_track_from_voyage(voyage)
    series = evolve_abrasion_field(profile, panels, track,
                                   n_time_steps=n_time_steps)
    ledger = volume_ledger(series)
    results["criteria"]["C3"] = {
        "description": "volume ledger of the spatio-temporal accumulation",
        "relative_error": ledger["relative_error"],
        "volume_from_depth_m3": ledger["volume_from_depth_m3"],
        "volume_from_rate_model_m3": ledger["volume_from_rate_model_m3"],
        "target": 1e-6,
        "passed": bool(ledger["passed"]),
    }

    # ---------------- C4: P95 vs N02 --------------------------------------
    duration = ens.voyage_duration_s
    n02_depth = ens.mean_rate * duration                     # (n_z, n_x) m
    n02_cells = n02_depth.ravel()
    n02_weights = np.broadcast_to(
        (np.diff(np.asarray(grid.z_edges))[:, None]
         * np.diff(np.asarray(grid.x_edges))[None, :]), grid.shape).ravel()
    n02_p95 = area_weighted_quantile(n02_cells, n02_weights, 95.0)
    field_uniform_track = evolve_abrasion_field(
        profile, panels, uniform_track(1.0, 1.0e5, 5.0),
        n_time_steps=3, duration_s=duration)
    field_p95 = area_weighted_quantile(field_uniform_track.depth_m[-1],
                                       panels.areas_flat, 95.0)
    conn = connect_p95_to_n02(field_p95, n02_p95)
    results["criteria"]["C4"] = {
        "description": "P95 extracted from the evolved field vs the N02 direct P95",
        "field_p95_um": field_p95 * 1e6,
        "n02_p95_um": n02_p95 * 1e6,
        "relative_error": conn.relative_error,
        "target": 0.10,
        "passed": bool(conn.passed),
        "n02_definition": ("P95 of the one-voyage DEM ensemble-mean wear-depth field "
                           "on the 0.25 m waterline band grid, area weighted"),
        "field_definition": ("P95 of the evolved field after one voyage at reference "
                             "concentration 1.0 on the 40x25 panel grid, area weighted"),
    }

    # ---------------- C6: performance -------------------------------------
    tperf = _time.perf_counter()
    big = build_panel_grid(grid=grid, n_x=50, n_z=20)      # 1000 panels
    big_series = evolve_abrasion_field(profile, big, track,
                                       n_time_steps=n_time_steps)
    _ = volume_ledger(big_series)
    perf = _time.perf_counter() - tperf
    cond_time = None
    tcond = _time.perf_counter()
    _ = simulate_conditional_field(
        covariance, big.panel_x, big.panel_z,
        observation_indices=obs_idx[obs_idx < big.n_panels],
        observation_values=obs_true[obs_idx[obs_idx < big.n_panels]],
        n_samples=n_samples, seed=seed)
    cond_time = _time.perf_counter() - tcond
    results["criteria"]["C6"] = {
        "description": "1000 panels x 100 steps evolution + ledger on one CPU core",
        "n_panels": int(big.n_panels),
        "n_time_steps": int(n_time_steps),
        "evolution_cpu_time_s": perf,
        "conditional_simulation_cpu_time_s": cond_time,
        "target_s": 60.0,
        "passed": bool(perf + cond_time < 60.0),
    }

    # ---------------- auxiliary checks ------------------------------------
    # moving-frame onset time: a single ice edge at s0 is met by panel i at
    # t_i = (s0 - xi_i)/V
    speed = 5.0
    s0 = 20_000.0
    track_edge = IceTrack(break_s_m=np.array([0.0, s0, s0 + 10_000.0]),
                          concentration=np.array([0.0, 1.0]),
                          speed_m_s=speed)
    edge_profile = _uniform_profile(1.0e-9, panels.hull_length_m,
                                    panels.hull_draught_m)
    edge_series = evolve_abrasion_field(edge_profile, panels, track_edge,
                                        n_time_steps=4,
                                        duration_s=(s0 + 10_000.0) / speed)
    times = edge_series.time_s
    onset_error = 0.0
    for i in (0, panels.n_panels // 2, panels.n_panels - 1):
        expected_t = (s0 - panels.streamwise_offset_m[i]) / speed
        before = edge_series.depth_m[times < expected_t - 1e-9, i]
        after = edge_series.depth_m[times > expected_t + 1e-9, i]
        if before.size:
            onset_error = max(onset_error,
                              float(np.max(np.abs(before))) / max(float(np.max(after)), 1e-30))
        if after.size:
            onset_error = max(onset_error,
                              float(np.max(np.abs(after - 1.0e-9 * (times[times > expected_t + 1e-9]
                                                                   - expected_t)))) /
                              max(1.0e-9 * (times[-1] - expected_t), 1e-30))
    results["auxiliary"] = {
        "moving_frame_onset_relative_error": onset_error,
        "regional_statistics": series.regional_statistics(),
        "field_summary": series.summary(),
        "n02_volume_one_voyage_m3": float(np.sum(n02_depth * n02_weights.reshape(grid.shape))),
        "field_volume_one_voyage_m3": float(field_uniform_track.volume_removed_m3),
        "timing_total_s": _time.perf_counter() - t0,
    }
    vol_err = abs(results["auxiliary"]["field_volume_one_voyage_m3"]
                  - results["auxiliary"]["n02_volume_one_voyage_m3"]) / max(
        results["auxiliary"]["n02_volume_one_voyage_m3"], 1e-30)
    results["auxiliary"]["one_voyage_volume_relative_error"] = vol_err

    # time-step convergence: closed form vs time-stepped integration
    conv: list[dict[str, float]] = []
    for nts in (11, 21, 41, 81):
        stepped = evolve_time_stepped(profile, panels, track, n_time_steps=nts)
        closed = evolve_abrasion_field(profile, panels, track, n_time_steps=nts)
        err = float(np.max(np.abs(stepped.depth_m - closed.depth_m))
                    / max(float(np.max(closed.depth_m)), 1e-30))
        conv.append({"n_time_steps": float(nts), "rel_error": err})
    ratios = [conv[k]["rel_error"] / max(conv[k + 1]["rel_error"], 1e-30)
              for k in range(len(conv) - 1)]
    results["auxiliary"]["time_step_convergence"] = conv
    results["auxiliary"]["time_step_convergence_ratios"] = ratios

    # grid convergence of the field P95 (same profile, refined panels)
    p95_grid = []
    for (nx, nz) in ((20, 12), (40, 25), (80, 50)):
        pg = build_panel_grid(grid=grid, n_x=nx, n_z=nz)
        s = evolve_abrasion_field(profile, pg, track, n_time_steps=2)
        p95_grid.append({
            "n_panels": int(pg.n_panels),
            "p95_depth_um": area_weighted_quantile(s.depth_m[-1], pg.areas_flat, 95.0) * 1e6,
        })
    results["auxiliary"]["p95_grid_convergence"] = p95_grid

    results["all_criteria_passed"] = all(
        bool(c.get("passed", False)) for c in results["criteria"].values())
    results["timing_total_s"] = _time.perf_counter() - t0
    return results


# ---------------------------------------------------------------------------
# 4b. Lag-product covariance on a regular grid, and coarsening to panels
# ---------------------------------------------------------------------------

def estimate_covariance_lagged(
    grid: WaterlineGrid,
    realizations: np.ndarray,
    *,
    max_lag_x_cells: int = 8,
    max_lag_z_cells: int = 4,
    center: np.ndarray | None = None,
) -> SpatialCovariance:
    """Covariance of the DEM residual from *all* cell pairs at short lags.

    Random subsampling of cells (the ergodic route) throws away exactly the
    short-lag pairs that carry the contact-pit correlation, so on a regular grid
    the covariance is estimated here by complete lag products: for every pair of
    offsets ``(k, l)`` all valid cells contribute.  The zero-lag variance gives
    the total variance, the nugget is what the smooth term cannot explain.

    ``center`` (e.g. the fitted deterministic profile on the DEM grid) is
    subtracted from every realisation; with ``center=None`` the ensemble mean is
    subtracted instead.
    """
    r = np.asarray(realizations, dtype=NP_DTYPE)
    shape = (int(grid.shape[0]), int(grid.shape[1]))
    if r.ndim != 3 or r.shape[1:] != shape:
        raise ValueError(f"realizations must be (K, {shape[0]}, {shape[1]})")
    z_centers = np.asarray(grid.z_centers, dtype=NP_DTYPE)
    x_centers = np.asarray(grid.x_centers, dtype=NP_DTYPE)
    dz_cell = float(z_centers[1] - z_centers[0])
    dx_cell = float(x_centers[1] - x_centers[0])

    if center is None:
        centre = r.mean(axis=0)
    else:
        centre = np.asarray(center, dtype=NP_DTYPE).reshape(shape)
    fluct = r - centre[None, :, :]

    var_cell = float(np.mean(np.var(fluct, axis=0, ddof=1)))

    lags: list[tuple[float, float, float, int]] = []
    lg_x: list[float] = []
    lg_z: list[float] = []
    cov: list[float] = []
    cnt: list[float] = []
    for l in range(0, int(max_lag_z_cells) + 1):
        for k in range(0, int(max_lag_x_cells) + 1):
            if l == 0 and k == 0:
                continue
            a = fluct[:, :shape[0] - l, :shape[1] - k]
            b = fluct[:, l:, k:]
            if a.size == 0:
                continue
            prod = float(np.mean(a * b))
            n_pairs = int(a.shape[0] * a.shape[1] * a.shape[2])
            lg_x.append(k * dx_cell)
            lg_z.append(l * dz_cell)
            cov.append(prod)
            cnt.append(float(n_pairs))
            lags.append((k * dx_cell, l * dz_cell, prod, n_pairs))
    if not cov:
        raise ValueError("no lag products collected")
    lx = np.asarray(lg_x)
    lz = np.asarray(lg_z)
    cv = np.asarray(cov)
    wt = np.asarray(cnt)
    scale = max(var_cell, 1e-300)

    def model(p: np.ndarray) -> np.ndarray:
        return p[0] * np.exp(-lx / max(p[1], 1e-6) - lz / max(p[2], 1e-6))

    upper_sigma2 = max(10.0 * scale, 1.5 * float(cv.max()), 1e-300)
    p0 = np.clip([max(float(cv.max()), 0.5 * scale), 1.5 * dx_cell, 1.5 * dz_cell],
                 [1e-12, 1.01e-3, 1.01e-3],
                 [0.9 * upper_sigma2, 9.9e4, 9.9e4])
    res = least_squares(lambda p: (model(p) - cv) / scale, p0,
                        bounds=([0.0, 1e-3, 1e-3], [upper_sigma2, 1e5, 1e5]),
                        xtol=1e-14, ftol=1e-14, gtol=1e-14, x_scale="jac",
                        max_nfev=20000)
    pred = model(res.x)
    ss_res = float(np.sum(wt * (pred - cv) ** 2))
    ss_tot = float(np.sum(wt * (cv - np.average(cv, weights=wt)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    sigma2 = float(res.x[0])
    nugget2 = float(max(var_cell - sigma2, 0.0))
    return SpatialCovariance(
        sigma2=sigma2, length_x_m=float(res.x[1]), length_z_m=float(res.x[2]),
        nugget2=nugget2, r_squared=float(r2), n_pairs=int(np.sum(wt)),
        lag_table=tuple(lags))


def coarsen_covariance(covariance: SpatialCovariance, grid: WaterlineGrid,
                       panels: PanelGrid) -> np.ndarray:
    """Panel-scale covariance matrix implied by the cell-scale model.

    Panel values are area averages of the cell field, so with the separable
    covariance ``C = sigma^2 Ax (x) Az + tau^2 I`` the panel covariance is

        ``C_p = sigma^2 (Wx Ax Wx^T) (x) (Wz Az Wz^T) + diag(tau^2 / n_i)``

    with ``W`` the row-normalised overlap matrices and ``n_i`` the number of
    fine cells inside panel ``i``.  This keeps the exact short-lag structure of
    the DEM residual instead of refitting a smooth model to the (near white)
    panel field.
    """
    x_f = np.asarray(grid.x_centers, dtype=NP_DTYPE)
    z_f = np.asarray(grid.z_centers, dtype=NP_DTYPE)
    ax = np.exp(-np.abs(x_f[:, None] - x_f[None, :]) / max(covariance.length_x_m, 1e-12))
    az = np.exp(-np.abs(z_f[:, None] - z_f[None, :]) / max(covariance.length_z_m, 1e-12))
    wx = _overlap_matrix(panels.x_edges, np.asarray(grid.x_edges, dtype=NP_DTYPE))
    wz = _overlap_matrix(panels.z_edges, np.asarray(grid.z_edges, dtype=NP_DTYPE))
    bx = wx @ ax @ wx.T
    bz = wz @ az @ wz.T
    cp = covariance.sigma2 * np.kron(bz, bx)
    cell_area = (float(np.diff(np.asarray(grid.x_edges))[0])
                 * float(np.diff(np.asarray(grid.z_edges))[0]))
    n_cells = panels.areas_flat / cell_area
    cp = cp + np.diag(covariance.nugget2 / np.maximum(n_cells, 1.0))
    return 0.5 * (cp + cp.T)


def conditional_simulation_from_matrix(
    covariance: np.ndarray,
    *,
    observation_indices: np.ndarray,
    observation_values: np.ndarray,
    observation_noise: float = 0.0,
    n_samples: int = 100,
    seed: int = 42,
    prior_mean: np.ndarray | None = None,
    eigen_rtol: float = 1e-10,
) -> ConditionalField:
    """Conditional simulation from an explicit covariance matrix.

    Used for the panel-scale covariance of :func:`coarsen_covariance` (which is
    not of exponential form after area averaging).  Simple kriging + one
    eigendecomposition reused for all samples.
    """
    c = np.asarray(covariance, dtype=NP_DTYPE)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("covariance must be square")
    n = c.shape[0]
    idx = np.asarray(observation_indices, dtype=int).ravel()
    y = np.asarray(observation_values, dtype=NP_DTYPE).ravel()
    if idx.size != y.size:
        raise ValueError("observation_indices and observation_values differ in size")
    if idx.size and (idx.min() < 0 or idx.max() >= n):
        raise ValueError("observation index out of range")
    m0 = (np.zeros(n, dtype=NP_DTYPE) if prior_mean is None
          else np.asarray(prior_mean, dtype=NP_DTYPE).ravel())
    if m0.size != n:
        raise ValueError("prior_mean size mismatch")
    noise = float(observation_noise)
    if noise < 0.0:
        raise ValueError("observation_noise must be nonnegative")
    if idx.size == 0:
        lam, vec = np.linalg.eigh(c)
        lam = np.clip(lam, 0.0, None)
        keep = lam > eigen_rtol * max(float(lam.max()), 1e-300)
        lam, vec = lam[keep], vec[:, keep]
        rng = np.random.default_rng(seed)
        zc = rng.standard_normal((int(n_samples), lam.size))
        samples = m0[None, :] + (zc * np.sqrt(lam)[None, :]) @ vec.T
        return ConditionalField(mean=m0.copy(), covariance=c,
                                variance=np.diag(c), samples=samples,
                                observation_indices=idx, observation_values=y,
                                observation_noise=noise)

    c_uo = c[:, idx]
    c_oo = c[np.ix_(idx, idx)] + (noise ** 2) * np.eye(idx.size)
    w = np.linalg.solve(c_oo, c_uo.T)
    mean = m0 + w.T @ (y - m0[idx])
    cond_cov = c - c_uo @ w
    cond_cov = 0.5 * (cond_cov + cond_cov.T)
    lam, vec = np.linalg.eigh(cond_cov)
    lam = np.clip(lam, 0.0, None)
    keep = lam > eigen_rtol * max(float(lam.max()), 1e-300)
    lam, vec = lam[keep], vec[:, keep]
    rng = np.random.default_rng(seed)
    zc = rng.standard_normal((int(n_samples), lam.size))
    samples = mean[None, :] + (zc * np.sqrt(lam)[None, :]) @ vec.T
    return ConditionalField(mean=mean, covariance=cond_cov, variance=np.diag(cond_cov),
                            samples=samples, observation_indices=idx,
                            observation_values=y, observation_noise=noise)


# ---------------------------------------------------------------------------
# 4c. Epistemic uncertainty of the activity distribution (documented bands)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActivityUncertaintyBands:
    """Half-widths of the uniform bands on the activity-distribution parameters.

    The DEM ensemble varies only the *contact* statistics; the ice-belt
    distribution parameters (where contacts land) are a documented engineering
    judgement in :class:`tensordem.ice_abrasion.ActivityDistribution`.  Their
    bands below are the stated source of the smooth (epistemic) part of the
    residual field: uncertainty in *where* the wear lands at fixed total wear
    volume (the total is anchored by the friction work and the wear law, so it
    is not uncertain here).
    """

    x_background: float = 0.10
    x_decay_fraction: float = 0.04
    x_shoulder_center: float = 0.08
    x_shoulder_width: float = 0.06
    x_shoulder_boost: float = 0.15
    waterline_offset_m: float = 0.30
    sigma_below_m: float = 0.15
    sigma_above_m: float = 0.10

    def summary(self) -> dict[str, float]:
        return dict(self.__dict__)


def activity_parameter_ensemble(
    panels: PanelGrid,
    *,
    grid: WaterlineGrid | None = None,
    n_members: int = 200,
    seed: int = 20261108,
    bands: ActivityUncertaintyBands | None = None,
    reference_rate: float,
) -> np.ndarray:
    """Rate fields from activity-distribution parameter draws, panel averaged.

    Every member keeps the same total wear rate (area-weighted mean equals
    ``reference_rate``); only the *distribution* of that rate over the hull
    changes.  Returns ``(n_members, N)`` zero-mean residual rate fields
    ``member - reference_rate``.
    """
    from .ice_abrasion import ActivityDistribution

    grid = build_waterline_grid() if grid is None else grid
    bands = ActivityUncertaintyBands() if bands is None else bands
    if n_members < 2:
        raise ValueError("n_members must be >= 2")
    if not reference_rate > 0.0:
        raise ValueError("reference_rate must be positive")

    base = ActivityDistribution()
    rng = np.random.default_rng(seed)
    areas = panels.areas_flat
    members = np.empty((int(n_members), panels.n_panels), dtype=NP_DTYPE)

    def uniform(centre: float, half: float) -> float:
        return float(centre + rng.uniform(-half, half))

    for m in range(int(n_members)):
        dist = ActivityDistribution(
            x_peak_fraction=base.x_peak_fraction,
            x_decay_fraction=max(1e-3, uniform(base.x_decay_fraction,
                                               bands.x_decay_fraction)),
            x_background=max(0.0, uniform(base.x_background, bands.x_background)),
            x_shoulder_center=float(np.clip(
                uniform(base.x_shoulder_center, bands.x_shoulder_center), 0.05, 0.95)),
            x_shoulder_width=max(1e-2, uniform(base.x_shoulder_width,
                                               bands.x_shoulder_width)),
            x_shoulder_boost=max(0.0, uniform(base.x_shoulder_boost,
                                              bands.x_shoulder_boost)),
            waterline_offset_m=uniform(base.waterline_offset_m,
                                      bands.waterline_offset_m),
            sigma_below_m=max(1e-2, uniform(base.sigma_below_m,
                                            bands.sigma_below_m)),
            sigma_above_m=max(1e-2, uniform(base.sigma_above_m,
                                            bands.sigma_above_m)))
        prob = dist.cell_probability(grid).detach().cpu().numpy()
        rate = coarsen_field(prob / np.asarray(grid.cell_area_m2.detach().cpu(), dtype=NP_DTYPE),
                             grid, panels).ravel()
        mean = float(np.sum(rate * areas) / areas.sum())
        members[m] = reference_rate * rate / max(mean, 1e-300)
    return members - reference_rate
