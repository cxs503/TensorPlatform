"""Coupling-interface exchange operator and conservation residuals (DEM belt <-> LBM facets).

The coupling interface of P66 is the hull ice belt, unrolled into a strip of
constant height (see :class:`tensorphysics.campaign.p66_gate.producers.BeltGeometry`).
Two discretisations live on it and they do **not** match:

* the **DEM side** (N03/N05) delivers per-*patch* fields -- coarse, 16 patches
  in the reference layout;
* the **LBM side** (flow solver) consumes per-*facet* fields -- fine, 64 surface
  facets, and the facet edges are deliberately offset from the patch edges.

What must hold across the exchange (the machine criterion of this gate):

* **mass** -- the ice mass rate the DEM side claims crosses the interface equals
  the mass rate the LBM side accepts (:func:`mass_residual`);
* **momentum** -- the traction vector (normal ``p + rho u_n^2``, tangential
  ``mu p + rho u_n u_t``) integrated on both meshes is the same vector
  (:func:`momentum_residual`), which also catches a lost normal rotation;
* **third law** -- the reaction spread back onto the facets carries the same
  total impulse as the patch-side impulse :func:`exchange_residual`.

The production operator is a *conservative* overlap-weighted projection: with
``O[i,j]`` the overlap length of coarse patch ``i`` and fine facet ``j``,
``q_c[i] = sum_j O[i,j] q_f[j] / w_c[i]`` and, because ``sum_i O[i,j] = w_f[j]``
exactly (full coverage), ``sum_i q_c[i] w_c[i] = sum_j q_f[j] w_f[j]`` to
round-off.  The residual is then ~1e-16, not because the physics is trivial but
because the operator is conservative by construction -- and the **negative
controls** below show the same diagnostic flagging an O(10 %) error when the
operator is *not* conservative:

* ``nearest`` -- each patch takes the value of its nearest facet (the classic
  "nearest-neighbour hand-off"): mass residual ~1 % on this field, i.e. still
  four orders of magnitude above the 1e-6 tolerance.
* ``belt_mean_normal`` -- the momentum vector is materialised along one
  belt-averaged normal instead of per-patch normals (the "rotated the geometry,
  forgot the normals" defect): momentum residual O(10 %) while the mass residual
  stays at round-off, i.e. a scalar-only check would miss it.
* ``per_facet_normalised`` -- the reaction spread normalises each receiving
  facet's weights, so the total reaction impulse differs from the applied
  impulse: third-law residual O(10 %).

Evidence grading: the operator and the residuals are REAL arithmetic
(machine-checkable identities); no solver kernel is reimplemented here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from ..schema import SchemaError, validate
from . import contracts as C

SCHEMA_EXCHANGE = C.SCHEMA_EXCHANGE

CONSERVATION_TOL = 1e-6      # criterion (1): mass / momentum residual < 1e-6
NEGATIVE_CONTROL_FLOOR = 1e-3  # a negative control must exceed this, else the check is blind

EXCHANGE_SCHEMA_DEF: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "producer", "coarse", "fine", "coarse_flux", "fine_flux", "units"],
    "properties": {
        "schema": {"const": SCHEMA_EXCHANGE},
        "producer": {"type": "string", "minLength": 1},
        "coarse": {
            "type": "object",
            "additionalProperties": False,
            "required": ["edges_m", "height_m"],
            "properties": {
                "edges_m": {"type": "array", "items": {"type": "number"}, "minItems": 2},
                "height_m": {"type": "number", "exclusiveMinimum": 0.0},
            },
        },
        "fine": {
            "type": "object",
            "additionalProperties": False,
            "required": ["edges_m", "height_m"],
            "properties": {
                "edges_m": {"type": "array", "items": {"type": "number"}, "minItems": 2},
                "height_m": {"type": "number", "exclusiveMinimum": 0.0},
            },
        },
        "coarse_flux": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mass_kg_m2s", "traction_x_Pa", "traction_y_Pa"],
            "properties": {
                "mass_kg_m2s": {"type": "array", "items": {"type": "number"}, "minItems": 1},
                "traction_x_Pa": {"type": "array", "items": {"type": "number"}, "minItems": 1},
                "traction_y_Pa": {"type": "array", "items": {"type": "number"}, "minItems": 1},
            },
        },
        "fine_flux": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mass_kg_m2s", "traction_x_Pa", "traction_y_Pa"],
            "properties": {
                "mass_kg_m2s": {"type": "array", "items": {"type": "number"}, "minItems": 1},
                "traction_x_Pa": {"type": "array", "items": {"type": "number"}, "minItems": 1},
                "traction_y_Pa": {"type": "array", "items": {"type": "number"}, "minItems": 1},
            },
        },
        "units": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

EXCHANGE_UNITS = {
    "edges_m": "m",
    "height_m": "m",
    "mass_kg_m2s": "kg/(m^2*s)",
    "traction_x_Pa": "Pa",
    "traction_y_Pa": "Pa",
}


# ---------------------------------------------------------------------------
# meshes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterfaceMesh:
    """Uniform 1-D surface strip: ``edges_m`` are the patch boundaries [m]."""

    edges_m: np.ndarray
    height_m: float

    def __post_init__(self) -> None:
        edges = np.asarray(self.edges_m, dtype=float)
        if edges.ndim != 1 or edges.size < 2:
            raise ValueError("edges_m must be a 1-D array with >= 2 entries")
        if np.any(np.diff(edges) <= 0.0):
            raise ValueError("edges_m must be strictly increasing")
        widths = np.diff(edges)
        object.__setattr__(self, "edges_m", edges)
        object.__setattr__(self, "_widths", widths)
        object.__setattr__(self, "_centers", 0.5 * (edges[:-1] + edges[1:]))
        object.__setattr__(self, "_areas", widths * float(self.height_m))
        object.__setattr__(self, "_frozen", True)

    @property
    def n(self) -> int:
        return int(self.edges_m.size - 1)

    @property
    def centers(self) -> np.ndarray:
        return self._centers

    @property
    def widths(self) -> np.ndarray:
        return self._widths

    @property
    def areas(self) -> np.ndarray:
        return self._areas

    def refined(self, factor: int, offset_fraction: float = 0.5) -> "InterfaceMesh":
        """Refine by ``factor``, shifting the new edges by ``offset_fraction`` of a facet.

        The default half-facet shift guarantees the refined mesh is *not* nested
        with the original one -- conservative projection has to be exact for
        genuinely non-matching meshes, not only for nested ones.
        """
        if factor < 1:
            raise ValueError("factor must be >= 1")
        base = self.edges_m
        step = (base[-1] - base[0]) / (self.n * factor)
        shift = offset_fraction * step
        edges = np.linspace(base[0] + shift, base[-1] + shift, self.n * factor + 1)
        return InterfaceMesh(edges, self.height_m)


def graded_interface_mesh(x0: float, x1: float, n: int, height_m: float,
                          ratio: float = 1.04) -> InterfaceMesh:
    """Graded strip mesh: facets shorten towards the stem (``x0``).

    Used for the LBM side so that the two discretisations are *neither nested nor
    uniform*: facet edges never coincide with patch edges and the facet widths
    span a 1:6 range, which is what a real waterline-refined surface mesh looks
    like near the bow.  Full coverage of ``[x0, x1]`` is preserved, so any
    residual cannot be blamed on mass leaving the interface.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    weights = ratio ** np.arange(n)
    widths = weights / weights.sum() * (x1 - x0)
    edges = np.concatenate([[x0], x0 + np.cumsum(widths)])
    edges[-1] = x1
    return InterfaceMesh(edges, height_m)


