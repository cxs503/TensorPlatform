"""Tikhonov ice-load inversion, L-curve regularisation and uncertainty UQ."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .constants import DEFAULT_YIELD_PA
from .frame import IceBeltFrameModel, StrainInfluence, _as_float_array


# ---------------------------------------------------------------------------
# Tikhonov core
# ---------------------------------------------------------------------------


def tikhonov_solve(H: np.ndarray, strain_meas: Sequence[float], lam: float = 0.0) -> np.ndarray:
    """Solve ``argmin ||H F - eps||^2 + lam ||F||^2``.

    ``lam == 0`` reduces to ordinary least squares, solved by SVD so the
    noiseless case stays numerically exact to machine precision.
    """
    H_arr = np.asarray(H, dtype=float)
    eps = _as_float_array(strain_meas, "strain_meas")
    if H_arr.ndim != 2:
        raise ValueError("H must be two-dimensional")
    if H_arr.shape[0] != eps.size:
        raise ValueError(f"H has {H_arr.shape[0]} rows but {eps.size} gauges were given")
    lam_val = 0.0 if lam is None else float(lam)
    if not math.isfinite(lam_val) or lam_val < 0.0:
        raise ValueError(f"lam must be finite and >= 0, got {lam!r}")
    if lam_val <= 0.0:
        return np.linalg.lstsq(H_arr, eps, rcond=None)[0]
    gram = H_arr.T @ H_arr + lam_val * np.eye(H_arr.shape[1])
    return np.linalg.solve(gram, H_arr.T @ eps)


def _select_corner(curvature: np.ndarray) -> Tuple[int, float]:
    """Pick the L-corner as the interior point of largest curvature magnitude."""
    n = curvature.size
    if n < 3:
        return 0, 0.0
    interior = np.arange(1, n - 1)
    vals = curvature[interior]
    if not np.all(np.isfinite(vals)):
        vals = np.nan_to_num(vals, nan=0.0, posinf=0.0, neginf=0.0)
    if float(np.max(np.abs(vals))) == 0.0:
        return int(interior[0]), 0.0
    if abs(float(np.max(vals))) >= abs(float(np.min(vals))):
        idx = int(interior[int(np.argmax(vals))])
    else:
        idx = int(interior[int(np.argmin(vals))])
    return idx, float(curvature[idx])


def l_curve_scan(
    H: np.ndarray,
    strain_meas: Sequence[float],
    lam_grid: Optional[Sequence[float]] = None,
    n_scan: int = 37,
    true_loads: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """L-curve scan over Tikhonov weights with automatic corner detection.

    The curve is parametrised by ``lam``: ``rho(lam) = ||H F_lam - eps||`` and
    ``eta(lam) = ||F_lam||`` are traced in log-log space and the corner is the
    interior point of maximum triangle curvature

    ``kappa = (dx*ddy - dy*ddx) / (dx^2 + dy^2)^1.5``,  ``d/d log10(lam)``.
    """
    H_arr = np.asarray(H, dtype=float)
    eps = _as_float_array(strain_meas, "strain_meas")
    if H_arr.ndim != 2 or H_arr.shape[0] != eps.size:
        raise ValueError("H shape is inconsistent with the measurement vector")

    sv = np.linalg.svd(H_arr, compute_uv=False)
    s_max = float(sv[0]) if sv.size else 0.0
    if not math.isfinite(s_max) or s_max <= 0.0:
        raise ValueError("influence matrix is numerically singular (zero scale)")
    s_min = float(sv[-1]) if sv.size else 0.0
    s_min = max(s_min, s_max * 1e-16)
    scale = s_max ** 2
    if lam_grid is None:
        # Work in units of the squared singular values of H: the interesting
        # trade-off region always lies between lambda ~ sigma_min^2 (no
        # regularisation) and lambda ~ sigma_max^2 (dominated by the prior).
        lams = np.logspace(
            math.log10(s_min ** 2) - 2.0, math.log10(s_max ** 2) + 2.0, int(n_scan)
        )
    else:
        lams = _as_float_array(lam_grid, "lam_grid")
        if np.any(lams <= 0.0):
            raise ValueError("lam_grid must be strictly positive")
        lams = np.sort(lams)

    rho = np.zeros(lams.size)
    eta = np.zeros(lams.size)
    loads = np.zeros((lams.size, H_arr.shape[1]))
    for i, lam in enumerate(lams):
        f = tikhonov_solve(H_arr, eps, float(lam))
        loads[i] = f
        rho[i] = float(np.linalg.norm(H_arr @ f - eps))
        eta[i] = float(np.linalg.norm(f))

    err: Optional[np.ndarray] = None
    if true_loads is not None:
        f_true = _as_float_array(true_loads, "true_loads")
        if f_true.size != H_arr.shape[1]:
            raise ValueError("true_loads length must match the number of load unknowns")
        denom = max(float(np.linalg.norm(f_true)), 1e-30)
        err = np.array(
            [float(np.linalg.norm(loads[i] - f_true)) / denom for i in range(lams.size)]
        )

    t = np.log10(lams)
    x = np.log10(np.maximum(rho, 1e-300))
    y = np.log10(np.maximum(eta, 1e-300))
    curvature = np.zeros(lams.size)
    if lams.size >= 3:
        dx = np.gradient(x, t)
        dy = np.gradient(y, t)
        ddx = np.gradient(dx, t)
        ddy = np.gradient(dy, t)
        denom_c = np.power(dx ** 2 + dy ** 2, 1.5)
        curvature = np.divide(
            dx * ddy - dy * ddx, denom_c, out=np.zeros_like(dx), where=denom_c > 0.0
        )
        curvature = np.nan_to_num(curvature, nan=0.0, posinf=0.0, neginf=0.0)
    idx_opt, corner_curvature = _select_corner(curvature)

    out: Dict[str, Any] = {
        "lambda": lams,
        "residual_norm": rho,
        "solution_norm": eta,
        "curvature": curvature,
        "lambda_opt": float(lams[idx_opt]),
        "index_opt": int(idx_opt),
        "corner_curvature": corner_curvature,
        "loads": loads,
        "gram_scale": scale,
    }
    if err is not None:
        out["inversion_error"] = err
        out["lambda_error_opt"] = float(lams[int(np.argmin(err))])
        out["min_inversion_error"] = float(np.min(err))
        out["inversion_error_at_corner"] = float(err[idx_opt])
    return out


# ---------------------------------------------------------------------------
# Uncertainty propagation
# ---------------------------------------------------------------------------


def load_covariance(
    H: np.ndarray, lam: float, sigma_strain: Sequence[float]
) -> Tuple[np.ndarray, np.ndarray]:
    """Propagate independent gauge noise to the inverted load vector.

    ``F_hat = A eps`` with ``A = (H^T H + lam I)^-1 H^T`` gives
    ``Cov(F_hat) = A diag(sigma_eps^2) A^T``.  Returns ``(cov, sigma_F)``.
    """
    H_arr = np.asarray(H, dtype=float)
    sig = _as_float_array(sigma_strain, "sigma_strain")
    if sig.size != H_arr.shape[0]:
        raise ValueError("sigma_strain length must equal the number of gauges")
    if np.any(sig < 0.0):
        raise ValueError("sigma_strain must be non-negative")
    n_load = H_arr.shape[1]
    lam_val = float(lam)
    if lam_val <= 0.0:
        A = np.linalg.pinv(H_arr)
    else:
        A = np.linalg.solve(H_arr.T @ H_arr + lam_val * np.eye(n_load), H_arr.T)
    cov = A @ np.diag(sig ** 2) @ A.T
    cov = 0.5 * (cov + cov.T)
    return cov, np.sqrt(np.maximum(np.diag(cov), 0.0))


def relative_noise_level(strain_meas: Sequence[float], sigma_strain: Sequence[float]) -> float:
    """RMS relative strain noise ``sigma_eps / eps`` (the acceptance metric)."""
    eps = _as_float_array(strain_meas, "strain_meas")
    sig = _as_float_array(sigma_strain, "sigma_strain")
    ratios = sig / np.maximum(np.abs(eps), 1e-30)
    return float(np.sqrt(np.mean(ratios ** 2)))


def relative_load_uncertainty(loads: Sequence[float], sigma_loads: Sequence[float]) -> float:
    """RMS relative load uncertainty ``sigma_F / F`` (the acceptance metric)."""
    f = _as_float_array(loads, "loads")
    s = _as_float_array(sigma_loads, "sigma_loads")
    ratios = s / np.maximum(np.abs(f), 1e-30)
    return float(np.sqrt(np.mean(ratios ** 2)))


def synthesize_strain(
    influence: StrainInfluence,
    loads_true_n: Sequence[float],
    noise_rel: float = 0.0,
    seed: int = 0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Generate synthetic gauge readings ``eps = H F (1 + multiplicative noise)``.

    Noise is Gaussian and proportional to the local reading, which matches
    resistance-gauge chains (bridge excitation / amplifier gain errors scale with
    the signal).  Returns ``(strain_meas, sigma_strain)``.
    """
    loads = _as_float_array(loads_true_n, "loads_true_n")
    eps_clean = influence.forward(loads)
    sigma = float(noise_rel) * np.abs(eps_clean)
    if noise_rel <= 0.0:
        return eps_clean, np.zeros_like(eps_clean)
    rng = np.random.default_rng(seed)
    eps = eps_clean + rng.standard_normal(eps_clean.size) * sigma
    return eps, sigma


