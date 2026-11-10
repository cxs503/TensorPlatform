"""Validation-profile loader (all thresholds are *data*, never code).

A profile is a JSON document with this shape (validated on load)::

    {
      "schema_version": "1.0",
      "profile_id": "<str>",
      "description": "<str>",
      "geometry": {
        "declared_tolerance_rel": 0.01,      # |derived-declared| / |derived|
        "length_reference": "overall",       # overall | declared
        "declared_map": {"<declared_key>": "<derived metric path>"},
        "require_watertight": true,
        "degenerate_area_rel": 1e-12
      },
      "mesh": {
        "max_skewness": 0.85,
        "max_aspect_ratio": 100.0,
        "require_positive_volume": true,
        "y_plus_band": {"min": 30.0, "max": 300.0, "regime": "<label>"},
        "y_plus_unchecked": false,
        "skin_friction_correlation": {"id": "<id>", "a": 0.0576, "b": -0.2,
                                      "citation": "..."}
      },
      "envelopes": [
        {"id": "<id>", "severity": "warn|error", "citation": "...",
         "description": "...",
         "bounds": {"<metric path>": [min, max]}}
      ],
      "verdict": {"block_severities": ["error"]}
    }

Anything not supplied falls back to ``DEFAULT_PROFILE`` below, which is a
*neutral* default (permissive thresholds, no envelopes) so that the engine is
usable with zero domain knowledge.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .reason_codes import GeometryValidationError

DEFAULT_PROFILE: Dict[str, Any] = {
    "schema_version": "1.0",
    "profile_id": "neutral-default",
    "description": "permissive defaults; no domain envelope registered",
    "geometry": {
        "declared_tolerance_rel": 0.01,
        "length_reference": "overall",
        "declared_map": {},
        "require_watertight": True,
        "degenerate_area_rel": 1e-12,
    },
    "mesh": {
        "max_skewness": 0.85,
        "max_aspect_ratio": 100.0,
        "require_positive_volume": True,
        "y_plus_band": None,
        "y_plus_unchecked": True,
        "skin_friction_correlation": {
            "id": "power_law_cf",
            "a": 0.0576,
            "b": -0.2,
            "citation": "Schlichting (1979) local skin-friction power law, turbulent flat plate",
        },
    },
    "envelopes": [],
    "verdict": {"block_severities": ["error"]},
}


class ProfileError(GeometryValidationError):
    """Raised when a profile cannot be loaded or fails schema validation."""

    def __init__(self, message: str, path: Optional[str] = None, **ctx: Any) -> None:
        super().__init__("E_PROFILE_INVALID", message, path=path, **ctx)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def canonical_sha256(payload: Any) -> str:
    """Stable SHA-256 over a canonical JSON serialisation."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass
class ValidationProfile:
    """Immutable-ish view over a profile document."""

    data: Dict[str, Any]
    path: Optional[str] = None
    sha256: str = ""

    # -- convenience accessors ------------------------------------------
    @property
    def profile_id(self) -> str:
        return str(self.data.get("profile_id", "unnamed"))

    @property
    def geometry(self) -> Dict[str, Any]:
        return self.data["geometry"]

    @property
    def mesh(self) -> Dict[str, Any]:
        return self.data["mesh"]

    @property
    def envelopes(self) -> List[Dict[str, Any]]:
        return list(self.data.get("envelopes") or [])

    @property
    def block_severities(self) -> List[str]:
        return list((self.data.get("verdict") or {}).get("block_severities") or ["error"])

    @property
    def declared_tolerance_rel(self) -> float:
        return float(self.geometry.get("declared_tolerance_rel", 0.01))

    @property
    def declared_map(self) -> Dict[str, str]:
        return dict(self.geometry.get("declared_map") or {})

    def summary(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "schema_version": self.data.get("schema_version"),
            "path": self.path,
            "sha256": self.sha256,
            "n_envelopes": len(self.envelopes),
            "envelope_ids": [e.get("id") for e in self.envelopes],
            "thresholds": {
                "declared_tolerance_rel": self.declared_tolerance_rel,
                "max_skewness": self.mesh.get("max_skewness"),
                "max_aspect_ratio": self.mesh.get("max_aspect_ratio"),
                "y_plus_band": self.mesh.get("y_plus_band"),
            },
        }


