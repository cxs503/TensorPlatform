"""Shared builders for the p262w1-N07 capability auto-audit tests.

All capability ids are synthetic (``syn.*``): the engine is domain free, so the
fixtures are too.
"""
from __future__ import annotations

import json
from pathlib import Path

SIX_ELEMENTS = ("case", "method", "results", "figures", "notes", "error_judgement")


def write_case(
    base: Path,
    capability_id: str,
    *,
    pytest_kind: str = "junit",
    passed: int = 5,
    failed: int = 0,
    elements: tuple | None = SIX_ELEMENTS,
    measured: float = 1.02,
    reference: float = 1.0,
    claimed_rel_error=None,
    review=None,
    artifact_digest: str | None = "a" * 64,
) -> Path:
    """Materialise one ``verified/<capability_id>/`` evidence bundle."""
    case_dir = Path(base) / capability_id
    case_dir.mkdir(parents=True, exist_ok=True)

    if artifact_digest is not None:
        (case_dir / "artifact.json").write_text(
            json.dumps({"case_id": f"case::{capability_id}", "inputs_sha256": artifact_digest}),
            encoding="utf-8")

    if pytest_kind == "junit":
        cases = [f'<testcase classname="{capability_id}" name="t{i}"/>' for i in range(passed)]
        cases += [f'<testcase classname="{capability_id}" name="f{i}">'
                  f'<failure message="boom"/></testcase>' for i in range(failed)]
        xml = (f'<?xml version="1.0" encoding="utf-8"?><testsuites>'
               f'<testsuite name="{capability_id}" tests="{len(cases)}">'
               + "".join(cases) + "</testsuite></testsuites>")
        (case_dir / "pytest.xml").write_text(xml, encoding="utf-8")
    elif pytest_kind == "json":
        (case_dir / "pytest.json").write_text(
            json.dumps({"passed": passed, "failed": failed, "total": passed + failed}),
            encoding="utf-8")
    elif pytest_kind == "none":
        pass
    else:  # pragma: no cover - typo guard
        raise ValueError(pytest_kind)

    if elements is not None:
        (case_dir / "report.json").write_text(
            json.dumps({k: f"content::{k}" for k in elements}), encoding="utf-8")

    rel = abs(measured - reference) / abs(reference)
    (case_dir / "metrics.json").write_text(
        json.dumps({"measured": measured, "reference": reference,
                    "rel_error": rel if claimed_rel_error is None else claimed_rel_error}),
        encoding="utf-8")

    payload = review if review is not None else {
        "reviewer": "independent-reviewer-1", "verdict": "PASS", "independent": True}
    (case_dir / "review.json").write_text(json.dumps(payload), encoding="utf-8")
    return case_dir