"""Cross-mapping failure modes and the failure_faces/v2 artifact (v1-compatible)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tools.field_mapper import (  # noqa: E402
    CrossMapErrorMode,
    FaultTolerances,
    ConservativeMap,
    MapLaw,
    coverage_fraction,
    detect_faults,
    load_failure_faces,
    validate_failure_faces,
    write_failure_faces,
)


def _uniform(n: int) -> "object":
    edges = np.linspace(0.0, 1.0, n + 1)
    lo = np.array([[x, y] for x in edges[:-1] for y in edges[:-1]])
    hi = np.array([[x, y] for x in edges[1:] for y in edges[1:]])
    from tools.field_mapper import CellSet

    return CellSet.from_boxes(lo, hi, name=f"u{n}")


def _field(cells) -> np.ndarray:
    c = cells.centers
    return 2.0 + np.sin(np.pi * c[:, 0]) * np.cos(np.pi * c[:, 1])


def _modes(faults) -> set:
    return {f.mode for f in faults}


def test_clean_map_reports_no_faults():
    src, dst = _uniform(8), _uniform(24)
    mapper = ConservativeMap(src, dst)
    values = _field(src)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    audit = mapper.audit(values, mapped, law=MapLaw.DENSITY)
    faults = detect_faults(audit, coverage_fraction(mapper.entries, dst.measure))
    assert faults == []


def test_uncovered_destination_cells_are_named():
    src = _uniform(4)
    from tools.field_mapper import CellSet

    dst = CellSet.from_boxes(
        np.array([[0.0, 0.0], [2.0, 2.0]]),
        np.array([[0.5, 0.5], [3.0, 3.0]]),
        name="far",  # a quarter of the source is covered, one cell lies outside
    )
    mapper = ConservativeMap(src, dst)
    values = _field(src)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    audit = mapper.audit(values, mapped, law=MapLaw.DENSITY)
    faults = detect_faults(audit, coverage_fraction(mapper.entries, dst.measure))
    modes = _modes(faults)
    assert CrossMapErrorMode.UNCOVERED_DESTINATION in modes
    assert CrossMapErrorMode.TOTAL_MISMATCH in modes
    assert any(f.face_id is not None for f in faults)


def test_kind_law_mismatch_is_detected():
    src, dst = _uniform(6), _uniform(9)
    mapper = ConservativeMap(src, dst)
    values = np.full(src.n_cells, 1.0)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    audit = mapper.audit(values, mapped, law=MapLaw.DENSITY)
    faults = detect_faults(
        audit,
        coverage_fraction(mapper.entries, dst.measure),
        declared_kind="extensive",
        expected_law="extensive",
    )
    assert CrossMapErrorMode.KIND_LAW_MISMATCH in _modes(faults)


def test_overshoot_and_moment_drift_are_detected():
    src, dst = _uniform(6), _uniform(6)
    mapper = ConservativeMap(src, dst, operator="nearest")
    values = _field(src)
    # a deliberately shifted field: same total, all mass in the first cell
    shifted = np.zeros(dst.n_cells)
    shifted[0] = float((values * src.measure).sum() / dst.measure[0])
    audit = mapper.audit(values, shifted, law=MapLaw.DENSITY)
    moment_rel = float(
        np.max(
            np.abs(
                np.einsum("i,ik->k", values * src.measure, src.centers)
                - np.einsum("i,ik->k", shifted * dst.measure, dst.centers)
            )
        )
        / (np.sum(np.abs(values) * src.measure) * src.extent_scale)
    )
    faults = detect_faults(audit, None, moment_rel_error=moment_rel, tolerances=FaultTolerances(total_rel=1e-9))
    assert CrossMapErrorMode.MOMENT_DRIFT in _modes(faults)
    assert CrossMapErrorMode.OVERSHOOT in _modes(faults)


def test_failure_faces_v2_roundtrip_and_self_validation(tmp_path):
    src = _uniform(4)
    from tools.field_mapper import CellSet

    big = CellSet.from_boxes(
        np.array([[0.0, 0.0], [1.5, 1.5]]),
        np.array([[0.5, 0.5], [2.5, 2.5]]),
        name="big",  # partial coverage of the source + one disconnected cell
    )
    mapper = ConservativeMap(src, big)
    values = _field(src)
    mapped = mapper.apply(values, law=MapLaw.DENSITY)
    audit = mapper.audit(values, mapped, law=MapLaw.DENSITY)
    faults = detect_faults(audit, coverage_fraction(mapper.entries, big.measure))
    assert len(_modes(faults)) >= 2
    assert CrossMapErrorMode.UNCOVERED_DESTINATION in _modes(faults)
    assert CrossMapErrorMode.TOTAL_MISMATCH in _modes(faults)

    path = tmp_path / "failure_faces.json"
    write_failure_faces(
        str(path),
        faults,
        cross_map={"matrix_digest": audit.matrix_digest, "src_mesh": src.name, "dst_mesh": big.name},
    )
    payload = json.loads(path.read_text())
    assert payload["schema"] == "failure_faces/v2"
    assert len(payload["error_modes"]) >= 2  # two distinct modes are measured here
    check = validate_failure_faces(payload)
    assert check["ok"], check["problems"]

    records, summary = load_failure_faces(str(path))
    assert summary["schema_version"] == 2
    assert summary["n_faults"] == len(faults)
    assert set(summary["error_modes_present"]) == set(payload["error_modes"])
    assert summary["consistent"] is True
    assert all("error_mode" in r for r in records)


def test_failure_faces_v1_is_still_readable(tmp_path):
    """Backward compatibility with the fracture-face producer of the FEM side."""
    from src.coupling.fem_to_dem_exact import FractureFace, dump_failure_faces

    face = FractureFace(
        vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]),
        normal=np.array([0.0, 0.0, 1.0]),
        area=1.0,
    )
    path = tmp_path / "faces_v1.json"
    dump_failure_faces([face], str(path))
    records, summary = load_failure_faces(str(path))
    assert summary["schema_version"] == 1
    assert summary["n_faces"] == 1
    assert records[0]["error_mode"] is None
    assert summary["error_modes_present"] == []


def test_validator_rejects_inconsistent_documents():
    doc = {
        "schema": "failure_faces/v2",
        "error_modes": ["uncovered_destination"],
        "faces": [{"face_id": 0, "error_mode": "moment_drift"}],
        "cross_map": {},
    }
    report = validate_failure_faces(doc)
    assert report["ok"] is False
    problems = " ".join(report["problems"])
    assert "not declared" in problems
    assert "matrix_digest" in problems