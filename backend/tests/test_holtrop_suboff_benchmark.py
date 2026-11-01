"""m1673 · QR-P 复核回归测试：holtrop_mennen 服务 vs 正典 SUBOFF 基准（DTRC-90/012）。

工件：artifacts/m1673/holtrop_suboff_benchmark.json（tools/validate_holtrop_suboff.py 生成）。
判据：平台标准 |C_T 误差| <= 15%；SUBOFF 在 Holtrop 1982 回归域外（B/T、Cb、Fn），
结果按 WITHHELD 定级，测试只锁「可复现 + 域外已标注 + 缺陷哨兵」。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.holtrop_mennen import holtrop_resistance  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "m1673", "holtrop_suboff_benchmark.json")
CANON = os.path.join(ROOT, "backend", "data", "benchmark_reference.json")


def _artifact():
    return json.load(open(ART))


def _canon_suboff():
    d = json.load(open(CANON))
    return [c for c in d["cases"] if c.get("ship_type") == "SUBOFF"]


def test_artifact_verdict_fields():
    art = _artifact()
    assert art["n_ct_total"] == 4
    assert art["n_ct_pass_15pct"] >= 3
    assert max(abs(r["ct_err_pct"]) for r in art["rows"]) < 25.0
    assert all(r["canon_status"] == "DERIVED" for r in art["rows"])


def test_live_service_reproduces_artifact():
    """活服务复算必须与落盘工件一致（可复现性，零手抄）。"""
    art = _artifact()
    for c, r in zip(sorted(_canon_suboff(), key=lambda x: x["speed_ms"]), art["rows"]):
        g = c["geometry"]
        L = g["length_m"]
        B = T = g["diameter_m"]
        h = holtrop_resistance(
            L, B, T, 0.542, c["speed_ms"],
            rho=c["reference_density_kg_m3"],
            nu=c["reference_kinematic_viscosity_m2_s"],
            wet_surface=g["wetted_surface_m2"],
        )
        assert abs(h.ct - r["ct_holtrop"]) < 1e-9
        assert abs(h.rt_n - r["rt_holtrop_n"]) < 1e-3


def test_out_of_domain_is_flagged():
    """SUBOFF 在 1982 回归域外，服务必须显式告警（不许静默外推）。"""
    for r in _artifact()["rows"]:
        assert any("B/T" in w for w in r["holtrop_warnings"])
        assert any("Fn" in w for w in r["holtrop_warnings"])


def test_legacy_endpoint_defect_sentinel():
    """缺陷哨兵：/calculations/resistance 默认 S 口径在 V=6 m/s 超 15% 门（m1673 实测 +34.6%）。"""
    r6 = [r for r in _artifact()["rows"] if r["speed_ms"] == 6.0][0]
    assert r6["ct_legacy_err_pct"] > 15.0
