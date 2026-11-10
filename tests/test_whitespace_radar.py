"""Tests for the whitespace radar (pool coverage index + reproducible blank verdicts).

Run::

    python3 -m pytest tests/test_whitespace_radar.py -q

The suite is the acceptance harness of the node: (a) replay accuracy, (b) byte
identical reruns, (c) killer test (keyword removed from the index -> blank),
(d) domain-neutral code, (e) schema compatibility, (f) tri-state semantics on a
synthetic pool whose vocabulary is deliberately unrelated to any real domain.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core import (  # noqa: E402
    BLANK,
    COVERED,
    PARTIAL,
    Bm25Index,
    Doc,
    SCHEMA,
    Thresholds,
    analyze,
    check_compatible,
    dumps,
    load_bench,
    load_pool,
    passes_acceptance,
    pool_digest,
    replay,
    seal,
    verify,
)
from tools.whitespace_radar_core.bridge import GATE_SCHEMA, dedup_gate  # noqa: E402
from tools.whitespace_radar_core.pool_build import build_manifest, inventory_repo  # noqa: E402

POOL_MANIFEST = os.path.join(REPO_ROOT, "data/whitespace_radar/pool/manifest.json")
POOL_PIN = os.path.join(REPO_ROOT, "data/whitespace_radar/pool/pin.json")
BENCH = os.path.join(REPO_ROOT, "data/whitespace_radar/replay_bench.json")
NEUTRALITY = os.path.join(REPO_ROOT, "data/whitespace_radar/domain_terms.json")
CLI = os.path.join(REPO_ROOT, "tools/whitespace_radar.py")


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------
def synthetic_docs(n_filler: int = 60) -> list:
    """A pool with invented vocabulary (no real domain is involved).

    Filler docs make the fraction-based ``rare_frac`` behave as in production;
    three topical docs carry the ``alpha``/``beta``/``gamma`` concepts.
    """
    docs = [
        Doc(doc_id="doc:alpha", kind="doc", title="alpha gate",
            text="alpha 通道 预算 上限 复核 记录", ref="syn/alpha.md"),
        Doc(doc_id="doc:beta", kind="doc", title="beta index",
            text="beta 索引 倒排 词频 统计 记录", ref="syn/beta.md"),
        Doc(doc_id="doc:gamma", kind="doc", title="gamma audit",
            text="gamma 抽样 审计 记录 覆盖", ref="syn/gamma.md"),
    ]
    for i in range(n_filler):
        docs.append(
            Doc(
                doc_id=f"doc:filler{i:03d}",
                kind="doc",
                title=f"filler {i}",
                text=f"filler{i} payload record slot number{i} 填充 记录 编号{i}",
                ref=f"syn/filler{i}.md",
            )
        )
    return docs


@pytest.fixture(scope="module")
def pool():
    """The **pinned** pool: the replay labels were frozen on this document set.

    The live working tree keeps growing (docs/ is part of the pool), and unfrozen
    growth silently invalidates frozen labels — that defect is what the pin
    exists to prevent, so every replay here runs against the pinned snapshot.
    See ``tests/test_whitespace_specificity.py`` for the drift assertions.
    """
    from tools.whitespace_radar_core import load_pin

    pin = load_pin(POOL_PIN)
    docs = load_pool(REPO_ROOT, POOL_MANIFEST, pin=pin)
    return docs, Bm25Index.build(docs), pool_digest(docs)


@pytest.fixture(scope="module")
def bench():
    return load_bench(BENCH)


def scrub(docs, needles):
    """Return copies of ``docs`` with every ``needle`` removed from the text."""
    out = []
    for d in docs:
        text = d.text
        for needle in needles:
            text = text.replace(needle, "\u25a1" * len(needle))
        for needle in needles:  # titles are indexed too: scrub them as well
            d = Doc(d.doc_id, d.kind, d.title.replace(needle, "\u25a1" * len(needle)), text, d.ref)
            text = d.text
        out.append(d)
    return out


# --------------------------------------------------------------------------
# 1. tokenisation is orthographic only
# --------------------------------------------------------------------------
def test_tokenize_is_orthographic():
    from tools.whitespace_radar_core import terms, tokenize

    toks = tokenize("Alpha/BETA 样例文本")
    assert "alpha" in toks and "beta" in toks
    assert "样例" in toks and "例文" in toks  # 2-gram of a 4-char run
    assert tokenize("the of a") == []          # function words / 1-char latin dropped
    assert terms("文本 文本 文本") == ["文本"]  # unique, order preserving
    # same text -> same tokens (determinism of the tokeniser itself)
    assert tokenize("Alpha/BETA 样例文本") == tokenize("Alpha/BETA 样例文本")


# --------------------------------------------------------------------------
# 2. tri-state semantics on a synthetic pool
# --------------------------------------------------------------------------
def test_tri_state_on_synthetic_pool():
    idx = Bm25Index.build(synthetic_docs())
    th = Thresholds()
    covered = analyze(idx, "alpha 通道 预算", thresholds=th)
    partial = analyze(idx, "alpha 期望 钉扎 拓扑", thresholds=th)
    blank = analyze(idx, "zeta 磁通 钉扎 谐振", thresholds=th)
    assert covered["verdict"] == COVERED
    assert partial["verdict"] == PARTIAL
    assert blank["verdict"] == BLANK
    # the covered verdict must cite the document that actually contains it
    assert covered["hits"]["docs"][0]["doc_id"] == "doc:alpha"


def test_blank_refuses_undiscriminative_query():
    """All-generic queries must not be promoted to a blank verdict."""
    idx = Bm25Index.build(synthetic_docs())
    env = analyze(idx, "记录 索引", thresholds=Thresholds(rare_frac=0.0))
    assert env["verdict"] == PARTIAL
    assert env["query"]["n_specific"] == 0


# --------------------------------------------------------------------------
# 3. byte-identical rerun + tamper detection
# --------------------------------------------------------------------------
def test_rerun_is_byte_identical(pool):
    docs, idx, digest = pool
    a = analyze(idx, "引导回路 评价牵引 派发", pool_digest=digest, as_of="pool:x")
    b = analyze(idx, "引导回路 评价牵引 派发", pool_digest=digest, as_of="pool:x")
    assert dumps(a) == dumps(b)
    assert a["digest"]["sha256"] == b["digest"]["sha256"]
    assert verify(a)


def test_tamper_is_detected(pool):
    docs, idx, digest = pool
    env = analyze(idx, "引导回路 评价牵引 派发", pool_digest=digest, as_of="pool:x")
    env["verdict"] = COVERED if env["verdict"] != COVERED else BLANK
    assert not verify(env)


# --------------------------------------------------------------------------
# 4. schema compatibility gate (data interface with the arbitration de-dup gate)
# --------------------------------------------------------------------------
def test_schema_compatibility(pool):
    docs, idx, digest = pool
    env = analyze(idx, "alpha sample", pool_digest=digest, as_of="pool:x")
    assert env["schema"] == SCHEMA
    check_compatible(env)
    newer = seal({"verdict": "blank"}, schema=SCHEMA)
    newer["extra_field_from_future"] = {"x": 1}   # additive change: still readable
    check_compatible(newer)
    bad = dict(env)
    bad["schema"] = "radar/v2"
    with pytest.raises(ValueError):
        check_compatible(bad)


# --------------------------------------------------------------------------
# 5. blank evidence carries the executed query + zero-hit proof
# --------------------------------------------------------------------------
def test_blank_evidence_has_executed_query_and_zero_hits(pool):
    docs, idx, digest = pool
    env = analyze(idx, "超导磁体失超传播数值模拟", pool_digest=digest, as_of="pool:x")
    assert env["verdict"] == BLANK
    proof = env["zero_hit_proof"]
    assert proof["n_specific"] >= 1
    assert proof["n_existing_specific"] == 0
    assert proof["existence_hits"] == 0
    assert proof["specific_probes"], "every blank must list the probes it ran"
    for row in proof["specific_probes"]:
        assert row["df"] == 0
    assert set(proof["specific_probes"][0]) == {"probe", "type", "df"}
    assert "executed" in proof["statement"]
    assert env["query"]["probes"], "the query plan must be part of the evidence"


# --------------------------------------------------------------------------
# 6. killer test: remove the keyword from the index -> covered flips to blank
# --------------------------------------------------------------------------
def test_killer_index_scrub_flips_covered_to_blank(pool):
    docs, idx, digest = pool
    topic = "引导回路 评价牵引 派发 接线"
    before = analyze(idx, topic, pool_digest=digest, as_of="pool:x")
    assert before["verdict"] == COVERED
    needles = before["query"]["existing_specific_probes"]
    assert needles
    scrubbed = Bm25Index.build(scrub(docs, needles))
    after = analyze(scrubbed, topic, pool_digest="scrubbed", as_of="pool:scrub")
    assert after["verdict"] == BLANK
    assert after["zero_hit_proof"]["n_existing_specific"] == 0


def test_killer_query_drop_matches_scrub(pool):
    """Dropping the probes from the query must kill the positive evidence.

    If no discriminative probe survives the drop, the adjudicator returns
    ``partial`` (degenerate query, refuses to rule blank) rather than ``blank``;
    what must never survive is the ``covered`` verdict.
    """
    docs, idx, digest = pool
    topic = "引导回路 评价牵引 派发 接线"
    needles = analyze(idx, topic, pool_digest=digest, as_of="pool:x")["query"][
        "existing_specific_probes"
    ]
    after = analyze(idx, topic, pool_digest=digest, as_of="pool:x", drop_terms=needles)
    assert after["verdict"] != COVERED
    assert after["zero_hit_proof"]["existence_hits"] == 0
    assert after["query"]["dropped_terms"] == sorted(set(needles))


# --------------------------------------------------------------------------
# 7. replay benchmark acceptance (a) & (f)
# --------------------------------------------------------------------------
def test_replay_bench_acceptance(pool, bench):
    docs, idx, digest = pool
    report = replay(idx, bench, pool_digest=digest, as_of=f"pool:{digest[:16]}")
    assert report["n_topics"] == 18
    assert report["n_correct"] >= 16
    assert report["blank_false_positive"] == 0
    assert passes_acceptance(report)
    assert not report["mismatches"]


def test_verdict_monotonic_in_covered_threshold(pool, bench):
    """Raising the covered threshold must never turn a blank into covered."""
    docs, idx, digest = pool
    low = replay(idx, bench, thresholds=Thresholds(s_covered=2.0), pool_digest=digest)
    high = replay(idx, bench, thresholds=Thresholds(s_covered=30.0), pool_digest=digest)
    low_blank = {r["id"] for r in low["rows"] if r["verdict"] == BLANK}
    high_covered = {r["id"] for r in high["rows"] if r["verdict"] == COVERED}
    assert not (low_blank & high_covered)


# --------------------------------------------------------------------------
# 8. domain neutrality: the code carries no subject-matter vocabulary (d)
# --------------------------------------------------------------------------
def test_code_is_domain_neutral():
    spec = json.load(open(NEUTRALITY, encoding="utf-8"))
    hits = []
    for rel in spec["scope"]:
        path = os.path.join(REPO_ROOT, rel)
        assert os.path.isfile(path), rel
        text = open(path, encoding="utf-8").read()
        low = text.lower()
        for term in spec["terms"]:
            t = term.lower()
            # CJK has no word boundaries: substring. Latin: whole word only, so
            # that a short term does not fire inside a longer word.
            found = (t in low) if re.search(r"[^\x00-\x7f]", t) else re.search(rf"\b{re.escape(t)}\b", low)
            if found:
                hits.append((rel, term))
    assert hits == [], f"domain vocabulary leaked into code: {hits}"


# --------------------------------------------------------------------------
# 9. CLI end-to-end: deterministic file output + --verify
# --------------------------------------------------------------------------
def test_cli_is_deterministic_and_verifiable(tmp_path):
    outs = []
    for i in range(2):
        out = tmp_path / f"ev{i}.json"
        subprocess.run(
            [sys.executable, CLI, "--topic", "引导回路 评价牵引 派发", "--json-out", str(out),
             "--no-cache", "--quiet"],
            check=True, cwd=REPO_ROOT, capture_output=True,
        )
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    env = json.loads(outs[0])
    assert env["schema"] == SCHEMA and verify(env)
    res = subprocess.run(
        [sys.executable, CLI, "--verify", str(tmp_path / "ev0.json")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert res.returncode == 0 and "digest_ok=True" in res.stdout


# --------------------------------------------------------------------------
# 10. pool builders: multi-repo manifest, deterministic + pool digest
# --------------------------------------------------------------------------
def test_multi_repo_manifest_is_deterministic(tmp_path):
    for repo, mod in (("RepoA", "core_alpha"), ("RepoB", "core_beta")):
        d = tmp_path / repo / "pkg"
        d.mkdir(parents=True)
        (d / f"{mod}.py").write_text(f'"""module {mod} does {mod} things."""\n\n\ndef run():\n    return 1\n', encoding="utf-8")
    m1 = build_manifest([("RepoA", str(tmp_path / "RepoA")), ("RepoB", str(tmp_path / "RepoB"))])
    m2 = build_manifest([("RepoB", str(tmp_path / "RepoB")), ("RepoA", str(tmp_path / "RepoA"))])
    assert json.dumps(m1, sort_keys=True) == json.dumps(m2, sort_keys=True)
    assert [r["repo"] for r in m1["repos"]] == ["RepoA", "RepoB"]
    frag = inventory_repo("RepoA", str(tmp_path / "RepoA"))
    assert frag["modules"][0]["id"] == "pkg/core_alpha.py"
    assert "core_alpha" in frag["modules"][0]["text"]
    # include filter keeps one-off scripts out of the capability inventory
    (tmp_path / "RepoA" / "oneshots").mkdir()
    (tmp_path / "RepoA" / "oneshots" / "run_once.py").write_text("x = 1\n", encoding="utf-8")
    filt = inventory_repo("RepoA", str(tmp_path / "RepoA"), include=["pkg/"])
    assert [m["id"] for m in filt["modules"]] == ["pkg/core_alpha.py"]


def test_pool_digest_is_order_independent(pool):
    docs, idx, digest = pool
    assert pool_digest(list(reversed(docs))) == digest
    assert len({d.doc_id for d in docs}) == len(docs)


# --------------------------------------------------------------------------
# 11. sources adapters on a synthetic export (campaign nodes / proposals)
# --------------------------------------------------------------------------
def test_campaign_export_adapter(tmp_path):
    from tools.whitespace_radar_core import load_campaign_export

    export = {
        "schema": "radar-campaign/v1",
        "plans": [{"id": "P1", "title": "plan alpha", "goal": "do alpha things"}],
        "nodes": [{"plan_id": "P1", "node_id": "N1", "title": "node beta", "goal": "beta goal"}],
    }
    p = tmp_path / "campaign.json"
    p.write_text(json.dumps(export), encoding="utf-8")
    docs = load_campaign_export(str(p))
    assert [d.doc_id for d in docs] == ["campaign.node:P1/N1", "campaign.plan:P1"]
    assert all(d.text for d in docs)

# --------------------------------------------------------------------------
# 12. JSON-schema contracts (output v1 + input query v1) are committed and valid
# --------------------------------------------------------------------------
def test_evidence_matches_committed_schema(pool):
    jsonschema = pytest.importorskip("jsonschema")
    docs, idx, digest = pool
    schema = json.load(open(os.path.join(REPO_ROOT, "schemas/whitespace-radar.v1.schema.json")))
    for topic in ("引导回路 评价牵引 派发", "alpha 通道 预算", "地幔对流谱元法并行求解"):
        env = analyze(idx, topic, pool_digest=digest, as_of="pool:x")
        jsonschema.validate(env, schema)


def test_query_schema_rejects_unknown_major():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.load(open(os.path.join(REPO_ROOT, "schemas/whitespace-radar-query.v1.schema.json")))
    good = {"schema": "radar-query/v1", "topics": [{"id": "q1", "topic": "alpha beta"}]}
    jsonschema.validate(good, schema)
    bad = {"schema": "radar-query/v2", "topics": [{"topic": "alpha"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    empty = {"schema": "radar-query/v1", "topics": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(empty, schema)


# --------------------------------------------------------------------------
# 13. arbitration de-dup gate bridge
# --------------------------------------------------------------------------
def test_dedup_gate_bridge(pool):
    docs, idx, digest = pool
    cases = {
        "引导回路 评价牵引 派发": ("covered", "block_duplicate"),
        "提案去重门 跨组织 联邦学习 语义相似度": ("partial", "narrow_claim"),
        "地幔对流谱元法并行求解": ("blank", "allow"),
    }
    for topic, (verdict, decision) in cases.items():
        env = analyze(idx, topic, pool_digest=digest, as_of="pool:x")
        gate = dedup_gate(env)
        assert gate["schema"] == GATE_SCHEMA
        assert gate["verdict"] == verdict and gate["decision"] == decision
        assert gate["evidence_sha256"] == env["digest"]["sha256"]
        if verdict == "blank":
            assert gate["citations"] == []
            assert gate["zero_hit_proof"]["existence_hits"] == 0
            assert gate["zero_hit_proof"]["specific_probes"]
        else:
            assert gate["citations"], "a non-blank gate decision must cite documents"
            assert all(c["ref"] for c in gate["citations"])
        assert verify(gate)


def test_dedup_gate_rejects_non_verdict():
    with pytest.raises(ValueError):
        dedup_gate({"verdict": "maybe"})


# --------------------------------------------------------------------------
# 14. pool self-pollution guard: the radar must not find its own benchmark
# --------------------------------------------------------------------------
def test_pool_does_not_contain_bench_topics(pool, bench):
    """No indexed doc may quote a benchmark topic verbatim.

    Benchmark and tool documentation quote the topics they are about; indexing
    that text would let the radar find evidence it manufactured itself.  The
    pool manifest's ``exclude_globs`` is what prevents this, and this test is the
    tripwire if somebody removes it.
    """
    docs, idx, digest = pool
    offenders = []
    for item in bench["topics"]:
        needle = " ".join(item["topic"].lower().split())
        for d in docs:
            if needle in " ".join(f"{d.title}\n{d.text}".lower().split()):
                offenders.append((item["id"], d.doc_id))
    assert offenders == []


def test_pool_manifest_exclude_globs_work(tmp_path):
    import json as _json
    from tools.whitespace_radar_core import load_pool as _load

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("# keep alpha\n\nalpha body\n", encoding="utf-8")
    (tmp_path / "docs" / "selfquoted.md").write_text("# self beta\n\nbeta body\n", encoding="utf-8")
    manifest = {
        "schema": "radar-pool/v1",
        "exclude_globs": ["docs/selfquoted.md"],
        "sources": [{"type": "markdown", "kind_tag": "doc", "glob": "docs/*.md"}],
    }
    mp = tmp_path / "manifest.json"
    mp.write_text(_json.dumps(manifest), encoding="utf-8")
    docs = _load(str(tmp_path), str(mp))
    assert [d.ref for d in docs] == ["docs/keep.md"]
