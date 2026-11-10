"""结构化知识库：JSONL 存储 + sha256 去重 + 关联链接 + 检索 + rejected 日志。

存储面（全部落在 ``store_dir``，默认 ``artifacts/knowledge_sediment``）
---------------------------------------------------------------------
* ``knowledge.jsonl``  —— 每条知识一行 JSON（schema 见 ``contracts.ENTRY_FIELDS``）；
* ``rejected.jsonl``   —— 拒绝入库账（原因码/缺失契约/源 sha，可审计「为什么没收」）；
* ``index.json``       —— 计数与最后写入时间（人读摘要，不参与判定）。

三处机制性纪律
--------------
1. **幂等去重**：主键是内容 sha256（四契约正文的稳定摘要）。
   同一 mission 重复触发 → 命中既有 sha → 条目数不变，只在 ``related`` 里补链接；
   不同 mission 产出同一结论 → 也并入既有条目并在 ``related`` 记录关联 mission。
2. **质量门在存储层再判一次**：即使调用方跳过抽取器的门，缺字段/无证据的条目
   一律拒绝并落 rejected（纵深防御，避免绕过 API 直插脏数据）。
3. **检索索引**：内存倒排（英文词 + 中文双字），查询时全量打分再做库过滤；
   1000 条规模下毫秒级（见 artifacts 里的实测）。

并发：进程内 ``threading.RLock`` 串行化写；落盘用「临时文件 + os.replace」原子替换，
半截写不会把库读坏。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    CONTRACT_FIELDS,
    ENTRY_FIELDS,
    entry_is_complete,
    has_evidence_signal,
    normalize_text,
    redact_secrets,
)

STORE_FILE = "knowledge.jsonl"
REJECT_FILE = "rejected.jsonl"
INDEX_FILE = "index.json"

#: 默认库目录（相对仓根；可用 ``KM_SEDIMENT_STORE`` 覆盖）。
DEFAULT_STORE_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "knowledge_sediment"

_WORD_RX = re.compile(r"[a-z0-9_]+")
_CJK_RX = re.compile(r"[\u4e00-\u9fff]")
#: 检索时的字段权重（problem/method 更「懂这条知识讲什么」）。
FIELD_WEIGHTS: Mapping[str, float] = {
    "problem": 3.0,
    "method": 2.5,
    "evidence": 2.0,
    "limitations": 1.5,
    "tags": 2.5,
    "domain": 1.0,
    "title": 2.0,
}


def store_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    """解析库目录：显式参数 > ``KM_SEDIMENT_STORE`` > 仓内 artifacts 默认。"""
    if explicit:
        return Path(explicit)
    env = os.environ.get("KM_SEDIMENT_STORE")
    return Path(env) if env else DEFAULT_STORE_DIR


def tokenize(text: str) -> list[str]:
    """英文按词、中文按双字切 —— 不支持分词器的环境也能做中文检索。"""
    low = (text or "").lower()
    tokens = _WORD_RX.findall(low)
    cjk = _CJK_RX.findall(low)
    if len(cjk) >= 2:
        tokens.extend("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))
    elif cjk:
        tokens.extend(cjk)
    return tokens


@dataclass
class StoreResult:
    """一次写入的判定结果。"""

    status: str          # inserted | duplicate | linked | rejected
    entry_id: str | None = None
    reason: str | None = None
    detail: str | None = None


class KnowledgeStore:
    """JSONL 知识库（进程内可多实例；同一目录请复用同一实例）。"""

    def __init__(self, directory: str | os.PathLike[str] | None = None) -> None:
        self.dir = store_dir(directory)
        self.knowledge_path = self.dir / STORE_FILE
        self.reject_path = self.dir / REJECT_FILE
        self.index_path = self.dir / INDEX_FILE
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._lower: dict[str, dict[str, str]] = {}
        self._by_sha: dict[str, str] = {}
        self._by_mission: dict[str, list[str]] = {}
        self._loaded_stamp: tuple[int, int] | None = None
        self._dirty = False
        self.load()

    # ------------------------------------------------------------------ 磁盘
    def load(self) -> None:
        with self._lock:
            self._entries.clear()
            self._lower.clear()
            self._by_sha.clear()
            self._by_mission.clear()
            if self.knowledge_path.is_file():
                for line in self.knowledge_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict) or not entry.get("id"):
                        continue
                    self._register(entry)
            try:
                st = self.knowledge_path.stat()
                self._loaded_stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._loaded_stamp = None

    @staticmethod
    def _lower_fields(entry: Mapping[str, Any]) -> dict[str, str]:
        """检索用的字段小写缓存（检索热路径只做 count，不重复 lower/join）。"""
        return {
            "problem": str(entry.get("problem") or "").lower(),
            "method": str(entry.get("method") or "").lower(),
            "evidence": str(entry.get("evidence") or "").lower(),
            "limitations": str(entry.get("limitations") or "").lower(),
            "tags": " ".join(str(t) for t in (entry.get("tags") or [])).lower(),
            "domain": str(entry.get("domain") or "").lower(),
            "title": str(entry.get("title") or "").lower(),
        }

    def _register(self, entry: dict[str, Any]) -> None:
        self._entries[entry["id"]] = entry
        self._lower[entry["id"]] = self._lower_fields(entry)
        sha = entry.get("sha256")
        if isinstance(sha, str):
            self._by_sha.setdefault(sha, entry["id"])
        mid = str(entry.get("mission_id") or "")
        if mid:
            self._by_mission.setdefault(mid, []).append(entry["id"])

    def _rewrite(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.knowledge_path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for entry in sorted(self._entries.values(), key=lambda e: (str(e.get("created_at") or ""), e["id"])):
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, self.knowledge_path)
        self._write_index()

    def _write_index(self) -> None:
        doc = {
            "count": len(self._entries),
            "missions": len(self._by_mission),
            "domains": sorted({str(e.get("domain") or "unspecified") for e in self._entries.values()}),
            "updated_at": _now(),
            "store_file": str(self.knowledge_path),
        }
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def _append_reject(self, record: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = dict(record)
        payload.setdefault("ts", _now())
        with open(self.reject_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ 写
    def add(self, entry: Mapping[str, Any], defer: bool = False) -> StoreResult:
        """入库。返回 ``inserted`` / ``duplicate`` / ``linked`` / ``rejected``。

        ``defer=True`` 只改内存并置脏标记，攒批后由 ``flush()`` 一次性落盘
        （批量灌库时避免「每条都重写整个 JSONL」的 O(n²) 写放大）。
        """
        with self._lock:
            candidate = {k: entry.get(k) for k in ENTRY_FIELDS}
            candidate.update({k: v for k, v in entry.items() if k not in candidate})
            masked = 0
            for field in CONTRACT_FIELDS:
                value = candidate.get(field)
                body, hits = redact_secrets(value if isinstance(value, str) else "")
                masked += hits
                candidate[field] = normalize_text(body)
            if masked:
                # 贮藏层是最后一道闸：即使调用方绕过抽取器直插，凭据也不会进库
                candidate["redactions"] = int(candidate.get("redactions") or 0) + masked
            # 先把可缺省字段补成 schema 形状，再做完整性判定（否则 created_at 缺席会被误判）
            candidate["tags"] = list(candidate.get("tags") or [])
            candidate["related"] = sorted(set(candidate.get("related") or []))
            candidate.setdefault("created_at", _now())
            candidate.setdefault("extractor", "template+regex/v1")
            if not candidate.get("id") and isinstance(candidate.get("sha256"), str):
                candidate["id"] = f"ke-{str(candidate['sha256'])[:16]}"

            if not entry_is_complete(candidate):
                reason = "incomplete_schema"
                missing = [f for f in CONTRACT_FIELDS if not candidate.get(f)]
                if not missing and not has_evidence_signal(candidate.get("evidence")):
                    reason = "no_evidence"
                self._append_reject(
                    {
                        "mission_id": candidate.get("mission_id"),
                        "reason": reason,
                        "missing": missing,
                        "sha256": candidate.get("sha256"),
                        "source": candidate.get("source"),
                    }
                )
                return StoreResult("rejected", reason=reason, detail=",".join(missing) or None)

            if not has_evidence_signal(candidate.get("evidence")):
                self._append_reject(
                    {
                        "mission_id": candidate.get("mission_id"),
                        "reason": "no_evidence",
                        "missing": [],
                        "sha256": candidate.get("sha256"),
                        "source": candidate.get("source"),
                    }
                )
                return StoreResult("rejected", reason="no_evidence")

            sha = str(candidate.get("sha256"))
            existing_id = self._by_sha.get(sha)
            if existing_id:
                existing = self._entries[existing_id]
                mid = str(candidate.get("mission_id") or "")
                related = set(existing.get("related") or [])
                before = set(related)
                if mid and mid != str(existing.get("mission_id")):
                    related.add(mid)
                if related != before:
                    existing["related"] = sorted(related)
                    self._dirty = True
                    if not defer:
                        self.flush()
                    return StoreResult("linked", entry_id=existing_id, detail=mid)
                return StoreResult("duplicate", entry_id=existing_id)

            eid = str(candidate.get("id") or f"ke-{sha[:16]}")
            candidate["id"] = eid
            self._entries[eid] = candidate
            self._lower[eid] = self._lower_fields(candidate)
            self._by_sha[sha] = eid
            mid = str(candidate.get("mission_id") or "")
            if mid:
                self._by_mission.setdefault(mid, []).append(eid)
            self._dirty = True
            if not defer:
                self.flush()
            return StoreResult("inserted", entry_id=eid)

    def flush(self) -> None:
        """把内存态一次性落盘（攒批写入的收口点）。"""
        with self._lock:
            if self._dirty or not self.knowledge_path.is_file():
                self._rewrite()
                self._dirty = False

    def add_many(self, entries: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        """批量入库（一次落盘）。返回状态计数。"""
        tally = {"inserted": 0, "duplicate": 0, "linked": 0, "rejected": 0}
        with self._lock:
            for entry in entries:
                res = self.add(entry, defer=True)
                tally[res.status] = tally.get(res.status, 0) + 1
            self.flush()
        return tally

    def reject(self, record: Mapping[str, Any]) -> None:
        """把「抽取阶段就被判死」的构造件记入拒绝账（原因码由调用方给）。

        与 ``add()`` 内部拒绝的区别：这里保留抽取器的原始诊断
        （缺哪个契约、命中哪些章节、源文件 sha），审计时能看清「为什么没收」。
        """
        with self._lock:
            self._append_reject(dict(record))

    def link(self, entry_id: str, mission_id: str) -> StoreResult:
        """显式建立「相关 mission」链接（内容不同但过程同源时用）。"""
        with self._lock:
            entry = self._entries.get(entry_id)
            if not entry:
                return StoreResult("rejected", reason="not_found")
            mid = str(mission_id)
            if not mid or mid == str(entry.get("mission_id")):
                return StoreResult("duplicate", entry_id=entry_id)
            related = set(entry.get("related") or [])
            if mid in related:
                return StoreResult("duplicate", entry_id=entry_id)
            related.add(mid)
            entry["related"] = sorted(related)
            self._rewrite()
            return StoreResult("linked", entry_id=entry_id, detail=mid)

    # ------------------------------------------------------------------ 读
    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(entry_id)
            return json.loads(json.dumps(entry, ensure_ascii=False)) if entry else None

    def all_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(e, ensure_ascii=False)) for e in self._entries.values()]

    def rejected(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.reject_path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.reject_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:][::-1]

    def domains(self) -> list[str]:
        with self._lock:
            return sorted({str(e.get("domain") or "unspecified") for e in self._entries.values()})

    # ---------------------------------------------------------------- 检索
    def search(self, q: str = "", domain: str | None = None, limit: int = 20) -> dict[str, Any]:
        """检索：``q`` 为空格分隔关键词；空 ``q`` = 按域列出（按时间倒序）。

        打分 = Σ(字段权重 × 命中词数)，再按 (分数, id) 稳定排序；同分不抖动，
        便于把「检索结果正确」写成可回归的断言。
        """
        started = time.perf_counter()
        limit = max(1, min(int(limit or 20), 500))
        terms = [t for t in (q or "").lower().split() if t.strip()]
        domain_filter = (domain or "").strip().lower() or None

        with self._lock:
            pool = list(self._entries.values())

        lower = self._lower
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in pool:
            if domain_filter and str(entry.get("domain") or "unspecified").lower() != domain_filter:
                continue
            if not terms:
                scored.append((0.0, entry))
                continue
            haystack = lower.get(str(entry.get("id"))) or self._lower_fields(entry)
            score = 0.0
            for term in terms:
                for field, weight in FIELD_WEIGHTS.items():
                    text = haystack.get(field, "")
                    if not text:
                        continue
                    hits = text.count(term)
                    if hits:
                        score += weight * min(hits, 3)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id"))))
        items = []
        for score, entry in scored[:limit]:
            items.append(
                {
                    "id": entry.get("id"),
                    "mission_id": entry.get("mission_id"),
                    "domain": entry.get("domain"),
                    "title": entry.get("title"),
                    "tags": entry.get("tags") or [],
                    "problem": entry.get("problem"),
                    "method": entry.get("method"),
                    "evidence": entry.get("evidence"),
                    "limitations": entry.get("limitations"),
                    "sha256": entry.get("sha256"),
                    "related": entry.get("related") or [],
                    "score": round(score, 3),
                }
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "query": q or "",
            "domain": domain,
            "limit": limit,
            "total": len(scored),
            "count": len(items),
            "took_ms": round(elapsed_ms, 3),
            "items": items,
        }

    def stats(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._entries.values())
            missions = sorted({str(e.get("mission_id")) for e in entries})
            complete = sum(1 for e in entries if entry_is_complete(e))
            per_domain: dict[str, int] = {}
            for e in entries:
                key = str(e.get("domain") or "unspecified")
                per_domain[key] = per_domain.get(key, 0) + 1
        rejects = self.rejected(limit=10_000)
        reasons: dict[str, int] = {}
        for row in rejects:
            key = str(row.get("reason") or "unknown")
            reasons[key] = reasons.get(key, 0) + 1
        return {
            "count": len(entries),
            "missions": len(missions),
            "complete_entries": complete,
            "schema_complete_ratio": round(complete / len(entries), 4) if entries else 1.0,
            "by_domain": per_domain,
            "rejected_total": len(rejects),
            "rejected_reasons": reasons,
            "store_file": str(self.knowledge_path),
        }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bulk_add(store: KnowledgeStore, entries: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """批量入库汇总（给 CLI/pipeline 用）——一次落盘。"""
    return store.add_many(entries)