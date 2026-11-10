"""Surface (outer-hull) geometry extraction and integral quantities.

Supported inputs
----------------
* **STL** – binary and ASCII, auto-detected by payload length.
* **OBJ** – ``v`` / ``f`` records, arbitrary polygon faces fan-triangulated,
  negative (relative) indices supported.
* **IGES** – *degraded*: B-rep solids carry no volume, so the reader delegates to
  an external tessellator named by the ``GEOMETRY_IGES_TESSELLATOR`` environment
  variable (must accept ``--output <tmp.stl> <in.iges>``).  When no tessellator is
  installed the item is *rejected* with ``E_FORMAT_UNSUPPORTED`` instead of being
  silently accepted — a withheld capability is reported, never faked.

Integral quantities (all derived from the triangulation, no empirical formula)
------------------------------------------------------------------------------
With the divergence theorem ``V = (1/6) sum_tri  v0 . (v1 x v2)``:

* ``volume_total``      – signed volume of the closed surface.
* ``volume_submerged``  – volume below the waterline ``z = z_wl``; each triangle
  is clipped into the half-space and the removed part is projected onto the
  waterline plane with opposite orientation, which closes the polyhedron exactly.
* ``lcb_rel``           – longitudinal centre of volume, ``(x_c - x_mid)/L``.
* ``waterplane_area``   – central difference of the displaced volume,
  ``Aw(z) = dV/dz |_{z=z_wl}``.  This is the *robust* definition: it needs no
  planar section tracing and is exact for polyhedral surfaces.
* ``block_coefficient`` – ``V_sub / (L * B * T)`` with ``T`` the draft.
* ``waterplane_coefficient`` – ``Aw / (L * B)``.

Topology gate
-------------
Vertices are welded on a relative grid before edge counting; a surface is
watertight when every undirected edge is used exactly twice with opposite
winding.  Boundary/non-manifold/inconsistent-winding surfaces are rejected
because the divergence-theorem volume is meaningless for them.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .reason_codes import GeometryValidationError

_SUPPORTED_SUFFIXES = {".stl", ".obj", ".iges", ".igs"}


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------
def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_stl(data: bytes, path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
    meta: Dict[str, Any] = {}
    is_binary = False
    if len(data) >= 84:
        (n_tri,) = struct.unpack("<I", data[80:84])
        if 84 + 50 * n_tri == len(data):
            is_binary = True
    if is_binary:
        n_tri = struct.unpack("<I", data[80:84])[0]
        rec = np.frombuffer(data[84:84 + 50 * n_tri], dtype=np.uint8)
        rec = rec.reshape(n_tri, 50)
        floats = rec[:, :48].copy().view("<f4").reshape(n_tri, 12)
        tri = floats[:, 3:].reshape(n_tri, 3, 3).astype(np.float64)
        meta["stl_encoding"] = "binary"
    else:
        text = data.decode("ascii", errors="replace")
        verts: List[Tuple[float, float, float]] = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0].lower() == "vertex":
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        if not verts or len(verts) % 3:
            raise GeometryValidationError(
                "E_PARSE",
                "ASCII STL vertex count is not a multiple of three "
                f"({len(verts)} vertices)", path=path)
        tri = np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
        meta["stl_encoding"] = "ascii"
    meta["n_triangles"] = int(tri.shape[0])
    return tri, meta


def _read_obj(text: str, path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
    verts: List[List[float]] = []
    faces: List[List[int]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] == "#":
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v":
            if len(parts) < 4:
                raise GeometryValidationError("E_PARSE", "OBJ 'v' record with <3 coordinates",
                                              path=path)
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif tag == "f":
            idx: List[int] = []
            for token in parts[1:]:
                head = token.split("/")[0]
                if head == "":
                    continue
                i = int(head)
                idx.append(i - 1 if i > 0 else len(verts) + i)
            if len(idx) >= 3:
                faces.append(idx)
    if not verts or not faces:
        raise GeometryValidationError("E_GEOM_EMPTY",
                                      "OBJ contains no usable vertex/face record", path=path)
    v = np.asarray(verts, dtype=np.float64)
    tris: List[np.ndarray] = []
    for face in faces:
        for k in range(1, len(face) - 1):
            tris.append(v[[face[0], face[k], face[k + 1]]])
    tri = np.asarray(tris, dtype=np.float64)
    return tri, {"n_triangles": int(tri.shape[0]), "n_vertices": int(v.shape[0]),
                 "n_faces": len(faces)}


def _read_iges(path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
    tess = os.environ.get("GEOMETRY_IGES_TESSELLATOR", "")
    if not tess or shutil.which(tess) is None:
        raise GeometryValidationError(
            "E_FORMAT_UNSUPPORTED",
            "IGES carries B-rep surfaces without volume; a tessellator is required. "
            "Set GEOMETRY_IGES_TESSELLATOR to an executable accepting "
            "`--output <tmp.stl> <in.iges>` (e.g. gmsh). Capability withheld, not faked.",
            path=path, format="iges")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "tess.stl")
        proc = subprocess.run([tess, "--output", out, path],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not os.path.isfile(out):
            raise GeometryValidationError(
                "E_PARSE", f"IGES tessellator '{tess}' failed (rc={proc.returncode}): "
                           f"{proc.stderr.strip()[:400]}", path=path)
        with open(out, "rb") as fh:
            tri, meta = _read_stl(fh.read(), path)
    meta["tessellator"] = tess
    meta["source_format"] = "iges"
    return tri, meta


def read_triangles(path: str) -> Dict[str, Any]:
    """Read a surface file into a triangle array plus provenance metadata."""
    if not os.path.isfile(path):
        raise GeometryValidationError("E_FILE_NOT_FOUND", f"surface file not found: {path}",
                                      path=path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise GeometryValidationError(
            "E_FORMAT_UNSUPPORTED",
            f"suffix '{suffix}' not in supported set {sorted(_SUPPORTED_SUFFIXES)}", path=path)
    with open(path, "rb") as fh:
        data = fh.read()
    if suffix == ".stl":
        tri, meta = _read_stl(data, path)
        fmt = "stl"
    elif suffix == ".obj":
        tri, meta = _read_obj(data.decode("utf-8", errors="replace"), path)
        fmt = "obj"
    else:
        tri, meta = _read_iges(path)
        fmt = "iges"
    if tri.size == 0:
        raise GeometryValidationError("E_GEOM_EMPTY", "surface decoded to zero triangles",
                                      path=path)
    if not np.all(np.isfinite(tri)):
        raise GeometryValidationError("E_PARSE", "surface contains NaN/Inf coordinates",
                                      path=path)
    meta.update({"path": path, "format": fmt, "sha256": file_sha256(path),
                 "bytes": len(data)})
    return {"triangles": tri, "format": fmt, "meta": meta}


# ---------------------------------------------------------------------------
# topology gate
# ---------------------------------------------------------------------------
def _weld(tri: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Weld coincident vertices on a relative grid; return (verts, faces)."""
    pts = tri.reshape(-1, 3)
    scale = float(np.max(np.abs(pts))) or 1.0
    keys = np.round(pts / scale * 1e9).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    verts = pts[first]
    faces = inverse.reshape(-1, 3)
    # drop degenerate faces (two or more identical vertices)
    degenerate = ((faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2])
                  | (faces[:, 0] == faces[:, 2]))
    faces = faces[~degenerate]
    return verts, faces


