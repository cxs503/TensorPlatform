"""
NSGA-II Multi-Objective Optimization Framework — Full Verification Runner.

Generates:
1. Convergence curves (GD, IGD vs generation) for ZDT1-3
2. Pareto front visualizations vs analytical fronts
3. Diversity metrics (Spread, HV) vs generation
4. Ship resistance-seakeeping Pareto front with physical interpretation
5. Comprehensive JSON report with all metrics and sha256 anchors
"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json
import hashlib
import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))



class NumpyEncoder(json.JSONEncoder):
    """Custom JSON encoder for numpy types."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

from optimizer.multiobjective import (
    NSGA2, ZDT1, ZDT2, ZDT3, DTLZ1, DTLZ2,
    generational_distance, inverted_generational_distance,
    spread, hypervolume
)
from optimizer.multiobjective.ship_instance import ShipResistanceSeakeeping

# Output directories
RUNS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'runs', 'm8498_nsga2_verification')
DOCS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'm8498_nsga2_verification')
os.makedirs(RUNS_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)


def run_zdt_verification():
    """Run NSGA-II on ZDT1-3 and generate verification data."""
    results = {}
    
    problems = {
        'ZDT1': (ZDT1(n_var=10), 100, 200),
        'ZDT2': (ZDT2(n_var=10), 100, 200),
        'ZDT3': (ZDT3(n_var=10), 100, 200),
    }
    
    for name, (prob, pop_size, n_gen) in problems.items():
        print(f"Running {name}...")
        t0 = time.time()
        
        opt = NSGA2(
            n_var=prob.n_var, n_obj=prob.n_obj,
            pop_size=pop_size, n_gen=n_gen,
            seed=42
        )
        
        true_pf = prob.true_pareto_front(200)
        ref_point = np.array([1.1, 1.1])
        
        result = opt.optimize(
            prob, prob.lb, prob.ub,
            reference_point=ref_point,
            true_pareto=true_pf,
            verbose=False
        )
        
        elapsed = time.time() - t0
        
        # Final metrics
        final_pf = result['pareto_front']
        final_gd = generational_distance(final_pf, true_pf)
        final_igd = inverted_generational_distance(final_pf, true_pf)
        final_spread = spread(final_pf, true_pf)
        final_hv = hypervolume(final_pf, ref_point)
        
        # True Pareto front hypervolume (reference)
        hv_ref = hypervolume(true_pf, ref_point)
        
        results[name] = {
            'problem': name,
            'n_var': prob.n_var,
            'pop_size': pop_size,
            'n_gen': n_gen,
            'elapsed_seconds': elapsed,
            'pareto_front_size': len(final_pf),
            'final_gd': final_gd,
            'final_igd': final_igd,
            'final_spread': final_spread,
            'final_hv': final_hv,
            'hv_reference': hv_ref,
            'hv_ratio': final_hv / hv_ref if hv_ref > 0 else 0,
            'gd_converged': final_gd < 1e-3,
            'igd_converged': final_igd < 1e-2,
            'spread_ok': final_spread < 0.5,
            'hv_ok': final_hv > 0.8 * hv_ref,
            'history': {
                'gd': result['history']['gd'],
                'igd': result['history']['igd'],
                'spread': result['history']['spread'],
                'hypervolume': result['history']['hypervolume'],
            },
            'pareto_front': final_pf.tolist(),
            'true_pareto_front': true_pf.tolist(),
            'input_hash': result['input_hash'],
        }
        
        print(f"  {name}: GD={final_gd:.6f}, IGD={final_igd:.6f}, "
              f"Spread={final_spread:.4f}, HV={final_hv:.4f}/{hv_ref:.4f} "
              f"({final_hv/hv_ref*100:.1f}%), Time={elapsed:.1f}s")
    
    return results


