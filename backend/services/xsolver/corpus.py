"""Corpus harvest: discover -> filter by verification verdict -> snapshot.

The corpus is the *only* input the adjudicator sees.  It is built by walking the
source roots with config-declared globs, so nothing about a particular solver or
case is baked into code; and it is snapshotted next to its own provenance
(per-artifact sha256, source git commit, harvest time), so an adjudication run
months later can be reproduced without the source repositories being present and
still say exactly which bytes it judged.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import normalize as N
from .jsonschema_lite import load_config

SOURCES = load_config("corpus_sources")
CORPUS_SCHEMA = "xsolver/corpus/v1"


# ---------------------------------------------------------------------------
# selectors: read one number out of an artifact by a config-declared path
# ---------------------------------------------------------------------------
def resolve_selector(doc: Any, path: str, select: Any = None) -> Tuple[Any, str]:
    """Resolve ``a.b`` / ``a[].b`` (+ ``select``: first|last|int|finest) to a value.

    Returns ``(value, resolved_path)``; raises ``KeyError`` when the path does not
    exist, so a mis-declared config fails loudly instead of silently producing a
    ``None`` arm.
    """
    segments = [seg for seg in path.split(".") if seg]
    current = doc
    resolved: List[str] = []
    for seg in segments:
        wildcard = seg.endswith("[]")
        name = seg[:-2] if wildcard else seg
        if name:
            if not isinstance(current, dict) or name not in current:
                raise KeyError(f"{path}: missing segment {name!r} at {'.'.join(resolved) or '$'}")
            current = current[name]
            resolved.append(name)
        if wildcard:
            if not isinstance(current, list):
                raise KeyError(f"{path}: {'.'.join(resolved)} is not a list")
            idx = _select_index(current, select, path)
            current = current[idx]
            resolved.append(str(idx))
    return current, ".".join(resolved)


def _select_index(items: Sequence[Any], select: Any, path: str) -> int:
    if not items:
        raise KeyError(f"{path}: empty list")
    if select in (None, "first"):
        return 0
    if select == "last":
        return len(items) - 1
    if select == "finest":
        best, best_key = 0, None
        for i, item in enumerate(items):
            key = _grid_key(item)
            if best_key is None or key > best_key:
                best, best_key = i, key
        return best
    if isinstance(select, int):
        return select
    raise KeyError(f"{path}: unsupported select {select!r}")


def _grid_key(item: Any) -> Tuple[float, float]:
    if isinstance(item, dict):
        for key in ("L", "nx", "nz", "resolution", "panel_count", "n_sections", "n_levels"):
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (float(value), 0.0)
    return (0.0, 0.0)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def root_path(root_spec: Dict[str, Any]) -> Optional[Path]:
    env = root_spec.get("path_env")
    candidate = os.environ.get(env) if env else None
    if not candidate:
        candidate = root_spec.get("default")
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    return path if path.exists() else None


def discover(root_spec: Dict[str, Any], patterns: Sequence[str],
             max_files: int, max_bytes: int) -> List[Tuple[Path, str]]:
    """Return ``(path, repo)`` pairs found under one root."""
    root = root_path(root_spec)
    if root is None:
        return []
    found: List[Tuple[Path, str]] = []
    seen = set()
    for repo in root_spec.get("repos", ["."]):
        base = (root / repo).resolve()
        if not base.exists() or not base.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(base.glob(pattern)):
                if not path.is_file() or path in seen:
                    continue
                try:
                    if path.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                seen.add(path)
                found.append((path, repo))
                if len(found) >= max_files:
                    return found
    return found


def _verified(raw: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Did the artifact itself declare a verification verdict?"""
    pred = N.RULES["verified_predicate"]
    tokens = {t.lower() for t in pred["pass_tokens"]}
    for field in pred["boolean_fields"]:
        if raw.get(field) is True:
            return {"promoted": True, "evidence": f"{field}=true"}
    for field in pred["verdict_fields"]:
        value = raw.get(field)
        if isinstance(value, str) and value.strip().lower() in tokens:
            return {"promoted": True, "evidence": f"{field}={value}"}
        if isinstance(value, dict):
            inner = value.get("status")
            if isinstance(inner, str) and inner.strip().lower() in tokens:
                return {"promoted": True, "evidence": f"{field}.status={inner}"}
    lowered = "/" + str(path.as_posix()).lower() + "/"
    for token in pred["path_tokens"]:
        if token in lowered:
            return {"promoted": True, "evidence": f"path~{token}"}
    return {"promoted": False, "evidence": None}


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------
def harvest(sources: Optional[Dict[str, Any]] = None,
            limit_cases: Optional[int] = None) -> Dict[str, Any]:
    sources = sources or SOURCES
    limits = sources["harvest_limits"]
    patterns = sources["artifact_globs"]
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

    stats = {"files_seen": 0, "files_unreadable": 0, "files_not_verified": 0,
             "cases_promoted": 0, "matrix_cases": 0, "linked_cases": 0, "roots": {}}
    cases: List[Dict[str, Any]] = []
    # source glob (absolute) -> problem id, for problem_links assignment
    link_map: Dict[str, Dict[str, Any]] = {}

    for link in sources.get("problem_links", []):
        for member in link["members"]:
            root_spec = next((r for r in sources["roots"] if r["id"] == member.get("root")),
                             sources["roots"][0])
            root = root_path(root_spec)
            if root is None:
                continue
            for path in sorted((root).glob(member["source_glob"])):
                if path.is_file():
                    link_map[str(path.resolve())] = {**link, "member": member}

    for root_spec in sources["roots"]:
        root = root_path(root_spec)
        if root is None:
            stats["roots"][root_spec["id"]] = {"present": False}
            continue
        discovered = discover(root_spec, patterns, limits["max_files"], limits["max_file_bytes"])
        promoted = 0
        for path, repo in discovered:
            stats["files_seen"] += 1
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - unreadable artifacts are counted, not fatal
                stats["files_unreadable"] += 1
                continue
            if not isinstance(raw, dict):
                stats["files_unreadable"] += 1
                continue
            verdict = _verified(raw, path)
            if not verdict["promoted"]:
                stats["files_not_verified"] += 1
                continue
            link = link_map.get(str(path.resolve()))
            record = N.build_case(raw, source_path=str(path), repo=repo, root=root,
                                  problem_id=link["problem_id"] if link else None,
                                  geometry_override=_shared_geometry(link) if link else None)
            record["provenance"]["harvested_utc"] = now
            record["verification_evidence"] = verdict["evidence"]
            if link:
                _attach_link(record, raw, link)
                stats["linked_cases"] += 1
            cases.append(record)
            promoted += 1
            if limit_cases and len(cases) >= limit_cases:
                break
        stats["roots"][root_spec["id"]] = {"present": True, "path": str(root),
                                           "files": len(discovered), "promoted": promoted}
        if limit_cases and len(cases) >= limit_cases:
            break

    # -- declared problem members that the globs did not reach are still promoted:
    #    a cross-solver problem statement is itself the reason to include the artifact
    promoted_paths = {str(Path(c["provenance"]["source_path"]).resolve()) for c in cases}
    for source, link in link_map.items():
        if source in promoted_paths:
            continue
        path = Path(source)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(raw, dict):
            continue
        root = path.parents[3] if len(path.parents) > 3 else path.parent
        record = N.build_case(raw, source_path=str(path), repo=path.parents[2].name,
                              root=root, problem_id=link["problem_id"],
                              geometry_override=_shared_geometry(link))
        record["provenance"]["harvested_utc"] = now
        record["verification_evidence"] = "problem_link:member"
        _attach_link(record, raw, link)
        cases.append(record)
        stats["linked_cases"] += 1

    # -- matrix expansions: one artifact holding condition x solver x quantities
    for expansion in sources.get("matrix_expansions", []):
        for root_spec in sources["roots"]:
            root = root_path(root_spec)
            if root is None:
                continue
            for path in sorted(root.glob(expansion["source_glob"])):
                if not path.is_file():
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
                expanded = _expand_matrix(raw, path, expansion, root_spec.get("id"), now)
                cases.extend(expanded)
                stats["matrix_cases"] += len(expanded)

    cases = _ensure_unique_ids(cases)
    stats["cases_promoted"] = len(cases)
    corpus = {"schema": CORPUS_SCHEMA, "harvested_utc": now, "stats": stats, "cases": cases}
    corpus["stats"]["coverage"] = coverage(corpus)
    return corpus