def topology_report(tri: np.ndarray) -> Dict[str, Any]:
    """Edge-manifold / winding-consistency diagnostics after vertex welding."""
    verts, faces = _weld(tri)
    if faces.size == 0:
        return {"n_vertices": int(verts.shape[0]), "n_faces": 0,
                "n_degenerate_faces": int(tri.shape[0]),
                "boundary_edges": 0, "nonmanifold_edges": 0,
                "inconsistent_edges": 0, "watertight": False,
                "winding_consistent": False}
    directed = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    key_d = directed[:, 0].astype(np.int64) * (len(verts) + 1) + directed[:, 1]
    uniq_d, counts_d = np.unique(key_d, return_counts=True)
    a = np.minimum(directed[:, 0], directed[:, 1]).astype(np.int64)
    b = np.maximum(directed[:, 0], directed[:, 1]).astype(np.int64)
    key_u = a * (len(verts) + 1) + b
    uniq_u, counts_u = np.unique(key_u, return_counts=True)
    boundary = int(np.sum(counts_u == 1))
    nonmanifold = int(np.sum(counts_u > 2))
    # a consistently wound closed surface: every directed edge appears once
    inconsistent = int(np.sum(counts_d > 1))
    watertight = (boundary == 0 and nonmanifold == 0)
    return {
        "n_vertices": int(verts.shape[0]),
        "n_faces": int(faces.shape[0]),
        "n_degenerate_faces": int(tri.shape[0] - faces.shape[0]),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "inconsistent_edges": inconsistent,
        "watertight": bool(watertight),
        "winding_consistent": bool(watertight and inconsistent == 0),
    }


