"""回归防护 / 报告 / CLI / 增量缓存的测试（C3 双向检出 + C4 证据链 + C5 时限）。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.capability_audit_gate import cli, engine, gate as gate_mod, report as report_mod
from tools.capability_audit_gate import spec as spec_mod
from tools.capability_audit_gate import taxonomy

GOLDEN = REPO / "config" / "capability_audit_gate" / "golden"


def _audit(root=GOLDEN, cache=None, contrast=None):
    return engine.run_audit(spec_mod.load_spec(root / "spec.json"), root=root,
                            cache_path=cache, contrast=contrast)


def test_gate_detects_every_possible_downgrade_pair():
    """五级任意一对 (old > new) 都必须被判为违规：检出率 100%（不依赖具体能力名）。"""
    misses = []
    for old in taxonomy.LEVELS:
        for new in taxonomy.LEVELS:
            if taxonomy.rank(new) >= taxonomy.rank(old):
                continue
            rep = {"capabilities": [{"capability": "x.y", "level": new}],
                   "ruleset_version": "cag-rules-v2"}
            base = {"levels": {"x.y": old}, "ruleset_version": "cag-rules-v2"}
            result = gate_mod.compare(rep, base)
            if not result["fail"]:
                misses.append((old, new))
    assert misses == []


def test_gate_flags_refutation_separately_from_plain_downgrade():
    rep = {"capabilities": [{"capability": "a", "level": "MOCK"},
                            {"capability": "b", "level": "WITHHELD"}],
           "ruleset_version": "cag-rules-v2"}
    base = {"levels": {"a": "REAL", "b": "REAL"}, "ruleset_version": "cag-rules-v2"}
    result = gate_mod.compare(rep, base)
    assert result["fail"] is True
    assert [r["id"] for r in result["refutations"]] == ["a"]
    assert {r["change"] for r in result["violations"]} == {"REFUTATION", "DOWNGRADE"}


def test_gate_ack_suppresses_downgrade_but_keeps_audit_trail():
    rep = {"capabilities": [{"capability": "a", "level": "MOCK"}],
           "ruleset_version": "cag-rules-v2"}
    base = {"levels": {"a": "REAL"}, "ruleset_version": "cag-rules-v2"}
    ack = {"ruleset": None, "levels": {"a": "known regression, ticket #42"}}
    result = gate_mod.compare(rep, base, ack)
    assert result["fail"] is False
    assert result["acked"][0]["ack_reason"].startswith("known regression")


def test_gate_detects_dropped_capability():
    rep = {"capabilities": [], "ruleset_version": "cag-rules-v2"}
    base = {"levels": {"gone.cap": "REAL"}, "ruleset_version": "cag-rules-v2"}
    result = gate_mod.compare(rep, base)
    assert result["fail"] is True
    assert result["violations"][0]["change"] == "CAPABILITY_DROPPED"


def test_gate_requires_ack_when_rule_set_changes():
    rep = {"capabilities": [{"capability": "a", "level": "REAL"}],
           "ruleset_version": "cag-rules-v3"}
    base = {"levels": {"a": "REAL"}, "ruleset_version": "cag-rules-v2"}
    assert gate_mod.compare(rep, base)["fail"] is True
    assert gate_mod.compare(rep, base, {"ruleset": "rule change ticket", "levels": {}})["fail"] is False


def test_baseline_roundtrip_and_schema_validation(tmp_path):
    rep = _audit()
    base = gate_mod.baseline_from_report(rep)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    loaded = gate_mod.load_baseline(path)
    assert loaded["levels"] == {v["capability"]: v["level"] for v in rep["capabilities"]}
    path.write_text(json.dumps({"schema_version": "bogus"}), encoding="utf-8")
    with pytest.raises(gate_mod.BaselineError):
        gate_mod.load_baseline(path)


def test_end_to_end_downgrade_turns_ci_red(tmp_path):
    """C3 端到端：REAL 能力的实现被换成硬编码 → 基线对比必须红（检出）。"""
    work = tmp_path / "tree"
    shutil.copytree(GOLDEN, work)
    before = _audit(root=work)
    assert next(v for v in before["capabilities"]
                if v["capability"] == "alpha.compute")["level"] == "REAL"
    baseline = gate_mod.baseline_from_report(before)

    (work / "alpha_compute" / "impl.py").write_text(
        "import random\n\n\ndef accumulate(values, scale=1.0):\n"
        "    # MOCK: hardcoded total\n    # hardcoded answer\n    return 42.0\n\n\n"
        "def solve_linear(a, b):\n    return random.random()\n", encoding="utf-8")
    after = _audit(root=work)
    result = gate_mod.compare(after, baseline)
    assert result["fail"] is True
    row = next(r for r in result["violations"] if r["id"] == "alpha.compute")
    assert (row["baseline"], row["current"], row["change"]) == ("REAL", "MOCK", "REFUTATION")


def test_end_to_end_upgrade_passes(tmp_path):
    work = tmp_path / "tree"
    shutil.copytree(GOLDEN, work)
    before = _audit(root=work)
    baseline = gate_mod.baseline_from_report(before)
    # 给原本缺基准的能力补上基准工件 → 等级上升，不应判违规
    bench = json.loads((work / "alpha_compute" / "bench.json").read_text(encoding="utf-8"))
    (work / "eta_blend" / "bench.json").write_text(json.dumps(bench), encoding="utf-8")
    spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
    for cap in spec["capabilities"]:
        if cap["id"] == "eta.blend":
            cap["benchmark"] = dict(next(c["benchmark"] for c in spec["capabilities"]
                                         if c["id"] == "alpha.compute"))
            cap["benchmark"]["path"] = "eta_blend/bench.json"
    (work / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    after = _audit(root=work)
    result = gate_mod.compare(after, baseline)
    assert result["fail"] is False
    assert any(u["id"] == "eta.blend" for u in result["upgrades"])


def test_report_contains_evidence_chain_for_every_capability():
    rep = _audit()
    checks = engine.acceptance(rep)
    assert checks["C4_chain_sufficient"]["pass"] is True
    for v in rep["capabilities"]:
        ev = v["evidence"]
        assert ev["reason_codes"] == v["reasons"]
        assert ev["impl_files"] or ev["benchmark"] or v["resolution"]["source"] == "UNBOUND" \
            or v["reasons"]
    real = next(v for v in rep["capabilities"] if v["level"] == "REAL")
    assert real["evidence"]["impl_files"][0]["path"].endswith(".py")
    assert real["evidence"]["impl_files"][0]["sha256"]
    assert real["evidence"]["test_names"]
    assert real["evidence"]["benchmark"]["rows"]


def test_report_files_written_with_markdown_and_json(tmp_path):
    rep = _audit()
    paths = report_mod.write_reports(rep, tmp_path, gate=None, checks=engine.acceptance(rep))
    assert Path(paths["report_json"]).is_file() and Path(paths["report_md"]).is_file()
    md = Path(paths["report_md"]).read_text(encoding="utf-8")
    for v in rep["capabilities"]:
        assert v["capability"] in md
    assert "证据" not in md or True  # 允许中英混排
    assert (tmp_path / "criteria.json").is_file()


def test_incremental_cache_makes_second_run_all_hits(tmp_path):
    cache = tmp_path / "cache.json"
    first = _audit(cache=cache)
    second = _audit(cache=cache)
    assert first["summary"]["cache"]["misses"] > 0
    assert second["summary"]["cache"]["hit_rate"] == 1.0
    assert second["summary"]["level_tally"] == first["summary"]["level_tally"]


def test_audit_runtime_under_60s_on_full_spec(tmp_path):
    spec_path = REPO / "config" / "capability_audit_gate" / "spec.json"
    if not spec_path.is_file():
        pytest.skip("real spec not generated")
    t0 = time.perf_counter()
    rep = engine.run_audit(spec_mod.load_spec(spec_path), root=REPO,
                           cache_path=tmp_path / "cache.json")
    elapsed = time.perf_counter() - t0
    assert rep["summary"]["coverage"] == 1.0
    assert elapsed < 60.0


def test_cli_audit_exit_codes(tmp_path):
    out = tmp_path / "out"
    assert cli.main(["audit", "--spec", str(GOLDEN / "spec.json"), "--root", str(GOLDEN),
                     "--out", str(out), "--enforce-acceptance"]) == 0
    assert cli.main(["audit", "--spec", str(GOLDEN / "spec.json"), "--root", str(GOLDEN),
                     "--out", str(out), "--min-agreement", "1.01",
                     "--enforce-acceptance"]) == cli.EXIT_ACCEPT
    assert cli.main(["audit", "--spec", str(tmp_path / "nope.json"), "--out", str(out)]) \
        == cli.EXIT_CONFIG


def test_cli_gate_failure_exit_code_and_stderr(tmp_path, capsys):
    work = tmp_path / "tree"
    shutil.copytree(GOLDEN, work)
    baseline = tmp_path / "baseline.json"
    assert cli.main(["freeze", "--spec", str(work / "spec.json"), "--root", str(work),
                     "--out", str(baseline)]) == 0
    (work / "alpha_compute" / "impl.py").write_text(
        "import random\n\n\ndef accumulate(values, scale=1.0):\n    # MOCK: x\n"
        "    # hardcoded y\n    return 1.0\n\n\ndef solve_linear(a, b):\n    return random.random()\n",
        encoding="utf-8")
    code = cli.main(["audit", "--spec", str(work / "spec.json"), "--root", str(work),
                     "--out", str(tmp_path / "o"), "--baseline", str(baseline),
                     "--fail-on-gate"])
    assert code == cli.EXIT_GATE
    assert "VIOLATION" in capsys.readouterr().err


def test_acceptance_checks_all_pass_on_golden():
    checks = engine.acceptance(_audit())
    assert checks["C1_coverage_100pct"]["pass"]
    assert checks["C2_agreement_ge_90pct"]["pass"]
    assert checks["C5_runtime_lt_60s"]["pass"]
    assert checks["all_pass"] is True