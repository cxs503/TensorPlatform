"""append-only 审计链：每条记录带前序哈希，事后可校验未被篡改/未缺行。

为什么不是普通日志：合并口是**治理动作**（会把 PR 并进 main）。owner 纪律要求
「治理预算内动作全落审计」且「带回滚路径」。哈希链是最小成本的防篡改证据：
任何一条被改写或删除，``verify`` 立刻指出第一条断链。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

GENESIS = "0" * 64


def _canon(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _digest(prev: str, seq: int, ts: str, kind: str, payload: Any) -> str:
    return hashlib.sha256(
        ("%s|%d|%s|%s|%s" % (prev, seq, ts, kind, _canon(payload)))
        .encode("utf-8")).hexdigest()


class AuditLog:
    """JSONL 哈希链审计账本（append-only，fsync 落盘）。"""

    def __init__(self, path: str, *, fsync: bool = True):
        self.path = str(path)
        self.fsync = fsync
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._seq = 0
        self._last = GENESIS
        self._load_tail()

    # -- 内部 ---------------------------------------------------------------
    def _load_tail(self) -> None:
        if not os.path.exists(self.path):
            return
        last = None
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if last:
            rec = json.loads(last)
            self._seq = int(rec["seq"])
            self._last = rec["hash"]

    # -- 写 -----------------------------------------------------------------
    def append(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        seq = self._seq + 1
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        h = _digest(self._last, seq, ts, kind, payload)
        rec = {"seq": seq, "ts": ts, "kind": kind, "payload": payload,
               "prev": self._last, "hash": h}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(_canon(rec) + "\n")
            fh.flush()
            if self.fsync:
                os.fsync(fh.fileno())
        self._seq, self._last = seq, h
        return rec

    # -- 读 -----------------------------------------------------------------
    def records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    @property
    def head(self) -> Tuple[int, str]:
        return self._seq, self._last


def verify(path: str) -> Dict[str, Any]:
    """校验哈希链；返回 {ok, records, first_bad, head}。"""
    if not os.path.exists(path):
        return {"ok": False, "records": 0, "first_bad": None,
                "error": "audit file missing", "head": None}
    prev, seq_expect = GENESIS, 1
    n, first_bad = 0, None
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            n = i
            rec = json.loads(line)
            want = _digest(prev, int(rec.get("seq", -1)), rec.get("ts", ""),
                           rec.get("kind", ""), rec.get("payload"))
            if rec.get("prev") != prev or rec.get("hash") != want \
                    or int(rec.get("seq", -1)) != seq_expect:
                first_bad = {"line": i, "reason": "chain_break"}
                break
            prev, seq_expect = rec["hash"], seq_expect + 1
    return {"ok": first_bad is None, "records": n, "first_bad": first_bad,
            "head": prev if first_bad is None else None}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sleep_until(deadline: float) -> None:
    time.sleep(max(0.0, deadline - time.time()))


def _unused(_x: Optional[int] = None) -> None:  # pragma: no cover
    return None