"""Regression suite: artifact lineage DAG (sha256 + DAG + tamper detection)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tools.sustainment import lineage as lg


# ---------------------------------------------------------------------------
# fixtures: a small synthetic artifact tree (no domain vocabulary)
# ---------------------------------------------------------------------------


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    _write(root / "demo" / "source.csv", "a,b\n1,2\n")
    _write(root / "demo" / "summary.json", json.dumps({"n": 12}))
    _write(root / "demo" / "summary.png", "PNGDATA")
    _write(root / "demo" / "summary.md", "figure: summary.png from summary.json\n")
    _write(root / "demo" / "summary.json.provenance.json",
           json.dumps({"inputs": ["source.csv"], "note": "declared upstream"}))
    _write(root / "docs" / "note.md", "see artifacts/demo/summary.md\n")
    _write(root / "docs" / "unrelated.txt", "nothing to see\n")
    return root


def test_scan_hashes_every_file(tree: Path):
    inv = lg.scan([tree])
    assert len(inv.nodes) == 7
    assert all(len(n.sha256) == 64 for n in inv.nodes.values())
    assert inv.skipped == []
    kinds = {n.kind for n in inv.nodes.values()}
    assert {"data", "figure", "doc"} <= kinds


def test_declared_edge_is_r1(tree: Path):
    inv = lg.scan([tree])
    g = lg.build_graph(inv, rules=("R1",))
    assert g.number_of_nodes() == 7
    edges = [(s, d) for s, d in g.edges]
    # source.csv -> summary.json (declared by the sidecar)
    assert any(s.endswith("source.csv") and d.endswith("summary.json") for s, d in edges)


def test_sibling_edge_is_r2_and_ordered(tree: Path):
    inv = lg.scan([tree])
    g = lg.build_graph(inv, rules=("R2",))
    edges = {(s.split("/")[-1], d.split("/")[-1]) for s, d in g.edges}
    assert ("summary.json", "summary.png") in edges
    assert ("summary.png", "summary.md") in edges
    assert ("summary.json", "summary.md") in edges
    # direction invariant: data never depends on a figure
    assert ("summary.png", "summary.json") not in edges


def test_mention_edge_is_r3(tree: Path):
    inv = lg.scan([tree])
    g = lg.build_graph(inv, rules=("R3",))
    edges = {(s.split("/")[-1], d.split("/")[-1]) for s, d in g.edges}
    assert ("summary.png", "summary.md") in edges      # md mentions png
    assert ("summary.md", "note.md") in edges          # note cites the md path


def test_forward_backward_tracing(tree: Path):
    inv = lg.scan([tree])
    lin = lg.Lineage(lg.build_graph(inv), inv)
    src = next(n for n in inv.nodes if n.endswith("source.csv"))
    doc = next(n for n in inv.nodes if n.endswith("summary.md"))
    # backward: what was the doc derived from
    assert src in lin.backward(doc)
    # forward: touching source.csv impacts the whole family
    fwd = lin.forward(src)
    assert doc in fwd and any(n.endswith("summary.png") for n in fwd)
    # isolation: the unrelated file has no lineage either way
    unrel = next(n for n in inv.nodes if n.endswith("unrelated.txt"))
    assert lin.backward(unrel) == [] and lin.forward(unrel) == []
    assert lin.path_between(src, doc) is not None


def test_graph_is_dag_and_summary_fields(tree: Path):
    inv = lg.scan([tree])
    lin = lg.Lineage(lg.build_graph(inv), inv)
    s = lin.summary()
    assert s["is_dag"] is True and s["cycles"] == 0
    assert s["nodes"] == 7
    assert s["edges"] >= 5
    assert s["sha256_coverage"] == 1.0
    assert set(s["by_rule"]) <= {"R1_declared", "R2_sibling", "R3_mention"}
    assert s["by_rule"].get("R1_declared", 0) >= 1


# ---------------------------------------------------------------------------
# tamper detection
# ---------------------------------------------------------------------------


def test_lock_roundtrip_clean(tree: Path, tmp_path: Path):
    inv = lg.scan([tree])
    lin = lg.Lineage(lg.build_graph(inv), inv)
    lock = tmp_path / "lock.json"
    lg.save_lock(lin, lock, roots=[str(tree)])
    assert lock.exists()
    drift = lg.verify_lock(lin, lock)
    assert drift.ok, drift.to_json()
    assert drift.checked == 7
    assert drift.modified == [] and drift.missing == [] and drift.added == []


def test_lock_detects_modified_missing_and_added(tree: Path, tmp_path: Path):
    inv = lg.scan([tree])
    lin = lg.Lineage(lg.build_graph(inv), inv)
    lock = tmp_path / "lock.json"
    lg.save_lock(lin, lock, roots=[str(tree)])

    # 1) tamper with content, keeping size+path identical (mtimes irrelevant)
    victim = tree / "demo" / "source.csv"
    victim.write_text("a,b\n1,3\n".ljust(len("a,b\n1,2\n")))
    # 2) delete a file
    (tree / "docs" / "unrelated.txt").unlink()
    # 3) add a new file
    _write(tree / "docs" / "new.md", "added later\n")

    inv2 = lg.scan([tree])
    lin2 = lg.Lineage(lg.build_graph(inv2), inv2)
    drift = lg.verify_lock(lin2, lock)
    payload = drift.to_json()
    assert drift.ok is False
    assert any(m["id"].endswith("source.csv") for m in drift.modified)
    assert any(m.endswith("unrelated.txt") for m in drift.missing)
    assert any(m.endswith("new.md") for m in drift.added)
    assert payload["counts"]["modified"] >= 1


def test_report_build_writes_json_and_lock(tree: Path, tmp_path: Path):
    out = tmp_path / "lineage.json"
    lock = tmp_path / "lock.json"
    rep = lg.build_report([str(tree)], out_json=str(out), lock_path=str(lock))
    assert out.exists() and lock.exists()
    loaded = json.loads(out.read_text())
    assert loaded["summary"]["nodes"] == 7
    assert loaded["inventory"]["files_hashed"] == 7
    assert loaded["summary"]["is_dag"] is True


def test_dot_export_and_optional_figure(tree: Path, tmp_path: Path):
    inv = lg.scan([tree])
    g = lg.build_graph(inv)
    dot = lg.render_dot(g)
    assert dot.startswith("digraph lineage {") and "->" in dot
    try:
        import matplotlib  # noqa: F401
    except Exception:
        pytest.skip("matplotlib unavailable")
    png = tmp_path / "dag.png"
    lg.plot_dag(g, png)
    assert png.exists() and png.stat().st_size > 1000


def test_missing_root_is_reported_not_crashed(tmp_path: Path):
    inv = lg.scan([tmp_path / "nope"])
    assert inv.nodes == {}
    assert inv.skipped[0]["reason"] == "root_missing"
    lin = lg.Lineage(lg.build_graph(inv), inv)
    assert lin.summary()["nodes"] == 0

# ---------------------------------------------------------------------------
# evidence-ranked cycle resolution
# ---------------------------------------------------------------------------


def test_cycle_is_detected_and_broken_by_weakest_evidence(tmp_path: Path):
    root = tmp_path / "artifacts"
    # m.py writes table.md, and table.md mentions m.py -> R2 code->doc plus R3 doc mentions code
    _write(root / "x" / "tool.py", "reads tool.md and writes out.md\n")
    _write(root / "x" / "tool.md", "generated by tool.py\n")
    inv = lg.scan([root])
    g = lg.build_graph(inv)
    assert lg.cycles(g), "fixture must produce a cycle"

    g2, dropped = lg.enforce_dag(g)
    assert lg.cycles(g2) == [], "cycle must be resolved"
    assert len(dropped) >= 1
    assert all(d["rule"] == "R3_mention" for d in dropped), dropped
    assert all(len(d["cycle"]) >= 3 for d in dropped)


def test_report_records_cycle_resolution(tmp_path: Path):
    root = tmp_path / "artifacts"
    _write(root / "x" / "tool.py", "reads tool.md\n")
    _write(root / "x" / "tool.md", "made by tool.py\n")
    rep = lg.build_report([str(root)], enforce_dag_flag=True)
    assert rep["cycles_raw"] >= 1
    assert rep["cycle_resolution"]["applied"] is True
    assert rep["summary"]["is_dag"] is True
    assert rep["summary"]["dropped_edges"]

    rep2 = lg.build_report([str(root)], enforce_dag_flag=False)
    assert rep2["summary"]["is_dag"] is False
    assert rep2["cycles_raw"] >= 1


def test_strong_evidence_cycle_is_reported_not_hidden(tmp_path: Path):
    """A cycle that can only be broken by dropping R1 evidence must stay visible."""
    root = tmp_path / "artifacts"
    _write(root / "x" / "a.json", json.dumps({"n": 1}))
    _write(root / "x" / "b.json", json.dumps({"n": 2}))
    _write(root / "x" / "a.json.provenance.json", json.dumps({"inputs": ["b.json"]}))
    _write(root / "x" / "b.json.provenance.json", json.dumps({"inputs": ["a.json"]}))
    rep = lg.build_report([str(root)], rules=("R1",), enforce_dag_flag=True)
    # the only edges are R1 declared and mutually contradictory -> cycle survives
    assert rep["cycles_raw"] == 1
    assert rep["summary"]["is_dag"] is False
    assert rep["summary"]["cycles_unresolved"] >= 1
    # declared provenance is never dropped to force a DAG
    assert rep["cycle_resolution"]["dropped_edges"] == []


# ---------------------------------------------------------------------------
# the audit's own outputs are not its inputs (lock stays valid)
# ---------------------------------------------------------------------------


def test_build_then_verify_is_clean_with_outputs_inside_root(tmp_path: Path):
    root = tmp_path / "artifacts"
    _write(root / "d" / "a.json", '{"n": 1}')
    _write(root / "d" / "a.png", "PNG")
    out_dir = root / "sustainment"
    out, lock, png = out_dir / "lineage.json", out_dir / "lock.json", out_dir / "dag.png"
    lg.build_report([str(root)], out_json=str(out), lock_path=str(lock), out_png=str(png))
    assert out.exists() and lock.exists() and png.exists()

    # a fresh scan sees the outputs; the lock must exclude them wholesale
    inv = lg.scan([root])
    lin = lg.Lineage(lg.build_graph(inv), inv)
    drift = lg.verify_lock(lin, lock)
    assert drift.ok, drift.to_json()
    assert drift.checked == 2          # only the two real inputs

    # ... while real tampering is still caught
    (root / "d" / "a.png").write_text("PNGX")
    inv2 = lg.scan([root])
    lin2 = lg.Lineage(lg.build_graph(inv2), inv2)
    drift2 = lg.verify_lock(lin2, lock)
    assert drift2.ok is False
    assert [m["id"] for m in drift2.modified] == ["artifacts/d/a.png"]


def test_scan_excludes_dir_and_path(tmp_path: Path):
    root = tmp_path / "artifacts"
    _write(root / "keep.json", "{}")
    _write(root / "skip" / "noise.json", "{}")
    inv = lg.scan([root], exclude_dirs=[root / "skip"])
    assert [n.id for n in inv.nodes.values()] == ["artifacts/keep.json"]
    inv2 = lg.scan([root], exclude_paths=[root / "keep.json"])
    assert [n.id for n in inv2.nodes.values()] == ["artifacts/skip/noise.json"]
    assert any(s["reason"] == "excluded_output" for s in inv2.skipped)
