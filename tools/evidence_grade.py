#!/usr/bin/env python3
"""Machine-recomputable evidence grading engine (domain-neutral).

Why this module exists
----------------------
Evidence grades (REAL / MOCK / STUB / WITHHELD) used to be *author declarations*.
A declaration cannot be audited: whoever writes the ledger can write ``REAL``.
This module turns the grade into a function of the artifact structure declared in
``schemas/evidence_contract.schema.json``:

    grade = f(contract)   # pure, deterministic, offline, no imports of the subject

Rule DAG (evaluated top-down; first failing gate decides the grade)
-------------------------------------------------------------------
=====  ==========================  ========  ======================================
id     gate                        on fail   meaning
=====  ==========================  ========  ======================================
R00    schema validity             UNKNOWN   contract is not a contract
R01    numeric artifact present    STUB      only an interface is asserted
R02    repro_cmd present           MOCK      numbers with no way to regenerate
R03    anchor present              MOCK      number with no recorded origin
R04    anchor recomputable         MOCK      inline literal / hash mismatch
R05    residual present            WITHHELD  no measured disagreement
R06    independent path present    WITHHELD  a single path cannot corroborate
R07    path recomputable           WITHHELD  path value not reproducible offline
R08    residual recomputable       WITHHELD  declared residual != recomputed
R09    tolerance predeclared+met   WITHHELD  threshold missing or exceeded
R10    paths independent           WITHHELD  same-source copy (the classic trap)
R11    negative control fail-able  WITHHELD  control cannot fail => no discrimination
R12    provenance digest matches   WITHHELD  artifact set changed after hashing
R13    all gates pass              REAL      recomputable, corroborated, falsifiable
=====  ==========================  ========  ======================================

Design rules
------------
* Zero domain vocabulary: no formula, unit, substance or field name appears here.
  Vocabulary checks live in ``tools/evidence_domain_scan.py`` + its lexicon file.
* Zero network, zero import of the graded subject: only files, hashes and numbers.
* A *recomputable* source is one whose bytes can be re-read offline and whose
  sha256 is declared and matches. ``inline`` literals are hardcoded by construction;
  ``url``/``stdout`` sources are not re-readable offline and therefore cannot
  support REAL.
* Independence is decided by provenance (source sha256, ref, derived_from), never
  by the ``method`` label the producer wrote: a copy that renames its method is
  still a copy.

CLI
---
    python3 tools/evidence_grade.py grade --contract PATH [--base-dir DIR]
    python3 tools/evidence_grade.py grade-all --fixtures DIR
    python3 tools/evidence_grade.py gen-fixtures [--out DIR] [--seed N]
    python3 tools/evidence_grade.py run-all [--out DIR] [--seed N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:  # allow sibling imports when run as a script
    sys.path.insert(0, str(_TOOLS))

SCHEMA_PATH = ROOT / "schemas" / "evidence_contract.schema.json"
CONTRACT_VERSION = "evidence-contract/1.0"
GRADE_REPORT_VERSION = "evidence-grade-report/1.0"

GRADES = ("REAL", "MOCK", "STUB", "WITHHELD", "UNKNOWN")
#: monotone strength of a grade; used by reconciliation (declared vs derived)
GRADE_RANK = {"UNKNOWN": 0, "NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2,
              "WITHHELD": 3, "REAL": 4}

#: rule DAG, in evaluation order. ``on_fail`` is the grade the first failing gate yields.
RULE_DAG = (
    {"id": "R00_SCHEMA", "on_fail": "UNKNOWN"},
    {"id": "R01_NUMERIC_PRESENT", "on_fail": "STUB"},
    {"id": "R02_REPRO_CMD", "on_fail": "MOCK"},
    {"id": "R03_ANCHOR_PRESENT", "on_fail": "MOCK"},
    {"id": "R04_ANCHOR_RECOMPUTABLE", "on_fail": "MOCK"},
    {"id": "R05_RESIDUAL_PRESENT", "on_fail": "WITHHELD"},
    {"id": "R06_INDEPENDENT_PATH", "on_fail": "WITHHELD"},
    {"id": "R07_PATH_RECOMPUTABLE", "on_fail": "WITHHELD"},
    {"id": "R08_RESIDUAL_RECOMPUTABLE", "on_fail": "WITHHELD"},
    {"id": "R09_TOLERANCE", "on_fail": "WITHHELD"},
    {"id": "R10_INDEPENDENCE", "on_fail": "WITHHELD"},
    {"id": "R11_NEGATIVE_CONTROL", "on_fail": "WITHHELD"},
    {"id": "R12_PROVENANCE_DIGEST", "on_fail": "WITHHELD"},
    {"id": "R13_ALL_GATES_PASS", "on_fail": "REAL"},
)
_RULE_INDEX = {r["id"]: i for i, r in enumerate(RULE_DAG)}
#: error codes emitted per gate
FAIL_CODES = {
    "R00_SCHEMA": "SCHEMA_INVALID",
    "R01_NUMERIC_PRESENT": "INTERFACE_ONLY",
    "R02_REPRO_CMD": "NO_REPRO_CMD",
    "R03_ANCHOR_PRESENT": "NO_ANCHOR",
    "R04_ANCHOR_RECOMPUTABLE": "ANCHOR_NOT_RECOMPUTABLE",
    "R05_RESIDUAL_PRESENT": "NO_RESIDUAL",
    "R06_INDEPENDENT_PATH": "NO_INDEPENDENT_PATH",
    "R07_PATH_RECOMPUTABLE": "PATH_NOT_RECOMPUTABLE",
    "R08_RESIDUAL_RECOMPUTABLE": "RESIDUAL_NOT_RECOMPUTABLE",
    "R09_TOLERANCE": "TOLERANCE_UNMET",
    "R10_INDEPENDENCE": "SAME_SOURCE_COPY",
    "R11_NEGATIVE_CONTROL": "NEGATIVE_CONTROL_UNUSABLE",
    "R12_PROVENANCE_DIGEST": "PROVENANCE_DIGEST_MISMATCH",
    "R13_ALL_GATES_PASS": "ALL_GATES_PASS",
}

_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
#: relative slack when comparing a declared number against its recomputation
_RECOMPUTE_RTOL = 1e-9


class Unreadable(Exception):
    """A source cannot be re-read/re-hashed offline, so it cannot support a claim."""


# ── schema ──────────────────────────────────────────────────────────────────
def load_schema(path: Path | None = None) -> dict:
    return json.loads(Path(path or SCHEMA_PATH).read_text(encoding="utf-8"))


def validate_contract(contract, schema: dict | None = None) -> list[str]:
    """Return a list of schema violations (empty == structurally valid)."""
    schema = schema if schema is not None else load_schema()
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return _validate_minimal(contract)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(contract), key=lambda e: list(map(str, e.path)))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def _validate_minimal(contract) -> list[str]:
    """Dependency-free fallback (subset of the schema; used only without jsonschema)."""
    errs: list[str] = []
    if not isinstance(contract, dict):
        return ["$: contract must be an object"]
    for key in ("schema_version", "capability_id", "claim", "artifacts"):
        if key not in contract:
            errs.append(f"$: missing required '{key}'")
    if contract.get("schema_version") != CONTRACT_VERSION:
        errs.append(f"$.schema_version: expected {CONTRACT_VERSION!r}")
    arts = contract.get("artifacts")
    if not isinstance(arts, list) or not arts:
        errs.append("$.artifacts: must be a non-empty array")
    else:
        for i, a in enumerate(arts):
            if not isinstance(a, dict):
                errs.append(f"$.artifacts[{i}]: must be an object")
                continue
            for key in ("id", "kind", "source"):
                if key not in a:
                    errs.append(f"$.artifacts[{i}]: missing '{key}'")
            if a.get("kind") not in {"anchor", "independent_path", "residual",
                                     "negative_control", "interface"}:
                errs.append(f"$.artifacts[{i}].kind: not in enum")
            if not isinstance(a.get("source"), dict):
                errs.append(f"$.artifacts[{i}].source: must be an object")
    claim = contract.get("claim")
    if not isinstance(claim, dict) or claim.get("kind") not in {"numeric", "interface_only"}:
        errs.append("$.claim.kind: must be 'numeric' or 'interface_only'")
    return errs


# ── provenance helpers ──────────────────────────────────────────────────────
def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def provenance_digest(artifacts) -> str:
    """Digest of the artifact set: sorted ``[id, source.sha256]`` pairs.

    Recomputable from the contract alone, so a producer cannot silently swap an
    artifact after the fact without breaking the digest.
    """
    pairs = sorted([str(a.get("id")),
                    str((a.get("source") or {}).get("sha256") or "")]
                   for a in artifacts)
    blob = json.dumps(pairs, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(blob.encode("utf-8"))


def read_verified_text(source: dict, base_dir=None) -> tuple[str, str]:
    """Re-read a source's bytes offline and verify them against the declared sha256.

    Raises :class:`Unreadable` when the bytes cannot be re-read or re-hashed.
    """
    source = source or {}
    kind = source.get("kind")
    if kind == "inline":
        # An inline literal is the definition of hardcoded: nothing is re-read,
        # so it can never support REAL. Kept parseable so messages stay useful.
        ref = str(source.get("ref", "")).strip()
        if _NUM_RE.match(ref):
            raise Unreadable(f"inline literal {ref}: hardcoded, not re-readable")
        raise Unreadable("inline source: hardcoded, not re-readable")
    if kind != "file":
        raise Unreadable(f"source kind '{kind}' is not re-readable offline")
    ref = source.get("ref")
    if not ref:
        raise Unreadable("file source has no ref")
    path = Path(ref)
    if not path.is_absolute():
        if base_dir is None:
            raise Unreadable(f"relative ref {ref!r} without a base_dir")
        path = Path(base_dir) / path
    if not path.is_file():
        raise Unreadable(f"missing file {path}")
    raw = path.read_bytes()
    actual = sha256_bytes(raw)
    declared = source.get("sha256")
    if not declared:
        raise Unreadable(f"no source.sha256 declared for {path.name}")
    if declared != actual:
        raise Unreadable(f"sha256 mismatch for {path.name} "
                         f"(declared {str(declared)[:12]}, actual {actual[:12]})")
    return raw.decode("utf-8", "replace"), f"{path.name} sha256={actual[:12]}"


def read_numeric(source: dict, base_dir=None) -> tuple[float, str]:
    """Re-read a numeric value from a source, verifying bytes against sha256."""
    text, where = read_verified_text(source, base_dir)
    return _parse_number_payload(text), where


def read_payload(source: dict, base_dir=None) -> tuple[dict, str]:
    """Re-read a verified JSON payload (fallback: ``{"value": <number>}``)."""
    text, where = read_verified_text(source, base_dir)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"value": _parse_number_payload(text)}, where
    if not isinstance(payload, dict):
        return {"value": _parse_number_payload(text)}, where
    return payload, where


def _parse_number_payload(text: str) -> float:
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = stripped
    if isinstance(payload, dict):
        payload = payload.get("value")
    if isinstance(payload, bool) or not isinstance(payload, (int, float)):
        raise Unreadable("payload carries no numeric value")
    value = float(payload)
    if not math.isfinite(value):
        raise Unreadable("non-finite value")
    return value


# ── independence ────────────────────────────────────────────────────────────
def independence_violations(anchor: dict | None, paths: list[dict]) -> list[dict]:
    """Evidence-provenance checks for 'these two numbers came from two places'.

    The producer's ``method`` string is never trusted as an independence proof;
    only bytes (sha256), refs and declared derivation are.
    """
    violations: list[dict] = []
    seen_hash: dict[str, str] = {}
    anchor_src = (anchor or {}).get("source") or {}
    anchor_hash = anchor_src.get("sha256")
    anchor_ref = anchor_src.get("ref")
    for p in paths:
        src = p.get("source") or {}
        h, ref = src.get("sha256"), src.get("ref")
        pid = str(p.get("id"))
        if h:
            if h in seen_hash:
                violations.append({
                    "code": "DUPLICATE_SOURCE_HASH",
                    "artifacts": [seen_hash[h], pid],
                    "detail": f"{pid} re-reads the same bytes as {seen_hash[h]} (sha256 {h[:12]})",
                })
            else:
                seen_hash[h] = pid
            if anchor_hash and h == anchor_hash:
                violations.append({
                    "code": "PATH_IS_ANCHOR",
                    "artifacts": [str((anchor or {}).get("id")), pid],
                    "detail": f"{pid} re-reads the anchor bytes (sha256 {h[:12]})",
                })
        if anchor_ref and ref and ref == anchor_ref:
            violations.append({
                "code": "SAME_SOURCE_REF",
                "artifacts": [str((anchor or {}).get("id")), pid],
                "detail": f"{pid} points at the same ref as the anchor ({ref})",
            })
        derived = [str(d) for d in (p.get("derived_from") or [])]
        if derived:
            violations.append({
                "code": "DECLARED_DERIVATION",
                "artifacts": [pid] + derived,
                "detail": f"{pid} declares derivation from {derived}",
            })
    return violations


# ── grader ──────────────────────────────────────────────────────────────────
def grade_contract(contract, *, base_dir=None, schema: dict | None = None) -> dict:
    """Derive the evidence grade from the contract structure (pure function)."""
    trace: list[dict] = []
    metrics: dict = {
        "numeric_artifacts": 0,
        "independent_paths": 0,
        "anchor_value": None,
        "residual_declared": None,
        "residual_recomputed": None,
        "tolerance_limit": None,
        "tolerance_metric": None,
        "independence_violations": 0,
        "negative_control_outcome": None,
        "provenance_declared": None,
        "provenance_recomputed": None,
    }

    def log(rule_id: str, status: str, detail: str = "") -> None:
        trace.append({"rule": rule_id, "status": status, "detail": detail})

    def fail(rule_id: str, detail: str, code: str | None = None) -> dict:
        log(rule_id, "fail", detail)
        return {
            "grade": dict((r["id"], r["on_fail"]) for r in RULE_DAG)[rule_id],
            "reason_code": code or FAIL_CODES[rule_id],
            "reasons": [{"code": code or FAIL_CODES[rule_id], "detail": detail}],
            "failed_rule": rule_id,
            "trace": trace,
            "metrics": metrics,
            "contract_version": (contract or {}).get("schema_version")
            if isinstance(contract, dict) else None,
            "capability_id": (contract or {}).get("capability_id")
            if isinstance(contract, dict) else None,
        }

    # R00 ─ structure -------------------------------------------------------
    errors = validate_contract(contract, schema)
    if errors:
        return fail("R00_SCHEMA", "; ".join(errors[:8]))
    log("R00_SCHEMA", "pass")
    artifacts = list(contract["artifacts"])

    # R01 ─ is a number asserted at all? -----------------------------------
    numeric = [a for a in artifacts if isinstance(a.get("value"), (int, float))
               and not isinstance(a.get("value"), bool)]
    metrics["numeric_artifacts"] = len(numeric)
    if not numeric:
        kinds = sorted({str(a.get("kind")) for a in artifacts})
        return fail("R01_NUMERIC_PRESENT",
                    f"no numeric artifact (kinds present: {', '.join(kinds)})")
    log("R01_NUMERIC_PRESENT", "pass", f"{len(numeric)} numeric artifact(s)")

    # R02 ─ can the numbers be regenerated? --------------------------------
    repro = contract.get("repro_cmd")
    if not isinstance(repro, str) or not repro.strip():
        return fail("R02_REPRO_CMD", "numbers asserted but no repro_cmd")
    log("R02_REPRO_CMD", "pass", repro.strip())

    # R03 ─ is there a recorded origin? ------------------------------------
    anchors = [a for a in artifacts if a.get("kind") == "anchor"]
    if not anchors:
        return fail("R03_ANCHOR_PRESENT", "no anchor artifact (number has no origin)")
    anchor = anchors[0]
    log("R03_ANCHOR_PRESENT", "pass", str(anchor.get("id")))

    # R04 ─ is the anchor recomputable offline? ----------------------------
    try:
        anchor_value, anchor_where = read_numeric(anchor.get("source"), base_dir)
    except Unreadable as exc:
        if (anchor.get("source") or {}).get("kind") == "inline":
            return fail("R04_ANCHOR_RECOMPUTABLE",
                        f"anchor is an inline literal: {exc}", code="HARDCODED_INLINE")
        return fail("R04_ANCHOR_RECOMPUTABLE", str(exc))
    metrics["anchor_value"] = anchor_value
    log("R04_ANCHOR_RECOMPUTABLE", "pass", anchor_where)

    # R05 ─ is there a measured residual? ----------------------------------
    residuals = [a for a in artifacts if a.get("kind") == "residual"]
    if not residuals:
        return fail("R05_RESIDUAL_PRESENT", "no residual artifact (agreement unmeasured)")
    residual = residuals[0]
    declared = residual.get("value")
    metrics["residual_declared"] = declared
    log("R05_RESIDUAL_PRESENT", "pass", f"declared residual={declared}")

    # R06 ─ is there at least one corroborating path? ----------------------
    paths = [a for a in artifacts if a.get("kind") == "independent_path"]
    metrics["independent_paths"] = len(paths)
    if not paths:
        return fail("R06_INDEPENDENT_PATH", "no independent_path artifact")
    log("R06_INDEPENDENT_PATH", "pass", f"{len(paths)} path(s)")

    # R07 ─ do the paths re-read offline? ----------------------------------
    path_values: list[tuple[str, float]] = []
    for p in paths:
        try:
            val, where = read_numeric(p.get("source"), base_dir)
        except Unreadable as exc:
            return fail("R07_PATH_RECOMPUTABLE", f"{p.get('id')}: {exc}")
        path_values.append((str(p.get("id")), val))
        log("R07_PATH_RECOMPUTABLE", "pass", f"{p.get('id')}: {where}")

    # R08 ─ does the declared residual match the recomputation? ------------
    metric = (contract.get("claim", {}).get("tolerance") or {}).get("metric", "rel_abs_diff")
    diffs = []
    for pid, val in path_values:
        diff = abs(anchor_value - val)
        if metric == "rel_abs_diff":
            diff = diff / abs(anchor_value) if anchor_value else diff
        diffs.append((pid, diff))
    best_pid, recomputed = min(diffs, key=lambda kv: kv[1])
    metrics["residual_recomputed"] = recomputed
    if declared is None or not isinstance(declared, (int, float)) or isinstance(declared, bool):
        return fail("R08_RESIDUAL_RECOMPUTABLE", "residual artifact has no numeric value")
    tol = max(1e-12, _RECOMPUTE_RTOL * max(abs(float(declared)), abs(recomputed)))
    if abs(float(declared) - recomputed) > tol:
        return fail("R08_RESIDUAL_RECOMPUTABLE",
                    f"declared residual {declared} != recomputed {recomputed:.3e} "
                    f"(metric={metric}, best path={best_pid})")
    log("R08_RESIDUAL_RECOMPUTABLE", "pass",
        f"{metric}({anchor.get('id')},{best_pid})={recomputed:.3e}")

    # R09 ─ was the threshold predeclared, and is it met? ------------------
    tolerance = contract.get("claim", {}).get("tolerance") or {}
    limit = tolerance.get("limit")
    metrics["tolerance_limit"] = limit
    metrics["tolerance_metric"] = tolerance.get("metric")
    if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit <= 0:
        return fail("R09_TOLERANCE", "no predeclared tolerance limit")
    if recomputed > float(limit):
        return fail("R09_TOLERANCE",
                    f"recomputed residual {recomputed:.3e} exceeds predeclared limit {limit}")
    log("R09_TOLERANCE", "pass", f"{metric}={recomputed:.3e} <= {limit}")

    # R10 ─ are the paths actually independent? (the classic trap) --------
    violations = independence_violations(anchor, paths)
    metrics["independence_violations"] = len(violations)
    if violations:
        first = violations[0]
        return fail("R10_INDEPENDENCE",
                    first["detail"] + f" (+{len(violations) - 1} more)" if len(violations) > 1
                    else first["detail"])
    log("R10_INDEPENDENCE", "pass", f"{len(paths)} distinct source(s)")

    # R11 ─ is there a fail-able negative control? -------------------------
    controls = [a for a in artifacts if a.get("kind") == "negative_control"]
    if not controls:
        return fail("R11_NEGATIVE_CONTROL", "no negative_control artifact")
    control = controls[0]
    metrics["negative_control_outcome"] = control.get("outcome")
    if control.get("expectation") != "must_fail":
        return fail("R11_NEGATIVE_CONTROL",
                    f"control expectation={control.get('expectation')!r}, needs 'must_fail'")
    if control.get("outcome") != "fail":
        return fail("R11_NEGATIVE_CONTROL",
                    f"control outcome={control.get('outcome')!r}, needs 'fail'")
    try:
        payload, where = read_payload(control.get("source"), base_dir)
    except Unreadable as exc:
        if (control.get("source") or {}).get("kind") == "inline":
            payload, where = {}, "inline control verdict"
        else:
            return fail("R11_NEGATIVE_CONTROL", f"control not re-readable: {exc}")
    reflected = payload.get("outcome")
    if reflected is not None and reflected != "fail":
        return fail("R11_NEGATIVE_CONTROL",
                    f"control payload records outcome={reflected!r}, declaration says fail")
    log("R11_NEGATIVE_CONTROL", "pass",
        f"must_fail -> observed fail ({where}; payload outcome={reflected or '-'})")

    # R12 ─ does the provenance digest still cover the artifact set? -------
    prov = contract.get("provenance") or {}
    metrics["provenance_declared"] = prov.get("sha256")
    recomputed_digest = provenance_digest(artifacts)
    metrics["provenance_recomputed"] = recomputed_digest
    if prov.get("sha256") != recomputed_digest:
        return fail("R12_PROVENANCE_DIGEST",
                    f"declared {str(prov.get('sha256'))[:12]} != recomputed "
                    f"{recomputed_digest[:12]}")
    log("R12_PROVENANCE_DIGEST", "pass", recomputed_digest[:12])

    # R13 ─ every gate passed ---------------------------------------------
    log("R13_ALL_GATES_PASS", "pass")
    return {
        "grade": "REAL",
        "reason_code": "ALL_GATES_PASS",
        "reasons": [],
        "failed_rule": None,
        "trace": trace,
        "metrics": metrics,
        "contract_version": contract.get("schema_version"),
        "capability_id": contract.get("capability_id"),
    }


# ── batch helpers ───────────────────────────────────────────────────────────
def grade_dir(fixtures_dir, schema: dict | None = None) -> dict:
    """Grade every ``*/contract.json`` under *fixtures_dir*; return report dict."""
    fixtures_dir = Path(fixtures_dir)
    schema = schema if schema is not None else load_schema()
    results = []
    for contract_path in sorted(fixtures_dir.glob("*/contract.json")):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        res = grade_contract(contract, base_dir=contract_path.parent, schema=schema)
        res["case_id"] = contract_path.parent.name
        res["contract_path"] = str(contract_path.relative_to(fixtures_dir.parent))
        res["contract_sha256"] = sha256_file(contract_path)
        results.append(res)
    tally: dict[str, int] = {}
    for r in results:
        tally[r["grade"]] = tally.get(r["grade"], 0) + 1
    return {
        "schema_version": GRADE_REPORT_VERSION,
        "fixtures_dir": str(fixtures_dir),
        "cases": len(results),
        "tally": tally,
        "results": results,
    }


def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cmd_grade(args) -> int:
    contract = _load_json(Path(args.contract))
    base_dir = Path(args.base_dir) if args.base_dir else Path(args.contract).resolve().parent
    res = grade_contract(contract, base_dir=base_dir)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["grade"] == "REAL" else 1


def _cmd_grade_all(args) -> int:
    report = grade_dir(Path(args.fixtures))
    print(json.dumps({"cases": report["cases"], "tally": report["tally"]},
                     ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    return 0


def _cmd_gen_fixtures(args) -> int:
    import evidence_fixtures as FX  # noqa: PLC0415  (sibling module)

    out = Path(args.out)
    manifest = FX.write_fixtures(out, seed=args.seed)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    return 0


def _cmd_run_all(args) -> int:
    import evidence_fixtures as FX  # noqa: PLC0415
    import evidence_reconcile as RC  # noqa: PLC0415

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fixtures = out / "fixtures"
    seed = FX.DEFAULT_SEED if args.seed is None else args.seed
    manifest = FX.write_fixtures(fixtures, seed=seed)
    report = grade_dir(fixtures)
    (out / "grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    self_report = ROOT / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"
    recon = RC.reconcile_file(self_report, fixtures)
    recon_path = out / "reconciliation_vs_self_report.json"
    recon_path.write_text(json.dumps(recon, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    synth = RC.reconcile_synthetic(report, manifest)
    (out / "reconciliation_synthetic.json").write_text(
        json.dumps(synth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metrics = RC.build_metrics(report, manifest, fixtures, reconciliation_path=recon_path)

    metrics["artifacts"] = {
        "grades": "artifacts/evidence_grade/grades.json",
        "fixtures": "artifacts/evidence_grade/fixtures",
        "labels": "artifacts/evidence_grade/fixtures/labels.json",
        "reconciliation_self_report": "artifacts/evidence_grade/reconciliation_vs_self_report.json",
        "reconciliation_synthetic": "artifacts/evidence_grade/reconciliation_synthetic.json",
        "metrics": "artifacts/evidence_grade/metrics.json",
        "schema": "schemas/evidence_contract.schema.json",
        "engine": "tools/evidence_grade.py",
    }
    metrics["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics["seed"] = seed
    (out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tally": report["tally"],
                      "criteria": {k: v.get("pass") for k, v in metrics["criteria"].items()}},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grade", help="grade one contract")
    g.add_argument("--contract", required=True)
    g.add_argument("--base-dir", default=None)
    g.set_defaults(func=_cmd_grade)

    ga = sub.add_parser("grade-all", help="grade a fixture tree")
    ga.add_argument("--fixtures", required=True)
    ga.add_argument("--out", default=None)
    ga.set_defaults(func=_cmd_grade_all)

    gf = sub.add_parser("gen-fixtures", help="regenerate the synthetic fixture set")
    gf.add_argument("--out", default=str(ROOT / "artifacts" / "evidence_grade" / "fixtures"))
    gf.add_argument("--seed", type=int, default=None)
    gf.set_defaults(func=_cmd_gen_fixtures)

    ra = sub.add_parser("run-all", help="fixtures + grades + reconciliation + metrics")
    ra.add_argument("--out", default=str(ROOT / "artifacts" / "evidence_grade"))
    ra.add_argument("--seed", type=int, default=None)
    ra.set_defaults(func=_cmd_run_all)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())