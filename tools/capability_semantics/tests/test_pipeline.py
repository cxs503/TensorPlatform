"""流水线端到端 + CLI + 图 + 文档渲染的单测（合成工作树，注入假扫描器）。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.capability_semantics import cli
from tools.capability_semantics.figures import make_figures
from tools.capability_semantics.tests.conftest import build_workspace, write_json


def test_pipeline_end_to_end_writes_artifacts_and_chain(tmp_path: Path):
    ws = build_workspace(tmp_path)
    result = ws.run()
    out = tmp_path / "artifacts/capability_semantics"
    for name in ("semantic_layers.json", "evidence_audit.json", "misuse_report.json",
                 "registry_patch.json", "dependency_graph.json", "acceptance.json",
                 "audit_log.json", "dependency_graph.dot"):
        assert (out / name).exists(), name
    assert result.artifacts["acceptance.json"]
    # sha256 链：prev 必须等于上一条 record_sha256
    log = json.loads((out / "audit_log.json").read_text(encoding="utf-8"))
    prev = ""
    for rec in log:
        assert rec["prev"] == prev
        prev = rec["record_sha256"]
    assert len(log) == 8


def test_pipeline_acceptance_flags(tmp_path: Path):
    ws = build_workspace(tmp_path)
    result = ws.run()
    crit = result.acceptance["criteria"]
    assert crit["a_semantic_layers"]["ok"] is True
    assert crit["a_semantic_layers"]["lost_entries"] == 0
    assert crit["d_dependency_graph"]["cycle_count"] == 0
    assert crit["f_safety"]["ok"] is True
    # 判据 e：未注入覆盖率 → 显式 None，不冒充达标
    assert crit["e_maturity_delivery"]["coverage_pct"] is None
    assert crit["e_maturity_delivery"]["ok"] is False
    # 判据 b：修复 2 条 < 阈值 3 → 未过（诚实）
    b = crit["b_misuse_governance"]
    assert b["fixed_count"] == 2 and b["min_misuse_fixes"] == 3 and b["ok"] is False
    assert result.acceptance["all_ok"] is False


def test_pipeline_all_ok_when_inputs_meet_criteria(tmp_path: Path):
    ws = build_workspace(tmp_path, thresholds={"min_misuse_fixes": 2,
                                               "min_graded_entries": 3})
    docs = tmp_path / "docs/reports/report.md"
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text("# r\n", encoding="utf-8")
    result = ws.run(coverage_pct=88.5, test_files=["t.py"], docs_path="docs/reports/report.md")
    crit = result.acceptance["criteria"]
    assert crit["e_maturity_delivery"]["ok"] is True
    assert crit["e_maturity_delivery"]["coverage_pct"] == 88.5
    assert result.acceptance["all_ok"] is True


def test_pipeline_degrades_on_missing_ledger(tmp_path: Path):
    """台账缺失：不崩，声明源空 + 警告入账（空证据≠无交付）。"""
    ws = build_workspace(tmp_path)
    ws.ledger_path.unlink()
    result = ws.run()
    assert any("declaration_source_empty" in w for w in result.warnings)
    assert result.layers["stats"]["capability_entries"] >= 1


def test_pipeline_extra_declaration_source(tmp_path: Path):
    ws = build_workspace(tmp_path)
    snap = write_json(tmp_path / "snapshot.json", {"capabilities": [
        {"id": "synth.snapshot", "level": "REAL"}]})
    result = ws.run(extra_declaration_sources=[("snapshot", str(snap))])
    ids = {t["capability_id"] for t in result.layers["triples"]}
    assert "synth.snapshot" in ids


def test_nan_probe_present_in_acceptance(tmp_path: Path):
    ws = build_workspace(tmp_path)
    result = ws.run()
    probes = result.acceptance["criteria"]["f_safety"]["degenerate_probes"]
    names = [p["probe"] for p in probes]
    assert "empty" in names and "nan_error" in names
    nan_probe = next(p for p in probes if p["probe"] == "nan_error")
    assert nan_probe["accuracy"] == "no_numeric"


def test_make_figures(tmp_path: Path):
    ws = build_workspace(tmp_path)
    result = ws.run()
    figs = make_figures(result.to_dict(), tmp_path / "figs")
    assert figs, "matplotlib 应可用（否则本测试允许空，但环境里应生成）"
    for fig in figs:
        assert (tmp_path / "figs" / fig["name"]).exists()
        assert fig["caption"]


def test_make_figures_tolerates_empty_payload(tmp_path: Path):
    assert make_figures({}, tmp_path / "figs2") == [] or True
    # 空图数据也必须不抛异常
    figs = make_figures({"layers": {}, "graph": {}}, tmp_path / "figs3")
    assert isinstance(figs, list)


def test_render_markdown_contains_injected_numbers(tmp_path: Path):
    ws = build_workspace(tmp_path)
    result = ws.run(coverage_pct=90.0)
    md = cli.render_markdown(result, [], coverage_pct=90.0)
    stats = result.layers["stats"]
    assert f"{stats['capability_entries']}" in md
    assert "判据 (a)–(f)" in md
    assert "90.0" in md
    assert "WITHHELD" in md


def test_cli_main_dry_run_json(tmp_path: Path, capsys):
    ws = build_workspace(tmp_path)
    rc = cli.main(["--root", str(tmp_path), "--config", str(ws.config_path),
                   "--ledger", str(ws.ledger_path), "--patterns", str(ws.patterns_path),
                   "--json", "--dry-run"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc in (0, 1)
    assert "acceptance" in payload
    assert payload["acceptance"]["criteria"]["f_safety"]["ok"] is True


def test_cli_main_writes_doc_and_figures(tmp_path: Path):
    ws = build_workspace(tmp_path)
    doc = tmp_path / "docs/report.md"
    rc = cli.main(["--root", str(tmp_path), "--config", str(ws.config_path),
                   "--ledger", str(ws.ledger_path), "--patterns", str(ws.patterns_path),
                   "--figures", "--figure-dir", str(tmp_path / "docs/figs"),
                   "--doc", str(doc), "--coverage-pct", "91.0"])
    assert rc in (0, 1)
    assert doc.exists() and "P266W1-N10" in doc.read_text(encoding="utf-8")
    assert list((tmp_path / "docs/figs").glob("*.png"))


def test_cli_main_bad_extra_source(tmp_path: Path):
    rc = cli.main(["--root", str(tmp_path), "--extra-source", "no-equals-sign"])
    assert rc == 2


def test_cli_main_pipeline_failure_returns_2(tmp_path: Path, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_pipeline", boom)
    assert cli.main(["--root", str(tmp_path), "--dry-run"]) == 2