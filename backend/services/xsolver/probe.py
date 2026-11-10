"""Kernel conservation probe: measure mass / momentum residuals instead of quoting them.

Problem statement (why this is a legitimate conservation test)
--------------------------------------------------------------
Take a periodic box filled with a uniform equilibrium plus a small periodic
perturbation.  In that geometry:

* streaming is a pure permutation of the populations -> mass and momentum are
  transported, not created;
* the BGK collision relaxes ``f`` towards ``f_eq(rho, u)`` whose zeroth and first
  moments are exactly ``rho`` and ``rho u`` -> mass and momentum are conserved.

So the *reference* answer is "zero drift", and any measured drift is a property of
the kernel under test, not of the flow.  The probe records both residuals at
several horizons plus the per-step defect rate, because a drifting kernel can look
conforming at a short horizon and fail at a production horizon -- the horizon is
part of the claim, so it is stored with the number.

Provenance (why a number without an origin is worthless)
--------------------------------------------------------
A residual is only attributable to the commit written next to it if the code that
produced it *is* that checkout.  On this host several solver packages are installed
editable against other agents' worktrees, and ``import tensorlbm`` then resolves to
whichever worktree the install points at -- not to the repo whose commit the probe
was about to record.  Measured on this very box: the unpinned probe imported
``tensorlbm`` from ``/root/tp-agent/work/m6150/src`` while recording the commit of
``/root/tensor-ecosystem/TensorLBM``, and the two copies disagree by six orders of
magnitude on mass conservation (9.3e-7 vs 1.1e-16).  So the backend is imported by
*file location under the declared repo* and a foreign origin is a hard
:class:`ProvenanceError`, never a warning: an unattributable measurement is
withheld, not published.

Scope/limitations (stated, not implied): no walls (a wall exchanges momentum, so
wall-bounded momentum conservation is not claimed), isothermal model (no energy
equation, energy residual is reported as undefined).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import normalize as N
from .jsonschema_lite import load_config

SPEC = load_config("probe")


class ProbeUnavailable(RuntimeError):
    """The backend solver cannot be imported -- the probe is then WITHHELD, never faked."""


class ApiDriftError(ProbeUnavailable):
    """The declared kernel API is not what the resolved module exposes.

    The probe speaks only about the four declared entry points; if the resolved
    checkout has renamed them, the probe has measured nothing relevant and says so
    instead of falling back to whatever import happened to work.
    """


class ProvenanceError(ProbeUnavailable):
    """The resolved module does not live in the declared repo -> the residual would be
    attributed to a commit that did not produce it."""


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# provenance-pinned import
# ---------------------------------------------------------------------------
def _repo_dir(spec: Dict[str, Any]) -> Optional[Path]:
    root = os.environ.get(spec.get("repo_env") or "") or spec.get("repo_default")
    if not root:
        return None
    return Path(root) / spec.get("repo_relpath", "")


def _package_dir(repo_dir: Path, module_name: str) -> Optional[Path]:
    for base in (repo_dir / "src", repo_dir):
        candidate = base / module_name
        if (candidate / "__init__.py").is_file():
            return candidate
    return None


def _sha256(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _shadow_copies(module_name: str, origin: Optional[Path]) -> List[str]:
    """Every *other* importable copy of the package on ``sys.path``.

    This is the hazard the pin exists for; listing the copies makes the audit
    reproducible on a host whose installs drift (`pip install -e` from a worktree
    that is later cleaned up leaves an importable stale tree behind).
    """
    found: List[str] = []
    for entry in list(sys.path):
        if not entry:
            continue
        candidate = Path(entry) / module_name
        if not (candidate / "__init__.py").is_file():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - unreadable path
            continue
        if origin is None or resolved != origin.parent:
            found.append(str(resolved))
    return sorted(set(found))


def _pin_to_repo(module_name: str, package_dir: Path):
    """Import ``module_name`` from ``package_dir`` regardless of installed copies."""
    for name in [n for n in list(sys.modules)
                 if n == module_name or n.startswith(module_name + ".")]:
        del sys.modules[name]
    spec_obj = importlib.util.spec_from_file_location(
        module_name, package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)])
    if spec_obj is None or spec_obj.loader is None:
        raise ProbeUnavailable(f"cannot build an import spec for {package_dir}")
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[module_name] = module
    try:
        spec_obj.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - reported as WITHHELD
        sys.modules.pop(module_name, None)
        raise ProbeUnavailable(
            f"cannot execute {package_dir} as {module_name!r}: {exc}") from exc
    return module


def _load_backend(spec: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
    """Import the backend from its declared checkout and prove the origin."""
    module_name = spec["module"]
    repo_dir = _repo_dir(spec)
    require_pinned = bool(SPEC.get("provenance", {}).get("require_pinned_repo", True))

    if repo_dir is not None and repo_dir.is_dir():
        package_dir = _package_dir(repo_dir, module_name)
        if package_dir is None:
            raise ProvenanceError(
                f"{module_name!r} is not checked out under {repo_dir}")
        module = _pin_to_repo(module_name, package_dir)
        loaded_from = "repo-dir-pinned"
    elif require_pinned:
        raise ProvenanceError(
            f"declared repo for {module_name!r} not found ({repo_dir}); "
            "kernels/probe.json sets provenance.require_pinned_repo")
    else:  # pragma: no cover - only when pinning is explicitly disabled
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            raise ProbeUnavailable(
                f"cannot import backend {module_name!r}: {exc}") from exc
        loaded_from = "environment"

    origin = Path(module.__file__).resolve() if getattr(module, "__file__", None) else None
    inside = bool(repo_dir is not None and origin is not None
                  and repo_dir.resolve() in origin.parents)
    if repo_dir is not None and not inside:
        raise ProvenanceError(
            f"imported {module_name!r} from {origin}, outside declared repo {repo_dir}")

    missing = [name for name in spec.get("api", []) if not hasattr(module, name)]
    if missing:
        raise ApiDriftError(
            f"backend {module_name!r} at {origin} lacks the declared entry points {missing}")

    provenance = {
        "module": module_name,
        "module_file": str(origin) if origin else None,
        "module_sha256": _sha256(origin),
        "repo_root": str(repo_dir) if repo_dir else None,
        "repo_commit": N.git_commit(repo_dir) if repo_dir else None,
        "loaded_from": loaded_from,
        "api": list(spec.get("api", [])),
        "shadow_copies": _shadow_copies(module_name, origin),
        "verified": inside,
        "checked_utc": _utc(),
    }
    return module, provenance


def _backend_version(spec: Dict[str, Any], module,
                     provenance: Dict[str, Any]) -> Optional[str]:
    version = getattr(module, spec.get("version_attr", "__version__"), None)
    commit = provenance.get("repo_commit")
    if version and commit:
        return f"{version}+git.{commit}"
    return version or (f"git.{commit}" if commit else None)


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------
def measure(probe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the probe and return residuals at every declared horizon."""
    probe = dict(SPEC["probe"], **(probe or {}))
    backend_spec = SPEC["backends"][probe["backend"]]
    module, provenance = _load_backend(backend_spec)
    import torch

    torch_dtype = torch.float64 if probe.get("dtype", "float64") == "float64" else torch.float32
    nx, ny = probe["grid"]["nx"], probe["grid"]["ny"]
    tau, u0 = float(probe["tau"]), float(probe["velocity"])
    horizons = sorted(int(h) for h in probe["horizons"])

    x = torch.arange(nx, dtype=torch_dtype).view(1, nx)
    y = torch.arange(ny, dtype=torch_dtype).view(ny, 1)
    rho0 = torch.ones((ny, nx), dtype=torch_dtype)
    ux0 = torch.full((ny, nx), u0, dtype=torch_dtype) + 0.01 * torch.sin(2 * math.pi * y / ny)
    uy0 = 0.01 * torch.sin(2 * math.pi * x / nx) + torch.zeros((ny, nx), dtype=torch_dtype)

    f = module.equilibrium(rho0, ux0, uy0)
    rho, ux, uy = module.macroscopic(f)
    mass_ref = float(rho.sum())
    px_ref = float((rho * ux).sum())
    py_ref = float((rho * uy).sum())

    rows: List[Dict[str, Any]] = []
    prev_ux = ux.clone()
    step = 0
    for horizon in horizons:
        while step < horizon:
            f = module.collide_bgk(f, tau)
            f = module.stream(f)
            step += 1
        rho, ux, uy = module.macroscopic(f)
        mass = float(rho.sum())
        px = float((rho * ux).sum())
        py = float((rho * uy).sum())
        update = float(torch.linalg.vector_norm(ux - prev_ux) /
                       max(float(torch.linalg.vector_norm(prev_ux)), 1e-300))
        rows.append({
            "steps": horizon,
            "mass": abs(mass - mass_ref) / mass_ref,
            "momentum": abs(px - px_ref) / abs(px_ref),
            "momentum_y_abs": abs(py - py_ref),
            "iterative_residual": update,
        })
        prev_ux = ux.clone()

    certified = next(r for r in rows if r["steps"] == probe["certification_horizon"])
    longest = rows[-1]
    rate = certified["mass"] / max(certified["steps"], 1)
    crossing = (1e-6 / rate if rate > 0 else None)
    return {
        "schema": "xsolver/probe/v1",
        "probe_id": probe["id"],
        "backend": {"module": backend_spec["module"],
                    "version": _backend_version(backend_spec, module, provenance),
                    "repo_commit": provenance.get("repo_commit")},
        "provenance": provenance,
        "setup": {"grid": [nx, ny], "tau": tau, "velocity": u0, "dtype": probe.get("dtype"),
                  "horizons": horizons, "certification_horizon": probe["certification_horizon"]},
        "reference": {"mass": mass_ref, "momentum_x": px_ref, "momentum_y": py_ref},
        "horizons": rows,
        "certification": {"horizon": probe["certification_horizon"],
                          "mass": certified["mass"],
                          "momentum": certified["momentum"]},
        "drift_rate_per_step": rate,
        "mass_tolerance_crossing_steps": crossing,
        "longest_horizon": {"steps": longest["steps"], "mass": longest["mass"],
                            "momentum": longest["momentum"]},
        "energy": None,
        "energy_note": SPEC["residual_definitions"]["energy"],
    }


