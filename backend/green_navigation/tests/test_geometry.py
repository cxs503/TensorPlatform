"""几何先验门测试：合格算例通过、破坏 Cb/L/B 即拒算。"""
from __future__ import annotations

import pytest

from green_navigation import chain
from green_navigation.geometry import GeometryError, validate_geometry


def test_all_cases_pass_geometry_gate(cases):
    for case in cases.values():
        geo = case["geometry"]
        chk = validate_geometry(geo["L_m"], geo["B_m"], geo["T_m"], geo["cb"],
                                displacement_t=case.get("displacement_t"),
                                dwt_t=case.get("dwt_t"), strict=True)
        assert chk.ok, (case["case_id"], chk.checks)
        assert not chk.warnings, (case["case_id"], chk.warnings)


def test_displacement_mismatch_is_rejected(cases):
    """把 KVLCC2 排水量人为放大 20% 必须被拒（不允许"精致地错"）。"""
    case = cases["KVLCC2_VLCC"]
    geo = case["geometry"]
    with pytest.raises(GeometryError):
        validate_geometry(geo["L_m"], geo["B_m"], geo["T_m"], geo["cb"],
                          displacement_t=case["displacement_t"] * 1.2,
                          dwt_t=case["dwt_t"], strict=True)
    soft = validate_geometry(geo["L_m"], geo["B_m"], geo["T_m"], geo["cb"],
                             displacement_t=case["displacement_t"] * 1.2,
                             dwt_t=case["dwt_t"], strict=False)
    assert soft.ok is False
    assert soft.warnings


def test_cb_out_of_range_rejected(cases):
    geo = cases["KVLCC2_VLCC"]["geometry"]
    with pytest.raises(GeometryError):
        validate_geometry(geo["L_m"], geo["B_m"], geo["T_m"], 1.4)


def test_displacement_calc_matches_block_coefficient_identity(cases):
    """Δ_calc 必须逐位等于 Cb·L·B·T·ρ（分子级复算，不引用实现内部常量）。"""
    for case in cases.values():
        geo = case["geometry"]
        chk = validate_geometry(geo["L_m"], geo["B_m"], geo["T_m"], geo["cb"],
                                displacement_t=case.get("displacement_t"),
                                dwt_t=case.get("dwt_t"), strict=False)
        expect = geo["cb"] * geo["L_m"] * geo["B_m"] * geo["T_m"] * 1.025
        assert chk.displacement_calc_t == pytest.approx(expect, rel=1e-12)


def test_wetted_surface_reproduces_published_values(chain_result):
    """Holtrop 湿表面积 vs 公开值（KVLCC2 27,194 m²；KCS 9,424 m²）<2%。"""
    checked = 0
    for case in chain_result["cases"]:
        chk = case["cii"]["wetted_surface_check"]
        if chk is None:
            continue
        checked += 1
        assert chk["rel_err_pct"] < 2.0, (case["case_id"], chk)
    assert checked >= 2


def test_case_data_carries_provenance(cases):
    """领域数据必须自带出处与置信度（零手抄/零黑箱）。"""
    for case in cases.values():
        assert case["provenance"], case["case_id"]
        assert all(isinstance(v, str) and v for v in case["provenance"].values())


def test_cases_load_from_json_injection(tmp_path):
    """算例经 JSON 注入：改一个数字，链的输入随之改变（证明无硬编码船名）。"""
    import json
    import shutil

    src = chain.DATA_DIR
    for name in ("cases.json", "sfoc_reference.json", "engine_catalogue.json",
                 "cii_rating_boundaries.json", "external_anchors.json"):
        shutil.copy(src / name, tmp_path / name)
    data = json.loads((tmp_path / "cases.json").read_text())
    data["cases"][0]["geometry"]["T_m"] = 21.6
    (tmp_path / "cases.json").write_text(json.dumps(data, ensure_ascii=False))
    modified = chain.load_cases(tmp_path)
    assert modified[0]["geometry"]["T_m"] == 21.6
    assert modified[0]["geometry"]["T_m"] != chain.load_cases()[0]["geometry"]["T_m"]