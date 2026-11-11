"""Report body / metadata four-way binding with sha256 content fingerprints.

A published report is an accounting artefact: its body, its metadata, its
report id and its mission id must all describe the same thing.  The gate binds
all four with content fingerprints and treats every mismatch or duplicate as a
**ledger defect**, which makes the gate fail closed:

    report_id  <->  mission_id  <->  body_sha256  <->  metadata_sha256

The two hashes are declared *outside* the metadata (in the storage envelope /
row), so metadata cannot certify itself.  Body normalisation (CRLF -> LF,
trailing whitespace stripped, outer blank lines removed) makes a cosmetic
re-save idempotent while still detecting any content edit.

Defect codes
------------
``EMPTY_BODY``               body is blank
``MISSING_FINGERPRINT``      a declared hash is absent or not a sha256
``DECLARED_BODY_MISMATCH``   declared body hash != recomputed body hash
``DECLARED_METADATA_MISMATCH`` declared metadata hash != recomputed metadata hash
``ID_MISMATCH``              metadata report/mission id != envelope ids
``DUPLICATE_BODY``           same body hash under two different report ids
``DUPLICATE_REPORT_ID``      one report id with two different bodies
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .evidence import canonical_json

DEFECT_CODES = (
    "EMPTY_BODY",
    "MISSING_FINGERPRINT",
    "DECLARED_BODY_MISMATCH",
    "DECLARED_METADATA_MISMATCH",
    "ID_MISMATCH",
    "DUPLICATE_BODY",
    "DUPLICATE_REPORT_ID",
)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value.lower())


def normalize_body(body: str) -> str:
    if not isinstance(body, str):
        return ""
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def body_sha256(body: str) -> str:
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()


def metadata_sha256(metadata: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(metadata or {})).encode("utf-8")).hexdigest()


@dataclass
class ReportEnvelope:
    report_id: Any
    mission_id: Any
    body: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    declared_body_sha256: str = ""
    declared_metadata_sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "mission_id": self.mission_id,
            "body": self.body,
            "metadata": dict(self.metadata),
            "declared_body_sha256": self.declared_body_sha256,
            "declared_metadata_sha256": self.declared_metadata_sha256,
        }


@dataclass
class Defect:
    code: str
    report_id: Any
    detail: str
    related: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "report_id": self.report_id,
            "related": self.related,
            "detail": self.detail,
        }


@dataclass
class GateReceipt:
    report_id: Any
    mission_id: Any
    body_sha256: str
    metadata_sha256: str
    fingerprint_ok: bool
    identity_ok: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "mission_id": self.mission_id,
            "body_sha256": self.body_sha256,
            "metadata_sha256": self.metadata_sha256,
            "fingerprint_ok": self.fingerprint_ok,
            "identity_ok": self.identity_ok,
        }


@dataclass
class GateResult:
    ok: bool
    defects: List[Defect] = field(default_factory=list)
    receipts: List[GateReceipt] = field(default_factory=list)
    digest: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "defects": [d.as_dict() for d in self.defects],
            "defect_codes": sorted({d.code for d in self.defects}),
            "receipts": [r.as_dict() for r in self.receipts],
            "digest": self.digest,
        }


class ReportFingerprintGate:
    """Validate a batch of report envelopes; any defect closes the gate."""

    def check(self, envelopes: Iterable[ReportEnvelope]) -> GateResult:
        envelopes = list(envelopes)
        defects: List[Defect] = []
        receipts: List[GateReceipt] = []
        body_owners: Dict[str, Any] = {}
        id_bodies: Dict[str, str] = {}

        for env in envelopes:
            body_hash = body_sha256(env.body)
            meta_hash = metadata_sha256(env.metadata)
            fingerprint_ok = True
            identity_ok = True

            if not normalize_body(env.body):
                defects.append(Defect("EMPTY_BODY", env.report_id, "report body is blank"))
                fingerprint_ok = False
            if not _is_sha256(env.declared_body_sha256) or not _is_sha256(env.declared_metadata_sha256):
                defects.append(Defect(
                    "MISSING_FINGERPRINT", env.report_id,
                    "declared body/metadata sha256 missing or malformed"))
                fingerprint_ok = False
            else:
                if env.declared_body_sha256.lower() != body_hash:
                    defects.append(Defect(
                        "DECLARED_BODY_MISMATCH", env.report_id,
                        f"declared={env.declared_body_sha256[:12]} actual={body_hash[:12]}"))
                    fingerprint_ok = False
                if env.declared_metadata_sha256.lower() != meta_hash:
                    defects.append(Defect(
                        "DECLARED_METADATA_MISMATCH", env.report_id,
                        f"declared={env.declared_metadata_sha256[:12]} actual={meta_hash[:12]}"))
                    fingerprint_ok = False

            meta = env.metadata or {}
            if str(meta.get("report_id")) != str(env.report_id) or \
                    str(meta.get("mission_id")) != str(env.mission_id):
                defects.append(Defect(
                    "ID_MISMATCH", env.report_id,
                    f"metadata ids report={meta.get('report_id')!r} "
                    f"mission={meta.get('mission_id')!r} do not match envelope "
                    f"report={env.report_id!r} mission={env.mission_id!r}"))
                identity_ok = False

            key = str(env.report_id)
            if body_hash in body_owners and str(body_owners[body_hash]) != key:
                defects.append(Defect(
                    "DUPLICATE_BODY", env.report_id,
                    f"body hash already bound to report {body_owners[body_hash]!r}",
                    related=body_owners[body_hash]))
            else:
                body_owners.setdefault(body_hash, env.report_id)
            if key in id_bodies and id_bodies[key] != body_hash:
                defects.append(Defect(
                    "DUPLICATE_REPORT_ID", env.report_id,
                    f"report id reused with a different body "
                    f"({id_bodies[key][:12]} -> {body_hash[:12]})"))
            else:
                id_bodies.setdefault(key, body_hash)

            receipts.append(GateReceipt(
                report_id=env.report_id,
                mission_id=env.mission_id,
                body_sha256=body_hash,
                metadata_sha256=meta_hash,
                fingerprint_ok=fingerprint_ok,
                identity_ok=identity_ok,
            ))

        digest = hashlib.sha256(canonical_json(
            [r.as_dict() for r in receipts]
            + [d.as_dict() for d in defects]).encode("utf-8")).hexdigest()
        return GateResult(ok=not defects, defects=defects,
                          receipts=receipts, digest=digest)


def gate(envelopes: Iterable[ReportEnvelope]) -> GateResult:
    return ReportFingerprintGate().check(envelopes)