# ---------------------------------------------------------------------------
# End-to-end inversion result
# ---------------------------------------------------------------------------


@dataclass
class InversionResult:
    """Full output of one ice-load inversion / structural-assessment cycle."""

    loads_inverted_n: np.ndarray
    loads_true_n: Optional[np.ndarray]
    load_nodes: np.ndarray
    load_positions_m: np.ndarray
    gauge_positions_m: np.ndarray
    residual_norm: float
    residual_relative: float
    solution_norm: float
    lam: float
    lambda_selection: str
    lcurve: Optional[Dict[str, Any]]
    sigma_load_n: Optional[np.ndarray]
    noise_level: float
    load_uncertainty: float
    amplification_factor: float
    safety_factor: float
    safety: Dict[str, Any]
    stress: Dict[str, Any]
    influence_meta: Dict[str, Any]
    timing_ms: Dict[str, float] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    # -- convenience --------------------------------------------------------

    @property
    def load_relative_error(self) -> Optional[float]:
        """Relative L2 error against the true load vector (if known)."""
        if self.loads_true_n is None:
            return None
        denom = max(float(np.linalg.norm(self.loads_true_n)), 1e-30)
        return float(np.linalg.norm(self.loads_inverted_n - self.loads_true_n) / denom)

    @property
    def elementwise_relative_error(self) -> Optional[np.ndarray]:
        if self.loads_true_n is None:
            return None
        denom = np.maximum(np.abs(self.loads_true_n), 1e-30)
        return np.abs(self.loads_inverted_n - self.loads_true_n) / denom

    def as_dict(self, include_lcurve_points: bool = True) -> Dict[str, Any]:
        lc: Optional[Dict[str, Any]] = None
        if self.lcurve is not None:
            lc = {
                "lambda": np.asarray(self.lcurve["lambda"]).tolist(),
                "residual_norm": np.asarray(self.lcurve["residual_norm"]).tolist(),
                "solution_norm": np.asarray(self.lcurve["solution_norm"]).tolist(),
                "curvature": np.asarray(self.lcurve["curvature"]).tolist(),
                "lambda_opt": self.lcurve["lambda_opt"],
                "corner_curvature": self.lcurve["corner_curvature"],
                "index_opt": self.lcurve["index_opt"],
            }
            if "inversion_error" in self.lcurve:
                lc["inversion_error"] = np.asarray(self.lcurve["inversion_error"]).tolist()
                lc["min_inversion_error"] = self.lcurve["min_inversion_error"]
                lc["inversion_error_at_corner"] = self.lcurve["inversion_error_at_corner"]
                lc["lambda_error_opt"] = self.lcurve["lambda_error_opt"]
            if not include_lcurve_points:
                lc = {k: v for k, v in lc.items() if not isinstance(v, list)}

        mc = (self.stress.get("monte_carlo") or {})
        return {
            "loads_inverted_n": np.asarray(self.loads_inverted_n).tolist(),
            "loads_true_n": (
                None if self.loads_true_n is None else np.asarray(self.loads_true_n).tolist()
            ),
            "load_nodes": np.asarray(self.load_nodes).tolist(),
            "load_positions_m": np.asarray(self.load_positions_m).tolist(),
            "gauge_positions_m": np.asarray(self.gauge_positions_m).tolist(),
            "total_ice_load_inverted_n": float(np.sum(self.loads_inverted_n)),
            "residual_norm": self.residual_norm,
            "residual_relative": self.residual_relative,
            "solution_norm": self.solution_norm,
            "lambda": self.lam,
            "lambda_selection": self.lambda_selection,
            "lcurve": lc,
            "sigma_load_n": (
                None if self.sigma_load_n is None else np.asarray(self.sigma_load_n).tolist()
            ),
            "noise_level": self.noise_level,
            "load_uncertainty": self.load_uncertainty,
            "amplification_factor": self.amplification_factor,
            "load_relative_error": self.load_relative_error,
            "safety_factor": self.safety_factor,
            "safety": self.safety,
            "stress": {
                "max_von_mises_pa": self.stress.get("max_von_mises_pa"),
                "max_von_mises_location_m": self.stress.get("max_von_mises_location_m"),
                "max_bending_moment_nm": self.stress.get("max_bending_moment_nm"),
                "max_shear_n": self.stress.get("max_shear_n"),
                "max_deflection_m": self.stress.get("max_deflection_m"),
                "sigma_von_mises_pa": self.stress.get("sigma_von_mises_pa"),
                "sigma_sf_first_order": self.stress.get("sigma_sf_first_order"),
                "monte_carlo": {k: v for k, v in mc.items() if k != "samples"},
            },
            "influence": {k: v for k, v in self.influence_meta.items() if k != "H"},
            "timing_ms": self.timing_ms,
            "diagnostics": self.diagnostics,
        }