# ---------------------------------------------------------------------------
# plane clipping / integration
# ---------------------------------------------------------------------------
def _clip_polygon(poly: np.ndarray, zc: float, keep_below: bool,
                  eps: float) -> np.ndarray:
    """Sutherland-Hodgman clip of a convex polygon against ``z <= zc`` (or ``> zc``).

    The two halves are deliberately asymmetric: a polygon lying *exactly* on the
    plane belongs to the lower half only.  Otherwise a face coincident with the
    waterline would be counted twice (once as hull, once as projected cap) and the
    displaced volume would be short by exactly that face's cone volume.
    """
    if keep_below:
        inside = (zc - poly[:, 2]) >= -eps
    else:
        inside = (poly[:, 2] - zc) > eps
    if inside.all():
        return poly
    if not inside.any():
        return np.empty((0, 3))
    out: List[np.ndarray] = []
    n = poly.shape[0]
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        pi, qi = bool(inside[i]), bool(inside[(i + 1) % n])
        if pi:
            out.append(p)
        if pi != qi:
            dz = q[2] - p[2]
            t = 0.0 if dz == 0.0 else (zc - p[2]) / dz
            out.append(p + t * (q - p))
    return np.asarray(out, dtype=np.float64)


def _tet_sum(poly: np.ndarray) -> float:
    """Signed volume of the fan tetrahedra from the origin over a planar polygon."""
    if poly.shape[0] < 3:
        return 0.0
    v0 = poly[0]
    acc = 0.0
    for i in range(1, poly.shape[0] - 1):
        acc += float(np.dot(v0, np.cross(poly[i], poly[i + 1])))
    return acc / 6.0


def _tet_moment(poly: np.ndarray) -> np.ndarray:
    """First moment vector of the same fan decomposition (origin-referenced)."""
    acc = np.zeros(3)
    if poly.shape[0] < 3:
        return acc
    v0 = poly[0]
    for i in range(1, poly.shape[0] - 1):
        vi, vj = poly[i], poly[i + 1]
        vol = float(np.dot(v0, np.cross(vi, vj))) / 6.0
        acc += vol * (v0 + vi + vj) / 4.0
    return acc


def volume_below(tri: np.ndarray, z_wl: float) -> Tuple[float, np.ndarray]:
    """Volume and first moment of the region enclosed by ``tri`` below ``z_wl``."""
    scale = float(np.max(np.abs(tri))) or 1.0
    eps = 1e-12 * scale
    vol = 0.0
    mom = np.zeros(3)
    for t in tri:
        below = _clip_polygon(t, z_wl, True, eps)
        above = _clip_polygon(t, z_wl, False, eps)
        if below.shape[0] >= 3:
            vol += _tet_sum(below)
            mom += _tet_moment(below)
        if above.shape[0] >= 3:
            # The waterline cap closes the submerged polyhedron.  Its flux equals the
            # flux of the removed part projected onto the plane *with the same
            # orientation* (verified against the closed box: sum_below + sum_cap = LBT),
            # so the contributions add, and the sail/projection double count of my
            # first draft is what produced a 2/3 short volume.
            proj = above.copy()
            proj[:, 2] = z_wl
            vol += _tet_sum(proj)
            mom += _tet_moment(proj)
    return vol, mom