_SHARED_GEOMETRY_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}


def _shared_geometry(link: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Geometry declared once per problem and reused by arms that do not restate it."""
    if not link:
        return None
    spec = link.get("shared_geometry_from")
    if not spec:
        return None
    key = N.canonical_json(spec)
    if key in _SHARED_GEOMETRY_CACHE:
        return _SHARED_GEOMETRY_CACHE[key]
    root_spec = next((r for r in SOURCES["roots"] if r["id"] == spec.get("root")), SOURCES["roots"][0])
    root = root_path(root_spec)
    block = None
    if root is not None:
        for candidate in sorted(root.glob(spec["source_glob"])):
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            try:
                value, resolved = resolve_selector(raw, spec["path"], spec.get("select"))
            except KeyError:
                continue
            if isinstance(value, dict):
                block = N.extract_geometry({"geometry": value}, str(candidate))["geometry"]
                if block:
                    block["shared_from"] = str(candidate)
            break
    _SHARED_GEOMETRY_CACHE[key] = block
    return block


def _ensure_unique_ids(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Case ids must be unique: the residual gate is keyed by them.

    One artifact can describe several grids (``..._convergence``) and two
    repositories can use the same study name, so a duplicate id would silently
    merge two different runs into one gate entry.  Collisions get the source
    digest appended, and the input digest is recomputed so traceability stays
    correct.
    """
    seen: Dict[str, int] = {}
    for record in cases:
        base = record["case_id"]
        if base not in seen:
            seen[base] = 1
            continue
        seen[base] += 1
        suffix = record["provenance"]["source_sha256"][:8]
        new_id = f"{base}::{suffix}#{seen[base]}"
        record["case_id"] = new_id
        record["input"]["case_id"] = new_id
        record["output"]["case_id"] = new_id
        record["input"]["provenance"]["input_digest"] = N.digest_of(
            {k: v for k, v in record["input"].items() if k != "provenance"})
    return cases


def _attach_link(record: Dict[str, Any], raw: Dict[str, Any], link: Dict[str, Any]) -> None:
    """Assign a shared problem id and the config-declared quantity paths."""
    record["problem_id"] = link["problem_id"]
    record["input"]["problem_key"] = link["problem_id"]
    member = link.get("member", {})
    overrides: Dict[str, float] = {}
    paths: Dict[str, str] = {}
    for quantity, selector in (member.get("quantities") or {}).items():
        if isinstance(selector, str):
            value, resolved = resolve_selector(raw, selector)
        else:
            value, resolved = resolve_selector(raw, selector["path"], selector.get("select"))
        if N.is_number(value):
            overrides[quantity] = float(value)
            paths[quantity] = resolved
    if overrides:
        record["output"]["canonical_quantities"] = overrides
        record["output"]["quantity_paths"] = {**record["output"].get("quantity_paths", {}), **paths}
        record["parse"]["primary_quantities"]["link_quantities"] = len(overrides)
    if member.get("is_reference"):
        record["is_reference_arm"] = True
    record["linked"] = True


def _expand_matrix(raw: Dict[str, Any], path: Path, expansion: Dict[str, Any],
                   root_id: str, now: str) -> List[Dict[str, Any]]:
    """``{condition: {solver: quantities}}`` -> one canonical case per cell."""
    matrix = raw
    for seg in expansion["matrix_path"].split("."):
        matrix = matrix[seg]
    geometry = None
    if expansion.get("geometry_from"):
        raw_geometry = raw.get(expansion["geometry_from"])
        if isinstance(raw_geometry, dict):
            # the artifact states L/B/T in its own key names; run it through the
            # normaliser so the case carries a canonical geometry block, not raw keys
            geometry = N.extract_geometry({"geometry": raw_geometry}, str(path))["geometry"]
    root = path.parents[3] if len(path.parents) > 3 else path.parent
    cases: List[Dict[str, Any]] = []
    condition_label = expansion.get("condition_label", "condition")
    for label, arms in matrix.items():
        if not isinstance(arms, dict):
            continue
        for solver_name, arm in arms.items():
            if not isinstance(arm, dict):
                continue
            quantities: Dict[str, float] = {}
            paths: Dict[str, str] = {}
            for canonical, key in expansion["quantity_map"].items():
                if N.is_number(arm.get(key)):
                    quantities[canonical] = float(arm[key])
                    paths[canonical] = key
            try:
                value = float(label)
            except (TypeError, ValueError):
                value = label
            try:
                repo = path.resolve().relative_to(Path(root_path({"default": str(path.parents[3])}) or path).resolve()).parts[0]
            except Exception:  # noqa: BLE001 - fall back to the owning directory name
                repo = path.parents[2].name if len(path.parents) > 2 else path.parent.name
            record = N.build_case(
                arm,
                source_path=str(path),
                repo=repo,
                root=Path(root_path({"default": str(path.parents[3])}) or path.parents[3]),
                problem_id=expansion["problem_id"],
                condition={"label": condition_label, "value": value,
                           "case_name": f"{expansion['problem_id']}::"
                                        f"{arm.get(expansion.get('solver_name_key', 'solver')) or solver_name}"},
                solver_override={"name": str(arm.get(expansion.get("solver_name_key", "solver"))
                                             or solver_name),
                                 "family": N.solver_family(solver_name),
                                 "version": arm.get("version") or f"{solver_name}@{N.git_commit(path.parents[3])}",
                                 "method": arm.get(expansion.get("solver_method_key", "method"))},
                quantity_override=quantities,
                geometry_override=geometry,
            )
            record["output"]["quantity_paths"] = {**record["output"]["quantity_paths"], **paths}
            record["matrix_condition"] = value
            record["provenance"]["harvested_utc"] = now
            record["verification_evidence"] = f"matrix:{expansion['id']}"
            cases.append(record)
    return cases


# ---------------------------------------------------------------------------
# coverage (C1)
# ---------------------------------------------------------------------------
def coverage(corpus: Dict[str, Any]) -> Dict[str, Any]:
    cases = corpus.get("cases", [])
    total = len(cases)
    geom = sum(1 for c in cases if (c["parse"]["geometry"]["status"] == "parsed"
                                    and c["input"].get("geometry")))
    quantities = sum(1 for c in cases if c["output"]["primary_quantities"])
    canonical = sum(1 for c in cases if c["output"].get("canonical_quantities"))
    traceable = sum(1 for c in cases
                    if c["input"]["provenance"].get("input_digest")
                    and c["input"]["provenance"].get("source_sha256")
                    and c["provenance"].get("solver_version")
                    and c["provenance"].get("git_commit"))
    residual = {k: sum(1 for c in cases if c["output"]["conservation_residuals"].get(k) is not None)
                for k in ("mass", "momentum", "energy")}
    return {
        "cases": total,
        "geometry_parsed": geom,
        "geometry_rate": round(geom / total, 4) if total else 0.0,
        "primary_quantities_parsed": quantities,
        "primary_quantities_rate": round(quantities / total, 4) if total else 0.0,
        "canonical_quantities_parsed": canonical,
        "canonical_quantities_rate": round(canonical / total, 4) if total else 0.0,
        "traceable": traceable,
        "traceable_rate": round(traceable / total, 4) if total else 0.0,
        "residual_recorded": residual,
    }


# ---------------------------------------------------------------------------
# reproducibility fingerprint
# ---------------------------------------------------------------------------
#: config files that participate in normalisation; their bytes decide how an
#: artifact turns into a case, so a case fingerprint means nothing without them
FRAME_CONFIGS = ("normalization_rules", "quantity_aliases", "conservation_tolerances",
                 "corpus_sources")

#: keys whose value is wall clock / host layout rather than harvested content
VOLATILE_KEYS = ("harvested_utc",)


def _config_digests() -> Dict[str, Optional[str]]:
    from .jsonschema_lite import CONFIG_DIR
    out: Dict[str, Optional[str]] = {}
    for name in FRAME_CONFIGS:
        path = CONFIG_DIR / f"{name}.json"
        out[name] = N.file_sha256(path) if path.exists() else None
    return out


def case_fingerprint(case: Dict[str, Any]) -> str:
    """Per-case fingerprint of the *content*: source bytes + parsing outcome + attribution.

    ``source_path`` and ``harvested_utc`` are deliberately absent -- they change when the
    same bytes are read on another host or one second later, and a fingerprint that moves
    with the clock cannot answer "was this the same harvest?".
    """
    prov = case.get("provenance") or {}
    return N.digest_of({
        "case_id": case.get("case_id"),
        "problem_id": case.get("problem_id"),
        "source_sha256": prov.get("source_sha256"),
        "git_commit": prov.get("git_commit"),
        "solver_version": prov.get("solver_version"),
        "input_digest": (case.get("input", {}).get("provenance") or {}).get("input_digest"),
        "condition": case.get("condition"),
    })


def content_fingerprint(corpus: Dict[str, Any]) -> Dict[str, Any]:
    """Stable digest of what was harvested, so two harvests are comparable.

    ``digest_of(corpus)`` (the byte digest kept in the index as ``corpus_digest``)
    *cannot* serve this purpose: the corpus embeds ``harvested_utc``, so it changes on
    every run even when every source artifact is byte-identical.  This function is the
    reproducibility gate: equal ``content_digest`` <=> same case set, same source bytes,
    same commits, same parsing config.
    """
    cases = sorted((case_fingerprint(c) for c in corpus.get("cases", [])))
    frame = _config_digests()
    return {
        "content_digest": N.digest_of({"cases": cases, "frame_config": frame}),
        "case_count": len(cases),
        "frame_config": frame,
        "frame_config_digest": N.digest_of(frame),
        "per_case": {c.get("case_id"): case_fingerprint(c) for c in corpus.get("cases", [])},
    }


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def save_corpus(corpus: Dict[str, Any], out_dir: Path) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = out_dir / "corpus.json"
    corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=1, sort_keys=True),
                           encoding="utf-8")
    fingerprint = content_fingerprint(corpus)
    index = {
        "schema": "xsolver/corpus_index/v1",
        "corpus_path": str(corpus_path),
        # byte digest of this exact run (includes harvested_utc: identity, not reproducibility)
        "corpus_digest": N.digest_of(corpus),
        # content digest (excludes wall clock / host paths: the reproducibility gate)
        "content_digest": fingerprint["content_digest"],
        "frame_config_digest": fingerprint["frame_config_digest"],
        "case_count": len(corpus.get("cases", [])),
        "harvested_utc": corpus.get("harvested_utc"),
        "coverage": corpus.get("stats", {}).get("coverage"),
        "sources": [
            {
                "case_id": c["case_id"],
                "problem_id": c["problem_id"],
                "source_path": c["provenance"]["source_path"],
                "source_sha256": c["provenance"]["source_sha256"],
                "git_commit": c["provenance"]["git_commit"],
                "solver_version": c["provenance"]["solver_version"],
                "input_digest": c["input"]["provenance"]["input_digest"],
            }
            for c in corpus.get("cases", [])
        ],
    }
    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True),
                          encoding="utf-8")
    return corpus_path, index_path


def load_corpus(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "corpus.json"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)