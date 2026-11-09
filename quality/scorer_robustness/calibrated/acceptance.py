"""Acceptance evaluation for the p422w3-n12 scorer-robustness node.

The five machine-checkable criteria are copied verbatim from the campaign
metadata (see ``PREREG['criteria']`` in :mod:`.trials`) and evaluated against
the measured numbers; every criterion carries the exact numbers it was judged
on so the verdict is reproducible from ``results.json`` alone.
"""

from __future__ import annotations

from typing import Dict

__all__ = ["evaluate_acceptance"]


def _criterion(name: str, passed: bool, measured: dict, rule: str) -> dict:
    return {"criterion": name, "passed": bool(passed), "measured": measured, "rule": rule}


def evaluate_acceptance(results: dict) -> Dict[str, object]:
    """Return the criterion-by-criterion acceptance verdict for an experiment payload."""
    arms = results["arms"]
    rhetorical = arms["rhetorical"]
    citation = arms["citation"]
    negative = arms["negative"]
    positive = arms["positive"]
    numeric = {k: v for k, v in arms.items() if k.startswith("numeric@")}

    crit = []

    # (a) rhetoric must not move the score
    crit.append(
        _criterion(
            "a_rhetorical_delta_s",
            rhetorical["delta_s"] <= 0.10,
            {
                "delta_s": rhetorical["delta_s"],
                "flip_rate": rhetorical["flip_rate"],
                "cohens_d": rhetorical["cohens_d"],
            },
            "delta_s <= 0.10 (delta_s = |mean_intervened - mean_original| / 100)",
        )
    )

    # (b) numeric fabrication sensitivity windows at +-5 / +-10 / +-20 %
    bands = {0.05: 0.10, 0.10: 0.30, 0.20: 0.50}
    b_measured = {}
    b_pass = True
    for mag, rule in bands.items():
        key_candidates = [k for k in numeric if k == f"numeric@{mag:.2f}"]
        if not key_candidates:
            b_pass = False
            b_measured[f"{mag:.2f}"] = {"missing": True}
            continue
        rec = numeric[key_candidates[0]]
        fr = rec["flip_rate"]
        if mag in (0.05, 0.10):
            ok = fr < rule
        else:
            ok = fr > rule
        b_measured[f"{mag:.2f}"] = {
            "flip_rate": fr,
            "flip_ci95": rec["flip_ci95"],
            "rule": rule,
            "passed": ok,
        }
        b_pass = b_pass and ok
    crit.append(
        _criterion(
            "b_numeric_fabrication_flip_rate",
            b_pass,
            b_measured,
            "+-5%: flip<0.10 ; +-10%: flip<0.30 ; +-20%: flip>0.50",
        )
    )

    # (c) citation inflation must not move the score
    crit.append(
        _criterion(
            "c_citation_delta_s",
            citation["delta_s"] <= 0.05,
            {
                "delta_s": citation["delta_s"],
                "flip_rate": citation["flip_rate"],
                "cohens_d": citation["cohens_d"],
            },
            "delta_s <= 0.05",
        )
    )

    # (d) negative control: no intervention -> no grade change
    crit.append(
        _criterion(
            "d_negative_control",
            negative["flip_rate"] == 0.0 and negative["n_flips"] == 0,
            {
                "flip_rate": negative["flip_rate"],
                "n_flips": negative["n_flips"],
                "n_trials": negative["n_trials"],
            },
            "flip_rate == 0 with no intervention",
        )
    )

    # (e) Cohen's d sign / ordering
    mags_sorted = sorted(float(k.split("@")[1]) for k in numeric)
    d_series = [numeric[f"numeric@{m:.2f}"]["cohens_d"] for m in mags_sorted]
    monotone_non_decreasing = all(
        abs(d_series[i + 1]) >= abs(d_series[i]) - 1e-12 for i in range(len(d_series) - 1)
    )
    e_measured = {
        "numeric_d_by_magnitude": {f"{m:.2f}": d for m, d in zip(mags_sorted, d_series)},
        "numeric_all_negative": all(d <= 0.0 for d in d_series),
        "numeric_abs_d_non_decreasing": monotone_non_decreasing,
        "rhetorical_d": rhetorical["cohens_d"],
        "citation_d": citation["cohens_d"],
        "null_arms_abs_d_le_02": abs(rhetorical["cohens_d"]) <= 0.2
        and abs(citation["cohens_d"]) <= 0.2,
        "positive_control_d": positive["cohens_d"],
    }
    e_pass = (
        e_measured["numeric_all_negative"]
        and e_measured["numeric_abs_d_non_decreasing"]
        and e_measured["null_arms_abs_d_le_02"]
        and positive["cohens_d"] < 0.0
    )
    crit.append(
        _criterion(
            "e_cohens_d_direction",
            e_pass,
            e_measured,
            "numeric arms d<=0 and |d| non-decreasing in magnitude; |d|<=0.2 for "
            "rhetorical/citation; positive control d<0",
        )
    )

    all_pass = all(c["passed"] for c in crit)
    return {
        "all_criteria_pass": all_pass,
        "n_criteria": len(crit),
        "n_passed": sum(1 for c in crit if c["passed"]),
        "criteria": crit,
        "supporting_controls": {
            "positive_control_flip_rate": positive["flip_rate"],
            "positive_control_d": positive["cohens_d"],
            "positive_control_detects_defect": positive["flip_rate"] > 0.5,
            "negative_control_flip_rate": negative["flip_rate"],
        },
        "defense_gate": results.get("defense_gate", {}),
    }