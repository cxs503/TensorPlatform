"""Strict, side-effect-free checks for Pipeline execution and export contracts."""

import inspect
import hashlib
import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from types import SimpleNamespace

from api.ml_pipeline import (
    ExportReq,
    ExecuteNodeReq,
    WORKFLOW_PACKAGE_FILES,
    _normalise_validation_context,
    build_workflow_package,
    execute_node,
    export_pipeline,
    merge_dependency_outputs,
    pipeline_run_history_item,
    pipeline_access_allowed,
    propagate_identity_outputs,
    validate_dag,
)


class PipelineContractTest(unittest.TestCase):
    def test_complete_workflow_package_is_portable_and_verifiable(self):
        payload = build_workflow_package(
            {
                "id": 16,
                "name": "温度预测流水线",
                "description": "完整链路",
                "trigger_type": "manual",
                "tasks": [
                    {
                        "id": 101,
                        "pipeline_id": 16,
                        "name": "load",
                        "task_type": "data_load",
                        "config": {"dataset_id": 134},
                        "dependencies": [],
                        "timeout": 1800,
                        "retry": 0,
                    },
                    {
                        "id": 102,
                        "pipeline_id": 16,
                        "name": "train",
                        "task_type": "model_train",
                        "config": {"model_type": "random_forest"},
                        "dependencies": ["load"],
                        "timeout": 1800,
                        "retry": 0,
                    },
                ],
            }
        )

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(set(archive.namelist()), WORKFLOW_PACKAGE_FILES)
            definition = json.loads(archive.read("pipeline.json"))
            self.assertEqual(definition["tasks"][1]["dependencies"], ["load"])
            self.assertNotIn("id", definition["tasks"][0])
            compile(archive.read("pipeline_sdk.py"), "pipeline_sdk.py", "exec")
            compile(archive.read("pipeline_api.py"), "pipeline_api.py", "exec")
            compile(archive.read("run_pipeline.py"), "run_pipeline.py", "exec")
            sdk = archive.read("pipeline_sdk.py").decode("utf-8")
            self.assertIn("def import_pipeline", sdk)
            self.assertIn("def wait", sdk)
            self.assertIn("/pipelines/runs/", sdk)
            manifest = json.loads(archive.read("manifest.json"))
            for name, metadata in manifest["files"].items():
                content = archive.read(name)
                self.assertEqual(metadata["size"], len(content))
                self.assertEqual(metadata["sha256"], hashlib.sha256(content).hexdigest())

    def test_history_item_keeps_pipeline_name_node_outputs_and_metrics(self):
        task = SimpleNamespace(
            id=2,
            to_dict=lambda: {
                "id": 2,
                "task_type": "model_evaluate",
                "status": "SUCCESS",
                "outputs": {"model_id": 341, "metrics": {"R2": 0.85}},
            },
        )
        run = SimpleNamespace(
            id=16,
            pipeline_id=8,
            pipeline=SimpleNamespace(name="温度预测流水线"),
            task_runs=[task],
            to_dict=lambda: {"id": 16, "pipeline_id": 8, "status": "SUCCESS"},
        )

        item = pipeline_run_history_item(run)

        self.assertEqual(item["pipeline_name"], "温度预测流水线")
        self.assertEqual(item["task_runs"][0]["outputs"]["model_id"], 341)
        self.assertEqual(item["task_runs"][0]["outputs"]["metrics"]["R2"], 0.85)

    def test_missing_execution_evidence_fails_closed(self):
        result = execute_node(
            ExecuteNodeReq(node_id="load", task_type="data_load", config={}),
            db=None,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("dataset_id", result["error"])

        unknown = execute_node(
            ExecuteNodeReq(node_id="mystery", task_type="unknown", config={}),
            db=None,
        )
        self.assertEqual(unknown["status"], "error")
        self.assertIn("未实现", unknown["error"])

    def test_api_export_requires_runtime_bearer_token(self):
        result = export_pipeline(
            ExportReq(
                format="api",
                pipeline={
                    "id": 7,
                    "name": "sintering-pipeline",
                    "tasks": [{"name": "load", "task_type": "data_load"}],
                },
            )
        )
        self.assertEqual(result["format"], "api")
        self.assertIn("Bearer token required", result["code"])
        self.assertNotIn("CHANGE_ME_ADMIN", result["code"])
        compile(result["code"], "pipeline_api.py", "exec")

    def test_sdk_identifiers_are_valid_and_tokenized(self):
        result = export_pipeline(
            ExportReq(
                format="sdk",
                pipeline={
                    "name": "生产过程-01",
                    "tasks": [{"name": "数据-加载", "task_type": "data_load"}],
                },
            )
        )
        self.assertIn("token is required", result["code"])
        self.assertIn("def step", result["code"])
        compile(result["code"], "pipeline_sdk.py", "exec")

    def test_sdk_keywords_duplicates_and_injection_compile(self):
        result = export_pipeline(
            ExportReq(
                format="sdk",
                pipeline={
                    "name": "class",
                    "tasks": [
                        {"name": "def", "task_type": 'x"\\nraise RuntimeError()'},
                        {"name": "def", "task_type": "data_load"},
                        {"name": '"""', "task_type": "data_load"},
                    ],
                },
            )
        )
        compile(result["code"], "pipeline_sdk_adversarial.py", "exec")
        self.assertIn("def def_step", result["code"])
        self.assertIn("def def_step_2", result["code"])

    def test_export_schema_size_boundary(self):
        with self.assertRaises(HTTPException) as raised:
            export_pipeline(
                ExportReq(
                    format="json",
                    pipeline={"name": "oversized", "description": "x" * (257 * 1024)},
                )
            )
        self.assertEqual(raised.exception.status_code, 413)

    def test_execute_node_does_not_wait_or_copy_full_training_payload(self):
        source = inspect.getsource(execute_node)
        self.assertNotIn("sleep(", source)
        self.assertNotIn(".tolist()", source)

    def test_direct_real_node_uses_verified_tenant_capability_and_success_wrapper(self):
        dataset = SimpleNamespace(
            id=7,
            organization_id=3,
            file_path="/app/uploads/datasets/7_data.csv",
            name="source",
            row_count=20,
            size_bytes=200,
        )
        captured = {}

        def fake_execute(task_type, config, **kwargs):
            captured.update(task_type=task_type, config=config, **kwargs)
            return {"dataset_id": 7, "row_count": 20, "loaded": True}

        with patch("api.ml_pipeline._dataset", return_value=dataset), patch(
            "services.pipeline_node_executor.execute_pipeline_node",
            side_effect=fake_execute,
        ):
            result = execute_node(
                ExecuteNodeReq(
                    node_id="load",
                    task_type="data_load",
                    config={"dataset_id": 7},
                ),
                db=SimpleNamespace(),
                current_user={"id": 8, "organization_id": 3, "role": "ENGINEER"},
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["outputs"]["row_count"], 20)
        self.assertEqual(captured["task_type"], "data_load")
        self.assertEqual(captured["access"].organization_id, 3)

    def test_real_nodes_are_implemented_but_deploy_remains_closed(self):
        from api.ml_pipeline import IMPLEMENTED_TASK_TYPES

        self.assertTrue(
            {"data_load", "data_process", "feature_engineering", "model_evaluate"}
            <= IMPLEMENTED_TASK_TYPES
        )
        self.assertNotIn("model_deploy", IMPLEMENTED_TASK_TYPES)

    def test_celery_training_preserves_validation_context(self):
        source_path = Path(
            os.getenv(
                "PIPELINE_TRAINING_TASK_SOURCE",
                Path(__file__).resolve().parents[1] / "tasks" / "training_tasks.py",
            )
        )
        source = source_path.read_text(encoding="utf-8")
        self.assertIn('"validation_context": config.get("validation_context") or {}', source)
        self.assertIn('"validation_run_id": config.get("validation_run_id")', source)

    def test_dockerfile_is_non_root(self):
        result = export_pipeline(ExportReq(format="dockerfile", pipeline={}))
        self.assertIn("USER 65532:65532", result["code"])

    def test_unsupported_format_is_http_400(self):
        with self.assertRaises(HTTPException) as raised:
            export_pipeline(ExportReq(format="binary", pipeline={}))
        self.assertEqual(raised.exception.status_code, 400)

    def test_dag_fails_closed_for_unknown_dependencies_and_duplicates(self):
        self.assertFalse(validate_dag([{"name": "a", "dependencies": ["missing"]}]))
        self.assertFalse(
            validate_dag(
                [
                    {"name": "a", "dependencies": []},
                    {"name": "a", "dependencies": []},
                ]
            )
        )

    def test_dependency_outputs_propagate_identity_by_dependency_name(self):
        effective = merge_dependency_outputs(
            {"model_type": "random_forest"},
            {
                "load-node": {
                    "dataset_id": 42,
                    "samples": 1000,
                }
            },
        )
        self.assertEqual(effective["dataset_id"], 42)
        self.assertEqual(
            effective["_upstream_outputs"]["load-node"]["samples"],
            1000,
        )
        train_outputs = propagate_identity_outputs(
            effective,
            {"job_id": "train_abcd", "model_id": 9},
        )
        self.assertEqual(train_outputs["dataset_id"], 42)
        downstream = merge_dependency_outputs({}, {"train-node": train_outputs})
        self.assertEqual(downstream["dataset_id"], 42)
        self.assertEqual(downstream["model_id"], 9)

    def test_dependency_identity_conflicts_fail_closed(self):
        with self.assertRaises(ValueError):
            merge_dependency_outputs(
                {"dataset_id": 1},
                {"load-node": {"dataset_id": 2}},
            )
        with self.assertRaises(ValueError):
            merge_dependency_outputs(
                {},
                {
                    "left": {"model_id": 7},
                    "right": {"model_id": 8},
                },
            )
        with self.assertRaises(ValueError):
            propagate_identity_outputs(
                {"dataset_id": 1},
                {"dataset_id": 2},
            )

    def test_pipeline_tenant_and_owner_scope(self):
        pipeline = SimpleNamespace(organization_id=10, owner_id=2)
        owner = {"id": 2, "role": "ENGINEER", "organization_id": 10}
        colleague = {"id": 3, "role": "ENGINEER", "organization_id": 10}
        foreign = {"id": 4, "role": "ENGINEER", "organization_id": 11}
        admin = {"id": 1, "role": "ADMIN", "organization_id": 99}
        self.assertTrue(pipeline_access_allowed(pipeline, owner, write=True))
        self.assertTrue(pipeline_access_allowed(pipeline, colleague))
        self.assertFalse(pipeline_access_allowed(pipeline, colleague, write=True))
        self.assertFalse(pipeline_access_allowed(pipeline, foreign))
        self.assertTrue(pipeline_access_allowed(pipeline, admin, write=True))

    def test_validation_context_rejects_production(self):
        with self.assertRaises(HTTPException):
            _normalise_validation_context({"environment": "production"})


if __name__ == "__main__":
    unittest.main()