def _validate(doc: Dict[str, Any], path: Optional[str]) -> None:
    if not isinstance(doc, dict):
        raise ProfileError("profile root must be a JSON object", path=path)
    geo = doc.get("geometry")
    if not isinstance(geo, dict):
        raise ProfileError("profile.geometry must be an object", path=path)
    tol = geo.get("declared_tolerance_rel", 0.01)
    if not isinstance(tol, (int, float)) or not (0.0 < float(tol) < 1.0):
        raise ProfileError(
            f"geometry.declared_tolerance_rel must be in (0,1), got {tol!r}", path=path)
    if geo.get("length_reference", "overall") not in ("overall", "declared"):
        raise ProfileError(
            f"geometry.length_reference must be 'overall' or 'declared', "
            f"got {geo.get('length_reference')!r}", path=path)
    dmap = geo.get("declared_map", {})
    if not isinstance(dmap, dict) or not all(isinstance(v, str) for v in dmap.values()):
        raise ProfileError("geometry.declared_map must map strings to metric paths", path=path)

    mesh = doc.get("mesh")
    if not isinstance(mesh, dict):
        raise ProfileError("profile.mesh must be an object", path=path)
    for key in ("max_skewness", "max_aspect_ratio"):
        val = mesh.get(key)
        if val is not None and (not isinstance(val, (int, float)) or float(val) <= 0):
            raise ProfileError(f"mesh.{key} must be a positive number, got {val!r}", path=path)
    band = mesh.get("y_plus_band")
    if band is not None:
        if not isinstance(band, dict) or "min" not in band or "max" not in band:
            raise ProfileError("mesh.y_plus_band must be {min,max[,regime]}", path=path)
        if float(band["min"]) >= float(band["max"]):
            raise ProfileError("mesh.y_plus_band min must be < max", path=path)

    envs = doc.get("envelopes") or []
    if not isinstance(envs, list):
        raise ProfileError("profile.envelopes must be a list", path=path)
    seen = set()
    for i, env in enumerate(envs):
        if not isinstance(env, dict) or "id" not in env or "bounds" not in env:
            raise ProfileError(f"envelopes[{i}] must contain 'id' and 'bounds'", path=path)
        if env["id"] in seen:
            raise ProfileError(f"duplicate envelope id {env['id']!r}", path=path)
        seen.add(env["id"])
        if env.get("severity", "warn") not in ("warn", "error", "info"):
            raise ProfileError(
                f"envelopes[{i}].severity must be warn|error|info", path=path)
        bounds = env["bounds"]
        if not isinstance(bounds, dict):
            raise ProfileError(f"envelopes[{i}].bounds must be an object", path=path)
        for metric, lim in bounds.items():
            if not isinstance(lim, (list, tuple)) or len(lim) != 2:
                raise ProfileError(
                    f"envelopes[{i}].bounds[{metric!r}] must be a [min,max] pair", path=path)
            lo, hi = lim
            if lo is None and hi is None:
                raise ProfileError(
                    f"envelopes[{i}].bounds[{metric!r}] needs at least one bound", path=path)
            for x in (lo, hi):
                if x is not None and not isinstance(x, (int, float)):
                    raise ProfileError(
                        f"envelopes[{i}].bounds[{metric!r}] bounds must be numbers or null",
                        path=path)
            if lo is not None and hi is not None and float(lo) > float(hi):
                raise ProfileError(
                    f"envelopes[{i}].bounds[{metric!r}] has min > max", path=path)


def load_profile(path: Optional[str] = None,
                 overrides: Optional[Dict[str, Any]] = None) -> ValidationProfile:
    """Load a profile from JSON, merge over ``DEFAULT_PROFILE`` and validate."""
    doc: Dict[str, Any] = {}
    sha = ""
    if path is not None:
        if not os.path.isfile(path):
            raise ProfileError(f"profile file not found: {path}", path=path)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProfileError(f"profile is not valid JSON: {exc}", path=path) from exc
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    merged = _deep_merge(DEFAULT_PROFILE, doc or {})
    if overrides:
        merged = _deep_merge(merged, overrides)
    _validate(merged, path)
    if not sha:
        sha = canonical_sha256(merged)
    return ValidationProfile(data=merged, path=path, sha256=sha)