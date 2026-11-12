#!/usr/bin/env python3
"""Fixture matrix: re-execute every verdict class through the gate and plot it.

The pytest suite asserts each reason value independently; this module *re-runs*
the same verdict classes through the public entry point and records the result
as data (JSON) plus a figure (PNG), so the delivery report never has to quote a
number by hand. Two independent drivers over one judge: the test suite is the
gate, this module is the report's measuring instrument.

Usage::

    python3 -m tools.repro_exec_gate.matrix [--out-dir DIR]

Outputs ``fixture_matrix.json`` and ``fixture_matrix.png``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from . import core, report

#: one case per verdict class; digests are computed at build time (never typed in)
CASES = (
    ("pass_plain_command", "OK", {"repro_cmd": "printf 'a\\nb\\n' > out.txt"}),
    ("pass_artifact_digest", "OK", {"repro_cmd": "printf 'payload-42\\n' > result.txt",
                                    "artifacts": [{"path": "result.txt", "_digest_of": "payload-42\n"}]}),
    ("pass_no_artifacts", "OK", {"repro_cmd": "true"}),
    ("no_repro_cmd", "NO_REPRO_CMD", {"artifacts": [{"path": "x"}]}),
    ("spawn_error_bad_cwd", "SPAWN_ERROR", {"repro_cmd": "true", "_cwd": "/nonexistent-worktree"}),
    ("exit_nonzero", "EXIT_NONZERO", {"repro_cmd": "echo boom >&2; exit 3"}),
    ("artifact_missing", "ARTIFACT_MISSING", {"repro_cmd": "true",
                                              "artifacts": [{"path": "absent.txt"}]}),
    ("artifact_sha_mismatch", "ARTIFACT_SHA_MISMATCH",
     {"repro_cmd": "printf 'actual\\n' > result.txt",
      "artifacts": [{"path": "result.txt", "sha256": "0" * 64}]}),
    ("timeout", "TIMEOUT", {"repro_cmd": "sleep 5", "timeout_s": 0.3}),
)


def _materialize(payload: dict) -> tuple[dict, str | None]:
    """Expand the ``_digit_of`` / ``_cwd`` test hints into a real contract."""
    contract = {k: v for k, v in payload.items() if not k.startswith("_")}
    cwd = payload.get("_cwd")
    if "artifacts" in contract:
        artifacts = []
        for entry in contract["artifacts"]:
            entry = dict(entry)
            body = entry.pop("_digest_of", None)
            if body is not None:
                entry["sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            artifacts.append(entry)
        contract["artifacts"] = artifacts
    return contract, cwd


def run_matrix() -> dict:
    rows = []
    for name, expected, payload in CASES:
        base = Path(tempfile.mkdtemp(prefix=f"repro_gate_{name}_"))
        worktree = base / "wt"
        worktree.mkdir()
        try:
            contract, cwd = _materialize(payload)
            target = Path(cwd) if cwd else worktree
            verdict = core.evaluate(contract, target)
            rows.append({
                "case": name,
                "expected_reason": expected,
                "observed_reason": verdict["reason"],
                "status": verdict["status"],
                "exit_code": verdict["exit_code"],
                "duration_s": verdict["duration_s"],
                "match": verdict["reason"] == expected,
            })
        finally:
            shutil.rmtree(base, ignore_errors=True)
    matched = sum(1 for r in rows if r["match"])
    return {
        "schema_version": "repro-exec-gate-matrix/1.0",
        "gate_version": core.SCHEMA_VERSION,
        "reasons": list(core.REASONS),
        "rows": rows,
        "summary": {"cases": len(rows), "matched": matched,
                    "all_match": matched == len(rows)},
    }


def plot(matrix: dict, out_png: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    reasons = matrix["reasons"]
    rows = matrix["rows"]
    matrix_data = np.zeros((len(rows), len(reasons)))
    for i, row in enumerate(rows):
        matrix_data[i, reasons.index(row["observed_reason"])] = 1.0

    fig, (ax_m, ax_d) = plt.subplots(
        1, 2, figsize=(12.4, 4.6), gridspec_kw={"width_ratios": [1.55, 1.0]})
    ax_m.imshow(matrix_data, cmap="Greens", vmin=0, vmax=1.6, aspect="auto")
    ax_m.set_xticks(range(len(reasons)))
    ax_m.set_xticklabels(reasons, rotation=38, ha="right", fontsize=8)
    ax_m.set_yticks(range(len(rows)))
    ax_m.set_yticklabels([r["case"] for r in rows], fontsize=8)
    for i, row in enumerate(rows):
        j = reasons.index(row["observed_reason"])
        ax_m.text(j, i, "●", ha="center", va="center", fontsize=13,
                  color="#1b5e20" if row["match"] else "#b71c1c")
    ax_m.set_title("observed verdict (green = matches the predeclared reason)", fontsize=9)
    ax_m.set_xlabel("reason enum")
    for spine in ax_m.spines.values():
        spine.set_visible(False)

    durations = [max(r["duration_s"], 1e-3) for r in rows]
    colors = ["#2e7d32" if r["match"] else "#c62828" for r in rows]
    ax_d.barh(range(len(rows)), durations, color=colors, height=0.62)
    ax_d.set_yticks(range(len(rows)))
    ax_d.set_yticklabels([r["case"] for r in rows], fontsize=8)
    ax_d.invert_yaxis()
    ax_d.set_xscale("log")
    ax_d.set_xlabel("wall clock of the executed command [s, log]")
    ax_d.set_title(f"execution cost: {matrix['summary']['matched']}/"
                   f"{matrix['summary']['cases']} verdicts as predeclared", fontsize=9)
    for i, (dur, row) in enumerate(zip(durations, rows)):
        ax_d.text(dur * 1.15, i, f"{row['duration_s']:.3f}s", va="center", fontsize=7)
    ax_d.set_xlim(right=max(durations) * 6)
    ax_d.grid(axis="x", alpha=0.3, which="both")
    fig.suptitle("repro_exec_gate — fixture matrix (data: fixture_matrix.json)", fontsize=10)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return out_png


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tools.repro_exec_gate.matrix",
                                     description=__doc__.splitlines()[0])
    default_dir = Path(__file__).resolve().parents[2] / "artifacts" / "repro_exec_gate"
    parser.add_argument("--out-dir", default=str(default_dir))
    parser.add_argument("--no-figure", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix = run_matrix()
    matrix["payload_sha256"] = report.sha256_text(report.canonical_json(matrix))
    (out_dir / "fixture_matrix.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.no_figure:
        plot(matrix, out_dir / "fixture_matrix.png")
    print(report.canonical_json(matrix["summary"]))
    return 0 if matrix["summary"]["all_match"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())