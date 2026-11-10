"""Energy ledger acceptance tests (AC2).

The claim: saving and restoring a state does not perturb the gated invariant
(residual exactly 0 at the 1e-10 acceptance level) and a 1000-step restart does
not drift it by more than 0.1 %.  The negative control shows the same ledger is
able to *see* a change, which is what makes the zeros meaningful.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.multisolver_ckpt import (
    ACCEPT_REL_DRIFT,
    ACCEPT_REL_RESIDUAL,
    CouplingTimeGrid,
    EnergyLedger,
    EnergyReading,
    build_harness,
    quadratic_functional,
)
from tools.multisolver_ckpt.energy import EnergyLedgerError
from multisolver_ckpt_helpers import grid, make_store  # noqa: F401  (fixture re-export)


@pytest.mark.parametrize("level", (1, 2, 3))
def test_boundary_residual_is_zero(tmp_path, grid, level):
    harness, ledger = build_harness(level)
    store = make_store(tmp_path / "store", ledger, grid)
    run = harness.run(20, ledger=ledger, store=store, checkpoint_every=20)
    saved = store.last_save
    restored = ledger.measure(store.load(20).bundle)
    gate = ledger.check_boundary(saved.energy, restored)
    assert gate.ok, gate.detail
    assert gate.primary_residual == 0.0
    assert gate.rel_tol == ACCEPT_REL_RESIDUAL


@pytest.mark.parametrize("level", (1, 2, 3))
def test_thousand_step_drift_below_threshold(tmp_path, grid, level):
    harness, ledger = build_harness(level)
    store = make_store(tmp_path / "store", ledger, grid)
    run = harness.run(1000, ledger=ledger)
    gate = ledger.check_drift(run.energy_readings[0], run.energy_readings[-1])
    assert gate.ok, gate.detail
    assert gate.primary_residual < ACCEPT_REL_DRIFT
    assert gate.rel_tol == ACCEPT_REL_DRIFT


def test_negative_control_is_detected(grid):
    harness, ledger = build_harness(3, damped=True)
    run = harness.run(1000, ledger=ledger)
    gate = ledger.check_drift(run.energy_readings[0], run.energy_readings[-1])
    assert not gate.ok, "the ledger failed to notice a dissipative configuration"
    assert gate.primary_residual > ACCEPT_REL_DRIFT


def test_conserved_scenario_beats_damped_control(grid):
    good, good_ledger = build_harness(3)
    bad, bad_ledger = build_harness(3, damped=True)
    good_run = good.run(600, ledger=good_ledger)
    bad_run = bad.run(600, ledger=bad_ledger)
    good_drift = good_ledger.check_drift(good_run.energy_readings[0],
                                        good_run.energy_readings[-1]).primary_residual
    bad_drift = bad_ledger.check_drift(bad_run.energy_readings[0],
                                      bad_run.energy_readings[-1]).primary_residual
    assert good_drift < 1e-12
    assert bad_drift > 1e3 * max(good_drift, 1e-18)


def test_sub_energies_move_while_total_is_gated(grid):
    """Coupling exchanges transfer energy between solvers; only E_total is gated."""

    harness, ledger = build_harness(3)
    run = harness.run(500, ledger=ledger)
    first, last = run.energy_readings[0], run.energy_readings[-1]
    sub_movement = max(
        abs(last.values[k] - first.values[k]) / abs(first.values[k])
        for k in ledger.names if k != "E_total"
    )
    assert sub_movement > 1e-4, "no energy was exchanged between solvers"
    assert ledger.check_drift(first, last).ok
    assert ledger.gate == ("E_total",)


def test_reading_manifest_roundtrip_is_exact():
    reading = EnergyReading(step=7, values={"E_total": 1.2345678901234567,
                                            "E_other": -0.0}, primary="E_total")
    again = EnergyReading.from_manifest(reading.to_manifest())
    assert again.step == reading.step
    assert again.primary == reading.primary
    assert again.values["E_total"] == reading.values["E_total"]
    assert math.copysign(1.0, again.values["E_other"]) == math.copysign(1.0, reading.values["E_other"])


def test_duplicate_registration_is_rejected():
    ledger = EnergyLedger({"E_total": quadratic_functional([("s", "a", 1.0)])})
    with pytest.raises(EnergyLedgerError):
        ledger.register("E_total", quadratic_functional([("s", "a", 1.0)]))
    ledger.register("E_total", quadratic_functional([("s", "a", 1.0)]), replace=True)


def test_unknown_primary_is_rejected():
    with pytest.raises(EnergyLedgerError):
        EnergyLedger({"E_total": quadratic_functional([("s", "a", 1.0)])}, primary="E_missing")
    with pytest.raises(EnergyLedgerError):
        EnergyLedger({})


def test_missing_array_in_functional_raises():
    harness, _ = build_harness(1)
    ledger = EnergyLedger({"E_total": quadratic_functional([("kinetic", "nope", 1.0)])})
    with pytest.raises(EnergyLedgerError):
        ledger.measure(harness.snapshot())


def test_non_finite_functional_is_rejected():
    harness, _ = build_harness(1)

    def bad(_bundle):
        return float("nan")

    ledger = EnergyLedger({"E_total": bad})
    with pytest.raises(EnergyLedgerError):
        ledger.measure(harness.snapshot())


def test_boundary_gate_rejects_a_perturbed_reading():
    harness, ledger = build_harness(2)
    reading = ledger.measure(harness.snapshot())
    perturbed = EnergyReading(step=reading.step,
                              values={k: v * (1.0 + 1e-6) for k, v in reading.values.items()},
                              primary=reading.primary)
    gate = ledger.check_boundary(reading, perturbed)
    assert not gate.ok
    assert gate.residuals["E_total"] == pytest.approx(1e-6, rel=1e-9)


def test_absolute_functional_is_a_diagnostic_not_a_gate():
    from tools.multisolver_ckpt import absolute_functional

    harness, _ = build_harness(2)
    ledger = EnergyLedger({
        "E_total": quadratic_functional([("kinetic", "f", 1.0), ("nodal", "u", 1.0),
                                         ("nodal", "v", 1.0)]),
        "mass": absolute_functional([("kinetic", "f", 1.0)]),
    }, gate=("E_total",))
    reading = ledger.measure(harness.snapshot())
    assert set(reading.values) == {"E_total", "mass"}
    assert ledger.gate == ("E_total",)
    assert ledger.check_boundary(reading, ledger.measure(harness.snapshot())).ok