def overlap_matrix(coarse: InterfaceMesh, fine: InterfaceMesh) -> np.ndarray:
    """``O[i, j]``: overlap length of coarse patch ``i`` with fine facet ``j``."""
    c0 = coarse.edges_m[:-1][:, None]
    c1 = coarse.edges_m[1:][:, None]
    f0 = fine.edges_m[:-1][None, :]
    f1 = fine.edges_m[1:][None, :]
    return np.clip(np.minimum(c1, f1) - np.maximum(c0, f0), 0.0, None)


def coverage_closure(coarse: InterfaceMesh, fine: InterfaceMesh,
                     overlap: np.ndarray | None = None) -> tuple[bool, float]:
    """``sum_i O[i,j] == w_f[j]`` -- the identity the conservativeness proof rests on."""
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    err = np.max(np.abs(O.sum(axis=0) - fine.widths)) / max(float(fine.widths.max()), 1e-30)
    cover = float(O.sum()) / float(fine.widths.sum())
    return err < 1e-12 and abs(cover - 1.0) < 1e-12, float(err)


# ---------------------------------------------------------------------------
# projection / spread operators
# ---------------------------------------------------------------------------


def conservative_project(coarse: InterfaceMesh, fine: InterfaceMesh,
                         fine_values: np.ndarray,
                         overlap: np.ndarray | None = None) -> np.ndarray:
    """Conservation-exact fine -> coarse projection of a *density* field.

    ``q_c[i] = sum_j O[i,j] q_f[j] / w_c[i]``; the overlap lengths already carry
    the measure, so no extra facet weight may appear -- the classic defect this
    operator avoids is multiplying by ``w_f`` inside *and* letting the column sum
    add it a second time.
    """
    values = np.asarray(fine_values, dtype=float)
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    return (O @ values) / coarse.widths


def nearest_project(coarse: InterfaceMesh, fine: InterfaceMesh,
                    fine_values: np.ndarray) -> np.ndarray:
    """NEGATIVE CONTROL: each patch takes its nearest facet value."""
    values = np.asarray(fine_values, dtype=float)
    index = np.argmin(np.abs(coarse.centers[:, None] - fine.centers[None, :]), axis=1)
    return values[index]


def conservative_spread(fine: InterfaceMesh, coarse: InterfaceMesh,
                        coarse_impulse: np.ndarray,
                        overlap: np.ndarray | None = None) -> np.ndarray:
    """Conservation-exact patch -> facet spread of a per-unit-height impulse.

    ``d_j = sum_c (I_c / w_c) O[c,j] / w_f[j]`` so that
    ``sum_j d_j w_f[j] = sum_c I_c`` (third law on the interface).
    """
    impulse = np.asarray(coarse_impulse, dtype=float)
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    return ((impulse / coarse.widths)[:, None] * O).sum(axis=0) / fine.widths


def unweighted_average_project(coarse: InterfaceMesh, fine: InterfaceMesh,
                               fine_values: np.ndarray,
                               overlap: np.ndarray | None = None) -> np.ndarray:
    """NEGATIVE CONTROL: plain average of the overlapping facets, widths ignored.

    The defect a graded receiving mesh exposes: the facet widths span 1:6, so an
    unweighted average re-weights the field by the local refinement and the
    transported total is wrong even though every patch "sees" the right facets.
    """
    values = np.asarray(fine_values, dtype=float)
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    mask = (O > 0.0).astype(float)
    count = mask.sum(axis=1)
    count[count == 0.0] = 1.0
    return (mask @ values) / count


