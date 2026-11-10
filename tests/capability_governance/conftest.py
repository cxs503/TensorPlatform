"""Fixtures for the capability-governance suite.

The package is imported as ``services.capability_governance`` (backend on sys.path,
same convention as ``tests/xsolver``).  Every fixture here is synthetic and
domain-neutral: the mechanism must be testable without any domain checkout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

PROBE_SET = REPO / "config" / "capability_governance" / "probe_set.json"
SAMPLE_SPEC = REPO / "config" / "capability_governance" / "sample_spec.json"


@pytest.fixture(scope="session")
def frozen_specs():
    return json.loads(PROBE_SET.read_text(encoding="utf-8"))["endpoints"]


@pytest.fixture()
def small_frozen_specs(frozen_specs):
    """A fast, still-balanced slice of the frozen set: 4 positives + 8 negatives."""
    positives = [s for s in frozen_specs if s["expected_level"] == "REAL"][:4]
    negatives = [s for s in frozen_specs if s["expected_level"] != "REAL"][:8]
    return positives + negatives


@pytest.fixture()
def test_report(tmp_path):
    path = tmp_path / "junit.json"
    path.write_text(json.dumps({"passed": True, "n": 1}), encoding="utf-8")
    return path


@pytest.fixture()
def mini_sample_spec():
    """2 x 2 axes, 2 conditions, 5 replicates -> 40 rows."""
    return {
        "schema": "sample-corpus-spec/1.0",
        "source": "test_corpus",
        "axes": {"family": ["af", "bf"], "case_class": ["ck", "dk"],
                 "class_factor": {"ck": 1.0, "dk": 1.1}},
        "condition_grid": [
            {"forcing": 0.3, "duration": 4.0, "initial_rate": 1.0},
            {"forcing": 0.6, "duration": 4.0, "initial_rate": 0.5},
        ],
        "replicates": 5,
        "scale_range": [0.8, 1.2],
        "model": {"kind": "ivp_rk4",
                  "family_params": {"af": {"omega": 1.0, "zeta": 0.05},
                                    "bf": {"omega": 1.3, "zeta": 0.08}}},
    }