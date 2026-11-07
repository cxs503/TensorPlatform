"""IN47-N08 验收测试：交叉模型验证 / 外部效度 / 评分器稳健性。

判据（机器可裁）
---------------
1. 交叉模型验证：>3 个物理案例 × 2 个独立推理族，一致率 ≥80%，仅一致案例标
   ``cross_model_verified``；等级差 >1 的严重不匹配必须单列。
2. 外部效度：hold-out 船型 ≥3，hold-out 与标定集判级正确率 |Δ| ≤5 pp。
3. 评分器稳健性：≥20 案例 × 5 次独立评价，判级翻转率 ≤5%。
4. 反作弊：STUB/MOCK/几何不自洽/参考值自相矛盾/仓内自证 都不得被判 REAL。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.evaluation_cross_model import (  # noqa: E402
    CaseRecord,
    MOCK,
    NOT_SUPPORTED,
    REAL,
    REF_SELF_CONSISTENCY_TOL_PCT,
    STUB,
    TOLERANCE_PCT,
    WITHHELD,
    build_corpus,
    cross_verify,
    evaluate_external_validity,
    evaluate_robustness,
    family_a,
    family_b,
    geometry_gate,
    holdout_hulls,
    oracle_label,
    run_all,
    tau_for,
)


# ───────────────────────────── 夹具 ─────────────────────────────


def _case(case_id: str, *, err_pct: float = 1.0, ref_class: str = "analytic",
          split: str = "verified", domain: str = "hydrostatics", hull: str = "H",
          geometry: dict | None = None, **kw) -> CaseRecord:
    """合成案例：predicted 相对 reference 的偏差由 ``err_pct`` 精确控制。"""
    ref = 100.0
    return CaseRecord(
        case_id=case_id, domain=domain, hull=hull, split=split,
        quantity="q", unit="-", predicted=ref * (1.0 + err_pct / 100.0),
        reference=ref, ref_class=ref_class,
        geometry={"ok": True} if geometry is None else geometry, **kw,
    )


# ───────────────────────────── 1. 语料与几何门 ─────────────────────────────


def test_corpus_covers_domains_and_holdout_hulls():
    cases = build_corpus()
    assert len(cases) >= 20
    assert len({c.domain for c in cases}) >= 3
    assert len(holdout_hulls(cases)) >= 3
    # 几何先验证：全部案例过门
    assert all(c.geometry.get("ok") for c in cases)
    # 判据一所需的物理案例：SUBOFF 阻力 / Wigley 静水力 / KVLCC2 操纵 都在语料里
    hulls = {c.hull for c in cases}
    assert {"SUBOFF", "Wigley", "KVLCC2"} <= hulls


def test_geometry_gate_rejects_inconsistent_cb():
    ok = geometry_gate(320.0, 58.0, 20.8, 0.8098, 320.0 * 58.0 * 20.8 * 0.8098)
    bad = geometry_gate(320.0, 58.0, 20.8, 0.8098, 320.0 * 58.0 * 20.8 * 0.60)
    assert ok["ok"] is True
    assert bad["ok"] is False and bad["checks"]["cb_consistent"] is False


# ───────────────────────────── 2. 参考标准（oracle）─────────────────────────────


def test_oracle_labels_structural_failures():
    assert oracle_label(_case("stub", stub=True, value_present=False))["label"] == STUB
    assert oracle_label(_case("mock", responds_to_inputs=False))["label"] == MOCK
    assert oracle_label(_case("nosupport", geometry={"ok": False}))["label"] == NOT_SUPPORTED


def test_oracle_withholds_on_inconsistent_reference():
    bad = _case("badref", err_pct=1.0, ref_class="published",
                ref_self_consistency_pct=REF_SELF_CONSISTENCY_TOL_PCT * 10)
    assert oracle_label(bad)["label"] == WITHHELD
    good = _case("goodref", err_pct=1.0, ref_class="published",
                 ref_self_consistency_pct=0.5)
    assert oracle_label(good)["label"] == REAL


def test_oracle_withholds_on_internal_crosscheck_and_out_of_domain():
    assert oracle_label(_case("xc", err_pct=1.0, ref_class="internal_crosscheck"))["label"] == WITHHELD
    assert oracle_label(_case("ood", err_pct=1.0, domain_ok=False))["label"] == WITHHELD


def test_oracle_tolerance_is_preregistered_15pct():
    assert TOLERANCE_PCT == 15.0
    assert oracle_label(_case("pass", err_pct=14.0, ref_class="analytic"))["label"] == REAL
    assert oracle_label(_case("fail", err_pct=25.0, ref_class="analytic"))["label"] == WITHHELD


# ───────────────────────────── 3. 两个推理族 ─────────────────────────────


def test_family_a_threshold_behaviour():
    assert family_a(_case("good", err_pct=5.0))["label"] == REAL
    assert family_a(_case("bad", err_pct=25.0))["label"] == WITHHELD
    assert family_a(_case("mock", responds_to_inputs=False))["label"] == MOCK
    assert family_a(_case("stub", stub=True, value_present=False))["label"] == STUB


def test_family_b_tau_is_calibrated_at_tolerance_boundary():
    """τ 的标定规则：偏差正好等于容差的合成边界案例**不得**被判 REAL。"""
    boundary = _case("boundary", err_pct=TOLERANCE_PCT, ref_class="analytic")
    inside = _case("inside", err_pct=TOLERANCE_PCT / 2.0, ref_class="analytic")
    assert family_b(boundary)["label"] == WITHHELD
    assert family_b(inside)["label"] == REAL
    # τ 随参考等级单调（质量越低要求越严）
    assert tau_for("analytic") > tau_for("regression") > tau_for("published")


def test_independence_gate_blocks_inhouse_evidence():
    """仓内引用 / 仓内交叉实现不是独立证据：两族都不得判 REAL。"""
    repo_cit = _case("repo_citation", err_pct=0.5, ref_class="repo_citation")
    internal = _case("internal", err_pct=0.5, ref_class="internal_crosscheck")
    for case in (repo_cit, internal):
        assert family_a(case)["label"] == WITHHELD
        assert family_b(case)["label"] == WITHHELD


def test_families_are_independent_implementations():
    """两族机制不同：族 A 用硬阈值，族 B 用加权合成；族 B 有合成分 detail。"""
    case = _case("x", err_pct=8.0)
    va, vb = family_a(case), family_b(case)
    assert va["family"] != vb["family"]
    assert va["detail"]["stage"] == "deviation"
    assert vb["detail"]["stage"] == "composite"
    assert 0.0 <= vb["score"] <= 1.0


# ───────────────────────────── 4. 交叉验证 ─────────────────────────────


def test_cross_verify_agreement_and_verified_flags():
    cases = [_case(f"c{i}", err_pct=2.0) for i in range(5)]
    out = cross_verify(cases)
    assert out["n_cases"] == 5
    assert out["consistency"] == 1.0
    assert out["pass"] is True
    assert set(out["cross_model_verified"]) == {c.case_id for c in cases}
    assert out["precision_vs_oracle"] == 1.0


def test_cross_verify_detects_dispute_and_severe_mismatch():
    """族 A 判 REAL、族 B 判 WITHHELD 的案例必须同时记入 disputes 与 severe。"""
    # 参考等级 published(q=0.85) + 偏差 14.9%（贴着 15% 容差）：A 判 REAL；B 的合成分
    # 低于该等级的解析标定 τ → 形成真实争议（see ablation.tau_calibration）
    case = _case("edge", err_pct=14.9, ref_class="published")
    va, vb = family_a(case), family_b(case)
    assert va["label"] == REAL
    assert vb["label"] == WITHHELD
    out = cross_verify([case, _case("ok", err_pct=1.0)])
    assert out["n_agreed"] == 1
    assert out["disputes"] and out["disputes"][0]["case_id"] == "edge"
    assert out["severe_mismatches"], "等级差 >1 的不一致必须单列"
    assert "edge" not in out["cross_model_verified"]


# ───────────────────────────── 5. 外部效度 ─────────────────────────────


def test_external_validity_passes_when_holdout_matches():
    cases = [_case(f"v{i}", err_pct=1.0) for i in range(4)]
    cases += [_case(f"h{i}", err_pct=1.0, split="holdout", hull=f"H{i}") for i in range(3)]
    ev = evaluate_external_validity(cases)
    assert ev["n_holdout_hulls"] == 3
    assert ev["scorer_external_validity"]["delta_accuracy_pp"] == pytest.approx(0.0, abs=1e-9)
    assert ev["pass"] is True


def test_external_validity_fails_when_holdout_degrades():
    cases = [_case(f"v{i}", err_pct=1.0) for i in range(4)]
    holdout_ok = [_case(f"h{i}", err_pct=1.0, split="holdout", hull=f"H{i}") for i in range(3)]
    # 一个 hold-out 案例的参考值自相矛盾 → 两族 REAL 但参考标准 WITHHELD = 判级错误
    holdout_bad = _case("hbad", err_pct=1.0, split="holdout", hull="HX",
                        ref_self_consistency_pct=50.0)
    ev = evaluate_external_validity(cases + holdout_ok + [holdout_bad])
    assert ev["scorer_external_validity"]["delta_accuracy_pp"] < -5.0
    assert ev["pass"] is False


def test_external_validity_requires_three_holdout_hulls():
    cases = [_case(f"v{i}", err_pct=1.0) for i in range(4)]
    cases += [_case("h0", err_pct=1.0, split="holdout", hull="H0")]
    ev = evaluate_external_validity(cases)
    assert ev["n_holdout_hulls"] == 1
    assert ev["pass"] is False


# ───────────────────────────── 6. 稳健性 ─────────────────────────────


def test_robustness_no_noise_means_no_flips():
    cases = [_case(f"c{i}", err_pct=1.0) for i in range(25)]
    rb = evaluate_robustness(cases, n_evals=5)
    assert rb["n_cases"] == 25
    assert rb["flip_rate"] == 0.0
    assert rb["pass"] is True


def test_robustness_requires_twenty_cases():
    cases = [_case(f"c{i}", err_pct=1.0) for i in range(5)]
    rb = evaluate_robustness(cases, n_evals=5, seed=7)
    assert rb["n_cases"] == 5
    assert rb["pass"] is False  # 案例数不足 20，不得判通过


def test_robustness_reports_single_and_median_protocols():
    cases = [_case(f"c{i}", err_pct=1.0) for i in range(22)]
    rb = evaluate_robustness(cases, n_evals=5, seed=11)
    assert "single_measurement" in rb and "median3_protocol" in rb
    assert rb["single_measurement"]["n_evaluations"] == 22 * 5
    assert rb["median3_protocol"]["k_measurements"] == 3
    assert len(rb["noise_sweep"]) >= 3


# ───────────────────────────── 7. 端到端 ─────────────────────────────


def test_run_all_writes_artifacts_and_verdict(tmp_path):
    out = run_all(out_dir=tmp_path, n_evals=5)
    v = out["verdict"]
    assert v["schema"] == "in47-n08-maturity/1"
    assert v["verdict"] in ("PASS", "HONEST_FAIL")
    for key in ("cross_model", "external_validity", "scorer_robustness"):
        assert key in v["criteria"]
    # 判据数字必须落盘
    for name in ("verdict.json", "cross_model.json", "external_validity.json",
                 "robustness.json", "physics_error_table.json", "oracle_labels.json"):
        p = tmp_path / name
        assert p.exists(), name
        json.loads(p.read_text())
    payload = json.loads((tmp_path / "verdict.json").read_text())
    assert payload["criteria"]["cross_model"]["n_cases"] >= 20
    assert payload["criteria"]["external_validity"]["n_holdout_hulls"] >= 3
    assert payload["criteria"]["scorer_robustness"]["n_evaluations"] >= 100


def test_full_pipeline_meets_the_three_criteria():
    """真实语料上的三条判据（与 artifacts/in47_n08/verdict.json 同源）。"""
    v = run_all(write=False)["verdict"]
    c = v["criteria"]
    assert c["cross_model"]["n_cases"] >= 3 and c["cross_model"]["consistency"] >= 0.80
    assert c["cross_model"]["precision_vs_oracle"] >= 0.90
    assert c["external_validity"]["n_holdout_hulls"] >= 3
    assert abs(c["external_validity"]["delta_accuracy_pp"]) <= 5.0
    assert c["scorer_robustness"]["n_cases"] >= 20
    assert c["scorer_robustness"]["n_evals"] == 5
    assert c["scorer_robustness"]["flip_rate"] <= 0.05