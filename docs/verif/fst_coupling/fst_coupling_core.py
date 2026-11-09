"""
Fluid-Structure-Thermal (FST) Three-Field Coupling Verification Core
=====================================================================

Implements simplified 1D models for verifying partitioned vs monolithic
coupling strategies in conjugate heat transfer with fluid-structure interaction.

Physics:
  - Flow: 1D channel flow (Poiseuille) with temperature-dependent viscosity
  - Thermal: 1D conjugate heat transfer (fluid + solid domains)
  - Structure: 1D thermal expansion of channel walls
  - Coupling: Partitioned (Gauss-Seidel) and Monolithic (Newton-Raphson)

Reference:
  - Dittus-Boelter: Nu = 0.023 Re^0.8 Pr^n (n=0.4 heating, 0.3 cooling)
  - Biot number: Bi = h*L/k_solid
  - Thermal stress: sigma_th = E*alpha*DeltaT / (1-nu)

Author: agent/m8062 FST coupling verification
Date: 2026-11-09
"""

import numpy as np
from scipy.optimize import fsolve
from typing import Tuple, Dict, List, Optional
import warnings


# ============================================================================
# Physical Constants & Material Properties
# ============================================================================

class FluidProperties:
    """Temperature-dependent fluid properties (water-like)."""
    
    def __init__(self, T_ref: float = 300.0):
        self.T_ref = T_ref
        self.rho_ref = 998.0       # kg/m^3
        self.mu_ref = 1.0e-3       # Pa·s (dynamic viscosity at T_ref)
        self.k_ref = 0.6           # W/(m·K) (thermal conductivity)
        self.cp_ref = 4182.0       # J/(kg·K) (specific heat)
        self.beta_ref = 2.07e-4    # 1/K (thermal expansion coefficient)
        
    def viscosity(self, T: float) -> float:
        """Temperature-dependent viscosity (Arrhenius-like)."""
        return self.mu_ref * np.exp(-0.02 * (T - self.T_ref))
    
    def density(self, T: float) -> float:
        """Temperature-dependent density (Boussinesq)."""
        return self.rho_ref * (1 - self.beta_ref * (T - self.T_ref))
    
    def conductivity(self, T: float) -> float:
        """Thermal conductivity (weak T dependence)."""
        return self.k_ref * (1 + 0.001 * (T - self.T_ref))
    
    def prandtl(self, T: float) -> float:
        """Prandtl number."""
        return self.cp_ref * self.viscosity(T) / self.conductivity(T)


class SolidProperties:
    """Solid (channel wall) properties."""
    
    def __init__(self):
        self.E = 200e9             # Pa (Young's modulus, steel-like)
        self.nu = 0.3              # Poisson's ratio
        self.alpha = 1.2e-5        # 1/K (thermal expansion coefficient)
        self.k = 50.0              # W/(m·K) (thermal conductivity)
        self.rho = 7800.0          # kg/m^3
        self.cp = 500.0            # J/(kg·K)


# ============================================================================
# Flow Solver (1D Channel)
# ============================================================================

class ChannelFlowSolver:
    """
    1D channel flow with temperature-dependent viscosity.
    
    Geometry: channel of height H(x), length L
    Flow: fully-developed Poiseuille with variable viscosity
    
    Velocity profile: u(y) = (1/(2*mu)) * (-dp/dx) * (H^2/4 - y^2)
    Max velocity: u_max = H^2/(8*mu) * (-dp/dx)
    Mean velocity: U = H^2/(12*mu) * (-dp/dx)
    """
    
    def __init__(self, fluid: FluidProperties, L: float = 1.0, H0: float = 0.01):
        self.fluid = fluid
        self.L = L
        self.H0 = H0  # reference channel height
        
    def solve_poiseuille(self, dp_dx: float, H: float, T_avg: float) -> Dict:
        """
        Solve Poiseuille flow for given pressure gradient, height, temperature.
        
        Returns: dict with velocity profile, flow rate, Re, friction factor
        """
        mu = self.fluid.viscosity(T_avg)
        
        # Parabolic velocity profile: u(y) = (-dp_dx/(2*mu)) * (H^2/4 - y^2)
        N = 50
        y = np.linspace(-H/2, H/2, N)
        u = (-dp_dx / (2 * mu)) * (H**2/4 - y**2)
        
        # Mean velocity
        U_mean = -dp_dx * H**2 / (12 * mu)
        
        # Volume flow rate (per unit width)
        Q = U_mean * H
        
        # Reynolds number
        Re = self.fluid.rho_ref * U_mean * H / mu
        
        # Friction factor (f = 96/Re for parallel plates)
        f = 96.0 / Re if Re > 0 else 0.0
        
        return {
            'y': y,
            'u': u,
            'U_mean': U_mean,
            'Q': Q,
            'Re': Re,
            'f': f,
            'mu': mu
        }
    
    def pressure_gradient_from_flow_rate(self, Q: float, H: float, T_avg: float) -> float:
        """Compute pressure gradient for desired flow rate."""
        mu = self.fluid.viscosity(T_avg)
        dp_dx = -12 * mu * Q / H**3
        return dp_dx


