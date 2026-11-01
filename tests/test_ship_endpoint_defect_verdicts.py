"""Machine adjudication of the two ship-domain defects measured by the planner (m1780).

Defect 1 (SHIP-MAN-001): ``POST /api/v1/maneuvering-mmg/turning|zigzag`` with a legal
KVLCC2-class payload returned HTTP 500 — the endpoint inputs (``TurningTestInput`` /
``ZigzagTestInput``) drifted away from the internal ``MMGInput`` (``Hr`` required but
never supplied).  Defect 2 (SHIP-SEA-001): ``POST /api/v1/advanced/bem`` returned a
hardcoded 6-point RAO table, independent of hull, speed and frequency.

The suite consumes ``artifacts/ship_endpoint_verdicts.json`` (written by
``tools/ref_probe_8001.py``) and re-derives every verdict from the evidence stored
next to it, so a flipped verdict is rejected without human review.  When the platform
is unreachable the live assertions skip — unreachable is never treated as PASS.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import ref_probe_8001 as PROBE  # noqa: E402

VERDICT_ARTIFACT = os.path.join(ROOT, "artifacts", "ship_endpoint_verdicts.json")
PREFIX_BASELINE = os.path.join(ROOT, "artifacts",
                               "ship_endpoint_verdicts_prefix_baseline.json")


# ---------------------------------------------------------------------------
# helpers: injected transports (no live platform needed)
# ---------------------------------------------------------------------------
def prefix_platform_fetch():
    """Transport reproducing the *pre-repair* platform, byte for byte in shape."""

    def fetch(path, body=None, method=None):
        body = body or {}
        if path == PROBE.TURNING_PATH:
            if PROBE.MISSING_PARAM_FIELD not in body:
                detail = {"detail": [{"type": "missing",
                                      "loc": ["body", PROBE.MISSING_PARAM_FIELD],
                                      "msg": "Field required"}]}
                return 422, json.dumps(detail)
            return 500, "Internal Server Error"      # pydantic: Hr Field required
        if path == PROBE.ZIGZAG_PATH:
            return 500, "Internal Server Error"
        if path == PROBE.BEM_PATH:
            return 200, json.dumps({"rao": {
                "frequencies": list(PROBE.KNOWN_BEM_RAO_TABLE["frequencies"]),
                "heave": list(PROBE.KNOWN_BEM_RAO_TABLE["heave"]),
                "pitch": list(PROBE.KNOWN_BEM_RAO_TABLE["pitch"])}})
        return 404, "{}"

    return fetch


def unreachable_fetch():
    def fetch(path, body=None, method=None):
        return None, "URLError: [Errno 111] Connection refused"

    return fetch


def physical_rao(n=20):
    """1-DOF heave response curve: RAO(omega) = 1/sqrt((1-r^2)^2+(2 zeta r)^2)."""
    wn, zeta = 0.6, 0.15
    freqs = [0.1 + 1.9 * i / (n - 1) for i in range(n)]
    values = []
    for w in freqs:
        r = w / wn
        values.append(1.0 / math.sqrt((1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2))
    return freqs, values


def load_or_skip(path):
    if not os.path.exists(path):
        pytest.skip(f"{os.path.relpath(path, ROOT)} missing: run tools/ref_probe_8001.py")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def by_criterion(doc):
    return {r["criterion_id"]: r for r in doc["records"]}


# ---------------------------------------------------------------------------
# 1. committed verdict documents
# ---------------------------------------------------------------------------
def test_verdict_document_covers_two_defects_and_is_self_consistent():
    doc = load_or_skip(VERDICT_ARTIFACT)
    assert PROBE.verify_defect_document(doc) == []
    assert doc["self_check"]["ok"] is True
    subsystems = {r["subsystem"] for r in doc["records"]}
    assert subsystems == set(PROBE.DEFECT_SUBSYSTEMS)
    assert {r["defect_id"] for r in doc["records"]} == set(PROBE.DEFECT_IDS.values())
    criteria = {r["criterion_id"] for r in doc["records"]}
    assert {"C1", "C2", "C3", "C4", "C5", "C6", "L1", "L2"} <= criteria
    for record in doc["records"]:
        assert "http_status" in record and "verdict" in record
        assert record["verdict"] in PROBE.DOMAIN_VERDICTS
        assert record["ts"]


def test_prefix_baseline_freezes_the_pre_repair_evidence():
    """The FAIL/MOCK pair must stay on record even after a future repair."""
    doc = load_or_skip(PREFIX_BASELINE)
    assert doc.get("frozen") is True
    assert PROBE.verify_defect_document(doc) == []
    crit = by_criterion(doc)
    assert crit["C1"]["http_status"] == 500 and crit["C1"]["verdict"] == PROBE.FAIL
    assert crit["C2"]["http_status"] == 500 and crit["C2"]["verdict"] == PROBE.FAIL
    assert crit["L1"]["verdict"] == PROBE.FAIL          # contract defect
    assert crit["L2"]["verdict"] == "MOCK"              # hardcoded RAO table
    assert crit["C6"]["verdict"] == PROBE.FAIL          # fingerprint hit
    assert crit["C4"]["verdict"] == PROBE.FAIL          # 6 < 15 points


def test_no_pass_verdict_on_an_erroring_endpoint():
    """Durable invariant: a 4xx/5xx answer can never be laundered into PASS/REAL."""
    doc = load_or_skip(VERDICT_ARTIFACT)
    if not doc["platform_reachable"]:
        pytest.skip("platform unreachable: live verdicts withheld, not passed")
    for record in doc["records"]:
        if record["criterion_id"] == "C3":
            continue  # 422 is the *correct* answer for a deliberately incomplete payload
        if record["http_status"] is not None and record["http_status"] >= 400:
            assert record["verdict"] not in (PROBE.PASS, "REAL"), record["criterion_id"]


def test_live_platform_findings_are_the_measured_defects():
    """Pre-repair the recorded judgement is FAIL (contract) / MOCK (RAO) — not PASS."""
    doc = load_or_skip(VERDICT_ARTIFACT)
    if not doc["platform_reachable"]:
        pytest.skip("platform unreachable: cannot re-measure the defects")
    crit = by_criterion(doc)
    assert crit["L1"]["verdict"] in (PROBE.FAIL, PROBE.PASS)
    assert crit["L2"]["verdict"] in ("MOCK", "REAL", "STUB")
    assert not (crit["L1"]["verdict"] == PROBE.PASS
                and crit["C1"]["http_status"] == 500)


# ---------------------------------------------------------------------------
# 2. probe plumbing: one call, two new subsystems, unreachable != PASS
# ---------------------------------------------------------------------------
def test_one_probe_run_emits_two_new_subsystem_verdicts():
    records, summary = PROBE.run_probes(prefix_platform_fetch())
    doc = PROBE.build_defect_document(records, "injected://pre-repair-platform")
    assert PROBE.verify_defect_document(doc) == []
    subsystems = {r["subsystem"] for r in doc["records"]}
    assert subsystems == set(PROBE.DEFECT_SUBSYSTEMS)
    assert len(subsystems) >= 2
    crit = by_criterion(doc)
    assert crit["C1"]["http_status"] == 500 and crit["C1"]["verdict"] == PROBE.FAIL
    assert crit["C3"]["http_status"] == 422 and crit["C3"]["verdict"] == PROBE.PASS
    assert crit["L1"]["verdict"] == PROBE.FAIL
    assert crit["C6"]["verdict"] == PROBE.FAIL
    assert crit["L2"]["verdict"] == "MOCK"
    # legacy artifact compatibility: PASS/FAIL/ERROR still populated per record
    assert all(r["verdict"] in ("PASS", "FAIL", "ERROR") for r in records)
    assert any(r["subsystem"] == "maneuvering-mmg/contract" for r in records)


def test_unreachable_platform_is_withheld_never_pass():
    records, _ = PROBE.run_probes(unreachable_fetch())
    doc = PROBE.build_defect_document(records, "injected://down")
    assert doc["platform_reachable"] is False
    assert PROBE.verify_defect_document(doc) == []
    verdicts = {r["verdict"] for r in doc["records"]}
    assert verdicts == {PROBE.WITHHELD}
    assert by_criterion(doc)["L1"]["verdict"] == PROBE.WITHHELD
    assert by_criterion(doc)["L2"]["verdict"] == PROBE.WITHHELD


# ---------------------------------------------------------------------------
# 3. fingerprint detector, both directions
# ---------------------------------------------------------------------------
def test_fingerprint_detector_hits_injected_constant_table():
    for motion in ("heave", "pitch"):
        res = PROBE.constant_table_fingerprint(PROBE.KNOWN_BEM_RAO_TABLE[motion],
                                               PROBE.KNOWN_BEM_RAO_TABLE[motion])
        assert res["hit"] is True
        assert res["n_points"] == 6
    # default reference is the heave table shipped by advanced_theory.py:364-366
    assert PROBE.constant_table_fingerprint([0.8, 1.2, 1.5, 1.3, 1.0, 0.8])["hit"] is True


def test_fingerprint_detector_misses_physical_curve():
    _, heave = physical_rao(20)
    res = PROBE.constant_table_fingerprint(heave)
    assert res["hit"] is False and res["n_points"] == 20
    # a 6-point curve that is *not* the hardcoded table must not hit either
    assert PROBE.constant_table_fingerprint([0.9, 1.3, 1.6, 1.4, 1.1, 0.9])["hit"] is False


# ---------------------------------------------------------------------------
# 4. negative tests: tampered verdicts must be rejected
# ---------------------------------------------------------------------------
def _flip_L2_to_real(doc):
    by_criterion(doc)["L2"]["verdict"] = "REAL"


def _flip_L1_to_pass(doc):
    by_criterion(doc)["L1"]["verdict"] = PROBE.PASS


def _unreachable_but_passing(doc):
    doc["platform_reachable"] = False


def _drop_http_status(doc):
    by_criterion(doc)["C1"].pop("http_status")


def _tamper_evidence_not_verdict(doc):
    fingerprint = by_criterion(doc)["C6"]["evidence"]["fingerprint"]
    for motion in fingerprint:
        fingerprint[motion]["hit"] = False


def _tamper_status_not_verdict(doc):
    by_criterion(doc)["C1"]["evidence"]["http_status"] = 200


@pytest.mark.parametrize("mutate", [
    _flip_L2_to_real, _flip_L1_to_pass, _unreachable_but_passing,
    _drop_http_status, _tamper_evidence_not_verdict, _tamper_status_not_verdict,
])
def test_tampered_verdict_is_rejected(mutate):
    doc = copy.deepcopy(load_or_skip(PREFIX_BASELINE))
    assert PROBE.verify_defect_document(doc) == []
    mutate(doc)
    problems = PROBE.verify_defect_document(doc)
    assert problems, "tampered verdict document was accepted"


def test_verdict_document_cli_verify_exit_codes(tmp_path):
    doc = load_or_skip(PREFIX_BASELINE)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(doc), encoding="utf-8")
    assert PROBE.main(["--verify", str(good)]) == 0
    bad_doc = copy.deepcopy(doc)
    _flip_L2_to_real(bad_doc)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_doc), encoding="utf-8")
    assert PROBE.main(["--verify", str(bad)]) == 1


# ---------------------------------------------------------------------------
# 5. the probe's own reference payloads must be geometrically consistent
# ---------------------------------------------------------------------------
def test_reference_payload_geometry_is_consistent():
    """Cb/L/B gate before any number is compared: displacement = Cb*L*B*T*rho."""
    body = PROBE.TURNING_REQUEST
    rho_t_per_m3 = 1.025  # 1025 kg/m^3 in t/m^3; displacement is in tonnes
    implied = body["L"] * body["B"] * body["T"] * body["Cb"] * rho_t_per_m3
    rel = abs(implied - body["displacement"]) / body["displacement"]
    assert rel < 0.03
    assert 320.0 <= body["L"] <= 330.0 and 55.0 <= body["B"] <= 60.0
    # the two BEM hulls must be different ships, otherwise the invariance test is void
    assert PROBE.BEM_HULL_A["cb"] != PROBE.BEM_HULL_B["cb"]
    assert PROBE.BEM_HULL_A["length"] != PROBE.BEM_HULL_B["length"]