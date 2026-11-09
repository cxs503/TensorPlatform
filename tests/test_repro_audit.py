"""P70-N08 复证审计的可计算判据测试（纯函数层 + 编排层常量）。

覆盖设计要点：
* 两类计量口径（observed 相对容差 / diagnostic 绝对容差）各自的边界；
* 结构差异（少算/多算量）必须判 drift，不能被静默通过；
* ULP/量化实现与既有协议 ``tools/repro_digest/ulp.py`` **逐位一致**（对齐而非另起一套）；
* KPI = pass/(pass+drift)、blocked 不进分母、且 |KPI − JSON 重算| ≤ 1e-9；
* 四分类归因按实测证据位走，不猜；
* 探针输出解析对截断/噪声稳健。
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.repro_audit import canonical as C  # noqa: E402
from tools.repro_audit import run_audit as R  # noqa: E402


# ─────────────────────── ULP / 量化：与协议逐位一致 ───────────────────────


def _load_protocol():
    path = os.path.join(ROOT, "tools", "repro_digest", "ulp.py")
    if not os.path.exists(path):
        return None
    try:
        return importlib.load_module if False else __import__(
            "tools.repro_digest.ulp", fromlist=["ulp"])
    except Exception:
        return None


def test_ulp_matches_existing_protocol_on_random_doubles():
    proto = _load_protocol()
    if proto is None:
        pytest.skip("protocol module unavailable")
    rng = random.Random(7008)
    vals = [0.0, -0.0, 1.0, -1.0, 1e-300, 1e300, math.pi, 1e-9, -1e-9, 2.5e-16]
    vals += [rng.uniform(-1e6, 1e6) for _ in range(200)]
    for a, b in zip(vals, vals[1:] + vals[:1]):
        assert C.ulp_distance(a, b) == proto.ulp_distance(a, b)
        assert C.quantize(a) == proto.quantize(a)


def test_ulp_distance_monotone_and_symmetric():
    assert C.ulp_distance(1.0, 1.0) == 0
    assert C.ulp_distance(0.0, 5e-324) == 1          # 最小次正规数相邻
    assert C.ulp_distance(-1.0, 1.0) > C.ulp_distance(-1.0, -0.9999999999999999)
    assert C.ulp_distance(1.0, 2.0) == C.ulp_distance(2.0, 1.0)
    assert C.ulp_distance(float("nan"), float("nan")) == 0


# ─────────────────────── 口径分类 / 判据 ───────────────────────


def test_classify_quantity_splits_near_zero():
    assert C.classify_quantity(0.0) == "diagnostic"
    assert C.classify_quantity(1e-12) == "diagnostic"
    assert C.classify_quantity(-3e-9) == "diagnostic"
    assert C.classify_quantity(1e-3) == "observed"
    assert C.classify_quantity(-24.5) == "observed"


def test_observed_uses_relative_tolerance():
    ok = C.compare_value("dR", 1000.0, 1000.0 + 5e-4)      # 5e-7 相对
    bad = C.compare_value("dR", 1000.0, 1000.0 + 5e-3)     # 5e-6 相对
    assert ok["verdict"] == "pass" and ok["cls"] == "observed"
    assert bad["verdict"] == "drift"
    assert bad["tol"] == {"rel": C.REL_TOL}


def test_diagnostic_uses_absolute_tolerance_even_when_relative_huge():
    # 近零量：相对误差 100% 但绝对差 1e-10 → 仍判 pass（相对口径会误杀）
    it = C.compare_value("residual", 1e-10, 2e-10)
    assert it["cls"] == "diagnostic" and it["verdict"] == "pass"
    # 绝对差 1e-8 > 1e-9 → drift
    it2 = C.compare_value("residual", 1e-10, 1e-8)
    assert it2["verdict"] == "drift" and it2["tol"] == {"abs": C.ABS_TOL}


def test_zero_reference_diagnostic_exact_zero_only():
    assert C.compare_value("x", 0.0, 0.0)["verdict"] == "pass"
    assert C.compare_value("x", 0.0, 1e-12)["verdict"] == "pass"
    assert C.compare_value("x", 0.0, 1e-6)["verdict"] == "drift"


def test_nan_handling():
    assert C.compare_value("x", float("nan"), float("nan"))["verdict"] == "pass"
    assert C.compare_value("x", float("nan"), 1.0)["verdict"] == "drift"
    assert C.compare_value("x", 1.0, float("nan"))["verdict"] == "drift"


# ─────────────────────── 数值表比对 ───────────────────────


def test_flatten_numbers_skips_bool_and_walks_containers():
    flat = C.flatten_numbers({"a": 1, "b": [1.5, True], "c": {"d": {"e": -2.0}}})
    assert flat == {"a": 1.0, "b[0]": 1.5, "c.d.e": -2.0}


def test_compare_number_maps_flags_structural_differences():
    ref = {"a": 1.0, "b": 0.0}
    cur = {"a": 1.0, "c": 3.0}
    cmp = C.compare_number_maps(ref, cur)
    kinds = sorted(s["kind"] for s in cmp["structural"])
    assert kinds == ["added_in_rerun", "missing_in_rerun"]
    assert cmp["counts"]["structural"] == 2
    assert cmp["counts"]["pass"] == 1 and cmp["counts"]["drift"] == 0
    # 结构差异必须算进节点判级 → drift
    assert C.node_verdict(cmp, {"collected": 2, "regressions": 0}) == "drift"


def test_bit_identical_replay_passes_with_zero_ulp():
    ref = {"x": 0.1 + 0.2, "r": 1e-12}
    cur = dict(ref)
    cmp = C.compare_number_maps(ref, cur)
    assert cmp["counts"]["drift"] == 0
    assert cmp["counts"]["max_ulp"] == 0
    assert C.node_verdict(cmp, None) == "pass"


def test_compare_runs_detects_inconsistent_drift():
    a = {"x": 1.0}
    b = {"x": 1.0001}
    out = C.compare_runs(a, b)
    assert out["drift_paths"] == ["x"]


# ─────────────────────── 归因 ───────────────────────


def test_attribution_source_change_requires_evidence():
    item = {"path": "x", "cls": "observed", "ulp": 10 ** 12, "rel": 1e-3}
    lab = C.attribute_drift(item, {"workdir_differs_from_commit": True,
                                   "workdir_replay_matches_ref": True})
    assert lab["class"] == "source_change"
    lab2 = C.attribute_drift(item, {"workdir_differs_from_commit": True,
                                    "workdir_replay_matches_ref": False})
    assert lab2["class"] != "source_change"


def test_attribution_threads_beats_dependency():
    item = {"path": "x", "cls": "observed", "ulp": 10 ** 9, "rel": 1e-4}
    lab = C.attribute_drift(item, {"threads1_matches_ref": True,
                                   "default_threads_matches_ref": False,
                                   "local_same_isa_matches_ref": True,
                                   "numeric_stack_differs": True})
    assert lab["class"] == "environment"


def test_attribution_dependency_version_path():
    item = {"path": "x", "cls": "observed", "ulp": 10 ** 9, "rel": 1e-5}
    lab = C.attribute_drift(item, {"local_same_isa_matches_ref": True,
                                   "numeric_stack_differs": True})
    assert lab["class"] == "dependency_version"


def test_attribution_float_path_only_within_ulp_gate():
    within = C.attribute_drift({"path": "x", "ulp": C.ULP_GATE, "rel": 1e-16}, {})
    beyond = C.attribute_drift({"path": "x", "ulp": C.ULP_GATE * 10, "rel": 1e-16}, {})
    assert within["class"] == "float_path"
    assert beyond["class"] == "environment"
    assert "unresolved" in beyond["evidence"]


def test_attribution_labels_are_from_the_four_classes():
    variants = [
        {"workdir_differs_from_commit": True, "workdir_replay_matches_ref": True},
        {"threads1_matches_ref": True},
        {"local_same_isa_matches_ref": True, "numeric_stack_differs": True},
        {},
    ]
    for ev in variants:
        lab = C.attribute_drift({"path": "x", "ulp": 3, "rel": 0.0}, ev)
        assert lab["class"] in C.ATTRIBUTION_CLASSES


# ─────────────────────── KPI 可加总 ───────────────────────


def _node(status, compared=10, drift=0):
    return {"status": status,
            "numeric": {"counts": {"compared": compared, "pass": compared - drift,
                                   "drift": drift, "structural": 0}},
            "pytest": {"collected": 5, "regressions": 0}}


def test_aggregate_excludes_blocked_from_denominator():
    nodes = [_node("pass"), _node("pass"), _node("drift", drift=2), _node("blocked", compared=0)]
    kpi = C.aggregate(nodes)
    assert kpi["nodes_pass"] == 2 and kpi["nodes_drift"] == 1 and kpi["nodes_blocked"] == 1
    assert kpi["rate_denominator"] == 3
    assert kpi["repro_rate"] == pytest.approx(2 / 3)
    cons = C.kpi_consistency(kpi)
    assert cons["ok"] and cons["delta"] <= 1e-9


def test_aggregate_empty_is_zero_not_nan():
    kpi = C.aggregate([])
    assert kpi["repro_rate"] == 0.0 and kpi["rate_denominator"] == 0
    assert C.kpi_consistency(kpi)["ok"]


def test_kpi_recomputes_from_serialised_json(tmp_path):
    """判据 (b)：落库 KPI 必须与 JSON 逐项可加总一致（|Δ| ≤ 1e-9）。"""
    nodes = [_node("pass") for _ in range(7)] + [_node("drift", drift=1) for _ in range(3)]
    doc = {"nodes": nodes, "kpi": C.aggregate(nodes)}
    p = tmp_path / "audit.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    back = json.loads(p.read_text())
    recomputed = C.aggregate(back["nodes"])
    assert abs(recomputed["repro_rate"] - back["kpi"]["repro_rate"]) <= 1e-9


# ─────────────────────── 节点判级 / 探针解析 ───────────────────────


def test_node_verdict_blocked_only_without_evidence():
    assert C.node_verdict(None, None, "x") == "blocked"
    assert C.node_verdict(None, {"collected": 0, "regressions": 0}) == "blocked"
    assert C.node_verdict(None, {"collected": 3, "regressions": 0}) == "pass"
    assert C.node_verdict(None, {"collected": 3, "regressions": 1}) == "drift"


def test_node_verdict_numeric_drift_dominates():
    cmp = {"counts": {"compared": 4, "pass": 3, "drift": 1, "structural": 0}}
    assert C.node_verdict(cmp, {"collected": 3, "regressions": 0}) == "drift"


def test_parse_probe_output_survives_noise_and_truncation_tail():
    payload = {"rc": 0, "output_tail": "noise\n%s\n%s\n%s" % (
        R.MARK_BEGIN, json.dumps({"mission_id": 1, "runs": []}), R.MARK_END)}
    parsed = R.parse_probe_output(payload)
    assert parsed == {"mission_id": 1, "runs": []}
    assert R.parse_probe_output({"output_tail": "nothing"}) is None
    # 尾部被截断（无结束标记、JSON 不完整）→ 返回 None 而不是半截对象
    assert R.parse_probe_output({"output_tail": R.MARK_BEGIN + '{"a": 1'}) is None


def test_parse_probe_output_stops_at_first_balanced_object():
    body = "%s%s\n{\"junk\": true}%s" % (R.MARK_BEGIN, json.dumps({"a": [1, 2]}), R.MARK_END)
    assert R.parse_probe_output({"output_tail": body}) == {"a": [1, 2]}


def test_probe_cmd_budget_constants():
    from tools.repro_audit import probe_runner as P
    assert P.GEN_TIMEOUT_DEFAULT <= R.NODE_TIMEOUT
    assert P.PYTEST_TIMEOUT_DEFAULT <= R.NODE_TIMEOUT
    assert R.NODE_TIMEOUT == 600          # 判据 (e)：单节点 ≤ 10 min


def test_shims_install_is_idempotent_and_reports_names():
    from tools.repro_audit import shims
    got = shims.install(["matplotlib"])
    assert got == ["matplotlib"]
    import matplotlib
    matplotlib.use("Agg")
    fig, axes = matplotlib.pyplot.subplots(2, 2)
    assert len(axes.ravel()) == 4
    shims.install(["matplotlib"])          # 二次安装不炸
    assert shims.install(["nope"]) == []