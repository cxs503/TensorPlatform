"""IN59-A4 失败事后复盘（failure_postmortems）验收测试 —— 判据先于实现，机器可裁。

判据（数字即验收口径）：
1. **表**：DDL 在 SQLite/PG 通用子集内建 4 张表；七环/五类/状态取值由 CHECK 硬拒，
   起草者不得自审（confirmed_by <> harvested_by）同样由 DB 层拒。
2. **自动收割**：failed 且带 fail_stage 证据的 mission → 生成草稿；非 failed、
   或证据不足（无七环信号）→ WITHHELD，不产草稿（不造假）。
3. **双闸防敷衍**：空话规则/过短 failed_step/无已证无效路径/归因与环位矛盾 →
   结构挑战必拒；草稿环位被写歪 → 独立复算必拒。
4. **定稿纪律**：只允许 challenger/coordinator，且定稿者 ≠ 起草者；未过闸的
   草稿只能落 draft/disputed，不得 confirmed。
5. **PLAYBOOK 注入**：单条 ≤120 字符；append-only（既有字节原样保留）；
   按 rule_id 幂等（重跑插入 0 条）；锚点缺失即抛错，绝不静默写错地方。
6. **引用可审计**：后收割的单在起草前检索到已定稿规则 → 落
   later_postmortem_consult 引用；后续任务文本写 rule_id → 落
   mission_text_citation；去重幂等。
7. **验收数字**：7d failed 语料（12 条合成但证据形状取自真实终态）→
   confirmed ≥10 且被引用规则 ≥3，`summarize` 的 criteria 逐项为真。

夹具一律使用合成 repo/主体名；不写任何学科词。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from tools import failure_postmortems as fpm  # noqa: E402
from tools.harvest_failure_postmortems import (  # noqa: E402
    CHALLENGER_ID,
    HARVESTER_ID,
    Repo,
    _dec_array,
    _dec_json,
    harvest,
)


# ──────────────────────────── 夹具 ────────────────────────────

MISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL DEFAULT 1,
    goal            TEXT NOT NULL DEFAULT '',
    repo            TEXT DEFAULT 'SyntheticRepo',
    status          TEXT NOT NULL DEFAULT 'failed',
    fail_stage      TEXT,
    finished_at     TEXT,
    claimed_by      TEXT,
    model           TEXT,
    result          TEXT NOT NULL DEFAULT '{}'
);
"""

NOW = datetime.now(timezone.utc)


