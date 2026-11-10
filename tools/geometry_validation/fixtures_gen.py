"""Deterministic fixture generators for the geometry/mesh validation gate.

No binary fixtures are committed: every case is generated from an analytic
definition, so a reviewer can recompute the expected value by hand.

* ``wigley_triangles`` – closed surface of the classical Wigley hull
  ``y(x,z) = (B/2)(1-(2x/L)^2)(1-(z/T)^2)`` with a flat bottom at ``z=0``.
  Analytic displacement ``V = (4/9) L B T`` (Cb = 4/9) and analytic waterplane
  area ``Aw(z) = (2LB/3)(1-(z/T)^2)`` (zero at the design waterline).
* ``box_triangles`` – closed box, ``V = LBT``, ``Cb = 1``, ``Aw = LB``.
* ``hex_mesh`` / ``write_vtk_ascii`` – structured hexahedral volume mesh with
  optional shear, anisotropic grading and node-swap inversion.
* ``write_openfoam_polymesh`` – minimal but valid ``polyMesh`` (points / faces /
  owner / neighbour, ASCII) for the production reader.
"""
from __future__ import annotations

import math
import os
import struct
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# surfaces
# ---------------------------------------------------------------------------
def box_triangles(L: float = 10.0, B: float = 4.0, T: float = 2.0,
                  z0: float = 0.0) -> np.ndarray:
    """Closed axis-aligned box (12 triangles, outward winding)."""
    x0, x1 = -L / 2.0, L / 2.0
    y0, y1 = -B / 2.0, B / 2.0
    z0, z1 = z0, z0 + T
    p = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]])
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append([p[a], p[b], p[c]])
        tris.append([p[a], p[c], p[d]])
    return np.asarray(tris, dtype=np.float64)


def wigley_half_breadth(x: np.ndarray, z: np.ndarray, L: float, B: float,
                        T: float) -> np.ndarray:
    return (B / 2.0) * (1.0 - (2.0 * x / L) ** 2) * (1.0 - (z / T) ** 2)


def wigley_triangles(L: float = 10.0, B: float = 1.0, T: float = 0.625,
                     n_u: int = 40, n_v: int = 12,
                     mixed_winding: bool = False,
                     omit_bottom: bool = False,
                     rng_seed: int = 0) -> np.ndarray:
    """Triangulated closed Wigley hull (both sides + flat bottom)."""
    xs = np.linspace(-L / 2.0, L / 2.0, n_u + 1)
    zs = np.linspace(0.0, T, n_v + 1)
    grid = np.zeros(((n_u + 1) * (n_v + 1), 3))
    idx = lambda i, j: i * (n_v + 1) + j
    for i, x in enumerate(xs):
        for j, z in enumerate(zs):
            y = float(wigley_half_breadth(np.asarray(x), np.asarray(z), L, B, T))
            grid[idx(i, j)] = (x, y, z)
    tris: List[List[Sequence[float]]] = []

    def quad(a: int, b: int, c: int, d: int) -> None:
        tris.append([grid[a], grid[b], grid[c]])
        tris.append([grid[a], grid[c], grid[d]])

    for i in range(n_u):
        for j in range(n_v):
            quad(idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1))  # +y sheet
            # -y sheet (mirror) uses the *other* diagonal: at the stem and along the
            # ridge the two sheets meet on the symmetry plane, so a shared diagonal
            # would create a non-manifold edge with four incident triangles.
            m = lambda k: (grid[k][0], -grid[k][1], grid[k][2])
            tris.append([m(idx(i + 1, j)), m(idx(i + 1, j + 1)), m(idx(i, j + 1))])
            tris.append([m(idx(i + 1, j)), m(idx(i, j + 1)), m(idx(i, j))])
    if not omit_bottom:
        for i in range(n_u):
            a = grid[idx(i, 0)]
            b = grid[idx(i + 1, 0)]
            c = grid[idx(i + 1, 0)].copy()
            d = grid[idx(i, 0)].copy()
            c[1] = -c[1]
            d[1] = -d[1]
            tris.append([a, c, b])
            tris.append([a, d, c])
    tri = np.asarray(tris, dtype=np.float64)
    # the flat bottom collapses to a single point at both tips: drop the resulting
    # zero-area triangles instead of shipping a surface that trips the degeneracy gate
    if tri.size:
        ar = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]),
                                  axis=1)
        tri = tri[ar > 1e-14 * max(ar.max(), 1e-30)]
    if mixed_winding:
        rng = np.random.default_rng(rng_seed)
        flips = rng.random(tri.shape[0]) < 0.4
        tri[flips] = tri[flips][:, ::-1, :]
    return tri


