"""RunManifest extension: the ``coupling_clock`` block and its contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tensorphysics.clock_sync import (  # noqa: E402
    REQUIRED_COUPLING_CLOCK_FIELDS,
    ClockSpec,
    CoupledOscillatorConfig,
    CouplingClock,
    coupling_clock_block,
    simulate_coupled_oscillators,
    validate_coupling_clock,
)

SCHEMA_PATH = ROOT / "contracts" / "tensorphysics-run-manifest-v1.schema.json"


def _block_and_trace():
    cfg = CoupledOscillatorConfig(coupling_interval=0.05, slow_substeps=4, ratio=10.0, t_end=0.5)
    run = simulate_coupled_oscillators(cfg)
    clock = CouplingClock(
        [
            ClockSpec("slow", dt=cfg.dt_slow, role="master", max_time=cfg.t_end),
            ClockSpec("fast", dt=cfg.dt_fast, role="sub", max_time=cfg.t_end),
        ],
        cfg.coupling_interval,
    )
    return coupling_clock_block(clock, run.trace, exchanged_fields=("density", "momentum")), run.trace


def test_block_carries_the_four_required_fields_and_validates():
    block, trace = _block_and_trace()
    for field in REQUIRED_COUPLING_CLOCK_FIELDS:
        assert field in block
    report = validate_coupling_clock(block)
    assert report["ok"], report["problems"]
    assert report["n_required_present"] == len(REQUIRED_COUPLING_CLOCK_FIELDS)
    assert block["max_skew_s"] == pytest.approx(trace.max_skew)
    assert block["n_coupling_points"] == len(trace.points)
    assert block["subcycle_ratios"]["fast"] == pytest.approx(trace.subcycle_ratios["fast"])


def test_missing_field_is_rejected():
    block, _ = _block_and_trace()
    reduced = {k: v for k, v in block.items() if k != "max_skew_s"}
    report = validate_coupling_clock(reduced)
    assert not report["ok"]
    assert any("max_skew_s" in p for p in report["problems"])


def test_skew_above_the_declared_tolerance_is_rejected():
    block, _ = _block_and_trace()
    block["sync_tolerance_s"] = block["max_skew_s"] / 2.0 if block["max_skew_s"] > 0 else 1e-12
    block["max_skew_s"] = block["sync_tolerance_s"] * 2.0 + 1e-9
    report = validate_coupling_clock(block)
    assert not report["ok"]
    assert any("exceeds" in p for p in report["problems"])


def test_coupling_point_schedule_must_be_increasing_and_counted():
    block, _ = _block_and_trace()
    block["coupling_points_s"] = [0.1, 0.1, 0.2]
    block["n_coupling_points"] = 3
    report = validate_coupling_clock(block)
    assert not report["ok"]
    assert any("strictly increasing" in p for p in report["problems"])
    block["coupling_points_s"] = [0.1, 0.2]
    block["n_coupling_points"] = 5
    report = validate_coupling_clock(block)
    assert any("n_coupling_points" in p for p in report["problems"])


def test_contract_accepts_the_block_and_stays_backward_compatible():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text())
    block, _ = _block_and_trace()
    manifest = {
        "schema_version": 1,
        "run_id": "unit-test-run",
        "status": "succeeded",
        "validation_status": "passed",
        "outputs": [
            {
                "uri": "results.json",
                "media_type": "application/json",
                "checksum_sha256": "0" * 64,
                "size_bytes": 10,
            }
        ],
        "coupling_clock": block,
    }
    jsonschema.validate(manifest, schema)

    legacy = {k: v for k, v in manifest.items() if k != "coupling_clock"}
    jsonschema.validate(legacy, schema)  # old manifests keep validating

    incomplete = dict(manifest)
    incomplete["coupling_clock"] = {
        k: v for k, v in block.items() if k not in ("master_dt_s", "sync_tolerance_s")
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incomplete, schema)


def test_schema_declares_at_least_four_coupling_clock_fields():
    schema = json.loads(SCHEMA_PATH.read_text())
    block = schema["properties"]["coupling_clock"]
    assert len(block["required"]) >= 4
    assert set(REQUIRED_COUPLING_CLOCK_FIELDS) <= set(block["required"])
    assert len(block["properties"]) >= 4