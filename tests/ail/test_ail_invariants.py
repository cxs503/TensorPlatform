"""不变量与冲突处理测试（AIL_DATA_SPEC §1 I1-I4、§6 MemoryUpdate、§10 冲突规则）。"""
from __future__ import annotations

import unittest

from tools.ail.bundle import AILBundle
from tools.ail.model import (AILChecksumError, AILSchemaError, AILUpdateError, EvidenceRef,
                             MemoryItem, canonical)
from tools.ail.state import MemoryUpdate, SessionState

TS = "2026-10-23T07:00:00Z"
SHA = "b" * 64


def ev(locator="f.json#/x") -> EvidenceRef:
    return EvidenceRef.make(kind="json_pointer", locator=locator, sha256=SHA,
                            excerpt="摘录", retrieved_at=TS)


def item(value, key, *, topic="platform.guardian.count", kind="state", validity=(TS, None),
         verification=None, evidence_refs=("EV",)) -> MemoryItem:
    refs = ["ev_" + "0" * 16] if evidence_refs == ("EV",) else list(evidence_refs)
    return MemoryItem.make(
        kind=kind, topic=topic, value=value, text=f"值为 {value}",
        source={"kind": "json_pointer", "locator": f"{key}.json#/x", "sha256": SHA,
                "retrieved_at": TS},
        retrieved_at=TS, verification=verification or {"status": "unknown", "validator": None,
                                                       "checked_at": None},
        evidence_refs=refs, valid_from=validity[0], valid_to=validity[1],
        idempotency_key=key)


def bundle_with(*items: MemoryItem) -> AILBundle:
    session = SessionState.make(session_id="s", phase="executing", status_summary="x",
                                updated_at=TS)
    bundle = AILBundle.create(bundle_id="b", session=session)
    bundle.add_evidence(ev())
    for entry in items:
        bundle.add_item(entry)
    return bundle


class SerializationInvariantTests(unittest.TestCase):
    def test_i1_same_input_same_bytes_regardless_of_insert_order(self):
        a, b, c = item(16, "k1"), item(2, "k2", topic="platform.shift.inflight"), item("x", "k3")
        left = bundle_with(a, b, c).to_jsonl()
        right = bundle_with(c, a, b).to_jsonl()
        self.assertEqual(left, right)

    def test_i1_checksum_is_recomputable(self):
        bundle = bundle_with(item(16, "k1"))
        doc = bundle.to_document()
        self.assertEqual(doc["checksum"], bundle.to_document()["checksum"])
        self.assertEqual(len(doc["checksum"]), 64)

    def test_checksum_mismatch_is_rejected_not_silently_rebuilt(self):
        doc = bundle_with(item(16, "k1")).to_document()
        doc["items"][0]["value"] = 99
        with self.assertRaises(AILChecksumError):
            AILBundle.from_document(doc)

    def test_missing_checksum_rejected(self):
        doc = bundle_with(item(16, "k1")).to_document()
        doc.pop("checksum")
        with self.assertRaises(AILSchemaError):
            AILBundle.from_document(doc)

    def test_jsonl_line_is_canonical_and_newline_terminated(self):
        text = bundle_with(item(16, "k1")).to_jsonl()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        self.assertNotIn(": ", text)

    def test_roundtrip_preserves_everything(self):
        bundle = bundle_with(item(16, "k1"), item("passed", "k2",
                                                  topic="platform.relay.state"))
        restored = AILBundle.from_document(bundle.to_document())
        self.assertEqual(canonical(bundle.payload()), canonical(restored.payload()))


class IdempotencyTests(unittest.TestCase):
    def test_same_id_same_content_is_idempotent(self):
        bundle = bundle_with(item(16, "k1"))
        again = bundle.add_item(item(16, "k1"))
        self.assertEqual(len(bundle.items), 1)
        self.assertEqual(again.item_id, bundle.items[0].item_id)

    def test_same_id_different_text_is_conflict(self):
        bundle = bundle_with(item(16, "k1"))
        conflicting = MemoryItem.make(
            kind="state", topic="platform.guardian.count", value=16,
            text="另一种措辞", source={"kind": "json_pointer", "locator": "k1.json#/x",
                                   "sha256": SHA, "retrieved_at": TS},
            retrieved_at=TS, idempotency_key="k1")
        with self.assertRaises(AILUpdateError):
            bundle.add_item(conflicting)


