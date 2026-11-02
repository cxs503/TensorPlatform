"""优化器目标函数复核（m1601 建，m1610 改为双轨）的回归/失效测试。

轨道约定
--------
* ``audit_mod.audit("live")``   —— 实际目标函数（委托正典 Holtrop-1984），应判 REAL；
* ``audit_mod.audit("legacy")`` —— 强制旧手拟 fallback，必须仍判 MOCK 且失败项
  固定为 {C3, C4, C5, C6}（"退回旧链条"的回归钉子）。

每个判据至少一个"变异体判负"用例：注入物理上不可能的实现，判据必须翻成 FAIL，
证明判据不是恒真的装饰物。四个变异体用例（湿面积×4 / Ct 与 V 无关 / 正典物理 /
按实际走向判级）保留原断言，不得删改。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimizer.audit import optimizer_objective_audit as audit_mod  # noqa: E402

ART = ROOT / "artifacts" / "optimizer_objective_audit" / "optimizer_objective_audit.json"


@pytest.fixture(scope="module")
def result():
    """live 轨（实际目标函数）。"""
    return audit_mod.audit("live")


@pytest.fixture(scope="module")
def legacy():
    """legacy 轨（强制旧手拟 fallback）。"""
    return audit_mod.audit("legacy")


def _by(res, cid):
    for c in res["checks"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


# ---------------------------------------------------------------------------
# 判决
# ---------------------------------------------------------------------------
def test_objective_verdict_flipped_to_real(result):
    """核心结论：目标函数委托正典后证据等级由 MOCK 翻 REAL，七条判据无 FAIL。"""
    assert result["verdict_evidence_level"] == "REAL"
    assert result["failed"] == []
    assert result["objective_evidence_level"] == "REAL"
    assert "holtrop1984" in result["objective_physics_source"]


def test_legacy_fallback_stays_mock(legacy):
    """回归钉子：旧手拟链条一旦被当成目标函数，必须仍判 MOCK。"""
    assert legacy["verdict_evidence_level"] == "MOCK"
    assert legacy["objective_evidence_level"] == "MOCK"
    assert set(legacy["failed"]) == {"C3", "C4", "C5", "C6"}


def test_geometry_identity_passes(result):
    """KCS 几何先过 Cb/L/B：L/B、B/T、Cb 都在 Holtrop 标定域，∇=Cb·L·B·T。"""
    c1 = _by(result, "C1")
    assert c1["pass"] is True
    v = c1["values"]
    assert 3.0 <= v["L_B"] <= 8.0 and 2.0 <= v["B_T"] <= 4.0 and 0.55 <= v["Cb"] <= 0.75
    g = result["inputs"]["geom"]
    assert v["nabla_m3"] == pytest.approx(g["Cb"] * g["L"] * g["B"] * g["T"], rel=1e-12)
    # m1610：几何必须携带正典需要的 cm/cwp
    assert 0.85 <= v["Cm"] <= 0.999
    assert v["Cwp"] > g["Cb"]


def test_ca_dimensional_inconsistency(legacy):
    """legacy 把 Ca 以 [m²] 量加到 [N] 上：隐含系数比无量纲值低 ~2 个数量级。"""
    c3 = _by(legacy, "C3")
    assert c3["pass"] is False
    v = c3["values"]
    assert v["implied_coefficient"] < 1e-5 < v["Ca_dimensionless"]
    assert v["rel_err_pct_of_coefficient"] > 99.0
    assert v["Ra_correct_N"] > 10 * v["Ca_force_recorded_N"]


def test_ca_dimension_consistent_on_live(result):
    """live 轨的 Ca 无量纲进入 0.5ρV²S：隐含系数与正典一致（<3%）。"""
    c3 = _by(result, "C3")
    assert c3["pass"] is True
    assert c3["values"]["rel_err_pct_of_coefficient"] < 3.0
    assert c3["values"]["implied_coefficient"] == pytest.approx(
        c3["values"]["Ca_dimensionless"], rel=1e-6)


def test_ct_is_bare_friction_line(legacy):
    """legacy 的 Ct 与 ITTC-57 Cf 相对差 <0.1%：残余+兴波被指数项抹平。"""
    mc = legacy["inputs"]["mock_components"]
    assert mc["Ct"] == pytest.approx(mc["Cf"], rel=1e-2)
    assert mc["Rw_N"] / mc["Rt_N"] < 1e-6
    c4 = _by(legacy, "C4")
    assert c4["pass"] is False and c4["values"]["Cr_Cf_mock"] < 1e-3


def test_ct_error_vs_canonical_exceeds_gate(legacy):
    """总阻力 vs 正典 Holtrop-1984 误差 >15%（平台不可接受门），且不满足 <3%。"""
    c5 = _by(legacy, "C5")
    assert c5["pass"] is False
    assert c5["values"]["err_pct_Ct"] > 15.0
    assert c5["values"]["err_pct_Rt"] > 15.0
    assert c5["values"]["under_3pct"] is False


def test_ct_matches_canonical_on_live(result):
    """live 轨与正典同源：Ct / Rt 相对误差 <3%。"""
    c5 = _by(result, "C5")
    assert c5["pass"] is True
    assert c5["values"]["err_pct_Ct"] < 3.0
    assert c5["values"]["err_pct_Rt"] < 3.0
    assert c5["values"]["under_3pct"] is True


def test_physical_lower_bound_violated(legacy):
    """legacy Ct 未达到 (1+k)Cf 粘性下界 → 形阻被系统性漏算。"""
    c6 = _by(legacy, "C6")
    assert c6["pass"] is False
    assert c6["values"]["Ct_mock"] < c6["values"]["Cf_times_1pk"]


def test_physical_lower_bound_holds_on_live(result):
    """live 轨满足 Ct ≥ (1+k)Cf，且高出的部分来自兴波+补贴。"""
    c6 = _by(result, "C6")
    assert c6["pass"] is True
    assert c6["values"]["Ct_over_Cf"] > 1.0


def test_speed_limits_hold(result):
    """速度极限/单调性这条对新旧两轨都过：证明判据不是全盘判负。"""
    assert _by(result, "C7")["pass"] is True
    assert _by(result, "C7")["values"]["Rt_monotonic_increasing"] is True


def test_drift_gate_matches_artifact():
    """漂移闸：复算结果必须等于入库 JSON（否则入库产物被手改或不新鲜）。"""
    if not ART.exists():
        pytest.skip("入库 JSON 缺失")
    assert audit_mod.main(["--check"]) == 0


def test_tools_entry_point_matches_impl():
    """验收入口 tools/audit/...py 与实现同源（薄封装，不得有第二份判据逻辑）。"""
    shim = ROOT / "tools" / "audit" / "optimizer_objective_audit.py"
    if not shim.exists():
        pytest.skip("tools 入口不在本工作树")
    artifact = ART / "optimizer_objective_audit.json"
    # 入库 JSON 被 pusher 黑名单排除时（GitHub CI 树）漂移闸无从比起；
    # 退到无参模式：复算双轨并打印判决，同样完整走 shim 再导出链。
    args = ["--check"] if artifact.exists() else []
    proc = subprocess.run([sys.executable, str(shim), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ("PASS" in proc.stdout) or ("verdict=REAL" in proc.stdout)


# ---------------------------------------------------------------------------
# 变异体（不得删改）
# ---------------------------------------------------------------------------
def _patched(fn):
    """把 optimizer 目标函数替换为变异体后取一条判据。"""
    with mock.patch("optimizer.interfaces.resistance_eval.holtrop_resistance", fn):
        return audit_mod.audit()


def test_mutation_inflated_wetted_surface_fails_geometry(result):
    """变异体：湿面积放大 4 倍 → C2 必须 FAIL（夹逼/异实现双保险）。"""
    orig = audit_mod._mock_path()["raw"]

    def bad(geom, V):
        r = dict(orig)
        r["S_wet"] = orig["S_wet"] * 4.0
        return r

    res = _patched(bad)
    assert _by(res, "C2")["pass"] is False


def test_mutation_velocity_independent_ct_fails_monotonicity(result):
    """变异体：Ct 与 V 无关 → C7（Ct(V) 单调下降）必须 FAIL。"""
    orig = audit_mod._mock_path()["raw"]

    def bad(geom, V):
        r = dict(orig)
        r["Ct"] = 0.004
        r["Rt_N"] = 0.5 * 1025.0 * V ** 2 * orig["S_wet"] * 0.004
        return r

    res = _patched(bad)
    assert _by(res, "C7")["pass"] is False


def test_mutation_correct_physics_flips_verdict(result):
    """变异体（正向）：把 Ct/Rt 换成正典值 → C5/C6 必须翻 PASS，证明判据可升不可降。"""
    can = result["inputs"]["canonical"]
    assert can["available"], "正典内核不可用则本用例无意义"

    def good(geom, V):
        mod = audit_mod._load_canonical()["module"]
        hg = mod.HullGeometry(name="kcs", l=geom["L"], b=geom["B"], t=geom["T"],
                              cb=geom["Cb"], cm=mod.estimate_cm(geom["Cb"]),
                              cwp=mod.estimate_cwp(geom["Cb"]))
        r = mod.holtrop1984_resistance(hg, V, rho=1025.0, nu=1.19e-6)
        return {"V_ms": V, "Fn": r.fn, "Re": r.re, "S_wet": r.s_ref, "Rf_N": r.rf,
                "Rr_N": r.rb + r.rtr, "Rw_N": r.rw, "Ca_N": r.ra, "Rt_N": r.rt,
                "Ct": r.ct, "Cf": r.cf, "Cr": r.cw}

    res = _patched(good)
    assert _by(res, "C5")["pass"] is True
    assert _by(res, "C6")["pass"] is True


def test_objective_stamps_evidence_level():
    """目标函数输出必须自带证据等级，下游不得把 MOCK 当 REAL 消费。"""
    import numpy as np

    from optimizer.interfaces.kcs_bow_param import KCS_BASELINE, generate_geometry
    from optimizer.interfaces.resistance_eval import evaluate_resistance

    out = evaluate_resistance(generate_geometry(np.zeros(8)), KCS_BASELINE["Fn_design"])
    assert out["evidence_level"] == "REAL"
    assert out["objective_evidence_level"] == "REAL"
    assert "holtrop1984" in out["objective_physics_source"]
    assert out["physics_source"] == out["objective_physics_source"]


def test_fallback_path_is_stamped_mock(monkeypatch):
    """正典内核不可用时退回旧链条：必须继续标注 MOCK 并给出 fallback_reason。"""
    import numpy as np

    from optimizer.interfaces import canonical_bridge, resistance_eval
    from optimizer.interfaces.kcs_bow_param import KCS_BASELINE, generate_geometry

    geom = generate_geometry(np.zeros(8))
    fn = KCS_BASELINE["Fn_design"]
    V = fn * (9.81 * geom["L"]) ** 0.5

    monkeypatch.setattr(canonical_bridge, "canonical_kernel",
                        lambda: {"module": None, "available": False,
                                 "provenance": {"source": "unavailable"}})
    monkeypatch.setattr(resistance_eval, "canonical_kernel",
                        canonical_bridge.canonical_kernel)
    out = resistance_eval.evaluate_resistance(geom, fn)
    assert out["objective_evidence_level"] == "MOCK"
    assert out["objective_physics_source"] == resistance_eval.LEGACY_SOURCE
    assert "fallback_reason" in out

    # 明确禁止 fallback 时抛错，而不是静默给数
    monkeypatch.undo()
    monkeypatch.setattr(canonical_bridge, "canonical_kernel",
                        lambda: {"module": None, "available": False,
                                 "provenance": {"source": "unavailable"}})
    monkeypatch.setattr(resistance_eval, "canonical_kernel",
                        canonical_bridge.canonical_kernel)
    with pytest.raises(RuntimeError):
        resistance_eval.holtrop_resistance(geom, V, allow_fallback=False)


def test_no_second_holtrop_chain_in_optimizer():
    """禁则在测：优化器侧不得复制 Holtrop 系数链（c1/c2/c5/c7 的数值指纹）。"""
    src = (ROOT / "optimizer" / "interfaces" / "resistance_eval.py").read_text()
    for fingerprint in ("2223105", "c14", "0.487118", "1.446 * cp", "cf_schoenherr"):
        assert fingerprint not in src, f"optimizer 侧出现正典系数链指纹 {fingerprint!r}"


def test_optimizer_result_carries_objective_evidence(tmp_path):
    """优化器结果 JSON 携带 objective_evidence_level / objective_physics_source。"""
    from optimizer.core.optimization_loop import run_optimization

    out = run_optimization(seed=7, max_evals=12, patience=2, output_dir=str(tmp_path / "run"))

    assert out["objective_evidence_level"] == "REAL"
    assert "holtrop1984" in out["objective_physics_source"]
    assert out["metadata"]["objective_evidence_level"] == "REAL"

    written = json.loads((tmp_path / "run" / "optimization_result.json").read_text())
    assert written["objective_evidence_level"] == "REAL"
    assert "holtrop1984" in written["objective_physics_source"]
    assert written["metadata"]["objective_physics_source"] == written["objective_physics_source"]
    assert written["top5_candidates"], "top5 不得为空"
    assert all(c["objective_evidence_level"] == "REAL" for c in written["top5_candidates"])