"""Contracts for low-cardinality model-factory experiment telemetry."""
from __future__ import annotations

import logging
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from prometheus_client import REGISTRY, generate_latest

from utils.experiment_metrics import (
    EXPERIMENT_DISPATCH_FAILURES,
    EXPERIMENT_DURATION,
    EXPERIMENT_JOBS,
    EXPERIMENT_KINDS,
    EXPERIMENT_OUTCOMES,
    log_experiment_event,
    metric_kind,
    observe_created,
    observe_dispatch_failure,
    observe_terminal,
)


def _counter_value(metric, **labels):
    return metric.labels(**labels)._value.get()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class ExperimentObservabilityTest(unittest.TestCase):
    def test_metric_label_sets_are_fixed_and_do_not_accept_identifiers(self):
        self.assertEqual(EXPERIMENT_JOBS._labelnames, ("kind", "outcome"))
        self.assertEqual(EXPERIMENT_DURATION._labelnames, ("kind", "outcome"))
        self.assertEqual(EXPERIMENT_DISPATCH_FAILURES._labelnames, ("kind",))
        forbidden = {"organization_id", "job_id", "name", "config", "task_id"}
        self.assertTrue(forbidden.isdisjoint(EXPERIMENT_JOBS._labelnames))
        self.assertTrue(forbidden.isdisjoint(EXPERIMENT_DURATION._labelnames))
        self.assertTrue(forbidden.isdisjoint(EXPERIMENT_DISPATCH_FAILURES._labelnames))

    def test_kind_vocabulary_is_bounded(self):
        self.assertEqual(metric_kind("recommender"), "recsys")
        self.assertEqual(metric_kind("tenant-123-custom-model"), "other")
        self.assertEqual(
            {metric_kind(value) for value in (*EXPERIMENT_KINDS, "anything")},
            set(EXPERIMENT_KINDS) | {"other"},
        )

    def test_lifecycle_duration_and_dispatch_metrics_are_exposed(self):
        jobs_before = _counter_value(
            EXPERIMENT_JOBS, kind="causal", outcome="created"
        )
        failed_before = _counter_value(
            EXPERIMENT_JOBS, kind="causal", outcome="failed"
        )
        dispatch_before = _counter_value(EXPERIMENT_DISPATCH_FAILURES, kind="causal")
        created = datetime(2026, 1, 1, 0, 0, 0)
        observe_created("causal")
        observe_dispatch_failure("causal")
        observe_terminal(
            "causal", "failed", created_at=created,
            completed_at=created + timedelta(seconds=2),
        )
        self.assertEqual(
            _counter_value(EXPERIMENT_JOBS, kind="causal", outcome="created"),
            jobs_before + 1,
        )
        self.assertEqual(
            _counter_value(EXPERIMENT_JOBS, kind="causal", outcome="failed"),
            failed_before + 1,
        )
        self.assertEqual(
            _counter_value(EXPERIMENT_DISPATCH_FAILURES, kind="causal"),
            dispatch_before + 1,
        )
        exposed = generate_latest(REGISTRY).decode("utf-8")
        self.assertIn("model_factory_experiment_jobs_total", exposed)
        self.assertIn("model_factory_experiment_job_duration_seconds", exposed)
        self.assertIn("model_factory_experiment_dispatch_failures_total", exposed)

    def test_terminal_outcome_is_allowlisted(self):
        self.assertEqual(
            EXPERIMENT_OUTCOMES,
            frozenset({"created", "completed", "failed", "cancelled"}),
        )
        with self.assertRaises(ValueError):
            observe_terminal("gan", "user-supplied-status")

    def test_structured_log_contains_no_config_or_exception_message(self):
        capture = _Capture()
        telemetry_logger = logging.getLogger("foundation.experiment")
        old_level = telemetry_logger.level
        telemetry_logger.setLevel(logging.INFO)
        telemetry_logger.addHandler(capture)
        try:
            log_experiment_event(
                "failed", kind="gan", job_id=7, organization_id=9,
                status="FAILED", error_type="RuntimeError",
            )
        finally:
            telemetry_logger.removeHandler(capture)
            telemetry_logger.setLevel(old_level)
        self.assertEqual(len(capture.records), 1)
        record = capture.records[0]
        self.assertEqual(record.experiment_event, "failed")
        self.assertEqual(record.experiment_kind, "gan")
        self.assertEqual(record.experiment_error_type, "RuntimeError")
        rendered = record.getMessage()
        self.assertNotIn("config", record.__dict__)
        self.assertNotIn("password", record.__dict__)
        self.assertNotIn("secret-value", rendered)

    def test_celery_success_path_emits_bounded_structured_event(self):
        from tasks.experiment_tasks import execute_experiment_job

        db = SimpleNamespace(close=lambda: None, rollback=lambda: None)
        job = SimpleNamespace(
            id=7, organization_id=9, status=SimpleNamespace(value="COMPLETED")
        )
        execute_experiment_job.push_request(id="delivery-1")
        try:
            with (
                patch("tasks.experiment_tasks.SessionLocal", return_value=db),
                patch(
                    "services.experiment_runtime.execute_persisted_experiment",
                    return_value=job,
                ),
                patch("services.experiment_runtime.experiment_kind", return_value="gnn"),
                patch(
                    "services.experiment_runtime.serialize_experiment",
                    return_value={"status": "COMPLETED"},
                ),
                patch("tasks.experiment_tasks.log_experiment_event") as log_event,
            ):
                result = execute_experiment_job.run(7, 9)
        finally:
            execute_experiment_job.pop_request()
        self.assertEqual(result, {"status": "COMPLETED"})
        self.assertEqual(log_event.call_args.args, ("celery_delivery_finished",))
        self.assertEqual(log_event.call_args.kwargs["kind"], "gnn")
        self.assertNotIn("config", log_event.call_args.kwargs)

    def test_celery_failure_logs_type_but_not_exception_text(self):
        from tasks.experiment_tasks import execute_experiment_job

        db = SimpleNamespace(close=lambda: None, rollback=lambda: None)
        job = SimpleNamespace(
            id=7, organization_id=9, status=SimpleNamespace(value="FAILED")
        )
        execute_experiment_job.push_request(id="delivery-2")
        try:
            with (
                patch("tasks.experiment_tasks.SessionLocal", return_value=db),
                patch(
                    "services.experiment_runtime.execute_persisted_experiment",
                    side_effect=RuntimeError("secret-value"),
                ),
                patch(
                    "services.experiment_runtime.mark_experiment_failed",
                    return_value=job,
                ),
                patch("services.experiment_runtime.experiment_kind", return_value="gan"),
                patch(
                    "services.experiment_runtime.serialize_experiment",
                    return_value={"status": "FAILED"},
                ),
                patch("tasks.experiment_tasks.log_experiment_event") as log_event,
            ):
                result = execute_experiment_job.run(7, 9)
        finally:
            execute_experiment_job.pop_request()
        self.assertEqual(result, {"status": "FAILED"})
        kwargs = log_event.call_args.kwargs
        self.assertEqual(kwargs["error_type"], "RuntimeError")
        self.assertNotIn("error", kwargs)
        self.assertNotIn("config", kwargs)
        self.assertNotIn("secret-value", str(log_event.call_args))