def invert_ice_load(
    model: IceBeltFrameModel,
    strain_meas: Sequence[float],
    load_positions_m: Sequence[float],
    gauge_positions_m: Sequence[float],
    lam: Optional[float] = None,
    lambda_selection: str = "l_curve",
    loads_true_n: Optional[Sequence[float]] = None,
    sigma_strain: Optional[Sequence[float]] = None,
    yield_strength_pa: float = DEFAULT_YIELD_PA,
    n_mc: int = 0,
    seed: int = 0,
    lam_grid: Optional[Sequence[float]] = None,
    influence: Optional[StrainInfluence] = None,
    fibre_offset_m: Optional[float] = None,
) -> InversionResult:
    """Run one complete inversion + structural-health assessment cycle."""
    from .monitor import safety_factor_with_uncertainty  # local import: avoids cycle

    t_start = time.perf_counter()
    eps = _as_float_array(strain_meas, "strain_meas")

    if influence is None:
        influence = model.build_influence_matrix(
            load_positions_m, gauge_positions_m, fibre_offset_m=fibre_offset_m
        )
    H = influence.H
    if eps.size != influence.n_gauge:
        raise ValueError(
            f"got {eps.size} strain readings for {influence.n_gauge} gauge positions"
        )
    if influence.n_load > influence.n_gauge:
        raise ValueError(
            f"inverse problem is underdetermined without prior information: "
            f"{influence.n_load} load unknowns vs {influence.n_gauge} gauges"
        )
    if lambda_selection not in ("l_curve", "fixed"):
        raise ValueError(
            f"lambda_selection must be 'l_curve' or 'fixed', got {lambda_selection!r}"
        )
    t_influence = time.perf_counter()

    # -- regularisation weight ---------------------------------------------
    lcurve: Optional[Dict[str, Any]] = None
    noise_free = False
    if lambda_selection == "l_curve":
        lcurve = l_curve_scan(H, eps, lam_grid=lam_grid, true_loads=loads_true_n)
        # The L-curve is a discrepancy-driven criterion: it only carries
        # information when the data have a noise floor.  For (near) noise-free
        # data the residual can be driven to machine precision and the corner
        # loses its meaning, so the unregularised solution is used instead.
        ls_solution = tikhonov_solve(H, eps, 0.0)
        rho_ls = float(np.linalg.norm(H @ ls_solution - eps))
        noise_free = rho_ls <= 1e-10 * max(float(np.linalg.norm(eps)), 1e-300)
        lam_used = 0.0 if noise_free else float(lcurve["lambda_opt"])
    else:
        if lam is None:
            raise ValueError("lambda_selection='fixed' requires an explicit lam")
        lam_used = float(lam)
        if not math.isfinite(lam_used) or lam_used < 0.0:
            raise ValueError(f"lam must be finite and >= 0, got {lam!r}")

    loads_inv = tikhonov_solve(H, eps, lam_used)
    t_inversion = time.perf_counter()

    residual = float(np.linalg.norm(H @ loads_inv - eps))
    eps_norm = max(float(np.linalg.norm(eps)), 1e-300)

    # -- uncertainty --------------------------------------------------------
    cov_load: Optional[np.ndarray] = None
    sigma_load: Optional[np.ndarray] = None
    noise_level = 0.0
    load_unc = 0.0
    amplification = 0.0
    if sigma_strain is not None:
        cov_load, sigma_load = load_covariance(H, lam_used, sigma_strain)
        noise_level = relative_noise_level(eps, sigma_strain)
        ref_loads = loads_inv if loads_true_n is None else np.asarray(loads_true_n, dtype=float)
        load_unc = relative_load_uncertainty(ref_loads, sigma_load)
        amplification = load_unc / noise_level if noise_level > 0.0 else 0.0

    # -- structural assessment --------------------------------------------
    safety = safety_factor_with_uncertainty(
        model,
        loads_inv,
        influence.load_nodes,
        cov_loads=cov_load,
        yield_strength_pa=yield_strength_pa,
        n_mc=n_mc,
        seed=seed,
    )
    t_end = time.perf_counter()

    return InversionResult(
        loads_inverted_n=loads_inv,
        loads_true_n=None if loads_true_n is None else np.asarray(loads_true_n, dtype=float),
        load_nodes=influence.load_nodes,
        load_positions_m=influence.load_positions_m,
        gauge_positions_m=influence.gauge_positions_m,
        residual_norm=residual,
        residual_relative=residual / eps_norm,
        solution_norm=float(np.linalg.norm(loads_inv)),
        lam=lam_used,
        lambda_selection=lambda_selection,
        lcurve=lcurve,
        sigma_load_n=sigma_load,
        noise_level=noise_level,
        load_uncertainty=load_unc,
        amplification_factor=amplification,
        safety_factor=float(safety["safety_factor"]),
        safety=safety["classification"],
        stress=safety,
        influence_meta=influence.as_dict(),
        timing_ms={
            "influence_ms": (t_influence - t_start) * 1e3,
            "inversion_ms": (t_inversion - t_influence) * 1e3,
            "stress_uq_ms": (t_end - t_inversion) * 1e3,
            "total_ms": (t_end - t_start) * 1e3,
        },
        diagnostics={
            "noise_free_data": noise_free,
            "n_gauges": influence.n_gauge,
            "n_loads": influence.n_load,
            "condition_number_H": influence.condition_number,
            "rank_H": influence.rank,
            "gram_scale": (lcurve or {}).get("gram_scale", lam_used),
        },
    )