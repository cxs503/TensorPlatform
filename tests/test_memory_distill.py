"""memory_distill 单测：判例蒸馏的准入/append-only/幂等/验收裁决。

判据先于实现（可机器裁）：
- T1 引用抽取：带单位/百分号/误差上下文才算数字引用；裸整数、裸 's' 不算；
- T2 门控：引用门槛 3 是硬边界（2 拒 / 3 收），秘密与过程流水账一律拒；
- T3 池 append-only：追加后既有字节前缀 sha256 不变；重复 candidate_id 幂等跳过；
- T4 PLAYBOOK 同步：前缀/后缀字节不变、单条 <=120、二次 promote 不重复追加；
- T5 端到端：真实报告快照（fixture）→ >=10 条候选且全带 >=3 引用项；同输入同指纹；
- T6 验收裁据：候选数>=10 且抽样合格率>=60% 才 all_ok。
"""

from __future__ import annotations

import json
import os

import pytest

from tools.memory_distill.citations import extract_citations, extract_numbers, extract_paths
from tools.memory_distill.cli import cmd_acceptance, main
from tools.memory_distill.distill import Candidate, Boundary, candidate_id_for, derive_candidates
from tools.memory_distill.gate import GateConfig, gate_candidate, jaccard
from tools.memory_distill.ground import ground_candidate, ground_pool
from tools.memory_distill.promote import promote, read_playbook_lines
from tools.memory_distill.sources import load_reports, load_shadow_dispositions
from tools.memory_distill.store import AppendOnlyStore, AppendOnlyViolation

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "memory_distill")
REPORTS_FIXTURE = os.path.join(FIXTURES, "reports_sample.jsonl")
SHADOW_FIXTURE = os.path.join(FIXTURES, "shadow_merge_sample.json")


# ---------------------------------------------------------------- T1 引用抽取
def test_numbers_require_unit_or_context():
    assert extract_numbers("误差 0.9% vs 基准") == ["0.9%"]
    assert extract_numbers("耗时 92.1ms，核数 128核") == ["92.1ms", "128核"]
    # 裸整数/年份/mission 号不算引用
    assert extract_numbers("mission 5259 于 2026 年完成") == []
    # 科学计数法单位量
    assert "1e5" in extract_numbers("Re=1e5 的方腔")


def test_paths_are_whitelisted_and_not_double_counted():
    t = "见 docs/a.md、artifacts/x/y.json 与 tests/test_t.py::test_case"
    assert extract_paths(t) == ["docs/a.md", "artifacts/x/y.json"]
    cit = extract_citations(t)
    assert cit.tests == ["tests/test_t.py::test_case"]  # 测试锚点单独计
    assert "tests/test_t.py" not in cit.paths  # 不重复计数


def test_citation_extraction_is_deterministic():
    t = "误差 3.0%，Nu=6.98，docs/r.md，sha256:" + "a" * 32
    a, b = extract_citations(t), extract_citations(t)
    assert a.as_dict() == b.as_dict()
    assert a.paths == ["docs/r.md"]


# ---------------------------------------------------------------- T2 门控
def test_gate_citation_threshold_is_hard():
    two = "- m1 方腔 Nu 误差 3.0%，docs/a.md"  # 2 个引用项
    three = "- m1 方腔 Nu 误差 3.0%，92.1ms，docs/a.md"  # 3 个引用项
    assert gate_candidate(two).code == "REJECT_CITATIONS"
    assert gate_candidate(three).passed


def test_gate_rejects_secret_and_process_log():
    assert gate_candidate("- m1 Authorization: Bearer abcdefghijkl 3.0% docs/a.md").code == "REJECT_SECRET"
    assert gate_candidate("- m1 我先跑了测试 3.0% 92.1ms docs/a.md").code == "REJECT_PROCESS_LOG"


def test_gate_length_bounds():
    assert gate_candidate("- 短").code == "REJECT_LENGTH"
    long_line = "- m1 " + "误差 3.0% " * 20
    assert gate_candidate(long_line).code == "REJECT_LENGTH"


def test_gate_duplicate_by_jaccard():
    line = "- m1 方腔 Nu 误差 3.0%，92.1ms，docs/a.md"
    assert gate_candidate(line, existing=[]).passed
    dec = gate_candidate(line, existing=[line])
    assert dec.code == "REJECT_DUPLICATE"
    assert jaccard(line, line) == 1.0


