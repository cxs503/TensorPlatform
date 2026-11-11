#!/usr/bin/env python3
"""KPI-TRACE-100 工具模块测试（合成数据模式）。

覆盖：
- synthetic 数据生成一致性
- measure 计算正确性
- 端到端 run-all 流程
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.kpi_trace.synthetic import generate_all, generate_terminal_missions, generate_reports_facts
from tools.kpi_trace.measure import compute_kpi, read_csv


class TestSynthetic:
    """合成数据生成测试。"""

    def test_generate_terminal_missions_count(self):
        rows = generate_terminal_missions(n=1000, traceable_n=500, seed=42)
        assert len(rows) == 1000
        traceable = sum(1 for r in rows if r["link_report_row"] == "t" or r["link_report_id"] == "t")
        assert traceable == 500

    def test_generate_terminal_missions_statuses(self):
        rows = generate_terminal_missions(n=1000, traceable_n=500, seed=42)
        statuses = set(r["status"] for r in rows)
        assert statuses == {"succeeded", "failed", "stopped", "revoked"}

    def test_generate_all_produces_csv(self):
        with tempfile.TemporaryDirectory() as td:
            result = generate_all(Path(td), n_terminal=500, n_traceable=250, seed=42)
            assert result["terminal"] == 500
            assert result["traceable"] == 250
            assert Path(result["terminal_csv"]).exists()
            assert Path(result["reports_csv"]).exists()

    def test_reproducibility(self):
        """相同 seed + 相同 now 产生相同数据。"""
        now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)
        rows1 = generate_terminal_missions(n=200, traceable_n=100, seed=123, now=now)
        rows2 = generate_terminal_missions(n=200, traceable_n=100, seed=123, now=now)
        assert rows1 == rows2

    def test_different_seed_different_data(self):
        rows1 = generate_terminal_missions(n=200, traceable_n=100, seed=1)
        rows2 = generate_terminal_missions(n=200, traceable_n=100, seed=2)
        assert rows1 != rows2


class TestComputeKPI:
    """KPI 计算测试。"""

    def _make_csv(self, tmpdir, rows, name):
        import csv
        path = Path(tmpdir) / name
        if rows:
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        else:
            path.write_text("", encoding="utf-8")
        return path

    def test_basic_computation(self, tmp_path):
        terminal = [
            {"id": "1", "status": "succeeded", "repo": "TensorPlatform", "campaign_id": "1",
             "report_owed": "t", "link_report_id": "t", "link_report_row": "t",
             "created_at": "2026-11-01 00:00:00.000000+00", "finished_at": "2026-11-01 01:00:00.000000+00",
             "evidence_items": "3"},
            {"id": "2", "status": "succeeded", "repo": "TensorPlatform", "campaign_id": "1",
             "report_owed": "t", "link_report_id": "f", "link_report_row": "f",
             "created_at": "2026-11-01 00:00:00.000000+00", "finished_at": "2026-11-01 01:00:00.000000+00",
             "evidence_items": "0"},
            {"id": "3", "status": "failed", "repo": "TensorFEM", "campaign_id": "",
             "report_owed": "f", "link_report_id": "f", "link_report_row": "t",
             "created_at": "2026-11-01 00:00:00.000000+00", "finished_at": "2026-11-01 01:00:00.000000+00",
             "evidence_items": "1"},
            {"id": "4", "status": "stopped", "repo": "TensorLBM", "campaign_id": "",
             "report_owed": "f", "link_report_id": "f", "link_report_row": "f",
             "created_at": "2026-11-01 00:00:00.000000+00", "finished_at": "2026-11-01 01:00:00.000000+00",
             "evidence_items": "0"},
        ]
        reports = [
            {"id": "100", "mission_id": "1", "qa_status": "passed", "status": "published",
             "no_mission_id": "f", "mission_missing": "f", "mission_status": "succeeded",
             "created_at": "2026-11-01 02:00:00.000000+00"},
            {"id": "101", "mission_id": "3", "qa_status": "pending", "status": "published",
             "no_mission_id": "f", "mission_missing": "f", "mission_status": "failed",
             "created_at": "2026-11-01 02:00:00.000000+00"},
        ]

        term_csv = self._make_csv(tmp_path, terminal, "terminal.csv")
        rep_csv = self._make_csv(tmp_path, reports, "reports.csv")

        result = compute_kpi(term_csv, rep_csv)

        assert result["denominator"] == 4
        # id=1: link_report_id=t → traceable; id=3: link_report_row=t → traceable
        assert result["numerator"] == 2
        assert result["current"] == 50.0
        assert result["gap"] == 50.0

    def test_empty_input(self, tmp_path):
        term_csv = self._make_csv(tmp_path, [], "terminal.csv")
        rep_csv = self._make_csv(tmp_path, [], "reports.csv")

        result = compute_kpi(term_csv, rep_csv)
        assert result["denominator"] == 0
        assert result["numerator"] == 0
        assert result["current"] is None
        assert result["gap"] is None

    def test_all_traceable(self, tmp_path):
        terminal = [
            {"id": str(i), "status": "succeeded", "repo": "Test", "campaign_id": "1",
             "report_owed": "t", "link_report_id": "t", "link_report_row": "t",
             "created_at": "2026-11-01 00:00:00.000000+00", "finished_at": "2026-11-01 01:00:00.000000+00",
             "evidence_items": "1"}
            for i in range(10)
        ]
        term_csv = self._make_csv(tmp_path, terminal, "terminal.csv")
        rep_csv = self._make_csv(tmp_path, [], "reports.csv")

        result = compute_kpi(term_csv, rep_csv)
        assert result["denominator"] == 10
        assert result["numerator"] == 10
        assert result["current"] == 100.0
        assert result["gap"] == 0.0

    def test_none_traceable(self, tmp_path):
        terminal = [
            {"id": str(i), "status": "succeeded", "repo": "Test", "campaign_id": "1",
             "report_owed": "t", "link_report_id": "f", "link_report_row": "f",
             "created_at": "2026-11-01 00:00:00.000000+00", "finished_at": "2026-11-01 01:00:00.000000+00",
             "evidence_items": "0"}
            for i in range(5)
        ]
        term_csv = self._make_csv(tmp_path, terminal, "terminal.csv")
        rep_csv = self._make_csv(tmp_path, [], "reports.csv")

        result = compute_kpi(term_csv, rep_csv)
        assert result["denominator"] == 5
        assert result["numerator"] == 0
        assert result["current"] == 0.0
        assert result["gap"] == 100.0


class TestEndToEnd:
    """端到端流程测试。"""

    def test_run_all_synthetic(self, tmp_path):
        """验证 run-all 流程产生所有预期工件。"""
        from tools.kpi_trace.entry import cmd_run_all
        import argparse

        outdir = tmp_path / "artifacts"
        doc_out = tmp_path / "docs" / "report.md"

        args = argparse.Namespace(
            outdir=str(outdir),
            synthetic=True,
            dsn=None,
            seed=42,
            now=None,
            doc_out=str(doc_out),
        )

        ret = cmd_run_all(args)
        assert ret == 0

        # 验证所有工件存在
        assert (outdir / "result.json").exists()
        assert (outdir / "terminal_missions.csv").exists()
        assert (outdir / "reports_facts.csv").exists()
        assert (outdir / "kpi_trace_100_queries.sql").exists()
        assert (outdir / "fig_1_gap.png").exists()
        assert (outdir / "fig_2_by_status.png").exists()
        assert (outdir / "fig_3_weekly.png").exists()
        assert (outdir / "fig_4_repo_7d.png").exists()
        assert (outdir / "fig_5_debt.png").exists()
        assert doc_out.exists()

        # 验证 result.json 内容完整性
        res = json.loads((outdir / "result.json").read_text())
        assert res["kpi"] == "KPI-TRACE-100"
        assert res["data_mode"] == "synthetic"
        assert res["current"] is not None
        assert res["denominator"] > 0
        assert len(res["by_status"]) == 4
        assert len(res["weekly_trend"]) > 0
        assert len(res["top10_untraceable"]) == 10
        assert len(res["proposed_anchors"]) == 6
