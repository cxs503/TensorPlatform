import unittest

import pandas as pd

from services.intelligent_data_analysis_service import build_llm_prompt, llm_summary, profile_frame


class IntelligentDataAnalysisTests(unittest.TestCase):
    def test_profile_builds_real_charts_and_detects_time(self):
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=20, freq="h").astype(str),
            "temperature": list(range(19)) + [1000],
            "pressure": [1.0, 2.0] * 10,
            "batch": ["A", "B"] * 10,
        })
        result = profile_frame(frame, source_name="test.csv")
        self.assertEqual(result["time_column"], "timestamp")
        self.assertEqual(result["total_rows"], 20)
        self.assertTrue(result["charts"]["correlation"])
        self.assertTrue(result["charts"]["time_series"])
        self.assertGreater(result["columns"][1]["statistics"]["anomaly_count"], 0)

    def test_sensitive_category_values_are_not_charted_or_sent_as_sample_by_default(self):
        frame = pd.DataFrame({"phone": ["13800000000", "13900000000"], "value": [1, 2]})
        profile = profile_frame(frame, source_name="sensitive.csv")
        self.assertEqual(profile["charts"]["categories"], [])
        summary = llm_summary(profile)
        self.assertNotIn("optional_sample", summary)
        self.assertNotIn("target_column", summary)
        self.assertTrue(summary["columns"][0]["sensitive"])

    def test_prompt_does_not_assume_prediction_context(self):
        profile = profile_frame(pd.DataFrame({"value": [1, 2, 3]}), source_name="plain.csv")
        prompt = build_llm_prompt(llm_summary(profile))
        self.assertIn("没有指定业务场景、预测目标或算法", prompt)
        self.assertIn("不要推荐具体预测模型", prompt)


if __name__ == "__main__":
    unittest.main()
