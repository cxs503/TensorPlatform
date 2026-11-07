"""只追加哈希链留痕账本（append-only, hash-chained JSONL）。

为什么不是普通日志
------------------
ABM 的「全程留痕」要求事后可**独立复算**：任何一条记录被改写、删除或插入，
都必须能被机器发现。故每条记录的 ``hash`` = sha256(canonical(记录本体 + ``prev``))，
``prev`` 为前一条的 hash（首条为 64 个 0）。``verify()`` 重算整条链并给出首个断点。

确定性
------
``ts`` 由调用方给出（内容派生时钟），账本不自造 wall clock；同一输入两次运行
逐字节相同（``sort_keys=True``、固定分隔符、``ensure_ascii=False``）。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional

GENESIS = "0" * 64


def canonical_json(obj: Any) -> str:
    """规范化 JSON：键排序、紧凑分隔符、不转义非 ASCII（跨语言可复算）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_hex(obj: Any) -> str:
    """对任意对象取内容哈希（先规范化 JSON）。字符串输入按 UTF-8 原文哈希。"""
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(bytes(obj)).hexdigest()
    if isinstance(obj, str):
        return hashlib.sha256(obj.encode("utf-8")).hexdigest()
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class TraceLedger:
    """哈希链账本。每条记录一行 JSONL，落到 ``path``；内存里保留同序副本。"""

    def __init__(self, path: str, reset: bool = True):
        self.path = path
        self._records: List[Dict[str, Any]] = []
        if reset and os.path.exists(path):
            os.remove(path)
        elif not reset and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._records.append(json.loads(line))

    # ------------------------------------------------------------------ append
    def append(
        self,
        record_type: str,
        actor_role: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        gate: Optional[str] = None,
        verdict: Optional[str] = None,
        ts: Optional[str] = None,
        evidence_refs: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """追加一条记录并返回它（含 hash）。payload 必须是 JSON 可序列化 dict。"""
        body = {
            "seq": len(self._records),
            "record_type": str(record_type),
            "actor_role": str(actor_role),
            "gate": None if gate is None else str(gate),
            "verdict": None if verdict is None else str(verdict),
            "ts": ts,
            "evidence_refs": sorted([str(x) for x in (evidence_refs or [])]),
            "payload": payload or {},
        }
        body["payload_sha256"] = sha256_hex(body["payload"])
        body["prev"] = self._records[-1]["hash"] if self._records else GENESIS
        body["hash"] = sha256_hex({k: v for k, v in body.items() if k != "hash"})
        self._records.append(body)
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(canonical_json(body) + "\n")
        return body

    # ------------------------------------------------------------------ read
    @property
    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def of_role(self, role: str) -> List[Dict[str, Any]]:
        return [r for r in self._records if r["actor_role"] == role]

    def of_gate(self, gate: str) -> List[Dict[str, Any]]:
        return [r for r in self._records if r["gate"] == gate]

    def first_seq(self, predicate) -> Optional[int]:
        """返回首个满足谓词的记录 seq（无则 None）。谓词收到记录 dict。"""
        for rec in self._records:
            if predicate(rec):
                return int(rec["seq"])
        return None

    # ------------------------------------------------------------------ verify
    def verify(self) -> Dict[str, Any]:
        """重算整条链。返回 {ok, n, first_bad_seq, reason, digest}。"""
        prev = GENESIS
        for expected_seq, rec in enumerate(self._records):
            if rec.get("seq") != expected_seq:
                return {"ok": False, "n": len(self._records), "first_bad_seq": expected_seq,
                        "reason": "seq 不连续", "digest": None}
            if rec.get("prev") != prev:
                return {"ok": False, "n": len(self._records), "first_bad_seq": expected_seq,
                        "reason": "prev 指针不匹配", "digest": None}
            recomputed = sha256_hex({k: v for k, v in rec.items() if k != "hash"})
            if recomputed != rec.get("hash"):
                return {"ok": False, "n": len(self._records), "first_bad_seq": expected_seq,
                        "reason": "本体哈希不匹配（记录被改写）", "digest": None}
            if sha256_hex(rec.get("payload") or {}) != rec.get("payload_sha256"):
                return {"ok": False, "n": len(self._records), "first_bad_seq": expected_seq,
                        "reason": "payload 哈希不匹配", "digest": None}
            prev = rec["hash"]
        digest = sha256_hex("|".join(r["hash"] for r in self._records)) if self._records else sha256_hex("")
        return {"ok": True, "n": len(self._records), "first_bad_seq": None, "reason": None, "digest": digest}

    def export(self) -> Dict[str, Any]:
        """账本导出（含逐条 hash 与整体 digest），供跳链审计与落库。"""
        return {
            "path": self.path,
            "n_records": len(self._records),
            "tip_hash": self._records[-1]["hash"] if self._records else GENESIS,
            "chain": self.verify(),
            "records": self._records,
        }