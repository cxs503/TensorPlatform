"""Structural-health assessment: safety factor, UQ and the real-time monitor."""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .constants import DEFAULT_YIELD_PA, SF_FAIL_THRESHOLD, SF_SAFE_THRESHOLD
from .frame import IceBeltFrameModel, StrainInfluence, _as_float_array
from .inversion import (
    load_covariance,
    relative_load_uncertainty,
    relative_noise_level,
    tikhonov_solve,
)


def classify_safety_factor(sf: float) -> Dict[str, Any]:
    """Classify ``SF = sigma_yield / sigma_vm`` against the acceptance bands."""
    sf_val = float(sf)
    if not math.isfinite(sf_val):
        raise ValueError(f"safety factor must be finite, got {sf!r}")
    if sf_val >= SF_SAFE_THRESHOLD:
        level, verdict = "SAFE", "ACCEPTABLE"
    elif sf_val >= SF_FAIL_THRESHOLD:
        level, verdict = "MARGINAL", "ATTENTION"
    else:
        level, verdict = "FAIL", "NOT_ACCEPTABLE"
    return {
        "safety_factor": sf_val,
        "level": level,
        "verdict": verdict,
        "utilisation": (1.0 / sf_val) if sf_val > 0.0 else float("inf"),
        "sf_safe_threshold": SF_SAFE_THRESHOLD,
        "sf_fail_threshold": SF_FAIL_THRESHOLD,
        "criterion": "SF >= 1.5 SAFE, 1.0 <= SF < 1.5 MARGINAL, SF < 1.0 FAIL",
    }


def safety_factor_with_uncertainty(
    model: IceBeltFrameModel,
    loads_n: Sequence[float],
    load_nodes: Sequence[int],
    cov_loads: Optional[np.ndarray] = None,
    yield_strength_pa: float = DEFAULT_YIELD_PA,
    n_mc: int = 0,
    seed: int = 0,
    fd_step_rel: float = 1e-6,
) -> Dict[str, Any]:
    """Safety factor plus first-order (delta) and optional Monte-Carlo scatter."""
    loads = _as_float_array(loads_n, "loads_n")
    state = model.stress_state(loads, load_nodes)
    sigma_vm = float(state["max_von_mises_pa"])
    sf = float(yield_strength_pa) / sigma_vm if sigma_vm > 0.0 else float("inf")

    out: Dict[str, Any] = {
        "safety_factor": sf,
        "classification": classify_safety_factor(sf),
        "max_von_mises_pa": sigma_vm,
        "max_von_mises_location_m": state["max_von_mises_location_m"],
        "max_bending_moment_nm": state["max_bending_moment_nm"],
        "max_shear_n": state["max_shear_n"],
        "max_deflection_m": state["max_deflection_m"],
        "yield_strength_pa": float(yield_strength_pa),
        "profile": state["profile"],
    }
    if cov_loads is None:
        return out

    cov = np.asarray(cov_loads, dtype=float)
    if cov.shape != (loads.size, loads.size):
        raise ValueError(
            f"cov_loads must have shape ({loads.size}, {loads.size}), got {cov.shape}"
        )
    base_scale = max(float(np.max(np.abs(loads))), 1.0)
    step = fd_step_rel * base_scale
    maps = model.stress_maps(load_nodes)

    # Central-difference gradient of the governing von Mises stress; all probe
    # points are evaluated in one vectorised batch.
    probes = np.tile(loads, (2 * loads.size, 1))
    for j in range(loads.size):
        probes[2 * j, j] += step
        probes[2 * j + 1, j] -= step
    vm_probes = model.stress_state_many(probes, load_nodes, maps=maps)["max_von_mises_pa"]
    grad = np.array(
        [(vm_probes[2 * j] - vm_probes[2 * j + 1]) / (2.0 * step) for j in range(loads.size)]
    )

    sigma_sigma_vm = math.sqrt(max(float(grad @ cov @ grad), 0.0))
    out["sigma_von_mises_pa"] = sigma_sigma_vm
    out["sigma_sf_first_order"] = (
        float(yield_strength_pa) / sigma_vm ** 2 * sigma_sigma_vm
        if sigma_vm > 0.0
        else float("inf")
    )
    out["stress_gradient"] = grad.tolist()

    if n_mc and int(n_mc) > 0:
        rng = np.random.default_rng(seed)
        samples = rng.multivariate_normal(loads, cov, size=int(n_mc), method="eigh")
        samples = np.clip(samples, 0.0, None)
        vm_samples = model.stress_state_many(samples, load_nodes, maps=maps)["max_von_mises_pa"]
        vm_samples = np.maximum(vm_samples, 1e-12)
        sf_samples = float(yield_strength_pa) / vm_samples
        out["monte_carlo"] = {
            "n_samples": int(n_mc),
            "seed": int(seed),
            "sf_mean": float(np.mean(sf_samples)),
            "sf_std": float(np.std(sf_samples, ddof=1)) if int(n_mc) > 1 else 0.0,
            "sf_p05": float(np.percentile(sf_samples, 5.0)),
            "sf_p50": float(np.percentile(sf_samples, 50.0)),
            "sf_p95": float(np.percentile(sf_samples, 95.0)),
            "prob_fail": float(np.mean(sf_samples < SF_FAIL_THRESHOLD)),
            "prob_safe": float(np.mean(sf_samples >= SF_SAFE_THRESHOLD)),
            "samples": sf_samples.tolist(),
        }
    return out


