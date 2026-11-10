"""Registry tests: declarative case injection, fail-closed validation, discovery.

Every fixture is synthetic — the registry must never need a physical domain to be
exercised (acceptance criterion A1/A4).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.verification_harness.model import SCHEMA_VERSION, ReferenceKind, Tolerance
from tools.verification_harness.registry import (
    CallableResolutionError,
    CaseRegistry,
    RegistryError,
    case_spec_from_mapping,
    resolve_callable,
)

REPO = Path(__file__).resolve().parents[1]
CASE_DIR = REPO / "tools" / "verification_harness" / "cases"


def _case(**overrides):
    base = {
        "case_id": "synth-fixture-case",
        "domain": "synthetic-fixture",
        "implementation": "tools.verification_harness.anchors.synthetic:slab_closed_form",
        "reference": "tools.verification_harness.anchors.synthetic:slab_closed_form",
        "inputs": {"conductivity": 1.0, "source": 0.0, "length": 1.0,
                   "t_left": 0.0, "t_right": 1.0, "nodes": 5},
        "quantities": {"t_mid": {"rtol": 1e-9}},
    }
    base.update(overrides)
    return base


def test_register_and_access_by_id():
    registry = CaseRegistry()
    registry.register(_case(case_id="zeta"))
    registry.register(_case(case_id="alpha"))
    assert len(registry) == 2
    assert registry.case_ids == ("alpha", "zeta")          # deterministic order
    assert "alpha" in registry
    assert registry.get("alpha").case_id == "alpha"
    assert registry.domains() == {"synthetic-fixture": 2}


def test_duplicate_case_id_is_rejected():
    registry = CaseRegistry()
    registry.register(_case())
    with pytest.raises(RegistryError) as excinfo:
        registry.register(_case())
    assert "duplicate case_id" in str(excinfo.value)


@pytest.mark.parametrize("key", ["implementation", "reference", "quantities", "case_id"])
def test_missing_required_key_is_rejected(key):
    payload = _case()
    payload.pop(key)
    with pytest.raises(RegistryError) as excinfo:
        case_spec_from_mapping(payload)
    assert "missing required keys" in str(excinfo.value)


def test_unknown_key_is_rejected_fail_closed():
    with pytest.raises(RegistryError) as excinfo:
        case_spec_from_mapping(_case(kcs_draft=14.5))
    assert "unknown keys" in str(excinfo.value)


@pytest.mark.parametrize("bad", [{"rtol": -1e-3}, {"rtol": 1.5}, {"rtol": 0.1, "atol": -2.0}])
def test_illegal_tolerance_is_rejected(bad):
    with pytest.raises(RegistryError):
        case_spec_from_mapping(_case(quantities={"t_mid": bad}))


def test_tolerance_parsing_number_shorthand():
    spec = case_spec_from_mapping(_case(quantities={"t_mid": 1e-6}))
    assert spec.tolerance_for("t_mid") == Tolerance(rtol=1e-6, atol=0.0)


def test_bad_reference_kind_is_rejected():
    with pytest.raises(RegistryError):
        case_spec_from_mapping(_case(reference_kind="oracle"))


def test_discover_loads_every_declared_case_file():
    registry = CaseRegistry()
    registered = registry.discover(CASE_DIR)
    files = sorted(CASE_DIR.glob("*.json"))
    assert len(files) >= 5                                     # A1: >= 5 cases
    assert len(registered) == len(registry) >= 5
    assert all(spec.reference_kind in tuple(ReferenceKind) for spec in registry)
    assert len(set(registry.case_ids)) == len(registry)


def test_domain_labels_are_injected_not_enumerated():
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    domains = registry.domains()
    assert len(domains) >= 3, "the anchors must span several synthetic domains"
    assert all(name == "" or name for name in domains)
    # the registry treats the label as opaque: registering an unseen label works
    registry.register(_case(case_id="opaque-domain", domain="never-seen-before"))
    assert "never-seen-before" in registry.domains()


def test_registry_payload_snapshot_roundtrips():
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    snapshot = registry.to_dict()
    assert snapshot["schema_version"] == SCHEMA_VERSION
    rebuilt = CaseRegistry()
    rebuilt.register_payload(json.loads(json.dumps(snapshot)))
    assert rebuilt.case_ids == registry.case_ids


def test_resolve_callable_accepts_both_notations():
    a = resolve_callable("tools.verification_harness.anchors.synthetic:slab_closed_form")
    b = resolve_callable("tools.verification_harness.anchors.synthetic.slab_closed_form")
    assert a is b and callable(a)


@pytest.mark.parametrize("dotted", [
    "",
    "tools.verification_harness.anchors.synthetic:no_such_attribute",
    "no_such_package_xyz:thing",
    "tools.verification_harness.model:SCHEMA_VERSION",
])
def test_resolve_callable_rejects_bad_paths(dotted):
    with pytest.raises(CallableResolutionError):
        resolve_callable(dotted)


def test_missing_case_directory_is_reported(tmp_path):
    with pytest.raises(RegistryError):
        CaseRegistry().discover(tmp_path / "nope")


def test_empty_case_directory_is_reported(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RegistryError) as excinfo:
        CaseRegistry().discover(tmp_path / "empty")
    assert "no case files" in str(excinfo.value)