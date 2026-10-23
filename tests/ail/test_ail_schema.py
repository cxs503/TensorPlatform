"""规范层测试：字段约束、canonical 序列化、版本规则、确定性 ID（AIL_DATA_SPEC §2-§9）。"""
from __future__ import annotations

import json
import unittest

from tools.ail import AIL_VERSION, SCHEMA_VERSION
from tools.ail.model import (AILError, AILSchemaError, AILChecksumError, EvidenceRef,
                             MemoryItem, Relation, canonical, checksum)

TS = "2026-10-23T07:00:00Z"
SHA = "a" * 64


def source(**overrides):
    base = {"kind": "json_pointer", "locator": "operator_log/x.json#/a/b", "sha256": SHA,
            "retrieved_at": TS}
    base.update(overrides)
    return base


def make_item(**overrides) -> MemoryItem:
    kwargs = dict(kind="state", topic="platform.guardian.count", value=16,
                  text="守护进程实测 16 = 基线 6 + 池化槽 10", source=source(),
                  retrieved_at=TS, idempotency_key="k1")
    kwargs.update(overrides)
    return MemoryItem.make(**kwargs)


class CanonicalTests(unittest.TestCase):
    def test_canonical_is_key_sorted_and_compact(self):
        self.assertEqual(canonical({"b": 1, "a": [1, 2]}), '{"a":[1,2],"b":1}')

    def test_canonical_keeps_non_ascii_verbatim(self):
        self.assertEqual(canonical({"t": "中文"}), '{"t":"中文"}')

    def test_canonical_rejects_nan(self):
        with self.assertRaises(ValueError):
            canonical({"x": float("nan")})

    def test_checksum_stable_across_key_order(self):
        self.assertEqual(checksum({"a": 1, "b": 2}), checksum({"b": 2, "a": 1}))

    def test_checksum_matches_soul_algorithm(self):
        """与 Soul 的 canonical 口径逐字一致：同输入同哈希（两侧互认可复算）。"""
        import hashlib

        expected = hashlib.sha256('{"a":1,"b":"中文"}'.encode("utf-8")).hexdigest()
        self.assertEqual(checksum({"b": "中文", "a": 1}), expected)


class EvidenceRefTests(unittest.TestCase):
    def test_file_requires_sha256(self):
        with self.assertRaises(AILSchemaError):
            EvidenceRef.make(kind="file", locator="a.json", sha256=None, excerpt=None,
                             retrieved_at=TS)

    def test_excerpt_must_be_single_line(self):
        with self.assertRaises(AILSchemaError):
            EvidenceRef.make(kind="file", locator="a.json", sha256=SHA,
                             excerpt="两\n行", retrieved_at=TS)

    def test_id_is_deterministic(self):
        first = EvidenceRef.make(kind="file", locator="a.json", sha256=SHA, excerpt=None, retrieved_at=TS)
        second = EvidenceRef.make(kind="file", locator="a.json", sha256=SHA, excerpt="不同摘录", retrieved_at=TS)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertRegex(first.evidence_id, r"^ev_[0-9a-f]{16}$")


