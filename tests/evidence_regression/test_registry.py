"""Registry layout + anti-degradation invariants (negative controls included)."""
from __future__ import annotations

import copy

import pytest

from tools.evidence_regression.registry import ProbeRegistry, RegistryError

from conftest import synthetic_registry_payload


def test_registry_loads_and_reports_counts(registry):
    summary = registry.summary()
    assert summary["capability_entries"] == 5
    assert summary["probe_specs"] == 20
    assert summary["entries_without_variation_probe"] == []
    assert summary["probe_kinds"]["determinism"] == 5


def test_duplicate_capability_id_rejected(write_registry):
    payload = synthetic_registry_payload()
    payload["entries"].append(copy.deepcopy(payload["entries"][0]))
    with pytest.raises(RegistryError, match="duplicate capability_id"):
        ProbeRegistry.from_dict(payload)


def test_entry_without_variation_sensitive_probe_rejected():
    payload = {
        "schema_version": "evidence_probe_registry.v1",
        "entries": [{
            "capability_id": "cap.x",
            "sys_path": ["tests/evidence_regression"],
            "target": {"steps": [{"ref": "synthetic_lab:constant", "kwargs": {"x": 1.0}}], "field": None},
            "probes": [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 1.0}, "expect": 42.0, "tol_rel": 1e-12}]},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 1.0}}]},
            ],
        }],
    }
    with pytest.raises(RegistryError, match="variation-sensitive"):
        ProbeRegistry.from_dict(payload)


def test_entry_without_determinism_probe_rejected():
    payload = {
        "schema_version": "evidence_probe_registry.v1",
        "entries": [{
            "capability_id": "cap.y",
            "sys_path": ["tests/evidence_regression"],
            "target": {"steps": [{"ref": "synthetic_lab:linear", "kwargs": {"x": 1.0}}], "field": None},
            "probes": [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 1.0}, "expect": 2.0, "tol_rel": 1e-12}]},
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 2.0}}],
                 "min_rel_spread": 0.2},
            ],
        }],
    }
    with pytest.raises(RegistryError, match="determinism"):
        ProbeRegistry.from_dict(payload)


def test_entry_without_expectation_probe_rejected():
    payload = {
        "schema_version": "evidence_probe_registry.v1",
        "entries": [{
            "capability_id": "cap.z",
            "sys_path": ["tests/evidence_regression"],
            "target": {"steps": [{"ref": "synthetic_lab:linear", "kwargs": {"x": 1.0}}], "field": None},
            "probes": [
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 2.0}}],
                 "min_rel_spread": 0.2},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 1.0}}]},
            ],
        }],
    }
    with pytest.raises(RegistryError, match="expectation-bearing"):
        ProbeRegistry.from_dict(payload)


def test_unknown_probe_kind_rejected():
    payload = {
        "schema_version": "evidence_probe_registry.v1",
        "entries": [{
            "capability_id": "cap.k",
            "target": {"steps": [{"ref": "synthetic_lab:linear", "kwargs": {"x": 1.0}}], "field": None},
            "probes": [{"kind": "vibes", "cases": []}],
        }],
    }
    with pytest.raises(ValueError, match="unknown probe kind"):
        ProbeRegistry.from_dict(payload)


def test_denied_module_reference_rejected():
    payload = {
        "schema_version": "evidence_probe_registry.v1",
        "entries": [{
            "capability_id": "cap.denied",
            "target": {"steps": [{"ref": "os:system", "kwargs": {"x": 1.0}}], "field": None},
            "probes": [{"kind": "anchor", "cases": [{"kwargs": {}, "expect": 0.0}]},
                       {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 2.0}}]},
                       {"kind": "determinism", "cases": [{"kwargs": {"x": 1.0}}]}],
        }],
    }
    with pytest.raises(RegistryError, match="denied module"):
        ProbeRegistry.from_dict(payload)


def test_call_chain_and_field_selection(chained_registry):
    report = chained_registry.run("cap.chain")
    assert report.passed, report.to_dict()
    anchor = [c for c in report.checks if c.kind == "anchor"][0]
    assert anchor.observed[0]["observed"] == pytest.approx(7.0)


def test_per_probe_field_override(registry):
    """cap.delta declares no default field; each probe selects its own scalar."""
    report = registry.run("cap.delta")
    assert report.passed, report.to_dict()
    anchors = [c for c in report.checks if c.kind == "anchor"]
    assert len(anchors) == 2
    assert [a.observed[0]["observed"] for a in anchors] == [6.0, 5.0]