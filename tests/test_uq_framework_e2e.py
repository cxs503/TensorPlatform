"""C5/C7 acceptance: the framework end to end, and its zero-domain-semantics rule.

C5 -- nine cases, three per uncertainty category, all driven through the single
entry point ``run_uq(solver, config)``:

* **input propagation**: ``linear/normal+uniform``, ``linear/triangular+normal``,
  ``quadratic/interaction`` -- each scored against closed-form moments;
* **numerical error**: Euler (p=1), three-point elliptic (p=2), RK4 (p=4) -- each
  scored against its formal order;
* **model form**: affine bias ensemble, truncation ensemble resolved by BMA, and
  a common-bias ensemble -- each scored against its known truth.

C7 -- the automatic report must be machine-parsable: the JSON round-trips through
``UQResult.from_dict``, carries the criterion verdicts with the numbers that
decided them, and ships the two required figures with SHA-256 digests that match
the bytes on disk.

Zero-domain rule -- the package (and these tests) must contain none of the
platform's domain vocabulary, checked with a word-boundary scan, so the framework
stays reusable by any domain repository.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.uq_framework import (  # noqa: E402
    InputDistribution,
    UQConfig,
    UQResult,
    load_report,
    run_uq,
    write_report,
)
from backend.services.uq_framework.pce import latin_hypercube  # noqa: E402
from backend.services.uq_framework.acceptance_cases import (  # noqa: E402
    DISTS_3 as DISTS_3_SHARED,
    DISTS_NU as DISTS_NU_SHARED,
    DISTS_TN as DISTS_TN_SHARED,
    all_cases,
    full_matrix_case,
    truncation_truth_reference,
)
from backend.services.uq_framework.synthetic import (  # noqa: E402
    diffusion_bvp,
    linear_response,
    model_ensemble,
)

PKG = Path(_ROOT) / "backend" / "services" / "uq_framework"

DISTS_NU = DISTS_NU_SHARED
DISTS_TN = DISTS_TN_SHARED
DISTS_3 = DISTS_3_SHARED

CASES = {}
for _category, _cases in all_cases().items():
    CASES.update(_cases)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_each_case_passes_its_criteria(case_id):
    """C5: every one of the nine cases passes its own criteria."""
    solvers, cfg, expected, _reference = CASES[case_id]()
    res = run_uq(solvers, cfg)
    for key in expected:
        rec = res.criteria[key]
        assert rec["status"] == "pass", (case_id, key, rec)
        assert rec["value"] is not None and np.isfinite(rec["value"])
    # criteria without evidence are not silently 'pass'
    for key in ("C3_gci_order", "C4_model_form"):
        if key not in expected:
            assert res.criteria[key]["status"] == "not_evaluated", (case_id, key)


def test_three_categories_three_cases_each_are_all_green():
    """C5 summary: the acceptance matrix, as the ledger will record it."""
    summary = {}
    for category, prefix in (("input_propagation", "in_"), ("numerical_error", "num_"),
                             ("model_form", "mf_")):
        rows = {}
        for case_id in sorted(c for c in CASES if c.startswith(prefix)):
            solvers, cfg, expected, _reference = CASES[case_id]()
            res = run_uq(solvers, cfg)
            rows[case_id] = {k: res.criteria[k]["status"] for k in expected}
        assert len(rows) >= 3, (category, rows)
        assert all(s == "pass" for r in rows.values() for s in r.values()), rows
        summary[category] = rows
    assert len(summary) == 3


# --------------------------------------------------------------------------- #
# C7 -- automatic, machine-parsable report
# --------------------------------------------------------------------------- #
_full_case = full_matrix_case


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_each_case_passes_its_criteria(case_id):
    """C5: every one of the nine cases passes its own criteria."""
    solvers, cfg, expected, _reference = CASES[case_id]()
    res = run_uq(solvers, cfg)
    for key in expected:
        rec = res.criteria[key]
        assert rec["status"] == "pass", (case_id, key, rec)
        assert rec["value"] is not None and np.isfinite(rec["value"])
    # criteria without evidence are not silently 'pass'
    for key in ("C3_gci_order", "C4_model_form"):
        if key not in expected:
            assert res.criteria[key]["status"] == "not_evaluated", (case_id, key)


def test_three_categories_three_cases_each_are_all_green():
    """C5 summary: the acceptance matrix, as the ledger will record it."""
    summary = {}
    for category, prefix in (("input_propagation", "in_"), ("numerical_error", "num_"),
                             ("model_form", "mf_")):
        rows = {}
        for case_id in sorted(c for c in CASES if c.startswith(prefix)):
            solvers, cfg, expected, _reference = CASES[case_id]()
            res = run_uq(solvers, cfg)
            rows[case_id] = {k: res.criteria[k]["status"] for k in expected}
        assert len(rows) >= 3, (category, rows)
        assert all(s == "pass" for r in rows.values() for s in r.values()), rows
        summary[category] = rows
    assert len(summary) == 3


# --------------------------------------------------------------------------- #
# C7 -- automatic, machine-parsable report
# --------------------------------------------------------------------------- #
def test_report_is_machine_parsable_and_carries_the_figures(tmp_path):
    solvers, cfg, _ = _full_case()
    res = run_uq(solvers, cfg)
    bundle = write_report(res, tmp_path)

    # 1. JSON re-reads and round-trips into the result object
    doc = load_report(bundle.json_path)
    back = UQResult.from_dict({k: v for k, v in doc.items()
                               if k not in ("figures", "report_version", "criteria_summary")})
    assert back.case_id == res.case_id
    assert back.mean == pytest.approx(res.mean, rel=1e-12)

    # 2. the verdicts travel with the numbers that decided them
    crit = doc["criteria"]
    assert crit["C1_mc_mean"]["status"] in ("pass", "fail")
    assert "threshold" in crit["C1_mc_mean"] and "value" in crit["C1_mc_mean"]
    assert doc["criteria_summary"]["all_pass"] is True
    assert doc["criteria_summary"]["statuses"]["C3_gci_order"] == "pass"

    # 3. both required figures exist, with matching digests
    for key in ("confidence_interval", "sensitivity"):
        fig = doc["figures"][key]
        path = Path(fig["path"])
        assert path.exists() and path.stat().st_size > 1000
        import hashlib
        assert hashlib.sha256(path.read_bytes()).hexdigest() == fig["sha256"]
        assert fig["png_base64"].startswith("iVBORw0KGgo")  # PNG magic
    assert doc["provenance"]["framework"] == "uq_framework/v1"
    assert doc["numerical"]["discretization"]["order_ok"] is True
    assert doc["numerical"]["iteration"]["ok"] is True
    assert doc["numerical"]["roundoff"]["rel_diff"] > 0
    assert doc["model_form"]["deviation_vs_truth"] < 0.10


def test_report_marks_missing_evidence_as_not_evaluated(tmp_path):
    func, meta = linear_response([2.0, 3.0], 1.0)
    ref = meta["reference"](DISTS_NU)
    res = run_uq(func, UQConfig("sparse", DISTS_NU, propagation_method="monte_carlo",
                                n_mc=300, sobol=False,
                                reference={"mean": ref["mean"], "std": ref["std"]}))
    bundle = write_report(res, tmp_path, make_figures=False)
    doc = load_report(bundle.json_path)
    assert doc["criteria"]["C3_gci_order"]["status"] == "not_evaluated"
    assert doc["figures"] == {}
    assert doc["criteria_summary"]["all_pass"] is True  # evaluated ones all pass


# --------------------------------------------------------------------------- #
# zero domain semantics
# --------------------------------------------------------------------------- #
def _leak_patterns():
    """The platform's own domain denylist (data, not code)."""
    sys.path.insert(0, str(Path(_ROOT, "tools")))
    from audit.domain_leak_scan import compile_patterns, load_denylist

    return compile_patterns(load_denylist())


