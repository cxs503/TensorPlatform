"""Global clock manager: exact rational derivation, plans, audits, contracts."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from src.multifidelity.clock_manager import (
    ClockContractViolation,
    GlobalClockManager,
    Strategy,
    as_fraction,
    rational_gcd,
    rational_lcm,
)

DTS = {"fast": 1 / 960, "mid": 1 / 480, "slow": 1 / 60}


def _manager(strategy=Strategy.GCD):
    manager = GlobalClockManager(strategy)
    for name, dt in DTS.items():
        manager.register(name, dt)
    return manager


def test_rational_gcd_lcm_are_exact():
    assert rational_gcd(Fraction(1, 2), Fraction(1, 3)) == Fraction(1, 6)
    assert rational_lcm(Fraction(1, 2), Fraction(1, 3)) == Fraction(1, 1)
    assert rational_lcm(Fraction(2, 3), Fraction(4, 9)) == Fraction(4, 3)
    assert rational_gcd(Fraction(2, 3), Fraction(4, 9)) == Fraction(2, 9)


def test_float_steps_are_rationalised_not_rounded():
    frac = as_fraction(1 / 3)
    assert frac == Fraction(1, 3)
    assert as_fraction(0.1) == Fraction(1, 10)
    with pytest.raises(ClockContractViolation):
        as_fraction(0.0)
    with pytest.raises(ClockContractViolation):
        as_fraction(float("nan"))


def test_gcd_grid_is_the_finest_common_grid():
    grid = _manager(Strategy.GCD).build(0.5)
    assert grid.dt_global == Fraction(1, 960)
    assert grid.ratios == {"fast": Fraction(1), "mid": Fraction(2), "slow": Fraction(16)}
    assert grid.n_steps == 480
    # every solver's own step is an integer number of global steps
    for name, ratio in grid.ratios.items():
        assert ratio.denominator == 1
        assert grid.global_steps_per_own_step(name) == int(ratio)


def test_lcm_grid_is_the_macro_step():
    grid = _manager(Strategy.LCM).build(0.5)
    assert grid.dt_global == Fraction(1, 60)
    assert grid.ratios == {"fast": Fraction(1, 16), "mid": Fraction(1, 8), "slow": Fraction(1)}
    for name, ratio in grid.ratios.items():
        assert (1 / ratio).denominator == 1
        assert grid.solver_steps_per_global(name) == int(1 / ratio)
    assert grid.n_steps == 30


def test_grid_nodes_are_exact_multiples():
    for strategy in (Strategy.GCD, Strategy.LCM):
        grid = _manager(strategy).build(0.5)
        nodes = grid.grid()
        for k in (0, 1, 7, grid.n_steps):
            assert abs(nodes[k] - float(k * grid.dt_global)) <= 1e-15
        assert nodes[-1] <= 0.5 + 1e-12


def test_publication_nodes_are_monotone_and_bounded():
    grid = _manager(Strategy.GCD).build(0.5)
    nodes = grid.publication_nodes("slow")
    assert nodes[0] == 0 and nodes[-1] <= grid.n_steps
    assert len(nodes) == grid.n_steps // 16 + 1
    for name in DTS:
        assert grid.publication_nodes(name) == grid.consumer_windows(name)


def test_skew_audit_separates_float_drift_from_the_rounding_shortcut():
    audit = _manager(Strategy.GCD).skew_audit(_manager(Strategy.GCD).build(0.5))
    assert audit["dt_global_exact"] == "1/960"
    assert audit["max_skew_float_accumulation"] < 1e-12      # round-off only
    assert all(audit["ratio_integrality"].values())
    # quantising to 1 ms destroys the common instant: it is not a small perturbation
    assert audit["integer_rounding_shortcut_rel_error"] > 0.5


def test_nesting_contract():
    gcd_grid = _manager(Strategy.GCD).build(0.5)
    assert gcd_grid.nesting_ok("fast", "slow")
    assert not gcd_grid.nesting_ok("slow", "fast")     # slow frames cannot sit on a fast lattice


def test_incommensurate_clocks_are_rejected_by_both_strategies():
    rejection = {}
    for strategy in (Strategy.GCD, Strategy.LCM):
        manager = GlobalClockManager(strategy)
        manager.register("a", 1 / 3)
        manager.register("b", np.sqrt(2) / 7)
        with pytest.raises(ClockContractViolation):
            manager.build(1.0)
        rejection[strategy.value] = True
    assert all(rejection.values())


def test_negative_no_registration_is_an_error_not_an_empty_grid():
    """Negative control: an unregistered manager must refuse, not silently co-simulate."""
    manager = GlobalClockManager(Strategy.GCD)
    with pytest.raises(ClockContractViolation):
        manager.dt_global()
    with pytest.raises(ClockContractViolation):
        manager.build(1.0)
    manager.register("only", 1 / 100)
    assert manager.build(0.1).dt_global == Fraction(1, 100)
    manager.unregister("only")
    with pytest.raises(ClockContractViolation):
        manager.build(0.1)