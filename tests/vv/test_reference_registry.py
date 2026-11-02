"""m1863 参考数据登记册 + 闸门评估器的判据测试。

判据（不是"文件里写着什么"，而是独立重算）：
* 无出处的参考表**不得**作判据（WITHHELD，且 usable_as_gate=False）；
* 误差定义唯一：|sim-ref|/max(|ref|, y_floor)，超 15% 即 FAIL，不允许 WARN 缓冲；
* 不外推：仿真未覆盖的刻度记 WITHHELD，不产生"误差"；
* 覆盖率不足不下结论（WITHHELD）；
* 已在 TensorLBM 合并的三个误差数字，用登记册 + 已落盘历史独立重算后必须逐项复现
  （只容许四舍五入差 0.05 个百分点）。

运行：python3 -m pytest tests/vv/test_reference_registry.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from vv import reference_registry as rr  # noqa: E402

DATASET_ID = "martin_moyce_1952_dambreak_front"


@pytest.fixture(scope="module")
def curated() -> dict:
    return json.loads((ROOT / "references" / "registry" / f"{DATASET_ID}.json").read_text(encoding="utf-8"))


def _synthetic(points, y_scale=None) -> dict:
    ds = {
        "schema": rr.SCHEMA_ID,
        "dataset_id": "synthetic",
        "title": "synthetic",
        "points": points,
        "provenance": {"citation": "Synthetic et al. (2000)", "doi": "10.0/synthetic",
                       "digitised_by": "unit test", "retrieved_from": "tests"},
        "tolerance": {"gate": 0.15},
    }
    if y_scale is not None:
        ds["y_scale"] = y_scale
    return ds


# --------------------------------------------------------------------------- #
# 数据集自检
# --------------------------------------------------------------------------- #
def test_curated_dataset_is_usable_as_gate(curated):
    v = rr.validate_dataset(curated)
    assert v["verdict"] == rr.PASS, v["checks"]
    assert v["usable_as_gate"] is True
    # 出处四项齐全且被钉住（DOI + 源仓 commit + 源文件 sha256）
    assert curated["provenance"]["doi"] == "10.1098/rsta.1952.0006"
    assert len(curated["provenance"]["retrieved_repo_commit"]) == 40
    assert len(curated["provenance"]["retrieved_sha256"]) == 64


def test_dataset_without_provenance_is_withheld_not_pass(curated):
    stripped = json.loads(json.dumps(curated))
    stripped["provenance"]["digitised_by"] = ""
    v = rr.validate_dataset(stripped)
    assert v["verdict"] == rr.WITHHELD
    assert v["usable_as_gate"] is False
    assert "provenance" in v["blocking"]


def test_non_monotonic_reference_is_rejected(curated):
    broken = json.loads(json.dumps(curated))
    broken["points"] = list(reversed(broken["points"]))
    v = rr.validate_dataset(broken)
    assert v["verdict"] == rr.FAIL
    assert v["usable_as_gate"] is False


def test_missing_uncertainty_warns_but_stays_usable(curated):
    bare = json.loads(json.dumps(curated))
    bare["points"] = [[x, y] for x, y, _ in bare["points"]]
    v = rr.validate_dataset(bare)
    assert v["verdict"] == rr.WARN
    assert v["usable_as_gate"] is True  # 瑕疵非阻断，但不得静默当 PASS


# --------------------------------------------------------------------------- #
# 闸门语义
# --------------------------------------------------------------------------- #
def test_identity_curve_passes_with_zero_error(curated):
    sim = [(p[0], p[1]) for p in curated["points"]]
    g = rr.gate(curated, sim)
    assert g["verdict"] == rr.PASS
    assert g["max_rel_err"] == pytest.approx(0.0, abs=1e-12)
    assert g["coverage"] == pytest.approx(1.0)


def test_twenty_percent_offset_fails_the_platform_line(curated):
    sim = [(p[0], p[1] * 1.20) for p in curated["points"]]
    g = rr.gate(curated, sim)
    assert g["verdict"] == rr.FAIL
    assert g["max_rel_err"] == pytest.approx(0.20, rel=1e-9)


def test_all_zero_simulation_cannot_pass(curated):
    """退化曲线（全 0）必须被拦住：它历史上真的发生过（TensorLBM front_series 全 0）。"""
    sim = [(p[0], 0.0) for p in curated["points"]]
    g = rr.gate(curated, sim)
    assert g["verdict"] == rr.FAIL
    assert g["max_rel_err"] > 0.5


def test_partial_coverage_is_withheld_not_graded(curated):
    pts = curated["points"]
    sim = [(p[0], p[1]) for p in pts[: len(pts) // 3]]
    g = rr.gate(curated, sim)
    assert g["verdict"] == rr.WITHHELD
    assert g["coverage"] < rr.DEFAULT_MIN_COVERAGE


def test_no_extrapolation_beyond_simulation_range(curated):
    assert rr.interp([0.0, 1.0], [0.0, 2.0], 1.5) is None
    assert rr.interp([0.0, 1.0], [0.0, 2.0], -0.1) is None
    cps = rr.checkpoint_errors(curated, [(0.0, 1.0), (0.5, 1.1)], [2.5])
    assert cps["T=2.5"]["verdict"] == rr.WITHHELD
    assert cps["T=2.5"]["err"] is None


def test_near_zero_reference_needs_y_scale():
    pts = [[i * 0.1, max(0.0, i * 0.1 - 0.05) + 0.0] for i in range(8)]  # 含真零点
    v = rr.validate_dataset(_synthetic(pts))
    assert v["usable_as_gate"] is False
    assert "y_scale" in v["blocking"]
    v2 = rr.validate_dataset(_synthetic(pts, y_scale={"value": 1.0}))
    assert v2["usable_as_gate"] is True


# --------------------------------------------------------------------------- #
# 复核已合并的误差数字（集成）
# --------------------------------------------------------------------------- #
CLAIMS = {
    "a40": (10.526, -7.579, -19.889),
    "a80": (2.49, -9.723, -19.086),
}
FILES = {"a40": "tensorlbm_dam_break_sc_a40.csv", "a80": "tensorlbm_dam_break_sc_a80.csv"}


@pytest.mark.parametrize("grid", ["a40", "a80"])
def test_recorded_tensorlbm_errors_are_reproduced(curated, grid):
    sim = rr.load_sim_csv(ROOT / "references" / "sim" / FILES[grid], "T_MM", "X_toe")
    assert len(sim) >= 20
    cps = rr.checkpoint_errors(curated, sim, (1.0, 2.0, 2.96))
    mine = [cps[k]["signed_err_pct"] for k in ("T=1", "T=2", "T=2.96")]
    for got, want in zip(mine, CLAIMS[grid]):
        assert got == pytest.approx(want, abs=0.05), (grid, got, want)


def test_reference_checkpoints_match_tensorlbm_table(curated):
    """T=1/2/2.96 的参考值必须与 TensorLBM 记录一致（1.326 / 2.360 / 3.670）。"""
    cps = rr.checkpoint_errors(curated, [(0.0, 1.0), (3.0, 3.7)], (1.0, 2.0, 2.96))
    assert cps["T=1"]["ref"] == pytest.approx(1.326, abs=1e-9)
    assert cps["T=2"]["ref"] == pytest.approx(2.36, abs=1e-9)
    assert cps["T=2.96"]["ref"] == pytest.approx(3.67, abs=1e-9)


# --------------------------------------------------------------------------- #
# 采集器
# --------------------------------------------------------------------------- #
def test_harvest_accepts_sourced_table_and_rejects_unsourced(tmp_path):
    isaac = {"reference": "Doe & Roe (1969), doi:10.1/xyz", "provenance": {"digitised_by": "unit test"},
             "reference_digitisation_x": {"T": [0, 1, 2, 3, 4], "Z": [1.0, 1.2, 1.5, 1.9, 2.4]}}
    (tmp_path / "issac.json").write_text(json.dumps(isaac), encoding="utf-8")
    (tmp_path / "bare.json").write_text(
        json.dumps({"reference_digitisation_y": {"T": [0, 1, 2, 3, 4], "Z": [9, 9, 9, 9, 9]}}),
        encoding="utf-8")
    ok, rejected = rr.harvest([tmp_path])
    assert len(ok) == 1 and ok[0]["provenance"]["doi"] == "10.1/xyz"
    assert len(rejected) == 1 and rejected[0]["validation"]["usable_as_gate"] is False