import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_consistency_gate", ROOT / "scripts" / "release_consistency_gate.py")
gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gate)
IMAGE = "registry.example/foundation/backend@sha256:" + "a" * 64
FRONTEND_IMAGE = "registry.example/foundation/frontend@sha256:" + "b" * 64


def service(volumes=None, image=IMAGE, revision="r-123", build=None):
    result = {"image": image, "environment": {"ODF_RELEASE_REVISION": revision}, "volumes": volumes or []}
    if build is not None:
        result["build"] = build
    return result


def compose(**changes):
    services = {name: service() for name in gate.REQUIRED_SERVICES}
    services["frontend"] = service(image=FRONTEND_IMAGE)
    services.update(changes)
    return {"services": services}


class ReleaseConsistencyGateTests(unittest.TestCase):
    def test_accepts_uniform_immutable_release_with_recorded_migration(self):
        self.assertEqual([], gate.validate(compose(), {"applied": [gate.REQUIRED_MIGRATION]}))

    def test_rejects_tag_build_revision_and_pipeline_migration_gaps(self):
        config = compose(backend=service(image="open-foundation-backend:2.2.0", build={"context": "."}), **{"celery-worker": service(revision="other")})
        violations = gate.validate(config, {"applied": []})
        self.assertIn("backend: image is not an immutable sha256 digest", violations)
        self.assertIn("backend: build is present; release must use a prebuilt image", violations)
        self.assertIn("celery-worker: release revision differs from backend", violations)
        self.assertIn("required migration is pending: 016_pipeline_tenant_isolation.sql", violations)

    def test_rejects_mutable_frontend_and_requires_the_same_revision(self):
        config = compose(frontend=service(image="open-foundation-frontend:2.2.0", revision="other", build={"context": "."}))
        violations = gate.validate(config, {"applied": [gate.REQUIRED_MIGRATION]})
        self.assertIn("frontend: image is not an immutable sha256 digest", violations)
        self.assertIn("frontend: build is present; release must use a prebuilt image", violations)
        self.assertIn("frontend: release revision differs from backend", violations)

    def test_rejects_api_or_task_bind_mounts_even_when_read_only(self):
        config = compose(backend=service(volumes=[{"type": "bind", "target": "/app/api/ml_pipeline.py", "read_only": True}]))
        self.assertIn("backend: bind mount masks executable application path /app/api/ml_pipeline.py", gate.validate(config, {"applied": [gate.REQUIRED_MIGRATION]}))

    def test_runtime_inspect_rejects_masking_mount_and_missing_worker(self):
        inspect = [{"Config": {"Image": IMAGE, "Labels": {"com.docker.compose.service": "backend"}}, "Mounts": [{"Type": "bind", "Destination": "/app/tasks/pipeline_tasks.py"}]}]
        violations = gate.validate(compose(), {"applied": [gate.REQUIRED_MIGRATION]}, inspect)
        self.assertIn("backend: runtime bind mount masks executable application path /app/tasks/pipeline_tasks.py", violations)
        self.assertIn("celery-worker: expected exactly one running container", violations)

    def test_release_overlay_declares_the_same_required_image_and_revision(self):
        overlay = (ROOT / "docker-compose.release-consistency.yml").read_text(encoding="utf-8")
        self.assertEqual(3, overlay.count("image: ${ODF_RELEASE_IMAGE"))
        self.assertEqual(1, overlay.count("image: ${ODF_FRONTEND_RELEASE_IMAGE"))
        self.assertEqual(4, overlay.count("\n      ODF_RELEASE_REVISION:"))
        self.assertEqual(4, overlay.count("build: !reset null"))
        self.assertEqual(2, overlay.count("volumes: !reset"))
        self.assertNotIn("/app/tasks", overlay)
        self.assertNotIn("/app/api", overlay)


if __name__ == "__main__":
    unittest.main()
