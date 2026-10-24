"""检索、装配与 token 预算测试（AIL_DATA_SPEC §11-§12）。"""
from __future__ import annotations

import unittest

from tools.ail.assembly import assemble
from tools.ail.bundle import AILBundle
from tools.ail.model import EvidenceRef, MemoryItem, canonical
from tools.ail.retrieval import RetrieveQuery, retrieve, score_item
from tools.ail.state import SessionState
from tools.ail.tokens import TokenCounter, default_counter

TS = "2026-10-23T07:00:00Z"
NOW = TS
SHA = "c" * 64


def ev(locator="f.json#/x", excerpt="摘录") -> EvidenceRef:
    return EvidenceRef.make(kind="json_pointer", locator=locator, sha256=SHA,
                            excerpt=excerpt, retrieved_at=TS)


def item(*, kind, topic, value, text, key, salience=50,
         verification=None, refs=None, scope=None) -> MemoryItem:
    return MemoryItem.make(
        kind=kind, topic=topic, value=value, text=text,
        source={"kind": "json_pointer", "locator": f"{key}.json#/x", "sha256": SHA,
                "retrieved_at": TS},
        retrieved_at=TS, scope=scope or {"agent_id": None, "session_id": None,
                                         "mission_id": None},
        verification=verification or {"status": "observed", "validator": None,
                                      "checked_at": None},
        evidence_refs=refs if refs is not None else ["ev_" + "0" * 16],
        salience=salience, idempotency_key=key)


def sample_bundle() -> AILBundle:
    session = SessionState.make(
        session_id="s-test", phase="executing", status_summary="阶段一文档在写",
        updated_at=TS,
        next_actions=[{"action_id": "n1", "text": "实现转换器", "preconditions": [],
                       "owner": "agent"}],
        budget={"tool_calls_used": 40, "wall_seconds_used": 1800, "token_budget": 1200})
    bundle = AILBundle.create(bundle_id="b-test", session=session)
    bundle.add_evidence(ev("operator_log/x.json#/2_guardian/pgrep_total", "守护 16"))
    for entry in (
        item(kind="state", topic="platform.guardian.count", value=16,
             text="守护进程实测 16 = 基线 6 + 池化槽 10", key="g", salience=95),
        item(kind="next_action", topic="platform.shift.next", value="巡检",
             text="下一班先做五步巡检", key="n", salience=90),
        item(kind="decision", topic="ail.layer.choice", value="semantic",
             text="先做语义记忆层而非新语法", key="d", salience=80),
        item(kind="evidence", topic="platform.relay.time_total", value=0.213707,
             text="中继探测耗时 0.213707 秒", key="r", salience=60),
        item(kind="fact", topic="platform.misc.note", value="无关",
             text="一条与查询无关的杂项记录", key="m", salience=10),
    ):
        bundle.add_item(entry)
    return bundle


class TokenCounterTests(unittest.TestCase):
    def test_counter_reports_its_caliber(self):
        info = default_counter().info()
        self.assertIn(info["kind"], ("tiktoken", "char_estimate"))
        if info["kind"] == "char_estimate":
            self.assertFalse(info["comparable_across_tools"])
        else:
            self.assertTrue(info["comparable_across_tools"])

    def test_counts_are_monotone_and_nonzero(self):
        counter = default_counter()
        self.assertEqual(counter.count(""), 0)
        self.assertGreater(counter.count("hello world"), 0)
        self.assertGreaterEqual(counter.count("hello world hello world"),
                                counter.count("hello world"))

    def test_fallback_estimate_is_upper_boundish(self):
        """降级口径必须*偏保守*（估高），否则预算会被突破。"""
        counter = TokenCounter()
        counter._enc = None          # 强制走降级路径
        counter._kind = "char_estimate"
        self.assertGreater(counter.count("中文四个字abcd"), 0)


