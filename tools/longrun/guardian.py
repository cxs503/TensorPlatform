"""Out-of-process crash autopsy: ptrace-attached guardian that captures real ELF cores.

Why a separate process (and not an in-process signal handler)?  A synchronous
fault such as ``ctypes.string_at(0)`` raises SIGSEGV *inside a C frame*; CPython
runs Python-level signal handlers at bytecode boundaries, so the "handler" never
runs, and if it did, returning from it would re-execute the faulting instruction
and spin forever.  The only reliable design is the crashpad/breakpad one: a
supervisor that owns the tracee via ``ptrace``, observes the signal-delivery
stop *before* the faulting thread is killed, freezes it, and dumps a core.

Sequence implemented by :func:`run_guarded`:

1. ``fork()``; the child calls ``ptrace(PTRACE_TRACEME)`` then ``execv``s the job.
2. The guardian ``waitpid``s.  Every signal produces a stop; benign signals are
   re-injected (``PTRACE_CONT`` with the same signal), which keeps job semantics.
3. On a fatal signal the guardian reads ``siginfo`` via ``PTRACE_GETSIGINFO``
   (signal, ``si_code``, fault address), then ``PTRACE_DETACH``s with ``SIGSTOP``
   so the process stays frozen while ``gdb`` attaches.
4. ``gdb -p <pid> -batch -ex "thread apply all bt" -ex "gcore <path>"`` writes a
   real ELF core; the core is then re-opened standalone (``gdb exe core -ex bt``)
   to prove it is a parseable artifact, and the ``??`` frames are symbolized with
   ``addr2line``.
5. The frozen child is killed, the autopsy bundle is written and scored.

No domain semantics: the guardian never looks inside the job's data, only at
process-level evidence.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import forensics

PTRACE_TRACEME = 0
PTRACE_CONT = 7
PTRACE_DETACH = 17
PTRACE_GETSIGINFO = 0x4202

_libc = ctypes.CDLL(None, use_errno=True)
_libc.ptrace.restype = ctypes.c_long
_libc.ptrace.argtypes = [ctypes.c_ulong, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]

_SIGTRAP = int(signal.SIGTRAP)
_SIGSTOP = int(signal.SIGSTOP)


#: POSIX si_code values we may see for the fatal signals (negative == generic).
_SI_CODE_NAMES = {
    11: {1: "SEGV_MAPERR", 2: "SEGV_ACCERR", 3: "SEGV_BNDERR", 4: "SEGV_PKUERR"},
    7: {1: "BUS_ADRALN", 2: "BUS_ADRERR", 3: "BUS_OBJERR"},
    8: {1: "FPE_INTDIV", 2: "FPE_INTOVF", 3: "FPE_FLTDIV", 4: "FPE_FLTOVF", 5: "FPE_FLTUND",
        6: "FPE_FLTRES", 7: "FPE_FLTINV", 8: "FPE_FLTSUB"},
    4: {1: "ILL_ILLOPC", 2: "ILL_ILLOPN", 3: "ILL_ILLADR", 4: "ILL_ILLTRP", 5: "ILL_PRVOPC"},
}
_GENERIC_SI_CODE = {-6: "SI_TKILL", -4: "SI_KERNEL", -1: "SI_QUEUE", 128: "SI_KERNEL"}


class _SigInfo(ctypes.Structure):
    """Full-size ``siginfo_t`` (128 B on Linux) with the fields we need decoded.

    The buffer *must* be the full 128 bytes: ``PTRACE_GETSIGINFO`` copies the whole
    kernel struct, and a short buffer silently corrupts the caller's heap (this was
    observed as a delayed SIGSEGV inside the guardian itself).
    """

    _fields_ = [
        ("si_signo", ctypes.c_int),
        ("si_errno", ctypes.c_int),
        ("si_code", ctypes.c_int),
        ("_pad", ctypes.c_int),
        ("si_addr", ctypes.c_uint64),
        ("_reserved", ctypes.c_char * 104),
    ]


def ptrace(request: int, pid: int, addr: int = 0, data: int = 0) -> int:
    res = _libc.ptrace(
        ctypes.c_ulong(request),
        ctypes.c_long(pid),
        ctypes.c_void_p(addr),
        ctypes.c_void_p(data),
    )
    if res == -1:
        err = ctypes.get_errno()
        raise OSError(err, f"ptrace({request}) on {pid} failed: {os.strerror(err)}")
    return int(res)


def get_siginfo(pid: int) -> Dict[str, Any]:
    buf = _SigInfo()
    res = _libc.ptrace(
        ctypes.c_ulong(PTRACE_GETSIGINFO),
        ctypes.c_long(pid),
        ctypes.c_void_p(0),
        ctypes.byref(buf),
    )
    if res == -1:
        err = ctypes.get_errno()
        return {"error": os.strerror(err), "errno": err}
    return {
        "si_signo": int(buf.si_signo),
        "si_errno": int(buf.si_errno),
        "si_code": int(buf.si_code),
        "si_addr": int(buf.si_addr),
        "si_code_name": _SI_CODE_NAMES.get(int(buf.si_signo), {}).get(int(buf.si_code)),
    }



_DEBUG_PATH = os.environ.get("LONGRUN_DEBUG")


def _dbg(msg: str) -> None:
    """Opt-in step tracing (``LONGRUN_DEBUG=/path/to/log``) -- no effect by default."""
    if not _DEBUG_PATH:
        return
    try:
        with open(_DEBUG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {time.time():.3f} {msg}\n")
    except OSError:
        pass

# --------------------------------------------------------------------------- #
# gdb / symbolization helpers
# --------------------------------------------------------------------------- #
_FRAME_RE = re.compile(r"^#(?P<idx>\d+)\s+(?:0x(?P<addr>[0-9a-fA-F]+)\s+in\s+)?(?P<rest>.*)$")


def find_gdb(preferred: Optional[str] = None) -> Optional[str]:
    if preferred:
        return preferred if shutil.which(preferred) or os.path.exists(preferred) else None
    return shutil.which("gdb")


def parse_bt(output: str) -> List[Dict[str, Any]]:
    """Parse ``gdb`` backtrace text into frame dicts."""
    frames: List[Dict[str, Any]] = []
    for line in output.splitlines():
        m = _FRAME_RE.match(line.strip())
        if not m:
            continue
        rest = (m.group("rest") or "").strip()
        tail = line.strip()
        symbol = rest
        source = ""
        if " from " in rest:
            symbol, source = rest.split(" from ", 1)
        frames.append(
            {
                "index": int(m.group("idx")),
                "addr": ("0x" + m.group("addr").lower()) if m.group("addr") else None,
                "symbol": symbol.strip() or None,
                "source": source.strip() or None,
                "raw": tail[:400],
            }
        )
    return frames


def symbolize(frames: Sequence[Dict[str, Any]], exe: Optional[str], max_frames: int = 12) -> List[Dict[str, Any]]:
    """Fill in ``file:line`` for frames whose symbol is unknown, via addr2line."""
    addr2line = shutil.which("addr2line")
    if not addr2line or not exe:
        return list(frames)
    todo = [
        f
        for f in frames[:max_frames]
        if f.get("addr") and not f.get("symbol") or (f.get("symbol") in (None, "??"))
    ]
    if not todo:
        return list(frames)
    addrs = [f["addr"] for f in todo]
    try:
        proc = subprocess.run(
            [addr2line, "-f", "-C", "-e", exe] + addrs,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return list(frames)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    for i, frame in enumerate(todo):
        if 2 * i + 1 >= len(lines):
            break
        frame["symbolized_function"] = lines[2 * i].strip()
        frame["symbolized_location"] = lines[2 * i + 1].strip()
    return list(frames)


def _sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def capture_core(
    pid: int,
    exe: Optional[str],
    core_path: Path,
    gdb: Optional[str] = None,
    timeout: float = 120.0,
    full_core: bool = False,
    max_core_bytes: int = 2 << 30,
    keep_oversize: bool = False,
    siginfo: Optional[Dict[str, Any]] = None,
    full_registers: bool = False,
) -> Dict[str, Any]:
    """Capture an autopsy of a frozen process. Two tiers, both hashed.

    tier 1 (always): ``stackcore`` JSON -- gdb ``info threads`` / ``thread apply all
    bt`` / ``info proc mappings`` / per-thread registers, plus ``addr2line``
    symbolization.  Kilobytes, always available, and enough to classify + localize.

    tier 2 (opt-in ``full_core=True``): a *real* ELF core via ``gcore``.  It is the
    strongest artifact but its size is the size of the address space: this runtime
    carries a 4 GiB anonymous mapping in every process, so a python core is ~4.2 GB
    (measured).  ``max_core_bytes`` therefore guards the disk: an oversized core is
    deleted, its size/sha256 are still recorded, and ``core_status`` says so --
    silently keeping 4 GB per crash would be a production hazard.
    """
    gdb = find_gdb(gdb)
    result: Dict[str, Any] = {
        "gdb": gdb,
        "core_path": None,
        "core_size": 0,
        "core_sha256": None,
        "core_status": "unavailable",
        "native_bt": [],
        "native_bt_all_threads": "",
        "mappings": "",
        "registers": "",
        "capture_s": None,
        "elf_core_path": None,
        "elf_core_size": 0,
        "elf_core_sha256": None,
        "elf_core_retained": False,
        "core_reopen_bt": [],
        "core_reopen_ok": None,
    }
    core_path = Path(core_path)
    core_path.parent.mkdir(parents=True, exist_ok=True)
    stackcore_path = core_path.with_suffix(".stackcore.json")
    if not gdb:
        result["core_status"] = "unavailable:gdb_missing"
        return result
    t0 = time.time()
    cmd = [
        gdb,
        "-p",
        str(pid),
        "--batch",
        "-ex",
        "set confirm off",
        "-ex",
        "set pagination off",
        "-ex",
        "set width 250",
        "-ex",
        "info threads",
        "-ex",
        "thread apply all bt 12",
        "-ex",
        "info proc mappings",
        "-ex",
        ("thread apply all info registers" if full_registers
         else "thread apply all info registers rip pc sp"),
    ]
    if full_core:
        cmd += ["-ex", f"gcore {core_path}"]
    cmd += ["-ex", "detach"]
    out = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        result["core_status"] = "unavailable:gdb_timeout"
    result["capture_s"] = round(time.time() - t0, 3)
    result["gdb_output_tail"] = out[-4000:]

    frames = symbolize(parse_bt(out), exe)
    result["native_bt"] = frames
    result["native_bt_all_threads"] = out[:20000]
    result["mappings"] = _section(out, "Mapped address spaces")[:20000]
    result["registers"] = _section(out, "rax")[:8000] or _section(out, "x0")[:8000]

    # ---- tier 1: stackcore (always) ------------------------------------- #
    try:
        gdb_version = subprocess.run(
            [gdb, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.splitlines()[:1]
    except (OSError, subprocess.SubprocessError):
        gdb_version = []
    payload = {
        "schema": "longrun.stackcore/1",
        "captured_unix": time.time(),
        "pid": pid,
        "exe": exe,
        "siginfo": siginfo or {},
        "gdb": gdb,
        "gdb_version": gdb_version,
        "threads_and_backtrace": out[:60000],
        "frames": frames,
        "capture_s": result["capture_s"],
    }
    body = json.dumps(payload, indent=2, sort_keys=True, default=str)
    stackcore_path.write_text(body, encoding="utf-8")
    result["core_path"] = str(stackcore_path)
    result["core_size"] = len(body.encode())
    result["core_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    if result["core_status"] == "unavailable":
        result["core_status"] = "stackcore_only"

    # ---- tier 2: real ELF core (opt-in) --------------------------------- #
    if full_core and core_path.exists() and core_path.stat().st_size > 0:
        size = core_path.stat().st_size
        digest = _sha256_file(str(core_path))
        result["elf_core_size"] = size
        result["elf_core_sha256"] = digest
        if size > max_core_bytes and not keep_oversize:
            try:
                core_path.unlink()
            except OSError:
                pass
            result["elf_core_retained"] = False
            result["core_status"] = "stackcore_only:elf_core_oversize"
            result["elf_core_note"] = (
                f"elf core {size} B > max_core_bytes {max_core_bytes} B, discarded after hashing"
            )
        else:
            result["elf_core_retained"] = True
            result["elf_core_path"] = str(core_path)
            result["core_status"] = "stackcore+elf"
            if exe:
                try:
                    reopen = subprocess.run(
                        [gdb, exe, str(core_path), "--batch", "-ex", "set confirm off",
                         "-ex", "set pagination off", "-ex", "bt 12"],
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                    )
                    text = (reopen.stdout or "") + (reopen.stderr or "")
                    result["core_reopen_bt"] = symbolize(parse_bt(text), exe)
                    result["core_reopen_ok"] = bool(result["core_reopen_bt"])
                except (OSError, subprocess.SubprocessError):
                    result["core_reopen_ok"] = False
    elif full_core:
        result["core_status"] = result["core_status"] + ":gcore_failed"
    return result


def _section(text: str, after: str, max_lines: int = 400) -> str:
    """Return the block of gdb output starting at the first line containing ``after``."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if after in line:
            return "\n".join(lines[i : i + max_lines])
    return ""