# ============================================================================
# Thermal Solver (1D Conjugate Heat Transfer)
# ============================================================================

class ThermalSolver:
    """
    1D conjugate heat transfer: fluid domain + solid wall.
    
    Solves: d/dx(k dT/dx) = 0 (steady conduction)
    With convective BC on fluid side: -k dT/dx = h(T - T_fluid)
    """
    
    def __init__(self, fluid: FluidProperties, solid: SolidProperties):
        self.fluid = fluid
        self.solid = solid
        
    def solve_conduction(self, T_left: float, T_right: float, 
                         k: float, L: float, N: int = 50) -> Dict:
        """Solve 1D steady conduction: d²T/dx² = 0."""
        x = np.linspace(0, L, N)
        T = T_left + (T_right - T_left) * x / L
        q = -k * (T_right - T_left) / L  # heat flux (constant)
        return {'x': x, 'T': T, 'q': q}
    
    def compute_heat_transfer_coefficient(self, Re: float, Pr: float, 
                                          D_h: float) -> float:
        """
        Dittus-Boelter correlation for turbulent flow.
        Nu = 0.023 Re^0.8 Pr^n
        h = Nu * k / D_h
        """
        if Re < 2300:
            # Laminar: Nu = 4.36 (constant heat flux) or 3.66 (constant wall temp)
            Nu = 4.36
        else:
            # Turbulent: Dittus-Boelter
            Nu = 0.023 * Re**0.8 * Pr**0.4
        
        h = Nu * self.fluid.k_ref / D_h
        return h
    
    def conjugate_ht(self, T_fluid: float, T_solid_outer: float,
                     h: float, t_wall: float) -> Dict:
        """
        Solve conjugate heat transfer: fluid → wall → outer surface.
        
        Returns interface temperature and heat flux.
        """
        # Thermal resistance network
        R_conv = 1.0 / h                    # convective resistance
        R_cond = t_wall / self.solid.k      # conductive resistance
        R_total = R_conv + R_cond
        
        # Heat flux
        q = (T_fluid - T_solid_outer) / R_total
        
        # Interface temperature (fluid-wall interface)
        T_interface = T_fluid - q * R_conv
        
        return {
            'q': q,
            'T_interface': T_interface,
            'R_conv': R_conv,
            'R_cond': R_cond,
            'R_total': R_total
        }


# ============================================================================
# Structural Solver (1D Thermal Stress)
# ============================================================================

