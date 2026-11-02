"""m1873 —— 船舶阻力/静水力学双实现漂移的回归闸门.

本文件把 ``tests/audit/audit_dup_kernel.py`` 的审计结论钉成可执行的判据。
它不是"再跑一遍同样的断言"，而是选了三类必须被机器守住的边界：

* **不变量（0 % 类）**：闭式量（ITTC-57 Cf、规范形体湿面积、矩形剖面 KB/BM、
  矩形剖面复原力臂）必须与独立解析基准逐位一致（≤1e-6 %）；任何一侧被改动
  都会在这里立刻炸。
* **已知缺陷指纹（钉死类）**：两套内核的湿面积默认值差异导致 Rt 分歧
  −33.6 %（KVLCC2）/ −43.0 %（DTMB-5415）。**这是"修复即需复核"闸门**：
  若有人把 axisym 默认换成 published S，下面的带宽断言会失败，强制重跑
  ``audit_dup_kernel.py`` 并更新 ``outputs/m1873_dup_kernel/divergence.json``，
  而不是让旧结论悄悄失效。
* **适用域边界**：wall-sided 公式只在其适用倾角内精确；越界后误差必须显式
  增长（45° ≈ +11.8 %，50° ≈ +35.8 %），禁止被静默外推。

运行::

    python3 -m pytest tests/audit/test_dup_kernel_divergence.py -q
"""

from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import audit_dup_kernel as audit  # noqa: E402
import dup_kernel_reference as ref  # noqa: E402

JSON_PATH = os.path.join(ROOT, "outputs", "m1873_dup_kernel", "divergence.json")


@pytest.fixture(scope="module")
def result():
    return audit.run_audit(write=False)


@pytest.fixture(scope="module")
def mods():
    return audit.load_platform()


# ---------------------------------------------------------------------------
# 0. Reference implementation self-checks (baseline must be trustworthy)
# ---------------------------------------------------------------------------
def test_reference_self_consistency():
    # ITTC-57 at Re = 1e9 -> classic 1.7163e-3-class value
    assert ref.ittc57_cf(1e6) == pytest.approx(0.075 / (6.0 - 2.0) ** 2, rel=1e-15)
    # box: S must be monotone in each dimension and equal to the hand formula
    assert ref.box_wetted_surface(40.0, 10.0, 4.0) == pytest.approx(800.0)
    # exact GZ of a box reduces to GM*sin(phi) in the small-angle limit
    B, T, depth, KG = 10.0, 4.0, 8.0, 3.0
    gm = ref.box_hydrostatics(B, T, KG)["gm"]
    for phi in (0.1, 0.5, 1.0):
        assert ref.box_gz_exact(B, T, depth, KG, phi) == pytest.approx(
            gm * (phi * 3.141592653589793 / 180.0), rel=2e-3)


# ---------------------------------------------------------------------------
# 1. Closed-form invariants: both platform families must be exact
# ---------------------------------------------------------------------------
def test_friction_line_is_exact_both_families(result):
    worst = result["C_friction_line"]["worst_abs_err_pct"]
    assert worst["api"] <= 1e-9, worst
    assert worst["svc"] <= 1e-9, worst


def test_canonical_wetted_surface_and_cb_are_exact(result):
    for row in result["B_canonical_exact"]["rows"]:
        assert abs(row["S_err_pct"]) <= 1e-6, row
        assert abs(row["Cb_err_pct"]) <= 1e-6, row


def test_box_hydrostatics_is_exact(result):
    err = result["D_box_hydrostatics"]["hydrostatics"]["err_pct"]
    for key in ("kb", "bm", "km", "gm"):
        assert abs(err[key]) <= 1e-6, (key, err)


def test_polygon_solver_matches_exact_integral(result):
    for row in result["D_box_hydrostatics"]["rows"]:
        assert abs(row["err_numeric_pct"]) <= 0.05, row


