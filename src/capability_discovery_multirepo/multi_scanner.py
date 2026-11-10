"""Multi-repo capability scanner.

Scans multiple solver repositories for capability markers and evidence,
producing unified discovery records. Integrates with the existing
tools/capability_discovery marker scanner and src/capability_ledger_auto
evidence scanner.

Zero domain semantics: repo paths and patterns come from configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import DiscoveryConfig, RepoDescriptor


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return ""


def sha256_text(text: str) -> str:
    """Compute SHA-256 hash of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class DiscoveryRecord:
    """A single capability discovery from scanning.

    Attributes
    ----------
    capability_id : str
        Capability identifier (e.g., "multiphase", "lbm.collision").
    repo_name : str
        Source repository name.
    source_file : str
        Relative path to source file (posix style).
    source_sha256 : str
        SHA-256 of source file.
    lineno : int
        Line number of discovery.
    discovery_type : str
        Type: "marker" | "pattern" | "test" | "report".
    evidence_level : str
        Claimed evidence level (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED).
    params : dict
        Additional parameters (e.g., density_ratio, reynolds_number).
    anchors : list[str]
        Verification anchors (analytical solutions, references).
    tags : list[str]
        Additional tags.
    test_outcome : str
        Test outcome if applicable (passed/failed/skipped/not_run).
    """
    capability_id: str
    repo_name: str
    source_file: str
    source_sha256: str
    lineno: int = 0
    discovery_type: str = "pattern"
    evidence_level: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    anchors: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    test_outcome: str = "not_run"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "repo_name": self.repo_name,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "lineno": self.lineno,
            "discovery_type": self.discovery_type,
            "evidence_level": self.evidence_level,
            "params": dict(self.params),
            "anchors": list(self.anchors),
            "tags": list(self.tags),
            "test_outcome": self.test_outcome,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiscoveryRecord":
        return cls(
            capability_id=d["capability_id"],
            repo_name=d.get("repo_name", ""),
            source_file=d.get("source_file", ""),
            source_sha256=d.get("source_sha256", ""),
            lineno=int(d.get("lineno", 0)),
            discovery_type=d.get("discovery_type", "pattern"),
            evidence_level=d.get("evidence_level", ""),
            params=dict(d.get("params") or {}),
            anchors=list(d.get("anchors") or []),
            tags=list(d.get("tags") or []),
            test_outcome=d.get("test_outcome", "not_run"),
        )


@dataclass
class ScanDiagnostics:
    """Diagnostics from a scan run."""
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    files_scanned: int = 0
    repos_scanned: int = 0

    def add_error(self, repo: str, file: str, lineno: int, message: str) -> None:
        self.errors.append({
            "repo": repo, "source_file": file, "lineno": lineno,
            "message": message, "severity": "error"
        })

    def add_warning(self, repo: str, file: str, lineno: int, message: str) -> None:
        self.warnings.append({
            "repo": repo, "source_file": file, "lineno": lineno,
            "message": message, "severity": "warning"
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "files_scanned": self.files_scanned,
            "repos_scanned": self.repos_scanned,
        }


# Capability marker pattern: @pytest.mark.capability("id", ...)
# Handles multi-line and various kwarg formats
_CAPABILITY_MARKER_RE = re.compile(
    r'@pytest\.mark\.capability\(\s*["\']([^"\']+)["\']'
    r'(?:\s*,\s*(.*?))?\s*\)',
    re.MULTILINE | re.DOTALL,
)

# Evidence level in marker kwargs
_LEVEL_KWARG_RE = re.compile(r'(?:evidence_level|level)\s*=\s*["\']([^"\']+)["\']')

# List value pattern: key=["value1", "value2"] or key=["value"]
_LIST_KWARG_RE = re.compile(r'(\w+)\s*=\s*\[([^\]]*)\]')

# String values inside lists
_LIST_STRING_RE = re.compile(r'["\']([^"\']*)["\']')

# Generic keyword argument pattern (for simple values)
_KWARG_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\d+(?:\.\d+)?)|(\w+))')


