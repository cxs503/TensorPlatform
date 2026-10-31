"""Panel meshes for the icing model: superstructure/hull surface discretisation.

The platform ``geometry`` capability (:8001 ``geometry/generate``) emits hull
and superstructure *block* definitions; here those blocks are discretised into
flat quad panels so that the surface heat balance can be evaluated per panel and
mass/centroid contributions accumulated facet-by-facet (as required by the node
definition).  The mesh is deliberately independent of any CAD kernel: a block is
a box (origin + size) or an explicitly given planar quad.

Every panel carries

* ``area`` [m^2], outward unit ``normal``, ``centroid`` [m] in the ship frame
  (x aft->fore, y port, z up from baseline),
* ``zone`` — surface class (windward / side / leeward / horizontal_up / down),
* ``char_length`` — characteristic length for the convective correlation
  (panel diagonal, clipped to a physically sensible range),
* ``height`` — centroid height above the baseline (used for KG bookkeeping).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class Panel:
    name: str
    zone: str
    area: float
    centroid: Vec3
    normal: Vec3
    char_length: float
    face: str = ""
    block: str = ""

    @property
    def height(self) -> float:
        return self.centroid[2]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "zone": self.zone,
            "area_m2": self.area,
            "centroid_m": list(self.centroid),
            "normal": list(self.normal),
            "char_length_m": self.char_length,
            "face": self.face,
            "block": self.block,
        }


@dataclass(frozen=True)
class Block:
    """Axis-aligned superstructure block (or deck slab)."""

    name: str
    origin: Vec3
    size: Vec3
    faces: Sequence[str] = ("front", "back", "port", "starboard", "top", "bottom")

    @property
    def top_z(self) -> float:
        return self.origin[2] + self.size[2]

    @property
    def footprint(self) -> float:
        return abs(self.size[0] * self.size[1])


_FACE_NORMALS = {
    "front": (1.0, 0.0, 0.0),      # +x (bow)
    "back": (-1.0, 0.0, 0.0),      # -x (stern)
    "port": (0.0, 1.0, 0.0),       # +y
    "starboard": (0.0, -1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
}


def classify_zone(normal: Vec3, wind_dir: Vec3) -> str:
    """Surface class from the outward normal and the wind direction."""
    nx, ny, nz = normal
    nz_abs = abs(nz)
    if nz_abs > 0.85:
        return "horizontal_up" if nz > 0 else "horizontal_down"
    wx, wy, _ = wind_dir
    wmag = math.hypot(wx, wy) or 1.0
    # windward = the face the wind blows *onto* (outward normal into the wind)
    proj = -(nx * wx + ny * wy) / (math.hypot(nx, ny) * wmag)
    if proj > 0.5:
        return "windward"
    if proj < -0.3:
        return "leeward"
    return "side"


def _face_rectangle(block: Block, face: str) -> tuple[Vec3, Tuple[Vec3, Vec3]]:
    """Return (corner, (u_axis*length, v_axis*width)) of a block face."""
    x0, y0, z0 = block.origin
    lx, ly, lz = block.size
    if face == "front":       # +x, spans y,z
        return (x0 + lx, y0, z0), ((0.0, ly, 0.0), (0.0, 0.0, lz))
    if face == "back":        # -x
        return (x0, y0, z0), ((0.0, ly, 0.0), (0.0, 0.0, lz))
    if face == "port":        # +y
        return (x0, y0 + ly, z0), ((lx, 0.0, 0.0), (0.0, 0.0, lz))
    if face == "starboard":   # -y
        return (x0, y0, z0), ((lx, 0.0, 0.0), (0.0, 0.0, lz))
    if face == "top":         # +z
        return (x0, y0, z0 + lz), ((lx, 0.0, 0.0), (0.0, ly, 0.0))
    if face == "bottom":      # -z
        return (x0, y0, z0), ((lx, 0.0, 0.0), (0.0, ly, 0.0))
    raise ValueError(f"unknown block face: {face}")


def _subdivide(length: float, width: float, target: float) -> tuple[int, int]:
    nu = max(1, int(math.ceil(length / max(target, 1e-6))))
    nv = max(1, int(math.ceil(width / max(target, 1e-6))))
    return nu, nv


def block_panels(
    block: Block,
    panel_size: float = 0.75,
    wind_dir: Vec3 = (0.0, -1.0, 0.0),
) -> List[Panel]:
    """Discretise the requested faces of a block into flat quad panels."""
    panels: List[Panel] = []
    for face in block.faces:
        corner, (u_vec, v_vec) = _face_rectangle(block, face)
        lu = math.sqrt(sum(c * c for c in u_vec))
        lv = math.sqrt(sum(c * c for c in v_vec))
        if lu <= 0.0 or lv <= 0.0:
            continue
        nu, nv = _subdivide(lu, lv, panel_size)
        du = [c / nu for c in u_vec]
        dv = [c / nv for c in v_vec]
        normal = _FACE_NORMALS[face]
        zone = classify_zone(normal, wind_dir)
        panel_area = (lu * lv) / (nu * nv)
        # Characteristic length for the flat-plate correlations: the *face*
        # extent (mean of the two face dimensions), because the convective
        # boundary layer develops over the whole face, not over one sub-panel.
        # Clipped to 0.05..8 m, the validity range of the correlations used.
        char_len = min(max(0.5 * (lu + lv), 0.05), 8.0)
        for i in range(nu):
            for j in range(nv):
                cx = corner[0] + du[0] * (i + 0.5) + dv[0] * (j + 0.5)
                cy = corner[1] + du[1] * (i + 0.5) + dv[1] * (j + 0.5)
                cz = corner[2] + du[2] * (i + 0.5) + dv[2] * (j + 0.5)
                panels.append(
                    Panel(
                        name=f"{block.name}:{face}[{i},{j}]",
                        zone=zone,
                        area=panel_area,
                        centroid=(cx, cy, cz),
                        normal=normal,
                        char_length=char_len,
                        face=face,
                        block=block.name,
                    )
                )
    return panels


def build_superstructure_mesh(
    blocks: Iterable[Block],
    deck_area: float = 0.0,
    deck_height: float = 0.0,
    panel_size: float = 0.75,
    wind_dir: Vec3 = (0.0, -1.0, 0.0),
) -> List[Panel]:
    """Assemble the full exposed-surface mesh.

    ``deck_area`` is the free weather-deck area *not* occupied by superstructure
    blocks; it is added as a single up-facing panel at ``deck_height`` so that
    the mass/centroid integration covers the whole icing-relevant envelope.
    """
    panels: List[Panel] = []
    occupied = 0.0
    footprint = 0.0
    for block in blocks:
        panels.extend(block_panels(block, panel_size=panel_size, wind_dir=wind_dir))
        occupied += block.footprint
        footprint += block.footprint
    net_deck = max(deck_area - occupied, 0.0)
    if net_deck > 0.0:
        panels.append(
            Panel(
                name="weather_deck",
                zone="horizontal_up",
                area=net_deck,
                centroid=(0.0, 0.0, deck_height),
                normal=(0.0, 0.0, 1.0),
                char_length=min(max(math.sqrt(net_deck), 0.05), 8.0),
                face="top",
                block="hull",
            )
        )
    return panels


def panel_area_summary(panels: Sequence[Panel]) -> dict:
    total = sum(p.area for p in panels)
    by_zone: dict[str, float] = {}
    for p in panels:
        by_zone[p.zone] = by_zone.get(p.zone, 0.0) + p.area
    return {"total_area_m2": total, "by_zone": by_zone, "n_panels": len(panels)}