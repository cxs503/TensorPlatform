"""Maturity study for the cross-solver multi-fidelity infrastructure.

Runs the three acceptance studies on *synthetic, domain-neutral* fixtures and
returns one machine-readable record.  Nothing here types a number into a report:
the report generator reads this record.

Studies
-------
``clock``      exact gcd/lcm derivation, per-solver sub-stepping plan, skew audit
               against the two floating point shortcuts, lattice nesting checks.
``exchange``   boundary conservation on the global grid versus free-running
               clocks (offset sweep), plus non-synchronous interpolation error.
``distill``    teacher → student transfer: label budget ladder (closed-form
               baseline / distillation disabled / distillation enabled), the
               alpha × width Pareto sweep and the measured cost model.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List

import numpy as np

from src.multifidelity.clock_manager import (
    ClockContractViolation,
    GlobalClockManager,
    Strategy,
)
from src.multifidelity.distill import (
    DistillConfig,
    EmpiricalFormulaStudent,
    HighFidelityTeacher,
    accuracy,
    cost_us,
    pareto_sweep,
    rel_rmse,
    sample_params,
    train_student,
)
from src.multifidelity.exchange import (
    Channel,
    exchange_free_running,
    exchange_synced,
    interpolation_audit,
    summarise,
)

# --------------------------------------------------------------------------- #
# Fixture definition (synthetic: three opaque participants, no physics)
# --------------------------------------------------------------------------- #
HORIZON = 0.5                      # dimensionless coupling horizon
CLOCKS = {                          # dimensionless natural time steps
    "solver_fast": 1.0 / 960.0,
    "solver_mid": 1.0 / 480.0,
    "solver_slow": 1.0 / 60.0,
}
PRODUCER = "solver_fast"
CONSUMER = "solver_slow"
CHANNELS = (
    Channel("mass_flux", base=1.0, amp=0.50, freq=8.0, phase=0.0, tag="mass"),
    Channel("momentum_flux", base=0.8, amp=0.35, freq=8.0, phase=0.7, amp2=0.10, phase2=0.3, tag="momentum"),
    Channel("energy_flux", base=1.2, amp=0.60, freq=8.0, phase=1.9, tag="energy"),
)
OFFSETS = (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)


def _digest(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# 1. clock synchronisation
# --------------------------------------------------------------------------- #
def clock_study() -> Dict[str, object]:
    out: Dict[str, object] = {"horizon": HORIZON, "clocks": CLOCKS, "strategies": {}}
    for strategy in (Strategy.GCD, Strategy.LCM):
        manager = GlobalClockManager(strategy)
        for name, dt in CLOCKS.items():
            manager.register(name, dt)
        grid = manager.build(HORIZON)
        out["strategies"][strategy.value] = {
            "grid": grid.to_dict(),
            "skew": manager.skew_audit(grid),
            "nesting": {
                "producer_to_consumer": grid.nesting_ok(PRODUCER, CONSUMER),
                "consumer_to_producer": grid.nesting_ok(CONSUMER, PRODUCER),
            },
            "plan": {
                name: {
                    "dt": CLOCKS[name],
                    "publication_nodes": len(grid.publication_nodes(name)),
                    "global_steps_per_own_step": grid.global_steps_per_own_step(name),
                    "own_steps_per_global_step": grid.solver_steps_per_global(name),
                }
                for name in CLOCKS
            },
        }
    # the integer-ratio contract must reject a clock that cannot join the grid
    rejections = {}
    for strategy in (Strategy.GCD, Strategy.LCM):
        manager = GlobalClockManager(strategy)
        try:
            manager.register("a", 1.0 / 3.0)
            manager.register("b", np.sqrt(2) / 7.0)   # incommensurate by construction
            manager.build(1.0)
            rejections[strategy.value] = False
        except ClockContractViolation as exc:
            rejections[strategy.value] = str(exc)
    out["incommensurate_rejection"] = rejections
    out["record_digest"] = _digest(out)
    return out


# --------------------------------------------------------------------------- #
# 2. boundary conservation
# --------------------------------------------------------------------------- #
def exchange_study() -> Dict[str, object]:
    manager = GlobalClockManager(Strategy.GCD)
    for name, dt in CLOCKS.items():
        manager.register(name, dt)
    grid = manager.build(HORIZON)

    synced = exchange_synced(manager, grid, CHANNELS, PRODUCER, CONSUMER)
    synced_summary = summarise(synced)

    free: Dict[str, Dict[str, object]] = {"hold": {}, "extrapolate": {}}
    for mode in ("hold", "extrapolate"):
        for offset in OFFSETS:
            ledger = exchange_free_running(
                manager.clocks, CHANNELS, PRODUCER, CONSUMER,
                float(grid.horizon), mode=mode, consumer_offset=offset)
            free[mode][f"{offset:.3f}"] = {
                "summary": summarise(ledger),
                "channels": {k: v.to_dict() for k, v in ledger.items()},
            }

    def worst(mode: str, key: str) -> Dict[str, float]:
        best_offset, best_value = None, -1.0
        for offset, payload in free[mode].items():
            value = payload["summary"][key]
            if value > best_value:
                best_value, best_offset = value, offset
        return {"offset": float(best_offset), "value": float(best_value)}

    # worst-case aggregation over channels and consumer phase
    free_worst = {
        mode: {
            "max_frame_l1_defect": worst(mode, "max_frame_l1_defect"),
            "max_net_defect": worst(mode, "max_net_defect"),
            "max_frame_max_defect": worst(mode, "max_frame_max_defect"),
        }
        for mode in ("hold", "extrapolate")
    }

    interp = {
        "producer_lattice": interpolation_audit(
            CHANNELS[0], CLOCKS[PRODUCER], n_query=2000, horizon=HORIZON),
        "consumer_lattice": interpolation_audit(
            CHANNELS[0], CLOCKS[CONSUMER], n_query=2000, horizon=HORIZON),
    }
    out = {
        "horizon": float(grid.horizon),
        "channels": [c.to_dict() for c in CHANNELS],
        "synced": {
            "summary": synced_summary,
            "channels": {k: v.to_dict() for k, v in synced.items()},
            "frame_edges": synced[CHANNELS[0].name].frame_edges.tolist(),
            "frame_required": synced[CHANNELS[0].name].required.tolist(),
            "frame_booked": synced[CHANNELS[0].name].booked.tolist(),
        },
        "free_running": {
            "offsets": list(OFFSETS),
            "by_mode": free,
            "worst": free_worst,
        },
        "interpolation": interp,
    }
    ref_ledger = exchange_free_running(
        manager.clocks, CHANNELS[:1], PRODUCER, CONSUMER, float(grid.horizon),
        mode="hold", consumer_offset=0.5)
    ledger0 = ref_ledger[CHANNELS[0].name]
    out["free_running"]["frame_reference"] = {
        "edges": ledger0.frame_edges.tolist(),
        "required": ledger0.required.tolist(),
        "booked": ledger0.booked.tolist(),
        "offset": 0.5,
    }
    out["record_digest"] = _digest({k: v for k, v in out.items() if k != "record_digest"})
    return out


# --------------------------------------------------------------------------- #
# 3. distillation
# --------------------------------------------------------------------------- #
DISTILL = {
    "n_soft": 600,
    "n_val": 250,
    "n_hard": 12,
    "seed_data": 1,
    "seed_val": 2,
    "widths": (8, 16, 32, 64),
    "alphas": (0.0, 0.25, 0.5, 0.75, 1.0),
    "epochs": 400,
    "bins": 81,
    "temperature_rel": 0.10,
    "main_width": 32,
    "main_alpha": 0.5,
}


def distill_study(verbose: bool = False) -> Dict[str, object]:
    cfg = DISTILL
    teacher = HighFidelityTeacher(grid_sizes=(256, 384, 512, 768))
    X_pool = sample_params(cfg["n_soft"], seed=cfg["seed_data"])
    X_val = sample_params(cfg["n_val"], seed=cfg["seed_val"])
    y_pool, spread_pool = teacher.evaluate_batch(X_pool)
    y_val = teacher.reference_batch(X_val)                 # third, independent number
    teacher_val, _ = teacher.evaluate_batch(X_val)
    teacher_dev = np.abs(teacher_val - y_val) / np.abs(y_val)
    teacher_cost = cost_us(lambda X: teacher.evaluate_batch(X)[0], X_val[:20], repeats=3)

    n_hard = cfg["n_hard"]
    hard = (X_pool[:n_hard], y_pool[:n_hard], spread_pool[:n_hard])
    base_config = DistillConfig(epochs=cfg["epochs"], bins=cfg["bins"],
                               temperature_rel=cfg["temperature_rel"],
                               width=cfg["main_width"])

    ladder = {}
    closed_form = EmpiricalFormulaStudent().fit(hard[0], hard[1])
    pred_cf = closed_form.predict(X_val)
    ladder["closed_form_baseline"] = {
        "label": "closed-form regression (low-fidelity formula)",
        "val_accuracy_pct": accuracy(pred_cf, y_val),
        "val_rel_rmse": rel_rmse(pred_cf, y_val),
        "cost_us": cost_us(closed_form.predict, X_val),
        "speedup_vs_teacher": teacher_cost / cost_us(closed_form.predict, X_val),
        "n_hard": n_hard,
        "n_soft": 0,
    }
    for name, alpha in (("distillation_disabled", 1.0), ("distillation_enabled", cfg["main_alpha"])):
        res = train_student(*hard, X_pool, y_pool, spread_pool, X_val, y_val,
                            DistillConfig(**{**base_config.__dict__, "alpha": alpha}),
                            teacher_cost=teacher_cost, label=name)
        ladder[name] = res.to_dict()
        if verbose:
            print(f"  {name}: acc={res.val_accuracy:.2f}% cost={res.cost_us:.1f}us")

    results, front = pareto_sweep(
        *hard, X_pool, y_pool, spread_pool, X_val, y_val,
        teacher_cost=teacher_cost,
        widths=cfg["widths"], alphas=cfg["alphas"], base=base_config)
    if verbose:
        print(f"  sweep: {len(results)} points, front {len(front)}")

    out = {
        "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.items()},
        "teacher": {
            "grid_sizes": list(teacher.grid_sizes),
            "reference_sizes": list(teacher.reference_sizes),
            "cost_us": teacher_cost,
            "val_accuracy": accuracy(teacher_val, y_val),
            "val_rel_rmse": rel_rmse(teacher_val, y_val),
            "mean_label_spread_rel": float(np.median(spread_pool) / np.std(y_pool)),
            "deviation_vs_reference_mean": float(np.mean(teacher_dev)),
            "deviation_vs_reference_p95": float(np.percentile(teacher_dev, 95)),
            "deviation_vs_reference_max": float(np.max(teacher_dev)),
        },
        "ladder": ladder,
        "sweep": [r.to_dict() for r in results],
        "pareto": [r.label for r in front],
        "pareto_points": [r.to_dict() for r in front],
        "record_digest": _digest({"ladder": ladder, "sweep": [r.to_dict() for r in results]}),
    }
    return out


# --------------------------------------------------------------------------- #
# criteria
# --------------------------------------------------------------------------- #
def build_criteria(clock: Dict, exchange: Dict, distill: Dict) -> Dict[str, object]:
    gcd = clock["strategies"]["gcd"]
    lcm = clock["strategies"]["lcm"]
    syn = exchange["synced"]["summary"]
    free_worst = exchange["free_running"]["worst"]
    # the criterion is attached to the global grid (that is where exchange
    # happens); the consumer's own lattice is reported as context, because a
    # 1/60 lattice cannot carry an 8 Hz signal and that is exactly why the
    # interface transmits interval integrals instead of samples
    interp_max = exchange["interpolation"]["producer_lattice"]["linear"]["max_rel_error"]
    interp_consumer = exchange["interpolation"]["consumer_lattice"]["linear"]["max_rel_error"]
    ladder = distill["ladder"]
    teacher_acc = distill["teacher"]["val_accuracy"]
    acc_enabled = ladder["distillation_enabled"]["val_accuracy_pct"]
    acc_disabled = ladder["distillation_disabled"]["val_accuracy_pct"]
    gap = teacher_acc - acc_enabled
    drop_points = acc_enabled - acc_disabled
    drop_rel = drop_points / acc_enabled
    speedup = ladder["distillation_enabled"]["speedup_vs_teacher"]

    criteria = {
        "a_clock_sync": {
            "statement": "dt_global computed correctly for gcd and lcm; data exchange "
                         "error at non-synchronous instants < 0.1%",
            "dt_global_gcd_exact": gcd["grid"]["dt_global"],
            "dt_global_lcm_exact": lcm["grid"]["dt_global"],
            "ratio_integrality_gcd": all(gcd["skew"]["ratio_integrality"].values()),
            "ratio_integrality_lcm": all(lcm["skew"]["ratio_integrality"].values()),
            "max_skew_float_accumulation_gcd": gcd["skew"]["max_skew_float_accumulation"],
            "max_interpolation_rel_error_linear_global_grid": interp_max,
            "max_interpolation_rel_error_consumer_lattice_context": interp_consumer,
            "threshold_rel_error": 0.001,
            "pass": interp_max < 0.001,
        },
        "b_conservation": {
            "statement": "mass/momentum/energy flux non-conservation at the solver "
                         "boundary < 1% (frame-wise, all channels)",
            "synced_max_net_defect": syn["max_net_defect"],
            "synced_max_frame_l1_defect": syn["max_frame_l1_defect"],
            "synced_max_frame_max_defect": syn["max_frame_max_defect"],
            "threshold": 0.01,
            "pass": syn["max_frame_l1_defect"] < 0.01 and syn["max_net_defect"] < 0.01,
        },
        "c_distillation_gap": {
            "statement": "student validation accuracy vs teacher < 5 points",
            "teacher_accuracy": teacher_acc,
            "student_accuracy": acc_enabled,
            "gap_points": gap,
            "threshold_points": 5.0,
            "pass": gap < 5.0,
        },
        "d_speedup": {
            "statement": "student inference speedup vs teacher > 10x",
            "teacher_cost_us": distill["teacher"]["cost_us"],
            "student_cost_us": ladder["distillation_enabled"]["cost_us"],
            "speedup": speedup,
            "threshold": 10.0,
            "pass": speedup > 10.0,
        },
        "e_negative_controls": {
            "statement": "disable clock sync => frame-wise conservation violation > 5%; "
                         "disable distillation => student accuracy drop > 10%",
            "no_sync_frame_l1_defect": free_worst["hold"]["max_frame_l1_defect"]["value"],
            "no_sync_frame_l1_offset": free_worst["hold"]["max_frame_l1_defect"]["offset"],
            "no_sync_net_defect": free_worst["hold"]["max_net_defect"]["value"],
            "no_sync_net_offset": free_worst["hold"]["max_net_defect"]["offset"],
            "no_sync_threshold": 0.05,
            "no_distill_accuracy_disabled": acc_disabled,
            "no_distill_accuracy_enabled": acc_enabled,
            "no_distill_drop_points": drop_points,
            "no_distill_drop_relative": drop_rel,
            "no_distill_threshold_points": 10.0,
            "pass": (free_worst["hold"]["max_frame_l1_defect"]["value"] > 0.05
                     and drop_points > 10.0),
        },
        "f_deliverables": {
            "statement": "pytest >= 15 (>=2 negative controls), 3 figures, six-element report",
            "pytest_count": None,       # filled by the CLI after the test run
            "negative_controls": ["e_negative_controls.no_sync", "e_negative_controls.no_distill"],
            "figures": ["fig1_clock_sync_timeline.png", "fig2_conservation_error.png",
                        "fig3_distillation_pareto.png"],
            "report": "report.md",
            "pass": None,
        },
    }
    return criteria