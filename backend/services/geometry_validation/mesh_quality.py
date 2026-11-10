"""Volume-mesh quality evaluation (domain-neutral).

Formats
-------
* **legacy VTK ASCII** ``UNSTRUCTURED_GRID`` with tetra (10), hexahedron (12) and
  wedge (13) cells.
* **OpenFOAM ``polyMesh`` directory** (``points`` / ``faces`` / ``owner`` /
  ``neighbour`` in ASCII) — the production format for the field solvers.
* **Solver logs** — ``yPlus`` function-object output and ``checkMesh`` summaries
  for cases where only the log survives (a real, and common, production path).

Metric definitions (stated explicitly because every gate number depends on them)
-------------------------------------------------------------------------------
* ``volume``   – ``V = (1/3) * sum_faces  f_centre . S_f`` (divergence theorem on
  the closed cell, Newell area vectors).  Exact for planar-faced cells, works for
  any topology, and returns a negative value for inverted / wrongly ordered cells.
* ``skewness`` – equiangle skewness: for each cell face the corner angles are
  compared with the equiangular value ``theta_e``; per-face skewness is
  ``max((theta_max-theta_e)/(180-theta_e), (theta_e-theta_min)/theta_e)`` and the
  cell value is the maximum over its faces (0 = perfectly regular).
  For OpenFOAM meshes the *checkMesh* definition is used instead, because cell
  corner angles are not available: ``skew = |p - f_centre| / |C_owner - C_neighbour|``
  where ``p`` is the intersection of the owner-neighbour line with the face plane.
* ``aspect_ratio`` – ratio of the largest to the smallest extent of the cell
  vertex bounding box (OpenFOAM ``cellQuality::aspectRatio`` convention).
* ``y_plus``   – ``y+ = y * u_tau / nu`` with ``u_tau = U * sqrt(Cf/2)``.  ``Cf``
  is either supplied (measured wall shear) or evaluated from a correlation whose
  coefficients live in the profile (``a * Re^b``).  The inverse
  ``first_layer_height`` gives the wall-normal spacing that *hits* a target y+,
  which is what a mesh designer actually needs.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .reason_codes import GeometryValidationError

# ---------------------------------------------------------------------------
# cell topology (legacy VTK node ordering, outward-facing node loops)
# ---------------------------------------------------------------------------
VTK_CELL_TYPES = {10: "tetra", 12: "hexahedron", 13: "wedge"}

CELL_FACES: Dict[str, List[Tuple[int, ...]]] = {
    "tetra": [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)],
    "hexahedron": [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                   (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    "wedge": [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
}

CELL_EDGES: Dict[str, List[Tuple[int, int]]] = {
    "tetra": [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)],
    "hexahedron": [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
                   (0, 4), (1, 5), (2, 6), (3, 7)],
    "wedge": [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3),
              (0, 3), (1, 4), (2, 5)],
}


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------
def _newell_area_vector(poly: np.ndarray) -> np.ndarray:
    """Area vector ``0.5 * sum_i r_i x r_{i+1}`` (magnitude = polygon area)."""
    acc = np.zeros(3)
    n = poly.shape[0]
    for i in range(n):
        acc += np.cross(poly[i], poly[(i + 1) % n])
    return 0.5 * acc


def _corner_angles(poly: np.ndarray) -> List[float]:
    angles: List[float] = []
    n = poly.shape[0]
    for i in range(n):
        prev_pt = poly[(i - 1) % n]
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        a = prev_pt - cur
        b = nxt - cur
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na <= 0 or nb <= 0:
            angles.append(0.0)
            continue
        cosv = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
        angles.append(math.degrees(math.acos(cosv)))
    return angles


def _face_skewness(poly: np.ndarray) -> float:
    angles = _corner_angles(poly)
    if not angles:
        return 0.0
    theta_e = 180.0 * (len(angles) - 2) / len(angles)
    tmax, tmin = max(angles), min(angles)
    s = 0.0
    if theta_e < 180.0:
        s = max(s, (tmax - theta_e) / (180.0 - theta_e))
    if theta_e > 0.0:
        s = max(s, (theta_e - tmin) / theta_e)
    return float(max(0.0, s))


def _cell_volume_and_faces(points: np.ndarray, cell: Sequence[int],
                           kind: str) -> Tuple[float, List[np.ndarray]]:
    """Divergence-theorem volume of one cell plus its outward-oriented face loops."""
    polys = [points[[cell[i] for i in idx]] for idx in CELL_FACES[kind]]
    vol = 0.0
    for poly in polys:
        sf = _newell_area_vector(poly)
        fc = poly.mean(axis=0)
        vol += float(np.dot(fc, sf))
    return vol / 3.0, polys


def cell_metrics(points: np.ndarray, cells: Iterable[Tuple[List[int], str]]) -> Dict[str, np.ndarray]:
    vols: List[float] = []
    skews: List[float] = []
    ars: List[float] = []
    spans: List[float] = []
    for nodes, kind in cells:
        if kind not in CELL_FACES:
            raise GeometryValidationError(
                "E_FORMAT_UNSUPPORTED", f"cell type '{kind}' is not supported")
        local = points[nodes]
        vol, polys = _cell_volume_and_faces(points, nodes, kind)
        vols.append(vol)
        skews.append(max((_face_skewness(p) for p in polys), default=0.0))
        edges = [float(np.linalg.norm(local[i] - local[j])) for i, j in CELL_EDGES[kind]]
        emin = min(edges) if edges else 0.0
        emax = max(edges) if edges else 0.0
        ars.append(emax / emin if emin > 1e-30 else float("inf"))
        spans.append(float(np.min(local.max(axis=0) - local.min(axis=0))) / 2.0)
    return {"volume": np.asarray(vols), "skewness": np.asarray(skews),
            "aspect_ratio": np.asarray(ars),
            "cell_min_half_span": np.asarray(spans)}


# ---------------------------------------------------------------------------
# legacy VTK ASCII reader
# ---------------------------------------------------------------------------
def read_vtk_ascii(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise GeometryValidationError("E_FILE_NOT_FOUND", f"mesh file not found: {path}",
                                      path=path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not text.lstrip().startswith("# vtk DataFile"):
        raise GeometryValidationError("E_FORMAT_UNSUPPORTED",
                                      "not a legacy VTK file (missing magic header)", path=path)
    if "ASCII" not in text[:200].upper():
        raise GeometryValidationError(
            "E_FORMAT_UNSUPPORTED", "only ASCII legacy VTK is supported (binary VTK withheld)",
            path=path)
    tokens = text.split()
    try:
        i_points = next(i for i, t in enumerate(tokens) if t.upper() == "POINTS")
        n_points = int(tokens[i_points + 1])
        start = i_points + 3
        pts = np.asarray([float(t) for t in tokens[start:start + 3 * n_points]],
                         dtype=np.float64).reshape(n_points, 3)
        i_cells = next(i for i, t in enumerate(tokens) if t.upper() == "CELLS")
        n_cells = int(tokens[i_cells + 1])
        pos = i_cells + 3
        raw_cells: List[List[int]] = []
        for _ in range(n_cells):
            k = int(tokens[pos])
            raw_cells.append([int(t) for t in tokens[pos + 1:pos + 1 + k]])
            pos += 1 + k
        i_types = next(i for i, t in enumerate(tokens) if t.upper() == "CELL_TYPES")
        types = [int(t) for t in tokens[i_types + 2:i_types + 2 + n_cells]]
    except (StopIteration, IndexError, ValueError) as exc:
        raise GeometryValidationError("E_PARSE", f"malformed legacy VTK: {exc}", path=path) from exc
    cells: List[Tuple[List[int], str]] = []
    skipped = 0
    expected_nodes = {"tetra": 4, "hexahedron": 8, "wedge": 6}
    for nodes, vtk_type in zip(raw_cells, types):
        kind = VTK_CELL_TYPES.get(vtk_type)
        if kind is None or len(nodes) != expected_nodes[kind]:
            skipped += 1
            continue
        cells.append((nodes, kind))
    return {"points": pts, "cells": cells, "skipped_cells": skipped,
            "format": "vtk", "path": path, "n_cells": len(cells)}


# ---------------------------------------------------------------------------
# OpenFOAM polyMesh reader
# ---------------------------------------------------------------------------
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _foam_tokens(text: str) -> str:
    return _COMMENT_RE.sub(" ", text)


_FOAM_HEADER_RE = re.compile(r"FoamFile\s*\{[^}]*\}", re.S)
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def _foam_body(path: str) -> str:
    """Read a polyMesh file and return only the list content (banner + count removed).

    The element count sits *before* the opening parenthesis; keeping it would let
    the face regex swallow ``<count>(<first face>`` as one huge polygon, so the body
    is sliced between the first ``(`` and the last ``)``.
    """
    if not os.path.isfile(path):
        raise GeometryValidationError("E_FILE_NOT_FOUND", f"polyMesh file not found: {path}",
                                      path=path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    text = _FOAM_HEADER_RE.sub(" ", _COMMENT_RE.sub(" ", text))
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        raise GeometryValidationError("E_PARSE", f"polyMesh file has no list body: {path}",
                                      path=path)
    return text[start + 1:end]


def read_openfoam_polymesh(directory: str) -> Dict[str, Any]:
    if not os.path.isdir(directory):
        raise GeometryValidationError("E_FILE_NOT_FOUND",
                                      f"polyMesh directory not found: {directory}",
                                      path=directory)
    for name in ("points", "faces", "owner"):
        if not os.path.isfile(os.path.join(directory, name)):
            raise GeometryValidationError(
                "E_FILE_NOT_FOUND",
                f"polyMesh is missing required file '{name}'", path=directory)

    pts_body = _foam_body(os.path.join(directory, "points"))
    triplets: List[Tuple[float, float, float]] = []
    for match in re.finditer(r"\(([^)]*)\)", pts_body):
        nums = re.findall(_NUM, match.group(1))
        if len(nums) != 3:
            raise GeometryValidationError(
                "E_PARSE", f"point tuple with {len(nums)} component(s): {match.group(0)[:60]}",
                path=directory)
        triplets.append((float(nums[0]), float(nums[1]), float(nums[2])))
    points = np.asarray(triplets, dtype=np.float64)
    if points.size == 0:
        raise GeometryValidationError("E_PARSE", "polyMesh points file yielded no point",
                                      path=directory)

    faces_body = _foam_body(os.path.join(directory, "faces"))
    faces: List[List[int]] = []
    for match in re.finditer(r"(\d+)\s*\(([^)]*)\)", faces_body):
        cnt = int(match.group(1))
        idx = [int(t) for t in re.findall(r"-?\d+", match.group(2))][:cnt]
        if len(idx) >= 3:
            faces.append(idx)
    if not faces:
        raise GeometryValidationError("E_PARSE", "polyMesh faces file yielded no face",
                                      path=directory)

    def _int_list(name: str) -> List[int]:
        body = _foam_body(os.path.join(directory, name))
        return [int(t) for t in re.findall(r"-?\d+", body)]

    owner = _int_list("owner")
    neighbour = _int_list("neighbour") if os.path.isfile(
        os.path.join(directory, "neighbour")) else []
    if len(owner) != len(faces):
        raise GeometryValidationError(
            "E_PARSE", f"owner size {len(owner)} != faces size {len(faces)}", path=directory)
    if neighbour and len(neighbour) > len(faces):
        raise GeometryValidationError(
            "E_PARSE", f"neighbour size {len(neighbour)} > faces size {len(faces)}",
            path=directory)
    for fidx in faces:
        if max(fidx) >= len(points) or min(fidx) < 0:
            raise GeometryValidationError(
                "E_PARSE", "face references a point index outside the points array",
                path=directory)
    return {"points": points, "faces": faces, "owner": owner,
            "neighbour": neighbour, "format": "openfoam", "path": directory}


def _openfoam_cell_metrics(mesh: Dict[str, Any]) -> Dict[str, Any]:
    points = mesh["points"]
    faces = mesh["faces"]
    owner = np.asarray(mesh["owner"], dtype=np.int64)
    neighbour = np.asarray(mesh["neighbour"], dtype=np.int64)
    n_internal = len(neighbour)
    n_cells = int(max(owner.max(), neighbour.max() if n_internal else 0)) + 1

    face_centre = np.zeros((len(faces), 3))
    face_area_vec = np.zeros((len(faces), 3))
    for f, idx in enumerate(faces):
        poly = points[idx]
        face_centre[f] = poly.mean(axis=0)
        face_area_vec[f] = _newell_area_vector(poly)

    # cell volume: V = 1/3 * sum_faces (f_centre . S_f) with outward orientation
    cell_vol = np.zeros(n_cells)
    centre_acc = np.zeros((n_cells, 3))
    centre_w = np.zeros(n_cells)
    for f, idx in enumerate(faces):
        poly = points[idx]
        fc = face_centre[f]
        sf = face_area_vec[f]
        o = int(owner[f])
        cell_vol[o] += float(np.dot(fc, sf)) / 3.0
        centre_acc[o] += fc * np.linalg.norm(sf)
        centre_w[o] += np.linalg.norm(sf)
        if f < n_internal:
            nb = int(neighbour[f])
            cell_vol[nb] -= float(np.dot(fc, sf)) / 3.0
            centre_acc[nb] += fc * np.linalg.norm(sf)
            centre_w[nb] += np.linalg.norm(sf)
    cell_centre = np.where(centre_w[:, None] > 0, centre_acc / np.maximum(centre_w, 1e-30)[:, None], 0.0)

    # checkMesh-style face skewness
    skew = np.zeros(n_cells)
    face_skew = np.zeros(len(faces))
    for f in range(n_internal):
        o, nb = int(owner[f]), int(neighbour[f])
        d = cell_centre[nb] - cell_centre[o]
        nd = float(np.dot(d, d))
        if nd <= 1e-300:
            continue
        n_vec = face_area_vec[f]
        denom = float(np.dot(d, n_vec))
        if abs(denom) < 1e-300:
            continue
        t = float(np.dot(face_centre[f] - cell_centre[o], n_vec)) / denom
        p = cell_centre[o] + t * d
        s = float(np.linalg.norm(p - face_centre[f]) / math.sqrt(nd))
        face_skew[f] = s
        skew[o] = max(skew[o], s)
        skew[nb] = max(skew[nb], s)

    # aspect ratio from the cell vertex bounding box
    cell_nodes: List[set] = [set() for _ in range(n_cells)]
    for f, idx in enumerate(faces):
        o = int(owner[f])
        cell_nodes[o].update(idx)
        if f < n_internal:
            cell_nodes[int(neighbour[f])].update(idx)
    ar = np.zeros(n_cells)
    for c, nodes in enumerate(cell_nodes):
        if len(nodes) < 4:
            ar[c] = float("nan")
            continue
        pts = points[sorted(nodes)]
        spans = pts.max(axis=0) - pts.min(axis=0)
        pos = spans[spans > 1e-30]
        ar[c] = float(pos.max() / pos.min()) if pos.size else float("nan")

    # cell bounding-box min wall distance proxy: half of the smallest span
    return {
        "volume": cell_vol,
        "skewness": skew,
        "aspect_ratio": ar,
        "face_skewness": face_skew,
        "cell_centre": cell_centre,
        "cell_min_half_span": np.asarray([
            (points[sorted(n)].max(axis=0) - points[sorted(n)].min(axis=0)).min() / 2.0
            if len(n) >= 4 else float("nan") for n in cell_nodes]),
    }


# ---------------------------------------------------------------------------
# log parsers
# ---------------------------------------------------------------------------
def parse_checkmesh_log(text: str) -> Dict[str, Any]:
    """Parse the summary block of an OpenFOAM ``checkMesh`` log."""
    out: Dict[str, Any] = {}
    num = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
    patterns = {
        "max_aspect_ratio": r"Max\s+aspect\s+ratio[^=]*=\s*" + num,
        "max_skewness": r"Max\s+skewness\s*=\s*" + num,
        "n_cells": r"cells:\s+(\d+)",
        "n_faces": r"faces:\s+(\d+)",
        "n_points": r"points:\s+(\d+)",
        "max_non_orthogonality": r"non-orthogonality\s+Max:?\s*=?\s*" + num,
        "min_volume": r"Min\s+volume\s*=\s*" + num,
        "max_volume": r"Max\s+volume\s*=\s*" + num,
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, re.I)
        if m and m.group(1) is not None:
            val = m.group(1)
            out[key] = int(val) if key.startswith("n_") else float(val)
    if "Mesh OK" in text:
        out["checkmesh_ok"] = True
    elif "Failed" in text:
        out["checkmesh_ok"] = False
    return out


def parse_yplus_log(text: str) -> Dict[str, Dict[str, float]]:
    """Parse an OpenFOAM ``yPlus`` function-object log into per-patch statistics."""
    patches: Dict[str, Dict[str, float]] = {}
    for match in re.finditer(
            r"^\s*(?:patch\s+)?(\S+)\s+y\+\s*:\s*min\s*=\s*(" + _NUM +
            r")\s*,\s*max\s*=\s*(" + _NUM + r")\s*,\s*average\s*=\s*(" + _NUM + r")",
            text, re.M):
        name = match.group(1)
        patches[name] = {
            "min": float(match.group(2)),
            "max": float(match.group(3)),
            "average": float(match.group(4)),
        }
    return patches


# ---------------------------------------------------------------------------
# y+ physics
# ---------------------------------------------------------------------------
def skin_friction_coefficient(reynolds: float, correlation: Dict[str, Any]) -> float:
    a = float(correlation.get("a", 0.0576))
    b = float(correlation.get("b", -0.2))
    if reynolds <= 0:
        raise GeometryValidationError("E_INPUT_TYPE", "Reynolds number must be positive",
                                      value=reynolds)
    return a * reynolds ** b


def y_plus(wall_distance: float, u_inf: float, nu: float,
           cf: Optional[float] = None, reynolds: Optional[float] = None,
           correlation: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Friction-velocity based wall coordinate ``y+ = y u_tau / nu``."""
    for name, val in (("wall_distance", wall_distance), ("u_inf", u_inf), ("nu", nu)):
        if not np.isfinite(val) or val == 0:
            raise GeometryValidationError("E_INPUT_TYPE", f"{name} must be finite and non-zero",
                                          field=name, value=val)
    if nu < 0:
        raise GeometryValidationError("E_INPUT_TYPE", "nu must be positive", value=nu)
    if cf is None:
        if reynolds is None:
            reynolds = max(u_inf * 1.0 / nu, 1.0e3)
        cf = skin_friction_coefficient(reynolds, correlation or {})
    u_tau = abs(u_inf) * math.sqrt(max(cf, 0.0) / 2.0)
    yp = abs(wall_distance) * u_tau / nu
    return {"y_plus": yp, "cf": cf, "u_tau": u_tau, "reynolds": reynolds}


def first_layer_height(target_y_plus: float, u_inf: float, nu: float,
                       cf: Optional[float] = None, reynolds: Optional[float] = None,
                       correlation: Optional[Dict[str, Any]] = None) -> float:
    """Wall-normal spacing that achieves ``target_y_plus`` (design guidance)."""
    solved = y_plus(1.0, u_inf, nu, cf=cf, reynolds=reynolds, correlation=correlation)
    if solved["u_tau"] <= 0:
        return float("inf")
    return target_y_plus * nu / solved["u_tau"]


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
def _stats(arr: np.ndarray) -> Dict[str, Any]:
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return {"n": 0, "min": None, "mean": None, "max": None, "p95": None}
    return {
        "n": int(finite.size),
        "min": float(finite.min()),
        "mean": float(finite.mean()),
        "max": float(finite.max()),
        "p95": float(np.percentile(finite, 95)),
    }


def evaluate_mesh(mesh: Dict[str, Any], profile_mesh: Dict[str, Any],
                  flow: Optional[Dict[str, Any]] = None,
                  wall_log_text: Optional[str] = None) -> Dict[str, Any]:
    """Compute mesh metrics + threshold violations for one loaded mesh."""
    violations: List[Dict[str, Any]] = []
    if mesh["format"] == "openfoam":
        raw = _openfoam_cell_metrics(mesh)
        cells = raw
    else:
        if not mesh["cells"]:
            raise GeometryValidationError("E_MESH_EMPTY", "mesh contains no cell",
                                          path=mesh.get("path"))
        cells = cell_metrics(mesh["points"], mesh["cells"])
        raw = dict(cells)
    vol = np.asarray(cells["volume"], dtype=np.float64)
    skew = np.asarray(cells["skewness"], dtype=np.float64)
    ar = np.asarray(cells["aspect_ratio"], dtype=np.float64)
    if vol.size == 0:
        raise GeometryValidationError("E_MESH_EMPTY", "mesh contains no cell",
                                      path=mesh.get("path"))

    max_skew = profile_mesh.get("max_skewness")
    max_ar = profile_mesh.get("max_aspect_ratio")
    need_positive = bool(profile_mesh.get("require_positive_volume", True))

    bad_vol = np.where(vol <= 0.0)[0]
    if need_positive and bad_vol.size:
        violations.append({
            "code": "E_MESH_NEGATIVE_VOLUME",
            "metric": "volume",
            "count": int(bad_vol.size),
            "worst": float(vol[bad_vol].min()),
            "cells": bad_vol[:20].tolist(),
        })
    if max_skew is not None:
        bad = np.where(skew > float(max_skew))[0]
        if bad.size:
            violations.append({
                "code": "E_MESH_SKEWNESS",
                "metric": "skewness",
                "count": int(bad.size),
                "limit": float(max_skew),
                "worst": float(np.nanmax(skew)),
                "cells": bad[:20].tolist(),
            })
    if max_ar is not None:
        bad = np.where(ar > float(max_ar))[0]
        if bad.size:
            violations.append({
                "code": "E_MESH_ASPECT_RATIO",
                "metric": "aspect_ratio",
                "count": int(bad.size),
                "limit": float(max_ar),
                "worst": float(np.nanmax(ar)),
                "cells": bad[:20].tolist(),
            })

    wall: Dict[str, Any] = {}
    band = profile_mesh.get("y_plus_band")
    patches = parse_yplus_log(wall_log_text) if wall_log_text else {}
    if patches:
        wall["source"] = "solver_log"
        wall["patches"] = patches
        outside = {name: stats for name, stats in patches.items()
                   if band and (stats["max"] > float(band["max"])
                                or stats["min"] < float(band["min"]))}
        worst = max(patches.values(), key=lambda s: s["max"])
        wall["max_y_plus"] = worst["max"]
        wall["min_y_plus"] = min(s["min"] for s in patches.values())
        if band:
            wall["band"] = {"min": float(band["min"]), "max": float(band["max"]),
                            "regime": band.get("regime")}
            wall["within_band"] = not outside
            if outside:
                violations.append({
                    "code": "E_MESH_YPLUS",
                    "metric": "y_plus",
                    "count": len(outside),
                    "limit": [float(band["min"]), float(band["max"])],
                    "worst": worst["max"],
                    "patches": sorted(outside),
                })
    elif flow:
        corridor = None
        if "wall_distance" not in flow and raw.get("cell_min_half_span") is not None:
            spans = np.asarray(raw["cell_min_half_span"], dtype=np.float64)
            finite = spans[np.isfinite(spans) & (spans > 0)]
            corridor = float(np.min(finite)) if finite.size else None
        y_dist = flow.get("wall_distance", corridor)
        if y_dist is None:
            violations.append({
                "code": "W_MESH_YPLUS_UNKNOWN",
                "metric": "y_plus",
                "message": "no wall distance supplied and no wall-adjacent cell detected",
            })
        else:
            solved = y_plus(float(y_dist), float(flow["u_inf"]), float(flow["nu"]),
                            cf=flow.get("cf"), reynolds=flow.get("reynolds"),
                            correlation=profile_mesh.get("skin_friction_correlation"))
            wall.update({
                "source": "first_cell_half_span" if corridor is not None and "wall_distance" not in flow else "declared",
                "wall_distance": float(y_dist),
                **solved,
            })
            if band:
                lo, hi = float(band["min"]), float(band["max"])
                wall["band"] = {"min": lo, "max": hi, "regime": band.get("regime")}
                wall["within_band"] = bool(lo <= solved["y_plus"] <= hi)
                wall["recommended_wall_distance"] = first_layer_height(
                    (lo + hi) / 2.0, float(flow["u_inf"]), float(flow["nu"]),
                    cf=flow.get("cf"), reynolds=flow.get("reynolds"),
                    correlation=profile_mesh.get("skin_friction_correlation"))
                if not wall["within_band"]:
                    violations.append({
                        "code": "E_MESH_YPLUS",
                        "metric": "y_plus",
                        "count": 1,
                        "limit": [lo, hi],
                        "worst": solved["y_plus"],
                    })
    elif band and not profile_mesh.get("y_plus_unchecked", True):
        violations.append({
            "code": "W_MESH_YPLUS_UNKNOWN",
            "metric": "y_plus",
            "message": "y+ band is required by the profile but no flow condition was supplied",
        })

    return {
        "format": mesh["format"],
        "path": mesh.get("path"),
        "n_cells": int(vol.size),
        "skipped_cells": int(mesh.get("skipped_cells", 0)),
        "volume": _stats(vol),
        "total_volume": float(np.nansum(vol)),
        "skewness": _stats(skew),
        "aspect_ratio": _stats(ar),
        "y_plus": wall,
        "violations": violations,
        "checks": {
            "max_skewness_limit": max_skew,
            "max_aspect_ratio_limit": max_ar,
            "positive_volume_required": need_positive,
        },
    }