"""Contract tests: configuration validation, payload validation, drift classifier.

These cases are cheap and independent of the numeric chain, so a regression in
the *contracts* (a silently accepted bad threshold, an unjudged metric, a
mis-classified drift) surfaces without running a generator.
"""

from __future__ import annotations


import pytest

import helpers
from tools.evidence_pipeline import PipelineError
from tools.evidence_pipeline.config import BenchmarkConfig, ROOT, load_config
from tools.evidence_pipeline.execution import validate_payload
from tools.evidence_pipeline.gate import run_gates, summarise
from tools.evidence_pipeline.regression import compare, decide

GEN = "tools/evidence_pipeline/generators/syn_alpha_quadrature.py"


def base_config(**overrides):
    data = {
        "node": "syn-alpha-quadrature",
        "generator": GEN,
        "params": {},
        "gates": [{"id": "g1", "type": "numeric", "thresholds": {"p_order": {"min": 1.9}}}],
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"gates": [{"id": "g", "type": "magnitude", "thresholds": {"a": {"min": 1}}}]}, "unknown type"),
        ({"gates": [{"id": "g", "type": "numeric", "thresholds": {"a": {"min": 1, "max": 0}}}]}, "min > max"),
        ({"gates": [{"id": "g", "type": "numeric", "thresholds": {"a": {}}}]}, "neither min nor max"),
        ({"gates": [{"id": "g", "type": "numeric", "thresholds": {"a": {"max": "x"}}}]}, "must be a number"),
        ({"gates": [{"id": "g", "type": "numeric", "thresholds": {}}]}, "non-empty object"),
        ({"gates": []}, "non-empty array"),
        ({"gates": [{"id": "g", "type": "conservation",
                     "thresholds": {"sum_rel_residual": 1e-12}}]}, "must declare every metric"),
        ({"gates": [{"id": "g", "type": "conservation",
                     "thresholds": {"sum_rel_residual": 1e-12, "moment_rel_residual": 1e-12,
                                    "nonneg_violation": 1e-12, "overshoot_ratio": 1.0,
                                    "bogus": 1.0}}]}, "unknown conservation threshold"),
        ({"gates": [{"id": "g", "type": "conservation",
                     "thresholds": {"sum_rel_residual": 1e-12, "moment_rel_residual": 1e-12,
                                    "nonneg_violation": 1e-12, "overshoot_ratio": 0.99}}]},
         "must be >= 1.0"),
        ({"gates": [{"id": "g", "type": "numeric", "thresholds": {"a": {"min": 1}}},
                    {"id": "g", "type": "numeric", "thresholds": {"a": {"min": 1}}}]}, "duplicate gate ids"),
        ({"generator": "tools/evidence_pipeline/generators/nope.py"}, "generator not found"),
        ({"node": ""}, "non-empty string"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"regression": {"tol": -1.0}}, "regression.tol"),
        ({"regression": {"reruns": -1}}, "regression.reruns"),
        ({"params": []}, "'params' must be an object"),
    ],
)
def test_invalid_configs_are_rejected(mutation, message) -> None:
    data = base_config(**mutation)
    with pytest.raises(PipelineError) as exc:
        BenchmarkConfig.parse(data, source="<test>")
    assert message in str(exc.value)


def test_config_digest_is_canonical() -> None:
    a = BenchmarkConfig.parse(base_config(), "<a>")
    b = BenchmarkConfig.parse(base_config(params={"z": 1.0, "y": 2.0}), "<b>")
    c = BenchmarkConfig.parse(base_config(params={"y": 2.0, "z": 1.0}), "<c>")
    assert a.digest() != b.digest()
    # numerically equal JSON with different key order / int-vs-float digests equal
    assert b.digest() == c.digest()
    # threshold changes must move the digest (an artifact cannot silently outlive its config)
    d = BenchmarkConfig.parse(
        base_config(gates=[{"id": "g1", "type": "numeric", "thresholds": {"p_order": {"min": 1.0}}}]),
        "<d>",
    )
    assert d.digest() != a.digest()


