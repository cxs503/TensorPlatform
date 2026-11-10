"""Configuration for multi-repo capability discovery.

Defines the structure for describing solver repositories and
discovery parameters. Zero domain semantics: repo names and
paths are injected via configuration, never hardcoded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RepoDescriptor:
    """Describes a solver repository to scan.

    Attributes
    ----------
    name : str
        Repository name (e.g., "TensorLBM").
    root : str
        Path to repository root (absolute or relative to workspace).
    test_globs : list[str]
        Glob patterns for test files (default: ["test_*.py", "*_test.py"]).
    src_globs : list[str]
        Glob patterns for source files (default: ["*.py"]).
    capability_registry : str | None
        Path to repo-local capability registry JSON (optional).
    enabled : bool
        Whether this repo is included in scans.
    """
    name: str
    root: str
    test_globs: List[str] = field(default_factory=lambda: ["test_*.py", "*_test.py"])
    src_globs: List[str] = field(default_factory=lambda: ["*.py"])
    capability_registry: Optional[str] = None
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "root": self.root,
            "test_globs": list(self.test_globs),
            "src_globs": list(self.src_globs),
            "capability_registry": self.capability_registry,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RepoDescriptor":
        return cls(
            name=d["name"],
            root=d["root"],
            test_globs=d.get("test_globs", ["test_*.py", "*_test.py"]),
            src_globs=d.get("src_globs", ["*.py"]),
            capability_registry=d.get("capability_registry"),
            enabled=d.get("enabled", True),
        )


@dataclass
class DiscoveryConfig:
    """Configuration for a multi-repo discovery run.

    Attributes
    ----------
    repos : list[RepoDescriptor]
        Repositories to scan.
    central_registry : str | None
        Path to central capability registry JSON (capabilities.json).
    ledger_path : str | None
        Path to capability ledger JSON for auto-registration.
    policy_path : str | None
        Path to grading policy JSON.
    output_dir : str
        Directory for reports and artifacts.
    include_runtime : bool
        Whether to include runtime (pytest plugin) results.
    min_confidence : float
        Minimum confidence threshold for auto-registration (0.0-1.0).
        Default 0.0 means all discovered capabilities are registered.
    """
    repos: List[RepoDescriptor] = field(default_factory=list)
    central_registry: Optional[str] = None
    ledger_path: Optional[str] = None
    policy_path: Optional[str] = None
    output_dir: str = "artifacts/capability_discovery_multirepo"
    include_runtime: bool = False
    min_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repos": [r.to_dict() for r in self.repos],
            "central_registry": self.central_registry,
            "ledger_path": self.ledger_path,
            "policy_path": self.policy_path,
            "output_dir": self.output_dir,
            "include_runtime": self.include_runtime,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiscoveryConfig":
        return cls(
            repos=[RepoDescriptor.from_dict(r) for r in d.get("repos", [])],
            central_registry=d.get("central_registry"),
            ledger_path=d.get("ledger_path"),
            policy_path=d.get("policy_path"),
            output_dir=d.get("output_dir", "artifacts/capability_discovery_multirepo"),
            include_runtime=d.get("include_runtime", False),
            min_confidence=d.get("min_confidence", 0.0),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "DiscoveryConfig":
        """Load configuration from JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return cls.from_dict(raw)

    def save_json(self, path: str | Path) -> None:
        """Save configuration to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def enabled_repos(self) -> List[RepoDescriptor]:
        """Return only enabled repositories."""
        return [r for r in self.repos if r.enabled]
