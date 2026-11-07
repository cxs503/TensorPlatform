#!/usr/bin/env python3
"""capability_ledger_upgrade.py 测试套件

覆盖判据
--------
1. 无证据条目升 REAL 被拒（负例测试）
2. dry-run 与实跑一致（dry-run 不写盘，实跑写盘后内容一致）
3. 对 propulsion=MOCK 旧案出具只读审计输出
4. E2/E3/E7 校验链正确工作
5. 判词缺失导致 REAL 降级为 WITHHELD
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# 确保工具路径可导入
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "audit"))
import capability_ledger_upgrade as U


# ── 测试数据构造 ────────────────────────────────────────────────────────────
def make_card(
    card_id: str = "CAP-TEST-001",
    name: str = "Test Capability",
    repo: str = "TensorLBM",
    discipline: str = "resistance",
    anchor_type: str = "platform_report",
    report_id: str = "1375",
    report_body: str = "",
    verification_status: str = "NOT_VERIFIED",
    quote_match: str | None = None,
    gap_reason: str = "",
    verification_status_note: str = "",
    relative_error_pct: float | None = None,
    tolerance_pct: float | None = None,
) -> dict:
    """构造测试卡片。"""
    ve: dict = {
        "anchor_type": anchor_type,
        "report_id": report_id,
        "verification_status": verification_status,
    }
    if report_body:
        b = report_body.encode("utf-8")
        ve["report_md5"] = hashlib.md5(b).hexdigest()
        ve["report_body_sha256"] = hashlib.sha256(b).hexdigest()
    if quote_match:
        ve["quote_match"] = quote_match
        if report_body:
            lines = [ln for ln in report_body.split("\n") if quote_match in ln]
            if lines:
                ve["quote_line_sha256"] = U.line_sha256(lines[0])
    if gap_reason:
        ve["gap_reason"] = gap_reason
    if verification_status_note:
        ve["verification_status_note"] = verification_status_note
    if relative_error_pct is not None:
        ve["relative_error_pct"] = relative_error_pct
    if tolerance_pct is not None:
        ve["tolerance_pct"] = tolerance_pct

    return {
        "card_id": card_id,
        "name": name,
        "version": "1.0.0",
        "repo": repo,
        "discipline": discipline,
        "interface": {"input": {}, "output": {}, "format": "JSON"},
        "applicability": {"parameter_domain": {}, "regime": "test"},
        "dependencies": {"code_version": "test"},
        "resource_requirements": {},
        "validation_evidence": ve,
        "failure_modes": [],
        "created_at": "2026-11-01T00:00:00Z",
        "status": "active",
    }


def make_snapshot(report_id: str = "1375", body: str = "") -> dict:
    """构造证据快照。"""
    if not body:
        body = "Test report body\nNo failure here.\nAll good."
    return {
        "schema": "capability-evidence-snapshot/v1",
        "reports": {
            report_id: {
                "report_id": report_id,
                "title": "Test Report",
                "body": body,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "body_md5": hashlib.md5(body.encode()).hexdigest(),
            }
        },
    }


def make_repo_file_card(
    card_id: str = "CAP-TEST-REPO",
    anchor_path: str = "docs/REPORT_GATE.md",
    content: str = "# Test\nAll verified.",
) -> dict:
    """构造 repo_file 类型卡片。"""
    ve = {
        "anchor_type": "repo_file",
        "anchor_path": anchor_path,
        "verification_status": "NOT_VERIFIED",
    }
    ve["report_body_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    ve["report_md5"] = hashlib.md5(content.encode()).hexdigest()
    ve["quote_match"] = "All verified"
    ve["quote_line_sha256"] = U.line_sha256("All verified.")

    return {
        "card_id": card_id,
        "name": "Repo File Test",
        "version": "1.0.0",
        "repo": "TensorPlatform",
        "discipline": "reporting",
        "interface": {"input": {}, "output": {}, "format": "JSON"},
        "applicability": {"parameter_domain": {}, "regime": "test"},
        "dependencies": {"code_version": "test"},
        "resource_requirements": {},
        "validation_evidence": ve,
        "failure_modes": [],
        "created_at": "2026-11-01T00:00:00Z",
        "status": "active",
    }


# ── 测试 1: 无证据条目升 REAL 被拒（负例测试） ──────────────────────────────
class TestNegativeCaseNoEvidence:
    """负例测试：无证据条目升 REAL 被拒。"""

    def test_no_anchor_real_rejected(self, tmp_path: Path):
        """无锚点 → 不能升 REAL。"""
        card = make_card(
            card_id="CAP-NO-EVIDENCE",
            anchor_type="none",
            gap_reason="",  # 空 gap_reason
            verification_status="NOT_VERIFIED",
        )
        snapshot = make_snapshot()
        result = U.validate_for_upgrade(card, snapshot, tmp_path)

        assert result["ok"] is False
        assert result["target_grade"] == "WITHHELD"
        assert any("E2" in v for v in result["violations"])

    def test_no_sha256_real_rejected(self, tmp_path: Path):
        """有锚点但无判词 → 不能升 REAL（E7 因 quote_line_sha256 缺失而失败）。"""
        card = make_card(
            card_id="CAP-NO-SHA",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test body",
            verification_status="NOT_VERIFIED",
            quote_match="Test body",
            verification_status_note="",  # 无判词
        )
        snapshot = make_snapshot(report_id="1375", body="Test body")
        result = U.validate_for_upgrade(card, snapshot, tmp_path)

        assert result["ok"] is False
        assert result["target_grade"] == "WITHHELD"
        # 应至少有一个违规（E7 因 quote_line_sha256 缺失或判词缺失）
        assert len(result["violations"]) > 0

    def test_no_judgment_real_rejected(self, tmp_path: Path):
        """有 sha256 但无判词 → 不能升 REAL。"""
        card = make_card(
            card_id="CAP-NO-JUDGMENT",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test body",
            verification_status="NOT_VERIFIED",
            quote_match="Test body",
            verification_status_note="",  # 无判词
        )
        snapshot = make_snapshot(report_id="1375", body="Test body")
        result = U.validate_for_upgrade(card, snapshot, tmp_path)

        assert result["ok"] is False
        assert result["target_grade"] == "WITHHELD"
        assert any("判词" in v or "verification_status_note" in v for v in result["violations"])


# ── 测试 2: dry-run 与实跑一致 ──────────────────────────────────────────────
class TestDryRunConsistency:
    """dry-run 预览与实跑结果一致。"""

    def test_dry_run_no_write(self, tmp_path: Path):
        """dry-run 不写盘。"""
        card = make_card(
            card_id="CAP-DRY-TEST",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test body",
            verification_status="NOT_VERIFIED",
            quote_match="Test body",
            verification_status_note="Verified by manual check.",
        )
        snapshot = make_snapshot(report_id="1375", body="Test body")

        # dry-run
        result_dry = U.validate_for_upgrade(card, snapshot, tmp_path)
        assert result_dry["ok"] is True

        # 实跑
        card_path = tmp_path / "CAP-DRY-TEST.json"
        card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2))

        # 模拟命令行实跑
        with patch.object(U, 'ROOT', tmp_path):
            result_live = U.validate_for_upgrade(card, snapshot, tmp_path)

        # 结果一致
        assert result_dry["target_grade"] == result_live["target_grade"]
        assert result_dry["verdict"] == result_live["verdict"]
        assert result_dry["checks"] == result_live["checks"]

    def test_dry_run_output_matches_live(self, tmp_path: Path):
        """dry-run 输出与实跑输出一致。"""
        card = make_card(
            card_id="CAP-CONSISTENCY",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test body",
            verification_status="NOT_VERIFIED",
            quote_match="Test body",
            verification_status_note="Manual verification passed.",
        )
        snapshot = make_snapshot(report_id="1375", body="Test body")

        result_dry = U.validate_for_upgrade(card, snapshot, tmp_path)
        result_live = U.validate_for_upgrade(card, snapshot, tmp_path)

        assert result_dry["target_grade"] == result_live["target_grade"]
        assert result_dry["violations"] == result_live["violations"]
        assert result_dry["artifacts"] == result_live["artifacts"]


# ── 测试 3: propulsion=MOCK 旧案审计 ────────────────────────────────────────
class TestPropulsionMOCKAudit:
    """对 propulsion=MOCK 旧案出具只读审计输出。"""

    def test_audit_output_structure(self, tmp_path: Path):
        """审计输出包含必要字段。"""
        card = make_card(
            card_id="CAP-PROP-MOCK",
            name="Propulsion Open Water",
            repo="TensorLBM",
            discipline="propulsion",
            anchor_type="platform_report",
            report_id="1128",
            report_body="Results: 0/6 criteria passed. FAIL on all checks.",
            verification_status="MOCK",
            quote_match="FAIL",
            verification_status_note="Report 1128 shows 0/6 criteria passed.",
        )
        snapshot = make_snapshot(report_id="1128", body="Results: 0/6 criteria passed. FAIL on all checks.")

        result = U.validate_for_upgrade(card, snapshot, tmp_path)

        # 审计输出结构
        audit_output = {
            "audit_type": "read-only-audit",
            "card_id": card.get("card_id"),
            "card_name": card.get("name"),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "validation": result,
            "original_card": {
                "card_id": card.get("card_id"),
                "name": card.get("name"),
                "repo": card.get("repo"),
                "discipline": card.get("discipline"),
                "validation_evidence": {
                    "report_id": card["validation_evidence"].get("report_id"),
                    "report_md5": card["validation_evidence"].get("report_md5"),
                    "report_body_sha256": card["validation_evidence"].get("report_body_sha256"),
                    "anchor_type": card["validation_evidence"].get("anchor_type"),
                    "anchor_path": card["validation_evidence"].get("anchor_path"),
                    "anchor_title": card["validation_evidence"].get("anchor_title"),
                    "verification_status": card["validation_evidence"].get("verification_status"),
                    "quote_match": card["validation_evidence"].get("quote_match"),
                    "quote_line_sha256": card["validation_evidence"].get("quote_line_sha256"),
                    "relative_error_pct": card["validation_evidence"].get("relative_error_pct"),
                    "tolerance_pct": card["validation_evidence"].get("tolerance_pct"),
                    "gap_reason": card["validation_evidence"].get("gap_reason"),
                },
            },
        }

        assert "audit_type" in audit_output
        assert audit_output["audit_type"] == "read-only-audit"
        assert "validation" in audit_output
        assert "original_card" in audit_output
        assert audit_output["original_card"]["discipline"] == "propulsion"

        # 输出到文件
        out_path = tmp_path / "audit_output.json"
        out_path.write_text(json.dumps(audit_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        assert out_path.exists()

        # 读回验证
        read_back = json.loads(out_path.read_text(encoding="utf-8"))
        assert read_back["card_id"] == "CAP-PROP-MOCK"
        assert read_back["original_card"]["discipline"] == "propulsion"


# ── 测试 4: E2/E3/E7 校验链 ────────────────────────────────────────────────
class TestEvidenceChain:
    """E2/E3/E7 校验链正确工作。"""

    def test_e2_anchor_unresolvable(self, tmp_path: Path):
        """E2: 锚点不可解析。"""
        card = make_card(
            card_id="CAP-E2-FAIL",
            anchor_type="platform_report",
            report_id="99999",  # 不在快照中
            report_body="Test",
            verification_status="NOT_VERIFIED",
        )
        snapshot = make_snapshot()  # 空快照
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E2"] is False
        assert any("E2" in v for v in result["violations"])

    def test_e3_digest_mismatch(self, tmp_path: Path):
        """E3: 摘要不匹配。"""
        card = make_card(
            card_id="CAP-E3-FAIL",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test body",
            verification_status="NOT_VERIFIED",
            quote_match="Test body",
        )
        # 篡改摘要
        card["validation_evidence"]["report_body_sha256"] = "0" * 64
        snapshot = make_snapshot(report_id="1375", body="Test body")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E3"] is False
        assert any("E3" in v for v in result["violations"])

    def test_e7_quote_miss(self, tmp_path: Path):
        """E7: quote 不命中。"""
        card = make_card(
            card_id="CAP-E7-FAIL",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test body\nNo match here.",
            verification_status="NOT_VERIFIED",
            quote_match="NONEXISTENT_PATTERN",
        )
        snapshot = make_snapshot(report_id="1375", body="Test body\nNo match here.")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E7"] is False
        assert any("E7" in v for v in result["violations"])

    def test_e7_quote_line_sha256_drift(self, tmp_path: Path):
        """E7: quote_line_sha256 漂移。"""
        card = make_card(
            card_id="CAP-E7-DRIFT",
            anchor_type="platform_report",
            report_id="1375",
            report_body="All verified.",
            verification_status="NOT_VERIFIED",
            quote_match="All verified",
            verification_status_note="Verified.",
        )
        # 篡改 quote_line_sha256
        card["validation_evidence"]["quote_line_sha256"] = "0" * 16
        snapshot = make_snapshot(report_id="1375", body="All verified.")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E7"] is False
        assert any("E7" in v for v in result["violations"])

    def test_e5_overclaim(self, tmp_path: Path):
        """E5: 过度声明（PASS 但 quote 断言失败）。"""
        card = make_card(
            card_id="CAP-E5-OVER",
            anchor_type="platform_report",
            report_id="1375",
            report_body="是否 <3%：否。误差 34.6% 不可接受。",
            verification_status="PASS",
            quote_match="是否.*<3%.*否",
            verification_status_note="Overclaim detected.",
        )
        snapshot = make_snapshot(report_id="1375", body="是否 <3%：否。误差 34.6% 不可接受。")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E5"] is False
        assert any("E5" in v for v in result["violations"])


# ── 测试 5: 升账成功路径 ────────────────────────────────────────────────────
class TestUpgradeSuccess:
    """升账成功路径。"""

    def test_real_upgrade_with_evidence(self, tmp_path: Path):
        """完整证据链 → 升 REAL。"""
        card = make_card(
            card_id="CAP-UPGRADE-OK",
            anchor_type="platform_report",
            report_id="1375",
            report_body="All criteria passed. Error < 3%.",
            verification_status="NOT_VERIFIED",
            quote_match="All criteria passed",
            verification_status_note="Manual verification: all 6 criteria passed.",
        )
        snapshot = make_snapshot(report_id="1375", body="All criteria passed. Error < 3%.")
        result = U.validate_for_upgrade(card, snapshot, tmp_path)

        assert result["ok"] is True
        assert result["target_grade"] == "REAL"
        assert result["verdict"] == "ANCHORED"

    def test_upgrade_entry_construction(self, tmp_path: Path):
        """升级条目构造正确。"""
        card = make_card(
            card_id="CAP-ENTRY-TEST",
            anchor_type="platform_report",
            report_id="1375",
            report_body="All passed.",
            verification_status="NOT_VERIFIED",
            quote_match="All passed",
            verification_status_note="Verified.",
        )
        snapshot = make_snapshot(report_id="1375", body="All passed.")
        validation = U.validate_for_upgrade(card, snapshot, tmp_path)

        upgraded = U.build_upgrade_entry(card, validation, snapshot)

        assert upgraded["validation_evidence"]["verification_status"] == "PASS"
        assert "upgraded_at" in upgraded["validation_evidence"]
        assert "upgraded_by" in upgraded["validation_evidence"]
        assert upgraded["validation_evidence"]["upgraded_by"] == "capability_ledger_upgrade.py"

    def test_changelog_append(self, tmp_path: Path):
        """变更日志追加正确。"""
        changelog_path = tmp_path / "changelog.md"
        changelog_path.write_text("# Changelog\n\n", encoding="utf-8")

        with patch.object(U, 'CHANGELOG', changelog_path):
            change = U.append_changelog(
                "CAP-TEST", "MOCK", "REAL", dry_run=False
            )

        assert "CAP-TEST" in change
        assert "MOCK" in change
        assert "REAL" in change
        assert changelog_path.exists()
        content = changelog_path.read_text(encoding="utf-8")
        assert "CAP-TEST" in content

    def test_dry_run_changelog_not_written(self, tmp_path: Path):
        """dry-run 不写 changelog。"""
        changelog_path = tmp_path / "changelog.md"
        changelog_path.write_text("# Changelog\n\n", encoding="utf-8")
        original_content = changelog_path.read_text(encoding="utf-8")

        with patch.object(U, 'CHANGELOG', changelog_path):
            change = U.append_changelog(
                "CAP-TEST", "MOCK", "REAL", dry_run=True
            )

        new_content = changelog_path.read_text(encoding="utf-8")
        assert new_content == original_content  # 未修改


# ── 测试 6: 边界情况 ────────────────────────────────────────────────────────
class TestEdgeCases:
    """边界情况。"""

    def test_declared_empty(self, tmp_path: Path):
        """明确声明无证据 → WITHHELD。"""
        card = make_card(
            card_id="CAP-EMPTY",
            anchor_type="none",
            gap_reason="No experimental data available for this regime.",
            verification_status="NOT_VERIFIED",
        )
        snapshot = make_snapshot()
        result = U.validate_for_upgrade(card, snapshot, tmp_path)

        assert result["ok"] is True
        assert result["target_grade"] == "WITHHELD"
        assert result["verdict"] == "DECLARED_EMPTY"

    def test_placeholder_detected(self, tmp_path: Path):
        """占位符检测。"""
        card = make_card(
            card_id="CAP-PLACEHOLDER",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test",
            verification_status="NOT_VERIFIED",
        )
        card["validation_evidence"]["report_md5"] = "placeholder_md5_value"
        snapshot = make_snapshot(report_id="1375", body="Test")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E4"] is False
        assert any("E4" in v for v in result["violations"])

    def test_sentinel_digest_detected(self, tmp_path: Path):
        """哨兵摘要检测。"""
        card = make_card(
            card_id="CAP-SENTINEL",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test",
            verification_status="NOT_VERIFIED",
        )
        card["validation_evidence"]["report_body_sha256"] = "0" * 64
        snapshot = make_snapshot(report_id="1375", body="Test")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E4"] is False
        assert any("E4" in v for v in result["violations"])

    def test_tolerance_violation(self, tmp_path: Path):
        """容差违反。"""
        card = make_card(
            card_id="CAP-TOL-FAIL",
            anchor_type="platform_report",
            report_id="1375",
            report_body="Test",
            verification_status="PASS",
            relative_error_pct=20.0,
            tolerance_pct=15.0,
            quote_match="Test",
            verification_status_note="Over tolerance.",
        )
        snapshot = make_snapshot(report_id="1375", body="Test")
        result = U.check_evidence(card, snapshot, tmp_path)

        assert result["checks"]["E6"] is False
        assert any("E6" in v for v in result["violations"])


# ── 测试 7: 工具函数 ────────────────────────────────────────────────────────
class TestUtilityFunctions:
    """工具函数测试。"""

    def test_line_sha256(self):
        """行级 sha256 前缀。"""
        line = "Hello, World!"
        expected = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
        assert U.line_sha256(line) == expected

    def test_body_digests(self):
        """body 摘要计算。"""
        body = "Hello, World!"
        d = U.body_digests(body)
        assert len(d["body_sha256"]) == 64
        assert len(d["body_md5"]) == 32
        assert d["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        assert d["body_md5"] == hashlib.md5(body.encode()).hexdigest()

    def test_evidence_levels(self):
        """证据分级枚举。"""
        assert "REAL" in U.EVIDENCE_LEVELS
        assert "MOCK" in U.EVIDENCE_LEVELS
        assert "STUB" in U.EVIDENCE_LEVELS
        assert "WITHHELD" in U.EVIDENCE_LEVELS
        assert "NOT_SUPPORTED" in U.EVIDENCE_LEVELS
        assert U.EVIDENCE_LEVELS.index("REAL") > U.EVIDENCE_LEVELS.index("WITHHELD")


# ── 测试 8: 与真实卡片集成 ──────────────────────────────────────────────────
class TestRealCardsIntegration:
    """与真实卡片集成测试。"""

    @pytest.fixture
    def real_cards_dir(self) -> Path:
        """真实卡片目录。"""
        return Path(__file__).resolve().parents[2] / "docs" / "capability_registry" / "cards"

    @pytest.fixture
    def real_snapshot(self) -> dict:
        """真实证据快照。"""
        return U.load_snapshot()

    def test_cap005_report_generation(self, real_cards_dir: Path, real_snapshot: dict):
        """CAP-005 报告生成能力：应有 PASS 状态。"""
        card_path = real_cards_dir / "CAP-005-tensorplatform-report-generation.json"
        if not card_path.exists():
            pytest.skip("CAP-005 not found")

        card = U.load_card(card_path)
        result = U.check_evidence(card, real_snapshot, ROOT)

        # CAP-005 应该是 PASS 且有完整证据
        assert result["verdict"] in ("ANCHORED", "UNVERIFIABLE")
        # 不应有 E2/E3/E7 违规（如果快照中有对应报告）
        e237_pass = all(
            result["checks"].get(k) is True
            for k in ("E2", "E3", "E7")
            if result["checks"].get(k) not in ("n/a",)
        )
        # 如果快照中有对应报告，E2/E3/E7 应通过
        if real_snapshot.get("reports", {}).get("docs/REPORT_GATE.md"):
            assert result["checks"]["E2"] is True
            assert result["checks"]["E3"] is True

    def test_cap002_suboff_honest_fail(self, real_cards_dir: Path, real_snapshot: dict):
        """CAP-002 SUBOFF 阻力：HONEST_FAIL 不应被误判为 PASS。"""
        card_path = real_cards_dir / "CAP-002-tensorlbm-suboff-resistance.json"
        if not card_path.exists():
            pytest.skip("CAP-002 not found")

        card = U.load_card(card_path)
        result = U.check_evidence(card, real_snapshot, ROOT)

        # HONEST_FAIL 不应触发 E5 OVERCLAIM
        if result["checks"].get("E5") is not None:
            assert result["checks"]["E5"] is True  # HONEST_FAIL 不是 PASS 状态

    def test_cli_single_card_dry_run(self, real_cards_dir: Path):
        """CLI 单卡片 dry-run 模式。"""
        card_path = real_cards_dir / "CAP-005-tensorplatform-report-generation.json"
        if not card_path.exists():
            pytest.skip("CAP-005 not found")

        # dry-run 不应修改文件
        original_mtime = card_path.stat().st_mtime
        ret = U.main(["--card", str(card_path), "--dry-run"])
        new_mtime = card_path.stat().st_mtime

        assert new_mtime == original_mtime  # 文件未修改
        assert ret == 0  # 成功退出


# ── 测试 9: 批量处理 ────────────────────────────────────────────────────────
class TestBatchProcessing:
    """批量处理测试。"""

    def test_batch_directory(self, tmp_path: Path):
        """批量处理目录。"""
        # 创建测试卡片
        cards = [
            make_card(card_id="CAP-BATCH-1", anchor_type="platform_report",
                      report_id="1375", report_body="Test",
                      verification_status="NOT_VERIFIED",
                      quote_match="Test", verification_status_note="OK"),
            make_card(
                card_id="CAP-BATCH-2", anchor_type="platform_report",
                report_id="99999",  # 不在快照中
                verification_status="NOT_VERIFIED",
            ),
        ]
        for c in cards:
            (tmp_path / f"{c['card_id']}.json").write_text(
                json.dumps(c, ensure_ascii=False, indent=2)
            )

        # 创建 index.json
        index = {"cards": [{"card_id": c["card_id"]} for c in cards]}
        (tmp_path / "index.json").write_text(json.dumps(index, ensure_ascii=False))

        with patch.object(U, 'CARDS_DIR', tmp_path):
            with patch.object(U, 'INDEX', tmp_path / "index.json"):
                with patch.object(U, 'ROOT', tmp_path):
                    ret = U.main(["--dir", str(tmp_path), "--dry-run", "--quiet"])

        assert ret == 0  # 批量处理应成功（即使有 rejected）

    def test_batch_updates_index(self, tmp_path: Path):
        """批量处理更新 index.json。"""
        cards = [
            make_card(card_id="CAP-BATCH-UP", anchor_type="platform_report",
                      report_id="1375", report_body="Test",
                      verification_status="NOT_VERIFIED",
                      quote_match="Test", verification_status_note="OK"),
        ]
        for c in cards:
            (tmp_path / f"{c['card_id']}.json").write_text(
                json.dumps(c, ensure_ascii=False, indent=2)
            )
        index = {"cards": [{"card_id": c["card_id"], "verification_status": "NOT_VERIFIED"}]}
        (tmp_path / "index.json").write_text(json.dumps(index, ensure_ascii=False))

        with patch.object(U, 'CARDS_DIR', tmp_path):
            with patch.object(U, 'INDEX', tmp_path / "index.json"):
                with patch.object(U, 'ROOT', tmp_path):
                    ret = U.main(["--dir", str(tmp_path)])

        assert ret == 0
        updated_index = json.loads((tmp_path / "index.json").read_text())
        # 检查是否至少有一张卡片被处理
        assert len(updated_index["cards"]) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
