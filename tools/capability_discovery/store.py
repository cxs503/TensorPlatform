"""能力台账（capability ledger）持久化 + 哈希链 + 回滚。

设计要点
--------
* **单文件 JSON**（``artifacts/capability_discovery/ledger.json``）：可 diff、
  可 review、可随分支进版本库，不引入迁移负担；
* **哈希链**：每次写操作（登记/更新/撤回/恢复）在 ``chain`` 追加一条
  ``prev_hash → hash`` 记录，任何事后篡改都能被 ``verify_chain()`` 抓出来；
* **幂等**：证据指纹未变时**不写盘**（不刷时间戳），因此重复运行产出字节
  相同的台账——这是"自动登记没有噪声"的可验证判据；
* **回滚优先于删除**：能力从测试里消失（``missing``）只报告不删除；误登记
  用 ``retract``（记原因），恢复用 ``restore``（记原因）。全部留痕。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .grading import policy_digest
from .model import CapabilityFeature, Grade, digest, level_rank, now_iso, norm_level

LEDGER_SCHEMA = "capability_discovery.ledger.v1"
PIPELINE = "capability_discovery"
PIPELINE_VERSION = "1.0.0"

ACTIONS = ("registered", "updated", "retracted", "restored", "rekeyed")


class LedgerError(RuntimeError):
    """台账操作非法（未知能力 / 重复撤回 / 链校验失败等）。"""


def _empty_doc() -> Dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "pipeline": PIPELINE,
        "pipeline_version": PIPELINE_VERSION,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "policy_digest": "",
        "entries": {},
        "chain": [],
    }


class Ledger:
    """能力台账：条目 + 写操作哈希链。"""

    def __init__(self, doc: Optional[Dict[str, Any]] = None, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path else None
        self.doc = doc if doc is not None else _empty_doc()
        if self.doc.get("schema") != LEDGER_SCHEMA:
            raise LedgerError(f"台账 schema 不匹配: {self.doc.get('schema')!r} != {LEDGER_SCHEMA!r}")
        self.doc.setdefault("entries", {})
        self.doc.setdefault("chain", [])

    # ── 载入 / 保存 ──────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | str) -> "Ledger":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"台账不存在: {path}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        return cls(doc=doc, path=path)

    @classmethod
    def load_or_empty(cls, path: Path | str) -> "Ledger":
        path = Path(path)
        if path.exists():
            return cls.load(path)
        return cls(doc=_empty_doc(), path=path)

    def dumps(self) -> str:
        return json.dumps(self.doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"

    def save(self, path: Optional[Path | str] = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise LedgerError("未指定台账路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(self.dumps(), encoding="utf-8")
        os.replace(tmp, target)
        self.path = target
        return target

    # ── 查询 ────────────────────────────────────────────────────────────────
    @property
    def entries(self) -> Dict[str, Dict[str, Any]]:
        return self.doc["entries"]

    @property
    def chain(self) -> List[Dict[str, Any]]:
        return self.doc["chain"]

    def get(self, capability_id: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(capability_id)

    def active_ids(self) -> List[str]:
        return sorted(cid for cid, e in self.entries.items() if e.get("state") == "active")

    def active_entries(self) -> List[Dict[str, Any]]:
        return [self.entries[cid] for cid in self.active_ids()]

    def head_hash(self) -> str:
        return self.chain[-1]["hash"] if self.chain else ""

    # ── 写操作 ──────────────────────────────────────────────────────────────
    def apply_grade(
        self,
        feature: CapabilityFeature,
        grade: Grade,
        run_id: str = "",
        at: Optional[str] = None,
        reason: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        """把一条特征+定级写进台账；返回 (动作, 变更摘要)。

        动作：``added`` / ``updated`` / ``unchanged`` / ``held_retracted``。
        """
        at = at or now_iso()
        cid = feature.capability_id
        fingerprint = feature.evidence_fingerprint()
        evidence = {
            "declared_level": feature.declared_level,
            "anchors": list(feature.anchors),
            "params": feature.params,
            "tags": list(feature.tags),
            "tests": feature.tests,
            "sources": feature.sources,
            "marker_formats": list(feature.marker_formats),
            "outcomes": feature.outcomes,
            "declared_levels_seen": list(feature.declared_levels_seen),
            "fingerprint": fingerprint,
        }
        existing = self.entries.get(cid)

        if existing is None:
            entry = {
                "capability_id": cid,
                "label": feature.label,
                "level": norm_level(grade.level),
                "state": "active",
                "first_seen": at,
                "updated_at": at,
                "revision": 1,
                "grade": grade.to_dict(),
                "evidence": evidence,
                "level_history": [
                    {
                        "at": at,
                        "from": None,
                        "to": norm_level(grade.level),
                        "reason": reason or "initial registration",
                    }
                ],
                "retraction": None,
                "provenance": {
                    "pipeline": PIPELINE,
                    "version": PIPELINE_VERSION,
                    "run_id": run_id,
                },
            }
            entry["entry_hash"] = self._entry_hash(entry)
            self.entries[cid] = entry
            self._append_chain("registered", cid, entry["entry_hash"], reason or "new capability")
            return "added", {"level": entry["level"], "from_level": None, "revision": 1}

        if existing.get("state") == "retracted":
            # 撤回是人的裁决，自动登记不得擅自复活；只报告证据更新
            return "held_retracted", {
                "level": existing["level"],
                "from_level": existing["level"],
                "revision": existing["revision"],
                "retraction_reason": (existing.get("retraction") or {}).get("reason", ""),
            }

        old_level = existing["level"]
        level_changed = norm_level(grade.level) != norm_level(old_level)
        evidence_changed = existing.get("evidence", {}).get("fingerprint") != fingerprint
        if not level_changed and not evidence_changed:
            return "unchanged", {"level": old_level, "from_level": old_level, "revision": existing["revision"]}

        existing["evidence"] = evidence
        existing["grade"] = grade.to_dict()
        existing["label"] = feature.label or existing.get("label", "")
        existing["revision"] = int(existing.get("revision", 1)) + 1
        existing["updated_at"] = at
        existing["provenance"] = {
            "pipeline": PIPELINE,
            "version": PIPELINE_VERSION,
            "run_id": run_id,
        }
        if level_changed:
            existing["level"] = norm_level(grade.level)
            existing["level_history"].append(
                {
                    "at": at,
                    "from": old_level,
                    "to": existing["level"],
                    "reason": reason or "; ".join(grade.reasons) or "evidence change",
                }
            )
        existing["entry_hash"] = self._entry_hash(existing)
        self._append_chain("updated", cid, existing["entry_hash"], reason or "evidence/level change")
        return "updated", {
            "level": existing["level"],
            "from_level": old_level,
            "revision": existing["revision"],
            "level_changed": level_changed,
        }

    def retract(
        self, capability_id: str, reason: str, actor: str = "human", at: Optional[str] = None
    ) -> Dict[str, Any]:
        """撤回一条能力登记（回滚机制 C6）：必须给原因，留痕，不删除证据。"""
        if not reason or not reason.strip():
            raise LedgerError("撤回必须给出原因（可审计）")
        entry = self.entries.get(capability_id)
        if entry is None:
            raise LedgerError(f"未知能力: {capability_id}")
        if entry.get("state") == "retracted":
            raise LedgerError(f"能力已处于撤回态: {capability_id}")
        at = at or now_iso()
        entry["state"] = "retracted"
        entry["updated_at"] = at
        entry["revision"] = int(entry.get("revision", 1)) + 1
        entry["retraction"] = {
            "at": at,
            "by": actor,
            "reason": reason.strip(),
            "previous_level": entry["level"],
        }
        entry["entry_hash"] = self._entry_hash(entry)
        self._append_chain("retracted", capability_id, entry["entry_hash"], reason.strip())
        return entry

    def restore(
        self, capability_id: str, reason: str, actor: str = "human", at: Optional[str] = None
    ) -> Dict[str, Any]:
        """恢复一条被撤回的能力（记录恢复原因）。"""
        if not reason or not reason.strip():
            raise LedgerError("恢复必须给出原因（可审计）")
        entry = self.entries.get(capability_id)
        if entry is None:
            raise LedgerError(f"未知能力: {capability_id}")
        if entry.get("state") != "retracted":
            raise LedgerError(f"能力未处于撤回态: {capability_id}")
        at = at or now_iso()
        entry["state"] = "active"
        entry["updated_at"] = at
        entry["revision"] = int(entry.get("revision", 1)) + 1
        entry.setdefault("level_history", []).append(
            {"at": at, "from": entry["level"], "to": entry["level"], "reason": f"restored: {reason.strip()}"}
        )
        entry["retraction"] = None
        entry["entry_hash"] = self._entry_hash(entry)
        self._append_chain("restored", capability_id, entry["entry_hash"], reason.strip())
        return entry

    def missing(self, discovered_ids: Iterable[str]) -> List[Dict[str, Any]]:
        """台账里 active 但本次未发现的条目（只报告，不删除）。"""
        seen = set(discovered_ids)
        out = []
        for cid in self.active_ids():
            if cid not in seen:
                entry = self.entries[cid]
                out.append(
                    {
                        "capability_id": cid,
                        "level": entry["level"],
                        "revision": entry.get("revision", 1),
                        "last_updated": entry.get("updated_at"),
                        "last_seen_tests": [t.get("nodeid") for t in entry.get("evidence", {}).get("tests", [])],
                        "note": "ledger 有条目但本次扫描未发现（测试被删/丢 marker）；需人工 retract 或修复测试",
                    }
                )
        return out

    # ── 哈希链 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _entry_hash(entry: Dict[str, Any]) -> str:
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        return digest(body)

    def _append_chain(self, action: str, capability_id: str, entry_hash: str, reason: str = "") -> None:
        if action not in ACTIONS:
            raise LedgerError(f"未知动作: {action}")
        prev = self.head_hash()
        seq = len(self.chain) + 1
        at = now_iso()
        body = {
            "seq": seq,
            "at": at,
            "action": action,
            "capability_id": capability_id,
            "entry_hash": entry_hash,
            "prev_hash": prev,
            "reason": reason,
        }
        body["hash"] = digest(body)
        self.chain.append(body)
        self.doc["updated_at"] = at

    def verify_chain(self) -> Tuple[bool, str]:
        """重算整条链；任何断层/篡改都返回 (False, 说明)。"""
        prev = ""
        for idx, rec in enumerate(self.chain, start=1):
            if rec.get("seq") != idx:
                return False, f"链序号断裂于 seq={rec.get('seq')} (期望 {idx})"
            if rec.get("prev_hash") != prev:
                return False, f"prev_hash 不匹配于 seq={idx}"
            body = {k: v for k, v in rec.items() if k != "hash"}
            if digest(body) != rec.get("hash"):
                return False, f"记录被篡改于 seq={idx}"
            prev = rec["hash"]
        last_by_cap: Dict[str, Dict[str, Any]] = {}
        for rec in self.chain:
            last_by_cap[rec["capability_id"]] = rec
        for cid, rec in last_by_cap.items():
            entry = self.entries.get(cid)
            if entry is None:
                return False, f"链记录指向不存在的条目: {cid}"
            if self._entry_hash(entry) != entry.get("entry_hash"):
                return False, f"条目内容与自身 entry_hash 不一致（疑似篡改）: {cid}"
            if entry.get("entry_hash") != rec.get("entry_hash"):
                return False, f"条目哈希与链记录不一致（疑似篡改）: {cid}"
        for cid in self.entries:
            if cid not in last_by_cap:
                return False, f"台账条目缺写操作链记录（疑似手改）: {cid}"
        return True, f"chain ok ({len(self.chain)} records)"


def set_policy_digest(ledger: Ledger, policy: Dict[str, Any]) -> None:
    """把定级策略指纹写进台账（等级可追溯到「用哪套规则定的」）。"""
    dg = policy_digest(policy)
    if ledger.doc.get("policy_digest") != dg:
        ledger.doc["policy_digest"] = dg
        ledger.doc["updated_at"] = now_iso()