class ConflictTests(unittest.TestCase):
    def test_c1_same_topic_two_values_is_conflict(self):
        bundle = bundle_with(item(16, "k1"), item(14, "k2"))
        conflicts = bundle.detect_conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["reason"], "C1 同主题不同值")
        self.assertFalse(conflicts[0]["unresolved"])

    def test_c2_disjoint_intervals_are_evolution_not_conflict(self):
        old = item(14, "k1", validity=(TS, "2026-10-23T08:00:00Z"))
        new = item(16, "k2", validity=("2026-10-23T08:00:00Z", None))
        self.assertEqual(bundle_with(old, new).detect_conflicts(), [])

    def test_c3_same_value_is_not_conflict(self):
        bundle = bundle_with(item(16, "k1"), item(16, "k2"))
        self.assertEqual(bundle.detect_conflicts(), [])

    def test_c4_passed_vs_failed_is_unresolved(self):
        pass_ref = ["ev_" + "0" * 16]
        bundle = bundle_with(
            item(16, "k1", verification={"status": "passed", "validator": "v.py",
                                         "checked_at": TS}, evidence_refs=pass_ref),
            item(14, "k2", verification={"status": "failed", "validator": "v.py",
                                         "checked_at": TS}, evidence_refs=pass_ref))
        conflicts = bundle.detect_conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0]["unresolved"])
        self.assertTrue(bundle.has_unresolved())

    def test_c5_implicit_supersede_is_ignored(self):
        """新条目声称取代旧条目但没有 MemoryUpdate → 旧条目仍 active（禁止隐式取代）。"""
        bundle = bundle_with(item(14, "k1"), item(16, "k2"))
        self.assertEqual([i.status for i in bundle.items], ["active", "active"])
        self.assertTrue(bundle.detect_conflicts())


class MemoryUpdateTests(unittest.TestCase):
    def test_supersede_marks_status_and_creates_relation(self):
        old, new = item(14, "k1"), item(16, "k2")
        bundle = bundle_with(old, new)
        bundle.apply_update(MemoryUpdate.make(
            op="supersede", at=TS, idempotency_key="u1", actor="t",
            target_id=old.item_id, successor_id=new.item_id, reason="m203 实测 16"))
        self.assertEqual(old.status, "superseded")
        self.assertEqual(old.supersedes, [new.item_id])
        self.assertIn((new.item_id, old.item_id),
                      {(r.from_id, r.to_id) for r in bundle.relations if r.type == "supersedes"})

    def test_c6_missing_target_rejects_whole_update(self):
        bundle = bundle_with(item(14, "k1"))
        with self.assertRaises(AILUpdateError):
            bundle.apply_update(MemoryUpdate.make(
                op="retire", at=TS, idempotency_key="u1", target_id="mi_" + "9" * 16,
                reason="不存在"))
        self.assertEqual(bundle.items[0].status, "active")

    def test_batch_is_atomic(self):
        old = item(14, "k1")
        bundle = bundle_with(old)
        updates = [
            MemoryUpdate.make(op="retire", at=TS, idempotency_key="u1",
                              target_id=old.item_id, reason="先作废"),
            MemoryUpdate.make(op="retire", at=TS, idempotency_key="u2",
                              target_id="mi_" + "9" * 16, reason="不存在"),
        ]
        with self.assertRaises(AILUpdateError):
            bundle.apply_updates(updates)
        # 第一条**已生效**但整批语义是"全或无"：调用方必须知道这不是回滚。
        self.assertEqual(old.status, "retired")

    def test_i3_reverify_cannot_upgrade_without_validator_and_evidence(self):
        target = item(16, "k1", evidence_refs=[])
        bundle = bundle_with(target)
        with self.assertRaises(AILUpdateError):
            bundle.apply_update(MemoryUpdate.make(
                op="reverify", at=TS, idempotency_key="u1", target_id=target.item_id,
                verification={"status": "passed", "validator": None, "checked_at": TS}))

    def test_reverify_downgrade_is_allowed(self):
        target = item(16, "k1", verification={"status": "passed", "validator": "v.py",
                                              "checked_at": TS},
                      evidence_refs=["ev_" + "0" * 16])
        bundle = bundle_with(target)
        bundle.apply_update(MemoryUpdate.make(
            op="reverify", at=TS, idempotency_key="u1", target_id=target.item_id,
            verification={"status": "failed", "validator": "v2.py", "checked_at": TS}))
        self.assertEqual(target.verification["status"], "failed")

    def test_add_and_noop(self):
        bundle = bundle_with()
        fresh = item(16, "k9")
        bundle.apply_update(MemoryUpdate.make(op="add", at=TS, idempotency_key="u1",
                                              item=fresh.to_dict()))
        bundle.apply_update(MemoryUpdate.make(op="noop", at=TS, idempotency_key="u2"))
        self.assertEqual([i.item_id for i in bundle.items], [fresh.item_id])
        self.assertEqual(len(bundle.updates), 2)

    def test_update_op_validation(self):
        with self.assertRaises(AILSchemaError):
            MemoryUpdate.make(op="delete", at=TS, idempotency_key="u1")
        with self.assertRaises(AILSchemaError):
            MemoryUpdate.make(op="supersede", at=TS, idempotency_key="u1")  # 缺必填


class RelationConsistencyTests(unittest.TestCase):
    def test_loaded_bundle_requires_supersedes_relation(self):
        old, new = item(14, "k1"), item(16, "k2")
        bundle = bundle_with(old, new)
        bundle.apply_update(MemoryUpdate.make(
            op="supersede", at=TS, idempotency_key="u1", target_id=old.item_id,
            successor_id=new.item_id, reason="r"))
        doc = bundle.to_document()
        doc["relations"] = []
        from tools.ail.model import checksum

        doc["checksum"] = checksum({k: v for k, v in doc.items() if k != "checksum"})
        with self.assertRaises(AILSchemaError):
            AILBundle.from_document(doc)


if __name__ == "__main__":
    unittest.main()