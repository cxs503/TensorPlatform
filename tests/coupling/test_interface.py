"""failure_faces/v1 implementation-level tests.

Synthetic case: a unit cube split by the plane x = 0.5 into two uniform
boxes (0.5 x 1 x 1). All fragment data (mass, com, velocity, inertia about
own com) is analytical, so the exported ledgers must close to machine
precision when recomputed from the raw document.
"""
import copy

import numpy as np
import pytest

from coupling.interface import (
    InterfaceError,
    SCHEMA_VERSION,
    check_ledger_closure,
    export_failure_faces,
    face_geometry,
    load_fragments,
    validate,
)


def _box_nodes(x0, x1, y0, y1, z0, z1):
    """6 faces (4 quads + 2 caps) of an axis-aligned box, CCW from outside."""
    v = {
        "000": [x0, y0, z0], "100": [x1, y0, z0], "010": [x0, y1, z0], "110": [x1, y1, z0],
        "001": [x0, y0, z1], "101": [x1, y0, z1], "011": [x0, y1, z1], "111": [x1, y1, z1],
    }
    faces = [
        [v["000"], v["100"], v["110"], v["010"]],  # z-
        [v["001"], v["011"], v["111"], v["101"]],  # z+
        [v["000"], v["010"], v["011"], v["001"]],  # y-
        [v["100"], v["101"], v["111"], v["110"]],  # y+
        [v["000"], v["001"], v["101"], v["100"]],  # x-
        [v["010"], v["110"], v["111"], v["011"]],  # x+
    ]
    return [np.array(f, dtype=float) for f in faces]


def _split_cube_state():
    """Unit cube split at x=0.5; two fragments with analytical rigid-body data."""
    faces = []
    faces += [{"nodes": f} for f in _box_nodes(0.0, 0.5, 0.0, 1.0, 0.0, 1.0)]   # fragment 0 (left)
    faces += [{"nodes": f} for f in _box_nodes(0.5, 1.0, 0.0, 1.0, 0.0, 1.0)]  # fragment 1 (right)
    # internal crack faces (the actual failure faces)
    crack_left = np.array([[0.5, 0, 0], [0.5, 1, 0], [0.5, 1, 1], [0.5, 0, 1]], float)
    crack_right = np.array([[0.5, 0, 1], [0.5, 1, 1], [0.5, 1, 0], [0.5, 0, 0]], float)
    faces.append({"nodes": crack_left})
    faces.append({"nodes": crack_right})

    def box_inertia(m, a, b, c):
        return (m / 12.0) * np.array(
            [[b * b + c * c, 0, 0], [0, a * a + c * c, 0], [0, 0, a * a + b * b]]
        )

    m = 0.5  # rho=1, volume 0.5
    fragments = [
        {
            "mass": m,
            "com": [0.25, 0.5, 0.5],
            "velocity": [1.0, 0.0, 0.0],
            "inertia": box_inertia(m, 0.5, 1.0, 1.0),
            "face_ids": [0, 1, 2, 3, 4, 5, 12],
        },
        {
            "mass": m,
            "com": [0.75, 0.5, 0.5],
            "velocity": [0.0, 1.0, 0.0],
            "inertia": box_inertia(m, 0.5, 1.0, 1.0),
            "face_ids": [6, 7, 8, 9, 10, 11, 13],
        },
    ]
    return {"faces": faces, "fragments": fragments}


def test_export_validate_load_closure_machine_precision():
    doc = export_failure_faces(_split_cube_state())
    assert doc["schema"] == SCHEMA_VERSION
    assert validate(doc) == []
    fs = load_fragments(doc)
    assert fs.n == 2
    assert np.allclose(fs.masses, [0.5, 0.5])
    rep = check_ledger_closure(doc, rel_tol=1e-9)
    assert rep["closed"], rep
    assert rep["max_error"] < 1e-9
    # every one of the five ledgers closes
    for k in ("area_per_face", "area_total", "mass", "momentum", "energy", "inertia"):
        assert rep["errors"][k] < 1e-9, (k, rep["errors"][k])


