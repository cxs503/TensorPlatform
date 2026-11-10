"""Configuration-injected benchmark dataset generator.

Design contract
---------------
* **Body-agnostic / zero domain vocabulary**: the generator never mentions a
  vessel class, project or fleet.  A body is seven numbers (see
  :class:`tools.benchmark_gen.numeric.BodyForm`) supplied through a JSON spec;
  a condition is an immersion ratio plus fluid properties and a speed list.
  The same code therefore serves any body family and any physical domain whose
  observables reduce to volume integrals of a parametric geometry — the domain
  tag is injected (``domain_key``), never hard-coded.
* **Extensible pipeline registry**: the computing step is looked up by name, or
  by a dotted ``module:callable`` reference, so a new domain pipeline can be
  plugged in through configuration only (proved by ``tests/benchmark_gen``).
* **Provenance**: every dataset carries a ``provenance_key`` derived from
  parameters + seed + generator version (+ code fingerprint) and a
  ``content_sha256`` over the payload; see
  :mod:`tools.benchmark_gen.provenance`.
* **Verification anchor**: each dataset ships the *closed-form* reference of the
  same quantities and the relative deviation, so the analytic gate is a property
  of the artefact itself, not of the test suite.

CLI
---
    python -m tools.benchmark_gen --spec-dir tools/benchmark_gen/specs \\
        --out artifacts/benchmark_gen/datasets --seed 20260101
    python -m tools.benchmark_gen --verify artifacts/benchmark_gen/datasets
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import analytic, numeric, provenance

__all__ = [
    "GENERATOR_VERSION",
    "ANALYTIC_TOLERANCE",
    "PIPELINES",
    "register_pipeline",
    "resolve_pipeline",
    "hydrostatics_pipeline",
    "generate_dataset",
    "generate_matrix",
    "load_spec_directory",
    "code_fingerprint",
    "main",
]

GENERATOR_VERSION = "1.0.0"

#: Acceptance gate A3 — relative deviation against the closed-form reference.
ANALYTIC_TOLERANCE = 1.0e-3

DEFAULT_GRID: Dict[str, int] = {
    "n_stations": 801,
    "n_waterlines": 401,
    "surface_cells_x": 100,
    "surface_cells_z": 50,
}

#: Monte-Carlo volume sanity check (independent third method; seed-controlled).
DEFAULT_MC_SAMPLES = 20000
MC_SIGMA_GATE = 4.0

PIPELINES: Dict[str, Callable[[Mapping[str, Any]], Dict[str, Any]]] = {}

_FINGERPRINT_FILES = ("numeric.py", "analytic.py", "generator.py", "provenance.py")


def code_fingerprint() -> str:
    """sha256 fingerprint of the generator source files (provenance input)."""
    root = Path(__file__).resolve().parent
    return provenance.code_fingerprint([root / name for name in _FINGERPRINT_FILES])


def register_pipeline(name: str, func: Callable[[Mapping[str, Any]], Dict[str, Any]]) -> None:
    """Register a computing pipeline under ``name`` (idempotent re-registration)."""
    if not isinstance(name, str) or not name:
        raise ValueError("pipeline name must be a non-empty string")
    if not callable(func):
        raise TypeError("pipeline must be callable")
    PIPELINES[name] = func


def resolve_pipeline(ref: str) -> Callable[[Mapping[str, Any]], Dict[str, Any]]:
    """Resolve ``name`` or ``package.module:callable`` to a pipeline callable."""
    if ":" in ref:
        module_name, _, attr = ref.partition(":")
        if not module_name or not attr:
            raise ValueError(f"malformed pipeline reference: {ref!r}")
        module = importlib.import_module(module_name)
        func = getattr(module, attr)
        if not callable(func):
            raise TypeError(f"pipeline {ref!r} is not callable")
        return func
    try:
        return PIPELINES[ref]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown pipeline {ref!r}; registered: {sorted(PIPELINES)}") from exc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _compare(numeric_values: Mapping[str, float], reference_values: Mapping[str, float],
             scale: float) -> Dict[str, Any]:
    """Relative deviation of ``numeric`` against the closed-form ``reference``.

    Quantities whose reference is exactly zero (symmetry-trivial centres) are
    compared with an absolute deviation normalised by ``scale`` (a length), so
    the metric stays dimensionless and finite.
    """
    deviations: Dict[str, float] = {}
    for key, ref in reference_values.items():
        if key in ("draft", "waterline_z"):
            continue
        num = float(numeric_values[key])
        ref = float(ref)
        if abs(ref) <= 1e-12 * scale:
            deviations[key] = abs(num - ref) / scale
        else:
            deviations[key] = abs(num - ref) / abs(ref)
    worst_key = max(deviations, key=lambda k: deviations[k])
    worst = deviations[worst_key]
    return {
        "relative_deviation": deviations,
        "max_relative_deviation": worst,
        "worst_metric": worst_key,
        "tolerance": ANALYTIC_TOLERANCE,
        "pass": worst <= ANALYTIC_TOLERANCE,
    }


def monte_carlo_volume(form: numeric.BodyForm, draft: float, *, n_samples: int,
                       seed: int) -> Dict[str, float]:
    """Independent volume estimate by uniform sampling in the bounding box."""
    z_wl = form.waterline_z(draft)
    rng = random.Random(seed)
    box = form.length * form.beam * (z_wl + form.form_depth)
    hits = 0
    for _ in range(int(n_samples)):
        x = rng.uniform(-0.5 * form.length, 0.5 * form.length)
        y = rng.uniform(-0.5 * form.beam, 0.5 * form.beam)
        z = rng.uniform(-form.form_depth, z_wl)
        if abs(y) <= form.half_breadth(x, z):
            hits += 1
    p = hits / float(n_samples)
    volume = box * p
    stderr = box * math.sqrt(max(p * (1.0 - p), 0.0) / n_samples)
    return {"n_samples": int(n_samples), "seed": int(seed),
            "volume_estimate": volume, "volume_stderr": stderr,
            "hit_fraction": p}


# ---------------------------------------------------------------------------
# built-in pipeline: hydrostatics of a parametric body
# ---------------------------------------------------------------------------
def hydrostatics_pipeline(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Volume integrals / stability radii of one body at one condition."""
    body_spec = dict(request["body"]["params"])
    condition = dict(request["condition"]["params"])
    seed = int(request.get("seed", 0))
    grid = {**DEFAULT_GRID, **dict(request.get("grid") or {})}
    form = numeric.BodyForm.from_spec(body_spec)

    draft_ratio = condition.get("draft_ratio")
    if condition.get("draft_m") is not None:
        draft = float(condition["draft_m"])
    elif draft_ratio is not None:
        draft = float(draft_ratio) * form.form_depth
    else:
        raise ValueError("condition must provide 'draft_ratio' or 'draft_m'")
    density = float(condition.get("density", 1025.0))
    gravity = float(condition.get("gravity", 9.81))
    nu = condition.get("kinematic_viscosity")
    speeds = [float(v) for v in condition.get("speeds_ms", [])]

    num = numeric.hydrostatics(form, draft,
                               n_stations=int(grid["n_stations"]),
                               n_waterlines=int(grid["n_waterlines"]))
    ref = analytic.closed_form_hydrostatics(form, draft)
    comparison = _compare(num, ref, scale=form.length)

    surf = numeric.wetted_surface(form, draft,
                                  n_x=int(grid["surface_cells_x"]),
                                  n_z=int(grid["surface_cells_z"]))
    surf_ref = analytic.surface_reference(form, draft)
    surf_dev = abs(surf - surf_ref) / surf_ref

    mc = monte_carlo_volume(form, draft,
                            n_samples=int(request.get("mc_samples", DEFAULT_MC_SAMPLES)),
                            seed=seed)
    mc_dev = abs(mc["volume_estimate"] - ref["displacement_volume"])
    mc_z = mc_dev / mc["volume_stderr"] if mc["volume_stderr"] > 0 else math.inf

    conditions = []
    for v in speeds:
        entry = {"speed_ms": v, "froude": v / math.sqrt(gravity * form.length)}
        if nu:
            entry["reynolds"] = v * form.length / float(nu)
        conditions.append(entry)

    return {
        "pipeline": "hydrostatics",
        "domain_key": str(request.get("domain_key", "generic_continuum_body")),
        "body": {"id": request["body"].get("id"), "params": form.as_dict()},
        "condition": {
            "id": request["condition"].get("id"),
            "draft_m": draft,
            "draft_ratio_to_form_depth": draft / form.form_depth,
            "density_kg_m3": density,
            "gravity_m_s2": gravity,
            "kinematic_viscosity_m2_s": None if nu is None else float(nu),
            "speeds_ms": speeds,
        },
        "results": {
            "displacement_volume_m3": num["displacement_volume"],
            "displacement_mass_kg": density * num["displacement_volume"],
            "midship_section_area_m2": num["section_area_midship"],
            "waterplane_area_m2": num["waterplane_area"],
            "block_coefficient": num["cb"],
            "midship_coefficient": num["cm"],
            "prismatic_coefficient": num["cp"],
            "waterplane_coefficient": num["cwp"],
            "vertical_prismatic_coefficient": num["cvp"],
            "kb_m": num["kb"],
            "lcb_m": num["lcb"],
            "lcf_m": num["lcf"],
            "transverse_metacentric_radius_m": num["bmt"],
            "longitudinal_metacentric_radius_m": num["bml"],
            "kmt_m": num["kmt"],
            "wetted_surface_m2": surf,
        },
        "derived": {
            "speed_cases": conditions,
            "displacement_to_length_ratio": (density * num["displacement_volume"]) / form.length ** 3,
        },
        "reference": {
            "source": "closed_form_beta_function",
            "evidence_level": "analytical",
            "values": {k: v for k, v in ref.items()},
            "surface": {"source": "high_order_gauss_quadrature",
                        "evidence_level": "quadrature",
                        "value_m2": surf_ref,
                        "relative_deviation": surf_dev},
            **comparison,
        },
        "monte_carlo_check": {
            "method": "uniform_sampling_bounding_box",
            **mc,
            "deviation_vs_closed_form_m3": mc_dev,
            "sigmas": mc_z,
            "gate_sigmas": MC_SIGMA_GATE,
            "pass": bool(mc_z <= MC_SIGMA_GATE),
        },
        "grids": {
            "hydrostatics": num["grid"],
            "surface_cells": {"n_x": int(grid["surface_cells_x"]),
                              "n_z": int(grid["surface_cells_z"])},
        },
    }


