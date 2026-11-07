"""放门自动化 daemon 验收测试（IN20-R2）。

判据（先于实现，机器可裁）：
1. 判据解析：metadata.gate_criteria / acceptance_artifacts 显式声明优先；
   缺省时从 goal / acceptance_criteria 文本抽 `artifacts/...` 路径；
   治理标记（SPEC军/规范修订/规范落实/治理规则）→ owner_only。
2. 满足者自动放：上游全 done + 判据工件可解析 → status blocked→pending，
   审计行 action=release、from/to status、reason、detail 完整。
3. 不满足者留：上游在飞 / 工件缺失 / 工件 JSON 损坏 / 仓归属不符 → 只等，
   status 保持 blocked（绝不写 failed），审计 reason 与判据一致。
4. owner_only 负例 MUST 不放：显式 metadata.owner_only 与治理门名都只标记
   metadata.owner_only=1（显式人工位），status 不变；重复跑不重复记账。
5. 回滚路径：--rollback 语义（Store.rollback）把节点退回 blocked，写 rollback
   审计并置原 release 行为 rolled_back；同一行不可二次回滚。
6. 幂等 + dry-run：重复一轮不二次放门；dry-run 不写库不写审计。
7. SQL 安全：标识符白名单、字面量单引号转义（发布路径拼 SQL 不注入）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.taskplane.gate_auto_release import (
    ACTION_HOLD_OWNER_ONLY,
    ACTION_RELEASE,
    ACTION_ROLLBACK,
    ACTION_WAIT,
    FROM_STATUS,
    RELEASED_STATUS,
    Criteria,
    MemoryStore,
    PsqlStore,
    _ident,
    _lit,
    evaluate_gate,
    parse_criteria,
    resolve_artifact,
    run_once,
)


# ─────────────────────────── 夹具 ───────────────────────────

def _gate(node_id, *, deps, name="测试门", goal="", extra=None, status=FROM_STATUS):
    meta = {"goal": goal}
    meta.update(extra or {})
    return {"id": node_id, "plan_id": 1, "name": name, "node_type": "gate",
            "status": status, "dependencies": deps, "metadata": meta}


def _task(node_id, status="done", plan_id=1):
    return {"id": node_id, "plan_id": plan_id, "name": f"任务{node_id}",
            "node_type": "task", "status": status, "dependencies": [], "metadata": {}}


def _write(path: Path, payload=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        path.write_text("ok", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


GOAL_G13 = ("体系贯穿验收门（gate）。判据：参与度矩阵全覆盖+覆盖度表零粉饰+"
            "artifacts/system_pierce_acceptance.json。")


# ─────────────────────────── 1. 判据解析 ───────────────────────────

def test_parse_criteria_from_goal_and_governance_marker():
    crit = parse_criteria({"goal": GOAL_G13, "acceptance_criteria": ["acceptance.json"]},
                          name="纵贯军G13·体系贯穿验收门")
    assert crit.declared_by == "goal_text"
    assert crit.artifacts == ("artifacts/system_pierce_acceptance.json",)
    assert crit.owner_only is False

    spec = parse_criteria({"goal": "规范落实军验收门（gate）。判据：artifacts/spec_impl_acceptance.json"},
                          name="SPEC军G20·S×E闭环验收门")
    assert spec.owner_only is True
    assert "governance_marker" in spec.owner_only_reason

    explicit = parse_criteria({"owner_only": 1, "goal": GOAL_G13}, name="普通门")
    assert explicit.owner_only is True and explicit.owner_only_reason == "metadata_owner_only"

    declared = parse_criteria({"gate_criteria": {"artifacts": ["runs/a.json"]},
                               "goal": "artifacts/should_not_win.json"}, name="门")
    assert declared.declared_by == "metadata.gate_criteria"
    assert declared.artifacts == ("runs/a.json",)


# ─────────────────────────── 2. 满足者自动放 ───────────────────────────

def test_release_when_upstream_done_and_artifact_resolves(tmp_path):
    art = _write(tmp_path / "artifacts/system_pierce_acceptance.json", {"verdict": "pass"})
    assert art.stat().st_size > 0
    gates = [_gate(129, deps=[1, 2], name="纵贯军G13·体系贯穿验收门", goal=GOAL_G13)]
    nodes = [_task(1), _task(2)] + gates
    store = MemoryStore(nodes)

    summary = run_once(store, roots=[str(tmp_path)], actor="pytest", run_id="r1")

    assert summary["counts"][ACTION_RELEASE] == 1
    assert summary["released"] == [129]
    assert summary["snapshot_changed"] is True
    node = next(n for n in store.nodes() if n["id"] == 129)
    assert node["status"] == RELEASED_STATUS

    audit = [a for a in store.audit if a["action"] == ACTION_RELEASE]
    assert len(audit) == 1
    row = audit[0]
    assert (row["node_id"], row["plan_id"], row["from_status"], row["to_status"]) == \
        (129, 1, FROM_STATUS, RELEASED_STATUS)
    assert row["reason"] == "satisfied" and row["actor"] == "pytest" and row["run_id"] == "r1"
    assert row["detail"]["artifacts"]["items"][0]["path"].endswith("system_pierce_acceptance.json")
    assert row["detail"]["upstream"]["ok"] is True


# ─────────────────────────── 3. 不满足者留（不 failed） ───────────────────────────

def test_wait_when_upstream_in_flight(tmp_path):
    _write(tmp_path / "artifacts/system_pierce_acceptance.json", {"ok": True})
    nodes = [_task(1), _task(2, status="in-flight"),
             _gate(165, deps=[1, 2], name="饱和池G16·池核销门",
                   goal="判据：artifacts/system_pierce_acceptance.json")]
    store = MemoryStore(nodes)

    summary = run_once(store, roots=[str(tmp_path)], run_id="r2")

    assert summary["counts"][ACTION_WAIT] == 1
    node = next(n for n in store.nodes() if n["id"] == 165)
    assert node["status"] == FROM_STATUS          # 继续等，不是 failed
    assert node["status"] != "failed"
    waits = [a for a in store.audit if a["action"] == ACTION_WAIT]
    assert len(waits) == 1
    assert waits[0]["reason"] == "upstream_pending"
    assert waits[0]["detail"]["upstream"]["not_done"] == [{"node_id": 2, "status": "in-flight"}]

    # 同判据重复轮询：不再重复记账（审计不膨胀）
    run_once(store, roots=[str(tmp_path)], run_id="r3")
    assert len([a for a in store.audit if a["action"] == ACTION_WAIT]) == 1


def test_wait_when_artifact_missing_corrupt_or_wrong_repo(tmp_path):
    _write(tmp_path / "artifacts/good.json", {"ok": 1})
    (tmp_path / "artifacts/broken.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path / "other-repo/artifacts/good.json", {"ok": 1})

    cases = {
        "missing": (_gate(1, deps=[9], goal="判据：artifacts/nope.json"), "artifact_missing"),
        "corrupt": (_gate(2, deps=[9], goal="判据：artifacts/broken.json"), "artifact_missing"),
        "no_criteria": (_gate(3, deps=[9], name="饱和池G16·池核销门",
                              goal="判据：saturation_pulse 表可查"), "no_criteria_declared"),
        "repo_mismatch": (_gate(4, deps=[9], goal="判据：artifacts/good.json",
                                extra={"repo": "TensorPhysics"}), "artifact_missing"),
    }
    for label, (gate, expected_reason) in cases.items():
        store = MemoryStore([_task(9), gate])
        summary = run_once(store, roots=[str(tmp_path)], run_id=f"r-{label}")
        assert summary["counts"][ACTION_WAIT] == 1, label
        assert summary["nodes"][0]["reason"] == expected_reason, label
        assert store.nodes()[1]["status"] == FROM_STATUS, label

    # 明确区分：仓归属不符的命中路径被记下来（人可复核），而不是静默放过
    check = resolve_artifact("artifacts/good.json", [str(tmp_path)], repo="TensorPhysics")
    assert check.resolved is False and check.detail.startswith("repo_mismatch")
    assert check.path is not None


# ─────────────────────────── 4. owner_only 负例 ───────────────────────────

def test_owner_only_never_released_and_marked(tmp_path):
    _write(tmp_path / "artifacts/spec_impl_acceptance.json", {"ok": True})
    goal = "规范落实军验收门（gate）。判据：五项各留证工件 artifacts/spec_impl_acceptance.json。"
    nodes = [_task(176)] + [_gate(185, deps=[176], name="SPEC军G20·S×E闭环验收门", goal=goal)]
    store = MemoryStore(nodes)

    summary = run_once(store, roots=[str(tmp_path)], run_id="r-owner")
    assert summary["counts"][ACTION_HOLD_OWNER_ONLY] == 1
    assert summary["released"] == []

    node = next(n for n in store.nodes() if n["id"] == 185)
    assert node["status"] == FROM_STATUS               # MUST 不放
    assert node["metadata"]["owner_only"] == 1         # 显式人工位
    assert "governance_marker" in node["metadata"]["owner_only_reason"]
    holds = [a for a in store.audit if a["action"] == ACTION_HOLD_OWNER_ONLY]
    assert len(holds) == 1 and holds[0]["reason"].startswith("governance_marker")

    # 第二轮：已标记 → 不重复记账
    run_once(store, roots=[str(tmp_path)], run_id="r-owner2")
    assert len([a for a in store.audit if a["action"] == ACTION_HOLD_OWNER_ONLY]) == 1


# ─────────────────────────── 5. 回滚 ───────────────────────────

def test_rollback_restores_blocked_and_audits(tmp_path):
    _write(tmp_path / "artifacts/ok.json", {"ok": True})
    store = MemoryStore([_task(1), _gate(129, deps=[1], goal="判据：artifacts/ok.json")])
    run_once(store, roots=[str(tmp_path)], run_id="r-rel")
    release_id = [a["id"] for a in store.audit if a["action"] == ACTION_RELEASE][0]
    assert store.nodes()[1]["status"] == RELEASED_STATUS

    out = store.rollback(release_id, actor="pytest-manual", run_id="r-rb")
    assert out["ok"] is True and out["node_id"] == 129
    assert store.nodes()[1]["status"] == FROM_STATUS
    rollbacks = [a for a in store.audit if a["action"] == ACTION_ROLLBACK]
    assert len(rollbacks) == 1
    assert rollbacks[0]["rollback_of"] == release_id
    assert (rollbacks[0]["from_status"], rollbacks[0]["to_status"]) == (RELEASED_STATUS, FROM_STATUS)
    assert [a for a in store.audit if a["id"] == release_id][0]["rolled_back"] is True

    # 不可二次回滚（幂等保护）
    assert store.rollback(release_id, actor="pytest-manual", run_id="r-rb2")["ok"] is False
    assert store.nodes()[1]["status"] == FROM_STATUS


# ─────────────────────────── 6. 幂等 + dry-run ───────────────────────────

def test_second_run_idempotent_and_dry_run_writes_nothing(tmp_path):
    _write(tmp_path / "artifacts/ok.json", {"ok": True})
    store = MemoryStore([_task(1), _gate(129, deps=[1], goal="判据：artifacts/ok.json")])

    dry = run_once(store, roots=[str(tmp_path)], dry_run=True, run_id="r-dry")
    assert dry["counts"][ACTION_RELEASE] == 1        # 判词给的是「会放」
    assert store.nodes()[1]["status"] == FROM_STATUS  # 但库里没动
    assert store.audit == []                          # 也不记账

    run_once(store, roots=[str(tmp_path)], run_id="r-a")
    run_once(store, roots=[str(tmp_path)], run_id="r-b")
    assert len([a for a in store.audit if a["action"] == ACTION_RELEASE]) == 1


# ─────────────────────────── 7. SQL 安全 ───────────────────────────

def test_sql_literal_and_identifier_guards():
    assert _lit("a'b") == "'a''b'"
    assert _lit(None) == "NULL"
    assert _ident("campaign_nodes") == "campaign_nodes"
    assert _ident("gate_drill.campaign_nodes") == "gate_drill.campaign_nodes"
    with pytest.raises(ValueError):
        _ident("campaign_nodes; DROP TABLE x")
    store = PsqlStore(table="campaign_nodes", audit="gate_release_audit")
    assert store.table == "campaign_nodes" and store.audit == "gate_release_audit"


# ─────────────────────────── 8. 六门合成演练（G13-G20 形状） ───────────────────────────

def test_six_gate_drill_matches_expected_matrix(tmp_path):
    """按 G13-G20 真实门形状造 6 门：2 门满足即放、3 门缺工件留、1 门 owner_only。"""
    for name in ("system_pierce_acceptance.json", "autoregression_acceptance.md"):
        _write(tmp_path / f"artifacts/{name}", {"ok": True})
    gates = [
        _gate(129, deps=[1], name="纵贯军G13·体系贯穿验收门",
              goal="判据：artifacts/system_pierce_acceptance.json"),
        _gate(144, deps=[1], name="深水区G14·缺口账本验收门",
              goal="判据：产出 artifacts/deepwater_gap_ledger_update.json"),
        _gate(153, deps=[1], name="自回归G15·自回归回路验收门",
              goal="产出 artifacts/autoregression_acceptance.md"),
        _gate(165, deps=[2], name="饱和池G16·池核销门",
              goal="判据：核销表全覆盖+脉搏回放数据在 saturation_pulse 表可查"),
        _gate(170, deps=[1], name="战史官G18·顶刊报告验收门",
              goal="判据：artifacts/chronicler_acceptance.json"),
        _gate(185, deps=[1], name="SPEC军G20·S×E闭环验收门",
              goal="规范落实军验收门。判据：artifacts/spec_impl_acceptance.json"),
    ]
    store = MemoryStore([_task(1), _task(2, status="in-flight")] + gates)
    summary = run_once(store, roots=[str(tmp_path)], run_id="drill")

    assert summary["counts"] == {ACTION_RELEASE: 2, ACTION_WAIT: 3, ACTION_HOLD_OWNER_ONLY: 1}
    assert summary["released"] == [129, 153]
    assert summary["held_owner_only"] == [185]
    reasons = {r["node_id"]: r["reason"] for r in summary["nodes"]}
    assert reasons[144] == "artifact_missing"
    assert reasons[165] == "no_criteria_declared"
    assert reasons[170] == "artifact_missing"
    assert reasons[185].startswith("governance_marker")
    # 全部 6 门都有判词，且没有一门被写 failed
    assert len(summary["nodes"]) == 6
    assert all(n["status"] != "failed" for n in store.nodes())

# ─────────────────────────── 9. 六门演练产物回归锁 ───────────────────────────

def test_committed_drill_artifact_holds_acceptance_invariants():
    """artifacts/gate_auto_release_drill.json（真实 DB 演练产物）不变量。

    变更此产物（重跑演练）必须继续满足：六门全覆盖、生产全表快照未变、
    沙箱真写入且 rollback 全还原、审计链完整、G20（185）生产与沙箱都未放行。
    """
    drill_path = Path(__file__).resolve().parents[1] / "artifacts/gate_auto_release_drill.json"
    data = json.loads(drill_path.read_text(encoding="utf-8"))
    prod = data["production"]
    reh = data["rehearsal"]

    assert data["gate_ids"] == [129, 144, 153, 165, 170, 185]
    assert len(prod["gates"]) == 6
    assert prod["production_untouched"] is True
    assert prod["snapshot_before_sha256"] == prod["snapshot_after_sha256"]

    # 生产：判据不满足者只等（无 failed），治理门只标不放
    assert prod["counts"][ACTION_RELEASE] == 0
    assert prod["counts"][ACTION_HOLD_OWNER_ONLY] == 1
    g20 = next(g for g in prod["gates"] if g["node_id"] == 185)
    assert g20["action"] == ACTION_HOLD_OWNER_ONLY
    assert g20["checks"]["owner_only"]["flag"] is True

    # 沙箱：写入路径真跑（release>0），等待/人工位对照齐备，回滚全还原
    assert reh["pass"] is True
    assert len(reh["run"]["released"]) >= 1
    assert 185 not in reh["run"]["released"]
    assert reh["run"]["counts"][ACTION_WAIT] >= 1      # G16 上游在飞负例
    assert reh["run"]["counts"][ACTION_HOLD_OWNER_ONLY] == 1
    assert reh["all_released_restored_to_blocked"] is True
    assert reh["audit_rows_complete"] is True
    assert reh["release_rollback_pairs_ok"] is True
    assert reh["audit_action_counts"][ACTION_ROLLBACK] == len(reh["run"]["released"])
