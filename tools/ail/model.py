"""AIL 数据结构与规范化：MemoryItem / EvidenceRef / Relation / SessionState / Bundle。

字段与约束逐条对应 docs/specs/AIL_DATA_SPEC.md；违反约束一律抛 AILSchemaError，
不做静默截断或补齐（截断会改变语义，见 SPEC §2）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import AIL_VERSION, SCHEMA_VERSION


class AILError(RuntimeError):
    """AIL 基础错误：调用方必须显式处理。"""


class AILSchemaError(AILError):
    """字段/取值违反 SPEC。"""


class AILChecksumError(AILError):
    """canonical 校验和不符：拒绝加载，绝不静默重建（SPEC §9.8）。"""


class AILUpdateError(AILError):
    """MemoryUpdate 非法（引用不存在 / 越权验证 / 部分应用被拒）。"""


# ── canonical JSON（与 Soul soul_runtime.canonical 逐字一致）─────────────────
def canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def checksum(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


# ── 枚举 ──────────────────────────────────────────────────────────────────
ITEM_KINDS = ("state", "next_action", "decision", "evidence", "constraint", "fact",
              "lesson", "open_question")
KIND_ORDER = {k: i for i, k in enumerate(ITEM_KINDS)}
ITEM_STATUSES = ("active", "superseded", "retired")
VERIFICATION_STATUSES = ("passed", "failed", "unknown", "observed")
SENSITIVITIES = ("private", "organization", "shared")
EVIDENCE_KINDS = ("git_commit", "file", "json_pointer", "artifact", "mission", "test_run", "url")
RELATION_TYPES = ("supports", "contradicts", "supersedes", "derived_from", "refers_to", "blocks")
PHASES = ("planning", "executing", "blocked", "awaiting_acceptance", "done")
UPDATE_OPS = ("add", "supersede", "retire", "attest", "reverify", "noop")
INTENTS = ("state", "decision", "evidence", "next_action", "recall")

_TOPIC_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_ITEM_ID_RE = re.compile(r"^mi_[0-9a-f]{16}$")
_EVIDENCE_ID_RE = re.compile(r"^ev_[0-9a-f]{16}$")
_RELATION_ID_RE = re.compile(r"^rel_[0-9a-f]{16}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise AILSchemaError(message)


def _check_utc(value: str | None, field_name: str, *, allow_none: bool = False) -> None:
    if value is None:
        _require(allow_none, f"{field_name} 不允许为空")
        return
    _require(isinstance(value, str) and bool(_UTC_RE.match(value)),
             f"{field_name} 必须是 ISO-8601 UTC（形如 2026-10-23T06:40:00Z），实际 {value!r}")


def _check_single_line(value: str, field_name: str, max_len: int) -> None:
    _require(isinstance(value, str), f"{field_name} 必须是字符串")
    _require("\n" not in value and "\r" not in value, f"{field_name} 必须是单行（禁止换行）")
    _require(1 <= len(value) <= max_len,
             f"{field_name} 长度必须是 1..{max_len} 码点，实际 {len(value)}")


def _check_scalar_value(value: Any, field_name: str = "value") -> None:
    if value is None or isinstance(value, (bool, int, float)):
        _require(not (isinstance(value, float) and (value != value or value in (float("inf"), float("-inf")))),
                 f"{field_name} 禁止 NaN/Infinity")
        return
    if isinstance(value, str):
        _require(len(value) <= 120, f"{field_name} 字符串长度必须 <= 120 码点，实际 {len(value)}")
        _require("\n" not in value and "\r" not in value, f"{field_name} 字符串必须单行")
        return
    if isinstance(value, (list, tuple)):
        _require(1 <= len(value) <= 16, f"{field_name} 数组长度必须是 1..16，实际 {len(value)}")
        kinds = {type(v) for v in value}
        _require(len(kinds) == 1, f"{field_name} 数组必须同质，实际 {sorted(str(k) for k in kinds)}")
        for v in value:
            _require(not isinstance(v, (dict, list, tuple)), f"{field_name} 数组元素必须是标量")
            _check_scalar_value(v, field_name + "[]")
        return
    raise AILSchemaError(f"{field_name} 类型不受支持：{type(value).__name__}（禁止嵌套对象）")


# ── EvidenceRef ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    kind: str
    locator: str
    sha256: str | None
    excerpt: str | None
    retrieved_at: str

    @staticmethod
    def make(*, kind: str, locator: str, sha256: str | None, excerpt: str | None,
             retrieved_at: str) -> "EvidenceRef":
        _require(kind in EVIDENCE_KINDS, f"evidence.kind 非法：{kind!r}")
        _check_single_line(locator, "evidence.locator", 300)
        if sha256 is not None:
            _require(bool(_SHA_RE.match(sha256)), "evidence.sha256 必须是 64 位小写十六进制")
        _require(kind not in ("file", "artifact") or sha256 is not None,
                 f"evidence.kind={kind} 必须带 sha256（否则不可核验）")
        if excerpt is not None:
            _check_single_line(excerpt, "evidence.excerpt", 300)
        _check_utc(retrieved_at, "evidence.retrieved_at")
        eid = "ev_" + checksum({"kind": kind, "locator": locator, "sha256": sha256})[:16]
        return EvidenceRef(eid, kind, locator, sha256, excerpt, retrieved_at)

    def to_dict(self) -> dict:
        return {"evidence_id": self.evidence_id, "kind": self.kind, "locator": self.locator,
                "sha256": self.sha256, "excerpt": self.excerpt,
                "retrieved_at": self.retrieved_at}

    @staticmethod
    def from_dict(payload: dict) -> "EvidenceRef":
        expected = {"evidence_id", "kind", "locator", "sha256", "excerpt", "retrieved_at"}
        missing = expected - set(payload)
        _require(not missing, f"EvidenceRef 缺字段：{sorted(missing)}")
        extra = set(payload) - expected
        _require(not extra, f"EvidenceRef 含未定义字段：{sorted(extra)}")
        _require(bool(_EVIDENCE_ID_RE.match(payload["evidence_id"])),
                 f"evidence_id 非法：{payload['evidence_id']!r}")
        return EvidenceRef.make(kind=payload["kind"], locator=payload["locator"],
                                sha256=payload["sha256"], excerpt=payload["excerpt"],
                                retrieved_at=payload["retrieved_at"])


# ── MemoryItem ────────────────────────────────────────────────────────────
@dataclass
class MemoryItem:
    item_id: str
    kind: str
    topic: str
    value: Any
    text: str
    status: str
    verification: dict
    source: dict
    evidence_refs: list[str]
    scope: dict
    sensitivity: str
    valid_from: str
    valid_to: str | None
    recorded_at: str
    revision: int
    supersedes: list[str]
    salience: int
    idempotency_key: str
    tags: list[str] = field(default_factory=list)
    stale: bool = False

    # 构造入口：所有校验集中在这里，构造成功即保证 SPEC 合规。
    @staticmethod
    def make(*, kind: str, topic: str, value: Any, text: str, source: dict,
             retrieved_at: str, scope: dict | None = None, sensitivity: str = "organization",
             verification: dict | None = None, evidence_refs: list[str] | None = None,
             valid_from: str | None = None, valid_to: str | None = None,
             supersedes: list[str] | None = None, salience: int = 50,
             idempotency_key: str, tags: list[str] | None = None) -> "MemoryItem":
        _require(kind in ITEM_KINDS or kind.startswith("x-"),
                 f"kind 非法：{kind!r}（未知 kind 需 x- 前缀）")
        _require(bool(_TOPIC_RE.match(topic)) and len(topic) <= 120,
                 f"topic 非法：{topic!r}（须为小写点分，如 platform.guardian.count）")
        _check_scalar_value(value)
        _check_single_line(text, "text", 400)
        _check_single_line(idempotency_key, "idempotency_key", 200)

        src_keys = {"kind", "locator", "sha256", "retrieved_at"}
        _require(set(source) == src_keys, f"source 字段必须是 {sorted(src_keys)}，实际 {sorted(source)}")
        _require(source["kind"] in EVIDENCE_KINDS,
                 f"source.kind 非法：{source['kind']!r}")
        _check_single_line(source["locator"], "source.locator", 300)
        if source["sha256"] is not None:
            _require(bool(_SHA_RE.match(source["sha256"])), "source.sha256 必须是 64 位小写十六进制")
        _check_utc(source["retrieved_at"], "source.retrieved_at")

        scope = scope or {"agent_id": None, "session_id": None, "mission_id": None}
        _require(set(scope) == {"agent_id", "session_id", "mission_id"},
                 "scope 字段必须是 agent_id/session_id/mission_id")
        _require(sensitivity in SENSITIVITIES, f"sensitivity 非法：{sensitivity!r}")

        verification = verification or {"status": "unknown", "validator": None, "checked_at": None}
        _require(set(verification) == {"status", "validator", "checked_at"},
                 "verification 字段必须是 status/validator/checked_at")
        _require(verification["status"] in VERIFICATION_STATUSES,
                 f"verification.status 非法：{verification['status']!r}")
        _check_utc(verification["checked_at"], "verification.checked_at", allow_none=True)
        # passed/failed 是**判定**，必须有 validator；observed 是原始观察，允许无 validator；
        # unknown 表示未知（绝不等于 failed）。
        if verification["status"] in ("passed", "failed"):
            _require(bool(verification["validator"]),
                     "verification.status=passed/failed 时必须给 validator（否则等于无依据声称已验）")
        _require(not (sensitivity == "shared" and verification["status"] != "passed"),
                 "sensitivity=shared 只允许 verification.status=passed（对齐 Soul CHECK 约束）")

        refs = list(evidence_refs or [])
        _require(len(refs) <= 8, f"evidence_refs 最多 8 条，实际 {len(refs)}")
        for r in refs:
            _require(bool(_EVIDENCE_ID_RE.match(r)), f"evidence_refs 元素非法：{r!r}")
        if verification["status"] in ("passed", "failed"):
            _require(len(refs) >= 1, "verification.status=passed/failed 时 evidence_refs 不得为空")

        valid_from = valid_from or retrieved_at
        _check_utc(valid_from, "valid_from")
        _check_utc(valid_to, "valid_to", allow_none=True)

        _require(isinstance(salience, int) and 0 <= salience <= 100,
                 f"salience 必须是 0..100 整数，实际 {salience!r}")
        supersedes = list(supersedes or [])
        _require(all(isinstance(s, str) for s in supersedes), "supersedes 元素必须是字符串")
        tags = list(tags or [])
        _require(len(tags) <= 12, "tags 最多 12 个")
        for t in tags:
            _check_single_line(t, "tags[]", 40)

        item_id = "mi_" + checksum({"kind": kind, "topic": topic, "value": value,
                                    "scope": scope,
                                    "idempotency_key": idempotency_key})[:16]
        return MemoryItem(item_id=item_id, kind=kind, topic=topic, value=value, text=text,
                          status="active", verification=dict(verification),
                          source=dict(source), evidence_refs=refs, scope=dict(scope),
                          sensitivity=sensitivity, valid_from=valid_from, valid_to=valid_to,
                          recorded_at=retrieved_at, revision=1, supersedes=list(supersedes),
                          salience=salience, idempotency_key=idempotency_key, tags=tags)

    def to_dict(self) -> dict:
        # 可选字段显式写 null（SPEC §9.5），字段集固定（SPEC §2 additionalProperties=false）。
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "topic": self.topic,
            "value": self.value,
            "text": self.text,
            "status": self.status,
            "verification": dict(self.verification),
            "source": dict(self.source),
            "evidence_refs": list(self.evidence_refs),
            "scope": dict(self.scope),
            "sensitivity": self.sensitivity,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "recorded_at": self.recorded_at,
            "revision": self.revision,
            "supersedes": list(self.supersedes),
            "salience": self.salience,
            "idempotency_key": self.idempotency_key,
            "tags": list(self.tags),
            "stale": self.stale,
        }

    @staticmethod
    def from_dict(payload: dict) -> "MemoryItem":
        expected = {"item_id", "kind", "topic", "value", "text", "status", "verification",
                    "source", "evidence_refs", "scope", "sensitivity", "valid_from",
                    "valid_to", "recorded_at", "revision", "supersedes", "salience",
                    "idempotency_key", "tags", "stale"}
        missing = expected - set(payload)
        _require(not missing, f"MemoryItem 缺字段：{sorted(missing)}")
        extra = set(payload) - expected
        _require(not extra, f"MemoryItem 含未定义字段：{sorted(extra)}")
        item = MemoryItem.make(kind=payload["kind"], topic=payload["topic"],
                               value=payload["value"], text=payload["text"],
                               source=payload["source"],
                               retrieved_at=payload["source"]["retrieved_at"],
                               scope=payload["scope"], sensitivity=payload["sensitivity"],
                               verification=payload["verification"],
                               evidence_refs=payload["evidence_refs"],
                               valid_from=payload["valid_from"], valid_to=payload["valid_to"],
                               supersedes=payload["supersedes"], salience=payload["salience"],
                               idempotency_key=payload["idempotency_key"], tags=payload["tags"])
        _require(item.item_id == payload["item_id"],
                 f"item_id 与内容不匹配（可复算性被破坏）：{payload['item_id']!r} != {item.item_id!r}")
        _require(payload["status"] in ITEM_STATUSES, f"status 非法：{payload['status']!r}")
        item.status = payload["status"]
        item.revision = payload["revision"]
        item.recorded_at = payload["recorded_at"]
        item.stale = bool(payload["stale"])
        return item


# ── Relation ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Relation:
    relation_id: str
    type: str
    from_id: str
    to_id: str
    note: str
    created_at: str

    @staticmethod
    def make(*, type: str, from_id: str, to_id: str, created_at: str,
             note: str = "") -> "Relation":
        _require(type in RELATION_TYPES, f"relation.type 非法：{type!r}")
        _require(from_id != to_id, "relation 不允许自环")
        for ref in (from_id, to_id):
            _require(bool(_ITEM_ID_RE.match(ref) or _EVIDENCE_ID_RE.match(ref)),
                     f"relation 端点必须是 item_id 或 evidence_id：{ref!r}")
        if note:
            _check_single_line(note, "relation.note", 200)
        _check_utc(created_at, "relation.created_at")
        rid = "rel_" + checksum({"type": type, "from_id": from_id, "to_id": to_id})[:16]
        return Relation(rid, type, from_id, to_id, note, created_at)

    def to_dict(self) -> dict:
        return {"relation_id": self.relation_id, "type": self.type, "from_id": self.from_id,
                "to_id": self.to_id, "note": self.note, "created_at": self.created_at}

    @staticmethod
    def from_dict(payload: dict) -> "Relation":
        expected = {"relation_id", "type", "from_id", "to_id", "note", "created_at"}
        _require(set(payload) == expected,
                 f"Relation 字段集必须恰好为 {sorted(expected)}，实际 {sorted(payload)}")
        _require(bool(_RELATION_ID_RE.match(payload["relation_id"])),
                 f"relation_id 非法：{payload['relation_id']!r}")
        rel = Relation.make(type=payload["type"], from_id=payload["from_id"],
                            to_id=payload["to_id"], created_at=payload["created_at"],
                            note=payload["note"])
        _require(rel.relation_id == payload["relation_id"], "relation_id 与内容不匹配")
        return rel