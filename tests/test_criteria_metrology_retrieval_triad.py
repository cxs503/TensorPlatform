"""检索凭证重放门 + 负空间图谱 + 能力三元台账测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.criteria_metrology.retrieval import (
    FileTermIndex,
    SnapshotIndex,
    build_receipt,
    claim_gate,
    negative_space_map,
    replay_receipt,
)
from tools.criteria_metrology.triad import (
    CapabilityEntry,
    Delivery,
    Evidence,
    triad_validate,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "criteria_metrology"


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("term_alpha = 1\nother = 2\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("term_alpha += 1\n", encoding="utf-8")
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "c.py").write_text("term_alpha = 3\n", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# 凭证与重放
# --------------------------------------------------------------------------- #


def test_receipt_digest_is_content_addressed():
    a = build_receipt(claim_id="c", kind="code", query=["x"], scope=["p"], hits=["p/a.py:1"])
    b = build_receipt(claim_id="c", kind="code", query=["x"], scope=["p"], hits=["p/a.py:1"])
    c = build_receipt(claim_id="c", kind="code", query=["x"], scope=["p"], hits=["p/a.py:2"])
    assert a.digest == b.digest
    assert a.digest != c.digest


def test_replay_match_drift_and_unreplayable(corpus: Path):
    idx = FileTermIndex(corpus)
    rec = build_receipt(claim_id="c", kind="code", query=["term_alpha"], scope=["pkg"], hits=["pkg/a.py:1", "pkg/b.py:1"])
    res = replay_receipt(rec, {"code": idx})
    assert res.status == "MATCH" and res.ok

    stale = build_receipt(claim_id="c", kind="code", query=["term_alpha"], scope=["pkg"], hits=["pkg/a.py:1"])
    res2 = replay_receipt(stale, {"code": idx})
    assert res2.status == "DRIFT"
    assert any("b.py" in h for h in res2.added)

    gone = build_receipt(claim_id="c", kind="code", query=["term_alpha"], scope=["nope"], hits=[])
    assert replay_receipt(gone, {"code": idx}).status == "UNREPLAYABLE"
    assert replay_receipt(rec, {}).status == "UNREPLAYABLE"


def test_receipt_exclusion_prevents_self_proof(corpus: Path):
    """主张"除本模块外无实现"时，凭证必须带 exclude，否则重放会把自己的实现算成先例。"""
    idx = FileTermIndex(corpus)
    with_self = build_receipt(claim_id="c", kind="code", query=["term_alpha"], scope=["pkg", "self"], hits=["pkg/a.py:1", "pkg/b.py:1"])
    assert replay_receipt(with_self, {"code": idx}).status == "DRIFT"
    excluded = build_receipt(
        claim_id="c",
        kind="code",
        query=["term_alpha"],
        scope=["pkg", "self"],
        hits=["pkg/a.py:1", "pkg/b.py:1"],
        exclude=["self"],
    )
    assert replay_receipt(excluded, {"code": idx}).status == "MATCH"


def test_claim_gate_verdicts(corpus: Path):
    idx = FileTermIndex(corpus)
    nov_empty = build_receipt(claim_id="n", kind="code", query=["absent_term"], scope=["pkg"], hits=[])
    g = claim_gate({"claim_id": "n", "claim_kind": "novelty"}, [nov_empty], {"code": idx})
    assert g["verdict"] == "SUPPORTED"

    nov_hit = build_receipt(claim_id="n2", kind="code", query=["term_alpha"], scope=["pkg"], hits=["pkg/a.py:1", "pkg/b.py:1"])
    g2 = claim_gate({"claim_id": "n2", "claim_kind": "novelty"}, [nov_hit], {"code": idx})
    assert g2["verdict"] == "REFUTED"

    cov_empty = build_receipt(claim_id="c3", kind="code", query=["absent_term"], scope=["pkg"], hits=[])
    g3 = claim_gate({"claim_id": "c3", "claim_kind": "coverage"}, [cov_empty], {"code": idx})
    assert g3["verdict"] == "REFUTED"

    g4 = claim_gate({"claim_id": "c4", "claim_kind": "coverage"}, [nov_hit], {"code": idx})
    assert g4["verdict"] == "SUPPORTED"

    unrep = build_receipt(claim_id="c5", kind="pool", query=["x"], scope=["pool"], hits=[])
    g5 = claim_gate({"claim_id": "c5", "claim_kind": "novelty"}, [unrep], {"code": idx})
    assert g5["verdict"] == "UNREPLAYABLE"


def test_snapshot_index_reports_missing_key():
    snap = {"entries": {}, "digest": "d", "version": 1}
    idx = SnapshotIndex(snap, kind="node")
    rec = build_receipt(claim_id="c", kind="node", query=["q"], scope=["s"], hits=[])
    res = replay_receipt(rec, {"node": idx})
    assert res.status == "UNREPLAYABLE"


# --------------------------------------------------------------------------- #
# 负空间图谱
# --------------------------------------------------------------------------- #


def test_negative_space_counts_unprobed_terms(corpus: Path):
    idx = FileTermIndex(corpus)
    receipts = [
        build_receipt(claim_id="c", kind="code", query=["term_alpha"], scope=["pkg"], hits=["pkg/a.py:1", "pkg/b.py:1"]),
        build_receipt(claim_id="c", kind="code", query=["absent_term"], scope=["pkg"], hits=[]),
    ]
    regions = {"r1": ["term_alpha", "absent_term", "never_searched"]}
    m = negative_space_map(regions, receipts, resolvers={"code": idx})
    reg = m["regions"]["r1"]
    assert reg["n_declared"] == 3
    assert reg["n_searched"] == 2
    assert reg["n_covered"] == 1
    assert reg["unprobed_terms"] == ["never_searched"]
    assert m["probe_ratio"] == pytest.approx(2 / 3)


# --------------------------------------------------------------------------- #
# 能力三元台账
# --------------------------------------------------------------------------- #


def _entry(**kw) -> CapabilityEntry:
    kw.setdefault("statement", "does something checkable")
    kw.setdefault("grade", "REAL")
    return CapabilityEntry(**kw)


def test_real_requires_delivery_and_strong_evidence(tmp_path: Path):
    (tmp_path / "m.py").write_text("x=1", encoding="utf-8")
    ok = _entry(
        capability_id="ok",
        deliveries=(Delivery(delivery_id="d", kind="module", path="m.py"),),
        evidence=(Evidence(evidence_id="e", kind="test", ref="tests/t.py"),),
    )
    res = triad_validate([ok], root=tmp_path)
    assert res["n_violations"] == 0

    weak = _entry(
        capability_id="weak",
        deliveries=(Delivery(delivery_id="d", kind="module", path="m.py"),),
        evidence=(Evidence(evidence_id="e", kind="manual-review", ref="x"),),
    )
    res2 = triad_validate([weak], root=tmp_path)
    assert any(v["problem"] == "REAL_without_strong_evidence" for v in res2["violations"])


def test_missing_delivery_path_is_a_violation(tmp_path: Path):
    e = _entry(
        capability_id="ghost",
        deliveries=(Delivery(delivery_id="d", kind="module", path="nope.py"),),
        evidence=(Evidence(evidence_id="e", kind="test", ref="t"),),
    )
    res = triad_validate([e], root=tmp_path)
    assert any(v["problem"].startswith("delivery_path_missing") for v in res["violations"])


def test_not_supported_must_be_explicit_and_bare(tmp_path: Path):
    good = _entry(capability_id="ns", grade="NOT_SUPPORTED", reason="out of scope", deliveries=(), evidence=())
    assert triad_validate([good], root=tmp_path)["n_violations"] == 0

    silent = _entry(capability_id="ns2", grade="NOT_SUPPORTED", reason="", deliveries=(), evidence=())
    assert any(v["problem"] == "NOT_SUPPORTED_without_reason" for v in triad_validate([silent], root=tmp_path)["violations"])

    pretending = _entry(
        capability_id="ns3",
        grade="NOT_SUPPORTED",
        reason="r",
        deliveries=(Delivery(delivery_id="d", kind="module", path="m.py"),),
    )
    assert any(
        v["problem"] == "NOT_SUPPORTED_with_deliveries" for v in triad_validate([pretending], root=tmp_path)["violations"]
    )


def test_committed_triad_ledger_is_clean():
    from tools.criteria_metrology.triad import ledger_from_json

    entries = ledger_from_json(DATA / "capability_triad.json")
    res = triad_validate(entries, root=Path(__file__).resolve().parents[1])
    # 交付物路径可能尚未产出（artifacts 由流水线生成）时只要求结构约束不违规
    structural = [v for v in res["violations"] if not v["problem"].startswith(("delivery_path_missing", "delivery_digest_mismatch"))]
    assert structural == []
    assert res["grade_counts"]["NOT_SUPPORTED"] >= 3