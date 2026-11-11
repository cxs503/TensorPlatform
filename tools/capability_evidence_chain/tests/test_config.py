"""Configuration loading and fail-fast validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.capability_evidence_chain import config as cfg_mod


def test_load_config_reads_repos_and_capabilities(synth_config, synth_root):
    cfg = cfg_mod.load_config(synth_config, root=synth_root)
    assert [r.name for r in cfg.repos] == ["synth_solver_a", "synth_solver_b"]
    assert len(cfg.capabilities) == 4
    assert cfg.capabilities[0].scenario == "mean"
    assert cfg.reclassification is not None
    assert cfg.root == synth_root


def test_load_config_missing_file_raises(tmp_path: Path):
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(tmp_path / "nope.json")


def test_load_config_invalid_json_raises(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(p)


def test_missing_capability_group_raises(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["capabilities"][0]["groups"] = {}
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(p, root=synth_root)


def test_unknown_scenario_is_rejected(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["capabilities"][0]["scenario"] = "magic"
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(p, root=synth_root)


def test_two_group_scenario_requires_group_b(synth_config, synth_root, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["capabilities"][2]["groups"].pop("b")
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(p, root=synth_root)


def test_repo_lookup_by_name(synth_config, synth_root):
    cfg = cfg_mod.load_config(synth_config, root=synth_root)
    assert cfg.repo("synth_solver_b").path == "synth_solver_b"
    with pytest.raises(cfg_mod.ConfigError):
        cfg.repo("does_not_exist")


def test_value_bounds_parsed(synth_config, synth_root):
    cfg = cfg_mod.load_config(synth_config, root=synth_root)
    lo, hi = cfg.capabilities[0].group("a").value_bounds
    assert (lo, hi) == (0.0, 1.0)
    assert cfg.capabilities[2].theta == 2.0