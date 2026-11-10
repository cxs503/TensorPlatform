"""Multi-solver coupling with energy-conservation verification.

This package matures the TensorPlatform multi-solver coupling mechanism.  It
provides three verifiable faces, each with an analytic ground truth (no
empirical dataset is fabricated):

* **Cross-solver energy-flux conservation** -- in the canonical ``+x`` frame the
  flux computed by solver ``A`` at the shared interface must equal the flux that
  solver ``B`` actually consumes there.  The relative mismatch is driven below
  ``1e-6`` and a deliberate 1 % transfer error is shown to be detected.
* **Interface-contract standardization** -- a single
  :class:`~.contracts.InterfaceContract` schema (field names, SI units, sign
  convention, per-solver aliases) is registered for ``>= 3`` solver pairs and is
  validated live against the states the solvers really produce; negative
  controls prove the validator rejects missing fields, wrong units and the
  own-outward sign convention.
* **Coupling-iteration convergence** -- a Dirichlet--Neumann (Schwarz) iteration
  with Aitken dynamic relaxation drives the interface residual below ``1e-8``.

The solvers are genuine numerical methods (finite volume, finite element,
boundary element) and every result is checked against a closed-form solution, so
accuracy is provable to discretization order without any empirical anchor.

SI units throughout.
"""
from __future__ import annotations

from .contracts import (
    CONTRACT_REGISTRY,
    ContractReport,
    FieldSpec,
    InterfaceContract,
    InterfaceState,
    audit_coverage,
    get_contract,
    list_pairs,
    load_contracts,
    register_contract,
)
from .coupling import CouplingResult, DirichletNeumannCoupler, coupled_solution_profile
from .energy import (
    coupled_energy_report,
    dirichlet_energy,
    flux_closure_error,
    global_energy_balance,
)

__all__ = [
    "CONTRACT_REGISTRY",
    "ContractReport",
    "CouplingResult",
    "DirichletNeumannCoupler",
    "FieldSpec",
    "InterfaceContract",
    "InterfaceState",
    "audit_coverage",
    "coupled_energy_report",
    "coupled_solution_profile",
    "dirichlet_energy",
    "flux_closure_error",
    "get_contract",
    "global_energy_balance",
    "list_pairs",
    "load_contracts",
    "register_contract",
]