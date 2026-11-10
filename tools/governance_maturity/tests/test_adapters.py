"""Tests for the adapter layer (synthetic upstream shapes only)."""

from __future__ import annotations

import pytest

from tools.governance_maturity.adapters import (
    AdapterError,
    EngineProfile,
    adapt_criterion,
    adapt_engine_report,
    adapt_metric_row,
    adapt_mission_report,
    profile_from_registry_doc,
)


def test_metric_row_flat():
    payload = adapt_metric_row(
        {"metric": "repro_rate", "value": 0.95, "unit": "ratio", "window": "W45", "evidence_count": 20}
    )
    assert payload == {
        "metric": "repro_rate", "value": 0.95, "unit": "ratio", "window": "W45", "evidence_count": 20,
    }


def test_metric_row_nested_fields():
    payload = adapt_metric_row(
        {"fields": {"metric": "uplift", "value": 1.5, "unit": "ratio", "window": "W45", "evidence_count": 3}}
    )
    assert payload["metric"] == "uplift"
    assert payload["value"] == 1.5


def test_metric_row_missing_field():
    with pytest.raises(AdapterError) as exc:
        adapt_metric_row({"metric": "x", "value": 1.0})
    assert exc.value.code == "missing_source_field"


def test_criterion_adapter():
    payload = adapt_criterion(
        {
            "criterion_id": "synthetic/bound",
            "comparator": "<=",
            "threshold": 1e-3,
            "source_type": "analytic",
            "source_ref": "a.py#sha256:" + "0" * 64,
            "probe": "synthetic",
        }
    )
    assert payload["decidable"] is True
    assert payload["probe"] == "synthetic"


def test_criterion_adapter_requires_keys():
    with pytest.raises(AdapterError):
        adapt_criterion({"criterion_id": "x"})


def test_mission_report_adapter():
    payload = adapt_mission_report(
        {
            "mission_id": 7,
            "title": "t",
            "verdict": "PASS",
            "sections": [{}, {}],
            "criteria": [1, 2, 3],
            "checks": {"passed": 2},
        }
    )
    assert payload["section_count"] == 2
    assert payload["criteria_count"] == 3
    assert payload["checks_passed"] == 2


def test_engine_profile_from_dict():
    profile = EngineProfile.from_dict(
        {"name": "p", "case_id_keys": ["id"], "anchor_list_keys": ["checks"],
         "anchor_name_key": "name", "anchor_passed_key": "ok"}
    )
    assert profile.name == "p"
    assert profile.case_id_keys == ("id",)


def test_adapt_engine_report_all_passed():
    profile = EngineProfile.from_dict(
        {"name": "p", "case_id_keys": ["case"], "anchor_list_keys": ["anchors"]}
    )
    payload = adapt_engine_report(
        {"case": "c1", "anchors": [{"anchor": "a", "passed": True}, {"anchor": "b", "passed": True}]},
        profile,
    )
    assert payload == {
        "case_id": "c1", "anchor_count": 2, "passed": True,
        "failure_count": 0, "failing_anchors": [],
    }


def test_adapt_engine_report_with_failures():
    profile = EngineProfile.from_dict(
        {"name": "p", "case_id_keys": ["case"], "anchor_list_keys": ["anchors"]}
    )
    payload = adapt_engine_report(
        {"case": "c2", "anchors": [{"anchor": "a", "passed": False}, {"anchor": "b", "passed": True}]},
        profile,
    )
    assert payload["passed"] is False
    assert payload["failure_count"] == 1
    assert payload["failing_anchors"] == ["a"]


def test_adapt_engine_report_missing_case_id():
    profile = EngineProfile.from_dict({"name": "p", "case_id_keys": ["case"], "anchor_list_keys": ["anchors"]})
    with pytest.raises(AdapterError) as exc:
        adapt_engine_report({"anchors": []}, profile)
    assert exc.value.code == "missing_source_field"


def test_adapt_engine_report_missing_anchors():
    profile = EngineProfile.from_dict({"name": "p", "case_id_keys": ["case"], "anchor_list_keys": ["anchors"]})
    with pytest.raises(AdapterError):
        adapt_engine_report({"case": "c"}, profile)


def test_profile_registry_document(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('{"profiles": [{"name": "a", "case_id_keys": ["id"]}]}', encoding="utf-8")
    profiles = profile_from_registry_doc(path)
    assert set(profiles) == {"a"}