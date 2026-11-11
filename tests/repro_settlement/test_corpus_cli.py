"""账本校验与 CLI 端到端（幂等：两次跑 sha256 一致）。"""
import json
import os

from tools.repro_settlement import cli
from tools.repro_settlement import corpus as corpus_mod


def _mini_corpus(path):
    data = {"corpus_id": "mini", "cases": [
        {"case_id": "p1", "status": "pass"},
        {"case_id": "b1", "status": "blocked", "fail_source": "dependency_missing",
         "blocked": {"reason": "dependency_missing", "pins": ["numpy"],
                     "solver_commit": "abc"}},
        {"case_id": "d1", "status": "drift", "fail_source": "numeric_drift",
         "drift": {"evidence": {"resolution": {"recorded": {"n": 1}, "observed": {"n": 2}}},
                   "delta_rel": 0.01, "tolerance": 0.05, "rebaselined": True}}],
        "ci": {"pytest_count": 30, "architectures": {"x86_64": "pass", "loongarch64": "pass"}}}
    corpus_mod.dump(data, path)
    return data


def test_validate_accepts_wellformed_corpus(tmp_path):
    data = _mini_corpus(str(tmp_path / "c.json"))
    assert corpus_mod.validate(data) == []


def test_validate_rejects_duplicate_ids():
    data = {"cases": [{"case_id": "x", "status": "pass"}, {"case_id": "x", "status": "pass"}]}
    assert any("duplicate_case_id" in e for e in corpus_mod.validate(data))


def test_validate_requires_fail_source_on_nonpass():
    data = {"cases": [{"case_id": "x", "status": "drift", "drift": {}}]}
    assert any("fail_source" in e for e in corpus_mod.validate(data))


def test_validate_rejects_empty_corpus():
    assert corpus_mod.validate({"cases": []}) == ["cases_missing_or_empty"]


def test_tally_counts_statuses(tmp_path):
    data = _mini_corpus(str(tmp_path / "c.json"))
    assert corpus_mod.tally(data) == {"pass": 1, "blocked": 1, "drift": 1}


def test_cli_run_is_idempotent_and_writes_locks(tmp_path):
    src = str(tmp_path / "c.json")
    _mini_corpus(src)
    out1 = cli.run(src, str(tmp_path / "out1"))
    out2 = cli.run(src, str(tmp_path / "out2"))
    assert out1["idempotent"] is True
    assert out1["report"]["digest"] == out2["report"]["digest"]
    lock_path = os.path.join(str(tmp_path / "out1"), "environment_locks", "b1.json")
    assert os.path.exists(lock_path)
    assert json.load(open(lock_path))["sha256"] == out1["report"]["environment_locks"]["b1"]


def test_cli_from_channel_ledger_builds_cases(tmp_path):
    ledger = {"commit": "abcdef123456", "channels": [
        {"channel_id": "a#x", "grade": "L3"},
        {"channel_id": "b#y", "grade": "L0"},
        {"channel_id": "c#z", "grade": "L2"}]}
    p = tmp_path / "led.json"
    p.write_text(json.dumps(ledger))
    built = cli.from_channel_ledger(str(p))
    assert [c["status"] for c in built["cases"]] == ["pass", "blocked", "drift"]
    assert built["provenance"]["verified_against_source"] is True


def test_cli_report_markdown_contains_matrix(tmp_path):
    src = str(tmp_path / "c.json")
    _mini_corpus(src)
    res = cli.run(src, str(tmp_path / "out"))
    md = open(res["md"], encoding="utf-8").read()
    assert "逐条归因矩阵" in md and "p1" in md and "d1" in md
