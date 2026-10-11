import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "docker-compose.experiment-runtime.yml"


class ExperimentDeploymentOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))

    def test_overlay_never_builds_an_image(self):
        for service in self.config["services"].values():
            self.assertNotIn("build", service)

    def test_artifacts_use_one_persistent_volume_and_initializer(self):
        volume = self.config["volumes"]["experiment_artifacts"]
        self.assertEqual(
            volume["name"],
            "${EXPERIMENT_ARTIFACT_VOLUME:-kp-experiment-artifacts}",
        )
        initializer = self.config["services"]["experiment-artifact-init"]
        self.assertEqual(initializer["user"], "0:0")
        self.assertIn("0750", initializer["command"][-1])
        for name in ("backend", "celery-worker"):
            service = self.config["services"][name]
            self.assertIn(
                "EXPERIMENT_ARTIFACT_ROOT=/app/uploads/experiments",
                service["environment"],
            )
            self.assertIn(
                "experiment_artifacts:/app/uploads/experiments",
                service["volumes"],
            )

    def test_revision_and_host_packages_are_read_only(self):
        for name in ("backend", "celery-worker"):
            volumes = self.config["services"][name]["volumes"]
            runtime = [item for item in volumes if "/app/services/experiment_runtime.py" in item]
            audit = [item for item in volumes if "/app/services/experiment_audit.py" in item]
            celery_app = [item for item in volumes if "/app/tasks/celery_app.py" in item]
            task = [item for item in volumes if "/app/tasks/experiment_tasks.py" in item]
            metrics = [item for item in volumes if "/app/utils/experiment_metrics.py" in item]
            host_packages = [item for item in volumes if "/opt/foundation-host-site-packages" in item]
            pth = [item for item in volumes if "foundation_host_runtime.pth" in item]
            self.assertEqual(len(runtime), 1)
            self.assertEqual(len(audit), 1)
            self.assertEqual(len(celery_app), 1)
            self.assertEqual(len(task), 1)
            self.assertEqual(len(metrics), 1)
            self.assertEqual(len(host_packages), 1)
            self.assertEqual(len(pth), 1)
            self.assertTrue(runtime[0].endswith(":ro"))
            self.assertTrue(audit[0].endswith(":ro"))
            self.assertTrue(celery_app[0].endswith(":ro"))
            self.assertTrue(task[0].endswith(":ro"))
            self.assertTrue(metrics[0].endswith(":ro"))
            self.assertTrue(host_packages[0].endswith(":ro"))
            self.assertTrue(pth[0].endswith(":ro"))

    def test_worker_explicitly_registers_experiment_task(self):
        command = self.config["services"]["celery-worker"]["command"]
        self.assertIn("--include=tasks.experiment_tasks", command)

    def test_backend_mounts_persisted_metrics_endpoint(self):
        volumes = self.config["services"]["backend"]["volumes"]
        metrics_api = [item for item in volumes if "/app/api/metrics_api.py" in item]
        self.assertEqual(len(metrics_api), 1)
        self.assertTrue(metrics_api[0].endswith(":ro"))

    def test_runbook_requires_no_build_and_preserves_artifacts(self):
        runbook = (
            ROOT / "docs/operations/model-factory-experiment-runtime-rollout.md"
        ).read_text(encoding="utf-8")
        self.assertIn("--no-build", runbook)
        self.assertIn("不执行 `docker compose down -v`", runbook)


if __name__ == "__main__":
    unittest.main()
