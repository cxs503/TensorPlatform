"""External ground-truth probes for worker occupancy — /proc + cgroup v2 (v1 fallback).

Design constraints (mission IN23-N08): the measurement plane is **read-only**.
Nothing here opens a file for writing except the caller's own JSONL sink; no DDL/DML,
no platform table is touched, no cgroup control file is ever written.

Three probe families, in decreasing authority:

  ``cgroup_v2``  unified hierarchy: ``cpu.stat`` (``usage_usec``), ``memory.current``,
                 ``pids.current``.  This is the metric the kernel itself charges, so it is
                 the strongest truth available — *provided* the worker lives in its own
                 cgroup (see :func:`cgroup_scope_report`).
  ``cgroup_v1``  legacy split hierarchies (``cpu,cpuacct`` / ``memory`` / ``pids``) with
                 ``cpuacct.usage`` (ns), ``memory.usage_in_bytes``, ``pids.current``.
  ``proc``       ``/proc/<pid>/stat`` ``utime+stime`` summed over the *process tree* and
                 ``/proc/<pid>/statm`` resident pages.  Per-worker and always available;
                 this is the workhorse when every worker shares one cgroup.

All parsing functions are pure (text in, numbers out) so they can be unit-tested against
fixtures without the live kernel.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# ── parse helpers (pure) ──────────────────────────────────────────────────────

def parse_proc_stat(text: str) -> Optional[Dict[str, object]]:
    """``/proc/<pid>/stat`` → {pid, comm, ppid, utime_s, stime_s}.

    ``comm`` may contain spaces and parentheses, so split on the *last* ``)``.
    """
    try:
        lpar = text.index("(")
        rpar = text.rindex(")")
    except ValueError:
        return None
    comm = text[lpar + 1:rpar]
    rest = text[rpar + 2:].split()
    if len(rest) < 15:
        return None
    # rest[0] = state, rest[1] = ppid, ... utime = rest[11], stime = rest[12]
    try:
        return {
            "pid": int(text[:lpar - 1]),
            "comm": comm,
            "state": rest[0],
            "ppid": int(rest[1]),
            "utime_s": int(rest[11]) / CLK_TCK,
            "stime_s": int(rest[12]) / CLK_TCK,
        }
    except (ValueError, IndexError):
        return None


def parse_statm_resident(text: str, page_size: int) -> int:
    """``/proc/<pid>/statm`` → resident set size in bytes (2nd field, pages)."""
    parts = text.split()
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1]) * page_size
    except ValueError:
        return 0


def parse_cgroup_v2(text: str) -> Optional[str]:
    """``/proc/<pid>/cgroup`` → unified path (``0::/foo``) or ``None`` on a v1 host."""
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            return fields[2]
    return None


def parse_cgroup_v1(text: str) -> Optional[str]:
    """``/proc/<pid>/cgroup`` → the ``cpu`` (or ``cpu,cpuacct``) v1 path, if any."""
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        ctrls = fields[1].split(",")
        if "cpuacct" in ctrls or "cpu" in ctrls:
            return fields[2]
    return None


def parse_cpu_stat_v2(text: str) -> Dict[str, int]:
    """cgroup v2 ``cpu.stat`` → {usage_usec, user_usec, system_usec, nr_periods, ...}."""
    out: Dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


CLK_TCK = 100  # Linux USER_HZ; /proc/<pid>/stat utime/stime are in this unit
if hasattr(os, "sysconf"):
    try:  # pragma: no cover - platform constant, exercised on the live host
        CLK_TCK = int(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError):  # pragma: no cover
        pass

PAGE_SIZE = 4096


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


# ── cgroup readers ────────────────────────────────────────────────────────────

@dataclass
class CgroupMetrics:
    backend: str                     # "cgroup_v2" | "cgroup_v1"
    path: str
    cpu_s: Optional[float] = None    # cumulative CPU seconds charged to the cgroup
    pids: Optional[int] = None       # pids.current
    mem_bytes: Optional[int] = None  # memory.current / memory.usage_in_bytes
    raw: Dict[str, int] = field(default_factory=dict)


def read_cgroup_v2(path: str, sysroot: str = "/sys/fs/cgroup") -> Optional[CgroupMetrics]:
    """Read unified-hierarchy metrics for cgroup *path* (e.g. ``/system.slice/foo``)."""
    base = os.path.join(sysroot, path.lstrip("/"))
    stat_txt = _read(os.path.join(base, "cpu.stat"))
    if stat_txt is None:
        return None
    stat = parse_cpu_stat_v2(stat_txt)
    if "usage_usec" not in stat:
        return None
    pids_txt = _read(os.path.join(base, "pids.current"))
    mem_txt = _read(os.path.join(base, "memory.current"))
    return CgroupMetrics(
        backend="cgroup_v2",
        path=path,
        cpu_s=stat["usage_usec"] / 1e6,
        pids=int(pids_txt.strip()) if pids_txt and pids_txt.strip().isdigit() else None,
        mem_bytes=int(mem_txt.strip()) if mem_txt and mem_txt.strip().isdigit() else None,
        raw=stat,
    )


def read_cgroup_v1(path: str, sysroot: str = "/sys/fs/cgroup") -> Optional[CgroupMetrics]:
    """Read legacy split-hierarchy metrics for cgroup *path*."""
    base = path.lstrip("/")
    usage = _read(os.path.join(sysroot, "cpu,cpuacct", base, "cpuacct.usage"))
    if usage is None:
        return None
    mem = _read(os.path.join(sysroot, "memory", base, "memory.usage_in_bytes"))
    pids = _read(os.path.join(sysroot, "pids", base, "pids.current"))
    try:
        cpu_s = int(usage.strip()) / 1e9
    except ValueError:
        return None
    return CgroupMetrics(
        backend="cgroup_v1",
        path=path,
        cpu_s=cpu_s,
        pids=int(pids.strip()) if pids and pids.strip().isdigit() else None,
        mem_bytes=int(mem.strip()) if mem and mem.strip().isdigit() else None,
    )


def cgroup_backend(sysroot: str = "/sys/fs/cgroup") -> str:
    """Which hierarchy the host exposes: ``cgroup_v2`` if unified, else ``cgroup_v1``."""
    if os.path.exists(os.path.join(sysroot, "cgroup.controllers")):
        return "cgroup_v2"
    if os.path.isdir(os.path.join(sysroot, "cpu,cpuacct")):
        return "cgroup_v1"
    return "none"


def cgroup_of_pid(pid: int, proc_root: str = "/proc") -> Tuple[Optional[str], Optional[str]]:
    """(unified_path, v1_cpu_path) for *pid*."""
    txt = _read(os.path.join(proc_root, str(pid), "cgroup"))
    if txt is None:
        return None, None
    return parse_cgroup_v2(txt), parse_cgroup_v1(txt)


# ── /proc process table ───────────────────────────────────────────────────────

@dataclass
class Proc:
    pid: int
    ppid: int
    comm: str
    utime_s: float
    stime_s: float
    state: str = "R"
    rss_bytes: int = 0
    cmdline: Optional[str] = None

    @property
    def cpu_s(self) -> float:
        return self.utime_s + self.stime_s


def _wants_cmdline(comm: str) -> bool:
    """Only interpreter-ish processes can be worker roots / wrappers — keeps the scan cheap."""
    return ("python" in comm) or comm in ("sh", "bash", "node", "perl", "ruby", "env")


def scan_procs(proc_root: str = "/proc", cmdline_for: Callable[[str], bool] = _wants_cmdline) -> Dict[int, Proc]:
    """One pass over ``/proc``: ppid/cpu for every pid, cmdline for interpreter-like pids."""
    procs: Dict[int, Proc] = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return procs
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        txt = _read(os.path.join(proc_root, name, "stat"))
        if txt is None:
            continue
        rec = parse_proc_stat(txt)
        if rec is None:
            continue
        p = Proc(pid=pid, ppid=int(rec["ppid"]), comm=str(rec["comm"]),
                 utime_s=float(rec["utime_s"]), stime_s=float(rec["stime_s"]),
                 state=str(rec["state"]))
        if cmdline_for(p.comm):
            raw = _read(os.path.join(proc_root, name, "cmdline"))
            if raw:
                p.cmdline = raw.replace("\x00", " ").strip()
        procs[pid] = p
    return procs


def children_map(procs: Dict[int, Proc]) -> Dict[int, List[int]]:
    cmap: Dict[int, List[int]] = {}
    for pid, p in procs.items():
        cmap.setdefault(p.ppid, []).append(pid)
    return cmap


def descendants(root_pid: int, cmap: Dict[int, List[int]], limit: int = 4096) -> List[int]:
    """All pids under *root_pid* (inclusive), breadth-first, cycle-safe."""
    out: List[int] = []
    seen = set()
    stack = [root_pid]
    while stack and len(out) < limit:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        stack.extend(cmap.get(pid, ()))
    return out


def worker_roots(procs: Dict[int, Proc], marker: str = "tp_worker.py") -> Dict[str, List[int]]:
    """worker_id → root pids, discovered from ``tp_worker.py --agent-id <id>`` cmdlines."""
    roots: Dict[str, List[int]] = {}
    for pid, p in procs.items():
        if not p.cmdline or marker not in p.cmdline:
            continue
        wid = None
        parts = p.cmdline.split()
        for i, tok in enumerate(parts):
            if tok == "--agent-id" and i + 1 < len(parts):
                wid = parts[i + 1]
                break
            if tok.startswith("--agent-id="):
                wid = tok.split("=", 1)[1]
                break
        if wid:
            roots.setdefault(wid, []).append(pid)
    return roots


def read_proc_io(pid: int, proc_root: str = "/proc") -> Optional[Dict[str, int]]:
    """``/proc/<pid>/io`` → {rchar, wchar, syscr, syscw} (may be permission-limited)."""
    txt = _read(os.path.join(proc_root, str(pid), "io"))
    if txt is None:
        return None
    out: Dict[str, int] = {}
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0].rstrip(":")] = int(parts[1])
    return out or None


def count_sockets(pid: int, proc_root: str = "/proc", limit: int = 512) -> int:
    """Open socket fds of *pid* (``fd`` symlinks pointing at ``socket:[inode]``)."""
    base = os.path.join(proc_root, str(pid), "fd")
    try:
        names = os.listdir(base)[:limit]
    except OSError:
        return 0
    n = 0
    for name in names:
        try:
            if os.readlink(os.path.join(base, name)).startswith("socket:"):
                n += 1
        except OSError:
            continue
    return n


def tree_metrics(procs: Dict[int, Proc], cmap: Dict[int, List[int]], roots: Iterable[int],
                 proc_root: str = "/proc", want_rss: bool = True,
                 want_io: bool = False, want_sockets: bool = False) -> Dict[str, object]:
    """Aggregate CPU / RSS / pid count over the union of the root pids' trees."""
    pids: List[int] = []
    for r in roots:
        pids.extend(descendants(r, cmap))
    pids = sorted(set(pids))
    live = [p for p in pids if p in procs]
    cpu_s = sum(procs[p].cpu_s for p in live)
    rss = 0
    if want_rss:
        for p in live:
            txt = _read(os.path.join(proc_root, str(p), "statm"))
            if txt:
                rss += parse_statm_resident(txt, PAGE_SIZE)
    io_syscalls = io_bytes = 0
    if want_io:
        for p in live:
            io = read_proc_io(p, proc_root)
            if io:
                io_syscalls += io.get("syscr", 0) + io.get("syscw", 0)
                io_bytes += io.get("rchar", 0) + io.get("wchar", 0)
    sockets = sum(count_sockets(p, proc_root) for p in live) if want_sockets else None
    return {"pids": len(live), "pid_list": live, "cpu_s": cpu_s, "rss_bytes": rss,
            "io_syscalls": io_syscalls if want_io else None,
            "io_bytes": io_bytes if want_io else None,
            "sockets": sockets}