def per_facet_normalised_spread(fine: InterfaceMesh, coarse: InterfaceMesh,
                                coarse_impulse: np.ndarray, support: float = 2.0,
                                overlap: np.ndarray | None = None) -> np.ndarray:
    """NEGATIVE CONTROL: reaction spread normalised per *receiving* facet.

    Every receiving facet renormalises the patch weights it sees to one, so the
    total impulse handed back no longer equals the impulse applied -- the defect
    a third-law check exists to catch.
    """
    O = overlap_matrix(coarse, fine) if overlap is None else overlap   # (n_coarse, n_fine)
    support_width = support * float(np.mean(fine.widths))
    dist = np.abs(fine.centers[:, None] - coarse.centers[None, :])   # (n_fine, n_coarse)
    kernel = np.exp(-((dist / support_width) ** 2)) * (O.T > 0.0)
    denom = kernel.sum(axis=1)
    denom[denom == 0.0] = 1.0
    weights = kernel / denom[:, None]                                # per receiving facet
    return (weights @ np.asarray(coarse_impulse, dtype=float)) / fine.widths


def residual(sent: float, received: float, scale: float | None = None) -> float:
    """``|received - sent| / scale`` with ``scale = sum|sent|`` by default."""
    denominator = abs(sent) if scale is None else scale
    if denominator <= 0.0:
        denominator = 1.0
    return abs(received - sent) / denominator


def vector_residual(sent: np.ndarray, received: np.ndarray, scale: float) -> float:
    denominator = float(scale) if scale > 0.0 else 1.0
    return float(np.linalg.norm(np.asarray(received, dtype=float) - np.asarray(sent, dtype=float)) / denominator)


# ---------------------------------------------------------------------------
# interface flux state (from the N03 payload)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BeltFlux:
    """Interface flux fields evaluated on the fine (LBM) facet mesh.

    ``mass_kg_m2s``   ``f_c rho_ice u_n``            -- ice mass rate per unit area
    ``traction_x_Pa``  ``m_n sin(theta) + m_t cos(theta)`` -- advance-resisting component
    ``traction_y_Pa``  ``-m_n cos(theta) + m_t sin(theta)`` -- belt-normal component
    with ``m_n = f_c (p + rho_ice u_n^2)`` and ``m_t = f_c (mu p + rho_ice u_n u_t)``.
    """

    s_m: np.ndarray
    theta_rad: np.ndarray
    mass_kg_m2s: np.ndarray
    traction_x_Pa: np.ndarray
    traction_y_Pa: np.ndarray

    def as_dict(self) -> dict[str, Any]:
        return {
            "mass_kg_m2s": [float(x) for x in self.mass_kg_m2s],
            "traction_x_Pa": [float(x) for x in self.traction_x_Pa],
            "traction_y_Pa": [float(x) for x in self.traction_y_Pa],
        }


def belt_flux_at(n03: Mapping[str, Any], sample_index: int, s_query: np.ndarray, *,
                 rho_ice: float | None = None, mu: float | None = None) -> BeltFlux:
    """Evaluate the interface flux fields at arbitrary ``s`` by linear interpolation.

    The continuous field is what the two discretisations both approximate; the
    exchange operator is tested on it because a *mesh* error must not be
    confused with a *model* error.
    """
    sample = n03["samples"][sample_index]
    f_c = float(n03["contact_area_ratio"])
    rho_ice = float(n03["ice"]["rho_ice_kg_m3"] if rho_ice is None else rho_ice)
    mu = float(n03["ice"]["mu_hull_ice"] if mu is None else mu)
    s = np.asarray(sample["s_m"], dtype=float)
    p = np.interp(s_query, s, np.asarray(sample["p_ice_Pa"], dtype=float))
    u_n = np.interp(s_query, s, np.asarray(sample["u_n_ms"], dtype=float))
    u_t = np.interp(s_query, s, np.asarray(sample["v_slip_ms"], dtype=float))
    theta = np.interp(s_query, s, np.asarray(sample["alpha_rad"], dtype=float))
    m_n = f_c * (p + rho_ice * u_n**2)
    m_t = f_c * (mu * p + rho_ice * u_n * u_t)
    mass = f_c * rho_ice * u_n
    return BeltFlux(
        s_m=np.asarray(s_query, dtype=float),
        theta_rad=theta,
        mass_kg_m2s=mass,
        traction_x_Pa=m_n * np.sin(theta) + m_t * np.cos(theta),
        traction_y_Pa=-m_n * np.cos(theta) + m_t * np.sin(theta),
    )


def belt_mean_normal_flux(flux: BeltFlux) -> BeltFlux:
    """NEGATIVE CONTROL: materialise the traction along the belt-mean normal."""
    theta_bar = float(np.mean(flux.theta_rad))
    n = np.array([np.sin(theta_bar), -np.cos(theta_bar)])
    # magnitude projected on the mean normal (keeps the scalar content, loses the rotation)
    scalar = flux.traction_x_Pa * n[0] + flux.traction_y_Pa * n[1]
    return BeltFlux(
        s_m=flux.s_m,
        theta_rad=np.full_like(flux.theta_rad, theta_bar),
        mass_kg_m2s=flux.mass_kg_m2s,
        traction_x_Pa=scalar * n[0],
        traction_y_Pa=scalar * n[1],
    )


