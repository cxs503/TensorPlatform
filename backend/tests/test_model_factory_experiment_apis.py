"""Offline API contracts for the durable model-factory experiment modules."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from api import causal_api, gan_training, gnn_training, recsys_training
from api.experiment_http import experiment_call
from services.experiment_runtime import ExperimentValidationError


USER = {"id": 3, "role": "engineer", "organization_id": 17}
JOB = SimpleNamespace(id=9)


def serialized(config=None, *, status="PENDING", progress=25):
    return {
        "id": 9,
        "training_job_id": "experiment-test",
        "status": status,
        "metrics": {},
        "hyperparameters": {"config": config or {}},
        "progress": progress,
    }


class ModelFactoryExperimentApiTest(unittest.TestCase):
    def test_causal_requires_dataset_or_explicit_fixture(self):
        request = causal_api.CausalReq(
            treatment="X", outcome="Y", confounders=["Z"], fixture_mode=False
        )
        with self.assertRaises(HTTPException) as caught:
            causal_api.analyze_causal(request, db=object(), current_user=USER)
        self.assertEqual(caught.exception.status_code, 422)

    def test_causal_fixture_is_queued_without_dataset_and_keeps_progress(self):
        request = causal_api.CausalReq(
            treatment="X", outcome="Y", confounders=["Z"], fixture_mode=True
        )
        payload = serialized(
            {"fixture_mode": True, "treatment": "X", "outcome": "Y"}
        )
        with (
            patch.object(causal_api, "create_experiment", return_value=JOB) as create,
            patch.object(causal_api, "serialize_experiment", return_value=payload),
        ):
            result = causal_api.analyze_causal(request, db=object(), current_user=USER)
        self.assertIsNone(create.call_args.kwargs["dataset_id"])
        self.assertEqual(create.call_args.kwargs["organization_id"], 17)
        self.assertEqual(result["progress"], 25)

    def test_recsys_maps_ui_algorithm_to_real_matrix_factorization(self):
        request = recsys_training.RecSysTrainReq(
            algorithm="mf", factors=8, epochs=5, fixture_mode=True
        )
        with (
            patch.object(recsys_training, "create_experiment", return_value=JOB) as create,
            patch.object(
                recsys_training,
                "serialize_experiment",
                return_value=serialized({"fixture_mode": True}),
            ),
        ):
            recsys_training.train_recsys(request, db=object(), current_user=USER)
        config = create.call_args.kwargs["config"]
        self.assertEqual(config["method"], "matrix_factorization")
        self.assertEqual(create.call_args.kwargs["kind"], "recsys")

    def test_gnn_rejects_unimplemented_models_instead_of_simulating(self):
        request = gnn_training.GNNConfig(
            model="gat", task_type="node_classification", fixture_mode=True
        )
        with self.assertRaises(HTTPException) as caught:
            gnn_training.train_gnn(request, db=object(), current_user=USER)
        self.assertEqual(caught.exception.status_code, 422)

    def test_gan_rejects_unimplemented_variants_instead_of_simulating(self):
        request = gan_training.GANTrainReq(gan_type="stylegan", fixture_mode=True)
        with self.assertRaises(HTTPException) as caught:
            gan_training.train_gan(request, db=object(), current_user=USER)
        self.assertEqual(caught.exception.status_code, 422)

    def test_runtime_validation_errors_are_safe_422_responses(self):
        def fail():
            raise ExperimentValidationError("bounded validation failed")

        with self.assertRaises(HTTPException) as caught:
            experiment_call(fail)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail, "bounded validation failed")


if __name__ == "__main__":
    unittest.main()