def test_ledger_values_match_analytical():
    doc = export_failure_faces(_split_cube_state())
    led = doc["ledgers"]
    # area: 2 boxes * 6 faces * 0.5 (avg) ... compute directly
    assert led["total_mass"] == pytest.approx(1.0)
    assert np.allclose(led["total_momentum"], [0.5, 0.5, 0.0])
    assert led["total_energy"] == pytest.approx(0.5 * (0.5 * 1.0 + 0.5 * 1.0))
    # global com at x=0.5; each fragment offset 0.25 in x
    I_own = (0.5 / 12.0) * np.array([[1 + 1, 0, 0], [0, 0.25 + 1, 0], [0, 0, 0.25 + 1]])
    I_par = 0.5 * (0.25**2 * np.eye(3) - np.outer([0.25, 0, 0], [0.25, 0, 0]))
    I_expected = 2 * (I_own + I_par)
    assert np.allclose(led["total_inertia"], I_expected, atol=1e-12)


def test_tampered_mass_breaks_closure():
    doc = export_failure_faces(_split_cube_state())
    doc["fragments"][0]["mass"] = 0.505  # +1%
    rep = check_ledger_closure(doc, rel_tol=1e-9)
    assert not rep["closed"]
    assert rep["errors"]["mass"] > 1e-3


def test_tampered_inertia_breaks_closure():
    doc = export_failure_faces(_split_cube_state())
    doc["fragments"][1]["inertia"][0][0] *= 1.02
    rep = check_ledger_closure(doc, rel_tol=1e-9)
    assert not rep["closed"]
    assert rep["errors"]["inertia"] > 1e-3


def test_invalid_schema_detected():
    doc = export_failure_faces(_split_cube_state())
    bad = copy.deepcopy(doc)
    del bad["ledgers"]["total_energy"]
    assert any("total_energy" in v for v in validate(bad))

    bad = copy.deepcopy(doc)
    bad["faces"][0]["area"] = -1.0
    assert any("area" in v for v in validate(bad))

    bad = copy.deepcopy(doc)
    bad["faces"][0]["normal"] = [2.0, 0.0, 0.0]
    assert any("normal" in v for v in validate(bad))

    bad = copy.deepcopy(doc)
    bad["schema"] = "failure_faces/v0"
    assert any("schema" in v for v in validate(bad))

    bad = copy.deepcopy(doc)
    bad["fragments"][0]["face_ids"] = [999]
    assert any("face_ids" in v for v in validate(bad))


def test_non_psd_inertia_rejected():
    doc = export_failure_faces(_split_cube_state())
    doc["fragments"][0]["inertia"] = [[1.0, 0, 0], [0, -1.0, 0], [0, 0, 1.0]]
    assert any("positive semi-definite" in v for v in validate(doc))


def test_asymmetric_inertia_rejected():
    doc = export_failure_faces(_split_cube_state())
    doc["fragments"][0]["inertia"] = [[1.0, 0.5, 0], [0.0, 1.0, 0], [0, 0, 1.0]]
    assert any("symmetric" in v for v in validate(doc))


def test_load_fragments_fail_closed_on_invalid():
    doc = export_failure_faces(_split_cube_state())
    doc["fragments"][0]["mass"] = -1.0
    with pytest.raises(InterfaceError):
        load_fragments(doc)


def test_degenerate_face_rejected():
    with pytest.raises(InterfaceError):
        face_geometry(np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], float))


def test_face_geometry_newell_translation_invariant():
    tri = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    g1 = face_geometry(tri)
    g2 = face_geometry(tri + np.array([7.0, -3.0, 2.0]))
    assert g1["area"] == pytest.approx(g2["area"])
    assert np.allclose(g1["normal"], g2["normal"])
    assert g1["area"] == pytest.approx(0.5)
