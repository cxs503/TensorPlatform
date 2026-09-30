"""Side-effect-free privacy and correlation contracts for Pipeline telemetry."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from services.pipeline_observability import (
    ensure_run_trace,
    ensure_task_trace,
    metric_task_type,
    refresh_running_gauge,
)


class RecordingSession:
    def __init__(self):
        self.calls = []
        self.committed = False
        self.closed = False

    def execute(self, statement, values):
        self.calls.append((str(statement), values))

    def commit(self):
        self.committed = True

    def rollback(self):
        raise AssertionError("successful audit must not roll back")

    def close(self):
        self.closed = True


class BrokenMetricDatabase:
    def execute(self, _statement):
        raise RuntimeError("metrics database unavailable")


class PipelineObservabilityTests(unittest.TestCase):
    def test_trace_ids_are_stable_and_never_client_supplied(self):
        run = SimpleNamespace(id=71, organization_id=9, parameters={"config": {"token": "secret"}})
        task = SimpleNamespace(id=3, outputs={})
        first = ensure_run_trace(run)
        self.assertEqual(first, ensure_run_trace(run))
        self.assertEqual(f"{first}:task:3", ensure_task_trace(run, task))
        self.assertNotIn("secret", json.dumps(run.parameters["observability"]))
        self.assertNotIn("secret", json.dumps(task.outputs["observability"]))

    def test_audit_metadata_excludes_config_and_error_content(self):
        session = RecordingSession()
        from services.pipeline_observability import emit_pipeline_audit

        self.assertTrue(
            emit_pipeline_audit(
                actor_id="engineer",
                organization_id=9,
                run_id=71,
                task_run_id=3,
                action="node_failed",
                result="failed",
                trace_id="a" * 32,
                session_factory=lambda: session,
            )
        )
        detail = session.calls[0][1]["detail"]
        self.assertNotIn("secret", detail)
        self.assertNotIn("sensitive", detail)
        self.assertEqual(
            {"action", "actor_id", "organization_id", "result", "run_id", "task_run_id", "trace_id"},
            set(json.loads(detail)),
        )
        self.assertTrue(session.committed)
        self.assertTrue(session.closed)

    def test_metric_labels_are_bounded_and_metric_refresh_cannot_raise(self):
        self.assertEqual("data_load", metric_task_type("data_source"))
        self.assertEqual("model_train", metric_task_type("train"))
        self.assertEqual("other", metric_task_type("arbitrary-user-value"))
        refresh_running_gauge(BrokenMetricDatabase())


if __name__ == "__main__":
    unittest.main()
