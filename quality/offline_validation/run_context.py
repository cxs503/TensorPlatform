"""Fail-closed registry for synthetic/replay validation assets.

Only files inside a registry-owned run directory can be registered as owned.
External URIs are metadata references and are never touched by cleanup.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse


class ValidationError(ValueError):
    """Raised when a validation run would cross an isolation boundary."""


RUN_ID_PATTERN = re.compile(r"^val-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
ALLOWED_ENVIRONMENTS = {"test", "staging"}
ALLOWED_ASSET_TYPES = {"fixture", "synthetic", "replay", "anonymized"}
TERMINAL_STATUSES = {"passed", "failed", "cancelled"}
EXTERNAL_POLICIES = {"metadata-only", "read-only"}
MAX_TTL_HOURS = 24 * 14
DEFAULT_LEASE_SECONDS = 15 * 60
MIN_LEASE_SECONDS = 30
MAX_LEASE_SECONDS = 24 * 60 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _allowed_roots() -> list[Path]:
    configured = os.getenv(
        "ODF_VALIDATION_ALLOWED_ROOTS",
        "/tmp,/srv/tensor/operator-b/foundation-validation-runs",
    )
    return [Path(item).expanduser().resolve() for item in configured.split(",") if item]


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunRegistry:
    def __init__(self, root: str | Path = "/tmp/foundation-offline-validation"):
        requested = Path(root).expanduser()
        if requested.is_symlink():
            raise ValidationError("validation root cannot be a symlink")
        self.root = requested.resolve()
        if not any(_is_below(self.root, allowed) for allowed in _allowed_roots()):
            raise ValidationError(
                f"validation root must be below an approved scratch root: {self.root}"
            )
        created = not self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            self.root.chmod(0o700)
        self._assert_owned_directory(self.root, "validation root")
        self._lock_path = self.root / ".registry.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
        except OSError as exc:
            raise ValidationError(f"cannot safely open registry lock: {exc}") from exc
        os.close(descriptor)
        self._assert_owned_file(self._lock_path, "registry lock")

    @staticmethod
    def new_run_id(now: datetime | None = None) -> str:
        current = (now or _utcnow()).astimezone(timezone.utc)
        return f"val-{current.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _validate_lease(lease_seconds: int) -> None:
        if lease_seconds < MIN_LEASE_SECONDS or lease_seconds > MAX_LEASE_SECONDS:
            raise ValidationError(
                f"lease_seconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
            )

    def _assert_owned_directory(self, path: Path, label: str) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValidationError(f"cannot inspect {label}: {exc}") from exc
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
            raise ValidationError(f"{label} must be a real directory")
        if info.st_uid != os.geteuid():
            raise ValidationError(f"{label} must be owned by uid {os.geteuid()}")
        if info.st_mode & 0o022:
            raise ValidationError(f"{label} must not be group/world writable")

    def _assert_owned_file(
        self, path: Path, label: str, *, require_private: bool = True
    ) -> None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise ValidationError(f"{label} must be a real file")
        if info.st_uid != os.geteuid():
            raise ValidationError(f"{label} must be owned by uid {os.geteuid()}")
        if require_private and info.st_mode & 0o022:
            raise ValidationError(f"{label} must not be group/world writable")

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        with self._lock_path.open("r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _run_dir(self, run_id: str, *, require_owned: bool = False) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValidationError(f"invalid validation run id: {run_id}")
        candidate = self.root / run_id
        if candidate.is_symlink():
            raise ValidationError("validation run directory cannot be a symlink")
        path = candidate.resolve()
        if not _is_below(path, self.root):
            raise ValidationError("run path escapes validation root")
        if require_owned:
            self._assert_owned_directory(path, "validation run directory")
        return path

    def _manifest_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def create(
        self,
        scenario_id: str,
        *,
        scenario_version: str = "1.0.0",
        environment: str = "test",
        asset_type: str = "synthetic",
        ttl_hours: int = 24,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ValidationError("offline validation cannot target production")
        if asset_type not in ALLOWED_ASSET_TYPES:
            raise ValidationError(f"unsupported validation asset type: {asset_type}")
        if not scenario_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", scenario_id):
            raise ValidationError("scenario_id must be a non-empty portable identifier")
        if ttl_hours < 1 or ttl_hours > MAX_TTL_HOURS:
            raise ValidationError(f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
        self._validate_lease(lease_seconds)
        try:
            json.dumps(metadata or {}, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"metadata must be JSON serializable: {exc}") from exc

        now = _utcnow()
        with self._lock(exclusive=True):
            run_id = self.new_run_id(now)
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(mode=0o700)
            self._assert_owned_directory(run_dir, "validation run directory")
            manifest = {
                "schema_version": "1.1",
                "run_id": run_id,
                "scenario_id": scenario_id,
                "scenario_version": scenario_version,
                "environment": environment,
                "asset_type": asset_type,
                "status": "running",
                "created_at": _iso(now),
                "expires_at": _iso(now + timedelta(hours=ttl_hours)),
                "last_heartbeat_at": _iso(now),
                "lease_expires_at": _iso(now + timedelta(seconds=lease_seconds)),
                "finished_at": None,
                "assets": [],
                "metadata": metadata or {},
            }
            self._write_unlocked(manifest)
            return manifest

    def _load_unlocked(self, run_id: str) -> dict[str, Any]:
        self._run_dir(run_id, require_owned=True)
        path = self._manifest_path(run_id)
        if not path.is_file() or path.is_symlink():
            raise ValidationError(f"validation run not found: {run_id}")
        self._assert_owned_file(path, "run manifest")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"invalid run manifest for {run_id}: {exc}") from exc

    def load(self, run_id: str) -> dict[str, Any]:
        with self._lock(exclusive=False):
            return self._load_unlocked(run_id)

    def _owned_asset_path(self, run_id: str, uri: str) -> Path:
        run_dir = self._run_dir(run_id, require_owned=True)
        parsed = urlparse(uri)
        if parsed.scheme == "scratch":
            if parsed.query or parsed.fragment or parsed.params:
                raise ValidationError("scratch URI cannot contain query or fragment")
            relative = unquote("/".join(part for part in (parsed.netloc, parsed.path.lstrip("/")) if part))
            prefix = f"{run_id}/"
            if relative.startswith(prefix):
                relative = relative[len(prefix):]
            candidate = run_dir / relative
        elif parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise ValidationError("file URI host is not supported")
            candidate = Path(unquote(parsed.path))
        elif parsed.scheme:
            raise ValidationError("registry-owned assets require a run-local file or scratch URI")
        else:
            raw = Path(uri).expanduser()
            candidate = raw if raw.is_absolute() else run_dir / raw
        if candidate.is_symlink():
            raise ValidationError("registry-owned asset cannot be a symlink")
        resolved = candidate.resolve()
        if not _is_below(resolved, run_dir):
            raise ValidationError("registry-owned asset escapes its run directory")
        if not resolved.is_file() or resolved.is_symlink():
            raise ValidationError("registry-owned asset must be an existing regular file")
        self._assert_owned_file(
            resolved, "registry-owned asset", require_private=False
        )
        return resolved

    def register_asset(
        self,
        run_id: str,
        *,
        kind: str,
        uri: str,
        checksum: str | None = None,
        cleanup: str = "registry-owned",
    ) -> dict[str, Any]:
        if not kind or not uri:
            raise ValidationError("asset kind and uri are required")
        with self._lock(exclusive=True):
            manifest = self._load_unlocked(run_id)
            if manifest["status"] != "running":
                raise ValidationError("assets can only be registered on a running validation")
            if cleanup == "registry-owned":
                path = self._owned_asset_path(run_id, uri)
                actual_checksum = _sha256(path)
                if checksum and checksum.lower() != actual_checksum:
                    raise ValidationError("asset checksum does not match file content")
                recorded_checksum = actual_checksum
                owned = True
            elif cleanup in EXTERNAL_POLICIES:
                parsed = urlparse(uri)
                if not parsed.scheme:
                    raise ValidationError("external metadata references require an absolute URI")
                if parsed.scheme == "scratch":
                    raise ValidationError("scratch assets must use registry-owned cleanup")
                if parsed.scheme == "file":
                    referenced = Path(unquote(parsed.path)).resolve()
                    if _is_below(referenced, self._run_dir(run_id, require_owned=True)):
                        raise ValidationError("run-local assets must use registry-owned cleanup")
                if checksum and not SHA256_PATTERN.fullmatch(checksum):
                    raise ValidationError("checksum must be a SHA-256 hexadecimal digest")
                recorded_checksum = checksum.lower() if checksum else None
                owned = False
            else:
                raise ValidationError("cleanup must be registry-owned, metadata-only, or read-only")
            asset = {
                "kind": kind,
                "uri": uri,
                "checksum": recorded_checksum,
                "cleanup": cleanup,
                "registry_owned": owned,
                "registered_at": _iso(_utcnow()),
            }
            manifest["assets"].append(asset)
            self._write_unlocked(manifest)
            return asset

    def heartbeat(
        self, run_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._validate_lease(lease_seconds)
        current = (now or _utcnow()).astimezone(timezone.utc)
        with self._lock(exclusive=True):
            manifest = self._load_unlocked(run_id)
            if manifest["status"] != "running":
                raise ValidationError("only running validations accept heartbeats")
            manifest["last_heartbeat_at"] = _iso(current)
            manifest["lease_expires_at"] = _iso(current + timedelta(seconds=lease_seconds))
            self._write_unlocked(manifest)
            return manifest

    def finish(self, run_id: str, status: str, *, summary: dict[str, Any] | None = None):
        if status not in TERMINAL_STATUSES:
            raise ValidationError(f"invalid terminal status: {status}")
        with self._lock(exclusive=True):
            manifest = self._load_unlocked(run_id)
            if manifest["status"] != "running":
                raise ValidationError("validation run is already terminal")
            manifest["status"] = status
            manifest["finished_at"] = _iso(_utcnow())
            manifest["summary"] = summary or {}
            self._write_unlocked(manifest)
            return manifest

    def cleanup(self, run_id: str, *, force: bool = False) -> bool:
        with self._lock(exclusive=True):
            manifest = self._load_unlocked(run_id)
            if manifest["status"] == "running" and not force:
                raise ValidationError("refusing to clean a running validation without --force")
            run_dir = self._run_dir(run_id, require_owned=True)
            shutil.rmtree(run_dir)
            return not run_dir.exists()

    def janitor(self, *, now: datetime | None = None) -> dict[str, list[str]]:
        """Abandon expired leases, then remove only expired registry run directories."""
        current = (now or _utcnow()).astimezone(timezone.utc)
        abandoned: list[str] = []
        cleaned: list[str] = []
        with self._lock(exclusive=True):
            for manifest_path in sorted(self.root.glob("val-*/run.json")):
                try:
                    run_id = manifest_path.parent.name
                    manifest = self._load_unlocked(run_id)
                    if manifest.get("status") == "running" and _parse(manifest["lease_expires_at"]) <= current:
                        manifest["status"] = "abandoned"
                        manifest["finished_at"] = _iso(current)
                        manifest["summary"] = {"reason": "lease_expired"}
                        self._write_unlocked(manifest)
                        abandoned.append(run_id)
                    if manifest.get("status") != "running" and _parse(manifest["expires_at"]) <= current:
                        run_dir = self._run_dir(run_id, require_owned=True)
                        shutil.rmtree(run_dir)
                        cleaned.append(run_id)
                except (OSError, KeyError, ValueError, ValidationError):
                    continue
        return {"abandoned": abandoned, "cleaned": cleaned}

    def cleanup_expired(self, *, now: datetime | None = None) -> list[str]:
        """Compatibility wrapper; use :meth:`janitor` for abandonment details."""
        return self.janitor(now=now)["cleaned"]

    def list(self) -> list[dict[str, Any]]:
        result = []
        with self._lock(exclusive=False):
            for path in sorted(self.root.glob("val-*/run.json")):
                try:
                    result.append(self._load_unlocked(path.parent.name))
                except (OSError, ValidationError):
                    continue
        return result

    def _write_unlocked(self, manifest: dict[str, Any]) -> None:
        path = self._manifest_path(manifest["run_id"])
        descriptor, temporary = tempfile.mkstemp(prefix=".run-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