# ---------------------------------------------------------------------------
# 2. Domain of applicability of the wall-sided formula
# ---------------------------------------------------------------------------
def test_wallsided_exact_within_limit_and_diverges_beyond(result):
    lim = result["D_box_hydrostatics"]["wall_sided_limit_deg"]
    inside = [r for r in result["D_box_hydrostatics"]["rows"]
              if r["phi_deg"] <= lim]
    outside = [r for r in result["D_box_hydrostatics"]["rows"]
               if r["beyond_wallsided_limit"]]
    for row in inside:
        assert abs(row["err_wallsided_pct"]) <= 0.1, row
    assert outside, "grid must probe past the wall-sided limit"
    assert max(abs(r["err_wallsided_pct"]) for r in outside) > 5.0, outside


# ---------------------------------------------------------------------------
# 3. Cross-implementation divergence fingerprints (pinned)
# ---------------------------------------------------------------------------
def test_holtrop_with_published_coefficients_meets_3pct(result):
    err = result["headline"]["holtrop_published_cwp_err_pct"]
    assert abs(err["KVLCC2"]) <= 3.0, err          # -0.47 %
    # DTMB-5415's Cm/Cwp are flagged *derived* in the published table, so the
    # 3 % criterion is deliberately NOT asserted here; only a 5 % ceiling.
    assert abs(err["DTMB-5415"]) <= 5.0, err       # -4.02 %


def test_axisym_default_is_the_known_outlier(result):
    rows = [r for r in result["A_wetted_surface"]["rows"]
            if r["estimator"] == "api_core default S (axisym)"]
    err = {r["hull"]: r["err_vs_published_pct"] for r in rows}
    assert 48.0 <= err["KVLCC2"] <= 53.0, err      # 50.62 %, docstring claims ~+52 %
    assert 73.0 <= err["DTMB-5415"] <= 78.0, err   # 75.55 %
    assert set(result["headline"]["wetted_surface_gate_failures_15pct"]) == {
        "CLAIM(axisym vs published)", "api_core default S (axisym)",
        "ref_axisym(pi*B*L*(0.5+0.25Cb))", "svc.legacy_wetted_surface"}


def test_two_cores_diverge_only_through_the_surface_default(result):
    """Fingerprint: −33.6 % / −43.0 % before pinning S, 0 % after."""
    rows = result["E_resistance_end_to_end"]["rows"]
    before = {r["hull"]: r["div_api_vs_svc_pct"] for r in rows}
    assert before["KVLCC2"] == pytest.approx(-33.6058, abs=0.05), before
    assert before["DTMB-5415"] == pytest.approx(-43.0355, abs=0.05), before
    for r in rows:
        assert abs(r["div_api_vs_svc_S_pinned_pct"]) <= 1e-3, r
        assert r["cf_api"] == pytest.approx(r["cf_svc"], rel=1e-12)


def test_friction_line_contract_drift_is_known(result):
    drift = result["C_friction_line"]["low_re_contract_drift"]
    assert drift["50.0"]["api_raises"] is True
    assert drift["50.0"]["svc_value"] == 0.0
    assert drift["120.0"]["api_raises"] is False


def test_nonphysical_cb_is_caught_downstream(result):
    guard = result["B_canonical_exact"]["nonphysical_cb_guard_probe"]
    assert guard["cylinder"]["Cb_above_1"] is True
    assert guard["cylinder"]["downstream_gate_catches"] is True


# ---------------------------------------------------------------------------
# 4. On-disk artifact must be regenerable / in sync
# ---------------------------------------------------------------------------
def test_committed_artifact_matches_live_run(result):
    assert os.path.exists(JSON_PATH), (
        "run `python3 tests/audit/audit_dup_kernel.py` to regenerate the artifact")
    on_disk = json.load(open(JSON_PATH, encoding="utf-8"))
    assert on_disk["headline"] == result["headline"]
    assert len(on_disk["A_wetted_surface"]["rows"]) == len(result["A_wetted_surface"]["rows"])