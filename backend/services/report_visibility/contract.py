"""Write-read visibility contract for the report pipeline.

Problem
-------
A report write can answer ``200`` while a subsequent read cannot find the
record (commit landing in a different transaction/session, row filtered out by
the tenant predicate, a replica lagging behind, a rollback that nobody
observed).  The failure mode is *silent*: the writer believes it succeeded, the
reader sees nothing, and the coverage metric quietly drifts down.

Contract
--------
Every successful write must be followed by an **independent read-back** that
asserts field-by-field identity of the three fields a consumer actually reads:

============  ==========================================================
field         identity rule
============  ==========================================================
``markdown``  byte identity after transport newline normalisation
              (``\\r\\n`` / ``\\r`` → ``\\n``); nothing else is normalised,
              so truncation, mojibake and whitespace edits are all caught
``figures``   ordered list of ``{name, caption}``; captions default to ``""``
``mission_id``  integer identity (``None`` stays ``None``)
============  ==========================================================

The verdict is a *machine* verdict: digests are SHA-256 of the canonical JSON
of the expected/observed projection, plus a character offset for the first
divergence so a human can act on it.  It is never "probably fine".

This module is pure: no DB, no HTTP, no clock, no logging side channel.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Fields covered by the contract.  Ordering is part of the reported output.
CONTRACT_FIELDS: tuple[str, ...] = ("markdown", "figures", "mission_id")

#: Machine codes carried by a failed verdict.
CODE_RECORD_MISSING = "record_missing"
CODE_FIELD_ABSENT = "field_absent"
CODE_MARKDOWN_MISMATCH = "markdown_mismatch"
CODE_MARKDOWN_TRUNCATED = "markdown_truncated"
CODE_FIGURES_MISMATCH = "figures_mismatch"
CODE_MISSION_ID_MISMATCH = "mission_id_mismatch"


def canonical(value: Any) -> str:
    """Canonical JSON: key order independent, UTF-8, stable floats."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    """SHA-256 of the canonical JSON projection of ``value``."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalize_markdown(text: str) -> str:
    """The *only* tolerated transport transform: newline normalisation.

    Note this is deliberately NOT a text normalisation: no unicode folding, no
    whitespace collapsing, no trailing-newline forgiveness.  A write-read pair
    that disagrees on any other byte is a contract violation.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def figures_projection(figures: Iterable[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    """Map whatever the store returns onto the contracted ``{name, caption}`` shape."""
    out: list[dict[str, str]] = []
    for fig in figures or []:
        if isinstance(fig, Mapping):
            out.append({"name": str(fig.get("name", "")),
                        "caption": str(fig.get("caption") or "")})
        else:  # tolerate a bare name (legacy rows)
            out.append({"name": str(fig), "caption": ""})
    return out


def first_divergence(expected: str, observed: str) -> int | None:
    """Character offset of the first differing byte, or ``None`` if identical."""
    for idx, (a, b) in enumerate(zip(expected, observed)):
        if a != b:
            return idx
    if len(expected) == len(observed):
        return None
    return min(len(expected), len(observed))


@dataclass(frozen=True)
class FieldCheck:
    """Verdict for one contracted field."""

    field: str
    ok: bool
    expected_digest: str
    observed_digest: str
    detail: str = ""
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "ok": self.ok,
                "expected_digest": self.expected_digest,
                "observed_digest": self.observed_digest,
                "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class ReadbackVerdict:
    """Outcome of one write-read assertion."""

    ok: bool
    record_missing: bool
    checks: tuple[FieldCheck, ...]
    codes: tuple[str, ...] = ()

    @property
    def issues(self) -> list[str]:
        return list(self.codes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "record_missing": self.record_missing,
            "codes": list(self.codes),
            "checks": [c.as_dict() for c in self.checks],
            "checked_fields": [c.field for c in self.checks],
        }


def _check_markdown(expected: Any, observed: Any) -> FieldCheck:
    exp = normalize_markdown(str(expected if expected is not None else ""))
    obs = normalize_markdown(str(observed if observed is not None else ""))
    exp_digest, obs_digest = digest(exp), digest(obs)
    if exp_digest == obs_digest:
        return FieldCheck("markdown", True, exp_digest, obs_digest)
    offset = first_divergence(exp, obs)
    if offset is not None and obs == exp[: len(obs)]:
        detail = f"truncated at char {offset} (expected {len(exp)} chars, got {len(obs)})"
        code = CODE_MARKDOWN_TRUNCATED
    else:
        detail = (f"diverges at char {offset} "
                  f"(expected {len(exp)} chars, got {len(obs)})")
        code = CODE_MARKDOWN_MISMATCH
    return FieldCheck("markdown", False, exp_digest, obs_digest, detail, code)


def _check_figures(expected: Any, observed: Any) -> FieldCheck:
    exp = figures_projection(expected)
    obs = figures_projection(observed)
    exp_digest, obs_digest = digest(exp), digest(obs)
    if exp_digest == obs_digest:
        return FieldCheck("figures", True, exp_digest, obs_digest)
    exp_names = [f["name"] for f in exp]
    obs_names = [f["name"] for f in obs]
    return FieldCheck("figures", False, exp_digest, obs_digest,
                      f"expected {exp_names}, observed {obs_names}",
                      CODE_FIGURES_MISMATCH)


def _check_mission_id(expected: Any, observed: Any) -> FieldCheck:
    exp = None if expected is None else int(expected)
    obs = None if observed is None else int(observed)
    exp_digest, obs_digest = digest(exp), digest(obs)
    if exp_digest == obs_digest:
        return FieldCheck("mission_id", True, exp_digest, obs_digest)
    return FieldCheck("mission_id", False, exp_digest, obs_digest,
                      f"expected {exp}, observed {obs}", CODE_MISSION_ID_MISMATCH)


_CHECKERS = {
    "markdown": _check_markdown,
    "figures": _check_figures,
    "mission_id": _check_mission_id,
}


def assert_readback(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
    *,
    fields: Sequence[str] = CONTRACT_FIELDS,
) -> ReadbackVerdict:
    """Assert that what was written is what a reader can now see.

    ``observed=None`` means the reader found no record at all — the loudest
    possible violation (``record_missing``), never a pass by default.
    """
    unknown = [f for f in fields if f not in _CHECKERS]
    if unknown:
        raise ValueError(f"unknown contract field(s): {unknown}")

    if observed is None:
        return ReadbackVerdict(ok=False, record_missing=True, checks=(),
                               codes=(CODE_RECORD_MISSING,))

    checks: list[FieldCheck] = []
    codes: list[str] = []
    for field in fields:
        checker = _CHECKERS[field]
        if field not in observed:
            checks.append(FieldCheck(field, False, digest(expected.get(field)),
                                     digest(None),
                                     "field not returned by the read", CODE_FIELD_ABSENT))
            codes.append(CODE_FIELD_ABSENT)
            continue
        check = checker(expected.get(field), observed.get(field))
        checks.append(check)
        if not check.ok:
            codes.append(check.code)

    return ReadbackVerdict(ok=all(c.ok for c in checks), record_missing=False,
                           checks=tuple(checks), codes=tuple(codes))