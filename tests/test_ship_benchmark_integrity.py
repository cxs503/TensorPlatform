"""基准数据集完整性裁定 + 阻力模型契约的回归测试。

覆盖本单修复的三处缺陷（见 docs/verification/ship_benchmark_integrity.md）：

1. 基准验证端点曾跨服务 HTTP 调用并发送 ``speed``、读取 ``ct``，而端点的契约是
   ``speed_ms`` 且从不返回 ``ct`` → 每个算例 calc_ct 恒为 0、误差恒为 100%。
   现改为进程内调用 ``services.resistance_model``，本文件断言 Ct 的解析恒等式。
2. 几何预置曾与数据集重复且单位不一致（如 DTMB-5415 排水量 466.3「t」实为 kg）。
   现由数据集单一来源派生，本文件断言二者一致且物理域合法。
3. 参考数据集从未机器校验。本文件断言裁定结果：
   DTMB-5415 全块因「模型尺度几何 + 实船尺度航速」的 Fn 不自洽而被剔除，
   SUBOFF 总阻力列低于摩擦下界（物理不可接受）故仅 Ct 通道可用。
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _load(name: str, rel_path: str):
    """按文件路径加载后端模块（绕开 api 包 __init__ 的重依赖链）。"""
    spec = importlib.util.spec_from_file_location(name, BACKEND / rel_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rm = _load("resistance_model", "services/resistance_model.py")
integ = _load("ship_benchmark_integrity", "services/ship_benchmark_integrity.py")
bench = _load("ship_benchmark", "api/domain/ship_benchmark.py")


# ---------------------------------------------------------------------------
# 阻力模型：与解析基准逐项对照
# ---------------------------------------------------------------------------

def test_ittc57_matches_independent_formula():
    for re in (1e6, 5e6, 1.3e7, 1e9):
        expected = 0.075 / (math.log10(re) - 2.0) ** 2
        assert rm.ittc57_cf(re) == pytest.approx(expected, rel=1e-15)


def test_blasius_is_exact_analytic_solution():
    for re in (1e4, 1e5, 4.9e5):
        assert rm.blasius_cf(re) == pytest.approx(1.328 / math.sqrt(re), rel=1e-15)


def test_regime_switch_at_critical_reynolds():
    below, regime_below = rm.friction_coefficient(rm.RE_CRIT * 0.999)
    at_crit, regime_at = rm.friction_coefficient(rm.RE_CRIT)
    assert regime_below == rm.REGIME_LAMINAR
    assert regime_at == rm.REGIME_TURBULENT
    assert below == pytest.approx(rm.blasius_cf(rm.RE_CRIT * 0.999), rel=1e-15)
    assert at_crit == pytest.approx(rm.ittc57_cf(rm.RE_CRIT), rel=1e-15)
    # ITTC-57 在层流区外推会高估 (documented, not hidden)
    assert rm.ittc57_cf(1e5) > 1.9 * rm.blasius_cf(1e5)


def test_resistance_decomposition_identity():
    res = rm.compute_resistance(length=100.0, beam=20.0, draft=8.0, cb=0.8, speed_ms=7.0)
    q = 0.5 * res["water_density"] * res["wetted_surface_m2"] * res["speed_ms"] ** 2
    assert res["ct"] == pytest.approx(res["friction_coefficient"] + res["residual_coefficient"], rel=1e-15)
    assert res["total_resistance_n"] == pytest.approx(q * res["ct"], rel=1e-12)
    assert res["frictional_resistance_n"] + res["residual_resistance_n"] == pytest.approx(
        res["total_resistance_n"], rel=1e-15)
    assert res["ct"] > res["friction_coefficient"]  # 无推力船体总阻力必高于摩擦阻力


def test_wetted_surface_override_is_used_verbatim():
    base = dict(length=4.356, beam=0.508, draft=0.508, cb=0.542, speed_ms=3.0,
                water_density=rm.RHO_SEA_WATER, kinematic_viscosity=rm.NU_SEA_WATER_15C)
    estimated = rm.compute_resistance(**base)
    measured = rm.compute_resistance(**base, wetted_surface_m2=5.42)
    assert estimated["wetted_surface_source"] == "estimate"
    assert measured["wetted_surface_source"] == "input"
    assert measured["wetted_surface_m2"] == pytest.approx(5.42)
    # 同一 Ct 下阻力按面积线性缩放：这是契约,不是巧合
    assert measured["total_resistance_n"] / estimated["total_resistance_n"] == pytest.approx(
        5.42 / estimated["wetted_surface_m2"], rel=1e-12)
    assert measured["ct"] == pytest.approx(estimated["ct"], rel=1e-12)


def test_geometry_validation_rejects_nonphysical_input():
    with pytest.raises(rm.ResistanceInputError):
        rm.compute_resistance(length=0.0, beam=1.0, draft=1.0, cb=0.8, speed_ms=1.0)
    with pytest.raises(rm.ResistanceInputError):
        rm.compute_resistance(length=10.0, beam=1.0, draft=1.0, cb=1.2, speed_ms=1.0)
    with pytest.raises(rm.ResistanceInputError):
        rm.compute_resistance(length=10.0, beam=1.0, draft=1.0, cb=0.8, speed_ms=-1.0)


# ---------------------------------------------------------------------------
# 参考数据集完整性裁定
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def audit():
    return integ.load_and_audit()


def _case(audit, case_id):
    return next(c for c in audit["cases"] if c["case_id"] == case_id)


def _check(audit, case_id, name):
    return next(c for c in _case(audit, case_id)["checks"] if c["name"] == name)


def test_asset_is_fingerprinted(audit):
    assert len(audit["asset_sha256"]) == 64
    assert audit["n_cases"] == 12
    assert audit["duplicate_case_ids"] == []


def test_dtmb5415_block_rejected_for_scale_mixing(audit):
    """模型尺度几何（L=5.72 m）配实船尺度航速 → Fn 与 V/√(gL) 差 5-14 倍。"""
    chk = _check(audit, "DTMB5415-01", "fn_identity")
    assert chk["status"] == integ.STATUS_FAIL
    assert chk["metrics"]["rel_dev"] > 0.5  # Fn 偏 80%+
    for cid in ("DTMB5415-01", "DTMB5415-02", "DTMB5415-03", "DTMB5415-04"):
        assert cid not in audit["admission"][integ.CHANNEL_CT]
        assert "fn_identity" in audit["excluded"][integ.CHANNEL_CT][cid]["blocking_checks"]


def test_suboff_total_resistance_below_friction_floor(audit):
    """SUBOFF 表列 Rt=12.5 N @3 m/s 反算 Ct=5e-4 < Cf=2.9e-3：无推力船体不可能,故 Rt 通道不可用。"""
    chk = _check(audit, "SUBOFF-01", "rt_friction_floor")
    assert chk["status"] == integ.STATUS_FAIL
    assert chk["metrics"]["rt"] < chk["metrics"]["rf_ittc57_n"]
    assert "SUBOFF-01" not in audit["admission"][integ.CHANNEL_RT]
    # 但 Ct 列本身不违反物理下界,故 Ct 通道仍可准入
    assert _check(audit, "SUBOFF-01", "ct_friction_floor")["status"] == integ.STATUS_PASS
    assert "SUBOFF-01" in audit["admission"][integ.CHANNEL_CT]


def test_kvlcc2_admitted_with_block_coefficient_warning(audit):
    assert "KVLCC2-01" in audit["admission"][integ.CHANNEL_CT]
    assert _check(audit, "KVLCC2-01", "fn_identity")["status"] == integ.STATUS_PASS
    cb = _check(audit, "KVLCC2-01", "cb_identity")
    assert cb["severity"] == integ.SEVERITY_WARN
    assert cb["status"] == integ.STATUS_FAIL
    assert cb["metrics"]["rel_dev"] > integ.TOL_IDENTITY
    # Cb 自洽性只记警告,不阻断 Ct 比较（模型直接吃 Cb,不吃排水量）
    assert _case(audit, "KVLCC2-01")["channels"][integ.CHANNEL_CT]["status"] == "PASS_WITH_WARNINGS"


def test_admission_counts(audit):
    assert audit["summary"]["ct_admitted"] == 8
    assert audit["summary"]["ct_excluded"] == 4
    assert audit["summary"]["rt_admitted"] == 4
    per_hull = {k: v for k, v in audit["admitted_per_hull_type"][integ.CHANNEL_CT].items() if v}
    assert sorted(per_hull) == ["KVLCC2", "SUBOFF"]
    assert all(len(v) == 4 for v in per_hull.values())


# ---------------------------------------------------------------------------
# 基准验证端点：契约与准入
# ---------------------------------------------------------------------------

def test_geometry_presets_come_from_dataset_single_source():
    assert bench.SHIP_GEOMETRY == bench.build_ship_geometry()
    for ship_type, preset in bench.SHIP_GEOMETRY.items():
        assert preset["length"] > 0 and preset["beam"] > 0 and preset["draft"] > 0
        assert 0.0 < preset["cb"] < 1.0, ship_type
    # 公布湿面积随几何一起带入模型（而非用回转体估算）
    assert bench.SHIP_GEOMETRY["SUBOFF"]["wetted_surface_m2"] == pytest.approx(5.42)
    # 单位修正：数据集 displacement_kg=466.3 → 0.4663 t（旧硬编码表误当 466.3 t）
    assert bench.SHIP_GEOMETRY["DTMB-5415"]["displacement"] == pytest.approx(0.4663)


def test_benchmark_ct_is_nonzero_and_matches_model():
    """回归：修复前 calc_ct 恒为 0.0（跨服务契约不匹配）。"""
    resp = bench.benchmark_validate(bench.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
    assert len(resp.results) == 4
    for row in resp.results:
        assert 0.0 < row.calc_ct < 0.05
        expected = abs(row.calc_ct - row.ref_ct) / row.ref_ct * 100.0
        assert row.error_pct == pytest.approx(expected, abs=0.05)
        assert row.pass_ == (row.error_pct <= 15.0)


def test_benchmark_excludes_dtmb5415_but_reports_it():
    resp = bench.benchmark_validate(bench.BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all"))
    assert resp.results == []
    excluded = resp.summary["excluded_by_integrity"]
    assert len(excluded) == 4
    assert all("fn_identity" in row["blocking_checks"] for row in excluded)
    assert resp.summary["integrity"]["ct_excluded"] == 4


def test_include_excluded_flag_returns_audit_rows():
    resp = bench.benchmark_validate(bench.BenchmarkValidateRequest(
        ship_type="DTMB-5415", benchmark_id="all", include_excluded=True))
    assert len(resp.results) == 4
    for row in resp.results:
        assert row.method == "EXCLUDED"
        assert row.pass_ is False
        assert "INTEGRITY_EXCLUDED" in row.note


def test_kvlcc2_error_is_reported_honestly():
    resp = bench.benchmark_validate(bench.BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
    assert len(resp.results) == 4
    assert resp.summary["evaluated_cases"] == 4
    assert resp.summary["mean_abs_error_pct"] == pytest.approx(
        sum(r.error_pct for r in resp.results) / 4, abs=0.01)
    # honest-FAIL 不得被粉饰：失败的算例必须仍然出现在结果里
    assert resp.summary["passed"] + resp.summary["failed"] == 4


def test_unknown_benchmark_id_raises_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        bench.benchmark_validate(bench.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="does-not-exist"))
    assert exc.value.status_code == 404