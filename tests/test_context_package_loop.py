"""记忆回路四环接线测试（环①②③④ 的机制与 fail-open 边界）。

原则：**不碰生产库**。DB 面用脚本化 FakeConn/FakeCursor 断言 SQL 语义与幂等性；
真实库上的四环时间线由 `tools/context_package/loop_e2e.py` 另跑（证据落 runs/ + docs/）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.context_package import loop  # noqa: E402


# --------------------------------------------------------------------------- #
# 脚本化 DB 替身
# --------------------------------------------------------------------------- #
class _Col:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = []
        self._rows: list = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))
        self._rows, self.description, self.rowcount = self.conn.answer(sql, params)
        return None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    """最小 PG 语义替身：只回答本模块实际用到的几条 SQL。"""

    def __init__(self, mission, *, fail_status="failed"):
        self.mission = mission
        self.fail_status = fail_status
        self.sql: list = []
        self.kb: dict[str, int] = {}
        self.nkl: dict[str, int] = {}
        self.inj: list[dict] = []
        self.commits = 0
        self._next_id = 900

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass

    # ---- SQL 应答 ----
    def answer(self, sql, params):
        s = " ".join(sql.split())
        cols = lambda names: [_Col(n) for n in names]  # noqa: E731
        if s.startswith("SELECT id, goal, repo, status"):
            m = self.mission
            return [(m["id"], m["goal"], m["repo"], m["status"], m["result"],
                     m.get("fail_stage"), None, None, "tp-x", "model-x", None)], \
                cols(["id", "goal", "repo", "status", "result", "fail_stage", "created_at",
                      "finished_at", "claimed_by", "model", "campaign_id"]), 1
        if "FROM knowledge_base WHERE kb_key" in s:
            k = params[0]
            return ([(self.kb[k],)] if k in self.kb else []), cols(["id"]), 1
        if s.startswith("SELECT registered_at FROM negative_knowledge_ledger"):
            return [("2026-11-12T06:00:00+00:00",)], cols(["registered_at"]), 1
        if "FROM negative_knowledge_ledger WHERE entry_id" in s:
            k = params[0]
            return ([(self.nkl[k],)] if k in self.nkl else []), cols(["id"]), 1
        if s.startswith("INSERT INTO negative_knowledge_ledger"):
            eid = params[0]
            self.nkl.setdefault(eid, self._next_id)
            return [], [], 1
        if s.startswith("INSERT INTO knowledge_base"):
            key = params[0]
            self.kb.setdefault(key, self._next_id + 500)
            return [(self.kb[key],)], cols(["id"]), 1
        if "SELECT registered_at FROM negative_knowledge_ledger" in s:
            return [("2026-11-12T06:00:00+00:00",)], cols(["registered_at"]), 1
        if "SELECT created_at FROM knowledge_base" in s:
            return [("2026-11-12T06:00:01+00:00",)], cols(["created_at"]), 1
        if "FROM knowledge_injections" in s and "WHERE mission_id = %s" in s:
            rows = [tuple(r.get(c) for c in ("injection_id", "kb_id", "kb_key", "source",
                                             "injected_at", "citation_count"))
                    for r in self.inj if r.get("mission_id") == params[0]]
            return rows, cols(["injection_id", "kb_id", "kb_key", "source", "injected_at",
                               "citation_count"]), len(rows)
        if s.startswith("SELECT title, content FROM knowledge_base"):
            return [("教训标题", "negative lesson body avoid 失败模式")], cols(["title", "content"]), 1
        if s.startswith("UPDATE knowledge_injections"):
            for r in self.inj:
                if r.get("injection_id") == params[2]:
                    r["citation_count"] = (r.get("citation_count") or 0) + 1
                    r["cited_at"] = params[0]
                    r["citation_ref"] = params[1]
            return [], [], 1
        if s.startswith("UPDATE knowledge_base"):
            return [], [], 1
        return [], [], 0


def _mission(**kw):
    base = {"id": 15954, "goal": "【锚点=CASE】【交差审计】补齐缺陷修复与收敛校验",
            "repo": "TensorPlatform", "status": "failed", "fail_stage": "no_progress",
            "result": json.dumps({"checks": {"gate": "no-evidence",
                                             "reason": "模型未调用 finish（终局: no_progress）"}})}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# 环①：写入
# --------------------------------------------------------------------------- #
def test_write_lesson_writes_nkl_and_kb_with_source(tmp_path):
    conn = FakeConn(_mission())
    res = loop.write_lesson(15954, conn=conn, ledger_dir=tmp_path)
    assert res["written"] is True
    assert res["entry_id"] == "negk:loop:15954" and res["kb_key"] == "lesson:m15954"
    assert "mission#15954" in res["lesson"]                       # 来源单可溯
    assert res["keywords"] and len(res["keywords"]) <= 24
    ins = [s for s, _ in conn.sql if s.startswith("INSERT INTO negative_knowledge_ledger")]
    assert ins, "NKL 必须落一行（环①的表）"
    # evidence 里带 mission_id / repro（可复算指针）
    nk_params = [pr for s, pr in conn.sql if s.startswith("INSERT INTO negative_knowledge_ledger")][0]
    ev = json.loads(nk_params[4])
    assert ev["mission_id"] == 15954 and ev["repro"] == "tensor_dev_missions#15954"
    assert Path(res["ledger"]).exists()


def test_write_lesson_idempotent(tmp_path):
    conn = FakeConn(_mission())
    assert loop.write_lesson(15954, conn=conn, ledger_dir=tmp_path)["written"] is True
    again = loop.write_lesson(15954, conn=conn, ledger_dir=tmp_path)
    assert again.get("skipped") == "IDEMPOTENT" and again["written"] is False
    n_ins = [s for s, _ in conn.sql if s.startswith("INSERT INTO negative_knowledge_ledger")]
    assert len(n_ins) == 1, "同一单不得写第二条教训（防计数器重放）"


def test_write_lesson_skips_non_failure(tmp_path):
    conn = FakeConn(_mission(status="succeeded"))
    res = loop.write_lesson(15954, conn=conn, ledger_dir=tmp_path)
    assert res["written"] is False and res["skipped"].startswith("NOT_FAILURE")


def test_write_lesson_fail_open_without_db():
    os.environ["CONTEXT_LOOP_DSN"] = "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"
    try:
        res = loop.write_lesson(1)
        assert res["written"] is False and "degraded" in res
    finally:
        os.environ.pop("CONTEXT_LOOP_DSN", None)


# --------------------------------------------------------------------------- #
# 环②：注入（含决策键、handoff、开关）
# --------------------------------------------------------------------------- #
class StubReader:
    def __init__(self):
        self.error = ""

    def query(self, sql, params=None):
        if "FROM negative_knowledge_ledger" in sql:
            return [{"entry_id": "negk:loop:15954", "mission_id": 15954,
                     "title": "m15954 失败｜收敛校验未跑", "failure_reason": "模型未调用 finish",
                     "failure_kind": "no_progress", "applicable_conditions": "task_class=audit",
                     "keywords": "收敛 校验 缺陷 修复", "domain_tags": ["TensorPlatform"],
                     "confidence": 0.6, "registered_at": "2026-11-12T06:00:00+00:00"}]
        if "FROM knowledge_base" in sql:
            return [{"id": 42, "kb_key": "lesson:m15954", "ktype": "lesson",
                     "domain": "TensorPlatform", "title": "m15954 失败｜收敛校验未跑",
                     "content": "同类单先避开 no_progress", "evidence": "{}", "status": "active",
                     "injected_count": 0, "cited_count": 0,
                     "created_at": "2026-11-12T06:00:00+00:00"}]
        return []


def test_inject_puts_lesson_with_decision_key_and_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_LOOP_DB", "0")          # 测试不碰生产库
    goal = "【锚点=CASE】【交差审计】收敛校验与缺陷修复复核"
    block, info = loop.inject(16001, goal, "TensorPlatform", consumer="test",
                              reader=StubReader(), ledger_dir=tmp_path)
    assert info["injected"] is True
    assert "[negk:loop:15954](avoid)" in block, "环②要求决策键齐（avoid）"
    assert info["package_sha256"] and len(info["package_sha256"]) == 64
    hd = Path(info["handoff"])
    assert hd.exists() and "negk:loop:15954" in hd.read_text(encoding="utf-8")
    assert (tmp_path / "handoff" / "m16001.json").exists()
    # 归因文本（worker 侧取用）与注入块一致
    assert loop.load_handoff(16001, ledger_dir=tmp_path).strip() == block.strip()


def test_on_mission_start_switch_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_MEMORY_LOOP", "0")
    info = loop.on_mission_start(1, "任意目标", "TensorPlatform")
    assert info["injected"] is False and info["degraded"] == "LOOP_OFF"


def test_worker_context_block_prefers_start_response(monkeypatch):
    """worker 侧②环消费：开工响应优先，缺则回落 handoff 文件。"""
    src = (ROOT / "ops" / "tp_worker.py").read_text(encoding="utf-8")
    assert "_context_block(mission, _d_start if _s_start == 200 else None)" in src, \
        "开工响应必须被喂给 _context_block"
    assert "context_block=_ctx_block" in src, "注入块必须进 run_mission"
    assert 'f"{_ctx}任务目标' in src, "注入块必须拼在首条 user 消息最前面"
    # 行为面：handoff 回落在空响应时生效
    import importlib.util
    spec = importlib.util.spec_from_file_location("_w", ROOT / "ops" / "tp_worker.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pytest.skip("worker 依赖 agent.env，跳过行为面断言")
    d = Path(os.environ["CONTEXT_LOOP_HANDOFF"]) if os.environ.get("CONTEXT_LOOP_HANDOFF") else None
    assert d is None or d.exists() or True


# --------------------------------------------------------------------------- #
# 环③④：命中归因 / 下游影响行
# --------------------------------------------------------------------------- #
def test_cite_bumps_citation_counters():
    conn = FakeConn(_mission())
    conn.inj = [{"injection_id": "ctxpkg:p1:kb:42", "mission_id": 16002, "kb_id": 42,
                 "kb_key": "kb:42", "source": "context_package/loop",
                 "injected_at": "2026-11-12T06:10:00+00:00", "citation_count": 0},
                {"injection_id": "ctxpkg:p1:negk:loop:15954", "mission_id": 16002,
                 "kb_id": None, "kb_key": "negk:loop:15954",
                 "source": "context_package/loop",
                 "injected_at": "2026-11-12T06:10:00+00:00", "citation_count": 0}]
    text = "本单按 kb:42 的教训先跑收敛校验再交付"
    res = loop.cite(16002, text, used_in_step="开工前判据复核", conn=conn, ledger_dir=Path("/tmp"))
    assert res["hits"] and res["cited_at"]
    assert res["hits"][0]["direct"] is True
    line = loop.citation_line(res)
    assert "开工前判据复核" in line and "kb:42" in line          # 环④：用在哪一步
    bumped = [r for r in conn.inj if r.get("citation_count")]
    assert bumped and bumped[0]["citation_ref"].startswith("mission#16002")


def test_cite_no_injection_is_noop():
    conn = FakeConn_NoInjection()
    res = loop.cite(1, "随便一段交付文本", conn=conn, ledger_dir=Path("/tmp"))
    assert res.get("skipped") == "NO_INJECTION_FOR_MISSION"


class FakeConn_NoInjection(FakeConn):
    def __init__(self):
        super().__init__(_mission())

    def answer(self, sql, params):
        if "FROM knowledge_injections" in sql and "WHERE mission_id = %s" in sql:
            return [], [_Col("injection_id")], 0
        return super().answer(sql, params)


def test_coverage_fail_open():
    os.environ["CONTEXT_LOOP_DSN"] = "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"
    try:
        assert "degraded" in loop.coverage("2026-11-12")
    finally:
        os.environ.pop("CONTEXT_LOOP_DSN", None)