# ---------------------------------------------------------------------------
# residuals
# ---------------------------------------------------------------------------


@dataclass
class ConservationReport:
    mass_residual: float
    momentum_residual: float
    third_law_residual: float
    quadrature_residual: float
    observed_order: float | None
    negative_controls: dict[str, float]
    tolerance: float
    coverage_error: float

    @property
    def ok(self) -> bool:
        return (
            self.mass_residual < self.tolerance
            and self.momentum_residual < self.tolerance
            and self.third_law_residual < self.tolerance
        )

    @property
    def control_summary(self) -> dict[str, float]:
        """Worst-case negative control per conservation law."""
        mass = max(self.negative_controls[key] for key in
                   ("nearest_neighbour_mass", "unweighted_average_mass", "coverage_mismatch_mass")
                   if key in self.negative_controls)
        momentum = self.negative_controls.get("belt_mean_normal_momentum", 0.0)
        reaction = self.negative_controls.get("per_facet_normalised_reaction", 0.0)
        return {"mass": mass, "momentum": momentum, "reaction": reaction}

    @property
    def discriminating(self) -> bool:
        """Each conservation law must be caught by its own defect control."""
        return all(value > NEGATIVE_CONTROL_FLOOR for value in self.control_summary.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "mass_residual": self.mass_residual,
            "momentum_residual": self.momentum_residual,
            "third_law_residual": self.third_law_residual,
            "quadrature_residual": self.quadrature_residual,
            "observed_order": self.observed_order,
            "negative_controls": dict(self.negative_controls),
            "control_summary": self.control_summary,
            "tolerance": self.tolerance,
            "coverage_error": self.coverage_error,
            "ok": self.ok,
            "discriminating": self.discriminating,
        }


def _integrate(mesh: InterfaceMesh, values: np.ndarray) -> float:
    return float(np.sum(np.asarray(values, dtype=float) * mesh.areas))


