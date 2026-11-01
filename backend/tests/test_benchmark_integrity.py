"""物理自洽性审计与几何闸门的回归测试。

运行（本文件自带 sys.path 与 `api` 包引导，不依赖 backend/tests/conftest.py 的
整站 app 导入——该 conftest 需要 psycopg2/cryptography 等运行期依赖）：

    python3 -m pytest backend/tests/test_benchmark_integrity.py --noconftest -q

覆盖三类断言：
  A 判据本身：能算、边界正确、能区分"通过"与"不通过"（防"永远 FAIL"的自证式判据）；
  B 物理不变量：Ct 与湿表面积无关、Rt ∝ S、Cb↔∇ 互为逆运算；
  C 端点行为：几何闸门拦截、Cb 缺省时给误差带而不臆造、基准端点把参考值不可信
    标成 evidence=MOCK/trustworthy=False。
"""
from __future__ import annotations

import importlib
import json
import math
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _import_api_module(name: str):
    """导入 api.* 子模块，绕过 ``api/__init__.py`` 的数据库依赖。"""
    if "api" not in sys.modules:
        pkg = types.ModuleType("api")
        pkg.__path__ = [str(BACKEND_DIR / "api")]
        sys.modules["api"] = pkg
    return importlib.import_module(name)


from services import benchmark_integrity as bi  # noqa: E402
from services import hull_geometry as hg  # noqa: E402
from services import resistance_core as rc  # noqa: E402

DATA_PATH = BACKEND_DIR / "data" / "benchmark_reference.json"
DATASET = json.loads(DATA_PATH.read_text())


def _case(case_id: str) -> dict:
    return next(c for c in DATASET["cases"] if c["case_id"] == case_id)


# ---------------------------------------------------------------------------
# A. 基础闭合：Fn / Cb / 湿表面积
# ---------------------------------------------------------------------------

class TestGeometryClosures:
    def test_froude_known_answer(self):
        # SUBOFF-01: V=3.0 m/s, L=4.356 m → 3.0/sqrt(9.81*4.356)
        expected = 3.0 / math.sqrt(9.81 * 4.356)
        assert hg.froude_number(3.0, 4.356) == pytest.approx(expected, rel=1e-12)

    def test_implied_length_roundtrip(self):
        fn = hg.froude_number(7.202, 320.0)
        assert hg.implied_length_from_froude(7.202, fn) == pytest.approx(320.0, rel=1e-12)

    def test_cb_volume_are_inverse(self):
        cb = 0.8098
        vol = hg.volume_from_cb(cb, 320.0, 58.0, 20.8)
        assert hg.cb_from_volume(vol, 320.0, 58.0, 20.8) == pytest.approx(cb, rel=1e-12)

    def test_wetted_surface_closure_monotone_in_cb(self):
        s_lo = hg.axisymmetric_wetted_surface(0.1, 4.356, 0.508)
        s_hi = hg.axisymmetric_wetted_surface(0.8, 4.356, 0.508)
        assert s_hi > s_lo

    def test_invalid_length_raises(self):
        with pytest.raises(hg.GeometryError):
            hg.froude_number(1.0, 0.0)

    def test_parse_case_geometry_suboff_marks_missing_cb(self):
        geo = hg.parse_case_geometry(_case("SUBOFF-01"))
        assert geo["length_m"] == pytest.approx(4.356)
        assert geo["cb"] is None and geo["cb_from_volume"] is None
        assert geo["wetted_surface_m2"] == pytest.approx(5.42)

    def test_parse_case_geometry_dtmb_derives_cb(self):
        geo = hg.parse_case_geometry(_case("DTMB5415-01"))
        # displacement_kg=466.3, ρ=998 → ∇=0.4672 m³ → Cb=0.4375 ≠ 声明 0.508
        assert geo["cb_from_volume"] == pytest.approx(0.4375, abs=1e-3)
        assert geo["cb"] == pytest.approx(0.508, abs=1e-6)


