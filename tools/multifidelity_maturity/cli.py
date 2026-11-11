"""CLI: run the whole multi-fidelity maturity study and emit the artifact set.

Artifact order is deliberate: the study records are written first, then the
criteria, then the figures, then ``verification.json`` (which hashes everything
that exists at that point but *not* itself), then the report (which cites the
verification digest), and finally ``provenance.json`` with the hashes of the
complete set including the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from .figures import all_figures
from .report import write_report
from .study import build_criteria, clock_study, distill_study, exchange_study

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts" / "multifidelity"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest(project_root: Path) -> Dict[str, object]:
    """Run this module's test files and count tests / negative-control markers."""
    tests: List[str] = sorted(str(p) for p in (project_root / "tests").glob("test_multifidelity_*.py"))
    if not tests:
        return {"collected": 0, "negative_controls": 0, "passed": False, "output": "no test files"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *tests],
        cwd=str(project_root), capture_output=True, text=True)
    collected = 0
    for line in (proc.stdout or "").splitlines():
        stripped = line.replace(",", " ").split()
        for i, token in enumerate(stripped):
            if token in ("passed", "failed") and i > 0 and stripped[i - 1].isdigit():
                collected = max(collected, int(stripped[i - 1]))
                break
        if collected:
            break
    negative = 0
    for path in tests:
        negative += Path(path).read_text(encoding="utf-8").count("def test_negative_")
    return {
        "collected": collected,
        "negative_controls": negative,
        "passed": proc.returncode == 0,
        "output": (proc.stdout or "") + (proc.stderr or "")[-1500:],
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--quick", action="store_true", help="smaller distillation sweep")
    parser.add_argument("--skip-distill", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.quick:
        import tools.multifidelity_maturity.study as study
        study.DISTILL = {**study.DISTILL, "n_soft": 160, "n_val": 80, "widths": (8, 32),
                         "alphas": (0.0, 0.5, 1.0), "epochs": 120}

    print("[1/5] clock synchronisation study")
    clock = clock_study()
    print("[2/5] boundary conservation study")
    exchange = exchange_study()
    distill_path = out / "distill.json"
    if args.skip_distill and distill_path.exists():
        print("[3/5] distillation study: reusing", distill_path)
        distill = json.loads(distill_path.read_text(encoding="utf-8"))
    else:
        print("[3/5] distillation study")
        distill = distill_study(verbose=True)

    (out / "clock_sync.json").write_text(json.dumps(clock, indent=1, default=str), encoding="utf-8")
    (out / "exchange.json").write_text(json.dumps(exchange, indent=1, default=str), encoding="utf-8")
    distill_path.write_text(json.dumps(distill, indent=1, default=str), encoding="utf-8")

    criteria = build_criteria(clock, exchange, distill)
    (out / "criteria.json").write_text(json.dumps(criteria, indent=1, default=str), encoding="utf-8")

    print("[4/5] figures")
    figures = all_figures({"clock": clock, "exchange": exchange, "distill": distill}, out / "figures")

    pytest_summary = ({"collected": None, "negative_controls": None, "passed": None, "output": "skipped"}
                      if args.skip_pytest else run_pytest(ROOT))
    f_pass = None if args.skip_pytest else bool(
        (pytest_summary.get("collected") or 0) >= 15
        and (pytest_summary.get("negative_controls") or 0) >= 2
        and pytest_summary.get("passed"))
    criteria["f_deliverables"].update({
        "pytest_count": pytest_summary.get("collected"),
        "pytest_negative_controls": pytest_summary.get("negative_controls"),
        "pytest_passed": pytest_summary.get("passed"),
        "pass": f_pass,
    })
    (out / "criteria.json").write_text(json.dumps(criteria, indent=1, default=str), encoding="utf-8")

    digests = {
        "clock_sync.json": _sha(out / "clock_sync.json"),
        "exchange.json": _sha(out / "exchange.json"),
        "distill.json": _sha(out / "distill.json"),
        "criteria.json": _sha(out / "criteria.json"),
        **{f"figures/{p.name}": _sha(p) for p in figures},
    }
    record: Dict[str, object] = {"clock": clock, "exchange": exchange, "distill": distill,
                                 "digests": digests, "pytest": pytest_summary}
    verification = out / "verification.json"
    verification.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    verification_digest = _sha(verification)

    print("[5/5] report")
    record_digests = {**digests, "verification": verification_digest}
    write_report({**record, "digests": record_digests}, criteria, pytest_summary, out / "report.md")
    (out / "pytest.json").write_text(json.dumps(pytest_summary, indent=1), encoding="utf-8")
    (out / "pytest.log").write_text(pytest_summary.get("output") or "", encoding="utf-8")

    provenance = {
        "verification.json": verification_digest,
        "report.md": _sha(out / "report.md"),
        "pytest.json": _sha(out / "pytest.json"),
        **{f"figures/{p.name}": _sha(p) for p in figures},
        "criteria.json": _sha(out / "criteria.json"),
        "clock_sync.json": _sha(out / "clock_sync.json"),
        "exchange.json": _sha(out / "exchange.json"),
        "distill.json": _sha(out / "distill.json"),
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=1), encoding="utf-8")

    summary = {
        "criteria": {k: v.get("pass") for k, v in criteria.items()},
        "pytest": {k: pytest_summary.get(k) for k in ("collected", "negative_controls", "passed")},
        "figures": [p.name for p in figures],
        "report": str(out / "report.md"),
        "verification_sha256": verification_digest,
    }
    print(json.dumps(summary, indent=1))
    return 0 if all(v is not False for v in summary["criteria"].values()) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())