"""Experiment Runner — dispatch experiments to compute resources.

Supports:
  - Local execution (subprocess)
  - Cluster dispatch via run_cluster semantics (simulated for testing)
  - Timeout handling
  - Structured result capture

Zero domain semantics: the runner knows nothing about what the experiment computes.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class RunResult:
    """Result of an experiment run."""
    output: Any
    exit_code: int | None
    timed_out: bool
    duration_sec: float
    error: str | None = None
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_sec": self.duration_sec,
            "error": self.error,
            "stdout": self.stdout[-4000:] if self.stdout else "",
            "stderr": self.stderr[-2000:] if self.stderr else "",
            "has_output": self.output is not None,
        }


class ExperimentRunner:
    """Runs experiments with timeout and error handling."""

    def __init__(self, timeout_sec: int = 3600, work_dir: str | None = None):
        self.timeout_sec = timeout_sec
        self.work_dir = work_dir

    def run(self, experiment_spec: dict[str, Any]) -> RunResult:
        """Execute an experiment based on its spec.

        The spec must contain a ``command`` field (list of strings) or a
        ``python_code`` field (string to execute).

        For testing/simulation: if ``simulate_output`` is present in the spec,
        returns that directly without executing anything.
        """
        # Simulation mode (for testing)
        if "simulate_output" in experiment_spec:
            return RunResult(
                output=experiment_spec["simulate_output"],
                exit_code=experiment_spec.get("simulate_exit_code", 0),
                timed_out=experiment_spec.get("simulate_timeout", False),
                duration_sec=experiment_spec.get("simulate_duration", 0.0),
            )

        # Command execution mode
        command = experiment_spec.get("command")
        if command:
            return self._run_command(command)

        # Python code execution mode
        python_code = experiment_spec.get("python_code")
        if python_code:
            return self._run_python(python_code)

        raise ValueError("Experiment spec must contain 'command', 'python_code', or 'simulate_output'")

    def _run_command(self, command: list[str]) -> RunResult:
        """Run a shell command with timeout."""
        started = time.time()
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=self.work_dir,
            )
            duration = time.time() - started
            output = None
            try:
                output = json.loads(proc.stdout)
            except (json.JSONDecodeError, ValueError):
                output = proc.stdout

            return RunResult(
                output=output,
                exit_code=proc.returncode,
                timed_out=False,
                duration_sec=round(duration, 2),
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.time() - started
            return RunResult(
                output=None,
                exit_code=None,
                timed_out=True,
                duration_sec=round(duration, 2),
                stdout=(e.stdout or "").decode("utf-8", "replace")[-4000:] if isinstance(e.stdout, bytes) else str(e.stdout or "")[-4000:],
                stderr=(e.stderr or "").decode("utf-8", "replace")[-2000:] if isinstance(e.stderr, bytes) else str(e.stderr or "")[-2000:],
                error=f"Command timed out after {self.timeout_sec}s",
            )

    def _run_python(self, code: str) -> RunResult:
        """Execute Python code and capture output."""
        started = time.time()
        try:
            result = {}
            exec_globals: dict[str, Any] = {"__builtins__": __builtins__}
            exec(compile(code, "<experiment>", "exec"), exec_globals)
            # Look for 'result' or 'output' variable
            output = exec_globals.get("result") or exec_globals.get("output")
            duration = time.time() - started
            return RunResult(
                output=output,
                exit_code=0,
                timed_out=False,
                duration_sec=round(duration, 2),
            )
        except Exception as e:
            duration = time.time() - started
            return RunResult(
                output=None,
                exit_code=1,
                timed_out=False,
                duration_sec=round(duration, 2),
                error=str(e),
            )
