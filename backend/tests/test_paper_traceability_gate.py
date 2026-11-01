#!/usr/bin/env python3
"""Tests for the manuscript traceability gate (PAPER-TRACE).

Each test pins one documented blind-review failure mode:
  PG1 phantom path      — cited artifact that does not exist
  PG2 phantom report    — cited report id absent from the ledger
  PG3/4 figure hygiene  — phantom figure / zero-citation figure (H4)
  PG4 table arithmetic  — error column inconsistent with value/reference
  PG5 inline error      — prose "A vs B, 误差 Z%" that does not recompute
  PG7 provenance        — numeric tables with no artifact-level source
  PG8 value-in-source   — number absent from the report it claims (H2/H3)

Run: python3 -m pytest backend/tests/test_paper_traceability_gate.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "paper_gate"))

from traceability_gate import (  # noqa: E402
    check_manuscript,
    numbers_in,
    parse_number,
)


# ───────────────────────────── number parsing ─────────────────────────────

def test_parse_unicode_power_of_ten():
    assert parse_number("1.16 × 10⁻¹³") == pytest.approx(1.16e-13)
    assert parse_number("6.16 × 10⁻¹⁶") == pytest.approx(6.16e-16)
    assert parse_number("| 0.9997 |") == pytest.approx(0.9997)
    assert parse_number("2,450 m") == pytest.approx(2450.0)
    assert parse_number("no number here") is None


def test_numbers_in_folds_superscript():
    assert 1.05e-4 in numbers_in("误差 1.05 × 10⁻⁴ 与 213.9 Hz")


# ───────────────────────────── PG4 table arithmetic ─────────────────────────────

def test_pg4_detects_inconsistent_error_column():
    md = """
| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| Cd | 0.0420 | 0.0427 | 1.00 |
"""
    out = check_manuscript(md, artifact_roots=[ROOT])
    codes = [f["code"] for f in out["findings"]]
    assert "PG4_table_arithmetic" in codes
    f = [f for f in out["findings"] if f["code"] == "PG4_table_arithmetic"][0]
    assert "1.6666" in f["detail"]


def test_pg4_accepts_consistent_error_column():
    md = """
| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| Cd | 0.0420 | 0.0427 | 1.6667 |
"""
    out = check_manuscript(md, artifact_roots=[ROOT])
    assert "PG4_table_arithmetic" not in [f["code"] for f in out["findings"]]


def test_pg4_tolerance_boundary():
    md = """
| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| Cd | 1.0 | 1.03 | 3.02 |
"""
    out = check_manuscript(md, artifact_roots=[ROOT], tol_pp=0.05)
    assert "PG4_table_arithmetic" not in [f["code"] for f in out["findings"]]


# ───────────────────────────── PG5 inline claim ─────────────────────────────

def test_pg5_detects_wrong_inline_error():
    md = ("试验对标：0.0427 vs 0.0420，误差 5.00%，结论通过。\n"
          "另一个工件在 `docs/x.json` 里。")
    (ROOT / "docs").mkdir(exist_ok=True)
    out = check_manuscript(md, artifact_roots=[ROOT])
    assert "PG5_inline_error" in [f["code"] for f in out["findings"]]


def test_pg5_accepts_correct_inline_error():
    md = "试验对标：0.0427 vs 0.0420，误差 1.67%。"
    out = check_manuscript(md, artifact_roots=[ROOT])
    assert "PG5_inline_error" not in [f["code"] for f in out["findings"]]


# ───────────────────────────── PG1 / PG2 resolvability ─────────────────────────────

def test_pg1_phantom_path(tmp_path):
    md = "判据工件：`artifacts/nope/gone.json`。"
    out = check_manuscript(md, artifact_roots=[tmp_path])
    assert "PG1_phantom_path" in [f["code"] for f in out["findings"]]


def test_pg1_real_path_resolves_and_shell_commands_ignored(tmp_path):
    (tmp_path / "artifacts" / "ok").mkdir(parents=True)
    (tmp_path / "artifacts" / "ok" / "gate.json").write_text("{}")
    md = ("判据工件：`artifacts/ok/gate.json`。复现：`python scripts/run_gate.py`，"
          "测试：`pytest tests/test_gate.py`。")
    out = check_manuscript(md, artifact_roots=[tmp_path])
    assert "PG1_phantom_path" not in [f["code"] for f in out["findings"]]
    assert out["stats"]["paths"]["resolved"] == 1


def test_pg2_phantom_report():
    md = "全部结果溯源至报告 #999999。"
    out = check_manuscript(md, report_ids=[823, 857])
    f = [x for x in out["findings"] if x["code"] == "PG2_phantom_report"]
    assert len(f) == 1 and "999999" in f[0]["detail"]


def test_pg2_known_report_passes():
    md = "全部结果溯源至报告 #823。"
    out = check_manuscript(md, report_ids=[823])
    assert "PG2_phantom_report" not in [f["code"] for f in out["findings"]]


# ───────────────────────────── PG3 figure hygiene ─────────────────────────────

def test_pg3_phantom_figure(tmp_path):
    md = "![色散谱](figures/fig1.png)"
    out = check_manuscript(md, bundle=tmp_path)
    assert "PG3_phantom_figure" in [f["code"] for f in out["findings"]]


def test_pg3_zero_citation_figure(tmp_path):
    figdir = tmp_path / "figures"
    figdir.mkdir()
    (figdir / "fig2_love_weber.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    md = "正文只讨论 fig1，没有提到第二张图。"
    out = check_manuscript(md, bundle=tmp_path)
    f = [x for x in out["findings"] if x["code"] == "PG3_zero_citation_figure"]
    assert len(f) == 1 and "fig2" in f[0]["detail"]


def test_pg3_cited_figure_is_clean(tmp_path):
    figdir = tmp_path / "figures"
    figdir.mkdir()
    (figdir / "fig1_disp.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    md = "![色散谱](figures/fig1_disp.png)\n\n如 fig1 所示。"
    out = check_manuscript(md, bundle=tmp_path)
    assert not [x for x in out["findings"] if x["code"].startswith("PG3")]


# ───────────────────────────── PG7 provenance ─────────────────────────────

def test_pg7_flags_report_only_provenance():
    md = ("| Metric | Value |\n|---|---|\n| residual | 1.16e-13 |\n\n"
          "All results are traceable to report #823.")
    out = check_manuscript(md, report_ids=[823])
    assert "PG7_no_artifact_provenance" in [f["code"] for f in out["findings"]]


def test_pg7_clean_when_artifact_anchor_present(tmp_path):
    (tmp_path / "gate.json").write_text('{"case": {"H": 100.0}}')
    md = ("判据工件：`gate.json`。水深 H=100.0 m 取自 `gate.json#case.H`。\n\n"
          "| Metric | Value |\n|---|---|\n| 水深 | 100.0 |\n")
    out = check_manuscript(md, artifact_roots=[tmp_path])
    assert "PG7_no_artifact_provenance" not in [f["code"] for f in out["findings"]]


# ───────────────────────────── PG8 value-in-source ─────────────────────────────

def test_pg8_flags_number_absent_from_cited_report():
    md = ("| 结果 | 来源 |\n|---|---|\n"
          "| 色散残差 | Report #823 |\n\n"
          "| Numerical Value | Section | Source |\n|---|---|---|\n"
          "| 1.16 × 10⁻¹³ | §3.1 | Report #823 |\n")
    texts = {823: "报告正文：残差 4.20e-9，R²=0.9997。"}
    out = check_manuscript(md, report_ids=[823], report_texts=texts)
    f = [x for x in out["findings"] if x["code"] == "PG8_value_not_in_source"]
    assert f, "fabricated value must be caught"
    assert "1.16e-13" in f[0]["detail"]


def test_pg8_accepts_value_present_in_source():
    md = ("| Numerical Value | Section | Source |\n|---|---|---|\n"
          "| 1.16 × 10⁻¹³ | §3.1 | Report #823 |\n")
    texts = {823: "报告正文：色散残差 1.16 × 10⁻¹³，通过。"}
    out = check_manuscript(md, report_ids=[823], report_texts=texts)
    assert "PG8_value_not_in_source" not in [x["code"] for x in out["findings"]]
    assert out["stats"]["report_values"]["resolved"] == 1


def test_pg8_source_unavailable_is_warn_not_fail():
    md = ("| Numerical Value | Source |\n|---|---|\n| 0.5 | Report #823 |\n")
    out = check_manuscript(md, report_ids=[823], report_texts={})
    sev = {x["code"]: x["severity"] for x in out["findings"]}
    assert sev.get("PG8_source_unavailable") == "WARN"


# ───────────────────────────── end-to-end ─────────────────────────────

def test_clean_manuscript_passes(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "gate.json").write_text('{"cd": 0.0427}')
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    md = (
        "# 案例\n\n阻力 Cd 取自 `artifacts/gate.json#cd`，与试验 0.0420 vs 0.0427，误差 1.67%。\n\n"
        "![比较](figures/fig1.png)\n\n如 fig1 所示。溯源报告 #823。\n\n"
        "| 量 | 参考 | 数值 | 误差% |\n|---|---|---|---|\n| Cd | 0.0420 | 0.0427 | 1.6667 |\n"
    )
    out = check_manuscript(md, bundle=tmp_path, artifact_roots=[tmp_path],
                          report_ids=[823], report_texts={823: "Cd 0.0427 试验 0.0420"})
    assert out["verdict"] == "PASS", out["findings"]
    assert out["coverage"]["rate"] == 1.0


def test_verdict_fail_beats_warn():
    md = "报告 #1 不存在；`artifacts/missing.json` 不存在。"
    out = check_manuscript(md, artifact_roots=[Path("/nonexistent-root")], report_ids=[])
    assert out["verdict"] == "FAIL"