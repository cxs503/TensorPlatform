"""Binding: artefact -> capability samples, association rate, MDE table."""
from __future__ import annotations

import json
from pathlib import Path

from tools.capability_evidence_chain import binder
from tools.capability_evidence_chain import config as cfg_mod
from tools.capability_evidence_chain import scanner


def _run(synth_config, synth_root):
    cfg = cfg_mod.load_config(synth_config, root=synth_root)
    scan_result = scanner.scan(cfg)
    samples = binder.collect_samples(cfg, scan_result)
    return cfg, scan_result, samples


def test_collect_samples_groups_by_capability(synth_config, synth_root):
    cfg, _, samples = _run(synth_config, synth_root)
    a = samples[("synth.alpha.relative_error", "a")]
    assert a.values == [0.010, 0.020, 0.015, 0.0125, 0.0300]
    assert a.n_available == 5 and not a.truncated
    b = samples[("synth.ab.error_distribution", "b")]
    assert len(b.values) == 3


def test_value_range_filters_and_counts(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["capabilities"][0]["groups"]["a"]["value_range"] = [0.0, 0.015]
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg, _, samples = _run(p, synth_root)
    s = samples[("synth.alpha.relative_error", "a")]
    assert s.values == [0.010, 0.015, 0.0125]
    assert s.out_of_range == 2


def test_sample_truncation_is_flagged(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["capabilities"][0]["groups"]["a"]["max_samples"] = 2
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg, _, samples = _run(p, synth_root)
    s = samples[("synth.alpha.relative_error", "a")]
    assert len(s.values) == 2 and s.truncated and s.n_available == 5


def test_association_rate_on_synthetic_chain(synth_config, synth_root):
    cfg, scan_result, samples = _run(synth_config, synth_root)
    assoc = binder.associate(cfg, scan_result, samples)
    assert assoc["recognized_files"] == 3     # case_01, case_02, bench_01
    assert assoc["bound_files"] == 3
    assert assoc["association_rate"] == 1.0
    assert assoc["n_unbound"] == 0
    assert assoc["bound_by_repo"] == {"synth_solver_a": 2, "synth_solver_b": 1}


def test_association_reports_value_range_unbound(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    # exclude every value of the only metric bound to repo a
    for cap in raw["capabilities"]:
        for grp in cap["groups"].values():
            if grp["repo"] == "synth_solver_a":
                grp["value_range"] = [10.0, 20.0]
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg, scan_result, samples = _run(p, synth_root)
    assoc = binder.associate(cfg, scan_result, samples)
    reasons = {u["reason"] for u in assoc["unbound"]}
    assert assoc["bound_files"] < assoc["recognized_files"]
    assert any("value range" in r for r in reasons)


def test_evaluate_produces_all_three_scenarios(synth_config, synth_root):
    cfg, _, samples = _run(synth_config, synth_root)
    rows = binder.evaluate(cfg, samples, ks_boot=200, sensitivity=False)
    scenarios = {r["scenario"] for r in rows}
    assert scenarios == {"mean", "variance", "distribution"}
    for r in rows:
        assert r["decision"] in ("EQUIVALENT", "NOT_EQUIVALENT",
                                 "WITHHELD_NO_DATA", "WITHHELD_SMALL_SAMPLE",
                                 "WITHHELD_NO_SPREAD")
        assert "margin" in r


def test_evaluate_flags_missing_data_as_withheld(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["capabilities"].append({
        "id": "synth.empty", "scenario": "mean", "margin": 0.1, "unit": "relative",
        "groups": {"a": {"repo": "synth_solver_a", "include": ["artifacts/**/*.json"],
                         "metric": "^never_present$"}},
    })
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg, _, samples = _run(p, synth_root)
    rows = binder.evaluate(cfg, samples, ks_boot=100, sensitivity=False)
    empty = next(r for r in rows if r["capability"] == "synth.empty")
    assert empty["decision"] == "WITHHELD_NO_DATA"
    assert empty["mde"] is None


def test_mde_table_has_one_row_per_capability(synth_config, synth_root):
    cfg, _, samples = _run(synth_config, synth_root)
    rows = binder.evaluate(cfg, samples, ks_boot=200, sensitivity=False)
    table = binder.mde_table(rows)
    assert len(table) == len(cfg.capabilities)
    assert {"capability", "scenario", "n", "mde", "power", "decision"} <= set(table[0])
    underpowered = [r for r in table if r["underpowered"]]
    assert all(isinstance(r["underpowered"], bool) for r in table)

def test_ledger_proposals_cover_all_four_outcomes():
    results = [
        {"capability": "synth.a", "scenario": "mean", "decision": "EQUIVALENT",
         "power": 1.0, "mde": 0.14, "margin": 0.15, "groups": {"a": {"files": 3}}},
        {"capability": "synth.b", "scenario": "mean", "decision": "EQUIVALENT",
         "power": 0.4, "mde": 0.02, "margin": 0.15, "groups": {"a": {"files": 1}}},
        {"capability": "synth.c", "scenario": "mean", "decision": "NOT_EQUIVALENT",
         "power": 0.1, "mde": 0.05, "margin": 0.15, "groups": {"a": {"files": 2}}},
        {"capability": "synth.d", "scenario": "mean", "decision": "WITHHELD_NO_DATA",
         "power": None, "mde": None, "margin": 0.15, "groups": {"a": {"files": 0}}},
    ]
    actions = [p["action"] for p in binder.ledger_proposals(results)]
    assert actions == ["UPGRADE_TO_REAL", "RETAIN_WITHHELD_LOW_POWER",
                       "DOWNGRADE_OR_FIX", "RETAIN_WITHHELD_NO_DATA"]
    levels = [p["proposed_level"] for p in binder.ledger_proposals(results)]
    assert levels == ["REAL", "WITHHELD", "MOCK", "WITHHELD"]
