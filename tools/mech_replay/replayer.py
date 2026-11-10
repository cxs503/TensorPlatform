"""Replay engine — loads golden fixtures and replays mechanisms, checking consistency.

Core guarantees:
  - C1: Golden consistency (0 diff on clean replay)
  - C3: Idempotency (same fixture twice → identical sha256)
  - C4: Pure function (zero DB/network — enforced by import monitoring)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .mechanisms import get_mechanism, ALL_MECHANISMS


class ReplayResult:
    """Result of replaying a single fixture."""
    def __init__(self, fixture_id: str, mechanism: str,
                 golden_sha: str, replay_sha: str,
                 output_match: bool, diff_detail: Optional[str] = None):
        self.fixture_id = fixture_id
        self.mechanism = mechanism
        self.golden_sha = golden_sha
        self.replay_sha = replay_sha
        self.output_match = output_match
        self.diff_detail = diff_detail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "mechanism": self.mechanism,
            "golden_sha": self.golden_sha,
            "replay_sha": self.replay_sha,
            "output_match": self.output_match,
            "diff_detail": self.diff_detail,
        }


def replay_fixture(fixture: Dict[str, Any]) -> ReplayResult:
    """Replay a single fixture through its mechanism. Returns ReplayResult."""
    mech_name = fixture["mechanism"]
    mech = get_mechanism(mech_name)
    inp = fixture["input"]
    golden_sha = fixture["expected_sha256"]

    output = mech["invoke"](inp)
    replay_sha = mech["output_sha256"](output)

    match = (golden_sha == replay_sha)
    diff = None
    if not match:
        diff = f"sha256 mismatch: golden={golden_sha[:16]}... replay={replay_sha[:16]}..."

    return ReplayResult(
        fixture_id=fixture["fixture_id"],
        mechanism=mech_name,
        golden_sha=golden_sha,
        replay_sha=replay_sha,
        output_match=match,
        diff_detail=diff,
    )


def replay_idempotent(fixture: Dict[str, Any]) -> Tuple[bool, str, str]:
    """C3: Run the same fixture twice, check sha256 identical.
    Returns (is_idempotent, sha_run1, sha_run2)."""
    mech = get_mechanism(fixture["mechanism"])
    inp = fixture["input"]
    out1 = mech["invoke"](inp)
    out2 = mech["invoke"](inp)
    sha1 = mech["output_sha256"](out1)
    sha2 = mech["output_sha256"](out2)
    return (sha1 == sha2, sha1, sha2)


def replay_directory(fixtures_dir: Path,
                     mechanism_name: Optional[str] = None) -> List[ReplayResult]:
    """Replay all fixtures in a directory. Optionally filter by mechanism."""
    results = []
    for fpath in sorted(fixtures_dir.glob("*.json")):
        if "manifest" in fpath.name:
            continue
        fixture = json.loads(fpath.read_text(encoding="utf-8"))
        if mechanism_name and fixture.get("mechanism") != mechanism_name:
            continue
        results.append(replay_fixture(fixture))
    return results


def verify_manifest(fixtures_dir: Path, mechanism_name: str) -> Dict[str, Any]:
    """Verify fixture files against their sha256 manifest."""
    manifest_path = fixtures_dir / f"{mechanism_name}_manifest.json"
    if not manifest_path.exists():
        return {"valid": False, "error": "manifest not found"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = []
    for fname, expected_sha in manifest.items():
        fpath = fixtures_dir / fname
        if not fpath.exists():
            mismatches.append({"file": fname, "issue": "missing"})
            continue
        actual_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            mismatches.append({"file": fname, "issue": "sha256_mismatch",
                               "expected": expected_sha[:16],
                               "actual": actual_sha[:16]})
    return {
        "valid": len(mismatches) == 0,
        "total_files": len(manifest),
        "mismatches": mismatches,
    }


class PurityMonitor:
    """C4: Monitor that replay makes zero DB/network calls.

    Wraps socket and psycopg2 to count invocations during replay.
    """
    def __init__(self):
        self.socket_count = 0
        self.psycopg_count = 0
        self._orig_socket = None
        self._orig_psycopg = None

    def __enter__(self):
        import socket
        self._orig_socket_create = socket.socket.__init__
        monitor = self

        class CountingSocket(socket.socket):
            def __init__(self, *args, **kwargs):
                monitor.socket_count += 1
                super().__init__(*args, **kwargs)

        socket.socket = CountingSocket

        # Monitor psycopg2 if available
        try:
            import psycopg2
            self._orig_connect = psycopg2.connect
            def counting_connect(*args, **kwargs):
                monitor.psycopg_count += 1
                return self._orig_connect(*args, **kwargs)
            psycopg2.connect = counting_connect
        except ImportError:
            pass
        return self

    def __exit__(self, *args):
        import socket
        if self._orig_socket_create:
            socket.socket.__init__ = self._orig_socket_create
        try:
            import psycopg2
            if self._orig_connect:
                psycopg2.connect = self._orig_connect
        except ImportError:
            pass

    @property
    def is_pure(self) -> bool:
        return self.socket_count == 0 and self.psycopg_count == 0

    def report(self) -> Dict[str, int]:
        return {"socket_calls": self.socket_count,
                "psycopg_calls": self.psycopg_count}
