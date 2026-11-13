"""端点契约：``GET /tensor/missions/anchor-precheck``（M-R17-01，只读预检）。

三层断言：

1. **纯桩层**（无库、必跑）：用假 Session 喂候选行，锁住 reject/allow/exempt
   判定与「只读」性质——假 Session 会记录每一条 SQL，测试断言它们全是 SELECT。
2. **装配层**：``anchor-precheck`` 必须声明在 ``/{mission_id}`` 之前（否则
   ``/anchor-precheck`` 会被参数路由吞掉，与 2026-11-05 的 pr-merge-queue 同型）。
3. **实库层**（``skipif`` 无库）：真 Session + 真表，挑**数据里现存**的锚点做
   两例实测——在飞锚点 → reject（含 conflicts）；仅 failed 同锚点 → allow。
   锚点一律取自数据，测试里不出现任何域名/算例名词。

零领域语义：合成夹具锚点统一 ``SYN-*``；实库层锚点来自 SQL 结果。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
# backend 路径追加（backend/tools 存在，前置会遮蔽仓库根 tools 包）
if str(BACKEND) not in sys.path:
    sys.path.append(str(BACKEND))

from tools.mission_governance import anchor_dedup_gate as g  # noqa: E402

tm = pytest.importorskip("api.tensor_missions", reason="backend app importable?")

NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
SYN = "SYN-ENDPOINT-ANCHOR"


# ── 假 Session：记录 SQL，返回预设行 ─────────────────────────────────────
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _StubSession:
    """最小 Session 双：只实现 endpoint 用到的 ``execute``，并记录每条 SQL。"""

    def __init__(self, in_flight_rows=(), terminal_rows=()):
        self.in_flight_rows = list(in_flight_rows)
        self.terminal_rows = list(terminal_rows)
        self.statements: list[str] = []
        self.params: list[dict] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(dict(params or {}))
        return _Result(self.in_flight_rows if "NOT IN" in sql else self.terminal_rows)


def _row(mid, status, *, anchor=SYN, parent=None, hours_ago=1, now=NOW):
    when = (now - timedelta(hours=hours_ago)).isoformat()
    return {
        "id": mid,
        "status": status,
        "goal": f"【锚点={anchor}】synthetic fixture",
        "parent_mission_id": parent,
        "created_at": when,
        "finished_at": when,
    }


USER = {"id": 1, "organization_id": 1, "username": "fixture", "roles": ["ADMIN"]}


# ── 1. 纯桩层 ────────────────────────────────────────────────────────────
def test_precheck_rejects_anchor_in_flight():
    db = _StubSession(in_flight_rows=[_row(9001, "running")])
    out = tm.anchor_precheck(anchor=SYN, db=db, user=USER)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [9001]
    assert out["readonly"] is True
    assert out["reason"]


def test_precheck_allows_when_only_failed_in_window():
    db = _StubSession(terminal_rows=[_row(9002, "failed", hours_ago=3)])
    out = tm.anchor_precheck(anchor=SYN, db=db, user=USER)
    assert out["decision"] == "allow"
    assert out["conflicts"] == []
    assert "failed" in out["reason"]


def test_precheck_rejects_when_delivered_in_window():
    db = _StubSession(terminal_rows=[_row(9003, "succeeded", hours_ago=2)])
    out = tm.anchor_precheck(anchor=SYN, db=db, user=USER)
    assert out["decision"] == "reject"
    assert out["conflicts"] == [9003]


def test_precheck_is_readonly_only_select():
    """只读硬约束：端点发的每条 SQL 必须是 SELECT（不得 UPDATE/INSERT/DELETE）。"""
    db = _StubSession(in_flight_rows=[_row(9004, "running")],
                      terminal_rows=[_row(9005, "failed")])
    tm.anchor_precheck(anchor=SYN, db=db, user=USER)
    assert db.statements, "端点必须真的查了候选池"
    for sql in db.statements:
        head = sql.strip().split()[0].upper()
        assert head == "SELECT", f"非只读语句：{sql[:80]}"
        for verb in ("UPDATE", "INSERT", "DELETE", "FOR UPDATE"):
            assert verb not in sql.upper(), f"SQL 含写操作 {verb}: {sql[:80]}"


def test_precheck_derived_candidate_is_exempt_without_queries():
    """派生单豁免先于候选池查询：豁免时一条 SQL 都不发（不误报父单在飞）。"""
    db = _StubSession(in_flight_rows=[_row(9006, "running")])
    out = tm.anchor_precheck(anchor=SYN, parent_mission_id=4242, db=db, user=USER)
    assert out["decision"] == "exempt"
    assert db.statements == []


def test_precheck_exempt_via_candidate_goal_marker():
    db = _StubSession(in_flight_rows=[_row(9007, "running")])
    out = tm.anchor_precheck(anchor=SYN, candidate_goal="【返修】补做验收项", db=db, user=USER)
    assert out["decision"] == "exempt"


def test_precheck_response_contract_keys():
    db = _StubSession()
    out = tm.anchor_precheck(anchor=SYN, db=db, user=USER)
    for key in ("decision", "conflicts", "reason", "readonly", "as_of",
                "window_hours", "normalized_anchor", "organization_id"):
        assert key in out, f"响应缺字段 {key}"
    assert out["decision"] in g.DECISIONS
    assert out["normalized_anchor"] == g.normalize_anchor(SYN)


def test_precheck_escapes_like_metacharacters():
    """锚点里的 % _ 必须转义，否则 LIKE 会退化成通配符（假冲突）。"""
    db = _StubSession()
    tm.anchor_precheck(anchor="SYN%_A", db=db, user=USER)
    patterns = [p.get("pat") for p in db.params if "pat" in p]
    assert patterns and all("\\%" in pat and "\\_" in pat for pat in patterns)


def test_precheck_window_hours_clamped():
    db = _StubSession()
    out = tm.anchor_precheck(anchor=SYN, window_hours=99999, db=db, user=USER)
    assert out["window_hours"] == g.MAX_WINDOW_HOURS


def test_precheck_route_declared_before_param_route():
    """``/anchor-precheck`` 必须早于 ``/{mission_id}``（同 pr-merge-queue 事故型）。"""
    src = (BACKEND / "api" / "tensor_missions.py").read_text(encoding="utf-8")
    i_pre = src.find('@router.get("/anchor-precheck"')
    i_param = src.find('@router.get("/{mission_id}"')
    assert i_pre != -1, "anchor-precheck 端点缺失"
    assert i_param != -1 and i_pre < i_param, "anchor-precheck 会被 /{mission_id} 吞掉"


# ── 2b. HTTP 层：真 router + 真 TestClient（依赖 override 掉 DB/鉴权）─────
def _http_client(stub):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.deps import require_auth  # type: ignore[import-not-found]
    from database.config import get_db  # type: ignore[import-not-found]

    app = FastAPI()
    app.include_router(tm.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: stub
    app.dependency_overrides[require_auth] = lambda: USER
    return TestClient(app)


def test_http_precheck_returns_reject_with_conflicts():
    """HTTP 层：路径/查询参数/响应体一次跑通（不是只调函数）。"""
    db = _StubSession(in_flight_rows=[_row(9101, "running")])
    resp = _http_client(db).get(
        "/api/v1/tensor/missions/anchor-precheck",
        params={"anchor": SYN, "window_hours": 24},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "reject"
    assert body["conflicts"] == [9101]
    assert body["readonly"] is True


def test_http_precheck_literal_route_not_shadowed_by_param_route():
    """``/anchor-precheck`` 不被 ``/{mission_id}`` 吞掉（同 pr-merge-queue 事故型）。"""
    db = _StubSession(terminal_rows=[_row(9102, "failed")])
    resp = _http_client(db).get("/api/v1/tensor/missions/anchor-precheck",
                                params={"anchor": SYN})
    assert resp.status_code == 200, f"被参数路由吞掉时会是 422：{resp.text}"
    assert resp.json()["decision"] == "allow"


def test_http_precheck_allows_when_only_failed():
    db = _StubSession(terminal_rows=[_row(9103, "failed", hours_ago=4)])
    resp = _http_client(db).get("/api/v1/tensor/missions/anchor-precheck",
                                params={"anchor": SYN})
    assert resp.json()["decision"] == "allow"


# ── 3. 实库层：真数据两例实测（无库即 skip，绝不静默通过）─────────────────
# 本环境（loong64 沙箱）没有真 psycopg2（只有 stub），所以优先走 SQLAlchemy
# Session；连不上时退回 **psql 只读通道**：把端点自己发出的 SQL（带 bind 参数）
# 用 postgres 方言编译成字面量 SQL 交给 psql 真跑——端点的 SQL 与判定逻辑都是
# 真的，缺的只有 DBAPI 传输层。两条路都不可用才 skip。
SEP = "\x1f"
TP_PG_DB = os.environ.get("TP_PG_DB", "tensorplatform")


def _psql_raw(sql: str, with_header: bool = False) -> list[list[str]]:
    base = ["psql", "-d", TP_PG_DB]
    if not with_header:
        base.append("-t")  # tuples only
    base += ["-A", "-F", SEP, "-v", "ON_ERROR_STOP=1", "-c", sql]
    cmd = base if (os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD")) \
        else ["runuser", "-u", "postgres", "--"] + base
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return [line.split(SEP) for line in proc.stdout.splitlines() if line.strip()]


def _psql(sql: str) -> list[list[str]]:
    return _psql_raw(sql, with_header=False)


def _psql_available() -> bool:
    try:
        return _psql("SELECT 1")[0][0] == "1"
    except Exception:  # noqa: BLE001
        return False


class _PsqlSession:
    """把端点的 ``execute(text_sql, params)`` 翻成 psql 只读调用（无 DBAPI 环境）。"""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        from sqlalchemy.dialects import postgresql

        bound = statement.bindparams(**{k: v for k, v in (params or {}).items()
                                        if k in statement._bindparams}) \
            if params else statement
        compiled = bound.compile(dialect=postgresql.dialect(),
                                 compile_kwargs={"literal_binds": True})
        sql = str(compiled)
        # 测试通道专有：psql 一行一单，goal 里的换行会把行切断 → 先单行化。
        # （真 DBAPI 不需要这层处理，故只在测试侧改写，不进端点代码。）
        sql = re.sub(r"(?<![\w.])goal(?=\s*,)",
                     "replace(replace(goal, E'\\\\n', ' '), E'\\\\r', ' ') AS goal",
                     sql, count=1)
        self.statements.append(sql)
        lines = _psql_raw(sql, with_header=True)
        if not lines:
            return _Result([])
        header = [c.strip().lower() for c in lines[0]]
        return _Result([dict(zip(header, row)) for row in lines[1:]])

    def close(self):
        return None


@pytest.fixture(scope="module")
def live_session():
    try:
        from database.config import SessionLocal  # type: ignore[attr-defined]
        from sqlalchemy import text as _text

        db = SessionLocal()
        db.execute(_text("SELECT 1"))
        yield db
        db.close()
        return
    except Exception:  # noqa: BLE001
        pass
    if not _psql_available():
        pytest.skip("实库不可用（无 DBAPI 且 psql 通道不通），实库两例实测未跑")
    yield _PsqlSession()


def _live_window(session, limit: int = 400):
    """实库窗口（最近 N 单）。psql 通道与 SQLAlchemy 通道各走各的取数。"""
    if isinstance(session, _PsqlSession):
        sql = ("SELECT id, status, replace(replace(goal, E'\\n', ' '), E'\\r', ' '), "
               "       parent_mission_id, created_at, finished_at "
               "FROM tensor_dev_missions WHERE organization_id = 1 "
               f"ORDER BY id DESC LIMIT {int(limit)}")
        cols = ["id", "status", "goal", "parent_mission_id", "created_at", "finished_at"]
        return [dict(zip(cols, row)) for row in _psql(sql)]
    from sqlalchemy import text as _text

    rows = session.execute(_text(
        "SELECT id, status, goal, parent_mission_id, created_at, finished_at "
        "FROM tensor_dev_missions WHERE organization_id = 1 ORDER BY id DESC LIMIT :n"
    ), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def _pick_cases(rows):
    """从实库数据里挑两例：在飞锚点 / 仅 failed 锚点（不硬编码任何锚点）。"""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        anchor = g.extract_anchor(row.get("goal"))
        if not anchor:
            continue
        groups.setdefault(g.normalize_anchor(anchor), []).append(row)
    terminal = {"succeeded", "failed", "stopped", "revoked", "orphan"}

    in_flight_case = None
    failed_case = None
    for key, members in groups.items():
        non_derived = [m for m in members if not g.is_derived(m)]
        flying = [m for m in non_derived if str(m["status"]).lower() not in terminal]
        if flying and in_flight_case is None:
            in_flight_case = (key, members, flying)
        terminal_members = [m for m in non_derived if str(m["status"]).lower() in terminal]
        statuses = {str(m["status"]).lower() for m in terminal_members}
        if not flying and terminal_members and statuses == {"failed"} and failed_case is None:
            failed_case = (key, members, terminal_members)
    return in_flight_case, failed_case


def test_live_in_flight_anchor_rejects(live_session):
    rows = _live_window(live_session)
    case, _ = _pick_cases(rows)
    if case is None:
        pytest.skip("实库窗口内没有非终态同锚点单，本例无法实测（不静默通过）")
    key, _members, flying = case
    out = tm.anchor_precheck(anchor=key, db=live_session, user=USER)
    assert out["decision"] == "reject", f"在飞锚点 {key} 应 reject，实际 {out}"
    assert out["conflicts"], "reject 必须带 conflicts"
    flying_ids = {int(str(m["id"]).strip()) for m in flying}
    conflict_ids = {int(str(c).strip()) for c in out["conflicts"]}
    assert flying_ids & conflict_ids, (
        f"conflicts {out['conflicts']} 未覆盖实库在飞单 {[m['id'] for m in flying]}"
    )


def test_live_failed_only_anchor_allows(live_session):
    rows = _live_window(live_session)
    _, case = _pick_cases(rows)
    if case is None:
        pytest.skip("实库窗口内没有「仅 failed」的同锚点组，本例无法实测（不静默通过）")
    key, _members, _failed = case
    out = tm.anchor_precheck(anchor=key, db=live_session, user=USER)
    assert out["decision"] == "allow", f"仅 failed 锚点 {key} 应 allow，实际 {out}"
    assert out["conflicts"] == []