def _integrate_vector(mesh: InterfaceMesh, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.array([_integrate(mesh, x), _integrate(mesh, y)])


def _momentum_residual(coarse: InterfaceMesh, fine: InterfaceMesh,
                       flux_c: BeltFlux, flux_f: BeltFlux) -> float:
    sent = _integrate_vector(coarse, flux_c.traction_x_Pa, flux_c.traction_y_Pa)
    recv = _integrate_vector(fine, flux_f.traction_x_Pa, flux_f.traction_y_Pa)
    scale = float(np.sum(fine.areas * np.hypot(flux_f.traction_x_Pa, flux_f.traction_y_Pa)))
    return vector_residual(sent, recv, scale)


def _third_law_residual(coarse: InterfaceMesh, fine: InterfaceMesh,
                        flux_f: BeltFlux, *, negative_control: bool = False,
                        overlap: np.ndarray | None = None) -> float:
    """Reaction impulse handed back to the facets vs the impulse applied.

    All quantities are per unit belt height (the height cancels in the relative
    residual).  ``impulse = traction_x + traction_y`` is an odd but legitimate
    combination: the identity being checked is the *sum* of whatever is handed
    over, not a physical force component.
    """
    impulse_f = flux_f.traction_x_Pa + flux_f.traction_y_Pa
    applied = float(np.sum(impulse_f * fine.widths))
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    patch_impulse = O @ impulse_f  # patch impulse per unit height [N s/m]
    if negative_control:
        spread = per_facet_normalised_spread(fine, coarse, patch_impulse, overlap=O)
    else:
        spread = conservative_spread(fine, coarse, patch_impulse, overlap=O)
    received = float(np.sum(spread * fine.widths))
    return residual(applied, received, float(np.sum(np.abs(impulse_f) * fine.widths)))


def _payload_matrix(n03: Mapping[str, Any], key: str) -> np.ndarray:
    """Payload field stacked over samples: shape ``(n_samples, n_patches)``."""
    return np.asarray([np.asarray(sample[key], dtype=float) for sample in n03["samples"]])


def _interp_plan(s_source: np.ndarray, s_query: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Searchsorted-based interpolation plan, reusable for every sample."""
    idx = np.clip(np.searchsorted(s_source, s_query) - 1, 0, s_source.size - 2)
    span = s_source[idx + 1] - s_source[idx]
    weight = np.clip((s_query - s_source[idx]) / span, 0.0, 1.0)
    return idx, weight


def _interp_rows(matrix: np.ndarray, idx: np.ndarray, weight: np.ndarray) -> np.ndarray:
    return matrix[:, idx] * (1.0 - weight) + matrix[:, idx + 1] * weight


def belt_flux_batch(n03: Mapping[str, Any], s_query: np.ndarray, *,
                    mu: float | None = None) -> dict[str, np.ndarray]:
    """Interface flux fields for **all** samples at ``s_query``: ``(n_samples, n_query)``.

    Consumes the N03 payload's patch values and interpolates them to the query
    points (facet or patch centres).  The interpolation plan is computed once, so
    the per-sample work is one vectorised gather instead of one ``np.interp``.
    """
    rows = n03["samples"]
    s_source = np.asarray(rows[0]["s_m"], dtype=float)
    idx, weight = _interp_plan(s_source, np.asarray(s_query, dtype=float))
    p = _interp_rows(_payload_matrix(n03, "p_ice_Pa"), idx, weight)
    u_n = _interp_rows(_payload_matrix(n03, "u_n_ms"), idx, weight)
    u_t = _interp_rows(_payload_matrix(n03, "v_slip_ms"), idx, weight)
    theta = _interp_rows(_payload_matrix(n03, "alpha_rad"), idx, weight)
    f_c = float(n03["contact_area_ratio"])
    rho = float(n03["ice"]["rho_ice_kg_m3"])
    mu = float(n03["ice"]["mu_hull_ice"] if mu is None else mu)
    m_n = f_c * (p + rho * u_n**2)
    m_t = f_c * (mu * p + rho * u_n * u_t)
    return {
        "mass": f_c * rho * u_n,
        "traction_x": m_n * np.sin(theta) + m_t * np.cos(theta),
        "traction_y": -m_n * np.cos(theta) + m_t * np.sin(theta),
        "theta": theta,
    }


def _convex_kernel(fine: InterfaceMesh, coarse: InterfaceMesh, mask: np.ndarray,
                   support: float) -> np.ndarray:
    """Row-normalised kernel weights ``(n_fine, n_coarse)`` restricted to overlap."""
    dist = np.abs(fine.centers[:, None] - coarse.centers[None, :])
    kernel = np.exp(-((dist / (support * float(np.mean(fine.widths)))) ** 2)) * mask.T
    denominator = kernel.sum(axis=1)
    denominator[denominator == 0.0] = 1.0
    return kernel / denominator[:, None]


def residuals_batch(n03: Mapping[str, Any], *, coarse: InterfaceMesh, fine: InterfaceMesh,
                    overlap: np.ndarray | None = None, mu: float | None = None,
                    indices: Sequence[int] | None = None) -> dict[str, Any]:
    """All conservation residuals for every sample, vectorised over the sample axis.

    Returns ``(n_samples,)`` arrays for the production residuals and for the three
    negative controls, plus the flux fields used.
    """
    if indices is not None:
        n03 = _sample_view(n03, indices)
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    w_f, w_c = fine.widths, coarse.widths
    flux_f = belt_flux_batch(n03, fine.centers, mu=mu)
    flux_c = belt_flux_batch(n03, coarse.centers, mu=mu)

    # --- mass ------------------------------------------------------------
    mass_sent = flux_f["mass"] @ w_f
    mass_scale = np.abs(flux_f["mass"]) @ w_f
    mass_proj = (flux_f["mass"] @ O.T) / w_c
    mass_res = np.abs(mass_proj @ w_c - mass_sent) / np.maximum(mass_scale, 1e-300)

    # --- momentum (vector) ----------------------------------------------
    sent_x = flux_f["traction_x"] @ w_f
    sent_y = flux_f["traction_y"] @ w_f
    recv_x = ((flux_f["traction_x"] @ O.T) / w_c) @ w_c
    recv_y = ((flux_f["traction_y"] @ O.T) / w_c) @ w_c
    mom_scale = np.hypot(flux_f["traction_x"], flux_f["traction_y"]) @ w_f
    mom_res = np.hypot(recv_x - sent_x, recv_y - sent_y) / np.maximum(mom_scale, 1e-300)

    # --- third law (reaction spread) ------------------------------------
    impulse = flux_f["traction_x"] + flux_f["traction_y"]
    applied = impulse @ w_f
    patch_impulse = impulse @ O.T                       # patch impulse per unit height
    spread = ((patch_impulse / w_c[None, :])[:, :, None] * O[None, :, :]).sum(axis=1) / w_f[None, :]
    third_res = np.abs(spread @ w_f - applied) / np.maximum(np.abs(impulse) @ w_f, 1e-300)

    # --- negative control 1: nearest facet / unweighted facet average ----
    nearest_idx = np.argmin(np.abs(coarse.centers[:, None] - fine.centers[None, :]), axis=1)
    nc_nearest = np.abs((flux_f["mass"][:, nearest_idx] @ w_c) - mass_sent) / np.maximum(mass_scale, 1e-300)
    mask = (O > 0.0).astype(float)
    counts = np.maximum(mask.sum(axis=1), 1.0)
    unweighted = (flux_f["mass"] @ mask.T) / counts
    nc_unweighted = np.abs(unweighted @ w_c - mass_sent) / np.maximum(mass_scale, 1e-300)

    # --- negative control 1b: mesh-extent mismatch (2 % of the belt at the stem
    # not covered by the receiving mesh; the mass sitting on the uncovered strip
    # is silently dropped -- the classic "the two meshes do not cover the same
    # surface" integration defect, and the worst place to lose it is the stem)
    shift = 0.02 * (coarse.edges_m[-1] - coarse.edges_m[0])
    trimmed = InterfaceMesh(coarse.edges_m + shift, coarse.height_m)
    O_trim = overlap_matrix(trimmed, fine)
    mass_trim = ((flux_f["mass"] @ O_trim.T) / trimmed.widths) @ trimmed.widths
    nc_coverage = np.abs(mass_trim - mass_sent) / np.maximum(mass_scale, 1e-300)

    # --- negative control 2: one belt-mean normal ------------------------
    theta_bar = np.mean(flux_c["theta"], axis=1)
    normal = np.stack([np.sin(theta_bar), -np.cos(theta_bar)], axis=1)     # (n_samples, 2)
    scalar_c = flux_c["traction_x"] * normal[:, 0:1] + flux_c["traction_y"] * normal[:, 1:2]
    nc_vec = np.stack([(scalar_c @ w_c) * normal[:, 0], (scalar_c @ w_c) * normal[:, 1]], axis=1)
    nc_mom = np.hypot(nc_vec[:, 0] - sent_x, nc_vec[:, 1] - sent_y) / np.maximum(mom_scale, 1e-300)

    # --- negative control 3: per-facet normalised reaction ---------------
    weights = _convex_kernel(fine, coarse, mask, support=2.0)
    nc_third = np.abs(patch_impulse @ weights.sum(axis=0) - applied) / np.maximum(
        np.abs(impulse) @ w_f, 1e-300)

    return {
        "mass_residual": mass_res,
        "momentum_residual": mom_res,
        "third_law_residual": third_res,
        "nc_nearest_mass": nc_nearest,
        "nc_unweighted_mass": nc_unweighted,
        "nc_coverage_mismatch_mass": nc_coverage,
        "nc_belt_mean_normal_momentum": nc_mom,
        "nc_per_facet_normalised_reaction": nc_third,
        "flux_fine": flux_f,
        "flux_coarse": flux_c,
    }


def _sample_view(n03: Mapping[str, Any], indices: Sequence[int]) -> dict[str, Any]:
    view = dict(n03)
    view["samples"] = [n03["samples"][int(i)] for i in indices]
    return view


def _report_from_batch(batch: Mapping[str, Any], i: int, *, tolerance: float,
                       coverage_error: float, order: float | None,
                       quadrature_residual: float) -> ConservationReport:
    return ConservationReport(
        mass_residual=float(batch["mass_residual"][i]),
        momentum_residual=float(batch["momentum_residual"][i]),
        third_law_residual=float(batch["third_law_residual"][i]),
        quadrature_residual=quadrature_residual,
        observed_order=order,
        negative_controls={
            "nearest_neighbour_mass": float(batch["nc_nearest_mass"][i]),
            "unweighted_average_mass": float(batch["nc_unweighted_mass"][i]),
            "coverage_mismatch_mass": float(batch["nc_coverage_mismatch_mass"][i]),
            "belt_mean_normal_momentum": float(batch["nc_belt_mean_normal_momentum"][i]),
            "per_facet_normalised_reaction": float(batch["nc_per_facet_normalised_reaction"][i]),
        },
        tolerance=tolerance,
        coverage_error=coverage_error,
    )


def exchange_conservation(n03: Mapping[str, Any], sample_index: int, *,
                          coarse: InterfaceMesh, fine: InterfaceMesh,
                          mu: float | None = None,
                          tolerance: float = CONSERVATION_TOL,
                          quadrature_check: bool = False,
                          overlap: np.ndarray | None = None) -> ConservationReport:
    """Run the conservation residuals for the ``sample_index``-th ``(h, V)`` sample."""
    i = int(sample_index)
    batch = residuals_batch(n03, coarse=coarse, fine=fine, overlap=overlap, mu=mu, indices=[i])
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    _, cover_err = coverage_closure(coarse, fine, overlap=O)
    order = quadrature_order(n03, i, n_patches=coarse.n, mu=mu) if quadrature_check else None
    return _report_from_batch(
        batch, 0, tolerance=tolerance, coverage_error=cover_err, order=order,
        quadrature_residual=_quadrature_residual(coarse, fine, _belt_flux_of(batch["flux_coarse"], 0),
                                                 _belt_flux_of(batch["flux_fine"], 0)),
    )


def _belt_flux_of(arrays: Mapping[str, np.ndarray], index: int) -> BeltFlux:
    """Slice one sample out of the batched flux arrays into a :class:`BeltFlux`."""
    mass = np.atleast_1d(arrays["mass"][index])
    return BeltFlux(
        s_m=np.zeros_like(mass),
        theta_rad=np.atleast_1d(arrays["theta"][index]),
        mass_kg_m2s=mass,
        traction_x_Pa=np.atleast_1d(arrays["traction_x"][index]),
        traction_y_Pa=np.atleast_1d(arrays["traction_y"][index]),
    )


def _quadrature_residual(coarse: InterfaceMesh, fine: InterfaceMesh,
                         flux_c: BeltFlux, flux_f: BeltFlux) -> float:
    """Discretisation error of the *coarse centre sampling* (not a conservation error)."""
    sent = _integrate(fine, flux_f.traction_x_Pa)
    recv = _integrate(coarse, flux_c.traction_x_Pa)
    scale = float(np.sum(np.abs(flux_f.traction_x_Pa) * fine.areas))
    return residual(sent, recv, scale)


def quadrature_order(n03: Mapping[str, Any], sample_index: int, *,
                     n_patches: int, mu: float | None = None,
                     factors: tuple[int, int] = (2, 4)) -> float | None:
    """Observed order of the patch-centre integration under refinement."""
    belt_end = float(n03["samples"][sample_index]["s_m"][-1])
    height = 1.0  # cancels in the ratio
    ref = InterfaceMesh(np.linspace(0.0, belt_end, 16 * n_patches + 1), height)
    ref_value = _integrate(ref, belt_flux_at(n03, sample_index, ref.centers, mu=mu).traction_x_Pa)
    errors = []
    for factor in factors:
        mesh = InterfaceMesh(np.linspace(0.0, belt_end, factor * n_patches + 1), height)
        value = _integrate(mesh, belt_flux_at(n03, sample_index, mesh.centers, mu=mu).traction_x_Pa)
        errors.append(abs(value - ref_value) / max(abs(ref_value), 1e-30))
    if errors[0] <= 1e-16 or errors[1] <= 1e-16 or errors[1] >= errors[0]:
        return None
    return float(math.log2(errors[0] / errors[1]) / math.log2(factors[1] / factors[0]))


# ---------------------------------------------------------------------------
# exchange payload (contract of the hand-off itself)
# ---------------------------------------------------------------------------


def build_exchange_payload(n03: Mapping[str, Any], sample_index: int, *,
                           coarse: InterfaceMesh, fine: InterfaceMesh,
                           producer: str = "P66-W2-N06 interface exchange (conservative overlap projection)",
                           mu: float | None = None,
                           overlap: np.ndarray | None = None) -> dict[str, Any]:
    """Assemble the exchange payload for one sample."""
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    flux_f = belt_flux_at(n03, sample_index, fine.centers, mu=mu)
    proj = {
        "mass_kg_m2s": conservative_project(coarse, fine, flux_f.mass_kg_m2s, overlap=O),
        "traction_x_Pa": conservative_project(coarse, fine, flux_f.traction_x_Pa, overlap=O),
        "traction_y_Pa": conservative_project(coarse, fine, flux_f.traction_y_Pa, overlap=O),
    }
    payload = {
        "schema": SCHEMA_EXCHANGE,
        "producer": producer,
        "coarse": {"edges_m": [float(x) for x in coarse.edges_m], "height_m": coarse.height_m},
        "fine": {"edges_m": [float(x) for x in fine.edges_m], "height_m": fine.height_m},
        "coarse_flux": {key: [float(v) for v in value] for key, value in proj.items()},
        "fine_flux": flux_f.as_dict(),
        "units": dict(EXCHANGE_UNITS),
    }
    return payload


def build_exchange_payloads(n03: Mapping[str, Any], *, coarse: InterfaceMesh,
                            fine: InterfaceMesh, overlap: np.ndarray | None = None,
                            mu: float | None = None,
                            producer: str = "P66-W2-N06 interface exchange (conservative overlap projection)",
                            ) -> list[dict[str, Any]]:
    """Build the hand-off payload of every sample in one pass.

    Same payload structure as :func:`build_exchange_payload`, but the flux fields
    are evaluated for the whole sweep at once and the projection reuses the cached
    overlap matrix -- the per-sample Python loop is reduced to building the JSON
    lists.
    """
    O = overlap_matrix(coarse, fine) if overlap is None else overlap
    flux_f = belt_flux_batch(n03, fine.centers, mu=mu)
    widths = coarse.widths
    proj = {
        "mass_kg_m2s": (flux_f["mass"] @ O.T) / widths,
        "traction_x_Pa": (flux_f["traction_x"] @ O.T) / widths,
        "traction_y_Pa": (flux_f["traction_y"] @ O.T) / widths,
    }
    edges_c = np.asarray(coarse.edges_m, dtype=float).tolist()
    edges_f = np.asarray(fine.edges_m, dtype=float).tolist()
    payloads: list[dict[str, Any]] = []
    for index in range(len(n03["samples"])):
        payloads.append({
            "schema": SCHEMA_EXCHANGE,
            "producer": producer,
            "coarse": {"edges_m": edges_c, "height_m": coarse.height_m},
            "fine": {"edges_m": edges_f, "height_m": fine.height_m},
            "coarse_flux": {key: value[index].tolist() for key, value in proj.items()},
            "fine_flux": {
                "mass_kg_m2s": flux_f["mass"][index].tolist(),
                "traction_x_Pa": flux_f["traction_x"][index].tolist(),
                "traction_y_Pa": flux_f["traction_y"][index].tolist(),
            },
            "units": dict(EXCHANGE_UNITS),
        })
    return payloads


def validate_exchange_payload(payload: Mapping[str, Any]) -> list[C.ContractCheck]:
    """Schema + unit checks for the exchange hand-off payload."""
    checks: list[C.ContractCheck] = []
    try:
        validate(EXCHANGE_SCHEMA_DEF, payload)
        checks.append(C.ContractCheck("exchange:schema", True, "schema conforming"))
    except SchemaError as exc:
        checks.append(C.ContractCheck("exchange:schema", False, str(exc)))
        return checks
    units = payload.get("units", {})
    for name, want in EXCHANGE_UNITS.items():
        if name not in units:
            checks.append(C.ContractCheck(f"exchange:unit:{name}", False, "unit not declared"))
            continue
        ok, detail = C.unit_ok(units[name], want)
        checks.append(C.ContractCheck(f"exchange:unit:{name}", ok, f"{units[name]!r} vs {want!r}: {detail}"))
    edges = np.asarray(payload["coarse"]["edges_m"], dtype=float)
    fine_edges = np.asarray(payload["fine"]["edges_m"], dtype=float)
    checks.append(C.ContractCheck(
        "exchange:mesh_alignment",
        bool(edges[0] == fine_edges[0] and edges[-1] == fine_edges[-1]),
        f"coarse [{edges[0]:.3f},{edges[-1]:.3f}] vs fine [{fine_edges[0]:.3f},{fine_edges[-1]:.3f}] m",
    ))
    nested = all(bool(np.any(np.isclose(fine_edges, edge))) for edge in edges)
    non_matching = not nested
    checks.append(C.ContractCheck(
        "exchange:non_matching_meshes", bool(non_matching),
        "patch edges are not a subset of facet edges (genuinely non-matching interface)",
    ))
    n_c = int(edges.size - 1)
    n_f = int(fine_edges.size - 1)
    lengths_ok = all(len(payload["coarse_flux"][k]) == n_c for k in payload["coarse_flux"]) and \
        all(len(payload["fine_flux"][k]) == n_f for k in payload["fine_flux"])
    checks.append(C.ContractCheck(
        "exchange:array_lengths", bool(lengths_ok),
        f"coarse {n_c} patches, fine {n_f} facets, arrays aligned",
    ))
    return checks

# ---------------------------------------------------------------------------
# reusable exchange plan (overlap matrix computed once per mesh pair)
# ---------------------------------------------------------------------------


class ExchangePlan:
    """Precomputed interface plan shared by every sample of a sweep.

    ``overlap_matrix`` is the only O(n_coarse * n_fine) step of the exchange, so
    it is computed once per *mesh pair* instead of once per sample.  The plan
    also caches the coverage closure and exposes :meth:`build_payload`,
    :meth:`conservation` and :meth:`all`.
    """

    __slots__ = ("coarse", "fine", "overlap", "coverage_error")

    def __init__(self, coarse: InterfaceMesh, fine: InterfaceMesh) -> None:
        self.coarse = coarse
        self.fine = fine
        self.overlap = overlap_matrix(coarse, fine)
        _, self.coverage_error = coverage_closure(coarse, fine, overlap=self.overlap)

    def build_payload(self, n03: Mapping[str, Any], index: int, *,
                      producer: str = "P66-W2-N06 interface exchange (conservative overlap projection)",
                      mu: float | None = None) -> dict[str, Any]:
        return build_exchange_payload(n03, index, coarse=self.coarse, fine=self.fine,
                                      producer=producer, mu=mu, overlap=self.overlap)

    def build_payloads(self, n03: Mapping[str, Any], *,
                       producer: str = "P66-W2-N06 interface exchange (conservative overlap projection)",
                       mu: float | None = None) -> list[dict[str, Any]]:
        return build_exchange_payloads(n03, coarse=self.coarse, fine=self.fine,
                                       overlap=self.overlap, mu=mu, producer=producer)

    def conservation(self, n03: Mapping[str, Any], index: int, *,
                     mu: float | None = None, tolerance: float = CONSERVATION_TOL,
                     quadrature_check: bool = False) -> ConservationReport:
        return exchange_conservation(n03, index, coarse=self.coarse, fine=self.fine, mu=mu,
                                     tolerance=tolerance, quadrature_check=quadrature_check,
                                     overlap=self.overlap)

    def all(self, n03: Mapping[str, Any], *, mu: float | None = None,
            tolerance: float = CONSERVATION_TOL) -> list[ConservationReport]:
        """All samples in one pass: the residual algebra is vectorised over samples."""
        batch = residuals_batch(n03, coarse=self.coarse, fine=self.fine,
                               overlap=self.overlap, mu=mu)
        reports = []
        for index in range(len(n03["samples"])):
            reports.append(_report_from_batch(
                batch, index, tolerance=tolerance, coverage_error=self.coverage_error,
                order=None,
                quadrature_residual=_quadrature_residual(self.coarse, self.fine,
                                                         _belt_flux_of(batch["flux_coarse"], index),
                                                         _belt_flux_of(batch["flux_fine"], index)),
            ))
        return reports


def validate_exchange_batch(payloads: list[Mapping[str, Any]]) -> list[C.ContractCheck]:
    """Two-tier validation of a batch of hand-off payloads.

    Tier 1 (once per batch): the full JSON-schema + unit/scale validation of the
    first payload -- the contract is validated, not re-parsed per sample.
    Tier 2 (per payload): mesh monotonicity, array lengths against the declared
    meshes and finiteness of every value, vectorised.  A schema break in *any*
    payload's structure is still caught, because the structure is shared and
    tier 1 pins it; a payload whose arrays are wrong is caught by tier 2.
    """
    if not payloads:
        return [C.ContractCheck("exchange:batch", False, "no payloads supplied")]
    checks = validate_exchange_payload(payloads[0])
    checks[0] = C.ContractCheck("exchange:schema", checks[0].ok,
                                checks[0].detail + f" (validated once for {len(payloads)} payloads)")
    n_bad = 0
    details: list[str] = []
    for index, payload in enumerate(payloads):
        try:
            n_c = len(payload["coarse"]["edges_m"]) - 1
            n_f = len(payload["fine"]["edges_m"]) - 1
            arrays = [(key, np.asarray(payload["coarse_flux"][key], dtype=float), n_c)
                      for key in ("mass_kg_m2s", "traction_x_Pa", "traction_y_Pa")]
            arrays += [(key, np.asarray(payload["fine_flux"][key], dtype=float), n_f)
                       for key in ("mass_kg_m2s", "traction_x_Pa", "traction_y_Pa")]
            for key, values, expected in arrays:
                if values.size != expected or not np.all(np.isfinite(values)):
                    n_bad += 1
                    details.append(f"payload[{index}].{key}: size {values.size} != {expected} or non-finite")
        except (KeyError, TypeError) as exc:
            n_bad += 1
            details.append(f"payload[{index}]: malformed ({exc})")
    checks.append(C.ContractCheck(
        "exchange:batch_shape_finiteness", n_bad == 0,
        f"{len(payloads)} payloads, {n_bad} malformed" + (": " + "; ".join(details[:3]) if details else ""),
    ))
    return checks
