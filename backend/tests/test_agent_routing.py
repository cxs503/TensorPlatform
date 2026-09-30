import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests
from fastapi import HTTPException

from api.agent import (
    COPILOT_TEMPLATES,
    ChatReq,
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_MESSAGES,
    _select_tools,
    _trim_conversation,
    chat,
)


class AgentRoutingTest(unittest.TestCase):
    def setUp(self):
        self.request = SimpleNamespace(headers={"authorization": "Bearer test"})

    def test_general_process_question_does_not_expose_tools(self):
        tools = _select_tools(
            "钢铁生产工艺过程中有哪几步需要用到算法辅助，用了哪些算法？",
            COPILOT_TEMPLATES["energy_operations"],
        )
        self.assertEqual(tools, [])

    def test_live_platform_question_selects_only_relevant_tools(self):
        tools = _select_tools(
            "平台有多少数据集和模型？",
            COPILOT_TEMPLATES["energy_operations"],
        )
        names = {item["function"]["name"] for item in tools}
        self.assertIn("get_platform_stats", names)
        self.assertIn("list_datasets", names)
        self.assertNotIn("query_energy_summary", names)

    def test_context_is_bounded_and_keeps_latest_message(self):
        messages = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 2000 + str(index)}
            for index in range(30)
        ]
        trimmed = _trim_conversation(messages)
        self.assertLessEqual(len(trimmed), MAX_CONTEXT_MESSAGES)
        self.assertLessEqual(sum(len(item["content"]) for item in trimmed), MAX_CONTEXT_CHARS)
        self.assertTrue(trimmed[-1]["content"].endswith("29"))

    @patch("api.agent.ZHIPU_API_KEY", "configured")
    @patch("api.agent._call_zhipu")
    def test_general_question_calls_model_once(self, call_zhipu):
        call_zhipu.return_value = {
            "choices": [{"message": {"content": "answer"}}]
        }
        result = chat(
            ChatReq(messages=[{"role": "user", "content": "介绍钢铁生产工艺算法"}]),
            self.request,
        )
        self.assertEqual(result["reply"], "answer")
        self.assertEqual(result["mode"], "general")
        call_zhipu.assert_called_once()
        self.assertIsNone(call_zhipu.call_args.kwargs["tools"])

    @patch("api.agent.ZHIPU_API_KEY", "configured")
    @patch("api.agent._call_zhipu", side_effect=requests.Timeout("slow"))
    def test_provider_timeout_returns_504(self, _call_zhipu):
        with self.assertRaises(HTTPException) as raised:
            chat(
                ChatReq(messages=[{"role": "user", "content": "介绍生产过程工艺"}]),
                self.request,
            )
        self.assertEqual(raised.exception.status_code, 504)


if __name__ == "__main__":
    unittest.main()
