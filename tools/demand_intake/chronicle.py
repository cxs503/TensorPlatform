"""chronicle —— 铸造/审计事件留痕（``chronicle_snapshots``）。

纪律：留痕失败**不能**反过来卡主链（铸造已经落库、单子已经建好，写不进 chronicle
不能因此回滚业务）。所以这里吞 ``StoreError`` 并把失败原因回传给调用方统计，而不是
抛异常。代价是「chronicle 静默丢失」——故 :func:`record` 返回值必须进审计摘要
（``chronicle_errors``），让漏记在报告里可见，而不是无声。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .store import StoreError, Transport, q, sql_json


def record(t: Transport, phase: str, payload: Dict[str, Any], *,
           schema: str = "") -> Optional[str]:
    """追加一条 ``{"kind": "demand_intake_v2", "phase": ...}`` 事件；返回错误串或 None。"""
    body = {"kind": "demand_intake_v2", "phase": phase}
    body.update(payload)
    sql = (f"INSERT INTO {q('chronicle_snapshots', schema)} (ts, payload) "
           f"VALUES (now(), {sql_json(body)});")
    try:
        t.execute(sql)
    except StoreError as exc:            # 留痕不能反过来卡主链
        return str(exc)[:200]
    return None


def fetch(t: Transport, *, limit: int = 20, schema: str = "") -> list:
    """最近 chronicle 事件（只读；phase 过滤交调用方）。"""
    return t.query(
        f"SELECT ts, payload FROM {q('chronicle_snapshots', schema)} "
        f"ORDER BY id DESC LIMIT {int(limit)}")