register_pipeline("hydrostatics", hydrostatics_pipeline)


# ---------------------------------------------------------------------------
# dataset / matrix assembly
# ---------------------------------------------------------------------------
def generate_dataset(body: Mapping[str, Any], condition: Mapping[str, Any], *,
                     seed: int = 20260101, pipeline: str = "hydrostatics",
                     domain_key: str = "generic_continuum_body",
                     grid: Optional[Mapping[str, int]] = None,
                     mc_samples: int = DEFAULT_MC_SAMPLES,
                     generator_version: str = GENERATOR_VERSION,
                     fingerprint: Optional[str] = None) -> Dict[str, Any]:
    """Generate one dataset document for ``(body, condition)``.

    ``body`` / ``condition`` are spec mappings of shape ``{"id": str, "params":
    {...}}``; unknown pipeline names and malformed specs raise immediately.
    """
    for label, spec in (("body", body), ("condition", condition)):
        if not isinstance(spec, Mapping) or "params" not in spec:
            raise ValueError(f"{label} spec must be a mapping with a 'params' key")
    request = {
        "body": {"id": body.get("id"), "params": dict(body["params"])},
        "condition": {"id": condition.get("id"), "params": dict(condition["params"])},
        "seed": int(seed),
        "domain_key": str(domain_key),
        "grid": dict(grid or {}),
        "mc_samples": int(mc_samples),
    }
    content = resolve_pipeline(pipeline)(request)
    if fingerprint is None:
        fingerprint = code_fingerprint()
    return provenance.attach_provenance(
        content,
        generator_version=generator_version,
        code_fingerprint=fingerprint,
        pipeline=pipeline,
        seed=int(seed),
        body=request["body"],
        condition=request["condition"],
        extra={"domain_key": str(domain_key),
               "evidence_summary": {
                   "hydrostatics": "numeric_offset_grid + closed_form_reference",
                   "wetted_surface": "cell_gauss + high_order_quadrature_reference",
                   "volume": "monte_carlo_sanity_check",
               }},
    )