class StructuralSolver:
    """
    1D thermal stress in constrained plate.
    
    Thermal strain: eps_th = alpha * DeltaT
    Thermal stress (constrained): sigma_th = E * alpha * DeltaT / (1 - nu)
    """
    
    def __init__(self, solid: SolidProperties):
        self.solid = solid
        
    def thermal_expansion(self, DeltaT: float, L0: float) -> Dict:
        """Compute thermal expansion (free expansion)."""
        eps_th = self.solid.alpha * DeltaT
        dL = eps_th * L0
        L_new = L0 * (1 + eps_th)
        return {
            'eps_th': eps_th,
            'dL': dL,
            'L_new': L_new
        }
    
    def thermal_stress(self, DeltaT: float, constrained: bool = True) -> Dict:
        """
        Compute thermal stress.
        
        If constrained: sigma = E * alpha * DeltaT / (1 - nu)
        If free: sigma = 0
        """
        if constrained:
            sigma = self.solid.E * self.solid.alpha * DeltaT / (1 - self.solid.nu)
        else:
            sigma = 0.0
        
        eps_mech = -self.solid.alpha * DeltaT if constrained else 0.0
        
        return {
            'sigma_th': sigma,
            'eps_mech': eps_mech,
            'DeltaT': DeltaT
        }
    
    def channel_deformation(self, DeltaT: float, H0: float) -> float:
        """
        Compute channel height change due to thermal expansion of walls.
        
        Both walls expand, so delta_H = 2 * alpha * DeltaT * (H0/2)
        """
        dH = 2 * self.solid.alpha * DeltaT * (H0 / 2)
        return dH


# ============================================================================
# Coupling Strategies
# ============================================================================

class PartitionedCoupling:
    """
    Partitioned (sequential) coupling using Gauss-Seidel iteration.
    
    Coupling sequence:
    1. Flow → Thermal: compute h from Re, Pr
    2. Thermal → Structure: compute DeltaT, thermal stress
    3. Structure → Flow: update geometry, recompute flow
    4. Iterate until convergence
    """
    
    def __init__(self, flow: ChannelFlowSolver, thermal: ThermalSolver,
                 struct: StructuralSolver):
        self.flow = flow
        self.thermal = thermal
        self.struct = struct
        
    def solve(self, Q_in: float, T_fluid_in: float, T_solid_outer: float,
              t_wall: float, max_iter: int = 50, tol: float = 1e-6) -> Dict:
        """
        Solve coupled FST problem with partitioned approach.
        
        Args:
            Q_in: inlet flow rate (m^2/s per unit width)
            T_fluid_in: fluid inlet temperature (K)
            T_solid_outer: outer wall temperature (K)
            t_wall: wall thickness (m)
            max_iter: maximum iterations
            tol: convergence tolerance
            
        Returns:
            Dict with converged solution and iteration history
        """
        H = self.flow.H0
        T_avg = T_fluid_in
        iteration_history = []
        
        for iteration in range(max_iter):
            # Store previous state
            H_old = H
            T_old = T_avg
            
            # Step 1: Solve flow
            dp_dx = self.flow.pressure_gradient_from_flow_rate(Q_in, H, T_avg)
            flow_sol = self.flow.solve_poiseuille(dp_dx, H, T_avg)
            Re = flow_sol['Re']
            
            # Step 2: Compute heat transfer coefficient
            Pr = self.flow.fluid.prandtl(T_avg)
            D_h = 2 * H  # hydraulic diameter for parallel plates
            h = self.thermal.compute_heat_transfer_coefficient(Re, Pr, D_h)
            
            # Step 3: Solve conjugate heat transfer
            cht_sol = self.thermal.conjugate_ht(T_fluid_in, T_solid_outer, h, t_wall)
            T_interface = cht_sol['T_interface']
            q = cht_sol['q']
            
            # Update fluid average temperature (simple mixing model)
            T_avg = 0.5 * (T_fluid_in + T_interface)
            
            # Step 4: Compute thermal expansion
            DeltaT = T_avg - self.flow.fluid.T_ref
            dH = self.struct.channel_deformation(DeltaT, self.flow.H0)
            H_new = self.flow.H0 + dH
            
            # Check convergence
            rel_change_H = abs(H_new - H_old) / self.flow.H0
            rel_change_T = abs(T_avg - T_old) / max(abs(T_old), 1.0)
            
            iteration_history.append({
                'iter': iteration + 1,
                'H': H_new,
                'T_avg': T_avg,
                'h': h,
                'Re': Re,
                'q': q,
                'rel_change_H': rel_change_H,
                'rel_change_T': rel_change_T
            })
            
            H = H_new
            
            if rel_change_H < tol and rel_change_T < tol:
                break
        
        # Final solution
        dp_dx = self.flow.pressure_gradient_from_flow_rate(Q_in, H, T_avg)
        flow_sol = self.flow.solve_poiseuille(dp_dx, H, T_avg)
        stress_sol = self.struct.thermal_stress(DeltaT, constrained=True)
        
        return {
            'converged': iteration < max_iter - 1,
            'iterations': len(iteration_history),
            'history': iteration_history,
            'H_final': H,
            'T_avg_final': T_avg,
            'flow': flow_sol,
            'stress': stress_sol,
            'h_final': h,
            'q_final': q
        }


