"""knowledge_distill 新颖性门控测试（合成参考池，零域词）。

被测：``tools/knowledge_distill/novelty.py``
（参考池读取 / 三路相似度 / novelty 与平均相似度双约束 / 大规模池的两级检索）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.knowledge_distill.novelty import ReferenceCorpus, gate  # noqa: E402
from tools.knowledge_distill.params import DistillParams  # noqa: E402

DUP_TEXT = "报告模板缺审计字段，工件哈希与参数指纹都没有落点"


def _proposal(path: Path, items) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"proposals": items}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def test_corpus_reads_json_jsonl_and_markdown(tmp_path: Path) -> None:
    _proposal(tmp_path / "p" / "prop-1.json",
              [{"title": "报告模板缺审计字段", "why_frontier": "报告无法回溯输入工件与参数"}])
    (tmp_path / "p" / "batch.jsonl").write_text(
        json.dumps({"title": "任务队列支持优先级重排"}, ensure_ascii=False) + "\n" +
        "not-json-line\n", encoding="utf-8")
    (tmp_path / "p" / "notes.md").write_text(
        "# 说明\n\n配置变更缺灰度批次与回滚窗口。\n", encoding="utf-8")
    corpus = ReferenceCorpus(DistillParams())
    added = corpus.add_paths([tmp_path / "p"])
    assert added >= 3
    assert corpus.ensure_index() is corpus
    sources = {e["source_path"] for e in corpus.entries}
    assert any(s.endswith("prop-1.json") for s in sources)
    assert all(len(e["text"]) >= 8 for e in corpus.entries)


def test_corpus_skips_unreadable_and_unsupported(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "keep.bin").write_bytes(b"\x00\x01")
    corpus = ReferenceCorpus(DistillParams())
    assert corpus.add_paths([tmp_path]) == 0
    assert len(corpus) == 0
    empty = corpus.max_similarity("任意一句用于比对的长文本内容")
    assert empty["max_sim"] == 0.0 and empty["ref_count"] == 0


def test_gate_rejects_near_duplicate(tmp_path: Path) -> None:
    _proposal(tmp_path / "prop-1.json", [{"title": DUP_TEXT,
                                          "why_frontier": "报告缺审计字段导致无法回溯"}])
    corpus = ReferenceCorpus(DistillParams())
    corpus.add_paths([tmp_path])
    candidates = [
        {"seed_id": "s-dup", "novelty_text": DUP_TEXT},
        {"seed_id": "s-new", "novelty_text": "命令行工具缺 dry-run 开关，无法预演破坏性操作"},
    ]
    result = gate(candidates, corpus)
    verdict = {d["seed_id"]: d for d in result["decisions"]}
    assert verdict["s-dup"]["passed"] is False
    assert verdict["s-dup"]["novelty"] < 0.55
    assert verdict["s-dup"]["reasons"]
    assert verdict["s-new"]["passed"] is True
    assert result["passed"] == 1
    assert result["passed_ids"] == ["s-new"]


def test_gate_enforces_mean_similarity_ceiling(tmp_path: Path) -> None:
    """只卡最大值不够：均值超限（模板化的伪新颖）也要拒。"""
    items = [{"title": f"报告模板缺审计字段：第{i}版说明", "why_frontier":
              f"报告模板缺审计字段导致输入工件哈希无处记录，第{i}版"}
             for i in range(6)]
    _proposal(tmp_path / "prop-1.json", items)
    corpus = ReferenceCorpus(DistillParams())
    corpus.add_paths([tmp_path])
    ca = {"seed_id": "s-a", "novelty_text": "报告模板缺审计字段导致工件哈希无处记录，需补齐"}
    strict = gate([ca], corpus, DistillParams(mean_sim_max=0.01))
    loose = gate([ca], corpus, DistillParams(mean_sim_max=0.99, novelty_min=0.0))
    assert strict["passed"] == 0
    assert any("mean_sim" in r for r in strict["decisions"][0]["reasons"])
    assert loose["passed"] == 1
    assert strict["decisions"][0]["match_ref_id"] is not None
    assert len(strict["decisions"][0]["match_sha256"]) == 64


def test_gate_novelty_threshold_is_configurable(tmp_path: Path) -> None:
    _proposal(tmp_path / "prop-1.json", [{"title": "任务队列支持优先级重排",
                                          "why_frontier": "队列只有先进先出"}])
    corpus = ReferenceCorpus(DistillParams())
    corpus.add_paths([tmp_path])
    cand = {"seed_id": "s-b", "novelty_text": "任务队列缺优先级与超时重排，长作业会阻塞短作业"}
    strict = gate([cand], corpus, DistillParams(novelty_min=0.99))
    loose = gate([cand], corpus, DistillParams(novelty_min=0.0, mean_sim_max=1.0))
    assert strict["passed"] == 0 and loose["passed"] == 1


def test_gate_requires_top_k_for_large_pool(tmp_path: Path) -> None:
    _proposal(tmp_path / "prop-1.json",
              [{"title": f"无关提案条目编号{i}，描述一段独立的机制设想"} for i in range(2100)])
    corpus = ReferenceCorpus(DistillParams())
    corpus.add_paths([tmp_path])
    with pytest.raises(ValueError):
        corpus.max_similarity("任意候选文本", top_k=None)
    fast = corpus.max_similarity("任意候选文本")  # 默认两级检索
    assert fast["max_sim"] >= 0.0
    assert fast["exact_refs"] < 400


def test_two_tier_matches_full_scan_on_small_pool(tmp_path: Path) -> None:
    """两级检索在参考池小于 top_k 时必须与全量精确一致。"""
    _proposal(tmp_path / "prop-1.json", [
        {"title": DUP_TEXT, "why_frontier": "报告缺审计字段"},
        {"title": "任务队列支持优先级重排", "why_frontier": "队列只有先进先出"},
        {"title": "配置中心支持灰度批次与回滚窗口", "why_frontier": "配置全量下发"},
    ])
    corpus = ReferenceCorpus(DistillParams())
    corpus.add_paths([tmp_path])
    text = DUP_TEXT + " 报告模板缺审计字段导致哈希无处记录"
    assert corpus.max_similarity(text, top_k=64)["max_sim"] == \
        corpus.max_similarity(text, top_k=None)["max_sim"]