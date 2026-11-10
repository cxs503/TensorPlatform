"""In-process flight recorder, failure-mode classification and bundle completeness.

The recorder is the *inside* half of the survival layer; :mod:`tools.longrun.guardian`
is the outside half.  Design constraints that shaped this file:

* Recording must be cheap on the happy path (measured budget: <=2 % wall-clock,
  see ``tools/longrun/bench_m9082.py``).  Stack snapshots therefore go through
  :func:`faulthandler.dump_traceback_later` (a C watchdog thread, no GIL churn in
  the job thread) and the state journal is append-only JSONL.
* A synchronous fault (``ctypes.string_at(0)`` -> SIGSEGV from a C frame) can *not*
  be turned into ordinary Python code: returning from a Python-level SIGSEGV
  handler re-executes the faulting instruction and the process spins forever
  (verified experimentally, see docs/M9082_LONGRUN_SURVIVAL.md).  Hence Python
  threads never try to handle fatal signals; the out-of-process guardian does.

Nothing in this module contains domain vocabulary: jobs are opaque, the classifier
only inspects generic evidence (signal numbers, signatures, exit codes, probe data).
"""

from __future__ import annotations

import faulthandler
import json
import subprocess
import os
import platform
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = "longrun.forensics/1"

#: Signals whose delivery is normally fatal and therefore worth an autopsy.
FATAL_SIGNALS = {
    int(signal.SIGSEGV): "SIGSEGV",
    int(signal.SIGABRT): "SIGABRT",
    int(signal.SIGBUS): "SIGBUS",
    int(signal.SIGFPE): "SIGFPE",
    int(signal.SIGILL): "SIGILL",
}
SIGKILL = int(signal.SIGKILL)

#: Failure modes produced by :func:`classify_failure`.
MODES = (
    "native_segfault",
    "native_abort",
    "native_bus_error",
    "native_arith_fault",
    "native_illegal_instruction",
    "memory_oom_kill",
    "killed_externally",
    "python_exception",
    "python_memory_error",
    "graceful_degraded_exit",
    "budget_exhausted",
    "state_corruption",
    "completed",
    "unknown",
)

#: Fields every autopsy must carry for the bundle to score 100 %.
REQUIRED_FIELDS = (
    "schema",
    "job_id",
    "pid",
    "started_unix",
    "ended_unix",
    "exit_code",
    "term_signal",
    "failure_mode",
    "classification_reasons",
    "python_stack_snapshot",
    "state_journal_tail",
    "memory_probe",
    "runtime_env",
    "code_version",
    "last_checkpoint_digest",
)


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
def memory_probe() -> Dict[str, Any]:
    """Best-effort memory pressure reading (cgroup v2 -> v1 -> /proc/meminfo)."""

    def _read(path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except OSError:
            return None

    probe: Dict[str, Any] = {"source": None}
    used = limit = None
    cur = _read("/sys/fs/cgroup/memory.current")
    mx = _read("/sys/fs/cgroup/memory.max")
    if cur is not None and mx is not None and mx != "max":
        try:
            used, limit = int(cur), int(mx)
            probe["source"] = "cgroup_v2"
        except ValueError:
            used = limit = None
    if used is None:
        cur = _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        mx = _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if cur is not None and mx is not None:
            try:
                limit_candidate = int(mx)
                # cgroup v1 reports a huge sentinel when unlimited
                if limit_candidate < (1 << 62):
                    used, limit = int(cur), limit_candidate
                    probe["source"] = "cgroup_v1"
            except ValueError:
                used = limit = None
    if used is None:
        avail = _read("/proc/meminfo")
        if avail:
            info = {}
            for line in avail.splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    info[parts[0]] = parts[1].strip().split()[0]
            try:
                total = int(info["MemTotal"]) * 1024
                free = int(info["MemAvailable"]) * 1024
                used, limit = total - free, total
                probe["source"] = "proc_meminfo"
            except (KeyError, ValueError):
                pass
    probe["used_bytes"] = used
    probe["limit_bytes"] = limit
    probe["utilization"] = (used / limit) if (used is not None and limit) else None
    probe["oom_kill_total"] = _oom_kill_total()
    rss = _read("/proc/self/statm")
    if rss:
        try:
            pages = int(rss.split()[1])
            probe["rss_bytes"] = pages * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, IndexError, OSError):
            pass
    probe["timestamp_unix"] = time.time()
    return probe


