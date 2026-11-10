"""IN55W3-N08 · 证据链与口径冻结测试（判据 c + 预注册完整性）。

锁死的性质：
* 每行基线必须同时带 SQL 原文与 sha256；篡改任一字段都必须被 ``verify_row`` 抓到；
* 口径指纹 ``SPEC_SHA256`` 由口径字典重算得出（改口径必然改指纹 → 报告与 prereg 对不上）；
* 终态词表兜底与 ``backend/contracts/mission_status_vocab.v1.json`` 一致（不许第二份真相）。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tools.cycle_baseline import provenance, render, runner, segments, spec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_seal_and_verify_roundtrip():
    row = provenance.seal_row("t_queue", "cohort", {"n": 3, "p50": 1.5}, "SELECT 1")
    assert provenance.verify_row(row)
    assert row["sql_sha256"] and row["value_sha256"]


def test_tampered_values_detected():
    row = provenance.seal_row("t_queue", "cohort", {"n": 3, "p50": 1.5}, "SELECT 1")
    row["values"]["p50"] = 999.0
    assert not provenance.verify_row(row)


def test_tampered_sql_detected():
    row = provenance.seal_row("t_queue", "cohort", {"n": 3}, "SELECT 1")
    row["sql"] = "SELECT 2"
    assert not provenance.verify_row(row)


def test_missing_sql_is_rejected():
    assert not provenance.verify_row({"key": "x", "values": {}, "value_sha256": "0"})


def test_spec_fingerprint_is_recomputable_and_frozen():
    assert spec.SPEC_SHA256 == spec.sha256_of(spec._canonical_spec())
    assert len(spec.SPEC_SHA256) == 64
    assert spec.PREREG_ID == "cycle-baseline-2026-11-10"
    # 门限属于预注册：改动即须改版本号
    assert spec.COVERAGE_MIN == 0.80
    assert spec.JAIN_RECOVERY_TOL == 0.02
    assert spec.DETECTOR_F1_MIN == 0.90
    assert spec.POLICY_THEORY_TOL == 0.05


def test_terminal_vocab_single_source():
    contract = REPO_ROOT / "backend" / "contracts" / "mission_status_vocab.v1.json"
    if not contract.exists():
        pytest.skip("contract file not present in this checkout")
    data = json.loads(contract.read_text(encoding="utf-8"))
    terms = data.get("terminal") or data.get("statuses", {}).get("terminal")
    assert terms, f"unexpected contract shape: {list(data)}"
    if isinstance(terms, dict):
        terms = list(terms)
    assert set(terms) == set(spec.FALLBACK_TERMINAL)


def test_report_provenance_and_gates_pass_on_fixture_cohort():
    from tools.cycle_baseline import fixtures
    recs, _ = fixtures.starvation_fixture(n_normal=40, n_planted=4, n_borderline=2)
    report = runner.build_report(recs, {}, None, source="fixture")
    # 夹具里没有 merge/report 证据 → 这两段必须 WITHHELD（判据 d 生效）
    assert "t_merge" in report["coverage_gate"]["withheld_segments"]
    assert report["criteria"]["d_coverage_gate"]["pass"] is True
    # 证据链：行数与逐行校验
    prov = report["provenance"]
    assert prov["verify"]["all_ok"] and prov["verify"]["n_rows"] >= 5
    for row in prov["tables"]["ledger"]:
        assert provenance.verify_row(row)
    # 判据 c 在无 SQL 对拍源（fixture）时应如实报失配，而不是假装通过
    assert report["criteria"]["c_provenance"]["pass"] is False


def test_report_criteria_shape_and_render_contains_numbers():
    from tools.cycle_baseline import fixtures
    recs, _ = fixtures.starvation_fixture(n_normal=40, n_planted=4, n_borderline=2)
    report = runner.build_report(recs, {}, None, source="fixture")
    for key in ("a_jain_recovery", "b_detector_f1", "c_provenance", "d_coverage_gate",
                "e_policy_theory", "overall"):
        assert key in report["criteria"]
    md = render.render_markdown(report, figures=[])
    assert spec.PREREG_ID in md and report["spec_sha256"] in md
    assert "WITHHELD" in md and "判据 (b)" in md or "检测器" in md
    # 数字必须来自 report（抽查 p50 渲染值出现在文中）
    p50 = report["ledger"]["cycle_s"]["p50"]
    assert f"{p50:,.1f}" in md


def test_coverage_gate_reports_rule_text():
    rows = [{"mission_id": 1, "status": "succeeded",
             "created_at": "2026-11-01T00:00:00+00:00",
             "claimed_at": "2026-11-01T00:01:00+00:00",
             "finished_at": "2026-11-01T00:02:00+00:00"}]
    ledger = segments.segment_ledger(segments.build_records(rows))
    gate = segments.coverage_gate(ledger)
    assert gate["coverage_min"] == spec.COVERAGE_MIN
    assert "WITHHELD" in gate["rule"]
    assert gate["withheld_segments"]

# ── 预注册漂移守卫 + 落盘工件回验 ────────────────────────────────────────
def test_prereg_document_matches_frozen_spec():
    prereg = REPO_ROOT / "prereg" / f"{spec.PREREG_ID}.md"
    assert prereg.exists(), f"prereg 未冻结: {prereg}"
    text = prereg.read_text(encoding="utf-8")
    assert spec.SPEC_SHA256 in text, "prereg 的口径指纹与 spec 不一致（口径漂移）"
    for seg in spec.SEGMENTS:
        assert seg in text


def test_baseline_artifact_rows_verify_and_spec_matches():
    art = REPO_ROOT / "artifacts" / "cycle_baseline" / "baseline.json"
    if not art.exists():
        pytest.skip("baseline artifact not generated in this checkout")
    data = json.loads(art.read_text(encoding="utf-8"))
    assert data["spec_sha256"] == spec.SPEC_SHA256
    rows = data["provenance"]["tables"]["ledger"] + data["provenance"]["tables"]["attribution"]
    assert rows, "基线表为空"
    v = provenance.verify_table(rows)
    assert v["all_ok"], v["bad_keys"]
    # 判据 (c) 在真实工件上必须成立（SQL 对拍零失配）
    assert data["criteria"]["c_provenance"]["pass"] is True
    # 判据 (d) 必须如实降级，不得偷偷放行
    assert set(data["criteria"]["d_coverage_gate"]["withheld_segments"]) <= set(spec.SEGMENTS)
    # 每行都真的带 SQL
    assert all(r["sql"].strip() for r in rows)
