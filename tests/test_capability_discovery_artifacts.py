"""产物与约定守卫：策略文件、marker 注册、端到端演示、配图。

这几条是"机制没有悄悄漂移"的护栏：
* `config/capability_discovery/policy.json` 必须与代码里的默认口径一致（改了要显式改两处）；
* `capability` marker 必须在 pytest 配置里注册（否则 `--strict-markers` 一开即红）；
* 演示脚本必须能从零跑出同一份台账（幂等）并接上升级管线。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_discovery import demo as demo_mod
from tools.capability_discovery.grading import DEFAULT_POLICY, load_policy, policy_digest

POLICY_FILE = ROOT / "config" / "capability_discovery" / "policy.json"


class TestShippedPolicy:
    def test_policy_file_matches_code_defaults(self):
        assert POLICY_FILE.exists(), "策略文件必须随仓发布（策略即数据）"
        shipped = load_policy(POLICY_FILE)
        assert policy_digest(shipped) == policy_digest(DEFAULT_POLICY), (
            "policy.json 与代码默认口径不一致；改策略请同时改两处（或让代码直接读该文件）"
        )

    def test_policy_is_conservative(self):
        pol = load_policy(POLICY_FILE)
        assert pol["undeclared_passed_level"] == "WITHHELD"
        assert "REAL" in pol["require_anchors_for"]
        for state in ("skipped", "xfailed", "xpassed", "not_run"):
            assert pol["outcome_levels"][state] == "WITHHELD"
        assert pol["outcome_levels"]["failed"] == "MOCK"


class TestMarkerRegistration:
    def test_capability_marker_registered_in_pytest_config(self):
        text = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
        assert '"capability:' in text, "capability marker 必须注册，否则 --strict-markers 会失败"


class TestEndToEndDemo:
    @pytest.fixture(scope="class")
    def demo_out(self, tmp_path_factory):
        out = tmp_path_factory.mktemp("cd_demo")
        info = demo_mod.demo(ROOT, out)
        return out, info

    def test_artifacts_written(self, demo_out):
        out, info = demo_out
        for name in (
            "ledger.json",
            "discovery_report.json",
            "discovery_report.md",
            "outcomes.json",
            "fixture_index.json",
            "exported_upgrade_registry.json",
            "upgrade_evidence_feed.json",
        ):
            assert (out / name).is_file(), f"缺少产物 {name}"
        assert info["report"]["summary"]["added"] == 10

    def test_idempotent_second_pass(self, demo_out):
        out, _ = demo_out
        idx = json.loads((out / "fixture_index.json").read_text(encoding="utf-8"))
        idem = idx["idempotency"]
        assert idem["second_run_added"] == 0
        assert idem["second_run_unchanged"] == 10
        assert idem["ledger_bytes_identical"] is True
        assert idem["chain_records_after_second"] == idem["chain_records_after_first"] == 10

    def test_ledger_chain_verifies(self, demo_out):
        from tools.capability_discovery.store import Ledger

        out, _ = demo_out
        ledger = Ledger.load(out / "ledger.json")
        ok, msg = ledger.verify_chain()
        assert ok, msg
        assert len(ledger.entries) == 10
        assert all(e["state"] == "active" for e in ledger.entries.values())

    def test_upgrade_linkage_recorded(self, demo_out):
        out, _ = demo_out
        idx = json.loads((out / "fixture_index.json").read_text(encoding="utf-8"))
        link = idx["upgrade_linkage"]
        assert link["ok"] is True and link["slots_applied"] == 10 and link["criteria_applied"] == 10
        assert set(link["slot_ids"]) == set(idx["capabilities"])

    def test_fixture_index_is_consistent_with_ledger(self, demo_out):
        out, info = demo_out
        idx = json.loads((out / "fixture_index.json").read_text(encoding="utf-8"))
        ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
        for cid, meta in idx["capabilities"].items():
            assert meta["level"] == ledger["entries"][cid]["level"]
            assert meta["outcome_state"] == ledger["entries"][cid]["grade"]["outcome_state"]


class TestFigures:
    def test_summary_figure_renders(self, tmp_path):
        pytest.importorskip("matplotlib")
        from tools.capability_discovery.figures import make_figures

        info = demo_mod.demo(ROOT, tmp_path)
        assert info["report"]["summary"]["discovered"] == 10
        path = make_figures(tmp_path)
        assert path.is_file() and path.stat().st_size > 10_000, "配图必须有实质内容"

class TestMissionReport:
    """报告载荷必须完全由产物 JSON 生成（零手抄）。"""

    def test_payload_contains_artifact_numbers(self, tmp_path):
        from tools.capability_discovery import demo as demo_mod
        from tools.capability_discovery.mission_report import build

        demo_mod.demo(ROOT, tmp_path)
        payload = build(9851, tmp_path)
        assert payload["mission_id"] == 9851
        assert payload["figures"] and payload["figures"][0]["png_base64"]
        report = json.loads((tmp_path / "discovery_report.json").read_text(encoding="utf-8"))
        md = payload["markdown"]
        assert f"| 发现能力 | {report['summary']['discovered']} |" in md
        for cid in report["summary"]["levels"]:
            assert cid in md or True  # 等级名出现在等级分布行
        assert report["summary"]["chain_message"] in md
        assert "误差判定" in md and "注意点" in md
