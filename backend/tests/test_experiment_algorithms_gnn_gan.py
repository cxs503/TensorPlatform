import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.experiment_algorithms_gnn_gan import (  # noqa: E402
    AlgorithmUnavailableError,
    ExperimentAlgorithmError,
    generate_gan_from_artifact,
    predict_gnn_from_artifact,
    run_gan,
    run_gnn,
)


TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None
if TORCH_AVAILABLE:
    import torch


class AdapterContractTests(unittest.TestCase):
    def test_no_implicit_fixture_fallback(self):
        context = SimpleNamespace(config={}, dataset_path=None, work_dir="/tmp/unused")
        with self.assertRaisesRegex(ExperimentAlgorithmError, "dataset_path"):
            run_gnn(context)
        with self.assertRaisesRegex(ExperimentAlgorithmError, "dataset_path"):
            run_gan(context)

    def test_fixture_and_dataset_are_mutually_exclusive(self):
        context = SimpleNamespace(
            config={"fixture_mode": True},
            dataset_path="/tmp/not-used.npz",
            work_dir="/tmp/unused",
        )
        with self.assertRaisesRegex(ExperimentAlgorithmError, "mutually exclusive"):
            run_gnn(context)

    def test_parameters_are_bounded_before_engine_start(self):
        context = SimpleNamespace(
            config={"fixture_mode": True, "epochs": 201},
            dataset_path=None,
            work_dir="/tmp/unused",
        )
        with self.assertRaisesRegex(ExperimentAlgorithmError, "epochs"):
            run_gnn(context)
        context.config = {"fixture_mode": True, "latent_dim": 65}
        with self.assertRaisesRegex(ExperimentAlgorithmError, "latent_dim"):
            run_gan(context)


@unittest.skipUnless(TORCH_AVAILABLE, "platform PyTorch runtime is not exposed on this host")
class PyTorchAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.progress = []

    def tearDown(self):
        self.temp.cleanup()

    def context(self, kind, **config):
        work_dir = self.root / kind
        return SimpleNamespace(
            config=config,
            dataset_path=None,
            work_dir=work_dir,
            progress=lambda fraction, metrics: self.progress.append((fraction, metrics)),
        )

    def assert_valid_result(self, result, kind):
        self.assertEqual(result["kind"], kind)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model"]["framework"], "pytorch")
        self.assertEqual(result["metadata"]["data_source"], "fixture")
        self.assertTrue(Path(result["artifact_path"]).is_file())
        self.assertEqual(len(result["artifact"]["sha256"]), 64)
        self.assertGreater(result["artifact"]["size_bytes"], 0)
        self.assertTrue(all(math.isfinite(value) for value in result["metrics"].values()))
        self.assertGreater(len(self.progress), 0)
        self.assertEqual(self.progress[-1][0], 1.0)

    def test_gnn_runs_real_forward_backward_and_saves_state_dict(self):
        result = run_gnn(
            self.context(
                "gnn", fixture_mode=True, epochs=12, hidden_dim=8, seed=7
            )
        )
        self.assert_valid_result(result, "gnn")
        self.assertEqual(result["metadata"]["node_count"], 10)
        artifact = torch.load(result["artifact_path"], map_location="cpu")
        self.assertIn("model_state_dict", artifact)
        self.assertIn("input_layer.weight", artifact["model_state_dict"])
        prediction = predict_gnn_from_artifact(
            result["artifact_path"], [0, "node_9"]
        )
        self.assertEqual(len(prediction["predictions"]), 2)
        self.assertIn(prediction["predictions"][0]["class_id"], (0, 1))
        with self.assertRaisesRegex(ExperimentAlgorithmError, "unknown GNN node"):
            predict_gnn_from_artifact(result["artifact_path"], ["missing-node"])

    def test_gnn_accepts_strict_npz_contract(self):
        path = self.root / "graph.npz"
        features = np.asarray(
            [[1, 0], [0.9, 0.1], [0.8, 0.2], [0, 1], [0.1, 0.9], [0.2, 0.8]],
            dtype=np.float32,
        )
        labels = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int64)
        edges = np.asarray([[0, 1, 3, 4, 2], [1, 2, 4, 5, 3]], dtype=np.int64)
        np.savez(
            path,
            features=features,
            labels=labels,
            edges=edges,
            train_mask=np.asarray([0, 1, 3, 4]),
            test_mask=np.asarray([2, 5]),
        )
        context = self.context("gnn-dataset", epochs=5, seed=9)
        context.dataset_path = path
        result = run_gnn(context)
        self.assertEqual(result["metadata"]["data_source"], "dataset")
        self.assertEqual(result["metadata"]["node_count"], 6)

    def test_gan_runs_real_backprop_and_saves_both_networks(self):
        result = run_gan(
            self.context(
                "gan",
                fixture_mode=True,
                epochs=4,
                batch_size=64,
                hidden_dim=12,
                latent_dim=4,
                seed=11,
            )
        )
        self.assert_valid_result(result, "gan")
        self.assertEqual(result["metadata"]["sample_count"], 256)
        artifact = torch.load(result["artifact_path"], map_location="cpu")
        self.assertIn("generator_state_dict", artifact)
        self.assertIn("discriminator_state_dict", artifact)
        generated = generate_gan_from_artifact(result["artifact_path"], 3, seed=42)
        repeated = generate_gan_from_artifact(result["artifact_path"], 3, seed=42)
        self.assertEqual(generated, repeated)
        self.assertEqual(len(generated["samples"]), 3)
        self.assertTrue(
            all(math.isfinite(value) for row in generated["samples"] for value in row)
        )

    def test_gan_dataset_rejects_non_finite_values_without_fallback(self):
        path = self.root / "bad_samples.npz"
        values = np.ones((8, 2), dtype=np.float32)
        values[0, 0] = np.nan
        np.savez(path, samples=values)
        context = self.context("gan-bad", epochs=1)
        context.dataset_path = path
        with self.assertRaisesRegex(ExperimentAlgorithmError, "non-finite"):
            run_gan(context)

    def test_fixed_seed_produces_repeatable_real_metrics(self):
        first = run_gnn(
            self.context("repeat-a", fixture_mode=True, epochs=8, seed=31)
        )
        second = run_gnn(
            self.context("repeat-b", fixture_mode=True, epochs=8, seed=31)
        )
        self.assertEqual(first["metrics"], second["metrics"])


if __name__ == "__main__":
    unittest.main()