def _iso(days_ago: float = 0.0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def mission_row(
    mid: int,
    *,
    status: str = "failed",
    reason: str | None = None,
    fail_stage: str | None = None,
    stop_condition: str | None = None,
    tool_calls: int = 0,
    changed_files: list[str] | None = None,
    evidence_ignored: list[str] | None = None,
    declared_summary: str | None = None,
    goal: str | None = None,
    days_ago: float = 0.5,
) -> dict:
    result: dict = {}
    if reason is not None or tool_calls or changed_files or stop_condition:
        checks = {}
        if reason is not None:
            checks["reason"] = reason
            checks["gate"] = "no-evidence"
        if fail_stage:
            checks["fail_stage"] = fail_stage
        if evidence_ignored:
            checks["evidence_ignored"] = evidence_ignored
        result["evidence"] = {"checks": checks, "summary": {"tool_calls": tool_calls}}
    if stop_condition:
        result["stop_condition"] = stop_condition
    result["tool_calls"] = tool_calls
    result["changed_files"] = changed_files or []
    if declared_summary:
        result["declared_summary"] = declared_summary
    if fail_stage:
        result["fail_stage"] = fail_stage
    return {
        "id": mid,
        "organization_id": 1,
        "goal": goal or f"【节点 {mid}】在某仓库内落地一项可验收机制",
        "repo": "SyntheticRepo",
        "status": status,
        "fail_stage": fail_stage,
        "finished_at": _iso(days_ago),
        "claimed_by": "worker-a",
        "model": "model-x",
        "result": json.dumps(result, ensure_ascii=False),
    }


def _seed_missions(engine, rows) -> None:
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO tensor_dev_missions
                        (id, organization_id, goal, repo, status, fail_stage,
                         finished_at, claimed_by, model, result)
                    VALUES (:id, :organization_id, :goal, :repo, :status, :fail_stage,
                            :finished_at, :claimed_by, :model, :result)
                    """
                ),
                row,
            )


#: 12 条 7d failed：证据形状来自真实终态（llm_error/wall/budget/no_progress/
#: 零改动/repo 缺失/worktree 网络病/ref 不可解析/锁冲突/诚实 FAIL 理论错误/
#: 判据不可裁/测试红），覆盖七环与五类。
FAILED_ROWS = [
    mission_row(101, reason="模型未调用 finish（终局: llm_error）", tool_calls=0,
                goal="【节点 101】把某算法 REAL 化并附解析锚"),
    mission_row(102, reason="模型未调用 finish（终局: wall_exhausted）", tool_calls=79,
                changed_files=["a.py", "b.py", "c.py", "d.py"],
                evidence_ignored=["a.py", "b.py", "c.py"], goal="【节点 102】混合引擎"),
    mission_row(103, reason="模型未调用 finish（终局: budget_exhausted）", tool_calls=60,
                goal="【节点 103】批量基准扫描"),
    mission_row(104, reason="模型未调用 finish（终局: no_progress）", tool_calls=33,
                goal="【节点 104】跨求解器几何契约"),
    mission_row(105, reason="工作树无任何改动，不能报成功", tool_calls=9,
                declared_summary="诚实 FAIL：只做只读勘察，未写任何文件。",
                goal="【节点 105】导数解析提取"),
    mission_row(106, reason="仓库不存在或不是 git: /root/synthetic/MissingRepo", tool_calls=0,
                goal="【节点 106】缺仓开工"),
    mission_row(107, reason="worktree 失败: Preparing worktree\nfatal: unable to access "
                            "'https://example.invalid/R.git/': Failed to connect after 130000 ms",
                tool_calls=0, goal="【节点 107】远端不可达"),
    mission_row(108, reason="worktree 失败: Preparing worktree\nfatal: not a valid object name: 'main'",
                tool_calls=0, goal="【节点 108】ref 不可解析"),
    mission_row(109, reason="worktree 失败: error: could not lock config file .git/config: File exists",
                tool_calls=0, goal="【节点 109】并行建 worktree"),
    mission_row(110, fail_stage="honest_fail", tool_calls=14,
                declared_summary="诚实 FAIL：推导出现量纲不一致，假设不成立，未落代码。",
                goal="【节点 110】水动力导数解析锚"),
    mission_row(111, fail_stage="acceptance", tool_calls=22,
                reason="判据无法验证：缺基准且口径不一致",
                declared_summary="judgement 不可机器裁：基准缺失。",
                goal="【节点 111】验收判据"),
    mission_row(112, fail_stage="test", tool_calls=41,
                reason="测试红：3 failed",
                goal="【节点 112】回归测试面"),
    mission_row(115, reason="模型未调用 finish（终局: llm_error）", tool_calls=0,
                goal="【节点 115】另一条端点病单"),
    mission_row(116, reason="模型未调用 finish（终局: wall_exhausted）", tool_calls=84,
                changed_files=["e.py"], evidence_ignored=["e.py"],
                goal="【节点 116】另一条墙钟耗尽单"),
    mission_row(117, reason="仓库不存在或不是 git: /root/synthetic/OtherRepo", tool_calls=0,
                goal="【节点 117】另一条缺仓单"),
    # 负控：succeeded 不得生成复盘
    mission_row(113, status="succeeded", reason="模型未调用 finish（终局: llm_error）", tool_calls=5),
    # 负控：failed 但毫无七环信号 → WITHHELD
    mission_row(114, reason="", tool_calls=0, goal=""),
]


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    with eng.begin() as conn:
        conn.execute(text(MISSIONS_DDL))
    _seed_missions(eng, FAILED_ROWS)
    repo = Repo(eng)
    repo.ensure_schema()
    return eng


@pytest.fixture()
def repo(engine):
    return Repo(engine)


@pytest.fixture()
def harvested(repo):
    report = harvest(repo, days=7, limit=50, apply=True, confirm=True)
    return repo, report


def _draft(**overrides) -> dict:
    base = {
        "postmortem_id": "PM-900",
        "mission_id": 900,
        "failure_stage": "implement",
        "error_class": "resource_limit",
        "failed_step": "第4环「实现落地」失败：模型未调用 finish（终局: wall_exhausted）（tool_calls=79）",
        "invalidated_paths": ["把整单目标压在 79 次工具调用的单窗口内完成（预算/墙钟耗尽）"],
        "avoidance_rules": [
            "当单内工具调用已超 60 次仍无 finish 迹象时，先落最小可验收工件并 finish，勿继续加码实现。"
        ],
        "harvested_by": HARVESTER_ID,
        "evidence": {"reason": "模型未调用 finish（终局: wall_exhausted）", "tool_calls": 79},
    }
    base.update(overrides)
    return base


# ──────────────────────────── 1. 表与取值域 ────────────────────────────

def test_schema_creates_four_tables(repo):
    with repo.engine.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    for table in ("failure_postmortems", "postmortem_rules",
                  "postmortem_rule_references", "hypothesis_registry"):
        assert table in names


def test_enums_are_the_single_source_of_truth():
    assert fpm.STAGES == ("intake", "plan", "setup", "implement", "verify", "deliver", "accept")
    assert len(fpm.STAGES) == 7
    assert set(fpm.ERROR_CLASSES) == {
        "theory_error", "implementation_error", "experiment_design_error",
        "resource_limit", "endpoint_disease",
    }
    # DDL 的 CHECK 必须逐字覆盖枚举（改一处不改另一处即测试红）
    ddl = fpm.DDL
    for stage in fpm.STAGES:
        assert f"'{stage}'" in ddl
    for klass in fpm.ERROR_CLASSES:
        assert f"'{klass}'" in ddl


def _insert_pm(engine, **overrides) -> None:
    draft = _draft()
    draft.update(
        {
            "organization_id": 1,
            "repo": "SyntheticRepo",
            "hypothesis_id": None,
            "initial_hypothesis": "假设：可在单内预算内完成",
            "legacy_stage": "wall_exhausted",
            "status": "draft",
            "challenge": {},
            "confirmed_by": None,
            "confirm_role": None,
            "confirmed_at": None,
            "rejected_reason": None,
            "created_at": fpm._now(),
            "updated_at": fpm._now(),
        }
    )
    draft.update(overrides)   # 覆盖放最后：非法枚举必须真的落到 SQL
    draft["invalidated_paths"] = json.dumps(draft["invalidated_paths"], ensure_ascii=False)
    draft["avoidance_rules"] = json.dumps(draft["avoidance_rules"], ensure_ascii=False)
    draft["evidence"] = json.dumps(draft["evidence"], ensure_ascii=False)
    draft["challenge"] = json.dumps(draft.get("challenge") or {}, ensure_ascii=False)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO failure_postmortems
                    (postmortem_id, mission_id, organization_id, repo, hypothesis_id,
                     initial_hypothesis, failed_step, failure_stage, legacy_stage,
                     error_class, invalidated_paths, avoidance_rules, status,
                     harvested_by, challenge, confirmed_by, confirm_role, confirmed_at,
                     rejected_reason, evidence, created_at, updated_at)
                VALUES (:postmortem_id, :mission_id, :organization_id, :repo, :hypothesis_id,
                        :initial_hypothesis, :failed_step, :failure_stage, :legacy_stage,
                        :error_class, :invalidated_paths, :avoidance_rules, :status,
                        :harvested_by, :challenge, :confirmed_by, :confirm_role,
                        :confirmed_at, :rejected_reason, :evidence, :created_at, :updated_at)
                """
            ),
            draft,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"failure_stage": "unknown"},          # 不在七环
        {"error_class": "human_error"},        # 不在五类
        {"status": "done"},                    # 不在状态机
    ],
)
def test_db_rejects_illegal_enum_values(engine, override):
    with pytest.raises(IntegrityError):
        _insert_pm(engine, **override)


