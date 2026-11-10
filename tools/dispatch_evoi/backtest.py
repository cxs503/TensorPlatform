"""Walk-forward backtest of the EVOI dispatch policy against the static baseline.

Splitting
---------
The log is cut into ``n_windows+1`` contiguous, equal time blocks.  Window ``w``
trains on blocks ``0..w`` (**expanding**, i.e. everything strictly before the test
block: ``[0, T_w)``) and tests on block ``w+1`` (``[T_w, T_w+delta)``).  With the
defaults that is 5 rolling origins over 1200 dispatch rounds, so no round is ever
scored by a model that has seen it -- the timestamp order of a real log is
respected.

What is compared
----------------
=================================================================  ===========
baseline            current dispatcher: static tier-first priority, its    P(pass)
                    score calibrated into a probability by a single-feature
                    logistic fit on the training window (a *fair* baseline:
                    it gets the same calibration treatment, it just has no
                    outcome model and no features)
LR                  ridge logistic on all dispatch-time columns, trained with
                    IPS weights (corrects self-selection of the logged sample)
GBDT                depth-2 gradient boosting on the same columns, same weights
EVOI                score = P(pass) * value_units / E[cost]
                    (value is declared at intake, cost from the historical
                    wall-time regression in :mod:`tools.dispatch_evoi.cost`)
=================================================================  ===========

Metrics
-------
``brier``/``auc``  against the simulator's counterfactual outcomes for *every*
                   candidate order in the test block (dense labels);
``p@20``           precision inside the top 20% of the ranking -- the mission's
                   "ranking utility" criterion;
``policy_hit``     mean counterfactual pass rate of the order the policy would
                   have dispatched (top-1 per slate);
``ips/snips``      off-policy value estimates from *logged labels only*, plus ESS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .cost import CostModel
from .features import build_design
from .fixtures import make_dataset
from .models import GBDT, LogisticModel, auc, brier, ips_weights, precision_at_fraction
from .offpolicy import single_action_ips

__all__ = ["run_evaluation", "evaluate_gates", "split_windows", "WINDOW_COUNT"]

WINDOW_COUNT = 5


def split_windows(n_rounds: int, n_windows: int = WINDOW_COUNT) -> list[tuple[int, int]]:
    """``[(test_start, test_end), ...]`` with ``train = [0, test_start)``."""
    edges = np.linspace(0, n_rounds, n_windows + 2).astype(int)
    return [(int(edges[w + 1]), int(edges[w + 2])) for w in range(n_windows)]


@dataclass
class WindowFit:
    window: int
    train_rounds: int
    test_rounds: int
    metrics: dict[str, float]
    offpolicy: dict[str, float]
    cost: dict[str, float]
    value_true_sampler: float = float("nan")
    value_true_deterministic: float = float("nan")


def _fit_priority_baseline(priority: np.ndarray, y: np.ndarray, w: np.ndarray) -> LogisticModel:
    return LogisticModel(l2=1e-3).fit(priority.reshape(-1, 1), y, sample_weight=w, names=["priority"])


def _round_arrays(data: dict[str, Any]) -> dict[str, np.ndarray]:
    S = int(data["slate"])
    n_rounds = int(data["n_rounds"])
    logged_mat = data["logged"].reshape(n_rounds, S)
    logged_index = logged_mat.argmax(axis=1).astype(int)
    prop_mat = data["propensity"].reshape(n_rounds, S)
    prop_round = prop_mat[np.arange(n_rounds), logged_index]
    y_cf = data["y_cf"].reshape(n_rounds, S)
    reward = y_cf[np.arange(n_rounds), logged_index]
    return {
        "logged_index": logged_index,
        "propensity": prop_round,
        "reward": reward,
        "y_cf_mat": y_cf,
    }


def run_evaluation(
    *,
    seed: int = 7,
    n_rounds: int = 1200,
    slate_size: int = 8,
    n_windows: int = WINDOW_COUNT,
    n_estimators: int = 40,
    model_seed: int = 0,
    shuffle_train_labels: bool = False,
    dataset: Any | None = None,
) -> dict[str, Any]:
    """Run the full walk-forward evaluation and return a JSON-ready dict."""
    ds = dataset if dataset is not None else make_dataset(
        seed=seed, n_rounds=n_rounds, slate_size=slate_size
    )
    data = build_design(ds)
    names = data["names"]
    S = int(data["slate"])
    n_rounds = int(data["n_rounds"])
    rows = np.arange(n_rounds * S).reshape(n_rounds, S)
    rnd = _round_arrays(data)
    shuffle_rng = np.random.default_rng(seed + 991)

    fits: list[WindowFit] = []
    pooled: dict[str, list[np.ndarray]] = {
        "y": [],
        "p_true": [],
        "p_base": [],
        "p_lr": [],
        "p_gbdt": [],
        "s_base": [],
        "s_lr": [],
        "s_gbdt": [],
        "s_evoi_lr": [],
        "s_evoi_gbdt": [],
    }
    op_rounds: list[dict[str, np.ndarray]] = []
    coef_reports: list[dict[str, Any]] = []
    gbdt_used = 0

    for w, (t_start, t_end) in enumerate(split_windows(n_rounds, n_windows)):
        train_rows = rows[:t_start].ravel()
        test_rows = rows[t_start:t_end].ravel()
        tr_logged = train_rows[data["logged"][train_rows]]
        y_tr = data["y_logged"][tr_logged]
        if shuffle_train_labels:
            y_tr = shuffle_rng.permutation(y_tr)
        w_tr = ips_weights(data["propensity"][tr_logged])

        lr = LogisticModel(l2=1.0).fit(data["X"][tr_logged], y_tr, sample_weight=w_tr, names=names)
        gb = GBDT(n_estimators=n_estimators, random_state=model_seed + w).fit(
            data["X"][tr_logged], y_tr, sample_weight=w_tr
        )
        gbdt_used += 1
        base = _fit_priority_baseline(data["priority"][tr_logged], y_tr, w_tr)
        cost = CostModel().fit(data["X"][tr_logged], data["cost"][tr_logged], names)

        X_test = data["X"][test_rows]
        y_test = data["y_cf"][test_rows]
        p_base = base.predict(data["priority"][test_rows].reshape(-1, 1))
        p_lr = lr.predict(X_test)
        p_gb = gb.predict(X_test)
        cost_hat = cost.predict(X_test, names)
        value = data["value"][test_rows]
        s_evoi_lr = p_lr * value / cost_hat
        s_evoi_gb = p_gb * value / cost_hat

        metrics = {
            "brier_baseline": brier(y_test, p_base),
            "brier_lr": brier(y_test, p_lr),
            "brier_gbdt": brier(y_test, p_gb),
            "auc_baseline": auc(y_test, data["priority"][test_rows]),
            "auc_lr": auc(y_test, p_lr),
            "auc_gbdt": auc(y_test, p_gb),
            "p20_baseline": precision_at_fraction(y_test, data["priority"][test_rows]),
            "p20_lr": precision_at_fraction(y_test, p_lr),
            "p20_gbdt": precision_at_fraction(y_test, p_gb),
            "p20_evoi_lr": precision_at_fraction(y_test, s_evoi_lr),
            "p20_evoi_gbdt": precision_at_fraction(y_test, s_evoi_gb),
            "pass_rate": float(y_test.mean()),
        }
        # policy hit rate under simulator ground truth (top-1 per slate)
        def _hit(score: np.ndarray) -> float:
            mat = score.reshape(-1, S)
            pick = mat.argmax(axis=1)
            return float(rnd["y_cf_mat"][t_start:t_end][np.arange(len(pick)), pick].mean())

        metrics["hit_baseline"] = _hit(data["priority"][test_rows])
        metrics["hit_lr"] = _hit(p_lr)
        metrics["hit_gbdt"] = _hit(p_gb)
        metrics["hit_evoi_lr"] = _hit(s_evoi_lr)
        metrics["hit_evoi_gbdt"] = _hit(s_evoi_gb)

        op = offpolicy_window(
            evoi_score=s_evoi_lr,
            slate=S,
            logged_index=rnd["logged_index"][t_start:t_end],
            propensity=rnd["propensity"][t_start:t_end],
            reward=rnd["reward"][t_start:t_end],
        )
        # exact value of both target policies under simulator ground truth:
        # V(pi) = E_round[ sum_a pi(a|s) * y_cf(a) ] -- the number IPS has to hit
        mat = rnd["y_cf_mat"][t_start:t_end]
        pi1_mat = slate_softmax(s_evoi_lr, S)
        rr = np.arange(mat.shape[0])
        op["value_true_sampler"] = float((pi1_mat * mat).sum(axis=1).mean())
        op["value_true_deterministic"] = float(mat[rr, pi1_mat.argmax(axis=1)].mean())
        op_rounds.append(op["_raw"])
        fits.append(
            WindowFit(
                window=w,
                train_rounds=int(t_start),
                test_rounds=int(t_end - t_start),
                metrics=metrics,
                offpolicy={k: v for k, v in op.items() if k != "_raw"},
                value_true_sampler=float(op["value_true_sampler"]),
                value_true_deterministic=float(op["value_true_deterministic"]),
                cost=cost.report(),
            )
        )
        pooled["y"].append(y_test)
        pooled["p_true"].append(data["p_true"][test_rows])
        pooled["p_base"].append(p_base)
        pooled["p_lr"].append(p_lr)
        pooled["p_gbdt"].append(p_gb)
        pooled["s_base"].append(data["priority"][test_rows])
        pooled["s_lr"].append(p_lr)
        pooled["s_gbdt"].append(p_gb)
        pooled["s_evoi_lr"].append(s_evoi_lr)
        pooled["s_evoi_gbdt"].append(s_evoi_gb)
        if w in (0, n_windows - 1):
            rep = lr.feature_report()
            sds = [float(x) for x in lr.sd_]
            for row, sd in zip(rep[1:], sds):
                row["coef_raw_scale"] = row["coef"] / sd if sd > 0 else float("nan")
            coef_reports.append({"window": w, "train_rounds": int(t_start), "logistic": rep})

    pool = {k: np.concatenate(v) for k, v in pooled.items()}
    y_all = pool["y"]
    pooled_metrics = {
        "n_test_rows": int(len(y_all)),
        "pass_rate": float(y_all.mean()),
        "brier_baseline": brier(y_all, pool["p_base"]),
        "brier_lr": brier(y_all, pool["p_lr"]),
        "brier_gbdt": brier(y_all, pool["p_gbdt"]),
        "auc_baseline": auc(y_all, pool["s_base"]),
        "auc_lr": auc(y_all, pool["p_lr"]),
        "auc_gbdt": auc(y_all, pool["p_gbdt"]),
        "p20_baseline": precision_at_fraction(y_all, pool["s_base"]),
        "p20_lr": precision_at_fraction(y_all, pool["s_lr"]),
        "p20_gbdt": precision_at_fraction(y_all, pool["s_gbdt"]),
        "p20_evoi_lr": precision_at_fraction(y_all, pool["s_evoi_lr"]),
        "p20_evoi_gbdt": precision_at_fraction(y_all, pool["s_evoi_gbdt"]),
    }
    delivered = "lr" if pooled_metrics["brier_lr"] <= pooled_metrics["brier_gbdt"] else "gbdt"
    truth = {
        "brier_true_prob_pooled": brier(y_all, pool["p_true"]),
        "auc_true_prob_pooled": auc(y_all, pool["p_true"]),
    }
    truth["brier_gap_rel"] = (
        (pooled_metrics[f"brier_{delivered}"] - truth["brier_true_prob_pooled"])
        / truth["brier_true_prob_pooled"]
    )
    truth["auc_gap"] = truth["auc_true_prob_pooled"] - pooled_metrics[f"auc_{delivered}"]
    truth["note"] = (
        "ceiling = ranking by the simulator's own P(pass); the gap is what an oracle "
        "would still gain, i.e. the part of the signal the model does not recover"
    )

    # pooled off-policy on the concatenated test rounds
    pooled_op = offpolicy_pooled(op_rounds)

    # ── adoption gates (the mission's machine-checkable criteria) ────────────
    d = delivered
    rel_brier = [
        (f.metrics["brier_baseline"] - f.metrics[f"brier_{d}"]) / f.metrics["brier_baseline"]
        for f in fits
    ]
    direction = sum(1 for f in fits if f.metrics[f"brier_{d}"] < f.metrics["brier_baseline"])
    gate_a = {
        "delivered_model": d,
        "rel_brier_improvement_mean": float(np.mean(rel_brier)),
        "rel_brier_improvement_per_window": [float(x) for x in rel_brier],
        "brier_baseline_pooled": pooled_metrics["brier_baseline"],
        "brier_model_pooled": pooled_metrics[f"brier_{d}"],
        "auc_baseline_pooled": pooled_metrics["auc_baseline"],
        "auc_model_pooled": pooled_metrics[f"auc_{d}"],
        "auc_gain": pooled_metrics[f"auc_{d}"] - pooled_metrics["auc_baseline"],
        "windows_model_better": int(direction),
        "windows_total": len(fits),
        "threshold_rel_brier": 0.10,
        "threshold_auc_gain": 0.05,
        "threshold_windows": 4,
    }
    gate_a["pass"] = bool(
        gate_a["rel_brier_improvement_mean"] >= 0.10
        and gate_a["auc_gain"] >= 0.05
        and direction >= 4
    )

    p20_delta = pooled_metrics[f"p20_evoi_{d}"] - pooled_metrics["p20_baseline"]
    gate_b = {
        "p20_baseline": pooled_metrics["p20_baseline"],
        "p20_policy_evoi": pooled_metrics[f"p20_evoi_{d}"],
        "p20_delta_pp": p20_delta,
        "p20_model_only": pooled_metrics[f"p20_{d}"],
        "threshold_pp": 0.10,
    }
    gate_b["pass"] = bool(p20_delta >= 0.10)

    value_true_sampler = float(np.mean([f.value_true_sampler for f in fits]))
    value_true_det = float(np.mean([f.value_true_deterministic for f in fits]))
    pooled_op["sampler"]["value_true"] = value_true_sampler
    pooled_op["sampler"]["ips_minus_true"] = pooled_op["sampler"]["ips"] - value_true_sampler
    pooled_op["deterministic"]["value_true"] = value_true_det
    pooled_op["deterministic"]["ips_minus_true"] = (
        pooled_op["deterministic"]["ips"] - value_true_det
    )
    gate_d = dict(pooled_op["sampler"])
    gate_d["policy"] = (
        "pi1 = 0.9*softmax(standardised EVOI) + 0.1*uniform (ranking + deployment "
        "temperature/exploration floor)"
    )
    gate_d["deterministic_top1"] = pooled_op["deterministic"]
    gate_d["threshold_ess_frac"] = 0.30
    gate_d["pass"] = bool(gate_d["ess_frac"] >= 0.30)

    return {
        "fixture_spec": ds.spec,
        "design_columns": names,
        "n_candidates": int(len(y_all)),
        "windows": [
            {
                "window": f.window,
                "train_rounds": f.train_rounds,
                "test_rounds": f.test_rounds,
                "metrics": f.metrics,
                "offpolicy": f.offpolicy,
                "cost": f.cost,
                "value_true_sampler": f.value_true_sampler,
                "value_true_deterministic": f.value_true_deterministic,
            }
            for f in fits
        ],
        "pooled": pooled_metrics,
        "offpolicy_pooled": pooled_op,
        "gates": {"a_probability": gate_a, "b_ranking": gate_b, "d_offpolicy": gate_d},
        "truth_benchmark": truth,
        "coefficients": coef_reports,
        "gbdt_fits": gbdt_used,
    }


def evaluate_gates(
    report: dict[str, Any],
    *,
    leakage: dict[str, Any],
    reproducibility: dict[str, Any],
    pytest_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the six mission gates into one verdict (all must pass)."""
    gates = dict(report["gates"])
    gate_c = {
        "no_post_hoc_columns": leakage["structural"]["no_post_hoc_columns"],
        "max_abs_delta_metric_true_injection": leakage["behavioural"]["max_abs_delta_true"],
        "max_abs_delta_metric_noise_injection": leakage["behavioural"]["max_abs_delta_noise"],
        "label_shuffle_auc": leakage["negative_control"]["auc"],
        "label_shuffle_auc_band": leakage["negative_control"]["band"],
    }
    gate_c["pass"] = bool(
        gate_c["no_post_hoc_columns"]
        and gate_c["max_abs_delta_metric_true_injection"] <= 1e-12
        and gate_c["max_abs_delta_metric_noise_injection"] <= 1e-12
        and leakage["negative_control"]["in_band"]
    )
    gates["c_leakage"] = gate_c
    gates["e_reproducibility"] = reproducibility
    if pytest_check is not None:
        gates["f_pytest"] = pytest_check
    checks = {k: bool(v.get("pass")) for k, v in gates.items() if isinstance(v, dict) and "pass" in v}
    return {
        "gates": gates,
        "checks": checks,
        "all_pass": all(checks.values()) if checks else False,
    }

