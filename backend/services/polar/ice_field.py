"""2-D ice-field discretisation for polar route optimisation.

The ice field is discretised on a regular Cartesian grid (equal to a lat/lon
patch for domains of a few hundred km, where the metric distortion is small).
Every grid cell carries the four quantities requested by the campaign node:

* ``h[i, j]``      level/brash ice thickness            [m]
* ``sigma[i, j]``  ice compressive strength             [MPa]
* ``u,v_current``  surface current velocity             [m/s]
* ``u,v_wind``     10 m wind velocity                    [m/s]

A cell can additionally be flagged as land / immovable fast ice, in which case
it is not navigable.

Data provenance is explicit: :meth:`IceField.generate_scenario` builds a
*reproducible synthetic field* from documented physical gradients (ice edge to
coast thickening, a heavy-ice tongue, deterministic lead pattern).  It is **not**
observed satellite ice data; :meth:`IceField.from_csv` / :meth:`from_dict` load
real fields.  ``IceField.source`` records which one was used so reports cannot
silently mix them.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .constants import NM_M

#: Guard rails: thickness/strength outside these windows is almost certainly a
#: unit error, and the empirical resistance formulas are invalid there anyway.
ICE_LIMITS = {
    "h_m": (0.0, 6.0),
    "sigma_mpa": (0.05, 8.0),
    "current_ms": (-3.0, 3.0),
    "wind_ms": (-40.0, 40.0),
}


@dataclass
class IceField:
    """A gridded ice field.  Arrays are indexed ``[iy, ix]``."""

    h: np.ndarray
    sigma: np.ndarray
    u_current: np.ndarray
    v_current: np.ndarray
    u_wind: np.ndarray
    v_wind: np.ndarray
    dx_km: float = 3.0
    dy_km: float = 3.0
    x0_km: float = 0.0
    y0_km: float = 0.0
    land: Optional[np.ndarray] = None
    name: str = "ice_field"
    source: str = "synthetic"

    # -- construction --------------------------------------------------------
    def __post_init__(self) -> None:
        self.h = np.asarray(self.h, dtype=float)
        self.sigma = np.asarray(self.sigma, dtype=float)
        self.u_current = np.asarray(self.u_current, dtype=float)
        self.v_current = np.asarray(self.v_current, dtype=float)
        self.u_wind = np.asarray(self.u_wind, dtype=float)
        self.v_wind = np.asarray(self.v_wind, dtype=float)
        if self.land is not None:
            self.land = np.asarray(self.land, dtype=bool)

    @property
    def shape(self) -> Tuple[int, int]:
        return self.h.shape

    @property
    def nx(self) -> int:
        return self.h.shape[1]

    @property
    def ny(self) -> int:
        return self.h.shape[0]

    @property
    def extent_km(self) -> Tuple[float, float, float, float]:
        return (
            self.x0_km,
            self.x0_km + (self.nx - 1) * self.dx_km,
            self.y0_km,
            self.y0_km + (self.ny - 1) * self.dy_km,
        )

    # -- validation ----------------------------------------------------------
    def validate(self) -> Dict[str, Any]:
        """Structural + physical sanity check.  Raises ``ValueError`` on error."""
        problems: List[str] = []
        arrays = {
            "h": self.h,
            "sigma": self.sigma,
            "u_current": self.u_current,
            "v_current": self.v_current,
            "u_wind": self.u_wind,
            "v_wind": self.v_wind,
        }
        for name, arr in arrays.items():
            if arr.ndim != 2:
                problems.append(f"{name}: expected 2-D array, got shape {arr.shape}")
            if not np.all(np.isfinite(arr)):
                problems.append(f"{name}: contains non-finite values")
        if problems:
            raise ValueError("ice field validation failed: " + "; ".join(problems))

        shapes = {arr.shape for arr in arrays.values()}
        if len(shapes) != 1:
            raise ValueError(f"ice field arrays have inconsistent shapes: {shapes}")
        if self.land is not None and self.land.shape != self.h.shape:
            raise ValueError("land mask shape differs from h")

        for name, arr in arrays.items():
            lim_key = {
                "h": "h_m",
                "sigma": "sigma_mpa",
                "u_current": "current_ms",
                "v_current": "current_ms",
                "u_wind": "wind_ms",
                "v_wind": "wind_ms",
            }[name]
            lo, hi = ICE_LIMITS[lim_key]
            amin, amax = float(np.min(arr)), float(np.max(arr))
            if amin < lo - 1e-9 or amax > hi + 1e-9:
                problems.append(
                    f"{name}: range [{amin:.4g}, {amax:.4g}] outside physical window {ICE_LIMITS[lim_key]}"
                )
        if self.dx_km <= 0 or self.dy_km <= 0:
            problems.append("grid spacing must be positive")
        if problems:
            raise ValueError("ice field validation failed: " + "; ".join(problems))

        return self.summary()

    def summary(self) -> Dict[str, Any]:
        """Aggregate statistics used for reporting and API responses."""
        h = self.h
        ice = h > 0.02
        land = self.land if self.land is not None else np.zeros_like(h, dtype=bool)
        water = ~land
        navigable_ice = ice & water
        h_nav = h[navigable_ice] if np.any(navigable_ice) else np.array([0.0])
        return {
            "grid": {"nx": self.nx, "ny": self.ny, "dx_km": self.dx_km, "dy_km": self.dy_km},
            "extent_km": [round(v, 3) for v in self.extent_km],
            "h_m": {
                "mean_ice": round(float(np.mean(h_nav)), 4),
                "max": round(float(np.max(h)), 4),
                "p95": round(float(np.percentile(h_nav, 95)), 4),
            },
            "sigma_mpa": {
                "mean_ice": round(float(np.mean(self.sigma[navigable_ice])) if np.any(navigable_ice) else 0.0, 4),
                "max": round(float(np.max(self.sigma)), 4),
            },
            "current_ms": {
                "mean_magnitude": round(
                    float(np.mean(np.hypot(self.u_current, self.v_current)[water])), 4
                ),
                "max_magnitude": round(
                    float(np.max(np.hypot(self.u_current, self.v_current))), 4
                ),
            },
            "wind_ms": {
                "mean_magnitude": round(
                    float(np.mean(np.hypot(self.u_wind, self.v_wind)[water])), 4
                ),
                "max_magnitude": round(
                    float(np.max(np.hypot(self.u_wind, self.v_wind))), 4
                ),
            },
            "ice_covered_fraction": round(float(np.mean(navigable_ice)), 4),
            "land_fraction": round(float(np.mean(land)), 4),
            "name": self.name,
            "source": self.source,
        }

    # -- sampling ------------------------------------------------------------
    def _bilinear(self, arr: np.ndarray, x_km: float, y_km: float) -> float:
        """Bilinear interpolation of ``arr`` at world coordinate (x, y) [km]."""
        fx = (x_km - self.x0_km) / self.dx_km
        fy = (y_km - self.y0_km) / self.dy_km
        fx = min(max(fx, 0.0), self.nx - 1.0)
        fy = min(max(fy, 0.0), self.ny - 1.0)
        i0 = int(math.floor(fx))
        j0 = int(math.floor(fy))
        i1 = min(i0 + 1, self.nx - 1)
        j1 = min(j0 + 1, self.ny - 1)
        tx = fx - i0
        ty = fy - j0
        v00 = arr[j0, i0]
        v01 = arr[j0, i1]
        v10 = arr[j1, i0]
        v11 = arr[j1, i1]
        return float(
            v00 * (1 - tx) * (1 - ty)
            + v01 * tx * (1 - ty)
            + v10 * (1 - tx) * ty
            + v11 * tx * ty
        )

    def sample(self, x_km: float, y_km: float) -> Dict[str, float]:
        """Sample the field at a world coordinate (bilinear)."""
        land = False
        if self.land is not None:
            fx = int(round((x_km - self.x0_km) / self.dx_km))
            fy = int(round((y_km - self.y0_km) / self.dy_km))
            fx = min(max(fx, 0), self.nx - 1)
            fy = min(max(fy, 0), self.ny - 1)
            land = bool(self.land[fy, fx])
        return {
            "h_m": self._bilinear(self.h, x_km, y_km),
            "sigma_mpa": self._bilinear(self.sigma, x_km, y_km),
            "u_current_ms": self._bilinear(self.u_current, x_km, y_km),
            "v_current_ms": self._bilinear(self.v_current, x_km, y_km),
            "u_wind_ms": self._bilinear(self.u_wind, x_km, y_km),
            "v_wind_ms": self._bilinear(self.v_wind, x_km, y_km),
            "land": land,
        }

    def cell_center(self, iy: int, ix: int) -> Tuple[float, float]:
        return (self.x0_km + ix * self.dx_km, self.y0_km + iy * self.dy_km)

    def world_to_index(self, x_km: float, y_km: float) -> Tuple[int, int]:
        ix = int(round((x_km - self.x0_km) / self.dx_km))
        iy = int(round((y_km - self.y0_km) / self.dy_km))
        return (
            min(max(iy, 0), self.ny - 1),
            min(max(ix, 0), self.nx - 1),
        )

    # -- serialisation -------------------------------------------------------
    def to_dict(self, include_grid: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "dx_km": self.dx_km,
            "dy_km": self.dy_km,
            "x0_km": self.x0_km,
            "y0_km": self.y0_km,
            "summary": self.summary(),
        }
        if include_grid:
            out.update(
                {
                    "h": self.h.tolist(),
                    "sigma": self.sigma.tolist(),
                    "u_current": self.u_current.tolist(),
                    "v_current": self.v_current.tolist(),
                    "u_wind": self.u_wind.tolist(),
                    "v_wind": self.v_wind.tolist(),
                    "land": None if self.land is None else self.land.tolist(),
                }
            )
        return out

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "IceField":
        """Build a field from a JSON payload (see :meth:`to_dict`)."""
        def _arr(key: str, value) -> np.ndarray:
            if value is None:
                raise ValueError(f"ice field payload missing '{key}'")
            return np.asarray(value, dtype=float)

        field = cls(
            h=_arr("h", payload.get("h")),
            sigma=_arr("sigma", payload.get("sigma")),
            u_current=_arr("u_current", payload.get("u_current")),
            v_current=_arr("v_current", payload.get("v_current")),
            u_wind=_arr("u_wind", payload.get("u_wind")),
            v_wind=_arr("v_wind", payload.get("v_wind")),
            dx_km=float(payload.get("dx_km", 3.0)),
            dy_km=float(payload.get("dy_km", payload.get("dx_km", 3.0))),
            x0_km=float(payload.get("x0_km", 0.0)),
            y0_km=float(payload.get("y0_km", 0.0)),
            land=None if payload.get("land") is None else np.asarray(payload["land"], dtype=bool),
            name=str(payload.get("name", "ice_field")),
            source=str(payload.get("source", "uploaded")),
        )
        field.validate()
        return field

    @classmethod
    def from_csv(cls, path: str | Path, dx_km: float = 3.0, dy_km: float = 3.0,
                 name: Optional[str] = None) -> "IceField":
        """Load a field from a tidy CSV with columns ``ix, iy, x_km, y_km, h,
        sigma, u_current, v_current, u_wind, v_wind[, land]``.

        Missing values in the CSV are rejected (the physics must not see NaNs);
        a missing row is filled with open water (h = 0, sigma = 0.1 MPa).
        """
        path = Path(path)
        rows: List[Dict[str, str]] = []
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        if not rows:
            raise ValueError(f"empty ice-field CSV: {path}")
        nx = max(int(r["ix"]) for r in rows) + 1
        ny = max(int(r["iy"]) for r in rows) + 1
        shape = (ny, nx)
        h = np.zeros(shape)
        sigma = np.full(shape, 0.1)
        uc = np.zeros(shape)
        vc = np.zeros(shape)
        uw = np.zeros(shape)
        vw = np.zeros(shape)
        land = np.zeros(shape, dtype=bool)
        for r in rows:
            iy, ix = int(r["iy"]), int(r["ix"])
            h[iy, ix] = float(r["h"])
            sigma[iy, ix] = float(r.get("sigma", 0.1))
            uc[iy, ix] = float(r.get("u_current", 0.0))
            vc[iy, ix] = float(r.get("v_current", 0.0))
            uw[iy, ix] = float(r.get("u_wind", 0.0))
            vw[iy, ix] = float(r.get("v_wind", 0.0))
            land[iy, ix] = str(r.get("land", "0")).strip().lower() in {"1", "true", "yes"}
        field = cls(
            h=h, sigma=sigma, u_current=uc, v_current=vc, u_wind=uw, v_wind=vw,
            dx_km=dx_km, dy_km=dy_km, land=land,
            name=name or path.stem, source=f"csv:{path.name}",
        )
        field.validate()
        return field

    # -- generators ----------------------------------------------------------
    @classmethod
    def uniform(
        cls,
        h_m: float,
        sigma_mpa: float,
        nx: int = 41,
        ny: int = 41,
        dx_km: float = 3.0,
        current_ms: float = 0.0,
        wind_ms: float = 0.0,
        name: Optional[str] = None,
    ) -> "IceField":
        """Uniform (homogeneous) ice field -- used for verification cases."""
        shape = (ny, nx)
        return cls(
            h=np.full(shape, float(h_m)),
            sigma=np.full(shape, float(sigma_mpa)),
            u_current=np.full(shape, float(current_ms)),
            v_current=np.zeros(shape),
            u_wind=np.full(shape, float(wind_ms)),
            v_wind=np.zeros(shape),
            dx_km=dx_km,
            dy_km=dx_km,
            land=np.zeros(shape, dtype=bool),
            name=name or f"uniform_h{h_m:.2f}",
            source="synthetic:uniform",
        )

    @classmethod
    def generate_scenario(
        cls,
        kind: str = "nsr_corridor",
        nx: int = 141,
        ny: int = 101,
        dx_km: float = 3.0,
        dy_km: float = 3.0,
    ) -> "IceField":
        """Generate a reproducible synthetic polar corridor field.

        ``nsr_corridor`` (default) models a transit corridor of 420 km x 300 km.
        From south (y = 0, ice edge) to north (y = 260 km, coast) the level-ice
        thickness grows as ``0.15 + 1.45 (y/260)^1.2`` m, a *heavy-ice tongue*
        (Gaussian, +0.30 m) sits at y = 178 km, thick ice and land / fast ice take
        over for y >= 260 km, and two **shore leads** are superposed as corridors
        where the thickness drops to 30 % of the ambient field:

        * a meandering east-west shore lead along the coast:
          (10,206) - (120,214) - (240,200) - (360,212) - (400,206)
        * a lead crossing the heavy-ice tongue and joining the shore lead at
          (352,215): (352,215) - (348,178) - (344,120) - (340,60)

        plus a polynya patch at (330, 70) km.  Leads and polynyas are the very
        features gridded route optimisation can exploit, which is why the field
        carries them: it makes the comparison against a latitude-band rule
        meaningful instead of a strawman.

        Ice strength follows h via ``sigma = 0.9 + 0.55 h / 1.6`` MPa, a
        deterministic small-scale lead texture is superposed, and a uniform
        0.12 m/s eastward current plus an 8 m/s north-westerly wind is applied.
        Everything is analytic -- no random numbers -- so the scenario is
        bit-reproducible; ``source`` is tagged accordingly.  This is a physically
        structured **synthetic** field, not observed ice-chart data.
        """
        if kind != "nsr_corridor":
            raise ValueError(f"unknown scenario kind '{kind}' (available: nsr_corridor)")

        xs = np.arange(nx) * dx_km
        ys = np.arange(ny) * dy_km
        X, Y = np.meshgrid(xs, ys)

        y_coast = 260.0
        frac = np.clip(Y / y_coast, 0.0, 1.0)
        h = 0.15 + 1.45 * frac ** 1.2
        # heavy-ice tongue the ship has to cross or avoid
        h = h + 0.30 * np.exp(-(((Y - 178.0) / 16.0) ** 2))
        # deterministic small-scale lead texture
        texture = 0.10 * np.exp(
            -((np.sin(2 * np.pi * X / 90.0 + 0.6 * np.sin(Y / 40.0))) ** 2) / 0.02
        )
        h = np.clip(h - texture, 0.05, None)

        # lead corridors: thickness reduced to 30 % inside half_width of a polyline
        lead_polylines = [
            [(10.0, 206.0), (120.0, 214.0), (240.0, 200.0), (360.0, 212.0), (400.0, 206.0)],
            [(352.0, 215.0), (348.0, 178.0), (344.0, 120.0), (340.0, 60.0)],
        ]
        lead_mask = _distance_to_polylines(X, Y, lead_polylines) < 8.0
        h = np.where(lead_mask, 0.30 * h, h)
        # polynya patch
        h = np.where(np.hypot(X - 330.0, Y - 70.0) < 20.0, 0.08, h)
        # fast ice / land at the coast
        h = np.where(Y >= y_coast, 2.0, h)

        sigma = 0.9 + 0.55 * np.clip(h / 1.6, 0.0, 1.0)
        sigma = sigma * (1.0 + 0.08 * np.sin(2 * np.pi * X / 210.0))
        sigma = np.where(Y >= y_coast, 2.2, sigma)

        u_current = np.full((ny, nx), 0.12) + 0.04 * np.sin(Y / 55.0)
        v_current = np.full((ny, nx), 0.03 * np.cos(X / 70.0))
        u_wind = np.full((ny, nx), 6.0)      # wind FROM the north-west -> +x
        v_wind = np.full((ny, nx), -5.0)

        land = Y >= y_coast

        field = cls(
            h=h, sigma=sigma, u_current=u_current, v_current=v_current,
            u_wind=u_wind, v_wind=v_wind, dx_km=dx_km, dy_km=dy_km,
            land=land, name="nsr_corridor", source="synthetic:nsr_corridor",
        )
        field.validate()
        return field

    @staticmethod
    def scenario_lead_polylines(kind: str = "nsr_corridor") -> List[List[Tuple[float, float]]]:
        """Lead polylines of a scenario (used by the lead-aware manual baseline)."""
        if kind != "nsr_corridor":
            raise ValueError(f"unknown scenario kind '{kind}'")
        return [
            [(10.0, 206.0), (120.0, 214.0), (240.0, 200.0), (360.0, 212.0), (400.0, 206.0)],
            [(352.0, 215.0), (348.0, 178.0), (344.0, 120.0), (340.0, 60.0)],
        ]


def _distance_to_polylines(X: np.ndarray, Y: np.ndarray, polylines) -> np.ndarray:
    """Minimum distance from every grid point to a set of polylines [km]."""
    best = np.full(X.shape, np.inf)
    for pl in polylines:
        for (xa, ya), (xb, yb) in zip(pl[:-1], pl[1:]):
            vx, vy = xb - xa, yb - ya
            seg2 = vx * vx + vy * vy
            if seg2 <= 0:
                continue
            t = np.clip(((X - xa) * vx + (Y - ya) * vy) / seg2, 0.0, 1.0)
            dx = X - (xa + t * vx)
            dy = Y - (ya + t * vy)
            best = np.minimum(best, np.hypot(dx, dy))
    return best


def route_length_km(field: IceField, points: Sequence[Tuple[float, float]]) -> float:
    """Polyline length in km for a list of world points."""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def km_to_nm(km: float) -> float:
    return km * 1000.0 / NM_M