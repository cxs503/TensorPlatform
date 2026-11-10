"""语料推导测试：引用推断 / 派单时刻回放（禁止未来信息渗漏）/ 标签 / oracle 交叉核对。

夹具全部用合成任务号与中性路径，无领域词条。
"""

from __future__ import annotations

from tools.dispatch_contract.contract_schema import load_config
from tools.dispatch_contract.corpus import (
    build_corpus,
    infer_contract,
    infer_declared,
    normalise_path,
    oracle_input_unavailable,
)

CFG = load_config()
MARK = "CAMPAIGN-MARKER"


def _mission(mid, *, status="succeeded", created="2026-01-02T00:00:00Z",
             finished="2026-01-02T01:00:00Z", goal="", changed=()):
    return {
        "id": str(mid), "status": status, "repo": "repo-a",
        "created_at": created, "finished_at": finished, "goal": goal,
        "result": repr({"evidence": {"checks": {"gate": "ok", "fail_stage": None},
                                      "summary": {"changed_files": list(changed)}}}),
    }


def test_normalise_path_strips_worktree_prefix():
    assert normalise_path("/root/x/work/m1/artifacts/a.json") == "artifacts/a.json"
    assert normalise_path("docs/report.md") == "docs/report.md"


def test_infer_declared_splits_inputs_and_outputs():
    goal = f"{MARK} 见 mission 1001 的输出 artifacts/t1.json，产出 docs/report_m1001.md；判据 误差<3%"
    d = infer_declared(goal, "2002", CFG)
    assert "1001" in d["refs"]
    assert d["input_paths"] == ["artifacts/t1.json"]
    assert d["output_paths"] == ["docs/report_m1001.md"]
    assert d["n_criteria"] >= 1


def test_infer_declared_drops_self_reference():
    d = infer_declared("mission 2002 自我引用", "2002", CFG)
    assert "2002" not in d["refs"]


def test_infer_contract_none_when_no_inputs():
    d = infer_declared(f"{MARK} 无依赖", "3003", CFG)
    assert infer_contract(d, "3003", CFG) is None


def test_infer_contract_builds_requirements():
    d = infer_declared("上游 mission 4004", "5005", CFG)
    c = infer_contract(d, "5005", CFG)
    assert c["contract_version"] == "dispatch-contract/v1"
    assert c["input_contract"][0] == {"kind": "upstream_delivery", "ref": "4004",
                                      "required": True, "note": "任务书引用上游任务号"}


def test_build_corpus_replays_dispatch_time_snapshot():
    """派单时刻之后才完成的交付不得被视为可得（no look-ahead）。"""
    missions = [
        _mission(1001, status="succeeded", finished="2026-01-05T00:00:00Z",
                 changed=["artifacts/t1.json"]),
        _mission(2002, status="succeeded", created="2026-01-03T00:00:00Z",
                 finished="2026-01-03T02:00:00Z",
                 goal=f"{MARK} 用 mission 1001 + artifacts/t1.json"),
        _mission(2003, status="succeeded", created="2026-01-06T00:00:00Z",
                 finished="2026-01-06T02:00:00Z",
                 goal=f"{MARK} 用 artifacts/t1.json"),
    ]
    records, stats = build_corpus(missions, CFG, population_marker=MARK)
    by = {r["mission_id"]: r for r in records}
    early = by["2002"]["dispatch"]["upstream"]
    late = by["2003"]["dispatch"]["upstream"]
    assert early["1001"]["delivered_before_dispatch"] is False   # 上游 01-05 才完成
    assert early["artifacts/t1.json"]["delivered_before_dispatch"] is False
    assert late["artifacts/t1.json"]["delivered_before_dispatch"] is True
    assert stats["n_population"] == 2 and stats["n_terminal"] == 2  # 上游 1001 自身不在种群内


def test_build_corpus_labels_and_degradation():
    missions = [
        _mission(1001, status="failed", goal=f"{MARK} 独立任务", changed=[]),
        _mission(2002, status="succeeded", goal=f"{MARK} 用 mission 3003", changed=["docs/a.md"]),
        _mission(3003, status="failed", goal=f"{MARK} 独立任务"),
    ]
    records, stats = build_corpus(missions, CFG, population_marker=MARK)
    by = {r["mission_id"]: r for r in records}
    assert by["1001"]["label_blocked"] == 1
    assert by["1001"]["label_non_delivery"] == 1
    assert by["2002"]["label_blocked"] == 0
    assert by["2002"]["dispatch"]["contract"] is not None
    assert by["1001"]["dispatch"]["contract"] is None
    assert by["1001"]["label_input_unavailable"] is None   # 无契约 → 不做可满足性断言
    assert stats["n_degraded"] == 2 and stats["n_with_contract"] == 1


def test_non_terminal_excluded():
    missions = [_mission(9, status="queued", goal=f"{MARK} x"),
                _mission(10, status="running", goal=f"{MARK} y")]
    records, stats = build_corpus(missions, CFG, population_marker=MARK)
    assert records == [] and stats["n_terminal"] == 0


def test_oracle_is_independent_and_agrees_on_facts():
    missions = [
        _mission(1001, status="succeeded", finished="2026-01-01T00:00:00Z",
                 changed=["artifacts/t1.json"]),
        _mission(2002, status="failed", created="2026-01-02T00:00:00Z",
                 goal=f"{MARK} 用 mission 1001 + artifacts/t1.json"),
        _mission(2003, status="failed", created="2026-01-02T00:00:00Z",
                 goal=f"{MARK} 用 mission 9999 + artifacts/none.json"),
    ]
    records, _ = build_corpus(missions, CFG, population_marker=MARK)
    by = {r["mission_id"]: r for r in records}
    assert by["2002"]["label_input_unavailable"] == 0      # 两项输入均可得
    assert by["2003"]["label_input_unavailable"] == 1      # 上游不存在 + 工件不存在
    why = {u["ref"]: u["why"] for u in by["2003"]["oracle_unavailable"]}
    assert why["9999"] == "NOT_FOUND"
    assert why["artifacts/none.json"] == "NO_PRODUCER_BEFORE_DISPATCH"


def test_oracle_flags_late_delivery():
    missions = [
        _mission(1001, status="succeeded", finished="2026-02-01T00:00:00Z"),
        _mission(2002, status="failed", created="2026-01-02T00:00:00Z",
                 goal=f"{MARK} 用 mission 1001"),
    ]
    records, _ = build_corpus(missions, CFG, population_marker=MARK)
    by = {r["mission_id"]: r for r in records}
    why = {u["ref"]: u["why"] for u in by["2002"]["oracle_unavailable"]}
    assert why["1001"] == "LATE"


def test_oracle_helper_direct_call():
    declared = {"refs": ["55"], "input_paths": ["artifacts/z.json"]}
    table = [{"id": "55", "status": "succeeded", "ok_status": True,
              "fin": "2026-01-01T00:00:00Z", "paths": {"artifacts/z.json"}}]
    by_id = {"55": {"id": "55", "status": "succeeded", "finished_at": "2026-01-01T00:00:00Z"}}
    assert oracle_input_unavailable(declared, by_id, table, "2026-01-02T00:00:00Z", CFG) == []
    out = oracle_input_unavailable(declared, by_id, table, "2025-12-31T00:00:00Z", CFG)
    assert {u["why"] for u in out} == {"LATE", "NO_PRODUCER_BEFORE_DISPATCH"}