class TestGeometryGate:
    def test_gate_rejects_scale_mismatch(self):
        result = hg.check_geometry(length=320.0, beam=0.508, draft=0.508)
        assert result["ok"] is False
        assert any("L/B" in v for v in result["violations"])

    def test_gate_flags_cb_closure_violation(self):
        result = hg.check_geometry(length=5.72, beam=0.759, draft=0.246, cb=0.508, volume_m3=0.4672)
        assert result["cb_closure"]["status"] == hg.FAIL
        assert result["cb_closure"]["rel_err"] > hg.CB_REL_TOL

    def test_gate_reports_unavailable_when_volume_missing(self):
        result = hg.check_geometry(length=4.356, beam=0.508, draft=0.508, cb=None)
        assert result["cb_closure"]["status"] == hg.UNAVAILABLE

    def test_gate_passes_self_consistent_geometry(self):
        vol = hg.volume_from_cb(0.75, 100.0, 15.0, 6.0)
        result = hg.check_geometry(length=100.0, beam=15.0, draft=6.0, cb=0.75, volume_m3=vol)
        assert result["cb_closure"]["status"] == hg.PASS
        assert result["ok"] is True

    def test_validate_geometry_raises_with_reasons(self):
        with pytest.raises(hg.GeometryError) as excinfo:
            hg.validate_geometry(length=-1.0, beam=15.0, draft=6.0, cb=0.7)
        assert excinfo.value.reasons


# ---------------------------------------------------------------------------
# B. 阻力核：不变量与守卫
# ---------------------------------------------------------------------------

class TestResistanceCore:
    def test_ittc1957_known_answer(self):
        # Cf = 0.075/(log10(1e7)-2)^2 = 0.075/25 = 0.003
        assert rc.ittc1957_cf(1.0e7) == pytest.approx(0.003, rel=1e-12)

    def test_ct_independent_of_wetted_surface(self):
        """Ct = Cf+Cr 与 S 无关 —— 湿表面积偏差只作用于力，不作用于无量纲系数。"""
        a = rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104, speed_ms=3.0)
        b = rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104,
                                   speed_ms=3.0, wetted_surface_m2=5.42)
        assert a["ct"] == pytest.approx(b["ct"], rel=1e-12)
        assert a["total_resistance_n"] != pytest.approx(b["total_resistance_n"], rel=1e-3)

    def test_rt_proportional_to_wetted_surface(self):
        a = rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104, speed_ms=3.0)
        b = rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104,
                                   speed_ms=3.0, wetted_surface_m2=2 * a["wetted_surface_m2"])
        assert b["total_resistance_n"] / a["total_resistance_n"] == pytest.approx(2.0, rel=1e-6)

    def test_declared_surface_recorded_as_source(self):
        out = rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104,
                                     speed_ms=3.0, wetted_surface_m2=5.42)
        assert out["wetted_surface_source"] == "declared"
        assert out["wetted_surface_m2"] == pytest.approx(5.42)
        assert out["wetted_surface_closure_bias"] < 0  # 闭式偏小

    def test_absurd_declared_surface_rejected(self):
        with pytest.raises(ValueError):
            rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104,
                                   speed_ms=3.0, wetted_surface_m2=50.0)

    def test_validity_range_flag(self):
        inside = rc.estimate_resistance(length=320.0, beam=58.0, draft=20.8, cb=0.81, speed_ms=7.2)
        outside = rc.estimate_resistance(length=4.356, beam=0.508, draft=0.508, cb=0.104, speed_ms=6.0)
        assert inside["validity"]["in_validity_range"] is True
        assert outside["validity"]["in_validity_range"] is False

    def test_invalid_cb_rejected(self):
        with pytest.raises(ValueError):
            rc.estimate_resistance(length=10.0, beam=2.0, draft=1.0, cb=1.5, speed_ms=2.0)


# ---------------------------------------------------------------------------
# C. 数据集审计：判据可区分真假
# ---------------------------------------------------------------------------

