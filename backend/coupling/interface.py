"""``failure_faces/v1`` -- implementation-level fracture-interface exchange.

Promotes the documented ``failure_faces.json`` contract to code:

* :func:`validate` -- schema validation (returns a list of violations);
* :func:`export_failure_faces` -- build a fully ledgered document from raw
  face/fragment state (areas, normals, centroids and all ledger totals are
  *computed*, never hand-entered);
* :func:`load_fragments` -- fail-closed consumer side: validates first,
  then returns a plain :class:`FragmentSet` ready for a particle-based
  solver to ingest;
* :func:`check_ledger_closure` -- recomputes the five ledgers (area, mass,
  momentum, energy, inertia tensor) from the raw geometry and compares
  them against the declared totals. Closure to machine precision is the
  acceptance bar for the hand-off.

Zero domain semantics: "faces" are 3D polygon loops, "fragments" are rigid
bodies (mass, center of mass, velocity, inertia about own center of mass).
The total inertia ledger is defined about the *global* center of mass via
the parallel-axis theorem, so it is a well-defined composite quantity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "SCHEMA_VERSION",
    "InterfaceError",
    "FragmentSet",
    "face_geometry",
    "validate",
    "export_failure_faces",
    "load_fragments",
    "check_ledger_closure",
]

SCHEMA_VERSION = "failure_faces/v1"

_LEDGER_KEYS = ("total_area", "total_mass", "total_momentum", "total_energy", "total_inertia")


class InterfaceError(ValueError):
    """Schema or ledger violation (fail-closed)."""


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------

def face_geometry(nodes: np.ndarray) -> dict:
    """Area / unit normal / centroid of a 3D polygon via Newell's formula.

    The Newell area vector is translation-invariant for a closed loop, so
    the result does not depend on the coordinate origin.
    """
    n = np.asarray(nodes, dtype=float)
    if n.ndim != 2 or n.shape[1] != 3 or n.shape[0] < 3:
        raise InterfaceError(f"face nodes must be (k>=3, 3), got {n.shape}")
    if not np.all(np.isfinite(n)):
        raise InterfaceError("face nodes contain non-finite values")
    a = 0.5 * np.sum(np.cross(n, np.roll(n, -1, axis=0)), axis=0)
    area = float(np.linalg.norm(a))
    if area <= 0.0:
        raise InterfaceError("degenerate face (zero area)")
    return {
        "area": area,
        "normal": a / area,
        "centroid": n.mean(axis=0),
    }


def _inertia_box(mass: float, a: float, b: float, c: float) -> np.ndarray:
    """Inertia tensor of a uniform box (edges a,b,c) about its own center."""
    return (mass / 12.0) * np.array(
        [[b * b + c * c, 0.0, 0.0], [0.0, a * a + c * c, 0.0], [0.0, 0.0, a * a + b * b]]
    )


def composite_inertia(fragments: list[dict]) -> np.ndarray:
    """Total inertia about the global center of mass (parallel-axis theorem)."""
    masses = np.array([f["mass"] for f in fragments])
    coms = np.array([f["com"] for f in fragments])
    com_global = (masses[:, None] * coms).sum(axis=0) / masses.sum()
    I = np.zeros((3, 3))
    for f in fragments:
        d = np.asarray(f["com"]) - com_global
        I = I + np.asarray(f["inertia"]) + f["mass"] * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return I


# ---------------------------------------------------------------------------
# Export (producer side)
# ---------------------------------------------------------------------------

def export_failure_faces(state: dict) -> dict:
    """Build a ledgered ``failure_faces/v1`` document from raw state.

    ``state`` keys:
      * ``faces``: list of ``{"nodes": (k,3)}`` -- raw interface loops;
      * ``fragments``: list of ``{"mass", "com", "velocity", "inertia"
        (3x3 about own com), "face_ids"}``;
      * ``units``: optional dict (default SI).

    All derived quantities (face area/normal/centroid, ledger totals) are
    computed here -- nothing is hand-entered.
    """
    if "faces" not in state or "fragments" not in state:
        raise InterfaceError("state must contain 'faces' and 'fragments'")
    faces_out = []
    for i, fc in enumerate(state["faces"]):
        g = face_geometry(fc["nodes"])
        faces_out.append(
            {
                "id": int(fc.get("id", i)),
                "nodes": np.asarray(fc["nodes"], dtype=float).tolist(),
                "area": g["area"],
                "normal": g["normal"].tolist(),
                "centroid": g["centroid"].tolist(),
            }
        )
    frags_out = []
    for i, fr in enumerate(state["fragments"]):
        for key in ("mass", "com", "velocity", "inertia"):
            if key not in fr:
                raise InterfaceError(f"fragment {i} missing '{key}'")
        frags_out.append(
            {
                "id": int(fr.get("id", i)),
                "face_ids": [int(x) for x in fr.get("face_ids", [])],
                "mass": float(fr["mass"]),
                "com": np.asarray(fr["com"], dtype=float).tolist(),
                "velocity": np.asarray(fr["velocity"], dtype=float).tolist(),
                "inertia": np.asarray(fr["inertia"], dtype=float).tolist(),
            }
        )
    masses = np.array([f["mass"] for f in frags_out])
    coms = np.array([f["com"] for f in frags_out])
    vels = np.array([f["velocity"] for f in frags_out])
    ledgers = {
        "total_area": float(sum(f["area"] for f in faces_out)),
        "total_mass": float(masses.sum()),
        "total_momentum": (masses[:, None] * vels).sum(axis=0).tolist(),
        "total_energy": float(0.5 * np.sum(masses * np.einsum("ij,ij->i", vels, vels))),
        "total_inertia": composite_inertia(
            [{"mass": f["mass"], "com": f["com"], "inertia": f["inertia"]} for f in frags_out]
        ).tolist(),
    }
    return {
        "schema": SCHEMA_VERSION,
        "units": state.get("units", {"length": "m", "mass": "kg", "time": "s"}),
        "faces": faces_out,
        "fragments": frags_out,
        "ledgers": ledgers,
    }


# ---------------------------------------------------------------------------
# Validation (fail-closed)
# ---------------------------------------------------------------------------

def _finite(x) -> bool:
    try:
        return bool(np.all(np.isfinite(np.asarray(x, dtype=float))))
    except (TypeError, ValueError):
        return False


def validate(doc: dict) -> list[str]:
    """Return a list of schema violations (empty list == valid)."""
    v: list[str] = []
    if not isinstance(doc, dict):
        return ["document is not a JSON object"]
    if doc.get("schema") != SCHEMA_VERSION:
        v.append(f"schema must be {SCHEMA_VERSION!r}, got {doc.get('schema')!r}")
    if not isinstance(doc.get("units"), dict):
        v.append("units must be an object")

    faces = doc.get("faces")
    if not isinstance(faces, list) or not faces:
        v.append("faces must be a non-empty list")
        faces = []
    face_ids = set()
    for i, fc in enumerate(faces):
        if not isinstance(fc, dict):
            v.append(f"faces[{i}] is not an object")
            continue
        if fc.get("id") in face_ids:
            v.append(f"faces[{i}]: duplicate id {fc.get('id')!r}")
        face_ids.add(fc.get("id"))
        nodes = fc.get("nodes")
        if not (isinstance(nodes, list) and len(nodes) >= 3
                and all(isinstance(p, list) and len(p) == 3 for p in nodes)):
            v.append(f"faces[{i}]: nodes must be a list of >=3 [x,y,z] triples")
        elif not _finite(nodes):
            v.append(f"faces[{i}]: non-finite node coordinates")
        if not (isinstance(fc.get("area"), (int, float)) and fc["area"] > 0):
            v.append(f"faces[{i}]: area must be a positive number")
        nrm = fc.get("normal")
        if not (isinstance(nrm, list) and len(nrm) == 3 and _finite(nrm)):
            v.append(f"faces[{i}]: normal must be a 3-vector")
        elif abs(np.linalg.norm(nrm) - 1.0) > 1e-6:
            v.append(f"faces[{i}]: normal is not unit (|n|={np.linalg.norm(nrm):.6g})")
        if not (isinstance(fc.get("centroid"), list) and len(fc["centroid"]) == 3
                and _finite(fc["centroid"])):
            v.append(f"faces[{i}]: centroid must be a finite 3-vector")

    frags = doc.get("fragments")
    if not isinstance(frags, list) or not frags:
        v.append("fragments must be a non-empty list")
        frags = []
    frag_ids = set()
    for i, fr in enumerate(frags):
        if not isinstance(fr, dict):
            v.append(f"fragments[{i}] is not an object")
            continue
        if fr.get("id") in frag_ids:
            v.append(f"fragments[{i}]: duplicate id {fr.get('id')!r}")
        frag_ids.add(fr.get("id"))
        if not (isinstance(fr.get("mass"), (int, float)) and fr["mass"] > 0):
            v.append(f"fragments[{i}]: mass must be > 0")
        for key in ("com", "velocity"):
            val = fr.get(key)
            if not (isinstance(val, list) and len(val) == 3 and _finite(val)):
                v.append(f"fragments[{i}]: {key} must be a finite 3-vector")
        I = fr.get("inertia")
        if not (isinstance(I, list) and len(I) == 3
                and all(isinstance(row, list) and len(row) == 3 for row in I)):
            v.append(f"fragments[{i}]: inertia must be a 3x3 matrix")
        else:
            Ia = np.asarray(I, dtype=float)
            if not _finite(Ia):
                v.append(f"fragments[{i}]: inertia contains non-finite values")
            else:
                if np.max(np.abs(Ia - Ia.T)) > 1e-9 * max(1.0, np.max(np.abs(Ia))):
                    v.append(f"fragments[{i}]: inertia not symmetric")
                else:
                    eig = np.linalg.eigvalsh(0.5 * (Ia + Ia.T))
                    if eig.min() < -1e-9 * max(1.0, float(np.trace(Ia))):
                        v.append(f"fragments[{i}]: inertia not positive semi-definite")
        fids = fr.get("face_ids")
        if not (isinstance(fids, list) and all(int(x) in face_ids for x in fids)):
            v.append(f"fragments[{i}]: face_ids must reference existing face ids")

    led = doc.get("ledgers")
    if not isinstance(led, dict):
        v.append("ledgers must be an object")
    else:
        for key in _LEDGER_KEYS:
            if key not in led:
                v.append(f"ledgers missing '{key}'")
        if "total_momentum" in led and not (
            isinstance(led["total_momentum"], list) and len(led["total_momentum"]) == 3
            and _finite(led["total_momentum"])
        ):
            v.append("ledgers.total_momentum must be a finite 3-vector")
        if "total_inertia" in led:
            I = led["total_inertia"]
            if not (isinstance(I, list) and len(I) == 3
                    and all(isinstance(row, list) and len(row) == 3 for row in I)):
                v.append("ledgers.total_inertia must be a 3x3 matrix")
    return v


def load_fragments(doc: dict) -> "FragmentSet":
    """Consumer side: validate (fail-closed) then return a plain data set."""
    violations = validate(doc)
    if violations:
        raise InterfaceError("failure_faces document failed validation:\n  - " + "\n  - ".join(violations))
    faces = doc["faces"]
    frags = doc["fragments"]
    return FragmentSet(
        n=len(frags),
        masses=np.array([f["mass"] for f in frags]),
        coms=np.array([f["com"] for f in frags]),
        velocities=np.array([f["velocity"] for f in frags]),
        inertias=np.array([f["inertia"] for f in frags]),
        face_nodes=[np.asarray(f["nodes"], dtype=float) for f in faces],
        face_areas=np.array([f["area"] for f in faces]),
        face_normals=np.array([f["normal"] for f in faces]),
        face_centroids=np.array([f["centroid"] for f in faces]),
        fragment_face_ids=[list(f["face_ids"]) for f in frags],
        ledgers={k: (np.asarray(v, dtype=float) if isinstance(v, (list, np.ndarray)) else float(v))
                 for k, v in doc["ledgers"].items()},
    )


@dataclass
class FragmentSet:
    """Plain, solver-agnostic fragment data ready for ingestion."""

    n: int
    masses: np.ndarray
    coms: np.ndarray
    velocities: np.ndarray
    inertias: np.ndarray
    face_nodes: list
    face_areas: np.ndarray
    face_normals: np.ndarray
    face_centroids: np.ndarray
    fragment_face_ids: list
    ledgers: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ledger closure (the four/five books must balance at the hand-off)
# ---------------------------------------------------------------------------

def _rel(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    scale = max(float(np.max(np.abs(b))) if b.size else 0.0, 1e-300)
    return float(np.max(np.abs(a - b))) / scale


def check_ledger_closure(doc: dict, rel_tol: float = 1e-9) -> dict:
    """Recompute all ledgers from raw geometry and compare with declared.

    Ledgers: area (per-face + total), mass, momentum, energy, inertia
    tensor (about the global center of mass). Returns per-ledger relative
    errors and a ``closed`` flag (all errors <= rel_tol).
    """
    violations = validate(doc)
    if violations:
        raise InterfaceError("cannot check closure of an invalid document:\n  - "
                             + "\n  - ".join(violations))
    faces = doc["faces"]
    frags = doc["fragments"]
    masses = np.array([f["mass"] for f in frags])
    coms = np.array([f["com"] for f in frags])
    vels = np.array([f["velocity"] for f in frags])

    recomputed_areas = np.array([face_geometry(f["nodes"])["area"] for f in faces])
    declared_areas = np.array([f["area"] for f in faces])
    errors = {
        "area_per_face": _rel(recomputed_areas, declared_areas),
        "area_total": _rel(np.array([sum(recomputed_areas)]),
                        np.array([doc["ledgers"]["total_area"]])),
        "mass": _rel(np.array([masses.sum()]), np.array([doc["ledgers"]["total_mass"]])),
        "momentum": _rel((masses[:, None] * vels).sum(axis=0),
                         np.asarray(doc["ledgers"]["total_momentum"])),
        "energy": _rel(np.array([0.5 * np.sum(masses * np.einsum("ij,ij->i", vels, vels))]),
                        np.array([doc["ledgers"]["total_energy"]])),
        "inertia": _rel(composite_inertia(frags),
                        np.asarray(doc["ledgers"]["total_inertia"])),
    }
    return {
        "rel_tol": rel_tol,
        "errors": errors,
        "max_error": float(max(errors.values())),
        "closed": bool(max(errors.values()) <= rel_tol),
    }


def to_json(doc: dict, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)


def from_json(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)
