import unittest

from services.sandbox_ai_assistant_service import (
    SandboxAssistantError,
    build_assistant_prompt,
    parse_edit_response,
    run_sandbox_assistant,
)


class SandboxAIAssistantTests(unittest.TestCase):
    def payload(self, **overrides):
        value = {
            "action": "modify", "code": "x = 1\n", "selected_code": "x = 1",
            "instruction": "改为2", "error_log": "", "language": "python",
            "file_path": "demo.py", "environment_name": "Python 3.11",
        }
        value.update(overrides)
        return value

    def test_prompt_marks_code_as_untrusted_and_contains_no_dataset(self):
        prompt = build_assistant_prompt(**self.payload())
        self.assertIn("不可信输入", prompt)
        self.assertIn("ODF_SOURCE", prompt)
        self.assertNotIn("数据集内容", prompt)

    def test_parse_complete_python_proposal(self):
        result = parse_edit_response(
            "ODF_SUMMARY: 修改变量\nODF_CODE_START\nx = 2\nODF_CODE_END",
            language="python",
        )
        self.assertEqual(result["proposed_code"], "x = 2\n")
        self.assertEqual(result["summary"], "修改变量")

    def test_rejects_invalid_python(self):
        with self.assertRaises(SandboxAssistantError):
            parse_edit_response(
                "ODF_CODE_START\ndef broken(:\nODF_CODE_END",
                language="python",
            )

    def test_create_allows_empty_source_and_returns_safe_filename(self):
        prompt = build_assistant_prompt(**self.payload(
            action="create", code="", selected_code=None,
            instruction="生成一个参数校验脚本", file_path="",
        ))
        self.assertIn("生成一个完整、可独立保存的脚本", prompt)
        result = parse_edit_response(
            "ODF_SUMMARY: 新脚本\nODF_FILENAME: ../参数 校验.py\n"
            "ODF_CODE_START\nprint('ok')\nODF_CODE_END",
            language="python",
        )
        self.assertEqual(result["suggested_filename"], "参数_校验.py")

    def test_marker_response_can_contain_nested_markdown_fence(self):
        result = parse_edit_response(
            "ODF_FILENAME: demo.py\nODF_CODE_START\n"
            "```python\nprint('ok')\n```\nODF_CODE_END",
            language="python",
        )
        self.assertEqual(result["proposed_code"], "print('ok')\n")

    def test_invalid_first_edit_is_corrected_once(self):
        class FakeLLM:
            provider = "zhipu"
            def __init__(self): self.calls = 0
            def chat(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return "ODF_CODE_START\ndef broken(:\nODF_CODE_END"
                return "ODF_SUMMARY: 已纠正\nODF_CODE_START\nx = 2\nODF_CODE_END"

        llm = FakeLLM()
        result = run_sandbox_assistant(llm, self.payload())
        self.assertEqual(llm.calls, 2)
        self.assertTrue(result["validation_retry"])
        self.assertEqual(result["proposed_code"], "x = 2\n")


if __name__ == "__main__":
    unittest.main()