class MemoryItemTests(unittest.TestCase):
    def test_minimal_item_is_valid_and_roundtrips(self):
        item = make_item()
        restored = MemoryItem.from_dict(item.to_dict())
        self.assertEqual(item.item_id, restored.item_id)
        self.assertEqual(canonical(item.to_dict()), canonical(restored.to_dict()))

    def test_topic_must_be_lowercase_dotted(self):
        for bad in ("Platform.Foo", "platform", "platform..x", "平台.状态"):
            with self.assertRaises(AILSchemaError, msg=bad):
                make_item(topic=bad)

    def test_unknown_kind_rejected_unless_x_prefix(self):
        with self.assertRaises(AILSchemaError):
            make_item(kind="hypothesis")
        self.assertEqual(make_item(kind="x-hypothesis").kind, "x-hypothesis")

    def test_text_single_line_and_length(self):
        with self.assertRaises(AILSchemaError):
            make_item(text="a\nb")
        with self.assertRaises(AILSchemaError):
            make_item(text="x" * 401)

    def test_value_rejects_nested_object(self):
        with self.assertRaises(AILSchemaError):
            make_item(value={"a": 1})
        with self.assertRaises(AILSchemaError):
            make_item(value=[1, "mixed"])

    def test_passed_requires_validator_and_evidence(self):
        with self.assertRaises(AILSchemaError):
            make_item(verification={"status": "passed", "validator": None, "checked_at": TS},
                      evidence_refs=["ev_" + "0" * 16])
        with self.assertRaises(AILSchemaError):
            make_item(verification={"status": "passed", "validator": "v.py", "checked_at": TS},
                      evidence_refs=[])

    def test_observed_does_not_require_validator(self):
        """observed 是原始观察（工具输出），不强制独立验证器；passed/failed 才强制。"""
        item = make_item(verification={"status": "observed", "validator": None, "checked_at": None})
        self.assertEqual(item.verification["status"], "observed")
        self.assertEqual(item.evidence_refs, [])

    def test_unknown_is_not_failed(self):
        unknown = make_item(verification={"status": "unknown", "validator": None, "checked_at": None})
        failed = make_item(value=15,
                           verification={"status": "failed", "validator": "v.py", "checked_at": TS},
                           evidence_refs=["ev_" + "0" * 16])
        self.assertNotEqual(unknown.verification["status"], failed.verification["status"])

    def test_shared_sensitivity_requires_passed(self):
        with self.assertRaises(AILSchemaError):
            make_item(sensitivity="shared",
                      verification={"status": "observed", "validator": None, "checked_at": None})

    def test_time_must_be_utc_seconds(self):
        for bad in ("2026-10-23T07:00:00+08:00", "2026-10-23 07:00:00", "2026-10-23T07:00:00.123Z"):
            with self.assertRaises(AILSchemaError, msg=bad):
                make_item(valid_from=bad)

    def test_unknown_field_rejected_on_load(self):
        payload = make_item().to_dict()
        payload["extra_field"] = 1
        with self.assertRaises(AILSchemaError):
            MemoryItem.from_dict(payload)

    def test_tampered_item_id_rejected(self):
        payload = make_item().to_dict()
        payload["item_id"] = "mi_" + "f" * 16
        with self.assertRaises(AILSchemaError):
            MemoryItem.from_dict(payload)

    def test_item_id_deterministic_and_independent_of_text(self):
        """item_id 由 (kind,topic,value,scope,idempotency_key) 决定：
        文本措辞变化不改变身份，值变化才改变身份（这是"可复算性"的基础）。"""
        a = make_item(text="措辞一")
        b = make_item(text="措辞二")
        c = make_item(value=17)
        self.assertEqual(a.item_id, b.item_id)
        self.assertNotEqual(a.item_id, c.item_id)

    def test_item_id_prefix_and_length(self):
        self.assertRegex(make_item().item_id, r"^mi_[0-9a-f]{16}$")


class RelationTests(unittest.TestCase):
    def test_self_loop_rejected(self):
        with self.assertRaises(AILSchemaError):
            Relation.make(type="supports", from_id="mi_" + "0" * 16,
                          to_id="mi_" + "0" * 16, created_at=TS)

    def test_endpoint_kind_validated(self):
        with self.assertRaises(AILSchemaError):
            Relation.make(type="supports", from_id="bogus", to_id="ev_" + "0" * 16,
                          created_at=TS)

    def test_type_enum(self):
        with self.assertRaises(AILSchemaError):
            Relation.make(type="likes", from_id="mi_" + "0" * 16,
                          to_id="ev_" + "0" * 16, created_at=TS)


class VersionTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(SCHEMA_VERSION, 1)
        self.assertEqual(AIL_VERSION, "1.0")

    def test_future_schema_version_rejected(self):
        from tools.ail.bundle import AILBundle
        from tools.ail.state import SessionState

        session = SessionState.make(session_id="s", phase="executing",
                                    status_summary="x", updated_at=TS)
        bundle = AILBundle.create(bundle_id="b", session=session)
        doc = bundle.to_document()
        doc["schema_version"] = SCHEMA_VERSION + 1
        doc["checksum"] = checksum({k: v for k, v in doc.items() if k != "checksum"})
        with self.assertRaises(AILSchemaError):
            AILBundle.from_document(doc)


if __name__ == "__main__":
    unittest.main()