class ExperimentRuntimeMetricHookTest(unittest.TestCase):
    KIND = "observability_test"

    def setUp(self):
        from services.experiment_runtime import register_adapter

        register_adapter(
            self.KIND, lambda _context: {"metrics": {"score": 1.0}},
            replace=True,
        )

    def tearDown(self):
        from services.experiment_runtime import unregister_adapter

        unregister_adapter(self.KIND)

    @staticmethod
    def _db():
        from backend.tests.test_experiment_runtime import FakeDB

        return FakeDB()

    @staticmethod
    def _publisher(**kwargs):
        return SimpleNamespace(id=kwargs["task_id"])

    def _create(self, db, **kwargs):
        from services.experiment_runtime import create_experiment

        publisher = kwargs.pop("publisher", self._publisher)
        return create_experiment(
            db, kind=self.KIND, config={"fixture_mode": True},
            dataset_id=None, organization_id=7, publisher=publisher,
            audit_writer=lambda **_event: True, **kwargs,
        )

    def test_create_and_completed_transition_count_once_on_redelivery(self):
        import tempfile
        from services.experiment_runtime import execute_persisted_experiment

        db = self._db()
        with (
            patch("services.experiment_runtime.observe_created") as created,
            patch("services.experiment_runtime.observe_terminal") as terminal,
        ):
            job = self._create(db)
            with tempfile.TemporaryDirectory() as root:
                execute_persisted_experiment(
                    db, job.id, 7, task_id=job.training_job_id,
                    artifact_root=root,
                    adapter=lambda _context: {"metrics": {"score": 1.0}},
                    audit_writer=lambda **_event: True,
                )
                execute_persisted_experiment(
                    db, job.id, 7, task_id=job.training_job_id,
                    artifact_root=root,
                    adapter=lambda _context: {"metrics": {"score": 1.0}},
                    audit_writer=lambda **_event: True,
                )
        created.assert_called_once_with(self.KIND)
        self.assertEqual(terminal.call_count, 1)
        self.assertEqual(terminal.call_args.args, (self.KIND, "completed"))

    def test_failed_transition_is_idempotent(self):
        from services.experiment_runtime import mark_experiment_failed

        db = self._db()
        job = self._create(db)
        with patch("services.experiment_runtime.observe_terminal") as terminal:
            mark_experiment_failed(
                db, job.id, 7, RuntimeError("first"),
                audit_writer=lambda **_event: True,
            )
            mark_experiment_failed(
                db, job.id, 7, RuntimeError("redelivery"),
                audit_writer=lambda **_event: True,
            )
        self.assertEqual(terminal.call_count, 1)
        self.assertEqual(terminal.call_args.args, (self.KIND, "failed"))


