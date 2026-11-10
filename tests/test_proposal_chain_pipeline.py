"""管线集成测试（合成域名 + 临时目录，零领域语义；不联网）。"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tools.proposal_chain import node_schema, pipeline
from tools.proposal_chain.config import load_config

SYNTH_NODES = [
    {"id": 1, "plan_id": 11, "name": "zeta widget solver",
     "metadata": {"ext_id": "n1", "repo": "RepoA", "goal": "solver g",
                  "acceptance_criteria": ["err ≤ 3 %"]}},
    {"id": 2, "plan_id": 12, "name": "kilo pump curve",
     "metadata": {"ext_id": "n2", "repo": "RepoB", "goal": "pump g"}},
    {"id": 3, "plan_id": 13, "name": "orphan node", "metadata": None},
]


@pytest.fixture()
def synth_cfg(tmp_path: Path):
    art = tmp_path / "artifacts"
    art.mkdir()
    snap = art / "nodes_snapshot.json.gz"
    with gzip.open(snap, "wb") as fh:
        fh.write(json.dumps({"items": SYNTH_NODES}).encode())
    (art / "proposal_chain").mkdir()
    cfg = load_config(
        overrides={
            "repo_whitelist": ["RepoA", "RepoB"],
            "paths": {
                "artifacts_dir": str(art / "proposal_chain"),
                "nodes_snapshot": str(art / "nodes_snapshot.json"),
                "corpus_gz": str(art / "proposal_chain" / "corpus.json.gz"),
                "docs_path": str(tmp_path / "report.md"),
                "proposals_root": str(tmp_path / "no_such_pool"),
            },
            "coverage": {
                "gap_ledger_path": str(tmp_path / "no_ledger.json"),
                "gap_ledger_source": str(tmp_path / "no_panorama.md"),
            },
            "labels": {"path": str(tmp_path / "no_labels.json")},
        }
    )
    return cfg


def test_stage_schema_writes_validation_artifact(synth_cfg):
    payload = pipeline.stage_schema(synth_cfg)
    assert payload["full"]["total"] == 3
    assert payload["full"]["degraded_count"] >= 1
    assert (synth_cfg.path_of("artifacts_dir") / "schema_validation.json").exists()


def test_stage_negative_all_pass(synth_cfg):
    payload = pipeline.stage_negative(synth_cfg)
    assert payload["all_pass"] is True
    assert payload["checks"]["empty_pool"]["ok"] is True


def test_stage_ledger_handles_missing_source(synth_cfg):
    payload = pipeline.stage_ledger(synth_cfg)
    assert payload["meta"]["available"] is False
    assert payload["items"] == []


def test_load_nodes_for_schema_reads_gzip_snapshot(synth_cfg):
    rows = pipeline.load_nodes_for_schema(synth_cfg)
    assert len(rows) == 3
    batch = node_schema.validate_batch(rows, synth_cfg.raw)
    assert batch.total == 3 and batch.missed_count == 0


def test_stage_dedup_on_empty_pool(synth_cfg):
    payload = pipeline.stage_dedup(synth_cfg)
    assert payload["proposals"]["stats"]["doc_count"] == 0
    assert payload["nodes"]["stats"]["doc_count"] == 3
    assert payload["nodes"]["stats"]["semantic_backend"] in ("tfidf", "lsa")


def test_stage_coverage_without_ledger_is_safe(synth_cfg):
    payload = pipeline.stage_coverage(synth_cfg)
    assert payload["ledger"]["stats"]["items"] == 0
    assert "domain_matrix" in payload["matrix"]


def test_stage_acceptance_marks_missing_evidence_as_fail(synth_cfg):
    payload = pipeline.stage_acceptance(synth_cfg)
    assert payload["all_pass"] is False
    assert payload["criteria"]["f_pytest"]["pass"] is False  # 未跑 pytest → 不得伪造通过
    assert payload["criteria"]["c_schema_miss"]["pass"] is False  # 子样不足 100


def test_write_json_is_deterministic(tmp_path):
    p = tmp_path / "x.json"
    a = pipeline.write_json(p, {"b": 2, "a": 1})
    first = p.read_text(encoding="utf-8")
    b = pipeline.write_json(p, {"a": 1, "b": 2})
    assert a == b and p.read_text(encoding="utf-8") == first