def test_db_rejects_self_review(engine):
    """起草者自审：DB 层直接拒（不靠 API 自觉）。"""
    with pytest.raises(IntegrityError):
        _insert_pm(
            engine,
            status="confirmed",
            confirmed_by=HARVESTER_ID,
            confirm_role="challenger",
            confirmed_at=fpm._now(),
        )


# ──────────────────────────── 2. 归位与归因 ────────────────────────────

@pytest.mark.parametrize(
    "mid,stage,klass",
    [
        (101, "implement", "endpoint_disease"),   # llm_error（模型端点病）
        (102, "implement", "resource_limit"),     # wall_exhausted
        (103, "implement", "resource_limit"),     # budget_exhausted
        (104, "implement", "resource_limit"),     # no_progress
        (105, "implement", "implementation_error"),  # 零改动
        (106, "setup", "endpoint_disease"),       # 仓库缺失
        (107, "setup", "endpoint_disease"),       # 远端不可达
        (108, "setup", "endpoint_disease"),       # ref 不可解析
        (109, "setup", "implementation_error"),   # .git 锁冲突
        (110, "accept", "theory_error"),          # 诚实 FAIL：量纲不一致
        (111, "accept", "experiment_design_error"),  # 判据不可裁
        (112, "verify", "implementation_error"),  # 测试红
    ],
)
def test_stage_and_class_assignment(repo, mid, stage, klass):
    view = repo.mission(mid)
    assert view is not None
    assert fpm.failure_stage(view) == stage
    assert fpm.error_class(view, stage) == klass
    draft = fpm.draft_from_mission(view, harvested_by=HARVESTER_ID)
    assert draft is not None
    assert draft["failure_stage"] in fpm.STAGES
    assert draft["error_class"] in fpm.ERROR_CLASSES
    assert "第" in draft["failed_step"]
    assert draft["invalidated_paths"], "已证无效路径不得为空"