def run_ship_verification():
    """Run NSGA-II on ship resistance-seakeeping instance."""
    print("Running Ship Resistance-Seakeeping...")
    t0 = time.time()
    
    ship = ShipResistanceSeakeeping()
    
    opt = NSGA2(
        n_var=3, n_obj=2,
        pop_size=60, n_gen=150,
        seed=42
    )
    
    ref_point = np.array([10000.0, 60.0])
    
    result = opt.optimize(
        ship, ship.lb, ship.ub,
        reference_point=ref_point,
        verbose=False
    )
    
    elapsed = time.time() - t0
    
    final_pf = result['pareto_front']
    final_ps = result['pareto_set']
    final_hv = hypervolume(final_pf, ref_point)
    
    # Get design info for each Pareto-optimal solution
    designs = []
    for i in range(len(final_ps)):
        info = ship.get_design_info(final_ps[i])
        designs.append({
            'x': final_ps[i].tolist(),
            'objectives': final_pf[i].tolist(),
            'L_B': info['L/B'],
            'B_T': info['B/T'],
            'Cb': info['Cb'],
            'B_m': info['B'],
            'T_m': info['T'],
            'displacement_t': info['displacement_t'],
            'Fr': info['Fr'],
        })
    
    # Compute approximate true front by dense sampling
    true_pf = ship.true_pareto_front(200)
    gd = generational_distance(final_pf, true_pf)
    igd = inverted_generational_distance(final_pf, true_pf)
    
    result_data = {
        'problem': 'ShipResistanceSeakeeping',
        'n_var': 3,
        'n_obj': 2,
        'pop_size': 60,
        'n_gen': 150,
        'elapsed_seconds': elapsed,
        'pareto_front_size': len(final_pf),
        'final_gd': gd,
        'final_igd': igd,
        'final_hv': final_hv,
        'designs': designs,
        'pareto_front': final_pf.tolist(),
        'input_hash': result['input_hash'],
        'history': {
            'hypervolume': result['history']['hypervolume'],
        }
    }
    
    print(f"  Ship: |PF|={len(final_pf)}, GD={gd:.6f}, IGD={igd:.6f}, "
          f"HV={final_hv:.2f}, Time={elapsed:.1f}s")
    
    return result_data


