"""能力证据自动采集管线 — pytest 套件（映射 / 执行归属 / 判级 / 工件 / 主管线）。

纪律：零领域语义 —— 夹具全部用 ``fx.*`` 合成能力名与合成仓库，不硬编码任何
船舶/冰区词条；真实 21 项能力只在"整仓端到端"用例里由注册表驱动。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evidence_automation.acceptance import (build_acceptance, build_coverage,
                                                 check_no_downgrade, content_digest)
from tools.evidence_automation.collect import collect, registry_capability_ids
from tools.evidence_automation.consistency import evaluate_labeled
from tools.evidence_automation.grader import grade_capability, load_manual_ceiling
from tools.evidence_automation.mapping import MappingError, load_mapping
from tools.evidence_automation.runner import parse_junit, run_tests

LABELED = ROOT / "tests" / "fixtures" / "capability_evidence" / "labeled_cases.json"


# ------------------------------------------------------------------ 夹具


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """最小合成仓库：一条能力、一个测试模块、一份注册表、一个探针代码目录。"""
    (tmp_path / "config").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "svc").mkdir()
    (tmp_path / "tests" / "test_fx_ok.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    (tmp_path / "svc" / "impl.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    mapping = {
        "schema": "capability_evidence_map/1.0",
        "artifact_root": "artifacts/capability_evidence",
        "acceptance_defaults": {"min_tests": 1, "min_pass_rate": 0.8},
        "capabilities": {
            "fx.alpha": {
                "evidence_scope": "in_repo_tests",
                "test_modules": ["tests/test_fx_ok.py"],
                "probes": [],
                "code_globs": ["svc/impl.py"],
                "acceptance": {"min_tests": 1, "min_pass_rate": 0.9},
            },
            "fx.beta": {
                "evidence_scope": "runtime_probe",
                "test_modules": [],
                "probes": [],
                "code_globs": [],
                "acceptance": {},
            },
        },
    }
    (tmp_path / "config" / "capability_evidence_map.json").write_text(
        json.dumps(mapping), encoding="utf-8")
    registry = {"schema_version": "1.0", "capabilities": [
        {"capability_id": "fx.alpha"}, {"capability_id": "fx.beta"}]}
    (tmp_path / "config" / "capabilities.json").write_text(
        json.dumps(registry), encoding="utf-8")
    return tmp_path


def _write_junit(path: Path, cases: list[dict]) -> Path:
    """构造最小 junitxml（cases 项：classname/name/outcome）。"""
    body = []
    for case in cases:
        outcome = case.get("outcome", "passed")
        child = {"failed": "<failure/>", "errored": "<error/>",
                 "skipped": "<skipped/>"}.get(outcome, "")
        body.append(
            f'<testcase classname="{case["classname"]}" name="{case["name"]}">{child}</testcase>')
    path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        + "".join(body) + "</testsuite></testsuites>", encoding="utf-8")
    return path


# ------------------------------------------------------------ 映射装载校验


def test_mapping_loader_binds_capabilities_and_artifact_root(synthetic_repo: Path):
    mapping = load_mapping("config/capability_evidence_map.json", root=synthetic_repo)
    assert mapping.count == 2
    assert mapping.artifact_root == "artifacts/capability_evidence"
    assert mapping.test_modules() == ("tests/test_fx_ok.py",)
    assert mapping.acceptance_for("fx.alpha")["min_pass_rate"] == 0.9
    # 能力级覆盖优先于默认值；未覆盖的键回落到默认
    assert mapping.acceptance_for("fx.alpha")["min_tests"] == 1


def test_mapping_loader_rejects_unknown_probe(synthetic_repo: Path):
    path = synthetic_repo / "config" / "capability_evidence_map.json"
    data = json.loads(path.read_text())
    data["capabilities"]["fx.alpha"]["probes"] = ["no_such_probe"]
    path.write_text(json.dumps(data))
    with pytest.raises(MappingError, match="binding_unknown_probe"):
        load_mapping("config/capability_evidence_map.json", root=synthetic_repo)


def test_mapping_loader_rejects_missing_test_module(synthetic_repo: Path):
    path = synthetic_repo / "config" / "capability_evidence_map.json"
    data = json.loads(path.read_text())
    data["capabilities"]["fx.alpha"]["test_modules"] = ["tests/test_absent.py"]
    path.write_text(json.dumps(data))
    with pytest.raises(MappingError, match="binding_test_missing"):
        load_mapping("config/capability_evidence_map.json", root=synthetic_repo)


def test_mapping_loader_rejects_wrong_schema(synthetic_repo: Path):
    path = synthetic_repo / "config" / "capability_evidence_map.json"
    data = json.loads(path.read_text())
    data["schema"] = "something_else/9.9"
    path.write_text(json.dumps(data))
    with pytest.raises(MappingError, match="mapping_schema_mismatch"):
        load_mapping("config/capability_evidence_map.json", root=synthetic_repo)


# ---------------------------------------------------- junit 归属（回归护栏）


def test_junit_attribution_handles_classname_and_file_attrs(tmp_path: Path):
    xml = _write_junit(tmp_path / "j.xml", [
        {"classname": "tests.pkg.test_mod.TestClass", "name": "test_a"},
        {"classname": "tests.pkg.test_mod", "name": "test_b", "outcome": "failed"},
        {"classname": "vendor.other.test_x", "name": "test_c", "outcome": "errored"},
    ])
    known = ["tests/pkg/test_mod.py"]
    results = parse_junit(xml, known)
    agg = results["tests/pkg/test_mod.py"]
    assert (agg.total, agg.passed, agg.failed, agg.errored) == (2, 1, 1, 0)
    assert agg.pass_rate == pytest.approx(0.5)
    # 未映射的用例单独成模块，不会被塞进已知能力（防虚增）
    assert "vendor/other/test_x.py" in results


def test_runner_reports_not_executed_for_absent_module(tmp_path: Path):
    run = run_tests(tmp_path, [])
    assert run.junit_available is False
    assert run.stdout_tail == "no_tests_mapped"


# ------------------------------------------------------------- 判级核心逻辑


def test_labeled_fixture_agreement_is_at_least_90_percent():
    """C2（构造集口径）：20+ 例人工标注对抗夹具的一致率 ≥90%。"""
    cases = json.loads(LABELED.read_text(encoding="utf-8"))
    assert len(cases) >= 20
    report = evaluate_labeled(cases)
    assert report["mismatches"] == []
    assert report["agreement"] >= 0.9
    assert report["C2_labeled_agreement_met"] is True


def test_grader_ceiling_never_raises_automated_level():
    """自动判级不得越过人工裁决（只降不升）。"""
    grade = grade_capability(
        "fx.ceiling", tests={"decisive": 10, "passed": 10, "failed": 0, "errored": 0,
                             "pass_rate": 1.0, "min_tests": 1},
        probes=[], markers={"files_scanned": 1, "stub_files": {}, "mock_files": {}},
        implementation_present=True, mapped=True, ceiling="WITHHELD")
    assert grade.level == "WITHHELD"
    assert grade.ceiling_applied == "WITHHELD"


def test_grader_withheld_when_probe_unavailable_only():
    grade = grade_capability(
        "fx.probe_off", tests={"decisive": 0}, probes=[
            {"probe": "p", "criterion_kind": "behavioral", "available": False, "ok": False}],
        markers={"files_scanned": 1, "stub_files": {}, "mock_files": {}},
        implementation_present=True, mapped=True)
    assert grade.level == "WITHHELD"
    assert any("不可用" in line for line in grade.reasoning)


def test_grader_error_dominance_withholds_instead_of_mock():
    """缺工件/环境造成的 collection error 占主导时，不得判 MOCK。"""
    grade = grade_capability(
        "fx.errors", tests={"decisive": 30, "passed": 12, "failed": 1, "errored": 17,
                            "pass_rate": 0.4, "min_tests": 1},
        probes=[], markers={"files_scanned": 1, "stub_files": {}, "mock_files": {}},
        implementation_present=True, mapped=True)
    assert grade.level == "WITHHELD"


# ------------------------------------------------------------ 工件与覆盖率


def test_acceptance_digest_is_deterministic_and_excludes_volatile_fields():
    kwargs = dict(criteria={"min_tests": 1}, tests={"decisive": 3, "passed": 3},
                  probes=[], mapping_digest="0" * 64, evidence_scope="in_repo_tests")
    grade = {"level": "REAL", "basis": "executed_tests", "reasoning": [], "markers": {}}
    a = build_acceptance("fx.digest", grade=grade, **kwargs)
    b = build_acceptance("fx.digest", grade=grade, **kwargs)
    assert a["content_digest"] == b["content_digest"]
    volatile = dict(a, generated_at="2026-01-01T00:00:00Z", run_metrics={"elapsed_s": 9})
    assert content_digest(volatile) == a["content_digest"]


def test_coverage_counts_only_executed_evidence_and_records_reasons():
    acceptances = {
        "fx.tested": {"covered": True, "verdict": {"level": "REAL"}, "content_digest": "x"},
        "fx.probed": {"covered": True, "verdict": {"level": "WITHHELD"}, "content_digest": "y"},
        "fx.empty": {"covered": False, "verdict": {"level": "NOT_SUPPORTED"},
                     "content_digest": "z"},
    }
    cov = build_coverage(capability_ids=["fx.tested", "fx.probed", "fx.empty", "fx.missing"],
                         acceptances=acceptances, target_covered=2)
    assert cov["covered"] == 2
    assert cov["uncovered_ids"] == ["fx.empty", "fx.missing"]
    assert cov["uncovered_reasons"]["fx.missing"] == "no_acceptance_artifact"
    assert cov["grade_tally"]["REAL"] == 1
    assert cov["C1_coverage_target_met"] is True


def test_no_downgrade_check_flags_manual_real_regression():
    acceptances = {"fx.a": {"verdict": {"level": "WITHHELD"}},
                   "fx.b": {"verdict": {"level": "REAL"}}}
    report = check_no_downgrade(capability_ids=["fx.a", "fx.b"], acceptances=acceptances,
                                manual_levels={"fx.a": "REAL", "fx.b": "MOCK"})
    assert report["C4_manual_real_not_downgraded"] is False
    assert report["C4_no_auto_overclaim"] is False
    assert report["downgraded"] == ["fx.a:WITHHELD<REAL"]
    assert report["overclaimed"] == ["fx.b:REAL>MOCK"]


# --------------------------------------------------------------- 端到端管线


def test_collect_writes_acceptance_artifacts_and_is_reproducible(synthetic_repo: Path):
    payload = {"tests/test_fx_ok.py": {"total": 1, "passed": 1, "failed": 0,
                                       "errored": 0, "skipped": 0, "executed": True,
                                       "failures": []}}
    first = collect(synthetic_repo, run_mapped_tests=False, tests_payload=payload)
    second = collect(synthetic_repo, run_mapped_tests=False, tests_payload=payload)
    assert first["content_digest"] == second["content_digest"]
    assert first["coverage"]["covered"] == 1
    acc_path = synthetic_repo / "artifacts" / "capability_evidence" / "fx.alpha" / "acceptance.json"
    assert acc_path.exists()
    artifact = json.loads(acc_path.read_text())
    assert artifact["verdict"]["level"] == "REAL"
    assert (acc_path.parent / "acceptance.json.sha256").exists()
    assert artifact["capability_id"] == "fx.alpha"


def test_collect_marks_unmapped_and_unexecuted_capabilities_honestly(synthetic_repo: Path):
    summary = collect(synthetic_repo, run_mapped_tests=False, tests_payload={})
    assert summary["coverage"]["covered"] == 0
    beta = json.loads((synthetic_repo / "artifacts" / "capability_evidence" / "fx.beta"
                       / "acceptance.json").read_text())
    assert beta["verdict"]["level"] in ("WITHHELD", "NOT_SUPPORTED")
    assert beta["covered"] is False


def test_registry_ids_and_manual_ceiling_loader_read_real_ledgers():
    ids = registry_capability_ids(ROOT)
    assert len(ids) == 21
    ceilings = load_manual_ceiling(
        ROOT / "artifacts" / "capability_ledger" / "capability_ledger_resolved.json")
    # 人工台账里有证据的能力才有上限；REAL 的那条必须存在（C4 的对照锚点）
    assert "REAL" in set(ceilings.values())