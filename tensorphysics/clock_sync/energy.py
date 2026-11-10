"""Interface energy bookkeeping for two-way coupling (domain-neutral).

The budget that a coupled run has to satisfy is::

    Σ E_in  =  Σ E_out  +  ΔE_stored          (interface work and heat)

:class:`InterfaceEnergyLedger` accumulates the signed net energy each participant
gains *from the interface*, plus whatever the coupling itself stores, and reports
the residual of that identity:

    residual = Σ_s net_s  +  ΔE_stored

For a conservative exchange the residual is zero to round-off; a non-conservative
mapping shows up immediately as ``mapping_loss = Σ emitted − Σ received``.

Why a ledger instead of a single number
---------------------------------------
Two questions must stay distinguishable:

* *is the exchange conservative?* -> ``mapping_loss`` (mapped quantity lost or
  created by the operator);
* *is the coupled budget closed?* -> ``closure_residual`` (the identity above).

Zero coupling is the negative control: no participant changes, storage does not
move, and every number stays exactly ``0.0`` — so the gate cannot be passed by
accumulating noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

__all__ = ["EnergyLedgerError", "EnergyReport", "InterfaceEnergyLedger"]


class EnergyLedgerError(ValueError):
    """Raised when a ledger entry is not physically meaningful."""


@dataclass
class EnergyReport:
    """Closure of the interface budget over the whole run."""

    participants: Dict[str, float]
    total_in: float
    total_out: float
    stored_delta: float
    mapping_loss: float
    closure_residual: float
    closure_rel: float
    energy_scale: float
    n_exchanges: int
    tolerance: float
    ok: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "participants": dict(self.participants),
            "total_in": self.total_in,
            "total_out": self.total_out,
            "stored_delta": self.stored_delta,
            "mapping_loss": self.mapping_loss,
            "closure_residual": self.closure_residual,
            "closure_rel": self.closure_rel,
            "energy_scale": self.energy_scale,
            "n_exchanges": self.n_exchanges,
            "tolerance": self.tolerance,
            "ok": self.ok,
        }


class InterfaceEnergyLedger:
    """Signed energy book for the participants of one coupling interface.

    Parameters
    ----------
    tolerance
        Admissible ``|closure_residual| / energy_scale``.
    energy_scale
        Fixed reference scale; when omitted the largest absolute participant
        total seen in the run is used (so the gate is scale-free).
    """

    def __init__(self, tolerance: float = 1e-6, energy_scale: Optional[float] = None, label: str = "interface"):
        self.tolerance = float(tolerance)
        self._fixed_scale = None if energy_scale is None else float(energy_scale)
        self.label = label
        self.net: Dict[str, float] = {}
        self.stored_delta: float = 0.0
        self.mapping_loss: float = 0.0
        self.n_exchanges: int = 0
        self.history: list[Dict[str, float]] = []

    # ------------------------------------------------------------------ record
    def record_net(self, participant: str, amount: float, time: float = 0.0) -> None:
        """Add ``amount`` (signed) to what ``participant`` gained from the interface."""
        amount = float(amount)
        if not np.isfinite(amount):
            raise EnergyLedgerError("energy entry must be finite")
        self.net[participant] = self.net.get(participant, 0.0) + amount
        self.history.append({"time": float(time), "participant": participant, "amount": amount})

    def exchange(
        self,
        emitter: str,
        receiver: str,
        emitted: float,
        received: Optional[float] = None,
        time: float = 0.0,
    ) -> None:
        """Record one directed exchange (``received`` defaults to ``emitted``)."""
        emitted = float(emitted)
        received = emitted if received is None else float(received)
        self.record_net(emitter, -emitted, time=time)
        self.record_net(receiver, received, time=time)
        self.mapping_loss += emitted - received
        self.n_exchanges += 1

    def record_storage(self, delta: float) -> None:
        """Change of energy stored *by the coupling itself* (interface potential, ...)."""
        delta = float(delta)
        if not np.isfinite(delta):
            raise EnergyLedgerError("storage delta must be finite")
        self.stored_delta += delta

    # ------------------------------------------------------------------ report
    @property
    def energy_scale(self) -> float:
        if self._fixed_scale is not None:
            return self._fixed_scale
        values = [abs(v) for v in self.net.values()] + [abs(self.stored_delta)]
        return max(values) if values and max(values) > 0.0 else 1.0

    def report(self) -> EnergyReport:
        net_sum = float(sum(self.net.values()))
        residual = net_sum + self.stored_delta
        positives = sum(v for v in self.net.values() if v > 0.0)
        negatives = -sum(v for v in self.net.values() if v < 0.0)
        scale = self.energy_scale
        rel = abs(residual) / scale if scale > 0.0 else abs(residual)
        return EnergyReport(
            participants=dict(self.net),
            total_in=float(positives),
            total_out=float(negatives),
            stored_delta=float(self.stored_delta),
            mapping_loss=float(self.mapping_loss),
            closure_residual=float(residual),
            closure_rel=float(rel),
            energy_scale=float(scale),
            n_exchanges=int(self.n_exchanges),
            tolerance=self.tolerance,
            ok=bool(rel <= self.tolerance and abs(self.mapping_loss) <= self.tolerance * scale),
        )

    def reset(self) -> None:
        self.net.clear()
        self.stored_delta = 0.0
        self.mapping_loss = 0.0
        self.n_exchanges = 0
        self.history.clear()