def generate_figures(zdt_results, ship_result):
    """Generate all verification figures."""
    figures = {}
    
    # Figure 1: Convergence curves (GD, IGD) for ZDT1-3
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    fig.suptitle('NSGA-II Convergence on ZDT Test Problems', fontsize=14, fontweight='bold')
    
    for i, name in enumerate(['ZDT1', 'ZDT2', 'ZDT3']):
        r = zdt_results[name]
        gens = list(range(len(r['history']['gd'])))
        
        # GD
        ax = axes[0, i]
        ax.semilogy(gens, r['history']['gd'], 'b-', linewidth=1.5)
        ax.axhline(y=1e-3, color='r', linestyle='--', alpha=0.7, label='GD threshold (1e-3)')
        ax.set_xlabel('Generation')
        ax.set_ylabel('GD')
        ax.set_title(f'{name} - Generational Distance')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        
        # IGD
        ax = axes[1, i]
        ax.semilogy(gens, r['history']['igd'], 'g-', linewidth=1.5)
        ax.axhline(y=1e-2, color='r', linestyle='--', alpha=0.7, label='IGD threshold (1e-2)')
        ax.set_xlabel('Generation')
        ax.set_ylabel('IGD')
        ax.set_title(f'{name} - Inverted Generational Distance')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    path = os.path.join(RUNS_DIR, 'fig1_convergence_zdt.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    figures['convergence_zdt'] = path
    
    # Figure 2: Pareto fronts vs analytical
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle('NSGA-II Pareto Fronts vs Analytical Fronts', fontsize=14, fontweight='bold')
    
    for i, name in enumerate(['ZDT1', 'ZDT2', 'ZDT3']):
        r = zdt_results[name]
        ax = axes[i]
        
        true_pf = np.array(r['true_pareto_front'])
        approx_pf = np.array(r['pareto_front'])
        
        ax.scatter(true_pf[:, 0], true_pf[:, 1], c='gray', s=5, alpha=0.5, label='True PF')
        ax.scatter(approx_pf[:, 0], approx_pf[:, 1], c='red', s=15, marker='x', label='NSGA-II PF')
        ax.set_xlabel('f₁')
        ax.set_ylabel('f₂')
        ax.set_title(f'{name} (GD={r["final_gd"]:.2e})')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    path = os.path.join(RUNS_DIR, 'fig2_pareto_fronts_zdt.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    figures['pareto_fronts_zdt'] = path
    
    # Figure 3: Diversity metrics (Spread, HV)
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    fig.suptitle('NSGA-II Diversity Metrics on ZDT Problems', fontsize=14, fontweight='bold')
    
    for i, name in enumerate(['ZDT1', 'ZDT2', 'ZDT3']):
        r = zdt_results[name]
        gens = list(range(len(r['history']['spread'])))
        
        # Spread
        ax = axes[0, i]
        valid_spread = [s for s in r['history']['spread'] if s < 10]
        ax.plot(gens[:len(valid_spread)], valid_spread, 'm-', linewidth=1.5)
        ax.axhline(y=0.5, color='r', linestyle='--', alpha=0.7, label='Spread threshold (0.5)')
        ax.set_xlabel('Generation')
        ax.set_ylabel('Spread')
        ax.set_title(f'{name} - Spread')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        
        # Hypervolume
        ax = axes[1, i]
        ax.plot(gens, r['history']['hypervolume'], 'c-', linewidth=1.5)
        ax.axhline(y=0.8 * r['hv_reference'], color='r', linestyle='--', alpha=0.7,
                   label=f'80% HV_ref ({0.8*r["hv_reference"]:.3f})')
        ax.set_xlabel('Generation')
        ax.set_ylabel('Hypervolume')
        ax.set_title(f'{name} - Hypervolume')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    path = os.path.join(RUNS_DIR, 'fig3_diversity_zdt.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    figures['diversity_zdt'] = path
    
    # Figure 4: Ship resistance-seakeeping Pareto front
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle('Ship Resistance-Seakeeping Dual-Objective Optimization', fontsize=14, fontweight='bold')
    
    pf = np.array(ship_result['pareto_front'])
    designs = ship_result['designs']
    
    # Pareto front
    colors = [d['Cb'] for d in designs]
    sc = ax1.scatter(pf[:, 0], pf[:, 1], c=colors, cmap='viridis', s=40, edgecolors='k', linewidth=0.5)
    plt.colorbar(sc, ax=ax1, label='Block Coefficient Cb')
    ax1.set_xlabel('Total Resistance R_T [kN]')
    ax1.set_ylabel('Roll RAO [deg/m]')
    ax1.set_title(f'Pareto Front (|PF|={len(pf)}, HV={ship_result["final_hv"]:.1f})')
    ax1.grid(True, alpha=0.3)
    
    # Design space
    cb_vals = [d['Cb'] for d in designs]
    lb_vals = [d['L_B'] for d in designs]
    sc2 = ax2.scatter(pf[:, 0], pf[:, 1], c=lb_vals, cmap='plasma', s=40, edgecolors='k', linewidth=0.5)
    plt.colorbar(sc2, ax=ax2, label='Length-Beam Ratio L/B')
    ax2.set_xlabel('Total Resistance R_T [kN]')
    ax2.set_ylabel('Roll RAO [deg/m]')
    ax2.set_title('Design Variable Mapping')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    path = os.path.join(RUNS_DIR, 'fig4_ship_pareto.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    figures['ship_pareto'] = path
    
    # Figure 5: Ship HV convergence
    fig, ax = plt.subplots(1, 1, figsize=(8, 5))
    gens = list(range(len(ship_result['history']['hypervolume'])))
    ax.plot(gens, ship_result['history']['hypervolume'], 'b-', linewidth=2)
    ax.set_xlabel('Generation')
    ax.set_ylabel('Hypervolume')
    ax.set_title('Ship Instance: Hypervolume Convergence')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(RUNS_DIR, 'fig5_ship_convergence.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    figures['ship_convergence'] = path
    
    return figures


def generate_report(zdt_results, ship_result, figures):
    """Generate comprehensive JSON report."""
    
    # Compute input hash for the entire verification
    all_config = {
        'zdt': {k: {'pop_size': v['pop_size'], 'n_gen': v['n_gen'], 'n_var': v['n_var']}
                for k, v in zdt_results.items()},
        'ship': {'pop_size': ship_result['pop_size'], 'n_gen': ship_result['n_gen']},
        'seed': 42
    }
    verification_hash = hashlib.sha256(
        json.dumps(all_config, sort_keys=True).encode()
    ).hexdigest()[:16]
    
    report = {
        'title': 'NSGA-II Multi-Objective Optimization Framework Verification',
        'mission_id': 'm8498',
        'verification_hash': verification_hash,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        
        # Verification Surface 1: Analytical Test Functions
        'analytical_functions': {
            'problems': ['ZDT1', 'ZDT2', 'ZDT3'],
            'results': zdt_results,
            'summary': {
                'all_gd_converged': all(r['gd_converged'] for r in zdt_results.values()),
                'all_igd_converged': all(r['igd_converged'] for r in zdt_results.values()),
            }
        },
        
        # Verification Surface 2: Convergence Metrics
        'convergence': {
            'metric': 'Generational Distance (GD) + Inverted Generational Distance (IGD)',
            'criterion': {
                'gd_threshold': 1e-3,
                'igd_threshold': 1e-2,
            },
            'results': {
                name: {
                    'final_gd': r['final_gd'],
                    'final_igd': r['final_igd'],
                    'gd_pass': r['gd_converged'],
                    'igd_pass': r['igd_converged'],
                }
                for name, r in zdt_results.items()
            }
        },
        
        # Verification Surface 3: Diversity Metrics
        'diversity': {
            'metrics': ['Spread', 'Hypervolume'],
            'criteria': {
                'spread_threshold': 0.5,
                'hv_ratio_threshold': 0.8,
            },
            'results': {
                name: {
                    'final_spread': r['final_spread'],
                    'final_hv': r['final_hv'],
                    'hv_reference': r['hv_reference'],
                    'hv_ratio': r['hv_ratio'],
                    'spread_pass': r['spread_ok'],
                    'hv_pass': r['hv_ok'],
                }
                for name, r in zdt_results.items()
            }
        },
        
        # Verification Surface 4: Ship Instance
        'ship_instance': {
            'problem': 'ShipResistanceSeakeeping',
            'design_variables': ['L/B', 'B/T', 'Cb'],
            'objectives': ['Total Resistance R_T [kN]', 'Roll RAO [deg/m]'],
            'pareto_front_size': ship_result['pareto_front_size'],
            'final_gd': ship_result['final_gd'],
            'final_igd': ship_result['final_igd'],
            'final_hv': ship_result['final_hv'],
            'designs': ship_result['designs'][:10],  # Top 10 designs
            'input_hash': ship_result['input_hash'],
        },
        
        # Figures
        'figures': {k: os.path.basename(v) for k, v in figures.items()},
        
        # Acceptance criteria
        'acceptance': {
            'zdt_gd_convergence': all(r['gd_converged'] for r in zdt_results.values()),
            'zdt_igd_convergence': all(r['igd_converged'] for r in zdt_results.values()),
            'ship_pareto_found': ship_result['pareto_front_size'] > 5,
            'reproducibility': 'sha256 input hash anchored',
        }
    }
    
    # Save JSON report
    report_path = os.path.join(RUNS_DIR, 'verification_report.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, cls=NumpyEncoder)
    
    return report, report_path


def main():
    """Run full verification suite."""
    print("=" * 60)
    print("NSGA-II Multi-Objective Optimization Framework Verification")
    print("=" * 60)
    
    # Run ZDT verification
    zdt_results = run_zdt_verification()
    
    # Run ship verification
    ship_result = run_ship_verification()
    
    # Generate figures
    print("\nGenerating figures...")
    figures = generate_figures(zdt_results, ship_result)
    
    # Generate report
    print("Generating report...")
    report, report_path = generate_report(zdt_results, ship_result, figures)
    
    # Print summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    for name, r in zdt_results.items():
        status = "✓" if (r['gd_converged'] and r['igd_converged']) else "✗"
        print(f"  {status} {name}: GD={r['final_gd']:.2e} (<1e-3: {r['gd_converged']}), "
              f"IGD={r['final_igd']:.2e} (<1e-2: {r['igd_converged']}), "
              f"HV={r['hv_ratio']*100:.1f}%")
    
    print(f"  {'✓' if ship_result['pareto_front_size'] > 5 else '✗'} Ship: "
          f"|PF|={ship_result['pareto_front_size']}, GD={ship_result['final_gd']:.2e}")
    
    print(f"\nReport: {report_path}")
    print(f"Figures: {RUNS_DIR}/")
    print(f"Verification hash: {report['verification_hash']}")
    
    return report


if __name__ == '__main__':
    main()