def _oom_kill_total() -> Optional[int]:
    for path, key in (
        ("/sys/fs/cgroup/memory.events", "oom_kill"),
        ("/sys/fs/cgroup/memory/memory.failcnt", None),
        ("/sys/fs/cgroup/memory/memory.oom_control", "oom_kill"),
    ):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].startswith(key or "oom_kill"):
                try:
                    return int(parts[1])
                except ValueError:
                    continue
    return None


def last_checkpoint(journal_path: os.PathLike | str) -> Optional[Dict[str, Any]]:
    """Recover the last checkpoint digest recorded by the flight journal.

    Needed because a signal-killed process never gets to write its own ``result.json``;
    the journal is fsync-free but line-buffered, so the record is normally on disk.
    """
    try:
        lines = Path(journal_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "checkpoint" and record.get("digest"):
            return {"digest": record["digest"], "step": record.get("step"),
                    "state_digest": record.get("state_digest")}
    return None


def runtime_env() -> Dict[str, Any]:
    """Static environment fingerprint (reproducibility evidence for the autopsy)."""
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "argv": list(sys.argv),
        "cwd": os.getcwd(),
        "ppid": os.getppid(),
        "pid": os.getpid(),
        "threads": threading.active_count(),
    }


def code_version(root: Optional[Path] = None) -> Dict[str, Any]:
    """Pin the job's source revision.  Works in plain clones *and* git worktrees.

    (A worktree has ``.git`` as a *file* containing ``gitdir: ...``; reading
    ``.git/HEAD`` naively therefore yields nothing -- which is exactly the kind of
    silent evidence hole an autopsy must not have.)
    """
    root = Path(root or Path(__file__).resolve().parents[2])
    info: Dict[str, Any] = {"root": str(root)}
    gitdir = root / ".git"
    try:
        if gitdir.is_file():
            text = gitdir.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split("ref:", 1)[1].strip()
            info["ref"] = ref
            common = gitdir
            commondir = gitdir / "commondir"
            if commondir.exists():
                common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
            try:
                info["commit"] = (common / ref).read_text(encoding="utf-8").strip()
            except OSError:
                packed = common / "packed-refs"
                if packed.exists():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref:
                            info["commit"] = parts[0]
                            break
        else:
            info["commit"] = head
    except OSError:
        info["commit"] = None
    if not info.get("commit"):
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode == 0:
                info["commit"] = proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return info


