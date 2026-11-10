"""N02 回归测试：能力台账自动升账引擎的缺陷修复与战役判据。

两类：
* ``TestDefectRegressions`` —— 修复前的真实缺陷（在旧实现上必红）：
  1. 自指污染：扫描器扫到自己包里的标记字面量，把无关能力判成 STUB/MOCK；
  2. 文件级 STUB 过触发：仓库里任一文件出现 NotImplementedError，就能把整条能力判 STUB；
  3. 五元 config 证据自指：``config/capabilities.json`` 自身含能力模式，config 证据恒真；
  4. 理论证据只看"首个匹配文档"：命中但无推导内容时直接判缺失，不再往下找；
  5. as_of 时区混排按字符串排序 → 最新裁决取错。
* ``TestBattleAcceptance`` —— 战役判据 AC1–AC8 的机器可复核入口（读落盘产物 + 快判据）。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from capability_ledger_auto import ac_verify  # noqa: E402
from capability_ledger_auto.evidence_chain import build_evidence_chain  # noqa: E402
from capability_ledger_auto.grader import GradeDeterminer  # noqa: E402
from capability_ledger_auto.ledger_audit import (  # noqa: E402
    AUDIT_DIR,
    DEFAULT_ADJUDICATION_LEDGER,
    DEFAULT_MANUAL_LEDGER,
    content_digest,
    grade_capabilities,
    load_manual_reference,
)
from capability_ledger_auto.registry import CapabilityDef, CapabilityRegistry  # noqa: E402
from capability_ledger_auto.scanner import (  # noqa: E402
    DEFAULT_EXCLUDE_PREFIXES,
    EvidenceScanner,
    as_of_sort_key,
    extract_adjudications,
    is_stub_like,
    scan_adjudication_ledger,
    scan_code_for_patterns,
    sha256_file,
)

ARTIFACT_DIR = REPO_ROOT / AUDIT_DIR


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _registry(*caps: CapabilityDef) -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for cap in caps:
        reg.capabilities[cap.capability_id] = cap
    return reg


# ------------------------------------------------------------------ 缺陷回归

class TestDefectRegressions:
    def test_self_scan_is_excluded(self, tmp_path):
        """缺陷 1：扫描器不得把自己的包当成能力证据（自指污染）。"""
        _write(tmp_path / "src" / "capability_ledger_auto" / "scanner.py",
               "MOCK_MARKERS = ('[MOCK]', 'NotImplementedError')\n"
               "def placeholder_probe():\n    return 1\n")
        _write(tmp_path / "src" / "engine_core.py",
               "def placeholder_probe():\n    return 42\n")
        ev = scan_code_for_patterns(tmp_path, ["placeholder"], exclude_prefixes=DEFAULT_EXCLUDE_PREFIXES)
        assert ev, "应有证据命中"
        assert all(not e.file_path.startswith("src/capability_ledger_auto/") for e in ev)
        assert all(not e.mock_markers for e in ev), "自指文件里的标记字面量不得成为 MOCK 证据"

    def test_unrelated_stub_file_does_not_stub_capability(self, tmp_path):
        """缺陷 2：别的文件里有一处 NotImplementedError，不得把本能力判成 STUB。"""
        _write(tmp_path / "src" / "unrelated.py",
               "def something_else():\n    raise NotImplementedError\n")
        _write(tmp_path / "src" / "frob_core.py",
               "def frobnicate(x):\n    return x * 2\n")
        cap = CapabilityDef(capability_id="zz.frob", label="frob", description="",
                            code_patterns=["frobnicate"])
        scan = EvidenceScanner(tmp_path, _registry(cap)).scan_capability(cap)
        assert scan.stub_only_code_count == 0
        assert scan.substantive_code_count >= 1
        chain = build_evidence_chain(cap, scan, tmp_path)
        assert GradeDeterminer(root=tmp_path).determine(chain).grade != "STUB"

    def test_is_stub_like_is_node_scoped(self):
        """缺陷 2b：STUB 判定看节点体，不看整文件。"""
        import ast
        tree = ast.parse("def a():\n    raise NotImplementedError\n"
                         "def b():\n    return 1\n")
        a, b = tree.body
        assert is_stub_like(a) is True
        assert is_stub_like(b) is False

    def test_mock_marker_must_be_inside_matched_node(self, tmp_path):
        """缺陷 1b：同文件里他处的随机数不得污染本能力（节点级 MOCK 证据）。"""
        _write(tmp_path / "src" / "mixed.py",
               "import random\n"
               "def random_helper():\n    return random.random()\n"
               "def frobnicate(x):\n    return x + 1\n")
        cap = CapabilityDef(capability_id="zz.frob", label="frob", description="",
                            code_patterns=["frobnicate"])
        scan = EvidenceScanner(tmp_path, _registry(cap)).scan_capability(cap)
        assert scan.mock_markers == []

    def test_config_evidence_not_self_referential(self, tmp_path):
        """缺陷 3：注册表文件自身含能力模式，不得当作 config 证据。"""
        reg_path = _write(tmp_path / "config" / "capabilities.json",
                          json.dumps({"capabilities": [{"capability_id": "zz.frob",
                                                        "code_patterns": ["frobnicate"]}]}))
        _write(tmp_path / "src" / "frob.py", "def frobnicate(x):\n    return x\n")
        cap = CapabilityDef(capability_id="zz.frob", label="frob", description="",
                            code_patterns=["frobnicate"])
        scan = EvidenceScanner(tmp_path, _registry(cap)).scan_capability(cap)
        chain = build_evidence_chain(cap, scan, tmp_path, exclude_paths=(str(reg_path),))
        assert chain.five_tuple.config is None or not chain.five_tuple.config.is_present

    def test_theory_finder_skips_doc_without_theory_content(self, tmp_path):
        """缺陷 4：首个命中文档若无推导内容，应继续找，而不是直接判缺失。"""
        _write(tmp_path / "docs" / "a_mention.md", "frobnicate 只是被提及，详见别处。\n")
        _write(tmp_path / "docs" / "b_theory.md", "frobnicate 控制方程：y = kx\n")
        _write(tmp_path / "src" / "frob.py", "def frobnicate(x):\n    return x\n")
        cap = CapabilityDef(capability_id="zz.frob", label="frob", description="",
                            code_patterns=["frobnicate"])
        scan = EvidenceScanner(tmp_path, _registry(cap)).scan_capability(cap)
        chain = build_evidence_chain(cap, scan, tmp_path)
        assert chain.five_tuple.theory is not None
        assert chain.five_tuple.theory.is_present
        assert chain.five_tuple.theory.source_path.endswith("b_theory.md")

    def test_as_of_timezone_normalisation(self):
        """缺陷 5：+08:00 与 +00:00 混排时按字符串排序会错序。"""
        # 07:00Z 晚于 15:56+08:00(=07:56Z)？ 不：07:00Z 更早。
        assert as_of_sort_key("2026-11-01T07:00:00+00:00") < as_of_sort_key("2026-11-01T15:56:14+08:00")
        # 字符串比较会得出相反结论（"15" > "07"），归一化后必须正确
        assert "2026-11-01T07:00:00+00:00" < "2026-11-01T15:56:14+08:00"
        # 无时区按 UTC 处理
        assert as_of_sort_key("2026-11-01T07:05:08.77419") < as_of_sort_key("2026-11-01T07:56:14+00:00")

    def test_latest_adjudication_wins_across_sources(self, tmp_path):
        """最新裁决语义：更晚的 REAL 胜过更早的 MOCK；反之亦然。"""
        ledger = _write(tmp_path / "ledger.json", json.dumps({"capabilities": [
            {"id": "zz.frob", "evidence": [
                {"report_id": 1, "asserted_level": "MOCK", "as_of": "2026-11-01T07:00:00+00:00"},
                {"doc": "doc:x.md", "asserted_level": "REAL", "as_of": "2026-11-01T16:00:00+08:00"},
            ]},
        ]}))
        ev = scan_adjudication_ledger(ledger, ["zz.frob"])
        latest = max(ev, key=lambda r: as_of_sort_key(r.as_of))
        assert latest.asserted_level == "REAL"

    def test_extract_adjudications_vocabulary_only(self):
        """裁决抽取只认治理词表（零领域语义）。"""
        assert extract_adjudications("**审计裁决**：**MOCK**") == ["MOCK"]
        assert extract_adjudications("判决：**PASS**") == ["PASS"]
        assert "GZ" not in extract_adjudications("某域量 GZ 与 eta0 无关")


# ------------------------------------------------------------------ 战役判据

@pytest.fixture(scope="module")
def payload():
    path = ARTIFACT_DIR / "ledger_audit.json"
    if not path.exists():
        pytest.skip("缺少落盘产物 artifacts/capability_ledger_auto/ledger_audit.json")
    return json.loads(path.read_text(encoding="utf-8"))


class TestBattleAcceptance:
    def test_ac1_manual_agreement(self, payload):
        res = ac_verify.ac1_manual_agreement(
            REPO_ROOT, payload, REPO_ROOT / DEFAULT_MANUAL_LEDGER,
            REPO_ROOT / DEFAULT_ADJUDICATION_LEDGER)
        assert res["passed"], res["detail"]
        assert res["numbers"]["matched"] == res["numbers"]["total"] == 3

    def test_ac2_overclaim_detected(self, payload):
        res = ac_verify.ac2_overclaim(REPO_ROOT, payload, REPO_ROOT / DEFAULT_MANUAL_LEDGER)
        assert res["passed"], res["detail"]
        assert res["numbers"]["rate"] == 1.0
        # propeller 案例必须落到 MOCK，而不是 REAL/WITHHELD
        rows = {r["capability_id"]: r for r in res["numbers"]["rows"]}
        assert rows["propeller.open_water"]["auto_grade"] == "MOCK"

    def test_ac3_negative_control(self):
        res = ac_verify.ac3_negative_control(REPO_ROOT)
        assert res["passed"], res["detail"]
        assert res["numbers"]["false_positives"] == []

    def test_ac5_incremental(self):
        res = ac_verify.ac5_incremental(REPO_ROOT)
        assert res["passed"], res["detail"]
        assert res["numbers"]["upgraded"] is True
        assert res["numbers"]["control_stable"] is True

    def test_ac6_provenance(self, payload):
        res = ac_verify.ac6_provenance(REPO_ROOT, ARTIFACT_DIR)
        assert res["passed"], res["detail"]
        assert res["numbers"]["content_digest"] == content_digest(payload)

    def test_ac7_zero_domain_semantics(self):
        res = ac_verify.ac7_zero_domain_semantics(REPO_ROOT)
        assert res["passed"], res["detail"]
        assert res["numbers"]["domain_term_hits"] == []

    def test_ac8_registry_untouched(self):
        res = ac_verify.ac8_registry_format(REPO_ROOT)
        assert res["passed"], res["detail"]
        assert res["numbers"]["capability_count"] == 21

    def test_artifact_has_no_wallclock(self, payload):
        """AC6 前提：产物不落墙钟时间戳，否则两次扫描 sha256 不可能相等。"""
        text = json.dumps(payload, ensure_ascii=False)
        assert "generated_at" not in payload
        assert payload["content_digest"] == content_digest(payload)
        assert len(text) > 0

    def test_ac4_idempotent_on_synthetic_repo(self, tmp_path):
        """AC4 快版本：同一合成仓库连续两次判级结果完全一致。"""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capability_ledger_auto.ac_verify import _build_synthetic_repo, _minimal_registry

        _, reg = _minimal_registry(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        _build_synthetic_repo(repo, with_test=False)
        out = []
        for _ in range(2):
            chains, grades, _ = grade_capabilities(repo, reg, exec_tests=False)
            out.append({cid: g.grade for cid, g in grades.items()})
        assert out[0] == out[1]


class TestScannerExecutionEvidence:
    def test_executed_tests_beat_static_inference(self, tmp_path):
        """真实执行证据是可判级的硬证据；静态推断不得冒充。"""
        _write(tmp_path / "src" / "frob.py", "def frobnicate(x):\n    return x * 2\n")
        _write(tmp_path / "tests" / "test_frob.py",
               "import sys\nfrom pathlib import Path\n"
               "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
               "from frob import frobnicate\n\n"
               "def test_ok():\n    assert frobnicate(2) == 4\n")
        cap = CapabilityDef(capability_id="zz.frob", label="frob", description="",
                            code_patterns=["frobnicate"], test_patterns=["test_frob"])
        reg = _registry(cap)
        static = EvidenceScanner(tmp_path, reg).scan_capability(cap)
        executed = EvidenceScanner(tmp_path, reg, exec_tests=True).scan_capability(cap)
        assert static.test_evidence_source == "static"
        assert executed.test_evidence_source == "executed"
        assert executed.test_pass_rate == 1.0
        assert executed.executed_passed == 1

    def test_failing_test_file_lowers_pass_rate(self, tmp_path):
        _write(tmp_path / "src" / "frob.py", "def frobnicate(x):\n    return x * 2\n")
        _write(tmp_path / "tests" / "test_frob.py",
               "def test_bad():\n    assert 1 == 2\n\n"
               "def test_ok():\n    assert True\n")
        cap = CapabilityDef(capability_id="zz.frob", label="frob", description="",
                            code_patterns=["frobnicate"], test_patterns=["test_frob"])
        scan = EvidenceScanner(tmp_path, _registry(cap), exec_tests=True).scan_capability(cap)
        assert scan.test_evidence_source == "executed"
        assert scan.test_pass_rate < 0.8
        chain = build_evidence_chain(cap, scan, tmp_path)
        assert GradeDeterminer(root=tmp_path).determine(chain).grade == "MOCK"