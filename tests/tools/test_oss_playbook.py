"""Tests for the OSS->knowledge->improvement playbook tooling.

Fixtures use synthetic repository/anchor names on purpose: the tooling must stay
domain-free (TensorPlatform boundary rule), so nothing here may depend on a real
numerical-method vocabulary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.oss_inventory import machine_blocks as mb          # noqa: E402
from tools.oss_inventory import playbook_metrics as pm        # noqa: E402

GOOD = """# inventory

[OSS-INV]
repo: acme/widget
lang: C++
license: MIT
methods: explicit marching
summary: reference implementation of a marching kernel
[/OSS-INV]

[OSS-SOL]
repo: acme/widget
slug: GAP-WIDGET-STEADY
tag: steady-state-marching
method: march the operator until residual drops below tolerance
files: src/march.cpp:120-160, src/ops.h:44
conventions: h=cell size; tol=residual target
transfer: adapt
[/OSS-SOL]
"""

BAD = """[OSS-SOL]
repo: acme/widget
slug: 性能优化
tag: t
method: m
files: src/march.cpp
transfer: reject-as-is
[/OSS-SOL]
"""


def test_parse_counts_and_fields():
    blocks = mb.parse_text(GOOD, "good.md")
    assert [b.kind for b in blocks] == ["OSS-INV", "OSS-SOL"]
    assert blocks[0].source == "good.md:3"
    assert blocks[1].fields["transfer"] == "adapt"
    assert blocks[1].repo == "acme/widget"
    assert mb.validate(blocks) == []


def test_parse_wrapped_continuation_is_tolerated():
    text = GOOD.replace("summary: reference implementation of a marching kernel",
                        "summary: reference implementation of a marching kernel\n  spanning two physical lines")
    blocks = mb.parse_text(text)
    assert "spanning two physical lines" not in blocks[0].fields.get("summary", "")
    assert blocks[0].fields["summary"].startswith("reference implementation")


def test_violations_for_vocabulary_and_pointer_drift():
    violations = mb.validate(mb.parse_text(BAD))
    detail = {}
    for v in violations:                      # one code may fire several times
        detail.setdefault(v["code"], set()).add(v["detail"])
    assert detail["bad_transfer"] == {"reject-as-is"}
    assert detail["no_line_pointer"] == {"src/march.cpp"}
    assert detail["slug_not_anchored"] == {"性能优化"}
    # every required-but-absent field of an OSS-SOL block is reported
    assert detail["missing_field"] == {"conventions"}


def test_missing_repo_shape_and_duplicate_keys():
    text = ("[OSS-SOL]\nrepo: not-a-repo\nslug: GAP-A-B\ntag: t\nmethod: m\n"
            "files: a.c:1\ntransfer: fits\nconventions: k=v\n"
            "conventions: k2=v2\n[/OSS-SOL]\n")
    codes = {v["code"] for v in mb.validate(mb.parse_text(text))}
    assert {"bad_repo", "duplicate_key"} <= codes


def test_slug_classification():
    assert mb.classify_slug("GAP-WIDGET-STEADY", "GAP-") == "gap"
    assert mb.classify_slug("NEW:CAP-WIDGET", "GAP-") == "namespaced"
    assert mb.classify_slug("perf-tuning", "GAP-") == "namespaced"
    assert mb.classify_slug("性能优化", "GAP-") == "free-label"
    assert mb.classify_slug("", "GAP-") == "missing"


def test_cli_strict_exit_codes(tmp_path):
    good = tmp_path / "good.md"
    good.write_text(GOOD, encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text(BAD, encoding="utf-8")
    assert mb.main([str(good), "--strict"]) == 0
    assert mb.main([str(bad), "--strict"]) == 1
    assert mb.main([str(bad)]) == 0  # non-strict: report only


def _state():
    return {
        "inventory": [
            {"repo": "acme/widget", "tier": 1},
            {"repo": "acme/gizmo", "tier": 2},
            {"repo": "acme/void", "tier": 3},
        ],
        "solutions": [
            {"repo": "acme/widget", "slug": "GAP-WIDGET-STEADY",
             "files": "src/march.cpp:120-160", "evidence": "src/march.cpp:120"},
            {"repo": "acme/gizmo", "slug": "GAP-GIZMO-LIMIT",
             "files": "lib/limit.h:9", "evidence": "lib/limit.h:9"},
            {"repo": "acme/gizmo", "slug": "性能优化", "files": "lib/x.h:1",
             "evidence": "report 42"},
        ],
        "goals": [
            {"id": 1, "goal": "consume GAP-WIDGET-STEADY; cite oss_solutions ids"},
            {"id": 2, "goal": "unrelated unit"},
        ],
    }


def test_compute_metrics_shape_and_values():
    m = pm.compute_metrics(_state())
    s1, s3, s5 = m["step1_inventory"], m["step3_knowledge"], m["step5_consumption"]
    assert s1["repos_registered"] == 3
    assert s1["repos_deep_dived"] == 2
    assert s1["repos_zero_solution"] == ["acme/void"]
    assert s3["rows"] == 3 and s3["distinct_slugs"] == 3
    assert s3["slug_class"]["gap"] == 2 and s3["slug_class"]["free-label"] == 1
    assert s3["pointer_ratio"] == 1.0
    assert s3["evidence_direct_ratio"] == pytest.approx(2 / 3, abs=1e-4)
    assert s5["consumption_by_anchor"] == {"GAP-WIDGET-STEADY": 1}
    assert s5["consumption_units"] == [1]
    assert s5["improvement_evidence_count"] == 1


def test_gates_and_strict_exit_use_thresholds(tmp_path):
    m = pm.compute_metrics(_state())
    g = m["gates"]
    # 2/3 = 0.6667 < 0.70 -> the gate must FAIL here; this guards the doc's
    # claim that "registered but not deep dived" cannot be rounded up to green.
    assert g["deep_dive_ratio"]["value"] == pytest.approx(0.6667, abs=1e-4)
    assert g["deep_dive_ratio"]["pass"] is False
    assert g["pointer_ratio"]["pass"] is True
    assert g["consumed_anchor_ratio"]["value"] == 0.5
    assert pm.gates_ok(m) is False

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(_state()), encoding="utf-8")
    assert pm.main(["--from-json", str(state_file), "--strict"]) == 1
    assert pm.main(["--from-json", str(state_file)]) == 0


def test_metrics_zero_rows_do_not_divide_by_zero():
    m = pm.compute_metrics({"inventory": [], "solutions": [], "goals": []})
    assert m["step1_inventory"]["deep_dive_ratio"] == 0.0
    assert m["step3_knowledge"]["pointer_ratio"] == 0.0
    assert m["step5_consumption"]["consumed_anchor_ratio"] == 0.0