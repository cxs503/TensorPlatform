"""p399w2-n02：证据指纹 / 证据驱动置信度 / 判级器效度 / 多根代码扫描 的回归测试。

覆盖的验收判据（数字与产物路径见 docs/capability_ledger_auto/P399W2_N02_*）：

- AC-FP  指纹确定性：同一仓库两次指纹逐字节一致；任一叶子被改写即可定位。
- AC-CF  置信度携带证据信息：同等级不同证据强度 → 置信度不同；单调、有界。
- AC-EV  带标注算例上真实扫描 + 真实 pytest 执行 → 五级全部召回（混淆矩阵对角）。
- AC-SR  代码扫描根缺陷修复：实现放在 ``backend/modules/`` 时仍能判 REAL
         （修复前恒定 WITHHELD："五元不全：4/5，缺 code"）。
- AC-WK  仅文本行命中（.md/.json 语料）不得算作"实质实现"。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from capability_ledger_auto import eval_validity as ev
from capability_ledger_auto.confidence import (
    CONFIDENCE_BANDS,
    evidence_confidence,
    evidence_features,
)
from capability_ledger_auto.evidence_chain import build_evidence_chain
from capability_ledger_auto.fingerprint import (
    MAX_ITEMS_PER_KIND,
    EvidenceLeaf,
    build_leaves,
    build_record,
    chain_root,
    fingerprint_chain,
    record_digest,
    verify_leaves,
)
from capability_ledger_auto.grader import GradeDeterminer
from capability_ledger_auto.registry import CapabilityDef, CapabilityRegistry
from capability_ledger_auto.scanner import (
    DEFAULT_CODE_ROOTS,
    CodeIndex,
    EvidenceScanner,
    scan_code_for_patterns,
)

LEVELS = ev.LEVELS


# ------------------------------------------------------------------ 夹具
@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory) -> tuple[Path, Path, list]:
    root = tmp_path_factory.mktemp("fp_fixture") / "repo"
    return ev.build_fixture_repo(root, replicas=1)


def _grade_one(repo: Path, registry_path: Path, cap_id: str, exec_tests: bool = False):
    registry = CapabilityRegistry.from_json(registry_path)
    cap = registry.get(cap_id)
    assert cap is not None, cap_id
    scanner = EvidenceScanner(repo, registry, exec_tests=exec_tests,
                              exec_timeout_s=120, exec_budget_s=300)
    scan = scanner.scan_capability(cap)
    chain = build_evidence_chain(cap, scan, repo, exclude_paths=[str(registry_path)])
    return scan, chain, GradeDeterminer(repo).determine(chain)


# ------------------------------------------------------------------ AC-FP
class TestFingerprint:
    def test_deterministic_across_runs(self, fixture_repo):
        repo, reg, _ = fixture_repo
        _, chain1, _ = _grade_one(repo, reg, "fx.withheld.1")
        _, chain2, _ = _grade_one(repo, reg, "fx.withheld.1")
        fp1 = fingerprint_chain(chain1, registry_sha256="reg-sha")
        fp2 = fingerprint_chain(chain2, registry_sha256="reg-sha")
        assert fp1.root == fp2.root
        assert fp1.to_dict() == fp2.to_dict()

    def test_root_covers_all_leaves_and_verifies(self, fixture_repo):
        repo, reg, _ = fixture_repo
        _, chain, _ = _grade_one(repo, reg, "fx.withheld.1")
        leaves = build_leaves(chain)
        assert leaves, "夹具应产生叶子"
        assert chain_root(chain.capability_id, leaves) == fingerprint_chain(chain).root
        ok, bad = verify_leaves(chain.capability_id, leaves,
                                expected_root=fingerprint_chain(chain).root)
        assert ok and bad is None

    def test_tampered_leaf_is_detected(self, fixture_repo):
        repo, reg, _ = fixture_repo
        _, chain, _ = _grade_one(repo, reg, "fx.withheld.1")
        leaves = build_leaves(chain)
        root = chain_root(chain.capability_id, leaves)
        # 改写第一条叶子的摘要 → 根哈希必须变化，且校验必须失败
        tampered = list(leaves)
        first = tampered[0]
        tampered[0] = EvidenceLeaf(first.kind, first.locator, "f" * 64, first.detail)
        assert chain_root(chain.capability_id, tampered) != root
        ok, _ = verify_leaves(chain.capability_id, tampered, expected_root=root)
        assert not ok

    def test_deleted_leaf_is_detected(self, fixture_repo):
        repo, reg, _ = fixture_repo
        _, chain, _ = _grade_one(repo, reg, "fx.withheld.1")
        leaves = build_leaves(chain)
        root = chain_root(chain.capability_id, leaves)
        ok, _ = verify_leaves(chain.capability_id, leaves[:-1], expected_root=root)
        assert not ok

    def test_leaf_emission_is_bounded_but_root_full(self):
        cap = CapabilityDef(
            capability_id="fx.bounded",
            label="bounded",
            description="synthetic",
            expected_evidence_types={"code"},
            code_patterns=["fxbounded"],
        )
        scan = _FakeScan(code_n=MAX_ITEMS_PER_KIND + 5, cap=cap.capability_id)
        chain = build_evidence_chain(cap, scan, Path("."), exclude_paths=())
        fp = fingerprint_chain(chain)
        assert fp.leaf_count > MAX_ITEMS_PER_KIND
        assert len(fp.leaves) <= MAX_ITEMS_PER_KIND * 4
        assert fp.truncated_by_kind.get("code", 0) >= 5

    def test_record_digest_binds_grade_and_confidence(self, fixture_repo):
        repo, reg, _ = fixture_repo
        _, chain, result = _grade_one(repo, reg, "fx.withheld.1")
        rec = build_record(chain, result, registry_sha256="reg-sha")
        assert rec["record_digest"] == record_digest(rec)
        # 等级或置信度一变，记录摘要必须变（防止"证据根对但判级被改"）
        rec2 = dict(rec, grade="REAL")
        assert record_digest(rec2) != rec["record_digest"]
        rec3 = dict(rec, confidence=round(rec["confidence"] + 0.1, 4))
        assert record_digest(rec3) != rec["record_digest"]


class _FakeScan:
    """最小 ScanResult 替身（手工构造证据，不依赖文件系统）。"""

    def __init__(self, code_n: int, cap: str = "fx.bounded"):
        from capability_ledger_auto.scanner import CodeEvidence
        self.capability_id = cap
        self.code_evidence = [
            CodeEvidence(file_path=f"src/fxbounded_{i}.py", line_number=i, match_type="function",
                         matched_text=f"def fxbounded_{i}", line_sha256="a" * 64,
                         file_sha256="b" * 64)
            for i in range(code_n)
        ]
        self.test_evidence = []
        self.report_evidence = []
        self.code_count = code_n
        self.test_count = 0
        self.report_count = 0
        self.has_implementation = True
        self.has_tests = False
        self.has_reports = False
        self.test_pass_rate = 0.0
        self.report_verdicts = []
        self.scan_notes = []
        self.substantive_code_count = code_n
        self.stub_only_code_count = 0
        self.mock_markers = []
        self.test_evidence_source = ""
        self.executed_test_files = 0
        self.executed_passed = 0
        self.executed_failed = 0
        self.adjudicated_level = ""
        self.adjudicated_as_of = ""
        self.adjudicated_source = ""
        self.weak_code_count = 0
        self.code_roots = ["src"]
        self.counts_authoritative = True


# ------------------------------------------------------------------ AC-CF
class TestConfidence:
    def test_same_grade_different_evidence_differs(self):
        """同等级不同证据强度必须给出不同置信度（修复前是常量 0.90）。"""
        strong = _FakeScan(code_n=3)
        strong.has_tests = True
        strong.test_evidence_source = "executed"
        strong.test_pass_rate = 1.0
        strong.executed_test_files = 2
        zero_chain = _FakeChain(present=5, quality=0.95)
        weak_chain = _FakeChain(present=3, quality=0.45)
        strong.has_reports = True
        strong.adjudicated_level = "PASS"

        c_strong, f_strong = evidence_confidence("REAL", strong, zero_chain, policy=None)
        c_weak, f_weak = evidence_confidence("REAL", strong, weak_chain, policy=None)
        assert c_strong != c_weak
        assert c_strong > c_weak
        assert f_strong["complete"] > f_weak["complete"]

    def test_withheld_confidence_falls_as_evidence_grows(self):
        scan = _FakeScan(code_n=2)
        low = evidence_confidence("WITHHELD", scan, _FakeChain(present=1, quality=0.1))[0]
        high = evidence_confidence("WITHHELD", scan, _FakeChain(present=5, quality=0.9))[0]
        assert high < low, "越接近 REAL，'证据不足'这一判断本身越不确信"

    def test_mock_confidence_rises_with_more_independent_reasons(self):
        scan = _FakeScan(code_n=2)
        one = evidence_confidence("MOCK", scan, _FakeChain(present=4, quality=0.5),
                                  mock_reason_count=1)[0]
        three = evidence_confidence("MOCK", scan, _FakeChain(present=4, quality=0.5),
                                    mock_reason_count=3)[0]
        assert three > one

    def test_bounds_and_determinism(self):
        scan = _FakeScan(code_n=2)
        chain = _FakeChain(present=5, quality=1.0)
        scan.test_evidence_source = "executed"
        scan.test_pass_rate = 1.0
        scan.has_tests = True
        scan.adjudicated_level = "PASS"
        scan.has_reports = True
        vals = [evidence_confidence("REAL", scan, chain)[0] for _ in range(3)]
        assert len(set(vals)) == 1
        lo, hi = CONFIDENCE_BANDS["REAL"]
        assert lo <= vals[0] <= hi <= 0.99

    def test_features_are_recomputable_from_public_fields(self):
        scan = _FakeScan(code_n=2)
        chain = _FakeChain(present=4, quality=0.6)
        feats = evidence_features("WITHHELD", scan, chain)
        assert feats["five_tuple_present"] == 4.0
        assert 0.0 <= feats["ready"] <= 1.0
        assert feats["ready"] == pytest.approx(0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.0, abs=1e-6)


class _FakeChain:
    """最小证据链替身（只提供置信度公式用到的两个公开量）。"""

    def __init__(self, present: int, quality: float):
        from types import SimpleNamespace
        self.five_tuple = SimpleNamespace(present_count=present)
        self.chain_quality = quality
        self.missing_items = []


# ------------------------------------------------------------------ AC-SR
class TestScanRoots:
    def test_default_roots_cover_platform_layout(self):
        assert "backend" in DEFAULT_CODE_ROOTS and "src" in DEFAULT_CODE_ROOTS

    def test_backend_rooted_implementation_grades_real(self, fixture_repo):
        """修复前：实现只在 backend/modules/ → code 证据为空 → 恒判 WITHHELD。"""
        repo, reg, _ = fixture_repo
        scan, chain, result = _grade_one(repo, reg, "fx.real_backend.1", exec_tests=True)
        assert scan.code_count > 0, "多根扫描必须看到 backend/modules/ 下的实现"
        assert chain.five_tuple.present_count == 5
        assert result.grade == "REAL", result.reasoning
        assert scan.test_evidence_source == "executed"

    def test_src_only_root_reproduces_old_false_negative(self, fixture_repo):
        """对照基线：把扫描根收回 src/ 时该能力只能判 WITHHELD（缺陷可复现）。"""
        repo, reg, _ = fixture_repo
        registry = CapabilityRegistry.from_json(reg)
        cap = registry.get("fx.real_backend.1")
        scanner = EvidenceScanner(repo, registry, exec_tests=True, code_roots=("src",))
        scan = scanner.scan_capability(cap)
        chain = build_evidence_chain(cap, scan, repo, exclude_paths=[str(reg)])
        result = GradeDeterminer(repo).determine(chain)
        assert scan.code_count == 0
        assert result.grade != "REAL"
        assert any("五元不全" in r for r in result.reasoning)

    def test_per_capability_code_roots_override(self, fixture_repo):
        repo, reg, _ = fixture_repo
        registry = CapabilityRegistry.from_json(reg)
        cap = registry.get("fx.real.1")
        cap.code_roots = ["backend"]  # 显式收窄：src/ 下的实现不应再被看到
        scanner = EvidenceScanner(repo, registry, exec_tests=False)
        scan = scanner.scan_capability(cap)
        assert scan.code_roots == ["backend"]
        assert scan.code_count == 0

    def test_code_index_reuse_matches_direct_scan(self, fixture_repo):
        repo, reg, _ = fixture_repo
        direct = scan_code_for_patterns(repo, ["fxheld1"], code_roots=("src", "backend"))
        idx = CodeIndex.build(repo, ("src", "backend"))
        indexed = idx.match(["fxheld1"]).evidence
        assert [(e.file_path, e.line_number, e.match_type) for e in direct] == \
               [(e.file_path, e.line_number, e.match_type) for e in indexed]


# ------------------------------------------------------------------ AC-WK
class TestWeakEvidence:
    def test_markdown_only_hit_is_not_substantive(self, tmp_path):
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        # 只在一份语义无关的 md 里出现模式名：不得算作"实质实现"
        # 语料型文件（.md/.json）在扫描根内、但不是实现：只能算弱证据
        (root / "src" / "notes.md").write_text("fxsemantic appears in prose only\n")
        (root / "src" / "other.py").write_text("def something_else():\n    return 1\n")
        cap = CapabilityDef(
            capability_id="fx.semantic", label="s", description="",
            expected_evidence_types={"code"}, code_patterns=["fxsemantic"],
        )
        reg = CapabilityRegistry(capabilities={"fx.semantic": cap})
        scanner = EvidenceScanner(root, reg)
        scan = scanner.scan_capability(cap)
        assert scan.substantive_code_count == 0
        assert scan.weak_code_count >= 1
        chain = build_evidence_chain(cap, scan, root)
        result = GradeDeterminer(root).determine(chain)
        assert result.grade != "REAL"


# ------------------------------------------------------------------ AC-EV
class TestEvalValidity:
    def test_labelled_fixture_recovers_every_level(self, tmp_path):
        repo, reg, cases = ev.build_fixture_repo(tmp_path / "ev", replicas=1)
        result = ev.evaluate(repo, reg, cases, exec_tests=True, exec_timeout_s=120)
        assert result["case_count"] == len(cases)
        assert result["accuracy"] == 1.0, result["mismatches"]
        for level in LEVELS:
            m = result["per_level"][level]
            assert m["support"] >= 1
            assert m["recall"] == 1.0, (level, m, result["mismatches"])
            assert m["precision"] == 1.0, (level, m, result["mismatches"])
        assert result["macro_recall"] == 1.0
        # 混淆矩阵总量守恒
        total = sum(sum(row.values()) for row in result["confusion_matrix"].values())
        assert total == result["case_count"]
        # 每行都带证据指纹与（证据驱动的）置信度
        for row in result["rows"]:
            assert len(row["evidence_root"]) == 64
            assert 0.0 < row["confidence"] < 1.0
            assert row["record_digest"] == record_digest(row)

    def test_artifact_sha256_sidecar_matches(self, tmp_path):
        repo, reg, cases = ev.build_fixture_repo(tmp_path / "ev2", replicas=1,
                                                 include_variants=False)
        result = ev.evaluate(repo, reg, cases, exec_tests=False)
        paths = ev.write_artifact(result, tmp_path / "out")
        payload = json.loads(Path(paths["json"]).read_text())
        assert payload["schema"] == "eval-validity/v1"
        assert "repo" not in payload  # 产物不含易变的绝对路径
        sidecar = Path(paths["sidecar"]).read_text().split()[0]
        from capability_ledger_auto.scanner import sha256_file
        assert sidecar == sha256_file(Path(paths["json"]))

    def test_variant_metric_separates_standard_and_backend(self, tmp_path):
        repo, reg, cases = ev.build_fixture_repo(tmp_path / "ev3", replicas=1)
        result = ev.evaluate(repo, reg, cases, exec_tests=False)
        assert set(result["by_variant"]) == {"standard", "backend_root"}
        assert result["by_variant"]["backend_root"]["total"] == 2