class MonolithicCoupling:
    """
    Monolithic (fully coupled) approach using Newton-Raphson.
    
    Solves the coupled system simultaneously:
    R_flow(Q, H, T) = 0
    R_thermal(T, H, Q) = 0
    R_struct(H, T) = 0
    
    Jacobian contains all cross-coupling terms.
    """
    
    def __init__(self, flow: ChannelFlowSolver, thermal: ThermalSolver,
                 struct: StructuralSolver):
        self.flow = flow
        self.thermal = thermal
        self.struct = struct
        
    def residual_system(self, x: np.ndarray, Q_in: float, 
                       T_fluid_in: float, T_solid_outer: float,
                       t_wall: float) -> np.ndarray:
        """
        Compute residual vector for coupled system.
        
        x = [H, T_avg]
        """
        H, T_avg = x
        
        # Flow residual: Q_computed - Q_target = 0
        dp_dx = self.flow.pressure_gradient_from_flow_rate(Q_in, H, T_avg)
        flow_sol = self.flow.solve_poiseuille(dp_dx, H, T_avg)
        R_flow = flow_sol['Q'] - Q_in
        
        # Thermal residual: energy balance
        Re = flow_sol['Re']
        Pr = self.flow.fluid.prandtl(T_avg)
        D_h = 2 * H
        h = self.thermal.compute_heat_transfer_coefficient(Re, Pr, D_h)
        cht_sol = self.thermal.conjugate_ht(T_fluid_in, T_solid_outer, h, t_wall)
        T_interface = cht_sol['T_interface']
        T_avg_computed = 0.5 * (T_fluid_in + T_interface)
        R_thermal = T_avg - T_avg_computed
        
        return np.array([R_flow, R_thermal])
    
    def solve(self, Q_in: float, T_fluid_in: float, T_solid_outer: float,
              t_wall: float, max_iter: int = 100, tol: float = 1e-10) -> Dict:
        """
        Solve coupled FST problem with monolithic Newton-Raphson.
        """
        # Initial guess
        H0 = self.flow.H0
        T0 = T_fluid_in
        x = np.array([H0, T0])
        
        iteration_history = []
        
        for iteration in range(max_iter):
            # Compute residual
            R = self.residual_system(x, Q_in, T_fluid_in, T_solid_outer, t_wall)
            
            # Check convergence
            res_norm = np.linalg.norm(R)
            iteration_history.append({
                'iter': iteration + 1,
                'residual_norm': res_norm,
                'H': x[0],
                'T_avg': x[1]
            })
            
            if res_norm < tol:
                break
            
            # Compute Jacobian numerically
            eps = 1e-8
            J = np.zeros((2, 2))
            for i in range(2):
                x_pert = x.copy()
                x_pert[i] += eps * max(abs(x[i]), 1.0)
                R_pert = self.residual_system(x_pert, Q_in, T_fluid_in, 
                                             T_solid_outer, t_wall)
                J[:, i] = (R_pert - R) / (eps * max(abs(x[i]), 1.0))
            
            # Newton update
            try:
                dx = np.linalg.solve(J, -R)
            except np.linalg.LinAlgError:
                # Singular Jacobian, use pseudo-inverse
                dx = np.linalg.lstsq(J, -R, rcond=None)[0]
            
            # Damped Newton (line search)
            alpha = 1.0
            for _ in range(20):
                x_new = x + alpha * dx
                # Ensure physical bounds
                if x_new[0] > 0 and x_new[1] > 0:
                    R_new = self.residual_system(x_new, Q_in, T_fluid_in,
                                                T_solid_outer, t_wall)
                    if np.linalg.norm(R_new) < res_norm * 1.1:
                        break
                alpha *= 0.5
            
            x = x + alpha * dx
        
        # Extract solution
        H_final, T_avg_final = x
        DeltaT = T_avg_final - self.flow.fluid.T_ref
        
        # Final computations
        dp_dx = self.flow.pressure_gradient_from_flow_rate(Q_in, H_final, T_avg_final)
        flow_sol = self.flow.solve_poiseuille(dp_dx, H_final, T_avg_final)
        
        Re = flow_sol['Re']
        Pr = self.flow.fluid.prandtl(T_avg_final)
        D_h = 2 * H_final
        h = self.thermal.compute_heat_transfer_coefficient(Re, Pr, D_h)
        cht_sol = self.thermal.conjugate_ht(T_fluid_in, T_solid_outer, h, t_wall)
        
        stress_sol = self.struct.thermal_stress(DeltaT, constrained=True)
        
        return {
            'converged': iteration < max_iter - 1,
            'iterations': len(iteration_history),
            'history': iteration_history,
            'H_final': H_final,
            'T_avg_final': T_avg_final,
            'flow': flow_sol,
            'stress': stress_sol,
            'h_final': h,
            'q_final': cht_sol['q']
        }


