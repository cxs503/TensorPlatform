"""Digitisation pipeline: archived table artefacts (CSV) -> validated points.

The pipeline is deliberately dumb and auditable:

    archived artefact (CSV, sha256 anchored)
        -> ``read_table``        : parse (no header magic, no unit inference)
        -> ``dual_pass_report``  : compare independent transcription A vs B
        -> ``require_dual_pass`` : hard gate (pointwise relative difference)

Two independent transcription passes are mandatory before an entry may be
served.  In this repository the two passes are produced by two *independent
transcription programs* (``tools/build_reference_corpus.py``: pass A transcribes
the published formula/closed form at machine precision, pass B re-types the
number as it appears in the printed table, i.e. rounded to the printed
precision, with independent ordering and header conventions).  The pass
disagreement is therefore dominated by print rounding, which is exactly the
digitisation error the corpus advertises.  Human double-keying is *not* claimed
-- see the report's limitations section.
"""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .errors import DualPassViolation

DEFAULT_GATE = 1e-3


def sha256_file(path: str | Path) -> str:
    """sha256 of an archived artefact, byte-exact."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_table(path: str | Path) -> Tuple[List[float], List[float]]:
    """Read a two-column table artefact (comma or tab separated, header optional).

    Deliberately minimal: no unit inference, no column guessing, no silent
    reordering.  Lines that do not parse as two numbers (headers, comments) are
    skipped; anything else that looks malformed raises.
    """
    xs: List[float] = []
    ys: List[float] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "\t" if "\t" in line else ","
        parts = [p for p in line.split(sep)]
        if len(parts) < 2:
            raise ValueError(f"{path}: row with fewer than two columns: {line!r}")
        try:
            x = float(parts[0])
            y = float(parts[1])
        except ValueError:
            continue  # header / comment line
        xs.append(x)
        ys.append(y)
    if len(xs) < 2:
        raise ValueError(f"{path}: fewer than two data rows")
    if any(xs[i] >= xs[i + 1] for i in range(len(xs) - 1)):
        raise ValueError(f"{path}: abscissa is not strictly increasing")
    return xs, ys


def pointwise_rel_diff(
    xs_a: Sequence[float],
    ys_a: Sequence[float],
    xs_b: Sequence[float],
    ys_b: Sequence[float],
    gate_scale: float | None = None,
) -> Dict[str, object]:
    """Pointwise relative difference between two independent transcriptions.

    The two passes must share the same abscissa grid (a different grid is itself
    a transcription error and raises).  ``gate_scale`` floors the denominator so
    that a tabulated quantity crossing zero cannot produce a spurious infinity;
    entries that cross zero therefore declare an absolute scale.
    """
    if len(xs_a) != len(xs_b):
        raise ValueError(f"pass A has {len(xs_a)} rows, pass B has {len(xs_b)} rows")
    diffs: List[float] = []
    worst_i = 0
    ymax = max(abs(v) for v in list(ys_a) + list(ys_b))
    scale = gate_scale if gate_scale is not None else ymax * 1e-6
    for i, (xa, xb) in enumerate(zip(xs_a, xs_b)):
        if abs(xa - xb) > 0.0:
            raise ValueError(f"row {i}: abscissa mismatch between passes ({xa!r} vs {xb!r})")
        denom = max(abs(ys_a[i]), abs(ys_b[i]), scale)
        diffs.append(abs(ys_a[i] - ys_b[i]) / denom)
    worst = max(diffs) if diffs else 0.0
    worst_i = int(max(range(len(diffs)), key=lambda i: diffs[i])) if diffs else 0
    return {
        "n_points": len(diffs),
        "max_rel_diff": float(worst),
        "mean_rel_diff": float(sum(diffs) / len(diffs)) if diffs else 0.0,
        "worst_row": worst_i,
        "worst_x": float(xs_a[worst_i]) if xs_a else math.nan,
        "rel_diffs": diffs,
    }


def dual_pass_report(path_a: str | Path, path_b: str | Path, gate_scale: float | None = None) -> Dict[str, object]:
    """Full dual-pass report for one table (two independent transcriptions)."""
    xs_a, ys_a = read_table(path_a)
    xs_b, ys_b = read_table(path_b)
    rep = pointwise_rel_diff(xs_a, ys_a, xs_b, ys_b, gate_scale=gate_scale)
    rep["pass_a"] = str(path_a)
    rep["pass_b"] = str(path_b)
    rep["sha256_pass_a"] = sha256_file(path_a)
    rep["sha256_pass_b"] = sha256_file(path_b)
    return rep


def require_dual_pass(entry_id: str, report: Dict[str, object], gate: float = DEFAULT_GATE) -> Dict[str, object]:
    """Hard gate: raise ``DualPassViolation`` when the two passes disagree."""
    max_rel = float(report["max_rel_diff"])
    if not (max_rel <= gate):
        raise DualPassViolation(entry_id, max_rel, gate)
    out = dict(report)
    out["gate"] = gate
    out["pass"] = True
    return out