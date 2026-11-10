#!/usr/bin/env python3
"""Evidence archiving: sha256 + environment fingerprint + dependency set + chain.

Every verification run is frozen into an *evidence bundle* (a directory)::

    <bundle>/
        manifest.json          # the ledger (this is what makes the bundle checkable)
        registry.json          # snapshot of the case declarations that were run
        report.json            # machine-readable results / error table
        error_table.md         # human-readable mirror of the same numbers
        cases/<case_id>.json   # one record per case (inputs + outputs + metrics)

``manifest.json`` records, for **every** artifact, its relative path, role,
size and ``sha256``; the payloads are chained so that entry *i* depends on
entry *i-1* (``chain_head`` is the end of the chain) and a ``root_digest``
covers the whole list.  A per-case record additionally embeds the
``environment_digest``, so "which machine / which dependency set produced this
number" travels with the number itself instead of living in a separate report.

:func:`verify_bundle` recomputes everything and classifies tampering:

===================  ==========================================================
``CONTENT``          a recorded file's bytes changed (sha256 mismatch)
``STRUCTURAL``       a recorded file vanished, or an unrecorded file appeared
``CHAIN``            an entry's chain hash / the head / the root digest is broken
===================  ==========================================================

Limitation (honest): the chain is unkeyed, so an attacker who rewrites *both* a
payload and the whole manifest consistently cannot be detected by hashing alone.
That is why the bundle also carries the environment fingerprint and why the
audit artifacts are committed next to the code — the digest chain proves
*internal* consistency and byte-level tampering, not authorship.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import SCHEMA_VERSION, _jsonable
from .registry import CaseRegistry
from .runner import RunReport, write_run_report

MANIFEST_NAME = "manifest.json"
CASES_DIRNAME = "cases"

# Tamper classes reported by verify_bundle.
TAMPER_CONTENT = "CONTENT"
TAMPER_STRUCTURAL = "STRUCTURAL"
TAMPER_CHAIN = "CHAIN"

# Packages whose versions are recorded by default when present in the env.
DEFAULT_DEPENDENCY_PROBE = ("numpy", "scipy", "pandas", "matplotlib", "pytest")


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------
def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(payload: Any) -> str:
    """Stable JSON text used for every digest (sorted keys, no trailing newline)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def chain_hash(previous: str, rel_path: str, content_sha256: str) -> str:
    """Link entry ``rel_path`` to the previous chain value."""
    return sha256_text(f"{previous}|{rel_path}|{content_sha256}")


def root_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    joined = "\n".join(f"{e['path']}:{e['sha256']}" for e in entries)
    return sha256_text(joined)


# ---------------------------------------------------------------------------
# Environment fingerprint / dependency set
# ---------------------------------------------------------------------------
def dependency_versions(names: Iterable[str]) -> Dict[str, str]:
    """Installed version per package (``"ABSENT"`` when not installed)."""
    out: Dict[str, str] = {}
    for name in sorted({str(n) for n in names}):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = "ABSENT"
        except Exception:  # pragma: no cover - broken metadata must not kill an audit
            out[name] = "ERROR"
    return out


def environment_fingerprint(dependencies: Iterable[str] = DEFAULT_DEPENDENCY_PROBE) -> Dict[str, Any]:
    """Reproducibility fingerprint of the executing environment.

    Deliberately volatile fields (hostname, wall clock, load) are excluded so
    that the fingerprint is stable across runs on the same machine and its
    digest can be compared across artifacts.
    """
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "byteorder": sys.byteorder,
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "dependencies": dependency_versions(dependencies),
    }


def environment_digest(fingerprint: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(dict(fingerprint)))


# ---------------------------------------------------------------------------
# Bundle writer
# ---------------------------------------------------------------------------
def _safe_name(case_id: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in case_id) or "case"


def case_record(registry: CaseRegistry, result: Any, env_digest: str) -> Dict[str, Any]:
    """Per-case evidence record: declaration + inputs + outputs + metrics + env."""
    spec = registry.get(result.case_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "case": spec.to_dict(),
        "result": result.to_dict(),
        "environment_digest": env_digest,
    }


