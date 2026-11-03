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
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CONTRACTS = BACKEND / "contracts"

CONTRACT_NAMES = (
    "mission_status_vocab.v1.json",
    "research_run_status_vocab.v1.json",
    "mission_task_status_map.v1.json",
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
        kinds = set(EVENT_KINDS)
        assert {"plan", "execution_started", "run_registered",
                "validation_seen", "decision", "note"} <= kinds


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
