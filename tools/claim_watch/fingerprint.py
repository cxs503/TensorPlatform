#!/usr/bin/env python3
"""Environment fingerprint: the portable half of a claim's provenance.

Why this module exists
----------------------
An artifact hash pins *bytes*; it says nothing about whether those bytes mean
the same thing somewhere else. A number produced on ``loongarch64`` with one
BLAS build and re-read on ``x86_64`` with another is not the same number, and a
ledger that cannot tell the two apart silently promotes cross-architecture
copies to REAL. The fingerprint makes the environment part of the claim:

    fingerprint = {arch, arch_family, platform, byteorder, pointer_bits,
                   python, packages{name: version}, digest}

``digest`` is a sha256 over the canonical JSON of every other field, so one
string comparison answers "same environment?" and the individual fields answer
"why not?".

Two failure classes, deliberately separated
-------------------------------------------
``ARCH_MISMATCH``   arch family / platform / byte order / pointer width differ.
                    The number cannot be reused as-is; the checker degrades the
                    claim to SUSPECT (evidence WITHHELD).
``RUNTIME_DRIFT``   same architecture, but interpreter or package versions
                    differ. Numerics may move by rounding; the checker degrades
                    one step (REAL -> MOCK, MOCK -> WITHHELD).

Absence of a recorded fingerprint is *not* a failure: legacy artifacts never
recorded one, and inventing a verdict there would be exactly the kind of
judgement this module exists to remove. It is reported as
``env_assurance="unasserted"``.

Zero domain vocabulary, no network, no mutation: the module reads interpreter
metadata and package versions only.
"""
from __future__ import annotations

import hashlib
import json
import platform as _platform
import struct
import sys
from importlib import metadata as _metadata
from typing import Any, Dict, Iterable, Mapping, Optional

SCHEMA = "claim-env/1.0"

# Distinct failure verdicts produced by compare().
MATCH = "MATCH"
ARCH_MISMATCH = "ARCH_MISMATCH"
RUNTIME_DRIFT = "RUNTIME_DRIFT"
UNASSERTED = "UNASSERTED"

# Fields whose difference means "different machine class".
_ARCH_FIELDS = ("arch_family", "platform", "byteorder", "pointer_bits")

_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "x64": "x86_64",
    "i386": "x86",
    "i686": "x86",
    "x86": "x86",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm32",
    "armv8l": "arm64",
    "loongarch64": "loongarch64",
    "loong64": "loongarch64",
    "riscv64": "riscv64",
    "ppc64le": "ppc64",
    "s390x": "s390x",
}


def arch_family(arch: Optional[str]) -> str:
    """Normalise a raw machine string to its portability family.

    Unknown strings are returned lower-cased so that two different unknown
    machines still compare unequal (fail closed, never assume portability).
    """
    if not arch:
        return "unknown"
    key = arch.strip().lower()
    return _ARCH_ALIASES.get(key, key)


def canonical(obj: Any) -> bytes:
    """Canonical JSON bytes: sorted keys, no whitespace, ASCII-safe."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest_fields(fields: Mapping[str, Any]) -> str:
    """sha256 over every field except ``digest`` itself."""
    payload = {k: v for k, v in fields.items() if k != "digest"}
    return hashlib.sha256(canonical(payload)).hexdigest()


def _package_versions(packages: Iterable[str]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for name in packages:
        try:
            out[name] = _metadata.version(name)
        except Exception:  # not installed / metadata unavailable
            out[name] = None
    return out


def env_fingerprint(
    packages: Iterable[str] = ("jsonschema",),
    *,
    arch: Optional[str] = None,
    platform_system: Optional[str] = None,
    byteorder: Optional[str] = None,
    pointer_bits: Optional[int] = None,
    python: Optional[str] = None,
    package_versions: Optional[Mapping[str, Optional[str]]] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a fingerprint.

    Every component can be injected, which is what makes the *tests* cheap and
    synthetic: a test can fabricate an x86_64 fingerprint on a loongarch host
    without any monkey patching of the interpreter.
    """
    raw_arch = arch if arch is not None else _platform.machine()
    fields: Dict[str, Any] = {
        "arch": raw_arch,
        "arch_family": arch_family(raw_arch),
        "platform": platform_system if platform_system is not None else sys.platform,
        "byteorder": byteorder if byteorder is not None else sys.byteorder,
        "pointer_bits": pointer_bits if pointer_bits is not None else struct.calcsize("P") * 8,
        "python": python if python is not None else _platform.python_version(),
        "packages": dict(
            package_versions if package_versions is not None else _package_versions(packages)
        ),
    }
    if extra:
        fields.update({k: v for k, v in extra.items() if k not in fields})
    fields["digest"] = digest_fields(fields)
    return fields