def test_payload_validation_reports_every_violation() -> None:
    cfg = load_config(helpers.BENCHMARKS / "syn-beta-exchange.json")
    bad = {
        "schema": "wrong",
        "node": "other-node",
        "metrics": {"sum_rel_residual": float("nan"), "flag": True},
        "fields": {"src": {"mesh": {}, "values": []}, "dst": "not-an-object"},
    }
    errors = validate_payload(bad, cfg)
    joined = " | ".join(errors)
    assert "payload.schema" in joined
    assert "payload.node" in joined
    assert "not finite" in joined
    assert "is not a number" in joined
    assert "needs 'lo' and 'hi'" in joined
    assert "must be an object" in joined
    assert validate_payload({"schema": "x"}, cfg) != []


def test_numeric_gate_missing_metric_fails_closed() -> None:
    cfg = BenchmarkConfig.parse(base_config(), "<t>")
    outcomes = run_gates(cfg, {"metrics": {}})
    assert outcomes[0].passed is False
    assert "missing from payload" in outcomes[0].failures[0]


def test_conservation_gate_without_fields_fails_closed() -> None:
    gate = {
        "id": "cons",
        "type": "conservation",
        "thresholds": {
            "sum_rel_residual": 1e-12,
            "moment_rel_residual": 1e-10,
            "nonneg_violation": 1e-12,
            "overshoot_ratio": 1.0,
        },
    }
    cfg = BenchmarkConfig.parse(
        base_config(gates=[gate], node="syn-beta-exchange",
                    generator="tools/evidence_pipeline/generators/syn_beta_exchange.py"),
        "<t>",
    )
    outcome = run_gates(cfg, {"metrics": {}})[0]
    assert outcome.passed is False
    assert "field_unavailable" in outcome.failures[0]
    assert summarise([outcome])["passed"] is False

    # a dimension mismatch is a gate failure, not an exception
    bad_fields = {
        "fields": {
            "src": {"mesh": {"lo": [[0.0, 0.0]], "hi": [[1.0, 1.0]]}, "values": [1.0]},
            "dst": {"mesh": {"lo": [[0.0]], "hi": [[1.0]]}, "values": [1.0]},
        }
    }
    outcome = run_gates(cfg, bad_fields)[0]
    assert outcome.passed is False
    assert "dimension mismatch" in outcome.failures[0]


def test_regression_compare_floors_protect_near_zero_metrics() -> None:
    comparison = compare(
        {"residual": 2.0e-16, "level": 1.0},
        {"residual": 0.0, "level": 1.0},
        tol=1e-6,
        floors={"residual": 1e-9},
    )
    assert comparison["within_tolerance"] is True
    assert comparison["per_metric"]["residual"]["rel_drift"] == pytest.approx(2e-7)

    missing = compare({"a": 1.0}, {}, tol=1e-6)
    assert missing["within_tolerance"] is False
    assert missing["per_metric"]["a"]["rel_drift"] == float("inf")


def test_drift_classification_table() -> None:
    drifted = compare({"x": 1.001}, {"x": 1.0}, tol=1e-6)
    assert drifted["within_tolerance"] is False

    stable = decide(compare({"x": 1.0}, {"x": 1.0}, 1e-6), [{"payload_digest": "a"}], True)
    assert stable["classification"] == "stable" and stable["passed"] is True

    no_base = decide(compare({"x": 1.0}, {}, 1e-6), [{"payload_digest": "a"}], False)
    assert no_base["classification"] == "no_baseline" and no_base["passed"] is True

    unverified = decide(drifted, [{"payload_digest": "a"}], True)
    assert unverified["classification"] == "drift_unverified" and unverified["passed"] is False

    reproducible = decide(
        drifted, [{"payload_digest": "a"}, {"payload_digest": "a"}], True
    )
    assert reproducible["classification"] == "drift_reproducible"
    assert reproducible["passed"] is False

    nondeterministic = decide(
        drifted, [{"payload_digest": "a"}, {"payload_digest": "b"}], True
    )
    assert nondeterministic["classification"] == "drift_nondeterministic"
    assert nondeterministic["passed"] is False


def test_package_contains_no_domain_vocabulary() -> None:
    """Platform iron rule: the pipeline layer carries no domain terms."""
    from tools.evidence_domain_scan import scan_paths

    result = scan_paths(
        [
            "tools/evidence_pipeline",
            "config/evidence_pipeline",
        ]
    )
    assert result["scanned_files"] >= 10
    assert result["hits"] == [], result["hits"]