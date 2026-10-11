import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_compose_topology.py"
spec = importlib.util.spec_from_file_location("compose_topology_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


def compose_service(name="kp-backend"):
    return {
        "container_name": name,
        "image": "open-foundation-backend:2.2.0",
        "environment": {"DATABASE_URL": "secret-compose-value", "LOG_LEVEL": "WARNING"},
        "networks": {"foundation-network": None},
        "volumes": [
            {"type": "bind", "source": "/release/runtime.py", "target": "/app/runtime.py", "read_only": True},
        ],
    }


def inspected(name="kp-backend", env=None, mounts=None):
    return {
        "Id": "a" * 64,
        "Name": "/" + name,
        "Config": {
            "Image": "open-foundation-backend:2.2.0",
            "Env": env or ["DATABASE_URL=secret-runtime-value", "LOG_LEVEL=WARNING", "PATH=/usr/bin"],
            "Labels": {
                "com.docker.compose.project": "foundation",
                "com.docker.compose.service": "backend",
            },
        },
        "NetworkSettings": {"Networks": {"foundation-network": {}}},
        "Mounts": mounts or [{"Type": "bind", "Destination": "/app/runtime.py", "RW": False}],
    }


class ComposeTopologyAuditTests(unittest.TestCase):
    def test_matching_topology_ignores_image_env_but_never_emits_values(self):
        compose = {"name": "foundation", "services": {"backend": compose_service()}}
        report = audit.build_report(compose, [inspected()], ("backend",))
        self.assertEqual(report["verdict"], "match")
        environment = report["services"][0]["comparison"]["environment"]
        self.assertEqual(environment["runtime_only_keys"], ["PATH"])
        rendered = json.dumps(report)
        self.assertNotIn("secret-compose-value", rendered)
        self.assertNotIn("secret-runtime-value", rendered)

    def test_name_mount_and_missing_environment_keys_are_drift(self):
        compose = {"name": "foundation", "services": {"backend": compose_service("open-foundation-backend")}}
        container = inspected(
            "kp-backend",
            env=["DATABASE_URL=hidden"],
            mounts=[{"Type": "bind", "Destination": "/app/api", "RW": True}],
        )
        report = audit.build_report(compose, [container], ("backend",))
        service = report["services"][0]
        self.assertEqual(report["verdict"], "drift")
        self.assertEqual(service["status"], "drift")
        self.assertEqual(
            service["drift"],
            ["container_name", "mounts", "environment_keys"],
        )
        self.assertEqual(
            service["comparison"]["environment"]["missing_keys"],
            ["LOG_LEVEL"],
        )

    def test_same_service_in_another_project_is_not_adopted(self):
        compose = {"name": "expected", "services": {"backend": compose_service()}}
        container = inspected()
        container["Config"]["Labels"]["com.docker.compose.project"] = "other"
        report = audit.build_report(compose, [container], ("backend",))
        self.assertEqual(report["services"][0]["status"], "running_container_missing")

    def test_duplicate_project_service_labels_fail_closed(self):
        compose = {"name": "foundation", "services": {"backend": compose_service()}}
        second = inspected("kp-backend-copy")
        report = audit.build_report(compose, [inspected(), second], ("backend",))
        self.assertEqual(report["services"][0]["status"], "ambiguous_running_containers")

    def test_deployment_gate_mounts_revision_and_diagnoses_label_drift(self):
        gate = (ROOT / "scripts" / "check_experiment_runtime_deployment.sh").read_text(encoding="utf-8")
        self.assertIn('revision_mounts+=( -v "$source_absolute/$path:/app/$path:ro" )', gate)
        self.assertIn("import api.causal_api,api.recsys_training", gate)
        self.assertIn("api.metrics_api,services.experiment_audit", gate)
        self.assertIn("assert 'experiment.execute_job' in celery_app.tasks", gate)
        self.assertIn("com.docker.compose.project=$project_name", gate)
        self.assertIn("topology ownership/config drift detected (fail-closed)", gate)
        inspect_created = gate.index("inspect_json=$(mktemp)")
        cleanup_installed = gate.index(
            "trap 'rm -f \"$config_json\" \"$inspect_json\" \"$topology_report\"' EXIT"
        )
        topology_audit = gate.index("python3 scripts/audit_compose_topology.py")
        self.assertLess(inspect_created, cleanup_installed)
        self.assertLess(cleanup_installed, topology_audit)


if __name__ == "__main__":
    unittest.main()