# ---------------------------------------------------------------------------
# surface writers
# ---------------------------------------------------------------------------
def write_stl_binary(path: str, tri: np.ndarray, header: str = "validation-gate") -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii")[:80].ljust(80, b" "))
        fh.write(struct.pack("<I", int(tri.shape[0])))
        for t in tri:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            norm = np.linalg.norm(n)
            n = n / norm if norm > 0 else np.zeros(3)
            fh.write(struct.pack("<3f", *n))
            for v in t:
                fh.write(struct.pack("<3f", *v))
            fh.write(struct.pack("<H", 0))
    return path


def write_obj(path: str, tri: np.ndarray, merge: bool = True) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines: List[str] = ["# generated fixture"]
    if merge:
        flat = tri.reshape(-1, 3)
        key = {}
        ids = []
        for p in flat:
            k = tuple(np.round(p, 12))
            if k not in key:
                key[k] = len(key) + 1
                lines.append(f"v {p[0]:.12g} {p[1]:.12g} {p[2]:.12g}")
            ids.append(key[k])
        for f in range(tri.shape[0]):
            a, b, c = ids[3 * f:3 * f + 3]
            lines.append(f"f {a} {b} {c}")
    else:
        for t in tri:
            for p in t:
                lines.append(f"v {p[0]:.12g} {p[1]:.12g} {p[2]:.12g}")
        n = tri.shape[0]
        for f in range(n):
            a, b, c = 3 * f + 1, 3 * f + 2, 3 * f + 3
            lines.append(f"f {a} {b} {c}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def write_truncated_stl(path: str, n_claim: int = 500, bytes_kept: int = 200) -> str:
    """Binary STL whose header claims more triangles than the payload holds."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"truncated fixture".ljust(80, b" "))
        fh.write(struct.pack("<I", n_claim))
        fh.write(b"\x00" * bytes_kept)
    return path


# ---------------------------------------------------------------------------
# volume meshes
# ---------------------------------------------------------------------------
def structured_points(nx: int, ny: int, nz: int,
                      dx: float = 1.0, dy: float = 1.0, dz: float = 1.0,
                      origin: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> np.ndarray:
    xs = origin[0] + np.arange(nx + 1) * dx
    ys = origin[1] + np.arange(ny + 1) * dy
    zs = origin[2] + np.arange(nz + 1) * dz
    return np.asarray([[x, y, z] for z in zs for y in ys for x in xs], dtype=np.float64)


def hex_cells(nx: int, ny: int, nz: int) -> List[Tuple[List[int], str]]:
    cells: List[Tuple[List[int], str]] = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                n0 = i + (nx + 1) * (j + (ny + 1) * k)
                n1 = n0 + 1
                n2 = n0 + 1 + (nx + 1)
                n3 = n0 + (nx + 1)
                n4, n5, n6, n7 = (n0 + (nx + 1) * (ny + 1), n1 + (nx + 1) * (ny + 1),
                                  n2 + (nx + 1) * (ny + 1), n3 + (nx + 1) * (ny + 1))
                cells.append(([n0, n1, n2, n3, n4, n5, n6, n7], "hexahedron"))
    return cells


def hex_mesh(nx: int = 4, ny: int = 3, nz: int = 2, dx: float = 1.0, dy: float = 1.0,
             dz: float = 1.0, shear: float = 0.0, invert_cell: Optional[int] = None,
             origin: Tuple[float, float, float] = (0.0, 0.0, 0.0)
             ) -> Tuple[np.ndarray, List[Tuple[List[int], str]]]:
    """Structured hex mesh with optional shear (top layer) and node-swap inversion."""
    pts = structured_points(nx, ny, nz, dx, dy, dz, origin)
    if shear:
        # displace every node of the top z-layer in +x: creates parallelogram
        # side faces whose corner angles become progressively sharper
        npts_y = ny + 1
        top_layer = (nz) * (nx + 1) * npts_y
        pts[top_layer:, 0] += shear * dx
    cells = hex_cells(nx, ny, nz)
    if invert_cell is not None:
        nodes, kind = cells[invert_cell]
        # swap the body diagonal: this reverses the cell orientation (negative
        # volume) while keeping the node set, i.e. a genuine inverted element
        perm = [6, 1, 2, 5, 4, 3, 0, 7] if kind == "hexahedron" else [3, 1, 2, 0]
        cells[invert_cell] = ([nodes[i] for i in perm], kind)
    return pts, cells


def write_vtk_ascii(path: str, points: np.ndarray,
                    cells: Sequence[Tuple[Sequence[int], str]]) -> str:
    type_id = {"tetra": 10, "hexahedron": 12, "wedge": 13}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = ["# vtk DataFile Version 3.0", "geometry validation fixture", "ASCII",
             "DATASET UNSTRUCTURED_GRID", f"POINTS {len(points)} double"]
    for p in points:
        lines.append(f"{p[0]:.12g} {p[1]:.12g} {p[2]:.12g}")
    lines.append(f"CELLS {len(cells)} {sum(len(c[0]) + 1 for c in cells)}")
    for nodes, _ in cells:
        lines.append(f"{len(nodes)} " + " ".join(str(int(n)) for n in nodes))
    lines.append(f"CELL_TYPES {len(cells)}")
    lines.extend(str(type_id[kind]) for _, kind in cells)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def write_openfoam_polymesh(directory: str, nx: int = 3, ny: int = 2, nz: int = 1,
                            dx: float = 1.0, dy: float = 1.0, dz: float = 1.0) -> str:
    """Write a minimal valid OpenFOAM polyMesh (points/faces/owner/neighbour)."""
    os.makedirs(directory, exist_ok=True)
    pts = structured_points(nx, ny, nz, dx, dy, dz)
    nid = lambda i, j, k: i + (nx + 1) * (j + (ny + 1) * k)
    seen: Dict[Tuple[int, ...], int] = {}
    faces: List[List[int]] = []
    owner: List[int] = []
    neighbour: List[int] = []
    internal: List[Tuple[int, int, int]] = []
    boundary: List[Tuple[int, int]] = []

    def cell_id(i, j, k):
        return i + nx * (j + ny * k)

    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                c = cell_id(i, j, k)
                loops = [
                    ([nid(i, j, k), nid(i, j, k + 1), nid(i, j + 1, k + 1), nid(i, j + 1, k)], -1, 0, 0),
                    ([nid(i + 1, j, k), nid(i + 1, j + 1, k), nid(i + 1, j + 1, k + 1), nid(i + 1, j, k + 1)], +1, 0, 0),
                    ([nid(i, j, k), nid(i + 1, j, k), nid(i + 1, j, k + 1), nid(i, j, k + 1)], 0, -1, 0),
                    ([nid(i, j + 1, k), nid(i, j + 1, k + 1), nid(i + 1, j + 1, k + 1), nid(i + 1, j + 1, k)], 0, +1, 0),
                    ([nid(i, j, k), nid(i, j + 1, k), nid(i + 1, j + 1, k), nid(i + 1, j, k)], 0, 0, -1),
                    ([nid(i, j, k + 1), nid(i + 1, j, k + 1), nid(i + 1, j + 1, k + 1), nid(i, j + 1, k + 1)], 0, 0, +1),
                ]
                for loop, di, dj, dk in loops:
                    key = tuple(sorted(loop))
                    if key in seen:
                        f = seen[key]
                        if neighbour[f] != -1:
                            raise RuntimeError("a polyMesh face is shared by >2 cells")
                        neighbour[f] = c
                        internal.append((f, owner[f], c))
                    else:
                        seen[key] = len(faces)
                        faces.append(loop)
                        owner.append(c)
                        neighbour.append(-1)
    # a face first seen as a wall may later be shared by the next cell, so the
    # boundary set must be resolved *after* the sweep
    boundary = [(f, owner[f]) for f in range(len(faces)) if neighbour[f] == -1]
    order = [f for f, _, _ in internal] + [f for f, _ in boundary]
    remap = {old: new for new, old in enumerate(order)}
    n_int = len(internal)
    new_faces = [faces[f] for f in order]
    new_owner = [owner[f] for f in order]
    nr = [-1] * len(order)
    for f, _, nb in internal:
        nr[remap[f]] = nb
    new_neighbour = [nr[i] for i in range(n_int)]
    assert all(x >= 0 for x in new_neighbour), "internal faces must carry a neighbour"

    def header(cls: str, note: str) -> str:
        return ("FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
                f"    class       {cls};\n    location    \"constant/polyMesh\";\n"
                f"    object      {note};\n}}\n")

    with open(os.path.join(directory, "points"), "w", encoding="utf-8") as fh:
        fh.write(header("vectorField", "points") + f"\n{len(pts)}\n(\n")
        for p in pts:
            fh.write(f"({p[0]:.12g} {p[1]:.12g} {p[2]:.12g})\n")
        fh.write(")\n")
    with open(os.path.join(directory, "faces"), "w", encoding="utf-8") as fh:
        fh.write(header("faceList", "faces") + f"\n{len(new_faces)}\n(\n")
        for loop in new_faces:
            fh.write(f"{len(loop)}(" + " ".join(str(int(n)) for n in loop) + ")\n")
        fh.write(")\n")
    with open(os.path.join(directory, "owner"), "w", encoding="utf-8") as fh:
        fh.write(header("labelList", "owner") + f"\n{len(new_owner)}\n(\n")
        for o in new_owner:
            fh.write(f"{o}\n")
        fh.write(")\n")
    with open(os.path.join(directory, "neighbour"), "w", encoding="utf-8") as fh:
        fh.write(header("labelList", "neighbour") + f"\n{len(new_neighbour)}\n(\n")
        for nb in new_neighbour:
            fh.write(f"{nb}\n")
        fh.write(")\n")
    return directory


def write_yplus_log(path: str, patches: Dict[str, Tuple[float, float, float]]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = ["yPlus yPlus write:"]
    for name, (lo, hi, avg) in patches.items():
        lines.append(f"    patch {name} y+ : min = {lo:.6g}, max = {hi:.6g}, "
                     f"average = {avg:.6g}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def write_checkmesh_log(path: str, n_cells: int, n_faces: int, n_points: int,
                        max_skewness: float, max_aspect_ratio: float,
                        ok: bool = True) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = [
        "Create time",
        "",
        f"Mesh stats\n    points:           {n_points}\n    faces:            {n_faces}\n"
        f"    internal faces:   {n_faces - 12}\n    cells:            {n_cells}\n",
        "Checking geometry...",
        "    Max aspect ratio = " + f"{max_aspect_ratio:.6g}" + " OK.",
        "    Mesh non-orthogonality Max: 12.3 average: 3.1",
        "    Max skewness = " + f"{max_skewness:.6g}" + " OK.",
        "    Min volume = 0.98. Max volume = 1.02.",
        "Mesh OK." if ok else "Failed 2 mesh checks.",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path