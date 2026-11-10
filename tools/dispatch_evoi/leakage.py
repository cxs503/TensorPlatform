"""Leakage audit: prove the pipeline cannot read the future.

Three independent probes:

1. **structural** -- no column of the fitted design matrix is a post-hoc field
   (:func:`tools.dispatch_evoi.schema.assert_no_post_hoc_columns`), and every
   used column carries ``field_ts <= dispatch_ts`` (checked on every candidate
   of every round while featurising, so a late-stamped field aborts the run).

2. **behavioural noise substitution** -- append the post-hoc columns
   (``passed``, ``wall_time``, ``cost_actual``, ``grade_reason``, ...) to every
   candidate order, stamped *after* dispatch, first with their true values and
   then with pure noise, and re-run the whole walk-forward backtest.  If any of
   them leaked into a feature, the true-value run would jump and the noise run
   would collapse.  Both must be **bit-identical** to the clean run.

3. **label-shuffle negative control** -- permute the training labels only; a
   model that is not learning anything from the features must collapse to
   AUC ~ 0.5 on the untouched test labels.  Reporting the band
   ``[0.45, 0.55]`` keeps the control falsifiable.

The audit also reports the *detectability* of a would-be leak: the AUC of the
injected ``passed`` column is 1.0, i.e. a genuine leak is impossible to miss.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from .backtest import run_evaluation
from .models import auc
from .schema import POST_HOC_FIELDS, assert_no_post_hoc_columns

__all__ = ["inject_post_hoc", "audit_leakage", "compare_metrics", "null_control"]

_INJECT = ("passed", "wall_time", "cost_actual", "grade_reason")


def inject_post_hoc(dataset: Any, *, mode: str, seed: int = 12345) -> Any:
    """Return a copy of ``dataset`` whose orders also carry post-hoc columns.

    ``mode='true'``  the real outcomes / wall times
    ``mode='noise'`` uncorrelated draws (so a leak shows up as a metric *drop*)
    """
    if mode not in {"true", "noise"}:
        raise ValueError("mode must be 'true' or 'noise'")
    rng = np.random.default_rng(seed)
    rounds = []
    for rnd in dataset.rounds:
        cands = []
        for j, order in enumerate(rnd.candidates):
            o = dict(order)
            o["field_ts"] = dict(order["field_ts"])
            if mode == "true":
                vals = {
                    "passed": int(rnd.outcomes[j]),
                    "wall_time": float(rnd.wall_sec[j]),
                    "cost_actual": float(rnd.cost[j]),
                    "grade_reason": "accepted" if rnd.outcomes[j] else "honest-fail",
                }
            else:
                vals = {
                    "passed": int(rng.integers(0, 2)),
                    "wall_time": float(rng.uniform(10.0, 5000.0)),
                    "cost_actual": float(rng.uniform(0.1, 500.0)),
                    "grade_reason": "n/a",
                }
            for k, v in vals.items():
                o[k] = v
                # post-hoc columns only exist *after* the order ran
                o["field_ts"][k] = float(rnd.dispatch_ts) + 3600.0
            cands.append(o)
        rounds.append(dataclasses.replace(rnd, candidates=cands))
    return dataclasses.replace(dataset, rounds=rounds)


def compare_metrics(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Max absolute difference between two ``pooled`` metric dicts."""
    deltas: dict[str, float] = {}
    for k, va in a["pooled"].items():
        vb = b["pooled"].get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            deltas[k] = abs(float(va) - float(vb))
    return (max(deltas.values()) if deltas else 0.0), deltas