def to_case_record(measurement: Dict[str, Any], artifact_path: Path) -> Dict[str, Any]:
    """Turn a probe measurement into a canonical case so the gate judges it like any other."""
    setup = measurement["setup"]
    certified = measurement["certification"]
    pseudo_raw = {
        "case": f"conservation_probe::{measurement['probe_id']}",
        "geometry": {"grid": {"nx": setup["grid"][0], "ny": setup["grid"][1]}},
        "params": {"tau": setup["tau"], "velocity": setup["velocity"]},
        "output": {"mass_residual": certified["mass"], "momentum_residual": certified["momentum"]},
    }
    record = N.build_case(
        pseudo_raw,
        source_path=str(artifact_path),
        repo=".",
        root=artifact_path.parents[3] if len(artifact_path.parents) > 3 else artifact_path.parent,
        quantity_override={"mass_residual": certified["mass"],
                           "momentum_residual": certified["momentum"]},
        residual_override={"mass": certified["mass"], "momentum": certified["momentum"],
                           "energy": None},
        solver_override={"name": measurement["backend"]["module"],
                         "family": "LB",
                         "version": measurement["backend"]["version"],
                         "method": f"BGK tau={setup['tau']}, periodic box, uniform equilibrium"},
        geometry_override={"kind": "periodic_box", "units": "lattice",
                           "dimensions": None,
                           "grid": {"nx": float(setup["grid"][0]), "ny": float(setup["grid"][1])},
                           "derived": None,
                           "source_path": str(artifact_path),
                           "stage": "probe"},
    )
    N.rename_case(record, f"TensorPlatform::conservation_probe::{measurement['probe_id']}"
                          f"::steps={certified['horizon']}")
    record["output"]["quantity_paths"] = {"mass_residual": "horizons[certification].mass",
                                         "momentum_residual": "horizons[certification].momentum"}
    record["verification_evidence"] = "probe:measured-in-this-run"
    record["problem_id"] = "conservation_probe::" + measurement["probe_id"]
    record["input"]["problem_key"] = record["problem_id"]
    record["condition"] = {"label": "steps", "value": certified["horizon"],
                           "case_name": "conservation_probe"}
    return record


def run(out_path: Path) -> Dict[str, Any]:
    """Measure, persist the raw artifact, and return ``(measurement, case_record)``."""
    measurement = measure()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(measurement, ensure_ascii=False, indent=1, sort_keys=True),
                        encoding="utf-8")
    record = to_case_record(measurement, out_path)
    return {"measurement": measurement, "record": record, "artifact": str(out_path)}