"""Shared fixtures: a synthetic probe registry (no domain semantics anywhere)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from tools.evidence_regression.registry import ProbeRegistry  # noqa: E402

SYS_PATH = ["tests/evidence_regression"]


def entry(capability_id: str, ref: str, kwargs: dict, probes: list, field=None) -> dict:
    return {
        "capability_id": capability_id,
        "sys_path": list(SYS_PATH),
        "target": {"steps": [{"ref": ref, "kwargs": kwargs}], "field": field},
        "probes": probes,
    }


def synthetic_registry_payload() -> dict:
    """Five entries covering every probe kind and every invariant."""
    return {
        "schema_version": "evidence_probe_registry.v1",
        "title": "synthetic probe registry (tests)",
        "entries": [
            entry("cap.alpha", "synthetic_lab:linear", {"x": 2.0, "k": 2.0}, [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 2.0, "k": 2.0}, "expect": 4.0, "tol_rel": 1e-12}],
                 "label": "analytic identity 2*x"},
                {"kind": "scale_law", "base": {"x": 2.0, "k": 2.0}, "axis": "x", "factor": 3.0, "exponent": 1.0,
                 "tol_rel": 1e-12},
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 2.0}}, {"kwargs": {"x": 5.0}}],
                 "min_rel_spread": 0.2},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 2.0}}]},
            ]),
            entry("cap.beta", "synthetic_lab:quadratic", {"x": 3.0, "a": 1.0}, [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 3.0, "a": 1.0}, "expect": 9.0, "tol_rel": 1e-12}],
                 "label": "analytic identity a*x^2"},
                {"kind": "monotonic", "base": {"a": 1.0}, "axis": "x", "values": [1.0, 2.0, 4.0],
                 "direction": "increasing", "tol_rel": 1e-12},
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 4.0}}],
                 "min_rel_spread": 0.5},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 3.0}}]},
            ]),
            entry("cap.gamma", "synthetic_lab:banded", {"x": 0.5}, [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 0.0}, "expect": 0.5, "tol_rel": 1e-12}],
                 "label": "analytic identity 0.5 + 0.1*x"},
                {"kind": "bounded", "cases": [{"kwargs": {"x": 0.0}}, {"kwargs": {"x": 1.0}}], "lo": 0.0, "hi": 1.0},
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 0.0}}, {"kwargs": {"x": 1.0}}],
                 "min_rel_spread": 0.05},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 0.5}}]},
            ]),
            entry("cap.delta", "synthetic_lab:vector", {"x": 3.0, "y": 2.0}, [
                {"kind": "anchor", "field": "product", "cases": [{"kwargs": {"x": 3.0, "y": 2.0}, "expect": 6.0,
                                                                  "tol_rel": 1e-12}],
                 "label": "analytic identity x*y"},
                {"kind": "anchor", "field": "scaled.1", "cases": [{"kwargs": {"x": 3.0, "y": 2.0}, "expect": 5.0,
                                                                    "tol_rel": 1e-12}],
                 "label": "sequence index selection"},
                {"kind": "nontriviality", "field": "product",
                 "cases": [{"kwargs": {"x": 1.0, "y": 1.0}}, {"kwargs": {"x": 4.0, "y": 4.0}}],
                 "min_rel_spread": 0.5},
                {"kind": "determinism", "field": "product", "cases": [{"kwargs": {"x": 3.0, "y": 2.0}}]},
            ]),
            entry("cap.epsilon", "synthetic_lab:chain_stage", {"x": 2.0, "factor": 3.0}, [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 2.0, "factor": 3.0}, "expect": 6.0, "tol_rel": 1e-12}],
                 "label": "analytic identity x*factor"},
                {"kind": "scale_law", "base": {"x": 2.0, "factor": 3.0}, "axis": "x", "factor": 2.0,
                 "exponent": 1.0, "tol_rel": 1e-12},
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 4.0}}],
                 "min_rel_spread": 0.5},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 2.0}}]},
            ]),
        ],
    }


def chained_registry_payload() -> dict:
    return {
        "schema_version": "evidence_probe_registry.v1",
        "entries": [{
            "capability_id": "cap.chain",
            "sys_path": list(SYS_PATH),
            "target": {
                "steps": [
                    {"ref": "synthetic_lab:chain_stage", "kwargs": {"x": 2.0, "factor": 3.0}},
                    {"ref": "synthetic_lab:chain_final", "kwargs": {"offset": 1.0}},
                ],
                "field": None,
            },
            "probes": [
                {"kind": "anchor", "cases": [{"kwargs": {"x": 2.0, "factor": 3.0}, "expect": 7.0, "tol_rel": 1e-12}],
                 "label": "analytic identity x*factor + offset"},
                {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 4.0}}],
                 "min_rel_spread": 0.2},
                {"kind": "determinism", "cases": [{"kwargs": {"x": 2.0}}]},
            ],
        }],
    }


def acceptance_registry_payload() -> dict:
    """Ten synthetic entries (C1 threshold) built from one analytic family: f(x)=k*x."""
    kinds = ("cap.unit.01", "cap.unit.02", "cap.unit.03", "cap.unit.04", "cap.unit.05",
             "cap.unit.06", "cap.unit.07", "cap.unit.08", "cap.unit.09", "cap.unit.10")
    entries = []
    for index, cid in enumerate(kinds, start=1):
        k = float(index)
        entries.append(entry(cid, "synthetic_lab:linear", {"x": 2.0, "k": k}, [
            {"kind": "anchor", "cases": [{"kwargs": {"x": 2.0, "k": k}, "expect": 2.0 * k, "tol_rel": 1e-12}],
             "label": f"analytic identity f(x)=k*x with k={k:g}"},
            {"kind": "scale_law", "base": {"x": 2.0, "k": k}, "axis": "x", "factor": 4.0, "exponent": 1.0,
             "tol_rel": 1e-12},
            {"kind": "nontriviality", "cases": [{"kwargs": {"x": 1.0, "k": k}}, {"kwargs": {"x": 5.0, "k": k}}],
             "min_rel_spread": 0.5},
            {"kind": "determinism", "cases": [{"kwargs": {"x": 2.0, "k": k}}]},
        ]))
    return {"schema_version": "evidence_probe_registry.v1",
            "title": "synthetic acceptance registry (tests)", "entries": entries}


def acceptance_ledger_payload() -> dict:
    return {"schema_version": "synthetic",
            "capabilities": [{"id": f"cap.unit.{i:02d}", "claimed_level": "REAL", "domain": "synthetic"}
                             for i in range(1, 11)]}


@pytest.fixture()
def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture()
def registry(workspace_root) -> ProbeRegistry:
    return ProbeRegistry.from_dict(synthetic_registry_payload(), workspace_root=workspace_root)


@pytest.fixture()
def chained_registry(workspace_root) -> ProbeRegistry:
    return ProbeRegistry.from_dict(chained_registry_payload(), workspace_root=workspace_root)


@pytest.fixture()
def claims(registry) -> dict:
    return {cid: "REAL" for cid in registry.registered_capabilities()}


@pytest.fixture()
def write_registry(tmp_path):
    def _write(payload: dict) -> Path:
        path = tmp_path / "probes.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path
    return _write