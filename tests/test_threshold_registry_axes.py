#!/usr/bin/env python3
"""依赖轴泛化的验收用例（P493-N05）：阈值 = dependency(h, p, N, ε) 的函数。

守的是什么
----------
旧引擎把"实测衰减指数"这件事写死在 h 轴上：``derivation_check`` 里
``if "h" not in deps: continue``。于是任何声明了 N（统计样本数）或 ε（相对容差）
依赖的判据既不进 PASS 也不进 FAIL —— 门照旧判绿，而门后面什么都没有。

本文件的用例把三件事钉死：

1. **轴反演能力**：N 轴（CLM，指数 -1/2）与 ε 轴（容差=步长，指数 +1 / +2）上，
   引擎从实测序列反演出的指数必须落在声明指数 ±0.3 内，且重推导闭环比值在 [0.5,2]。
2. **负控必须红**：把声明指数写错、把 h 探针接到 N 判据上、声明了依赖量却不给探针，
   三种情形都必须**阻塞**（``ok=False``），一条都不许静默跳过。
3. **零领域语义**：本包源码/契约/注册表工件里不出现域词条（词表从
   ``config/domain_word_denylist.json`` 以数据注入，测试自己也不硬编码域词）。

所有"实测"都来自纯数学合成对象：U(0,1) 样本均值的 CLT 收敛、f(x)=e^x 的差分截断误差。
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(ROOT))

from tools.criteria import gate_report as gr  # noqa: E402
from tools.criteria import harvest  # noqa: E402
from tools.criteria import probes_axes as pa  # noqa: E402
from tools.criteria import threshold_registry as reg  # noqa: E402


# --------------------------------------------------------------------------- #
# 合成判据构造器（零领域：只有量名、比较符、比较值与轴）
# --------------------------------------------------------------------------- #
def make_entry(probe_id: str, axis: str, exponent: float, *, probe: str | None = None, **over) -> dict:
    """按轴探针造一条合成判据（threshold 由表达式程序化算，零手抄）。"""
    p = pa.get_axis_probe(probe_id)
    deriv = {
        "expression": f"C*{axis}**({exponent})",
        "coefficient": p.bound_coefficient,
        "reference_value": p.reference_value,
        "axis": axis,
    }
    entry = {
        "criterion_id": f"analytic/{probe_id}/bound",
        "quantity": f"error bound of {probe_id}",
        "comparator": "<=",
        "threshold": reg.eval_derivation(deriv, p.reference_value, axis),
        "source_type": "analytic",
        "source_ref": f"tools/criteria/probes_axes.py#sha256:{'0' * 64}#probe:{probe_id}",
        "depends_on": [{"param": axis, "exponent": exponent}],
        "derivation": deriv,
        "probe": probe if probe is not None else probe_id,
        "review_date": "2026-11-10",
        "decidable": True,
    }
    entry.update(over)
    return entry


def make_registry(entries) -> dict:
    return {"schema_version": reg.SCHEMA_VERSION, "criteria": list(entries)}


# --------------------------------------------------------------------------- #
# 1. 轴探针的幂律本身（引擎的"实测"来源）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("probe_id", sorted(pa.AXIS_PROBES))
def test_axis_probe_recovers_its_own_exponent(probe_id):
    probe = pa.get_axis_probe(probe_id)
    values, errors = probe.series(6)
    fit = reg.estimate_order(values, errors)
    assert abs(fit["order"] - probe.exponent) <= reg.ORDER_TOL, (probe_id, fit, probe.exponent)
    assert fit["r2"] > 0.95, (probe_id, fit["r2"])
    # 先验界与实测误差同量级（比值落在重推导闭环允许带内）
    for v, e in zip(values, errors):
        lo, hi = reg.RATIO_BAND
        assert lo <= probe.bound(v) / e <= hi, (probe_id, v, probe.bound(v) / e)


def test_axis_series_direction_follows_exponent_sign():
    # 指数为负（N 轴）：放大变量才是细化
    inc = pa.refine_series(1024.0, -0.5, 3)
    assert inc == sorted(inc) and inc[0] == 1024.0
    # 指数为正（h / eps 轴）：缩小变量才是细化
    dec = pa.refine_series(1e-2, 1.0, 3)
    assert dec == sorted(dec, reverse=True) and dec[0] == pytest.approx(1e-2)


def test_axis_probes_are_deterministic():
    """蒙特卡洛探针必须确定性：否则门会自己抖成假红/假绿。"""
    a = pa.get_axis_probe("monte_carlo_mean").series(4)
    b = pa.get_axis_probe("monte_carlo_mean").series(4)
    assert a == b


# --------------------------------------------------------------------------- #
# 2. 派生一致性引擎：非 h 轴必须被真测（不再静默跳过）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "probe_id,axis,exponent",
    [
        ("monte_carlo_mean", "N", -0.5),
        ("tol_forward_difference", "eps", 1.0),
        ("tol_central_difference", "eps", 2.0),
    ],
)
def test_derivation_check_covers_non_h_axes(probe_id, axis, exponent):
    registry = make_registry([make_entry(probe_id, axis, exponent)])
    res = reg.derivation_check(registry)
    assert res["ok"], res
    assert res["n_checked"] == 1 and res["n_unchecked"] == 0
    row = res["rows"][0]
    assert row["axis"] == axis
    assert row["status"] == "PASS"
    assert row["deviation"] <= reg.ORDER_TOL
    assert res["by_axis"][axis]["checked"] == 1


def test_derivation_check_does_not_silently_skip_dependency_axis():
    """核心回归：声明了 N 依赖却没有可测探针 —— 必须阻塞，不许静默通过。

    这是旧实现的实际行为（``if "h" not in deps: continue``）：判据不进 PASS 也不进
    FAIL，``ok`` 仍为 True。修复后必须变 ``UNCHECKED_NO_PROBE`` 且 ``ok=False``。
    """
    entry = make_entry("monte_carlo_mean", "N", -0.5)
    entry.pop("probe")
    entry["derivation"] = None
    res = reg.derivation_check(make_registry([entry]))
    assert res["ok"] is False
    assert res["n_checked"] == 0 and res["n_unchecked"] == 1
    assert res["rows"][0]["status"] == "UNCHECKED_NO_PROBE"
    assert "UNCHECKED_NO_PROBE" in res["unchecked_reasons"]


def test_derivation_check_blocks_unknown_probe():
    entry = make_entry("monte_carlo_mean", "N", -0.5, probe="no_such_probe")
    res = reg.derivation_check(make_registry([entry]))
    assert res["ok"] is False
    assert res["rows"][0]["status"] == "UNCHECKED_UNKNOWN_PROBE"


def test_derivation_check_blocks_axis_mismatch():
    """拿 h 轴探针去"验证" N 判据 = 张冠李戴，必须阻塞。"""
    entry = make_entry("monte_carlo_mean", "N", -0.5, probe="trapezoid_exp")
    res = reg.derivation_check(make_registry([entry]))
    assert res["ok"] is False
    assert res["rows"][0]["status"] == "UNCHECKED_AXIS_MISMATCH"


def test_derivation_check_blocks_undeclared_axis():
    """表达式轴未在 depends_on 声明 → 阻塞（轴不能凭空出现）。"""
    entry = make_entry("monte_carlo_mean", "N", -0.5)
    entry["depends_on"] = [{"param": "h", "exponent": 2.0}]
    res = reg.derivation_check(make_registry([entry]))
    assert res["ok"] is False
    assert res["rows"][0]["status"] == "UNCHECKED_AXIS_UNDECLARED"


# --------------------------------------------------------------------------- #
# 3. 负控：声明指数写错必须判红（引擎不是"照着声明拟合"）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "probe_id,axis,wrong_exponent",
    [
        ("monte_carlo_mean", "N", -0.1),  # 中心极限定理是 -1/2，不是 -1/10
        ("monte_carlo_mean", "N", -1.0),  # 也不许当成 1/N
        ("tol_central_difference", "eps", 1.0),  # 中心差分是 ε²，不是 ε¹
        ("tol_forward_difference", "eps", 2.0),  # 前向差分是 ε¹，不是 ε²
    ],
)
def test_wrong_declared_exponent_fails(probe_id, axis, wrong_exponent):
    registry = make_registry([make_entry(probe_id, axis, wrong_exponent)])
    res = reg.derivation_check(registry)
    assert res["ok"] is False
    row = res["rows"][0]
    assert row["status"] == "FAIL"
    assert row["deviation"] > reg.ORDER_TOL


# --------------------------------------------------------------------------- #
# 4. 重推导闭环：非 h 轴上"预测 vs 实测"也要对得上
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "probe_id,axis,exponent",
    [
        ("monte_carlo_mean", "N", -0.5),
        ("tol_forward_difference", "eps", 1.0),
        ("tol_central_difference", "eps", 2.0),
    ],
)
def test_rederivation_closure_on_non_h_axis(probe_id, axis, exponent):
    registry = make_registry([make_entry(probe_id, axis, exponent)])
    res = reg.rederivation_closure(registry, levels=2)
    assert res["ok"], res
    assert res["axes"] == [axis]
    points = res["rows"][0]["points"]
    assert len(points) == 2
    for pt in points:
        assert axis in pt
        assert pt["in_band"] and pt["same_direction"]


def test_rederivation_closure_flags_wrong_sign_declaration():
    """声明指数符号写反（N 轴写成 +1/2）：预测与实测反向 → 必须判红。"""
    registry = make_registry([make_entry("monte_carlo_mean", "N", 0.5)])
    res = reg.rederivation_closure(registry, levels=2)
    assert res["ok"] is False
    assert res["rows"][0]["status"] == "FAIL"


# --------------------------------------------------------------------------- #
# 5. 契约层：schema/validate 认可新轴，且拒绝自相矛盾的记录
# --------------------------------------------------------------------------- #
def test_validate_accepts_axis_entry_with_negative_exponent():
    registry = make_registry(
        [
            make_entry("monte_carlo_mean", "N", -0.5),
            make_entry("tol_forward_difference", "eps", 1.0),
        ]
    )
    res = reg.validate(registry)
    assert res["ok"], res["errors"]


def test_validate_rejects_expression_variable_not_declared():
    entry = make_entry("monte_carlo_mean", "N", -0.5)
    entry["derivation"]["expression"] = "C*Z**(-0.5)"
    res = reg.validate(make_registry([entry]))
    assert not res["ok"]
    assert any("未在 depends_on 声明" in e for e in res["errors"])


def test_validate_rejects_exponent_mismatch_on_axis():
    entry = make_entry("monte_carlo_mean", "N", -0.5)
    entry["depends_on"] = [{"param": "N", "exponent": -0.25}]  # 与表达式 -0.5 冲突
    res = reg.validate(make_registry([entry]))
    assert not res["ok"]
    assert any("幂次" in e for e in res["errors"])


def test_validate_rejects_threshold_not_matching_expression_at_reference():
    entry = make_entry("tol_central_difference", "eps", 2.0)
    entry["threshold"] = entry["threshold"] * 1.5
    res = reg.validate(make_registry([entry]))
    assert not res["ok"]
    assert any("预测" in e or "不符" in e for e in res["errors"])


def test_expression_order_is_signed_and_handles_division():
    # 旧实现把 C/h**2 也读成 +2（符号错误），这里钉死修正后的口径
    assert reg.expression_order("C/h**2", "h") == -2.0
    assert reg.expression_order("C*N**(-0.5)", "N") == -0.5
    assert reg.expression_order("C*eps**(2)", "eps") == 2.0
    assert reg.expression_order("C*h*h", "h") == 2.0
    assert reg.expression_order("C*x**2", "h") is None  # 变量名对不上 → 判不了


def test_eval_derivation_rejects_non_whitelisted_variable_even_without_axis():
    with pytest.raises(ValueError):
        reg.eval_derivation({"expression": "C*z**2", "coefficient": 1.0}, 0.5)


# --------------------------------------------------------------------------- #
# 6. 入册与 CI 钉桩：轴判据真的进了现网注册表
# --------------------------------------------------------------------------- #
def test_committed_registry_contains_all_axis_families():
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    axes = reg.axis_coverage(registry)
    for axis in ("h", "N", "eps"):
        assert axes["declared_by_axis"].get(axis, 0) >= 1, axes
        assert axes["derived_by_axis"].get(axis, 0) >= 1, axes
        assert axes["checked_by_axis"][axis]["checked"] >= 1, axes
    assert axes["n_unchecked"] == 0, axes
    assert axes["ok"] is True


def test_committed_lock_has_no_drift():
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    lock = json.loads(pathlib.Path(reg.DEFAULT_LOCK).read_text(encoding="utf-8"))
    res = reg.drift_check(lock, registry)
    assert res["verdict"] == "CLEAR", [f for f in res["findings"] if f["level"] == "RED"]


def test_committed_registry_reproduces_from_harvest():
    """注册表必须是入册器的确定性产物（否则"人工塞进去的判据"无处审计）。"""
    current = reg.load_registry(reg.DEFAULT_REGISTRY)
    fresh = harvest.build_registry(reg.ROOT, current["harvest"]["generated_on"])
    assert reg.registry_sha(fresh) == reg.registry_sha(current)


def test_gate_report_reports_axis_ledger():
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    lock = json.loads(pathlib.Path(reg.DEFAULT_LOCK).read_text(encoding="utf-8"))
    report = gr.run(reg.DEFAULT_REGISTRY, reg.DEFAULT_LOCK)
    assert report["verdict"] == "CLEAR", {k: report[k]["statement"] for k in report if k.startswith("C")}
    assert report["C2"]["n_checked"] == report["C2"]["n_analytic"]
    assert report["C2"]["n_unchecked"] == 0
    assert len(report["C5"]["axes"]) >= 2
    assert set(registry["criteria"][0])  # 注册表可读
    assert lock["n_criteria"] == len(registry["criteria"])


# --------------------------------------------------------------------------- #
# 7. 零领域语义（c）
# --------------------------------------------------------------------------- #
def test_no_domain_vocabulary_in_criteria_package():
    """域词表以数据注入（config/domain_word_denylist.json），源码/契约/工件必须干净。"""
    patterns = harvest.load_domain_patterns()
    if not patterns:  # 词表缺失时降级为"不判"，绝不因为缺词表就把门判绿
        pytest.skip("域词表缺失，零领域扫描降级")
    targets = [
        ROOT / "tools" / "criteria" / "probes_axes.py",
        ROOT / "tools" / "criteria" / "threshold_registry.py",
        ROOT / "tools" / "criteria" / "harvest.py",
        ROOT / "schemas" / "threshold_registry.schema.json",
        ROOT / "data" / "criteria" / "threshold_registry.json",
        pathlib.Path(__file__),
    ]
    hits = {}
    for path in targets:
        text = path.read_text(encoding="utf-8")
        found = sorted({m.group(0) for p in patterns for m in [p.search(text)] if m})
        if found:
            hits[path.name] = found
    assert hits == {}, hits


def test_registry_criteria_have_no_domain_words():
    """注册表条目（含 quantity/notes）也不许出现域词；域语义只能进 source_ref。"""
    patterns = harvest.load_domain_patterns()
    if not patterns:
        pytest.skip("域词表缺失，零领域扫描降级")
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    bad = []
    for entry in registry["criteria"]:
        probe_view = {
            k: v
            for k, v in entry.items()
            if k in ("criterion_id", "quantity", "notes", "source_note", "comparator")
        }
        text = json.dumps(probe_view, ensure_ascii=False)
        found = sorted({m.group(0) for p in patterns for m in [p.search(text)] if m})
        if found:
            bad.append((entry["criterion_id"], found))
    assert bad == [], bad[:5]


# --------------------------------------------------------------------------- #
# 8. 漂移门对"改了指数却没改出处"同样生效
# --------------------------------------------------------------------------- #
def test_drift_gate_red_when_axis_exponent_changes_without_source():
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    lock = reg.build_lock(registry)
    mutated = copy.deepcopy(registry)
    target = next(e for e in mutated["criteria"] if e.get("derivation") and e["derivation"].get("axis") == "N")
    target["depends_on"] = [{"param": "N", "exponent": -0.25}]
    res = reg.drift_check(lock, mutated)
    assert res["verdict"] == "RED"
    reds = [f for f in res["findings"] if f["level"] == "RED" and f["criterion_id"] == target["criterion_id"]]
    assert reds, res["findings"]


def test_drift_gate_clear_when_axis_entry_untouched():
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    lock = reg.build_lock(registry)
    res = reg.drift_check(lock, copy.deepcopy(registry))
    assert res["verdict"] == "CLEAR", res["findings"]