class PersistedExperimentMetricSnapshotTest(unittest.TestCase):
    KIND = "causal"

    @staticmethod
    def _db():
        from backend.tests.test_experiment_runtime import FakeDB

        return FakeDB()

    @staticmethod
    def _create(db, **kwargs):
        from services.experiment_runtime import create_experiment

        publisher = kwargs.pop(
            "publisher", lambda **item: SimpleNamespace(id=item["task_id"])
        )
        return create_experiment(
            db, kind="causal", config={"fixture_mode": True}, dataset_id=None,
            organization_id=7, publisher=publisher,
            audit_writer=lambda **_event: True, **kwargs,
        )

    def test_snapshot_aggregates_worker_terminal_state_with_bounded_labels(self):
        from api.metrics_api import (
            _update_experiment_persisted_metrics,
            experiment_persisted_jobs,
            experiment_persisted_snapshot_up,
        )

        class Result:
            def all(self):
                return [
                    ("causal", "COMPLETED", 3),
                    ("recommender", "running", 2),
                    ("tenant-custom-kind", "FAILED", 4),
                    ("gan", "NOT_A_STATUS", 99),
                ]

        db = SimpleNamespace(execute=lambda _query: Result(), close=lambda: None)
        _update_experiment_persisted_metrics(session_factory=lambda: db)
        self.assertEqual(
            experiment_persisted_jobs.labels(
                kind="causal", status="COMPLETED"
            )._value.get(),
            3,
        )
        self.assertEqual(
            experiment_persisted_jobs.labels(
                kind="recsys", status="RUNNING"
            )._value.get(),
            2,
        )
        self.assertEqual(
            experiment_persisted_jobs.labels(
                kind="other", status="FAILED"
            )._value.get(),
            4,
        )
        self.assertEqual(experiment_persisted_snapshot_up._value.get(), 1)
        self.assertEqual(experiment_persisted_jobs._labelnames, ("kind", "status"))
        self.assertTrue(
            {"organization_id", "job_id", "name", "config"}.isdisjoint(
                experiment_persisted_jobs._labelnames
            )
        )

    def test_snapshot_failure_is_visible_and_session_is_closed(self):
        from api.metrics_api import (
            _update_experiment_persisted_metrics,
            experiment_persisted_snapshot_up,
        )

        state = {"closed": False}

        def fail(_query):
            raise OSError("database unavailable")

        db = SimpleNamespace(
            execute=fail,
            close=lambda: state.__setitem__("closed", True),
        )
        with self.assertRaises(OSError):
            _update_experiment_persisted_metrics(session_factory=lambda: db)
        self.assertTrue(state["closed"])
        self.assertEqual(experiment_persisted_snapshot_up._value.get(), 0)

    def test_cancelled_transition_is_idempotent(self):
        from services.experiment_runtime import cancel_experiment

        db = self._db()
        job = self._create(db)
        with patch("services.experiment_runtime.observe_terminal") as terminal:
            cancel_experiment(
                db, job.id, organization_id=7, revoker=lambda *_args: None,
                audit_writer=lambda **_event: True,
            )
            cancel_experiment(
                db, job.id, organization_id=7, revoker=lambda *_args: None,
                audit_writer=lambda **_event: True,
            )
        self.assertEqual(terminal.call_count, 1)
        self.assertEqual(terminal.call_args.args, (self.KIND, "cancelled"))

    def test_dispatch_failure_counts_failure_and_dispatch_once(self):
        from services.experiment_runtime import ExperimentRuntimeError

        db = self._db()
        with (
            patch("services.experiment_runtime.observe_created") as created,
            patch("services.experiment_runtime.observe_dispatch_failure") as dispatch,
            patch("services.experiment_runtime.observe_terminal") as terminal,
            self.assertRaises(ExperimentRuntimeError),
        ):
            self._create(
                db,
                publisher=lambda **_kwargs: (_ for _ in ()).throw(OSError("offline")),
            )
        created.assert_called_once_with(self.KIND)
        dispatch.assert_called_once_with(self.KIND)
        self.assertEqual(terminal.call_count, 1)
        self.assertEqual(terminal.call_args.args, (self.KIND, "failed"))


if __name__ == "__main__":
    unittest.main()
