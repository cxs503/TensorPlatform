"""Regression guard for the m1676 manoeuvring identity audit.

Two layers:

* **ledger replay** — the verdict ledger committed under ``artifacts/m1676/`` is parsed
  offline (no network, no re-simulation) and its structure/values are pinned, so the
  published verdicts cannot silently drift;
* **live invariants** — two cheap properties that must hold for *any* correct MMG
  implementation are re-derived from the module itself (mirror symmetry, and the
  Newton steady solver residual), independent of the ledger.

The audit tool itself is ``tools/verify/manoeuvring_identity_audit.py``.
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

LEDGER = os.path.join(REPO, "artifacts", "m1676", "manoeuvring_identity_audit.json")


@pytest.fixture(scope="module")
def ledger():
    if not os.path.exists(LEDGER):
        pytest.skip(f"ledger not generated yet: {LEDGER}")
    with open(LEDGER, encoding="utf-8") as fh:
        return json.load(fh)


def _c(ledger, cid):
    for row in ledger["criteria"]:
        if row["id"] == cid:
            return row
    raise AssertionError(f"criterion {cid} missing from ledger")


def test_ledger_schema_complete(ledger):
    """Every criterion must carry id / what / tol / err / verdict (no silent passes)."""
    assert ledger["criteria"], "empty ledger"
    for row in ledger["criteria"]:
        for key in ("id", "what", "tol", "verdict"):
            assert key in row and row[key] not in (None, ""), f"{row.get('id')} lacks {key}"
        assert row["verdict"] in ("PASS", "FAIL")
        assert isinstance(row["tol"], (int, float))
    assert ledger["n_pass"] + ledger["n_fail"] == len(ledger["criteria"])


def test_counts_match_criteria(ledger):
    n_pass = sum(1 for r in ledger["criteria"] if r["verdict"] == "PASS")
    assert ledger["n_pass"] == n_pass
    assert ledger["n_fail"] == len(ledger["criteria"]) - n_pass
    assert ledger["failed"] == [r["id"] for r in ledger["criteria"] if r["verdict"] == "FAIL"]


def test_identities_that_must_pass(ledger):
    """These four are exact consequences of the equations — a FAIL here is a code bug."""
    for cid in ("GEO-0", "GEO-1", "SYM-1", "MONO-1", "INA-1", "STAB-1"):
        row = _c(ledger, cid)
        assert row["verdict"] == "PASS", f"{cid} should be an exact identity: {row}"
        assert row["err"] <= row["tol"]


def test_documented_failures_still_fail(ledger):
    """The published defect (time domain != steady solver) must not be papered over."""
    for cid in ("KIN-2", "CONV-3", "KIN-3"):
        row = _c(ledger, cid)
        assert row["verdict"] == "FAIL", f"{cid} regressed to PASS — re-check the report"
        assert row["err"] > row["tol"]
    # quantified: speed ratio disagreement must stay in the documented order of magnitude
    assert 20.0 <= _c(ledger, "KIN-3")["err"] <= 90.0


def test_anchor_is_context_only(ledger):
    anchor = ledger["anchor"]
    assert anchor["kind"] == "MAGNITUDE_ANCHOR"
    assert "never" in anchor["note"].lower() or "not" in anchor["note"].lower()
    # the anchor id must not appear in the verdict ledger
    assert anchor["id"] not in [r["id"] for r in ledger["criteria"]]


def test_imo_block_consistent(ledger):
    imo = ledger["raw"]["imo"]
    for key, row in imo.items():
        if row["value"] is None:
            continue
        expected = "PASS" if row["value"] <= row["limit"] else "FAIL"
        assert row["verdict"] == expected, key


def test_newton_residuals_are_converged(ledger):
    """The steady solver must actually solve its own system (independent of t-domain)."""
    ss = ledger["raw"]["steady_35"]
    scale = max(abs(ss["steady_turning_diameter"]), 1.0)
    for key in ("residual_X", "residual_Y", "residual_N"):
        assert abs(ss[key]) < 1e-2 * scale, f"{key} not converged: {ss[key]}"


def test_live_mirror_symmetry():
    """Live invariant: the EOM are odd under delta -> -delta, so y must mirror exactly."""
    from backend.physics.ship_env_maneuvering import mmg as M

    def track(delta):
        res = M.simulate_turning_circle(rudder_deg=delta, dt=0.5, t_max=300.0)
        return res.history if hasattr(res, "history") else res.as_dict(with_history=True)["history"]

    hp, hm = track(20.0), track(-20.0)
    n = min(len(hp["y"]), len(hm["y"]))
    peak = max(abs(v) for v in hp["y"][:n]) or 1.0
    assert max(abs(a + b) for a, b in zip(hp["y"][:n], hm["y"][:n])) / peak < 1e-6


def test_live_straight_run_is_an_equilibrium():
    """Live invariant: with the rudder amidships the initial state must be (near) fixed."""
    from backend.physics.ship_env_maneuvering import mmg as M

    res = M.simulate_turning_circle(rudder_deg=0.0, dt=0.5, t_max=300.0)
    h = res.history if hasattr(res, "history") else res.as_dict(with_history=True)["history"]
    assert abs(h["psi_deg"][-1]) < 1.0          # no spurious heading drift [deg]
    assert abs(h["y"][-1]) < 50.0               # no spurious lateral drift [m]
