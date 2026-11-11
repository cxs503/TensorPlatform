"""Cross-repository scanner behaviour on synthetic repository trees."""
from __future__ import annotations

import json
from pathlib import Path

from tools.capability_evidence_chain import config as cfg_mod
from tools.capability_evidence_chain import scanner


def _scan(synth_config, synth_root):
    cfg = cfg_mod.load_config(synth_config, root=synth_root)
    return cfg, scanner.scan(cfg)


def test_scanner_discovers_globs_and_records_status(synth_config, synth_root):
    _, result = _scan(synth_config, synth_root)
    statuses = {(a.repo, a.path): a.status for a in result.artifacts}
    assert statuses[("synth_solver_a", "artifacts/case_01.json")] == "ok"
    assert statuses[("synth_solver_a", "artifacts/broken.json")] == "invalid_json"
    assert statuses[("synth_solver_b", "benchmarks/bench_01.json")] == "ok"
    # globs are respected: files outside include patterns are not scanned
    assert not any("acceptance_synth" in a.path for a in result.artifacts)


def test_scanner_records_sha256_and_sizes(synth_config, synth_root):
    _, result = _scan(synth_config, synth_root)
    rec = next(a for a in result.artifacts if a.path.endswith("case_01.json"))
    assert len(rec.sha256) == 64
    assert rec.bytes > 0


def test_scanner_matches_only_declared_patterns(synth_config, synth_root):
    _, result = _scan(synth_config, synth_root)
    keys = {o.key for o in result.observations}
    assert "rel_error" in keys and "mass_drift" in keys
    assert "iterations" not in keys            # numeric but not a declared metric
    labels = {lab for o in result.observations for lab in o.labels}
    assert "synth.alpha.relative_error:a" in labels


def test_scanner_is_deterministic(synth_config, synth_root):
    _, r1 = _scan(synth_config, synth_root)
    _, r2 = _scan(synth_config, synth_root)
    assert [(a.repo, a.path) for a in r1.artifacts] == [
        (a.repo, a.path) for a in r2.artifacts
    ]
    assert [(o.path, o.key_path) for o in r1.observations] == [
        (o.path, o.key_path) for o in r2.observations
    ]


def test_scanner_flags_oversized_files(synth_config, synth_root, monkeypatch):
    monkeypatch.setattr(scanner, "MAX_BYTES", 10)
    _, result = _scan(synth_config, synth_root)
    statuses = {a.status for a in result.artifacts}
    assert "too_large" in statuses


def test_walk_numeric_leaves_uses_nearest_key():
    payload = {"rows": [{"err": 0.1}, {"err": 0.2}], "meta": {"scale": 3}}
    found = [(k, v) for _, k, v in scanner.walk_numeric_leaves(payload)]
    assert found[:2] == [("err", 0.1), ("err", 0.2)]
    assert ("scale", 3.0) in found


def test_walk_numeric_leaves_skips_bools_and_nan():
    payload = {"ok": True, "bad": float("nan"), "good": 1.5}
    found = {k: v for _, k, v in scanner.walk_numeric_leaves(payload)}
    assert found == {"good": 1.5}


def test_scan_stats_aggregate_per_repo(synth_config, synth_root):
    _, result = _scan(synth_config, synth_root)
    stats = result.stats()
    assert stats["repos"] == 2
    assert stats["per_repo"]["synth_solver_a"]["invalid_json"] == 1
    assert stats["per_repo"]["synth_solver_b"]["files"] == 1


def test_repo_commit_of_non_git_dir_is_unknown(tmp_path: Path):
    assert scanner.repo_commit(tmp_path) == "unknown"


def test_missing_repo_root_is_recorded_not_crashing(synth_config, tmp_path):
    raw = json.loads(Path(synth_config).read_text())
    raw["repos"][0]["path"] = "does_not_exist"
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = cfg_mod.load_config(p, root=tmp_path)
    result = scanner.scan(cfg)
    assert result.commits["synth_solver_a"] == "missing"
    assert all(a.repo != "synth_solver_a" for a in result.artifacts)

def test_glob_matcher_handles_double_star_at_any_depth():
    m, seg = scanner._match_segments, scanner._segments
    assert m(seg("benchmarks/verified/deep/result.json"), seg("benchmarks/**/*.json"))
    assert m(seg("benchmarks/result.json"), seg("benchmarks/**/*.json"))
    assert m(seg("docs/a/b/c.json"), seg("docs/**/*.json"))
    assert not m(seg("docs/a/b/c.txt"), seg("docs/**/*.json"))
    assert not m(seg("other/a/b/c.json"), seg("docs/**/*.json"))


def test_nested_artefacts_are_discovered(synth_root):
    """Regression guard: a ``**`` bug once made nested benchmark dirs invisible."""
    from tools.capability_evidence_chain.config import RepoSpec

    nested = synth_root / "synth_solver_b" / "benchmarks" / "verified" / "deep"
    nested.mkdir(parents=True)
    (nested / "result.json").write_text(json.dumps({"rows": [{"rel_error": 0.02}]}),
                                        encoding="utf-8")
    repo = RepoSpec(name="synth_solver_b", path="synth_solver_b",
                    include=["benchmarks/**/*.json"])
    rels = [rel for rel, _ in scanner.iter_json_files(
        synth_root / "synth_solver_b", repo, 100)]
    assert "benchmarks/verified/deep/result.json" in rels
