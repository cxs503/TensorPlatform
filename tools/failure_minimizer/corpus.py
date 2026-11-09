"""
Corpus export from mission snapshots.

Exports frozen snapshots of failed/blocked missions for delta debugging.
Each mission becomes a manifest with goal, checks, changed_files, log tail,
commands, and environment whitelist.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator


@dataclass
class MissionSnapshot:
    """Frozen snapshot of a mission for reproduction."""
    mission_id: int
    goal: str
    status: str
    result: dict[str, Any]
    checks: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    env_whitelist: dict[str, str] = field(default_factory=dict)
    log_tail: list[str] = field(default_factory=list)
    created_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    def compute_sha256(self) -> str:
        """Compute SHA256 of the snapshot for integrity."""
        content = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class CorpusExporter:
    """Export mission snapshots to corpus directory."""

    def __init__(self, corpus_dir: str | Path = "corpus"):
        self.corpus_dir = Path(corpus_dir)
        self.corpus_dir.mkdir(parents=True, exist_ok=True)

    def export_mission(self, snapshot: MissionSnapshot) -> Path:
        """
        Export a single mission snapshot to corpus.

        Creates:
            corpus/{mission_id}/manifest.json
            corpus/{mission_id}/sha256.txt

        Returns:
            Path to the manifest file
        """
        mission_dir = self.corpus_dir / str(snapshot.mission_id)
        mission_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = mission_dir / "manifest.json"
        sha256_path = mission_dir / "sha256.txt"

        # Write manifest
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(snapshot.to_dict(), f, indent=2, ensure_ascii=False)

        # Write SHA256
        sha256 = snapshot.compute_sha256()
        with open(sha256_path, "w", encoding="utf-8") as f:
            f.write(sha256)

        return manifest_path

    def export_from_dict(self, mission_data: dict[str, Any]) -> Path:
        """
        Export a mission from a dictionary.

        Expected structure:
        {
            "id": int,
            "goal": str,
            "status": str,
            "result": dict,
            "checks": list[str],
            "changed_files": list[str],
            "commands": list[str],
            "env_whitelist": dict,
            "log_tail": list[str],
            "created_at": str,
            "finished_at": str
        }
        """
        # Extract fields from result if not at top level
        result = mission_data.get("result", {}) or {}
        
        snapshot = MissionSnapshot(
            mission_id=mission_data["id"],
            goal=mission_data.get("goal", ""),
            status=mission_data.get("status", "unknown"),
            result=result,
            checks=mission_data.get("checks") or result.get("checks", []),
            changed_files=mission_data.get("changed_files") or result.get("changed_files", []),
            commands=mission_data.get("commands") or result.get("commands", []),
            env_whitelist=mission_data.get("env_whitelist") or result.get("env_whitelist", {}),
            log_tail=mission_data.get("log_tail") or result.get("log_tail", []),
            created_at=mission_data.get("created_at"),
            finished_at=mission_data.get("finished_at"),
        )
        return self.export_mission(snapshot)

    def list_missions(self) -> list[int]:
        """List all mission IDs in the corpus."""
        missions = []
        for entry in self.corpus_dir.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                missions.append(int(entry.name))
        return sorted(missions)

    def load_mission(self, mission_id: int) -> MissionSnapshot:
        """Load a mission snapshot from corpus."""
        mission_dir = self.corpus_dir / str(mission_id)
        manifest_path = mission_dir / "manifest.json"

        if not manifest_path.exists():
            raise FileNotFoundError(f"Mission {mission_id} not found in corpus")

        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return MissionSnapshot(**data)

    def verify_integrity(self, mission_id: int) -> bool:
        """Verify SHA256 integrity of a mission snapshot."""
        mission_dir = self.corpus_dir / str(mission_id)
        manifest_path = mission_dir / "manifest.json"
        sha256_path = mission_dir / "sha256.txt"

        if not (manifest_path.exists() and sha256_path.exists()):
            return False

        snapshot = self.load_mission(mission_id)
        computed = snapshot.compute_sha256()

        with open(sha256_path, "r", encoding="utf-8") as f:
            stored = f.read().strip()

        return computed == stored


def extract_snapshot_from_mission(mission: dict[str, Any]) -> MissionSnapshot:
    """
    Extract a snapshot from a mission dictionary (e.g., from API).

    This is a helper for converting mission data to snapshot format.
    """
    result = mission.get("result", {}) or {}

    # Extract checks from result
    checks = mission.get("checks") or result.get("checks", [])
    if isinstance(checks, str):
        checks = [checks]

    # Extract changed files
    changed_files = mission.get("changed_files") or result.get("changed_files", [])

    # Extract commands (if available)
    commands = mission.get("commands") or result.get("commands", [])

    # Extract environment whitelist
    env_whitelist = mission.get("env_whitelist") or result.get("env_whitelist", {})

    # Extract log tail
    log_tail = mission.get("log_tail") or result.get("log_tail", [])
    if isinstance(log_tail, str):
        log_tail = log_tail.split("\n")[-50:]  # Last 50 lines

    return MissionSnapshot(
        mission_id=mission["id"],
        goal=mission.get("goal", ""),
        status=mission.get("status", "unknown"),
        result=result,
        checks=checks,
        changed_files=changed_files,
        commands=commands,
        env_whitelist=env_whitelist,
        log_tail=log_tail,
        created_at=mission.get("created_at"),
        finished_at=mission.get("finished_at"),
    )
