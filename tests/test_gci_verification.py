"""Tests for the numerical-V&V / GCI verification layer (mission m1646).

The toolkit's correctness is pinned on three independent grounds:

1. **Synthetic exact model** -- if ``phi_i = phi_ext + a*h_i**p`` is imposed
   exactly, the estimator must return that ``p`` and that ``phi_ext``; any
   algebra slip in the Richardson/GCI chain shows up here to round-off.
2. **Textbook limit** -- for a constant refinement ratio the general
   bisection solve must reduce to ``p = ln(1/R)/ln(r)``, so results stay
   comparable with the published GCI literature.
3. **Manufactured solutions** -- real discretisations with formal orders 1 and
   2 must have their order recovered and their GCI band must bracket the exact
   value (an uncertainty band that never contains truth is useless).

Reported numbers are the *observed* ones; nothing is hand-copied from a paper.
"""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.verification import (  # noqa: E402
    FS_THREE_GRIDS,
    FS_TWO_GRIDS,
    GCIRegime,
    gci,
    observed_order,
    refinement_ratio_ok,
    richardson_extrapolate,
)
from examples.verification.gci_mms_study import (  # noqa: E402
    run_study,
    solve_advdiff_1d,
    solve_poisson_2d,
)


# ---------------------------------------------------------------- helpers ---
def synth(phi_ext: float, a: float, p: float, h):
    """Exact asymptotic-sequence model: phi_i = phi_ext + a * h_i**p."""
    return [phi_ext + a * (hi ** p) for hi in h]


# ------------------------------------------------ 1. synthetic exact model ---
@pytest.mark.parametrize("p_true", [1.0, 1.5, 2.0, 3.0])
def test_synthetic_constant_ratio_recovers_order_and_extrapolation(p_true):
    h = [1 / 80.0, 1 / 40.0, 1 / 20.0]
    phi = synth(1.0, 0.7, p_true, h)
    res = gci(h, phi, fs=FS_THREE_GRIDS)

    assert res.regime == GCIRegime.MONOTONE
    assert res.asymptotic is True
    assert res.p == pytest.approx(p_true, rel=1e-6)
    # Extrapolation must recover the imposed h -> 0 limit.
    assert res.phi_ext == pytest.approx(1.0, rel=1e-9)
    assert res.r21 == pytest.approx(2.0) and res.r32 == pytest.approx(2.0)


def test_synthetic_nonuniform_ratio_recovers_order():
    """r21 != r32 is the case where the textbook formula is invalid."""
    h = [1 / 90.0, 1 / 60.0, 1 / 37.5]  # r21 = 1.5, r32 = 1.6
    p_true = 3.0
    phi = synth(2.5, -0.4, p_true, h)
    res = gci(h, phi)

    assert res.p == pytest.approx(p_true, rel=1e-6)
    assert res.phi_ext == pytest.approx(2.5, rel=1e-9)
    # The constant-ratio shortcut is measurably wrong here -- and says so.
    assert res.p_constant_ratio is not None
    assert abs(res.p_constant_ratio - p_true) > 0.1
    assert any("non-uniform" in n for n in res.notes)


def test_general_solve_reduces_to_textbook_constant_ratio_formula():
    h = [1 / 64.0, 1 / 32.0, 1 / 16.0]
    phi = synth(1.0, 0.5, 2.0, h)
    res = gci(h, phi)
    eps21, eps32 = phi[1] - phi[0], phi[2] - phi[1]
    p_textbook = abs(math.log(abs(eps21 / eps32))) / math.log(2.0)
    assert res.p == pytest.approx(p_textbook, rel=1e-9)
    assert res.p_constant_ratio == pytest.approx(p_textbook, rel=1e-9)