def cgroup_scope_report(procs: Dict[int, Proc], roots: Dict[str, List[int]],
                        cmap: Dict[int, List[int]], sysroot: str = "/sys/fs/cgroup",
                        proc_root: str = "/proc") -> Dict[str, object]:
    """Are workers *individually* cgroup-scoped?  If not, per-worker cgroup truth is impossible.

    Returns ``{backend, distinct_paths, per_worker: {wid: path}, exclusive: {wid: bool}}``.
    ``exclusive`` is true only when a worker's cgroup path holds that worker alone, which is
    the precondition for attributing ``cpu.stat``/``memory.current``/``pids.current`` to it.
    """
    backend = cgroup_backend(sysroot)
    per_worker: Dict[str, str] = {}
    for wid, rpids in roots.items():
        paths = set()
        for pid in rpids:
            v2, v1 = cgroup_of_pid(pid, proc_root)
            p = v2 if backend == "cgroup_v2" else v1
            if p:
                paths.add(p)
        if paths:
            per_worker[wid] = sorted(paths)[0]
    counts: Dict[str, int] = {}
    for p in per_worker.values():
        counts[p] = counts.get(p, 0) + 1
    exclusive = {wid: counts[p] == 1 for wid, p in per_worker.items()}
    return {
        "backend": backend,
        "distinct_paths": len(set(per_worker.values())),
        "per_worker": per_worker,
        "exclusive": exclusive,
        "n_workers_scoped_alone": sum(1 for v in exclusive.values() if v),
        "n_workers": len(per_worker),
    }