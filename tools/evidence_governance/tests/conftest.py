"""Shared fixtures for the evidence-governance test-suite.

All test declarations use synthetic capability ids (``synth.*``). No subject
vocabulary is used anywhere in this package or its tests: the domain data enters
only through configuration / injected JSON, which is what the platform rule
"zero domain semantics" requires.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evidence_governance.runner import build_pipeline

ROOT = Path(__file__).resolve().parents[3]
PKG = Path(__file__).resolve().parents[1]
CONFIG_PATH = PKG / "config" / "governance.v1.json"
DEMO_CONFIG_PATH = PKG / "fixtures" / "governance.demo.v1.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def real_config() -> dict:
    return load(CONFIG_PATH)


@pytest.fixture()
def demo_config() -> dict:
    return load(DEMO_CONFIG_PATH)


@pytest.fixture()
def pipeline(demo_config):
    """(scanner, classifier, compiler, engine) bound to the synthetic library."""
    return build_pipeline(demo_config, ROOT)


@pytest.fixture()
def synth_items(pipeline):
    scanner, _classifier, _compiler, _engine = pipeline
    return scanner.scan().items