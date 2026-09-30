"""Deterministic audit contracts for the model-factory experiment runtime."""
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from models.core import Dataset, Model, ModelVersion, TrainingJob, TrainingStatus
from services.experiment_audit import ENTITY_TYPE, emit_experiment_audit
from services.experiment_runtime import (
    ExperimentAccessDenied,
    ExperimentRuntimeError,
    cancel_experiment,
    create_experiment,
    execute_persisted_experiment,
    mark_experiment_failed,
    register_adapter,
    unregister_adapter,
)


class _Query:
    def __init__(self, db, model): self.db, self.model = db, model
    def filter(self, *_args): return self
    def with_for_update(self): return self
    def order_by(self, *_args): return self
    def offset(self, *_args): return self
    def limit(self, *_args): return self
    def first(self):
        if self.model is Dataset: return self.db.dataset
        if self.model is TrainingJob: return self.db.job
        return None
    def all(self): return list(self.db.active_jobs) if self.model is TrainingJob else []


class _DB:
    def __init__(self, dataset=None):
        self.dataset, self.job, self.active_jobs = dataset, None, []
        self.models, self.versions, self.commits = [], [], 0
    def query(self, model): return _Query(self, model)
    def add(self, value):
        if isinstance(value, TrainingJob):
            value.id = value.id or 1
            self.job = value
        elif isinstance(value, Model):
            value.id = len(self.models) + 10
            self.models.append(value)
        elif isinstance(value, ModelVersion):
            value.id = len(self.versions) + 20
            self.versions.append(value)
    def flush(self): pass
    def refresh(self, _value): pass
    def commit(self): self.commits += 1
    def rollback(self): pass


class _AuditDB:
    def __init__(self, fail=False):
        self.fail = fail
        self.params = None
        self.committed = self.rolled_back = self.closed = False
    def execute(self, _statement, params):
        self.params = params
        if self.fail: raise RuntimeError("audit unavailable")
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True


class ExperimentAuditAdapterTest(unittest.TestCase):
    def test_existing_audit_table_receives_only_sanitized_structure(self):
        db = _AuditDB()
        ok = emit_experiment_audit(
            actor_id="42", organization_id=7, job_id=9, kind="causal",
            action="create", outcome="success", session_factory=lambda: db,
        )
        self.assertTrue(ok)
        self.assertTrue(db.committed)
        self.assertTrue(db.closed)
        self.assertEqual(db.params["action"], "experiment.create")
        self.assertEqual(db.params["entity_type"], ENTITY_TYPE)
        self.assertEqual(db.params["entity_id"], "9")
        self.assertEqual(db.params["user_name"], "42")
        detail = json.loads(db.params["detail"])
        self.assertEqual(
            set(detail),
            {"actor_id", "organization_id", "job_id", "kind", "action", "outcome", "occurred_at"},
        )
        self.assertEqual(detail["organization_id"], 7)
        self.assertNotIn("config", db.params["detail"])
        self.assertNotIn("dataset", db.params["detail"])

    def test_audit_storage_failure_is_explicit_and_rolled_back(self):
        db = _AuditDB(fail=True)
        with self.assertLogs("services.experiment_audit", level="WARNING") as logs:
            ok = emit_experiment_audit(
                actor_id="42", organization_id=7, job_id=9, kind="gan",
                action="execute", outcome="failed", session_factory=lambda: db,
            )
        self.assertFalse(ok)
        self.assertTrue(db.rolled_back)
        self.assertTrue(db.closed)
        self.assertIn("error_type=RuntimeError", logs.output[0])


