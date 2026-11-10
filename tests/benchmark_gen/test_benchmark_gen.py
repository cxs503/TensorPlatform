"""Acceptance tests for the benchmark dataset generator (mission P269W2-N03).

Coverage map (machine-checkable acceptance criteria)
----------------------------------------------------
A1  generation pipeline: >= 3 synthetic bodies x >= 3 conditions   -> test_matrix_three_bodies_three_conditions
A2  provenance: dataset sha256 <-> (params, seed, version) 1:1     -> test_provenance_key_* / test_article_archive_*
A3  analytic comparison: closed-form deviation <= 0.1 %            -> test_numeric_matches_closed_form / test_rational_anchors
A4  zero domain vocabulary: no vessel name in the generator code   -> test_generator_has_no_vessel_names
A5  >= 12 pytest cases                                             -> this module

The analytic anchor is the classical *parabolic* form family: with
``plan_exponent=2, plan_shape=1, section_exponent=2, midbody_fraction=0`` it is
the standard Wigley-type parabolic hull, whose coefficients are rational
(Cb = 4/9, Cm = Cp = Cwp = 2/3, KB = 5T/8 above the keel, BM_T = 3B^2/(35T),
BM_L = 3L^2/(40T)).  The name appears here only for traceability; the generator
itself stays free of vessel names (A4).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.benchmark_gen import analytic, generator, numeric, provenance

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "tools" / "benchmark_gen" / "specs"
PKG_DIR = REPO_ROOT / "tools" / "benchmark_gen"

FAST_GRID = {"n_stations": 201, "n_waterlines": 101,
             "surface_cells_x": 40, "surface_cells_z": 20}

PARABOLIC = {"length": 10.0, "beam": 1.0, "form_depth": 0.625}
MIDBODY = {"length": 10.0, "beam": 1.0, "form_depth": 0.625,
           "midbody_fraction": 0.35, "plan_exponent": 2.5,
           "plan_shape": 1.2, "section_exponent": 1.5}
VEE = {"length": 10.0, "beam": 1.0, "form_depth": 0.625,
       "plan_exponent": 3.0, "section_exponent": 1.0}
BODY_PARAMS = [("parabolic", PARABOLIC), ("midbody", MIDBODY), ("vee", VEE)]
DRAFT_RATIOS = [1.0, 0.7, 0.5]


def _body(spec):
    return {"id": "test_body", "params": dict(spec)}


def _condition(ratio=1.0):
    return {"id": "test_condition", "params": {"draft_ratio": ratio, "density": 1025.0,
                                               "gravity": 9.81,
                                               "kinematic_viscosity": 1.189e-6,
                                               "speeds_ms": [1.0, 2.0]}}


# ---------------------------------------------------------------------------
# A3 — closed-form anchors
# ---------------------------------------------------------------------------
def test_rational_anchors_of_closed_form():
    """Parabolic family: every coefficient is a rational multiple of L, B, T."""
    form = numeric.BodyForm.from_spec(PARABOLIC)
    T, L, B = form.form_depth, form.length, form.beam
    ref = analytic.closed_form_hydrostatics(form, T)
    assert ref["displacement_volume"] == pytest.approx(4.0 / 9.0 * L * B * T, rel=1e-14)
    assert ref["cb"] == pytest.approx(4.0 / 9.0, rel=1e-14)
    assert ref["cm"] == pytest.approx(2.0 / 3.0, rel=1e-14)
    assert ref["cp"] == pytest.approx(2.0 / 3.0, rel=1e-14)
    assert ref["cwp"] == pytest.approx(2.0 / 3.0, rel=1e-14)
    assert ref["cvp"] == pytest.approx(2.0 / 3.0, rel=1e-14)
    assert ref["kb"] == pytest.approx(5.0 * T / 8.0, rel=1e-14)          # 3T/8 below DWL
    assert ref["transverse_inertia"] == pytest.approx(4.0 * B ** 3 * L / 105.0, rel=1e-14)
    assert ref["longitudinal_inertia"] == pytest.approx(B * L ** 3 / 30.0, rel=1e-14)
    assert ref["bmt"] == pytest.approx(3.0 * B ** 2 / (35.0 * T), rel=1e-13)
    assert ref["bml"] == pytest.approx(3.0 * L ** 2 / (40.0 * T), rel=1e-13)


@pytest.mark.parametrize("name,params", BODY_PARAMS)
@pytest.mark.parametrize("ratio", DRAFT_RATIOS)
def test_numeric_matches_closed_form(name, params, ratio):
    """A3: discretised offsets vs exact closed form <= 0.1 % on every metric."""
    form = numeric.BodyForm.from_spec(params)
    draft = ratio * form.form_depth
    num = numeric.hydrostatics(form, draft)
    ref = analytic.closed_form_hydrostatics(form, draft)
    for key, expected in ref.items():
        if key in ("draft", "waterline_z"):
            continue
        if abs(expected) < 1e-12:
            assert abs(num[key]) / form.length < 1e-9, key      # symmetry-trivial centre
        else:
            rel = abs(num[key] - expected) / abs(expected)
            assert rel <= generator.ANALYTIC_TOLERANCE, f"{name} ratio={ratio} {key}: {rel:.3e}"


@pytest.mark.parametrize("name,params", BODY_PARAMS)
def test_surface_reference_agrees(name, params):
    """Wetted surface: production 2x2 Gauss vs high-order quadrature < 0.1 %."""
    form = numeric.BodyForm.from_spec(params)
    s = numeric.wetted_surface(form, form.form_depth)
    ref = analytic.surface_reference(form, form.form_depth)
    assert abs(s - ref) / ref <= generator.ANALYTIC_TOLERANCE
    assert s > 0.0


def test_surface_grows_with_immersion():
    """Monotonicity sanity: deeper immersion wets more surface."""
    form = numeric.BodyForm.from_spec(PARABOLIC)
    areas = [numeric.wetted_surface(form, r * form.form_depth, n_x=30, n_z=15)
             for r in (0.5, 0.7, 1.0)]
    assert areas[0] < areas[1] < areas[2]


# ---------------------------------------------------------------------------
# A1 — generation pipeline over bodies x conditions
# ---------------------------------------------------------------------------
def test_spec_directory_bundles_three_bodies_and_three_conditions():
    specs = generator.load_spec_directory(SPEC_DIR)
    assert len(specs["bodies"]) >= 3
    assert len(specs["conditions"]) >= 3
    assert specs["pipeline"] == "hydrostatics"
    assert specs["domain_key"]


def test_matrix_three_bodies_three_conditions(tmp_path):
    """A1: 3 x 3 datasets generated, all inside the analytic tolerance."""
    specs = generator.load_spec_directory(SPEC_DIR)
    docs = generator.generate_matrix(specs["bodies"], specs["conditions"],
                                     seed=20260101, grid=FAST_GRID, mc_samples=3000)
    assert len(docs) == 9
    for doc in docs:
        content = doc["content"]
        assert content["reference"]["pass"] is True
        assert content["reference"]["max_relative_deviation"] <= generator.ANALYTIC_TOLERANCE
        assert content["monte_carlo_check"]["pass"] is True
        assert len(content["derived"]["speed_cases"]) == len(content["condition"]["speeds_ms"])
    pairs = {(d["content"]["body"]["id"], d["content"]["condition"]["id"]) for d in docs}
    assert len(pairs) == 9


def test_dataset_payload_is_self_describing():
    doc = generator.generate_dataset(_body(PARABOLIC), _condition(1.0),
                                     seed=7, grid=FAST_GRID, mc_samples=2000)
    content = doc["content"]
    for key in ("body", "condition", "results", "reference", "monte_carlo_check", "grids"):
        assert key in content
    body = content["body"]["params"]
    assert numeric.BodyForm.from_spec(body) == numeric.BodyForm.from_spec(PARABOLIC)
    # Froude numbers are consistent with the condition's speed list
    for case in content["derived"]["speed_cases"]:
        expected = case["speed_ms"] / math.sqrt(9.81 * PARABOLIC["length"])
        assert case["froude"] == pytest.approx(expected, rel=1e-12)


def echo_pipeline(request):
    """Trivial pipeline used to prove dotted-path injection (module level on purpose)."""
    return {"pipeline": "echo", "domain_key": request.get("domain_key"),
            "echo_body": request["body"]["id"], "echo_condition": request["condition"]["id"]}


def test_pipeline_registry_dotted_path_injection():
    """Cross-domain reuse: a pipeline referenced by dotted path needs no code change."""
    ref = f"{__name__}:echo_pipeline"
    assert generator.resolve_pipeline(ref) is echo_pipeline
    doc = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=3,
                                     pipeline=ref, domain_key="synthetic_domain",
                                     grid=FAST_GRID, mc_samples=100)
    assert doc["content"]["domain_key"] == "synthetic_domain"
    assert doc["content"]["echo_body"] == "test_body"
    assert provenance.verify_record(doc)["ok"] is True


# ---------------------------------------------------------------------------
# A2 — provenance / sha256 one-to-one mapping
# ---------------------------------------------------------------------------
def test_dataset_is_deterministic():
    args = dict(seed=11, grid=FAST_GRID, mc_samples=1000)
    first = generator.generate_dataset(_body(PARABOLIC), _condition(0.7), **args)
    second = generator.generate_dataset(_body(PARABOLIC), _condition(0.7), **args)
    assert first == second
    assert first["dataset_id"] == second["dataset_id"]
    assert (first["provenance"]["content_sha256"]
            == second["provenance"]["content_sha256"])


def test_seed_changes_key_and_content():
    a = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=1,
                                   grid=FAST_GRID, mc_samples=1000)
    b = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=2,
                                   grid=FAST_GRID, mc_samples=1000)
    assert a["dataset_id"] != b["dataset_id"]
    assert a["provenance"]["content_sha256"] != b["provenance"]["content_sha256"]
    # the Monte-Carlo cross-check is the seed-controlled part
    assert a["content"]["monte_carlo_check"]["volume_estimate"] != \
        b["content"]["monte_carlo_check"]["volume_estimate"]


@pytest.mark.parametrize("field,value", [("beam", 1.05), ("length", 10.5),
                                         ("plan_exponent", 2.2), ("section_exponent", 2.1)])
def test_parameter_change_changes_key(field, value):
    base = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=5,
                                      grid=FAST_GRID, mc_samples=500)
    tweaked = dict(PARABOLIC)
    tweaked[field] = value
    other = generator.generate_dataset(_body(tweaked), _condition(1.0), seed=5,
                                       grid=FAST_GRID, mc_samples=500)
    assert base["dataset_id"] != other["dataset_id"]
    assert base["content"]["results"]["displacement_volume_m3"] != \
        other["content"]["results"]["displacement_volume_m3"]


def test_version_change_changes_key():
    base = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=5,
                                      grid=FAST_GRID, mc_samples=500)
    bumped = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=5,
                                        grid=FAST_GRID, mc_samples=500,
                                        generator_version="99.0.0")
    assert base["dataset_id"] != bumped["dataset_id"]
    assert base["provenance"]["generator_version"] == generator.GENERATOR_VERSION


def test_provenance_key_recomputes_from_request():
    doc = generator.generate_dataset(_body(PARABOLIC), _condition(0.5), seed=13,
                                     grid=FAST_GRID, mc_samples=500)
    report = provenance.verify_record(doc, current_code_fingerprint=generator.code_fingerprint())
    assert report["ok"] is True
    assert report["code_fingerprint_matches_current"] is True
    assert report["recomputed"]["provenance_key"] == doc["dataset_id"]


def test_tampered_payload_is_detected():
    doc = generator.generate_dataset(_body(PARABOLIC), _condition(1.0), seed=17,
                                     grid=FAST_GRID, mc_samples=500)
    doc["content"]["results"]["block_coefficient"] += 1e-6
    report = provenance.verify_record(doc)
    assert report["ok"] is False
    assert report["checks"]["content_sha256_matches_payload"] is False


def test_archive_round_trip_and_tamper_detection(tmp_path):
    """A2: write -> verify OK; a bit flip in one file must be caught."""
    specs = generator.load_spec_directory(SPEC_DIR)
    docs = generator.generate_matrix(specs["bodies"], specs["conditions"], seed=42,
                                     grid=FAST_GRID, mc_samples=1000)
    manifest = provenance.write_archive(docs, tmp_path,
                                        generator_version=generator.GENERATOR_VERSION,
                                        code_fingerprint=generator.code_fingerprint())
    assert manifest["n_datasets"] == 9
    clean = provenance.verify_archive(tmp_path,
                                     current_code_fingerprint=generator.code_fingerprint())
    assert clean["ok"] is True, clean["problems"]
    assert clean["unique_ids"] is True

    target = Path(manifest["datasets"][0]["path"])
    target.write_text(target.read_text(encoding="utf-8").replace(
        '"block_coefficient": ', '"block_coefficient": 1'), encoding="utf-8")
    dirty = provenance.verify_archive(tmp_path)
    assert dirty["ok"] is False
    assert any("digest" in problem for problem in dirty["problems"])


def test_cli_writes_verifiable_archive(tmp_path):
    out = tmp_path / "archive"
    rc = generator.main(["--spec-dir", str(SPEC_DIR), "--out", str(out),
                         "--grid", json.dumps(FAST_GRID), "--mc-samples", "800",
                         "--json"])
    assert rc == 0
    assert (out / "MANIFEST.json").exists()
    assert (out / "SHA256SUMS").exists()
    assert len(list(out.glob("*.json"))) == 10          # 9 datasets + manifest
    assert provenance.verify_archive(out)["ok"] is True


# ---------------------------------------------------------------------------
# A4 — no vessel-specific vocabulary in the generator code
# ---------------------------------------------------------------------------
def test_generator_has_no_vessel_names():
    banned = ("kcs", "kvlcc", "dtmb", "suboff", "jbc", "kriso", "wigley",
              "series 60", "series60", "fujino", "nmri")
    for path in sorted(PKG_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8").lower()
        hits = [word for word in banned if word in source]
        assert not hits, f"{path.name} contains vessel-specific vocabulary: {hits}"


def test_no_vessel_names_in_spec_files():
    banned = ("kcs", "kvlcc", "dtmb", "suboff", "jbc", "wigley")
    for path in sorted(SPEC_DIR.rglob("*.json")):
        source = path.read_text(encoding="utf-8").lower()
        hits = [word for word in banned if word in source]
        assert not hits, f"{path.name} contains vessel-specific vocabulary: {hits}"


def test_no_spec_ids_hardcoded_in_code():
    """Domain data lives in the JSON specs only: no body/condition id in the code."""
    spec_ids = {p.stem for p in (SPEC_DIR / "bodies").glob("*.json")} | \
               {p.stem for p in (SPEC_DIR / "conditions").glob("*.json")}
    assert len(spec_ids) >= 6
    for path in sorted(PKG_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        leaks = [sid for sid in spec_ids if sid in source]
        assert not leaks, f"{path.name} hardcodes spec ids: {leaks}"


def test_arbitrary_injected_body_params_are_accepted():
    """Any admissible parameter set works: nothing is specialised to the shipped specs."""
    arbitrary = {"length": 4.2, "beam": 0.73, "form_depth": 0.21,
                 "midbody_fraction": 0.18, "plan_exponent": 1.7,
                 "plan_shape": 1.35, "section_exponent": 1.15}
    doc = generator.generate_dataset(_body(arbitrary), _condition(0.9), seed=99,
                                     grid=FAST_GRID, mc_samples=2000)
    content = doc["content"]
    assert content["body"]["params"]["beam"] == pytest.approx(0.73)
    assert content["condition"]["draft_m"] == pytest.approx(0.9 * 0.21, rel=1e-12)
    assert content["reference"]["pass"] is True


# ---------------------------------------------------------------------------
# input validation / robustness
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"length": 0.0, "beam": 1.0, "form_depth": 0.5},
    {"length": 10.0, "beam": -1.0, "form_depth": 0.5},
    {"length": 10.0, "beam": 1.0, "form_depth": 0.5, "midbody_fraction": 1.0},
    {"length": 10.0, "beam": 1.0, "form_depth": 0.5, "plan_exponent": 0.5},
])
def test_invalid_body_specs_rejected(bad):
    with pytest.raises(ValueError):
        numeric.BodyForm.from_spec(bad)


def test_unknown_body_parameter_rejected():
    spec = dict(PARABOLIC)
    spec["beam_m"] = 1.0
    with pytest.raises(ValueError):
        numeric.BodyForm.from_spec(spec)


def test_draft_beyond_form_depth_rejected():
    form = numeric.BodyForm.from_spec(PARABOLIC)
    with pytest.raises(ValueError):
        numeric.hydrostatics(form, form.form_depth * 1.2)


def test_monte_carlo_sanity_check_within_gate():
    """Seed-controlled third method agrees with the closed form within 4 sigma."""
    for name, params in BODY_PARAMS:
        form = numeric.BodyForm.from_spec(params)
        draft = 0.7 * form.form_depth
        ref = analytic.closed_form_hydrostatics(form, draft)["displacement_volume"]
        mc = generator.monte_carlo_volume(form, draft, n_samples=20000, seed=2026)
        z = abs(mc["volume_estimate"] - ref) / mc["volume_stderr"]
        assert z <= generator.MC_SIGMA_GATE, f"{name}: z={z:.2f}"