def test_gci_matches_definition_and_relates_to_extrapolated_error():
    h = [1 / 64.0, 1 / 32.0, 1 / 16.0]
    phi = synth(1.0, 0.5, 2.0, h)
    res = gci(h, phi, fs=FS_THREE_GRIDS)

    eps21 = phi[1] - phi[0]
    e_a21 = abs(eps21 / phi[0])
    expected = FS_THREE_GRIDS * e_a21 / (res.r21 ** res.p - 1.0)
    assert res.gci_fine == pytest.approx(expected, rel=1e-12)
    # e_ext is the GCI at Fs=1 to leading order in the relative error.
    assert res.gci_fine / FS_THREE_GRIDS == pytest.approx(res.e_ext21, rel=5e-3)
    # GCI must shrink as the solution error shrinks.
    assert res.gci_fine_pct == pytest.approx(100.0 * res.gci_fine, rel=1e-12)


# ------------------------------------------------- 2. non-asymptotic cases ---
def test_oscillatory_convergence_falls_back_to_range_bound():
    """Alternating changes (R < 0) must not be silently extrapolated."""
    h = [1 / 8.0, 1 / 4.0, 1 / 2.0]
    phi = [1.00, 1.06, 1.03]
    res = gci(h, phi)

    assert res.regime == GCIRegime.OSCILLATORY
    assert res.p is None and res.phi_ext is None
    assert res.asymptotic is False
    spread = max(phi) - min(phi)
    assert res.gci_fine == pytest.approx(FS_THREE_GRIDS * spread, rel=1e-12)
    assert any("not in asymptotic range" in n for n in res.notes)


def test_divergent_refinement_is_rejected():
    h = [1 / 8.0, 1 / 4.0, 1 / 2.0]
    # Finest-first: change on (h1,h2) exceeds the change on (h2,h3) -> R > 1,
    # i.e. the sequence is leaving the asymptotic range under refinement.
    phi = [1.0, 3.0, 4.5]
    res = gci(h, phi)
    assert res.regime == GCIRegime.DIVERGENT
    assert res.p is None and res.asymptotic is False


def test_identical_solutions_give_zero_uncertainty_without_crashing():
    h = [1 / 8.0, 1 / 4.0, 1 / 2.0]
    res = gci(h, [2.0, 2.0, 2.0])
    assert res.p is None
    assert res.gci_fine == pytest.approx(0.0)
    assert res.asymptotic is False


def test_small_refinement_ratio_is_flagged():
    h = [1.0, 1.1, 1.21]  # r = 1.1 < 1.3
    phi = synth(1.0, 0.5, 2.0, h)
    res = gci(h, phi)
    assert refinement_ratio_ok(res.r21) is False
    assert any("refinement ratio below" in n for n in res.notes)


def test_assumed_order_path_uses_two_grid_safety_factor():
    h = [1 / 64.0, 1 / 32.0, 1 / 16.0]
    phi = synth(1.0, 0.5, 2.0, h)
    res = gci(h, phi, p_assumed=2.0, fs=FS_TWO_GRIDS)
    assert res.p == pytest.approx(2.0)
    assert res.fs == pytest.approx(3.0)
    assert res.phi_ext == pytest.approx(1.0, rel=1e-12)
    assert any("p supplied by caller" in n for n in res.notes)


# ------------------------------------------------------- 3. input contract ---
@pytest.mark.parametrize("h,phi,frag", [
    ([1 / 4.0, 1 / 2.0], [1.0, 2.0], "exactly three"),
    ([1 / 4.0, 1 / 2.0, 1.0], [1.0, 2.0], "exactly three"),
    ([1 / 2.0, 1 / 4.0, 1 / 8.0], [1.0, 2.0, 3.0], "strictly increasing"),
])
def test_input_validation(h, phi, frag):
    with pytest.raises(ValueError, match=frag):
        gci(h, phi)


def test_observed_order_returns_none_on_zero_change():
    p, regime, note = observed_order(0.0, 1.0, 2.0, 2.0)
    assert p is None and "zero solution change" in note


def test_richardson_extrapolate_sign_conventions():
    # Monotone convergence from above: extrapolation must undershoot phi1.
    assert richardson_extrapolate(1.01, 1.04, 2.0, 2.0) == pytest.approx(1.0, rel=1e-12)
    # Converging from below.
    assert richardson_extrapolate(0.99, 0.96, 2.0, 2.0) == pytest.approx(1.0, rel=1e-12)
    with pytest.raises(ValueError):
        richardson_extrapolate(1.0, 2.0, 2.0, 0.0)