# ============================================================================
# Biot Number Analysis
# ============================================================================

class BiotAnalysis:
    """
    Analyze coupling strength via Biot number.
    
    Bi = h*L/k_solid
    
    Bi << 1: thermal resistance in solid (lumped capacitance OK)
    Bi >> 1: thermal resistance in fluid (surface T ≈ fluid T)
    Bi ≈ 1: both resistances important (full coupling needed)
    """
    
    def __init__(self, thermal: ThermalSolver, struct: StructuralSolver):
        self.thermal = thermal
        self.struct = struct
        
    def compute_biot(self, h: float, L_char: float) -> float:
        """Compute Biot number."""
        return h * L_char / self.struct.solid.k
    
    def coupling_regime(self, Bi: float) -> str:
        """Classify coupling regime based on Biot number."""
        if Bi < 0.1:
            return "weak (Bi << 1, lumped capacitance OK)"
        elif Bi > 10:
            return "strong (Bi >> 1, surface T ≈ fluid T)"
        else:
            return "moderate (Bi ≈ 1, full coupling needed)"
    
    def sweep_biot(self, h_range: np.ndarray, L_char: float) -> Dict:
        """Sweep Biot number and classify regimes."""
        Bi_array = [self.compute_biot(h, L_char) for h in h_range]
        regimes = [self.coupling_regime(Bi) for Bi in Bi_array]
        
        return {
            'h_range': h_range,
            'Bi_array': np.array(Bi_array),
            'regimes': regimes
        }


# ============================================================================
# Verification Utilities
# ============================================================================

class VerificationMetrics:
    """Compute verification metrics for FST coupling."""
    
    @staticmethod
    def relative_error(computed: float, reference: float) -> float:
        """Compute relative error."""
        if abs(reference) < 1e-12:
            return abs(computed - reference)
        return abs(computed - reference) / abs(reference)
    
    @staticmethod
    def energy_conservation_error(q_fluid: float, q_solid: float) -> float:
        """Compute energy conservation error at interface."""
        return abs(q_fluid - q_solid) / max(abs(q_fluid), abs(q_solid))
    
    @staticmethod
    def dittus_boelter_error(Re: float, Pr: float, Nu_computed: float) -> float:
        """Compare computed Nu with Dittus-Boelter correlation."""
        if Re < 2300:
            Nu_ref = 4.36  # laminar
        else:
            Nu_ref = 0.023 * Re**0.8 * Pr**0.4
        return VerificationMetrics.relative_error(Nu_computed, Nu_ref)
    
    @staticmethod
    def thermal_stress_analytical(DeltaT: float, E: float, alpha: float, 
                                  nu: float) -> float:
        """Analytical thermal stress for constrained plate."""
        return E * alpha * DeltaT / (1 - nu)
