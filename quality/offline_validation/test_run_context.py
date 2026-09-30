import concurrent.futures
import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quality.offline_validation import RunRegistry, ValidationError


class RunRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("ODF_VALIDATION_ALLOWED_ROOTS")
        os.environ["ODF_VALIDATION_ALLOWED_ROOTS"] = self.temp.name
        self.registry = RunRegistry(self.temp.name)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("ODF_VALIDATION_ALLOWED_ROOTS", None)
        else:
            os.environ["ODF_VALIDATION_ALLOWED_ROOTS"] = self.previous
        Path(self.temp.name).chmod(0o700)
        self.temp.cleanup()

    def _asset(self, run_id: str, name: str = "asset.jsonl", content: bytes = b"{}\n") -> Path:
        path = Path(self.temp.name, run_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o600)
        return path

    def test_lifecycle_computes_checksum_and_cleanup(self):
        run = self.registry.create("sintering_temperature", ttl_hours=2)
        path = self._asset(run["run_id"], content=b'{"temperature": 800}\n')
        asset = self.registry.register_asset(
            run["run_id"], kind="jsonl", uri=f"scratch://{run['run_id']}/{path.name}"
        )
        self.assertEqual(asset["checksum"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(asset["registry_owned"])
        finished = self.registry.finish(run["run_id"], "passed", summary={"rows": 1})
        self.assertEqual(finished["status"], "passed")
        self.assertTrue(self.registry.cleanup(run["run_id"]))

    def test_production_environment_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.registry.create("sintering", environment="production")

    def test_external_root_and_unsafe_permissions_are_rejected(self):
        with self.assertRaises(ValidationError):
            RunRegistry("/var/lib/foundation-production")
        Path(self.temp.name).chmod(0o777)
        with self.assertRaises(ValidationError):
            RunRegistry(self.temp.name)

    def test_running_cleanup_requires_force(self):
        run = self.registry.create("photovoltaic")
        with self.assertRaises(ValidationError):
            self.registry.cleanup(run["run_id"])
        self.assertTrue(self.registry.cleanup(run["run_id"], force=True))

    def test_owned_asset_must_exist_and_cannot_escape(self):
        run = self.registry.create("load_forecast")
        outside = Path(self.temp.name, "outside.csv")
        outside.write_text("production-like", encoding="utf-8")
        for uri in ("missing.csv", "../outside.csv", str(outside), "file://" + str(outside)):
            with self.subTest(uri=uri), self.assertRaises(ValidationError):
                self.registry.register_asset(run["run_id"], kind="csv", uri=uri)

    def test_owned_asset_symlink_is_rejected(self):
        run = self.registry.create("symlink")
        target = self._asset(run["run_id"], name="target.csv")
        link = target.with_name("link.csv")
        link.symlink_to(target.name)
        with self.assertRaisesRegex(ValidationError, "symlink"):
            self.registry.register_asset(run["run_id"], kind="csv", uri=str(link))

    def test_checksum_tamper_is_rejected(self):
        run = self.registry.create("fault_prediction")
        path = self._asset(run["run_id"], content=b"original")
        with self.assertRaisesRegex(ValidationError, "checksum"):
            self.registry.register_asset(
                run["run_id"], kind="binary", uri=str(path), checksum="0" * 64
            )
        self.assertEqual(self.registry.load(run["run_id"])["assets"], [])

    def test_production_uri_is_metadata_only_and_never_deleted(self):
        run = self.registry.create("metadata_reference")
        uri = "postgresql://production.example/energy"
        with self.assertRaises(ValidationError):
            self.registry.register_asset(run["run_id"], kind="database", uri=uri)
        asset = self.registry.register_asset(
            run["run_id"], kind="database", uri=uri, cleanup="read-only"
        )
        self.assertFalse(asset["registry_owned"])
        self.assertEqual(asset["cleanup"], "read-only")
        external = Path(self.temp.name, "external-object")
        external.write_text("must survive registry cleanup", encoding="utf-8")
        self.registry.register_asset(
            run["run_id"], kind="file-reference", uri=external.as_uri(),
            cleanup="metadata-only",
        )
        self.registry.finish(run["run_id"], "cancelled")
        self.assertTrue(self.registry.cleanup(run["run_id"]))
        self.assertTrue(external.is_file())

    def test_run_local_file_cannot_be_disguised_as_external(self):
        run = self.registry.create("ownership")
        local = self._asset(run["run_id"])
        for uri in (local.as_uri(), f"scratch://{run['run_id']}/{local.name}"):
            with self.subTest(uri=uri), self.assertRaises(ValidationError):
                self.registry.register_asset(
                    run["run_id"], kind="csv", uri=uri, cleanup="metadata-only"
                )

    def test_concurrent_manifest_updates_are_not_lost(self):
        run = self.registry.create("concurrent")
        path = self._asset(run["run_id"], content=b"shared")

        def register(index: int):
            return self.registry.register_asset(
                run["run_id"], kind=f"item-{index}", uri=str(path)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(register, range(32)))
        manifest = self.registry.load(run["run_id"])
        self.assertEqual(len(manifest["assets"]), 32)
        self.assertEqual({a["kind"] for a in manifest["assets"]}, {f"item-{i}" for i in range(32)})

    def test_heartbeat_and_safe_abandoned_janitor(self):
        start = datetime.now(timezone.utc)
        run = self.registry.create("lease", ttl_hours=1, lease_seconds=30)
        heartbeat = self.registry.heartbeat(run["run_id"], lease_seconds=60, now=start)
        self.assertGreater(heartbeat["lease_expires_at"], heartbeat["last_heartbeat_at"])

        first = self.registry.janitor(now=start + timedelta(seconds=61))
        self.assertEqual(first["abandoned"], [run["run_id"]])
        self.assertEqual(first["cleaned"], [])
        self.assertEqual(self.registry.load(run["run_id"])["status"], "abandoned")

        after_ttl = datetime.fromisoformat(run["expires_at"].replace("Z", "+00:00")) + timedelta(seconds=1)
        second = self.registry.janitor(now=after_ttl)
        self.assertEqual(second["cleaned"], [run["run_id"]])
        self.assertFalse(Path(self.temp.name, run["run_id"]).exists())


if __name__ == "__main__":
    unittest.main()
