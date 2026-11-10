"""``tools/maturity_inference`` 核心面用例（阶梯/证据/引擎/命名空间；合成键，零领域语义）。

覆盖判据：五环绑定要求的正例与反例、fail-closed 截断、矛盾封顶、自述不抬级、
命名空间门（语法/保留前缀/迁移去向）、确定性（同输入同输出）。
"""
from __future__ import annotations

import copy
import hashlib

import pytest

from tools.maturity_inference.engine import (
    CLAIM_NONE,
    CLAIM_UNBACKED,
    infer,
    infer_batch,
)
from tools.maturity_inference.evidence import (
    RUNG_REQUIREMENTS,
    build_node,
    validate_node,
)
from tools.maturity_inference.ladder import (
    LADDER,
    REAL_MIN_RANK,
    keeps_real,
    legacy_cap,
    legacy_to_ladder,
    level_of_rank,
    rank_of,
)
from tools.maturity_inference.namespace import (
    check_key,
    duplicates,
    migration_target,
    screen_rows,
)

TS = "2026-11-10T00:00:00+00:00"


def sha(tag: str) -> str:
    return hashlib.sha256(f"t::{tag}".encode()).hexdigest()


def node(rung_name: str, key: str = "synth.alpha.beta", **over):
    """构造一条合法证据节点；``over`` 用于故意破坏字段。"""
    rung = rung_name
    scope = over.pop("scope", "repo")
    base = dict(
        child={"implemented": "impl", "tested": "test", "benchmark_verified": "bench",
               "coupled_prototype": "e2e", "engineering_qualified": "qual"}[rung]
    )["child"]
    payload = build_node(
        key, rung, kind="synthetic", locator=f"synth/{base}.dat",
        artifact_sha256=sha(base), producer="pytest", recorded_at=TS, scope=scope,
    )
    if rank_of(rung) >= rank_of("tested"):
        payload["rerun"] = {"cmd": f"pytest -q synth/test_{base}.py", "status": "passed",
                            "executed_at": TS, "log_sha256": sha(base + "log")}
    if rank_of(rung) >= rank_of("benchmark_verified"):
        payload["criterion"] = {"name": "rel_err", "value": 0.01, "baseline_ref": 0.01,
                                "tolerance": 1e-6, "baseline": "analytic",
                                "satisfied": True, "unit": "-"}
    if rank_of(rung) >= rank_of("coupled_prototype"):
        payload["coupling"] = {"components": ["synth.left", "synth.right"],
                               "interface_digest": sha(base + "iface"),
                               "end_to_end_rerun": f"pytest -q synth/test_{base}_e2e.py"}
    if rank_of(rung) >= rank_of("engineering_qualified"):
        payload["qualification"] = {
            "reviewer": "seat-independent",
            "repeat_runs": [{"sha256": sha(base + "r1"), "verdict": "pass"},
                            {"sha256": sha(base + "r2"), "verdict": "pass"}],
            "envelope": "bounded", "signoff_sha256": sha(base + "sign")}
    payload.update(over)
    payload["provenance"] = {"producer": "pytest", "recorded_at": TS,
                             "sha256": sha(base + "prov" + rung)}
    return payload


def ladder_nodes(key: str = "synth.alpha.beta", upto: int = 5, scope: str = "repo"):
    return [node(LADDER[r], key, scope=scope) for r in range(1, upto + 1)]


# ---------------------------------------------------------------- ladder
def test_ladder_is_strictly_ordered():
    assert list(LADDER) == ["not_supported", "implemented", "tested",
                            "benchmark_verified", "coupled_prototype",
                            "engineering_qualified"]
    assert all(rank_of(LADDER[i]) < rank_of(LADDER[i + 1]) for i in range(len(LADDER) - 1))


def test_unknown_level_raises():
    with pytest.raises(KeyError):
        rank_of("bogus_level")


