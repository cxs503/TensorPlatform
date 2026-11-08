"""P22 能力台账消费者契约门测试（P22-N09 成熟化节点）。

覆盖四个机器可算判据：
E1: 已交付节点判据 100% 登记到能力台账
E2: 能力台账消费者路径可执行（下游任务能正确解析前置能力）
E3: 能力台账完整性报告无 MISSING 项
E4: 负控：故意注入一条未登记判据，扫描应检出
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.audit.p22_contract_gate import (
    P22_DELIVERED_NODES,
    CapabilityRegistry,
    auto_register_missing,
    extract_all_criteria,
    generate_completeness_report,
    negative_control_test,
    run_full_pipeline,
    scan_criteria_registration,
    validate_consumer_paths,
)


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def empty_registry():
    return CapabilityRegistry()


@pytest.fixture
def full_registry():
    """预登记所有 P22 判据的台账。"""
    reg = CapabilityRegistry()
    for node in P22_DELIVERED_NODES:
        for ac in node["metadata"]["acceptance_criteria"]:
            reg.register({
                "capability_id": ac["capability_id"],
                "name": f"{node['node_id']}: {ac['description']}",
                "description": ac["description"],
                "type": ac.get("type", "unknown"),
                "source_node": node["node_id"],
                "criterion_id": ac["id"],
            })
    return reg


@pytest.fixture
def tmp_output(tmp_path):
    return tmp_path / "p22_output"


# ── E1: 判据登记率 100% ────────────────────────────────────────────────

class TestE1RegistrationRate:
    """E1: 已交付节点判据 100% 登记到能力台账。"""

    def test_extract_criteria_count(self):
        """P22 N01-N08 共 15 条判据。"""
        criteria = extract_all_criteria(P22_DELIVERED_NODES)
        assert len(criteria) == 15

    def test_empty_registry_scan_finds_all_missing(self, empty_registry):
        """空台账 → 全部 missing。"""
        result = scan_criteria_registration(P22_DELIVERED_NODES, empty_registry)
        assert result["total_criteria"] == 15
        assert len(result["missing"]) == 15
        assert result["registration_rate"] == 0.0

    def test_full_registry_scan_all_registered(self, full_registry):
        """完整台账 → 全部 registered。"""
        result = scan_criteria_registration(P22_DELIVERED_NODES, full_registry)
        assert result["total_criteria"] == 15
        assert len(result["missing"]) == 0
        assert result["registration_rate"] == 100.0

    def test_auto_register_achieves_100pct(self, empty_registry):
        """自动登记后达到 100%。"""
        scan1 = scan_criteria_registration(P22_DELIVERED_NODES, empty_registry)
        assert scan1["registration_rate"] == 0.0

        auto_register_missing(scan1, empty_registry, P22_DELIVERED_NODES)

        scan2 = scan_criteria_registration(P22_DELIVERED_NODES, empty_registry)
        assert scan2["registration_rate"] == 100.0
        assert len(scan2["missing"]) == 0

    def test_pipeline_e1_passes(self, tmp_output):
        """完整流水线 E1 通过。"""
        result = run_full_pipeline(output_dir=tmp_output)
        assert result["criteria"]["E1_registration_rate_100pct"]["pass"] is True
        assert result["criteria"]["E1_registration_rate_100pct"]["value"] == 100.0


# ── E2: 消费者路径可执行 ────────────────────────────────────────────────

class TestE2ConsumerPaths:
    """E2: 能力台账消费者路径可执行。"""

    def test_consumer_paths_pass_with_full_registry(self, full_registry):
        """完整台账 → 消费者路径全部畅通。"""
        result = validate_consumer_paths(full_registry, P22_DELIVERED_NODES)
        assert result["executable"] is True
        assert result["checked_paths"] == 15
        assert len(result["failed_paths"]) == 0

    def test_consumer_paths_fail_with_empty_registry(self, empty_registry):
        """空台账 → 消费者路径全部失败。"""
        result = validate_consumer_paths(empty_registry, P22_DELIVERED_NODES)
        assert result["executable"] is False
        assert len(result["failed_paths"]) == 15
        assert all(f["reason"] == "NOT_IN_REGISTRY" for f in result["failed_paths"])

    def test_consumer_paths_detect_inactive(self):
        """台账条目 status != active → 路径失败。"""
        reg = CapabilityRegistry()
        for node in P22_DELIVERED_NODES:
            for ac in node["metadata"]["acceptance_criteria"]:
                reg.register({
                    "capability_id": ac["capability_id"],
                    "name": ac["description"],
                    "description": ac["description"],
                    "type": ac.get("type", "unknown"),
                    "source_node": node["node_id"],
                    "criterion_id": ac["id"],
                    "status": "deprecated",  # 故意设为 deprecated
                })
        result = validate_consumer_paths(reg, P22_DELIVERED_NODES)
        assert result["executable"] is False
        assert all("INACTIVE_STATUS" in f["reason"] for f in result["failed_paths"])

    def test_pipeline_e2_passes(self, tmp_output):
        """完整流水线 E2 通过。"""
        result = run_full_pipeline(output_dir=tmp_output)
        assert result["criteria"]["E2_consumer_paths_executable"]["pass"] is True
        assert result["criteria"]["E2_consumer_paths_executable"]["value"] is True


# ── E3: 完整性报告无 MISSING ────────────────────────────────────────────

class TestE3CompletenessReport:
    """E3: 能力台账完整性报告无 MISSING 项。"""

    def test_report_no_missing_after_auto_register(self, tmp_output):
        """自动登记后报告无 MISSING。"""
        result = run_full_pipeline(output_dir=tmp_output)
        report = result["completeness_report"]
        assert len(report["missing_items"]) == 0
        assert report["verdict"] == "COMPLETE"

    def test_report_has_missing_before_register(self, empty_registry):
        """未登记时报告有 MISSING。"""
        scan = scan_criteria_registration(P22_DELIVERED_NODES, empty_registry)
        consumer = validate_consumer_paths(empty_registry, P22_DELIVERED_NODES)
        report = generate_completeness_report(
            P22_DELIVERED_NODES, empty_registry, scan, consumer, []
        )
        assert len(report["missing_items"]) == 15
        assert report["verdict"] == "INCOMPLETE"

    def test_report_persisted_to_disk(self, tmp_output):
        """报告持久化到磁盘。"""
        result = run_full_pipeline(output_dir=tmp_output)
        report_path = tmp_output / "completeness_report.json"
        assert report_path.is_file()
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
        assert loaded["verdict"] == "COMPLETE"
        assert len(loaded["missing_items"]) == 0

    def test_pipeline_e3_passes(self, tmp_output):
        """完整流水线 E3 通过。"""
        result = run_full_pipeline(output_dir=tmp_output)
        assert result["criteria"]["E3_completeness_no_missing"]["pass"] is True
        assert result["criteria"]["E3_completeness_no_missing"]["value"] == 0


# ── E4: 负控检出 ────────────────────────────────────────────────────────

class TestE4NegativeControl:
    """E4: 负控——故意注入一条未登记判据，扫描应检出。"""

    def test_negative_control_detected(self):
        """负控注入的未登记判据被检出。"""
        neg = negative_control_test()
        assert neg["detected"] is True
        assert neg["injected_capability_id"] == "negative.control.injected_001"
        assert neg["scan_result"]["missing_count"] == 1

    def test_negative_control_with_empty_registry(self):
        """空台账下负控也检出。"""
        reg = CapabilityRegistry()
        test_nodes = [{
            "node_id": "NEG-TEST",
            "name": "test",
            "status": "done",
            "metadata": {
                "acceptance_criteria": [
                    {"id": "NEG-X", "capability_id": "neg.test.cap",
                     "description": "test", "type": "test"},
                ]
            }
        }]
        result = scan_criteria_registration(test_nodes, reg)
        assert len(result["missing"]) == 1
        assert result["missing"][0]["capability_id"] == "neg.test.cap"

    def test_pipeline_e4_passes(self, tmp_output):
        """完整流水线 E4 通过。"""
        result = run_full_pipeline(output_dir=tmp_output)
        assert result["criteria"]["E4_negative_control_detected"]["pass"] is True
        assert result["criteria"]["E4_negative_control_detected"]["value"] is True


# ── Integration: Full Pipeline ──────────────────────────────────────────

class TestFullPipeline:
    """完整流水线集成测试。"""

    def test_all_criteria_pass(self, tmp_output):
        """所有 E1-E4 判据全部通过。"""
        result = run_full_pipeline(output_dir=tmp_output)
        assert result["all_pass"] is True
        for k, v in result["criteria"].items():
            assert v["pass"] is True, f"{k} failed: {v}"

    def test_registry_persisted(self, tmp_output):
        """台账持久化到磁盘。"""
        result = run_full_pipeline(output_dir=tmp_output)
        reg_path = Path(result["artifacts"]["registry_path"])
        assert reg_path.is_file()
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        assert data["total_entries"] == 15

    def test_pipeline_result_persisted(self, tmp_output):
        """流水线结果持久化到磁盘。"""
        result = run_full_pipeline(output_dir=tmp_output)
        pipeline_path = tmp_output / "pipeline_result.json"
        assert pipeline_path.is_file()

    def test_idempotent_rerun(self, tmp_output):
        """重复运行结果一致（幂等）。"""
        r1 = run_full_pipeline(output_dir=tmp_output)
        r2 = run_full_pipeline(output_dir=tmp_output)
        assert r1["all_pass"] == r2["all_pass"]
        assert r1["scan_post_auto"]["rate"] == r2["scan_post_auto"]["rate"]

    def test_node_count(self):
        """P22 战役 8 个已交付节点。"""
        assert len(P22_DELIVERED_NODES) == 8
        for i, node in enumerate(P22_DELIVERED_NODES, 1):
            assert node["node_id"] == f"P22-N{i:02d}"
            assert node["status"] == "done"


# ── CapabilityRegistry Unit Tests ───────────────────────────────────────

class TestCapabilityRegistry:
    """CapabilityRegistry 单元测试。"""

    def test_empty_registry(self):
        reg = CapabilityRegistry()
        assert len(reg) == 0
        assert reg.has("anything") is False

    def test_register_and_get(self):
        reg = CapabilityRegistry()
        reg.register({"capability_id": "test.cap", "name": "Test", "description": "d"})
        assert reg.has("test.cap")
        assert reg.get("test.cap")["name"] == "Test"

    def test_all_ids(self):
        reg = CapabilityRegistry()
        reg.register({"capability_id": "a"})
        reg.register({"capability_id": "b"})
        assert reg.all_ids() == {"a", "b"}

    def test_json_roundtrip(self, tmp_path):
        reg = CapabilityRegistry()
        reg.register({"capability_id": "x", "name": "X", "description": "d"})
        path = tmp_path / "reg.json"
        reg.to_json(path)
        reg2 = CapabilityRegistry.from_json(path)
        assert reg2.has("x")
        assert len(reg2) == 1

    def test_from_json_missing_file(self, tmp_path):
        reg = CapabilityRegistry.from_json(tmp_path / "nonexistent.json")
        assert len(reg) == 0
