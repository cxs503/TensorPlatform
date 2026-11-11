"""Coupled-loop contracts and the verdicts they produce (P350-W2-N09, part 2).

A :class:`~tools.coupled_loop.Certificate` records what the iteration did; the
contract states what it had to do.  These tests pin down the fail-closed
validation of ``contracts/coupled_loop.schema.json`` (including the two semantic
cross-checks the schema cannot express — unique disciplines and a coupling graph
that only references declared blocks) and the gate that combines a certificate
with an optional sensitivity record into one verdict.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.coupled_loop import DIVERGED, Certificate, CONVERGED
from tools.coupling.loop_contract import (
    COUPLED_LOOP_SCHEMA_VERSION,
    LoopContractError,
    contract_gate,
    gate_certificate,
    gate_sensitivity,
    load_coupled_loop_contract,
    validate_coupled_loop_contract,
)
from tools.coupling.sensitivity import linear_two_discipline_system, sensitivity_consistency_gate

ROOT = Path(__file__).resolve().parents[1]
PLAIN = ROOT / "contracts" / "coupled_loop.example.v1.json"
AITKEN = ROOT / "contracts" / "coupled_loop.example.aitken.v1.json"
SCHEMA = ROOT / "contracts" / "coupled_loop.schema.json"


def _plain() -> dict:
    return json.loads(PLAIN.read_text(encoding="utf-8"))


def _certificate(**overrides) -> Certificate:
    fields = dict(
        converged=True,
        status=CONVERGED,
        rho_hat=0.5,
        iterations=41,
        final_residual=1e-12,
        residual_series=(1.0, 0.5, 1e-12),
        scheme="jacobi",
        relaxation="none",
        omega=1.0,
        reason="residual below tolerance",
    )
    fields.update(overrides)
    return Certificate(**fields)


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


def test_schema_and_examples_are_present_and_json():
    assert SCHEMA.exists() and PLAIN.exists() and AITKEN.exists()
    for path in (SCHEMA, PLAIN, AITKEN):
        json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["title"] == "Coupled Loop Contract v1"
    assert set(schema["required"]) >= {"disciplines", "coupling_vars", "scheme", "tolerance", "rho_max"}


def test_plain_example_declares_the_expected_loop():
    contract = load_coupled_loop_contract(PLAIN)
    assert contract["schema_version"] == COUPLED_LOOP_SCHEMA_VERSION
    assert contract["scheme"] == "jacobi"
    assert contract["relaxation"] == "none"
    assert [d["name"] for d in contract["disciplines"]] == ["blockA", "blockB"]
    assert validate_coupled_loop_contract(contract) == []


def test_aitken_example_requires_an_iteration_saving():
    contract = load_coupled_loop_contract(AITKEN)
    assert contract["relaxation"] == "aitken"
    assert contract["convergence_gate"]["min_iteration_reduction"] >= 0.4
    assert validate_coupled_loop_contract(contract) == []


@pytest.mark.parametrize("key", ["loop_id", "disciplines", "coupling_vars", "scheme", "rho_max"])
def test_missing_required_key_is_rejected(key):
    contract = _plain()
    del contract[key]
    errors = validate_coupled_loop_contract(contract)
    assert any(key in message for message in errors), errors


def test_unknown_key_is_rejected():
    contract = _plain()
    contract["something_else"] = 1
    assert any("unknown key" in e for e in validate_coupled_loop_contract(contract))


def test_wrong_schema_version_is_rejected():
    contract = _plain()
    contract["schema_version"] = "coupled-loop.v0"
    assert any("schema_version" in e for e in validate_coupled_loop_contract(contract))


def test_unknown_scheme_and_relaxation_are_rejected():
    contract = _plain()
    contract["scheme"] = "conjugate_gradient"
    contract["relaxation"] = "anderson"
    errors = validate_coupled_loop_contract(contract)
    assert any("scheme" in e for e in errors)
    assert any("relaxation" in e for e in errors)


def test_dangling_coupling_var_is_a_design_error_not_a_typo():
    contract = _plain()
    contract["coupling_vars"][0]["producer"] = "blockZ"
    errors = validate_coupled_loop_contract(contract)
    assert any("blockZ" in e for e in errors), errors


def test_isolated_discipline_is_rejected():
    contract = _plain()
    contract["coupling_vars"] = [{"name": "a_to_b", "producer": "blockA", "consumer": "blockB"}]
    errors = validate_coupled_loop_contract(contract)
    assert any("never produce" in e for e in errors), errors


def test_duplicate_discipline_names_are_rejected():
    contract = _plain()
    contract["disciplines"][1]["name"] = contract["disciplines"][0]["name"]
    assert any("unique" in e for e in validate_coupled_loop_contract(contract))


def test_self_coupling_is_rejected():
    contract = _plain()
    contract["coupling_vars"][0]["consumer"] = contract["coupling_vars"][0]["producer"]
    errors = validate_coupled_loop_contract(contract)
    assert any("must differ" in e for e in errors), errors


def test_negative_controls_must_be_declared_failing():
    contract = _plain()
    contract["negative_controls"][0]["must_fail"] = False
    assert any("must_fail" in e for e in validate_coupled_loop_contract(contract))


def test_load_raises_on_an_invalid_document(tmp_path):
    bad = _plain()
    bad["tolerance"] = -1.0
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(LoopContractError):
        load_coupled_loop_contract(path)


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------


def test_gate_certificate_accepts_a_converged_certificate():
    contract = load_coupled_loop_contract(PLAIN)
    verdict = gate_certificate(contract, _certificate(), spectral_radius=0.5)
    assert verdict["passed"], verdict["checks"]
    assert verdict["rho_deviation"] <= 0.05


def test_gate_certificate_rejects_a_divergent_certificate():
    contract = load_coupled_loop_contract(PLAIN)
    verdict = gate_certificate(
        contract, _certificate(converged=False, status=DIVERGED, rho_hat=1.2, final_residual=1e-1)
    )
    assert verdict["passed"] is False
    assert verdict["checks"]["converged"] is False
    assert verdict["checks"]["rho_below_max"] is False


def test_gate_certificate_rejects_a_scheme_mismatch():
    contract = load_coupled_loop_contract(PLAIN)
    verdict = gate_certificate(contract, _certificate(scheme="gauss_seidel"))
    assert verdict["passed"] is False
    assert verdict["checks"]["scheme_matches_contract"] is False


def test_gate_certificate_rejects_a_contraction_that_misses_the_spectral_radius():
    contract = load_coupled_loop_contract(PLAIN)
    verdict = gate_certificate(contract, _certificate(rho_hat=0.75), spectral_radius=0.5)
    assert verdict["passed"] is False
    assert verdict["checks"]["rho_matches_spectral_radius"] is False


def test_aitken_contract_requires_the_declared_iteration_saving():
    contract = load_coupled_loop_contract(AITKEN)
    good = gate_certificate(contract, _certificate(relaxation="aitken", iterations=16), unrelaxed_iterations=41)
    bad = gate_certificate(contract, _certificate(relaxation="aitken", iterations=40), unrelaxed_iterations=41)
    assert good["passed"], good["checks"]
    assert bad["passed"] is False
    assert bad["checks"]["relaxation_saves_iterations"] is False


def test_gate_sensitivity_enforces_the_contract_tolerance():
    contract = load_coupled_loop_contract(PLAIN)
    inside = gate_sensitivity(contract, {"passed": True, "relative_error": 0.01})
    outside = gate_sensitivity(contract, {"passed": True, "relative_error": 0.03})
    assert inside["passed"] is True
    assert outside["passed"] is False
    assert outside["declared_rtol"] == 0.02


def test_contract_gate_combines_loop_and_sensitivity_verdicts():
    contract = load_coupled_loop_contract(PLAIN)
    fixture = linear_two_discipline_system(0.5)
    system = fixture.to_system()
    _, certificate = system.solve([1.0])
    record = sensitivity_consistency_gate(system, [1.0], analytic=fixture.analytic_sensitivity)
    verdict = contract_gate(contract, certificate, sensitivity=record, spectral_radius=0.5)
    assert verdict["passed"], verdict["parts"]
    assert set(verdict["parts"]) == {"loop", "sensitivity"}
    assert verdict["parts"]["sensitivity"]["parameter"] == "load_scale"


def test_contract_gate_fails_when_only_the_sensitivity_part_fails():
    contract = load_coupled_loop_contract(PLAIN)
    fixture = linear_two_discipline_system(0.5)
    _, certificate = fixture.to_system().solve([1.0])
    verdict = contract_gate(
        contract, certificate, sensitivity={"passed": False, "relative_error": 0.5}, spectral_radius=0.5
    )
    assert verdict["passed"] is False
    assert verdict["parts"]["loop"]["passed"] is True


def test_gate_refuses_an_invalid_contract_instead_of_defaulting():
    contract = copy.deepcopy(_plain())
    contract["rho_max"] = 0.0
    with pytest.raises(LoopContractError):
        gate_certificate(contract, _certificate())