"""Read-only worker-occupancy sampler: external truth → JSONL (IN23-N08).

One **tick** = one pass over ``/proc`` (+ cgroup metrics) producing:

  * one ``host`` record   — cgroup backend, root-cgroup CPU/pids/memory, scan cost;
  * one ``worker`` record per worker — process-tree CPU (cumulative + delta), pid count,
    RSS, cgroup path, the platform's *claim-window* fields (claim active? heartbeat age?)
    and the worker table's self-reported state.

Discipline: read-only.  No DDL/DML, no cgroup writes, no schema use.  The only side effect
is appending to ``--out`` (a JSONL file inside the work tree).  ``--dry-run`` computes and
prints but never creates the file.

Claim sources
-------------
``db``    read-only ``SELECT`` on ``tensor_dev_missions`` / ``factory_resident_workers``;
``file``  a JSON snapshot (used by the replay harness so it needs no DB);
``none``  no claim information (truth-only: every worker looks unclaimed).

Usage
-----
    python3 -m tools.worker_truth.sampler --duration 3900 --interval 10 \
        --out runs/in23_n08/samples.jsonl --claims db --scope-out runs/in23_n08/scope.json
    python3 -m tools.worker_truth.sampler --ticks-file runs/x.jsonl --dry-run   # dry==commit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.worker_truth import probes, states  # noqa: E402

AGENT_ENV = "/root/tp-agent/agent.env"
DEFAULT_PG_PASSWORD = os.environ.get("TP_PG_PASSWORD", "")

# Completeness is judged on fields that *must* exist on any host.  ``mem_bytes``
# (cgroup ``memory.current``/``memory.usage_in_bytes``) is deliberately **not** in this
# list: it only exists when the worker is cgroup-scoped, which is exactly the structural
# gap this mission measures.  It is reported through ``cgroup_memory_coverage`` instead of
# being silently scored as 100 %.
REQUIRED_WORKER_FIELDS = (
    "ts", "ts_epoch", "worker_id", "backend", "pids", "cpu_s", "cpu_delta_s",
    "delta_basis", "rss_bytes", "mem_source", "claim_active", "io_syscalls",
)

# Required *when applicable*: an unclaimed worker has no heartbeat to age, so demanding a
# number there would be a fabricated field, not a complete one.
CONDITIONAL_REQUIRED = {
    "heartbeat_age_s": lambda r: bool(r.get("claim_active")),
    "io_delta_syscalls": lambda r: r.get("io_basis") == "prev",
}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ── claim / heartbeat sources ─────────────────────────────────────────────────

@dataclass
class ClaimInfo:
    claim_active: bool = False
    claim_ids: List[int] = field(default_factory=list)
    heartbeat_age_s: Optional[float] = None
    worker_hb_age_s: Optional[float] = None
    db_state: Optional[str] = None
    source: str = "none"


class ClaimSource:
    def snapshot(self) -> Dict[str, ClaimInfo]:  # pragma: no cover - interface
        raise NotImplementedError


class NullClaimSource(ClaimSource):
    def snapshot(self) -> Dict[str, ClaimInfo]:
        return {}


def _password_from_env_file(path: str = AGENT_ENV) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TP_PG_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return DEFAULT_PG_PASSWORD


def _psql(sql: str, timeout: int = 30) -> str:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PGPASSWORD": os.environ.get("TP_PG_PASSWORD") or _password_from_env_file(),
        "PGHOST": os.environ.get("TP_PG_HOST", "127.0.0.1"),
        "PGUSER": os.environ.get("TP_PG_USER", "foundation"),
        "PGDATABASE": os.environ.get("TP_PG_DB", "tensorplatform"),
    }
    proc = subprocess.run(["psql", "-tAF", "\t", "-c", sql], capture_output=True,
                          text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError("psql failed: " + (proc.stderr or "").strip()[:300])
    return proc.stdout


SQL_CLAIMS = """
SELECT claimed_by,
       coalesce(array_to_string(array_agg(id ORDER BY id), ','), ''),
       coalesce(round(extract(epoch FROM (now() - max(heartbeat_at)))::numeric, 2)::text, '')