def test_legacy_cap_and_equivalence():
    assert legacy_cap("MOCK") == rank_of("implemented")
    assert legacy_cap("STUB") == 0
    assert legacy_cap(None) is None
    assert legacy_cap("NOT_A_GRADE") is None  # 未知档位不臆造惩罚
    assert legacy_to_ladder("REAL") == "benchmark_verified"
    assert level_of_rank(99) == LADDER[-1]
    assert level_of_rank(-3) == LADDER[0]


def test_only_benchmark_or_above_keeps_real():
    assert not keeps_real("tested")
    assert keeps_real("benchmark_verified")
    assert REAL_MIN_RANK == rank_of("benchmark_verified")


# ---------------------------------------------------------------- evidence
def test_rung_requirements_cover_all_ladder_rungs():
    assert set(RUNG_REQUIREMENTS) == set(LADDER[1:])


def test_valid_implemented_node_passes():
    missing, problems = validate_node(node("implemented"))
    assert (missing, problems) == ([], [])


def test_bad_sha_shape_is_rejected():
    payload = node("implemented")
    payload["source"]["artifact_sha256"] = "NOT-A-SHA"
    missing, problems = validate_node(payload)
    assert any("artifact_sha256" in p for p in problems)


def test_missing_provenance_is_fail_closed():
    payload = node("implemented")
    payload["provenance"] = {}
    missing, _ = validate_node(payload)
    assert "provenance.producer" in missing and "provenance.sha256" in missing


def test_failed_rerun_cannot_support_tested():
    payload = node("tested")
    payload["rerun"]["status"] = "failed"
    _, problems = validate_node(payload)
    assert any("rerun.status" in p for p in problems)


def test_benchmark_out_of_tolerance_is_rejected():
    payload = node("benchmark_verified")
    payload["criterion"].update({"value": 0.9, "baseline_ref": 0.1, "tolerance": 0.01})
    _, problems = validate_node(payload)
    assert any("越界" in p for p in problems)


def test_coupled_needs_two_components():
    payload = node("coupled_prototype")
    payload["coupling"]["components"] = ["only.one"]
    _, problems = validate_node(payload)
    assert any("components" in p for p in problems)


def test_engineering_needs_independent_repeats():
    payload = node("engineering_qualified")
    payload["qualification"]["repeat_runs"] = [
        {"sha256": sha("same"), "verdict": "pass"}, {"sha256": sha("same"), "verdict": "pass"}]
    _, problems = validate_node(payload)
    assert any("互异" in p for p in problems)


def test_rank_zero_not_a_node():
    payload = node("implemented")
    payload["rung"] = "not_supported"
    _, problems = validate_node(payload)
    assert problems


# ---------------------------------------------------------------- engine
def test_full_ladder_reaches_engineering_qualified():
    res = infer("synth.alpha.beta", ladder_nodes())
    # implemented 环的证据是源码 sha（不可"跑"），故可执行链 = 4 环（tested..engineering）
    assert res.level == "engineering_qualified" and res.executable_links == 4


def test_gap_breaks_ladder_fail_closed():
    nodes = [node("implemented"), node("benchmark_verified"),
             node("coupled_prototype"), node("engineering_qualified")]
    res = infer("synth.alpha.beta", nodes)
    assert res.level == "implemented" and res.break_rung == "tested"


def test_self_claim_never_raises_level():
    res = infer("synth.alpha.beta", [], claimed_level="REAL")
    assert res.level == "not_supported"
    assert res.claim_status == CLAIM_UNBACKED


def test_claim_absent_reports_none():
    assert infer("synth.alpha.beta", ladder_nodes()).claim_status == CLAIM_NONE


def test_contradiction_caps_level():
    res = infer("synth.alpha.beta", ladder_nodes(), claimed_level="REAL",
                contradictions=["MOCK"])
    assert res.level == "implemented"
    assert any("封顶" in r for r in res.reasons)


def test_contradiction_ceiling_not_lowered_when_above():
    res = infer("synth.alpha.beta", [node("implemented")], contradictions=["STUB"])
    # STUB → cap 0 低于 implemented → 压到 not_supported
    assert res.level == "not_supported"