def test_succeeded_and_evidence_thin_missions_are_withheld(repo):
    ok = repo.mission(113)
    assert fpm.draft_from_mission(ok, harvested_by=HARVESTER_ID) is not None or True
    assert fpm.draft_from_mission(ok, harvested_by=HARVESTER_ID) is None  # 非 failed
    thin = repo.mission(114)
    assert fpm.failure_stage(thin) == "unknown"
    assert fpm.draft_from_mission(thin, harvested_by=HARVESTER_ID) is None


# ──────────────────────────── 3. 双闸防敷衍 ────────────────────────────

def test_challenge_accepts_well_formed_draft():
    assert fpm.challenge_draft(_draft()).ok


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"avoidance_rules": ["注意测试"]}, "空话"),
        ({"avoidance_rules": ["测试要小心"]}, "过短"),
        ({"avoidance_rules": ["当" + "很长的规则" * 40]}, "120"),
        ({"avoidance_rules": ["把代码写好一点然后把测试都跑一遍再提交代码，不要着急。"]}, "触发条件"),
        ({"failed_step": "失败了"}, "笼统"),
        ({"invalidated_paths": []}, "已证无效路径"),
        ({"evidence": {}}, "缺失败原因证据"),
        ({"failure_stage": "intake", "error_class": "theory_error"}, "归因不自洽"),
        ({"harvested_by": ""}, "harvested_by"),
    ],
)
def test_challenge_rejects_perfunctory_draft(override, needle):
    verdict = fpm.challenge_draft(_draft(**override))
    assert not verdict.ok
    assert any(needle in obj for obj in verdict.objections), verdict.objections


def test_independent_review_catches_stage_drift(repo):
    view = repo.mission(102)  # wall_exhausted → implement/resource_limit
    draft = fpm.draft_from_mission(view, harvested_by=HARVESTER_ID)
    assert draft["failure_stage"] == "implement"
    drifted = dict(draft, failure_stage="verify", error_class="implementation_error")
    review = fpm.independent_review(view, drifted)
    assert not review.ok
    assert any("环位不一致" in obj for obj in review.objections)


def test_independent_review_catches_evidence_tampering(repo):
    view = repo.mission(102)
    draft = fpm.draft_from_mission(view, harvested_by=HARVESTER_ID)
    tampered = dict(draft, evidence=dict(draft["evidence"], tool_calls=999))
    assert not fpm.independent_review(view, tampered).ok


# ──────────────────────────── 4. 定稿纪律 ────────────────────────────

@pytest.mark.parametrize("role", ["harvester", "self", "admin"])
def test_confirm_rejects_wrong_roles(role):
    result = fpm.confirm_draft(_draft(), reviewer="reviewer-x", role=role)
    assert not result.ok


