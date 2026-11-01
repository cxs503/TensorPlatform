"""
基准参考值溯源审计 + benchmark_validate 失效链路回归测试。

这些测试针对"验证门本身失效"这一类缺陷，而非被测求解器的精度：
  1. 参考数据集恒等式门：能判废已知伪造算例，且对自洽算例不误判（门非空转）。
  2. 参考值缺失字段 → WITHHELD，不猜。
  3. 上游不可达 → status=UPSTREAM_UNAVAILABLE 且 error_pct=None（禁止伪 999% 误差）。
  4. 上游只给 Rt 时按 Ct=Rt/(0.5 rho V^2 S) 派生，ct_source 标注来源。
  5. 死端口回归：默认上游基址必须是已部署的 :8001，源码中不得残留 :8002。
  6. 容差纪律：>15% 容差不得被视为合格判据。
  7. 活线契约测试（服务不在则 skip）：真实调用上游并拿到 Rt。

运行（本机缺 psycopg2 的隔离环境下）：
  DATABASE_URL="sqlite:///./_t.db" python3 -m pytest tests/feat/test_benchmark_ref_audit.py -q --noconftest
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services import benchmark_ref_audit as ra  # noqa: E402
from api.domain import ship_benchmark as sb  # noqa: E402

REPO_DATA = BACKEND_DIR / "data" / "benchmark_reference.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(REPO_DATA.read_text())


# ---------------------------------------------------------------------------
# 1. 恒等式门：判废能力 + 非空转
# ---------------------------------------------------------------------------

class TestIdentityGate:
    def test_dataset_path_resolves(self):
        assert REPO_DATA.exists(), f"benchmark dataset missing: {REPO_DATA}"
        assert sb._BENCHMARK_PATH.resolve() == REPO_DATA.resolve()

    def test_known_bad_cases_rejected(self, dataset):
        """审计结论必须与 12 算例已知矛盾一致：全部不可当外部基准。"""
        res = ra.audit_dataset(dataset)
        assert res["total_cases"] == 12
        assert res["inconsistent"] == 12, [c for c in res["cases"] if c["status"] == ra.CONSISTENT]
        assert res["baseline_usable"] is False
        assert res["verdict"] == "DATASET_REJECTED_AS_EXTERNAL_BASELINE"

    @pytest.mark.parametrize("case_id,identity,min_rel", [
        ("SUBOFF-01", "I2_ct_from_rt", 1.0),      # Ct 与 Rt 差 7.4 倍
        ("DTMB5415-01", "I1_froude", 0.5),        # 声明 Fn 被压低 ~5.6 倍
        ("KVLCC2-01", "I2_ct_from_rt", 0.5),      # 列值 Rt 与 Ct 差 3.6 倍
    ])
    def test_each_defect_is_named(self, dataset, case_id, identity, min_rel):
        case = next(c for c in dataset["cases"] if c["case_id"] == case_id)
        audit = ra.audit_case(case)
        chk = next(c for c in audit["checks"] if c["identity"] == identity)
        assert chk["status"] == ra.FAIL, chk
        assert chk["rel_diff"] >= min_rel, chk
        assert audit["status"] == ra.INCONSISTENT
        assert audit["reasons"]

    def test_gate_is_not_vacuous(self):
        """自洽算例必须三条恒等式全 PASS，否则门永远判废 = 空转。"""
        L, B, T, cb, V = 100.0, 16.0, 6.0, 0.70, 6.0
        rho = ra.density_for(L)
        rt = 0.5 * rho * V * V * ra.estimate_wetted_surface(L, B, T, cb) * 0.004
        good = {
            "case_id": "SYNTH-OK",
            "ship_type": "SYNTH",
            "speed_ms": V,
            "froude_number": V / math.sqrt(ra.G * L),
            "experimental_ct": 0.004,
            "experimental_rt_n": rt,
            "geometry": {"length_m": L, "beam_m": B, "draft_m": T, "cb": cb,
                         "displacement_t": rho * cb * L * B * T / 1000.0},
        }
        audit = ra.audit_case(good)
        assert [c["status"] for c in audit["checks"]] == [ra.PASS] * 3, audit["checks"]
        assert audit["status"] == ra.CONSISTENT

    def test_missing_fields_withheld_not_failed(self, dataset):
        """缺字段必须 WITHHELD（不猜），不能编造 FAIL 或 PASS。"""
        case = next(c for c in dataset["cases"] if c["case_id"] == "SUBOFF-01")
        audit = ra.audit_case(case)
        i3 = next(c for c in audit["checks"] if c["identity"] == "I3_displacement")
        assert i3["status"] == ra.WITHHELD
        assert i3["recomputed"] is None and "reason" in i3

    def test_ct_derivation_roundtrip(self):
        rho, V, S, ct = ra.RHO_SEA, 7.2, 26500.0, 0.0028
        rt = 0.5 * rho * V * V * S * ct
        assert ra.derive_ct_from_rt(rt, V, S, rho) == pytest.approx(ct, rel=1e-12)

    def test_wetted_surface_estimate_positive_and_scaling(self):
        s1 = ra.estimate_wetted_surface(100.0, 16.0, 6.0, 0.7)
        s2 = ra.estimate_wetted_surface(200.0, 16.0, 6.0, 0.7)
        assert s1 > 0 and s2 > s1
        with pytest.raises(ValueError):
            ra.estimate_wetted_surface(0.0, 16.0, 6.0, 0.7)


# ---------------------------------------------------------------------------
# 2. 上游失效语义（死端口 / 不可达 / 无 ct 字段）
# ---------------------------------------------------------------------------

def _validate(ship_type="KVLCC2", benchmark_id="all"):
    return sb.benchmark_validate(sb.BenchmarkValidateRequest(
        ship_type=ship_type, benchmark_id=benchmark_id))


class TestUpstreamFailureSemantics:
    def test_default_base_url_is_deployed_port(self):
        assert sb.DEFAULT_PLATFORM_URL.endswith(":8001"), sb.DEFAULT_PLATFORM_URL
        os.environ.pop("SHIP_PLATFORM_URL", None)
        assert sb._platform_base_url() == sb.DEFAULT_PLATFORM_URL

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SHIP_PLATFORM_URL", "http://example.invalid:9999/")
        assert sb._platform_base_url() == "http://example.invalid:9999"

    def test_no_dead_port_left_in_source(self):
        src = (BACKEND_DIR / "api" / "domain" / "ship_benchmark.py").read_text()
        assert ":8002" not in src, "死端口 :8002 残留（曾使全部算例伪 999% 误差）"

    def test_unreachable_upstream_yields_no_fabricated_error(self, monkeypatch):
        monkeypatch.setattr(sb, "_call_resistance_api",
                            lambda *a, **k: {"_upstream_error": "ConnectError: refused"})
        resp = _validate()
        assert resp.summary["upstream_unavailable"] == resp.summary["total_cases"] == 4
        assert resp.summary["compared"] == 0
        for r in resp.results:
            assert r.status == "UPSTREAM_UNAVAILABLE"
            assert r.error_pct is None, f"伪误差 {r.error_pct} 不允许出现"
            assert r.pass_ is False
            assert "UPSTREAM_UNAVAILABLE" in r.note
        assert "upstream_note" in resp.summary

    def test_none_return_also_marked_unavailable(self, monkeypatch):
        monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: None)
        resp = _validate()
        assert all(r.error_pct is None for r in resp.results)
        assert all(r.status == "UPSTREAM_UNAVAILABLE" for r in resp.results)

    def test_ct_derived_when_upstream_gives_only_rt(self, monkeypatch):
        monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: {
            "rt": 1200.0, "wetted_surface": 27000.0, "method": "ITTC-57+Holtrop"})
        resp = _validate(benchmark_id="KVLCC2-01")
        r = resp.results[0]
        assert r.ct_source == "derived_from_rt"
        assert r.calc_ct and r.calc_ct > 0
        expected = ra.derive_ct_from_rt(1200.0, r.speed_ms, 27000.0, ra.RHO_SEA)
        # 响应把 Ct 圆到 9 位小数（Ct~1e-6..1e-3），相对偏差 <=5e-4
        assert r.calc_ct == pytest.approx(expected, rel=1e-3)
        assert r.error_pct is not None and r.error_pct > 0

    def test_upstream_ct_takes_precedence(self, monkeypatch):
        monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: {
            "ct": 0.0031, "rt": 9.9e6, "wetted_surface": 27000.0})
        r = _validate(benchmark_id="KVLCC2-01").results[0]
        assert r.ct_source == "upstream"
        assert r.calc_ct == pytest.approx(0.0031)


# ---------------------------------------------------------------------------
# 3. 主机判定纪律：参考值判废时不得宣称精度
# ---------------------------------------------------------------------------

class TestJudgementDiscipline:
    def test_inconsistent_refs_downgrade_judgement(self, monkeypatch):
        monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: {"ct": 0.0031})
        resp = _validate(benchmark_id="KVLCC2-01")
        r = resp.results[0]
        assert r.ref_status == ra.INCONSISTENT
        assert r.judgement == "WITHHELD_REFS"
        assert resp.summary["baseline_usable"] is False
        assert "baseline_note" in resp.summary

    def test_summary_counts_are_consistent(self, monkeypatch):
        monkeypatch.setattr(sb, "_call_resistance_api", lambda *a, **k: {"ct": 0.0031})
        s = _validate().summary
        assert s["total_cases"] == s["compared"] + s["upstream_unavailable"]
        assert s["passed"] + s["failed"] == s["compared"]

    def test_geometry_payload_has_live_schema_keys(self):
        geo = sb.geometry_payload("DTMB-5415")
        for k in ("length", "beam", "draft", "displacement", "cb", "wetted_surface_m2"):
            assert k in geo
        assert geo["wetted_surface_m2"] > 0
        # 排水量必须与 rho Cb L B T 自洽（平台纪律：几何先验 Cb/L/B）
        mass = ra.density_for(geo["length"]) * geo["cb"] * geo["length"] * geo["beam"] * geo["draft"]
        assert ra._rel(geo["displacement"], mass) <= sb.GEOMETRY_MASS_TOL, geo["warnings"]

    def test_high_tolerance_is_not_compliance(self):
        assert ra.tolerance_is_acceptable(15.0) is True
        # ship_proxy 内置 SUBOFF 用 300% 容差 —— 属豁免，不是合格
        assert ra.tolerance_is_acceptable(300.0) is False

    def test_audit_endpoint_shape(self):
        out = sb.benchmark_ref_audit(ship_type="KVLCC2")
        assert out["total_cases"] == 12 and len(out["cases"]) == 4
        assert out["baseline_usable"] is False


# ---------------------------------------------------------------------------
# 4. 活线契约测试（上游服务不在则 skip）
# ---------------------------------------------------------------------------

def _upstream_alive() -> bool:
    try:
        import httpx
        r = httpx.get(f"{sb._platform_base_url()}/health", timeout=2.0)
        return r.status_code < 500
    except Exception:
        return False


@pytest.mark.skipif(not _upstream_alive(), reason="ship platform (:8001) 未监听")
class TestLiveContract:
    def test_payload_schema_accepted(self):
        geo = sb.geometry_payload("KVLCC2")
        data = sb._call_resistance_api("KVLCC2", 7.974, geo)
        assert data is not None and "_upstream_error" not in data, data
        assert any(k in data for k in ("rt", "ct")), data

    def test_live_validate_produces_numbers(self):
        resp = _validate(benchmark_id="KVLCC2-01")
        r = resp.results[0]
        assert r.status == "COMPARED"
        assert r.calc_ct is not None and math.isfinite(r.calc_ct)
        assert r.error_pct is not None