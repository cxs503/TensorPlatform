"""交差审计发现结构化追踪机制测试（合成夹具，零领域语义）。

覆盖矩阵
--------
* 五类发现逐一反例：``fake_pass`` / ``hand_copy`` / ``label_mismatch`` /
  ``undefined_criteria`` / ``other`` 单类别报告都必须被识别且退出码 0
* 异构报告容器：显式键、嵌套、顶层列表、启发式列表都能定位
* 容错边界：非对象/非字符串条目降级 ``WITHHELD`` → 退出码 1；无法归类的文本同样
  ``WITHHELD``（不许静默算通过）；无发现结构 → ``NO_FINDINGS_STRUCTURE``
* 反假通过派生发现：自述计数 ≠ 实测计数 → ``DECLARED_COUNT_MISMATCH``；声明类别与
  其它分类字段互相矛盾 → ``LABEL_CONFLICT``（均为 ``label_mismatch`` 派生条目）
* 确定性：同输入两次运行逐字节相同；sha256 入档
* CLI：子进程跑 ``python3 -m tools.audit_findings_tracker``，退出码 / --out / --quiet
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.audit_findings_tracker import cli as CLI  # noqa: E402
from tools.audit_findings_tracker import core as C  # noqa: E402

SCHEMA = C.DEFAULT_SCHEMA_PATH
SAMPLE_DIR = REPO_ROOT / "artifacts" / "audit_findings_tracker"


def _write(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")
    return path


def _one(category: str) -> dict:
    return {"findings": [{"category": category, "summary": f"synthetic finding for {category}"}]}


# ---------------------------------------------------------------- 五类反例
@pytest.mark.parametrize("category", C.CANONICAL_CATEGORIES)
def test_each_canonical_category_is_recognized(tmp_path, category):
    report = _write(tmp_path, f"{category}.json", _one(category))
    result = C.analyze_report(report)
    assert result["status"] == "PASS"
    assert result["exit_code"] == 0
    assert result["counts"][category] == 1
    assert result["counts"]["WITHHELD"] == 0
    assert result["findings"][0]["match_scope"].startswith("structured:")


def test_fake_pass_via_code_alias(tmp_path):
    report = _write(tmp_path, "fp.json", {"findings": [
        {"code": "AUDIT-FAKE_PASS-07", "summary": "claim says green"}]})
    result = C.analyze_report(report)
    assert result["counts"]["fake_pass"] == 1
    assert result["findings"][0]["declared_id"] is None


def test_hand_copy_via_free_text_string(tmp_path):
    report = _write(tmp_path, "hc.json", {"findings": ["F2: hand_copy of upstream table"]})
    result = C.analyze_report(report)
    assert result["counts"]["hand_copy"] == 1
    assert result["findings"][0]["match_scope"] == "text"


def test_label_mismatch_via_chinese_alias(tmp_path):
    report = _write(tmp_path, "lm.json", {"findings": [
        {"category": "标签错位", "summary": "tag says WITHHELD, ledger says REAL"}]})
    result = C.analyze_report(report)
    assert result["counts"]["label_mismatch"] == 1


def test_undefined_criteria_via_text_fields(tmp_path):
    report = _write(tmp_path, "uc.json", {"findings": [
        {"title": "goal has no machine-adjudicable criterion",
         "rule": "undefined criteria cannot be replayed"}]})
    result = C.analyze_report(report)
    assert result["counts"]["undefined_criteria"] == 1
    assert result["findings"][0]["match_scope"] == "text"


# ---------------------------------------------------------------- 异构容器
@pytest.mark.parametrize("payload,pointer", [
    ({"findings": [{"type": "other", "summary": "x"}]}, "$.findings"),
    ({"audit": {"data": {"issues": [{"type": "other", "summary": "x"}]}}}, "$.audit.data.issues"),
    ([{"type": "other", "summary": "x"}], "$"),
    ({"ledger": {"records": [{"type": "other", "summary": "x"}]}}, "$.ledger.records"),
])
def test_tolerant_container_locations(tmp_path, payload, pointer):
    report = _write(tmp_path, "layout.json", payload)
    result = C.analyze_report(report)
    assert result["status"] == "PASS"
    assert result["layout"]["finding_pointers"] == [pointer]
    assert result["counts"]["other"] == 1


def test_heuristic_layout_when_no_known_key(tmp_path):
    report = _write(tmp_path, "heur.json", {"audit": {"log": [
        {"category": "hand_copy", "summary": "x"}, {"category": "other", "summary": "y"}]}})
    result = C.analyze_report(report)
    assert result["status"] == "PASS"
    assert result["layout"]["mode"] == "heuristic"
    assert result["layout"]["finding_pointers"] == ["$.audit.log"]


def test_explicit_empty_findings_is_pass(tmp_path):
    report = _write(tmp_path, "empty.json", {"findings": []})
    result = C.analyze_report(report)
    assert result["status"] == "PASS"
    assert result["counts"]["total"] == 0
    assert result["layout"]["mode"] == "explicit_empty"


# ---------------------------------------------------------------- 容错降级 / FAIL
def test_unsupported_entry_is_withheld_and_fails(tmp_path):
    report = _write(tmp_path, "bad_entry.json", {"findings": [42]})
    result = C.analyze_report(report)
    assert result["status"] == "FAIL"
    assert result["exit_code"] == 1
    assert result["counts"]["WITHHELD"] == 1
    assert result["findings"][0]["reason_code"] == "UNSUPPORTED_ENTRY"
    assert any(r["code"] == "UNSUPPORTED_ENTRY" for r in result["reasons"])


def test_unrecognized_text_is_withheld_not_silently_passed(tmp_path):
    report = _write(tmp_path, "unknown.json", {"findings": [
        {"summary": "colour of the banner changed"}]})
    result = C.analyze_report(report)
    assert result["status"] == "FAIL"
    assert result["counts"]["WITHHELD"] == 1
    assert result["findings"][0]["reason_code"] == "UNRECOGNIZED_CATEGORY"


def test_no_findings_structure_is_failure(tmp_path):
    report = _write(tmp_path, "nostruct.json", {"meta": {"note": "nothing to see"}})
    result = C.analyze_report(report)
    assert result["status"] == "FAIL"
    assert result["reasons"][0]["code"] == "NO_FINDINGS_STRUCTURE"


def test_malformed_report_json_is_failure(tmp_path):
    report = _write(tmp_path, "broken.json", "{not json")
    result = C.analyze_report(report)
    assert result["status"] == "FAIL"
    assert result["exit_code"] == 1
    assert result["reasons"][0]["code"] == "PARSE_ERROR"


def test_missing_report_file_is_failure(tmp_path):
    result = C.analyze_report(tmp_path / "absent.json")
    assert result["status"] == "FAIL"
    assert result["reasons"][0]["code"] == "REPORT_MISSING"


def test_schema_without_all_categories_is_invalid(tmp_path):
    schema = _write(tmp_path, "schema.json", {"categories": ["fake_pass", "other"]})
    report = _write(tmp_path, "r.json", _one("other"))
    result = C.analyze_report(report, schema)
    assert result["status"] == "FAIL"
    assert result["reasons"][0]["code"] == "SCHEMA_INVALID"


# ---------------------------------------------------------------- 反假通过派生发现
def test_declared_count_mismatch_creates_label_mismatch(tmp_path):
    report = _write(tmp_path, "counts.json", {
        "findings": [{"category": "fake_pass", "summary": "x"}],
        "counts": {"fake_pass": 5, "hand_copy": 0},
    })
    result = C.analyze_report(report)
    derived = [f for f in result["findings"] if f["origin"] == "derived"]
    assert len(derived) == 1
    assert derived[0]["category"] == "label_mismatch"
    assert derived[0]["reason_code"] == "DECLARED_COUNT_MISMATCH"
    assert derived[0]["detail"] == {"declared": 5, "actual": 1}
    assert result["status"] == "PASS"  # 不一致本身被结构化 = PASS
    assert result["summary"]["derived"] == 1


def test_consistent_declared_counts_produce_no_derived_findings(tmp_path):
    report = _write(tmp_path, "ok_counts.json", {
        "findings": [{"category": "hand_copy", "summary": "x"}],
        "counts": {"hand_copy": 1},
    })
    result = C.analyze_report(report)
    assert result["summary"]["derived"] == 0
    assert result["status"] == "PASS"


def test_label_conflict_between_category_and_code_is_flagged(tmp_path):
    report = _write(tmp_path, "conflict.json", {"findings": [
        {"category": "hand_copy", "code": "AUDIT-FAKE_PASS-03", "summary": "x"}]})
    result = C.analyze_report(report)
    original = [f for f in result["findings"] if f["origin"] == "report"][0]
    assert original["category"] == "hand_copy"  # 原始发现类别不被覆盖
    derived = [f for f in result["findings"] if f["origin"] == "derived"]
    assert derived and derived[0]["category"] == "label_mismatch"
    assert derived[0]["reason_code"] == "LABEL_CONFLICT"
    assert derived[0]["related_pointer"] == original["finding_id"]


def test_ambiguous_text_is_flagged_but_structured(tmp_path):
    report = _write(tmp_path, "amb.json", {"findings": [
        {"summary": "fake pass caused by undefined criteria"}]})
    result = C.analyze_report(report)
    finding = result["findings"][0]
    assert finding["category"] == "fake_pass"  # 优先级顺序
    assert finding["ambiguous"] is True


def test_required_categories_gate(tmp_path):
    report = _write(tmp_path, "cov.json", _one("fake_pass"))
    ok = C.analyze_report(report, require_categories=["fake_pass"])
    assert ok["status"] == "PASS"
    bad = C.analyze_report(report, require_categories=list(C.CANONICAL_CATEGORIES))
    assert bad["status"] == "FAIL"
    assert any(r["code"] == "MISSING_REQUIRED_CATEGORY" for r in bad["reasons"])


def test_schema_taxonomy_matches_canonical_set():
    schema = C.load_schema(SCHEMA)
    assert set(schema.categories) == set(C.CANONICAL_CATEGORIES)
    assert len(C.CANONICAL_CATEGORIES) == 5


# ---------------------------------------------------------------- 确定性与溯源
def test_deterministic_output_and_sha256(tmp_path):
    report = _write(tmp_path, "det.json", _one("fake_pass"))
    first = C.dump_result(C.analyze_report(report))
    second = C.dump_result(C.analyze_report(report))
    assert first == second
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert json.loads(first)["inputs"]["report"]["sha256"] == digest


# ---------------------------------------------------------------- CLI / 真实样本
def test_sample_reports_cover_five_categories():
    for name in ("report_15311.json", "report_15312.json"):
        result = C.analyze_report(SAMPLE_DIR / name)
        assert result["status"] == "PASS", result["reasons"]
        assert set(result["categories_seen"]) == set(C.CANONICAL_CATEGORIES)


def test_cli_subprocess_exit_codes_and_out_file(tmp_path):
    ok_report = SAMPLE_DIR / "report_15311.json"
    out = tmp_path / "structured.json"
    proc = subprocess.run(
        [sys.executable, "-m", "tools.audit_findings_tracker",
         "--report", str(ok_report), "--schema", str(SCHEMA), "--out", str(out), "--quiet"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "PASS"

    bad_report = _write(tmp_path, "bad.json", {"findings": [None]})
    proc = subprocess.run(
        [sys.executable, "-m", "tools.audit_findings_tracker",
         "--report", str(bad_report), "--quiet"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 1
    assert "withheld=1" in proc.stderr


def test_cli_default_schema_and_stdout_json(tmp_path):
    report = _write(tmp_path, "cli.json", _one("other"))
    proc = subprocess.run(
        [sys.executable, "-m", "tools.audit_findings_tracker", "--report", str(report)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["counts"]["other"] == 1
    assert CLI.main(["--report", str(report), "--quiet"]) == 0