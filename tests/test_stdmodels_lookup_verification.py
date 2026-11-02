#!/usr/bin/env python3
"""``stdmodels.lookup`` 独立复核的回归测试（离线，不依赖 :8001）。

分两层，与 `tests/test_prop_openwater_audit.py` 同构：

1. **判据单元层**：对每条判据都注入"已知错 / 已知对"的合成样本，断言判据能分辨。
   一个永远 FAIL（或永远 PASS）的判据不构成证据，因此每条判据都至少有正负两例。
2. **快照回放层**：用冻结快照 `tests/fixtures/stdmodels/stdmodels_snapshot.json`
   （:8001 `standard-models/*` 于 2026-11-02 的只读抓取）复跑整份裁决，钉死结论与
   关键计数；快照缺失则 skip，保持离线可跑。

运行：python3 -m pytest tests/test_stdmodels_lookup_verification.py -q
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "validation"))

import verify_stdmodels_lookup as V  # noqa: E402
import references_stdmodels as R  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "tests", "fixtures", "stdmodels",
                        "stdmodels_snapshot.json")
AUDIT_JSON = os.path.join(ROOT, "artifacts", "stdmodels_audit", "audit.json")


def _snapshot() -> dict:
    if not os.path.exists(SNAPSHOT):
        pytest.skip("stdmodels snapshot fixture missing")
    return V.load_snapshot(SNAPSHOT)


def _hull(name="TEST", lpp=100.0, b=20.0, t=8.0, cb=0.70, disp=None):
    """A *self-consistent* hull at rho = 1.025 t/m^3 unless told otherwise."""
    cm, cp = 0.95, cb / 0.95
    vol = lpp * b * t * cb
    return {"name": name, "type": "container", "Lpp": lpp, "B": b, "T": t, "Cb": cb,
            "Cm": cm, "Cp": cp, "Cw": 0.8, "KG": 8.0, "DWT": 1000,
            "displacement": round(vol * 1.025, 3) if disp is None else disp,
            "design_speed_kn": 14.0, "source": "synthetic"}


# --------------------------------------------------------------- helpers / sanity
def test_reference_density_switch():
    assert R.reference_density(230.0) == pytest.approx(1.025)
    assert R.reference_density(5.0) == pytest.approx(1.0)


def test_rel_error_zero_reference():
    assert R.rel_error(0.0, 0.0) == 0.0
    assert R.rel_error(1.0, 0.0) == float("inf")


@pytest.mark.parametrize("ratio,expected", [
    (1027.0, "x1000"), (10.0, "x10"), (0.1, "x0.1"), (1.0, None), (2.0, None),
])
def test_unit_scale_diagnosis(ratio, expected):
    assert R.unit_scale_diagnosis(ratio) == expected


# ------------------------------------------------------------------ R2: Cb identity
def test_cb_identity_accepts_consistent_hull():
    out = V.check_cb_identity([_hull()])
    assert out["verdict"] == "PASS"
    assert out["n_violations"] == 0


def test_cb_identity_rejects_planted_mismatch():
    bad = _hull()
    bad["Cb"] = bad["Cm"] * bad["Cp"] + 0.02  # plant a 2 % identity break
    out = V.check_cb_identity([bad])
    assert out["verdict"] == "FAIL"
    assert out["n_violations"] == 1
    assert out["violations"][0]["abs_diff"] == pytest.approx(0.02, abs=1e-9)


def test_cb_identity_deduplicates_identical_rows():
    row = _hull()
    out = V.check_cb_identity([row, dict(row)])
    assert out["n_checked"] == 1


# ------------------------------------------------------- R3: displacement / density
def test_density_accepts_self_consistent_ship():
    out = V.check_displacement_density([_hull()])
    assert out["verdict"] == "PASS"
    assert out["n_violations"] == 0
    assert out["n_warnings"] == 0


def test_density_flags_decimal_slip():
    row = _hull("TEN-X")
    row["displacement"] = row["displacement"] * 10.0
    out = V.check_displacement_density([row])
    assert out["verdict"] == "FAIL"
    assert out["violations"][0]["unit_scale"] == "x10"


def test_density_flags_kg_instead_of_tonnes():
    row = _hull("MODEL", lpp=5.0, b=1.0, t=0.4, cb=0.6)
    vol = row["Lpp"] * row["B"] * row["T"] * row["Cb"]
    row["displacement"] = vol * 1000.0  # kg where tonnes were expected
    out = V.check_displacement_density([row])
    assert out["verdict"] == "FAIL"
    assert out["violations"][0]["unit_scale"] == "x1000"


def test_density_warn_band_is_not_a_failure():
    row = _hull()
    row["displacement"] = row["displacement"] * 1.10  # +10 % -> warning band
    out = V.check_displacement_density([row])
    assert out["verdict"] == "PASS"
    assert out["n_warnings"] == 1
    assert out["n_violations"] == 0


def test_density_skips_entries_without_displacement():
    row = _hull()
    row["displacement"] = 0.0
    out = V.check_displacement_density([row])
    assert out["n_checked"] == 0
    assert out["verdict"] == "PASS"


# ------------------------------------------------------------- R4: registry integrity
def _snap_with(ships, propellers=(), platforms=(), types=("container",)):
    return {"fetched_utc": "synthetic", "base": "synthetic", "endpoints": {
        "ships": {"status": 200, "body": {"ships": list(ships)}},
        "propellers": {"status": 200, "body": {"propellers": list(propellers)}},
        "platforms": {"status": 200, "body": {"platforms": list(platforms)}},
        "types": {"status": 200, "body": {"types": list(types)}},
        "regulations": {"status": 200, "body": {"regulations": []}},
        "spectrums": {"status": 200, "body": {"spectrums": []}},
    }}


def _integrity(ships, propellers=(), platforms=(), types=("container",)):
    snap = _snap_with(ships, propellers, platforms, types)
    return V.check_registry_integrity(snap, list(ships), list(propellers),
                                      list(platforms))


def test_integrity_accepts_clean_registry():
    out = _integrity([_hull()], [_hull("PROP")])
    assert out["verdict"] == "PASS"


def test_integrity_flags_propeller_in_ship_list():
    prop = _hull("PROP")
    out = _integrity([_hull(), prop], [prop])
    assert out["verdict"] == "FAIL"
    assert out["violations"]["propeller_in_ship_list"] == ["PROP"]


def test_integrity_flags_unknown_type():
    row = _hull()
    row["type"] = "not_a_type"
    out = _integrity([row])
    assert out["verdict"] == "FAIL"
    assert out["violations"]["unknown_type"][0]["type"] == "not_a_type"


def test_integrity_flags_cb_out_of_range():
    row = _hull()
    row["Cb"] = 1.4
    out = _integrity([row])
    assert out["verdict"] == "FAIL"
    assert any("Cb=" in r for v in out["violations"]["geometry_nonphysical"]
               for r in v.get("reasons", []))


def test_integrity_allows_deep_draft_spar():
    spar = _hull("Spar Platform", lpp=40.0, b=40.0, t=200.0, cb=0.785)
    spar["type"] = "offshore_platform"
    out = _integrity([spar], platforms=[spar], types=["offshore_platform"])
    assert out["verdict"] == "PASS", out["violations"]


def test_integrity_reports_cross_category_duplicate_as_soft():
    row = _hull("Twin")
    out = _integrity([row], platforms=[dict(row)])
    assert out["verdict"] == "PASS"
    assert out["soft"]["cross_category_duplicate"][0]["name"] == "Twin"


# ------------------------------------------------------------------ R5: aux registries
def test_aux_registries_accept_published_names():
    snap = _snap_with([], types=())
    snap["endpoints"]["spectrums"]["body"] = {"spectrums": [{"id": "jonswap"},
                                                            {"id": "ittc"}]}
    snap["endpoints"]["regulations"]["body"] = {"regulations": [
        {"code": "IS Code 2008"}, {"code": "EEDI/EEXI"}]}
    assert V.check_auxiliary_registries(snap)["verdict"] == "PASS"


def test_aux_registries_flag_unknown_spectrum():
    snap = _snap_with([], types=())
    snap["endpoints"]["spectrums"]["body"] = {"spectrums": [{"id": "made_up"}]}
    out = V.check_auxiliary_registries(snap)
    assert out["verdict"] == "FAIL"
    assert out["unknown_spectra"] == ["made_up"]


# ----------------------------------------------------------------- R6: propeller schema
def test_propeller_schema_pass_with_propeller_columns():
    out = V.check_propeller_schema([{"name": "P", "diameter": 0.3, "P_over_D": 1.08,
                                     "EAR": 0.6}])
    assert out["verdict"] == "PASS"


def test_propeller_schema_fails_on_hull_columns_only():
    out = V.check_propeller_schema([_hull("P")])
    assert out["verdict"] == "FAIL"
    assert out["columns_present"] == []


# --------------------------------------------------------------- R1: literature anchors
def test_anchors_pass_on_published_kcs():
    kcs = _hull("KCS", lpp=230.0, b=32.2, t=10.8, cb=0.6505,
                disp=R.SHIP_ANCHORS["KCS"]["displacement_volume"][0] * 1.025)
    out = V.check_literature_anchors([kcs], [])
    graded = [f for f in out["fields"] if f["verdict"] != "WITHHELD"]
    assert graded and all(f["verdict"] == "PASS" for f in graded)
    assert out["worst_abs_rel_error"] < 1e-9


def test_anchors_reject_off_by_20_percent_cb():
    kcs = _hull("KCS", lpp=230.0, b=32.2, t=10.8, cb=0.7806,  # +20 % on published Cb
                disp=R.SHIP_ANCHORS["KCS"]["displacement_volume"][0] * 1.025)
    out = V.check_literature_anchors([kcs], [])
    assert out["verdict"] == "FAIL"
    field = next(f for f in out["fields"] if f["case"] == "KCS" and f["field"] == "Cb")
    assert field["rel_error"] == pytest.approx(0.20, abs=1e-3)


def test_anchors_withheld_when_benchmark_absent():
    out = V.check_literature_anchors([], [])
    assert out["verdict"] == "WITHHELD"
    assert out["worst_abs_rel_error"] is None


# --------------------------------------------------------------- verdict aggregation
def test_verdict_is_worst_of_checks():
    # plant an R2 failure (Cb breaks the Cm*Cp identity) in an otherwise clean registry
    bad = _hull()
    bad["Cb"] = bad["Cb"] + 0.05
    snap = _snap_with([bad], propellers=[{"name": "P", "diameter": 0.3,
                                         "P_over_D": 1.08, "EAR": 0.6}])
    report = V.run(snap)
    grades = {c["id"]: c["verdict"] for c in report["checks"]}
    assert grades["R2"] == "FAIL"
    assert report["verdict"] == "FAIL"


def test_empty_propeller_registry_is_withheld_not_pass():
    snap = _snap_with([_hull()])
    report = V.run(snap)
    grades = {c["id"]: c["verdict"] for c in report["checks"]}
    assert grades["R6"] == "WITHHELD"
    assert report["verdict"] != "PASS"


def test_missing_endpoint_yields_withheld_not_pass():
    snap = _snap_with([_hull()])
    del snap["endpoints"]["ships"]
    report = V.run(snap)
    assert report["checks"][0]["id"] == "R0"
    assert report["checks"][0]["verdict"] == "WITHHELD"
    assert report["verdict"] != "PASS"


def test_markdown_renders_every_check():
    report = V.run(_snap_with([_hull()]))
    md = V.render_markdown(report)
    for chk in report["checks"]:
        assert f"| {chk['id']} |" in md
    assert "verdict" in md


# ------------------------------------------------------------- snapshot replay layer
def test_snapshot_replay_pins_the_published_verdict():
    report = V.run(_snapshot())
    grades = {c["id"]: c["verdict"] for c in report["checks"]}
    assert grades == {"R1": "PASS", "R2": "PASS", "R3": "FAIL", "R4": "FAIL",
                      "R5": "PASS", "R6": "FAIL"}, grades
    assert report["verdict"] == "FAIL"
    r1 = next(c for c in report["checks"] if c["id"] == "R1")
    assert r1["worst_abs_rel_error"] <= R.TOL_PASS  # anchors meet the 3 % standard
    r3 = next(c for c in report["checks"] if c["id"] == "R3")
    assert r3["n_violations"] >= 1
    # the planted-scale defects are diagnosed, not merely counted
    assert {"x1000", "x10"} <= {v["unit_scale"] for v in r3["violations"]}


def test_artifact_matches_snapshot_replay():
    if not os.path.exists(AUDIT_JSON):
        pytest.skip("audit artifact missing")
    with open(AUDIT_JSON, encoding="utf-8") as fh:
        art = json.load(fh)
    replay = V.run(_snapshot())
    assert art["verdict"] == replay["verdict"]
    assert {c["id"]: c["verdict"] for c in art["checks"]} == \
           {c["id"]: c["verdict"] for c in replay["checks"]}