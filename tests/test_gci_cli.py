"""CLI + end-to-end tests for the ITTC GCI chain.

Acceptance criteria covered
---------------------------
(a) part of the >= 12-test suite;
(e) the CLI emits one JSON line with all required fields, and the three-state
    verdict is decided programmatically by ``U_V`` and the experimental
    deviation (exit status 0/1/2);
the solver case reproduces its analytic reference and converges at the
expected second order.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gci.anchors import ANCHORS, load_anchors  # noqa: E402
from tools.gci.cases import ANALYTIC_CA, circle_added_mass  # noqa: E402
from tools.gci.cli import main as cli_main  # noqa: E402
from tools.gci.core import gci  # noqa: E402

REQUIRED_FIELDS = ("r21", "r32", "p", "R_G", "GCI21", "GCI32", "U_V", "verdict")


def _run_cli(capsys, argv):
    code = cli_main(argv)
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0, "CLI must emit exactly one JSON line"
    return code, json.loads(out)


# ---------------------------------------------------------------------------
# (e) CLI contract
# ---------------------------------------------------------------------------
def test_cli_emits_all_required_fields(capsys):
    code, rec = _run_cli(
        capsys,
        ["--f1", "0.9998380299669203", "--f2", "0.9995025002950253",
         "--f3", "0.998166119701974", "--r", "2", "--reference", "1.0"],
    )
    assert code == 0
    for key in REQUIRED_FIELDS:
        assert key in rec, key
    assert rec["verdict"] == "PASS"
    assert rec["p"] == pytest.approx(1.994302904724744, rel=1e-12)
    assert rec["U_V"] == pytest.approx(rec["GCI21"], rel=1e-15)
    assert 0.0 < rec["R_G"] < 1.0


@pytest.mark.parametrize(
    "f1,f2,f3,reference,expected,exit_code",
    [
        # tight band, small deviation -> PASS
        (1.0, 0.99, 0.9504, 1.0, "PASS", 0),
        # tight band, 20% bias -> FAIL (solver distortion)
        (1.2, 1.2 * (1 - 1e-3), 1.2 * (1 - 1e-3) * (1 - 4e-3), 1.0, "FAIL", 1),
        # oscillatory triplet -> fallback order -> wide U_V -> WITHHELD
        (1.0, 1.30, 1.20, 1.0, "WITHHELD", 2),
    ],
)
def test_cli_verdict_three_states(capsys, f1, f2, f3, reference, expected, exit_code):
    code, rec = _run_cli(
        capsys,
        ["--f1", repr(f1), "--f2", repr(f2), "--f3", repr(f3), "--r", "2",
         "--reference", repr(reference), "--gate", "0.15"],
    )
    assert rec["verdict"] == expected
    assert code == exit_code
    # the verdict must be reproducible from the reported numbers alone
    if rec["U_V"] >= rec["gate"]:
        assert rec["verdict"] == "WITHHELD"
    else:
        assert rec["verdict"] == ("PASS" if rec["deviation"] <= rec["gate"] else "FAIL")


def test_cli_withheld_never_reports_pass_or_fail(capsys):
    code, rec = _run_cli(capsys, ["--f1", "1.0", "--f2", "1.30", "--f3", "1.20",
                                  "--r", "2", "--reference", "1.0"])
    assert rec["convergence"] == "oscillatory"
    assert rec["p_source"].startswith("fallback:")
    assert rec["verdict"] == "WITHHELD" and code == 2


def test_cli_anchor_replay(capsys):
    anchor = {a.id: a for a in load_anchors()}["ITTC-P2-EXACT"]
    code, rec = _run_cli(capsys, ["--anchor", "ITTC-P2-EXACT", "--reference", "1.0"])
    assert code == 0
    assert rec["anchor"] == "ITTC-P2-EXACT"
    assert rec["anchor_sha256"] == anchor.sha256
    assert rec["p"] == pytest.approx(anchor.p_expected, rel=1e-12)
    assert rec["GCI21"] == pytest.approx(anchor.gci21_expected, rel=1e-12)


def test_cli_unknown_anchor_is_an_error(capsys):
    code, rec = _run_cli(capsys, ["--anchor", "NO-SUCH-ANCHOR"])
    assert code == 3
    assert "unknown anchor" in rec["error"]


def test_cli_bad_geometry_is_an_error_exit(capsys):
    code, rec = _run_cli(capsys, ["--f1", "1.0", "--f2", "0.99", "--f3", "0.95", "--r", "1.0"])
    assert code == 3
    assert "error" in rec


# ---------------------------------------------------------------------------
# end-to-end numerical case
# ---------------------------------------------------------------------------
def test_case_matches_analytic_reference_within_three_percent():
    fine = circle_added_mass(160, 256)
    assert fine.rel_err_vs_analytic < 0.03
    assert fine.rel_err_vs_analytic < 1e-3  # actually ~1.6e-4
    assert fine.resid < 1e-8
    assert abs(fine.ca - ANALYTIC_CA) < 5e-4


def test_case_refinement_is_monotone_and_second_order():
    grids = [(40, 64), (80, 128), (160, 256)]
    runs = [circle_added_mass(*g) for g in grids]
    errs = [abs(r.ca - ANALYTIC_CA) for r in runs]
    assert errs[0] > errs[1] > errs[2], "error must shrink monotonically"
    for coarse, fine in zip(errs, errs[1:]):
        assert 0.15 < fine / coarse < 0.4, "refinement ratio 2 should shrink the error ~4x"
    res = gci(runs[2].ca, runs[1].ca, runs[0].ca, 2.0, 2.0)
    assert res.p_used == pytest.approx(2.0, abs=0.15)
    assert 0.0 < res.R_G < 0.5


def test_e2e_case_gate_adjudication():
    runs = [circle_added_mass(*g) for g in [(40, 64), (80, 128), (160, 256)]]
    res = gci(runs[2].ca, runs[1].ca, runs[0].ca, 2.0, 2.0)
    dev = abs(runs[2].ca - ANALYTIC_CA) / ANALYTIC_CA
    assert res.u_v < 0.15, "this case must be verifiable, otherwise the gate is vacuous"
    assert dev < 0.15
    from tools.gci.core import verdict

    assert verdict(res.u_v, dev, 0.15) == "PASS"


def test_anchor_internal_row_matches_the_solver():
    anchor = {a.id: a for a in ANCHORS}["REPO-CIRCLE-CA-E2E"]
    runs = [circle_added_mass(*g) for g in [(40, 64), (80, 128), (160, 256)]]
    assert abs(runs[2].ca - anchor.f1) < 1e-9
    assert abs(runs[1].ca - anchor.f2) < 1e-9
    assert abs(runs[0].ca - anchor.f3) < 1e-9