# --------------------------------------------------------------------------- #
# guardian
# --------------------------------------------------------------------------- #
@dataclass
class GuardianResult:
    exit_code: Optional[int] = None
    term_signal: Optional[int] = None
    crashed: bool = False
    timed_out: bool = False
    stops: List[Dict[str, Any]] = field(default_factory=list)
    siginfo: Dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    core: Dict[str, Any] = field(default_factory=dict)
    pid: Optional[int] = None
    oom_kill_delta: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "term_signal": self.term_signal,
            "signal_name": forensics.FATAL_SIGNALS.get(int(self.term_signal or -1), None),
            "crashed": self.crashed,
            "timed_out": self.timed_out,
            "stops": self.stops,
            "siginfo": self.siginfo,
            "duration_s": round(self.duration_s, 6),
            "pid": self.pid,
            "oom_kill_delta": self.oom_kill_delta,
            "core": dict(self.core),
        }


def run_guarded(
    exe: str,
    argv: Sequence[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    core_path: Optional[os.PathLike | str] = None,
    timeout: Optional[float] = None,
    gdb: Optional[str] = None,
    capture: bool = True,
    poll_s: float = 0.01,
    full_core: bool = False,
    max_core_bytes: int = 2 << 30,
    keep_oversize_core: bool = False,
    full_registers: bool = False,
) -> GuardianResult:
    """Run ``exe``/``argv`` under ptrace supervision and perform an autopsy on fatality."""
    result = GuardianResult()
    oom_before = forensics._oom_kill_total()
    t0 = time.time()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child branch
        try:
            if cwd:
                os.chdir(cwd)
            ptrace(PTRACE_TRACEME, 0)
            args = [exe] + list(argv)
            if env is not None:
                os.execve(exe, args, env)
            else:
                os.execv(exe, args)
        except BaseException:  # noqa: BLE001 - never leak into the parent
            os._exit(127)
    result.pid = pid
    _dbg('forked child')
    exe_path: Optional[str] = exe
    exec_seen = False
    frozen = False
    try:
        while True:
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid == 0:
                if timeout is not None and (time.time() - t0) > timeout:
                    result.timed_out = True
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                    break
                time.sleep(poll_s)
                continue
            if os.WIFSTOPPED(status):
                sig = os.WSTOPSIG(status)
                _dbg(f'stop sig={sig} exited={os.WIFEXITED(status)}')
                if sig == _SIGTRAP and not exec_seen:
                    exec_seen = True
                    ptrace(PTRACE_CONT, pid, 0, 0)
                    continue
                result.stops.append({"signal": sig, "t": round(time.time() - t0, 6)})
                if sig in forensics.FATAL_SIGNALS:
                    result.term_signal = sig
                    result.crashed = True
                    result.siginfo = get_siginfo(pid)
                    try:
                        exe_path = os.readlink(f"/proc/{pid}/exe")
                    except OSError:
                        pass
                    ptrace(PTRACE_DETACH, pid, 0, _SIGSTOP)
                    frozen = True
                    break
                if sig == forensics.SIGKILL:
                    result.term_signal = sig
                    ptrace(PTRACE_CONT, pid, 0, sig)
                    continue
                ptrace(PTRACE_CONT, pid, 0, sig)
                continue
            if os.WIFEXITED(status):
                result.exit_code = os.WEXITSTATUS(status)
                break
            if os.WIFSIGNALED(status):
                result.term_signal = os.WTERMSIG(status)
                result.crashed = result.crashed or result.term_signal in forensics.FATAL_SIGNALS
                break
            break
        _dbg(f'loop done crashed={result.crashed} frozen={frozen}')
        if capture and frozen:
            core_target = Path(
                core_path or Path.cwd() / f"core.longrun.{pid}"
            )
            _dbg('capture_core start')
            result.core = capture_core(
                pid,
                exe_path,
                core_target,
                gdb=gdb,
                full_core=full_core,
                max_core_bytes=max_core_bytes,
                keep_oversize=keep_oversize_core,
                siginfo=result.siginfo,
                full_registers=full_registers,
            )
            _dbg('capture_core done')
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        elif frozen:
            # tracee is detached and frozen in a group-stop: only SIGKILL ends it
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
    finally:
        if result.duration_s == 0.0:
            result.duration_s = time.time() - t0
        oom_after = forensics._oom_kill_total()
        if oom_before is not None and oom_after is not None:
            result.oom_kill_delta = max(0, oom_after - oom_before)
        # POSIX convention: a signalled process has no exit code; report -signum
        # so the autopsy always carries a numeric status (137 == SIGKILL, etc.).
        if result.exit_code is None and result.term_signal is not None:
            result.exit_code = -int(result.term_signal)
    return result