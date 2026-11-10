"""P167W2-N08 平台自主基准测试引擎 —— 单测/集成测试。

覆盖四件套 + 判据门的**判别力**与**特异性**（这是本单验收的核心，不是"跑绿就算"）：

1. 配置校验：未知算子 / 重复 id / 空 cases / 缺 suite_criteria → ConfigError；YAML 可读；
2. 判据门 fail-closed：无判据 / 缺指标 / NaN / 单调性破缺 → FAIL；算子合法路径 → PASS；
3. runner：3 个正示例 suite 共 13 算例全通过；
4. **负对照**：2 个恶意算例必须 FAIL（错误码 1）——门空转就会被这条抓住；
5. 工件：JSON + PNG + manifest，sha256 可重算一致，provenance 无凭据泄漏；
6. 回归检测：+2% 不告警（特异性）、+5% warn、+15% critical、pass→fail 翻转 critical、
   基线缺失 status=no_baseline（不冒充"干净"）；
7. 端到端回归注入：门通过但漂移告警 → 退出码 2（两种裁决互相独立）；
8. 确定性：同一配置两次运行指标逐位一致（无人值守可复现）；
9. KPI 桥：按 case_id 去重计数 = 13（≥10，本单贡献口径）。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_runner import artifact as art          # noqa: E402
from tools.benchmark_runner import config as cfgmod       # noqa: E402
from tools.benchmark_runner import gate                    # noqa: E402
from tools.benchmark_runner import kpi_bridge              # noqa: E402
from tools.benchmark_runner import regression as reg       # noqa: E402
from tools.benchmark_runner import runner as run           # noqa: E402
from tools.benchmark_runner.model import ConfigError        # noqa: E402

CONFIGS = ROOT / "tools" / "benchmark_runner" / "configs"
POSITIVE = ("fx_integration", "fx_series", "fx_rootfinding")


def _cfg(name: str) -> dict:
    return cfgmod.load_config(CONFIGS / f"{name}.json")


# ── 1. 配置校验 ────────────────────────────────────────────────────────────
def test_config_requires_suite_criteria_and_cases():
    with pytest.raises(ConfigError):
        cfgmod.validate_config({"suite": "s", "cases": []})
    with pytest.raises(ConfigError):
        cfgmod.validate_config({"suite": "s", "cases": [{"id": "a", "workload": "m:f",
                                                         "criteria": []}]})  # 无 suite_criteria
    base = {"suite": "s", "suite_criteria": [{"metric": "n_cases", "op": ">=", "value": 1}],
            "cases": [{"id": "a", "workload": "m:f", "criteria": []}]}
    assert cfgmod.validate_config(base)["suite"] == "s"


def test_config_rejects_bad_shapes():
    sc = [{"metric": "n_cases", "op": ">=", "value": 1}]
    # 未知算子
    with pytest.raises(ConfigError, match="unknown op"):
        cfgmod.validate_config({"suite": "s", "suite_criteria": sc,
                                "cases": [{"id": "a", "workload": "m:f",
                                           "criteria": [{"metric": "x", "op": "~=", "value": 1}]}]})
    # 重复 id
    case = {"id": "a", "workload": "m:f", "criteria": []}
    with pytest.raises(ConfigError, match="duplicate"):
        cfgmod.validate_config({"suite": "s", "suite_criteria": sc, "cases": [case, dict(case)]})
    # 点分路径缺冒号
    with pytest.raises(ConfigError, match="module:function"):
        cfgmod.validate_config({"suite": "s", "suite_criteria": sc,
                                "cases": [{"id": "a", "workload": "nofn", "criteria": []}]})
    # 阈值非有限
    with pytest.raises(ConfigError):
        cfgmod.validate_config({"suite": "s", "suite_criteria": sc,
                                "cases": [{"id": "a", "workload": "m:f",
                                           "criteria": [{"metric": "x", "op": "<=", "value": None}]}]})


def test_config_loads_yaml(tmp_path):
    yml = tmp_path / "s.yaml"
    yml.write_text(
        "suite: fx_yaml\n"
        "suite_criteria:\n"
        "  - {metric: n_cases, op: '>=', value: 1}\n"
        "cases:\n"
        "  - id: c1\n"
        "    workload: 'tools.benchmark_runner.synthetic:series_partial_sum'\n"
        "    inputs: {ratio: 0.5, terms: 10}\n"
        "    reference: {workload: 'tools.benchmark_runner.synthetic:series_reference',\n"
        "                inputs: {ratio: 0.5}}\n"
        "    criteria:\n"
        "      - {metric: rel_error, op: '<=', value: 0.01}\n",
        encoding="utf-8")
    cfg = cfgmod.load_config(yml)
    assert cfg["suite"] == "fx_yaml" and len(cfg["cases"]) == 1
    assert len(cfg["_config_sha256"]) == 64
    verdict = run.run_suite(cfg, run_id="yaml")
    assert verdict.passed and verdict.cases[0].passed


# ── 2. 判据门 fail-closed ──────────────────────────────────────────────────
def test_gate_no_criteria_is_fail():
    checks = gate.evaluate([], {"x": 1.0}, label="case z")
    assert len(checks) == 1 and not checks[0].passed
    assert checks[0].op == "__no_criteria__"
    assert not gate.verdict_of(checks)


def test_gate_missing_and_nonfinite_metric_fail():
    crit = [{"metric": "rel_error", "op": "<=", "value": 1e-6}]
    for metrics in ({}, {"rel_error": None}, {"rel_error": float("nan")},
                    {"rel_error": float("inf")}, {"rel_error": "0.0"}, {"rel_error": True}):
        checks = gate.evaluate(crit, metrics)
        assert not checks[0].passed, metrics
        assert not gate.verdict_of(checks)
    assert gate.verdict_of(gate.evaluate(crit, {"rel_error": 0.0}))


def test_gate_operators():
    assert gate.verdict_of(gate.evaluate([{"metric": "v", "op": "in_range", "min": 0, "max": 2}],
                                        {"v": 1.0}))
    assert not gate.verdict_of(gate.evaluate([{"metric": "v", "op": "in_range", "min": 0, "max": 2}],
                                            {"v": 3.0}))
    assert gate.verdict_of(gate.evaluate([{"metric": "v", "op": "==", "value": 2.0, "tol": 1e-9}],
                                        {"v": 2.0 + 1e-12}))
    assert not gate.verdict_of(gate.evaluate([{"metric": "v", "op": "==", "value": 2.0, "tol": 1e-9}],
                                            {"v": 2.001}))
    assert gate.verdict_of(gate.evaluate([{"metric": "t", "op": "monotonic",
                                           "direction": "decreasing"}], {"t": [3, 2, 1]}))
    bad = gate.evaluate([{"metric": "t", "op": "monotonic", "direction": "decreasing"}],
                        {"t": [3, 2, 2.5]})
    assert not bad[0].passed and "breaks" in bad[0].detail
    assert not gate.evaluate([{"metric": "t", "op": "monotonic", "direction": "increasing"}],
                             {"t": [1.0]})[0].passed          # 样本 < 2 也不许过
    assert gate.verdict_of(gate.evaluate([{"metric": "t", "op": "finite"}], {"t": [1, 2, 3]}))
    assert not gate.verdict_of(gate.evaluate([{"metric": "t", "op": "finite"}], {"t": [1, float("nan")]}))


# ── 3/4. runner：正示例全过、负对照必挂 ────────────────────────────────────
@pytest.mark.parametrize("name", POSITIVE)
def test_positive_suites_pass(name):
    verdict = run.run_suite(_cfg(name), run_id=f"unit-{name}")
    assert verdict.passed, [c.failed_checks for c in verdict.cases]
    assert all(c.passed for c in verdict.cases)
    assert verdict.counts["pass_ratio"] == 1.0


def test_all_positive_cases_have_reference_error():
    total = 0
    for name in POSITIVE:
        for c in run.run_suite(_cfg(name), run_id=f"unit-{name}").cases:
            assert "rel_error" in c.metrics and c.metrics["rel_error"] is not None
            assert c.metrics["rel_error"] <= 1e-2
            total += 1
    assert total == 13          # 本单贡献的无人值守算例数（≥10 判据）


def test_negative_control_must_fail():
    verdict = run.run_suite(_cfg("fx_negative_control"), run_id="unit-negctl")
    assert not verdict.passed
    status = {c.case_id: (c.status, c.failed_checks) for c in verdict.cases}
    # (a) 欠分辨率算例：误差判据必须红
    assert status["fx_neg_coarse_integral"][0] == "fail"
    assert "rel_error:<=" in status["fx_neg_coarse_integral"][1]
    # (b) 无判据算例：fail-closed，绝不许静默通过
    assert status["fx_neg_no_criteria"][0] == "fail"
    assert "__none__:__no_criteria__" in status["fx_neg_no_criteria"][1]


def test_case_error_is_recorded_not_raised():
    case = {"id": "boom", "workload": "tools.benchmark_runner.synthetic:numeric_integral",
            "inputs": {"func": "nope", "a": 0, "b": 1, "n": 4},
            "reference": None, "criteria": [{"metric": "value", "op": "finite"}],
            "title": "boom", "derived_metrics": [], "tags": []}
    cv = run.run_case(case)
    assert cv.status == "error" and not cv.passed and cv.error


# ── 5. 工件：JSON + PNG + manifest + provenance ────────────────────────────
def test_artifacts_written_and_hashes_recompute(tmp_path):
    summary = run.run_config(CONFIGS / "fx_series.json", out_root=tmp_path,
                             run_id="artifact-run", write=True)
    assert summary["exit_code"] == run.EXIT_OK
    paths = summary["paths"]
    run_dir = pathlib.Path(paths["out_dir"])
    assert pathlib.Path(paths["figure"]).is_file()
    assert pathlib.Path(paths["figure"]).stat().st_size > 5000        # 真 PNG，不是空壳
    manifest = json.loads(pathlib.Path(paths["manifest"]).read_text())
    assert manifest["file_count"] >= 1 + 4 + 1                        # result + 4 cases + png
    for entry in manifest["files"]:
        p = run_dir / entry["path"]
        assert p.is_file(), entry
        assert art.sha256_file(p) == entry["sha256"], entry
    assert manifest["manifest_digest"] == art.sha256_obj(manifest["files"])
    # provenance：配置摘要/git/引擎版本齐全，且不夹带凭据
    result = json.loads(pathlib.Path(paths["result"]).read_text())
    prov = result["provenance"]
    for key in ("engine_version", "config_sha256", "git_sha", "python_version", "generated_at"):
        assert key in prov
    blob = json.dumps(result)
    for secret in ("password", "PASS", "Bearer", "sk-", "token"):
        assert secret not in blob
    # 逐算例工件自带自己的 sha256 与出处
    one = json.loads(pathlib.Path(paths["case_files"][0]).read_text())
    assert len(one["artifact_sha256"]) == 64 and one["provenance"]["workload"]


# ── 6. 回归检测：判别力 + 特异性 ───────────────────────────────────────────
def _suite(cases):
    return {"suite": "fx", "run_id": "r1", "cases": cases}


def _case(cid, **metrics):
    return {"case_id": cid, "status": "pass", "passed": True, "metrics": metrics}


def test_regression_specificity_and_power():
    base = reg.build_baseline(_suite([_case("c1", rel_error=1e-6, value=1.0)]), {}, "t0")
    for cur, expect in ((1.02e-6, "clean"), (1.035e-6, "regression"), (1.12e-6, "regression")):
        rep = reg.compare(_suite([_case("c1", rel_error=cur, value=1.0)]), base, drift_threshold=0.03)
        assert rep["status"] == expect, (cur, rep)
    rep = reg.compare(_suite([_case("c1", rel_error=1.035e-6, value=1.0)]), base)
    sev = {a["severity"] for a in rep["alerts"] if a["kind"] == "drift"}
    assert "warn" in sev and "critical" not in sev
    rep = reg.compare(_suite([_case("c1", rel_error=1.12e-6, value=1.0)]), base)
    assert any(a["severity"] == "critical" and a["kind"] == "drift" for a in rep["alerts"])


def test_regression_zero_baseline_does_not_false_alarm():
    """基线 rel_error=0.0（Simpson 对三次式精确）→ 2e-16 不许报回归。"""
    base = reg.build_baseline(_suite([_case("c", rel_error=0.0)]), {}, "t0")
    rep = reg.compare(_suite([_case("c", rel_error=2e-16)]), base)
    assert rep["status"] == "clean", rep["alerts"]


def test_regression_verdict_flip_missing_new_and_absent_baseline():
    base = reg.build_baseline(_suite([_case("a", rel_error=1e-6), _case("b", rel_error=1e-6)]), {}, "t0")
    flip = {"case_id": "a", "status": "fail", "passed": False, "metrics": {"rel_error": 1e-5}}
    rep = reg.compare(_suite([flip, _case("c", rel_error=1e-6)]), base)
    kinds = {(a["kind"], a.get("case_id")) for a in rep["alerts"]}
    assert ("verdict_flip", "a") in kinds
    assert ("case_missing", "b") in kinds
    assert ("case_new", "c") in kinds
    assert reg.has_regression(rep)
    absent = reg.compare(_suite([_case("a", rel_error=1e-6)]), None)
    assert absent["status"] == "no_baseline" and not reg.has_regression(absent)
    assert "NOT evidence" in absent["note"]


def test_regression_ignores_scale_parameters_by_default():
    """默认不把分辨率参数（intervals/terms/...）当退化指标。"""
    base = reg.build_baseline(_suite([_case("a", rel_error=1e-6, intervals=32.0)]), {}, "t0")
    rep = reg.compare(_suite([_case("a", rel_error=1e-6, intervals=64.0)]), base)
    assert rep["status"] == "clean"


# ── 7. 端到端：门通过但回归告警 → 退出码 2 ────────────────────────────────
def test_end_to_end_drift_exit_code_2(tmp_path):
    base_path = tmp_path / "base.json"
    run.run_config(CONFIGS / "fx_integration.json", out_root=tmp_path / "art",
                   baseline_path=base_path, update_baseline=True, run_id="b")
    # 数值不变、工作量 +5%（instrumented 包装）→ 门应通过、漂移应告警
    drift_cfg = json.loads((CONFIGS / "fx_integration.json").read_text())
    drift_cfg["suite"] = "fx_integration_drift_e2e"
    for c in drift_cfg["cases"]:
        c["inputs"] = dict(c["inputs"], overhead=0.05)
        c["workload"] = "tools.benchmark_runner.instrumented:integral_with_overhead"
    cfg_path = tmp_path / "drift.json"
    cfg_path.write_text(json.dumps(drift_cfg), encoding="utf-8")

    summary = run.run_config(cfg_path, out_root=tmp_path / "art", baseline_path=base_path,
                             track_metrics=["evals"], run_id="drift")
    assert summary["passed"] is True                     # 判据门通过
    assert summary["exit_code"] == run.EXIT_REGRESSION    # 回归告警另立退出码
    assert summary["regression"]["status"] == "regression"
    assert all(abs(a["drift"] - 0.05) < 1e-9 for a in summary["regression"]["alerts"])

    # 特异性：2%（< 3% 容差）不许告警
    for c in drift_cfg["cases"]:
        c["inputs"] = dict(c["inputs"], overhead=0.02)
    cfg2 = tmp_path / "drift2.json"
    cfg2.write_text(json.dumps(drift_cfg), encoding="utf-8")
    s2 = run.run_config(cfg2, out_root=tmp_path / "art", baseline_path=base_path,
                        track_metrics=["evals"], run_id="drift2")
    assert s2["exit_code"] == run.EXIT_OK and s2["regression"]["status"] == "clean"


def test_cli_exit_codes(tmp_path, capsys):
    assert run.main(["--config", str(CONFIGS / "fx_series.json"), "--out", str(tmp_path),
                     "--run-id", "cli-ok", "--no-write"]) == run.EXIT_OK
    assert run.main(["--config", str(CONFIGS / "fx_negative_control.json"), "--out", str(tmp_path),
                     "--run-id", "cli-fail", "--no-write"]) == run.EXIT_GATE_FAIL
    # 坏 JSON / 文件不存在 → 配置问题（退出码 3），不是内部错误
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert run.main(["--config", str(bad), "--no-write"]) == run.EXIT_CONFIG
    assert run.main(["--config", str(tmp_path / "nope.json"), "--no-write"]) == run.EXIT_CONFIG


def test_config_error_exit_code(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"suite": "x", "cases": []}), encoding="utf-8")
    assert run.main(["--config", str(bad), "--no-write"]) == run.EXIT_CONFIG


# ── 8. 确定性（无人值守可复现） ────────────────────────────────────────────
def test_determinism_across_runs():
    a = run.run_suite(_cfg("fx_integration"), run_id="d1").to_dict()
    b = run.run_suite(_cfg("fx_integration"), run_id="d1").to_dict()
    assert a == b
    assert cfgmod.config_digest(_cfg("fx_integration")) == cfgmod.config_digest(_cfg("fx_integration"))


# ── 9. KPI 桥 ──────────────────────────────────────────────────────────────
def test_kpi_bridge_counts_distinct_passed_cases(tmp_path):
    for name in POSITIVE:
        run.run_config(CONFIGS / f"{name}.json", out_root=tmp_path, run_id="lead")
    run.run_config(CONFIGS / "fx_negative_control.json", out_root=tmp_path, run_id="lead")
    ledger = kpi_bridge.collect(tmp_path)
    assert ledger["count"] == 13, ledger["count"]
    assert {c["case_id"] for c in ledger["cases"]}.isdisjoint({"fx_neg_coarse_integral",
                                                              "fx_neg_no_criteria"})
    assert all(len(c["manifest_sha256"] or "") == 64 for c in ledger["cases"])
    # 无 manifest 的 run 默认不计（无哈希链 = 无证据）
    naked = tmp_path / "fx_series" / "naked"
    naked.mkdir(parents=True)
    (naked / "result.json").write_text(json.dumps({"suite": "fx_series", "run_id": "naked",
                                                   "cases": [{"case_id": "ghost", "passed": True}]}),
                                       encoding="utf-8")
    assert kpi_bridge.collect(tmp_path)["count"] == 13
    assert kpi_bridge.collect(tmp_path, require_manifest=False)["count"] == 14