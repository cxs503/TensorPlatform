#!/usr/bin/env python3
"""High-quality precedent library + PLAYBOOK injection (IN24-E3).

What this module owns
---------------------
1. **Case store** (``data/precedents.json``): graded, evidence-backed precedents
   distilled from real battle history — S1 executed-to-spec, S2 baseline-controlled
   improvement, S3 capability build, S4 breakthrough (declared vacant when absent),
   and anti-cases (formal closure / spinning / machine-scan passing itself off as
   final review).
2. **Validation** (``validate_cases``): each grade carries its own evidentiary bar.
   A case that only has "it ran" cannot claim S2; an S2 without an A/B baseline is
   rejected; an S4 without independent review must be marked vacant; an anti-case
   without observed-failure evidence is rejected. Bars are code, not prose.
3. **Injection** (``render_playbook_block`` / ``inject_playbook``): render one
   <=120-char line per distilled rule and append it to the PLAYBOOK section of the
   agent bootstrap file. Injection is append-only and idempotent (case-id token
   dedupe), never rewrites existing lines.

Zero domain semantics
---------------------
This module (code) contains no discipline-specific vocabulary: domain terms live in
``data/precedents.json`` and in the target bootstrap file, i.e. they arrive as data.
Grading vocabulary (S1-S5 / S-N, E0-E4, reviewer kinds) is imported from
``tools.outcome_evaluations`` so there is exactly one definition in the tree.

CLI
---
    python3 -m tools.precedent_library.library validate
    python3 -m tools.precedent_library.library render
    python3 -m tools.precedent_library.library inject --target /path/to/BOOTSTRAP.md            # dry run
    python3 -m tools.precedent_library.library inject --target /path/to/BOOTSTRAP.md --apply    # append
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.outcome_evaluations import EVIDENCE_KINDS

DATA_DIR = Path(__file__).with_name("data")
CASES_PATH = DATA_DIR / "precedents.json"

#: Allowed grades in this library. Positive grades are the S-layer; ``anti`` is the
#: negative track (kept separate, never counted into the positive distribution).
POSITIVE_GRADES: tuple[str, ...] = ("S1", "S2", "S3", "S4")
ANTI_GRADE = "anti"
ALL_GRADES: tuple[str, ...] = POSITIVE_GRADES + (ANTI_GRADE,)

#: Injection line budget (characters, CJK counts as one). PLAYBOOK discipline.
MAX_LINE_CHARS = 120

#: Anchor heading that terminates the PLAYBOOK section in the bootstrap file.
PLAYBOOK_ANCHOR = "## 逃生规则"

_CASE_TOKEN_RE = re.compile(r"E3-(?:S\d|A)-\d{2}")


class PrecedentError(ValueError):
    """Raised when the case store or an injection target violates the contract."""


# ─────────────────────────────── model ───────────────────────────────

@dataclass(frozen=True)
class Case:
    id: str
    grade: str
    title: str
    repo: str
    problem: str
    delta: str
    why_grade: str
    injection_line: str
    evidence: tuple[Mapping[str, Any], ...]
    missions: tuple[int, ...] = ()
    commits: tuple[str, ...] = ()
    baseline: Mapping[str, Any] | None = None
    status: str = "active"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Case":
        return cls(
            id=str(raw.get("id", "")),
            grade=str(raw.get("grade", "")),
            title=str(raw.get("title", "")),
            repo=str(raw.get("repo", "")),
            problem=str(raw.get("problem", "")),
            delta=str(raw.get("delta", "")),
            why_grade=str(raw.get("why_grade", "")),
            injection_line=str(raw.get("injection_line", "")),
            evidence=tuple(raw.get("evidence", ()) or ()),
            missions=tuple(int(m) for m in raw.get("missions", ()) or ()),
            commits=tuple(str(c) for c in raw.get("commits", ()) or ()),
            baseline=raw.get("baseline"),
            status=str(raw.get("status", "active")),
        )

    @property
    def is_positive(self) -> bool:
        return self.grade in POSITIVE_GRADES

    @property
    def case_token(self) -> str:
        match = _CASE_TOKEN_RE.search(self.id)
        return match.group(0) if match else self.id


def load_cases(path: str | Path | None = None) -> tuple[Mapping[str, Any], list[Case]]:
    """Load the case store; returns (header metadata, cases)."""
    target = Path(path) if path is not None else CASES_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise PrecedentError(f"case store unreadable: {target}: {exc}") from exc
    except ValueError as exc:
        raise PrecedentError(f"case store is not valid JSON: {target}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise PrecedentError("case store must be an object with a 'cases' list")
    return raw, [Case.from_mapping(item) for item in raw["cases"]]


# ─────────────────────────── grading bars ───────────────────────────

def _require_text(case: Case, field: str, minimum: int = 1) -> list[str]:
    value = getattr(case, field)
    if len(value.strip()) < minimum:
        return [f"{case.id}: field '{field}' must be >= {minimum} chars"]
    return []


def _require_numbers(text: str, case: Case, field: str) -> list[str]:
    if not re.search(r"\d", text):
        return [f"{case.id}: {field} must cite numeric evidence (no digits found)"]
    return []


def validate_case(case: Case) -> list[str]:
    """Return a list of violations (empty == valid). Grade-specific bars are code."""
    errors: list[str] = []
    if case.grade not in ALL_GRADES:
        errors.append(f"{case.id}: grade '{case.grade}' not in {ALL_GRADES}")
    for field in ("title", "problem", "delta", "why_grade", "injection_line", "repo"):
        errors.extend(_require_text(case, field))

    # Injection line budget (characters, not bytes): PLAYBOOK lines are one bullet.
    if len(case.injection_line) > MAX_LINE_CHARS:
        errors.append(
            f"{case.id}: injection_line is {len(case.injection_line)} chars "
            f"> {MAX_LINE_CHARS}"
        )
    if not _CASE_TOKEN_RE.search(case.id):
        errors.append(f"{case.id}: id must carry an E3-Sn-nn / E3-A-nn token")

    # Evidence: every case needs at least one reference of a whitelisted kind.
    if not case.evidence:
        errors.append(f"{case.id}: evidence must not be empty")
    for item in case.evidence:
        kind = str(item.get("kind", ""))
        ref = str(item.get("ref", "")).strip()
        if kind not in EVIDENCE_KINDS:
            errors.append(f"{case.id}: evidence kind '{kind}' not in {EVIDENCE_KINDS}")
        if not ref:
            errors.append(f"{case.id}: evidence entry with empty ref")

    errors.extend(_require_numbers(case.delta, case, "delta"))
    errors.extend(_require_numbers(case.why_grade, case, "why_grade"))

    if case.grade == "S1":
        # Executed to spec only: no self-declared improvement baseline.
        if case.baseline is not None:
            errors.append(
                f"{case.id}: S1 must not claim a comparison baseline "
                "(that is an S2 bar) — use grade S2"
            )
    elif case.grade == "S2":
        base = case.baseline
        if not isinstance(base, Mapping):
            errors.append(f"{case.id}: S2 requires a baseline dict with before/after/metric/delta")
        else:
            missing = [k for k in ("before", "after", "metric", "delta") if not base.get(k)]
            if missing:
                errors.append(f"{case.id}: S2 baseline missing keys {missing}")
            else:
                errors.extend(_require_numbers(str(base["delta"]), case, "baseline.delta"))
    elif case.grade == "S3":
        # Capability build must name the failure mode it removes AND ship artifacts.
        kinds = {str(item.get("kind", "")) for item in case.evidence}
        if not kinds & {"artifact", "run", "commit"}:
            errors.append(
                f"{case.id}: S3 needs artifact/run/commit evidence "
                "(capability must be inspectable, not asserted)"
            )
        if len(case.evidence) < 2:
            errors.append(f"{case.id}: S3 needs >= 2 independent evidence refs")
    elif case.grade == "S4":
        # Breakthrough is either declared vacant or backed by independent review.
        independent = any(
            "independent" in json.dumps(dict(item), ensure_ascii=False).lower()
            for item in case.evidence
        )
        if case.status != "vacant" and not independent:
            errors.append(
                f"{case.id}: S4 needs independent review evidence or status='vacant'"
            )
        if case.status == "vacant" and not case.problem.strip():
            errors.append(f"{case.id}: vacant S4 must explain the gap")
    else:  # anti-case
        if case.missions and not case.evidence:
            errors.append(f"{case.id}: anti-case must cite the observed failure")
        if not case.missions and not case.evidence:
            errors.append(f"{case.id}: anti-case needs mission ids or evidence refs")
    return errors


def validate_cases(cases: Sequence[Case], minimum: int = 8) -> list[str]:
    """Whole-store validation: count, unique ids, >=1 anti-case, S4 present."""
    errors: list[str] = []
    if len(cases) < minimum:
        errors.append(f"library has {len(cases)} cases, need >= {minimum}")
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"duplicate case ids: {dupes}")
    if not any(c.grade == ANTI_GRADE for c in cases):
        errors.append("library needs >= 1 anti-case (grade 'anti')")
    if not any(c.grade == "S4" for c in cases):
        errors.append("library must acknowledge the S4 track (even as vacant)")
    lines = [c.injection_line for c in cases]
    if len(set(lines)) != len(lines):
        errors.append("injection lines must be unique")
    for case in cases:
        errors.extend(validate_case(case))
    return errors


# ────────────────────────── injection channel ──────────────────────────

def render_playbook_block(
    cases: Sequence[Case],
    *,
    only_positive: bool = False,
    gate_line: str | None = None,
) -> list[str]:
    """Render the append-only PLAYBOOK bullet lines (gate checklist first)."""
    selected = [c for c in cases if (c.is_positive or not only_positive)]
    lines: list[str] = []
    if gate_line:
        gate = gate_line.strip()
        gate = gate if gate.startswith("- ") else "- " + gate
        if len(gate) > MAX_LINE_CHARS:
            raise PrecedentError(f"gate line {len(gate)} chars > {MAX_LINE_CHARS}")
        lines.append(gate)
    for case in selected:
        line = case.injection_line.strip()
        if not line.startswith("- "):
            line = "- " + line
        if len(line) > MAX_LINE_CHARS:
            raise PrecedentError(
                f"{case.id}: rendered line {len(line)} chars > {MAX_LINE_CHARS}"
            )
        lines.append(line)
    return lines


def inject_playbook(
    target: str | Path,
    lines: Iterable[str],
    *,
    anchor: str = PLAYBOOK_ANCHOR,
    apply: bool = False,
) -> dict[str, Any]:
    """Append ``lines`` to the PLAYBOOK section, idempotently and append-only.

    Invariants (tested):
    - existing bytes are preserved in order (no rewrite of prior lines);
    - a line whose case token is already present is skipped (re-run safe);
    - new lines land immediately before ``anchor`` heading;
    - missing anchor -> PrecedentError (never silently append to the wrong section).
    """
    path = Path(target)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PrecedentError(f"injection target unreadable: {path}: {exc}") from exc
    if anchor not in text:
        raise PrecedentError(f"injection anchor {anchor!r} not found in {path}")

    new_lines: list[str] = []
    skipped: list[str] = []
    for line in lines:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if len(line) > MAX_LINE_CHARS:
            raise PrecedentError(f"line exceeds {MAX_LINE_CHARS} chars: {line[:40]}...")
        tokens = _CASE_TOKEN_RE.findall(line)
        if tokens and all(t in text for t in tokens):
            skipped.append(line)
            continue
        if line in text:
            skipped.append(line)
            continue
        new_lines.append(line)

    if apply and new_lines:
        head, tail = text.split(anchor, 1)
        patched = head.rstrip("\n") + "\n" + "\n".join(new_lines) + "\n\n" + anchor + tail
        path.write_text(patched, encoding="utf-8")
        written = path.read_text(encoding="utf-8")
        # Receipt check: every new line must be readable back.
        missing = [line for line in new_lines if line not in written]
        if missing:
            raise PrecedentError(f"injection receipt failed for {len(missing)} line(s)")

    return {
        "target": str(path),
        "anchor": anchor,
        "applied": bool(apply and new_lines),
        "inserted": new_lines,
        "skipped": skipped,
        "inserted_count": len(new_lines),
        "skipped_count": len(skipped),
    }


def summarize(cases: Sequence[Case]) -> dict[str, Any]:
    """Layered counts only — no weighting, no ranking (mirrors outcome_evaluations)."""
    by_grade: dict[str, int] = {}
    for case in cases:
        by_grade[case.grade] = by_grade.get(case.grade, 0) + 1
    vacant = [c.id for c in cases if c.status == "vacant"]
    return {
        "total": len(cases),
        "positive": sum(1 for c in cases if c.is_positive),
        "anti": sum(1 for c in cases if c.grade == ANTI_GRADE),
        "by_grade": by_grade,
        "vacant": vacant,
        "weighted_score": None,
        "ranking": None,
    }


# ─────────────────────────────── CLI ───────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "render", "summary"):
        sub.add_parser(name)
    inj = sub.add_parser("inject")
    inj.add_argument("--target", required=True)
    inj.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    meta, cases = load_cases()
    if args.cmd == "validate":
        errors = validate_cases(cases)
        print(json.dumps({"ok": not errors, "errors": errors, **summarize(cases)},
                         ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    if args.cmd == "summary":
        print(json.dumps(summarize(cases), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "render":
        for line in render_playbook_block(cases, gate_line=meta.get("injection_gate_line")):
            print(line)
        return 0
    block = render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    result = inject_playbook(args.target, block, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())