class TestDatasetAudit:
    def test_c1_flags_dtmb_scale_mixing(self):
        check = bi.check_froude(_case("DTMB5415-01"))
        assert check["status"] == bi.FAIL
        # Fn 列隐含长度 ≈ 181 m，与几何 5.72 m 相差约 31.7 倍
        assert check["implied_length_m"] == pytest.approx(181.0, abs=2.0)
        assert check["length_ratio"] > 30.0

    def test_c1_passes_suboff(self):
        check = bi.check_froude(_case("SUBOFF-01"))
        assert check["status"] == bi.PASS
        assert check["rel_err"] < 1e-3

    def test_c2_flags_dtmb_and_kvlcc2(self):
        assert bi.check_cb_closure(_case("DTMB5415-01"))["status"] == bi.FAIL
        kvlcc2 = bi.check_cb_closure(_case("KVLCC2-01"))
        assert kvlcc2["status"] == bi.FAIL
        # ρ 假设翻转（海水/淡水）不改变结论
        assert kvlcc2["rel_err_if_seawater"] > 0.01

    def test_c4_ct_rt_identity_broken_everywhere(self):
        for case in DATASET["cases"]:
            if case["ship_type"] == "SUBOFF":
                check = bi.check_ct_rt_identity(case)
                assert check["status"] == bi.FAIL
                assert check["rt_declared_over_implied"] < 0.2

    def test_c3_surface_closure_best_case_still_fails_for_suboff(self):
        check = bi.check_surface_closure(_case("SUBOFF-01"))
        assert check["basis"] == "best_case_over_cb_band"
        assert check["status"] == bi.FAIL
        assert check["closure_bias"] < -0.10

    def test_c5_flags_non_monotonic_ct(self):
        check = bi.check_ct_monotonicity(
            [c for c in DATASET["cases"] if c["ship_type"] == "DTMB-5415"]
        )
        assert check["status"] == bi.FAIL
        assert check["violations"][0]["ship_type"] == "DTMB-5415"

    def test_dataset_graded_mock(self):
        audit = bi.audit_dataset(DATASET)
        assert audit["n_cases"] == len(DATASET["cases"])
        assert audit["dataset_evidence"] == bi.MOCK
        assert audit["grade_counts"][bi.MOCK] == audit["n_cases"]

    def test_consistent_synthetic_case_graded_real(self):
        """防自证式判据：一份真正自洽的记录必须判 REAL，而不是无脑 FAIL。"""
        L, B, T, cb = 100.0, 15.0, 6.0, 0.70
        vol = hg.volume_from_cb(cb, L, B, T)
        s = hg.axisymmetric_wetted_surface(cb, L, B)
        speed = 5.0
        ct = 0.0035
        rt = ct * 0.5 * hg.RHO_MODEL_FRESH * s * speed ** 2
        case = {
            "case_id": "SYN-01", "ship_type": "SYN", "speed_ms": speed,
            "froude_number": hg.froude_number(speed, L), "experimental_ct": ct,
            "experimental_rt_n": rt,
            "geometry": {"length_m": L, "beam_m": B, "draft_m": T, "volume_m3": vol,
                         "cb": cb, "wetted_surface_m2": round(s, 6)},
        }
        audit = bi.audit_case(case)
        assert audit["failed_checks"] == []
        assert audit["evidence"] == bi.REAL

    def test_single_field_corruption_is_detected(self):
        """把 Ct 乘 3（模拟录入错误）必须被 C4 抓到。"""
        case = json.loads(json.dumps(_case("SUBOFF-01")))
        case["experimental_ct"] = case["experimental_ct"] * 3.0
        assert bi.check_ct_rt_identity(case)["status"] == bi.FAIL

    def test_accuracy_vs_reference_reports_no_case_within_3pct(self):
        accuracy = bi.platform_vs_reference(DATASET)
        summary = accuracy["summary"]
        assert summary["ct_vs_ref"]["n"] == len(DATASET["cases"])
        assert summary["ct_vs_ref"]["n_within_3pct_best_case"] == 0
        for row in accuracy["rows"]:
            assert row["err_pct_ct"] is not None

    def test_audit_artifact_is_reproducible(self):
        """脚本产出的工件与函数现算结果一致（防止工件与代码漂移）。"""
        artifact_path = BACKEND_DIR.parent / "artifacts" / "benchmark_integrity_audit.json"
        if not artifact_path.exists():
            pytest.skip("artifact not generated yet; run scripts/audit_benchmark.py")
        artifact = json.loads(artifact_path.read_text())
        fresh = bi.audit_dataset(DATASET)
        assert artifact["integrity"]["dataset_evidence"] == fresh["dataset_evidence"]
        assert artifact["integrity"]["grade_counts"] == fresh["grade_counts"]


# ---------------------------------------------------------------------------
# D. 端点行为
# ---------------------------------------------------------------------------

