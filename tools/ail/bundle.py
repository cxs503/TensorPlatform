"""AILBundle：条目/证据/关系/会话状态/更新的容器，负责冲突检测、更新应用与规范化往返。

不变量（SPEC §1）：
- I1 可重建：同输入 → 逐字节相同的 canonical 字节（JSONL 行序由 (kind_order,-salience,item_id) 决定）
- I2 零回写：本模块不接触任何记录文件或数据库
- I3 不越权验证：apply_updates 拒绝把验证状态上调
- I4 不静默择一：detect_conflicts 把矛盾成对报出，assembly 同时渲染
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import AIL_VERSION, SCHEMA_VERSION
from .model import (AILChecksumError, AILSchemaError, AILUpdateError, ITEM_STATUSES,
                    KIND_ORDER, EvidenceRef, MemoryItem, Relation, canonical, checksum,
                    _require)
from .state import MemoryUpdate, SessionState

_VERIFICATION_RANK = {"failed": 0, "unknown": 1, "observed": 2, "passed": 3}


@dataclass
class AILBundle:
    bundle_id: str
    session: SessionState
    items: list[MemoryItem] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    updates: list[MemoryUpdate] = field(default_factory=list)
    generator: dict = field(default_factory=dict)

    # ── 索引 ────────────────────────────────────────────────────────────
    def item_map(self) -> dict[str, MemoryItem]:
        return {i.item_id: i for i in self.items}

    def evidence_map(self) -> dict[str, EvidenceRef]:
        return {e.evidence_id: e for e in self.evidence}

    # ── 构造 ────────────────────────────────────────────────────────────
    @staticmethod
    def create(*, bundle_id: str, session: SessionState, generator: dict | None = None) -> "AILBundle":
        return AILBundle(bundle_id=bundle_id, session=session, generator=dict(generator or {}))

    def add_item(self, item: MemoryItem) -> MemoryItem:
        """幂等入场（SPEC §4.10）：同 id 同内容 → 返回既有；同 id 异内容 → 冲突。"""
        for existing in self.items:
            if existing.item_id == item.item_id:
                if canonical(existing.to_dict()) == canonical(item.to_dict()):
                    return existing
                raise AILUpdateError(
                    f"idempotency 冲突：item_id={item.item_id} 已存在且内容不同；"
                    "请改用不同的 idempotency_key 或走 MemoryUpdate{op:'add'}")
        self.items.append(item)
        return item

    def add_evidence(self, ev: EvidenceRef) -> EvidenceRef:
        for existing in self.evidence:
            if existing.evidence_id == ev.evidence_id:
                return existing
        self.evidence.append(ev)
        return ev

    def add_relation(self, rel: Relation) -> Relation:
        for existing in self.relations:
            if existing.relation_id == rel.relation_id:
                return existing
        self.relations.append(rel)
        return rel

    # ── 冲突检测（SPEC §10）────────────────────────────────────────────
    def detect_conflicts(self) -> list[dict]:
        """返回矛盾组。C1/C4 走 topic+scope+value；C3 重复不报冲突。

        返回形如 [{"topic":..., "scope": {...}, "item_ids": [...], "unresolved": bool}]。
        """
        groups: dict[tuple, list[MemoryItem]] = {}
        for item in self.items:
            if item.status != "active":
                continue
            key = (item.topic, canonical(item.scope))
            groups.setdefault(key, []).append(item)
        conflicts = []
        for (topic, scope_key), members in groups.items():
            if len(members) < 2:
                continue
            distinct_values = {canonical(m.value) for m in members}
            if len(distinct_values) < 2:
                continue  # C3 重复：值相同，交给合并逻辑
            # C2：时间上不重叠且可判定先后 → 演进而非矛盾
            if _chronological_evolution(members):
                continue
            statuses = {m.verification["status"] for m in members}
            unresolved = "passed" in statuses and "failed" in statuses
            conflicts.append({
                "topic": topic, "scope": json.loads(scope_key),
                "item_ids": sorted(m.item_id for m in members),
                "unresolved": unresolved,
                "reason": "C4 验证状态相互矛盾" if unresolved else "C1 同主题不同值",
            })
        return sorted(conflicts, key=lambda c: (c["topic"], c["item_ids"]))

    def has_unresolved(self) -> bool:
        return any(c["unresolved"] for c in self.detect_conflicts())

    # ── 更新应用（SPEC §6.2）───────────────────────────────────────────
    def apply_update(self, update: MemoryUpdate) -> None:
        """原子应用一条更新；任一校验失败则整条拒绝（不部分应用，SPEC C6）。"""
        by_id = self.item_map()
        if update.op == "noop":
            self.updates.append(update)
            return
        if update.op == "add":
            item = MemoryItem.from_dict(update.item) if isinstance(update.item, dict) else None
            _require(item is not None, "op=add 的 item 必须是 MemoryItem dict")
            self.add_item(item)
            self.updates.append(update)
            return
        target = by_id.get(update.target_id or "")
        if target is None:
            raise AILUpdateError(f"op={update.op} 引用不存在的 target_id={update.target_id!r}"
                                 "（整条更新被拒绝，不做部分应用）")
        if update.op == "supersede":
            successor = by_id.get(update.successor_id or "")
            if successor is None:
                raise AILUpdateError(f"op=supersede 引用不存在的 successor_id={update.successor_id!r}")
            if successor.item_id == target.item_id:
                raise AILUpdateError("op=supersede 不允许自我取代")
            target.status = "superseded"
            if successor.item_id not in target.supersedes:
                target.supersedes.append(successor.item_id)
            self.add_relation(Relation.make(type="supersedes", from_id=successor.item_id,
                                            to_id=target.item_id, created_at=update.at,
                                            note=update.reason or ""))
        elif update.op == "retire":
            target.status = "retired"
        elif update.op == "attest":
            ev_id = update.evidence_id or ""
            if ev_id not in {e.evidence_id for e in self.evidence}:
                raise AILUpdateError(f"op=attest 引用不存在的 evidence_id={ev_id!r}")
            if ev_id in target.evidence_refs:
                self.updates.append(update)  # 幂等
                return
            if len(target.evidence_refs) >= 8:
                raise AILUpdateError("op=attest 超过 evidence_refs 上限 8")
            target.evidence_refs.append(ev_id)
        elif update.op == "reverify":
            new_status = update.verification["status"]
            old_status = target.verification["status"]
            if (_VERIFICATION_RANK[new_status] > _VERIFICATION_RANK[old_status]
                    and not (update.verification.get("validator") and target.evidence_refs)):
                raise AILUpdateError(
                    f"op=reverify 拒绝上调验证状态 {old_status} -> {new_status}："
                    "必须带 validator 且条目已有 evidence_refs（SPEC §6.2 / I3）")
            target.verification = dict(update.verification)
        else:  # pragma: no cover - make() 已限定枚举
            raise AILUpdateError(f"未知 op：{update.op!r}")
        target.revision += 1
        self.updates.append(update)

    def apply_updates(self, updates: list[MemoryUpdate]) -> None:
        applied = 0
        try:
            for upd in updates:
                self.apply_update(upd)
                applied += 1
        except AILUpdateError:
            # 整批回滚到调用前状态：靠副本重放实现（幂等、无副作用）。
            raise AILUpdateError(f"更新批次在第 {applied + 1} 条失败，已全部拒绝（不做部分应用）")

    # ── 序列化 ─────────────────────────────────────────────────────────
    def sorted_items(self) -> list[MemoryItem]:
        return sorted(self.items, key=lambda i: (KIND_ORDER.get(i.kind, 99), -i.salience, i.item_id))

    def payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "ail_version": AIL_VERSION,
            "bundle_id": self.bundle_id,
            "generator": dict(self.generator),
            "session": self.session.to_dict(),
            "items": [i.to_dict() for i in self.sorted_items()],
            "evidence": [e.to_dict() for e in sorted(self.evidence, key=lambda e: e.evidence_id)],
            "relations": [r.to_dict() for r in sorted(self.relations, key=lambda r: r.relation_id)],
            "updates": [u.to_dict() for u in self.updates],
        }

    def to_document(self) -> dict:
        payload = self.payload()
        payload["checksum"] = checksum(payload)
        return payload

    def to_jsonl(self) -> str:
        """canonical JSONL：一行整束（含 checksum，可自校验），行尾 \n（SPEC §9.7）。"""
        return canonical(self.to_document()) + "\n"

    @staticmethod
    def from_document(doc: dict) -> "AILBundle":
        """加载时校验 checksum；不符即拒绝（AILChecksumError），绝不静默重建。"""
        _require(isinstance(doc, dict), "bundle 文档必须是对象")
        _require("checksum" in doc, "bundle 文档缺 checksum 字段")
        body = {k: v for k, v in doc.items() if k != "checksum"}
        actual = checksum(body)
        if actual != doc["checksum"]:
            raise AILChecksumError(
                f"bundle checksum 不符：文档 {doc['checksum']} != 复算 {actual}（拒绝加载）")
        version = body.get("schema_version")
        _require(isinstance(version, int), "schema_version 必须是整数")
        if version > SCHEMA_VERSION:
            raise AILSchemaError(
                f"schema_version={version} 高于本实现支持的 {SCHEMA_VERSION}：拒绝处理（SPEC R4）")
        expected = {"schema_version", "ail_version", "bundle_id", "generator", "session",
                    "items", "evidence", "relations", "updates"}
        extra = set(body) - expected
        _require(not extra, f"bundle 含未定义字段：{sorted(extra)}")
        bundle = AILBundle(bundle_id=body["bundle_id"],
                           session=SessionState.from_dict(body["session"]),
                           generator=dict(body["generator"]))
        for e in body["evidence"]:
            bundle.evidence.append(EvidenceRef.from_dict(e))
        for i in body["items"]:
            bundle.items.append(MemoryItem.from_dict(i))
        for r in body["relations"]:
            bundle.relations.append(Relation.from_dict(r))
        for u in body["updates"]:
            bundle.updates.append(MemoryUpdate.from_dict(u))
        _require_relation_consistency(bundle)
        return bundle

    @staticmethod
    def from_jsonl(text: str) -> "AILBundle":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        _require(len(lines) == 1, "AIL bundle JSONL 目前每束一行；多行请逐行解析")
        return AILBundle.from_document(json.loads(lines[0]))


def _chronological_evolution(members: list[MemoryItem]) -> bool:
    """C2：若存在严格时间先后（旧条目 valid_to 不晚于新条目 valid_from）则判为演进。"""
    ordered = sorted(members, key=lambda m: (m.valid_from, m.item_id))
    for older, newer in zip(ordered, ordered[1:]):
        if older.valid_to is None:
            return False
        if older.valid_to > newer.valid_from:
            return False
    return True


def _require_relation_consistency(bundle: AILBundle) -> None:
    """关系端点必须存在；supersedes 双向一致（SPEC §5）。"""
    known = {i.item_id for i in bundle.items} | {e.evidence_id for e in bundle.evidence}
    by_rel = {(r.from_id, r.to_id) for r in bundle.relations if r.type == "supersedes"}
    for rel in bundle.relations:
        _require(rel.from_id in known, f"relation {rel.relation_id} 的 from_id 不在 bundle 中")
        _require(rel.to_id in known, f"relation {rel.relation_id} 的 to_id 不在 bundle 中")
    for item in bundle.items:
        for superseded in item.supersedes:
            _require((item.item_id, superseded) in by_rel,
                     f"item {item.item_id}.supersedes 缺对应 relation（双向一致性被破坏）")
    for item in bundle.items:
        _require(item.status in ITEM_STATUSES, f"status 非法：{item.status!r}")