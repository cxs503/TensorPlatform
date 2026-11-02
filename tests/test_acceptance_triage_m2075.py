"""pytest for tools/acceptance_triage.py (mission m2075).

Acceptance criteria under test (all machine-checkable):
  * coverage: triaged entries == API ``total`` for awaiting_acceptance (delta 0)
  * every suggested_action is in ACTIONS and every reason is non-empty
  * idempotency: two runs produce byte-identical acceptance_queue.json
  * counterexamples: 3 synthetic missions -> revoke / verify / rerelease
  * read-only: no POST/PUT/PATCH/DELETE and no mutation path is ever requested
  * >= 8 tests
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.acceptance_triage import (  # noqa: E402
    ACTIONS,
    CachingClient,
    SnapshotTransport,
    build_snapshot,
    dumps_queue,
    fetch_all,
    match_criteria,
    parse_commit_shas,
    parse_report_ids,
    render_markdown,
    run_triage,
    run_triage_from,
    session_evidence,
    write_outputs,
)

COMMIT_GREEN = (
    "pytest tests/test_x.py -q -> 12 passed；commit 32249c3，工作树干净"
)


def mk_mission(
    mid: int,
    goal: str = "Implement thing. 验收：误差 <3%",
    repo: str = "TensorPlatform",
    session_id=None,
    agent_session_id=None,
    result=None,
    model=None,
    created_at: str = "2026-11-02T00:00:00+00:00",
) -> dict:
    return {
        "id": mid,
        "goal": goal,
        "repo": repo,
        "base_ref": "main",
        "co_repos": None,
        "parent_mission_id": None,
        "budget": {"max_tokens": 1},
        "runner": "native",
        "origin": "owner",
        "model": model,
        "agent_session_id": agent_session_id,
        "worker_role": None,
        "campaign_id": None,
        "campaign_phase": None,
        "status": "awaiting_acceptance",
        "claimed_by": None,
        "session_id": session_id,
        "result": result,
        "created_by": 1,
        "created_at": created_at,
    }


def mk_report(rid: int, mid: int, summary: str = "done", title: str = "report") -> dict:
    return {
        "id": rid,
        "title": title,
        "summary": summary,
        "scenario_key": f"m{mid}",
        "mission_id": mid,
        "stage": None,
        "tags": [],
        "figures": [],
        "status": "published",
        "created_by": 1,
        "created_at": "2026-11-02T01:00:00+00:00",
    }


class FakeTransport:
    """In-memory GET-only stand-in for HttpTransport (records every call)."""

    def __init__(self, missions, reports_by_mission, page_cap=None, markdowns=None,
                 report_details=None):
        self.missions = list(missions)
        self.reports = dict(reports_by_mission)
        self.page_cap = page_cap
        self.markdowns = dict(markdowns or {})
        # rid -> detail payload; ids absent here are "not found" (raise KeyError)
        self.report_details = dict(report_details or {})
        self.calls: list[dict] = []

    def get(self, path: str, **params):
        self.calls.append({"method": "GET", "path": path, "params": dict(params)})
        if path == "tensor/missions":
            limit = int(params.get("limit") or 200)
            if self.page_cap:
                limit = min(limit, self.page_cap)
            offset = int(params.get("offset") or 0)
            return {"items": self.missions[offset: offset + limit], "total": len(self.missions)}
        if path == "tensor/reports":
            mid = int(params.get("mission_id") or 0)
            items = self.reports.get(mid, [])
            return {"items": items, "total": len(items)}
        m = re.fullmatch(r"tensor/reports/(\d+)", path)
        if m:
            rid = int(m.group(1))
            if rid in self.report_details:
                detail = dict(self.report_details[rid])
                detail.setdefault("markdown", self.markdowns.get(rid, ""))
                return detail
            raise KeyError(f"report {rid} not found")
        raise KeyError(f"FakeTransport has no data for {path}")


def base_fleet():
    """Six missions spanning every action branch."""
    missions = [
        # 1: report + commit + criteria + green -> accept
        mk_mission(900, goal="验收：12 例全绿，pytest >=8 例", session_id=11,
                   result={"model": "qwen3.6-plus", "checks": COMMIT_GREEN}),
        # 2: report + commit, no green receipt -> verify
        mk_mission(901, goal="do stuff", session_id=12,
                   result={"model": "qwen3.6-plus", "checks": "commit abc1234 pushed"}),
        # 3: report but no commit sha -> rerelease
        mk_mission(902, goal="验收：误差 <3%", session_id=13,
                   result={"model": "deepseek-v4-flash", "checks": "did things"}),
        # 4: no report, no session -> revoke
        mk_mission(903, goal="自找活 batch", repo="ship-performance-platform"),
        # 5: session but no report -> rerelease
        mk_mission(904, goal="do stuff", session_id=14),
        # 6: no report, no session (second repo/model) -> revoke
        mk_mission(905, goal="自找活 batch", repo="ship-performance-platform",
                   model="MiniMax-M2.5-AWQ"),
    ]
    reports = {
        900: [mk_report(1000, 900, summary="commit 32249c3；12 passed")],
        901: [mk_report(1001, 901, summary="commit abc1234")],
        902: [mk_report(1002, 902)],
    }
    return missions, reports


# ---------------------------------------------------------------- 1. coverage
def test_coverage_equals_api_total_with_pagination():
    missions, reports = base_fleet()
    client = FakeTransport(missions, reports, page_cap=2)
    queue = run_triage(client)
    assert queue["entry_count"] == len(missions) == queue["mission_total_from_api"]
    assert queue["entry_count"] - queue["mission_total_from_api"] == 0
    assert queue["coverage_ok"] is True
    offsets = [c["params"].get("offset") for c in client.calls if c["path"] == "tensor/missions"]
    assert offsets == [0, 2, 4]


def test_long_fleet_coverage_154_like_pagination():
    missions = [mk_mission(1000 + i, goal="x 验收：误差 <3%") for i in range(154)]
    client = FakeTransport(missions, {}, page_cap=100)
    queue = run_triage(client)
    assert queue["entry_count"] == 154
    assert queue["mission_total_from_api"] == 154
    assert len({e["mission_id"] for e in queue["items"]}) == 154


# ------------------------------------------------- 2. action domain + reasons
def test_action_domain_and_reason_nonempty():
    missions, reports = base_fleet()
    queue = run_triage(FakeTransport(missions, reports))
    for entry in queue["items"]:
        assert entry["suggested_action"] in ACTIONS
        assert isinstance(entry["reason"], str) and entry["reason"].strip()
        assert len(entry["report_ids"]) == (1 if entry["has_report"] else 0)
        assert set(entry["commit_evidence"]) >= {"status", "shas", "sources", "green_receipt"}
        assert entry["commit_evidence"]["status"] in {"found", "unknown"}


# ---------------------------------------------------------- 3. idempotency
def test_idempotent_json_and_markdown_bytes(tmp_path):
    missions, reports = base_fleet()
    q1 = run_triage(FakeTransport(missions, reports))
    q2 = run_triage(FakeTransport(missions, reports))
    assert dumps_queue(q1) == dumps_queue(q2)
    assert render_markdown(q1) == render_markdown(q2)
    write_outputs(q1, tmp_path, figure=False)
    first = (tmp_path / "acceptance_queue.json").read_bytes()
    write_outputs(q2, tmp_path, figure=False)
    second = (tmp_path / "acceptance_queue.json").read_bytes()
    assert first == second


# ----------------------------------------- 4. counterexample triple (spec)
def test_counterexample_revoke_verify_rerelease():
    missions = [
        mk_mission(1, goal="no session no report"),
        mk_mission(
            2,
            goal="验收：误差 <3%",
            session_id=5,
            result={"checks": "commit deadbeef1，工作树干净"},
        ),
        mk_mission(3, goal="验收：误差 <3%", session_id=6, result={"checks": "done, no sha here"}),
    ]
    reports = {2: [mk_report(10, 2)], 3: [mk_report(11, 3)]}
    queue = run_triage(FakeTransport(missions, reports))
    by_id = {e["mission_id"]: e for e in queue["items"]}
    assert by_id[1]["suggested_action"] == "revoke"
    assert by_id[2]["suggested_action"] == "verify"
    assert by_id[3]["suggested_action"] == "rerelease"
    assert by_id[2]["commit_evidence"]["status"] == "found"
    assert by_id[3]["commit_evidence"]["status"] == "unknown"


# ------------------------------------------------------------- 5. read-only
def test_read_only_only_get_calls_and_no_mutation_paths():
    missions, reports = base_fleet()
    client = FakeTransport(missions, reports)
    run_triage(client)
    assert client.calls, "expected at least one request"
    for call in client.calls:
        assert call["method"] == "GET"
        assert re.fullmatch(r"tensor/(missions|reports|reports/\d+)", call["path"]), call
        assert call["path"] not in {
            "tensor/missions/claim", "tensor/missions/complete", "tensor/missions/start",
        }


def test_transport_exposes_no_write_verb():
    from tools.acceptance_triage import HttpTransport

    for verb in ("post", "put", "patch", "delete"):
        assert not hasattr(HttpTransport, verb)
    source = (ROOT / "tools" / "acceptance_triage.py").read_text(encoding="utf-8")
    # the only POST in the module is the credential exchange
    posts = [ln for ln in source.splitlines() if re.search(r"method=\"POST\"|'POST'", ln)]
    assert len(posts) == 1
    assert "/auth/login" in source


# ------------------------------------------------------------- 6. sha parsing
def test_commit_sha_parsing_positive_and_negative():
    assert parse_commit_shas("commit 32249c3，工作树干净") == ["32249c3"]
    assert parse_commit_shas("merge 9c1f0ab2d into main") == ["9c1f0ab2d"]
    assert parse_commit_shas("提交 abcdef1") == ["abcdef1"]
    # all-digit tokens are never shas
    assert parse_commit_shas("commit 1234567") == []
    # no cue word within 24 chars -> unknown, not a guess
    assert parse_commit_shas("the artifact hash 32249c3 sits in a file") == []
    # hex-ish english word is not a sha
    assert parse_commit_shas("commit defaced") == []
    # too short
    assert parse_commit_shas("commit abc123") == []


# ---------------------------------------------------------- 7. criteria rules
@pytest.mark.parametrize(
    "goal,expected",
    [
        ("验收：误差 <15% 不可接受", True),
        ("pytest >=8 例全绿", True),
        ("差值 0，断言", True),
        ("acceptance_queue.json 逐字节相同", True),
        ("覆盖率 >=90%", True),
        ("全量 1712 张任务中 awaiting_acceptance=154", False),
        ("2029–2058 共 30 张自找活单", False),
        ("请写一个分诊器，输出 json 和 md", False),
    ],
)
def test_criteria_computable_rules(goal, expected):
    assert bool(match_criteria(goal)) is expected


# ------------------------------------------------- 8. accept needs green copy
def test_accept_requires_green_receipt():
    green = mk_mission(
        500, goal="验收：pytest >=8 例全绿，误差 <3%", session_id=7,
        result={"checks": "pytest -q -> 12 passed；commit 32249c3"},
    )
    no_green = mk_mission(
        501, goal="验收：pytest >=8 例全绿，误差 <3%", session_id=8,
        result={"checks": "commit 32249c3, ran some commands"},
    )
    reports = {500: [mk_report(20, 500)], 501: [mk_report(21, 501)]}
    queue = run_triage(FakeTransport([green, no_green], reports))
    by_id = {e["mission_id"]: e for e in queue["items"]}
    assert by_id[500]["suggested_action"] == "accept"
    assert by_id[501]["suggested_action"] == "verify"


# ------------------------------------------------------------- 9. md ordering
def test_markdown_queue_ordered_verify_before_rerelease_revoke_last():
    missions, reports = base_fleet()
    queue = run_triage(FakeTransport(missions, reports))
    actions = [e["suggested_action"] for e in queue["items"]]
    order = {"accept": 0, "verify": 1, "rerelease": 2, "revoke": 3}
    assert actions == sorted(actions, key=lambda a: order[a])
    revoke_idx = [i for i, a in enumerate(actions) if a == "revoke"]
    verify_idx = [i for i, a in enumerate(actions) if a == "verify"]
    assert max(verify_idx) < min(revoke_idx)
    md = render_markdown(queue)
    assert md.index("**verify**") < md.index("**revoke**")
    assert "未执行任何裁决写入" in md


# ---------------------------------------------------- 10. distribution sanity
def test_summary_distribution_sums_to_total():
    missions, reports = base_fleet()
    queue = run_triage(FakeTransport(missions, reports))
    s = queue["summary"]
    assert sum(s["by_action"].values()) == s["total"] == len(missions)
    for action in ACTIONS:
        assert sum(s["by_action_repo"].get(action, {}).values()) == s["by_action"].get(action, 0)
        assert sum(s["by_action_model"].get(action, {}).values()) == s["by_action"].get(action, 0)
    assert s["with_session"] == sum(1 for m in missions if m["session_id"] or m["agent_session_id"])


# ------------------------------------------------------- 11. snapshot replay
def test_snapshot_replay_is_byte_identical():
    missions, reports = base_fleet()
    client = FakeTransport(missions, reports)
    fetched, total, reports_by_mission = fetch_all(client, page=200)
    queue = run_triage_from(
        fetched, total, reports_by_mission, client, fetch_markdown=False
    )
    snapshot = build_snapshot(
        queue, fetched, reports_by_mission, getattr(client, "details", {})
    )
    replay = run_triage(SnapshotTransport(snapshot), fetch_markdown=False)
    assert dumps_queue(replay) == dumps_queue(queue)
    assert replay["input_digest"] == queue["input_digest"]


# ------------------------------------------- 12. session evidence in result
def test_session_evidence_from_result_payload():
    """F1: DB columns are NULL but result carries the session UUID."""
    mission = mk_mission(
        700,
        session_id=None,
        result={"agent_session_id": "cadc5b3d-d817-4303-aef9-5e5f6bde355e",
                "checks": "did work"},
    )
    has, source = session_evidence(mission)
    assert has is True and source == "result:agent_session_id"
    nested = mk_mission(710, result={"evidence": {"summary": {"agent_session_id": "u-1"}}})
    assert session_evidence(nested) == (True, "result:evidence.summary.agent_session_id")
    assert session_evidence(mk_mission(711)) == (False, "none")

    # a mission with no report but with an embedded session is NOT an empty shell
    queue = run_triage(FakeTransport([mission], {}))
    entry = queue["items"][0]
    assert entry["has_session"] is True
    assert entry["session_source"] == "result:agent_session_id"
    assert entry["suggested_action"] == "rerelease"


# ------------------------------------- 13. report id mentioned in result text
def test_mentioned_report_promotes_has_report():
    mission = mk_mission(
        720,
        goal="no machine criterion here",
        result={"agent_session_id": "u-2",
                "checks": "平台报告 id=1653 published；pytest -q -> 7 passed；commit abc1234"},
    )
    detail = {"id": 1653, "mission_id": None, "title": "m720 report",
              "summary": "commit abc1234 shipped", "status": "published"}
    client = FakeTransport([mission], {}, report_details={1653: detail})
    entry = run_triage(client)["items"][0]
    assert entry["linked_report_ids"] == []
    assert entry["mentioned_report_ids"] == [1653]
    assert entry["report_link"] == "mentioned"
    assert entry["has_report"] is True
    assert entry["suggested_action"] == "verify"
    # unknown report ids must not be invented
    orphan = mk_mission(721, result={"checks": "平台报告 id=999999 published"})
    entry2 = run_triage(FakeTransport([orphan], {}))["items"][0]
    assert entry2["mentioned_report_ids"] == []
    assert entry2["has_report"] is False


def test_parse_report_ids_positive_and_negative():
    assert parse_report_ids("平台报告 id=1653 published") == [1653]
    assert parse_report_ids("报告 1642/1643") == []
    assert parse_report_ids("see /tensor/reports/1827") == [1827]
    assert parse_report_ids("no report at all") == []


def test_write_outputs_creates_json_and_md(tmp_path):
    missions, reports = base_fleet()
    queue = run_triage(FakeTransport(missions, reports))
    written = write_outputs(queue, tmp_path, figure=False)
    data = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
    md = Path(written["markdown"]).read_text(encoding="utf-8")
    assert data["entry_count"] == len(missions)
    assert "分诊分布" in md and "排序队列" in md
    assert data["rules"]["action_order"]["verify"] < data["rules"]["action_order"]["revoke"]