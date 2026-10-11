"""Side-effect-free contracts for the durable experiment runtime."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from models.core import Dataset, Model, ModelVersion, TrainingJob, TrainingStatus
from services.experiment_runtime import (
    METADATA_KEY,
    ExperimentAccessDenied,
    ExperimentRuntimeError,
    ExperimentValidationError,
    create_experiment,
    execute_persisted_experiment,
    register_adapter,
    serialize_experiment,
    unregister_adapter,
    validate_config,
)


class FakeQuery:
    def __init__(self, db, model):
        self.db, self.model = db, model
    def filter(self, *_args): return self
    def with_for_update(self): return self
    def order_by(self, *_args): return self
    def offset(self, *_args): return self
    def limit(self, *_args): return self
    def first(self):
        if self.model is Dataset: return self.db.dataset
        if self.model is TrainingJob: return self.db.job
        return None
    def all(self):
        if self.model is TrainingJob: return list(self.db.active_jobs)
        return []
    def count(self): return 0


class FakeDB:
    def __init__(self, dataset=None):
        self.dataset, self.job, self.active_jobs = dataset, None, []
        self.models, self.versions, self.commits = [], [], 0
    def query(self, model): return FakeQuery(self, model)
    def add(self, value):
        if isinstance(value, TrainingJob):
            value.id = value.id or 1; self.job = value
        elif isinstance(value, Model):
            value.id = len(self.models) + 10; self.models.append(value)
        elif isinstance(value, ModelVersion):
            value.id = len(self.versions) + 20; self.versions.append(value)
    def flush(self): pass
    def refresh(self, _value): pass
    def commit(self): self.commits += 1
    def rollback(self): pass


class ExperimentRuntimeTest(unittest.TestCase):
    KIND = "unit_test"

    def setUp(self):
        self.audit_patch = mock.patch(
            "services.experiment_runtime._audit", return_value=True
        )
        self.audit_patch.start()
        register_adapter(self.KIND, lambda _context: {"metrics": {"score": 1.0}}, replace=True)

    def tearDown(self):
        unregister_adapter(self.KIND)
        self.audit_patch.stop()

    def test_config_rejects_secrets_and_non_finite_numbers(self):
        with self.assertRaises(ExperimentValidationError): validate_config({"token": "x"})
        with self.assertRaises(ExperimentValidationError): validate_config({"rate": float("nan")})

    def test_fixture_job_requires_explicit_fixture_mode_and_no_dataset(self):
        db = FakeDB()
        sent = {}
        def publish(**kwargs):
            sent.update(kwargs); return SimpleNamespace(id=kwargs["task_id"])
        job = create_experiment(
            db, kind=self.KIND, config={"fixture_mode": True}, dataset_id=None,
            organization_id=7, publisher=publish,
        )
        self.assertEqual(job.status, TrainingStatus.PENDING)
        self.assertIsNone(job.dataset_id)
        self.assertEqual(sent["args"], [job.id, 7])
        self.assertEqual(serialize_experiment(job)["runtime"]["dispatch_state"], "ENQUEUED")
        with self.assertRaises(ExperimentValidationError):
            create_experiment(FakeDB(), kind=self.KIND, config={}, dataset_id=None, organization_id=7, publisher=publish)

    def test_dataset_tenant_mismatch_fails_before_persistence(self):
        dataset = SimpleNamespace(id=3, organization_id=8, size_bytes=1, row_count=1)
        db = FakeDB(dataset)
        with self.assertRaises(ExperimentAccessDenied):
            create_experiment(db, kind=self.KIND, config={}, dataset_id=3, organization_id=7, publisher=lambda **_: None)
        self.assertIsNone(db.job)

    def test_queue_failure_is_persisted_failed(self):
        db = FakeDB()
        with self.assertRaises(ExperimentRuntimeError):
            create_experiment(
                db, kind=self.KIND, config={"fixture_mode": True}, dataset_id=None,
                organization_id=7, publisher=lambda **_: (_ for _ in ()).throw(OSError("offline")),
            )
        self.assertEqual(db.job.status, TrainingStatus.FAILED)
        self.assertIn("dispatch failed", db.job.error_message)

    def test_fixture_exec_persists_metrics_artifact_model_version_and_source(self):
        db = FakeDB()
        job = create_experiment(
            db, kind=self.KIND, config={"fixture_mode": True}, dataset_id=None,
            organization_id=7,
            publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
        )
        def adapter(context):
            self.assertIsNone(context.dataset_id)
            context.progress(0.5, {"loss": 0.2})
            artifact = context.work_dir / "model.json"
            artifact.write_text('{"ok":true}', encoding="utf-8")
            return {
                "metrics": {"score": 0.9}, "artifact_path": str(artifact),
                "source": {"type": "deterministic_fixture"},
                "model": {"name": "unit-model", "model_type": "test", "framework": "stdlib"},
            }
        with tempfile.TemporaryDirectory() as root:
            result = execute_persisted_experiment(
                db, job.id, 7, task_id=job.training_job_id,
                artifact_root=root, adapter=adapter,
            )
        payload = serialize_experiment(result)
        self.assertEqual(result.status, TrainingStatus.COMPLETED)
        self.assertEqual(result.metrics, {"score": 0.9})
        self.assertEqual(len(db.models), 1)
        self.assertEqual(len(db.versions), 1)
        self.assertEqual(payload["metadata"]["data_source"]["type"], "deterministic_fixture")
        self.assertEqual(payload["progress"], 100.0)

    def test_unknown_kind_fails_closed(self):
        with self.assertRaises(ExperimentValidationError):
            create_experiment(
                FakeDB(), kind="not_registered", config={"fixture_mode": True},
                dataset_id=None, organization_id=1, publisher=lambda **_: None,
            )

    def test_all_four_builtin_adapters_complete_through_durable_runtime(self):
        cases = {
            "causal": {
                "fixture_mode": True, "method": "linear_regression",
                "treatment": "X", "outcome": "Y", "confounders": ["Z"],
            },
            "recsys": {
                "fixture_mode": True, "method": "matrix_factorization",
                "factors": 4, "epochs": 5,
            },
            "gnn": {"fixture_mode": True, "epochs": 5, "hidden_dim": 8},
            "gan": {
                "fixture_mode": True, "epochs": 2, "latent_dim": 4,
                "hidden_dim": 8, "batch_size": 64,
            },
        }
        with tempfile.TemporaryDirectory() as root:
            for kind, config in cases.items():
                with self.subTest(kind=kind):
                    db = FakeDB()
                    job = create_experiment(
                        db, kind=kind, config=config, dataset_id=None,
                        organization_id=7,
                        publisher=lambda **kw: SimpleNamespace(id=kw["task_id"]),
                    )
                    result = execute_persisted_experiment(
                        db, job.id, 7, task_id=job.training_job_id,
                        artifact_root=root,
                    )
                    payload = serialize_experiment(result)
                    self.assertEqual(result.status, TrainingStatus.COMPLETED)
                    self.assertTrue(result.metrics)
                    self.assertTrue(Path(payload["artifact"]["path"]).is_file())
                    self.assertEqual(payload["progress"], 100.0)


if __name__ == "__main__":
    unittest.main()
