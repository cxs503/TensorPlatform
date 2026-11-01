"""Tests for the benchmark-reference integrity gate.

覆盖三层：
1. 审计引擎（tools/audit/benchmark_reference_integrity.py）的物理恒等判定；
2. 数据集元数据与引擎结论同步（防止 integrity 块过期）；
3. 消费侧门禁：WITHHELD 真值**永不**产出 pass，且不计入 pass_rate。

第 3 组是关键回归：即便求解器恰好命中一条伪造真值（error=0%），也不得判 PASS。
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))


def _load_module(name: str, path: Path):
    """按文件路径加载模块。

    不走 ``import api.domain.ship_benchmark``：``api/__init__`` 会连带导入
    database（psycopg2）/cryptography，在被测逻辑之外引入部署依赖。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# 审计工具（tools/ 不是安装包）+ 被测消费模块
audit_mod = _load_module(
    "benchmark_reference_integrity", REPO_ROOT / "tools" / "audit" / "benchmark_reference_integrity.py"
)
ship_benchmark = _load_module(
    "ship_benchmark_under_test", BACKEND_DIR / "api" / "domain" / "ship_benchmark.py"
)

BenchmarkValidateRequest = ship_benchmark.BenchmarkValidateRequest
_load_benchmark_data = ship_benchmark._load_benchmark_data
benchmark_validate = ship_benchmark.benchmark_validate
list_benchmark_cases = ship_benchmark.list_benchmark_cases


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _consistent_case(case_id="SYN-01", ship_type="DTMB-5415", fr_override=None, rt_scale=1.0,
                     ct_scale=1.0, declare_surface=True):
    """Build a physically self-consistent synthetic case (identity holds by construction)."""
    L, B, T, cb = 5.0, 1.0, 0.35, 0.55
    rho, nu = audit_mod.MEDIA["fresh"]
    speed = 2.0
    hw = audit_mod.holtrop_wetted_surface(L, B, T, cb, cm=0.85)
    s = round(hw["wetted_surface_m2"], 3)
    cf = audit_mod.ittc57_cf(speed * L / nu)
    ct = round(1.4 * cf * ct_scale, 6)                      # 摩擦 + 形状/兴波余量
    rt = round(ct * 0.5 * rho * speed * speed * s * rt_scale, 3)
    geo = {"length_m": L, "beam_m": B, "draft_m": T, "cb": cb}
    if declare_surface:
        geo["wetted_surface_m2"] = s
    return {
        "case_id": case_id,
        "ship_type": ship_type,
        "speed_ms": speed,
        "froude_number": fr_override if fr_override is not None
        else round(audit_mod.froude_number(speed, L), 4),
        "experimental_ct": ct,
        "experimental_rt_n": rt,
        "geometry": geo,
        "source_citation": "synthetic",
    }


# ---------------------------------------------------------------------------
# 1. 审计引擎
# ---------------------------------------------------------------------------

