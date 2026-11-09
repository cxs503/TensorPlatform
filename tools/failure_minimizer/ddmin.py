"""
Delta debugging (ddmin) for failure minimization.

Implements generalized delta debugging on candidate element sets:
- changed_files
- checks
- command fragments
- environment keys

The algorithm finds a 1-minimal subset that still reproduces the failure.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .signature import FailureSignature, signature_from_result
from .corpus import MissionSnapshot


@dataclass
class ReductionResult:
    """Result of delta debugging reduction."""
    original_elements: list[str]
    minimal_elements: list[str]
    reduction_log: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    duration_seconds: float = 0.0
    original_signature: FailureSignature | None = None
    final_signature: FailureSignature | None = None

    @property
    def reduction_rate(self) -> float:
        """Compute reduction rate (0.0 to 1.0)."""
        if not self.original_elements:
            return 0.0
        removed = len(self.original_elements) - len(self.minimal_elements)
        return removed / len(self.original_elements)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "original_elements": self.original_elements,
            "minimal_elements": self.minimal_elements,
            "reduction_log": self.reduction_log,
            "iterations": self.iterations,
            "duration_seconds": self.duration_seconds,
            "reduction_rate": self.reduction_rate,
            "original_signature": self.original_signature.to_dict() if self.original_signature else None,
            "final_signature": self.final_signature.to_dict() if self.final_signature else None,
        }


class SandboxExecutor:
    """
    Execute reproduction scripts in an isolated sandbox.

    The sandbox is a temporary directory with controlled environment.
    """

    def __init__(self, timeout_seconds: int = 600):
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        snapshot: MissionSnapshot,
        elements: list[str],
    ) -> dict[str, Any]:
        """
        Execute a reproduction with a subset of elements.

        Args:
            snapshot: Mission snapshot to reproduce
            elements: Subset of elements to include

        Returns:
            Result dictionary with exit_code, fail_stage, exception, etc.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Build reproduction script
            script = self._build_script(snapshot, elements, tmppath)
            script_path = tmppath / "repro.sh"
            script_path.write_text(script)
            script_path.chmod(0o755)

            # Build environment
            env = self._build_env(snapshot, elements)

            # Execute
            try:
                result = subprocess.run(
                    [str(script_path)],
                    cwd=str(tmppath),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )

                return {
                    "exit_code": result.returncode,
                    "fail_stage": self._detect_fail_stage(result, elements),
                    "exception": self._extract_exception(result),
                    "missing_artifacts": self._detect_missing_artifacts(tmppath, elements),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            except subprocess.TimeoutExpired:
                return {
                    "exit_code": -1,
                    "fail_stage": "timeout",
                    "exception": "Execution timed out",
                    "missing_artifacts": [],
                    "stdout": "",
                    "stderr": "Timeout",
                }
            except Exception as e:
                return {
                    "exit_code": -1,
                    "fail_stage": "execution_error",
                    "exception": str(e),
                    "missing_artifacts": [],
                    "stdout": "",
                    "stderr": str(e),
                }

    def _build_script(
        self,
        snapshot: MissionSnapshot,
        elements: list[str],
        workdir: Path,
    ) -> str:
        """Build reproduction shell script."""
        lines = ["#!/bin/bash", "set -e", ""]

        # Add environment variables
        for key, value in snapshot.env_whitelist.items():
            if key in elements or not any(e.startswith("env:") for e in elements):
                lines.append(f"export {key}={value}")

        lines.append("")

        # Add file creation for changed_files
        for elem in elements:
            if elem.startswith("file:"):
                filepath = elem[5:]
                filedir = workdir / Path(filepath).parent
                filedir.mkdir(parents=True, exist_ok=True)
                # Create placeholder file
                (workdir / filepath).write_text(f"# Placeholder for {filepath}\n")
                lines.append(f"# Created: {filepath}")

        lines.append("")

        # Add checks
        for elem in elements:
            if elem.startswith("check:"):
                check_cmd = elem[6:]
                lines.append(f"# Check: {check_cmd}")
                lines.append(check_cmd)

        lines.append("")

        # Add commands
        for cmd in snapshot.commands:
            if cmd in elements or not any(e.startswith("cmd:") for e in elements):
                lines.append(cmd)

        return "\n".join(lines)

    def _build_env(
        self,
        snapshot: MissionSnapshot,
        elements: list[str],
    ) -> dict[str, str]:
        """Build environment variables for execution."""
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}

        # Add whitelisted env vars
        for key, value in snapshot.env_whitelist.items():
            env_key = f"env:{key}"
            if env_key in elements or not any(e.startswith("env:") for e in elements):
                env[key] = value

        return env

    def _detect_fail_stage(
        self,
        result: subprocess.CompletedProcess,
        elements: list[str],
    ) -> str:
        """Detect which stage failed."""
        if result.returncode == 0:
            return "success"

        stderr = result.stderr.lower()
        if "check" in stderr or "assert" in stderr:
            return "check"
        if "command" in stderr or "exec" in stderr:
            return "command"
        if "file" in stderr or "not found" in stderr:
            return "file"

        return "unknown"

    def _extract_exception(self, result: subprocess.CompletedProcess) -> str | None:
        """Extract first exception from output."""
        output = result.stderr or result.stdout
        if not output:
            return None

        # Look for common exception patterns
        lines = output.split("\n")
        for line in lines:
            if "error" in line.lower() or "exception" in line.lower():
                return line.strip()

        return None

    def _detect_missing_artifacts(
        self,
        workdir: Path,
        elements: list[str],
    ) -> list[str]:
        """Detect missing expected artifacts."""
        missing = []
        for elem in elements:
            if elem.startswith("artifact:"):
                artifact_path = workdir / elem[9:]
                if not artifact_path.exists():
                    missing.append(elem[9:])
        return missing