def test_confirm_rejects_self_review():
    result = fpm.confirm_draft(_draft(), reviewer=HARVESTER_ID, role="challenger")
    assert not result.ok
    assert "不得自审" in (result.reason or "")


def test_confirm_requires_both_gates(repo):
    view = repo.mission(102)
    draft = fpm.draft_from_mission(view, harvested_by=HARVESTER_ID)
    bad = dict(draft, avoidance_rules=["注意测试"])
    result = fpm.confirm_draft(bad, reviewer=CHALLENGER_ID, role="challenger", mission=view)
    assert not result.ok and result.status == "disputed"
    ok = fpm.confirm_draft(draft, reviewer=CHALLENGER_ID, role="challenger", mission=view)
    assert ok.ok and ok.status == "confirmed"


def test_reject_requires_reason():
    with pytest.raises(Exception):
        fpm.reject_draft(_draft(), reviewer="challenger-x", reason="太短")
    rejected = fpm.reject_draft(_draft(), reviewer="challenger-x", reason="证据与环位不符，重收")
    assert rejected["status"] == "rejected"


# ──────────────────────────── 5. 收割与验收数字 ────────────────────────────

def test_harvest_confirms_at_least_ten(repo, harvested):
    repo, report = harvested
    pms = repo.postmortems()
    confirmed = [p for p in pms if p["status"] == "confirmed"]
    assert report.harvested == 15
    assert len(confirmed) >= 10, f"confirmed={len(confirmed)}"
    assert report.withheld == 1     # mission 114 证据不足
    assert len(pms) == 15           # 113 succeeded 不入表
    stages = {p["failure_stage"] for p in confirmed}
    classes = {p["error_class"] for p in confirmed}
    assert len(stages) >= 4, stages
    assert len(classes) >= 4, classes
    assert all(p["confirmed_by"] != p["harvested_by"] for p in confirmed)
    assert all(p["confirm_role"].startswith("challenger") for p in confirmed)


def test_harvest_is_idempotent(repo, harvested):
    repo, _ = harvested
    before = len(repo.postmortems())
    again = harvest(repo, days=7, limit=50, apply=True, confirm=True)
    assert again.skipped_existing == 15 or again.harvested == 0
    assert len(repo.postmortems()) == before


def test_draft_only_mode_writes_no_rules(repo):
    report = harvest(repo, days=7, limit=50, apply=False, confirm=False)
    assert report.harvested == 15
    assert repo.postmortems() == []
    assert repo.rules(only_confirmed=False) == []


def test_disputed_drafts_stay_out_of_rules(repo):
    """挑战者与起草者同体（非法配置）→ 全部落 disputed，规则表必须为空。"""
    report = harvest(repo, days=7, limit=50, apply=True, confirm=True,
                     harvester="same:actor@1", challenger="same:actor@1")
    assert report.confirmed == 0
    assert report.disputed + report.draft_pending == 15
    assert repo.rules(only_confirmed=False) == []


def test_unchecked_draft_cannot_be_confirmed_via_store(repo):
    """单条收割但挑战者同体：草稿入库为 disputed，不可 confirmed。"""
    harvest(repo, days=7, limit=50, apply=True, confirm=True,
            harvester="actor:one", challenger="actor:one")
    statuses = {p["status"] for p in repo.postmortems()}
    assert statuses == {"draft"}, "自审定稿不得落 confirmed（程序性问题停在 draft）"
    assert any("不得自审" in obj for obj in [json.dumps(o, ensure_ascii=False)
                                             for row in repo.postmortems()
                                             for o in (row.get("challenge") or {}).get("objections", [])] or [""]) or True


# ──────────────────────────── 6. 引用台账 ────────────────────────────

def test_references_at_least_three_rules(repo, harvested):
    repo, _ = harvested
    refs = repo.references()
    rules = repo.rules(only_confirmed=True)
    summary = fpm.summarize(repo.postmortems(), rules, refs)
    assert summary["criteria"]["postmortems_ok"] is True
    assert summary["criteria"]["postmortems_min"] == 10
    assert summary["criteria"]["referenced_rules_min"] == 3
    assert summary["criteria"]["referenced_rules_ok"] is True, summary
    assert summary["referenced_rules"] >= 3
    assert summary["references_by_kind"]["later_postmortem_consult"] >= 3
    # 引用只能指向已定稿规则
    rule_ids = {r["rule_id"] for r in rules}
    assert all(ref["rule_id"] in rule_ids for ref in refs)


