"""P167W2B-N09 测试：批级 campaign 编排 + 既有验证面 + KPI 对账（零领域语义）。

判别力优先：负向对照不是"跑通"，而是**必须挂住**——
(1) 判据带外（5% > 3%）必须 FAIL；
(2) 缺基线 / 基线不可用 → 对账 fail-closed（"没比过" ≠ "达标"）；
(3) 正向 suite 断掉时 campaign 必须判不通过（不许把"少跑"读成"没退化"）；
另有回归探针的特异性（+2% 不报）与判别力（+5% warn / +16% critical）。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_runner import artifact as art          # noqa: E402
from tools.benchmark_runner import campaign_report as crep  # noqa: E402
from tools.benchmark_runner import domain_guard            # noqa: E402
from tools.benchmark_runner import kpi_baseline            # noqa: E402
from tools.benchmark_runner import kpi_bridge              # noqa: E402
from tools.benchmark_runner import surfaces                # noqa: E402
from tools.benchmark_runner import unattended as un        # noqa: E402
from tools.benchmark_runner.config import ConfigError, load_config  # noqa: E402
from tools.benchmark_runner.runner import run_config       # noqa: E402

CFG = ROOT / "tools/benchmark_runner/configs"
CAMPAIGN = ROOT / "tools/benchmark_runner/campaigns/pf_default.json"


# ─────────────────────────── campaign 配置：fail-closed 校验 ───────────────────────────
def _minimal_campaign(**over):
    data = {
        "campaign": "t_unit",
        "suites": [{"id": "fx_integration", "config": str(CFG / "fx_integration.json")}],
        "criteria": [{"metric": "steps_ok_ratio", "op": "==", "value": 1.0}],
    }
    data.update(over)
    return data


def test_campaign_config_loads_and_records_digest():
    camp = un.load_campaign(CAMPAIGN)
    assert camp["campaign"] == "pf_default"
    assert len(camp["_campaign_sha256"]) == 64
    assert camp["targets"]["unattended_cases"] == 75
    assert all(s["expect"] in ("pass", "fail") for s in camp["suites"])


def test_campaign_rejects_unknown_expect():        # 负向对照 1：含糊期望一律拒收
    with pytest.raises(ConfigError):
        un.validate_campaign(_minimal_campaign(
            suites=[{"id": "s1", "config": str(CFG / "fx_integration.json"), "expect": "maybe"}]))


def test_campaign_requires_predeclared_criteria():  # 负向对照 2：没有批级判据 → 配置非法
    with pytest.raises(ConfigError):
        un.validate_campaign(_minimal_campaign(criteria=[]))


def test_campaign_rejects_probe_with_unknown_base():
    with pytest.raises(ConfigError):
        un.validate_campaign(_minimal_campaign(drift_probes=[
            {"id": "p", "base": "nope", "overhead": 0.05, "expect_status": "warn"}]))


def test_campaign_rejects_duplicate_suite_id():
    dup = [{"id": "dup", "config": str(CFG / "fx_integration.json")},
           {"id": "dup", "config": str(CFG / "fx_series.json")}]
    with pytest.raises(ConfigError):
        un.validate_campaign(_minimal_campaign(suites=dup))


# ─────────────────────────── 漂移探针构造与分级 ───────────────────────────
def test_probe_config_keeps_case_ids_and_drops_reference():
    base = load_config(CFG / "fx_integration.json")
    probe = un.build_probe_config(base, 0.05, "probe_x")
    assert [c["id"] for c in probe["cases"]] == [c["id"] for c in base["cases"]]
    assert all("reference" not in c for c in probe["cases"])
    assert all(c["workload"].endswith("instrumented:integral_with_overhead") for c in probe["cases"])
    assert all(c["inputs"]["overhead"] == 0.05 for c in probe["cases"])
    assert probe["suite_criteria"]          # 预声明聚合判据必须在场


def test_probe_config_is_still_gate_green():
    base = load_config(CFG / "fx_integration.json")
    probe = un.build_probe_config(base, 0.16, "probe_gate")
    tmp = pathlib.Path("/tmp") / "n09_probe_gate"
    cfg_path = tmp / "probe_gate.json"
    art.write_json(cfg_path, probe)
    summary = run_config(cfg_path, out_root=tmp / "out", baseline_path=None, write=False)
    assert summary["passed"] is True        # 数值没变 → 判据门仍绿
    assert summary["suite_metrics"]["max_rel_error"] is None    # 探针不做误差判据


@pytest.mark.parametrize("value,expected", [(1.0, 0), (1.02, 0), (1.05, 1), (1.16, 2)])
def test_probe_severity_code_mapping(value, expected):
    from tools.benchmark_runner import regression as reg
    baseline = {"cases": {"c": {"passed": True, "metrics": {"evals": 1.0}}}}
    suite = {"suite": "s", "cases": [{"case_id": "c", "status": "pass", "passed": True,
                                      "metrics": {"evals": value}}]}
    report = reg.compare(suite, baseline, drift_threshold=0.03, track_metrics=["evals"])
    assert un.probe_severity_code(report) == expected


# ─────────────────────────── KPI 对账（fail-closed） ───────────────────────────
def _ledger(cases):
    return {"root": "x", "cases": [{"case_id": cid, "suite": s, "status": "pass"} for cid, s in cases]}


def test_reconcile_fails_without_baseline():       # 负向对照 3：没比过 ≠ 达标
    out = un.reconcile(None, _ledger([("a", "s1")]), 75)
    assert out["status"] == "unavailable" and out["verdict"] == "fail"
    assert "not evidence" in out["reason"]


def test_reconcile_fails_when_baseline_unusable():
    rec = {"status": "unavailable", "value": None, "metric": "unattended_cases"}
    out = un.reconcile(rec, _ledger([("a", "s1")]), 75)
    assert out["verdict"] == "fail" and out["status"] == "unavailable"


def test_reconcile_below_target_fails():
    rec = kpi_baseline.build_record(10, [f"n{i}" for i in range(10)], {"host": "h"})
    out = un.reconcile(rec, _ledger([("a", "s1")]), 75)
    assert out["status"] == "ok" and out["verdict"] == "fail" and out["combined"] == 11


def test_reconcile_dedups_overlapping_ids():
    rec = kpi_baseline.build_record(2, ["a", "b"], {"host": "h"})
    out = un.reconcile(rec, _ledger([("a", "s1"), ("c", "s1")]), 5)
    assert out["overlap_count"] == 1 and out["delta_case_count"] == 1
    assert out["combined"] == 3 and out["verdict"] == "fail"


def test_reconcile_filters_uncounted_suites():
    rec = kpi_baseline.build_record(70, [f"n{i}" for i in range(70)], {"host": "h"})
    led = _ledger([("a", "counted"), ("b", "excluded")])
    out = un.reconcile(rec, led, 75, counted_suites=["counted"])
    assert out["delta_case_ids"] == ["a"] and out["combined"] == 71


def test_baseline_record_flags_count_name_mismatch():
    rec = kpi_baseline.build_record(3, ["a", "b"], {"host": "h"})
    assert rec["status"] == "inconsistent" and rec["value"] == 3
    assert rec["case_ids_sha256"] == art.sha256_obj(["a", "b"])


def test_baseline_sql_matches_etl_definition():
    from tools.guide_kpi_ledger import KPI_SPECS
    spec = next(s for s in KPI_SPECS if s["key"] == "unattended_cases")
    norm = lambda s: " ".join(str(s).split())        # noqa: E731
    assert norm(kpi_baseline.KPI_SQL) == norm(spec["sql"])


def test_baseline_capture_is_unavailable_without_psql(monkeypatch, tmp_path):
    monkeypatch.setattr(kpi_baseline.shutil, "which", lambda _: None)
    rec = kpi_baseline.capture(tmp_path / "b.json", "h", "d", "u", None)
    assert rec["status"] == "unavailable" and rec["value"] is None
    assert art.read_json(tmp_path / "b.json")["status"] == "unavailable"


# ─────────────────────────── 既有验证面适配（独立参考解） ───────────────────────────
def test_surfaces_hour_bucket_matches_independent_reference():
    for ts in ("2026-11-10T05:37:12.345678+00:00", "1970-01-01T00:00:00+00:00",
               "2026-03-01T23:59:59-05:00"):
        a, b = surfaces.kpi_hour_bucket(ts), surfaces.ref_hour_bucket(ts)
        assert a["value"] == pytest.approx(b["value"], rel=0, abs=1e-9)
        assert a["has_tz"] == b["has_tz"] == 1.0


def test_surfaces_ratio_and_pct_undefined_semantics():
    for num, den, nd in ((1, 3, 4), (7, 8, 3), (2, 7, 4)):
        assert surfaces.kpi_ratio(num, den, nd)["value"] == surfaces.ref_ratio(num, den, nd)["value"]
    assert surfaces.kpi_ratio(5, 0)["defined"] == surfaces.ref_ratio(5, 0)["defined"] == 0.0
    assert surfaces.kpi_pct(0.12345)["value"] == surfaces.ref_pct(0.12345)["value"] == 12.3
    assert surfaces.kpi_pct(None)["defined"] == surfaces.ref_pct(None)["defined"] == 0.0


def test_surfaces_digest_invariants():
    assert surfaces.digest_order_invariance()["value"] == 1.0
    assert surfaces.ref_digest_order_invariance()["value"] == 1.0
    assert surfaces.digest_payload_sensitivity()["value"] == 1.0
    assert surfaces.ref_digest_payload_sensitivity()["value"] == 1.0


def test_surfaces_manifest_tamper_is_detected():
    out = surfaces.manifest_tamper_detected()
    assert out["value"] == 1.0 and out["clean_ok"] == 1.0 and out["tamper_flagged"] == 1.0


@pytest.mark.parametrize("cur,code", [(1.02, 0.0), (1.05, 1.0), (1.16, 2.0)])
def test_surfaces_regression_severity_matches_reference(cur, code):
    got, ref = surfaces.regression_severity_code(cur), surfaces.ref_regression_severity_code(cur)
    assert got["value"] == ref["value"] == code


# ─────────────────────────── 判据门带内/带外（fail-closed） ───────────────────────────
def test_gate_band_pass_suite_passes_at_about_one_percent(tmp_path):
    summary = run_config(CFG / "fx_gate_band_pass.json", out_root=tmp_path,
                         baseline_path=None, write=True)
    assert summary["passed"] is True and summary["exit_code"] == 0
    errs = [c["metrics"]["rel_error"] for c in summary["cases"]]
    assert all(e < 0.03 for e in errs)
    assert min(errs) < 0.012          # ≈1% 带内算例
    assert summary["integrity"]["ok"] is True


def test_gate_band_fail_suite_must_fail_above_three_percent(tmp_path):   # 负向对照 4
    summary = run_config(CFG / "fx_gate_band_fail.json", out_root=tmp_path,
                         baseline_path=None, write=True)
    assert summary["passed"] is False and summary["exit_code"] == 1
    errs = [c["metrics"]["rel_error"] for c in summary["cases"]]
    assert max(errs) > 0.04           # ≈5% 带外
    assert min(errs) > 0.03           # 3.125% 贴线也不算过（无边界松量）
    assert all(c["failed_checks"] for c in summary["cases"])


# ─────────────────────────── end-to-end campaign ───────────────────────────
def _write_campaign(tmp_path, baseline_value=60, expect_band="pass"):
    base = kpi_baseline.build_record(baseline_value,
                                     [f"seed_case_{i}" for i in range(baseline_value)],
                                     {"host": "fixture", "database": "fixture", "user": "fixture"})
    art.write_json(tmp_path / "baseline.json", base)
    return {
        "campaign": "t_e2e",
        "drift_threshold": 0.03,
        "targets": {"unattended_cases": 75},
        "reconciliation": {"baseline": str(tmp_path / "baseline.json")},
        "suites": [
            {"id": "fx_integration", "config": str(CFG / "fx_integration.json"), "expect": "pass"},
            {"id": "pf_surfaces", "config": str(CFG / "pf_surfaces.json"), "expect": "pass"},
            {"id": "fx_gate_band_pass", "config": str(CFG / "fx_gate_band_pass.json"),
             "expect": expect_band},
            {"id": "fx_gate_band_fail", "config": str(CFG / "fx_gate_band_fail.json"),
             "expect": "fail", "control": True},
            {"id": "fx_negative_control", "config": str(CFG / "fx_negative_control.json"),
             "expect": "fail", "control": True, "baseline": "none"},
        ],
        "drift_probes": [
            {"id": "p02", "base": "fx_integration", "overhead": 0.02, "expect_status": "clean"},
            {"id": "p05", "base": "fx_integration", "overhead": 0.05, "expect_status": "warn"},
        ],
        "criteria": [
            {"metric": "steps_ok_ratio", "op": "==", "value": 1.0},
            {"metric": "negative_controls_held", "op": "==", "value": 2},
            {"metric": "integrity_failures", "op": "==", "value": 0},
            {"metric": "drift_specificity_ok", "op": "==", "value": 1},
            {"metric": "drift_power_ok", "op": "==", "value": 1},
            {"metric": "unattended_cases_delta", "op": ">=", "value": 10},
            {"metric": "unattended_cases", "op": ">=", "value": 75},
        ],
    }


@pytest.fixture(scope="module")
def e2e_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("n09_e2e")
    camp = un.validate_campaign(_write_campaign(tmp))
    camp["_campaign_sha256"] = "0" * 64
    summary = un.run_campaign(camp, out_root=tmp / "artifacts", run_id="T_E2E", write=True)
    return tmp, summary


def test_e2e_campaign_passes_all_predeclared_criteria(e2e_run):
    _, summary = e2e_run
    assert summary["passed"] is True
    failed = [c for c in summary["criteria"] if not c["passed"]]
    assert failed == []
    m = summary["metrics"]
    assert m["unattended_cases_delta"] >= 10
    assert m["unattended_cases"] == 60 + m["unattended_cases_delta"]
    assert m["negative_controls_held"] == 2 and m["integrity_failures"] == 0


def test_e2e_ledger_only_counts_hash_chained_passed_cases(e2e_run):
    _, summary = e2e_run
    led = kpi_bridge.collect(pathlib.Path(summary["kpi_ledger"]["root"]))
    assert led["count"] == len(led["cases"])
    for c in led["cases"]:
        assert c["status"] == "pass"
        assert c["manifest_sha256"] and c["case_artifact_sha256"]


def test_e2e_artifacts_verified_and_three_figures_open(e2e_run):
    _, summary = e2e_run
    arts = summary["artifacts"]
    assert arts["verified"] is True
    figs = arts["figures"]
    assert len(figs) >= 2
    for f in figs:
        p = pathlib.Path(f["path"])
        assert p.is_file() and p.stat().st_size > 5000
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"     # 真 PNG，能打开
        assert art.sha256_file(p) == f["sha256"]
    man = art.read_json(arts["manifest"])
    assert art.sha256_obj(man["files"]) == man["manifest_digest"]


def test_e2e_report_numbers_come_from_artifacts(e2e_run):
    tmp, summary = e2e_run
    run_dir = pathlib.Path(summary["artifacts"]["dir"])
    rendered = crep.render(run_dir)
    rep = crep.machine_report(run_dir, rendered)
    acc = rep["acceptance"]
    assert acc["unattended_cases_65_to_at_least_75"]["combined"] >= 75
    assert acc["unattended_cases_65_to_at_least_75"]["baseline"] == 60
    assert acc["artifacts_sha256_verifiable"]["integrity_failures"] == 0
    assert acc["regression_2pct_pass_5pct_alert"]["probes"]["p02"]["observed_code"] == 0
    assert acc["regression_2pct_pass_5pct_alert"]["probes"]["p05"]["observed_code"] == 1
    assert "①" in rendered["markdown"] and "⑥" in rendered["markdown"]
    assert str(rep["passed"]) in ("True", "False")


def test_campaign_fails_when_positive_suite_breaks(tmp_path):             # 负向对照 5
    camp = un.validate_campaign(_write_campaign(tmp_path, expect_band="pass"))
    camp["suites"] = [{"id": "fx_gate_band_pass", "config": str(CFG / "fx_gate_band_pass.json"),
                       "expect": "fail", "control": True, "baseline": "establish_if_missing",
                       "count_in_kpi": True}]
    camp["drift_probes"] = []
    camp["criteria"] = [{"metric": "steps_ok_ratio", "op": "==", "value": 1.0}]
    summary = un.run_campaign(camp, out_root=tmp_path / "out2", run_id="T_NEG", write=False)
    assert summary["passed"] is False          # 期望 fail 但实际 pass → 步骤不符 → 不通过


def test_domain_guard_clean_on_engine_package():
    verdict = domain_guard.scan(["tools/benchmark_runner"])
    assert verdict["ok"] is True, verdict.get("hits")
    assert verdict["status"] == "clean" and verdict["scanned_files"] > 0