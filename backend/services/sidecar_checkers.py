"""
Sidecar Checkers — Multi-constraint physical property evidence library.

Pluggable checkers registered by output type. Each checker validates
physical constraints (monotonicity, magnitude, peak position, symmetry, etc.)
on simulation outputs.

Failure = red flag → auto-enqueue for reproduction priority queue.
Does NOT kill the mission (non-destructive audit).

Two initial instances:
  1. HoltropCdReChecker: Cd vs Re monotonicity + magnitude domain
  2. WaveCwFnChecker: Cw(Fn) peak position domain (peak at Fn≈0.4-0.5)

References:
  - Holtrop & Mennen 1982, "A computer program for the prediction of
    ship resistance and propulsion"
  - ITTC 1957 model-ship correlation line
  - Michlet wave resistance theory
"""
from __future__ import annotations
from typing import Any
import math


class BaseSidecarChecker:
    """Base class for sidecar physical-property checkers."""

    name: str = "base"
    output_type: str = "generic"

    def check(self, data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class HoltropCdReChecker(BaseSidecarChecker):
    """
    Check Holtrop friction resistance coefficient Cd vs Reynolds number Re.

    Physical constraints:
      1. Cd must decrease monotonically with increasing Re (turbulent BL)
         - ITTC-57 line: Cf = 0.075 / (log10(Re) - 2)^2
         - Cd should follow similar trend (form factor applied)
      2. Magnitude domain: for ship Re (1e6 - 1e10), Cd should be in [1e-4, 1e-1]
      3. No negative values (physical impossibility)
    """

    name = "holtrop_cd_re_monotonicity"
    output_type = "holtrop_cd_re"

    # Physical bounds
    CD_MIN = 1e-4
    CD_MAX = 1e-1
    RE_MIN = 1e5
    RE_MAX = 1e11

    def check(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Args:
            data: {
                "reynolds_numbers": [Re1, Re2, ...],  # sorted ascending
                "cd_values": [Cd1, Cd2, ...],          # corresponding Cd
            }

        Returns:
            {
                "passed": bool,
                "red_flag": bool,
                "details": {
                    "monotonicity_violations": [...],
                    "magnitude_violations": [...],
                    "negative_violations": [...],
                }
            }
        """
        re_list = data.get("reynolds_numbers", [])
        cd_list = data.get("cd_values", [])

        if len(re_list) != len(cd_list) or len(re_list) < 2:
            return {
                "passed": False,
                "red_flag": True,
                "details": {"error": "Need at least 2 paired (Re, Cd) points"},
            }

        violations = {
            "monotonicity_violations": [],
            "magnitude_violations": [],
            "negative_violations": [],
        }

        # Check 1: No negative Cd
        for i, cd in enumerate(cd_list):
            if cd < 0:
                violations["negative_violations"].append({
                    "index": i,
                    "Re": re_list[i],
                    "Cd": cd,
                    "reason": "Negative resistance coefficient is unphysical",
                })

        # Check 2: Magnitude domain
        for i, cd in enumerate(cd_list):
            if cd < self.CD_MIN or cd > self.CD_MAX:
                violations["magnitude_violations"].append({
                    "index": i,
                    "Re": re_list[i],
                    "Cd": cd,
                    "expected_range": [self.CD_MIN, self.CD_MAX],
                    "reason": f"Cd={cd:.2e} outside physical range [{self.CD_MIN}, {self.CD_MAX}]",
                })

        # Check 3: Monotonicity (Cd should decrease with increasing Re)
        # Allow small numerical noise (1% relative tolerance)
        for i in range(1, len(cd_list)):
            if re_list[i] > re_list[i - 1]:  # Re is increasing
                if cd_list[i] > cd_list[i - 1] * 1.01:  # Cd increased by >1%
                    violations["monotonicity_violations"].append({
                        "index": i,
                        "Re_prev": re_list[i - 1],
                        "Re_curr": re_list[i],
                        "Cd_prev": cd_list[i - 1],
                        "Cd_curr": cd_list[i],
                        "reason": "Cd should decrease with increasing Re (turbulent BL physics)",
                    })

        has_violations = any(len(v) > 0 for v in violations.values())

        return {
            "passed": not has_violations,
            "red_flag": has_violations,
            "details": violations,
            "summary": {
                "n_points": len(re_list),
                "n_monotonicity_violations": len(violations["monotonicity_violations"]),
                "n_magnitude_violations": len(violations["magnitude_violations"]),
                "n_negative_violations": len(violations["negative_violations"]),
            },
        }


class WaveCwFnChecker(BaseSidecarChecker):
    """
    Check wave resistance coefficient Cw vs Froude number Fn.

    Physical constraints:
      1. Peak position: Cw should peak around Fn ≈ 0.4 - 0.5
         (hump speed for typical ship forms)
      2. Cw should be non-negative
      3. Cw should approach 0 as Fn → 0 (no waves at zero speed)
      4. Magnitude: Cw typically in [1e-5, 1e-2] for displacement ships
    """

    name = "wave_cw_fn_peak_position"
    output_type = "wave_cw_fn"

    # Physical bounds
    CW_MIN = 1e-6
    CW_MAX = 1e-1
    FN_PEAK_MIN = 0.3
    FN_PEAK_MAX = 0.6

    def check(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Args:
            data: {
                "froude_numbers": [Fn1, Fn2, ...],  # sorted ascending
                "cw_values": [Cw1, Cw2, ...],        # corresponding Cw
            }

        Returns:
            {
                "passed": bool,
                "red_flag": bool,
                "details": {
                    "peak_fn": float,
                    "peak_cw": float,
                    "peak_position_ok": bool,
                    "magnitude_violations": [...],
                    "negative_violations": [...],
                }
            }
        """
        fn_list = data.get("froude_numbers", [])
        cw_list = data.get("cw_values", [])

        if len(fn_list) != len(cw_list) or len(fn_list) < 3:
            return {
                "passed": False,
                "red_flag": True,
                "details": {"error": "Need at least 3 paired (Fn, Cw) points"},
            }

        violations = {
            "magnitude_violations": [],
            "negative_violations": [],
        }

        # Check 1: No negative Cw
        for i, cw in enumerate(cw_list):
            if cw < 0:
                violations["negative_violations"].append({
                    "index": i,
                    "Fn": fn_list[i],
                    "Cw": cw,
                    "reason": "Negative wave resistance is unphysical",
                })

        # Check 2: Magnitude domain
        for i, cw in enumerate(cw_list):
            if cw < self.CW_MIN or cw > self.CW_MAX:
                violations["magnitude_violations"].append({
                    "index": i,
                    "Fn": fn_list[i],
                    "Cw": cw,
                    "expected_range": [self.CW_MIN, self.CW_MAX],
                    "reason": f"Cw={cw:.2e} outside typical range [{self.CW_MIN}, {self.CW_MAX}]",
                })

        # Check 3: Peak position
        max_cw_idx = cw_list.index(max(cw_list))
        peak_fn = fn_list[max_cw_idx]
        peak_cw = cw_list[max_cw_idx]

        peak_position_ok = self.FN_PEAK_MIN <= peak_fn <= self.FN_PEAK_MAX

        has_violations = (
            any(len(v) > 0 for v in violations.values())
            or not peak_position_ok
        )

        return {
            "passed": not has_violations,
            "red_flag": has_violations,
            "details": {
                "peak_fn": peak_fn,
                "peak_cw": peak_cw,
                "peak_position_ok": peak_position_ok,
                "expected_peak_range": [self.FN_PEAK_MIN, self.FN_PEAK_MAX],
                "magnitude_violations": violations["magnitude_violations"],
                "negative_violations": violations["negative_violations"],
            },
            "summary": {
                "n_points": len(fn_list),
                "peak_at_fn": peak_fn,
                "peak_position_in_range": peak_position_ok,
            },
        }


# Registry of available checkers
CHECKER_REGISTRY: dict[str, BaseSidecarChecker] = {
    "holtrop_cd_re": HoltropCdReChecker(),
    "wave_cw_fn": WaveCwFnChecker(),
}


def run_checker(checker_name: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Run a named sidecar checker on the provided data.

    Args:
        checker_name: key in CHECKER_REGISTRY
        data: checker-specific input data

    Returns:
        Checker result dict with {passed, red_flag, details, ...}
    """
    if checker_name not in CHECKER_REGISTRY:
        return {
            "passed": False,
            "red_flag": True,
            "details": {"error": f"Unknown checker: {checker_name}"},
        }

    checker = CHECKER_REGISTRY[checker_name]
    result = checker.check(data)
    result["checker_name"] = checker.name
    result["output_type"] = checker.output_type
    return result


def run_all_checkers(data_by_type: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Run all registered checkers on their respective data.

    Args:
        data_by_type: {output_type: data_dict}

    Returns:
        List of checker results
    """
    results = []
    for output_type, checker in CHECKER_REGISTRY.items():
        if output_type in data_by_type:
            result = checker.check(data_by_type[output_type])
            result["checker_name"] = checker.name
            result["output_type"] = checker.output_type
            results.append(result)
    return results
