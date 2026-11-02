"""m1825 — 静水力恒等式审计的判据测试（把 `geometry.hydro_curves` 的缺陷钉成显式断言）。

两类断言，缺一不可：

1. **判据自检（正控）**：解析方箱 (∇ = L·B·d, A_wp = L·B, KB = d/2, Cb = 1) 上，
   H1/H2/H3/H6 的残差必须严格为 0 —— 证明审计器本身不含截断误差、不会误杀。
2. **被测端点（负控）**：对 `artifacts/hydrostatics_audit/probe_raw.json` 里录下的
   live 响应，六条船必须逐条触发违反 —— 若哪天端点被修好，这些断言会失败并强迫
   后来者更新判定（而不是让缺陷被"顺手修掉"或静默失效，沿用 m1773 的 pin 惯例）。

判据定义见 `tools/audit/hydrostatics_identity_audit.py` 模块文档。
离线可跑：不依赖 :8001 在线。
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "tools", "audit", "hydrostatics_identity_audit.py")
SNAP = os.path.join(ROOT, "artifacts", "hydrostatics_audit", "probe_raw.json")

_spec = importlib.util.spec_from_file_location("hydro_audit", MOD)
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 1. positive control: the judge itself is exact
# ---------------------------------------------------------------------------

def test_analytic_box_passes_every_identity():
    rep = ha.selftest()
    assert rep["grade"] == "REAL"
    assert rep["failed_identity_count"] == 0
    ids = {x["id"]: x for x in rep["identities"]}
    assert ids["H1"]["max_rel_err"] == pytest.approx(0.0, abs=1e-12)   # d∇/dT = A_wp
    assert ids["H2"]["max_rel_err"] == pytest.approx(0.0, abs=1e-12)   # d(∇KB)/dT = ∇
    assert ids["H3"]["max_rel_err"] == pytest.approx(0.0, abs=1e-12)   # TPC 定义
    assert ids["H6"]["rel_err"] == pytest.approx(0.0, abs=1e-12)


def test_wall_sided_box_is_not_flagged_as_placeholder():
    """方箱 Cb=1 是 A_wp 吃水不变的唯一合法退化情形，D1 不得误报。"""
    d1 = ha.selftest()["diagnostics"][0]
    assert d1["draft_invariant"] is True
    assert d1["suspected_placeholder"] is False


def test_judge_detects_a_synthetic_placeholder():
    """人为把 A_wp 改成与吃水无关的常数 -> H1 必须报警（判据有分辨力）。"""
    payload = ha.box_curves()
    # 破坏恒等式：排水体积按 L·B·d 增长，但水线面积申报成设计吃水的常数放大 20%
    cur = payload["curves"]
    cur["Awp"] = [cur["Awp"][-1] * 1.2 for _ in cur["drafts"]]
    rep = ha.Curves(payload).audit("SYNTH_BREAK")
    assert rep["passes"]["H1"] is False
    assert rep["passes"]["H6"] is True          # 排水量仍正确
    assert rep["identities"][0]["max_rel_err"] > 0.15


# ---------------------------------------------------------------------------
# 2. negative control: the live endpoint, pinned
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def snapshot():
    if not os.path.exists(SNAP):
        pytest.skip("no recorded snapshot (run tools/audit/hydrostatics_identity_audit.py --record)")
    with open(SNAP, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def audit(snapshot):
    return ha.audit_snapshot(snapshot)


def test_reference_baseline_is_validated_against_public_data(audit):
    """判据的基准先自证：Cb_meta·L·B·T 与公开排水体积差 < 0.5%。"""
    checks = audit["reference_anchor_check"]
    assert len(checks) >= 2
    for c in checks:
        assert c["reference_validated"] is True, c
        assert c["rel_err_pct"] < 0.5, c


def test_every_hull_violates_the_waterplane_identity(audit):
    for r in audit["hulls"]:
        h1 = next(x for x in r["identities"] if x["id"] == "H1")
        assert h1["max_rel_err"] > 0.15, f"{r['hull']}: H1 residual {h1['max_rel_err']}"


def test_every_hull_violates_the_buoyancy_identity(audit):
    for r in audit["hulls"]:
        h2 = next(x for x in r["identities"] if x["id"] == "H2")
        assert h2["max_rel_err"] > 0.15, f"{r['hull']}: H2 residual {h2['max_rel_err']}"


def test_waterplane_area_is_draft_invariant_on_all_hulls(audit):
    """D1：A_wp 吃水 spread < 0.5% 而 Cb_meta < 1 -> 占位常数指纹。"""
    for r in audit["hulls"]:
        d1 = r["diagnostics"][0]
        assert d1["suspected_placeholder"] is True, f"{r['hull']}: {d1}"


@pytest.mark.parametrize("hull,expected_cb_min", [("KCS", 1.15), ("KVLCC2", 1.40), ("JBC", 1.45)])
def test_block_coefficient_exceeds_unity(audit, hull, expected_cb_min):
    """Cb > 1 是物理不可能量：本断言把该量级钉住，防止不声不响地被调小。"""
    r = next(x for x in audit["hulls"] if x["hull"] == hull)
    h4 = next(x for x in r["identities"] if x["id"] == "H4")
    assert h4["physical_violation"] is True
    assert h4["cb_at_design_draft"] >= expected_cb_min


def test_design_displacement_error_exceeds_accuracy_standard(audit):
    """平台精度标准：关键量 vs 基准 > 15% 不可接受。全部 5 条计分船都超门。"""
    scored = [r for r in audit["hulls"] if not r["resolution_limited"]]
    assert len(scored) == 5
    for r in scored:
        h6 = next(x for x in r["identities"] if x["id"] == "H6")
        assert h6["rel_err"] > 0.15, f"{r['hull']}: ∇ error {h6['rel_err']*100:.1f}%"


def test_overall_verdict_is_not_real(audit):
    assert audit["summary"]["verdict"] == "NOT_REAL"
    assert audit["summary"]["grade_counts"]["REAL"] == 0
    assert audit["summary"]["hulls_failing_at_least_one_identity"] == 5