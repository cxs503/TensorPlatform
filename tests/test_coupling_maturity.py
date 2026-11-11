"""The aggregated maturity verdict for the coupled-loop node (P350-W2-N09, criterion g).

One record, seven criteria, every threshold next to its number.  These tests keep
the aggregate honest: they re-read the numbers the audit produced and assert the
acceptance thresholds independently of the audit's own ``passed`` flags, and they
check that every negative control inside the record really is a *failing* record
(so a gate that silently stopped discriminating would show up here).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.coupling import maturity_check
from tools.coupling.maturity_check import (
    AITKEN_REDUCTION_GATE,
    CONSERVATION_TOLERANCE,
    DIVERGENCE_ITERATION_CAP,
    LINEAR_ORDER_GATE,
    MIN_TEST_COUNT,
    QUADRATIC_ORDER_GATE,
    SENSITIVITY_RTOL,
    SPECTRAL_DEVIATION_GATE,
    count_tests,
    run_maturity_audit,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def audit() -> dict:
    return run_maturity_audit(ROOT)


# ---------------------------------------------------------------------------
# the aggregate
# ---------------------------------------------------------------------------


def test_audit_covers_all_seven_criteria(audit):
    assert sorted(audit["criteria"]) == ["a", "b", "c", "d", "e", "f", "g"]
    assert audit["summary"]["n_total"] == 7


def test_every_criterion_passes(audit):
    failures = {
        cid: record["checks"] for cid, record in audit["criteria"].items() if not record["passed"]
    }
    assert failures == {}, failures
    assert audit["summary"]["all_passed"] is True


def test_audit_is_deterministic(audit):
    second = run_maturity_audit(ROOT)
    assert json.dumps(second["summary"], sort_keys=True) == json.dumps(audit["summary"], sort_keys=True)
    for cid in ("a", "b", "c", "d", "f"):
        assert json.dumps(second["criteria"][cid]["numbers"], sort_keys=True) == json.dumps(
            audit["criteria"][cid]["numbers"], sort_keys=True
        )


# ---------------------------------------------------------------------------
# (a) conservation residual, (b) convergence order
# ---------------------------------------------------------------------------


def test_criterion_a_numbers_sit_below_one_e_minus_four(audit):
    numbers = audit["criteria"]["a"]["numbers"]
    assert numbers["residual_l2"] <= CONSERVATION_TOLERANCE
    assert numbers["analytic_error_left"] <= CONSERVATION_TOLERANCE
    assert numbers["analytic_error_right"] <= CONSERVATION_TOLERANCE


def test_criterion_a_negative_control_really_breaks_the_gate(audit):
    numbers = audit["criteria"]["a"]["numbers"]
    assert numbers["jittered_residual"] > CONSERVATION_TOLERANCE
    assert audit["criteria"]["a"]["checks"]["jittered_flux_breaks_gate"] is True


def test_criterion_b_orders_clear_their_gates(audit):
    numbers = audit["criteria"]["b"]["numbers"]
    assert numbers["linear_reconstruction_order"] >= LINEAR_ORDER_GATE
    assert numbers["quadratic_projection_order"] >= QUADRATIC_ORDER_GATE
    assert numbers["quadratic_projection_order"] > numbers["linear_reconstruction_order"]


def test_criterion_b_noise_control_cannot_show_an_order(audit):
    numbers = audit["criteria"]["b"]["numbers"]
    assert numbers["noisy_operator_order"] < QUADRATIC_ORDER_GATE


# ---------------------------------------------------------------------------
# (c) analytic anchor, (d) divergence, (e) Aitken
# ---------------------------------------------------------------------------


def test_criterion_c_contraction_matches_the_spectral_radius(audit):
    numbers = audit["criteria"]["c"]["numbers"]
    assert numbers["rho_hat"] == pytest.approx(0.5, rel=SPECTRAL_DEVIATION_GATE)
    assert numbers["rho_deviation"] <= SPECTRAL_DEVIATION_GATE
    assert numbers["solution_error"] <= 1e-9


def test_criterion_d_divergence_is_flagged_early_and_not_as_a_cap(audit):
    numbers = audit["criteria"]["d"]["numbers"]
    assert numbers["status"] == "DIVERGED"
    assert numbers["iterations"] <= DIVERGENCE_ITERATION_CAP
    assert audit["criteria"]["d"]["checks"]["not_confused_with_iteration_cap"] is True


def test_criterion_e_aitken_saving_is_measured_not_asserted(audit):
    numbers = audit["criteria"]["e"]["numbers"]
    assert numbers["iteration_reduction"] >= AITKEN_REDUCTION_GATE
    assert numbers["iterations_aitken"] < numbers["iterations_plain"]
    assert numbers["fixed_point_gap"] <= 1e-9


# ---------------------------------------------------------------------------
# (f) sensitivity consistency and its falsifiability
# ---------------------------------------------------------------------------


def test_criterion_f_composed_matches_both_references(audit):
    numbers = audit["criteria"]["f"]["numbers"]
    assert numbers["relative_error"] <= SENSITIVITY_RTOL
    assert numbers["relative_error_composed_vs_analytic"] <= SENSITIVITY_RTOL
    assert numbers["resolved"] == pytest.approx(numbers["analytic"], rel=1e-5)


def test_criterion_f_corrupted_partials_must_fail(audit):
    numbers = audit["criteria"]["f"]["numbers"]
    assert numbers["corrupted_coupling_passed"] is False
    assert numbers["corrupted_param_passed"] is False
    assert numbers["corrupted_coupling_relative_error"] > SENSITIVITY_RTOL
    assert numbers["corrupted_param_relative_error"] > SENSITIVITY_RTOL


# ---------------------------------------------------------------------------
# (g) coverage and neutrality
# ---------------------------------------------------------------------------


def test_criterion_g_counts_the_new_tests(audit):
    counts = count_tests(ROOT)
    assert sum(counts.values()) >= MIN_TEST_COUNT
    assert audit["criteria"]["g"]["numbers"]["tests_total"] >= MIN_TEST_COUNT


def test_criterion_g_neutrality_scan_is_clean(audit):
    numbers = audit["criteria"]["g"]["numbers"]
    assert numbers["neutrality_hits"] == []
    assert numbers["neutrality_scanned_files"] >= 6


# ---------------------------------------------------------------------------
# the artefact
# ---------------------------------------------------------------------------


def test_cli_writes_a_parseable_artefact(tmp_path):
    out = tmp_path / "nested" / "maturity.json"
    exit_code = maturity_check.main(["--out", str(out)])
    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["node"] == "P350W2-N09"
    assert payload["summary"]["all_passed"] is True
    assert payload["inputs"]["schema"] == "contracts/coupled_loop.schema.json"


def test_cli_exit_code_is_nonzero_when_a_criterion_regresses(tmp_path, monkeypatch):
    """The artefact is a gate, not a report: a regressed criterion must return 1."""

    def broken() -> dict:
        record = run_maturity_audit(ROOT)
        record["criteria"]["d"]["passed"] = False
        record["summary"] = {
            "passed": ["a"],
            "failed": ["d"],
            "all_passed": False,
            "n_passed": 1,
            "n_total": 7,
        }
        return record

    monkeypatch.setattr(maturity_check, "run_maturity_audit", broken)
    out = tmp_path / "regressed.json"
    assert maturity_check.main(["--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["all_passed"] is False