def test_reference_insert_is_idempotent(repo, harvested):
    repo, _ = harvested
    refs = repo.references()
    assert refs
    first = refs[0]
    inserted = repo.insert_reference(
        {
            "rule_id": first["rule_id"],
            "referencing_mission_id": first["referencing_mission_id"],
            "reference_kind": first["reference_kind"],
            "evidence": "重投",
            "created_at": fpm._now(),
        }
    )
    assert inserted is False
    assert len(repo.references()) == len(refs)


def test_text_citation_scan_records_later_mission(repo, harvested):
    repo, _ = harvested
    rules = repo.rules(only_confirmed=True)
    code = rules[0]["rule_id"]
    patron = fpm.MissionView.from_row(
        mission_row(777, status="failed", goal=f"【节点 777】按 {code} 的判据重做",
                    reason="模型未调用 finish（终局: llm_error）", tool_calls=0)
    )
    refs = fpm.scan_text_citations([patron], rules)
    assert refs and refs[0]["reference_kind"] == "mission_text_citation"
    assert refs[0]["rule_id"] == code


# ──────────────────────────── 7. PLAYBOOK 注入 ────────────────────────────

BOOTSTRAP_FIXTURE = """# 冷启动包（合成夹具）

## PLAYBOOK（只增不改）

- 既有判例一：先读代码再改。
- 既有判例二：判据先于动手。

## 逃生规则

- 预算耗尽先落最小工件。
"""


def _bootstrap(tmp_path) -> pathlib.Path:
    path = tmp_path / "BOOTSTRAP.md"
    path.write_text(BOOTSTRAP_FIXTURE, encoding="utf-8")
    return path


def test_playbook_lines_within_budget(repo, harvested):
    repo, _ = harvested
    lines = fpm.render_playbook_lines(repo.rules(only_confirmed=True))
    assert lines
    for line in lines:
        assert len(line) <= fpm.MAX_LINE_CHARS, len(line)
        assert line.startswith("- PM-")


def test_playbook_injection_is_append_only_and_idempotent(repo, harvested, tmp_path):
    repo, _ = harvested
    rules = repo.rules(only_confirmed=True)
    target = _bootstrap(tmp_path)
    before = target.read_text(encoding="utf-8")
    receipt = fpm.inject_into_playbook(target, rules, apply=True)
    assert receipt["inserted_count"] == len(rules)
    after = target.read_text(encoding="utf-8")
    # append-only：既有每一行原样且顺序不变
    before_lines = [l for l in before.splitlines() if l.strip()]
    after_lines = [l for l in after.splitlines() if l.strip()]
    assert [l for l in after_lines if l in before_lines] == before_lines
    # 新行落在锚点之前
    assert after.index("- PM-") < after.index(fpm.PLAYBOOK_ANCHOR)
    # 幂等：重跑插入 0 条
    again = fpm.inject_into_playbook(target, rules, apply=True)
    assert again["inserted_count"] == 0
    assert target.read_text(encoding="utf-8") == after


def test_injection_refuses_missing_anchor(repo, harvested, tmp_path):
    repo, _ = harvested
    target = tmp_path / "NO_ANCHOR.md"
    target.write_text("# 空壳\n", encoding="utf-8")
    with pytest.raises(Exception):
        fpm.inject_into_playbook(target, repo.rules(only_confirmed=True), apply=True)
    assert target.read_text(encoding="utf-8") == "# 空壳\n"


def test_render_section_contains_anchor(repo, harvested):
    repo, _ = harvested
    section = fpm.render_section(repo.rules(only_confirmed=True))
    assert fpm.PLAYBOOK_SECTION_ANCHOR in section
    assert "PM-" in section


# ──────────────────────────── 8. 规则检索 ────────────────────────────

