"""
Tests: held-out question bank coverage for stability / propulsion / green /
seakeeping, and end-to-end verifier behavior (pass on reference pilot,
fail on a deliberately broken candidate).

Run: python3 -m pytest tools/verifier/tests/test_domain_pilots.py -q
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from question_bank import QuestionBank  # noqa: E402
from verifier import Verifier  # noqa: E402
from pilots.domain_pilots import candidate as pilot  # noqa: E402

DOMAINS = ["stability", "propulsion", "green", "seakeeping"]
MIN_PER_DOMAIN = {"stability": 4, "propulsion": 3, "green": 2, "seakeeping": 2}


@pytest.fixture(scope="module")
def bank():
    return QuestionBank()


def test_bank_has_new_domains(bank):
    for d, n in MIN_PER_DOMAIN.items():
        qs = bank.list_questions(domain=d, committed_only=True)
        assert len(qs) >= n, f"domain {d}: {len(qs)} < {n} committed questions"


def _J(x):
    return json.loads(x) if isinstance(x, str) else x


def test_question_integrity(bank):
    for d in DOMAINS:
        for q in bank.list_questions(domain=d, committed_only=True):
            assert str(q["committed"]).lower() == "true"
            assert q["commit_hash"], f"{d}/{q['title']} not frozen"
            prov = _J(q["provenance"])
            assert prov.get("source"), f"{d}/{q['title']} missing provenance"
            for k, t in _J(q["tolerances"]).items():
                assert t > 0, f"{d}/{q['title']} tolerance {k} <= 0"


def test_verifier_passes_reference_pilots(bank):
    v = Verifier(qbank_path=bank.db_path, verifier_id="tp-ver-m1569")
    for d in DOMAINS:
        rep = v.verify_candidate("pilot-domain-v1", pilot, d,
                                 candidate_version="1.0.0")
        assert rep.overall_verdict == "pass", f"{d}: {rep.overall_verdict}"
        assert rep.fail_count == 0 and rep.error_count == 0, d
        assert rep.pass_count == rep.total_questions > 0, d


def _broken_stability(spec: dict) -> dict:
    """Deliberately wrong wall-sided GZ (sinφ instead of sin²φ arm)."""
    f = spec["formula"]
    if f == "wall_sided_gz":
        phi = math.radians(spec["phi_deg"])
        gz = spec["GM"] * math.sin(phi) + (spec["B"] / 2.0) * math.sin(phi) * math.tan(phi)
        return {"GZ": gz}
    return pilot(spec)


def test_verifier_fails_broken_candidate(bank):
    v = Verifier(qbank_path=bank.db_path, verifier_id="tp-ver-m1569")
    rep = v.verify_candidate("broken-wall-sided-v0", _broken_stability, "stability",
                             candidate_version="0.0.1")
    assert rep.overall_verdict == "fail"
    assert rep.fail_count >= 1
    # the GZ question must be the one that fails
    failed = [r for r in rep.results if r.verdict == "fail"]
    assert any("GZ" in r.question_title for r in failed)


def test_expected_values_independent_recompute(bank):
    """Third implementation (in-test) must reproduce bank expected values."""
    G = 9.81
    for q in bank.list_questions(committed_only=True):
        d, s = q["domain"], _J(q["specification"])
        f = s["formula"]
        exp = _J(q["expected_result"])
        if f == "barge_km":
            got = s["T"] / 2 + s["B"] ** 2 / (12 * s["T"])
        elif f == "wall_sided_gz":
            phi = math.radians(s["phi_deg"])
            got = (s["GM"] + s["B"] / 2 * math.tan(phi)) * math.sin(phi) ** 2
        elif f == "vanishing_angle":
            got = math.atan(2 * abs(s["GM"]) / s["B"])
        elif f == "roll_period":
            got = 2 * math.pi * math.sqrt(s["I44"] / (s["delta_kg"] * G * s["GM"]))
        elif f == "disk_eta_ideal":
            a = (-1 + math.sqrt(1 + 4 * s["C_T"])) / 2
            got = 1 / (1 + a)
        elif f == "disk_induced":
            got = (-1 + math.sqrt(1 + 4 * s["C_T"])) / 2
        elif f == "disk_ideal_power":
            a = (-1 + math.sqrt(1 + 4 * s["C_T"])) / 2
            got = s["T_N"] * s["V0"] * (1 + a)
        elif f in ("co2_mgo", "co2_lng"):
            w = 0.863 if f == "co2_mgo" else 0.812
            got = s["m_fuel_kg"] * w * 44.01 / 12.011
        elif f == "encounter_freq":
            mu = math.radians(s["mu_deg"])
            got = s["omega"] - s["V"] * math.cos(mu) * s["omega"] ** 2 / G
        elif f == "heave_period":
            got = 2 * math.pi / math.sqrt(
                s["rho"] * s["nabla_m3"] * G / (s["m_kg"] * (1 + s["a33_ratio"])))
        else:
            continue
        key = next(iter(exp))
        assert abs(got - exp[key]) <= 1e-9 * max(1.0, abs(exp[key])), f"{d}/{f}"