class MultiScanner:
    """Scans multiple repositories for capability markers and evidence.

    The scanner performs two types of discovery:
    1. **Marker-based**: AST/text scanning for @pytest.mark.capability() decorators
    2. **Pattern-based**: Regex matching against capability registry patterns

    Parameters
    ----------
    config : DiscoveryConfig
        Discovery configuration with repo descriptors.
    workspace_root : Path | str
        Workspace root for resolving relative paths.
    """

    def __init__(self, config: DiscoveryConfig, workspace_root: Path | str = "."):
        self.config = config
        self.workspace_root = Path(workspace_root).resolve()
        self.diagnostics = ScanDiagnostics()
        self._capability_patterns: Dict[str, Dict[str, Any]] = {}
        self._load_central_registry()

    def _load_central_registry(self) -> None:
        """Load central capability registry for pattern matching."""
        if not self.config.central_registry:
            return
        reg_path = self.workspace_root / self.config.central_registry
        if not reg_path.exists():
            self.diagnostics.add_warning(
                "config", str(reg_path), 0,
                f"Central registry not found: {reg_path}"
            )
            return
        try:
            with open(reg_path, encoding="utf-8") as f:
                raw = json.load(f)
            for cap in raw.get("capabilities", []):
                cid = cap.get("capability_id", "")
                if cid:
                    self._capability_patterns[cid] = {
                        "code_patterns": cap.get("code_patterns", []),
                        "test_patterns": cap.get("test_patterns", []),
                        "report_patterns": cap.get("report_patterns", []),
                        "label": cap.get("label", cid),
                    }
        except (json.JSONDecodeError, OSError) as exc:
            self.diagnostics.add_error(
                "config", str(reg_path), 0, f"Failed to load registry: {exc}"
            )

    def scan_all(self) -> Tuple[List[DiscoveryRecord], ScanDiagnostics]:
        """Scan all enabled repositories.

        Returns
        -------
        records : list[DiscoveryRecord]
            All discovery records from all repos.
        diagnostics : ScanDiagnostics
            Scan diagnostics (errors, warnings, counts).
        """
        all_records: List[DiscoveryRecord] = []
        self.diagnostics = ScanDiagnostics()

        for repo in self.config.enabled_repos():
            repo_root = self._resolve_repo_root(repo)
            if repo_root is None or not repo_root.exists():
                self.diagnostics.add_warning(
                    repo.name, repo.root, 0,
                    f"Repository root not found: {repo.root}"
                )
                continue

            self.diagnostics.repos_scanned += 1
            records = self._scan_repo(repo, repo_root)
            all_records.extend(records)

        all_records.sort(key=lambda r: (r.repo_name, r.capability_id, r.source_file, r.lineno))
        return all_records, self.diagnostics

    def _resolve_repo_root(self, repo: RepoDescriptor) -> Optional[Path]:
        """Resolve repository root path."""
        root = Path(repo.root)
        if root.is_absolute():
            return root
        return self.workspace_root / root

    def _scan_repo(
        self, repo: RepoDescriptor, repo_root: Path
    ) -> List[DiscoveryRecord]:
        """Scan a single repository."""
        records: List[DiscoveryRecord] = []

        # 1. Scan test files for capability markers
        for glob_pattern in repo.test_globs:
            for test_file in sorted(repo_root.rglob(glob_pattern)):
                if self._should_skip(test_file, repo_root):
                    continue
                self.diagnostics.files_scanned += 1
                file_records = self._scan_file_markers(test_file, repo, repo_root)
                records.extend(file_records)

        # 2. Pattern-based scanning against source files
        if self._capability_patterns:
            for glob_pattern in repo.src_globs:
                for src_file in sorted(repo_root.rglob(glob_pattern)):
                    if self._should_skip(src_file, repo_root):
                        continue
                    self.diagnostics.files_scanned += 1
                    file_records = self._scan_file_patterns(src_file, repo, repo_root)
                    records.extend(file_records)

        return records

    def _should_skip(self, path: Path, repo_root: Path) -> bool:
        """Check if a file should be skipped."""
        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".mypy_cache", ".pytest_cache",
        }
        rel = path.relative_to(repo_root)
        for part in rel.parts:
            if part in skip_dirs:
                return True
        # Skip files > 500KB
        try:
            if path.stat().st_size > 500_000:
                return True
        except OSError:
            return True
        return False

    def _scan_file_markers(
        self, file_path: Path, repo: RepoDescriptor, repo_root: Path
    ) -> List[DiscoveryRecord]:
        """Scan a file for @pytest.mark.capability() markers."""
        records: List[DiscoveryRecord] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.diagnostics.add_error(
                repo.name, str(file_path.relative_to(repo_root)), 0,
                f"Cannot read file: {exc}"
            )
            return records

        file_sha = sha256_file(file_path)
        rel_path = str(file_path.relative_to(repo_root)).replace(os.sep, "/")

        for lineno, line in enumerate(content.splitlines(), start=1):
            match = _CAPABILITY_MARKER_RE.search(line)
            if not match:
                continue

            cap_id = match.group(1).strip()
            kwargs_str = match.group(2) or ""

            # Parse kwargs
            params: Dict[str, Any] = {}
            evidence_level = ""
            anchors: List[str] = []
            tags: List[str] = []

            level_match = _LEVEL_KWARG_RE.search(kwargs_str)
            if level_match:
                evidence_level = level_match.group(1).upper()

            # Extract list-valued kwargs (anchors, tags)
            for list_match in _LIST_KWARG_RE.finditer(kwargs_str):
                key = list_match.group(1)
                list_content = list_match.group(2)
                values = _LIST_STRING_RE.findall(list_content)
                if key == "anchors":
                    anchors.extend(values)
                elif key == "tags":
                    tags.extend(values)
                else:
                    params[key] = values

            # Extract simple kwargs
            for kw_match in _KWARG_RE.finditer(kwargs_str):
                key = kw_match.group(1)
                value = (
                    kw_match.group(2) or kw_match.group(3)
                    or kw_match.group(4) or kw_match.group(5) or ""
                )
                if key in ("evidence_level", "level", "anchors", "tags"):
                    continue
                # Try numeric conversion
                try:
                    params[key] = int(value)
                except (ValueError, TypeError):
                    try:
                        params[key] = float(value)
                    except (ValueError, TypeError):
                        params[key] = value

            records.append(DiscoveryRecord(
                capability_id=cap_id,
                repo_name=repo.name,
                source_file=rel_path,
                source_sha256=file_sha,
                lineno=lineno,
                discovery_type="marker",
                evidence_level=evidence_level,
                params=params,
                anchors=anchors,
                tags=tags,
            ))

        return records

    def _scan_file_patterns(
        self, file_path: Path, repo: RepoDescriptor, repo_root: Path
    ) -> List[DiscoveryRecord]:
        """Scan a file for capability patterns from the registry."""
        records: List[DiscoveryRecord] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return records

        file_sha = sha256_file(file_path)
        rel_path = str(file_path.relative_to(repo_root)).replace(os.sep, "/")
        is_test = any(
            file_path.name.startswith(p.replace("*", ""))
            for p in repo.test_globs
        )

        for cap_id, patterns in self._capability_patterns.items():
            search_patterns = (
                patterns.get("test_patterns", []) if is_test
                else patterns.get("code_patterns", [])
            )
            for pattern in search_patterns:
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    continue
                for lineno, line in enumerate(content.splitlines(), start=1):
                    if regex.search(line):
                        records.append(DiscoveryRecord(
                            capability_id=cap_id,
                            repo_name=repo.name,
                            source_file=rel_path,
                            source_sha256=file_sha,
                            lineno=lineno,
                            discovery_type="pattern",
                            evidence_level="",
                            params={},
                            anchors=[],
                            tags=[],
                        ))
                        break  # One match per pattern per file

        return records

    def scan_repo_by_name(self, repo_name: str) -> Tuple[List[DiscoveryRecord], ScanDiagnostics]:
        """Scan a single repository by name.

        Parameters
        ----------
        repo_name : str
            Name of the repository to scan.

        Returns
        -------
        records : list[DiscoveryRecord]
            Discovery records from the repo.
        diagnostics : ScanDiagnostics
            Scan diagnostics.
        """
        self.diagnostics = ScanDiagnostics()
        for repo in self.config.repos:
            if repo.name == repo_name:
                repo_root = self._resolve_repo_root(repo)
                if repo_root is None or not repo_root.exists():
                    self.diagnostics.add_warning(
                        repo.name, repo.root, 0,
                        f"Repository root not found: {repo.root}"
                    )
                    return [], self.diagnostics
                self.diagnostics.repos_scanned = 1
                records = self._scan_repo(repo, repo_root)
                records.sort(key=lambda r: (r.capability_id, r.source_file, r.lineno))
                return records, self.diagnostics
        self.diagnostics.add_error(
            "config", "", 0, f"Repository not found in config: {repo_name}"
        )
        return [], self.diagnostics
