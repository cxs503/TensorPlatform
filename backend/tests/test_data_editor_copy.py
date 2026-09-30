import unittest

from api.data_profiling import EditedCopyRequest, _edited_copy_frame


class DataEditorCopyTests(unittest.TestCase):
    def test_builds_frame_in_declared_column_order(self):
        request = EditedCopyRequest(
            name="edited",
            columns=["temperature", "status"],
            rows=[
                {"status": "ok", "temperature": 1450.2},
                {"temperature": 1451.8, "status": "warn"},
            ],
        )
        frame = _edited_copy_frame(request)
        self.assertEqual(frame.columns.tolist(), ["temperature", "status"])
        self.assertEqual(frame["status"].tolist(), ["ok", "warn"])

    def test_rejects_empty_duplicate_and_undeclared_columns(self):
        invalid_requests = [
            EditedCopyRequest(columns=[], rows=[{}]),
            EditedCopyRequest(columns=["a", "a"], rows=[{"a": 1}]),
            EditedCopyRequest(columns=["a"], rows=[]),
            EditedCopyRequest(columns=["a"], rows=[{"a": 1, "unexpected": 2}]),
        ]
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    _edited_copy_frame(request)


if __name__ == "__main__":
    unittest.main()
