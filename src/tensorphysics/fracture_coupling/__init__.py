"""
FEM cohesive → DEM fracture fragment handoff module.

Physical background:
    In ice-zone navigation simulations, structural components may undergo
    progressive fracture modeled via cohesive zone elements (FEM). When a
    cohesive element fails (reaches critical energy release rate G_c via
    BK law or similar), the two adjacent surfaces separate, generating
    fracture fragments. These fragments must be seamlessly transferred to
    a Discrete Element Method (DEM) solver as free granular particles for
    subsequent contact/fragmentation simulation.

    This module implements the handoff with strict conservation:
    - Mass conservation: Σ m_particle = Σ m_fragment (error < 1e-10 kg)
    - Momentum conservation: Σ (m*v)_particle = Σ (m*v)_fragment (error < 1e-8 kg·m/s)
    - No orphan particles: every fragment maps to exactly one DEM particle

Reference upstream:
    - m668 (F03): FEM cohesive element with BK mixed-mode law
    - m671 (V04): Mixed-mode cohesive validation (BK law, Gc error < 1e-6)
"""

from .coupling import (
    CohesiveFragment,
    DEMParticle,
    FractureCouplingTransfer,
    TransferResult,
    transfer_fragments_to_dem,
)

__all__ = [
    "CohesiveFragment",
    "DEMParticle",
    "FractureCouplingTransfer",
    "TransferResult",
    "transfer_fragments_to_dem",
]
