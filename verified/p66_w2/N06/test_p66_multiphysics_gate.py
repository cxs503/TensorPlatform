"""Tests for the P66-W2-N06 multi-physics coupling integration gate.

The suite is written against behaviour, not against numbers copied from a report:
conservation identities, contract rejection, parameter-space overlap, scenario
outcomes, and the gate verdict are all recomputed here.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from tensorphysics.campaign.p66_gate import conservation as K
from tensorphysics.campaign.p66_gate import contracts as C
from tensorphysics.campaign.p66_gate import gate as G
from tensorphysics.campaign.p66_gate import parameter_space as PS
from tensorphysics.campaign.p66_gate import perf as PF
from tensorphysics.campaign.p66_gate import producers as P
from tensorphysics.campaign.p66_gate import scenarios as S
from tensorphysics.ice.resistance import DomainLimits, IceDomainError

SHIP = P.reference_ship()
BELT = P.belt_geometry(SHIP)
COARSE = K.InterfaceMesh(BELT.edges(), BELT.height_m)
FINE = K.graded_interface_mesh(0.0, BELT.length_m, 4 * BELT.n_patches, BELT.height_m)
SAMPLES = P.parameter_sweep(G.H_SWEEP, G.V_SWEEP)


@pytest.fixture(scope="module")
def chain():
    return P.produce_chain(SHIP, SAMPLES, belt=BELT)


# ---------------------------------------------------------------------------
# units and dimensional algebra
# ---------------------------------------------------------------------------


def test_unit_parser_rejects_non_si_scale():
    ok, detail = C.unit_ok("m", "m")
    assert ok, detail
    ok, detail = C.unit_ok("mm", "m")
    assert not ok and "scale" in detail
    ok, detail = C.unit_ok("kn", "m/s")
    assert not ok and "scale" in detail


def test_unit_parser_handles_parenthesised_and_powers():
    dims, scale = C.parse_unit("kg/(m^2*s)")
    assert dims == C.parse_unit("kg/m^2/s")[0]
    assert scale == pytest.approx(1.0)
    assert C.parse_unit("N")[0] == C.parse_unit("kg*m/s^2")[0]


def test_dimensional_identity_wear_law():
    units = {"k_w": "-", "p_ice_Pa": "Pa", "v_slip_ms": "m/s", "H_hull_Pa": "Pa", "dh_dt_ms": "m/s"}
    check = C.check_identity(C.DIMENSIONAL_IDENTITIES["wear_rate_law"], units)
    assert check.ok, check.detail
    broken = dict(units, H_hull_Pa="N")  # wrong dimension for the hardness
    assert not C.check_identity(C.DIMENSIONAL_IDENTITIES["wear_rate_law"], broken).ok


# ---------------------------------------------------------------------------
# interface contracts
# ---------------------------------------------------------------------------


def test_module_contracts_pass(chain):
    n03, n04, n05 = chain
    report = C.validate_cross_module(n03, n04, n05)
    failed = [check.check_id + ": " + check.detail for check in report.checks if not check.ok]
    assert report.ok, failed
    assert report.n_fail == 0
    assert report.n_checks > 40


def test_contract_rejects_millimetre_payload(chain):
    n03, n04, n05 = chain
    corrupted = json.loads(json.dumps(n03))
    corrupted["units"]["h_m"] = "mm"
    checks = C.validate_payload(C.CONTRACT_N03, corrupted)
    assert any(not check.ok for check in checks if check.check_id.endswith("unit:h_m"))


def test_contract_rejects_missing_field(chain):
    n03, n04, n05 = chain
    corrupted = json.loads(json.dumps(n04))
    for sample in corrupted["samples"]:
        sample.pop("R_crush_N")
    checks = C.validate_payload(C.CONTRACT_N04, corrupted)
    assert any(not check.ok for check in checks)


def test_parameter_key_consistency_detects_mismatch(chain):
    n03, n04, n05 = chain
    assert C.check_parameter_consistency(n03, n04, n05).ok
    truncated = json.loads(json.dumps(n04))
    truncated["samples"][0]["h_m"] = truncated["samples"][0]["h_m"] + 1e-3
    assert not C.check_parameter_consistency(n03, truncated, n05).ok


# ---------------------------------------------------------------------------
# interface conservation
# ---------------------------------------------------------------------------


def test_overlap_closure_holds_on_non_matching_meshes():
    ok, error = K.coverage_closure(COARSE, FINE)
    assert ok and error < 1e-12
    # the meshes are genuinely non-matching: no patch edge coincides with a facet edge
    nested = all(bool(np.any(np.isclose(FINE.edges_m, edge))) for edge in COARSE.edges_m)
    assert not nested


def test_conservative_projection_preserves_the_integral():
    values = np.linspace(1.0, 3.0, FINE.n) ** 2
    projected = K.conservative_project(COARSE, FINE, values)
    sent = float(np.sum(values * FINE.widths))
    received = float(np.sum(projected * COARSE.widths))
    assert abs(received - sent) / abs(sent) < 1e-14


def test_conservation_residuals_below_tolerance(chain):
    n03 = chain[0]
    plan = K.ExchangePlan(COARSE, FINE)
    reports = plan.all(n03)
    assert max(r.mass_residual for r in reports) < K.CONSERVATION_TOL
    assert max(r.momentum_residual for r in reports) < K.CONSERVATION_TOL
    assert max(r.third_law_residual for r in reports) < K.CONSERVATION_TOL


def test_negative_controls_exceed_the_floor(chain):
    n03 = chain[0]
    report = K.exchange_conservation(n03, 0, coarse=COARSE, fine=FINE)
    assert report.discriminating
    for law, value in report.control_summary.items():
        assert value > K.NEGATIVE_CONTROL_FLOOR, law
    # and the production residual is not accidentally large: the gap must be real
    assert report.control_summary["reaction"] > 10 * report.third_law_residual


def test_exchange_payload_contract(chain):
    n03 = chain[0]
    plan = K.ExchangePlan(COARSE, FINE)
    payloads = plan.build_payloads(n03)
    checks = K.validate_exchange_batch(payloads)
    assert all(check.ok for check in checks), [c.detail for c in checks if not c.ok]
    assert checks[0].detail.startswith("schema conforming")
    # a scale defect in the hand-off must be caught
    broken = json.loads(json.dumps(payloads[0]))
    broken["units"]["mass_kg_m2s"] = "g/(m^2*s)"
    assert any(not check.ok for check in K.validate_exchange_payload(broken))


def test_quadrature_order_is_first_order_or_better(chain):
    n03 = chain[0]
    order = K.quadrature_order(n03, 0, n_patches=COARSE.n)
    assert order is not None and 1.0 <= order <= 3.0


# ---------------------------------------------------------------------------
# parameter space
# ---------------------------------------------------------------------------


def test_parameter_space_overlap_nominal(chain):
    n03, n04, n05 = chain
    report = PS.parameter_space_consistency(n03, n04, ship=SHIP, limits=DomainLimits(), belt=BELT)
    assert report.fraction >= PS.OVERLAP_THRESHOLD
    assert report.ok
    assert 0.0 < report.envelope["jaccard"] <= 1.0


def test_parameter_space_negative_control_fails():
    control_samples = P.parameter_sweep(G.H_SWEEP_CONTROL, G.V_SWEEP)
    n03, n04, _ = P.produce_chain(SHIP, control_samples, belt=BELT)
    report = PS.parameter_space_consistency(n03, n04, ship=SHIP, limits=DomainLimits(), belt=BELT)
    assert report.fraction < PS.OVERLAP_THRESHOLD
    assert not report.ok


def test_out_of_domain_sample_is_flagged_not_extrapolated():
    n03, n04, _ = P.produce_chain(SHIP, [(3.0, 2.0)], belt=BELT)
    assert n04["samples"][0]["in_domain"] is False
    assert "OUTSIDE" in n04["samples"][0]["domain_note"]
    with pytest.raises(IceDomainError):
        P.produce_n04(SHIP, [(3.0, 2.0)], strict=True)


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------


def test_scenario_table_shape_and_outcomes():
    results = S.run_all_scenarios(ship=SHIP, belt=BELT)
    kinds = [result.kind for result in results]
    assert kinds.count("nominal") >= 5
    assert kinds.count("refusal") == 1
    assert kinds.count("truncation") == 1
    for result in results:
        if result.kind == "nominal":
            assert result.status == "PASS", (result.scenario_id, result.notes)
        elif result.kind == "refusal":
            assert result.status == "PASS(expected-refusal)"
        else:
            assert result.status == "FLAGGED"
        assert result.numbers or result.kind == "refusal"


def test_scenario_numbers_are_physical():
    results = {result.scenario_id: result for result in S.run_all_scenarios(ship=SHIP, belt=BELT)}
    s1 = results["S1"].numbers
    assert s1["R_total_N"] > s1["R_ice_N"] > 0.0
    assert 0.0 < s1["R_ow_fraction"] < 0.1          # open-water is a small part in ice
    assert s1["p_ice_peak_Pa"] >= s1["p_ice_mean_Pa"] > 0.0
    assert s1["tangential_force_N"] == pytest.approx(0.15 * s1["normal_force_N"], rel=1e-12)
    # heavier ice must cost more resistance
    assert results["S3"].numbers["R_total_N"] > results["S2"].numbers["R_total_N"]


# ---------------------------------------------------------------------------
# gate verdict
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def verdict():
    return G.run_gate(repeats=3)


def test_gate_verdict_passes_all_criteria(verdict):
    statuses = {criterion["criterion_id"]: criterion["status"] for criterion in verdict["criteria"]}
    assert statuses == {"1": "PASS", "2": "PASS", "3": "PASS", "4": "PASS", "5": "PASS"}
    assert verdict["overall_status"] == "PASS"
    assert verdict["blocking"] == []


def test_gate_verdict_is_json_serialisable(verdict):
    payload = json.dumps(verdict)
    restored = json.loads(payload)
    assert restored["schema"] == G.GATE_SCHEMA
    assert restored["verdict_digest"] == verdict["verdict_digest"]
    assert len(restored["criteria"]) == 5
    for criterion in restored["criteria"]:
        assert criterion["detail"]
        assert "evidence" in criterion


def test_gate_records_upstream_status(verdict):
    upstream = verdict["inputs"]["upstream_status"]
    assert upstream[C.CONTRACT_N04]["in_main"] is True
    assert upstream[C.CONTRACT_N03]["in_main"] is False
    assert "REFERENCE-PRODUCER" in upstream[C.CONTRACT_N05]["status"]


def test_producers_are_deterministic():
    a = P.produce_n03(SHIP, [(1.0, 2.0)], belt=BELT)
    b = P.produce_n03(SHIP, [(1.0, 2.0)], belt=BELT)
    assert C.payload_digest(a) == C.payload_digest(b)


def test_performance_benchmark_fields():
    bench = PF.benchmark(SHIP, SAMPLES[:10], belt=BELT, repeats=2)
    assert bench.ratio > 0.0
    assert bench.ratio_compute_only >= bench.ratio
    payload = bench.as_dict()
    assert payload["baseline_definition"].startswith("per-module compute")
    assert "t_coupled_s" in payload