class DeltaDebugger:
    """
    Delta debugging algorithm for failure minimization.

    Implements the ddmin algorithm to find a 1-minimal subset of elements
    that still reproduces the failure.
    """

    def __init__(
        self,
        executor: SandboxExecutor | None = None,
        max_iterations: int = 100,
    ):
        self.executor = executor or SandboxExecutor()
        self.max_iterations = max_iterations

    def minimize(
        self,
        snapshot: MissionSnapshot,
        elements: list[str] | None = None,
    ) -> ReductionResult:
        """
        Find 1-minimal subset of elements that reproduces failure.

        Args:
            snapshot: Mission snapshot to minimize
            elements: Candidate elements (if None, extracted from snapshot)

        Returns:
            ReductionResult with minimal elements
        """
        start_time = time.time()

        # Extract candidate elements if not provided
        if elements is None:
            elements = self._extract_elements(snapshot)

        if not elements:
            return ReductionResult(
                original_elements=[],
                minimal_elements=[],
                duration_seconds=time.time() - start_time,
            )

        # Compute original signature
        original_result = self.executor.execute(snapshot, elements)
        original_sig = signature_from_result(original_result)

        # Verify original reproduces failure
        if original_result["exit_code"] == 0:
            # Not a failure, return as-is
            return ReductionResult(
                original_elements=elements,
                minimal_elements=elements,
                original_signature=original_sig,
                final_signature=original_sig,
                duration_seconds=time.time() - start_time,
            )

        # Run ddmin algorithm
        minimal = self._ddmin(snapshot, elements, original_sig)

        # Compute final signature
        final_result = self.executor.execute(snapshot, minimal)
        final_sig = signature_from_result(final_result)

        return ReductionResult(
            original_elements=elements,
            minimal_elements=minimal,
            iterations=self.max_iterations,
            duration_seconds=time.time() - start_time,
            original_signature=original_sig,
            final_signature=final_sig,
        )

    def _extract_elements(self, snapshot: MissionSnapshot) -> list[str]:
        """Extract candidate elements from snapshot."""
        elements = []

        # Changed files
        for f in snapshot.changed_files:
            elements.append(f"file:{f}")

        # Checks
        for c in snapshot.checks:
            elements.append(f"check:{c}")

        # Commands
        for cmd in snapshot.commands:
            elements.append(f"cmd:{cmd}")

        # Environment keys
        for key in snapshot.env_whitelist.keys():
            elements.append(f"env:{key}")

        return elements

    def _ddmin(
        self,
        snapshot: MissionSnapshot,
        elements: list[str],
        target_sig: FailureSignature,
    ) -> list[str]:
        """
        Core ddmin algorithm.

        Finds 1-minimal subset by iteratively removing elements.
        """
        current = list(elements)
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            reduced = False

            # Try removing each element
            for i in range(len(current)):
                candidate = current[:i] + current[i+1:]

                if not candidate:
                    continue

                # Test candidate
                result = self.executor.execute(snapshot, candidate)
                sig = signature_from_result(result)

                # Check if signature matches
                if sig.matches(target_sig):
                    current = candidate
                    reduced = True
                    break

            # If no reduction possible, we're done
            if not reduced:
                break

        return current

    def verify_1minimality(
        self,
        snapshot: MissionSnapshot,
        minimal_elements: list[str],
        target_sig: FailureSignature,
    ) -> bool:
        """
        Verify that the minimal set is 1-minimal.

        For each element, removing it should flip the oracle.
        """
        for i in range(len(minimal_elements)):
            candidate = minimal_elements[:i] + minimal_elements[i+1:]

            if not candidate:
                # Empty set, should not reproduce
                result = self.executor.execute(snapshot, [])
                sig = signature_from_result(result)
                if sig.matches(target_sig):
                    return False  # Empty set still reproduces
                continue

            result = self.executor.execute(snapshot, candidate)
            sig = signature_from_result(result)

            if sig.matches(target_sig):
                return False  # Removing element still reproduces

        return True
