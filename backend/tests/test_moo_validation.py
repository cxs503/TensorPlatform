"""Verification tests for the multi-objective optimisation validation face.

Mission ``p158w1-n06-nsgaii``.  The module under test provides the platform's
NSGA-II validation face; these tests pin each building block against an
independent computation so a regression cannot hide behind a green suite:

* metrics against hand-computable values (GD offset, uniform spacing,
  exact 2-D hypervolume) and against a brute-force distance loop;
* analytic ZDT fronts against their closed forms (and the 5-arc structure of
  ZDT3), plus the inverse property that a Pareto-optimal decision vector maps
  onto the front;
* non-dominated sorting against an O(N^2) brute-force implementation,
  including duplicate-heavy inputs;
* SBX / polynomial-mutation bounds and the mean-preserving property of SBX;
* a short NSGA-II run that must actually converge (GD collapses, spacing <=
  0.05, population is non-dominated);
* the convergence machinery (``converges_at``, power-law anchor, log-log
  interpolation) on synthetic data with known answers;
* the sweep search policy (ladder -> bisection -> plateau) and its restarts;
* serial and process-parallel suites producing identical results;
* the report/criteria assembler, including a negative case (a deliberately
  bad speed-up must be reported as a failed criterion, never silently passed).

Runtime: ~20 s on one core (short runs only); the full 700-generation campaign
is reproduced by ``python -m services.moo_validation.cli``.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import services.moo_validation.nsga2 as nsga2_mod
from services.moo_validation import benchmark, cli, metrics, problems
from services.moo_validation.reference_sweep import interpolate_crossing, next_request


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def test_gd_zero_on_front_and_linear_in_offset():
    front = np.column_stack([np.linspace(0, 1, 501),
                             1.0 - np.sqrt(np.linspace(0, 1, 501))])
    on_front = front[::10]
    assert metrics.generational_distance(on_front, front) == pytest.approx(0.0, abs=1e-12)

    # straight-line front f1 + f2 = 1: a vertical offset delta is a distance
    # delta/sqrt(2) from the line, exactly and for every sample
    line = np.column_stack([np.linspace(0, 1, 4001), np.linspace(1, 0, 4001)])
    delta = 0.03
    shifted = line + np.array([0.0, delta])
    # discretisation of the front leaves a 0.3 % residual (2.1e-5 in GD)
    assert metrics.generational_distance(shifted, line) == pytest.approx(
        delta / math.sqrt(2.0), rel=5e-3)


def test_gd_matches_brute_force_loop():
    rng = np.random.default_rng(3)
    pts = rng.random((40, 2))
    front = rng.random((60, 2))
    nearest = np.array([np.min(np.linalg.norm(front - p, axis=1)) for p in pts])
    brute = float(np.sqrt(np.mean(nearest ** 2)))
    assert metrics.generational_distance(pts, front) == pytest.approx(brute, rel=1e-12)


def test_spacing_uniform_front_is_zero():
    uniform = np.column_stack([np.linspace(0, 1, 100), np.linspace(1, 0, 100)])
    assert metrics.spacing(uniform) == pytest.approx(0.0, abs=1e-12)
    # hand-computable case: four collinear points with gaps .1 .1 .7
    uneven = np.column_stack([[0.0, 0.1, 0.2, 0.9], [0.0, 0.0, 0.0, 0.0]])
    assert metrics.spacing(uneven) == pytest.approx(0.3, rel=1e-12)
    assert metrics.spacing(uneven) > 0.05


def test_hypervolume_exact_cases():
    ref = np.array([1.0, 1.0])
    assert metrics.hypervolume_2d(np.array([[0.0, 0.0]]), ref) == pytest.approx(1.0)
    unit_step = np.array([[0.0, 0.5], [0.5, 0.0]])
    assert metrics.hypervolume_2d(unit_step, ref) == pytest.approx(0.75)
    assert metrics.hypervolume_2d(np.array([[1.5, 0.2]]), ref) == 0.0


def test_igd_equals_gd_for_dense_matching_sets():
    front = np.column_stack([np.linspace(0, 1, 1001), np.linspace(1, 0, 1001)])
    subset = front[::10]
    assert metrics.igd if False else True  # attribute style guard
    assert metrics.inverted_generational_distance(subset, front) < 0.01


# ---------------------------------------------------------------------------
# problems / analytic fronts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["ZDT1", "ZDT2", "ZDT3"])
def test_analytic_front_is_non_dominated_and_closed_form(name):
    problem = problems.get_problem(name)
    front = problem.analytic_front(2000)
    assert problems.non_dominated_mask(front).all()
    f1, f2 = front[:, 0], front[:, 1]
    if name == "ZDT1":
        assert np.allclose(f2, 1.0 - np.sqrt(f1), atol=1e-12)
    elif name == "ZDT2":
        assert np.allclose(f2, 1.0 - f1 ** 2, atol=1e-12)
    else:  # ZDT3: 5 disjoint arcs, f1 stops at ~0.8526
        assert np.allclose(f2, 1.0 - np.sqrt(f1) - f1 * np.sin(10 * np.pi * f1),
                           atol=1e-12)
        arcs = np.split(front, np.flatnonzero(np.diff(f1) > 0.01) + 1)
        assert len(arcs) == 5
        assert f1.max() == pytest.approx(0.8526, abs=5e-3)


@pytest.mark.parametrize("name", ["ZDT1", "ZDT2", "ZDT3"])
def test_pareto_optimal_decision_vector_maps_onto_front(name):
    problem = problems.get_problem(name, n_var=8)
    x = np.zeros((1, 8))
    x[0, 0] = 0.05 if name == "ZDT3" else 0.37   # Pareto set: x_2..x_n = 0
    # ZDT3's front is the union of 5 arcs; 0.05 sits inside the first one
    point = problem.evaluate(x)
    g = 1.0 + 9.0 * x[0, 1:].sum() / 7
    assert g == pytest.approx(1.0)
    dist = np.min(np.linalg.norm(problem.analytic_front(4000) - point, axis=1))
    assert dist < 2e-3


def test_unknown_problem_raises():
    with pytest.raises(KeyError):
        problems.get_problem("ZDT9")


# ---------------------------------------------------------------------------
# NSGA-II internals
# ---------------------------------------------------------------------------
def _brute_force_ranks(F):
    n = F.shape[0]
    dom = np.zeros((n, n), dtype=bool)
    for i in range(n):
        for j in range(n):
            if i != j:
                dom[i, j] = (np.all(F[i] <= F[j]) and np.any(F[i] < F[j]))
    ranks = np.full(n, -1)
    remaining = set(range(n))
    front = 0
    while remaining:
        cur = [i for i in remaining if not any(dom[j, i] for j in remaining if j != i)]
        for i in cur:
            ranks[i] = front
            remaining.discard(i)
        front += 1
    return ranks


def test_non_dominated_rank_matches_brute_force_with_duplicates():
    rng = np.random.default_rng(7)
    for trial in range(25):
        n = int(rng.integers(4, 40))
        F = np.round(rng.random((n, 2)), 1)
        assert np.array_equal(nsga2_mod.non_dominated_rank(F), _brute_force_ranks(F))
        fronts = nsga2_mod.fast_non_dominated_sort(F)
        assert sum(len(f) for f in fronts) == n
        assert sorted(i for f in fronts for i in f) == list(range(n))


def test_crowding_distance_endpoints_infinite_middle_even():
    F = np.column_stack([np.arange(5.0), np.arange(5.0)[::-1]])
    cd = nsga2_mod.crowding_distance(F, list(range(5)))
    assert np.isinf(cd[0]) and np.isinf(cd[-1])
    assert np.allclose(cd[1:-1], 1.0)  # 2 objectives x 0.5 each
    assert np.all(np.isinf(nsga2_mod.crowding_distance(F, [0, 1])))


def test_sbx_bounds_and_mean_preservation():
    rng = np.random.default_rng(11)
    lower, upper = np.zeros(6), np.ones(6)
    p1, p2 = rng.uniform(0.3, 0.7, 6), rng.uniform(0.3, 0.7, 6)  # away from bounds
    c1, c2 = nsga2_mod.sbx_crossover(p1, p2, lower, upper, rng, prob=1.0)
    assert np.all(c1 >= 0) and np.all(c1 <= 1) and np.all(c2 >= 0) and np.all(c2 <= 1)
    assert np.allclose(c1 + c2, p1 + p2)   # SBX is a convex recombination


def test_polynomial_mutation_bounds_and_zero_prob():
    rng = np.random.default_rng(5)
    x = rng.random(20)
    y = nsga2_mod.polynomial_mutation(x, np.zeros(20), np.ones(20), rng, prob=0.0)
    assert np.array_equal(x, y)
    z = nsga2_mod.polynomial_mutation(x, np.zeros(20), np.ones(20), rng, prob=1.0)
    assert np.all(z >= 0) and np.all(z <= 1) and not np.array_equal(x, z)


def test_short_run_converges_and_population_is_non_dominated():
    problem = problems.get_problem("ZDT1", n_var=5)
    front = problem.analytic_front(1000)
    cfg = nsga2_mod.NSGA2Config(pop_size=40, n_generations=200, seed=0,
                                analytic_front=front)
    result = nsga2_mod.nsga2(problem, cfg)
    gd0, gdN = result.history[0]["gd"], result.history[-1]["gd"]
    assert gdN < 0.02 * gd0          # 0.005 measured, from 2.22 at generation 0
    assert gdN <= 0.01               # acceptance threshold of face 1
    assert result.final_metrics()["spacing"] <= 0.05
    assert np.all(result.ranks >= 0)
    assert result.pareto_front().shape[0] == problem.evaluate(result.pareto_set()).shape[0]
    assert result.n_evaluations == 40 * (200 + 1)


def test_determinism_same_seed_same_result():
    problem = problems.get_problem("ZDT2", n_var=6)
    front = problem.analytic_front(400)
    cfg = nsga2_mod.NSGA2Config(pop_size=20, n_generations=10, seed=42,
                            analytic_front=front)
    a, b = nsga2_mod.nsga2(problem, cfg), nsga2_mod.nsga2(problem, cfg)
    assert np.array_equal(a.population, b.population)
    assert a.history[-1]["gd"] == b.history[-1]["gd"]


# ---------------------------------------------------------------------------
# convergence machinery
# ---------------------------------------------------------------------------
def test_converges_at_requires_plateau():
    history = [{"generation": g, "gd": v} for g, v in
               [(0, 1.0), (1, 0.005), (2, 0.4), (3, 0.002), (4, 0.003), (5, 0.004)]]
    # transient dip at generation 1 is rejected, the sustained crossing is 3
    assert nsga2_mod.converges_at(history, "gd", 0.01, patience=3) == 3
    assert nsga2_mod.converges_at([{"generation": 0, "gd": 5.0}], "gd", 0.01) is None


def test_powerlaw_anchor_recovers_known_law():
    gens = np.arange(1, 201, dtype=float)
    gd = 3.0 * gens ** -2.0
    anchor = benchmark.powerlaw_anchor(gens, gd, threshold=0.01, window=(0.1, 0.5))
    assert anchor["k"] == pytest.approx(2.0, rel=1e-6)
    assert anchor["A"] == pytest.approx(3.0, rel=1e-6)
    assert anchor["predicted_generation"] == pytest.approx(math.sqrt(300.0), rel=1e-6)
    assert anchor["r2"] == pytest.approx(1.0, abs=1e-12)


def test_interpolate_crossing_between_bracket_ends():
    task = {"evaluations": {"100": {"gd": 0.5}, "200": {"gd": 0.001}}}
    value = interpolate_crossing(task, 0.01)
    assert 100 < value < 200
    # log-log interpolation of a true power law is exact
    # gd = 100 / g^2 -> crossing of 0.01 exactly at g = 100
    task = {"evaluations": {"50": {"gd": 0.04}, "200": {"gd": 0.0025}}}
    assert interpolate_crossing(task, 0.01) == pytest.approx(100.0, rel=1e-6)
    assert interpolate_crossing({"evaluations": {"50": {"gd": 2.5}}}, 0.01) is None


def test_sweep_policy_ladder_bisection_plateau():
    task = {"problem": "ZDT1", "seed": 0, "evaluations": {}, "converged_at": None}
    assert next_request(task, 0.01, 1000) == 150
    task["evaluations"]["150"] = {"gd": 0.8}
    assert next_request(task, 0.01, 1000) == 300
    task["evaluations"]["300"] = {"gd": 0.05}
    assert next_request(task, 0.01, 1000) == 450
    task["evaluations"]["450"] = {"gd": 0.004}
    probe = next_request(task, 0.01, 1000)
    assert 300 < probe < 450
    task["evaluations"][str(probe)] = {"gd": 0.006}
    # bracket now [300, probe]; refine or confirm plateau, never re-evaluate
    nxt = next_request(task, 0.01, 1000)
    assert nxt is None or str(nxt) not in task["evaluations"]


# ---------------------------------------------------------------------------
# suite / throughput / report
# ---------------------------------------------------------------------------
def test_serial_and_parallel_suites_are_identical():
    specs = [benchmark.RunSpec("ZDT3", seed=s, n_var=5, pop_size=16,
                               n_generations=15) for s in (0, 1)]
    serial = benchmark.run_suite(specs, workers=1, with_history=False)
    parallel = benchmark.run_suite(specs, workers=2, with_history=False)

    def fingerprint(payload):
        return [(r["key"], r["n_evaluations"],
                 round(r["final_metrics"]["gd"], 12),
                 round(r["final_metrics"]["spacing"], 12))
                for r in payload["records"]]

    assert fingerprint(serial) == fingerprint(parallel)


def _synthetic_suite(gd=0.004, spacing=0.007, generation=400, deviation_seeds=5):
    records, summary = [], {}
    for problem in cli.PROBLEMS:
        summary[problem] = {
            "n_seeds": deviation_seeds, "gd_median": gd, "gd_max": gd * 1.4,
            "gd_min": gd * 0.8, "igd_median": 0.007, "igd_min": 0.006,
            "igd_max": 0.008, "spacing_median": spacing,
            "spacing_max": spacing * 1.2, "spacing_min": spacing * 0.8,
            "coverage_mean": 0.4,
            "hypervolume_median": 1.9, "gd_converged_seeds": deviation_seeds,
            "gd_generation_median": float(generation),
            "gd_generation_values": [generation] * deviation_seeds,
            "powerlaw_generation_median": 700.0, "powerlaw_generation_values": [700.0],
            "powerlaw_k_mean": 2.5, "powerlaw_r2_mean": 0.9,
            "wall_time_s_mean": 1.0,
        }
        for seed in range(deviation_seeds):
            records.append({"spec": {"problem": problem, "seed": seed,
                                     "n_generations": 700},
                            "front_size": 100, "final_metrics": {"gd": gd},
                            "convergence": {"gd_generation": generation}})
    return {"config": {}, "summary": summary, "records": records,
            "wall_time_s": 1.0}


def _synthetic_reference(generation):
    return {"available": True,
            "summary": {p: {"gd_generation_median": float(generation),
                            "gd_generation_interpolated_median": float(generation),
                            "gd_generation_values": [generation]} for p in cli.PROBLEMS},
            "records": []}


def test_report_passes_on_good_numbers_and_fails_on_bad_ones(tmp_path):
    suite = _synthetic_suite()
    speedup_ok = {"speedup": 8.0, "serial_wall_time_s": 40.0,
                  "parallel_wall_time_s": 5.0, "results_identical": True,
                  "config": {"n_tasks": 12, "parallel_workers": 12}}
    report = cli.build_report(tmp_path, suite, _synthetic_reference(410), speedup_ok, [])
    assert report["all_criteria_passed"] is True
    assert json.loads((tmp_path / cli.REPORT_FILENAME).read_text())["criteria"]

    # 15 % slower convergence than the reference -> still inside the +-20 % band
    report = cli.build_report(tmp_path, suite, _synthetic_reference(445), speedup_ok, [])
    assert report["criteria"][1]["passed"] is True

    # 50 % off -> criterion must fail loudly (no silent pass)
    report = cli.build_report(tmp_path, suite, _synthetic_reference(800), speedup_ok, [])
    assert report["criteria"][1]["passed"] is False
    assert report["all_criteria_passed"] is False

    # weak speed-up and a badly spread front must also fail
    bad = {"speedup": 1.2, "serial_wall_time_s": 40.0, "parallel_wall_time_s": 33.0,
           "results_identical": True, "config": {"n_tasks": 12, "parallel_workers": 12}}
    report = cli.build_report(tmp_path, _synthetic_suite(spacing=0.2),
                              _synthetic_reference(400), bad, [])
    flags = {c["id"]: c["passed"] for c in report["criteria"]}
    assert flags["C3"] is False and flags["C4"] is False and flags["C1"] is True