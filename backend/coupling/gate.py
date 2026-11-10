"""Twin consistency gate: global conservation check across an exchange.

Zero domain semantics: the gate compares the *global integrals* of a
scalar field (and optionally a co-moving velocity field) before and after
the exchange. "mass" = integral of the scalar field; "momentum" and
"energy" are defined from the scalar x velocity pair. The default
acceptance bar is a 1% relative error per quantity.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mesh import Mesh

__all__ = ["TwinConsistencyGate", "GateReport"]


@dataclass(frozen=True)
class GateReport:
    rel_tol: float
    errors: dict  # quantity name -> relative error

    @property
    def passed(self) -> bool:
        return bool(max(self.errors.values()) <= self.rel_tol)

    def to_dict(self) -> dict:
        return {
            "rel_tol": self.rel_tol,
            "errors": {k: float(v) for k, v in self.errors.items()},
            "passed": self.passed,
        }


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


class TwinConsistencyGate:
    """Machine-checkable conservation gate for a field exchange."""

    def __init__(self, rel_tol: float = 0.01) -> None:
        if not (0.0 < rel_tol < 1.0):
            raise ValueError("rel_tol must be in (0, 1)")
        self.rel_tol = rel_tol

    def check(
        self,
        src: Mesh,
        src_field: np.ndarray,
        tgt: Mesh,
        tgt_field: np.ndarray,
        src_velocity: np.ndarray | None = None,
        tgt_velocity: np.ndarray | None = None,
    ) -> GateReport:
        f_s = np.asarray(src_field, dtype=float)
        f_t = np.asarray(tgt_field, dtype=float)
        if f_s.shape != (src.n_cells,) or f_t.shape != (tgt.n_cells,):
            raise ValueError("field shapes inconsistent with meshes")
        if not (np.all(np.isfinite(f_s)) and np.all(np.isfinite(f_t))):
            raise ValueError("fields contain non-finite values")

        errors: dict[str, float] = {}
        i_s = float(np.sum(src.volumes * f_s))
        i_t = float(np.sum(tgt.volumes * f_t))
        errors["mass"] = _rel(i_s, i_t)

        if src_velocity is not None and tgt_velocity is not None:
            v_s = np.asarray(src_velocity, dtype=float)
            v_t = np.asarray(tgt_velocity, dtype=float)
            if v_s.shape != (src.n_cells, src.dim) or v_t.shape != (tgt.n_cells, tgt.dim):
                raise ValueError("velocity shapes inconsistent with meshes")
            p_s = (src.volumes * f_s)[:, None] * v_s
            p_t = (tgt.volumes * f_t)[:, None] * v_t
            errors["momentum"] = _rel(
                float(np.linalg.norm(p_s.sum(axis=0))),
                float(np.linalg.norm(p_t.sum(axis=0))),
            )
            e_s = float(0.5 * np.sum(src.volumes * f_s * np.einsum("ij,ij->i", v_s, v_s)))
            e_t = float(0.5 * np.sum(tgt.volumes * f_t * np.einsum("ij,ij->i", v_t, v_t)))
            errors["energy"] = _rel(e_s, e_t)
        return GateReport(rel_tol=self.rel_tol, errors=errors)