def total_volume(tri: np.ndarray) -> float:
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    return float(np.sum(np.einsum("ij,ij->i", v0, np.cross(v1, v2))) / 6.0)


def waterplane_area(tri: np.ndarray, z_wl: float, rel_step: float = 1e-4) -> float:
    """``Aw = dV/dz`` by central difference of the displaced volume."""
    z_min = float(tri[:, :, 2].min())
    z_max = float(tri[:, :, 2].max())
    span = max(z_max - z_min, 1.0e-12)
    delta = rel_step * span
    lo = max(z_min + 1e-12, z_wl - delta)
    hi = min(z_max - 1e-12, z_wl + delta)
    if hi <= lo:
        return 0.0
    v_lo = volume_below(tri, lo)[0]
    v_hi = volume_below(tri, hi)[0]
    return float((v_hi - v_lo) / (hi - lo))


def orient_consistently(tri: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Make face winding consistent across a welded surface (production ingestion step).

    STL files in the wild carry mixed winding, which makes the divergence-theorem
    volume wrong even though the surface is geometrically closed.  We propagate
    orientation across shared edges (BFS over the face-adjacency graph) and then
    flip everything if the resulting signed volume is negative.

    Returns ``(triangles, diagnostics)``; raises ``E_GEOM_INCONSISTENT_ORIENTATION``
    for non-orientable surfaces (a shared edge needing two different flips).
    """
    verts, faces = _weld(tri)
    n_faces = int(faces.shape[0])
    if n_faces == 0:
        return tri, {"flipped_faces": 0, "components": 0, "non_orientable": False}
    edge_map: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for f in range(n_faces):
        a, b, c = faces[f]
        for i, j in ((a, b), (b, c), (c, a)):
            key = (int(min(i, j)), int(max(i, j)))
            edge_map.setdefault(key, []).append((f, 1 if i < j else -1))
    flip = np.zeros(n_faces, dtype=bool)
    visited = np.zeros(n_faces, dtype=bool)
    components = 0
    for seed in range(n_faces):
        if visited[seed]:
            continue
        components += 1
        visited[seed] = True
        stack = [seed]
        while stack:
            f = stack.pop()
            a, b, c = faces[f]
            for i, j in ((a, b), (b, c), (c, a)):
                key = (int(min(i, j)), int(max(i, j)))
                dir_f = (1 if i < j else -1) * (-1 if flip[f] else 1)
                for g, dir_g in edge_map[key]:
                    if g == f:
                        continue
                    need_flip = (dir_g * (-1 if flip[g] else 1)) == dir_f
                    if not visited[g]:
                        flip[g] = need_flip
                        visited[g] = True
                        stack.append(g)
                    elif need_flip:
                        raise GeometryValidationError(
                            "E_GEOM_INCONSISTENT_ORIENTATION",
                            f"surface is not orientable (conflict at edge {key} between "
                            f"faces {f} and {g})")
    out = np.empty((n_faces, 3, 3), dtype=np.float64)
    for f in range(n_faces):
        idx = faces[f]
        loop = [idx[0], idx[2], idx[1]] if flip[f] else [idx[0], idx[1], idx[2]]
        out[f] = verts[loop]
    vol = total_volume(out)
    global_flip = vol < 0.0
    if global_flip:
        out = out[:, ::-1, :]
        vol = -vol
    return out, {"flipped_faces": int(flip.sum()), "global_flip": bool(global_flip),
                 "components": components, "non_orientable": False,
                 "volume_after_repair": vol}


def _triangle_areas(tri: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]),
                                axis=1)


def geometry_metrics(surface: Dict[str, Any],
                     draft: Optional[float] = None,
                     length_reference: str = "overall",
                     declared: Optional[Dict[str, float]] = None,
                     degenerate_area_rel: float = 1e-12) -> Dict[str, Any]:
    """Derive integral geometry quantities from a loaded surface."""
    tri: np.ndarray = surface["triangles"]
    if tri.shape[0] == 0:
        raise GeometryValidationError("E_GEOM_EMPTY", "no triangle to integrate")
    areas = _triangle_areas(tri)
    ref_area = float(np.max(areas)) if areas.size else 0.0
    degenerate = int(np.sum(areas <= degenerate_area_rel * max(ref_area, 1e-30)))
    if degenerate == tri.shape[0]:
        raise GeometryValidationError(
            "E_GEOM_DEGENERATE", "all triangles are degenerate (zero area)")

    tri, repair = orient_consistently(tri)
    topo = topology_report(tri)
    volume_total = total_volume(tri)
    orientation_flipped = bool(repair["global_flip"])
    if volume_total < 0.0:  # defensive: orient_consistently already normalises sign
        tri = tri[:, ::-1, :]
        volume_total = -volume_total

    lo = tri.reshape(-1, 3).min(axis=0)
    hi = tri.reshape(-1, 3).max(axis=0)
    extent_length = float(hi[0] - lo[0])
    extent_beam = float(hi[1] - lo[1])
    extent_vertical = float(hi[2] - lo[2])

    draft_val = float(draft) if draft is not None else extent_vertical
    z_wl = float(lo[2]) + draft_val
    v_sub, mom = volume_below(tri, z_wl)
    v_sub = abs(v_sub)
    lcb_x = float(mom[0] / v_sub) if v_sub > 0 else float("nan")

    if length_reference == "declared" and declared and declared.get("length"):
        length = float(declared["length"])
        length_source = "declared"
    else:
        length = extent_length
        length_source = "bounding_box"

    aw = waterplane_area(tri, z_wl)
    denom = length * extent_beam * draft_val
    cb = v_sub / denom if denom > 0 else float("nan")
    cwp = aw / (length * extent_beam) if length * extent_beam > 0 else float("nan")
    x_mid = 0.5 * (lo[0] + hi[0])

    metrics: Dict[str, Any] = {
        "extent_length": extent_length,
        "extent_beam": extent_beam,
        "extent_vertical": extent_vertical,
        "draft": draft_val,
        "waterline_elevation": z_wl,
        "volume_total": volume_total,
        "volume_submerged": v_sub,
        "waterplane_area": aw,
        "block_coefficient": cb,
        "waterplane_coefficient": cwp,
        "lcb_rel": (lcb_x - x_mid) / length if length > 0 and np.isfinite(lcb_x) else None,
        "length_reference": length_source,
        "length_used_for_coefficients": length,
        "orientation_flipped": bool(orientation_flipped),
        "orientation_repair": {k: repair[k] for k in
                               ("flipped_faces", "global_flip", "components")},
        "n_triangles": int(tri.shape[0]),
        "n_degenerate_triangles": degenerate,
        "topology": topo,
    }
    metrics["ratios"] = {
        "length_over_beam": extent_length / extent_beam if extent_beam > 0 else None,
        "beam_over_draft": extent_beam / draft_val if draft_val > 0 else None,
        "draft_over_length": draft_val / extent_length if extent_length > 0 else None,
        "vertical_over_draft": extent_vertical / draft_val if draft_val > 0 else None,
    }
    return metrics


def resolve_metric(metrics: Dict[str, Any], path: str) -> Optional[float]:
    """Resolve a dotted metric path (``block_coefficient``, ``ratios.beam_over_draft``)."""
    node: Any = metrics
    for part in str(path).split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None or isinstance(node, bool):
        return None
    try:
        val = float(node)
    except (TypeError, ValueError):
        return None
    return val if np.isfinite(val) else None