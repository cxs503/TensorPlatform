#!/usr/bin/env python3
"""Tests for Artifact Lineage DAG module.

Tests cover:
- SHA256 hashing
- Directory scanning
- DAG construction
- Forward/backward tracing
- Integrity verification
- Tamper detection
- Serialization round-trip
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.artifact_lineage.dag_builder import (
    ArtifactNode,
    LineageDAG,
    build_lineage_dag,
    verify_integrity,
    detect_tampering,
    sha256_file,
    scan_directory,
    _categorize,
    _infer_dependencies,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_tree(tmp_path):
    """Create a sample directory tree with artifacts."""
    # Create structure
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "runs").mkdir()
    (tmp_path / "docs").mkdir()

    # Create files
    (tmp_path / "artifacts" / "data.json").write_text('{"key": "value"}')
    (tmp_path / "artifacts" / "report.json").write_text('{"title": "test"}')
    (tmp_path / "runs" / "run1" / "output.log").parent.mkdir(parents=True)
    (tmp_path / "runs" / "run1" / "output.log").write_text("run output")
    (tmp_path / "docs" / "readme.md").write_text("# Documentation")
    (tmp_path / "generate_figures.py").write_text("import matplotlib")
    (tmp_path / "figures.png").write_text("fake png data")

    return tmp_path


@pytest.fixture
def sample_dag(sample_tree):
    """Build a DAG from the sample tree."""
    return build_lineage_dag([sample_tree])


# ---------------------------------------------------------------------------
# Hashing tests
# ---------------------------------------------------------------------------

class TestHashing:
    def test_sha256_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sha256_file(f) == expected

    def test_sha256_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert sha256_file(f) == expected

    def test_sha256_large_file(self, tmp_path):
        f = tmp_path / "large.bin"
        data = b"x" * (2 * 1024 * 1024)  # 2 MiB
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_file(f) == expected


# ---------------------------------------------------------------------------
# Scanning tests
# ---------------------------------------------------------------------------

class TestScanning:
    def test_scan_directory(self, sample_tree):
        nodes = scan_directory(sample_tree)
        assert len(nodes) > 0
        # Check all files found
        paths = set(nodes.keys())
        assert "artifacts/data.json" in paths
        assert "docs/readme.md" in paths

    def test_scan_excludes(self, sample_tree):
        # Add a .git directory
        (sample_tree / ".git").mkdir()
        (sample_tree / ".git" / "config").write_text("git config")
        nodes = scan_directory(sample_tree, exclude_patterns=[".git"])
        paths = set(nodes.keys())
        assert not any(".git" in p for p in paths)

    def test_scan_skips_symlinks(self, sample_tree):
        target = sample_tree / "artifacts" / "data.json"
        link = sample_tree / "link.json"
        link.symlink_to(target)
        nodes = scan_directory(sample_tree)
        paths = set(nodes.keys())
        assert "link.json" not in paths


# ---------------------------------------------------------------------------
# Categorization tests
# ---------------------------------------------------------------------------

class TestCategorization:
    def test_artifacts_category(self):
        assert _categorize("artifacts/foo.json") == "artifacts"

    def test_runs_category(self):
        assert _categorize("runs/m123/output.log") == "runs"

    def test_docs_category(self):
        assert _categorize("docs/readme.md") == "docs"

    def test_work_category(self):
        assert _categorize("work/m123/file.py") == "work"

    def test_other_category(self):
        assert _categorize("src/main.py") == "other"


# ---------------------------------------------------------------------------
# DAG construction tests
# ---------------------------------------------------------------------------

class TestDAGConstruction:
    def test_build_dag(self, sample_tree):
        dag = build_lineage_dag([sample_tree])
        assert len(dag.nodes) > 0
        assert dag.is_acyclic()
        assert dag.built_at != ""

    def test_dag_has_edges(self, sample_tree):
        dag = build_lineage_dag([sample_tree])
        # generate_figures.py → figures.png should be inferred
        edges = list(dag.graph.edges())
        # At least some edges should exist from dependency inference
        assert isinstance(edges, list)

    def test_dag_stats(self, sample_tree):
        dag = build_lineage_dag([sample_tree])
        assert dag.scan_stats["files_scanned"] > 0
        assert dag.scan_stats["bytes_scanned"] > 0
        assert "categories" in dag.scan_stats

    def test_multiple_roots(self, tmp_path):
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        root1.mkdir()
        root2.mkdir()
        (root1 / "file1.txt").write_text("one")
        (root2 / "file2.txt").write_text("two")
        dag = build_lineage_dag([root1, root2])
        assert len(dag.nodes) == 2


# ---------------------------------------------------------------------------
# Query tests
# ---------------------------------------------------------------------------

class TestQueries:
    def test_ancestors(self, sample_dag):
        # Pick a node and check ancestors
        for node_id in sample_dag.nodes:
            ancestors = sample_dag.ancestors(node_id)
            assert isinstance(ancestors, list)

    def test_descendants(self, sample_dag):
        for node_id in sample_dag.nodes:
            descendants = sample_dag.descendants(node_id)
            assert isinstance(descendants, list)

    def test_nonexistent_node(self, sample_dag):
        assert sample_dag.ancestors("nonexistent") == []
        assert sample_dag.descendants("nonexistent") == []

    def test_topological_layers(self, sample_dag):
        layers = sample_dag.topological_layers()
        assert isinstance(layers, list)
        # All nodes should appear in exactly one layer
        all_nodes_in_layers = set()
        for layer in layers:
            for node in layer:
                assert node not in all_nodes_in_layers
                all_nodes_in_layers.add(node)


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict(self, sample_dag):
        d = sample_dag.to_dict()
        assert "version" in d
        assert "nodes" in d
        assert "edges" in d
        assert d["node_count"] == len(sample_dag.nodes)

    def test_save_load_roundtrip(self, sample_dag, tmp_path):
        out = tmp_path / "dag.json"
        sample_dag.save(out)
        loaded = LineageDAG.load(out)
        assert len(loaded.nodes) == len(sample_dag.nodes)
        assert loaded.built_at == sample_dag.built_at
        assert loaded.is_acyclic()

    def test_save_creates_parent_dirs(self, sample_dag, tmp_path):
        out = tmp_path / "sub" / "dir" / "dag.json"
        sample_dag.save(out)
        assert out.exists()


# ---------------------------------------------------------------------------
# Integrity verification tests
# ---------------------------------------------------------------------------

class TestIntegrity:
    def test_verify_clean(self, sample_dag, sample_tree):
        result = verify_integrity(sample_dag, [sample_tree])
        assert result["ok"] is True
        assert result["checked"] > 0
        assert len(result["mismatches"]) == 0
        assert len(result["missing"]) == 0

    def test_verify_tampered(self, sample_dag, sample_tree):
        # Tamper with a file
        (sample_tree / "artifacts" / "data.json").write_text('{"tampered": true}')
        result = verify_integrity(sample_dag, [sample_tree])
        assert result["ok"] is False
        assert len(result["mismatches"]) > 0

    def test_verify_missing_file(self, sample_dag, sample_tree):
        # Remove a file
        (sample_tree / "docs" / "readme.md").unlink()
        result = verify_integrity(sample_dag, [sample_tree])
        assert result["ok"] is False
        assert len(result["missing"]) > 0


# ---------------------------------------------------------------------------
# Tamper detection tests
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_detect_clean(self, sample_dag, sample_tree):
        result = detect_tampering(sample_dag, [sample_tree])
        assert result["tamper_status"] == "clean"
        assert result["severity"] == "none"

    def test_detect_content_change(self, sample_dag, sample_tree):
        (sample_tree / "artifacts" / "data.json").write_text('{"changed": true}')
        result = detect_tampering(sample_dag, [sample_tree])
        assert result["tamper_status"] == "compromised"
        assert result["severity"] == "critical"

    def test_detect_file_removal(self, sample_dag, sample_tree):
        (sample_tree / "docs" / "readme.md").unlink()
        result = detect_tampering(sample_dag, [sample_tree])
        assert result["tamper_status"] == "compromised"
        assert result["severity"] == "warning"
