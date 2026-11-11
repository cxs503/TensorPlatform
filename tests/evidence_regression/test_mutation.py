"""Drill: every MOCK family must be caught by every registered entry."""
from __future__ import annotations

import pytest

from tools.evidence_regression import mutation
from tools.evidence_regression.targets import MOCK_FAMILIES


def test_drill_detects_every_injection(registry):
    result = mutation.drill(registry, injections=20)
    assert result["injections"] == 20
    assert result["detected"] == 20
    assert result["detection_rate"] == 1.0
    assert result["families_used"] == sorted(MOCK_FAMILIES)


def test_full_matrix_has_no_miss(registry):
    result = mutation.drill_matrix(registry)
    expected = len(registry.entries) * len(MOCK_FAMILIES)
    assert result["cases"] == expected
    assert result["detected"] == expected
    assert result["misses"] == []


@pytest.mark.parametrize("family", MOCK_FAMILIES)
def test_every_family_is_detected_for_every_entry(registry, family):
    for cid in registry.registered_capabilities():
        injection = mutation.inject_and_probe(registry, cid, family)
        assert injection.detected, f"{family} escaped {cid}"
        assert injection.failed_kinds


def test_expectations_are_collected_for_echo_family(registry):
    values = mutation.expectations_of(registry.entries["cap.alpha"])
    assert 4.0 in values


def test_echo_family_is_caught_by_variation_probes(registry):
    """A stub that returns the *expected* values must still be caught (determinism/variation)."""
    injection = mutation.inject_and_probe(registry, "cap.gamma", "expected_echo")
    assert injection.detected
    assert any(kind in ("determinism", "nontriviality", "scale_law", "bounded") for kind in injection.failed_kinds)