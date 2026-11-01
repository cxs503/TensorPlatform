"""Acceptance tests for the domain reference-anchor set (artifacts/ref/anchors/).

Coverage
  1  anchor file inventory / checksum self-verification (sha256 over payload)
  2  required schema fields + note_sha256 integrity per entry
  3  tolerance declarations exist and are non-zero
  4  independent recomputation of the anchors
       - ITTC-78: m0 = A/(4B), m0 = Hs^2/16, m1 vs numerical quadrature, m2 divergence
       - B4-70   : eta0 = J*KT/(2*pi*KQ) to 1e-12, table from a second transcription
       - KVLCC2/KCS: resistance breakdown cross-check
  5  probe (tools/ref_probe_8001.py) verdict plumbing: schema, >= 3 subsystems,
     injected-transport behaviour (PASS on anchor-consistent platform, FAIL on the
     pre-fix placeholder platform, HTTP 500 recorded as FAIL)
"""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import gen_ref_anchors as GEN  # noqa: E402
from tools import ref_anchor_lib as L  # noqa: E402
from tools import ref_probe_8001 as PROBE  # noqa: E402

ANCHOR_DIR = os.path.join(ROOT, "artifacts", "ref", "anchors")
ANCHOR_FILES = ("spectra_ittc_issc.json",
                "propeller_wageningen_b4_70.json",
                "resistance_kvlcc2_kcs.json")
PROBE_ARTIFACT = os.path.join(ROOT, "artifacts", "ref", "probe_verdicts_r1.json")