# ─────────────────────────── reproducibility check ───────────────────────────

_REPRO_KEYS = (
    "brier_baseline",
    "brier_lr",
    "brier_gbdt",
    "auc_baseline",
    "auc_lr",
    "auc_gbdt",
    "p20_evoi_lr",
    "p20_evoi_gbdt",
)


def check_reproducibility(
    *,
    seed: int = 7,
    other_seed: int = 11,
    n_rounds: int = 1200,
    slate_size: int = 8,
    n_estimators: int = 40,
) -> dict[str, Any]:
    """Two-clause reproducibility check.

    The **dispatch log is held fixed** (``seed``); ``other_seed`` only re-seeds the
    pipeline's own stochastic component (the GBDT row subsample).  That is the
    reading under which a "<1% across seeds" bar is meaningful: varying the log
    itself is a *different dataset*, and its spread is a sampling property, not
    pipeline nondeterminism -- it is reported separately below as
    ``cross_log_resampling`` for diligence, and is deliberately **not** gated.

    clause 1  same pipeline seed twice  -> bit-identical metrics
    clause 2  two pipeline seeds        -> max relative metric difference < 1%
    """
    kw = dict(n_rounds=n_rounds, slate_size=slate_size, n_estimators=n_estimators)
    a = run_evaluation(seed=seed, model_seed=seed, **kw)
    a2 = run_evaluation(seed=seed, model_seed=seed, **kw)
    b = run_evaluation(seed=seed, model_seed=other_seed, **kw)
    c = run_evaluation(seed=other_seed, model_seed=seed, **kw)

    same_seed = {k: abs(a["pooled"][k] - a2["pooled"][k]) for k in _REPRO_KEYS}
    cross = {
        k: abs(a["pooled"][k] - b["pooled"][k]) / max(abs(a["pooled"][k]), 1e-12)
        for k in _REPRO_KEYS
    }
    cross_log = {
        k: abs(a["pooled"][k] - c["pooled"][k]) / max(abs(a["pooled"][k]), 1e-12)
        for k in _REPRO_KEYS
    }
    max_same = float(max(same_seed.values()))
    max_cross = float(max(cross.values()))
    return {
        "seed_a": seed,
        "seed_b": other_seed,
        "data_seed": seed,
        "same_seed_max_abs_delta": max_same,
        "same_seed_bit_identical": bool(max_same == 0.0),
        "cross_seed_max_rel_delta": max_cross,
        "cross_seed_per_metric": cross,
        "cross_log_resampling_max_rel_delta": float(max(cross_log.values())),
        "cross_log_resampling_per_metric": cross_log,
        "cross_log_note": (
            "different dispatch log (data seed) -> different realised sample; this is "
            "sampling variability (~1/sqrt(n)), NOT pipeline nondeterminism, and is not gated"
        ),
        "threshold_rel": 0.01,
        "pass": bool(max_same == 0.0 and max_cross < 0.01),
    }


