#!/usr/bin/env python3
"""Claim freshness checker: three fault classes, one deterministic verdict.

The question this module answers is narrow and machine-decidable:

    given a claim record, is the number it asserts still trustworthy *now*?

It answers with a status and, when the answer is "no", an automatic downgrade of
the evidence grade. No human judgement, no heuristics on text, no domain
vocabulary: every verdict is a comparison of bytes (sha256) or of environment
fields.

Fault classes (the three the campaign node names)
-------------------------------------------------
``BROKEN``   the artifact that carries the number is absent or its bytes changed
             (``artifact_missing`` / ``artifact_hash_mismatch`` /
             ``artifact_hash_absent``). The number cannot be located in the
             state the author signed, so it is not evidence of anything.
``STALE``    the claim's own artifact is intact, but at least one declared
             dependency changed, vanished or is unpinned
             (``dep_changed`` / ``dep_missing`` / ``dep_unpinned``). The number
             is a *conclusion from premises that no longer hold*.
``SUSPECT``  the environment fingerprint differs across architectures
             (``env_cross_arch``): same bytes, different machine class, so the
             bytes no longer guarantee the same number.

Two further statuses keep the classification honest rather than convenient:

``ENV_DRIFT``    same architecture, interpreter or package versions differ. The
                 numerics *may* move; the claim is degraded one step, not wiped.
``INVALID``      the record is not a claim (schema violation). Reported as such
                 instead of being graded.

Precedence is fixed: INVALID > BROKEN > STALE > SUSPECT > ENV_DRIFT > OK. Each
report lists *all* causes found, so a claim that is both BROKEN and STALE is
visible as such while still graded by the most severe class.

Grade arithmetic
----------------
Grades form a two-track lattice. ``STUB`` asserts only that an interface exists
and carries no number, so freshness never touches it. For numeric grades the
ladder is ``REAL > MOCK > WITHHELD``:

    hard fault (BROKEN / STALE / SUSPECT / INVALID)  -> WITHHELD
    runtime drift (ENV_DRIFT)                        -> one step down
                                                        REAL->MOCK, MOCK->WITHHELD

The checker can only downgrade. A claim whose grade was WITHHELD when written
stays WITHHELD even when everything matches: freshness is necessary, never
sufficient, for REAL.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import claims as claim_mod
from . import fingerprint as fp_mod

# Statuses
OK = "OK"
ENV_DRIFT = "ENV_DRIFT"
SUSPECT = "SUSPECT"
STALE = "STALE"
BROKEN = "BROKEN"
INVALID = "INVALID"

STATUSES = (OK, ENV_DRIFT, SUSPECT, STALE, BROKEN, INVALID)

# Precedence: lower index wins when several causes are present.
PRECEDENCE = {INVALID: 0, BROKEN: 1, STALE: 2, SUSPECT: 3, ENV_DRIFT: 4, OK: 5}

# Numeric grade ladder (STUB handled separately).
_LADDER = ("REAL", "MOCK", "WITHHELD")
HARD_FAULTS = (INVALID, BROKEN, STALE, SUSPECT)


def downgrade(grade: str, status: str) -> str:
    """Effective grade for a status. Monotone non-increasing, never upgrades."""
    grade = grade if grade in ("REAL", "MOCK", "STUB", "WITHHELD") else "WITHHELD"
    if grade == "STUB":
        # Interface-only claims assert no number; freshness cannot weaken them.
        return "STUB"
    if status in HARD_FAULTS:
        return "WITHHELD"
    if status == ENV_DRIFT:
        return _LADDER[min(_LADDER.index(grade) + 1, len(_LADDER) - 1)]
    return grade


@dataclass
class CheckResult:
    claim_id: str
    status: str
    recorded_grade: str
    effective_grade: str
    causes: List[Dict[str, Any]] = field(default_factory=list)
    artifact: Dict[str, Any] = field(default_factory=dict)
    deps: List[Dict[str, Any]] = field(default_factory=list)
    env: Dict[str, Any] = field(default_factory=dict)
    claim: Dict[str, Any] = field(default_factory=dict)

    @property
    def downgraded(self) -> bool:
        return self.effective_grade != self.recorded_grade

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status,
            "recorded_grade": self.recorded_grade,
            "effective_grade": self.effective_grade,
            "downgraded": self.downgraded,
            "causes": self.causes,
            "artifact": self.artifact,
            "deps": self.deps,
            "env": self.env,
            "claim": self.claim,
        }


def _file_digest(path: Optional[str], cache: Optional[Dict[Any, Optional[str]]] = None) -> Optional[str]:
    """sha256 of a file with an optional in-run cache.

    A batch reads the same dependency hundreds of times (835 claims over 5 trees
    reference far fewer files). The cache is keyed by (path, size, mtime_ns), so a
    file mutated during a run is re-hashed rather than served stale -- freshness
    checking that trusts a stale cache would be a self-defeating optimisation.
    """
    if not path:
        return None
    key = None
    if cache is not None:
        try:
            st = os.stat(path)
            key = (path, st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        if key is not None and key in cache:
            return cache[key]
    digest = claim_mod.sha256_file(path)
    if cache is not None and key is not None:
        cache[key] = digest
    return digest


def _cause(code: str, detail: str = "", **extra: Any) -> Dict[str, Any]:
    out = {"code": code, "detail": detail}
    out.update(extra)
    return out


def _check_artifact(claim: Mapping[str, Any], resolver: claim_mod.RepoResolver,
                    cache: Optional[Dict[Any, Optional[str]]] = None) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    uri = claim.get("artifact_uri")
    declared = claim.get("artifact_sha256")
    path = resolver.resolve(uri) if uri else None
    info: Dict[str, Any] = {"uri": uri, "resolved": path, "declared_sha256": declared}
    causes: List[Dict[str, Any]] = []
    if not declared:
        causes.append(_cause("artifact_hash_absent", f"{uri}: no artifact_sha256"))
        info["actual_sha256"] = _file_digest(path, cache)
        return info, causes
    if path is None:
        causes.append(_cause("artifact_missing", f"{uri} does not resolve under any root"))
        info["actual_sha256"] = None
        return info, causes
    actual = _file_digest(path, cache)
    info["actual_sha256"] = actual
    if actual is None:
        causes.append(_cause("artifact_missing", f"{path} unreadable"))
    elif actual != declared:
        causes.append(_cause("artifact_hash_mismatch", f"{uri}: declared {declared[:12]}… actual {actual[:12]}…"))
    return info, causes


def _check_deps(claim: Mapping[str, Any], resolver: claim_mod.RepoResolver,
                cache: Optional[Dict[Any, Optional[str]]] = None) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    causes: List[Dict[str, Any]] = []
    for i, dep in enumerate(claim.get("depends_on") or []):
        uri = dep.get("uri")
        declared = dep.get("sha256")
        kind = dep.get("kind", "artifact")
        path = resolver.resolve(uri)
        row: Dict[str, Any] = {"index": i, "uri": uri, "kind": kind, "declared_sha256": declared,
                               "resolved": path, "state": "ok"}
        if declared is None:
            row["state"] = "unpinned"
            causes.append(_cause("dep_unpinned", f"depends_on[{i}] {uri} ({kind}) has no pinned hash"))
        elif path is None:
            row["state"] = "missing"
            causes.append(_cause("dep_missing", f"depends_on[{i}] {uri} ({kind}) does not resolve"))
        else:
            actual = _file_digest(path, cache)
            row["actual_sha256"] = actual
            if actual is None:
                row["state"] = "missing"
                causes.append(_cause("dep_missing", f"depends_on[{i}] {path} unreadable"))
            elif actual != declared:
                row["state"] = "changed"
                causes.append(_cause("dep_changed",
                                     f"depends_on[{i}] {uri} ({kind}): declared {declared[:12]}… actual {actual[:12]}…"))
        rows.append(row)
    return rows, causes


def _check_env(claim: Mapping[str, Any], current_env: Optional[Mapping[str, Any]]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    recorded = claim.get("env_fingerprint")
    cmp = fp_mod.compare(recorded, current_env)
    info = {"assurance": "asserted" if recorded else "unasserted", **cmp}
    causes: List[Dict[str, Any]] = []
    if cmp["verdict"] == fp_mod.ARCH_MISMATCH:
        causes.append(_cause("env_cross_arch", "; ".join(cmp["reasons"])))
    elif cmp["verdict"] == fp_mod.RUNTIME_DRIFT:
        causes.append(_cause("env_runtime_drift", "; ".join(cmp["reasons"])))
    return info, causes


def check_claim(
    claim: Mapping[str, Any],
    resolver: Optional[claim_mod.RepoResolver] = None,
    current_env: Optional[Mapping[str, Any]] = None,
    schema: Optional[dict] = None,
    cache: Optional[Dict[Any, Optional[str]]] = None,
) -> CheckResult:
    """Check one claim. Pure function of (claim, filesystem, environment)."""
    resolver = resolver or claim_mod.RepoResolver({})
    grade = str(claim.get("evidence_grade") or "WITHHELD")
    cid = str(claim.get("claim_id") or "<unnamed>")

    errors = claim_mod.validate_claim(claim, schema)
    if errors:
        res = CheckResult(claim_id=cid, status=INVALID, recorded_grade=grade,
                          effective_grade=downgrade(grade, INVALID),
                          causes=[_cause("claim_invalid", e) for e in errors],
                          claim=dict(claim))
        return res

    art_info, art_causes = _check_artifact(claim, resolver, cache)
    dep_rows, dep_causes = _check_deps(claim, resolver, cache)
    env_info, env_causes = _check_env(claim, current_env)

    causes = art_causes + dep_causes + env_causes
    status = OK
    for candidate in (INVALID, BROKEN, STALE, SUSPECT, ENV_DRIFT):
        if any(_status_of(c["code"]) == candidate for c in causes):
            status = candidate
            break

    return CheckResult(
        claim_id=cid,
        status=status,
        recorded_grade=grade,
        effective_grade=downgrade(grade, status),
        causes=causes,
        artifact=art_info,
        deps=dep_rows,
        env=env_info,
        claim=dict(claim),
    )


_CODE_STATUS = {
    "claim_invalid": INVALID,
    "artifact_missing": BROKEN,
    "artifact_hash_mismatch": BROKEN,
    "artifact_hash_absent": BROKEN,
    "dep_changed": STALE,
    "dep_missing": STALE,
    "dep_unpinned": STALE,
    "env_cross_arch": SUSPECT,
    "env_runtime_drift": ENV_DRIFT,
}


def _status_of(code: str) -> str:
    return _CODE_STATUS.get(code, OK)


def check_claims(
    claims: Iterable[Mapping[str, Any]],
    resolver: Optional[claim_mod.RepoResolver] = None,
    current_env: Optional[Mapping[str, Any]] = None,
    schema: Optional[dict] = None,
) -> List[CheckResult]:
    resolver = resolver or claim_mod.RepoResolver({})
    env = current_env if current_env is not None else fp_mod.env_fingerprint()
    cache: Dict[Any, Optional[str]] = {}
    return [check_claim(c, resolver, env, schema, cache) for c in claims]


def summarize(results: Iterable[CheckResult]) -> Dict[str, Any]:
    """Counts by status, by cause code and by grade transition."""
    by_status: Dict[str, int] = {s: 0 for s in STATUSES}
    by_cause: Dict[str, int] = {}
    by_grade: Dict[str, int] = {}
    n = 0
    for r in results:
        n += 1
        by_status[r.status] = by_status.get(r.status, 0) + 1
        for c in r.causes:
            by_cause[c["code"]] = by_cause.get(c["code"], 0) + 1
        if r.downgraded:
            key = f"{r.recorded_grade}->{r.effective_grade}"
            by_grade[key] = by_grade.get(key, 0) + 1
    return {
        "n_claims": n,
        "by_status": by_status,
        "by_cause": dict(sorted(by_cause.items())),
        "downgrades": dict(sorted(by_grade.items())),
        "n_downgraded": sum(by_grade.values()),
        "n_ok": by_status.get(OK, 0),
    }