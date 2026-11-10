"""Coupling clock synchronisation for heterogeneous multi-solver runs (domain-neutral).

* :mod:`tensorphysics.clock_sync.clock`     —— master/sub clocks, coupling points,
  sub-cycling with exact landing and audited skew.
* :mod:`tensorphysics.clock_sync.subcycling` —— the two-subsystem plant used to
  prove the schedule, plus the reference comparison.
* :mod:`tensorphysics.clock_sync.energy`    —— interface energy ledger
  (``Σ E_in = Σ E_out + ΔE_stored``) with a zero-coupling negative control.
* :mod:`tensorphysics.clock_sync.lyapunov`  —— Benettin estimate of the coupled
  spectrum, verified against the exact spectrum of a linear map.
* :mod:`tensorphysics.clock_sync.manifest`  —— the ``coupling_clock`` RunManifest
  block (>= 4 audited fields).

Relationship to the rest of the platform
----------------------------------------
``tools.coupling.clock`` declares the *contract* between a master and a sub
solver (four exchange semantics, fail-closed); ``src.coupling.clock_sync`` offers
conservative/optimistic *synchronisation modes*.  This package adds the missing
piece: an executable schedule with sub-cycling, an explicit truncation audit, a
coupling-point time ledger, and a stability test of the resulting coupled map.

No physical meaning is attached anywhere: times are seconds, states are numbers.
"""

from .clock import ClockError, ClockSpec, CouplingClock, CouplingPointRecord, CouplingTrace
from .energy import EnergyLedgerError, EnergyReport, InterfaceEnergyLedger
from .lyapunov import LyapunovError, LyapunovResult, linear_map_probe, lyapunov_spectrum
from .manifest import (
    OPTIONAL_COUPLING_CLOCK_FIELDS,
    REQUIRED_COUPLING_CLOCK_FIELDS,
    coupling_clock_block,
    validate_coupling_clock,
)
from .subcycling import (
    CoupledOscillatorConfig,
    CoupledRun,
    compare_series,
    coupled_window_map,
    simulate_coupled_oscillators,
    substep_split,
)

__all__ = [
    "ClockError",
    "ClockSpec",
    "CouplingClock",
    "CouplingPointRecord",
    "CouplingTrace",
    "EnergyLedgerError",
    "EnergyReport",
    "InterfaceEnergyLedger",
    "LyapunovError",
    "LyapunovResult",
    "lyapunov_spectrum",
    "linear_map_probe",
    "REQUIRED_COUPLING_CLOCK_FIELDS",
    "OPTIONAL_COUPLING_CLOCK_FIELDS",
    "coupling_clock_block",
    "validate_coupling_clock",
    "CoupledOscillatorConfig",
    "CoupledRun",
    "simulate_coupled_oscillators",
    "compare_series",
    "coupled_window_map",
    "substep_split",
]