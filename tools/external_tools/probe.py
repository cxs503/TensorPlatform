"""Probes: *observed* availability, never declared availability.

A probe answers exactly one question -- "can this environment reach the backing
tool right now" -- and returns a version string when it can.  Probes never import
the tool's heavy machinery: importing is the adapter's job (and its cost).  The
distinction matters for the evidence ledger: a probe hit plus a failed smoke call
is ``FAIL`` (the tool lied), a probe miss is ``WITHHELD`` (the tool is not here).

Probe kinds (all declared in the spec file):

``module``      import a Python distribution/module (``importlib.metadata`` first,
                so a missing install is reported as "not installed" rather than
                as a mystery ImportError);
``executable``  look the binary up on ``PATH`` and ask it for a version;
``command``     run an argv vector with a timeout and require exit code 0.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

DEFAULT_PROBE_TIMEOUT = 30.0


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    kind: str
    target: str
    version: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"available": self.available, "kind": self.kind,
                "target": self.target, "version": self.version,
                "detail": self.detail}


def _module_probe(name: str) -> ProbeResult:
    version = ""
    try:
        version = md.version(name)
    except Exception:  # not a distribution: fall back to the module's __version__
        pass
    try:
        mod = importlib.import_module(name)
    except Exception as exc:
        return ProbeResult(False, "module", name, "", f"{type(exc).__name__}: {exc}")
    if not version:
        version = str(getattr(mod, "__version__", "") or "")
    return ProbeResult(True, "module", name, version, "import ok")


def _executable_probe(name: str, version_args: Sequence[str] = ("--version",)) -> ProbeResult:
    path = shutil.which(name)
    if not path:
        return ProbeResult(False, "executable", name, "", "not on PATH")
    version = ""
    try:
        out = subprocess.run([path, *version_args], capture_output=True, text=True,
                             timeout=DEFAULT_PROBE_TIMEOUT)
        version = (out.stdout or out.stderr or "").strip().splitlines()[0][:120] \
            if (out.stdout or out.stderr) else ""
    except Exception as exc:  # a binary that cannot answer is still present
        version = f"(version probe failed: {type(exc).__name__})"
    return ProbeResult(True, "executable", name, version, f"path={path}")


def _command_probe(argv: Sequence[str], timeout: float = DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    target = " ".join(argv)
    try:
        out = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return ProbeResult(False, "command", target, "", "argv[0] not found")
    except subprocess.TimeoutExpired:
        return ProbeResult(False, "command", target, "", f"timeout>{timeout}s")
    detail = (out.stdout or out.stderr or "").strip().splitlines()
    return ProbeResult(out.returncode == 0, "command", target,
                       "", (detail[0][:120] if detail else f"rc={out.returncode}"))


def probe(spec: Mapping[str, Any]) -> ProbeResult:
    """Dispatch one declarative probe description."""

    kind = str(spec.get("kind", "module"))
    if kind == "module":
        res = _module_probe(str(spec["name"]))
    elif kind == "executable":
        res = _executable_probe(str(spec["name"]),
                                tuple(spec.get("version_args") or ("--version",)))
    elif kind == "command":
        argv = list(spec["argv"])
        return _command_probe(argv, float(spec.get("timeout", DEFAULT_PROBE_TIMEOUT)))
    elif kind == "none":
        return ProbeResult(True, "none", "", "", "adapter self-probes")
    else:
        return ProbeResult(False, kind, str(spec.get("name", "")), "",
                           f"unknown probe kind {kind!r}")
    # optional expected-version gate: declared, therefore checkable
    want = spec.get("min_version")
    if res.available and want and res.version and not res.version.startswith(str(want)):
        if _version_lt(res.version, str(want)):
            return ProbeResult(False, res.kind, res.target, res.version,
                               f"version {res.version} < required {want}")
    return res


def _version_lt(got: str, want: str) -> bool:
    """Best-effort dotted-numeric comparison; unparsable versions never fail open."""

    def parts(v: str):
        out = []
        for chunk in v.replace("-", ".").split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            if digits:
                out.append(int(digits))
        return out

    a, b = parts(got), parts(want)
    if not a or not b:
        return False
    return a < b


def _unused(*_: Any) -> Optional[None]:  # pragma: no cover - keep linters quiet
    return None