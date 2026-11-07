"""Report corpus for scorer robustness experiments.

Contains realistic ship performance engineering reports with varying
quality levels, covering resistance, propulsion, seakeeping, etc.
"""

REPORT_CORPUS = [
    # Report 1: High-quality resistance analysis
    """Ship Resistance Analysis - DTMB 5415 Model

L = 14.053 m, B = 1.923 m, T = 0.563 m, Cb = 0.513
Fn = 0.28, Re = 1.2e7

Methodology: RANS simulation with k-omega SST turbulence model.
Grid independence study performed with 3 mesh densities (2M, 5M, 10M cells).
Time step convergence verified with dt = 0.001s, 0.0005s, 0.00025s.

Results:
Total resistance coefficient Ct = 3.85e-3 at Fn=0.28
Friction resistance Cf = 2.91e-3 (ITTC-57 line)
Residuary resistance Cr = 0.94e-3

Validation against experimental data:
Model test Ct_exp = 3.92e-3, error = 1.8%
RMSE = 0.12e-3, R² = 0.97

Uncertainty analysis:
Grid convergence index GCI = 1.2%
Iterative uncertainty Ui = 0.3%
Total numerical uncertainty Un = 1.5%

Table 1: Resistance components at various speeds
Figure 3: Wave pattern comparison CFD vs experiment
Equation 5: Form factor calculation k = 0.25
""",

    # Report 2: Medium-quality propulsion analysis
    """Propeller Open Water Test Analysis

Propeller diameter D = 0.25 m, pitch ratio P/D = 1.2
Number of blades Z = 5, expanded area ratio EAR = 0.75

Test conditions:
Speed range: 0 < J < 1.2
Reynolds number Re = 5e5

Results:
KT at J=0.6: 0.285
10KQ at J=0.6: 0.342
Efficiency eta_0 at J=0.6: 0.625

Comparison with B-series:
KT deviation: 2.1%
KQ deviation: 1.8%

The propeller performance was evaluated using potential flow BEM.
Convergence achieved within 50 iterations.
""",

    # Report 3: Low-quality report (minimal content)
    """Ship Performance Summary

We tested the ship model and found good results.
The resistance was measured at different speeds.
The propeller worked well.

Speed = 15 knots
Resistance = 50 kN
""",

    # Report 4: High-quality seakeeping analysis
    """Seakeeping Performance Assessment - S175 Container Ship

L = 175.0 m, B = 25.4 m, T = 9.5 m, Cb = 0.572
Displacement = 24,500 t, GM = 1.8 m

Methodology: 3D panel method (BEM) with forward speed correction.
Frequency range: 0.2 < omega < 2.0 rad/s
Heading angles: 0, 30, 60, 90, 120, 150, 180 degrees

RAO Analysis:
Heave RAO peak: 1.2 m/m at omega = 0.8 rad/s (head seas)
Pitch RAO peak: 2.5 deg/m at omega = 0.7 rad/s (head seas)
Roll RAO peak: 8.2 deg/m at omega = 0.5 rad/s (beam seas)

Short-term prediction:
Sea state: Pierson-Moskowitz, Hs = 3.0 m, Tp = 9.0 s
Significant heave motion: 0.85 m
Significant pitch motion: 1.9 deg
Significant roll motion: 4.2 deg

Validation:
Comparison with model test data shows agreement within 5%
for heave and pitch RAOs in head seas.

Table 2: RAO values at selected frequencies
Figure 5: Heave RAO comparison
Appendix A: Panel distribution details
""",

    # Report 5: Medium-quality maneuvering analysis
    """Maneuvering Simulation - Zigzag Test

Ship parameters:
L = 150 m, B = 23 m, T = 8.5 m
Speed = 15 knots

MMG model used for maneuvering prediction.
Hydrodynamic derivatives obtained from captive model tests.

10/10 zigzag test:
First overshoot: 12.5 degrees
Second overshoot: 14.2 degrees
Time to second overshoot: 45 s

IMO criteria check:
First overshoot < 15 deg: PASS
Second overshoot < 15 deg: PASS

The simulation results satisfy IMO maneuvering criteria.
""",

    # Report 6: High-quality intact stability analysis
    """Intact Stability Assessment - Bulk Carrier

L = 225 m, B = 32.26 m, T = 12.5 m, Cb = 0.82
Displacement = 82,000 t, GM = 2.1 m

GZ curve calculation:
Max GZ = 0.65 m at heel = 35 degrees
Angle of vanishing stability = 52 degrees
Area under GZ curve (0-30 deg) = 8.2 m-deg
Area under GZ curve (0-40 deg) = 12.5 m-deg

IMO A.749(18) criteria:
Max GZ > 0.20 m: PASS (0.65 m)
Angle of max GZ > 25 deg: PASS (35 deg)
Area 0-30 > 3.15 m-deg: PASS (8.2 m-deg)
Area 0-40 > 5.16 m-deg: PASS (12.5 m-deg)
Area 30-40 > 1.72 m-deg: PASS (4.3 m-deg)

Cross curves of stability computed for 15 displacement conditions.
Table 3: GZ values at 5-degree intervals
Figure 7: GZ curve with IMO criteria limits
""",

    # Report 7: Low-medium quality report
    """Resistance Test Results

Model scale 1:50 tested in towing tank.
Speed range 2-8 m/s.

Results show increasing resistance with speed.
Friction resistance calculated using ITTC-57 formula.

Total resistance at 6 m/s: 25 N
Form factor k = 0.2
""",

    # Report 8: High-quality green shipping analysis
    """Energy Efficiency Design Index (EEDI) Assessment

Ship type: Bulk carrier, DWT = 82,000 t
L = 225 m, B = 32.26 m, T = 12.5 m

Main engine: MAN B&W 6S60ME-C8.2
MCR = 13,560 kW at 105 rpm
SFOC = 171 g/kWh

EEDI calculation:
Attained EEDI = 3.85 g CO2/tonne-mile
Required EEDI (Phase 2) = 4.21 g CO2/tonne-mile
Reduction factor = 20%

Compliance: PASS (attained < required)

Energy saving devices evaluated:
- Pre-swirl stator: 2.3% fuel saving
- Ducted propeller: 1.8% fuel saving
- Air lubrication: 3.5% fuel saving

Table 4: EEDI component breakdown
Figure 8: EEDI compliance margin
""",

    # Report 9: Medium-quality ice resistance analysis
    """Ice Resistance Prediction - Baltic Ice Class

Ship parameters:
L = 120 m, B = 20 m, T = 7.5 m
Ice class: 1A Super

Ice conditions:
Level ice thickness: 0.8 m
Flexural strength: 500 kPa

Methodology: Lindqvist empirical formula with corrections.
Total ice resistance = 850 kN at 5 knots in 0.8 m level ice.

Components:
Breaking resistance: 520 kN
Submersion resistance: 210 kN
Friction resistance: 120 kN

Comparison with model test: 8% deviation.
""",

    # Report 10: High-quality vibration analysis
    """Hull Girder Vibration Analysis

Ship: 300m container vessel
L = 300 m, B = 48.2 m, D = 24.6 m

2-node vertical bending natural frequency:
FEM prediction: 1.25 Hz
Empirical estimate (Barrass): 1.18 Hz
Difference: 5.6%

Excitation frequencies:
Propeller blade frequency (7 blades at 105 rpm): 12.25 Hz
Engine firing frequency (6-cylinder at 105 rpm): 10.5 Hz

Avoidance margin > 15% from excitation frequencies: PASS

Table 5: Natural frequencies for modes 2-5
Figure 9: Mode shape visualization
Equation 12: Timoshenko beam correction
""",

    # Report 11: Low-quality report
    """Ship Design Notes

The ship has good stability.
Resistance is acceptable.
Propeller design is standard.

Length: 100 m
Beam: 16 m
Draft: 6 m
""",

    # Report 12: Medium-quality report with some validation
    """CFD Resistance Prediction - KVLCC2

L = 280 m, B = 45.2 m, T = 16.5 m, Cb = 0.833
Fn = 0.142, Re = 3.2e8

RANS simulation with SST k-omega model.
Mesh: 8M cells, y+ < 1 on hull surface.

Results:
Ct = 4.12e-3
Cf = 3.25e-3
Cp = 0.87e-3

Comparison with Tokyo 2015 workshop data:
Mean Ct from participants: 4.18e-3
Our result within scatter band.
""",
]


def get_corpus() -> list:
    """Return the full report corpus."""
    return list(REPORT_CORPUS)


def get_corpus_by_quality(threshold: str = "all") -> list:
    """Filter corpus by approximate quality level.

    threshold: 'high', 'medium', 'low', or 'all'
    """
    if threshold == "all":
        return list(REPORT_CORPUS)

    # Simple heuristic: longer reports with more technical content are higher quality
    scored = []
    for i, report in enumerate(REPORT_CORPUS):
        score = 0
        score += len(report) * 0.01
        score += report.lower().count("table") * 5
        score += report.lower().count("figure") * 5
        score += report.lower().count("validation") * 10
        score += report.lower().count("uncertainty") * 10
        scored.append((i, report, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    n = len(scored)

    if threshold == "high":
        return [r for _, r, _ in scored[:n // 3]]
    elif threshold == "medium":
        return [r for _, r, _ in scored[n // 3:2 * n // 3]]
    elif threshold == "low":
        return [r for _, r, _ in scored[2 * n // 3:]]
    return list(REPORT_CORPUS)
