"""SIR-PC2 | I01: Seven-leg SIR ice-coupled full-chain integration.

LBM → FEM → DEM → rigid body motion → back to LBM full chain.

Seven legs:
  Leg-1: LBM fluid solver (flow field, hydrodynamic forces)
  Leg-2: FEM hull structural response (stress, deformation)
  Leg-3: DEM ice particle dynamics (contact, fracture)
  Leg-4: Rigid body 6-DOF ship motion
  Leg-5: Ice sheet added mass (F02/m709)
  Leg-6: Fluid-DEM bidirectional coupling (F06/m706)
  Leg-7: Geometric nonlinear FEM large rotation (F08/m712)
"""

__version__ = "0.1.0"
__mission__ = "m714"
__campaign__ = "SIR-PC2|I01"