FROM tensor_dev_missions
WHERE status = 'running' AND claimed_by IS NOT NULL
GROUP BY claimed_by
"""

SQL_WORKERS = """
SELECT worker_id, state,
       coalesce(round(extract(epoch FROM (now() - heartbeat_at))::numeric, 2)::text, '')
FROM factory_resident_workers
"""


class DbClaimSource(ClaimSource):
    """Read-only claim/heartbeat snapshot (two SELECTs, no locks, no writes)."""

    def snapshot(self) -> Dict[str, ClaimInfo]:
        out: Dict[str, ClaimInfo] = {}
        for line in _psql(SQL_CLAIMS).splitlines():
            if not line.strip():
                continue
            wid, ids, age = (line.split("\t") + ["", ""])[:3]
            out[wid.strip()] = ClaimInfo(
                claim_active=True,
                claim_ids=[int(x) for x in ids.split(",") if x.strip().isdigit()],
                heartbeat_age_s=float(age) if age.strip() else None,
                source="db",
            )
        for line in _psql(SQL_WORKERS).splitlines():
            if not line.strip():
                continue
            wid, state, age = (line.split("\t") + ["", ""])[:3]
            ci = out.get(wid.strip(), ClaimInfo(source="db"))
            ci.db_state = state.strip() or None
            ci.worker_hb_age_s = float(age) if age.strip() else None
            ci.source = "db"
            out[wid.strip()] = ci
        return out


class FileClaimSource(ClaimSource):
    """JSON claim snapshot: ``{worker_id: {claim_active, claim_ids, heartbeat_age_s}}``.

    Live files are re-read each tick, so a replay harness can mutate claim state between
    samples (that is how a *silent death* differs from a *clean exit*).
    """

    def __init__(self, path: str):
        self.path = path

    def snapshot(self) -> Dict[str, ClaimInfo]:
        out: Dict[str, ClaimInfo] = {}
        if os.path.isdir(self.path):  # per-worker files: <dir>/<worker_id>.json
            for name in sorted(os.listdir(self.path)):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.path, name), encoding="utf-8") as fh:
                        rec = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(rec, dict):
                    out[name[:-5]] = ClaimInfo(
                        claim_active=bool(rec.get("claim_active")),
                        claim_ids=list(rec.get("claim_ids") or []),
                        heartbeat_age_s=rec.get("heartbeat_age_s"),
                        worker_hb_age_s=rec.get("worker_hb_age_s"),
                        db_state=rec.get("db_state"),
                        source="file",
                    )
            return out
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return {}
        for wid, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            out[wid] = ClaimInfo(
                claim_active=bool(rec.get("claim_active")),
                claim_ids=list(rec.get("claim_ids") or []),
                heartbeat_age_s=rec.get("heartbeat_age_s"),
                worker_hb_age_s=rec.get("worker_hb_age_s"),
                db_state=rec.get("db_state"),
                source="file",
            )
        return out


def make_claim_source(kind: str, path: Optional[str] = None) -> ClaimSource:
    if kind == "db":
        return DbClaimSource()
    if kind == "file":
        if not path:
            raise SystemExit("--claims file requires --claims-file")
        return FileClaimSource(path)
    return NullClaimSource()


# ── sampling ──────────────────────────────────────────────────────────────────

@dataclass
class SamplerConfig:
    interval_s: float = 10.0
    sysroot: str = "/sys/fs/cgroup"
    proc_root: str = "/proc"
    worker_marker: str = "tp_worker.py"
    known_workers: Sequence[str] = ()
    want_rss: bool = True
    want_io: bool = True          # /proc/<pid>/io + socket fds: is the worker *touching* things?
    want_sockets: bool = True


class Sampler:
    """Stateless-per-tick sampler; carries only the per-worker CPU baseline for deltas."""

    def __init__(self, cfg: SamplerConfig, claim_source: ClaimSource):
        self.cfg = cfg
        self.claims = claim_source
        self._prev_cpu: Dict[str, float] = {}
        self._prev_ts: Dict[str, float] = {}
        self._prev_io: Dict[str, int] = {}

    def tick(self, now: Optional[float] = None) -> List[Dict[str, object]]:
        cfg = self.cfg
        t0 = time.monotonic()
        now = time.time() if now is None else now
        procs = probes.scan_procs(cfg.proc_root)
        cmap = probes.children_map(procs)
        roots = probes.worker_roots(procs, marker=cfg.worker_marker)
        for wid in cfg.known_workers:
            roots.setdefault(wid, [])
        claims = self.claims.snapshot()

        def cg(path_attr: str) -> Optional[probes.CgroupMetrics]:
            backend = probes.cgroup_backend(cfg.sysroot)
            if backend == "cgroup_v2":
                return probes.read_cgroup_v2(path_attr, cfg.sysroot)
            if backend == "cgroup_v1":
                return probes.read_cgroup_v1(path_attr, cfg.sysroot)
            return None

        backend_host = probes.cgroup_backend(cfg.sysroot)
        host_cg = cg("/")
        records: List[Dict[str, object]] = [{
            "kind": "host",
            "ts": iso(now),
            "ts_epoch": now,
            "backend": backend_host if host_cg else "proc",
            "cgroup_cpu_s": host_cg.cpu_s if host_cg else None,
            "cgroup_mem_bytes": host_cg.mem_bytes if host_cg else None,
            "cgroup_pids": host_cg.pids if host_cg else None,
            "n_procs_scanned": len(procs),
            "n_worker_roots": sum(len(v) for v in roots.values()),
        }]

        for wid in sorted(set(roots) | set(claims)):
            rpids = roots.get(wid, [])
            tm = probes.tree_metrics(procs, cmap, rpids, proc_root=cfg.proc_root,
                                     want_rss=cfg.want_rss, want_io=cfg.want_io,
                                     want_sockets=cfg.want_sockets)
            cpu_s = float(tm["cpu_s"])
            prev = self._prev_cpu.get(wid)
            prev_ts = self._prev_ts.get(wid)
            if prev is None or prev_ts is None or now <= prev_ts:
                delta, basis = None, "warmup"
            else:
                delta, basis = max(0.0, cpu_s - prev), "prev"
            io_now = tm.get("io_syscalls")
            prev_io = self._prev_io.get(wid)
            if io_now is None or prev_io is None:
                io_delta, io_basis = None, "warmup"
            else:
                io_delta, io_basis = max(0, int(io_now) - int(prev_io)), "prev"
            if io_now is not None:
                self._prev_io[wid] = int(io_now)
            self._prev_cpu[wid] = cpu_s
            self._prev_ts[wid] = now

            ci = claims.get(wid, ClaimInfo())
            v2, v1 = (None, None)
            if rpids:
                v2, v1 = probes.cgroup_of_pid(rpids[0], cfg.proc_root)
            cg_path = v2 if backend_host == "cgroup_v2" else v1
            wcg = cg(cg_path) if cg_path else None

            records.append({
                "kind": "worker",
                "ts": iso(now),
                "ts_epoch": now,
                "worker_id": wid,
                "backend": wcg.backend if wcg else "proc",
                "pids": int(tm["pids"]),
                "root_pids": rpids,
                "cpu_s": round(cpu_s, 6),
                "cpu_delta_s": None if delta is None else round(delta, 6),
                "delta_basis": basis,
                "mem_bytes": wcg.mem_bytes if wcg and wcg.mem_bytes is not None else None,
                "mem_source": (f"{wcg.backend}" if (wcg and wcg.mem_bytes is not None)
                               else "proc_rss"),
                "rss_bytes": int(tm["rss_bytes"]) if cfg.want_rss else None,
                "cgroup_path": cg_path,
                "cgroup_cpu_s": round(wcg.cpu_s, 6) if wcg and wcg.cpu_s is not None else None,
                "cgroup_pids": wcg.pids if wcg else None,
                "io_syscalls": int(io_now) if io_now is not None else None,
                "io_delta_syscalls": io_delta,
                "io_basis": io_basis,
                "io_bytes": tm.get("io_bytes"),
                "sockets": tm.get("sockets"),
                "claim_active": bool(ci.claim_active),
                "claim_ids": ci.claim_ids,
                "heartbeat_age_s": ci.heartbeat_age_s,
                "worker_hb_age_s": ci.worker_hb_age_s,
                "db_state": ci.db_state,
                "claim_source": ci.source,
            })

        dt = time.monotonic() - t0
        records[0]["scan_s"] = round(dt, 3)
        return records


# ── field completeness ────────────────────────────────────────────────────────

def completeness(records: Sequence[Dict[str, object]],
                 fields: Sequence[str] = REQUIRED_WORKER_FIELDS,
                 conditional: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Field-presence audit: warm-up records (``delta_basis=warmup``) are reported apart.

    ``rate`` denominator = steady-state worker records only; a warm-up sample has no
    predecessor so ``cpu_delta_s`` is structurally absent, not missing.
    """
    steady = [r for r in records if r.get("kind") == "worker" and r.get("delta_basis") != "warmup"]
    warm = [r for r in records if r.get("kind") == "worker" and r.get("delta_basis") == "warmup"]
    conditional = CONDITIONAL_REQUIRED if conditional is None else conditional
    per_field: Dict[str, int] = {f: 0 for f in fields}
    missing: Dict[str, int] = {}
    applicable: Dict[str, int] = {}
    for f in fields:
        miss = sum(1 for r in steady if r.get(f) is None)
        missing[f] = miss
        per_field[f] = len(steady) - miss
        applicable[f] = len(steady)
    for f, applies in conditional.items():
        rows = [r for r in steady if applies(r)]
        miss = sum(1 for r in rows if r.get(f) is None)
        if miss:
            missing[f] = miss
        applicable[f] = len(rows)
        per_field[f] = len(rows) - miss
    total_cells = sum(applicable.values())
    present = sum(per_field.values())
    cg_cpu = sum(1 for r in steady if r.get("cgroup_cpu_s") is not None)
    cg_mem = sum(1 for r in steady if r.get("mem_bytes") is not None)
    return {
        "cgroup_cpu_coverage": (cg_cpu / len(steady)) if steady else 0.0,
        "cgroup_memory_coverage": (cg_mem / len(steady)) if steady else 0.0,
        "steady_records": len(steady),
        "warmup_records": len(warm),
        "fields": list(fields) + [f for f in conditional if f not in fields],
        "applicable_per_field": applicable,
        "present_per_field": per_field,
        "missing_per_field": {k: v for k, v in missing.items() if v},
        "rate": (present / total_cells) if total_cells else 0.0,
        "fully_complete": all(v == 0 for v in missing.values()),
    }


# ── writer / CLI ──────────────────────────────────────────────────────────────

def serialize(records: Sequence[Dict[str, object]]) -> List[str]:
    """Canonical JSONL lines — the single serialization used by both dry-run and commit.

    Field-by-field equality of dry-run vs commit is a machine criterion, so both paths must
    go through this function (no sorting/formatting divergence possible).
    """
    return [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records]


def run_ticks(sampler: Sampler, ticks: int, interval_s: float,
              out_path: Optional[str] = None, dry_run: bool = False) -> List[str]:
    """Sample *ticks* times; return the serialized lines. Writes only when not dry-run."""
    lines: List[str] = []
    fh = None
    if not dry_run and out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        fh = open(out_path, "a", encoding="utf-8")
    try:
        for i in range(ticks):
            t_start = time.monotonic()
            recs = sampler.tick()
            batch = serialize(recs)
            lines.extend(batch)
            if fh:
                fh.write("\n".join(batch) + "\n")
                fh.flush()
            if dry_run:
                for ln in batch[:2]:
                    print(ln)
            if i + 1 < ticks:
                time.sleep(max(0.0, interval_s - (time.monotonic() - t_start)))
    finally:
        if fh:
            fh.close()
    return lines


def run_until(sampler: Sampler, duration_s: float, interval_s: float,
              out_path: Optional[str] = None, dry_run: bool = False) -> int:
    """Wall-clock loop: sample every *interval_s* until *duration_s* elapsed."""
    t_end = time.monotonic() + duration_s
    fh = None
    if not dry_run and out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        fh = open(out_path, "a", encoding="utf-8")
    n = 0
    try:
        while time.monotonic() < t_end:
            t_start = time.monotonic()
            batch = serialize(sampler.tick())
            if fh:
                fh.write("\n".join(batch) + "\n")
                fh.flush()
            else:
                print(batch[0])
            n += 1
            time.sleep(max(0.0, interval_s - (time.monotonic() - t_start)))
    finally:
        if fh:
            fh.close()
    return n


def emit_ticks_file(path: str, out_path: Optional[str], dry_run: bool) -> List[str]:
    """Re-serialize a recorded tick stream — the dry-run==commit oracle."""
    with open(path, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    lines = serialize(recs)
    if not dry_run and out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        for ln in lines[:2]:
            print(ln)
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="read-only worker occupancy sampler")
    ap.add_argument("--out", default="runs/in23_n08/samples.jsonl")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0.0, help="seconds (0 = use --ticks)")
    ap.add_argument("--ticks", type=int, default=0)
    ap.add_argument("--claims", choices=["db", "file", "none"], default="db")
    ap.add_argument("--claims-file", help="JSON claim snapshot, or a directory of <worker>.json")
    ap.add_argument("--marker", default="tp_worker.py",
                    help="cmdline marker identifying a worker root process")
    ap.add_argument("--workers-from-db", action="store_true",
                    help="also enumerate workers registered in factory_resident_workers")
    ap.add_argument("--scope-out", help="write the cgroup scoping report here (one shot)")
    ap.add_argument("--ticks-file", help="re-serialize a recorded stream (dry==commit oracle)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.ticks_file:
        emit_ticks_file(args.ticks_file, args.out, args.dry_run)
        return 0

    known: List[str] = []
    if args.workers_from_db:
        try:
            for line in _psql("SELECT worker_id FROM factory_resident_workers WHERE enabled").splitlines():
                if line.strip():
                    known.append(line.strip())
        except Exception as exc:  # pragma: no cover - DB optional
            print(f"[warn] worker enumeration from DB failed: {exc}", file=sys.stderr)

    cfg = SamplerConfig(interval_s=args.interval, known_workers=known,
                        worker_marker=args.marker)
    sampler = Sampler(cfg, make_claim_source(args.claims, args.claims_file))

    if args.scope_out:
        procs = probes.scan_procs(cfg.proc_root)
        cmap = probes.children_map(procs)
        roots = probes.worker_roots(procs, marker=cfg.worker_marker)
        rep = probes.cgroup_scope_report(procs, roots, cmap, sysroot=cfg.sysroot,
                                        proc_root=cfg.proc_root)
        rep["ts"] = iso(time.time())
        if not args.dry_run:
            os.makedirs(os.path.dirname(os.path.abspath(args.scope_out)), exist_ok=True)
            with open(args.scope_out, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=2, sort_keys=True)
        print(json.dumps({k: v for k, v in rep.items() if k != "per_worker"}, sort_keys=True))

    if args.duration > 0:
        n = run_until(sampler, args.duration, args.interval, args.out, args.dry_run)
    else:
        n = len(run_ticks(sampler, max(1, args.ticks), args.interval, args.out, args.dry_run))
    print(f"[sampler] ticks={n} out={'(dry)' if args.dry_run else args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())