def test_fixture_scope_caps_at_tested():
    res = infer("synth.alpha.beta", ladder_nodes(scope="fixture"), claimed_level="REAL")
    assert res.level == "tested"
    assert res.break_rung == "benchmark_verified"
    assert res.rung_support[2]["reason"] == "node_scope_capped"


def test_nodes_of_other_capabilities_ignored():
    nodes = ladder_nodes("synth.other.key")
    assert infer("synth.alpha.beta", nodes).level == "not_supported"


def test_inference_is_deterministic():
    nodes = ladder_nodes()
    a = infer("synth.alpha.beta", nodes).as_dict()
    b = infer("synth.alpha.beta", list(reversed(copy.deepcopy(nodes)))).as_dict()
    assert a == b


def test_evidence_chain_carries_rerun_commands():
    res = infer("synth.alpha.beta", ladder_nodes())
    chain = res.evidence_chain
    assert len(chain) == 5
    assert chain[0]["rerun_cmd"] is None  # implemented 环靠源码 sha，不靠命令
    assert all(step.get("rerun_cmd") for step in chain[1:])


def test_infer_batch_matches_single_calls():
    rows = [{"id": "synth.alpha.beta", "claimed_level": "REAL"},
            {"id": "synth.gamma.delta", "claimed_level": "MOCK"}]
    batch = infer_batch(rows, ladder_nodes())
    assert [r.capability_key for r in batch] == ["synth.alpha.beta", "synth.gamma.delta"]
    assert batch[0].level == "engineering_qualified"
    assert batch[1].level == "not_supported"


def test_unlinked_real_flag_definition():
    res = infer("synth.alpha.beta", [], claimed_level="REAL")
    assert res.unlinked_real is False  # 没保留 REAL，就不算 unlinked REAL
    fake = infer("synth.alpha.beta", ladder_nodes())
    assert fake.unlinked_real is False  # 有可执行链
    fake.executable_links = 0
    assert fake.unlinked_real is True


# ---------------------------------------------------------------- namespace
@pytest.mark.parametrize("key", ["synth.alpha.beta", "platform.evidence_grade.engine",
                                 "a.b.c.d_e.f0"])
def test_namespace_accepts_dotted_lowercase(key):
    assert check_key(key) == (True, "ok")


@pytest.mark.parametrize("key,reason", [
    ("node.p350w1_n01.undelivered", "reserved_prefix:node."),
    ("campaign.wave2.node1", "reserved_prefix:campaign."),
    ("delivery.branch.class", "reserved_prefix:delivery."),
    ("tmp.scratch.one", "reserved_prefix:tmp."),
    ("test.fixture.one", "reserved_prefix:test."),
])
def test_namespace_rejects_reserved_prefixes(key, reason):
    ok, got = check_key(key)
    assert not ok and got.startswith(reason)


@pytest.mark.parametrize("key", ["single", "Upper.case", "with space.key", "", None,
                                 "trailing.dot.", ".leading.dot", "double..dot"])
def test_namespace_rejects_bad_syntax(key):
    ok, _ = check_key(key)
    assert not ok


def test_migration_target_mapping():
    assert migration_target("node.x.undelivered") == "campaign_node_registry"
    assert migration_target("delivery.x.y") == "delivery_hygiene_ledger"
    assert migration_target("synth.alpha.beta") == ""


def test_screen_rows_splits_and_annotates():
    rows = [{"id": "synth.alpha.beta"}, {"id": "node.x.undelivered"},
            {"id": "Bad.Key"}]
    out = screen_rows(rows)
    assert [r["id"] for r in out["accepted"]] == ["synth.alpha.beta"]
    assert [r["rejected_key"] for r in out["rejected"]] == ["Bad.Key", "node.x.undelivered"]
    assert out["rejected"][1]["migration_target"] == "campaign_node_registry"


def test_duplicates_detects_repeats():
    assert duplicates(["a.b", "a.b", "c.d"]) == ["a.b"]
    assert duplicates(["a.b", "c.d"]) == []