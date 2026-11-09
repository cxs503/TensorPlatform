#!/usr/bin/env python3
"""threshold_registry / 派生一致性 / 漂移门 / C1–C7 控制台的单元与集成测试。

纪律
====
* **合成夹具**：本文件里的每条判据都是构造出来的（探针 = 初等函数定积分 / 一阶初值
  问题），不含任何领域词条；引擎的裁决不得依赖任何具体领域内容。
* **反事实**：不只看"对的绿"，更看"错的红"——故意把声明阶数写错、把解析系数写错、
  把阈值写死成实测值，逐条断言引擎真的判红（否则判据不可证伪 = 门不存在）。
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from tools.criteria import gate_report as gr
from tools.criteria import probes as pr
from tools.criteria import threshold_registry as reg

ROOT = Path(__file__).resolve().parents[1]
PROBES_SHA = reg.sha256_file(ROOT / "tools" / "criteria" / "probes.py")


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def synthetic_ref(pointer: str) -> str:
    return f"tools/criteria/probes.py#sha256:{PROBES_SHA}#{pointer}"


def analytic_entry(
    cid: str = "analytic/fixture/bound",
    probe_id: str = "trapezoid_exp",
    declared_order: float | None = None,
    coefficient: float | None = None,
    reference_h: float = 0.125,
    decidable: bool = True,
) -> dict:
    probe = pr.get_probe(probe_id)
    order = probe.order if declared_order is None else declared_order
    coeff = probe.bound_coefficient if coefficient is None else coefficient
    deriv = {"expression": f"C*h**{order:g}", "coefficient": coeff, "reference_h": reference_h}
    return {
        "criterion_id": cid,
        "quantity": f"synthetic error bound of {probe_id}",
        "comparator": "<=",
        "threshold": reg.eval_derivation(deriv, reference_h),
        "source_type": "analytic",
        "source_ref": synthetic_ref(f"probe:{probe_id}"),
        "source_note": "合成夹具：解析误差界",
        "depends_on": [{"param": "h", "exponent": float(order)}],
        "derivation": deriv,
        "probe": probe_id,
        "review_date": "2026-11-09",
        "notes": "synthetic fixture",
        "decidable": decidable,
    }


def heuristic_entry(cid: str, threshold: float = 0.5) -> dict:
    return {
        "criterion_id": cid,
        "quantity": f"synthetic tuned value {cid}",
        "comparator": "<=",
        "threshold": threshold,
        "source_type": "heuristic",
        "source_ref": synthetic_ref(f"synthetic:{cid}"),
        "source_note": "合成夹具：拍脑袋阈值",
        "depends_on": [],
        "derivation": None,
        "probe": None,
        "review_date": "2026-11-09",
        "notes": "synthetic fixture",
        "decidable": False,
    }


def synthetic_registry() -> dict:
    entries = [analytic_entry(f"analytic/fixture/{pid}", probe_id=pid) for pid in sorted(pr.PROBES)]
    entries += [heuristic_entry(f"fixture/heuristic/{i:02d}", 0.1 + 0.01 * i) for i in range(26)]
    return {"schema_version": reg.SCHEMA_VERSION, "purpose": "synthetic", "criteria": entries}


def write_registry(path: Path, doc: dict) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 1. 探针自身：已知阶必须真被"测"出来
# --------------------------------------------------------------------------- #
def test_probes_measured_order_matches_declared():
    for pid, probe in pr.PROBES.items():
        hs, es = probe.error_series(0.25, 6)
        fit = reg.estimate_order(hs, es)
        assert abs(fit["order"] - probe.order) <= reg.ORDER_TOL, (pid, fit)
        assert fit["r2"] > 0.99, (pid, fit)
        # 误差必须远离双精度舍入地板，否则"实测指数"拟合的是舍入残差
        assert min(es) > 1e-13, (pid, min(es))


def test_probe_errors_decrease_monotonically():
    for pid, probe in pr.PROBES.items():
        hs, es = probe.error_series(0.25, 5)
        assert all(a > b for a, b in zip(es, es[1:])), pid


# --------------------------------------------------------------------------- #
# 2. 结构校验
# --------------------------------------------------------------------------- #
def test_validate_accepts_synthetic_registry(tmp_path):
    doc = synthetic_registry()
    assert len(doc["criteria"]) >= 30
    res = reg.validate(doc)
    assert res["ok"], res["errors"]


@pytest.mark.parametrize(
    "mutate,expect_fragment",
    [
        (lambda e: e.pop("source_ref"), "source_ref"),
        (lambda e: e.__setitem__("source_type", "vibes"), "vibes"),
        (lambda e: e.__setitem__("comparator", "≈"), "≈"),
        (lambda e: e.__setitem__("decidable", True), "decidable"),
    ],
)
def test_validate_rejects_bad_entries(tmp_path, mutate, expect_fragment):
    doc = synthetic_registry()
    doc["criteria"].append(heuristic_entry("fixture/bad"))
    target = doc["criteria"][-1]
    mutate(target)
    res = reg.validate(doc)
    assert not res["ok"]
    assert any(expect_fragment in e for e in res["errors"]), res["errors"]


@pytest.mark.parametrize(
    "mutate,expect_fragment",
    [
        (lambda e: e.__setitem__("threshold", e["threshold"] * 1.5), "与表达式在"),
        (
            lambda e: e["depends_on"].__setitem__(0, {"param": "h", "exponent": 3.0}),
            "表达式 h 幂次",
        ),
        (lambda e: e["depends_on"].clear(), "depends_on 无 h 依赖"),
        (lambda e: e["derivation"].__setitem__("expression", "C*x**2"), "未在 depends_on 声明"),
        (lambda e: e["derivation"].__setitem__("reference_h", 0.0), "reference_h"),
        (lambda e: e.__setitem__("decidable", False), "decidable"),
    ],
)
def test_validate_rejects_inconsistent_analytic_entry(mutate, expect_fragment):
    doc = synthetic_registry()
    target = doc["criteria"][0]
    mutate(target)
    res = reg.validate(doc)
    assert not res["ok"]
    assert any(expect_fragment in e for e in res["errors"]), res["errors"]


def test_validate_rejects_duplicate_ids():
    doc = synthetic_registry()
    doc["criteria"].append(copy.deepcopy(doc["criteria"][0]))
    res = reg.validate(doc)
    assert not res["ok"]
    assert any("重复" in e for e in res["errors"])


# --------------------------------------------------------------------------- #
# 3. JSONC 读取 + 语义指纹（注释/格式不敏感）
# --------------------------------------------------------------------------- #
def test_jsonc_parses_comments_and_trailing_commas(tmp_path):
    doc = synthetic_registry()
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    text = "// 头注释\n" + text.replace('\n}', ',\n}', 1)
    path = tmp_path / "registry.jsonc"
    path.write_text(text, encoding="utf-8")
    parsed = reg.load_registry(path)
    assert len(parsed["criteria"]) == len(doc["criteria"])


def test_strip_jsonc_keeps_string_content():
    text = '{"a": "http://x//y", "b": 1 // c\n}'
    assert json.loads(reg.strip_jsonc(text)) == {"a": "http://x//y", "b": 1}


def test_semantic_hash_ignores_comments_and_notation():
    entry = analytic_entry()
    base = reg.semantic_sha(entry)
    noised = dict(entry)
    noised["notes"] = "另一段说明"
    noised["_comment"] = "格式备注"
    noised["threshold"] = float(f"{entry['threshold']:.15g}")  # 同一浮点的另一种写法
    assert reg.semantic_sha(noised) == base


def test_semantic_hash_sees_threshold_change():
    entry = analytic_entry()
    changed = copy.deepcopy(entry)
    changed["threshold"] = float(entry["threshold"]) * 1.1
    assert reg.semantic_sha(changed) != reg.semantic_sha(entry)


def test_registry_sha_order_insensitive():
    doc = synthetic_registry()
    reordered = copy.deepcopy(doc)
    reordered["criteria"] = list(reversed(reordered["criteria"]))
    assert reg.registry_sha(doc) == reg.registry_sha(reordered)


# --------------------------------------------------------------------------- #
# 4. 漂移门（C3 检出 / C4 假阳性 / 来源腐烂）
# --------------------------------------------------------------------------- #
def test_drift_red_on_threshold_change_without_source_change():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    mutated = copy.deepcopy(doc)
    mutated["criteria"][0]["threshold"] = float(mutated["criteria"][0]["threshold"]) * 1.1
    res = reg.drift_check(lock, mutated)
    assert res["verdict"] == "RED"
    assert any(
        f["code"] == "threshold_drift_without_source_change"
        and f["criterion_id"] == mutated["criteria"][0]["criterion_id"]
        for f in res["findings"]
    )


def test_drift_red_on_decidable_label_flip():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    mutated = copy.deepcopy(doc)
    entry = mutated["criteria"][-1]  # heuristic 判据
    entry["decidable"] = True
    res = reg.drift_check(lock, mutated)
    assert res["verdict"] == "RED"
    assert any(f["code"] == "threshold_drift_without_source_change" for f in res["findings"])


def test_drift_green_after_retrace():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    retraced = copy.deepcopy(doc)
    entry = retraced["criteria"][-1]
    entry["threshold"] = 0.99
    entry["source_ref"] = synthetic_ref("synthetic:retraced")
    entry["review_date"] = "2026-12-01"
    res = reg.drift_check(lock, retraced)
    assert res["verdict"] == "CLEAR", res["findings"]


def test_drift_warn_when_retraced_without_review_date_bump():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    retraced = copy.deepcopy(doc)
    entry = retraced["criteria"][-1]
    entry["threshold"] = 0.99
    entry["source_ref"] = synthetic_ref("synthetic:retraced")
    res = reg.drift_check(lock, retraced)
    assert res["verdict"] == "CLEAR"
    assert any(f["code"] == "provenance_updated_without_review_date_bump" for f in res["findings"])


def test_drift_green_on_formatting_only():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    noised = copy.deepcopy(doc)
    for entry in noised["criteria"]:
        entry["notes"] = "仅文字改动"
    assert reg.drift_check(lock, noised)["verdict"] == "CLEAR"


def test_drift_yellow_on_removed_criterion():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    trimmed = copy.deepcopy(doc)
    trimmed["criteria"].pop()
    res = reg.drift_check(lock, trimmed)
    assert res["verdict"] == "CLEAR"
    assert any(f["code"] == "removed" for f in res["findings"])


def test_drift_red_on_missing_source_ref():
    doc = synthetic_registry()
    lock = reg.build_lock(doc)
    mutated = copy.deepcopy(doc)
    mutated["criteria"][-1]["source_ref"] = ""
    res = reg.drift_check(lock, mutated)
    assert res["verdict"] == "RED"
    assert any(f["code"] == "missing_source_ref" for f in res["findings"])


def test_source_ref_rot_detected_on_file_byte_change(tmp_path):
    target = tmp_path / "root" / "tools" / "criteria" / "probes.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    ref = f"tools/criteria/probes.py#sha256:{reg.sha256_file(target)}#synthetic:rot"
    assert reg.source_ref_status(ref, tmp_path / "root")["status"] == "OK"
    target.write_text("x = 1  # drift\n", encoding="utf-8")
    assert reg.source_ref_status(ref, tmp_path / "root")["status"] == "SHA_DRIFT"
    assert reg.source_ref_status("tools/criteria/probes.py", tmp_path / "root")["status"] == "MALFORMED"
    assert (
        reg.source_ref_status("no/such/file.py#sha256:deadbeef#p", tmp_path / "root")["status"]
        == "MISSING_FILE"
    )


class _RootPatch:
    """把 drift_check 的 root 指到临时树（合成夹具的出处都在临时树里）。"""


def test_drift_red_on_source_ref_rot(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "tools").mkdir(parents=True)
    src = root / "tools" / "fixture.json"
    src.write_text("{\n}\n", encoding="utf-8")
    doc = synthetic_registry()
    for i, entry in enumerate(doc["criteria"]):
        entry["source_ref"] = f"tools/fixture.json#sha256:{reg.sha256_file(src)}#synthetic:{i}"
    lock = reg.build_lock(doc)
    src.write_text("{\n}\n\n", encoding="utf-8")  # 被引文件改了，引用没更新
    res = reg.drift_check(lock, doc, root=root)
    assert res["verdict"] == "RED"
    assert any(f["code"] == "source_ref_rot" for f in res["findings"])


# --------------------------------------------------------------------------- #
# 5. 可裁/不可裁判定（C1 剔除 + C7 负控）
# --------------------------------------------------------------------------- #
def test_classify_heuristic_and_empirical_are_undecidable():
    for st in ("heuristic", "empirical"):
        entry = heuristic_entry("fixture/x")
        entry["source_type"] = st
        verdict = reg.classify_entry(entry)
        assert verdict["decidable"] is False
        assert "不可裁" in verdict["reason"]


def test_classify_analytic_with_ref_is_decidable():
    verdict = reg.classify_entry(analytic_entry())
    assert verdict["decidable"] is True


def test_classify_missing_ref_is_undecidable():
    entry = analytic_entry()
    entry["source_ref"] = ""
    assert reg.classify_entry(entry)["decidable"] is False


def test_tautology_forced_empirical_and_excluded():
    probe = pr.get_probe("trapezoid_exp")
    entry = heuristic_entry("fixture/tautology")
    entry["source_type"] = "analytic"  # 谎称有解析出处
    entry["probe"] = "trapezoid_exp"
    entry["threshold"] = probe.error(reg.DEFAULT_MEASURE_H)
    entry["decidable"] = True  # 谎称可裁
    verdict = reg.classify_entry(entry, probe.error(reg.DEFAULT_MEASURE_H))
    assert verdict["effective_source_type"] == "empirical"
    assert verdict["decidable"] is False
    assert verdict["forced"] is True
    doc = synthetic_registry()
    doc["criteria"].append(entry)
    assert "fixture/tautology" not in reg.decidable_ids(doc)
    # 结构校验必须抓到"可裁标注说谎"
    assert not reg.validate(doc)["ok"]


def test_analytic_entries_are_not_tautological():
    doc = synthetic_registry()
    measured = reg.measured_map(doc)
    for entry in doc["criteria"]:
        if entry["source_type"] != "analytic":
            continue
        verdict = reg.classify_entry(entry, measured[entry["criterion_id"]])
        assert verdict["decidable"] is True
        assert verdict["forced"] is False


# --------------------------------------------------------------------------- #
# 6. 派生一致性（C2）——两个方向都要咬
# --------------------------------------------------------------------------- #
def test_derivation_check_passes_on_true_orders():
    doc = synthetic_registry()
    res = reg.derivation_check(doc)
    assert res["ok"], res["rows"]
    assert res["n_checked"] == len(pr.PROBES)


def test_derivation_check_fails_on_wrong_declared_order():
    doc = synthetic_registry()
    doc["criteria"].append(
        analytic_entry("analytic/fixture/wrong", probe_id="simpson_exp", declared_order=2.0)
    )
    res = reg.derivation_check(doc)
    assert not res["ok"]
    bad = [r for r in res["rows"] if r["status"] == "FAIL"]
    assert [r["criterion_id"] for r in bad] == ["analytic/fixture/wrong"]
    assert bad[0]["deviation"] > reg.ORDER_TOL


def test_derivation_check_flags_unprobed_analytic():
    doc = synthetic_registry()
    entry = analytic_entry("analytic/fixture/noprobe")
    entry["probe"] = None
    doc["criteria"].append(entry)
    res = reg.derivation_check(doc)
    assert not res["ok"]
    assert res["n_unchecked"] == 1


def test_unknown_probe_is_reported_not_crashing():
    doc = synthetic_registry()
    entry = analytic_entry("analytic/fixture/unknown")
    entry["probe"] = "no_such_probe"
    doc["criteria"].append(entry)
    res = reg.derivation_check(doc)
    assert not res["ok"]
    assert any(r["status"] == "UNCHECKED_UNKNOWN_PROBE" for r in res["rows"])
    closure = reg.rederivation_closure(doc)
    assert not closure["ok"]
    assert any(r["status"] == "UNKNOWN_PROBE" for r in closure["rows"])


# --------------------------------------------------------------------------- #
# 7. 重推导闭环（C5）
# --------------------------------------------------------------------------- #
def test_rederivation_closure_band_bites():
    doc = synthetic_registry()
    # 系数放大 5 倍 → 预测/实测 ≈ 7.9，必须越界
    doc["criteria"].append(
        analytic_entry(
            "analytic/fixture/scaled",
            probe_id="trapezoid_exp",
            coefficient=pr.get_probe("trapezoid_exp").bound_coefficient * 5.0,
        )
    )
    res = reg.rederivation_closure(doc)
    assert not res["ok"]
    bad = [r for r in res["rows"] if r["status"] == "FAIL"]
    assert [r["criterion_id"] for r in bad] == ["analytic/fixture/scaled"]
    assert all(pt["ratio"] > reg.RATIO_BAND[1] for pt in bad[0]["points"])


def test_rederivation_points_are_two_levels_and_same_direction():
    doc = synthetic_registry()
    res = reg.rederivation_closure(doc)
    for row in res["rows"]:
        assert len(row["points"]) == 2
        assert all(pt["in_band"] and pt["same_direction"] for pt in row["points"])
        assert row["points"][1]["h"] * 2 == row["points"][0]["h"]


# --------------------------------------------------------------------------- #
# 8. 覆盖率账本（C6）
# --------------------------------------------------------------------------- #
def test_coverage_ledger_numbers(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "tools").mkdir(parents=True)
    src = root / "tools" / "fixture.json"
    src.write_text("{}", encoding="utf-8")
    doc = synthetic_registry()
    for i, entry in enumerate(doc["criteria"]):
        entry["source_ref"] = f"tools/fixture.json#sha256:{reg.sha256_file(src)}#synthetic:{i}"
    ledger = reg.coverage_ledger(doc, root=root)
    assert ledger["n_total"] == len(doc["criteria"])
    assert ledger["sourced_ratio"] == 1.0
    assert ledger["n_derivable"] == len(pr.PROBES)
    assert ledger["n_decidable"] == len(pr.PROBES)
    assert 0 < ledger["derivable_ratio"] < 1
    # 被引文件被改 → 有来源占比立刻掉下来（不是恒为 1 的空指标）
    src.write_text("{} ", encoding="utf-8")
    dropped = reg.coverage_ledger(doc, root=root)
    assert dropped["sourced_ratio"] == 0.0
    assert dropped["by_source_ref_status"] == {"SHA_DRIFT": len(doc["criteria"])}


# --------------------------------------------------------------------------- #
# 9. 表达式求值与阶数抽取（安全边界）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "expr,expected",
    [
        ("C*h**2", 2.0),
        ("C*h*h", 2.0),
        ("C*h", 1.0),
        ("C", 0.0),
        ("C*h**0.5", 0.5),
    ],
)
def test_expression_order(expr, expected):
    assert reg.expression_order(expr) == expected


@pytest.mark.parametrize("expr", ["C*h**2 + __import__('os').system('true')", "C*x**2", "h**2+"])
def test_eval_derivation_rejects_illegal_expressions(expr):
    with pytest.raises(Exception):
        reg.eval_derivation({"expression": expr, "coefficient": 1.0, "reference_h": 0.1}, 0.1)


# --------------------------------------------------------------------------- #
# 10. C1–C7 控制台：合成注册表也能全绿（证明引擎不含领域内容）
# --------------------------------------------------------------------------- #
def _synthetic_controls(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "tools").mkdir(parents=True)
    src = root / "tools" / "fixture.json"
    src.write_text("{}", encoding="utf-8")
    doc = synthetic_registry()
    for i, entry in enumerate(doc["criteria"]):
        entry["source_ref"] = f"tools/fixture.json#sha256:{reg.sha256_file(src)}#synthetic:{i}"
    path = write_registry(tmp_path / "registry.json", doc)
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(reg.build_lock(doc), ensure_ascii=False), encoding="utf-8")
    return root, path, lock_path, doc


def test_controls_all_green_on_synthetic_registry(tmp_path, monkeypatch):
    root, path, lock_path, doc = _synthetic_controls(tmp_path, monkeypatch)
    monkeypatch.setattr(reg, "ROOT", root)
    report = gr.run(path, lock_path)
    assert report["verdict"] == "CLEAR", {
        k: report[k]["statement"] for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C7")
    }
    assert report["C1"]["n_criteria"] >= 30
    assert report["C3"]["n_detected"] == report["C3"]["n_injections"]
    assert report["C4"]["n_false_positive"] == 0
    assert report["C7"]["ok"] is True


def test_controls_detect_tampered_registry(tmp_path, monkeypatch):
    root, path, lock_path, doc = _synthetic_controls(tmp_path, monkeypatch)
    monkeypatch.setattr(reg, "ROOT", root)
    tampered = copy.deepcopy(doc)
    tampered["criteria"][0]["threshold"] = float(tampered["criteria"][0]["threshold"]) * 1.1
    write_registry(path, tampered)
    res = reg.drift_check(json.loads(lock_path.read_text(encoding="utf-8")), tampered, root=root)
    assert res["verdict"] == "RED"


# --------------------------------------------------------------------------- #
# 11. 现网注册表集成（真文件；缺失则跳过）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not reg.DEFAULT_REGISTRY.is_file(), reason="现网注册表未入册")
def test_live_registry_gate_is_green():
    registry = reg.load_registry(reg.DEFAULT_REGISTRY)
    assert len(registry["criteria"]) >= 30
    assert reg.validate(registry)["ok"]
    lock = json.loads(reg.DEFAULT_LOCK.read_text(encoding="utf-8"))
    drift = reg.drift_check(lock, registry)
    assert drift["verdict"] == "CLEAR", drift["findings"][:5]
    derive = reg.derivation_check(registry)
    assert derive["ok"], derive["rows"]
    closure = reg.rederivation_closure(registry)
    assert closure["ok"] and closure["n_checked"] >= 3
    ledger = reg.coverage_ledger(registry)
    assert ledger["sourced_ratio"] == 1.0
    assert ledger["n_heuristic_excluded"] >= 1


@pytest.mark.skipif(not reg.DEFAULT_REGISTRY.is_file(), reason="现网注册表未入册")
def test_cli_exit_codes(tmp_path):
    assert reg.main(["validate", "--registry", str(reg.DEFAULT_REGISTRY)]) == 0
    assert (
        reg.main(
            [
                "gate",
                "--registry",
                str(reg.DEFAULT_REGISTRY),
                "--lock",
                str(reg.DEFAULT_LOCK),
                "--out",
                str(tmp_path / "gate.json"),
            ]
        )
        == 0
    )
    assert (
        reg.main(
            [
                "drift",
                "--registry",
                str(reg.DEFAULT_REGISTRY),
                "--lock",
                str(tmp_path / "missing.json"),
                "--out",
                str(tmp_path / "x.json"),
            ]
        )
        == 2
    )