def write_bundle(registry: CaseRegistry,
                 report: RunReport,
                 out_dir: Path | str, *,
                 dependencies: Iterable[str] = DEFAULT_DEPENDENCY_PROBE,
                 stamp: bool = False) -> Dict[str, Any]:
    """Freeze registry + report into ``out_dir`` and return the manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CASES_DIRNAME).mkdir(parents=True, exist_ok=True)

    fingerprint = environment_fingerprint(dependencies)
    if stamp:
        fingerprint["generated_at"] = report.generated_at
    env_digest = environment_digest(fingerprint)

    artifacts: List[Tuple[str, str, bytes]] = []

    registry_payload = registry.to_dict()
    artifacts.append(("registry.json", "registry",
                      (json.dumps(registry_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")))

    written = write_run_report(report, out_dir)
    artifacts.append(("report.json", "report", written["report"].read_bytes()))
    artifacts.append(("error_table.md", "error_table", written["error_table"].read_bytes()))

    for result in report.results:
        record = case_record(registry, result, env_digest)
        # The declared tolerance / inputs of the case are part of the record, so
        # the numbers can be re-derived from the bundle alone.
        payload = (json.dumps({**_jsonable(record)}, indent=2, sort_keys=True) + "\n").encode("utf-8")
        artifacts.append((f"{CASES_DIRNAME}/{_safe_name(result.case_id)}.json", "case_record", payload))

    entries: List[Dict[str, Any]] = []
    previous = env_digest
    for rel_path, role, payload in sorted(artifacts, key=lambda item: item[0]):
        target = out_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        digest = sha256_bytes(payload)
        link = chain_hash(previous, rel_path, digest)
        entries.append({
            "path": rel_path,
            "role": role,
            "size": len(payload),
            "sha256": digest,
            "chain_hash": link,
        })
        previous = link

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "environment": fingerprint,
        "environment_digest": env_digest,
        "counts": report.counts,
        "domains": {k: int(v) for k, v in sorted(report.domains.items())},
        "entries": entries,
        "root_digest": root_digest(entries),
        "chain_head": previous,
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Bundle verifier (tamper detection)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BundleVerdict:
    """Result of re-hashing an evidence bundle."""

    bundle: str
    ok: bool
    checked_entries: int = 0
    missing: Tuple[str, ...] = ()
    content_tampered: Tuple[str, ...] = ()
    chain_broken: Tuple[str, ...] = ()
    extra: Tuple[str, ...] = ()
    root_digest_ok: bool = True
    environment_match: bool = True
    environment_diff: Mapping[str, Any] = field(default_factory=dict)
    errors: Tuple[str, ...] = ()

    @property
    def tamper_classes(self) -> Tuple[str, ...]:
        found = []
        if self.content_tampered:
            found.append(TAMPER_CONTENT)
        if self.missing or self.extra:
            found.append(TAMPER_STRUCTURAL)
        if self.chain_broken or not self.root_digest_ok:
            found.append(TAMPER_CHAIN)
        return tuple(found)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle": self.bundle,
            "ok": self.ok,
            "checked_entries": self.checked_entries,
            "missing": list(self.missing),
            "content_tampered": list(self.content_tampered),
            "chain_broken": list(self.chain_broken),
            "extra": list(self.extra),
            "root_digest_ok": self.root_digest_ok,
            "environment_match": self.environment_match,
            "environment_diff": dict(self.environment_diff),
            "tamper_classes": list(self.tamper_classes),
            "errors": list(self.errors),
        }


def verify_bundle(out_dir: Path | str) -> BundleVerdict:
    """Recompute every digest of a bundle; fail-closed on anything unexpected."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return BundleVerdict(bundle=str(out_dir), ok=False,
                             errors=(f"missing {MANIFEST_NAME}",))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return BundleVerdict(bundle=str(out_dir), ok=False,
                             errors=(f"manifest is not valid JSON: {exc}",))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return BundleVerdict(bundle=str(out_dir), ok=False,
                             errors=(f"schema_version {manifest.get('schema_version')!r} != {SCHEMA_VERSION!r}",))

    entries = manifest.get("entries") or []
    env_digest = manifest.get("environment_digest") or ""
    missing: List[str] = []
    tampered: List[str] = []
    chain_broken: List[str] = []
    recorded: Dict[str, Dict[str, Any]] = {}

    previous = env_digest
    for entry in entries:
        rel_path = str(entry.get("path"))
        recorded[rel_path] = entry
        target = out_dir / rel_path
        if not target.is_file():
            missing.append(rel_path)
            previous = str(entry.get("chain_hash") or "")
            continue
        digest = sha256_file(target)
        if digest != entry.get("sha256"):
            tampered.append(rel_path)
        if chain_hash(previous, rel_path, str(entry.get("sha256"))) != entry.get("chain_hash"):
            chain_broken.append(rel_path)
        if int(entry.get("size", -1)) != target.stat().st_size:
            if rel_path not in tampered:
                tampered.append(rel_path)
        previous = str(entry.get("chain_hash") or "")

    head_ok = previous == manifest.get("chain_head") if entries else True
    root_ok = root_digest(entries) == manifest.get("root_digest") if entries else True

    on_disk = {p.relative_to(out_dir).as_posix()
               for p in out_dir.rglob("*") if p.is_file()}
    extra = tuple(sorted(on_disk - set(recorded) - {MANIFEST_NAME}))

    recomputed = environment_digest(manifest.get("environment") or {})
    environment_match = recomputed == env_digest
    environment_diff: Dict[str, Any] = {}
    if not environment_match:
        current = environment_fingerprint((manifest.get("environment") or {}).get("dependencies", {}) or {})
        for key in sorted(set(current) | set(manifest.get("environment") or {})):
            if current.get(key) != (manifest.get("environment") or {}).get(key):
                environment_diff[key] = {"recorded": (manifest.get("environment") or {}).get(key),
                                         "current": current.get(key)}

    # ``ok`` judges *integrity* only: a bundle carried to another machine keeps a
    # valid digest chain, so reproducibility is reported separately instead of
    # being folded into a tamper verdict.
    ok = (not missing and not tampered and not chain_broken and not extra
          and root_ok and head_ok)

    return BundleVerdict(
        bundle=str(out_dir),
        ok=ok,
        checked_entries=len(entries),
        missing=tuple(sorted(missing)),
        content_tampered=tuple(sorted(tampered)),
        chain_broken=tuple(sorted(chain_broken)),
        extra=extra,
        root_digest_ok=bool(root_ok and head_ok),
        environment_match=bool(environment_match),
        environment_diff=environment_diff,
        errors=(),
    )


def verification_markdown(verdict: BundleVerdict) -> str:
    """Short human-readable adjudication of a bundle."""
    lines = [
        "# Evidence bundle integrity",
        "",
        f"bundle: `{verdict.bundle}`",
        f"checked entries: {verdict.checked_entries}",
        f"verdict: **{'OK' if verdict.ok else 'TAMPERED / INCONSISTENT'}**",
        "",
        f"- tamper classes: {', '.join(verdict.tamper_classes) or 'none'}",
        f"- content mismatches: {list(verdict.content_tampered)}",
        f"- missing artifacts: {list(verdict.missing)}",
        f"- unrecorded artifacts: {list(verdict.extra)}",
        f"- chain breaks: {list(verdict.chain_broken)}",
        f"- root/head digest ok: {verdict.root_digest_ok}",
        f"- environment reproducible on this host: {verdict.environment_match}",
    ]
    if verdict.environment_diff:
        lines.append(f"- environment diff: {json.dumps(verdict.environment_diff, sort_keys=True)}")
    if verdict.errors:
        lines.append(f"- errors: {list(verdict.errors)}")
    lines.append("")
    return "\n".join(lines)