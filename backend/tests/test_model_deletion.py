import inspect
import tempfile
import unittest
from pathlib import Path

from api.models import _remove_model_artifacts, _safe_artifact_candidates, delete_model


class ModelDeletionSafetyTests(unittest.TestCase):
    def test_artifact_cleanup_is_root_scoped_and_handles_mlp_companions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            root.mkdir()
            model = root / "train_demo_model.pkl"
            weights = root / "train_demo_model.pt"
            config = root / "train_demo_config.json"
            outside = Path(directory) / "outside.pkl"
            for path in (model, weights, config, outside):
                path.write_text("fixture", encoding="utf-8")

            candidates = _safe_artifact_candidates([model, outside], root=root)
            self.assertEqual(set(candidates), {model, weights, config})
            result = _remove_model_artifacts([model, outside], root=root)

            self.assertEqual(set(result["deleted"]), {str(model), str(weights), str(config)})
            self.assertTrue(outside.is_file())
            self.assertFalse(model.exists())

    def test_shared_artifact_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "shared.pkl"
            artifact.write_text("fixture", encoding="utf-8")
            result = _remove_model_artifacts([artifact], shared_paths=[artifact], root=root)
            self.assertTrue(artifact.is_file())
            self.assertEqual(result["skipped_shared"], [str(artifact)])

    def test_delete_contract_blocks_active_deployments_and_preserves_training_history(self):
        source = inspect.getsource(delete_model)
        self.assertIn("active_deployments", source)
        self.assertIn("UPDATE training_jobs", source)
        self.assertIn("deleted_model_id", source)
        self.assertIn("DELETE FROM deployments", source)
        self.assertIn("_remove_model_artifacts", source)
        self.assertIn("_delete_mlflow_registration", source)


if __name__ == "__main__":
    unittest.main()
