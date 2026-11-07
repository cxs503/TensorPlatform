"""G-A/B/C 契约测试（docs/architecture/migration_disposition_2026-11.md §3，迁移前置）。

三判据：
  G-A 任务状态词表：契约 JSON == 运行时常量 == DB CHECK 终态；跨仓映射 100% 覆盖无孤儿
  G-B 一事实单源：run 登记面不写会话事件（事件流唯一写手=api.domain.tensor_common.append_event）
  G-C 登记词表：RunRegistration pattern 由契约派生；红队拒绝非成员

TensorPhysics 侧同名测试（tests/test_platform_status_contracts.py）内嵌同一批
契约文件字节副本并对拍——任一侧漂移都会红一边，这是 diff=0 契约的机器形态。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CONTRACTS = BACKEND / "contracts"

if str(ROOT) not in sys.path:  # 复用审计工具的 AST 判据，避免第二份实现
    sys.path.insert(0, str(ROOT))

CONTRACT_NAMES = (
    "mission_status_vocab.v1.json",
    "research_run_status_vocab.v1.json",
    "mission_task_status_map.v1.json",
    "session_event_kinds.v1.json",
)

# TensorPhysics TaskStatus 枚举冻结镜像（platform_registry / status_map 的消费源）。
# 与 TensorPhysics 侧测试对拍；两侧同改，否则必红一边。
TPH_TASK_STATUSES = (
    "queued", "claimed", "running", "verifying",
    "waiting_input", "retry_wait", "succeeded", "failed", "cancelled",
)


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text())


# ---------------------------------------------------------------------------
# G-A: mission 状态词表单一契约源
# ---------------------------------------------------------------------------

class TestGA_MissionVocab:
    def test_contract_files_wellformed(self):
        for name in CONTRACT_NAMES:
            doc = _load(name)
            assert doc["contract"] == name.removesuffix(".v1.json"), name
            assert isinstance(doc["version"], int) and doc["version"] >= 1, name

    def test_runtime_vocab_equals_contract(self):
        from api.domain.status_contracts import mission_status_contract
        from api.tensor_missions import MISSION_STATUSES, TERMINAL
        c = mission_status_contract()
        assert MISSION_STATUSES == tuple(c["statuses"])
        assert TERMINAL == tuple(c["terminal"])
        assert set(TERMINAL) <= set(MISSION_STATUSES)

    def _status_check_defs(self):
        """按文件字典序收集 tensor_dev_missions status CHECK 的全部定义。

        同一约束名被多份迁移 DROP+重建（086 初版 7 态 → 095 两份 10/8 态 →
        096 棘轮重宽）；终态=字典序最后一个定义，历史定义必须 ⊆ 契约。
        """
        defs = []
        for sql in sorted((BACKEND / "migrations").glob("*.sql")):
            for stmt in sql.read_text().split(";"):
                norm = re.sub(r"\s+", " ", stmt)
                # 目标表限定：语句须是 CREATE/ALTER tensor_dev_missions 本身——
                # 仅 REFERENCES tensor_dev_missions 的别表 status CHECK（如
                # 094_factory_soul 的 open/closed）不得混入本词表
                if "TABLE tensor_dev_missions" not in norm or "CHECK (status IN" not in norm:
                    continue
                m = re.search(r"CHECK \(status IN \(([^)]*)\)", stmt)
                defs.append((
                    sql.name,
                    {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()},
                ))
        return defs

    def test_db_check_terminal_equals_contract(self):
        """迁移终态（字典序最后定义）必须恰等于契约；历史定义只许更窄。"""
        from api.domain.status_contracts import mission_status_contract
        defs = self._status_check_defs()
        assert defs, "migrations 中未找到 tensor_dev_missions 的 status CHECK"
        contract = set(mission_status_contract()["statuses"])
        for _fname, states in defs:
            assert states <= contract, f"{_fname} 定义超出契约：{states - contract}"
        assert defs[-1][1] == contract, (
            f"终态定义 {defs[-1][0]} != 契约（095_tensor_repro_integrity 曾把同一约束名"
            "重 ADD 成 8 态收窄词表——096 棘轮已重宽，删 096 前先改这里）"
        )

    def test_map_covers_tph_vocab_no_orphans(self):
        from api.domain.status_contracts import mission_task_status_map_contract
        rows = mission_task_status_map_contract()["mapping"]
        assert {r["tph"] for r in rows} == set(TPH_TASK_STATUSES)
        assert all(r.get("tp") for r in rows)

    def test_platform_only_never_mapped(self):
        from api.domain.status_contracts import (
            mission_status_contract, mission_task_status_map_contract,
        )
        platform_only = set(mission_status_contract()["platform_only"])
        images = {r["tp"] for r in mission_task_status_map_contract()["mapping"]}
        assert platform_only & images == set()
        assert images <= set(mission_status_contract()["statuses"])


    # --- m4964 收尾复核：把「单源」扩到结构性判据 + 映射函数性质 ---

    def test_mapping_is_a_function_and_closed_on_contract(self):
        """每个 TensorPhysics 状态恰一个 TP 像；像集 ⊆ mission 词表。"""
        from api.domain.status_contracts import (
            mission_status_contract, mission_task_status_map_contract,
        )
        c = mission_status_contract()
        images: dict[str, set[str]] = {}
        for row in mission_task_status_map_contract()["mapping"]:
            images.setdefault(row["tph"], set()).add(row["tp"])
        assert {k: len(v) for k, v in images.items()} == {k: 1 for k in images}, images
        flat = set().union(*images.values())
        assert flat <= set(c["statuses"])

    def test_platform_only_agrees_across_the_two_contract_files(self):
        """两份 G-A 契约文件的 platform_only 必须 diff=0（v1 曾漏 awaiting_pr_merge）。

        mission_status_vocab v2 / migrations/100_pr_merge_gate 在任务侧新增了
        awaiting_pr_merge，映射文件落后一个词；审计器 platform_only_consistency
        项当场判红，v2 补齐后本测试锁死。
        """
        from api.domain.status_contracts import (
            mission_status_contract, mission_task_status_map_contract,
        )
        vocab = set(mission_status_contract()["platform_only"])
        mapped = {r["tp"] for r in mission_task_status_map_contract()["platform_only"]}
        assert vocab == mapped, {"canon_only": sorted(vocab - mapped),
                                 "runtime_only": sorted(mapped - vocab)}
        images = {r["tp"] for r in mission_task_status_map_contract()["mapping"]}
        assert mapped & images == set()

    def test_runtime_vocab_derived_not_hand_written(self):
        """MISSION_STATUSES/TERMINAL 的赋值右式必须调用契约装载器（AST 判据）。"""
        from tools.contracts.vocab_contract_audit import derives_symbol
        src = (BACKEND / "api" / "tensor_missions.py").read_text()
        for symbol in ("MISSION_STATUSES", "TERMINAL"):
            assert derives_symbol(src, symbol), f"{symbol} 又变成手写字面量分支了"

    def test_new_word_path_is_contract_only(self):
        """新增词项只许进契约：给运行面加一个未登记状态 → 契约测试立刻红。"""
        from api.domain.status_contracts import mission_status_contract
        canon = set(mission_status_contract()["statuses"])
        src = (BACKEND / "api" / "tensor_missions.py").read_text()
        # 源码里不得出现「状态元组字面量」（手写分支的形态）
        assert not re.search(r"MISSION_STATUSES\s*=\s*\(", src)
        assert "awaiting_pr_merge" in canon


# ---------------------------------------------------------------------------
# G-C: run 登记词表单一契约源
# ---------------------------------------------------------------------------

class TestGC_RunVocab:
    def test_patterns_derived_from_contract(self):
        from api.tensorphysics_runs import (
            _RUN_STATUS_PATTERN, _VALIDATION_STATUS_PATTERN,
        )
        from api.domain.status_contracts import research_run_contract
        c = research_run_contract()
        for s in c["run_statuses"]:
            assert re.match(_RUN_STATUS_PATTERN, s), s
        assert not re.match(_RUN_STATUS_PATTERN, "success")
        assert not re.match(_RUN_STATUS_PATTERN, "plann")
        for v in c["validation_statuses"]:
            assert re.match(_VALIDATION_STATUS_PATTERN, v), v
        assert not re.match(_VALIDATION_STATUS_PATTERN, "skipped")

    def test_handwritten_dual_source_is_gone(self):
        src = (BACKEND / "api" / "tensorphysics_runs.py").read_text()
        assert "Field(pattern=_RUN_STATUS_PATTERN)" in src
        assert "Field(pattern=_VALIDATION_STATUS_PATTERN)" in src
        # 手写正则双源（原 :37-38）必须已灭
        assert "planned|validating|queued" not in src
        assert "not_run|passed|failed|incomplete)$" not in src

    def test_manifest_bytes_cap_from_contract(self):
        from pydantic import ValidationError
        from api.domain.status_contracts import research_run_contract
        from api.tensorphysics_runs import RunRegistration, _MAX_MANIFEST_BYTES
        c = research_run_contract()
        assert _MAX_MANIFEST_BYTES == c["max_manifest_bytes"] == 1_000_000
        src = (BACKEND / "api" / "tensorphysics_runs.py").read_text()
        assert "> 1_000_000" not in src, "上限手写字面量应从契约派生"
        oversized = {"blob": "x" * (_MAX_MANIFEST_BYTES + 10)}
        with pytest.raises(ValidationError, match="exceeds"):
            RunRegistration.model_validate(
                {"run_id": "r-cap", "goal_id": "g", "status": "succeeded",
                 "validation_status": "passed", "manifest": oversized}
            )


# ---------------------------------------------------------------------------
# G-B: 一事实单源（事件流写手唯一）
# ---------------------------------------------------------------------------

class TestGB_SingleWriter:
    def test_run_registry_writes_no_session_events(self):
        src = (BACKEND / "api" / "tensorphysics_runs.py").read_text()
        assert "append_event" not in src, (
            "run 登记表是登记面不是事件面：会话事件唯一写手="
            "api.domain.tensor_common.append_event（migration_disposition §3 G-B）"
        )

    def test_event_kinds_cover_governance_members(self):
        from api.domain.tensor_common import EVENT_KINDS
        from api.domain.status_contracts import session_event_contract
        c = session_event_contract()
        kinds = set(EVENT_KINDS)
        assert set(c["governance_members"]) <= kinds
        assert {"plan", "execution_started", "run_registered",
                "validation_seen", "decision", "note"} <= kinds

    # --- m4964 收尾复核：词表进正典、写手唯一 ---

    def test_event_kinds_equals_contract(self):
        """G-B diff=0：运行时常量 ≡ 正典列表（逐词、含顺序）。"""
        from api.domain.status_contracts import session_event_contract
        from api.domain.tensor_common import EVENT_KINDS
        canon = session_event_contract()["kinds"]
        assert list(EVENT_KINDS) == list(canon)
        assert set(EVENT_KINDS) ^ set(canon) == set()

    def test_event_kinds_derived_not_hand_written(self):
        from tools.contracts.vocab_contract_audit import derives_symbol
        src = (BACKEND / "api" / "domain" / "tensor_common.py").read_text()
        assert derives_symbol(src, "EVENT_KINDS"), (
            "EVENT_KINDS 又变成手写元组：加词要过 backend/contracts/session_event_kinds.v1.json"
        )

    def test_only_one_event_row_writer(self):
        """一事实单源：全仓只有一处 INSERT INTO tensor_agent_session_events。"""
        from tools.contracts.vocab_contract_audit import event_insert_writers
        writers = event_insert_writers(ROOT)
        assert writers == ["backend/api/domain/tensor_common.py"], writers

    def test_batch_endpoint_reuses_shared_writer(self):
        src = (BACKEND / "api" / "tensor_sessions.py").read_text()
        assert "insert_event_row(" in src
        assert "INSERT INTO tensor_agent_session_events" not in src, (
            "批量端点自己拼 INSERT = 第二写手（G-B 双写）"
        )

    def test_append_event_goes_through_shared_writer(self):
        """行为判据：append_event 的行写入必须落在 insert_event_row 的 SQL 形态上。"""
        from fastapi import HTTPException
        from api.domain.tensor_common import append_event

        class _Result:
            def scalar(self):
                return 0  # 空事件流 → seq=1

        class _FakeDB:
            def __init__(self):
                self.sql: list[str] = []
                self.params: list[dict] = []

            def execute(self, stmt, params=None):
                self.sql.append(" ".join(str(stmt).split()))
                self.params.append(dict(params or {}))
                return _Result()

        db = _FakeDB()
        seq = append_event(db, 7, "note", {"k": "v"})
        assert seq == 1
        inserts = [q for q in db.sql if "INSERT INTO tensor_agent_session_events" in q]
        assert len(inserts) == 1
        assert db.params[-1]["kind"] == "note" and db.params[-1]["seq"] == 1
        with pytest.raises(HTTPException) as exc:
            append_event(db, 7, "not_a_kind", {})
        assert exc.value.status_code == 422

    def test_append_event_literals_are_contract_members(self):
        """api/ 里 append_event(..., '<kind>', ...) 的字面量必须都在正典内。"""
        from api.domain.status_contracts import session_event_contract
        from tools.contracts.vocab_contract_audit import append_event_literals
        canon = set(session_event_contract()["kinds"])
        used = set(append_event_literals(BACKEND / "api"))
        assert used <= canon, f"手写新增词项：{sorted(used - canon)}"


    def test_pattern_membership_equals_contract_exactly(self):
        """正则成员集 == 正典列表（不是「包含」）：多一个词也算漂移。"""
        from api.domain.status_contracts import research_run_contract
        from api.tensorphysics_runs import (
            _RUN_STATUS_PATTERN, _VALIDATION_STATUS_PATTERN,
        )
        from tools.contracts.vocab_contract_audit import pattern_tokens
        c = research_run_contract()
        assert pattern_tokens(_RUN_STATUS_PATTERN) == sorted(c["run_statuses"])
        assert pattern_tokens(_VALIDATION_STATUS_PATTERN) == sorted(c["validation_statuses"])

    def test_runtime_constants_derived_not_hand_written(self):
        from tools.contracts.vocab_contract_audit import derives_symbol
        src = (BACKEND / "api" / "tensorphysics_runs.py").read_text()
        for symbol in ("_RUN_STATUS_PATTERN", "_VALIDATION_STATUS_PATTERN",
                       "_MAX_MANIFEST_BYTES"):
            assert derives_symbol(src, symbol), f"{symbol} 不是从契约派生的"

# ---------------------------------------------------------------------------
# users/me 422 修复（PR #101 发现：int 路径参数吞掉 /me）
# ---------------------------------------------------------------------------

class TestUsersMe:
    def test_route_precedes_param_route(self):
        from api.users import router
        paths = [r.path for r in router.routes]
        assert "/users/me" in paths
        assert paths.index("/users/me") < paths.index("/users/{user_id}")

    def test_auth_me_exposes_organization_id(self):
        src = (BACKEND / "api" / "auth.py").read_text()
        assert '"organization_id"' in src
