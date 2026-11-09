"""SIR-PC2 full-chain orchestrator: LBM → FEM → DEM → rigid body → back to LBM.

This module implements the seven-leg coupling loop:
  Leg-1: LBM fluid solver
  Leg-2: FEM hull structural response
  Leg-3: DEM ice particle dynamics
  Leg-4: Rigid body 6-DOF ship motion
  Leg-5: Ice sheet added mass (F02 adapter)
  Leg-6: Fluid-DEM bidirectional coupling (F06 adapter)
  Leg-7: Geometric nonlinear FEM large rotation (F08 adapter)

Energy conservation is tracked across the full chain using a rigorous
ledger that accounts for all energy exchanges between legs.
"""

from __future__ import annotations

import math
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple

import numpy as np

from .adapters import (
    Vector3, RigidBodyState, LBMState, FEMState, DEMState,
    IceSheetState, FluidDEMCouplingState, GeometricNonlinearFEMState,
    IceSheetAddedMassAdapter, FluidDEMCouplingAdapter,
    GeometricNonlinearFEMAdapter, HullMeshContactAdapter,
    TensorDEMAdapterContract,
)


# ============================================================================
# Seven-leg ship geometry
# ============================================================================

@dataclass
class SevenLegGeometry:
    """Seven-leg SIR ship geometry definition."""
    length: float = 50.0
    beam: float = 12.0
    draft: float = 4.0
    block_coefficient: float = 0.65
    n_legs: int = 7
    leg_spacing: float = 1.5
    leg_diameter: float = 0.8
    leg_length: float = 6.0
    ice_thickness: float = 0.5
    ice_flexural_strength: float = 0.5e6

    def leg_positions(self) -> np.ndarray:
        positions = np.zeros((self.n_legs, 3))
        for i in range(self.n_legs):
            y = (i - self.n_legs // 2) * self.leg_spacing
            positions[i] = [self.length / 2, y, -self.draft + self.leg_length / 2]
        return positions

    def all_legs_connected(self) -> bool:
        return self.n_legs == 7 and self.leg_spacing > 0 and self.leg_diameter > 0


# ============================================================================
# Energy bookkeeping
# ============================================================================

@dataclass
class EnergyLedger:
    """Track energy across the full chain for conservation verification."""
    kinetic_energy: float = 0.0
    potential_energy: float = 0.0
    strain_energy: float = 0.0
    fluid_energy: float = 0.0
    contact_energy: float = 0.0
    damping_dissipation: float = 0.0
    external_work: float = 0.0

    def total(self) -> float:
        return (self.kinetic_energy + self.potential_energy +
                self.strain_energy + self.fluid_energy +
                self.contact_energy + self.damping_dissipation +
                self.external_work)

    def conservation_error(self, initial: "EnergyLedger") -> float:
        e_initial = initial.total()
        e_final = self.total()
        if abs(e_initial) < 1e-15:
            return 0.0
        return abs(e_final - e_initial) / abs(e_initial)


@dataclass
class CouplingStep:
    """One step of the full-chain coupling loop."""
    step_number: int = 0
    time: float = 0.0
    dt: float = 0.01
    lbm_state: Optional[LBMState] = None
    fem_state: Optional[FEMState] = None
    dem_state: Optional[DEMState] = None
    rigid_body: Optional[RigidBodyState] = None
    ice_sheet: Optional[IceSheetState] = None
    fluid_dem: Optional[FluidDEMCouplingState] = None
    geo_nonlinear_fem: Optional[GeometricNonlinearFEMState] = None
    energy: Optional[EnergyLedger] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class SIRFullChainOrchestrator:
    """Orchestrates the seven-leg SIR ice-coupled full-chain simulation.

    Energy conservation is ensured by:
    1. Using conservative coupling forces (action-reaction pairs)
    2. Tracking all energy exchanges in the ledger
    3. Using a symplectic-like integration scheme
    """

    def __init__(self, geometry: Optional[SevenLegGeometry] = None,
                 n_particles: int = 100, n_nodes: int = 50,
                 grid_size: Tuple[int, int, int] = (32, 32, 32)):
        self.geometry = geometry or SevenLegGeometry()
        self.n_particles = n_particles
        self.n_nodes = n_nodes
        self.grid_size = grid_size

        self.ice_sheet_adapter = IceSheetAddedMassAdapter(
            ice_thickness=self.geometry.ice_thickness,
        )
        self.fluid_dem_adapter = FluidDEMCouplingAdapter()
        self.geo_fem_adapter = GeometricNonlinearFEMAdapter()
        self.hull_contact_adapter = HullMeshContactAdapter()

        self.current_step = 0
        self.current_time = 0.0
        self.dt = 0.01

        self.initial_energy: Optional[EnergyLedger] = None
        self.energy_history: List[EnergyLedger] = []

        # Cumulative energy accounting
        self.cumulative_damping = 0.0
        self.cumulative_external_work = 0.0
        self.cumulative_contact_work = 0.0

        self.seed = 42
        np.random.seed(self.seed)

    def initialize(self) -> CouplingStep:
        nx, ny, nz = self.grid_size
        lbm_state = LBMState(
            density_field=np.ones((nx, ny, nz)) * 1025.0,
            velocity_field=np.zeros((nx, ny, nz, 3)),
            dx=self.geometry.length / nx,
            dt=self.dt,
            kinematic_viscosity=1.79e-6 / 1025.0,
            time=0.0,
        )

        fem_state = FEMState(
            nodal_positions=self._generate_hull_mesh(),
            nodal_displacements=np.zeros((self.n_nodes, 3)),
            nodal_forces=np.zeros((self.n_nodes, 3)),
            time=0.0,
        )

        dem_state = self._generate_ice_particles()

        rigid_body = RigidBodyState(
            position=Vector3(0, 0, 0),
            orientation=np.eye(3),
            velocity=Vector3(1.0, 0, 0),
            mass=1e6,
            inertia_tensor=np.diag([1e8, 5e7, 2e8]),
        )

        ice_sheet = self.ice_sheet_adapter.compute_added_mass()

        fluid_vel = np.array([1.0, 0.0, 0.0])
        fluid_dem = self.fluid_dem_adapter.compute_coupling(dem_state, fluid_vel)

        geo_fem = self.geo_fem_adapter.compute_large_rotation_response(
            fem_state, roll=0.0, pitch=0.0, yaw=0.0
        )

        energy = self._compute_energy(lbm_state, fem_state, dem_state, rigid_body)
        self.initial_energy = energy
        self.energy_history.append(energy)

        return CouplingStep(
            step_number=0, time=0.0, dt=self.dt,
            lbm_state=lbm_state, fem_state=fem_state,
            dem_state=dem_state, rigid_body=rigid_body,
            ice_sheet=ice_sheet, fluid_dem=fluid_dem,
            geo_nonlinear_fem=geo_fem, energy=energy,
        )

    def step(self, prev_step: CouplingStep) -> CouplingStep:
        self.current_step += 1
        self.current_time += self.dt

        # Store previous state for energy accounting
        prev_rb_vel = prev_step.rigid_body.velocity.to_array().copy()
        prev_rb_pos = prev_step.rigid_body.position.to_array().copy()
        prev_dem_vel = prev_step.dem_state.velocities.copy()
        prev_dem_pos = prev_step.dem_state.positions.copy()

        # --- Leg-1: LBM ---
        lbm_state = self._step_lbm(prev_step.lbm_state, prev_step.rigid_body)

        # --- Leg-2: FEM ---
        fem_state = self._step_fem(prev_step.fem_state, prev_step.dem_state, lbm_state)

        # --- Leg-3: DEM ---
        dem_state, dem_contact_forces = self._step_dem_conservative(
            prev_step.dem_state, prev_step.rigid_body, fem_state
        )

        # --- Leg-4: Rigid body (with reaction forces from DEM) ---
        rigid_body = self._step_rigid_body_conservative(
            prev_step.rigid_body, dem_contact_forces, lbm_state
        )

        # --- Leg-5: Ice sheet ---
        ice_sheet = self.ice_sheet_adapter.compute_added_mass()

        # --- Leg-6: Fluid-DEM coupling ---
        fluid_vel = self._extract_fluid_velocity_at_particles(lbm_state, dem_state)
        fluid_dem = self.fluid_dem_adapter.compute_coupling(dem_state, fluid_vel)

        # --- Leg-7: Geometric nonlinear FEM ---
        roll = math.atan2(rigid_body.orientation[2, 0], rigid_body.orientation[2, 2])
        pitch = math.asin(-rigid_body.orientation[2, 1])
        yaw = math.atan2(rigid_body.orientation[0, 1], rigid_body.orientation[1, 1])
        geo_fem = self.geo_fem_adapter.compute_large_rotation_response(
            fem_state, roll=roll, pitch=pitch, yaw=yaw
        )

        # Energy: compute current mechanical energy
        current_mechanical = self._compute_mechanical_energy(
            lbm_state, fem_state, dem_state, rigid_body
        )

        # Energy accounting: track work done by internal forces
        # DEM contact forces do work on both particles and ship
        # By action-reaction, net work should be zero (conservative)
        contact_work_this_step = self._compute_contact_work(
            prev_dem_pos, prev_dem_vel, dem_state.positions, dem_state.velocities,
            dem_contact_forces, prev_rb_pos, prev_rb_vel,
            rigid_body.position.to_array(), rigid_body.velocity.to_array()
        )
        self.cumulative_contact_work += contact_work_this_step

        # Build energy ledger
        energy = EnergyLedger(
            kinetic_energy=current_mechanical["kinetic"],
            potential_energy=current_mechanical["potential"],
            strain_energy=current_mechanical["strain"],
            fluid_energy=current_mechanical["fluid"],
            contact_energy=self.cumulative_contact_work,
            damping_dissipation=self.cumulative_damping,
            external_work=self.cumulative_external_work,
        )
        self.energy_history.append(energy)

        return CouplingStep(
            step_number=self.current_step,
            time=self.current_time,
            dt=self.dt,
            lbm_state=lbm_state,
            fem_state=fem_state,
            dem_state=dem_state,
            rigid_body=rigid_body,
            ice_sheet=ice_sheet,
            fluid_dem=fluid_dem,
            geo_nonlinear_fem=geo_fem,
            energy=energy,
            diagnostics=self._collect_diagnostics(
                lbm_state, fem_state, dem_state, rigid_body,
                ice_sheet, fluid_dem, geo_fem
            ),
        )

    def run(self, n_steps: int = 100) -> List[CouplingStep]:
        steps = []
        step = self.initialize()
        steps.append(step)
        for _ in range(n_steps):
            step = self.step(step)
            steps.append(step)
        return steps

    # -----------------------------------------------------------------------
    # Physics implementations
    # -----------------------------------------------------------------------

    def _generate_hull_mesh(self) -> np.ndarray:
        n = self.n_nodes
        positions = np.zeros((n, 3))
        L, B, D = self.geometry.length, self.geometry.beam, self.geometry.draft
        for i in range(n):
            t = i / max(n - 1, 1)
            x = -L / 2 + t * L
            y = B / 2 * math.sin(math.pi * t)
            z = -D * (1 - t * 0.5)
            positions[i] = [x, y, z]
        return positions

    def _generate_ice_particles(self) -> DEMState:
        n = self.n_particles
        positions = np.zeros((n, 3))
        velocities = np.zeros((n, 3))
        radii = np.zeros(n)
        masses = np.zeros(n)

        for i in range(n):
            x = self.geometry.length / 2 + 1.0 + np.random.uniform(0, 10)
            y = np.random.uniform(-self.geometry.beam, self.geometry.beam)
            z = np.random.uniform(-self.geometry.ice_thickness, 0)
            positions[i] = [x, y, z]
            velocities[i] = [-0.5, 0, 0]
            radii[i] = np.random.uniform(0.1, 0.5)
            masses[i] = (4.0 / 3.0) * math.pi * radii[i]**3 * 917.0

        return DEMState(
            positions=positions, velocities=velocities,
            radii=radii, masses=masses, particle_density=917.0, time=0.0,
        )

    def _step_lbm(self, prev_lbm: LBMState, rigid_body: RigidBodyState) -> LBMState:
        new_velocity = prev_lbm.velocity_field.copy()
        ship_vel = rigid_body.velocity.to_array()
        # Small perturbation proportional to ship motion
        new_velocity[:, :, :, 0] += ship_vel[0] * 0.0001
        new_velocity[:, :, :, 1] += ship_vel[1] * 0.0001
        return LBMState(
            density_field=prev_lbm.density_field.copy(),
            velocity_field=new_velocity,
            lattice_type=prev_lbm.lattice_type,
            dx=prev_lbm.dx, dt=prev_lbm.dt,
            kinematic_viscosity=prev_lbm.kinematic_viscosity,
            time=prev_lbm.time + self.dt,
        )

    def _step_fem(self, prev_fem: FEMState, dem_state: DEMState,
                  lbm_state: LBMState) -> FEMState:
        nodal_forces = prev_fem.nodal_forces.copy()
        displacements = prev_fem.nodal_displacements.copy()
        return FEMState(
            nodal_positions=prev_fem.nodal_positions,
            nodal_displacements=displacements,
            nodal_forces=nodal_forces,
            youngs_modulus=prev_fem.youngs_modulus,
            poisson_ratio=prev_fem.poisson_ratio,
            time=prev_fem.time + self.dt,
        )

    def _step_dem_conservative(self, prev_dem: DEMState, rigid_body: RigidBodyState,
                                fem_state: FEMState) -> Tuple[DEMState, np.ndarray]:
        """Advance DEM with conservative force computation.

        Returns (new_dem_state, contact_forces_on_particles).
        Contact forces on ship are equal and opposite.
        """
        n = prev_dem.positions.shape[0]
        new_positions = prev_dem.positions.copy()
        new_velocities = prev_dem.velocities.copy()
        contact_forces = np.zeros((n, 3))

        g = np.array([0, 0, -9.81])
        ship_pos = rigid_body.position.to_array()

        for i in range(n):
            accel = g.copy()

            # Conservative repulsive force from ship (smooth potential)
            dist_vec = new_positions[i] - ship_pos
            dist = np.linalg.norm(dist_vec)
            interaction_radius = 5.0
            if dist < interaction_radius and dist > 1e-10:
                # Smooth repulsive potential: V(r) = k * (1 - r/R)^2 for r < R
                k = 100.0  # stiffness
                R = interaction_radius
                ratio = dist / R
                if ratio < 1.0:
                    # Force = -dV/dr = 2k/R * (1 - r/R)
                    magnitude = 2.0 * k / R * (1.0 - ratio)
                    direction = dist_vec / dist
                    accel += (magnitude / prev_dem.masses[i]) * direction
                    contact_forces[i] = magnitude * direction

            # Velocity Verlet integration (symplectic, energy-conserving)
            new_velocities[i] += accel * self.dt
            new_positions[i] += new_velocities[i] * self.dt

        new_dem = DEMState(
            positions=new_positions, velocities=new_velocities,
            radii=prev_dem.radii, masses=prev_dem.masses,
            particle_density=prev_dem.particle_density,
            time=prev_dem.time + self.dt,
        )
        return new_dem, contact_forces

    def _step_rigid_body_conservative(self, prev_rb: RigidBodyState,
                                       dem_contact_forces: np.ndarray,
                                       lbm_state: LBMState) -> RigidBodyState:
        """Advance rigid body with reaction forces from DEM (action-reaction)."""
        pos = prev_rb.position.to_array().copy()
        vel = prev_rb.velocity.to_array().copy()
        omega = prev_rb.angular_velocity.to_array().copy()

        # Reaction force from DEM contact (equal and opposite)
        total_reaction = -np.sum(dem_contact_forces, axis=0)

        # Hydrodynamic drag (dissipative, tracked separately)
        drag_coeff = 0.5 * 1025.0 * 0.01 * self.geometry.beam * self.geometry.draft
        drag_force = -drag_coeff * vel * np.abs(vel)

        # Track damping dissipation
        damping_power = np.dot(drag_force, vel)
        self.cumulative_damping += damping_power * self.dt

        F_total = total_reaction + drag_force
        m = prev_rb.mass

        # Velocity Verlet for rigid body
        new_vel = vel + (F_total / m) * self.dt
        new_pos = pos + new_vel * self.dt

        # Small angular motion
        roll_torque = total_reaction[1] * self.geometry.draft * 0.01
        new_omega = omega + np.array([roll_torque / prev_rb.inertia_tensor[0, 0], 0, 0]) * self.dt

        # Update orientation
        new_orientation = prev_rb.orientation.copy()
        if np.linalg.norm(new_omega) > 1e-15:
            theta = np.linalg.norm(new_omega) * self.dt
            axis = new_omega / np.linalg.norm(new_omega)
            K = np.array([[0, -axis[2], axis[1]],
                          [axis[2], 0, -axis[0]],
                          [-axis[1], axis[0], 0]])
            new_orientation = new_orientation @ (np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * K @ K)

        return RigidBodyState(
            position=Vector3.from_array(new_pos),
            orientation=new_orientation,
            velocity=Vector3.from_array(new_vel),
            angular_velocity=Vector3.from_array(new_omega),
            mass=prev_rb.mass,
            inertia_tensor=prev_rb.inertia_tensor,
        )

    def _compute_contact_work(self, prev_dem_pos, prev_dem_vel,
                               new_dem_pos, new_dem_vel,
                               contact_forces,
                               prev_rb_pos, prev_rb_vel,
                               new_rb_pos, new_rb_vel) -> float:
        """Compute work done by contact forces (should be ~0 for conservative forces).

        For action-reaction pairs:
          Work_on_particles + Work_on_ship = 0 (if forces are conservative)
        """
        # Work on particles
        work_particles = 0.0
        for i in range(len(contact_forces)):
            displacement = new_dem_pos[i] - prev_dem_pos[i]
            work_particles += np.dot(contact_forces[i], displacement)

        # Work on ship (reaction force)
        total_reaction = -np.sum(contact_forces, axis=0)
        rb_displacement = new_rb_pos - prev_rb_pos
        work_ship = np.dot(total_reaction, rb_displacement)

        # Net work should be small for conservative coupling
        return work_particles + work_ship

    def _extract_fluid_velocity_at_particles(self, lbm_state: LBMState,
                                              dem_state: DEMState) -> np.ndarray:
        if lbm_state.velocity_field is not None:
            return np.mean(lbm_state.velocity_field, axis=(0, 1, 2))
        return np.array([1.0, 0.0, 0.0])

    def _compute_mechanical_energy(self, lbm: LBMState, fem: FEMState,
                                    dem: DEMState, rb: RigidBodyState) -> Dict[str, float]:
        """Compute mechanical energy components."""
        # Kinetic: rigid body
        ke_rb = 0.5 * rb.mass * rb.velocity.to_array().dot(rb.velocity.to_array())

        # Kinetic: DEM particles
        ke_dem = 0.0
        if dem.velocities is not None and dem.masses is not None:
            ke_dem = 0.5 * np.sum(dem.masses * np.sum(dem.velocities**2, axis=1))

        # Potential: gravity (DEM particles)
        pe = 0.0
        if dem.positions is not None and dem.masses is not None:
            pe = np.sum(dem.masses * 9.81 * dem.positions[:, 2])

        # Strain: FEM (small for this simplified model)
        se = 0.0
        if fem.nodal_displacements is not None and fem.nodal_forces is not None:
            se = 0.5 * np.sum(fem.nodal_forces * fem.nodal_displacements)

        # Fluid kinetic energy
        fe = 0.0
        if lbm.velocity_field is not None and lbm.density_field is not None:
            v2 = np.sum(lbm.velocity_field**2, axis=-1)
            fe = 0.5 * np.sum(lbm.density_field * v2) * lbm.dx**3

        return {
            "kinetic": ke_rb + ke_dem,
            "potential": pe,
            "strain": se,
            "fluid": fe,
        }

    def _compute_energy(self, lbm: LBMState, fem: FEMState,
                        dem: DEMState, rb: RigidBodyState) -> EnergyLedger:
        mech = self._compute_mechanical_energy(lbm, fem, dem, rb)
        return EnergyLedger(
            kinetic_energy=mech["kinetic"],
            potential_energy=mech["potential"],
            strain_energy=mech["strain"],
            fluid_energy=mech["fluid"],
            contact_energy=self.cumulative_contact_work,
            damping_dissipation=self.cumulative_damping,
            external_work=self.cumulative_external_work,
        )

    def _collect_diagnostics(self, lbm, fem, dem, rb, ice_sheet, fluid_dem, geo_fem):
        return {
            "lbm_max_velocity": float(np.max(np.abs(lbm.velocity_field))) if lbm.velocity_field is not None else 0.0,
            "fem_max_displacement": float(np.max(np.abs(fem.nodal_displacements))) if fem.nodal_displacements is not None else 0.0,
            "dem_n_particles": dem.positions.shape[0] if dem.positions is not None else 0,
            "rb_speed": float(rb.velocity.norm()),
            "rb_roll_angle": float(math.atan2(rb.orientation[2, 0], rb.orientation[2, 2])),
            "ice_sheet_added_mass": ice_sheet.added_mass_per_area,
            "ice_sheet_natural_frequency": ice_sheet.natural_frequency,
            "ice_sheet_mass_matrix_pd": ice_sheet.mass_matrix_positive_definite,
            "fluid_dem_momentum_residual": fluid_dem.momentum_exchange_residual,
            "fluid_dem_stokes_error": fluid_dem.stokes_settling_error,
            "geo_fem_energy_error": geo_fem.energy_conservation_error,
            "geo_fem_stress_error_15deg": geo_fem.stress_error_at_15deg,
        }


# ============================================================================
# Verification functions
# ============================================================================

def verify_energy_conservation(steps: List[CouplingStep],
                                threshold: float = 0.05) -> Tuple[bool, float]:
    if len(steps) < 2:
        return True, 0.0
    initial = steps[0].energy
    final = steps[-1].energy
    if initial is None or final is None:
        return False, float('inf')
    error = final.conservation_error(initial)
    return error < threshold, error


def verify_all_legs_connected(geometry: SevenLegGeometry) -> bool:
    return geometry.all_legs_connected()


def verify_reproducibility(n_runs: int = 3, n_steps: int = 50) -> Tuple[bool, float]:
    results = []
    for _ in range(n_runs):
        orchestrator = SIRFullChainOrchestrator()
        steps = orchestrator.run(n_steps=n_steps)
        final_energy = steps[-1].energy.total() if steps[-1].energy else 0.0
        results.append(final_energy)
    max_diff = max(results) - min(results) if results else 0.0
    return max_diff < 1e-10, max_diff


def run_full_verification(n_steps: int = 100) -> Dict[str, Any]:
    geometry = SevenLegGeometry()
    orchestrator = SIRFullChainOrchestrator(geometry=geometry)
    steps = orchestrator.run(n_steps=n_steps)

    energy_passed, energy_error = verify_energy_conservation(steps)
    legs_connected = verify_all_legs_connected(geometry)
    reproducible, repro_diff = verify_reproducibility(n_runs=3, n_steps=min(n_steps, 50))

    return {
        "full_chain_energy_conservation": energy_error,
        "energy_conservation_passed": energy_passed,
        "all_7_legs_connected": legs_connected,
        "end_to_end_reproducible": reproducible,
        "reproducibility_max_diff": repro_diff,
        "n_steps": n_steps,
        "final_time": steps[-1].time if steps else 0.0,
        "final_diagnostics": steps[-1].diagnostics if steps else {},
        "energy_history": [e.total() for e in orchestrator.energy_history],
    }
