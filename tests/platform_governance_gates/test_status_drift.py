"""Node status <-> mission final-state drift detection and convergence."""
from __future__ import annotations

import pytest

from tools.platform_governance_gates.status_drift import (
    DriftDetector,
    MissionState,
    NodeState,
    expected_node_status,
)


@pytest.mark.parametrize("status,result,expected", [
    ("done", {}, "done"),
    ("completed", {}, "done"),
    ("running", {"merged": True}, "done"),
    ("failed", {}, "failed"),
    ("running", {}, "in-flight"),
    ("pending", {}, "pending"),
    ("blocked", {}, "blocked"),
    ("mystery-state", {}, None),
])
def test_expected_status_mapping(status, result, expected):
    assert expected_node_status(MissionState(1, status, result)) == expected


def test_blocked_node_with_merged_mission_is_detected_and_converged():
    nodes = [NodeState("n1", "blocked", 7)]
    missions = [MissionState(7, "done", {"merged": True})]
    report = DriftDetector(auto_converge=True).scan(nodes, missions)
    assert report.detected == 1
    finding = report.findings[0]
    assert finding.merged_but_blocked is True
    assert finding.route == "converge:done"
    assert report.updates[0]["after"] == "done"


def test_ten_drifted_nodes_all_detected_and_converged():
    nodes, missions = [], []
    for i in range(10):
        nodes.append(NodeState(f"n{i}", "blocked", i))
        missions.append(MissionState(i, "done", {"merged": i % 2 == 0}))
    report = DriftDetector(auto_converge=True).scan(nodes, missions)
    assert report.detected == 10
    assert len(report.updates) == 10
    assert all(u["after"] == "done" for u in report.updates)
    assert report.alert is True


def test_orphan_node_is_visible_but_not_guessed():
    report = DriftDetector(auto_converge=True).scan(
        [NodeState("n1", "pending", None)], [])
    assert report.detected == 0
    assert len(report.orphans) == 1
    assert report.updates == []


def test_unmapped_mission_state_is_an_orphan_not_a_guess():
    report = DriftDetector(auto_converge=True).scan(
        [NodeState("n1", "pending", 1)], [MissionState(1, "mystery", {})])
    assert report.detected == 0
    assert report.orphans
    assert "unmapped" in report.orphans[0]["reason"]


def test_alert_threshold_controls_alert_flag():
    nodes = [NodeState("n1", "pending", 1), NodeState("n2", "done", 2)]
    missions = [MissionState(1, "running", {}), MissionState(2, "done", {})]
    strict = DriftDetector(alert_threshold=0.3).scan(nodes, missions)
    loose = DriftDetector(alert_threshold=0.9).scan(nodes, missions)
    assert strict.detected == loose.detected == 1
    assert strict.alert is True
    assert loose.alert is False


def test_auto_converge_off_reports_but_does_not_update():
    report = DriftDetector(auto_converge=False).scan(
        [NodeState("n1", "pending", 1)], [MissionState(1, "done", {})])
    assert report.detected == 1
    assert report.updates == []


def test_no_drift_is_quiet():
    report = DriftDetector().scan(
        [NodeState("n1", "done", 1)], [MissionState(1, "done", {})])
    assert report.detected == 0
    assert report.alert is False
    assert report.digest