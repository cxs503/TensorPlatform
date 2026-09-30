import unittest
from unittest.mock import patch

from services.llm_service import LLMService


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class LLMCallTracingTest(unittest.TestCase):
    @patch("services.llm_service.record_llm_trace")
    @patch("services.llm_service.requests.post")
    def test_trace_operation_is_not_sent_to_provider(self, mock_post, mock_record):
        mock_post.return_value = FakeResponse(
            payload={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            }
        )

        service = LLMService(api_key="test-key", provider="zhipu")
        result = service.chat(
            "secret prompt",
            trace_operation="prompt_test",
            max_tokens=10,
        )

        self.assertEqual(result, "ok")
        provider_payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("trace_operation", provider_payload)

        mock_record.assert_called_once()
        trace = mock_record.call_args.kwargs
        self.assertEqual(trace["operation"], "prompt_test")
        self.assertEqual(trace["status"], "success")
        self.assertEqual(trace["usage"]["prompt_tokens"], 2)
        self.assertEqual(trace["usage"]["completion_tokens"], 1)
        self.assertEqual(trace["usage"]["total_tokens"], 3)
        self.assertNotIn("prompt", trace)
        self.assertNotIn("response", trace)

    @patch("services.llm_service.record_llm_trace")
    @patch("services.llm_service.requests.post")
    def test_failed_provider_call_is_traced(self, mock_post, mock_record):
        mock_post.return_value = FakeResponse(status_code=500, text="provider error")

        service = LLMService(api_key="test-key", provider="zhipu")
        with self.assertRaises(RuntimeError):
            service.chat("hello", trace_operation="sandbox_explain")

        mock_record.assert_called_once()
        trace = mock_record.call_args.kwargs
        self.assertEqual(trace["operation"], "sandbox_explain")
        self.assertEqual(trace["status"], "error")
        self.assertIn("500", trace["error_message"])


if __name__ == "__main__":
    unittest.main()
