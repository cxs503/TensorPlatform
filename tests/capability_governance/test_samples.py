"""Sample corpus: coverage, provenance, dedupe and idempotent loading."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from services.capability_governance import samples


@pytest.fixture()
def engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'samples.db'}")


def _rows(spec, **kw):
    return list(samples.iter_rows(spec, **kw))


def test_row_expansion_and_unique_digests(mini_sample_spec):
    rows = _rows(mini_sample_spec)
    assert len(rows) == 2 * 2 * 2 * 5
    digests = [r["input_digest_sha256"] for r in rows]
    assert len(set(digests)) == len(digests)
    assert all(len(d) == 64 for d in digests)


def test_outputs_are_non_empty_finite(mini_sample_spec):
    for row in _rows(mini_sample_spec):
        result = row["result"]
        assert result and all(isinstance(v, float) for v in result.values())
        assert result["peak_abs"] >= 0.0
        assert result["steps"] > 0


def test_input_change_changes_digest(mini_sample_spec):
    rows = _rows(mini_sample_spec)
    a, b = rows[0], rows[1]
    assert a["params"] != b["params"]
    assert a["input_digest_sha256"] != b["input_digest_sha256"]


def test_load_is_idempotent(engine, mini_sample_spec):
    rows = _rows(mini_sample_spec)
    first = samples.load_rows(engine, rows)
    assert first["inserted"] == len(rows)
    assert first["total_after"] == len(rows)
    second = samples.load_rows(engine, rows)
    assert second["inserted"] == 0
    assert second["skipped_existing"] == len(rows)
    assert second["total_after"] == len(rows)


def test_audit_numbers_come_from_the_database(engine, mini_sample_spec):
    rows = _rows(mini_sample_spec)
    samples.load_rows(engine, rows)
    audit = samples.audit(engine, spec=mini_sample_spec, min_rows=len(rows),
                          min_per_family=1, min_per_class=1,
                          min_provenance=0.95, min_unique=0.99)
    assert audit["n_rows"] == len(rows)
    assert audit["dedupe_unique_rate"] == 1.0
    assert audit["provenance_completeness"] == 1.0
    assert audit["output_non_empty_ratio"] == 1.0
    assert audit["criteria"]["rows_at_least_min"] is True
    assert set(audit["families"]) == {"af", "bf"}
    assert set(audit["case_classes"]) == {"ck", "dk"}


def test_provenance_block_has_every_required_field(mini_sample_spec):
    for row in _rows(mini_sample_spec):
        prov = row["provenance"]
        for field in samples.PROVENANCE_FIELDS:
            assert prov.get(field), f"missing provenance field {field}"


def test_axis_labels_are_mapped_without_touching_code(engine, mini_sample_spec):
    samples.load_rows(engine, _rows(mini_sample_spec))
    labels = {"family": {"af": "alpha", "bf": "beta"},
              "case_class": {"ck": "gamma", "dk": "delta"}}
    audit = samples.audit(engine, spec=mini_sample_spec, axis_labels=labels,
                          min_rows=1, min_per_family=1, min_per_class=1)
    assert audit["family_labels"] == {"af": "alpha", "bf": "beta"}
    assert audit["class_labels"] == {"ck": "gamma", "dk": "delta"}


def test_spec_validation_rejects_unknown_model(mini_sample_spec):
    bad = json.loads(json.dumps(mini_sample_spec))
    bad["model"]["kind"] = "does_not_exist"
    with pytest.raises(KeyError):
        _rows(bad)


def test_spec_loader_rejects_wrong_schema(tmp_path, mini_sample_spec):
    path = tmp_path / "spec.json"
    bad = dict(mini_sample_spec, schema="something-else/9.9")
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        samples.load_spec(path)


def test_shipped_spec_meets_the_coverage_arithmetic():
    repo = Path(__file__).resolve().parents[2]
    spec = samples.load_spec(repo / "config" / "capability_governance" / "sample_spec.json")
    total = (len(spec["axes"]["family"]) * len(spec["axes"]["case_class"])
             * len(spec["condition_grid"]) * spec["replicates"])
    assert total >= 10000
    per_family = total // len(spec["axes"]["family"])
    per_class = total // len(spec["axes"]["case_class"])
    assert per_family >= 2000
    assert per_class >= 1000