"""无驱动环境下的取证连接（loong64 沙箱 psycopg2 只是 stub）。

本仓守护进程线一律走 ``runuser -u postgres -- psql``；本模块把这条通道包成
SQLAlchemy ``Connection`` 的最小鸭子面（``execute(clause, params)`` /
``.mappings().all()|.first()`` / ``.scalar()`` / ``.commit()``），让
``tools.tensorbench_eval.store`` 的同一套代码在「有驱动」和「只有 psql」两种
环境里都能落库。**不是**通用 DBAPI：只支持命名参数 + 本模块用到的语句形态。
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from typing import Any, Dict, List, Mapping, Sequence

SEP = "\x01"


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "'" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def render(sql: str, params: Mapping[str, Any] | None) -> str:
    """命名参数 → SQL 字面量（按名字长度倒序替换，避免 :a 吃掉 :ab）。"""
    if not params:
        return sql
    out = sql
    for key in sorted(params, key=len, reverse=True):
        out = out.replace(":" + key, _literal(params[key]))
    return out


class Result:
    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[str]]):
        self.columns = list(columns)
        self.rows = [list(r) for r in rows]

    def mappings(self) -> "Result":
        return self

    def all(self) -> List[Dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def scalar(self):
        row = self.rows[0] if self.rows else None
        return None if not row else row[0]


class PsqlConnection:
    """最小连接面；每条语句一次 psql（自带事务，等价 autocommit）。"""

    def __init__(self, database: str = "tensorplatform", user: str = "postgres",
                 psql: str = "/usr/bin/psql", runner: str = "runuser"):
        self.database, self.user, self.psql, self.runner = database, user, psql, runner

    def _run(self, sql: str) -> Result:
        cmd = ([self.runner, "-u", self.user, "--", self.psql, "-d", self.database,
                "--csv", "-A", "-F", SEP, "-v", "ON_ERROR_STOP=1", "-c", sql])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"psql error: {proc.stderr.strip()[:400]}")
        lines = [ln for ln in proc.stdout.splitlines() if ln or True]
        if not lines:
            return Result([], [])
        header, body = lines[0].split(SEP), lines[1:]
        return Result(header, [ln.split(SEP) for ln in body if ln])

    def execute(self, clause, params: Mapping[str, Any] | None = None) -> Result:
        sql = clause.text if hasattr(clause, "text") else str(clause)
        return self._run(render(sql, params))

    def commit(self) -> None:  # 每条语句已提交
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> "PsqlConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def connect(database: str | None = None) -> PsqlConnection:
    import os
    return PsqlConnection(database=os.environ.get("TP_PG_DB", "tensorplatform"))