# ------------------------------------------------ 4. manufactured solutions ---
def test_mms_central_second_order_recovers_formal_order():
    phi = [solve_advdiff_1d(n, scheme="central") for n in (20, 40, 80)]
    res = gci([1 / 80.0, 1 / 40.0, 1 / 20.0], list(reversed(phi)), fs=FS_THREE_GRIDS)
    assert res.p == pytest.approx(2.0, abs=0.05)
    assert abs(res.phi_ext - 1.0) < 1e-6
    # Extrapolation must beat the fine grid by orders of magnitude.
    assert abs(res.phi_ext - 1.0) < abs(res.phi[0] - 1.0) * 1e-2


def test_mms_upwind_first_order_recovers_formal_order():
    phi = [solve_advdiff_1d(n, scheme="upwind") for n in (80, 160, 320)]
    res = gci([1 / 320.0, 1 / 160.0, 1 / 80.0], list(reversed(phi)))
    assert res.p == pytest.approx(1.0, abs=0.1)
    assert abs(res.phi_ext - 1.0) < 1e-3


def test_mms_poisson_band_brackets_exact_value():
    phi = [solve_poisson_2d(n) for n in (16, 32, 64)]
    res = gci([1 / 64.0, 1 / 32.0, 1 / 16.0], list(reversed(phi)))
    assert res.p == pytest.approx(2.0, abs=0.05)
    exact = 1.0
    assert abs(res.phi[0] - exact) <= res.gci_fine * abs(res.phi[0])


def test_full_study_passes_both_acceptance_criteria():
    study = run_study()
    assert set(study["cases"]) == {"advdiff-central", "advdiff-upwind", "poisson2d-fv5"}
    for name, case in study["cases"].items():
        assert case["order_recovered"], f"{name}: order not recovered (p={case['gci']['p']})"
        assert case["band_contains_truth"], f"{name}: GCI band misses the exact value"
        assert case["extrapolated_rel_error"] < 1e-2, f"{name}: extrapolation not converged"

# ------------------------------------------------------------- 5. CLI gate ---
def test_cli_gate_passes_when_grids_are_asymptotic(tmp_path):
    from tools.gci_report import main as cli_main

    study = tmp_path / "ok.json"
    study.write_text(json.dumps({
        "h": ["1/64", "1/32", "1/16"],  # fraction strings are accepted
        "phi": [1.0002008218, 1.0008035777, 1.0032189644],
        "quantity": "poisson2d centre",
    }))
    out = tmp_path / "res.json"
    assert cli_main([str(study), "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["verification_status"] == "PASS"
    assert payload["result"]["p"] == pytest.approx(2.0, abs=0.05)


def test_cli_gate_fails_loudly_outside_asymptotic_range(tmp_path):
    from tools.gci_report import main as cli_main

    study = tmp_path / "bad.json"
    study.write_text(json.dumps({
        "h": [0.125, 0.25, 0.5], "phi": [1.0, 3.0, 4.5], "quantity": "divergent",
    }))
    out = tmp_path / "res.json"
    assert cli_main([str(study), "--out", str(out)]) == 1
    payload = json.loads(out.read_text())
    assert payload["verification_status"] == "UNVERIFIED"
    assert payload["result"]["p"] is None


def test_cli_sort_option_orders_grids_finest_first(tmp_path):
    from tools.gci_report import main as cli_main

    study = tmp_path / "unsorted.json"
    study.write_text(json.dumps({
        "h": [0.0625, 0.015625, 0.03125],
        "phi": [1.0032189644, 1.0002008218, 1.0008035777],
        "sort": True,
    }))
    out = tmp_path / "res.json"
    assert cli_main([str(study), "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["result"]["phi"] == pytest.approx(
        [1.0002008218, 1.0008035777, 1.0032189644])