class TestResistanceEndpoint:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        calculations = _import_api_module("api.calculations")
        app = FastAPI()
        app.include_router(calculations.router, prefix="/api/v1")
        return TestClient(app)

    def test_endpoint_with_cb(self, client):
        resp = client.post("/api/v1/calculations/resistance", json={
            "length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098, "speed_ms": 7.2,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["cb_source"] == "declared"
        assert body["wetted_surface_source"] == "platform_closure"
        assert body["ct"] == pytest.approx(body["frictional_resistance_n"] /
                                           (0.5 * 998.0 * body["wetted_surface_m2"] * 7.2 ** 2)
                                           + body["residual_resistance_n"] /
                                           (0.5 * 998.0 * body["wetted_surface_m2"] * 7.2 ** 2), rel=1e-4)

    def test_endpoint_without_cb_gives_band_not_fabricated_value(self, client):
        resp = client.post("/api/v1/calculations/resistance", json={
            "length": 4.356, "beam": 0.508, "draft": 0.508, "speed_ms": 3.0,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["cb_source"] == "band_over_geometry"
        assert body["cb_used"] is None
        lo, hi = body["total_resistance_band_n"]
        assert hi > lo > 0

    def test_endpoint_uses_declared_wetted_surface(self, client):
        base = {"length": 4.356, "beam": 0.508, "draft": 0.508, "cb": 0.104, "speed_ms": 3.0}
        closure = client.post("/api/v1/calculations/resistance", json=base).json()
        declared = client.post("/api/v1/calculations/resistance",
                               json={**base, "wetted_surface_m2": 5.42}).json()
        assert declared["wetted_surface_source"] == "declared"
        assert declared["wetted_surface_m2"] == pytest.approx(5.42)
        # 力随 S 变化，Ct 不变
        assert declared["total_resistance_n"] != pytest.approx(closure["total_resistance_n"], rel=1e-3)
        assert declared["ct"] == pytest.approx(closure["ct"], rel=1e-9)
        assert any("湿表面积" in w for w in declared["warnings"])

    def test_endpoint_rejects_scale_mismatch_geometry(self, client):
        resp = client.post("/api/v1/calculations/resistance", json={
            "length": 320.0, "beam": 0.508, "draft": 0.508, "cb": 0.8098, "speed_ms": 7.2,
        })
        assert resp.status_code == 400
        assert "geometry_violations" in resp.json()["detail"]

    def test_endpoint_rejects_absurd_declared_surface(self, client):
        resp = client.post("/api/v1/calculations/resistance", json={
            "length": 4.356, "beam": 0.508, "draft": 0.508, "cb": 0.104, "speed_ms": 3.0,
            "wetted_surface_m2": 80.0,
        })
        assert resp.status_code == 400


class TestBenchmarkEndpoint:
    @pytest.fixture()
    def module(self):
        return _import_api_module("api.domain.ship_benchmark")

    @pytest.fixture()
    def client(self, module):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(module.router, prefix="/api/v1")
        return TestClient(app)

    def test_geometry_presets_have_no_fabricated_cb(self, module):
        """回归：旧实现把 SUBOFF 的 Cb 写成 0.508（= 直径），现必须为 None。"""
        assert module.SHIP_GEOMETRY["SUBOFF"]["cb"] is None
        assert module.SHIP_GEOMETRY["KVLCC2"]["cb"] == pytest.approx(0.8098)
        assert module.SHIP_GEOMETRY["SUBOFF"]["wetted_surface_m2"] == pytest.approx(5.42)

    def test_validate_marks_reference_untrustworthy(self, module, client, monkeypatch):
        monkeypatch.setattr(module, "_call_resistance_api",
                            lambda ship_type, speed_ms, geometry: {"ct": 0.0038, "method": "stub"})
        resp = client.post("/api/v1/calculations/benchmark_validate",
                           json={"ship_type": "SUBOFF", "benchmark_id": "SUBOFF-01"})
        assert resp.status_code == 200
        body = resp.json()
        result = body["results"][0]
        assert result["evidence"] in ("MOCK", "WITHHELD")
        assert result["trustworthy"] is False
        assert body["summary"]["untrustworthy_cases"]
        assert "evidence_note" in body["summary"]

    def test_validate_reports_integrity_checks(self, module, client, monkeypatch):
        monkeypatch.setattr(module, "_call_resistance_api",
                            lambda ship_type, speed_ms, geometry: {"ct": 0.003, "method": "stub"})
        body = client.post("/api/v1/calculations/benchmark_validate",
                           json={"ship_type": "DTMB-5415", "benchmark_id": "all"}).json()
        assert body["summary"]["total_cases"] == 4
        failed = {f for r in body["results"] for f in r["failed_checks"]}
        assert "C1_froude_consistency" in failed
        assert "C4_ct_rt_identity" in failed or "C2_cb_closure" in failed

    def test_validate_404_on_unknown_case(self, module, client):
        resp = client.post("/api/v1/calculations/benchmark_validate",
                           json={"ship_type": "SUBOFF", "benchmark_id": "NOPE"})
        assert resp.status_code == 404

    def test_validate_surfaces_geometry_gate(self, module, client, monkeypatch):
        monkeypatch.setattr(module, "_call_resistance_api",
                            lambda ship_type, speed_ms, geometry: {"ct": 0.003, "method": "stub"})
        body = client.post("/api/v1/calculations/benchmark_validate",
                           json={"ship_type": "KVLCC2", "benchmark_id": "all"}).json()
        assert body["summary"]["geometry_gate"]["cb_closure"]["status"] == "FAIL"