class RetrievalTests(unittest.TestCase):
    def test_intent_weights_change_ordering(self):
        bundle = sample_bundle()
        state_first = retrieve(bundle, RetrieveQuery(intent="state", query="守护 16",
                                                    max_items=3, now=NOW))
        next_first = retrieve(bundle, RetrieveQuery(intent="next_action", query="下一步 巡检",
                                                    max_items=3, now=NOW))
        self.assertEqual(state_first["items"][0]["topic"], "platform.guardian.count")
        self.assertEqual(next_first["items"][0]["topic"], "platform.shift.next")

    def test_lexical_match_beats_pure_structure(self):
        bundle = sample_bundle()
        result = retrieve(bundle, RetrieveQuery(intent="decision",
                                                query="先做语义记忆层还是新语法",
                                                max_items=5, now=NOW))
        self.assertEqual(result["items"][0]["topic"], "ail.layer.choice")

    def test_irrelevant_items_are_penalized_when_query_given(self):
        bundle = sample_bundle()
        result = retrieve(bundle, RetrieveQuery(intent="state", query="守护进程数量",
                                                max_items=1, now=NOW))
        self.assertEqual(result["items"][0]["topic"], "platform.guardian.count")

    def test_deterministic_tie_break_by_item_id(self):
        bundle = sample_bundle()
        query = RetrieveQuery(intent="recall", query="", max_items=5, now=NOW)
        first = [i["item_id"] for i in retrieve(bundle, query)["items"]]
        second = [i["item_id"] for i in retrieve(bundle, query)["items"]]
        self.assertEqual(first, second)

    def test_scope_filter_is_hard(self):
        session = SessionState.make(session_id="s", phase="executing", status_summary="x",
                                    updated_at=TS)
        bundle = AILBundle.create(bundle_id="b", session=session)
        bundle.add_evidence(ev())
        bundle.add_item(item(kind="state", topic="a.one", value=1, text="范围 1", key="s1",
                             scope={"agent_id": None, "session_id": None, "mission_id": 192}))
        bundle.add_item(item(kind="state", topic="a.two", value=2, text="范围 2", key="s2",
                             scope={"agent_id": None, "session_id": None, "mission_id": 204}))
        result = retrieve(bundle, RetrieveQuery(intent="state", query="范围",
                                               max_items=10, now=NOW,
                                               scope_filter={"mission_id": 192}))
        topics = {i["topic"] for i in result["items"]}
        self.assertEqual(topics, {"a.one"})
        self.assertTrue(any(d["reason"] == "filtered_scope" for d in result["dropped"]))

    def test_failed_items_filtered_by_default(self):
        bundle = sample_bundle()
        bundle.add_item(item(kind="evidence", topic="platform.bad", value=0,
                             text="一条失败的判据", key="f",
                             verification={"status": "failed", "validator": "v.py",
                                           "checked_at": TS}))
        result = retrieve(bundle, RetrieveQuery(intent="evidence", query="失败 判据",
                                                max_items=10, now=NOW))
        self.assertNotIn("platform.bad", {i["topic"] for i in result["items"]})
        self.assertTrue(any(d["reason"] == "filtered_failed" for d in result["dropped"]))

    def test_conflict_members_are_returned_together(self):
        session = SessionState.make(session_id="s", phase="executing", status_summary="x",
                                    updated_at=TS)
        bundle = AILBundle.create(bundle_id="b", session=session)
        bundle.add_evidence(ev())
        old = item(kind="state", topic="platform.guardian.count", value=14,
                   text="旧值 14", key="old")
        new = item(kind="state", topic="platform.guardian.count", value=16,
                   text="新值 16", key="new")
        bundle.add_item(old)
        bundle.add_item(new)
        result = retrieve(bundle, RetrieveQuery(intent="state", query="守护", max_items=10,
                                                now=NOW))
        ids = {i["item_id"] for i in result["items"]}
        self.assertEqual(ids, {old.item_id, new.item_id})
        self.assertTrue(result["conflicts"])

    def test_budget_drops_are_reported(self):
        bundle = sample_bundle()
        result = retrieve(bundle, RetrieveQuery(intent="recall", query="", max_items=2,
                                                now=NOW))
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(any(d["reason"] == "budget" for d in result["dropped"]))


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.bundle = sample_bundle()
        self.counter = default_counter()

    def test_never_exceeds_budget(self):
        for budget in (80, 200, 400, 1200):
            text, report, _ = assemble(self.bundle, token_budget=budget, intent="recall",
                                       now=NOW, counter=self.counter)
            self.assertLessEqual(report.tokens_used, budget, f"budget={budget}")
            self.assertEqual(report.tokens_used, self.counter.count(text))

    def test_session_state_is_first_line(self):
        text, report, _ = assemble(self.bundle, token_budget=400, intent="state", now=NOW)
        self.assertIn("phase=executing", text.splitlines()[0])
        self.assertIn("L0", report.tiers_rendered)

    def test_unknown_marked_with_question_mark(self):
        session = SessionState.make(session_id="s", phase="executing", status_summary="x",
                                    updated_at=TS)
        bundle = AILBundle.create(bundle_id="b", session=session)
        bundle.add_evidence(ev())
        bundle.add_item(item(kind="state", topic="a.unknown", value="?",
                             text="不确定的数值", key="u",
                             verification={"status": "unknown", "validator": None,
                                           "checked_at": None}))
        text, _, _ = assemble(bundle, token_budget=400, intent="state", now=NOW)
        self.assertIn("?", text)

    def test_report_is_json_serializable_and_carries_tokenizer(self):
        import json

        _, report, _ = assemble(self.bundle, token_budget=600, intent="state", now=NOW)
        payload = report.to_dict()
        json.dumps(payload, ensure_ascii=False)
        self.assertIn("kind", payload["tokenizer"])
        self.assertEqual(payload["token_budget"], 600)

    def test_degradation_is_recorded(self):
        tiny, report, _ = assemble(self.bundle, token_budget=120, intent="recall", now=NOW)
        large, report_large, _ = assemble(self.bundle, token_budget=1200, intent="recall",
                                          now=NOW)
        self.assertGreaterEqual(len(report.degraded) + len(report.dropped_tiers),
                                len(report_large.degraded) + len(report_large.dropped_tiers))
        self.assertGreater(len(large), len(tiny))

    def test_conflict_footer_rendered_when_conflicts_exist(self):
        session = SessionState.make(session_id="s", phase="executing", status_summary="x",
                                    updated_at=TS)
        bundle = AILBundle.create(bundle_id="b", session=session)
        bundle.add_evidence(ev())
        bundle.add_item(item(kind="state", topic="platform.guardian.count", value=14,
                             text="旧值 14", key="o"))
        bundle.add_item(item(kind="state", topic="platform.guardian.count", value=16,
                             text="新值 16", key="n"))
        text, report, _ = assemble(bundle, token_budget=800, intent="state", now=NOW)
        self.assertIn("!CONFLICT", text)
        self.assertTrue(report.conflicts)

    def test_zero_budget_rejected(self):
        from tools.ail.model import AILSchemaError

        with self.assertRaises(AILSchemaError):
            assemble(self.bundle, token_budget=0, now=NOW)


class ScoreTests(unittest.TestCase):
    def test_score_components_sum(self):
        bundle = sample_bundle()
        entry = bundle.items[0]
        detail = score_item(entry, RetrieveQuery(intent="state", query="守护", now=NOW),
                            now=__import__("datetime").datetime(2026, 10, 23, 7,
                                                                tzinfo=__import__("datetime").timezone.utc))
        self.assertGreaterEqual(detail["score"], 0.0)
        self.assertLessEqual(detail["lex"], 1.0)


if __name__ == "__main__":
    unittest.main()