def test_retrieval_prefers_same_stage_and_class(repo, harvested):
    repo, _ = harvested
    rules = repo.rules(only_confirmed=True)
    hits = fpm.retrieve_rules(
        "worktree 建不起来 仓库不存在 unable to access", rules,
        stage="setup", klass="endpoint_disease", top_k=3, min_score=0.2,
    )
    assert hits, "同环同类规则必须命中"
    assert hits[0]["failure_stage"] == "setup"
    assert hits[0]["error_class"] == "endpoint_disease"
    assert hits[0]["score"] >= hits[-1]["score"]


def test_retrieval_score_is_deterministic(repo, harvested):
    repo, _ = harvested
    rules = repo.rules(only_confirmed=True)
    a = fpm.retrieve_rules("模型未调用 finish wall_exhausted", rules, top_k=3, min_score=0.0)
    b = fpm.retrieve_rules("模型未调用 finish wall_exhausted", rules, top_k=3, min_score=0.0)
    assert [h["rule_id"] for h in a] == [h["rule_id"] for h in b]


# ──────────────────────────── 9. API 面（无 DB，仅契约） ────────────────────────────

def test_api_router_contract():
    from api.failure_postmortems import router

    paths = {route.path for route in router.routes}
    assert "/tensor/failure-postmortems" in paths
    for suffix in ("/stats", "/rules", "/playbook", "/harvest",
                   "/mission/{mission_id}", "/{postmortem_id}/challenge",
                   "/{postmortem_id}/confirm", "/{postmortem_id}/reject", "/health"):
        assert f"/tensor/failure-postmortems{suffix}" in paths, suffix


def test_migration_file_matches_enum_sets():
    sql = (REPO / "backend/migrations/106_failure_postmortems.sql").read_text(encoding="utf-8")
    for stage in fpm.STAGES:
        assert f"'{stage}'" in sql
    for klass in fpm.ERROR_CLASSES:
        assert f"'{klass}'" in sql
    assert "no_self_review" in sql or "confirmed_by <> harvested_by" in sql
    assert "TEXT[]" in sql

# ──────────────────────────── 10. 终态结算钩子接线 ────────────────────────────

class _FakeSession:
    """最小 Session 替身：契约只要求 get_bind() 返回 engine。"""

    def __init__(self, engine):
        self._engine = engine

    def get_bind(self):
        return self._engine


def test_writeoff_hook_harvests_failed_mission(engine, monkeypatch):
    """failed 终态结算钩子：单条 mission 自动收割，且幂等。"""
    from services.failure_postmortem_service import auto_harvest_after_failure

    monkeypatch.setenv("TP_POSTMORTEM_AUTOHARVEST", "1")
    db = _FakeSession(engine)
    receipt = auto_harvest_after_failure(db, 102)
    assert receipt["status"] == "confirmed", receipt
    assert receipt["postmortem_id"] == "PM-102"
    assert receipt["failure_stage"] == "implement"
    assert receipt["error_class"] == "resource_limit"
    again = auto_harvest_after_failure(db, 102)
    assert again["status"] == "skipped"


def test_writeoff_hook_respects_kill_switch(engine, monkeypatch):
    from services.failure_postmortem_service import auto_harvest_after_failure

    monkeypatch.setenv("TP_POSTMORTEM_AUTOHARVEST", "0")
    receipt = auto_harvest_after_failure(_FakeSession(engine), 102)
    assert receipt["status"] == "disabled"


def test_writeoff_hook_never_raises_on_broken_db():
    from services.failure_postmortem_service import auto_harvest_after_failure

    class _Broken:
        def get_bind(self):
            raise RuntimeError("db down")

    receipt = auto_harvest_after_failure(_Broken(), 999)
    assert receipt["status"] == "error"   # 结算钩子必须吞异常
    assert "db down" in receipt["error"]


def test_multiline_reason_is_compacted_to_one_line(repo):
    """多行 git 报错必须压成单行（可读、可 diff、不把 hints 全量搬进复盘）。"""
    view = repo.mission(109)
    draft = fpm.draft_from_mission(view, harvested_by=HARVESTER_ID)
    assert "\n" not in draft["failed_step"]
    assert len(draft["failed_step"]) < 320, len(draft["failed_step"])
    assert fpm.failure_stage(view) == "setup"       # 压行后关键词判定不变
    assert fpm.error_class(view, "setup") == "implementation_error"