def test_package_contains_no_domain_vocabulary():
    patterns = _leak_patterns()
    offenders = {}
    for path in sorted(PKG.glob("*.py")):
        text = path.read_text()
        for word, rx in patterns:
            if rx.search(text):
                offenders.setdefault(path.name, []).append(word)
    assert offenders == {}, offenders


def test_tests_contain_no_domain_vocabulary():
    patterns = _leak_patterns()
    offenders = {}
    for path in sorted(Path(_ROOT, "tests").glob("test_uq_framework_*.py")):
        text = path.read_text()
        for word, rx in patterns:
            if rx.search(text):
                offenders.setdefault(path.name, []).append(word)
    assert offenders == {}, offenders


def test_regex_and_json_roundtrip_of_the_report_are_stable(tmp_path):
    """Same config + same seed => identical report payload (reproducibility)."""
    solvers, cfg, _ = _full_case()
    a = write_report(run_uq(solvers, cfg), tmp_path / "a", make_figures=False)
    b = write_report(run_uq(solvers, cfg), tmp_path / "b", make_figures=False)
    def _strip(obj):
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items()
                    if not k.endswith("seconds") and k != "generated_at"}
        if isinstance(obj, list):
            return [_strip(v) for v in obj]
        return obj

    for key in ("mean", "variance", "std", "criteria", "propagation", "numerical"):
        assert json.dumps(_strip(a.payload[key]), sort_keys=True) == json.dumps(
            _strip(b.payload[key]), sort_keys=True), key


# --------------------------------------------------------------------------- #
# the acceptance runner itself (artifact generation must stay reproducible)
# --------------------------------------------------------------------------- #
def test_acceptance_runner_emits_a_consistent_summary(tmp_path):
    """C7: the published artifact path is exercised by the suite, not by hand."""
    import importlib.util

    runner_path = Path(_ROOT, "tools", "uq_framework", "run_acceptance.py")
    spec = importlib.util.spec_from_file_location("uq_acceptance_runner", runner_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main(["--outdir", str(tmp_path), "--fast", "--no-figures"])
    assert rc == 0
    summary = json.loads((tmp_path / "acceptance_summary.json").read_text())
    assert summary["framework"] == "uq_framework/v1"
    assert all(summary["verdict"].values()), summary["verdict"]
    for category, rows in summary["matrix"].items():
        assert len(rows) >= 3, category
        for case_id, row in rows.items():
            assert row["all_expected_pass"], (case_id, row["criteria"])
            assert Path(row["report_json"]).exists()
    # the Monte Carlo convergence table shows the honest scale of the 2 % rule
    conv = summary["mc_convergence"]
    assert conv[0]["variance_rel_err_median"] > conv[-1]["variance_rel_err_median"]
    assert conv[-1]["variance_rel_err_median"] < 0.02
    # GCI order recovery for three different formal orders
    assert {r["p_expected"] for r in summary["gci_order_table"]} == {1.0, 2.0, 4.0}
    for row in summary["gci_order_table"]:
        assert row["deviation"] < 0.10
