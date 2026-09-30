import unittest

import pandas as pd

from services.data_alignment_service import DataAlignmentError, align_dataframes


class DataAlignmentServiceTests(unittest.TestCase):
    def test_nearest_uses_actual_time_distance(self):
        source = pd.DataFrame({"ts": ["2026-01-01 14:00", "2026-01-01 14:10"], "value": [0.0, 10.0]})
        target = pd.DataFrame({"ts": ["2026-01-01 14:04", "2026-01-01 14:09"], "value": [40.0, 90.0]})
        result = align_dataframes(
            source, target,
            source_time_column="ts", target_time_column="ts",
            method="nearest", tolerance="5min",
        )
        self.assertEqual(result.frame["tgt_value"].tolist(), [40.0, 90.0])
        self.assertEqual(result.stats["fully_matched_rows"], 2)
        self.assertEqual(result.stats["max_time_delta_seconds"], 240.0)

    def test_linear_uses_elapsed_time_not_row_position(self):
        source = pd.DataFrame({"ts": ["2026-01-01 14:00", "2026-01-01 14:10"], "value": [0.0, 10.0]})
        target = pd.DataFrame({"ts": ["2026-01-01 14:04", "2026-01-01 14:09"], "value": [40.0, 90.0]})
        result = align_dataframes(
            source, target,
            source_time_column="ts", target_time_column="ts",
            method="linear", frequency="5min", tolerance="5min",
        )
        self.assertEqual(result.frame["src_value"].round(6).tolist(), [4.0, 9.0])

    def test_rejects_non_overlapping_ranges_and_duplicate_times(self):
        source = pd.DataFrame({"ts": ["2026-01-01 14:00", "2026-01-01 14:10"], "value": [1, 2]})
        far_target = pd.DataFrame({"ts": ["2026-01-02 14:00"], "value": [3]})
        with self.assertRaisesRegex(DataAlignmentError, "没有可对齐记录"):
            align_dataframes(
                source, far_target,
                source_time_column="ts", target_time_column="ts",
                tolerance="5min",
            )

        duplicated = pd.DataFrame({"ts": ["2026-01-01", "2026-01-01"], "value": [1, 2]})
        with self.assertRaisesRegex(DataAlignmentError, "重复时间"):
            align_dataframes(
                duplicated, source,
                source_time_column="ts", target_time_column="ts",
            )

    def test_rejects_wrong_and_numeric_time_columns(self):
        source = pd.DataFrame({"ts": ["2026-01-01"], "value": [1]})
        with self.assertRaisesRegex(DataAlignmentError, "不存在时间列"):
            align_dataframes(
                source, source,
                source_time_column="missing", target_time_column="ts",
            )
        numeric_time = pd.DataFrame({"ts": [1, 2], "value": [3, 4]})
        with self.assertRaisesRegex(DataAlignmentError, "普通数值列"):
            align_dataframes(
                numeric_time, source,
                source_time_column="ts", target_time_column="ts",
            )


if __name__ == "__main__":
    unittest.main()
