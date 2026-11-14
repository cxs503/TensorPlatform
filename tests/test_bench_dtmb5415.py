"""
DTMB 5415 阻力对标测试
======================
验收测试：≥6 passed
"""
import json
import math
import os
import pytest

RECORD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "artifacts", "bench_dtmb5415", "benchmark_record.json"
)


@pytest.fixture(scope="module")
def record():
    """加载 benchmark_record.json."""
    with open(RECORD_PATH, "r") as f:
        return json.load(f)


# ── 测试 1: record schema 完整性 ─────────────────────────
class TestRecordSchema:
    """验证 benchmark_record.json 必备字段存在."""

    def test_has_geometry(self, record):
        assert "geometry" in record
        for key in ["Lpp", "B", "T", "Cb"]:
            assert key in record["geometry"], f"geometry.{key} missing"
            assert "value" in record["geometry"][key]
            assert "rel_err" in record["geometry"][key]

    def test_has_condition(self, record):
        assert "condition" in record
        assert "Fr" in record["condition"]
        assert "Re" in record["condition"]

    def test_has_baseline(self, record):
        assert "baseline" in record
        for key in ["value", "low", "high", "source", "evidence_grade"]:
            assert key in record["baseline"], f"baseline.{key} missing"

    def test_has_computed(self, record):
        assert "computed" in record
        for key in ["ct", "cf", "form_factor"]:
            assert key in record["computed"], f"computed.{key} missing"

    def test_has_verdict(self, record):
        assert "verdict" in record
        for key in ["rel_err_mid", "rel_err_low", "rel_err_high",
                     "pass_15pct", "pass_3pct"]:
            assert key in record["verdict"], f"verdict.{key} missing"

    def test_has_sweep(self, record):
        assert "sweep" in record
        assert len(record["sweep"]) >= 7, f"Need ≥7 sweep points, got {len(record['sweep'])}"


# ── 测试 2: rel_err 复算一致性 ────────────────────────────
class TestRelErrConsistency:
    """由 baseline/computed 复算 rel_err，与 record 存储值差 ≤1e-6."""

    def test_rel_err_mid_consistency(self, record):
        ct = record["computed"]["ct"]
        ct_mid = record["baseline"]["value"]
        expected = abs(ct - ct_mid) / ct_mid
        stored = record["verdict"]["rel_err_mid"]
        assert abs(expected - stored) <= 1e-6, (
            f"rel_err_mid mismatch: computed={expected}, stored={stored}"
        )

    def test_rel_err_low_consistency(self, record):
        ct = record["computed"]["ct"]
        ct_low = record["baseline"]["low"]
        expected = abs(ct - ct_low) / ct_low
        stored = record["verdict"]["rel_err_low"]
        assert abs(expected - stored) <= 1e-6, (
            f"rel_err_low mismatch: computed={expected}, stored={stored}"
        )

    def test_rel_err_high_consistency(self, record):
        ct = record["computed"]["ct"]
        ct_high = record["baseline"]["high"]
        expected = abs(ct - ct_high) / ct_high
        stored = record["verdict"]["rel_err_high"]
        assert abs(expected - stored) <= 1e-6, (
            f"rel_err_high mismatch: computed={expected}, stored={stored}"
        )


# ── 测试 3: 15% 门边界 ───────────────────────────────────
class TestFifteenPctGate:
    """验证 15% 硬门判定正确."""

    def test_pass_15pct_matches_rel_err(self, record):
        rel_err = record["verdict"]["rel_err_mid"]
        expected_pass = rel_err < 0.15
        stored_pass = record["verdict"]["pass_15pct"]
        assert expected_pass == stored_pass, (
            f"pass_15pct mismatch: rel_err={rel_err:.4%}, "
            f"expected={expected_pass}, stored={stored_pass}"
        )

    def test_pass_3pct_matches_rel_err(self, record):
        rel_err = record["verdict"]["rel_err_mid"]
        expected_pass = rel_err < 0.03
        stored_pass = record["verdict"]["pass_3pct"]
        assert expected_pass == stored_pass, (
            f"pass_3pct mismatch: rel_err={rel_err:.4%}, "
            f"expected={expected_pass}, stored={stored_pass}"
        )


# ── 测试 4: 几何 rel_err 上界 ────────────────────────────
class TestGeometryRelErr:
    """验证几何参数 rel_err 均 < 2%."""

    def test_geometry_rel_err_below_2pct(self, record):
        for key in ["Lpp", "B", "T", "Cb"]:
            rel_err = record["geometry"][key]["rel_err"]
            assert rel_err < 0.02, (
                f"geometry.{key} rel_err={rel_err:.4%} >= 2%"
            )


# ── 测试 5: 物理合理性 ───────────────────────────────────
class TestPhysicalReasonableness:
    """验证计算结果物理合理."""

    def test_ct_positive(self, record):
        assert record["computed"]["ct"] > 0

    def test_cf_positive(self, record):
        assert record["computed"]["cf"] > 0

    def test_form_factor_reasonable(self, record):
        fk = record["computed"]["form_factor"]
        assert 1.0 < fk < 2.0, f"form_factor {fk} out of reasonable range [1, 2]"

    def test_ct_gt_cf(self, record):
        """总阻力系数应大于摩擦阻力系数."""
        assert record["computed"]["ct"] > record["computed"]["cf"]

    def test_sweep_ct_trend(self, record):
        """Ct 应随 Fr 增加而增加（兴波阻力主导区）."""
        sweep = record["sweep"]
        for i in range(1, len(sweep)):
            # 在高 Fr 区，Ct 应随 Fr 增加
            if sweep[i]["fr"] > 0.25:
                assert sweep[i]["ct"] >= sweep[i-1]["ct"] * 0.95, (
                    f"Ct should generally increase with Fr at Fr>0.25"
                )


# ── 测试 6: 证据分级 ─────────────────────────────────────
class TestEvidenceGrade:
    """验证证据分级声明."""

    def test_evidence_grade_is_real(self, record):
        assert record["baseline"]["evidence_grade"] == "REAL", (
            f"Expected REAL, got {record['baseline']['evidence_grade']}"
        )

    def test_source_not_empty(self, record):
        assert len(record["baseline"]["source"]) > 20, (
            "Baseline source should be a meaningful citation"
        )