# ---------------------------------------------------------------- T3 append-only 池
def test_store_is_append_only_and_idempotent(tmp_path):
    pool = str(tmp_path / "pool.jsonl")
    store = AppendOnlyStore(pool)
    c1 = Candidate(
        candidate_id=candidate_id_for("report", "mission:1", "标题A", "误差 3.0%"),
        kind="report",
        source_ref="mission:1",
        title="标题A",
        lesson="m1 标题A；误差 3.0%，92.1ms；docs/a.md",
        citations=extract_citations("误差 3.0%，92.1ms，docs/a.md"),
        boundary=Boundary("测试范围"),
    )
    r1 = store.append_candidates([c1], [{"passed": True, "code": "ACCEPT"}])
    assert r1 == {"appended": 1, "skipped_dup": 0}
    head = open(pool, "rb").read()

    r2 = store.append_candidates([c1], [{"passed": True, "code": "ACCEPT"}])
    assert r2 == {"appended": 0, "skipped_dup": 1}
    assert open(pool, "rb").read() == head  # 幂等：字节不变

    c2 = Candidate(
        candidate_id=candidate_id_for("report", "mission:2", "标题B", "误差 1.2%"),
        kind="report",
        source_ref="mission:2",
        title="标题B",
        lesson="m2 标题B；误差 1.2%，45.0ms；docs/b.md",
        citations=extract_citations("误差 1.2%，45.0ms，docs/b.md"),
    )
    store.append_candidates([c2], [{"passed": True, "code": "ACCEPT"}])
    after = open(pool, "rb").read()
    assert after.startswith(head)  # 既有前缀逐字节不变
    assert len(store) == 2


def test_store_detects_tampering(tmp_path):
    pool = str(tmp_path / "pool.jsonl")
    store = AppendOnlyStore(pool)
    store.append([{"candidate_id": "x", "lesson": "- a 3.0% 92.1ms docs/a.md"}])
    with open(pool, "w", encoding="utf-8") as f:  # 篡改既有内容
        f.write('{"candidate_id": "y", "lesson": "- tampered"}\n')
    with pytest.raises(AppendOnlyViolation):
        store.append([{"candidate_id": "z", "lesson": "- b"}])


# ---------------------------------------------------------------- T4 PLAYBOOK 同步
BOOTSTRAP_MIN = """# BOOTSTRAP

## PLAYBOOK（append-only）
- 老条目一：修 bug 先找参考值。
- 老条目二：图只从 SQL 取数。

## 逃生规则（infra 濒死时）
- 心跳先写 append-only 面。
"""


def _cand(cid: str, lesson: str) -> dict:
    return {"candidate_id": cid, "lesson": lesson, "gate": {"passed": True, "code": "ACCEPT"}}


def test_promote_is_append_only_and_idempotent():
    cands = [_cand("c1", "m9001 新条目：误差 0.9%，92.1ms；docs/a.md")]
    res = promote(cands, BOOTSTRAP_MIN, date_tag="2026-11-07", limit=5)
    assert res.audit["appended"] == 1
    assert res.audit["prefix_unchanged"] and res.audit["suffix_unchanged"]
    assert "## 逃生规则" in res.new_text  # 后节完整保留
    lines = read_playbook_lines(res.new_text)
    assert len(lines) == 3 and lines[-1].startswith("- 2026-11-07")
    assert all(len(x) - 2 <= GateConfig().max_len for x in lines)

    again = promote(cands, res.new_text, date_tag="2026-11-07", limit=5)
    assert again.audit["appended"] == 0  # 幂等
    assert again.new_text == res.new_text


def test_promote_respects_limit_and_rejects_failed_gate():
    cands = [
        {"candidate_id": "bad", "lesson": "m2 只有 0.1% 一个数字", "gate": {"passed": False, "code": "REJECT_CITATIONS"}},
        _cand("ok", "m1 条目甲：误差 0.9%，92.1ms；docs/a.md"),
        _cand("ok2", "m3 条目乙：误差 1.7%，128核；runs/b.json"),
    ]
    res = promote(cands, BOOTSTRAP_MIN, date_tag="2026-11-07", limit=1)
    assert res.audit["appended"] == 1
    assert any("REJECT_CITATIONS" in s["why"] for s in res.skipped)


def test_promote_requires_playbook_section():
    with pytest.raises(ValueError):
        promote([_cand("c1", "m1 x 误差 0.9%，92.1ms；docs/a.md")], "# 无此节\n")


# ---------------------------------------------------------------- T5 端到端（真实快照）
@pytest.mark.skipif(not os.path.exists(REPORTS_FIXTURE), reason="缺报告快照 fixture")
def test_end_to_end_on_real_report_snapshot_tmp(tmp_path):
    reports = load_reports(REPORTS_FIXTURE)
    shadow = load_shadow_dispositions(SHADOW_FIXTURE)
    assert len(reports) >= 8
    cands = derive_candidates(reports, shadow, max_len=120)
    assert len(cands) >= 10, f"候选太少: {len(cands)}"

    pool = str(tmp_path / "pool.jsonl")
    store = AppendOnlyStore(pool)
    passed = []
    for cand in cands:
        dec = gate_candidate(f"- {cand.lesson}", existing=store.lessons())
        if dec.passed:
            passed.append((cand, dec.as_dict()))
    store.append_candidates([c for c, _ in passed], [d for _, d in passed])
    recs = store.load()
    assert len(recs) >= 10
    for rec in recs:
        assert rec["citations"]["count"] >= 3
        assert len(rec["lesson"]) <= 120
        assert rec["gate"]["passed"] is True