class TestAuditEngine:
    def test_friction_line_matches_ittc57_anchor(self):
        """ITTC-57 在 Re=1e7 处的 Cf 应落在文献 2.9e-3 附近（±3%）。"""
        cf = audit_mod.ittc57_cf(1.0e7)
        assert abs(cf - 0.002934) / 0.002934 < 0.03, cf

    def test_froude_number_identity(self):
        assert audit_mod.froude_number(3.0, 4.356) == pytest.approx(0.4589, abs=1e-3)

    def test_holtrop_wetted_surface_kvlcc2_band(self):
        """KVLCC2 主尺度下 Holtrop 湿表面积应接近公开值 27,194 m²（±10%）。"""
        s = audit_mod.holtrop_wetted_surface(
            320.0, 58.0, 20.8, 0.8098, cm=0.9986, cwp=0.8564
        )["wetted_surface_m2"]
        assert abs(s - 27194.0) / 27194.0 < 0.10, s

    def test_consistent_case_is_real(self):
        res = audit_mod.audit_case(_consistent_case())
        assert res["verdict"] == "REAL", res["reason"]
        assert all(c["pass"] for c in res["checks"].values())

    def test_estimated_surface_is_derived_not_real(self):
        res = audit_mod.audit_case(_consistent_case(declare_surface=False))
        assert res["verdict"] == "DERIVED", res["reason"]

    def test_froude_mismatch_withheld(self):
        res = audit_mod.audit_case(_consistent_case(fr_override=0.061))
        assert res["verdict"] == "WITHHELD"
        assert "C1_froude" in res["reason"]

    def test_resistance_identity_violation_withheld(self):
        """Rt 缩水 10 倍必须被 C2 抓到。"""
        res = audit_mod.audit_case(_consistent_case(rt_scale=0.1))
        assert res["verdict"] == "WITHHELD"
        assert "C2_resistance" in res["reason"]
        c2 = res["checks"]["C2_resistance"]
        assert c2["implied_over_ref"] == pytest.approx(0.1, rel=0.05)

    def test_ct_below_friction_floor_withheld(self):
        """Ct 低于平板摩擦阻力物理上不可能。"""
        res = audit_mod.audit_case(_consistent_case(ct_scale=0.3))
        assert res["verdict"] == "WITHHELD"
        assert "C3_friction" in res["reason"]

    def test_derived_resistance_recomputed_from_ct(self):
        case = _consistent_case()
        res = audit_mod.audit_case(case)
        rt_d = res["checks"]["C2_resistance"]["rt_derived_from_ct_n"]
        assert rt_d == pytest.approx(case["experimental_rt_n"], rel=0.01)

    def test_trend_check_flags_ct_drop(self):
        data = {
            "cases": [
                _consistent_case("A", fr_override=0.10),
                dict(_consistent_case("B", fr_override=0.20), experimental_ct=0.001),
            ]
        }
        audit = audit_mod.audit_dataset(data)
        assert audit["trend_flags"], "Ct 下降未被 C4 捕获"


# ---------------------------------------------------------------------------
# 2. 数据集元数据与引擎结论同步
# ---------------------------------------------------------------------------

class TestRegistryMetadata:
    """正典 v3.0 的落库元数据（``reference_status``/``audit_flags``/
    ``legacy_values``）必须与独立审计工具的重算结果一致。

    m1383 分支把判定写进自定义的 ``integrity`` 块；正典已用
    ``reference_status`` + ``adjudication_reason`` 承载同一语义，故这里按正典
    字段断言（数据判定面以正典为准，工具保留为第二独立复核）。
    """

    VERDICTS = {"REAL", "DERIVED", "WITHHELD"}

    def test_every_case_has_verdict(self):
        data = _load_benchmark_data()
        for case in data["cases"]:
            assert case.get("reference_status") in self.VERDICTS, case["case_id"]
            assert case.get("adjudication_reason"), case["case_id"]

    def test_metadata_in_sync_with_auditor(self):
        """落库判定必须与重跑独立审计一致（防元数据腐烂）。"""
        data = _load_benchmark_data()
        audit = audit_mod.audit_dataset(data)
        fresh = {r["case_id"]: r["verdict"] for r in audit["cases"]}
        # 工具的 REAL/DERIVED 与正典 REAL/DERIVED 语义等价：都表示"可作判据"
        for case in data["cases"]:
            stored = case["reference_status"]
            recomputed = fresh[case["case_id"]]
            assert (stored == "WITHHELD") == (recomputed == "WITHHELD"), (
                f"{case['case_id']}: 落库 {stored} != 重算 {recomputed}"
            )

    def test_auditor_reproduces_suboff_rt(self):
        """独立工具必须把 SUBOFF 复算到正典的 92.52 N（淡水 998.2）。"""
        data = _load_benchmark_data()
        audit = audit_mod.audit_dataset(data)
        rows = {r["case_id"]: r for r in audit["cases"]}
        assert rows["SUBOFF-01"]["verdict"] == "REAL"
        c2 = rows["SUBOFF-01"]["checks"]["C2_resistance"]
        assert c2["rt_reported_n"] == pytest.approx(92.52, abs=0.01)
        assert c2["rt_derived_from_ct_n"] == pytest.approx(92.52, abs=0.05)
        assert c2["s_wetted_source"] == "declared"
        assert abs(c2["implied_over_ref"] - 1.0) < 5e-3
        assert [rows[f"SUBOFF-0{i}"]["verdict"] for i in range(1, 5)] == ["REAL"] * 4

    def test_withheld_cases_keep_legacy_values(self):
        """审计不改写原始实验值，只把它降级为 legacy_* 并加判定。"""
        data = _load_benchmark_data()
        dtmb = next(c for c in data["cases"] if c["case_id"] == "DTMB5415-01")
        assert dtmb["reference_status"] == "WITHHELD"
        assert dtmb["legacy_values"]["filed_experimental_rt_n"] == pytest.approx(8.2)
        assert dtmb["legacy_values"]["filed_froude_number"] == pytest.approx(0.061)

    def test_no_withheld_case_claims_usable(self):
        """WITHHELD 行不得携带任何"可作判据"的标记。"""
        data = _load_benchmark_data()
        for case in data["cases"]:
            if case["reference_status"] == "WITHHELD":
                assert "resistance_not_reviewable" in case["audit_flags"] or \
                    case.get("rt_status") == "WITHHELD", case["case_id"]


