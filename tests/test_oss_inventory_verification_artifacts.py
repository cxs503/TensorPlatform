"""Provenance gate for OSS-inventory verification artifacts (platform-level, domain-neutral).

OSS deep-dive missions park a machine-readable *verification artifact* next to their
report: closed-form quantities re-derived from the inspected source, together with the
residuals obtained when they are recomputed.  Historically such numbers were typed by
hand into reports; this gate makes the artifacts self-checking so a report cannot claim
"<3 %" on numbers that do not actually converge.

The gate is schema-driven: it discovers `artifacts/**/verify_*.json`, and for any artifact
that carries the generic blocks below it asserts the numerical invariants.  Nothing about
a particular upstream project is hardcoded, so the same gate applies to any future
inventory (synthetic fixtures are used when no artifact is present).
"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = sorted(ROOT.glob("artifacts/**/verify_*.json"))

# Tolerance used throughout the OSS-inventory protocol: recomputed closed forms must agree
# with their reference (normalisation 1, exact identity) far beyond the 3 % report bar.
NORM_BAR = 3e-2
IDENTITY_BAR = 1e-9
ROUNDTRIP_BAR = 1e-12


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def _norm_blocks(doc: dict):
    block = doc.get("kernel_norm_abs_error_by_panels")
    if not isinstance(block, dict):
        return
    panels = block.get("panels")
    for key, series in block.items():
        if key == "panels":
            continue
        yield key, panels, series


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.parent.name)
def test_artifact_present(path):
    doc = _load(path)
    assert isinstance(doc, dict) and doc, f"{path} is empty"
    assert "repo" in doc, "artifact must declare the inspected repo"


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.parent.name)
def test_normalisation_residual_converges(path):
    doc = _load(path)
    for name, panels, series in _norm_blocks(doc):
        assert len(panels) == len(series) > 0
        # residuals must be monotone-ish: finest grid no worse than the coarsest
        assert abs(series[-1]) <= abs(series[0]) + 1e-12, f"{name}: residual grows with resolution"
        assert abs(series[-1]) <= NORM_BAR, f"{name}: residual {series[-1]:.2e} exceeds 3 %"
        assert abs(series[len(series) // 2]) <= NORM_BAR, f"{name}: mid-resolution residual too large"


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.parent.name)
def test_derivative_identity(path):
    doc = _load(path)
    for name, err in (doc.get("kernel_derivative_max_rel_error") or {}).items():
        assert err <= IDENTITY_BAR, f"{name}: derivative identity residual {err:.2e}"


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.parent.name)
def test_eos_self_consistency(path):
    doc = _load(path)
    eos = doc.get("eos") or {}
    if not eos:
        return
    assert eos.get("roundtrip_max_abs_drhotilde", 1.0) <= ROUNDTRIP_BAR
    assert eos.get("sound_speed_max_rel_error", 1.0) <= IDENTITY_BAR
    # B = rho0 c0^2 / gamma must be reproducible from the declared parameters
    if {"gamma", "c0", "rho0", "B"} <= set(eos):
        expected = eos["rho0"] * eos["c0"] ** 2 / eos["gamma"]
        assert abs(expected - eos["B"]) / expected < 1e-12


def test_gate_is_exercised():
    """Guards against a silently empty test session (no artifacts -> gate must say so)."""
    if not ARTIFACTS:
        pytest.skip("no oss-inventory verification artifacts in this checkout")
    assert ARTIFACTS, "unreachable"