def audit_leakage(
    *,
    seed: int = 7,
    n_rounds: int = 1200,
    slate_size: int = 8,
    n_estimators: int = 40,
    band: tuple[float, float] = (0.45, 0.55),
    n_permutations: int = 25,
) -> dict[str, Any]:
    """Run the three probes and return a JSON-ready audit record."""
    from .fixtures import make_dataset

    ds = make_dataset(seed=seed, n_rounds=n_rounds, slate_size=slate_size)
    base = run_evaluation(dataset=ds, seed=seed, n_estimators=n_estimators)
    true_run = run_evaluation(
        dataset=inject_post_hoc(ds, mode="true"), seed=seed, n_estimators=n_estimators
    )
    noise_run = run_evaluation(
        dataset=inject_post_hoc(ds, mode="noise", seed=seed + 1),
        seed=seed,
        n_estimators=n_estimators,
    )

    structural_ok = True
    try:
        assert_no_post_hoc_columns(base["design_columns"])
    except Exception:  # pragma: no cover - only on a real regression
        structural_ok = False
    leaked_names = sorted(set(base["design_columns"]) & set(POST_HOC_FIELDS))

    delta_true, per_true = compare_metrics(base, true_run)
    delta_noise, per_noise = compare_metrics(base, noise_run)

    # detectability of a would-be leak: rank the test rows by the injected label
    # itself (only possible inside the simulator) -- AUC must be 1.0
    from .features import build_design

    injected = inject_post_hoc(ds, mode="true")
    y = build_design(injected)["y_cf"]
    passed_col = np.asarray(
        [float(c["passed"]) for rnd in injected.rounds for c in rnd.candidates]
    )
    leak_auc = auc(y, passed_col)

    control = null_control(
        seed=seed,
        n_rounds=n_rounds,
        slate_size=slate_size,
        n_windows=5,
        n_permutations=n_permutations,
        band=band,
    )

    return {
        "structural": {
            "no_post_hoc_columns": structural_ok,
            "post_hoc_columns_found": leaked_names,
            "n_design_columns": len(base["design_columns"]),
            "field_ts_guard": "assert_dispatch_time() runs on every candidate of every round",
        },
        "behavioural": {
            "max_abs_delta_true": float(delta_true),
            "max_abs_delta_noise": float(delta_noise),
            "per_metric_delta_true": per_true,
            "per_metric_delta_noise": per_noise,
            "clean_pooled": base["pooled"],
        },
        "negative_control": control,
        "detectability": {
            "injected_label_auc": float(leak_auc),
            "note": "a post-hoc column read as a feature would score AUC 1.0, so the "
            "no-delta result is informative rather than vacuous",
        },
    }


def null_control(
    *,
    seed: int = 7,
    n_rounds: int = 2000,
    slate_size: int = 8,
    n_windows: int = 5,
    n_permutations: int = 25,
    band: tuple[float, float] = (0.45, 0.55),
) -> dict[str, Any]:
    """Label-permutation negative control, replicated.

    A *single* permutation of the training labels is a noisy null draw: with a
    thin early window (83 labelled rounds) the under-determined fit can land far
    from 0.5 by chance, which says nothing about leakage.  The control is
    therefore replicated ``n_permutations`` times and judged on the *mean* AUC,
    reported together with its spread.  Test labels are never touched, and the
    AUC is averaged per window before pooling so a base-rate drift between
    windows cannot masquerade as signal.
    """
    from .backtest import split_windows
    from .features import build_design
    from .fixtures import make_dataset
    from .models import LogisticModel, ips_weights

    ds = make_dataset(seed=seed, n_rounds=n_rounds, slate_size=slate_size)
    data = build_design(ds)
    names = data["names"]
    S = int(data["slate"])
    rows = np.arange(n_rounds * S).reshape(n_rounds, S)
    wins = split_windows(n_rounds, n_windows)

    per_perm: list[dict[str, float]] = []
    for p in range(n_permutations):
        rng = np.random.default_rng(seed * 1000 + p)
        aucs: list[float] = []
        pooled_y: list[np.ndarray] = []
        pooled_p: list[np.ndarray] = []
        for t_start, t_end in wins:
            tr = rows[:t_start].ravel()
            te = rows[t_start:t_end].ravel()
            tr_log = tr[data["logged"][tr]]
            w = ips_weights(data["propensity"][tr_log])
            y_perm = rng.permutation(data["y_logged"][tr_log])
            m = LogisticModel().fit(data["X"][tr_log], y_perm, sample_weight=w, names=names)
            pred = m.predict(data["X"][te])
            aucs.append(auc(data["y_cf"][te], pred))
            pooled_y.append(data["y_cf"][te])
            pooled_p.append(pred)
        per_perm.append(
            {
                "auc_per_window_mean": float(np.mean(aucs)),
                "auc_pooled": auc(np.concatenate(pooled_y), np.concatenate(pooled_p)),
            }
        )
    means = [d["auc_per_window_mean"] for d in per_perm]
    mean_auc = float(np.mean(means))
    return {
        "auc": mean_auc,
        "auc_per_permutation": means,
        "auc_std": float(np.std(means)),
        "auc_sem": float(np.std(means) / np.sqrt(len(means))),
        "auc_min": float(np.min(means)),
        "auc_max": float(np.max(means)),
        "auc_pooled_per_permutation": [d["auc_pooled"] for d in per_perm],
        "n_permutations": int(n_permutations),
        "band": [float(band[0]), float(band[1])],
        "in_band": bool(band[0] <= mean_auc <= band[1]),
        "estimator": "per-window AUC averaged (pooling across windows would mix base-rate drift)",
    }
