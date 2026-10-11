"""Side-effect-free contracts for durable Pipeline orchestration."""
import inspect
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from api.ml_pipeline import resume_pipeline_run, run_pipeline, run_resume_allowed
from tasks.celery_app import celery_app
from tasks.pipeline_tasks import (
    _dispatch_training,
    _execute_adapter_node,
    assert_adapter_dispatch_safe,
    _poll_training,
    _run_specs,
    _timed_out,
    _timeout_seconds,
    coordinator_intent_repairable,
    coordinator_message_current,
    enqueue_next_coordinator,
    execute_pipeline_run,
    mark_run_dispatch_failed,
    merge_dependency_outputs,
    node_action,
    retry_allowed,
)


class NoQueryDatabase:
    def query(self, *_args, **_kwargs):
        raise AssertionError("immutable snapshot must not query mutable task definitions")


class CommitRecorder:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class PipelineOrchestratorContractTest(unittest.TestCase):
    def test_task_is_registered_with_durable_delivery_boundaries(self):
        self.assertIn("pipeline.execute_run", celery_app.tasks)
        self.assertTrue(execute_pipeline_run.acks_late)
        self.assertTrue(execute_pipeline_run.reject_on_worker_lost)
        self.assertEqual(execute_pipeline_run.soft_time_limit, 75)
        self.assertEqual(execute_pipeline_run.time_limit, 90)

    def test_api_queues_coordinator_without_daemon_thread(self):
        source = inspect.getsource(run_pipeline)
        self.assertIn("execute_pipeline_run.apply_async", source)
        self.assertIn('status="PENDING"', source)
        self.assertIn("mark_run_dispatch_failed", source)
        self.assertNotIn("threading.Thread", source)
        self.assertNotIn("daemon=True", source)

    def test_resume_endpoint_is_write_scoped_and_never_redispatches_training(self):
        source = inspect.getsource(resume_pipeline_run)
        self.assertIn('require_permission("training:write")', source)
        self.assertIn("_get_run(db, rid, current_user)", source)
        self.assertIn("write=True", source)
        self.assertIn("coordinator_lock", source)
        self.assertIn("enqueue_next_coordinator", source)
        self.assertIn("mark_run_dispatch_failed", source)
        self.assertNotIn("_dispatch_training", source)
        self.assertNotIn("_enqueue_training", source)

    def test_resume_rejects_terminal_states(self):
        self.assertTrue(run_resume_allowed("PENDING"))
        self.assertTrue(run_resume_allowed("RUNNING"))
        for status in ("SUCCESS", "FAILED", "CANCELLED", "SKIPPED", None):
            self.assertFalse(run_resume_allowed(status))

    def test_dependency_failure_skips_only_descendants(self):
        self.assertEqual(node_action("PENDING", ["FAILED"]), "skip")
        self.assertEqual(node_action("PENDING", []), "execute")
        self.assertEqual(node_action("RUNNING", ["SUCCESS"]), "resume")
        self.assertEqual(node_action("PENDING", ["RUNNING"]), "wait")
        self.assertEqual(node_action("SUCCESS", ["FAILED"]), "terminal")

    def test_immutable_snapshot_is_recovery_source(self):
        snapshot = [
            {
                "task_run_id": 11,
                "name": "load",
                "task_type": "data_load",
                "config": {"dataset_id": 42},
                "dependencies": [],
                "timeout": 60,
            }
        ]
        run = SimpleNamespace(parameters={"orchestrator": {"tasks": snapshot}})
        result = _run_specs(NoQueryDatabase(), run)
        self.assertEqual(result, snapshot)
        self.assertIsNot(result[0], snapshot[0])

    def test_identity_conflict_fails_closed(self):
        with self.assertRaises(ValueError):
            merge_dependency_outputs(
                {},
                {"left": {"dataset_id": 1}, "right": {"dataset_id": 2}},
            )

    def test_timeout_is_bounded_and_deterministic(self):
        self.assertEqual(_timeout_seconds({"timeout": 1}), 30)
        self.assertEqual(_timeout_seconds({"timeout": 99999}), 3600)
        now = datetime.utcnow()
        self.assertTrue(_timed_out(now - timedelta(seconds=31), 30, now))
        self.assertFalse(_timed_out(now - timedelta(seconds=29), 30, now))

    def test_only_side_effect_free_nodes_are_auto_retryable(self):
        self.assertTrue(retry_allowed("data_load", 1, 1))
        self.assertFalse(retry_allowed("data_load", 2, 1))
        self.assertFalse(retry_allowed("data_process", 1, 5))
        self.assertFalse(retry_allowed("feature_engineering", 1, 5))
        self.assertFalse(retry_allowed("model_train", 1, 5))
        self.assertEqual(execute_pipeline_run.max_retries, 5)

    def test_training_dispatch_does_not_leak_orchestrator_metadata(self):
        task_run = SimpleNamespace(
            id=30,
            status="PENDING",
            started_at=None,
            celery_task_id=None,
            outputs={},
            logs=None,
        )
        run = SimpleNamespace(organization_id=3, validation_context={})
        db = SimpleNamespace(commit=lambda: None, execute=lambda *_args, **_kwargs: None)
        captured = {}

        def create_job(config):
            captured["config"] = config
            return "job-1"

        with patch("tasks.pipeline_tasks._validated_dataset", return_value=object()), patch(
            "services.real_training_service.TrainingService.create_job",
            side_effect=create_job,
        ), patch("tasks.training_tasks.train_model.apply_async", return_value=None):
            _dispatch_training(
                db,
                task_run,
                {
                    "dataset_id": 51,
                    "model_type": "random_forest",
                    "training_task_type": "regression",
                    "_upstream_outputs": {"load": {"dataset_id": 51}},
                },
                run,
                3600,
            )

        self.assertNotIn("_upstream_outputs", captured["config"])
        self.assertNotIn("training_task_type", captured["config"])
        self.assertEqual(captured["config"]["task_type"], "regression")

    def test_uncertain_derived_data_write_is_never_replayed(self):
        with self.assertRaisesRegex(RuntimeError, "避免重复写入"):
            assert_adapter_dispatch_safe("data_process", "resume")
        with self.assertRaisesRegex(RuntimeError, "避免重复写入"):
            assert_adapter_dispatch_safe("feature_engineering", "resume")
        self.assertIsNone(assert_adapter_dispatch_safe("data_load", "resume"))

    def test_durable_adapter_rechecks_run_organization_and_passes_capability(self):
        dataset = SimpleNamespace(
            id=7,
            organization_id=3,
            file_path="/app/uploads/datasets/7_data.csv",
            name="source",
            row_count=20,
            size_bytes=200,
        )
        model = SimpleNamespace(
            id=12,
            organization_id=3,
            model_blob=b"registered-model",
            model_type="regression",
        )
        run = SimpleNamespace(organization_id=3)
        captured = {}

        def fake_execute(task_type, config, **kwargs):
            captured.update(task_type=task_type, config=config, **kwargs)
            return {"dataset_id": 7, "model_id": 12, "metrics": {"R2": 0.9}}

        with patch("tasks.pipeline_tasks._validated_dataset", return_value=dataset), patch(
            "tasks.pipeline_tasks._validated_model", return_value=model
        ), patch(
            "services.pipeline_node_executor.execute_pipeline_node",
            side_effect=fake_execute,
        ):
            result = _execute_adapter_node(
                SimpleNamespace(),
                {"dataset_id": 7, "model_id": 12},
                run,
                "model_evaluate",
            )
        self.assertEqual(result["metrics"]["R2"], 0.9)
        self.assertEqual(captured["task_type"], "model_evaluate")
        self.assertEqual(captured["access"].organization_id, 3)
        self.assertEqual(set(captured["access"].datasets), {7})
        self.assertEqual(set(captured["access"].models), {12})
        self.assertIsNone(captured["derived_dataset_sink"])

    def test_reconcile_routes_adapter_nodes_through_real_executor(self):
        from tasks.pipeline_tasks import reconcile_pipeline_run

        source = inspect.getsource(reconcile_pipeline_run)
        self.assertIn("_execute_adapter_node", source)
        self.assertIn("assert_adapter_dispatch_safe", source)

    def test_stale_coordinator_generation_does_not_spawn_a_new_chain(self):
        parameters = {"orchestrator": {"coordinator_task_id": "generation-2"}}
        self.assertTrue(coordinator_message_current(parameters, "generation-2"))
        self.assertFalse(coordinator_message_current(parameters, "generation-1"))
        self.assertFalse(coordinator_message_current(parameters, None))
        self.assertTrue(coordinator_message_current({}, "legacy-message"))

    def test_fast_worker_sees_persisted_generation_before_publish(self):
        db = CommitRecorder()
        run = SimpleNamespace(
            parameters={"orchestrator": {"coordinator_task_id": "generation-1"}}
        )

        def immediate_worker(**options):
            self.assertEqual(db.commits, 1, "intent must commit before broker publish")
            self.assertEqual(options["task_id"], "generation-2")
            self.assertTrue(
                coordinator_message_current(run.parameters, options["task_id"])
            )
            self.assertEqual(
                run.parameters["orchestrator"]["dispatch_state"],
                "INTENT_RECORDED",
            )
            return SimpleNamespace(id=options["task_id"])

        published = enqueue_next_coordinator(
            db,
            run,
            7,
            "generation-1",
            publisher=immediate_worker,
            next_task_id="generation-2",
        )
        self.assertEqual(published.id, "generation-2")
        self.assertEqual(db.commits, 2)
        self.assertEqual(run.parameters["orchestrator"]["dispatch_state"], "ENQUEUED")

    def test_parent_redelivery_can_repair_unpublished_intent(self):
        parameters = {
            "orchestrator": {
                "coordinator_task_id": "generation-2",
                "dispatch_parent_task_id": "generation-1",
                "dispatch_state": "INTENT_RECORDED",
            }
        }
        self.assertTrue(coordinator_intent_repairable(parameters, "generation-1"))
        self.assertFalse(coordinator_intent_repairable(parameters, "unrelated"))

    def test_uncertain_training_dispatch_never_requeues(self):
        task_run = SimpleNamespace(
            outputs={"job_id": "train_123", "dispatch_state": "INTENT_RECORDED"},
            started_at=datetime.utcnow(),
        )
        with self.assertRaisesRegex(RuntimeError, "避免重复训练"):
            _poll_training(None, task_run, {}, datetime.utcnow(), 60)

    def test_queue_failure_marks_run_and_unstarted_tasks_terminal(self):
        task_runs = [
            SimpleNamespace(status="PENDING", error=None, finished_at=None),
            SimpleNamespace(status="RUNNING", error=None, finished_at=None),
            SimpleNamespace(status="SUCCESS", error=None, finished_at=None),
        ]
        run = SimpleNamespace(
            status="RUNNING", error_message=None, finished_at=None, task_runs=task_runs
        )
        db = CommitRecorder()
        mark_run_dispatch_failed(db, run, RuntimeError("broker down"))
        self.assertEqual(run.status, "FAILED")
        self.assertEqual([item.status for item in task_runs], ["SKIPPED", "FAILED", "SUCCESS"])
        self.assertEqual(db.commits, 1)


if __name__ == "__main__":
    unittest.main()
