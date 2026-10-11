import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.experiment_algorithms_causal_recsys import (  # noqa: E402
    ExperimentAlgorithmError,
    recommend_from_artifact,
    run_causal,
    run_recsys,
)


class ExperimentAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, name, config, dataset_path=None):
        work_dir = self.root / name
        progress = []
        context = SimpleNamespace(
            config=config,
            dataset_path=dataset_path,
            work_dir=work_dir,
            progress=lambda percent, stage=None: progress.append((percent, stage)),
        )
        return context, progress

    def assert_finite_metrics(self, metrics):
        self.assertTrue(metrics)
        for value in metrics.values():
            self.assertTrue(math.isfinite(float(value)))

    def test_causal_fixture_runs_real_ols_and_writes_auditable_artifact(self):
        context, progress = self.context(
            "causal-fixture",
            {"fixture_mode": True, "confounders": ["Z"], "treatment": "X", "outcome": "Y"},
        )
        result = run_causal(context)

        self.assertEqual(result["source"]["type"], "deterministic_fixture")
        self.assertAlmostEqual(result["metrics"]["causal_effect"], 2.0, delta=0.03)
        self.assertGreater(result["metrics"]["r2"], 0.99)
        self.assert_finite_metrics(result["metrics"])
        artifact_path = Path(result["artifact_path"])
        self.assertTrue(artifact_path.is_file())
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["algorithm"], "ordinary_least_squares_adjustment")
        self.assertIn("Z", payload["coefficients"])
        self.assertEqual(progress[-1][0], 100)

    def test_causal_is_deterministic(self):
        first, _ = self.context("causal-a", {"fixture_mode": True})
        second, _ = self.context("causal-b", {"fixture_mode": True})
        first_result = run_causal(first)
        second_result = run_causal(second)
        self.assertEqual(first_result["metrics"], second_result["metrics"])

    def test_causal_uses_explicit_csv_columns(self):
        row = np.arange(80, dtype=float)
        csv_path = self.root / "authorized-causal.csv"
        frame = pd.DataFrame(
            {
                "control": np.sin(row * 0.21),
                "action": 0.4 * np.sin(row * 0.21) + ((row * 7) % 13) / 13,
            }
        )
        frame["result"] = 1.5 * frame["action"] + 0.6 * frame["control"] + 0.02 * np.cos(row)
        frame.to_csv(csv_path, index=False)
        context, _ = self.context(
            "causal-csv",
            {"treatment": "action", "outcome": "result", "confounders": ["control"]},
            csv_path,
        )
        result = run_causal(context)
        self.assertEqual(result["source"]["type"], "authorized_csv")
        self.assertAlmostEqual(result["metrics"]["causal_effect"], 1.5, delta=0.03)

    def test_causal_fails_closed_without_source_or_on_bad_csv(self):
        context, _ = self.context("causal-none", {})
        with self.assertRaisesRegex(ExperimentAlgorithmError, "authorized CSV"):
            run_causal(context)

        bad_path = self.root / "missing.csv"
        context, _ = self.context("causal-bad", {"fixture_mode": True}, bad_path)
        with self.assertRaisesRegex(ExperimentAlgorithmError, "does not exist"):
            run_causal(context)

    def test_recsys_fixture_trains_real_model_and_recommendations(self):
        context, progress = self.context(
            "recsys-fixture",
            {"fixture_mode": True, "epochs": 45, "factors": 5, "seed": 7, "top_k": 3},
        )
        result = run_recsys(context)

        self.assertEqual(result["source"]["type"], "deterministic_fixture")
        self.assert_finite_metrics(result["metrics"])
        self.assertGreater(result["metrics"]["train_ratings"], 0)
        self.assertGreater(result["metrics"]["validation_ratings"], 0)
        self.assertGreater(len(result["recommendations"]["items"]), 0)
        artifact_path = Path(result["artifact_path"])
        self.assertTrue(artifact_path.is_file())
        with np.load(artifact_path, allow_pickle=False) as model:
            self.assertEqual(model["user_factors"].shape[1], 5)
            self.assertGreater(float(np.linalg.norm(model["user_factors"])), 0.0)
        inferred = recommend_from_artifact(artifact_path, "u0", top_k=3)
        self.assertEqual(inferred, result["recommendations"])
        self.assertEqual(progress[-1][0], 100)

    def test_recsys_is_deterministic(self):
        config = {"fixture_mode": True, "epochs": 30, "factors": 4, "seed": 42}
        first, _ = self.context("recsys-a", config)
        second, _ = self.context("recsys-b", config)
        first_result = run_recsys(first)
        second_result = run_recsys(second)
        self.assertEqual(first_result["metrics"], second_result["metrics"])
        self.assertEqual(first_result["recommendations"], second_result["recommendations"])

    def test_recsys_reads_custom_csv_and_persists_model(self):
        rows = []
        for user in range(4):
            for item in range(6):
                if (user + item) % 5 != 0:
                    rows.append(
                        {
                            "account": f"a{user}",
                            "plan": f"p{item}",
                            "preference": 1.0 + ((user * 2 + item * 3) % 9) / 2.0,
                        }
                    )
        csv_path = self.root / "authorized-ratings.csv"
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        context, _ = self.context(
            "recsys-csv",
            {
                "user_column": "account",
                "item_column": "plan",
                "rating_column": "preference",
                "epochs": 20,
                "factors": 3,
                "recommend_user_id": "a0",
            },
            csv_path,
        )
        result = run_recsys(context)
        self.assertEqual(result["source"]["type"], "authorized_csv")
        self.assertTrue(Path(result["artifact_path"]).is_file())
        self.assertEqual(result["recommendations"]["user_id"], "a0")

    def test_recsys_fails_closed_and_enforces_bounds(self):
        context, _ = self.context("recsys-none", {})
        with self.assertRaisesRegex(ExperimentAlgorithmError, "authorized CSV"):
            run_recsys(context)

        context, _ = self.context("recsys-bounds", {"fixture_mode": True, "epochs": 301})
        with self.assertRaisesRegex(ExperimentAlgorithmError, "epochs"):
            run_recsys(context)

    def test_recommendation_inference_rejects_unknown_user(self):
        context, _ = self.context(
            "recsys-inference", {"fixture_mode": True, "epochs": 10, "factors": 3}
        )
        result = run_recsys(context)
        with self.assertRaisesRegex(ExperimentAlgorithmError, "not present"):
            recommend_from_artifact(result["artifact_path"], "unknown", top_k=3)


if __name__ == "__main__":
    unittest.main()