def load(name):
    with open(os.path.join(ANCHOR_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def entries(doc):
    return doc["payload"]["entries"]


def by_id(doc):
    return {e["id"]: e for e in entries(doc) if not e.get("divergent")}


# ---------------------------------------------------------------------------
# 1. inventory + checksum
# ---------------------------------------------------------------------------
def test_anchor_files_exist_and_parse():
    for name in ANCHOR_FILES:
        doc = load(name)
        assert doc["schema_version"] == "ref-anchor/1.0"
        assert doc["payload"]["entries"], name
        assert doc["payload"].get("source_citation"), name


def test_anchor_checksum_self_verifies():
    for name in ANCHOR_FILES:
        doc = load(name)
        assert L.verify_document(doc), f"checksum mismatch in {name}"
        assert len(doc["checksum"]["value"]) == 64


# ---------------------------------------------------------------------------
# 2. schema fields + note hashes
# ---------------------------------------------------------------------------
def test_schema_required_fields_present():
    required = {"id", "quantity", "value", "unit", "tolerance_rel",
                "source_citation", "note", "note_sha256"}
    for name in ANCHOR_FILES:
        for e in entries(load(name)):
            missing = required - set(e)
            assert not missing, f"{name}:{e.get('id')} missing {missing}"
            assert isinstance(e["source_citation"], str) and len(e["source_citation"]) > 20
            if not e.get("divergent"):
                assert isinstance(e["value"], (int, float))
                assert e["unit"]


def test_note_sha256_matches_note():
    for name in ANCHOR_FILES:
        for e in entries(load(name)):
            assert L.sha256_text(e["note"]) == e["note_sha256"], e["id"]


def test_entry_ids_unique_within_and_across_files():
    seen = set()
    for name in ANCHOR_FILES:
        for e in entries(load(name)):
            key = (name, e["id"])
            assert key not in seen
            seen.add(key)


# ---------------------------------------------------------------------------
# 3. tolerances
# ---------------------------------------------------------------------------
def test_tolerances_declared_and_non_zero():
    for name in ANCHOR_FILES:
        numeric = [e for e in entries(load(name)) if not e.get("divergent")]
        assert numeric, name
        for e in numeric:
            assert e["tolerance_rel"] is not None, e["id"]
            assert 0.0 < e["tolerance_rel"] < 0.5, e["id"]


def test_geometry_gate_tolerances_are_tight():
    doc = load("resistance_kvlcc2_kcs.json")
    ents = by_id(doc)
    for ship in ("KVLCC2", "KCS"):
        for q in ("Lpp", "B", "T", "Cb", "design_speed"):
            assert ents[f"{ship}_{q}"]["tolerance_rel"] <= 1e-3
        assert ents[f"{ship}_S"]["tolerance_rel"] <= 0.02


# ---------------------------------------------------------------------------
# 4a. spectra
# ---------------------------------------------------------------------------
def test_spectrum_has_at_least_three_parameter_cases():
    doc = load("spectra_ittc_issc.json")
    cases = {e["id"].rsplit("_", 1)[0] for e in entries(doc)
             if e["id"].startswith("ittc78_") and "direct_ab" not in e["id"]}
    hs = {e["case"]["Hs_m"] for e in entries(doc) if "case" in e and "Hs_m" in e["case"]}
    assert len(cases) >= 3
    assert len(hs) >= 3


def test_spectrum_m0_equals_A_over_4B():
    doc = load("spectra_ittc_issc.json")
    ents = by_id(doc)
    checked = 0
    for e in entries(doc):
        if e["quantity"] != "m0" or "case" not in e:
            continue
        case = e["case"]
        if "Hs_m" in case:  # generated from (Hs, T1)
            A = ents[e["id"].replace("_m0", "_A")]["value"]
            B = ents[e["id"].replace("_m0", "_B")]["value"]
            assert A == pytest.approx(L.ittc78_ab(case["Hs_m"], case["T1_s"])[0], rel=1e-12)
            assert B == pytest.approx(L.ittc78_ab(case["Hs_m"], case["T1_s"])[1], rel=1e-12)
        else:               # generated from a direct (A, B) pair
            A, B = case["A"], case["B"]
        assert B > 0
        assert e["value"] == pytest.approx(A / (4.0 * B), rel=1e-12)
        checked += 1
    assert checked >= 4


def test_spectrum_m0_matches_Hs_squared_over_16():
    doc = load("spectra_ittc_issc.json")
    ents = by_id(doc)
    for e in entries(doc):
        if e["quantity"] != "m0_identity_Hs2_over_16":
            continue
        m0 = ents[e["id"].replace("_m0_identity", "_m0")]["value"]
        assert m0 == pytest.approx(e["value"], rel=0.01)
        assert e["value"] == pytest.approx(e["case"]["Hs_m"] ** 2 / 16.0, rel=1e-12)


def test_spectrum_m1_matches_numerical_quadrature():
    doc = load("spectra_ittc_issc.json")
    # integrate numerically with a substitution-safe scheme: integrate in
    # t = omega and split, since the integrand is regular for omega > 0.
    for e in entries(doc):
        if e["quantity"] != "m1" or "case" not in e:
            continue
        case = e["case"]
        A = case["A"] if "A" in case else L.ittc78_ab(case["Hs_m"], case["T1_s"])[0]
        B = case["B"] if "B" in case else L.ittc78_ab(case["Hs_m"], case["T1_s"])[1]

        # convolution-free numeric check: substitute u = B*omega^-4, then
        # m1 = (A/4) B^(-3/4) * integral_0^inf u^(-1/4) e^-u du and the gamma
        # integral is evaluated numerically here (independent of math.gamma).
        from scipy.integrate import quad
        integral, _ = quad(lambda u: u ** -0.25 * math.exp(-u), 0, 200,
                           limit=400, epsabs=1e-13, epsrel=1e-12)
        m1_numeric = 0.25 * A * B ** -0.75 * integral
        assert e["value"] == pytest.approx(m1_numeric, rel=1e-6)
        # and the direct frequency-space integral of omega*S(omega)
        direct, _ = quad(lambda w: w * L.spectrum_value(A, B, w), 1e-6, 50.0,
                         limit=800, epsabs=1e-14, epsrel=1e-11)
        assert e["value"] == pytest.approx(direct, rel=1e-3)


def test_spectrum_brief_formula_discrepancy_documented():
    """The brief's m1 = sqrt(pi)/4 * B^-3/4 is wrong by construction; the anchor
    must use the exact Gamma(3/4)/4 coefficient and record the discrepancy."""
    A, B = L.ittc78_ab(5.0, 9.5)
    exact = L.m1_closed(A, B)
    brief = L.m1_sqrt_pi_form(A, B)
    assert exact == pytest.approx(0.3063541755 * A * B ** -0.75, rel=1e-6)
    assert abs(brief - exact) / exact > 0.40          # 44% off, not a rounding issue
    assert "Gamma(3/4)/4" in load("spectra_ittc_issc.json")["payload"]["moment_formulae"]["m1"]


def test_spectrum_m2_divergence_declared_and_demonstrated():
    doc = load("spectra_ittc_issc.json")
    decl = [d for d in doc["payload"]["declarations"] if d["quantity"] == "m2"]
    assert decl and decl[0]["divergent"] is True
    assert decl[0]["value"] is None
    assert "diverge" in decl[0]["note"].lower()

    from scipy.integrate import quad
    A, B = L.ittc78_ab(5.0, 9.5)
    integrals = []
    for lo in (1e-1, 1e-2, 1e-3):
        val, _ = quad(lambda w: w ** 2 * L.spectrum_value(A, B, w), lo, 5.0, limit=500)
        integrals.append(val)
    assert integrals[0] < integrals[1] < integrals[2]   # grows as the cutoff -> 0


# ---------------------------------------------------------------------------
# 4b. propeller
# ---------------------------------------------------------------------------
def test_b4_70_table_grid_and_eta0_identity():
    doc = load("propeller_wageningen_b4_70.json")
    ents = by_id(doc)
    js = sorted({e["J"] for e in entries(doc)})
    assert len(js) >= 6 and js[0] == pytest.approx(0.1) and js[-1] == pytest.approx(0.8)
    for J in js:
        kt = ents[f"b4_70_J{J:.1f}_KT"]["value"]
        tenkq = ents[f"b4_70_J{J:.1f}_10KQ"]["value"]
        eta = ents[f"b4_70_J{J:.1f}_eta0"]["value"]
        kq = tenkq / 10.0
        assert eta == pytest.approx(J * kt / (2.0 * math.pi * kq), abs=1e-12)
        assert 0.0 < eta < 1.0


def test_b4_70_table_recomputed_from_second_transcription():
    doc = load("propeller_wageningen_b4_70.json")
    ents = by_id(doc)
    cc = load("b_series_coefficients_crosscheck.json")
    assert cc["deviation"]["max_abs_term_deviation_KQ"] < 1e-5
    for J in L.B4_70_J_POINTS:
        kt2 = L._poly_eval(cc["transcription_b"]["KT_terms"], J, 1.0, 0.70, 4.0)
        kq2 = L._poly_eval(cc["transcription_b"]["KQ_terms"], J, 1.0, 0.70, 4.0)
        assert ents[f"b4_70_J{J:.1f}_KT"]["value"] == pytest.approx(kt2, rel=5e-3)
        assert ents[f"b4_70_J{J:.1f}_10KQ"]["value"] == pytest.approx(10 * kq2, rel=5e-3)


def test_b4_70_is_physically_consistent():
    doc = load("propeller_wageningen_b4_70.json")
    ents = by_id(doc)
    kt = [ents[f"b4_70_J{0.1 * i:.1f}_KT"]["value"] for i in range(1, 9)]
    eta = [ents[f"b4_70_J{0.1 * i:.1f}_eta0"]["value"] for i in range(1, 9)]
    assert all(kt[i] > kt[i + 1] for i in range(len(kt) - 1))       # KT falls with J
    assert eta[-1] > eta[0]                                          # eta rises with J
    assert max(eta) < 0.75                                           # below eta_D limits
    assert kt[0] > 0.3 and kt[-1] > 0.0                              # inside series range
    # zero-thrust point inside the J <= 1.4 validity window
    lo, hi = 0.5, 1.4
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if L.b_series_kt(mid, 1.0, 0.70, 4) > 0:
            lo = mid
        else:
            hi = mid
    assert 0.9 < lo < 1.4


def test_propeller_placeholder_declaration_present():
    doc = load("propeller_wageningen_b4_70.json")
    decl = [d for d in doc["payload"]["declarations"] if d["quantity"] == "KQ/KT"]
    assert decl and decl[0]["divergent"] is True
    assert "constant" in decl[0]["note"]


# ---------------------------------------------------------------------------
# 4c. resistance
# ---------------------------------------------------------------------------
def test_kvlcc2_anchor_and_breakdown_consistent():
    doc = load("resistance_kvlcc2_kcs.json")
    ents = by_id(doc)
    rt = ents["KVLCC2_RT_full"]["value"]
    assert rt == pytest.approx(1.96e6, rel=1e-9)
    cross = doc["payload"]["resistance_breakdown_crosscheck"]["KVLCC2"]
    assert cross["CT_anchor"] == pytest.approx(rt / cross["q_S_N"], rel=1e-9)
    # implied wave resistance coefficient must be physically plausible (0 < CW < 1e-3)
    assert 0.0 < cross["CW_implied"] < 1e-3
    assert res_is_close(ents["KVLCC2_CT_full"]["value"], cross["CT_anchor"])


def res_is_close(a, b):
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def test_kcs_magnitude_anchor_matches_digitized_efd_wave_resistance():
    """Anchor cross-check: the residual wave coefficient implied by R_T = 1.80 MN at
    Fn=0.26 must agree with the digitized EFD value C_W(Fn=0.26) = 4.8e-4
    (Kinaci, Sukas & Bal 2015, Fig. 17) to within 20%."""
    doc = load("resistance_kvlcc2_kcs.json")
    cross = doc["payload"]["resistance_breakdown_crosscheck"]["KCS"]
    assert cross["CW_implied"] == pytest.approx(4.8e-4, rel=0.2)


def test_resistance_ships_have_full_parameter_sets():
    doc = load("resistance_kvlcc2_kcs.json")
    ents = by_id(doc)
    for ship in ("KVLCC2", "KCS"):
        for q in ("Lpp", "B", "T", "Cb", "S", "nabla", "design_speed"):
            assert ents[f"{ship}_{q}"]["value"] > 0
        assert 0.5 < ents[f"{ship}_Cb"]["value"] < 0.9
        assert 200.0 <= ents[f"{ship}_Lpp"]["value"] <= 400.0


# ---------------------------------------------------------------------------
# 5. probe plumbing
# ---------------------------------------------------------------------------
def test_probe_artifact_has_at_least_three_subsystems():
    assert os.path.exists(PROBE_ARTIFACT), "run tools/ref_probe_8001.py first"
    with open(PROBE_ARTIFACT, encoding="utf-8") as fh:
        doc = json.load(fh)
    subs = {r["subsystem"] for r in doc["verdicts"]}
    assert len(subs) >= 3
    assert doc["summary"]["n_subsystems"] == len(subs)
    for r in doc["verdicts"]:
        for field in ("subsystem", "status_code", "value", "anchor", "rel_err",
                      "verdict", "ts"):
            assert field in r
        assert r["verdict"] in ("PASS", "FAIL", "ERROR")


def test_probe_artifact_records_pre_fix_failures():
    """Regression evidence: the un-repaired platform must fail every probed subsystem."""
    with open(PROBE_ARTIFACT, encoding="utf-8") as fh:
        doc = json.load(fh)
    subs = doc["summary"]["subsystems"]
    for name in ("seakeeping/wave-spectrum", "propulsion/wageningen-b",
                 "resistance/calculations-resistance"):
        assert name in subs
        assert subs[name]["PASS"] == 0
        assert subs[name]["FAIL"] + subs[name]["ERROR"] > 0
    # the wave-spectrum crash must keep its status code
    spec = [r for r in doc["verdicts"] if r["subsystem"] == "seakeeping/wave-spectrum"]
    assert spec and spec[0]["status_code"] == 500 and spec[0]["verdict"] == "FAIL"
    # the wageningen endpoint must look like a placeholder (constant KQ/KT)
    prop = [r for r in doc["verdicts"] if r["subsystem"] == "propulsion/wageningen-b"]
    assert any(r.get("placeholder_flag") for r in prop)
    # the resistance probe must be off by more than 100%
    res = [r for r in doc["verdicts"] if r["subsystem"] == "resistance/calculations-resistance"]
    assert res[0]["rel_err"] > 1.0


class _FakeTransport:
    """Deterministic stand-in for the platform, used to test the probe logic."""

    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    def __call__(self, path, body=None, method=None):
        self.calls.append((path, body))
        if self.mode == "pre_fix":
            if "wave-spectrum" in path:
                return 500, "Internal Server Error"
            if "wageningen-b" in path:
                J = body["J"]
                kt = max(0.05, min(0.5, 0.3 * body["P_D"] - 0.2 * J)) * (0.5 + body["Ae_Ao"])
                return 200, json.dumps({"KT": round(kt, 4), "KQ": round(kt / 10.0, 5),
                                        "eta0": 0.6366})
            return 200, json.dumps({"rt": 5939.14})
        if self.mode == "anchor_consistent":
            if "wave-spectrum" in path:
                A, B = L.ittc78_ab(5.0, 9.5)
                omegas = [0.05 + 0.0025 * i for i in range(1400)]
                spectrum = [{"omega": w, "S": L.spectrum_value(A, B, w)} for w in omegas]
                return 200, json.dumps({"spectrum": spectrum, "m0": L.m0_closed(A, B)})
            if "wageningen-b" in path:
                pt = L.open_water_point(body["J"], body["P_D"], body["Ae_Ao"], body["Z"])
                return 200, json.dumps({"KT": pt["KT"], "KQ": pt["KQ"], "10KQ": pt["10KQ"],
                                        "eta0": pt["eta0"]})
            return 200, json.dumps({"rt": 1960.0})
        if self.mode == "unreachable":
            return None, "URLError: connection refused"
        raise AssertionError(self.mode)


def test_probe_flags_pre_fix_placeholder_platform():
    fake = _FakeTransport("pre_fix")
    records, summary = PROBE.run_probes(fake)
    assert summary["n_subsystems"] >= 3
    assert summary["overall_verdict"] == "FAIL"
    spec = [r for r in records if r["subsystem"] == "seakeeping/wave-spectrum"]
    assert spec[0]["verdict"] == "FAIL" and spec[0]["status_code"] == 500
    prop = [r for r in records if r["subsystem"] == "propulsion/wageningen-b"]
    kt_recs = [r for r in prop if r["quantity"] == "KT"]
    assert len(kt_recs) == 8 and all(r["verdict"] == "FAIL" for r in kt_recs)
    assert any(r.get("placeholder_flag") for r in prop)
    assert sum(1 for r in prop if r["verdict"] == "FAIL") >= 20
    res = [r for r in records if r["subsystem"] == "resistance/calculations-resistance"]
    assert res[0]["verdict"] == "FAIL" and res[0]["rel_err"] > 1.0


def test_probe_passes_anchor_consistent_platform():
    fake = _FakeTransport("anchor_consistent")
    records, summary = PROBE.run_probes(fake)
    bad = [r for r in records if r["verdict"] != "PASS"]
    assert not bad, bad[:3]
    assert summary["overall_verdict"] == "PASS"
    assert summary["n_subsystems"] >= 3


def test_probe_marks_unreachable_platform_as_error():
    records, summary = PROBE.run_probes(_FakeTransport("unreachable"))
    assert all(r["verdict"] == "ERROR" for r in records)
    assert summary["overall_verdict"] == "ERROR"


def test_probe_verdict_records_are_json_serialisable():
    records, _ = PROBE.run_probes(_FakeTransport("pre_fix"))
    json.dumps(records)  # must not raise


# ---------------------------------------------------------------------------
# generators stay reproducible
# ---------------------------------------------------------------------------
def test_generators_are_reproducible(tmp_path):
    GEN.main(["--outdir", str(tmp_path)])
    for name in ANCHOR_FILES:
        fresh = json.load(open(os.path.join(tmp_path, name), encoding="utf-8"))
        stored = load(name)
        assert fresh["payload"] == stored["payload"], name