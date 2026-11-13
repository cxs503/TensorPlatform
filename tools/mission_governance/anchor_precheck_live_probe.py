#!/usr/bin/env python3
"""``GET /tensor/missions/anchor-precheck`` 的实库两例探针（M-R17-01 证据面）。

它不新造逻辑：挑两例**数据里现存**的锚点，直接调用端点函数
（``api.tensor_missions.anchor_precheck``）并落证据 JSON：

* 例 1「同锚点在飞」→ 期望 ``decision=reject`` 且 ``conflicts`` 非空；
* 例 2「同锚点仅 failed」→ 期望 ``decision=allow``。

取数优先级：SQLAlchemy Session（有真 DBAPI 时）→ psql 只读通道（无 DBAPI 的
loong64 沙箱）。两条都不通则如实报 ``unavailable``，不伪造结果。

用法::

    python3 tools/mission_governance/anchor_precheck_live_probe.py \
        --json-out artifacts/r17/anchor_precheck_live_evidence.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
# backend 路径**追加**而非前置：backend/tools 会遮蔽仓库根的 tools 包
if str(REPO / "backend") not in sys.path:
    sys.path.append(str(REPO / "backend"))

SEP = "\x1f"
TP_PG_DB = os.environ.get("TP_PG_DB", "tensorplatform")
WINDOW = 400


def _psql_rows(sql: str, with_header: bool = False) -> list[list[str]]:
    base = ["psql", "-d", TP_PG_DB]
    if not with_header:
        base.append("-t")
    base += ["-A", "-F", SEP, "-v", "ON_ERROR_STOP=1", "-c", sql]
    cmd = base if (os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD")) \
        else ["runuser", "-u", "postgres", "--"] + base
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return [line.split(SEP) for line in proc.stdout.splitlines() if line.strip()]


def _live_session() -> tuple[Any, str]:
    """返回 (session, transport)。优先真 DBAPI，退回 psql 只读通道。"""
    try:
        from sqlalchemy import text as _text

        from database.config import SessionLocal  # type: ignore[attr-defined]

        db = SessionLocal()
        db.execute(_text("SELECT 1"))
        return db, "sqlalchemy"
    except Exception:  # noqa: BLE001
        pass

    class _PsqlSession:
        transport = "psql"

        def execute(self, statement, params=None):
            from sqlalchemy.dialects import postgresql

            import re as _re

            bound = statement.bindparams(**{
                k: v for k, v in (params or {}).items() if k in statement._bindparams
            }) if params else statement
            sql = str(bound.compile(dialect=postgresql.dialect(),
                                    compile_kwargs={"literal_binds": True}))
            sql = _re.sub(r"(?<![\w.])goal(?=\s*,)",
                          "replace(replace(goal, E'\\\\n', ' '), E'\\\\r', ' ') AS goal",
                          sql, count=1)
            lines = _psql_rows(sql, with_header=True)
            if not lines:
                return _R([])
            header = [c.strip().lower() for c in lines[0]]
            return _R([dict(zip(header, row)) for row in lines[1:]])

        def close(self):
            return None

    _psql_rows("SELECT 1")
    return _PsqlSession(), "psql"


class _R:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


def _window(session, transport: str, limit: int = WINDOW) -> list[dict[str, Any]]:
    if transport == "psql":
        sql = ("SELECT id, status, replace(replace(goal, E'\\n', ' '), E'\\r', ' '), "
               "       parent_mission_id, created_at, finished_at "
               "FROM tensor_dev_missions WHERE organization_id = 1 "
               f"ORDER BY id DESC LIMIT {int(limit)}")
        cols = ["id", "status", "goal", "parent_mission_id", "created_at", "finished_at"]
        return [dict(zip(cols, row)) for row in _psql_rows(sql)]
    from sqlalchemy import text as _text

    rows = session.execute(_text(
        "SELECT id, status, goal, parent_mission_id, created_at, finished_at "
        "FROM tensor_dev_missions WHERE organization_id = 1 ORDER BY id DESC LIMIT :n"
    ), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="anchor-precheck 实库两例探针")
    ap.add_argument("--json-out", type=Path,
                    default=REPO / "artifacts" / "r17" / "anchor_precheck_live_evidence.json")
    ap.add_argument("--window", type=int, default=WINDOW)
    args = ap.parse_args(argv)

    from api import tensor_missions as tm  # noqa: E402
    from tools.mission_governance import anchor_dedup_gate as g  # noqa: E402

    evidence: dict[str, Any] = {
        "tool": "tools/mission_governance/anchor_precheck_live_probe.py",
        "endpoint": "GET /api/v1/tensor/missions/anchor-precheck",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "window": args.window,
        "cases": [],
        "verdict": "unknown",
    }
    try:
        session, transport = _live_session()
    except Exception as exc:  # noqa: BLE001
        evidence["verdict"] = "unavailable"
        evidence["error"] = str(exc)[:300]
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[UNAVAILABLE] 实库不可用：{exc}")
        return 1

    evidence["transport"] = transport
    rows = _window(session, transport, args.window)
    evidence["window_row_count"] = len(rows)

    terminal = {"succeeded", "failed", "stopped", "revoked", "orphan"}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        anchor = g.extract_anchor(row.get("goal"))
        if anchor:
            groups.setdefault(g.normalize_anchor(anchor), []).append(row)

    in_flight_case = failed_case = None
    for key, members in groups.items():
        non_derived = [m for m in members if not g.is_derived(m)]
        flying = [m for m in non_derived if str(m["status"]).lower() not in terminal]
        if flying and in_flight_case is None:
            in_flight_case = (key, flying)
        done = [m for m in non_derived if str(m["status"]).lower() in terminal]
        if (not flying and done and {str(m["status"]).lower() for m in done} == {"failed"}
                and failed_case is None):
            failed_case = (key, done)

    user = {"id": 1, "organization_id": 1, "username": "probe", "roles": ["ADMIN"]}
    expectations = [("in_flight", in_flight_case, "reject"),
                    ("failed_only", failed_case, "allow")]
    ok = True
    for label, case, expect in expectations:
        if case is None:
            evidence["cases"].append({"case": label, "status": "no_sample",
                                      "expected": expect,
                                      "note": "实库窗口内无此类锚点样本（如实记录，不伪造）"})
            ok = False
            continue
        key, members = case
        out = tm.anchor_precheck(anchor=key, db=session, user=user)
        passed = out["decision"] == expect
        ok = ok and passed
        if expect == "reject":
            passed = passed and bool(out.get("conflicts"))
            ok = ok and bool(out.get("conflicts"))
        evidence["cases"].append({
            "case": label,
            "anchor": key,
            "member_missions": [{"id": m["id"], "status": m["status"]} for m in members][:10],
            "expected_decision": expect,
            "decision": out["decision"],
            "conflicts": out.get("conflicts"),
            "reason": out.get("reason"),
            "readonly": out.get("readonly"),
            "pass": passed,
        })
        print(f"[{'OK' if passed else 'FAIL'}] {label}: anchor={key} -> {out['decision']}"
              f" conflicts={out.get('conflicts')}")

    session.close()
    evidence["verdict"] = "pass" if ok else "fail"
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdict={evidence['verdict']}  工件：{args.json_out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())