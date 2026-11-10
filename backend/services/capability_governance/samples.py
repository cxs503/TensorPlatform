"""Structured sample corpus: build, load, and audit ``ship_samples`` rows.

The sample table is the platform's long-term memory of *computed* numbers.  A
corpus is only useful if every row can be traced back to the exact input that
produced it and if the same input cannot be stored twice — otherwise "10 000
samples" is a row count, not a dataset.

This module therefore does four mechanical things, all model-agnostic:

``build``   expand a declarative spec (axes x conditions x replicates) into rows;
            the numerical model is *declared* in the spec and executed by
            :func:`_solve_ivp`, so replacing the model with a real solver plugin
            does not touch this file
``digest``  ``input_digest_sha256`` over the canonical input record — the dedupe key
``load``    insert into ``ship_samples`` (SQLAlchemy Core, PG or SQLite) skipping
            digests that already exist, so re-runs are idempotent
``audit``   per-axis coverage, duplicate-free ratio, provenance completeness and
            non-empty output ratio, computed from the rows as stored

The corpus spec is *data*: axis labels, model parameters and provenance
requirements live in ``config/capability_governance/sample_spec.json``.  Nothing
in this file names a discipline or a geometry class.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "sample-corpus-spec/1.0"
GENERATOR = "capability_governance.samples/1.0"
PROVENANCE_FIELDS: Sequence[str] = (
    "spec_sha256", "model_kind", "model_params_sha256", "generator",
    "generator_source_sha256", "created_at", "run_id",
)


# ---------------------------------------------------------------------------
# canonicalisation / digests
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def input_digest(inputs: Mapping[str, Any]) -> str:
    """Dedupe key: sha256 of the canonical input record."""
    return digest_of(dict(inputs))


# ---------------------------------------------------------------------------
# numerical model (generic damped oscillator integrated by RK4)
# ---------------------------------------------------------------------------


def _solve_ivp(omega: float, zeta: float, forcing: float, duration: float,
               initial_rate: float, steps: int = 240) -> Dict[str, float]:
    """Integrate y'' + 2*zeta*omega*y' + omega^2*y = forcing*sin(2*omega*t).

    Returns three scalar summaries of the trajectory.  This is a *generic* linear
    second-order ODE; the physical interpretation is supplied by whoever injects
    the spec, never by this file.
    """
    dt = duration / steps
    y, v = 0.0, float(initial_rate)
    peak = 0.0
    integral = 0.0
    t = 0.0
    for _ in range(steps):
        def acc(yy: float, vv: float, tt: float) -> float:
            return forcing * math.sin(2.0 * omega * tt) - 2.0 * zeta * omega * vv - (omega ** 2) * yy

        a1 = acc(y, v, t)
        a2 = acc(y + 0.5 * dt * v, v + 0.5 * dt * a1, t + 0.5 * dt)
        a3 = acc(y + 0.5 * dt * (v + 0.5 * dt * a1), v + 0.5 * dt * a2, t + 0.5 * dt)
        a4 = acc(y + dt * (v + 0.5 * dt * a2), v + dt * a3, t + dt)
        y_new = y + dt * v + dt * dt / 6.0 * (a1 + a2 + a3)
        v_new = v + dt / 6.0 * (a1 + 2 * a2 + 2 * a3 + a4)
        t += dt
        y, v = y_new, v_new
        peak = max(peak, abs(y))
        integral += y * dt
    return {"peak_abs": peak, "final_abs": abs(y), "mean": integral / duration,
            "steps": float(steps)}


MODEL_KINDS = {"ivp_rk4": _solve_ivp}


# ---------------------------------------------------------------------------
# corpus construction
# ---------------------------------------------------------------------------


def load_spec(path: Path) -> Dict[str, Any]:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("schema") != SCHEMA:
        raise ValueError(f"unexpected spec schema '{spec.get('schema')}'")
    for key in ("axes", "condition_grid", "replicates", "model"):
        if key not in spec:
            raise KeyError(f"spec missing required section '{key}'")
    return spec


def _scale_for(replicates: int, index: int, bounds: Sequence[float]) -> float:
    lo, hi = float(bounds[0]), float(bounds[1])
    if replicates == 1:
        return lo + (hi - lo) / 2.0
    return lo + (hi - lo) * index / (replicates - 1)


def iter_rows(spec: Mapping[str, Any], *, run_id: Optional[str] = None,
              created_at: Optional[str] = None, limit: Optional[int] = None,
              source_path: Optional[Path] = None) -> Iterator[Dict[str, Any]]:
    """Expand the spec into rows: inputs, outputs, tags, provenance, digest."""
    spec_digest = digest_of(dict(spec))
    model = spec["model"]
    kind = model["kind"]
    if kind not in MODEL_KINDS:
        raise KeyError(f"unknown model kind '{kind}'")
    solve = MODEL_KINDS[kind]
    replicates = int(spec["replicates"])
    bounds = spec.get("scale_range") or [0.8, 1.2]
    run = run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    src_sha = file_sha256(source_path or Path(__file__))
    params_sha = digest_of(model)
    family_params = model.get("family_params") or {}
    axes = spec["axes"]
    emitted = 0
    for family in axes["family"]:
        fp = dict(family_params.get(family) or {})
        for case_class in axes["case_class"]:
            class_factor = float((axes.get("class_factor") or {}).get(case_class, 1.0))
            for c_idx, cond in enumerate(spec["condition_grid"]):
                for rep in range(replicates):
                    if limit is not None and emitted >= limit:
                        return
                    scale = _scale_for(replicates, rep, bounds) * class_factor
                    inputs = {
                        "family": family,
                        "case_class": case_class,
                        "condition_index": c_idx,
                        "forcing": float(cond["forcing"]) * scale,
                        "duration": float(cond["duration"]),
                        "omega": float(fp.get("omega", 1.0)) * scale,
                        "zeta": float(fp.get("zeta", 0.05)),
                        "initial_rate": float(cond.get("initial_rate", 1.0)),
                        "replicate": rep,
                        "scale": scale,
                    }
                    outputs = solve(inputs["omega"], inputs["zeta"], inputs["forcing"],
                                    inputs["duration"], inputs["initial_rate"])
                    row = {
                        "calc_type": f"{kind}:{family}",
                        "params": inputs,
                        "result": outputs,
                        "tags": {
                            "axis_family": family,
                            "axis_case_class": case_class,
                            "condition_index": c_idx,
                            "replicate": rep,
                            "model_kind": kind,
                        },
                        "input_digest_sha256": input_digest(inputs),
                        "source": spec.get("source", "governance_corpus"),
                        "provenance": {
                            "spec_sha256": spec_digest,
                            "model_kind": kind,
                            "model_params_sha256": params_sha,
                            "generator": GENERATOR,
                            "generator_source_sha256": src_sha,
                            "created_at": stamp,
                            "run_id": run,
                        },
                    }
                    emitted += 1
                    yield row


def write_jsonl(rows: Iterable[Mapping[str, Any]], path: Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


# ---------------------------------------------------------------------------
# database load / audit (backend-agnostic: PostgreSQL in production, SQLite in CI)
# ---------------------------------------------------------------------------


def _ship_samples_table():
    from models.ship_sample import ShipSample  # local import: keeps this module importable standalone
    return ShipSample.__table__


def load_rows(engine, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 500) -> Dict[str, Any]:
    """Insert rows, skipping input digests already present.  Idempotent."""
    from sqlalchemy import select

    table = _ship_samples_table()
    table.metadata.create_all(engine, tables=[table])
    existing = set()
    with engine.connect() as conn:
        for digest in conn.execute(select(table.c.input_digest_sha256)):
            existing.add(digest[0])
    inserted = 0
    skipped = 0
    batch: List[Dict[str, Any]] = []
    with engine.begin() as conn:
        for row in rows:
            if row["input_digest_sha256"] in existing:
                skipped += 1
                continue
            existing.add(row["input_digest_sha256"])
            batch.append(dict(row))
            if len(batch) >= batch_size:
                conn.execute(table.insert(), batch)
                inserted += len(batch)
                batch = []
        if batch:
            conn.execute(table.insert(), batch)
            inserted += len(batch)
    from sqlalchemy import func, select
    with engine.connect() as conn:
        total_after = int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)
    return {"inserted": inserted, "skipped_existing": skipped, "total_after": total_after}


def _existing_digests(engine, table) -> set:
    from sqlalchemy import select
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(select(table.c.input_digest_sha256))}


def audit(engine, *, spec: Optional[Mapping[str, Any]] = None,
          axis_labels: Optional[Mapping[str, Mapping[str, str]]] = None,
          min_per_family: int = 2000, min_per_class: int = 1000,
          min_rows: int = 10000, min_provenance: float = 0.95,
          min_unique: float = 0.99) -> Dict[str, Any]:
    """Compute the corpus acceptance numbers from the rows as stored."""
    from sqlalchemy import select

    table = _ship_samples_table()
    with engine.connect() as conn:
        rows = list(conn.execute(select(table.c.input_digest_sha256, table.c.params,
                                        table.c.result, table.c.tags, table.c.provenance,
                                        table.c.source)))
    total = len(rows)
    family_counts: Dict[str, int] = {}
    class_counts: Dict[str, int] = {}
    digests: List[str] = []
    provenance_complete = 0
    output_non_empty = 0
    for digest, params, result, tags, provenance, _source in rows:
        digests.append(digest)
        tags = tags or {}
        family = tags.get("axis_family")
        case_class = tags.get("axis_case_class")
        family_counts[family] = family_counts.get(family, 0) + 1
        class_counts[case_class] = class_counts.get(case_class, 0) + 1
        prov = provenance or {}
        if all(prov.get(f) for f in PROVENANCE_FIELDS):
            provenance_complete += 1
        if isinstance(result, dict) and result and all(v is not None for v in result.values()):
            output_non_empty += 1

    labels = axis_labels or {}
    fam_label = {k: labels.get("family", {}).get(k, k) for k in family_counts}
    cls_label = {k: labels.get("case_class", {}).get(k, k) for k in class_counts}

    unique = len(set(digests))
    return {
        "schema": "sample-corpus-audit/1.0",
        "n_rows": total,
        "families": family_counts,
        "case_classes": class_counts,
        "family_labels": fam_label,
        "class_labels": cls_label,
        "min_per_family": min(family_counts.values()) if family_counts else 0,
        "min_per_class": min(class_counts.values()) if class_counts else 0,
        "unique_digests": unique,
        "dedupe_unique_rate": (unique / total) if total else 0.0,
        "provenance_complete": provenance_complete,
        "provenance_completeness": (provenance_complete / total) if total else 0.0,
        "output_non_empty_ratio": (output_non_empty / total) if total else 0.0,
        "criteria": {
            "rows_at_least_min": total >= min_rows,
            "every_family_at_least_min": bool(family_counts) and min(family_counts.values()) >= min_per_family,
            "every_class_at_least_min": bool(class_counts) and min(class_counts.values()) >= min_per_class,
            "provenance_at_least_min": ((provenance_complete / total) if total else 0.0) >= min_provenance,
            "unique_at_least_min": ((unique / total) if total else 0.0) >= min_unique,
            "output_non_empty_all": total > 0 and output_non_empty == total,
        },
    }