class ExperimentRuntimeAuditTest(unittest.TestCase):
    KIND = "audit_unit"

    def setUp(self):
        register_adapter(self.KIND, lambda _ctx: {"metrics": {"score": 1.0}}, replace=True)

    def tearDown(self):
        unregister_adapter(self.KIND)

    @staticmethod
    def _writer(events, result=True):
        def write(**event):
            events.append(event)
            return result
        return write

    def _create(self, db, events, publisher=None, actor="77"):
        return create_experiment(
            db, kind=self.KIND, config={"fixture_mode": True, "column": "sensitive-column"},
            dataset_id=None, organization_id=7, actor_id=actor,
            audit_writer=self._writer(events),
            publisher=publisher or (lambda **kw: SimpleNamespace(id=kw["task_id"])),
        )

    def test_create_and_completed_execution_have_actor_and_worker_events(self):
        events, db = [], _DB()
        job = self._create(db, events)
        with tempfile.TemporaryDirectory() as root:
            execute_persisted_experiment(
                db, job.id, 7, task_id=job.training_job_id, artifact_root=root,
                actor_id="system/celery", audit_writer=self._writer(events),
            )
        self.assertEqual(job.status, TrainingStatus.COMPLETED)
        self.assertEqual(
            [(e["action"], e["outcome"], e["actor_id"]) for e in events],
            [("create", "success", "77"), ("execute", "completed", "system/celery")],
        )
        self.assertTrue(all("config" not in event for event in events))
        self.assertTrue(all("sensitive-column" not in repr(event) for event in events))

    def test_dispatch_failure_is_failed_and_audited_without_error_content(self):
        events, db = [], _DB()
        with self.assertRaises(ExperimentRuntimeError):
            self._create(
                db, events,
                publisher=lambda **_: (_ for _ in ()).throw(OSError("broker secret text")),
            )
        self.assertEqual(db.job.status, TrainingStatus.FAILED)
        self.assertEqual(
            [(e["action"], e["outcome"]) for e in events],
            [("create", "success"), ("dispatch", "failed")],
        )
        self.assertNotIn("broker secret text", repr(events))

    def test_cancel_is_tenant_bound_and_foreign_request_emits_nothing(self):
        events, db = [], _DB()
        job = self._create(db, events)
        events.clear()
        with self.assertRaises(ExperimentAccessDenied):
            cancel_experiment(
                db, job.id, organization_id=8, actor_id="88",
                audit_writer=self._writer(events), revoker=lambda *_args, **_kwargs: None,
            )
        self.assertEqual(events, [])
        self.assertEqual(job.status, TrainingStatus.PENDING)
        cancel_experiment(
            db, job.id, organization_id=7, actor_id="77",
            audit_writer=self._writer(events), revoker=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(job.status, TrainingStatus.CANCELLED)
        self.assertEqual((events[0]["action"], events[0]["outcome"]), ("cancel", "cancelled"))

    def test_audit_failure_cannot_change_failed_job_into_success(self):
        events, db = [], _DB()
        job = self._create(db, events)
        result = mark_experiment_failed(
            db, job.id, 7, RuntimeError("training failed"),
            audit_writer=lambda **_kwargs: False,
        )
        self.assertEqual(result.status, TrainingStatus.FAILED)
        self.assertIn("training failed", result.error_message)


class ExperimentApiActorTest(unittest.TestCase):
    def test_actor_helper_prefers_authenticated_user_id(self):
        from api.experiment_http import audit_actor

        self.assertEqual(audit_actor({"id": 123, "username": "alice"}), "123")
        self.assertEqual(audit_actor({"username": "alice"}), "alice")

    def test_all_training_apis_forward_actor_on_create(self):
        from api import causal_api, gan_training, gnn_training, recsys_training

        cases = [
            (causal_api, causal_api.analyze_causal, causal_api.CausalReq(fixture_mode=True)),
            (recsys_training, recsys_training.train_recsys, recsys_training.RecSysTrainReq(fixture_mode=True)),
            (gnn_training, gnn_training.train_gnn, gnn_training.GNNConfig(fixture_mode=True)),
            (gan_training, gan_training.train_gan, gan_training.GANTrainReq(fixture_mode=True)),
        ]
        for module, function, request in cases:
            with self.subTest(module=module.__name__), \
                 patch.object(module, "experiment_call", return_value=object()) as call, \
                 patch.object(module, "_shape", return_value={}):
                function(request, db=object(), current_user={"id": 55, "organization_id": 7})
                self.assertEqual(call.call_args.kwargs["actor_id"], "55")

    def test_cancellable_apis_forward_actor_on_cancel(self):
        from api import gan_training, gnn_training, recsys_training

        cases = [recsys_training, gnn_training, gan_training]
        for module in cases:
            function = next(
                getattr(module, name) for name in ("cancel_job", "cancel_gnn_job")
                if hasattr(module, name)
            )
            with self.subTest(module=module.__name__), \
                 patch.object(module, "experiment_call", return_value=object()) as call, \
                 patch.object(module, "_shape", return_value={}):
                function(9, db=object(), current_user={"id": 56, "organization_id": 7})
                self.assertEqual(call.call_args.kwargs["actor_id"], "56")


if __name__ == "__main__":
    unittest.main()
