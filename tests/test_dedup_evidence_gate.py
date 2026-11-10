"""去重证据复算门（p337w1-n03）行为测试：取证 → 篡改 → 复算判定，全脱库。

纪律：夹具全部用合成域名（foo/bar/widget 族的虚构词），不出现任何真实学科词；
域词扫描由 ``config/domain_word_denylist.json`` 提供词表（见
``test_gate_has_zero_domain_semantics``）。
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.proposal_gate.dedup_evidence import (  # noqa: E402
    R_COUNT,
    R_MALFORMED,
    R_MISSING,
    R_SCORE,
    R_SNAPSHOT_STALE,
    R_SOURCE,
    R_SPEC_COVERAGE,
    R_SPEC_TAMPERED,
    R_TAMPERED,
    R_TOPK,
    VERDICT_ACCEPT,
    VERDICT_REJECT,
    DbapiSource,
    DedupEvidence,
    EvidenceConfig,
    JsonSnapshotSource,
    MemorySource,
    QuerySpec,
    build_evidence,
    canonical_bytes,
    derive_terms,
    enforce_adoption,
    hashed_vector,
    sha256_hex,
    snapshot_fingerprint,
    specs_for_proposal,
    table_fingerprint,
    verify_evidence,
)

CONFIG_PATH = ROOT / "config" / "dedup_evidence.json"

# ── 合成语料（虚构词族，无任何真实域词）────────────────────────────────
FOO_ALPHA = (
    "widget foo pipeline: build a zorb-level quux ledger for bar entries, "
    "with a snarf gate that rejects ambiguous quux rows and records wobble metrics"
)
FOO_BETA = (
    "widget foo pipeline: build a zorb-level quux ledger for bar entries, with a snarf "
    "gate that rejects ambiguous quux rows and records wobble metrics plus a delta audit"
)
BAR_TEXT = "bar planner: schedule frobnicate batches and track glorp counters for each quux line"
BAZ_TEXT = "baz pool: collect snarf candidates, dedupe wobble entries, and report zorb summaries"


def _tables() -> dict[str, list[dict[str, object]]]:
    return {
        "campaign_nodes": [
            {"id": 11, "text": FOO_ALPHA},
            {"id": 12, "text": BAR_TEXT},
            {"id": 13, "text": BAZ_TEXT},
        ],
        "campaign_plans": [
            {"id": 21, "text": BAR_TEXT + " (plan level)"},
            {"id": 22, "text": "quux plan: frobnicate batch schedule for glorp counters"},
        ],
        "proposals": [
            {"id": 31, "text": BAZ_TEXT + " (proposal level)"},
            {"id": 32, "text": "zorb proposal: wobble ledger with snarf gate and quux audit"},
        ],
    }


@pytest.fixture()
def cfg() -> EvidenceConfig:
    return EvidenceConfig.load(CONFIG_PATH)


@pytest.fixture()
def source() -> MemorySource:
    return MemorySource(_tables())


@pytest.fixture()
def evidence(source, cfg):
    specs = specs_for_proposal(FOO_BETA, cfg, query_prefix="t", source=source)
    return build_evidence(source, specs, cfg)


# ── 基础机制 ───────────────────────────────────────────────────────────


def test_config_loads_and_declares_triple_retrieval(cfg):
    assert cfg.required_tables == ("campaign_nodes", "campaign_plans", "proposals")
    assert set(cfg.required_modes) <= {"ilike", "keyword_set", "vector"}
    assert cfg.fail_closed is True
    assert cfg.topk_min_jaccard == 0.8


def test_config_rejects_unknown_key(tmp_path):
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    doc["mystery_knob"] = 1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        EvidenceConfig.load(path)


def test_missing_config_path_is_fail_closed():
    with pytest.raises(ValueError):
        EvidenceConfig.load(None)


def test_table_fingerprint_is_stable_and_content_sensitive(source):
    rows = source.rows("campaign_nodes")
    fp1 = table_fingerprint("campaign_nodes", rows)
    fp2 = table_fingerprint("campaign_nodes", list(reversed(rows)))
    assert fp1 == fp2  # 行序无关
    assert fp1.row_count == 3 and fp1.max_id == "13"
    tampered = [dict(r) for r in rows]
    tampered[0]["text"] = FOO_ALPHA + "!"
    assert table_fingerprint("campaign_nodes", tampered).content_sha256 != fp1.content_sha256


def test_snapshot_fingerprint_covers_configured_tables(source, cfg):
    snap = snapshot_fingerprint(source, cfg)
    assert {fp.table for fp in snap.tables} == set(cfg.required_tables)
    assert snap.sha256 == snapshot_fingerprint(MemorySource(_tables()), cfg).sha256


def test_specs_are_serialisable_and_replayable(source, cfg):
    specs = specs_for_proposal(FOO_BETA, cfg, query_prefix="t", source=source)
    # 规格集 = 表 × 模式（每张必需表都被每种检索模式各查一遍，供逐表互证）
    assert len(specs) == len(cfg.required_tables) * len(cfg.required_modes)
    assert {s.table for s in specs} == set(cfg.required_tables)
    assert {s.mode for s in specs} == set(cfg.required_modes)
    for spec in specs:
        again = QuerySpec.from_dict(json.loads(json.dumps(spec.to_dict(), ensure_ascii=False)), cfg=cfg)
        assert again == spec and again.sha256() == spec.sha256()
        assert spec.terms and len(spec.terms) > 0
    ordered_pairs = [(s.table, s.mode) for s in specs]
    expected_pairs = [(t, m) for t in cfg.required_tables for m in cfg.required_modes]
    assert ordered_pairs == expected_pairs


def test_spec_rejects_free_text_or_unknown_mode(cfg):
    base = specs_for_proposal(FOO_BETA, cfg)[0].to_dict()
    with pytest.raises(ValueError):
        QuerySpec.from_dict({**base, "mode": "vibes"}, cfg=cfg)
    with pytest.raises(ValueError):
        QuerySpec.from_dict({**base, "terms": []}, cfg=cfg)
    with pytest.raises(ValueError):
        QuerySpec.from_dict({**base, "text": "too short"}, cfg=cfg)
    with pytest.raises(ValueError):
        QuerySpec.from_dict({**base, "extra": 1}, cfg=cfg)
    with pytest.raises(ValueError):
        QuerySpec.from_dict({**base, "table": "undeclared_table"}, cfg=cfg)


def test_hashed_vector_is_deterministic_across_processes():
    v1 = hashed_vector(FOO_ALPHA, 64)
    v2 = hashed_vector(FOO_ALPHA, 64)
    assert v1 == v2 and len(v1) == 64
    assert hashed_vector("completely different text", 64) != v1
    assert derive_terms(FOO_ALPHA, 5) == derive_terms(FOO_ALPHA, 5)


def test_canonical_bytes_is_order_insensitive():
    assert canonical_bytes({"a": 1, "b": [2, 3]}) == canonical_bytes({"b": [2, 3], "a": 1})
    assert sha256_hex({"a": 1}) == sha256_hex({"a": 1})


# ── 复算门正例 ─────────────────────────────────────────────────────────


def test_honest_evidence_is_accepted_or_deepened(evidence, source, cfg):
    result = verify_evidence(json.loads(evidence.to_json()), source, cfg)
    assert result.verdict in {VERDICT_ACCEPT, "DEEPEN"}
    assert result.snapshot_match is True
    assert all(c["count_ok"] and c["topk_ok"] and c["score_ok"] for c in result.checks)


def test_near_duplicate_turns_into_deepen_recommendation(source, cfg):
    # FOO_BETA 只在末尾多一句 → 与 FOO_ALPHA 高度相似 → 应给「深化/合并」建议而非放行新建
    specs = specs_for_proposal(FOO_ALPHA, cfg, query_prefix="near", source=source)
    ev = build_evidence(source, specs, cfg)
    result = verify_evidence(json.loads(ev.to_json()), source, cfg)
    assert result.verdict == "DEEPEN"
    assert result.recommendation and "DEEPEN_OR_MERGE" in result.recommendation


def test_verification_is_repeatable(evidence, source, cfg):
    payload = json.loads(evidence.to_json())
    first = verify_evidence(payload, source, cfg)
    second = verify_evidence(payload, source, cfg)
    assert first.verdict == second.verdict
    assert [c["live_hit_count"] for c in first.checks] == [c["live_hit_count"] for c in second.checks]
    assert [c["live_topk"] for c in first.checks] == [c["live_topk"] for c in second.checks]


# ── 复算门反例（fail-closed）──────────────────────────────────────────


def _rehashed(evidence, source, cfg, mutate) -> dict:
    doc = json.loads(evidence.to_json())
    mutate(doc)
    specs = tuple(QuerySpec.from_dict(s, cfg=cfg) for s in doc["specs"])
    outcomes = tuple(__import__("tools.proposal_gate.dedup_evidence", fromlist=["QueryOutcome"]).QueryOutcome.from_dict(o) for o in doc["outcomes"])
    snap = __import__("tools.proposal_gate.dedup_evidence", fromlist=["SnapshotFingerprint"]).SnapshotFingerprint.from_dict(doc["snapshot"])
    return DedupEvidence(snapshot=snap, specs=specs, outcomes=outcomes).to_dict()


def test_missing_evidence_rejected(cfg, source):
    assert verify_evidence(None, source, cfg).reason_codes == (R_MISSING,)
    proposal = enforce_adoption({"title": "no evidence attached"}, source, cfg)
    assert proposal.verdict == VERDICT_REJECT and R_MISSING in proposal.reason_codes


def test_free_text_evidence_rejected(cfg, source):
    result = verify_evidence("我查过了，没有重复", source, cfg)
    assert result.verdict == VERDICT_REJECT and R_MALFORMED in result.reason_codes


def test_edit_without_rehash_is_tampering(evidence, source, cfg):
    doc = json.loads(evidence.to_json())
    doc["outcomes"][0]["hit_count"] += 5
    result = verify_evidence(doc, source, cfg)
    assert result.verdict == VERDICT_REJECT and R_TAMPERED in result.reason_codes


def test_count_edit_with_rehash_is_count_mismatch(evidence, source, cfg):
    def mutate(doc):
        doc["outcomes"][1]["hit_count"] += 3
    result = verify_evidence(_rehashed(evidence, source, cfg, mutate), source, cfg)
    assert result.verdict == VERDICT_REJECT and R_COUNT in result.reason_codes


def test_neighbour_swap_is_topk_mismatch(evidence, source, cfg):
    def mutate(doc):
        outcome = doc["outcomes"][0]
        assert outcome["topk"], "夹具前提：该规格必须有近邻"
        outcome["topk"][0] = {"key": "campaign_nodes:999", "table": "campaign_nodes", "row_id": "999", "score": 0.5}
    doc = _rehashed(evidence, source, cfg, mutate)
    # 置换后需保证计数不变（只换 top-k 成员）
    result = verify_evidence(doc, source, cfg)
    assert result.verdict == VERDICT_REJECT
    assert R_TOPK in result.reason_codes


def test_score_edit_is_score_mismatch(evidence, source, cfg):
    def mutate(doc):
        hit = doc["outcomes"][0]["topk"][0]
        hit["score"] = float(hit["score"]) + 0.25
    result = verify_evidence(_rehashed(evidence, source, cfg, mutate), source, cfg)
    assert result.verdict == VERDICT_REJECT and R_SCORE in result.reason_codes


def test_forged_snapshot_fingerprint_rejected(evidence, source, cfg):
    def mutate(doc):
        doc["snapshot"]["tables"][0]["row_count"] += 1000
    result = verify_evidence(_rehashed(evidence, source, cfg, mutate), source, cfg)
    assert result.verdict == VERDICT_REJECT and R_SNAPSHOT_STALE in result.reason_codes


def test_stale_snapshot_rejected(cfg, source):
    smaller = MemorySource({k: list(v)[:1] for k, v in _tables().items()})
    old = build_evidence(smaller, specs_for_proposal(FOO_BETA, cfg, query_prefix="old", source=smaller), cfg)
    result = verify_evidence(json.loads(old.to_json()), source, cfg)
    assert result.verdict == VERDICT_REJECT and R_SNAPSHOT_STALE in result.reason_codes


def test_incomplete_triple_retrieval_rejected(evidence, source, cfg):
    def mutate(doc):
        doc["specs"] = doc["specs"][:2]
        doc["outcomes"] = doc["outcomes"][:2]
    result = verify_evidence(_rehashed(evidence, source, cfg, mutate), source, cfg)
    assert result.verdict == VERDICT_REJECT and R_SPEC_COVERAGE in result.reason_codes


def test_spec_tamper_rejected(evidence, source, cfg):
    def mutate(doc):
        doc["specs"][0]["terms"] = list(doc["specs"][0]["terms"])[:-1] or ["x"]
    result = verify_evidence(_rehashed(evidence, source, cfg, mutate), source, cfg)
    assert result.verdict == VERDICT_REJECT and R_SPEC_TAMPERED in result.reason_codes


def test_source_unreachable_is_fail_closed(evidence, cfg):
    class Broken:
        def rows(self, table):  # noqa: D401
            raise RuntimeError("db down")

        def statements(self):
            return ()

    result = verify_evidence(json.loads(evidence.to_json()), Broken(), cfg)
    assert result.verdict == VERDICT_REJECT and R_SOURCE in result.reason_codes


# ── 只读性 ────────────────────────────────────────────────────────────


def test_dbapi_source_is_read_only(tmp_path, cfg):
    db = tmp_path / "pool.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE campaign_nodes (id TEXT PRIMARY KEY, text TEXT)")
    conn.execute("CREATE TABLE campaign_plans (id TEXT PRIMARY KEY, text TEXT)")
    conn.execute("CREATE TABLE proposals (id TEXT PRIMARY KEY, text TEXT)")
    for table, rows in _tables().items():
        conn.executemany(f"INSERT INTO {table} (id, text) VALUES (?, ?)", [(str(r["id"]), r["text"]) for r in rows])
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db)
    src = DbapiSource(
        conn,
        {t: f"SELECT id, text FROM {t} ORDER BY id" for t in ("campaign_nodes", "campaign_plans", "proposals")},
    )
    before = snapshot_fingerprint(src, cfg)
    ev = build_evidence(src, specs_for_proposal(FOO_BETA, cfg, query_prefix="ro", source=src), cfg)
    result = verify_evidence(ev.to_dict(), src, cfg)
    after = snapshot_fingerprint(src, cfg)
    conn.close()

    assert result.verdict in {VERDICT_ACCEPT, "DEEPEN"}
    assert before.sha256 == after.sha256
    assert all(s.strip().upper().startswith("SELECT") for s in src.statements())


def test_dbapi_source_refuses_writes():
    with pytest.raises(ValueError):
        DbapiSource(object(), {"campaign_nodes": "DELETE FROM campaign_nodes"})


def test_json_snapshot_source_roundtrip(tmp_path, cfg, evidence):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"tables": _tables()}, ensure_ascii=False), encoding="utf-8")
    src = JsonSnapshotSource(path)
    result = verify_evidence(json.loads(evidence.to_json()), src, cfg)
    assert result.verdict in {VERDICT_ACCEPT, "DEEPEN"}


# ── 零领域语义 ────────────────────────────────────────────────────────


def test_gate_has_zero_domain_semantics():
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "tools/audit/domain_leak_scan.py",
            "--paths",
            "tools/proposal_gate/dedup_evidence.py",
            "tools/proposal_gate/dedup_evidence_cli.py",
            "tools/proposal_gate/dedup_evidence_collect.py",
            "tools/acceptance/dedup_evidence_gate.py",
            "tests/test_dedup_evidence_gate.py",
            "config/dedup_evidence.json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr