"""Crash-bundle assembling: turn one fatal signal into a self-verifying evidence directory.

The long-run layer (:mod:`tools.longrun`) already *captures* an autopsy; this module
makes the capture **auditable as a directory**.  A crash produces

.. code-block:: text

    artifacts/crash_<UTC-stamp>/
        siginfo.json        signal number/name, si_code, fault address, sender uid
        registers.json      machine registers of the faulting thread (>=5 fields) + digest
        stack.txt           symbolized native frames, one per line, with addresses
        mappings.txt        the address-space map at fault time (ASLR picture)
        manifest.json       run-time snapshot (interpreter, numpy, argv, env, config,
                            checkpoint chain) at the moment of death
        supervise_report.json  survival-layer report (failure mode, resume state)
        stackcore.json      raw gdb evidence (tier-1 stackcore), byte copy
        SHA256SUMS          sha256 of every file in the bundle
        digest.json         bundle_digest = sha256 over canonical "name sha256" lines

Two invariants are what make it evidence rather than logs:

* **enumerated completeness** -- :func:`verify_bundle` re-hashes every file and
  reports which ones changed; a truncated/edited copy is detected, not trusted;
* **location independence** -- ``bundle_digest`` covers *file names + contents*,
  not paths or timestamps, so a bundle can be copied, re-frozen and re-verified
  elsewhere and still recompute to the same digest.

Field-count discipline: :func:`parse_registers` returns a dict, and the acceptance
criterion requires at least five registers.  If gdb cannot deliver them the bundle
is written with ``registers.json["sufficient"] = false`` -- an honest, machine
checkable degradation instead of a fabricated register file.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "survival.crash_bundle/1"

#: Env variables worth freezing in the manifest (they change numerics / threading).
ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "PYTHONHASHSEED",
    "PYTHONPATH",
    "CUDA_VISIBLE_DEVICES",
    "TENSOR_AGENT_USER",
)

_REG_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9]{1,7})\s+(0x[0-9a-fA-F]+|-?\d+)\b")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: os.PathLike | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# field extraction
# --------------------------------------------------------------------------- #
def parse_registers(text: str) -> Dict[str, str]:
    """Parse gdb ``info registers`` output into ``{name: value}``.

    Accepts the per-thread layout produced by ``thread apply all info registers``:
    register lines are recognised by ``name<space>0x...`` and thread headers are
    skipped.  Register names are validated against the textual form gdb emits, so
    a garbage line ("Thread 1 ...") never lands in the dict.
    """
    regs: Dict[str, str] = {}
    skip = {"thread", "warning", "reading", "switching", "done", "no", "info"}
    for line in (text or "").splitlines():
        m = _REG_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in skip:
            continue
        if len(regs) < 64 and name not in regs:
            regs[name] = m.group(2)
    return regs


def _mappings_section(text: str) -> str:
    """Return the ``Mapped address spaces`` block of the gdb transcript."""
    lines = (text or "").splitlines()
    out: List[str] = []
    inside = False
    for line in lines:
        if line.strip().lower().startswith("mapped address spaces"):
            inside = True
            continue
        if inside:
            if not line.strip():
                if out:
                    break
                continue
            out.append(line)
    return "\n".join(out)


def _stack_lines(frames: Sequence[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for i, frame in enumerate(frames):
        func = frame.get("function") or frame.get("symbol") or "??"
        loc = frame.get("file") or frame.get("module") or "??"
        line = frame.get("line")
        addr = frame.get("address") or frame.get("pc") or "0x0"
        out.append(f"#{i:<2} {addr} in {func} ({loc}{':' + str(line) if line else ''})")
    return out


def _threads_section(stackcore_text: str) -> str:
    """``threads_and_backtrace`` minus the mappings block (kept as stack context)."""
    text = stackcore_text or ""
    idx = text.lower().find("mapped address spaces")
    if idx > 0:
        text = text[:idx]
    return text


def checkpoint_chain_digest(ckpt_dir: os.PathLike | str) -> Optional[Dict[str, Any]]:
    """Digest of the checkpoint chain (step -> manifest digest) found in ``ckpt_dir``."""
    root = Path(ckpt_dir)
    if not root.exists():
        return None
    entries: List[Dict[str, Any]] = []
    for p in sorted(root.glob("ckpt_*.json")):
        try:
            man = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append({"file": p.name, "step": man.get("step"), "digest": man.get("digest")})
    if not entries:
        return None
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {"n_checkpoints": len(entries), "chain_digest": sha256_bytes(payload.encode()), "entries": entries}


def manifest_snapshot(
    cfg: Optional[Dict[str, Any]] = None,
    ckpt_dir: Optional[os.PathLike | str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Freeze everything needed to interpret the crash later."""
    import numpy as np  # local import: manifest is only built on the crash path

    env = {k: os.environ[k] for k in ENV_KEYS if k in os.environ}
    snap: Dict[str, Any] = {
        "schema": "survival.manifest/1",
        "created_unix": time.time(),
        "pid": os.getpid(),
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "numpy": np.__version__,
        "platform": {"system": platform.system(), "machine": platform.machine(),
                     "release": platform.release()},
        "argv": list(sys.argv)[:24],
        "cwd": os.getcwd(),
        "env": env,
        "env_digest": sha256_bytes(json.dumps(env, sort_keys=True).encode()),
        "cfg_digest": sha256_bytes(
            json.dumps(cfg or {}, sort_keys=True, separators=(",", ":"), default=str).encode()
        ),
        "cpu_count": os.cpu_count(),
    }
    chain = checkpoint_chain_digest(ckpt_dir) if ckpt_dir is not None else None
    snap["checkpoint_chain"] = chain
    if extra:
        snap["extra"] = extra
    snap["digest"] = sha256_bytes(
        json.dumps({k: v for k, v in snap.items() if k != "digest"},
                   sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    return snap


# --------------------------------------------------------------------------- #
# bundle construction / verification
# --------------------------------------------------------------------------- #
def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def build_bundle(
    supervise_report: Dict[str, Any],
    stackcore: Dict[str, Any],
    outroot: os.PathLike | str,
    cfg: Optional[Dict[str, Any]] = None,
    ckpt_dir: Optional[os.PathLike | str] = None,
    stamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the crash bundle directory from a survival-layer report + raw stackcore."""
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bundle_dir = Path(outroot) / f"crash_{stamp}"
    bundle_dir.mkdir(parents=True, exist_ok=False)

    raw_text = stackcore.get("threads_and_backtrace") or ""
    frames = stackcore.get("frames") or supervise_report.get("core", {}).get("native_bt") or []
    regs = parse_registers(raw_text)
    siginfo = dict(stackcore.get("siginfo") or {})
    if not siginfo:
        siginfo = {
            "signal": supervise_report.get("term_signal"),
            "signal_name": (supervise_report.get("core") or {}).get("status"),
        }
    signal_no = int(supervise_report.get("term_signal") or siginfo.get("si_signo") or 0)

    _write(bundle_dir / "siginfo.json",
           json.dumps({"signal": signal_no,
                       "signal_name": supervise_report.get("signal_name"),
                       "failure_mode": supervise_report.get("failure_mode"),
                       "siginfo": siginfo,
                       "exit_code": supervise_report.get("exit_code"),
                       "classification_reasons": supervise_report.get("classification_reasons")},
                      indent=2, sort_keys=True))
    _write(bundle_dir / "registers.json",
           json.dumps({"n_fields": len(regs), "sufficient": len(regs) >= 5,
                       "registers": regs,
                       "raw_digest": sha256_bytes(raw_text.encode())}, indent=2, sort_keys=True))
    _write(bundle_dir / "stack.txt",
           "\n".join(_stack_lines(frames) + ["", "-- gdb transcript --", _threads_section(raw_text)]))
    _write(bundle_dir / "mappings.txt", _mappings_section(raw_text))
    manifest = manifest_snapshot(cfg=cfg, ckpt_dir=ckpt_dir,
                                 extra={"failure_mode": supervise_report.get("failure_mode"),
                                        "resume": {"resumed_from_step": supervise_report.get("child", {}).get("resumed_from_step"),
                                                   "result_digest": supervise_report.get("child", {}).get("result_digest"),
                                                   "final_checkpoint_digest": supervise_report.get("child", {}).get("final_checkpoint_digest")}})
    _write(bundle_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str))
    slim_report = {k: v for k, v in supervise_report.items()
                   if k not in {"core"}}
    slim_report["core_summary"] = {
        "status": (supervise_report.get("core") or {}).get("status"),
        "n_native_frames": (supervise_report.get("core") or {}).get("n_native_frames"),
    }
    _write(bundle_dir / "supervise_report.json",
           json.dumps(slim_report, indent=2, sort_keys=True, default=str))
    _write(bundle_dir / "stackcore.json", json.dumps(stackcore, indent=2, sort_keys=True, default=str))

    files = sorted(p for p in bundle_dir.iterdir() if p.is_file())
    sums = "".join(f"{sha256_file(p)}  {p.name}\n" for p in files)
    _write(bundle_dir / "SHA256SUMS", sums)
    digest = verify_bundle(bundle_dir)
    _write(bundle_dir / "digest.json", json.dumps(digest, indent=2, sort_keys=True))
    return {
        "schema": SCHEMA,
        "bundle_dir": str(bundle_dir),
        "bundle_digest": digest["bundle_digest"],
        "n_files": digest["n_files"],
        "register_fields": len(regs),
        "registers_sufficient": len(regs) >= 5,
        "frame_lines": len(_stack_lines(frames)),
        "signal": signal_no,
        "signal_name": supervise_report.get("signal_name"),
        "failure_mode": supervise_report.get("failure_mode"),
        "manifest_digest": manifest["digest"],
        "mappings_bytes": (bundle_dir / "mappings.txt").stat().st_size,
        "verify": digest,
    }


def verify_bundle(bundle_dir: os.PathLike | str) -> Dict[str, Any]:
    """Re-hash a bundle against its SHA256SUMS; recompute the location-free digest."""
    root = Path(bundle_dir)
    sums_path = root / "SHA256SUMS"
    if not sums_path.exists():
        raise FileNotFoundError(f"SHA256SUMS missing in {root}")
    recorded: Dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        recorded[name.strip()] = digest.strip()
    mismatched: List[str] = []
    missing: List[str] = []
    for name, expected in sorted(recorded.items()):
        p = root / name
        if not p.exists():
            missing.append(name)
            continue
        if sha256_file(p) != expected:
            mismatched.append(name)
    payload = "\n".join(f"{name} {recorded[name]}" for name in sorted(recorded))
    return {
        "schema": "survival.crash_bundle.verify/1",
        "ok": not mismatched and not missing,
        "n_files": len(recorded),
        "bundle_digest": sha256_bytes(payload.encode()),
        "mismatched": mismatched,
        "missing": missing,
        "files": sorted(recorded),
    }


def dump_crash(
    workdir: os.PathLike | str,
    outroot: os.PathLike | str,
    cfg: Optional[Dict[str, Any]] = None,
    crash_mode: str = "segv",
    at_step: int = 12,
    steps: int = 30,
    checkpoint_every: int = 5,
    stamp: Optional[str] = None,
    supervise_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a crashing job under the survival layer and assemble its crash bundle.

    ``supervise_fn`` is injectable so tests can exercise the assembly path with a
    synthetic report (no ptrace needed) while production uses
    :func:`tools.longrun.supervise.supervise`.
    """
    from tools.longrun import supervise as supervise_mod  # local: keep import cost off happy path

    cfg = dict(cfg or {"job": {"grid": 24}, "steps": steps,
                       "checkpoint_every": checkpoint_every,
                       "forensics": {"stack_interval_s": 0.5}})
    cfg.setdefault("crash", {"at_step": at_step, "mode": crash_mode})
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    runner = supervise_fn or supervise_mod.supervise
    report = runner(cfg, workdir=workdir, job_id=f"crash_{crash_mode}", full_registers=True)
    core = report.get("core") or {}
    stackcore: Dict[str, Any] = {}
    raw_path = core.get("path")
    if raw_path and Path(raw_path).exists():
        try:
            stackcore = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stackcore = {}
    if not stackcore:
        stackcore = {"threads_and_backtrace": "", "frames": core.get("native_bt") or [],
                     "siginfo": {}}
    ckpt_dir = (report.get("artifacts") or {}).get("checkpoints") or (workdir / "work" / "checkpoints")
    bundle = build_bundle(report, stackcore, outroot, cfg=cfg, ckpt_dir=ckpt_dir, stamp=stamp)
    bundle["supervise_report_path"] = report.get("report_path")
    bundle["failure_mode"] = report.get("failure_mode")
    return bundle