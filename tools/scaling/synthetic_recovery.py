"""Estimator-accuracy evidence for the Amdahl fit (synthetic, no measurement).

The physical ledger needs a quiet host; the *estimator* does not.  This script
quantifies how well :func:`tools.scaling.fitting.fit_amdahl` recovers a known
serial fraction from data that looks like a real measurement (5 repetitions per
core count, multiplicative timing noise), across many seeds.

It is deliberately *not* a benchmark: nothing here is timed on hardware.  It is the
evidence that C4's 3 pp accuracy target is achievable by the estimator, so a
measured miss can be attributed to the machine instead of the statistics.

    python -m tools.scaling.synthetic_recovery --out artifacts/scaling_ledger/synthetic_recovery.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from tools.scaling.fitting import fit_amdahl

__all__ = ["run"]


def run(
    truths=(0.0, 0.02, 0.05, 0.10, 0.20, 0.50),
    *,
    ns=(1, 2, 4, 8, 16, 32),
    reps: int = 5,
    noise: float = 0.01,
    seeds: int = 30,
    n_boot: int = 100,
) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    for s_true in truths:
        errs, r2s, cover = [], [], []
        for seed in range(seeds):
            rng = np.random.default_rng(1000 + seed)
            times = {}
            for n in ns:
                model = s_true + (1.0 - s_true) / n
                times[n] = [
                    max(model * (1.0 + noise * rng.standard_normal()), 1e-6) for _ in range(reps)
                ]
            fit = fit_amdahl(times, n_boot=n_boot, seed=seed)
            errs.append(abs(fit.s - s_true) * 100.0)  # percentage points
            r2s.append(fit.r2)
            cover.append(bool(fit.ci_low - 1e-9 <= s_true <= fit.ci_high + 1e-9))
        out.append(
            {
                "s_true": s_true,
                "seeds": seeds,
                "median_abs_error_pp": float(np.median(errs)),
                "p95_abs_error_pp": float(np.quantile(errs, 0.95)),
                "max_abs_error_pp": float(np.max(errs)),
                "median_r2": float(np.median(r2s)),
                "ci_coverage": float(np.mean(cover)),
                "within_3pp": float(np.mean([e <= 3.0 for e in errs])),
            }
        )
    return {
        "schema": "scaling_estimator_accuracy/v1",
        "noise_model": f"multiplicative Gaussian, sigma={noise}",
        "reps_per_point": reps,
        "core_counts": list(ns),
        "bootstrap": n_boot,
        "rows": out,
        "note": (
            "Estimator-only evidence: no wall-clock measurement is involved. "
            "Used to separate 'the statistics cannot hit 3 pp' from 'the host "
            "could not be measured'."
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--noise", type=float, default=0.01)
    args = ap.parse_args(argv)
    doc = run(seeds=args.seeds, noise=args.noise)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")
    for row in doc["rows"]:
        print(
            f"s={row['s_true']:.2f}: median|err|={row['median_abs_error_pp']:.2f} pp, "
            f"p95={row['p95_abs_error_pp']:.2f} pp, R2={row['median_r2']:.4f}, "
            f"CI coverage={row['ci_coverage']:.2f}, within3pp={row['within_3pp']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())