# ---------------------------------------------------------------------------
# 3. 消费侧门禁：伪真值不得授予 PASS
# ---------------------------------------------------------------------------

class TestConsumerGate:
    def test_perfect_hit_on_withheld_reference_never_passes(self, monkeypatch):
        """求解器恰好命中不可复核的真值（error=0%）时仍不得判 PASS。"""
        data = _load_benchmark_data()
        case = next(c for c in data["cases"] if c["reference_status"] == "WITHHELD")

        def _mock(ship_type, speed_ms, geometry):
            return {"ct": case["experimental_ct"], "method": "mock-echo"}

        monkeypatch.setattr(ship_benchmark, "_call_resistance_api", _mock)
        resp = benchmark_validate(
            BenchmarkValidateRequest(ship_type=case["ship_type"], benchmark_id=case["case_id"])
        )
        result = resp.results[0]
        assert result.error_pct == pytest.approx(0.0, abs=1e-6)
        assert result.pass_ is False, "WITHHELD 真值不得授予 PASS"
        assert result.withheld is True
        assert result.dataset_status != "CONSISTENT"
        assert "DATASET-WITHHELD" in result.note
        assert resp.summary["verified_cases"] == 0
        assert case["case_id"] in resp.summary["withheld_cases"]

    def test_honest_fail_semantics_preserved(self, monkeypatch):
        """原有 honest-FAIL 约定不得被门禁吞掉：pass 仍为 False。"""
        data = _load_benchmark_data()
        case = next(
            c for c in data["cases"]
            if c["ship_type"] == "SUBOFF" and (c.get("froude_number") or 0) > 0.4
        )

        def _mock(ship_type, speed_ms, geometry):
            # 该标定区外拟合对细长体高 Fr 系统性高估
            return {"ct": case["experimental_ct"] * 1.6, "method": "mock-holtrop"}

        monkeypatch.setattr(ship_benchmark, "_call_resistance_api", _mock)
        resp = benchmark_validate(
            BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id=case["case_id"])
        )
        result = resp.results[0]
        assert result.error_pct > 15.0
        assert result.pass_ is False
        assert "HONEST-FAIL" in result.note
        assert resp.summary["honest_fail"] is True

    def test_listing_surfaces_verdicts(self):
        out = list_benchmark_cases()
        assert out["total"] == len(out["cases"])
        statuses = out["dataset_status"]
        assert len(statuses) == out["total"]
        assert sum(1 for v in statuses.values() if v == "CONSISTENT") == 4