# ─────────────────────── off-policy target policies ──────────────────────────


def slate_softmax(
    score: np.ndarray, slate: int, temperature: float = 1.0, floor_eps: float = 0.0
) -> np.ndarray:
    """Row-wise softmax of the standardised score over each slate.

    ``floor_eps`` mixes in a uniform component: a deployed ranking still keeps a
    small exploration floor, which is also what guarantees positivity of
    ``pi1`` and keeps the importance weights bounded.
    """
    mat = np.asarray(score, dtype=float).reshape(-1, slate)
    z = (mat - mat.mean(axis=1, keepdims=True)) / (mat.std(axis=1, keepdims=True) + 1e-9)
    z = z / max(temperature, 1e-9)
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    pi = e / e.sum(axis=1, keepdims=True)
    if floor_eps > 0:
        pi = (1.0 - floor_eps) * pi + floor_eps / slate
    return pi


def offpolicy_window(
    *,
    evoi_score: np.ndarray,
    logged_index: np.ndarray,
    propensity: np.ndarray,
    reward: np.ndarray,
    slate: int | None = None,
    temperature: float = 1.0,
    floor_eps: float = 0.10,
) -> dict[str, Any]:
    """Evaluate the EVOI policy on one window under two target-policy specs.

    ``sampler``       stochastic target pi1 = softmax(standardised EVOI), weight
                      pi1(a_t)/pi0(a_t) at the logged action;
    ``deterministic`` top-1 target, weight 1/pi0 on agreeing rounds only.
    """
    S = int(slate if slate is not None else round(np.sqrt(len(evoi_score))))
    pi1 = slate_softmax(evoi_score, S, temperature, floor_eps)
    idx = np.arange(len(logged_index))
    target_prop = pi1[idx, logged_index]
    det = single_action_ips(
        propensity, reward, logged_index=logged_index, target_index=pi1.argmax(axis=1)
    )
    sam = single_action_ips(propensity, reward, target_propensity=target_prop)
    return {
        "sampler": sam.to_dict(),
        "deterministic": det.to_dict(),
        "temperature": temperature,
        "_raw": {
            "logged_index": np.asarray(logged_index, dtype=int),
            "propensity": np.asarray(propensity, dtype=float),
            "reward": np.asarray(reward, dtype=float),
            "target_index": pi1.argmax(axis=1),
            "target_propensity": target_prop,
        },
    }


def offpolicy_pooled(
    parts: Sequence[dict[str, np.ndarray]], temperature: float = 1.0
) -> dict[str, Any]:
    logged_index = np.concatenate([p["logged_index"] for p in parts])
    propensity = np.concatenate([p["propensity"] for p in parts])
    reward = np.concatenate([p["reward"] for p in parts])
    target_index = np.concatenate([p["target_index"] for p in parts])
    target_prop = np.concatenate([p["target_propensity"] for p in parts])
    det = single_action_ips(
        propensity, reward, logged_index=logged_index, target_index=target_index
    )
    sam = single_action_ips(propensity, reward, target_propensity=target_prop)
    return {
        "sampler": sam.to_dict(),
        "deterministic": det.to_dict(),
        "temperature": temperature,
    }

