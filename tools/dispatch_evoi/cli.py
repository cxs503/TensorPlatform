"""CLI: run the whole EVOI evaluation and write the deliverable.

    python -m tools.dispatch_evoi.cli --out artifacts/dispatch_evoi

Writes ``eval_report.json`` (machine-checkable gate numbers + artifact paths),
``eval_report.md`` (paper-style report, every number interpolated) and five PNG
figures.  With ``--with-pytest`` (default) the pytest suite for the module is run
as a subprocess and its exit code becomes gate (f).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from .report import build_report

__all__ = ["main", "run"]


def _pytest_check(path: str = "tests/test_dispatch_evoi.py") -> dict[str, Any]:
    if not os.path.exists(path):
        return {"pass": False, "reason": f"{path} missing", "command": None}
    cmd = [sys.executable, "-m", "pytest", "-q", path]
    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-3:]
    return {
        "pass": proc.returncode == 0,
        "returncode": int(proc.returncode),
        "command": " ".join(cmd),
        "tail": tail,
    }


def run(
    *,
    out: str = "artifacts/dispatch_evoi",
    seed: int = 7,
    other_seed: int = 11,
    rounds: int = 2000,
    slate: int = 8,
    windows: int = 5,
    estimators: int = 30,
    with_pytest: bool = True,
    figures: bool = True,
) -> dict[str, Any]:
    pytest_check = _pytest_check() if with_pytest else None
    payload = build_report(
        out_dir=out,
        seed=seed,
        other_seed=other_seed,
        n_rounds=rounds,
        slate_size=slate,
        n_windows=windows,
        n_estimators=estimators,
        pytest_check=pytest_check,
        figures=figures,
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/dispatch_evoi")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--other-seed", type=int, default=11)
    ap.add_argument("--rounds", type=int, default=2000)
    ap.add_argument("--slate", type=int, default=8)
    ap.add_argument("--windows", type=int, default=5)
    ap.add_argument("--estimators", type=int, default=30)
    ap.add_argument("--no-pytest", action="store_true")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    payload = run(
        out=args.out,
        seed=args.seed,
        other_seed=args.other_seed,
        rounds=args.rounds,
        slate=args.slate,
        windows=args.windows,
        estimators=args.estimators,
        with_pytest=not args.no_pytest,
        figures=not args.no_figures,
    )
    print(json.dumps(payload["verdict"]["checks"], indent=1))
    print("all_pass:", payload["verdict"]["all_pass"])
    print("json:", payload["artifacts"]["json"])
    print("md  :", payload["artifacts"]["markdown"])
    return 0 if payload["verdict"]["all_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())