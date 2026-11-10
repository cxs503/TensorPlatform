"""Artifact (any shape) -> canonical input + output record.

Everything shape-specific is vocabulary: ``config/xsolver/normalization_rules.json``
says which keys carry geometry, which carry grid extents, which numeric leaves are
output quantities (and which are just run parameters to be ignored), which keys
record conservation residuals and which solver names belong to which family.
The code only knows how to *walk* documents, so a new artifact convention is a
config edit.

Honesty contract
----------------
A field is either parsed (with the source path that produced it) or it is not.
``parse`` in the returned record carries per-field ``status`` and ``reason`` so
that coverage can be measured instead of assumed; nothing is invented to make a
case look complete.  A quantity that only exists as an absolute drift without a
companion reference mass is deliberately left ``None`` rather than guessed into a
relative residual.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .jsonschema_lite import load_config, load_schema, validate_or_raise

RULES = load_config("normalization_rules")
ALIASES = load_config("quantity_aliases")
TOLERANCES = load_config("conservation_tolerances")

GEOMETRY_KEYS = [k.lower() for k in RULES["geometry_container_keys"]]
HINT_KEYS = [k.lower() for k in RULES.get("geometry_hint_container_keys", [])]
GRID_KEYS = [k.lower() for k in RULES["grid_container_keys"]]
OUTPUT_KEYS = [k.lower() for k in RULES["output_container_keys"]]
EXCLUDE_TOKENS = {t.lower() for t in RULES["exclude_quantity_tokens"]}
INPUT_ROOTS = {k.lower() for k in RULES["input_container_keys"]}
QUANTITY_LIMIT = int(RULES.get("quantity_limits", {}).get("max_per_case", 60))

MAX_DEPTH = 9
MAX_LEAVES = 40000


# ---------------------------------------------------------------------------
# canonical serialisation / digests
# ---------------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_COMMIT_CACHE: Dict[str, Optional[str]] = {}


def git_commit(repo_dir: Path) -> Optional[str]:
    """Short HEAD of a source repository (None when not a git checkout)."""
    key = str(repo_dir)
    if key in _COMMIT_CACHE:
        return _COMMIT_CACHE[key]
    value: Optional[str] = None
    try:
        out = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--short=12", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            value = out.stdout.strip()
    except Exception:  # noqa: BLE001 - provenance must degrade to None, never crash a harvest
        value = None
    _COMMIT_CACHE[key] = value
    return value


# ---------------------------------------------------------------------------
# flattening
# ---------------------------------------------------------------------------
def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def flatten_all(obj: Any, prefix: str = "", depth: int = 0,
                out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Flatten a JSON document to ``dotted.path -> scalar`` (bounded)."""
    if out is None:
        out = {}
    if len(out) > MAX_LEAVES or depth > MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            flatten_all(value, child, depth + 1, out)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            child = f"{prefix}.{i}" if prefix else str(i)
            flatten_all(value, child, depth + 1, out)
    else:
        out[prefix] = obj
    return out


