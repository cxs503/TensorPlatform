"""工况级样本库回填 R1 测试集（KPI-SWEEP-1000 准入验证）。

覆盖：
  - 三门各自拒绝路径（manifest / mission / checksum）
  - 工况计数规则（一个 sample = mission × 工况点）
  - 去重逻辑（同 mission+run_id 只计一次）
  - 案例级 ↔ 工况级换算（65 案例 → 工况级重算值）
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# 确保 repo root 在 path 中
REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT))

from src.sample_db.admission import (
    AdmissionDecision,
    AdmissionRejected,
    GateResult,
    StaticMissionResolver,
    NullMissionResolver,
    MissionUnresolved,
    admit_sample,
    sha256_file,
    E_MANIFEST_MISSING,
    E_CONTRACT,
    E_UNVERIFIED,
    E_NO_OUTPUTS,
    E_MISSION_ID_MISSING,
    E_MISSION_UNRESOLVED,
    E_MISSION_STATE,
    E_CHECKSUM_FORMAT,
    E_CHECKSUM_MISMATCH,
    E_SIZE_MISMATCH,
    E_ARTIFACT_MISSING,
    E_ARTIFACT_ESCAPE,
)


# ── 辅助：构造最小合法 manifest ─────────────────────────────────────
def _make_manifest(run_id: str = "r001", validation_status: str = "passed",
                   outputs: list | None = None, mission_id: int = 811,
                   status: str = "succeeded") -> dict:
    if outputs is None:
        outputs = []
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "validation_status": validation_status,
        "mission_id": mission_id,
        "outputs": outputs,
    }


def _write_artifact(tmpdir: Path, name: str, content: dict) -> Path:
    p = tmpdir / name
    p.write_text(json.dumps(content), encoding="utf-8")
    return p


def _write_file(tmpdir: Path, name: str, content: str = "hello") -> Path:
    p = tmpdir / name
    p.write_text(content, encoding="utf-8")
    return p


def _setup_run_dir(tmp_path: Path, manifest: dict, artifact_content: dict | None = None,
                   files: dict[str, str] | None = None) -> tuple[Path, Path]:
    """构造一个完整的 run 目录（manifest + artifact + 可选文件）。"""
    if artifact_content is None:
        artifact_content = {"verdict": "PASS"}
    artifact = _write_artifact(tmp_path, "result.json", artifact_content)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if files:
        for name, content in files.items():
            _write_file(tmp_path, name, content)
    return artifact, manifest_path


# ── 门 1：RunManifest 拒绝路径 ──────────────────────────────────────
class TestGate1Manifest:
    """门 1 拒绝路径：manifest 缺失 / 不合法 / 契约失败 / 未验证 / 无 outputs。"""

    def test_manifest_missing(self, tmp_path):
        """artifact 所在目录无 manifest 文件，产物也无嵌入键 → E_MANIFEST_MISSING。"""
        artifact = _write_artifact(tmp_path, "result.json", {"verdict": "PASS"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=NullMissionResolver())
        assert not decision.admitted
        assert decision.code == E_MANIFEST_MISSING

    def test_manifest_not_object(self, tmp_path):
        """manifest 是数组而非对象 → E_MANIFEST_NOT_OBJECT。"""
        manifest_path = tmp_path / "run_manifest.json"
        manifest_path.write_text("[1,2,3]", encoding="utf-8")
        artifact = _write_artifact(tmp_path, "result.json", {"verdict": "PASS"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=NullMissionResolver())
        assert not decision.admitted
        assert decision.code == "E_MANIFEST_NOT_OBJECT"

    def test_manifest_contract_missing_field(self, tmp_path):
        """manifest 缺少必需字段 → E_CONTRACT。"""
        bad = {"schema_version": 1, "run_id": "r1"}  # 缺 status, validation_status, outputs
        manifest_path = tmp_path / "run_manifest.json"
        manifest_path.write_text(json.dumps(bad), encoding="utf-8")
        artifact = _write_artifact(tmp_path, "result.json", {"verdict": "PASS"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=NullMissionResolver())
        assert not decision.admitted
        assert decision.code == E_CONTRACT

    def test_manifest_unverified(self, tmp_path):
        """validation_status != 'passed' → E_UNVERIFIED。"""
        manifest = _make_manifest(validation_status="failed")
        artifact, _ = _setup_run_dir(tmp_path, manifest)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=NullMissionResolver())
        assert not decision.admitted
        assert decision.code == E_UNVERIFIED

    def test_manifest_no_outputs(self, tmp_path):
        """manifest.outputs 为空 → E_NO_OUTPUTS。"""
        manifest = _make_manifest(outputs=[])
        artifact, _ = _setup_run_dir(tmp_path, manifest)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=NullMissionResolver())
        assert not decision.admitted
        assert decision.code == E_NO_OUTPUTS


# ── 门 2：Mission 终态拒绝路径 ──────────────────────────────────────
class TestGate2Mission:
    """门 2 拒绝路径：mission_id 缺失 / 无法解析 / 终态非 succeeded。"""

    def test_mission_id_missing(self, tmp_path):
        """manifest 无 mission_id，路径也无 mNNN 模式 → E_MISSION_ID_MISSING。"""
        manifest = _make_manifest(outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": "a" * 64, "size_bytes": 5
        }])
        del manifest["mission_id"]
        # 放在非 mNNN 目录下
        run_dir = tmp_path / "some_dir"
        run_dir.mkdir()
        artifact, _ = _setup_run_dir(run_dir, manifest, files={"data.txt": "hello"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({}))
        assert not decision.admitted
        assert decision.code == E_MISSION_ID_MISSING

    def test_mission_unresolved(self, tmp_path):
        """resolver 中无该 mission → E_MISSION_UNRESOLVED。"""
        manifest = _make_manifest(mission_id=9999, outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": "a" * 64, "size_bytes": 5
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest, files={"data.txt": "hello"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({811: "succeeded"}))
        assert not decision.admitted
        assert decision.code == E_MISSION_UNRESOLVED

    def test_mission_failed(self, tmp_path):
        """mission 终态 failed → E_MISSION_STATE。"""
        manifest = _make_manifest(mission_id=812, outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": "a" * 64, "size_bytes": 5
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest, files={"data.txt": "hello"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({812: "failed"}))
        assert not decision.admitted
        assert decision.code == E_MISSION_STATE

    def test_mission_approval_failed(self, tmp_path):
        """approval.finished_status != succeeded → E_MISSION_STATE。"""
        manifest = _make_manifest(mission_id=813, outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": "a" * 64, "size_bytes": 5
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest, files={"data.txt": "hello"})
        resolver = StaticMissionResolver({
            813: {"status": "succeeded", "finished_status": "failed"}
        })
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=resolver)
        assert not decision.admitted
        assert decision.code == E_MISSION_STATE


# ── 门 3：Checksum 拒绝路径 ─────────────────────────────────────────
class TestGate3Checksum:
    """门 3 拒绝路径：哈希格式错误 / 不匹配 / 大小不匹配 / 文件缺失 / 路径穿越。"""

    def test_checksum_format_bad_contract(self, tmp_path):
        """checksum_sha256 不是 64 位 hex → 被契约校验拦截 (E_CONTRACT)。"""
        manifest = _make_manifest(outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": "not-a-hash", "size_bytes": 5
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest, files={"data.txt": "hello"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({811: "succeeded"}))
        assert not decision.admitted
        # 契约校验先于 checksum gate 拦截
        assert decision.code == E_CONTRACT

    def test_checksum_mismatch(self, tmp_path):
        """实际 sha256 与声明不一致 → E_CHECKSUM_MISMATCH。"""
        fake_hash = "a" * 64
        manifest = _make_manifest(outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": fake_hash, "size_bytes": 5
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest, files={"data.txt": "hello"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({811: "succeeded"}))
        assert not decision.admitted
        assert decision.code == E_CHECKSUM_MISMATCH

    def test_size_mismatch(self, tmp_path):
        """size_bytes 与实际文件大小不一致 → E_SIZE_MISMATCH。"""
        content = "hello"
        real_hash = sha256_file(_write_file(tmp_path, "data.txt", content))
        manifest = _make_manifest(outputs=[{
            "uri": "data.txt", "media_type": "text/plain",
            "checksum_sha256": real_hash, "size_bytes": 999
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({811: "succeeded"}))
        assert not decision.admitted
        assert decision.code == E_SIZE_MISMATCH

    def test_artifact_missing(self, tmp_path):
        """声明的 uri 对应文件不存在 → E_ARTIFACT_MISSING。"""
        real_hash = sha256_file(_write_file(tmp_path, "other.txt", "x"))
        manifest = _make_manifest(outputs=[{
            "uri": "missing.txt", "media_type": "text/plain",
            "checksum_sha256": real_hash, "size_bytes": 1
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({811: "succeeded"}))
        assert not decision.admitted
        assert decision.code == E_ARTIFACT_MISSING

    def test_artifact_escape(self, tmp_path):
        """uri 包含 ../ 逃逸 run 目录 → E_ARTIFACT_ESCAPE。"""
        manifest = _make_manifest(outputs=[{
            "uri": "../../etc/passwd", "media_type": "text/plain",
            "checksum_sha256": "a" * 64, "size_bytes": 1
        }])
        artifact, _ = _setup_run_dir(tmp_path, manifest)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as af:
            decision = admit_sample(artifact, audit_log=af.name,
                                    mission_resolver=StaticMissionResolver({811: "succeeded"}))
        assert not decision.admitted
        assert decision.code == E_ARTIFACT_ESCAPE


# ── 工况计数规则与去重 ──────────────────────────────────────────────
class TestWorkloadCounting:
    """工况级计数：一个 sample = (mission_id, run_id) 唯一实例。"""

    def test_sample_identity_tuple(self):
        """工况身份 = (mission_id, run_id) 二元组。"""
        from scripts.backfill_samples_r21 import sample_identity
        id1 = sample_identity({"mission_id": 811, "run_id": "r001"})
        id2 = sample_identity({"mission_id": 811, "run_id": "r002"})
        id3 = sample_identity({"mission_id": 812, "run_id": "r001"})
        assert id1 != id2
        assert id1 != id3
        assert id2 != id3

    def test_dedup_same_mission_run(self):
        """同 mission+run_id 只计一次。"""
        from scripts.backfill_samples_r21 import deduplicate_samples
        samples = [
            {"mission_id": 811, "run_id": "r001", "discipline": "resistance"},
            {"mission_id": 811, "run_id": "r001", "discipline": "resistance"},
            {"mission_id": 811, "run_id": "r002", "discipline": "resistance"},
        ]
        deduped = deduplicate_samples(samples)
        assert len(deduped) == 2

    def test_case_to_workload_conversion(self):
        """案例级 → 工况级换算：一个案例可含多个工况点。"""
        from scripts.backfill_samples_r21 import case_to_workload_ratio
        # 65 案例，假设平均每案例 3.2 工况点 → 208 工况
        case_count = 65
        avg_points = 3.2
        expected = int(case_count * avg_points)
        ratio = case_to_workload_ratio(case_count, avg_points)
        assert ratio == expected

    def test_workload_count_from_samples(self):
        """从样本明细复算工况数。"""
        from scripts.backfill_samples_r21 import count_workload
        samples = [
            {"mission_id": 811, "run_id": "r001"},
            {"mission_id": 811, "run_id": "r002"},
            {"mission_id": 812, "run_id": "r001"},
        ]
        assert count_workload(samples) == 3

    def test_workload_count_empty(self):
        """空样本列表 → 工况数 0。"""
        from scripts.backfill_samples_r21 import count_workload
        assert count_workload([]) == 0

    def test_stats_consistency(self):
        """stats 一致性：admitted_count + Σrejected_by_code == 扫描总数。"""
        from scripts.backfill_samples_r21 import verify_stats_consistency
        stats = {
            "scanned_total": 100,
            "admitted_count": 60,
            "rejected_by_code": {
                "E_MANIFEST_MISSING": 10,
                "E_MISSION_STATE": 20,
                "E_CHECKSUM_MISMATCH": 10,
            },
        }
        assert verify_stats_consistency(stats) is True

    def test_stats_inconsistency(self):
        """不一致时返回 False。"""
        from scripts.backfill_samples_r21 import verify_stats_consistency
        stats = {
            "scanned_total": 100,
            "admitted_count": 60,
            "rejected_by_code": {"E_MANIFEST_MISSING": 5},
        }
        assert verify_stats_consistency(stats) is False