class StructuralHealthMonitor:
    """Real-time strain-stream monitor: sensor data -> ice load -> SF assessment.

    The strain influence matrix and its Gram matrix are precomputed once, so a
    single monitoring cycle costs one small linear solve plus one forward stress
    pass (well below the 0.1 s budget for a frame girder).
    """

    def __init__(
        self,
        model: IceBeltFrameModel,
        gauge_positions_m: Sequence[float],
        load_positions_m: Sequence[float],
        yield_strength_pa: float = DEFAULT_YIELD_PA,
        lambda_selection: str = "l_curve",
        lam: Optional[float] = None,
        noise_sigma_strain: Optional[Sequence[float]] = None,
        noise_rel: float = 0.0,
        n_mc: int = 0,
        seed: int = 0,
        fibre_offset_m: Optional[float] = None,
    ) -> None:
        self.model = model
        self.influence: StrainInfluence = model.build_influence_matrix(
            load_positions_m, gauge_positions_m, fibre_offset_m=fibre_offset_m
        )
        self.yield_strength_pa = float(yield_strength_pa)
        self.lambda_selection = lambda_selection
        self.lam = lam
        self.n_mc = int(n_mc)
        self.seed = int(seed)
        if noise_sigma_strain is not None:
            self.noise_sigma_strain: Optional[np.ndarray] = _as_float_array(
                noise_sigma_strain, "noise_sigma_strain"
            )
        elif noise_rel > 0.0:
            # Worst-case (design) noise: noise_rel of the largest expected reading
            # is not known a priori, so the relative level is applied per frame.
            self.noise_sigma_strain = None
        else:
            self.noise_sigma_strain = None
        self.noise_rel = float(noise_rel)
        self.history: List[Dict[str, Any]] = []

    # -- single cycle -------------------------------------------------------

    def update(
        self, strain_meas: Sequence[float], timestamp: Optional[float] = None
    ) -> Dict[str, Any]:
        """Invert one strain frame and return the full health assessment."""
        t0 = time.perf_counter()
        eps = _as_float_array(strain_meas, "strain_meas")
        if eps.size != self.influence.n_gauge:
            raise ValueError(
                f"got {eps.size} strain readings for {self.influence.n_gauge} gauges"
            )
        if self.lambda_selection == "l_curve":
            from .inversion import l_curve_scan

            lam_used = float(
                l_curve_scan(self.influence.H, eps, n_scan=25)["lambda_opt"]
            )
        else:
            if self.lam is None:
                raise ValueError("lambda_selection='fixed' requires lam")
            lam_used = float(self.lam)

        loads = tikhonov_solve(self.influence.H, eps, lam_used)
        residual = float(np.linalg.norm(self.influence.H @ loads - eps))
        t_solve = time.perf_counter()

        sigma_strain = self.noise_sigma_strain
        if sigma_strain is None and self.noise_rel > 0.0:
            sigma_strain = self.noise_rel * np.abs(eps)
        cov = sigma_load = None
        noise_level = load_unc = amplification = 0.0
        if sigma_strain is not None:
            cov, sigma_load = load_covariance(self.influence.H, lam_used, sigma_strain)
            noise_level = relative_noise_level(eps, sigma_strain)
            load_unc = relative_load_uncertainty(loads, sigma_load)
            amplification = load_unc / noise_level if noise_level > 0.0 else 0.0

        safety = safety_factor_with_uncertainty(
            self.model,
            loads,
            self.influence.load_nodes,
            cov_loads=cov,
            yield_strength_pa=self.yield_strength_pa,
            n_mc=self.n_mc,
            seed=self.seed,
        )
        t_end = time.perf_counter()

        record: Dict[str, Any] = {
            "timestamp": float(timestamp if timestamp is not None else time.time()),
            "loads_inverted_n": loads.tolist(),
            "total_ice_load_n": float(np.sum(loads)),
            "lambda": lam_used,
            "residual_norm": residual,
            "sigma_load_n": None if sigma_load is None else sigma_load.tolist(),
            "noise_level": noise_level,
            "load_uncertainty": load_unc,
            "amplification_factor": amplification,
            "max_von_mises_pa": safety["max_von_mises_pa"],
            "max_von_mises_location_m": safety["max_von_mises_location_m"],
            "safety_factor": safety["safety_factor"],
            "sigma_sf": safety.get("sigma_sf_first_order"),
            "safety": safety["classification"],
            "alarm": safety["classification"]["level"] != "SAFE",
            "fail": safety["classification"]["level"] == "FAIL",
            "cycle_ms": (t_end - t0) * 1e3,
            "solve_ms": (t_solve - t0) * 1e3,
        }
        self.history.append(record)
        return record

    # -- stream -------------------------------------------------------------

    def run_stream(
        self, frames: Sequence[Sequence[float]], timestamps: Optional[Sequence[float]] = None
    ) -> Dict[str, Any]:
        """Process a stream of strain frames and return an aggregated report."""
        if timestamps is not None and len(timestamps) != len(frames):
            raise ValueError("timestamps and frames must have the same length")
        records = [
            self.update(frame, None if timestamps is None else timestamps[i])
            for i, frame in enumerate(frames)
        ]
        sfs = np.array([r["safety_factor"] for r in records])
        cycles = np.array([r["cycle_ms"] for r in records])
        return {
            "n_frames": len(records),
            "min_safety_factor": float(np.min(sfs)) if sfs.size else float("inf"),
            "max_safety_factor": float(np.max(sfs)) if sfs.size else float("inf"),
            "mean_safety_factor": float(np.mean(sfs)) if sfs.size else float("inf"),
            "n_alarms": int(sum(1 for r in records if r["alarm"])),
            "n_failures": int(sum(1 for r in records if r["fail"])),
            "max_total_ice_load_n": float(max(r["total_ice_load_n"] for r in records))
            if records
            else 0.0,
            "mean_cycle_ms": float(np.mean(cycles)) if cycles.size else 0.0,
            "max_cycle_ms": float(np.max(cycles)) if cycles.size else 0.0,
            "worst_record": min(records, key=lambda r: r["safety_factor"]) if records else None,
            "records": records,
        }

    def reset(self) -> None:
        self.history = []

    def trend(self) -> Dict[str, Any]:
        """Safety-factor trend over the recorded history."""
        if not self.history:
            return {"n": 0}
        sfs = np.array([r["safety_factor"] for r in self.history])
        return {
            "n": len(sfs),
            "safety_factor_series": sfs.tolist(),
            "min_safety_factor": float(np.min(sfs)),
            "last_safety_factor": float(sfs[-1]),
            "degradation": float(sfs[0] - sfs[-1]),
        }