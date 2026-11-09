"""
Generate figures for ice route optimization report.

Produces:
1. Pareto front visualization (fuel vs time)
2. Speed profile along route
3. Ice concentration vs fuel consumption
4. Time bucket convergence
5. Open water vs ice comparison
"""

import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

sys.path.insert(0, "/root/tp-agent/work/m7848")

from ice_route_optimization.core import (
    IceRouteOptimizer,
    RouteGraph,
    RouteEdge,
    IceCondition,
    segment_fuel_kg,
    default_ship,
)


def generate_pareto_front_figure():
    """Generate Pareto front visualization."""
    # Create a realistic route
    edges = []
    thicknesses = [0.3, 0.5, 0.7, 0.9, 1.1, 1.0, 0.8, 0.6, 0.4, 0.2]
    concentrations = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6]
    for i in range(10):
        edges.append(RouteEdge(
            edge_id=i,
            from_node=i,
            to_node=i + 1,
            distance_m=5000.0,
            ice=IceCondition(
                thickness_m=thicknesses[i],
                concentration=concentrations[i],
                flexural_strength_mpa=1.5,
            ),
        ))
    graph = RouteGraph(edges=edges)
    
    opt = IceRouteOptimizer(n_speed=25, n_time_buckets=100)
    pf = opt.pareto_front(graph, n_points=50)
    
    fig, ax = plt.subplots(figsize=(10, 7))
    
    fuels = [p[0] for p in pf.points]
    times = [p[1] for p in pf.points]
    
    ax.scatter(fuels, times, c='blue', s=50, alpha=0.7, label='Pareto-optimal solutions')
    ax.plot(fuels, times, 'b-', linewidth=2, alpha=0.5)
    
    # Mark extreme points
    min_fuel_idx = np.argmin(fuels)
    min_time_idx = np.argmin(times)
    ax.scatter([fuels[min_fuel_idx]], [times[min_fuel_idx]], c='green', s=200, 
              marker='*', label=f'Min fuel: {fuels[min_fuel_idx]:.0f} kg')
    ax.scatter([fuels[min_time_idx]], [times[min_time_idx]], c='red', s=200, 
              marker='*', label=f'Min time: {times[min_time_idx]:.2f} h')
    
    ax.set_xlabel('Fuel Consumption [kg]', fontsize=12, fontweight='bold')
    ax.set_ylabel('Voyage Time [hours]', fontsize=12, fontweight='bold')
    ax.set_title('Pareto Front: Fuel-Time Trade-off in Ice-Zone Voyage\n(10-segment route, varying ice conditions)', 
                fontsize=14, fontweight='bold')
    ax.legend(loc='upper right', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    
    # Add text box with statistics
    textstr = f'Pareto points: {pf.n_points}\nRoute distance: {graph.total_distance_m/1852:.1f} NM'
    props = dict(boxstyle='round', facecolor='wheat', alpha=0.8)
    ax.text(0.02, 0.98, textstr, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', bbox=props)
    
    plt.tight_layout()
    plt.savefig('/root/tp-agent/work/m7848/ice_route_optimization/docs/pareto_front.png', 
                dpi=150, bbox_inches='tight')
    plt.close()
    print("Generated: pareto_front.png")


def generate_speed_profile_figure():
    """Generate speed profile along route."""
    edges = []
    thicknesses = [0.3, 0.5, 0.7, 0.9, 1.1, 1.0, 0.8, 0.6, 0.4, 0.2]
    concentrations = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6]
    for i in range(10):
        edges.append(RouteEdge(
            edge_id=i,
            from_node=i,
            to_node=i + 1,
            distance_m=5000.0,
            ice=IceCondition(
                thickness_m=thicknesses[i],
                concentration=concentrations[i],
            ),
        ))
    graph = RouteGraph(edges=edges)
    
    opt = IceRouteOptimizer(n_speed=20, n_time_buckets=80)
    
    # Three different time budgets
    time_budgets = [4.0, 6.0, 10.0]
    colors = ['red', 'orange', 'green']
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    
    segments = np.arange(len(edges))
    
    # Plot ice conditions
    ax1_twin = ax1.twinx()
    ax1.bar(segments, thicknesses, color='cyan', alpha=0.5, label='Ice thickness')
    ax1_twin.plot(segments, concentrations, 'b-', linewidth=2, marker='o', label='Ice concentration')
    ax1.set_ylabel('Ice Thickness [m]', fontsize=11, color='cyan')
    ax1_twin.set_ylabel('Ice Concentration [-]', fontsize=11, color='blue')
    ax1.set_title('Ice Conditions Along Route', fontsize=13, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=9)
    ax1_twin.legend(loc='upper right', fontsize=9)
    ax1.grid(True, alpha=0.3)
    
    # Plot speed profiles
    for tb, color in zip(time_budgets, colors):
        result = opt.optimize(graph, time_budget_h=tb)
        if result.status == 'optimal' and result.speed_profile:
            speeds = result.speed_profile.speeds_kn
            ax2.plot(segments, speeds, color=color, linewidth=2, marker='s', 
                    label=f'Time budget = {tb} h (fuel = {result.fuel_kg:.0f} kg)')
    
    ax2.set_xlabel('Segment Index', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Optimal Speed [knots]', fontsize=12, fontweight='bold')
    ax2.set_title('Optimal Speed Profiles for Different Time Budgets', fontsize=13, fontweight='bold')
    ax2.legend(loc='best', fontsize=10)
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(segments)
    
    plt.tight_layout()
    plt.savefig('/root/tp-agent/work/m7848/ice_route_optimization/docs/speed_profile.png', 
                dpi=150, bbox_inches='tight')
    plt.close()
    print("Generated: speed_profile.png")


def generate_concentration_fuel_figure():
    """Generate ice concentration vs fuel consumption."""
    concentrations = np.linspace(0.2, 1.0, 15)
    ship = default_ship()
    
    fig, ax = plt.subplots(figsize=(10, 7))
    
    speeds = [6.0, 8.0, 10.0, 12.0]
    colors = ['blue', 'green', 'orange', 'red']
    
    for v, color in zip(speeds, colors):
        fuels = []
        for c in concentrations:
            edges = [RouteEdge(0, 0, 1, 20000.0, IceCondition(0.8, c))]
            fuel, _ = segment_fuel_kg(ship, edges[0], v)
            fuels.append(fuel)
        ax.plot(concentrations, fuels, color=color, linewidth=2, marker='o', 
               label=f'v = {v} kn')
    
    ax.set_xlabel('Ice Concentration [-]', fontsize=12, fontweight='bold')
    ax.set_ylabel('Fuel Consumption [kg]', fontsize=12, fontweight='bold')
    ax.set_title('Fuel Consumption vs Ice Concentration\n(Single segment, h = 0.8 m, distance = 20 km)', 
                fontsize=14, fontweight='bold')
    ax.legend(loc='upper left', fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 1.05)
    ax.set_ylim(bottom=0)
    
    plt.tight_layout()
    plt.savefig('/root/tp-agent/work/m7848/ice_route_optimization/docs/concentration_fuel.png', 
                dpi=150, bbox_inches='tight')
    plt.close()
    print("Generated: concentration_fuel.png")


def generate_convergence_figure():
    """Generate time bucket convergence plot."""
    edges = []
    for i in range(5):
        edges.append(RouteEdge(
            edge_id=i,
            from_node=i,
            to_node=i + 1,
            distance_m=10000.0,
            ice=IceCondition(thickness_m=0.8, concentration=0.9),
        ))
    graph = RouteGraph(edges=edges)
    
    n_buckets_list = [10, 20, 40, 80, 160, 320]
    fuels = []
    
    for n_buckets in n_buckets_list:
        opt = IceRouteOptimizer(n_speed=15, n_time_buckets=n_buckets)
        result = opt.optimize(graph, time_budget_h=8.0)
        if result.status == 'optimal':
            fuels.append(result.fuel_kg)
        else:
            fuels.append(np.nan)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # Left: fuel vs n_buckets
    ax1.semilogx(n_buckets_list, fuels, 'bo-', linewidth=2, markersize=8)
    ax1.set_xlabel('Number of Time Buckets', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Optimal Fuel [kg]', fontsize=12, fontweight='bold')
    ax1.set_title('Convergence: Fuel vs Time Bucket Resolution', fontsize=13, fontweight='bold')
    ax1.grid(True, alpha=0.3, which='both')
    
    # Right: relative change
    rel_changes = []
    for i in range(1, len(fuels)):
        rel_change = abs(fuels[i] - fuels[i-1]) / fuels[i-1] * 100
        rel_changes.append(rel_change)
    
    ax2.semilogx(n_buckets_list[1:], rel_changes, 'rs-', linewidth=2, markersize=8)
    ax2.axhline(y=2.0, color='green', linestyle='--', linewidth=2, label='2% threshold')
    ax2.set_xlabel('Number of Time Buckets', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Relative Change [%]', fontsize=12, fontweight='bold')
    ax2.set_title('Convergence Rate: Relative Change per Doubling', fontsize=13, fontweight='bold')
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3, which='both')
    
    plt.tight_layout()
    plt.savefig('/root/tp-agent/work/m7848/ice_route_optimization/docs/convergence.png', 
                dpi=150, bbox_inches='tight')
    plt.close()
    print("Generated: convergence.png")


def generate_open_water_comparison_figure():
    """Generate open water vs ice comparison."""
    ship = default_ship()
    speeds_kn = np.linspace(4.0, 14.0, 20)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # Open water
    fuels_ow = []
    for v in speeds_kn:
        edges = [RouteEdge(0, 0, 1, 50000.0, IceCondition(0.0, 0.0))]
        fuel, _ = segment_fuel_kg(ship, edges[0], v)
        fuels_ow.append(fuel)
    
    # Ice (h=0.8m, c=0.9)
    fuels_ice = []
    for v in speeds_kn:
        edges = [RouteEdge(0, 0, 1, 50000.0, IceCondition(0.8, 0.9))]
        fuel, _ = segment_fuel_kg(ship, edges[0], v)
        fuels_ice.append(fuel)
    
    # Left: fuel vs speed
    ax1.plot(speeds_kn, fuels_ow, 'b-', linewidth=2, label='Open water')
    ax1.plot(speeds_kn, fuels_ice, 'r-', linewidth=2, label='Ice (h=0.8m, c=0.9)')
    ax1.set_xlabel('Speed [knots]', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Fuel Consumption [kg]', fontsize=12, fontweight='bold')
    ax1.set_title('Fuel Consumption vs Speed\n(Distance = 50 km)', fontsize=13, fontweight='bold')
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)
    
    # Right: fuel ratio
    ratios = [f_ice / f_ow for f_ice, f_ow in zip(fuels_ice, fuels_ow)]
    ax2.plot(speeds_kn, ratios, 'g-', linewidth=2, marker='o')
    ax2.set_xlabel('Speed [knots]', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Fuel Ratio (Ice / Open Water)', fontsize=12, fontweight='bold')
    ax2.set_title('Ice Penalty Factor vs Speed', fontsize=13, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=1.0, color='gray', linestyle='--', linewidth=1)
    
    plt.tight_layout()
    plt.savefig('/root/tp-agent/work/m7848/ice_route_optimization/docs/open_water_comparison.png', 
                dpi=150, bbox_inches='tight')
    plt.close()
    print("Generated: open_water_comparison.png")


if __name__ == "__main__":
    print("Generating figures for ice route optimization report...")
    generate_pareto_front_figure()
    generate_speed_profile_figure()
    generate_concentration_fuel_figure()
    generate_convergence_figure()
    generate_open_water_comparison_figure()
    print("\nAll figures generated successfully!")
