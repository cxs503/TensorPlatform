import sys
import unittest
from pathlib import Path

import pandas as pd
from fastapi import HTTPException


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.datasets import _split_frame  # noqa: E402


class DatasetSplitTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"row_id": list(range(20)), "value": list(range(100, 120))})

    def test_chronological_split_keeps_last_ten_percent_for_test(self):
        train, test = _split_frame(self.frame, "chronological")
        self.assertEqual(len(train), 18)
        self.assertEqual(len(test), 2)
        self.assertEqual(train["row_id"].tolist(), list(range(18)))
        self.assertEqual(test["row_id"].tolist(), [18, 19])

    def test_random_split_is_reproducible_disjoint_and_complete(self):
        first_train, first_test = _split_frame(self.frame, "random")
        second_train, second_test = _split_frame(self.frame, "random")
        self.assertEqual(first_train["row_id"].tolist(), second_train["row_id"].tolist())
        self.assertEqual(first_test["row_id"].tolist(), second_test["row_id"].tolist())
        train_ids = set(first_train["row_id"])
        test_ids = set(first_test["row_id"])
        self.assertFalse(train_ids & test_ids)
        self.assertEqual(train_ids | test_ids, set(range(20)))

    def test_split_rejects_fewer_than_ten_rows(self):
        with self.assertRaises(HTTPException) as raised:
            _split_frame(self.frame.iloc[:9], "random")
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
