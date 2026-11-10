"""跨域工件集成门 (tools/cross_domain_integration) 单元与集成测试.

全部夹具用**合成域**: 生产者 alpha/beta/gamma，量名 q_force/q_error/q_area，
单位 kN/N/%/1/m·rad。测试文件里不出现任何领域词条 (零领域语义纪律)，
领域内容只在 artifacts/ 下的 manifest JSON 里。

覆盖: 量纲代数 · manifest fail-closed · 工件盘点 · 信封契约 ·
legacy 元数据契约 · 跨生产者一致性 · 基线读数/比对/界增量 · 判据 · CLI 端到端.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.cross_domain_integration import MANIFEST_SCHEMA
from tools.cross_domain_integration.baseline import (bound_delta, compare,
                                                     extract_bounds, read_baseline)
from tools.cross_domain_integration.cli import main as cli_main
from tools.cross_domain_integration.contract import (check_declarations,
                                                     check_envelope,
                                                     check_quantities,
                                                     cross_producer,
                                                     legacy_contract_checks,
                                                     legacy_metadata, summarize)
from tools.cross_domain_integration.criteria import evaluate, i4_report_doc
from tools.cross_domain_integration.gate import run_gate, seal
from tools.cross_domain_integration.ingest import (git_facts, load_json,
                                                   resolve_pointer, scan_node)
from tools.cross_domain_integration.manifest import ManifestError, load_manifest
from tools.cross_domain_integration.provenance import sha256_obj
from tools.cross_domain_integration.units import (UnitError, convert,
                                                  dimension_signature,
                                                  parse_unit, same_dimension)

ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────── fixtures ─────────────────────────

def _manifest(nodes, tmp_path, *, baseline=True, applicable=None):
    doc = {
        "schema": MANIFEST_SCHEMA,
        "campaign": {"key": "waveX", "plan_id": 1, "wave": 2,
                     "baseline": {"key": "waveW", "plan_id": 0, "wave": 1}},
        "scan": {"work_root": str(tmp_path)},
        "contract": {"allowed_frames": ["none", "body", "earth"]},
        "doc": {"path": None,
                "sections": {"case": "Case", "method": "Method",
                             "results": "Results", "figures": "Figures",
                             "caveats": "Caveats", "error": "Error"}},
        "nodes": nodes,
    }
    if applicable:
        doc["criteria_applicable"] = applicable
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return load_manifest(p)


def _envelope(node_ext, mission, repo, quantities, *, schema="tp.cdi.envelope/v1",
              ts="2026-01-01T00:00:00Z", git_head="deadbeef", **extra):
    doc = {"schema": schema, "node_ext_id": node_ext, "mission_id": mission,
           "repo": repo, "git_head": git_head, "generated_utc": ts,
           "quantities": quantities, "artifacts": []}
    doc.update(extra)
    return doc


@pytest.fixture()
def synthetic_tree(tmp_path):
    """两个生产者: alpha 有信封+数据, beta 什么也没有。"""
    (tmp_path / "wt_alpha" / "artifacts" / "n01").mkdir(parents=True)
    env = _envelope("n01", 11, "repo-alpha", {
        "q_force": {"value": 12.5, "unit": "kN", "frame": "body"},
        "q_error": {"value": 0.4, "unit": "%", "frame": "none"},
    })
    (tmp_path / "wt_alpha" / "artifacts" / "n01" / "envelope.json").write_text(
        json.dumps(env), encoding="utf-8")
    (tmp_path / "wt_alpha" / "artifacts" / "n01" / "data.json").write_text(
        json.dumps({"schema": "x/v1", "rows": [1, 2]}), encoding="utf-8")
    (tmp_path / "wt_beta").mkdir()
    return tmp_path


def _nodes_two():
    return [
        {"ext_id": "n01", "mission_id": 11, "repo": "repo-alpha", "worktree": "wt_alpha",
         "label": "alpha node",
         "required_artifacts": ["artifacts/n01/envelope.json"],
         "expected_artifacts": ["artifacts/n01/*.json"],
         "quantities": [
             {"name": "q_force", "unit": "kN", "frame": "body", "tolerance_rel": 0.1,
              "adapter": None, "baseline": None, "note": ""},
             {"name": "q_error", "unit": "%", "frame": "none", "tolerance_rel": 0.2,
              "adapter": None,
              "baseline": {"worktree": "wt_base", "artifact": "artifacts/b.json",
                           "pointer": "/residual", "unit": "%", "value": 0.5},
              "note": ""}],
         "criteria_text": ["deviation < 3 %", "residual <= 1e-8"],
         "baseline_criteria_text": ["deviation < 5 %", "residual <= 1e-6"],
         "baseline_node": "w0"},
        {"ext_id": "n02", "mission_id": 12, "repo": "repo-beta", "worktree": "wt_beta",
         "label": "beta node",
         "required_artifacts": ["artifacts/n02/envelope.json"],
         "expected_artifacts": [],
         "quantities": [
             {"name": "q_error", "unit": "%", "frame": "none", "tolerance_rel": 0.2,
              "adapter": None, "baseline": None, "note": ""}],
         "criteria_text": [], "baseline_criteria_text": [], "baseline_node": None},
    ]


@pytest.fixture()
def baseline_tree(synthetic_tree):
    base = synthetic_tree / "wt_base" / "artifacts"
    base.mkdir(parents=True)
    (base / "b.json").write_text(json.dumps({"residual": 0.5}), encoding="utf-8")
    return synthetic_tree


# ───────────────────────── units ─────────────────────────

def test_dimension_algebra_basics():
    assert dimension_signature("m") == "L^1"
    assert dimension_signature("m^2/s") == "L^2 T^-1"
    assert same_dimension("kN", "N")
    assert not same_dimension("kN", "kg")
    dims, scale = parse_unit("kN")
    assert dims == {"L": 1, "M": 1, "T": -2}
    assert scale == pytest.approx(1e3)


def test_unit_conversion_and_composites():
    assert convert(3.0, "kN", "N") == pytest.approx(3000.0)
    assert convert(1.0, "%", "1") == pytest.approx(0.01)
    # 复合单位: m·rad 与 m 同量纲 (rad 无量纲)
    assert same_dimension("m·rad", "m")
    assert same_dimension("g/(t*nmi)", "1/m")


def test_unknown_unit_is_fail_closed():
    with pytest.raises(UnitError):
        parse_unit("furlong_per_fortnight")
    with pytest.raises(UnitError):
        convert(1.0, "kN", "kg")


# ───────────────────────── manifest fail-closed ─────────────────────────

def test_manifest_rejects_bad_schema(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema": "wrong/v9", "campaign": {}, "scan": {},
                             "nodes": []}), encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_manifest_rejects_unknown_unit_and_frame(tmp_path):
    node = {"ext_id": "n1", "mission_id": 1, "repo": "r", "worktree": "w",
            "quantities": [{"name": "q", "unit": "furlong", "frame": "none"}]}
    with pytest.raises(ManifestError):
        _manifest([node], tmp_path)
    node["quantities"][0]["unit"] = "m"
    node["quantities"][0]["frame"] = "mars-surface"
    with pytest.raises(ManifestError):
        _manifest([node], tmp_path)


def test_manifest_rejects_duplicate_ext_id(tmp_path):
    node = {"ext_id": "dup", "mission_id": 1, "repo": "r", "worktree": "w",
            "quantities": []}
    with pytest.raises(ManifestError):
        _manifest([dict(node), dict(node)], tmp_path)


def test_manifest_accepts_minimal_node(tmp_path):
    node = {"ext_id": "n1", "mission_id": 1, "repo": "r", "worktree": "w",
            "required_artifacts": ["a.json"], "expected_artifacts": [],
            "quantities": [{"name": "q", "unit": "m", "frame": "none"}]}
    m = _manifest([node], tmp_path)
    assert m["nodes"][0]["ext_id"] == "n1"
    assert m["criteria_applicable"] == ["I1", "I2", "I3", "I4", "I5"]


# ───────────────────────── ingest ─────────────────────────

def test_scan_node_counts_and_completeness(synthetic_tree):
    node = _nodes_two()[0]
    scan = scan_node(node, synthetic_tree)
    assert scan["complete"] is True
    assert scan["completeness"] == pytest.approx(1.0)
    assert {a["rel"] for a in scan["artifacts"]} == {
        "artifacts/n01/envelope.json", "artifacts/n01/data.json"}
    assert all(len(a["sha256"]) == 64 for a in scan["artifacts"])


def test_scan_node_missing_worktree(synthetic_tree):
    node = _nodes_two()[1]
    scan = scan_node(node, synthetic_tree)
    assert scan["complete"] is False
    assert scan["completeness"] == 0.0
    assert scan["required_missing"] == node["required_artifacts"]
    assert scan["git"]["head"] is None


def test_resolve_pointer_and_schema_error(tmp_path):
    doc = {"a": [{"b": 1.5}]}
    assert resolve_pointer(doc, "/a/0/b") == 1.5
    with pytest.raises(Exception):
        resolve_pointer(doc, "/a/1/b")


def test_git_facts_reads_head():
    facts = git_facts(ROOT)
    assert facts["head"] and len(facts["head"]) >= 8
    assert facts["branch"]


# ───────────────────────── envelope + declarations ─────────────────────────

def test_envelope_checks_pass_on_valid_bundle():
    node = {"ext_id": "n01", "mission_id": 11}
    env = _envelope("n01", 11, "repo-alpha", {})
    checks = check_envelope(env, node)
    assert summary_result(checks) == "PASS"


def summary_result(checks):
    bad = [c for c in checks if c["result"] == "FAIL"]
    return "FAIL" if bad else "PASS"


def test_envelope_checks_detect_binding_and_timestamp_errors():
    node = {"ext_id": "n01", "mission_id": 11}
    env = _envelope("n99", 12, "repo-alpha", {}, ts="2026-01-01 00:00:00")
    checks = {c["code"]: c for c in check_envelope(env, node)}
    assert checks["C1.node_binding"]["result"] == "FAIL"
    assert checks["C1.mission_binding"]["result"] == "FAIL"
    assert checks["C1.timestamp"]["result"] == "FAIL"


def test_declaration_checks_and_summary_rate():
    declared = [{"name": "q", "unit": "kN", "frame": "body",
                 "node_ext_id": "n1", "repo": "r1"},
                {"name": "q", "unit": "furlong", "frame": "none",
                 "node_ext_id": "n2", "repo": "r2"}]
    s = summarize(check_declarations(declared, ["none", "body"]))
    assert s["counts"]["FAIL"] == 1
    assert s["pass_rate"] == pytest.approx(3 / 4)


def test_quantity_checks_manifest_sourced_is_na():
    entry = {"name": "q", "unit": "kN", "unit_ref": "kN", "frame": "body",
             "node_ext_id": "n1", "unit_source": "manifest", "frame_source": "manifest"}
    s = summarize(check_quantities([entry], ["body"]))
    assert s["counts"]["NA"] == 3 and s["counts"]["FAIL"] == 0
    assert s["pass_rate"] is None


def test_quantity_checks_detect_dimension_mismatch():
    entry = {"name": "q", "unit": "kN", "unit_ref": "m", "frame": "body",
             "node_ext_id": "n1"}
    codes = {c["code"]: c["result"] for c in check_quantities([entry], ["body"])}
    assert codes["C2.unit_parse"] == "PASS"
    assert codes["C3.dimension"] == "FAIL"
    assert codes["C4.frame"] == "PASS"


# ───────────────────────── legacy contract ─────────────────────────

def test_legacy_contract_checks_flag_missing_metadata():
    checks = {c["code"]: c for c in legacy_contract_checks("a.json", {"rows": []})}
    assert checks["C6.schema"]["result"] == "FAIL"
    assert checks["C6.unit"]["result"] == "FAIL"
    assert checks["C6.timestamp"]["result"] == "FAIL"
    non_object = legacy_contract_checks("c.json", [1, 2, 3])
    assert non_object[0]["code"] == "C6.object" and non_object[0]["result"] == "FAIL"
    ok = {c["code"]: c for c in legacy_contract_checks(
        "b.json", {"schema": "x/v1", "mission_id": 3,
                   "generated_at": "2026-01-01T00:00:00Z", "unit": "m"})}
    assert all(c["result"] == "PASS" for c in ok.values())


def test_legacy_metadata_presence_rate():
    stats = legacy_metadata({"a.json": {"schema": "x"}, "b.json": {"rows": []},
                             "c.json": ["not", "an", "object"]})
    assert stats["count"] == 3
    assert stats["presence_rate"]["schema"] == pytest.approx(1 / 3)
    assert stats["presence_rate"]["unit"] == 0.0


# ───────────────────────── cross-producer ─────────────────────────

def test_cross_producer_prefix_variant_passes_with_warning():
    out = cross_producer([{"name": "q", "unit": "kN", "frame": "body",
                           "repo": "r1", "node_ext_id": "n1"},
                          {"name": "q", "unit": "N", "frame": "body",
                           "repo": "r2", "node_ext_id": "n2"}])[0]
    assert out["result"] == "PASS"
    assert out["scale_warning"] is True
    assert len(out["scale_variants"]) == 2


def test_cross_producer_frame_mismatch_fails():
    out = cross_producer([{"name": "q", "unit": "kN", "frame": "body",
                           "repo": "r1", "node_ext_id": "n1"},
                          {"name": "q", "unit": "kN", "frame": "earth",
                           "repo": "r2", "node_ext_id": "n2"}])[0]
    assert out["result"] == "FAIL"


def test_cross_producer_single_producer_is_na():
    out = cross_producer([{"name": "q", "unit": "kN", "frame": "body",
                           "repo": "r1", "node_ext_id": "n1"}])[0]
    assert out["result"] == "NA"


# ───────────────────────── baseline ─────────────────────────

def test_read_baseline_and_drift_detection(baseline_tree):
    q = {"name": "q_error", "unit": "%", "frame": "none",
         "baseline": {"worktree": "wt_base", "artifact": "artifacts/b.json",
                      "pointer": "/residual", "unit": "%", "value": 0.5}}
    base = read_baseline(q, baseline_tree)
    assert base["value"] == 0.5
    assert base["artifact_sha256"] and len(base["artifact_sha256"]) == 64
    assert base["drift"]["consistent"] is True

    q["baseline"]["value"] = 0.7
    base = read_baseline(q, baseline_tree)
    assert base["drift"]["consistent"] is False


def test_compare_verdicts():
    base = {"value": 0.5, "declared": True}
    assert compare(base, None, 0.1)["verdict"] == "WITHHELD"
    assert compare(base, {"value": 0.55}, 0.2)["verdict"] == "PASS"
    fail = compare(base, {"value": 0.9}, 0.2)
    assert fail["verdict"] == "FAIL" and fail["direction"] == "degraded"
    assert compare(base, {"value": 0.25}, 0.2)["direction"] == "improved"


def test_extract_bounds_and_delta():
    bounds = extract_bounds(["deviation < 3 % and residual <= 1e-8"])
    assert {(b["op"], b["value"]) for b in bounds} == {("<", 3.0), ("<=", 1e-08)}
    delta = bound_delta(["deviation < 5 %"], ["deviation < 3 %"])
    assert delta["comparable"] is True
    assert len(delta["tightened"]) == 1
    assert delta["tightened"][0]["rel_change"] == pytest.approx(-0.4)
    loosened = bound_delta(["deviation < 1 %"], ["deviation < 2 %"])
    assert len(loosened["loosened"]) == 1


# ───────────────────────── gate end-to-end ─────────────────────────

def test_gate_end_to_end(baseline_tree, tmp_path):
    manifest = _manifest(_nodes_two(), baseline_tree)
    body = run_gate(manifest, baseline_tree, "2026-01-01T00:00:00Z",
                    report_path=str(tmp_path / "r.json"), doc_path=None)
    nodes = {n["node_ext_id"]: n for n in body["nodes"]}
    assert nodes["n01"]["status"] == "PRESENT"
    assert nodes["n02"]["status"] == "MISSING"
    # 声明层契约 100% 一致 (q_error 在两个仓都是 %/none)
    assert body["contract"]["summary"]["pass_rate"] == pytest.approx(1.0)
    cross = {c["quantity"]: c for c in body["contract"]["cross_producer"]}
    assert cross["q_error"]["result"] == "PASS"
    assert set(cross["q_error"]["producers"]) == {"repo-alpha", "repo-beta"}
    # 基线: n01 的 q_error 当前 0.4 vs 基线 0.5 → 可比且衰减 20%
    q = next(b for b in body["baseline"]["quantities"] if b["name"] == "q_error")
    assert q["comparison"]["delta_rel"] == pytest.approx(-0.2)
    assert q["comparison"]["verdict"] == "PASS"
    crit = body["criteria"]["criteria"]
    assert crit["I1"]["pass"] is False       # beta 没交工件
    assert crit["I2"]["pass"] is True        # 声明契约一致
    assert crit["I3"]["pass"] is True        # 有一条可量化基线
    assert body["gaps"] and any(g["kind"] == "missing_artifacts" for g in body["gaps"])


def test_gate_is_deterministic(baseline_tree, tmp_path):
    m = _manifest(_nodes_two(), baseline_tree)
    a = run_gate(m, baseline_tree, "2026-01-01T00:00:00Z")
    b = run_gate(m, baseline_tree, "2026-01-01T00:00:00Z")
    assert sha256_obj(a) == sha256_obj(b)
    sealed = seal(a, [])
    assert sealed["seal"]["body_sha256"] == sha256_obj(a)


def test_gate_records_legacy_artifacts_without_envelope(tmp_path):
    wt = tmp_path / "wt_legacy"
    (wt / "artifacts").mkdir(parents=True)
    (wt / "artifacts" / "d.json").write_text(
        json.dumps({"mission_id": 7, "residual": 0.25}), encoding="utf-8")
    node = {"ext_id": "n09", "mission_id": 7, "repo": "repo-legacy",
            "worktree": "wt_legacy", "label": "legacy",
            "required_artifacts": ["artifacts/d.json"], "expected_artifacts": [],
            "quantities": [{"name": "q_res", "unit": "1", "frame": "none",
                            "adapter": {"artifact": "artifacts/d.json",
                                        "pointer": "/residual"}}],
            "criteria_text": [], "baseline_criteria_text": [], "baseline_node": None}
    body = run_gate(_manifest([node], tmp_path), tmp_path, "2026-01-01T00:00:00Z")
    n = body["nodes"][0]
    assert n["envelope"]["present"] is False
    assert n["quantities"][0]["value"] == 0.25
    codes = {c["code"]: c["result"] for c in body["contract"]["checks"]}
    assert codes["C6.schema"] == "FAIL"
    assert codes["C2.unit_parse"] == "NA"
    assert any(g["kind"] == "missing_envelope" for g in body["gaps"])


def test_adapter_scale_is_applied_and_recorded(tmp_path):
    wt = tmp_path / "wt_s"
    (wt / "artifacts").mkdir(parents=True)
    (wt / "artifacts" / "e.json").write_text(json.dumps({"frac": 0.025}), encoding="utf-8")
    node = {"ext_id": "n10", "mission_id": 10, "repo": "repo-s", "worktree": "wt_s",
            "required_artifacts": ["artifacts/e.json"], "expected_artifacts": [],
            "quantities": [{"name": "q_error", "unit": "%", "frame": "none",
                            "adapter": {"artifact": "artifacts/e.json",
                                        "pointer": "/frac", "scale": 100.0,
                                        "unit_in_artifact": "1"}}],
            "criteria_text": [], "baseline_criteria_text": [], "baseline_node": None}
    body = run_gate(_manifest([node], tmp_path), tmp_path, "2026-01-01T00:00:00Z")
    q = body["nodes"][0]["quantities"][0]
    assert q["raw_value"] == pytest.approx(0.025)
    assert q["value"] == pytest.approx(2.5)
    assert q["scale_applied"] == 100.0
    assert q["unit_in_artifact"] == "1"


def test_stale_artifact_is_excluded_by_freshness(tmp_path):
    """工作树里上一波的同名工件不得计入本波完整性。"""
    wt = tmp_path / "wt_stale" / "artifacts"
    wt.mkdir(parents=True)
    (wt / "old.json").write_text(json.dumps({"v": 1}), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(wt.parent), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(wt.parent), check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-qm", "old"], cwd=str(wt.parent), check=True)
    node = {"ext_id": "n11", "mission_id": 11, "repo": "repo-t", "worktree": "wt_stale",
            "required_artifacts": ["artifacts/old.json"], "expected_artifacts": [],
            "quantities": []}
    old_scan = scan_node(node, tmp_path, started_utc="2999-01-01T00:00:00Z")
    assert old_scan["complete"] is False
    assert old_scan["stale_excluded"] == ["artifacts/old.json"]
    no_rule = scan_node(node, tmp_path, started_utc=None)
    assert no_rule["complete"] is True


# ───────────────────────── criteria + CLI ─────────────────────────

def test_i4_detects_missing_sections(tmp_path):
    doc = tmp_path / "r.md"
    doc.write_text("# Case\n## Method\n## Results\n## Figures\n", encoding="utf-8")
    sections = {"case": "Case", "method": "Method", "results": "Results",
                "figures": "Figures", "caveats": "Caveats", "error": "Error"}
    out = i4_report_doc(doc, sections)
    assert out["pass"] is False
    assert set(out["sections"]) == set(sections)
    assert out["detail"].count("caveats") + out["detail"].count("error") >= 1


def test_evaluate_marks_inapplicable_criteria(tmp_path):
    res = evaluate(nodes=[], contract_summary={"counts": {"PASS": 1, "FAIL": 0, "NA": 0},
                                               "pass_rate": 1.0, "violations": []},
                   quantities=[], doc_path=None, doc_sections={}, report_path=None,
                   report_sha256=None, figures=[], applicable=["I2"])
    assert res["criteria"]["I2"]["pass"] is True
    assert res["criteria"]["I1"]["applicable"] is False
    assert res["criteria"]["I1"]["pass"] is None
    assert res["verdict"] == "PASS" and res["failed"] == []


def test_cli_writes_report_and_strict_exit_code(baseline_tree, tmp_path):
    _manifest(_nodes_two(), baseline_tree)
    out = tmp_path / "report.json"
    code = cli_main(["--manifest", str(baseline_tree / "manifest.json"),
                     "--out", str(out), "--figures-dir", str(tmp_path / "figs"),
                     "--generated-utc", "2026-01-01T00:00:00Z"])
    assert code == 0
    data = load_json(out)
    assert data["seal"]["body_sha256"]
    assert len(data["figures"]) == 3
    assert all(f["sha256"] for f in data["figures"])
    strict = cli_main(["--manifest", str(baseline_tree / "manifest.json"),
                       "--out", str(out), "--generated-utc", "2026-01-01T00:00:00Z",
                       "--strict"])
    assert strict == 3   # I1 未满足 (beta 缺工件) → strict 非零退出


def test_cli_reports_manifest_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
    assert cli_main(["--manifest", str(bad), "--out", str(tmp_path / "o.json")]) == 2


# ───────────────────────── docrender (数字注入) ─────────────────────────

def test_docrender_flatten_and_render():
    from tools.cross_domain_integration.docrender import flatten, render
    values = flatten({"a": {"b": 1.23456}, "l": [{"v": 2}], "ok": True})
    assert values["a.b"] == pytest.approx(1.23456)
    assert values["l.0.v"] == 2
    text = render("x={{a.b|.2f}} y={{l.0.v}} z={{ok}}", values)
    assert text == "x=1.23 y=2 z=PASS"


def test_docrender_handles_escaped_pipe_in_tables():
    from tools.cross_domain_integration.docrender import flatten, render
    values = flatten({"q": {"rate": 0.5}})
    assert render("| {{q.rate\|.2f}} |", values) == "| 0.50 |"


def test_docrender_fails_closed_on_unknown_key():
    from tools.cross_domain_integration.docrender import TemplateError, render
    with pytest.raises(TemplateError):
        render("{{missing.key}}", {"present": 1})


def test_docrender_escapes_none_as_withheld():
    from tools.cross_domain_integration.docrender import render
    assert render("v={{x}}", {"x": None}) == "v=WITHHELD"


def test_module_runs_as_script(baseline_tree, tmp_path):
    """`python -m tools.cross_domain_integration.cli` 可执行 (部署面冒烟)。"""
    _manifest(_nodes_two(), baseline_tree)
    proc = subprocess.run(
        [sys.executable, "-m", "tools.cross_domain_integration.cli",
         "--manifest", str(baseline_tree / "manifest.json"),
         "--out", str(tmp_path / "r2.json"),
         "--generated-utc", "2026-01-01T00:00:00Z"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert "verdict" in proc.stdout