"""Tests for the pre-solve geometry / mesh validation gate (campaign node p372w2-n04).

Every assertion is tied to an independently computable reference: closed-form
volumes (box), analytic hull displacement (Wigley: Cb = 4/9), closed-form mesh
volumes (structured hex), and the analytic y+ definition. Run with::

    python3 -m pytest tests/test_geometry_validation_pipeline.py -q
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.geometry_validation import (  # noqa: E402
    AuditLog, GeometryValidationError, ProfileError, load_profile, validate_batch,
    validate_item, verify_audit_chain)
from backend.services.geometry_validation import geometry_extract as gx  # noqa: E402
from backend.services.geometry_validation import mesh_quality as mq  # noqa: E402
from backend.services.geometry_validation.cli import main as cli_main  # noqa: E402
from backend.services.geometry_validation.pipeline import exit_code  # noqa: E402
from tools.geometry_validation import fixtures_gen as fg  # noqa: E402

PROFILE_PATH = os.path.join(ROOT, "config/geometry_validation/profiles/hull_mesh_v1.json")
WIGLEY = dict(L=10.0, B=1.0, T=0.625)
ANALYTIC_CB = 4.0 / 9.0
BOX = dict(L=10.0, B=4.0, T=2.0)


@pytest.fixture(scope="module")
def profile():
    return load_profile(PROFILE_PATH)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------
def test_profile_loads_with_cited_envelopes(profile):
    assert profile.profile_id == "hull_mesh_v1"
    assert len(profile.sha256) == 64
    ids = [e["id"] for e in profile.envelopes]
    assert "physical_realizability" in ids and "holtrop_mennen_1982" in ids
    # every envelope must carry a citation: an uncited threshold is not auditable
    assert all(e.get("citation") for e in profile.envelopes)
    assert profile.declared_tolerance_rel == pytest.approx(0.01)


def test_profile_rejects_impossible_tolerance():
    with pytest.raises(ProfileError):
        load_profile(overrides={"geometry": {"declared_tolerance_rel": 2.0}})


def test_profile_rejects_open_bounds_pair():
    bad = {"envelopes": [{"id": "x", "bounds": {"cb": [None, None]}}]}
    with pytest.raises(ProfileError):
        load_profile(overrides=bad)


def test_neutral_default_profile_needs_no_domain_data():
    p = load_profile()
    assert p.envelopes == []
    assert p.geometry["declared_map"] == {}


# ---------------------------------------------------------------------------
# surface geometry: closed-form references
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("draft", [0.5, 1.0, 1.5, 2.0])
def test_box_volume_and_waterplane_exact(draft):
    tri = fg.box_triangles(**BOX)
    m = gx.geometry_metrics({"triangles": tri}, draft=draft)
    v_analytic = BOX["L"] * BOX["B"] * draft
    assert m["volume_submerged"] == pytest.approx(v_analytic, rel=1e-12)
    # Aw comes from a central difference of the displaced volume, so it carries the
    # finite-difference truncation error rather than exact round-off
    assert m["waterplane_area"] == pytest.approx(BOX["L"] * BOX["B"], rel=1e-6)
    assert m["block_coefficient"] == pytest.approx(1.0, rel=1e-12)
    assert m["waterplane_coefficient"] == pytest.approx(1.0, rel=1e-6)
    assert m["topology"]["watertight"] is True


def test_wigley_block_coefficient_converges_to_four_ninths():
    errors = []
    for n_u, n_v in [(20, 8), (40, 16), (80, 32)]:
        tri = fg.wigley_triangles(n_u=n_u, n_v=n_v, **WIGLEY)
        m = gx.geometry_metrics({"triangles": tri}, draft=WIGLEY["T"])
        assert m["topology"]["watertight"] is True
        errors.append(abs(m["block_coefficient"] - ANALYTIC_CB) / ANALYTIC_CB)
    assert errors[1] < 0.01              # 1% tolerance is met already at 40x16
    assert errors[2] < errors[1] < errors[0]   # monotone convergence


def test_wigley_waterplane_area_matches_analytic_profile():
    tri = fg.wigley_triangles(n_u=80, n_v=32, **WIGLEY)
    for z in (0.0, 0.3):
        m = gx.geometry_metrics({"triangles": tri}, draft=max(z, 1e-6))
        analytic = (2 * WIGLEY["L"] * WIGLEY["B"] / 3.0) * (1 - (z / WIGLEY["T"]) ** 2)
        assert m["waterplane_area"] == pytest.approx(analytic, rel=0.005)


def test_mixed_winding_is_repaired_and_volume_preserved(tmp_path):
    clean = fg.wigley_triangles(n_u=20, n_v=8, mixed_winding=False, **WIGLEY)
    mixed = fg.wigley_triangles(n_u=20, n_v=8, mixed_winding=True, rng_seed=3, **WIGLEY)
    m_clean = gx.geometry_metrics({"triangles": clean}, draft=WIGLEY["T"])
    m_mixed = gx.geometry_metrics({"triangles": mixed}, draft=WIGLEY["T"])
    assert m_mixed["topology"]["watertight"] is True
    assert m_mixed["orientation_repair"]["flipped_faces"] > 0
    assert m_mixed["volume_submerged"] == pytest.approx(m_clean["volume_submerged"], rel=1e-12)


@pytest.mark.parametrize("suffix", ["stl", "obj"])
def test_readers_agree_on_volume(tmp_path, suffix):
    tri = fg.wigley_triangles(n_u=20, n_v=8, **WIGLEY)
    path = os.path.join(str(tmp_path), f"hull.{suffix}")
    if suffix == "stl":
        fg.write_stl_binary(path, tri)
    else:
        fg.write_obj(path, tri)
    surface = gx.read_triangles(path)
    m = gx.geometry_metrics(surface, draft=WIGLEY["T"])
    assert m["volume_submerged"] == pytest.approx(ANALYTIC_CB * 6.25, rel=0.01)


def test_open_shell_is_rejected(tmp_path):
    tri = fg.wigley_triangles(n_u=12, n_v=5, omit_bottom=True, **WIGLEY)
    path = fg.write_obj(os.path.join(str(tmp_path), "open.obj"), tri)
    m = gx.geometry_metrics(gx.read_triangles(path), draft=WIGLEY["T"])
    assert m["topology"]["watertight"] is False
    assert m["topology"]["boundary_edges"] > 0


def test_truncated_stl_raises_parse_code(tmp_path):
    path = fg.write_truncated_stl(os.path.join(str(tmp_path), "bad.stl"))
    with pytest.raises(GeometryValidationError) as exc:
        gx.read_triangles(path)
    assert exc.value.code == "E_PARSE"


def test_iges_without_tessellator_is_withheld_not_faked(tmp_path, monkeypatch):
    monkeypatch.delenv("GEOMETRY_IGES_TESSELLATOR", raising=False)
    path = os.path.join(str(tmp_path), "part.iges")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("IGES fixture\nS      1\n")
    with pytest.raises(GeometryValidationError) as exc:
        gx.read_triangles(path)
    assert exc.value.code == "E_FORMAT_UNSUPPORTED"


# ---------------------------------------------------------------------------
# interceptors: declared values and applicability envelopes
# ---------------------------------------------------------------------------
def test_declared_comparison_tolerance_boundary(profile):
    tri = fg.wigley_triangles(n_u=80, n_v=32, **WIGLEY)
    metrics = gx.geometry_metrics({"triangles": tri}, draft=WIGLEY["T"])
    from backend.services.geometry_validation import interceptor
    ok = interceptor.check_declared(metrics, {"block_coefficient": ANALYTIC_CB * 1.005},
                                    profile)
    blocked = interceptor.check_declared(metrics, {"block_coefficient": ANALYTIC_CB * 1.05},
                                         profile)
    # 0.5% deviation is inside the 1% band: no blocking finding, only the
    # aggregated "some declared keys were not supplied" warning
    assert [f.code for f in ok["findings"] if f.severity == "error"] == []
    assert [f.code for f in ok["findings"]] == ["W_GEOM_DECLARED_MISSING"]
    codes = [f.code for f in blocked["findings"] if f.severity == "error"]
    assert codes == ["E_GEOM_DECLARED_MISMATCH"]
    row = [c for c in blocked["comparisons"] if c["declared_key"] == "block_coefficient"][0]
    assert row["status"] == "mismatch"
    assert row["rel_error"] == pytest.approx(0.05, abs=1e-3)


def test_envelope_warn_vs_block(profile):
    from backend.services.geometry_validation import interceptor
    box_metrics = gx.geometry_metrics({"triangles": fg.box_triangles(**BOX)},
                                      draft=BOX["T"])
    res = interceptor.check_envelopes(box_metrics, profile)
    codes = {f.code for f in res["findings"]}
    assert "W_DOMAIN_OUT_OF_ENVELOPE" in codes          # Cb=1 leaves the merchant envelope
    assert "E_DOMAIN_OUT_OF_ENVELOPE" not in codes      # ... but Cb=1 is still physical
    status = {e["id"]: e["status"] for e in res["envelopes"]}
    assert status["holtrop_mennen_1982"] == "outside"
    assert status["physical_realizability"] == "inside"

    double = np.vstack([fg.box_triangles(**BOX),
                        fg.box_triangles(BOX["L"] * 0.98, BOX["B"] * 0.98,
                                         BOX["T"] * 0.98)])
    m2 = gx.geometry_metrics({"triangles": double}, draft=BOX["T"])
    assert m2["block_coefficient"] > 1.0
    res2 = interceptor.check_envelopes(m2, profile)
    assert "E_DOMAIN_OUT_OF_ENVELOPE" in {f.code for f in res2["findings"]}


# ---------------------------------------------------------------------------
# volume-mesh quality
# ---------------------------------------------------------------------------
def test_regular_hex_mesh_is_exact(profile):
    pts, cells = fg.hex_mesh(4, 3, 2)
    res = mq.evaluate_mesh({"points": pts, "cells": cells, "format": "vtk",
                            "path": "-", "skipped_cells": 0}, profile.mesh)
    assert res["n_cells"] == 24
    assert res["total_volume"] == pytest.approx(24.0, rel=1e-12)
    assert res["skewness"]["max"] == pytest.approx(0.0, abs=1e-9)
    assert res["aspect_ratio"]["max"] == pytest.approx(1.0, rel=1e-9)
    # no blocking finding (the missing-flow warning is expected and non-blocking)
    assert [v["code"] for v in res["violations"] if v["code"].startswith("E_")] == []


@pytest.mark.parametrize("shear,expected", [(1.0, 0.500), (2.0, 0.705), (6.0, 0.895)])
def test_equiangle_skewness_responds_to_shear(profile, shear, expected):
    pts, cells = fg.hex_mesh(4, 3, 2, shear=shear)
    res = mq.evaluate_mesh({"points": pts, "cells": cells, "format": "vtk",
                            "path": "-", "skipped_cells": 0}, profile.mesh)
    # analytic: the sheared side face is a parallelogram whose largest corner is
    # 180 - atan(1/shear) degrees, so equiangle skewness = (theta_max - 90)/90
    theta_max = 180.0 - math.degrees(math.atan(1.0 / shear))
    analytic = (theta_max - 90.0) / 90.0
    assert res["skewness"]["max"] == pytest.approx(analytic, abs=0.01)
    assert res["skewness"]["max"] == pytest.approx(expected, abs=0.01)
    blocked = shear > 4.0
    assert ("E_MESH_SKEWNESS" in [v["code"] for v in res["violations"]]) is blocked


def test_aspect_ratio_gate(profile):
    pts, cells = fg.hex_mesh(3, 3, 3, dz=0.005)
    res = mq.evaluate_mesh({"points": pts, "cells": cells, "format": "vtk",
                            "path": "-", "skipped_cells": 0}, profile.mesh)
    assert res["aspect_ratio"]["max"] == pytest.approx(200.0, rel=1e-9)
    assert "E_MESH_ASPECT_RATIO" in [v["code"] for v in res["violations"]]


def test_inverted_cell_is_caught(profile):
    pts, cells = fg.hex_mesh(2, 2, 2, invert_cell=3)
    res = mq.evaluate_mesh({"points": pts, "cells": cells, "format": "vtk",
                            "path": "-", "skipped_cells": 0}, profile.mesh)
    assert res["volume"]["min"] <= 0.0
    viol = [v for v in res["violations"] if v["code"] == "E_MESH_NEGATIVE_VOLUME"]
    assert viol and viol[0]["count"] == 1


def test_openfoam_polymesh_volumes_are_exact(tmp_path, profile):
    d = fg.write_openfoam_polymesh(os.path.join(str(tmp_path), "case"), 4, 3, 2)
    mesh = mq.read_openfoam_polymesh(d)
    res = mq.evaluate_mesh(mesh, profile.mesh)
    n = 4 * 3 * 2
    assert res["n_cells"] == n
    assert res["total_volume"] == pytest.approx(float(n), rel=1e-12)
    assert res["volume"]["min"] == pytest.approx(1.0, rel=1e-12)
    assert res["volume"]["max"] == pytest.approx(1.0, rel=1e-12)
    assert res["skewness"]["max"] == pytest.approx(0.0, abs=1e-9)


def test_checkmesh_and_yplus_logs_parse(tmp_path):
    cm = fg.write_checkmesh_log(os.path.join(str(tmp_path), "cm.log"),
                                n_cells=6, n_faces=29, n_points=24,
                                max_skewness=0.02, max_aspect_ratio=1.0)
    parsed = mq.parse_checkmesh_log(open(cm, encoding="utf-8").read())
    assert parsed["n_cells"] == 6 and parsed["max_skewness"] == pytest.approx(0.02)
    assert parsed["max_non_orthogonality"] == pytest.approx(12.3)
    yp = fg.write_yplus_log(os.path.join(str(tmp_path), "yp.log"),
                            {"hull": (42.0, 168.0, 95.0)})
    patches = mq.parse_yplus_log(open(yp, encoding="utf-8").read())
    assert patches["hull"]["max"] == pytest.approx(168.0)


# ---------------------------------------------------------------------------
# y+ physics
# ---------------------------------------------------------------------------
def test_y_plus_matches_analytic_definition():
    u_inf, nu, y = 5.0, 1.0e-6, 3.0e-4
    cf = 0.0576 * 5.0e6 ** -0.2
    solved = mq.y_plus(y, u_inf, nu, cf=cf)
    u_tau = u_inf * math.sqrt(cf / 2.0)
    assert solved["u_tau"] == pytest.approx(u_tau, rel=1e-12)
    assert solved["y_plus"] == pytest.approx(y * u_tau / nu, rel=1e-12)


def test_first_layer_height_inverts_y_plus():
    u_inf, nu = 5.0, 1.0e-6
    cf = 0.0576 * 5.0e6 ** -0.2
    y = mq.first_layer_height(60.0, u_inf, nu, cf=cf)
    assert mq.y_plus(y, u_inf, nu, cf=cf)["y_plus"] == pytest.approx(60.0, rel=1e-12)


def test_y_plus_band_gate_from_log(tmp_path, profile):
    d = fg.write_openfoam_polymesh(os.path.join(str(tmp_path), "case"), 2, 2, 1)
    mesh = mq.read_openfoam_polymesh(d)
    good = fg.write_yplus_log(os.path.join(str(tmp_path), "good.log"),
                              {"hull": (42.0, 168.0, 95.0)})
    bad = fg.write_yplus_log(os.path.join(str(tmp_path), "bad.log"),
                             {"hull": (0.4, 7.5, 2.1)})
    ok = mq.evaluate_mesh(mesh, profile.mesh, wall_log_text=open(good, encoding="utf-8").read())
    ko = mq.evaluate_mesh(mesh, profile.mesh, wall_log_text=open(bad, encoding="utf-8").read())
    assert ok["y_plus"]["within_band"] is True and ok["violations"] == []
    assert ko["y_plus"]["within_band"] is False
    assert "E_MESH_YPLUS" in [v["code"] for v in ko["violations"]]


# ---------------------------------------------------------------------------
# pipeline: verdicts, error recovery, audit chain
# ---------------------------------------------------------------------------
def _good_item(tmp_path, idx=1):
    path = fg.write_obj(os.path.join(str(tmp_path), f"hull{idx}.obj"),
                        fg.wigley_triangles(n_u=20, n_v=8, **WIGLEY))
    return {"id": f"good-{idx}", "geometry": {"path": path, "draft": WIGLEY["T"],
                                              "declared": {"block_coefficient": ANALYTIC_CB}}}


def test_validate_item_pass_and_block(tmp_path, profile):
    ok = validate_item(_good_item(tmp_path), profile)
    assert ok["verdict"] == "PASS"
    bad = dict(_good_item(tmp_path, 2))
    bad["geometry"] = dict(bad["geometry"], declared={"block_coefficient": ANALYTIC_CB * 1.2})
    res = validate_item(bad, profile)
    assert res["verdict"] == "BLOCK"
    assert "E_GEOM_DECLARED_MISMATCH" in res["reason_codes"]


def test_batch_error_recovery_keeps_going(tmp_path, profile):
    items = [_good_item(tmp_path, 1), "not-an-object", _good_item(tmp_path, 3)]
    report = validate_batch(items, profile)
    verdicts = [it["verdict"] for it in report["items"]]
    assert verdicts.count("PASS") == 2
    assert "ERROR" in verdicts
    err = [it for it in report["items"] if it["verdict"] == "ERROR"][0]
    assert err["reason_codes"] == ["E_ITEM_EXCEPTION"]
    assert exit_code(report) == 2


def test_exit_codes(tmp_path, profile):
    pass_report = validate_batch([_good_item(tmp_path, 5)], profile)
    assert exit_code(pass_report) == 0
    block_report = validate_batch([{"id": "missing", "geometry": {}}], profile)
    assert exit_code(block_report) == 1


def test_duplicate_ids_are_made_unique(tmp_path, profile):
    a = _good_item(tmp_path, 7)
    b = _good_item(tmp_path, 7)
    report = validate_batch([a, b], profile)
    ids = sorted(it["id"] for it in report["items"])
    assert len(ids) == len(set(ids)) == 2


def test_audit_chain_detects_tampering(tmp_path, profile):
    log_path = os.path.join(str(tmp_path), "audit.jsonl")
    audit = AuditLog(log_path, run_id="t")
    report = validate_batch([_good_item(tmp_path, 11)], profile, audit=audit)
    assert report["audit"]["chain"]["ok"] is True
    assert verify_audit_chain(log_path)["ok"] is True
    # tamper: rewrite the first entry's verdict payload hash
    lines = open(log_path, encoding="utf-8").read().splitlines()
    entry = json.loads(lines[0])
    entry["event"] = "rewritten"
    lines[0] = json.dumps(entry)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    broken = verify_audit_chain(log_path)
    assert broken["ok"] is False and broken["broken_at"] == 0


def test_report_is_json_serialisable_and_deterministic(tmp_path, profile):
    items = [_good_item(tmp_path, 21), _good_item(tmp_path, 22)]
    a = validate_batch(items, profile)
    b = validate_batch(list(reversed(items)), profile)
    assert a["summary"]["items_sha256"] == b["summary"]["items_sha256"]
    json.dumps(a)  # must be strict-JSON serialisable (no numpy scalars leaking)


def test_cli_writes_report_and_returns_gate_exit_code(tmp_path, profile):
    batch = {"items": [_good_item(tmp_path, 31),
                       {"id": "blocked", "geometry": {"path": os.path.join(
                           str(tmp_path), "does-not-exist.stl")}}]}
    batch_path = os.path.join(str(tmp_path), "batch.json")
    with open(batch_path, "w", encoding="utf-8") as fh:
        json.dump(batch, fh)
    out = os.path.join(str(tmp_path), "report.json")
    log = os.path.join(str(tmp_path), "audit.jsonl")
    rc = cli_main(["--batch", batch_path, "--profile", PROFILE_PATH, "--out", out,
                   "--audit-log", log, "--quiet"])
    assert rc == 1
    report = json.load(open(out, encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["summary"]["verdict_counts"]["BLOCK"] == 1
    assert "E_FILE_NOT_FOUND" in report["summary"]["reason_code_counts"]
    assert verify_audit_chain(log)["ok"] is True


def test_cli_reports_harness_failure(tmp_path):
    rc = cli_main(["--batch", os.path.join(str(tmp_path), "nope.json"), "--quiet"])
    assert rc == 3