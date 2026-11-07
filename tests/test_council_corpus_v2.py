"""IN28-N07 语料扩充（26 例）与 owner 一致率：语料自洽 + 统计判据。

覆盖：
- 语料硬校验（案例数/源路径/事实 regex 现取/证据原文/裁决原文）；
- 端到端回放：全部案例过裁决器，一致率 ≥2/3 且不可辩护不一致 == 0；
- 负例：把判据阈值改一改或把证据引用改一改，必须立刻变红（门能失败）；
- 协议不变：词表/超级多数/账本哈希口径指纹 == 已提交基线，且 core.py 相对基线 ref 无 diff。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from services.council_constitution import (  # noqa: E402
    VERDICTS,
    Adjudicator,
    acceptance_summary,
    compare_with_owner,
    load_charter,
    load_redline_lexicon,
)
from services.council_constitution.ledger_store import (  # noqa: E402
    GENESIS_HASH,
    SqlLedgerStore,
)

import in28_n07_corpus_build as corpus_build  # noqa: E402
import in28_n07_ledger_pg as replay  # noqa: E402

CHARTER = ROOT / "data/governance/charter_manifest.json"
LEXICON = ROOT / "data/governance/redline_lexicon.json"
CASES = ROOT / "data/governance/replay_cases.json"
BASELINE = ROOT / "data/governance/protocol_baseline.json"
CORE_REL = "backend/services/council_constitution/core.py"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def classifier():
    return load_redline_lexicon(LEXICON)


# --------------------------------------------------------------------------- #
# 一、语料自洽（硬校验）
# --------------------------------------------------------------------------- #
def test_corpus_build_check_has_no_problems() -> None:
    out, problems = corpus_build.build()
    assert problems == []
    assert len(out["cases"]) >= 20
    assert len({c["case_id"] for c in out["cases"]}) == len(out["cases"])


def test_every_case_carries_source_paths(corpus: dict, classifier) -> None:
    for case in corpus["cases"]:
        assert case["facts"], case["case_id"]
        for f in case["facts"]:
            assert (ROOT / f["source"]).exists(), (case["case_id"], f["source"])
        ref = ROOT / case["owner_ruling"]["source_ref"]
        assert ref.exists(), (case["case_id"], case["owner_ruling"]["source_ref"])
        assert case["owner_ruling"]["label"] in VERDICTS
        assert case["owner_ruling"]["verbatim"] in ref.read_text(encoding="utf-8", errors="replace")


def test_corpus_covers_multiple_dispute_classes(corpus: dict) -> None:
    labels = {c["owner_ruling"]["label"] for c in corpus["cases"]}
    # 正向控制（ADOPT）必须在册，否则判据可能只会说"失败"
    assert "ADOPT" in labels
    assert len(labels) >= 4
    assert len(corpus["cases"]) >= 20


def test_negative_control_broken_quote_is_rejected(corpus: dict, classifier) -> None:
    """把证据引用改成非原文 → 校验必须报错（门能失败）。"""
    bad = json.loads(json.dumps(corpus["cases"][3]))
    bad["evidence"][0]["quote"] = "这句引用不在文件里（负例）"
    problems = corpus_build.validate_case(bad, {c.rule_id for c in load_charter(CHARTER).clauses},
                                          classifier)
    assert any("证据引用非原文" in p for p in problems)


def test_negative_control_wrong_fact_value_is_rejected(corpus: dict, classifier) -> None:
    bad = json.loads(json.dumps(corpus["cases"][3]))
    bad["facts"][0]["expected"] = int(bad["facts"][0]["expected"]) + 1 if isinstance(
        bad["facts"][0]["expected"], int) else 999.0
    problems = corpus_build.validate_case(bad, {c.rule_id for c in load_charter(CHARTER).clauses},
                                          classifier)
    assert any("现取" in p for p in problems)


# --------------------------------------------------------------------------- #
# 二、端到端回放（不落盘、不接库，纯内存）
# --------------------------------------------------------------------------- #
def test_replay_all_cases_meets_agreement(corpus: dict, classifier) -> None:
    charter = load_charter(CHARTER)
    adjudicator = Adjudicator(charter, classifier)
    comparisons = []
    for case in corpus["cases"]:
        facts, _trace = replay.collect_facts(case, classifier)
        ballots = replay.make_ballots(case, facts, {})
        res = adjudicator.adjudicate(case, ballots, facts)
        assert res["verdict"] in VERDICTS
        # 红线案例不得自决
        if res["route"] == "redline_escalation":
            assert res["self_decision"] is False
            assert res["owner_packet"]["recommendation_is_binding"] is False
        else:
            assert res["criteria_results"], case["case_id"]
        comparisons.append(compare_with_owner(res, case))

    n = len(comparisons)
    agreements = sum(1 for c in comparisons if c["kind"] == "agreement")
    mismatches = [c for c in comparisons if c["kind"] == "mismatch"]
    assert mismatches == []
    assert agreements / n >= 2 / 3
    summary = acceptance_summary(comparisons, min_agreements=int(-(-2 * n // 3)))
    assert summary["passed"] is True


def test_replay_ledger_chain_reproducible(corpus: dict, classifier, tmp_path: Path) -> None:
    """回放写出的文件账本与库镜像逐字段一致（协议不变的端到端证据）。"""
    charter = load_charter(CHARTER)
    adjudicator = Adjudicator(charter, classifier)
    path = tmp_path / "ledger.jsonl"
    from services.council_constitution import Ledger
    ledger = Ledger(path)
    store = SqlLedgerStore(f"sqlite:///{tmp_path/'mirror.db'}")
    store.ensure_schema()
    for case in corpus["cases"][:5]:
        facts, _ = replay.collect_facts(case, classifier)
        res = adjudicator.adjudicate(case, replay.make_ballots(case, facts, {}), facts)
        entry = ledger.append({"case_id": case["case_id"], "verdict": res["verdict"],
                               "route": res["route"]})
        store.append_entry(entry)
    parity = store.parity(path)
    assert parity["ok"] is True
    assert parity["rows_db"] == parity["rows_file"] == 5
    assert parity["field_diff_count"] == 0
    store.close()


# --------------------------------------------------------------------------- #
# 三、协议不变（词表 / 超级多数 / 账本哈希口径）
# --------------------------------------------------------------------------- #
def test_protocol_fingerprint_matches_committed_baseline() -> None:
    import services.council_constitution.core as core_mod
    fp = replay.protocol_fingerprint(core_mod)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert fp["sha256"] == baseline["sha256"]
    snap = fp["snapshot"]
    assert snap["verdicts"] == ["ADOPT", "REJECT", "DOWNGRADE_TO_MOCK", "HONEST_FAIL",
                                "ESCALATE_TO_OWNER"]
    # 超级多数规则写死在源码里，改它必然改指纹
    assert "Fraction(2, 3)" in snap["adjudicator_init_src"]
    assert "supermajority" in snap["conclave_tally_src"]
    assert "threshold = self.supermajority if contested else Fraction(1, 2)" in snap["conclave_tally_src"]


def test_core_py_unchanged_vs_base_ref() -> None:
    ref = replay.resolve_base_ref()
    assert ref, "找不到含 core.py 的基线 ref"
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", ref, "--", CORE_REL])
    assert r.returncode == 0, f"{CORE_REL} 相对 {ref} 有改动（协议必须逐字节不变）"


def test_store_does_not_redefine_protocol() -> None:
    """库镜像模块不得自带哈希口径：必须复用 core.sha256_obj（同序列化）。"""
    from services.council_constitution import core, ledger_store
    assert ledger_store.sha256_obj is core.sha256_obj
    assert ledger_store.SCHEMA_LEDGER is core.SCHEMA_LEDGER
    assert ledger_store.GENESIS_HASH == "0" * 64 == GENESIS_HASH