# --------------------------------------------------------------------------- #
# flight recorder
# --------------------------------------------------------------------------- #
class FlightRecorder:
    """Always-on, low-overhead crash evidence recorder.

    Writes three things next to each other:

    ``flight.log``     header + periodic all-thread Python stack snapshots
                       (C watchdog via :func:`faulthandler.dump_traceback_later`)
    ``journal.jsonl``  append-only state journal (step, checkpoint digest, ladder
                       rungs, memory pressure) written by the job thread
    ``meta.json``      environment fingerprint, refreshed on ``close()``

    ``overhead_ns`` accumulates the *in-thread* cost of journaling so the
    negative-control benchmark can report measured overhead per step.
    """

    def __init__(
        self,
        outdir: os.PathLike | str,
        job_id: str = "job",
        stack_interval: float = 1.0,
        memory_interval: float = 5.0,
        journal_interval: float = 0.5,
        enabled: bool = True,
    ) -> None:
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.job_id = job_id
        self.stack_interval = float(stack_interval)
        self.memory_interval = float(memory_interval)
        self.journal_interval = float(journal_interval)
        self.enabled = enabled
        self.flight_path = self.outdir / "flight.log"
        self.journal_path = self.outdir / "journal.jsonl"
        self.meta_path = self.outdir / "meta.json"
        self.overhead_ns = 0
        self._fh = None
        self._journal = None
        self._last_memory = 0.0
        self._last_step_log = 0.0
        self.last_step = None
        self._started = time.time()
        self.last_checkpoint_digest: Optional[str] = None
        self._lock = threading.Lock()
        if not enabled:
            return
        self._fh = open(self.flight_path, "a", encoding="utf-8", buffering=1)
        self._fh.write(
            json.dumps(
                {
                    "event": "flight_start",
                    "schema": SCHEMA,
                    "job_id": job_id,
                    "pid": os.getpid(),
                    "started_unix": self._started,
                    "stack_interval_s": self.stack_interval,
                    "env": runtime_env(),
                    "code_version": code_version(),
                },
                sort_keys=True,
            )
            + "\n"
        )
        # C watchdog thread: dumps every thread's Python stack, repeat forever.
        try:
            faulthandler.dump_traceback_later(
                self.stack_interval, repeat=True, file=self._fh, exit=False
            )
            self._stack_watchdog = True
        except (RuntimeError, ValueError):  # e.g. inside a subinterpreter
            self._stack_watchdog = False
        self._journal = open(self.journal_path, "a", encoding="utf-8", buffering=1)

    # -- journal ----------------------------------------------------------- #
    def log(self, event: str, **fields: Any) -> None:
        if self._journal is None:
            return
        t0 = time.perf_counter_ns()
        record = {"ts_unix": round(time.time(), 6), "event": event}
        record.update(fields)
        try:
            self._journal.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        except OSError:
            self.overhead_ns += time.perf_counter_ns() - t0
            return
        self.overhead_ns += time.perf_counter_ns() - t0

    def note_step(self, step: int, elapsed_s: float, extra: Optional[dict] = None) -> None:
        """Record progress.  Throttled to ``journal_interval`` seconds.

        Per-step journaling of a 10 ms step would cost ~0.2 % of every step; the
        forensic value of a *fresh* last-step number is already covered by the 1 s
        stack snapshots, so the journal is throttled and ``last_step`` is kept
        in memory (flushed by ``close()``).  Measured cost: see
        ``tests/test_longrun_overhead.py``.
        """
        self.last_step = int(step)
        now = time.monotonic()
        if self._journal is not None and (now - self._last_step_log) < self.journal_interval:
            return
        self._last_step_log = now
        payload = {"step": step, "elapsed_s": round(elapsed_s, 6)}
        if extra:
            payload.update(extra)
        self.log("step", **payload)

    def note_checkpoint(self, step: int, digest: str, path: str) -> None:
        self.last_checkpoint_digest = digest
        self.log("checkpoint", step=step, digest=digest, path=path)

    def sample_memory(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Memory pressure sample, throttled to ``memory_interval`` seconds."""

        if not self.enabled:
            return None
        now = time.monotonic()
        if not force and (now - self._last_memory) < self.memory_interval:
            return None
        t0 = time.perf_counter_ns()
        probe = memory_probe()
        self.overhead_ns += time.perf_counter_ns() - t0
        self._last_memory = now
        self.log("memory", **{k: v for k, v in probe.items() if k != "timestamp_unix"})
        return probe

    # -- teardown ---------------------------------------------------------- #
    def close(self, exit_code: Optional[int] = None, reason: str = "finished") -> None:
        if not self.enabled:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
        except (RuntimeError, ValueError):
            pass
        meta = {
            "event": "flight_stop",
            "schema": SCHEMA,
            "job_id": self.job_id,
            "pid": os.getpid(),
            "exit_code": exit_code,
            "reason": reason,
            "started_unix": self._started,
            "ended_unix": time.time(),
            "wall_s": round(time.time() - self._started, 6),
            "env": runtime_env(),
            "code_version": code_version(),
            "memory_probe": memory_probe(),
            "last_checkpoint_digest": self.last_checkpoint_digest,
            "last_step": self.last_step,
            "stack_watchdog": getattr(self, "_stack_watchdog", False),
            "in_thread_overhead_ns": self.overhead_ns,
        }
        try:
            self.meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
            if self._fh is not None:
                self._fh.write(json.dumps(meta, sort_keys=True) + "\n")
        finally:
            for fh in (self._fh, self._journal):
                try:
                    if fh is not None:
                        fh.close()
                except OSError:
                    pass


def dedent_stack_snapshot(flight_path: os.PathLike | str, max_chars: int = 20000) -> str:
    """Extract the last recorded Python stack snapshot from ``flight.log``."""

    try:
        text = Path(flight_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    marker = "Thread 0x"
    idx = text.rfind(marker)
    if idx < 0:
        return text[-max_chars:] if text else ""
    return text[idx : idx + max_chars]


def journal_tail(journal_path: os.PathLike | str, n: int = 12) -> List[Dict[str, Any]]:
    try:
        lines = Path(journal_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"raw": line[:400]})
    return out


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
_SIGNAL_MODES = {
    int(signal.SIGSEGV): "native_segfault",
    int(signal.SIGABRT): "native_abort",
    int(signal.SIGBUS): "native_bus_error",
    int(signal.SIGFPE): "native_arith_fault",
    int(signal.SIGILL): "native_illegal_instruction",
}


def classify_failure(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Map generic evidence to a failure-mode label + human-readable reasons.

    ``evidence`` accepts the keys produced by :func:`tools.longrun.guardian.run_guarded`
    (``term_signal``, ``exit_code``, ``siginfo``, ``oom_kill_delta``) and by the child
    report (``error``, ``error_type``, ``stop_reason``).
    """

    reasons: List[str] = []
    term_signal = evidence.get("term_signal")
    exit_code = evidence.get("exit_code")
    child = evidence.get("child_report") or {}
    stop_reason = child.get("stop_reason")
    error_type = child.get("error_type")
    siginfo = evidence.get("siginfo") or {}
    oom_delta = evidence.get("oom_kill_delta")

    mode = "unknown"
    if stop_reason == "graceful_degraded_exit":
        mode = "graceful_degraded_exit"
        reasons.append("child requested graceful exit after degradation ladder rungs")
    elif stop_reason == "budget_exhausted":
        mode = "budget_exhausted"
        reasons.append("child hit its declared step/time budget")
    elif error_type and any(
        token in error_type for token in ("Checkpoint", "Corruption", "Unreadable")
    ):
        mode = "state_corruption"
        reasons.append(f"checkpoint verification failed with {error_type}")
    elif error_type == "MemoryError":
        mode = "python_memory_error"
        reasons.append("python raised MemoryError (allocation refused)")
    elif term_signal in _SIGNAL_MODES:
        mode = _SIGNAL_MODES[int(term_signal)]
        reasons.append(f"fatal signal {term_signal} stopped the process")
        si_code = siginfo.get("si_code")
        if si_code is not None:
            reasons.append(f"siginfo.si_code={si_code}")
        if siginfo.get("si_addr") is not None:
            reasons.append(f"fault address 0x{int(siginfo['si_addr']):x}")
    elif term_signal == SIGKILL or exit_code == 137:
        if oom_delta:
            mode = "memory_oom_kill"
            reasons.append(f"cgroup oom_kill counter advanced by {oom_delta}")
        else:
            mode = "killed_externally"
            reasons.append("SIGKILL without cgroup oom evidence (external kill/cancel)")
    elif error_type:
        mode = "python_exception"
        reasons.append(f"uncaught python exception {error_type}")
    elif child.get("finished") and exit_code == 0:
        mode = "completed"
        reasons.append("child exited 0 with a result digest")
    return {"failure_mode": mode, "classification_reasons": reasons}


#: Failure modes that are *not* expected to carry a terminating signal.
NO_SIGNAL_MODES = frozenset(
    {
        "completed",
        "graceful_degraded_exit",
        "python_exception",
        "python_memory_error",
        "state_corruption",
        "budget_exhausted",
    }
)


def completeness(bundle: Dict[str, Any], require_core: bool = False) -> Dict[str, Any]:
    """Score an autopsy bundle: which required fields are present and non-empty."""

    required = [
        f for f in REQUIRED_FIELDS
        if not (f == "term_signal" and bundle.get("failure_mode") in NO_SIGNAL_MODES)
    ]
    present: Dict[str, bool] = {}
    for name in required:
        value = bundle.get(name)
        present[name] = not (
            value is None
            or value == ""
            or value == []
            or value == {}
        )
    if require_core:
        present["core_file"] = bool(bundle.get("core_path")) and int(bundle.get("core_size") or 0) > 0
    total = len(present)
    ok = sum(1 for v in present.values() if v)
    return {
        "fields": present,
        "present": ok,
        "total": total,
        "completeness": round(ok / total, 6),
        "missing": sorted(k for k, v in present.items() if not v),
    }


def write_bundle(bundle: Dict[str, Any], path: os.PathLike | str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(bundle, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def exception_evidence(exc: BaseException) -> Dict[str, Any]:
    """Turn an uncaught exception into the ``error*`` keys the classifier reads."""
    return {
        "error_type": type(exc).__name__,
        "error": str(exc)[:2000],
        "traceback": "".join(traceback.format_exception(exc))[-8000:],
    }