def compare(recorded: Optional[Mapping[str, Any]], current: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Compare a recorded fingerprint against the current environment.

    Returns ``{"verdict": MATCH|ARCH_MISMATCH|RUNTIME_DRIFT|UNASSERTED,
    "reasons": [...], "recorded_digest":..., "current_digest":...}``.
    ``UNASSERTED`` (no recorded fingerprint) is intentionally *not* a failure.
    """
    current = dict(current or env_fingerprint())
    if not recorded:
        return {
            "verdict": UNASSERTED,
            "reasons": ["no env_fingerprint recorded in claim"],
            "recorded_digest": None,
            "current_digest": current.get("digest"),
        }

    reasons = []
    rec_digest = recorded.get("digest")
    cur_digest = current.get("digest")
    if rec_digest and cur_digest and rec_digest == cur_digest:
        return {"verdict": MATCH, "reasons": [], "recorded_digest": rec_digest, "current_digest": cur_digest}

    arch_reasons = []
    for field in _ARCH_FIELDS:
        rv = recorded.get(field)
        if field == "arch_family" and not rv:
            rv = arch_family(recorded.get("arch"))
        cv = current.get(field)
        if rv is None or cv is None:
            continue
        if rv != cv:
            arch_reasons.append(f"{field}: recorded={rv!r} current={cv!r}")
    if recorded.get("arch") and current.get("arch") and arch_family(recorded["arch"]) != arch_family(current["arch"]):
        msg = f"arch: recorded={recorded['arch']!r} current={current['arch']!r}"
        if msg not in arch_reasons:
            arch_reasons.append(msg)

    if arch_reasons:
        return {
            "verdict": ARCH_MISMATCH,
            "reasons": arch_reasons,
            "recorded_digest": rec_digest,
            "current_digest": cur_digest,
        }

    # Same machine class: look for interpreter / package drift.
    if recorded.get("python") and current.get("python") and recorded["python"] != current["python"]:
        reasons.append(f"python: recorded={recorded['python']!r} current={current['python']!r}")
    rec_pkgs = recorded.get("packages") or {}
    cur_pkgs = current.get("packages") or {}
    for name in sorted(set(rec_pkgs) | set(cur_pkgs)):
        rv, cv = rec_pkgs.get(name), cur_pkgs.get(name)
        if rv != cv:
            reasons.append(f"packages.{name}: recorded={rv!r} current={cv!r}")
    if not reasons:
        # Same family, same interpreter, same packages: the only thing that can
        # still differ is the *spelling* of the machine string (x86_64 vs amd64).
        # That is a naming difference, not a portability difference, so it is
        # reported as a match with a note instead of a false drift alarm.
        rec_arch, cur_arch = recorded.get("arch"), current.get("arch")
        if rec_arch and cur_arch and rec_arch != cur_arch:
            return {
                "verdict": MATCH,
                "reasons": [f"arch spelling differs but family matches: {rec_arch!r} vs {cur_arch!r}"],
                "recorded_digest": rec_digest,
                "current_digest": cur_digest,
            }
        # Digest differs with no comparable field: fingerprints were made by
        # different producers. Fail closed rather than claiming equivalence we
        # cannot demonstrate.
        reasons.append("digest differs with no field-level difference visible (producer mismatch)")
    return {
        "verdict": RUNTIME_DRIFT,
        "reasons": reasons,
        "recorded_digest": rec_digest,
        "current_digest": cur_digest,
    }