def generate_matrix(bodies: Sequence[Mapping[str, Any]],
                    conditions: Sequence[Mapping[str, Any]], *,
                    seed: int = 20260101, pipeline: str = "hydrostatics",
                    domain_key: str = "generic_continuum_body",
                    grid: Optional[Mapping[str, int]] = None,
                    mc_samples: int = DEFAULT_MC_SAMPLES) -> List[Dict[str, Any]]:
    """Full cross product ``bodies x conditions`` (one dataset per cell)."""
    fingerprint = code_fingerprint()
    docs: List[Dict[str, Any]] = []
    for body in bodies:
        for condition in conditions:
            docs.append(generate_dataset(
                body, condition, seed=seed, pipeline=pipeline, domain_key=domain_key,
                grid=grid, mc_samples=mc_samples, fingerprint=fingerprint))
    return docs


def load_spec_directory(path: str | Path) -> Dict[str, Any]:
    """Read ``bodies/*.json`` and ``conditions/*.json`` plus ``pipeline.json``."""
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"spec directory not found: {root}")

    def _load(kind: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for file in sorted((root / kind).glob("*.json")):
            data = json.loads(file.read_text(encoding="utf-8"))
            data.setdefault("id", file.stem)
            if "params" not in data:
                raise ValueError(f"{file}: missing 'params'")
            items.append(data)
        if not items:
            raise ValueError(f"{root / kind}: no spec files found")
        return items

    meta: Dict[str, Any] = {}
    meta_path = root / "pipeline.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "bodies": _load("bodies"),
        "conditions": _load("conditions"),
        "pipeline": meta.get("pipeline", "hydrostatics"),
        "domain_key": meta.get("domain_key", "generic_continuum_body"),
        "seed": int(meta.get("seed", 20260101)),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark dataset generator")
    parser.add_argument("--spec-dir", default=str(Path(__file__).resolve().parent / "specs"),
                        help="directory with bodies/ and conditions/ spec files")
    parser.add_argument("--out", default="artifacts/benchmark_gen/datasets",
                        help="output archive directory")
    parser.add_argument("--seed", type=int, default=None, help="override the spec seed")
    parser.add_argument("--grid", default=None,
                        help="JSON grid override, e.g. '{\"n_stations\":201}'")
    parser.add_argument("--mc-samples", type=int, default=DEFAULT_MC_SAMPLES)
    parser.add_argument("--verify", default=None,
                        help="verify an existing archive directory instead of generating")
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args(argv)

    if args.verify:
        report = provenance.verify_archive(args.verify,
                                           current_code_fingerprint=code_fingerprint())
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1

    specs = load_spec_directory(args.spec_dir)
    grid = json.loads(args.grid) if args.grid else None
    seed = args.seed if args.seed is not None else specs["seed"]
    docs = generate_matrix(specs["bodies"], specs["conditions"], seed=seed,
                           pipeline=specs["pipeline"], domain_key=specs["domain_key"],
                           grid=grid, mc_samples=args.mc_samples)
    manifest = provenance.write_archive(
        docs, args.out, generator_version=GENERATOR_VERSION,
        code_fingerprint=code_fingerprint(),
        metadata={"seed": seed, "pipeline": specs["pipeline"],
                  "domain_key": specs["domain_key"],
                  "n_bodies": len(specs["bodies"]),
                  "n_conditions": len(specs["conditions"]),
                  "analytic_tolerance": ANALYTIC_TOLERANCE})
    summary = {
        "n_datasets": manifest["n_datasets"],
        "self_verify": provenance.verify_archive(args.out,
                                                 current_code_fingerprint=code_fingerprint())["ok"],
        "datasets": [
            {"dataset_id": doc["dataset_id"],
             "body": doc["content"]["body"]["id"],
             "condition": doc["content"]["condition"]["id"],
             "max_relative_deviation": doc["content"]["reference"]["max_relative_deviation"],
             "worst_metric": doc["content"]["reference"]["worst_metric"],
             "pass": doc["content"]["reference"]["pass"]}
            for doc in docs],
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"wrote {summary['n_datasets']} datasets to {args.out} "
              f"(self-verify ok={summary['self_verify']})")
        for row in summary["datasets"]:
            print(f"  {row['dataset_id'][:16]}…  {row['body']:<18} {row['condition']:<20} "
                  f"max_dev={row['max_relative_deviation']:.3e} ({row['worst_metric']}) "
                  f"pass={row['pass']}")
    return 0 if summary["self_verify"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())