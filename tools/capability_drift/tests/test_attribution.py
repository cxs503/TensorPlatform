"""git blame 退化定位单元测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.capability_drift import attribution, model, sandbox


def test_blame_finds_commit_that_changed_the_anchor_line(calibrated):
    sb, _ = calibrated
    cap = sb.capability(0)
    inject = sandbox.inject_degradation(sb.repo, cap, 0.68, "inject degradation for blame test")
    cap_model = model.load_bundle(sb.registry_dir).capability(cap.id)
    found = attribution.attribute(sb.repo, cap_model)
    assert found, "blame must resolve the anchor line"
    assert found[0].commit == inject
    assert found[0].short == inject[:10]
    assert "inject degradation" in found[0].subject
    sandbox.write_param(sb.repo, cap, 0.60)
    sandbox.git_commit(sb.repo, "restore")


def test_attribute_reports_only_requested_capabilities(calibrated):
    sb, _ = calibrated
    cap0, cap1 = sb.capabilities[0], sb.capabilities[1]
    c0 = sandbox.inject_degradation(sb.repo, cap0, 0.68, "degrade cap0")
    c1 = sandbox.inject_degradation(sb.repo, cap1, 0.68, "degrade cap1")
    bundle = model.load_bundle(sb.registry_dir)
    rep = attribution.attribute_report(sb.repo, bundle.capabilities, [cap0.id])
    assert list(rep) == [cap0.id]
    assert rep[cap0.id][0]["commit"] == c0
    assert c1 not in [s["commit"] for s in rep[cap0.id]]
    for cap in (cap0, cap1):
        sandbox.write_param(sb.repo, cap, 0.60)
    sandbox.git_commit(sb.repo, "restore both")


def test_blame_pattern_without_match_is_empty(calibrated):
    sb, _ = calibrated
    cap_model = model.Capability(
        id="synth.nope", solver="synth",
        cases=(model.Case(id="c", node="verif/test_cap_00.py::test_cap_00"),),
        metrics=("m",), gates=(model.Gate("m"),),
        blame=(model.BlamePattern(path="synth/params.py", pattern="^NOT_PRESENT = "),),
    )
    assert attribution.attribute(sb.repo, cap_model) == []


def test_blame_missing_path_is_empty(calibrated):
    sb, _ = calibrated
    cap_model = model.Capability(
        id="synth.nope", solver="synth",
        cases=(model.Case(id="c", node="verif/test_cap_00.py::test_cap_00"),),
        metrics=("m",), gates=(model.Gate("m"),),
        blame=(model.BlamePattern(path="synth/does_not_exist.py", pattern="x"),),
    )
    assert attribution.attribute(sb.repo, cap_model) == []


def test_parse_blame_porcelain_roundtrip(sandbox2):
    out = sandbox._git(sandbox2.repo, "blame", "--line-porcelain", "--", "synth/params.py")
    rows = attribution.parse_blame_porcelain(out)
    texts = [t for _c, _n, t in rows]
    assert any(t.startswith("RELAX_C00 = ") for t in texts)
    assert all(len(c) == 40 for c, _n, _t in rows)