def _last_segment(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _normalise_key(key: str) -> str:
    return key.strip().lower()


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def _affix_match(low: str, name: str) -> bool:
    """Exact, or an ``_``-bounded affix match for aliases long enough to be unambiguous."""
    name = _normalise_key(name)
    if low == name:
        return True
    if len(name) < 4:
        return False
    return low.endswith("_" + name) or low.startswith(name + "_")


def _role_of(leaf: str, table: Dict[str, List[str]]) -> Optional[str]:
    low = _normalise_key(leaf)
    for role, names in table.items():
        if any(low == _normalise_key(n) for n in names):
            return role
    for role, names in table.items():
        if any(_affix_match(low, n) for n in names):
            return role
    return None


def _dimension_role(leaf: str) -> Optional[str]:
    return _role_of(leaf, RULES["geometry_dimension_keys"])


def _grid_extent_role(leaf: str) -> Optional[str]:
    return _role_of(leaf, RULES["grid_extent_keys"])


def _units_from(paths: Sequence[str]) -> str:
    joined = " ".join(paths).lower()
    if "_m" in joined or "phys" in joined or "kg" in joined:
        return "m"
    return "lattice"


def _geometry_closure(dimensions: Dict[str, float], derived: Dict[str, float]) -> Dict[str, Any]:
    """Check the declared geometry against the block-coefficient identity.

    ``Cb = volume / (L * B * T)`` -- if a record declares both a volume and a
    block coefficient they must close; a silent geometry error makes every
    downstream coefficient meaningless, so this is gated before any comparison.
    """
    checks: List[Dict[str, Any]] = []
    length = dimensions.get("length")
    beam = dimensions.get("beam")
    draft = dimensions.get("draft")
    volume = dimensions.get("volume") or derived.get("volume")
    cb_declared = derived.get("Cb")
    if volume is not None and None not in (length, beam, draft) and length and beam and draft:
        cb_recomputed = volume / (length * beam * draft)
        derived["Cb_recomputed"] = cb_recomputed
        if cb_declared is not None and cb_declared > 0:
            rel = abs(cb_recomputed - cb_declared) / cb_declared
            checks.append({"id": "block_coefficient_closure", "value": rel,
                           "threshold": 0.02, "status": "PASS" if rel <= 0.02 else "FAIL",
                           "detail": f"recomputed Cb={cb_recomputed:.6f} vs declared {cb_declared:.6f}"})
        else:
            checks.append({"id": "block_coefficient_recomputed", "value": cb_recomputed,
                           "threshold": None, "status": "PASS",
                           "detail": "declared Cb absent; recomputed from volume/(L*B*T)"})
    else:
        checks.append({"id": "block_coefficient_closure", "status": "WITHHELD",
                       "detail": "volume and L/B/T not all present"})
    statuses = [c["status"] for c in checks]
    status = "FAIL" if "FAIL" in statuses else ("PASS" if "PASS" in statuses else "WITHHELD")
    return {"status": status, "checks": checks}


def extract_geometry(raw: Dict[str, Any], source_path: str = "") -> Dict[str, Any]:
    """Extract the canonical geometry block, or ``None`` when nothing parses."""
    dimensions: Dict[str, float] = {}
    grid: Dict[str, float] = {}
    derived: Dict[str, float] = {}
    dim_paths: List[str] = []
    stage = None

    # -- stage 1: explicit geometry containers
    for key, node in raw.items():
        if _normalise_key(key) not in GEOMETRY_KEYS or not isinstance(node, dict):
            continue
        for path, value in flatten_all(node, prefix=key).items():
            if not is_number(value):
                continue
            role = _dimension_role(_last_segment(path))
            if role and role not in dimensions:
                dimensions[role] = float(value)
                dim_paths.append(path)
            elif _last_segment(path).lower() in ("cb", "block_coefficient", "cm", "cp",
                                                 "cb_declared", "volume_m3"):
                derived[_last_segment(path).lower()] = float(value)
        if dimensions:
            stage = "container"
            units = _units_from(dim_paths)
            break

    # -- stage 1b: declared hint containers (``params``/``settings``/``mesh``/...); here
    #    single-letter dimension names are admissible because the container says
    #    "this is the case set-up"
    if not dimensions:
        for key, node in raw.items():
            if _normalise_key(key) not in HINT_KEYS or not isinstance(node, dict):
                continue
            for path, value in flatten_all(node, prefix=key).items():
                if not is_number(value):
                    continue
                role = _dimension_role(_last_segment(path))
                if role and role not in dimensions:
                    dimensions[role] = float(value)
                    dim_paths.append(path)
            if dimensions:
                stage = "hint_container"
                break

    # -- stage 2: document-wide dimension search (only when stage 1 found nothing)
    if not dimensions:
        for path, value in flatten_all(raw).items():
            if not is_number(value):
                continue
            leaf = _last_segment(path)
            if len(leaf) <= 1:  # single letters are hopelessly ambiguous outside a declared block
                continue
            role = _dimension_role(leaf)
            if role and role not in dimensions:
                dimensions[role] = float(value)
                dim_paths.append(path)
            elif not dimensions:
                grid_role = _grid_extent_role(leaf)
                if grid_role and grid_role not in grid:
                    grid[grid_role] = float(value)
        if dimensions:
            stage = "document"

    # -- grid / lattice extents (independent of the physical dimensions)
    for key, node in raw.items():
        if _normalise_key(key) not in GRID_KEYS:
            continue
        flat = flatten_all(node, prefix=key)
        # ``grids`` may be a mapping of grid label -> results; the extent then lives
        # one level deeper (``grids.128.nx``), which flattening already exposes.
        for path, value in flat.items():
            if not is_number(value):
                continue
            role = _grid_extent_role(_last_segment(path))
            if role and role not in grid:
                grid[role] = float(value)
        if isinstance(node, (int, float)) and not isinstance(node, bool) and "resolution" not in grid:
            grid["resolution"] = float(node)
        if isinstance(node, dict):
            labels = []
            for label in node:
                token = "".join(ch if ch.isdigit() else " " for ch in str(label)).split()
                for part in token:
                    try:
                        labels.append(float(part))
                    except ValueError:
                        continue
            if labels and "resolution" not in grid:
                grid["resolution"] = max(labels)

    # -- declared form coefficients that live outside the geometry container
    for path, value in flatten_all(raw).items():
        last = _last_segment(path).lower()
        if not is_number(value):
            continue
        if last in ("cb", "block_coefficient") and "Cb" not in derived:
            derived["Cb"] = float(value)
        elif last in ("volume", "volume_m3", "displacement_volume_m3") and "volume" not in derived:
            derived["volume"] = float(value)
        elif last in ("wetted_area", "wetted_area_m2", "wetted_surface_area_m2",
                      "s_wet_phys_m2") and "S_wet" not in derived:
            derived["S_wet"] = float(value)

    if not dimensions and not grid and not derived:
        return {"geometry": None, "status": "unparsed", "reason": "no geometry/grid container matched"}

    if stage is None:
        stage = "grid_only" if grid and not dimensions else "derived_only"
    if "units" in raw and isinstance(raw["units"], str):
        units = raw["units"]
    elif dimensions:
        units = _units_from(dim_paths)
    elif grid:
        units = "lattice"
    else:
        units = "mixed"

    # dimension-normalise the volume role: ``volume`` is a length^3 quantity
    geometry = {
        "kind": str(raw.get("kind") or raw.get("case") or raw.get("study") or "") or None,
        "units": units if units in ("m", "lattice", "mixed") else "mixed",
        "dimensions": dimensions or None,
        "grid": grid or None,
        "derived": derived or None,
        "source_path": source_path,
        "stage": stage,
    }
    closure = _geometry_closure(dimensions, derived)
    geometry["closure"] = closure
    status = "parsed"
    reason = None
    return {"geometry": geometry, "status": status, "reason": reason}


# ---------------------------------------------------------------------------
# output quantities
# ---------------------------------------------------------------------------
def _is_excluded(leaf: str) -> bool:
    low = _normalise_key(leaf)
    return low in EXCLUDE_TOKENS


def _grid_level(path: str) -> float:
    """Largest integer found in a path -- grid labels such as ``128`` or ``96x24``."""
    best = -1.0
    for seg in path.split("."):
        token = "".join(ch if ch.isdigit() else " " for ch in seg).split()
        for part in token:
            try:
                best = max(best, float(part))
            except ValueError:
                continue
    return best


def _alias_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for canonical, spec in ALIASES["quantities"].items():
        for alias in spec["aliases"]:
            index[_normalise_key(alias)] = canonical
    return index


ALIAS_INDEX = _alias_index()


def extract_quantities(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the output numbers out of an artifact.

    Selection rule (deterministic): a numeric leaf is a candidate when its first
    path segment is an output container and its last segment is not an excluded
    run parameter; among candidates sharing the same name the finest grid level
    wins (``grids.128.u`` beats ``grids.64.u``) and, on a tie, the shallowest path.
    """
    flat = flatten_all(raw)
    candidates: Dict[str, List[Tuple[str, float]]] = {}
    for path, value in flat.items():
        if not is_number(value):
            continue
        segs = path.split(".")
        if len(segs) < 2:
            continue
        if any(seg.lower() in INPUT_ROOTS for seg in segs[:-1]):
            continue
        leaf = _last_segment(path)
        if _is_excluded(leaf):
            continue
        candidates.setdefault(_normalise_key(leaf), []).append((path, float(value)))

    primary: Dict[str, float] = {}
    paths: Dict[str, str] = {}
    alternatives: Dict[str, int] = {}
    ordered = sorted(candidates.items(), key=lambda kv: (len(kv[1][0][0].split(".")), kv[0]))
    for name, hits in ordered:
        if len(primary) >= QUANTITY_LIMIT:
            break
        ranked = sorted(hits, key=lambda h: (-_grid_level(h[0]), h[0].count("."), h[0]))
        path, value = ranked[0]
        key = name
        if key in primary:  # collision across containers -> qualify with the container
            container = path.split(".")[0].lower()
            key = f"{container}.{name}"
            if key in primary:
                continue
        primary[key] = value
        paths[key] = path
        alternatives[key] = len(hits)

    canonical: Dict[str, float] = {}
    canonical_paths: Dict[str, str] = {}
    for name, value in primary.items():
        target = ALIAS_INDEX.get(name)
        if target and target not in canonical:
            canonical[target] = value
            canonical_paths[target] = paths[name]

    status = "parsed" if primary else "unparsed"
    return {
        "primary_quantities": primary,
        "quantity_paths": paths,
        "quantity_candidates": alternatives,
        "canonical_quantities": canonical,
        "canonical_paths": canonical_paths,
        "status": status,
        "reason": None if primary else "no numeric leaf under an output container",
    }


# ---------------------------------------------------------------------------
# conservation residuals
# ---------------------------------------------------------------------------
def _find_leaf(flat: Dict[str, Any], keys: Iterable[str]) -> Optional[Tuple[str, Any]]:
    wanted = {_normalise_key(k) for k in keys}
    hits: List[Tuple[str, Any]] = []
    for path, value in flat.items():
        if _normalise_key(_last_segment(path)) in wanted and is_number(value):
            hits.append((path, value))
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0].count("."), h[0]))
    return hits[0]


def extract_residuals(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Mass / momentum / energy residuals, with the basis of each one recorded."""
    flat = flatten_all(raw)
    residuals: Dict[str, Optional[float]] = {"mass": None, "momentum": None, "energy": None}
    paths: Dict[str, Optional[str]] = {"mass": None, "momentum": None, "energy": None}
    basis: Dict[str, str] = {}
    absolute_only: Dict[str, Dict[str, float]] = {}

    for kind in ("mass", "momentum", "energy"):
        spec = RULES["residual_keys"][kind]
        percent = {_normalise_key(k) for k in spec.get("percent_scale", [])}
        hit = None
        for key in spec["relative"]:
            hit = _find_leaf(flat, [key])
            if hit:
                path, value = hit
                scale = 0.01 if _normalise_key(_last_segment(path)) in percent else 1.0
                residuals[kind] = abs(float(value)) * scale
                paths[kind] = path
                basis[kind] = "recorded_relative" if scale == 1.0 else "recorded_percent"
                break
        if hit:
            continue
        absolute = _find_leaf(flat, spec.get("absolute", []))
        if absolute:
            absolute_only[kind] = {"path": absolute[0], "value": abs(float(absolute[1]))}
            basis[kind] = "absolute_without_reference"
        fallback = spec.get("fallback")
        if fallback and kind == "momentum" and residuals[kind] is None:
            proxy = _find_leaf(flat, fallback["from"])
            if proxy:
                residuals["momentum"] = abs(float(proxy[1]))
                paths["momentum"] = proxy[0]
                basis["momentum"] = "iterative_residual_proxy"

    return {
        "conservation_residuals": residuals,
        "residual_paths": paths,
        "residual_basis": basis,
        "absolute_only": absolute_only,
    }


# ---------------------------------------------------------------------------
# convergence evidence
# ---------------------------------------------------------------------------
def extract_convergence(raw: Dict[str, Any]) -> Dict[str, Any]:
    flat = flatten_all(raw)
    iterative = _find_leaf(flat, ["last_resid", "resid", "iterative_residual",
                                  "last_residual", "resid_u"])
    order = _find_leaf(flat, ["order_p", "observed_order", "p_obs"])

    levels: List[float] = []
    for key, node in raw.items():
        if _normalise_key(key) not in GRID_KEYS:
            continue
        if isinstance(node, dict):
            for label in node:
                token = "".join(ch if ch.isdigit() else " " for ch in str(label)).split()
                for part in token:
                    try:
                        levels.append(float(part))
                    except ValueError:
                        continue
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            levels.append(float(node))
    levels = sorted({lv for lv in levels if lv > 0})

    extrapolated: Dict[str, float] = {}
    for path, value in flat.items():
        if not is_number(value):
            continue
        if "extrap" in path.lower() and not _last_segment(path).lower().endswith("pct"):
            extrapolated[_last_segment(path)] = float(value)

    return {
        "iterative_residual": float(iterative[1]) if iterative else None,
        "iterative_residual_path": iterative[0] if iterative else None,
        "observed_order": float(order[1]) if order else None,
        "grid_levels": levels or None,
        "extrapolated": extrapolated or None,
    }


# ---------------------------------------------------------------------------
# solver identity
# ---------------------------------------------------------------------------
def solver_family(name: Optional[str]) -> str:
    low = _normalise_key(name or "")
    for family, tokens in RULES["solver_families"].items():
        if any(tok and tok in low for tok in [t.lower() for t in tokens]):
            return family
    return "OTHER"


def solver_identity(raw: Dict[str, Any], repo: str, commit: Optional[str]) -> Dict[str, Any]:
    sv_keys = RULES["known_artifact_variables"]["solver_version_keys"]
    method_keys = RULES["known_artifact_variables"]["method_keys"]
    name = None
    for key in ("solver", "library", "solver_name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break
    if name is None:
        name = repo
    version = None
    flat = flatten_all(raw)
    for key in sv_keys:
        hit = _find_leaf(flat, [key])
        if hit and isinstance(hit[1], str) and hit[1].strip():
            version = hit[1].strip()
            break
    if version is None:
        version = f"{repo}@{commit}" if commit else None
    method = None
    for key in method_keys:
        hit = _find_leaf(flat, [key])
        if hit and isinstance(hit[1], str) and hit[1].strip():
            method = hit[1].strip()
            break
    return {"name": name, "family": solver_family(name), "version": version, "method": method}


def case_name(raw: Dict[str, Any], path: Path) -> str:
    for key in RULES["known_artifact_variables"]["case_name_keys"]:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return path.parent.name or path.stem


# ---------------------------------------------------------------------------
# physics
# ---------------------------------------------------------------------------
def extract_physics(raw: Dict[str, Any]) -> Dict[str, Any]:
    flat = flatten_all(raw)
    dimensionless: Dict[str, float] = {}
    for role, names in RULES["physics_dimensionless_keys"].items():
        hit = _find_leaf(flat, names)
        if hit:
            dimensionless[role] = float(hit[1])
    parameters: Dict[str, float] = {}
    for key in RULES["physics_parameter_keys"]:
        hit = _find_leaf(flat, [key])
        if hit:
            parameters[_normalise_key(key)] = float(hit[1])
    return {"dimensionless": dimensionless or None, "parameters": parameters or None}


# ---------------------------------------------------------------------------
# record assembly
# ---------------------------------------------------------------------------
def build_case(raw: Dict[str, Any], *, source_path: str, repo: str, root: Path,
               problem_id: Optional[str] = None, condition: Optional[Dict[str, Any]] = None,
               solver_override: Optional[Dict[str, Any]] = None,
               quantity_override: Optional[Dict[str, float]] = None,
               residual_override: Optional[Dict[str, Optional[float]]] = None,
               geometry_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build one canonical case record (input + output + parse report + provenance)."""
    path = Path(source_path)
    commit = git_commit(Path(root) / repo) if (Path(root) / repo).exists() else None
    sha = file_sha256(path) if path.exists() else "0" * 64

    geom = {"geometry": geometry_override, "status": "parsed" if geometry_override else "unparsed",
            "reason": None if geometry_override else "override absent"}
    if geometry_override is None:
        geom = extract_geometry(raw, source_path)
    geometry = geom["geometry"]

    quantities = extract_quantities(raw)
    if quantity_override:
        quantities["canonical_quantities"] = dict(quantity_override)
        quantities["primary_quantities"] = {**quantities["primary_quantities"], **quantity_override}
        quantities["status"] = "parsed"

    residuals = extract_residuals(raw)
    if residual_override:
        residuals["conservation_residuals"] = {**residuals["conservation_residuals"],
                                               **residual_override}

    convergence = extract_convergence(raw)
    solver = solver_override or solver_identity(raw, repo, commit)
    name = case_name(raw, path) if not quantity_override else str(
        (condition or {}).get("case_name") or case_name(raw, path))

    case_id = f"{repo}::{name}" + (f"::{condition['label']}={condition['value']}"
                                   if condition else "")
    input_block = {
        "schema": "xsolver/input/v1",
        "case_id": case_id,
        "problem_key": problem_id or f"{repo}::{name}",
        "geometry": geometry,
        "physics": extract_physics(raw),
        "solver": solver,
        "condition": condition,
        "provenance": {
            "source_path": source_path,
            "source_sha256": sha,
            "input_digest": "",  # filled below (digest of the input block itself)
            "solver_version": solver.get("version"),
            "git_commit": commit,
        },
    }
    input_digest = digest_of({k: v for k, v in input_block.items() if k != "provenance"})
    input_block["provenance"]["input_digest"] = input_digest

    output_block = {
        "schema": "xsolver/output/v1",
        "case_id": case_id,
        "primary_quantities": quantities["primary_quantities"],
        "canonical_quantities": quantities["canonical_quantities"],
        "quantity_paths": quantities["quantity_paths"],
        "derived_quantities": (geometry or {}).get("derived") or {},
        "conservation_residuals": residuals["conservation_residuals"],
        "residual_paths": residuals["residual_paths"],
        "convergence": {
            "iterative_residual": convergence["iterative_residual"],
            "grid_levels": convergence["grid_levels"],
            "observed_order": convergence["observed_order"],
            "extrapolated": convergence["extrapolated"],
        },
    }

    record = {
        "schema": "xsolver/case/v1",
        "case_id": case_id,
        "problem_id": input_block["problem_key"],
        "condition": condition,
        "repo": repo,
        "input": input_block,
        "output": output_block,
        "parse": {
            "geometry": {"status": geom["status"], "reason": geom.get("reason"),
                         "stage": (geometry or {}).get("stage")},
            "primary_quantities": {"status": quantities["status"], "reason": quantities["reason"],
                                   "canonical": len(quantities["canonical_quantities"])},
            "conservation": {"basis": residuals["residual_basis"],
                             "absolute_only": residuals["absolute_only"]},
        },
        "provenance": {
            "source_path": source_path,
            "source_sha256": sha,
            "solver_version": solver.get("version"),
            "git_commit": commit,
            "harvested_utc": "",
        },
    }
    return record


def rename_case(record: Dict[str, Any], new_id: str) -> Dict[str, Any]:
    """Rename a record and re-derive its input digest (traceability follows the id)."""
    record["case_id"] = new_id
    record["input"]["case_id"] = new_id
    record["output"]["case_id"] = new_id
    record["input"]["provenance"]["input_digest"] = digest_of(
        {k: v for k, v in record["input"].items() if k != "provenance"})
    return record


def validate_records(records: Sequence[Dict[str, Any]], only_parsed: bool = True) -> None:
    """Schema-check the canonical records; the negative control C4 relies on this raising.

    ``only_parsed`` skips the output contract for cases whose artifact yielded no
    output quantity: such a case is *reported* (and counted against coverage), not
    silently promoted to a comparable record.  The output schema deliberately
    requires at least one primary quantity -- removing it from a parsed case is
    therefore a hard error, which is exactly what the negative control asserts.
    """
    in_schema = load_schema("input_schema")
    out_schema = load_schema("output_schema")
    for rec in records:
        validate_or_raise(rec["input"], in_schema, f"input[{rec['case_id']}]")
        if only_parsed and rec.get("parse", {}).get("primary_quantities", {}).get("status") != "parsed":
            continue
        validate_or_raise(rec["output"], out_schema, f"output[{rec['case_id']}]")