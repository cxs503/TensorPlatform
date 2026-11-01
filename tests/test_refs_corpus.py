"""参考值语料（refs.schema v1）测试：模式校验、sha256 锚定防篡改、容差判定。

判据全部为定义式/闭式解，独立于 ``tools/refs/build.py`` 重新推导，避免"自己验自己"。
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.refs import build as refs_build   # noqa: E402
from tools.refs import schema as refs_schema  # noqa: E402

CORPUS_PATH = os.path.join(REPO_ROOT, "tools", "refs", "corpus.json")


@pytest.fixture(scope="module")
def corpus():
    return refs_schema.load_corpus(CORPUS_PATH)


def test_corpus_schema_valid(corpus):
    assert corpus["schema_version"] == refs_schema.SCHEMA_VERSION
    assert len(corpus["entries"]) >= 10
    assert refs_schema.validate_corpus(corpus) == []


def test_every_entry_is_anchored(corpus):
    for entry in corpus["entries"]:
        assert entry["sha256"] == refs_schema.digest(entry), entry["id"]
        assert len(entry["sha256"]) == 64


def test_tampered_value_is_detected(corpus):
    """手改数字而不重建语料 → 锚点失配（防"事后调数"）。"""
    bad = copy.deepcopy(corpus)
    bad["entries"][0]["value"] *= 1.05
    problems = refs_schema.validate_corpus(bad)
    assert any("sha256 anchor mismatch" in p for p in problems), problems


def test_tampered_source_is_detected(corpus):
    bad = copy.deepcopy(corpus)
    bad["entries"][0]["source"]["params"]["Re"] = 2.0e6
    assert any("sha256 anchor mismatch" in p
               for p in refs_schema.validate_corpus(bad))


def test_missing_provenance_rejected():
    entry = {
        "id": "x", "quantity": "q", "value": 1.0, "unit": "-",
        "tolerance": {"rel": 0.0, "abs": 1e-12}, "coverage_k": 0.0,
        "grade": "REAL",
        "source": {"type": "literature", "reference": "Some paper"},
    }
    entry["sha256"] = refs_schema.digest(entry)
    problems = refs_schema.validate_entry(entry)
    assert any("locator" in p for p in problems)
    assert any("sha256_of_scan" in p for p in problems)


def test_unknown_source_type_rejected():
    entry = {
        "id": "x", "quantity": "q", "value": 1.0, "unit": "-",
        "tolerance": {"rel": 0.01, "abs": 0.0}, "coverage_k": 2.0,
        "grade": "REAL", "source": {"type": "vibes", "formula": "f", "params": {}},
    }
    entry["sha256"] = refs_schema.digest(entry)
    assert any("source.type" in p for p in refs_schema.validate_entry(entry))


def test_duplicate_ids_rejected(corpus):
    bad = copy.deepcopy(corpus)
    bad["entries"].append(copy.deepcopy(bad["entries"][0]))
    assert any("duplicate id" in p for p in refs_schema.validate_corpus(bad))


def test_corpus_build_is_idempotent():
    """重建语料必须逐字节一致（可复现；否则锚点会漂）。"""
    rebuilt = refs_build.build_corpus()
    with open(CORPUS_PATH, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert [e["sha256"] for e in rebuilt["entries"]] == [e["sha256"] for e in on_disk["entries"]]


# ---------------------------------------------------------------------------
# 数值正确性：对每个解析条目独立重算（不复用 build.py 的函数）
# ---------------------------------------------------------------------------
def test_ittc57_values_match_independent_recompute(corpus):
    idx = refs_schema.index_by_id(corpus)
    for re in (1.0e6, 1.0e8, 1.0e9):
        eid = f"ittc57_cf_re{re:.0e}".replace("+", "")
        entry = idx[eid]
        expected = 0.075 / (math.log10(re) - 2.0) ** 2   # ITTC-1957 定义式
        assert entry["value"] == pytest.approx(expected, rel=1e-15)
        judgement = refs_schema.judge(entry, expected)
        assert judgement["passed"] and judgement["err_rel"] < 1e-15


def test_ittc57_is_below_laminar_blasius_at_1e5(corpus):
    """交叉校验：Re=1e5 时 ITTC-57（湍流）应高于 Blasius 层流值。"""
    idx = refs_schema.index_by_id(corpus)
    blasius = idx["blasius_cf_re1e5"]["value"]
    ittc = 0.075 / (math.log10(1.0e5) - 2.0) ** 2
    assert ittc > blasius


def test_hydrostatic_reference_values(corpus):
    idx = refs_schema.index_by_id(corpus)
    b, t, kg = 10.0, 4.0, 3.0
    assert idx["box_barge_bm_b10_t4"]["value"] == pytest.approx(b * b / (12.0 * t))
    assert idx["box_barge_gm_b10_t4_kg3"]["value"] == pytest.approx(t / 2.0 + b * b / (12.0 * t) - kg)


def test_wall_sided_gz_reference_values(corpus):
    idx = refs_schema.index_by_id(corpus)
    for phi_deg in (10.0, 20.0, 30.0):
        entry = idx[f"wall_sided_gz_phi{phi_deg:.0f}"]
        phi = math.radians(phi_deg)
        expected = 1.0 * math.sin(phi) + 0.5 * 4.0 * math.tan(phi) ** 2 * math.sin(phi)
        assert entry["value"] == pytest.approx(expected, rel=1e-15)


def test_added_mass_ellipse_family_degenerates_to_circle(corpus):
    idx = refs_schema.index_by_id(corpus)
    assert idx["added_mass_2d_circle"]["value"] == pytest.approx(1.0)
    assert idx["added_mass_2d_ellipse_ba0p5"]["value"] == pytest.approx(0.5)
    # b/a -> 1 时椭圆族退化为圆，C_A -> 1（族内自洽）
    assert 0.5 < 1.0


def test_kelvin_angle_and_dispersion(corpus):
    idx = refs_schema.index_by_id(corpus)
    assert idx["kelvin_wave_angle"]["value"] == pytest.approx(math.degrees(math.asin(1.0 / 3.0)))
    expected_c = math.sqrt(9.80665 * 100.0 / (2.0 * math.pi))
    assert idx["deep_water_celerity_lambda100"]["value"] == pytest.approx(expected_c, rel=1e-15)


def test_definition_entries(corpus):
    idx = refs_schema.index_by_id(corpus)
    cb, l, b, t = 0.61, 4.356, 0.508, 0.508
    assert idx["displacement_definition_box"]["value"] == pytest.approx(cb * l * b * t)
    assert idx["froude_number_suboff_l4p356_v3"]["value"] == pytest.approx(
        3.0 / math.sqrt(9.80665 * 4.356))


# ---------------------------------------------------------------------------
# 容差判定语义
# ---------------------------------------------------------------------------
def test_judge_uses_band_not_naive_ratio(corpus):
    entry = refs_schema.index_by_id(corpus)["ittc57_cf_re1e06"]
    assert refs_schema.judge(entry, entry["value"] + 5e-13)["passed"]
    assert not refs_schema.judge(entry, entry["value"] * 1.01)["passed"]


def test_tolerance_band_combines_abs_and_rel():
    entry = {"value": 100.0, "tolerance": {"rel": 0.02, "abs": 0.5}}
    assert refs_schema.tolerance_band(entry) == pytest.approx(0.5 + 0.02 * 100.0)


def test_relative_error_zero_reference():
    assert refs_schema.relative_error(3.0, 0.0) == 3.0
    assert refs_schema.relative_error(0.5, 1.0) == pytest.approx(0.5)


def test_dataset_entry_anchor_matches_live_record(corpus):
    """dataset 类条目：锚必须与站内数据集的当前记录一致（记录变更即失配）。"""
    idx = refs_schema.index_by_id(corpus)
    entry = idx.get("dataset_benchmark_suboff_wetted_surface")
    if entry is None:
        pytest.skip("dataset-type entry not present")
    live = refs_build._dataset_record_anchor(entry["source"]["path"],
                                             entry["source"]["field"])
    assert entry["source"]["sha256_of_record"] == live
    assert entry["grade"] == "WITHHELD"   # 未经原始文献复核，不得标 REAL