@pytest.mark.skipif(not os.path.exists(REPORTS_FIXTURE), reason="缺报告快照 fixture")
def test_distillation_is_deterministic():
    reports = load_reports(REPORTS_FIXTURE)
    a = derive_candidates(reports, [], max_len=120)
    b = derive_candidates(reports, [], max_len=120)
    assert [c.candidate_id for c in a] == [c.candidate_id for c in b]
    assert [c.lesson for c in a] == [c.lesson for c in b]


# ---------------------------------------------------------------- T5b 可核性（防幻觉）
def test_grounding_flags_invented_numbers_and_paths():
    src = "方腔 Ra=1e6 时 Nu=6.98，相对误差 3.0%，见 docs/a.md"
    good = "- m1 方腔 Nu=6.98，误差 3.0%；docs/a.md"
    bad = "- m1 方腔 Nu=9.99，误差 3.0%；docs/ghost.md"
    assert ground_candidate("g1", good, src).ok is True
    miss = ground_candidate("g2", bad, src)
    assert miss.ok is False
    assert "9.99" in miss.missing and "docs/ghost.md" in miss.missing
    assert ground_candidate("g3", good, "").ok is False  # 无源=不可核


def test_ground_pool_marks_missing_source():
    cands = [
        {"candidate_id": "a", "source_ref": "mission:1", "lesson": "- m1 x 3.0% 92.1ms docs/a.md"},
        {"candidate_id": "b", "source_ref": "mission:9", "lesson": "- m9 y 1.0% 2.0ms docs/b.md"},
    ]
    out = ground_pool(cands, {"1": "3.0% 92.1ms docs/a.md x"})
    assert out["summary"]["candidates"] == 2
    assert out["summary"]["grounded_full"] == 1
    assert any("<源报告缺失>" in i["missing"] for i in out["items"])


# ---------------------------------------------------------------- T6 验收裁据
def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return str(path)


def test_acceptance_requires_both_conditions(tmp_path):
    import argparse

    good_metrics = _write(tmp_path / "m.json", {"pool": {"total": 12, "path": "p.jsonl"}})
    weak_metrics = _write(tmp_path / "m2.json", {"pool": {"total": 9, "path": "p.jsonl"}})
    good_sampling = _write(tmp_path / "s.json", {"adjudicated": 12, "pass_rate": 0.6667})
    bad_sampling = _write(tmp_path / "s2.json", {"adjudicated": 12, "pass_rate": 0.5})

    ok = cmd_acceptance(
        argparse.Namespace(metrics=good_metrics, sampling=good_sampling, out=str(tmp_path / "v1.json"))
    )
    assert ok["all_ok"] is True

    no_cands = cmd_acceptance(
        argparse.Namespace(metrics=weak_metrics, sampling=good_sampling, out=str(tmp_path / "v2.json"))
    )
    assert no_cands["C1_candidates_ge_10"]["ok"] is False
    assert no_cands["all_ok"] is False

    low_rate = cmd_acceptance(
        argparse.Namespace(metrics=good_metrics, sampling=bad_sampling, out=str(tmp_path / "v3.json"))
    )
    assert low_rate["C2_sample_pass_rate_ge_60pct"]["ok"] is False
    assert low_rate["all_ok"] is False


def test_cli_promote_writes_synced_copy(tmp_path):
    pool = str(tmp_path / "pool.jsonl")
    store = AppendOnlyStore(pool)
    store.append([_cand("c1", "m9001 新条目：误差 0.9%，92.1ms；docs/a.md")])
    boot = str(tmp_path / "boot.md")
    with open(boot, "w", encoding="utf-8") as f:
        f.write(BOOTSTRAP_MIN)
    out = str(tmp_path / "synced.md")
    rc = main(
        [
            "promote",
            "--pool", pool,
            "--bootstrap", boot,
            "--date-tag", "2026-11-07",
            "--out", out,
            "--audit-out", str(tmp_path / "audit.json"),
        ]
    )
    assert rc == 0
    synced = open(out, encoding="utf-8").read()
    assert synced.startswith(BOOTSTRAP_MIN.split("## 逃生规则")[0].rstrip("\n")[:40])
    assert open(boot, encoding="utf-8").read() == BOOTSTRAP_MIN  # 未 --apply 不写回
    assert json.load(open(tmp_path / "audit.json", encoding="utf-8"))["audit"]["appended"] == 1