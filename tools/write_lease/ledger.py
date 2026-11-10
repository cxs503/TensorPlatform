#!/usr/bin/env python3
"""append-only 事件台账（JSONL）+ 等待图重建。

纪律
----
* 台账是**唯一审计源**：等待图、回收、CAS 拒绝都能从台账逐行复算出当时的图；
* 单条事件单次 ``write()`` 追加（``O_APPEND``），写入不含墙钟以外的可变量；
* 重建函数是纯函数，同一台账 + 同一截断点 → 逐字节相同的图摘要（D7 依据）。
"""
from __future__ import annotations

import json
import os

from . import model


class Ledger:
    """文件台账：每行一条事件，追加写。"""

    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # 常驻 fd（O_APPEND）：热路径上每条事件只付一次 write()，不再 open/close。
        self._fd: int | None = None

    def append(self, kind: str, **fields) -> dict:
        ev = model.event(kind, **fields)
        line = json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n"
        if self._fd is None:
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        # O_APPEND 单次 write：多进程追加不交错（≤4KiB 的单行）
        os.write(self._fd, line.encode("utf-8"))
        return ev

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def read(self) -> list[dict]:
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue          # 半行（崩溃）→ 跳过（fail-open 只影响审计行数）
        except FileNotFoundError:
            return []
        return out


def live_edges(events, at: int | None = None, now: float | None = None) -> list[dict]:
    """截至第 ``at`` 条事件的**活跃等待边**（waiter → blocker）。

    边的生命周期：``wait_start`` 登记，``wait_end`` 或该 waiter 的 ``acquire_key``
    撤销；边还带 ``deadline``，超时未撤销视为陈旧（等待者已放弃/进程已死）。
    """
    evs = events if at is None else events[:at]
    now = model.now_ts() if now is None else now
    live: dict[tuple[str, str], dict] = {}
    for ev in evs:
        kind = ev.get("kind")
        if kind == model.WAIT_START:
            live[(ev["waiter"], ev["key"])] = {
                "waiter": ev["waiter"],
                "blocker": ev["blocker"],
                "key": ev["key"],
                "holder": ev.get("holder"),
                "blocker_holder": ev.get("blocker_holder"),
                "since": ev.get("t"),
                "deadline": ev.get("t", 0.0) + float(ev.get("wait_timeout", 0.0)),
                "seq": ev.get("seq", len(live)),
            }
        elif kind == model.WAIT_END:
            live.pop((ev["waiter"], ev["key"]), None)
        elif kind == model.ACQUIRE_KEY:
            # 该键拿到 → 对同一键的等待边自然终止
            for k in [k for k in live if k[0] == ev["waiter"] and live[k]["key"] == ev["key"]]:
                live.pop(k, None)
    stale = [k for k, e in live.items() if e["deadline"] and now > e["deadline"]]
    for k in stale:
        live.pop(k, None)
    return sorted(live.values(), key=lambda e: (e["waiter"], e["key"]))


def wait_graph(events, at: int | None = None, now: float | None = None) -> dict[str, list[str]]:
    """活跃等待图：``{waiter_lease: [blocker_lease, ...]}``（去重、升序）。"""
    graph: dict[str, set[str]] = {}
    for edge in live_edges(events, at=at, now=now):
        graph.setdefault(edge["waiter"], set()).add(edge["blocker"])
    return {k: sorted(v) for k, v in sorted(graph.items())}


def reconstruct_wait_graph(ledger: Ledger | str, at: int | None = None, now: float | None = None) -> dict:
    """D7 入口：从台账重建某时刻的等待图 + 可复算摘要。"""
    if isinstance(ledger, Ledger):
        events = ledger.read()
        src = ledger.path
    else:
        events = Ledger(ledger).read()
        src = str(ledger)
    graph = wait_graph(events, at=at, now=now)
    return {
        "ledger": src,
        "at_seq": len(events) if at is None else at,
        "events_total": len(events),
        "graph": graph,
        "digest": model.digest(graph),
    }