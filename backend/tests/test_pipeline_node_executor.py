import io
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import joblib
import pandas as pd
from sklearn.linear_model import LinearRegression


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.pipeline_node_executor import (  # noqa: E402
    AuthorizedDataset,
    AuthorizedModel,
    PersistedDataset,
    PipelineNodeExecutionError,
    PipelineNodeExecutor,
    VerifiedResourceAccess,
)


class MemorySink:
    def __init__(self):
        self.calls = []

    def persist(self, **kwargs):
        self.calls.append(kwargs)
        frame = kwargs["frame"]
        return PersistedDataset(
            dataset_id=900 + len(self.calls),
            row_count=len(frame),
            size_bytes=len(frame.to_csv(index=False).encode()),
            stage=kwargs["stage"],
        )


class PipelineNodeExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv_path = self.root / "7_data.csv"
        pd.DataFrame(
            {
                "x": list(range(20)),
                "z": [value * 2 for value in range(20)],
                "target": [value * 3 + 1 for value in range(20)],
            }
        ).to_csv(self.csv_path, index=False)
        self.dataset = AuthorizedDataset(
            dataset_id=7,
            organization_id=3,
            file_path=str(self.csv_path),
            name="bounded",
            row_count=20,
            size_bytes=self.csv_path.stat().st_size,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def access(self, *, model=None):
        models = {model.model_id: model} if model else {}
        return VerifiedResourceAccess(3, datasets={7: self.dataset}, models=models)

    def executor(self, *, model=None, sink=None, **kwargs):
        return PipelineNodeExecutor(
            self.access(model=model),
            derived_dataset_sink=sink,
            dataset_roots=[self.root],
            model_roots=[self.root],
            **kwargs,
        )

    def test_data_load_reads_real_csv_but_returns_metadata_only(self):
        result = self.executor().execute("data_load", {"dataset_id": 7})
        self.assertEqual(result["row_count"], 20)
        self.assertEqual(result["column_count"], 3)
        self.assertTrue(result["loaded"])
        self.assertNotIn("data", result)
        self.assertNotIn("records", result)

    def test_resource_capability_rejects_missing_and_cross_org_access(self):
        with self.assertRaisesRegex(PipelineNodeExecutionError, "未经调用方"):
            self.executor().execute("data_load", {"dataset_id": 8})
        other = AuthorizedDataset(8, 4, str(self.csv_path))
        with self.assertRaisesRegex(PipelineNodeExecutionError, "组织不一致"):
            VerifiedResourceAccess(3, datasets={8: other})

    def test_csv_must_be_inside_controlled_root(self):
        access = VerifiedResourceAccess(3, datasets={7: self.dataset})
        executor = PipelineNodeExecutor(access, dataset_roots=[self.root / "other"])
        with self.assertRaisesRegex(PipelineNodeExecutionError, "受控 CSV 根目录"):
            executor.execute("data_load", {"dataset_id": 7})

    def test_actual_rows_are_bounded_even_when_metadata_is_wrong(self):
        executor = self.executor(max_rows=10)
        with self.assertRaisesRegex(PipelineNodeExecutionError, "行上限"):
            executor.execute("data_load", {"dataset_id": 7})

    def test_data_process_govern_persists_derived_identifier_only(self):
        frame = pd.DataFrame(
            {"a": [1, None, 3], "empty": [None, None, None], "b": [2, None, 4]}
        )
        frame.to_csv(self.csv_path, index=False)
        sink = MemorySink()
        result = self.executor(sink=sink).execute(
            "data_process", {"dataset_id": 7, "operation": "govern"}
        )
        self.assertEqual(result["dataset_id"], 901)
        self.assertEqual(result["source_dataset_id"], 7)
        self.assertEqual(result["removed_columns"], 1)
        self.assertNotIn("data", result)
        self.assertEqual(sink.calls[0]["source"].organization_id, 3)

    def test_data_process_rejects_unsupported_operation_without_write(self):
        sink = MemorySink()
        with self.assertRaisesRegex(PipelineNodeExecutionError, "仅支持 govern"):
            self.executor(sink=sink).execute(
                "data_process", {"dataset_id": 7, "operation": "shell"}
            )
        self.assertEqual(sink.calls, [])

    def test_feature_engineering_runs_existing_safe_transform_service(self):
        sink = MemorySink()
        result = self.executor(sink=sink).execute(
            "feature_engineering",
            {
                "dataset_id": 7,
                "transformations": [
                    {"type": "expression", "name": "sum", "expression": "{x}+{z}"}
                ],
            },
        )
        self.assertEqual(result["dataset_id"], 901)
        self.assertEqual(result["new_columns"], ["sum"])
        self.assertIn("sum", sink.calls[0]["frame"].columns)
        self.assertNotIn("frame", result)

    def test_feature_engineering_requires_sink_before_any_untracked_output(self):
        with self.assertRaisesRegex(PipelineNodeExecutionError, "持久化 sink"):
            self.executor().execute(
                "feature_engineering",
                {
                    "dataset_id": 7,
                    "transformations": [
                        {"type": "rolling", "name": "avg", "source_column": "x", "agg": "mean"}
                    ],
                },
            )

    def test_model_evaluation_returns_aggregate_metrics_not_predictions(self):
        train = pd.read_csv(self.csv_path)
        estimator = LinearRegression().fit(train[["x", "z"]], train["target"])
        buffer = io.BytesIO()
        joblib.dump(estimator, buffer)
        model = AuthorizedModel(
            model_id=12,
            organization_id=3,
            model_blob=buffer.getvalue(),
            model_type="regression",
            artifact_source="pipeline_internal",
            artifact_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
        )
        result = self.executor(model=model).execute(
            "model_evaluate",
            {
                "dataset_id": 7,
                "model_id": 12,
                "target_column": "target",
                "feature_columns": ["x", "z"],
                "test_size": 0.2,
            },
        )
        self.assertEqual(result["model_id"], 12)
        self.assertEqual(result["sample_count"], 20)
        self.assertEqual(result["test_scope"], "full_dataset")
        self.assertAlmostEqual(result["metrics"]["R2"], 1.0)
        self.assertNotIn("predictions", result)

    def test_evaluation_rejects_cross_validation_and_deploy_fails_closed(self):
        model = AuthorizedModel(model_id=12, organization_id=3, model_blob=b"not-loaded")
        with self.assertRaisesRegex(PipelineNodeExecutionError, "cross_validation"):
            self.executor(model=model).execute(
                "model_evaluate",
                {"dataset_id": 7, "model_id": 12, "cross_validation": True},
            )
        with self.assertRaisesRegex(PipelineNodeExecutionError, "未开放"):
            self.executor(model=model).execute("model_deploy", {"model_id": 12})


if __name__ == "__main__":
    unittest.main()
