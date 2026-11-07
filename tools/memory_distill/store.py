"""append-only 候选池：判例只追加、不覆写、不删改。

不变量（每次 append 自检，违反即抛 ``AppendOnlyViolation``）：
  I1 追加前文件字节前缀哈希 == 追加后同长度前缀哈希（既有内容一字不变）
  I2 同一 fingerprint 只在池内出现一次（幂等；重复提交静默跳过并计数）
  I3 每条记录自带 citations 计数与 gate 裁决，便于事后审计/抽样

池文件=JSONL，一行一条 candidate（含 gate 裁决），可被下轮 distill 重放。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Iterable, Sequence

from .citations import extract_citations
from .distill import Candidate


class AppendOnlyViolation(RuntimeError):
    """既有内容被改动/截断——append-only 纪律被破坏。"""


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class AppendOnlyStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8"):
                pass
        with open(path, "rb") as f:
            self._prefix_hash = _sha256_bytes(f.read())
        self._size = os.path.getsize(path)

    # ---------------- 读 ----------------
    def load(self) -> list[dict]:
        out: list[dict] = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def fingerprints(self) -> set[str]:
        return {rec.get("candidate_id", "") for rec in self.load()}

    def lessons(self) -> list[str]:
        return [rec.get("lesson", "") for rec in self.load() if rec.get("lesson")]

    def __len__(self) -> int:
        return len(self.load())

    # ---------------- 写 ----------------
    def verify_prefix(self) -> None:
        """I1 自检：现有前缀哈希必须与上次 append 后一致。"""
        with open(self.path, "rb") as f:
            head = f.read(self._size)
        if _sha256_bytes(head) != self._prefix_hash:
            raise AppendOnlyViolation(f"前缀被改动: {self.path}")

    def append(self, records: Iterable[dict]) -> dict:
        """追加记录；返回 {appended, skipped_dup}。既有字节前缀不变（自检）。"""
        self.verify_prefix()
        known = self.fingerprints()
        appended = 0
        skipped = 0
        buf: list[str] = []
        for rec in records:
            fp = rec.get("candidate_id") or ""
            if fp in known:
                skipped += 1
                continue
            known.add(fp)
            buf.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))
            appended += 1
        if buf:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(buf) + "\n")
            self._size = os.path.getsize(self.path)
            with open(self.path, "rb") as f:
                self._prefix_hash = _sha256_bytes(f.read())
        return {"appended": appended, "skipped_dup": skipped}

    def append_candidates(
        self, candidates: Sequence[Candidate], decisions: Sequence[dict] | None = None
    ) -> dict:
        """把候选 + 门控裁决一起入库（正文重算引用计数，防上游篡改计数）。"""
        records = []
        for i, cand in enumerate(candidates):
            rec = cand.to_dict()
            rec["citations"] = extract_citations(cand.lesson).as_dict()
            if decisions is not None and i < len(decisions):
                rec["gate"] = decisions[i]
            records.append(rec)
        return self.append(records)

    def digest(self) -> str:
        with open(self.path, "rb") as f:
            return _sha256_bytes(f.read())