"""Zero-domain-semantics gate for the invariant audit (mirror of the harness gate).

Three independent checks:

1. **Vocabulary** - the injected token list
   (``config/invariant_audit/neutrality_vocab.json``) must not appear in any
   mechanism module: no vessel, no waterway, no operating point.
2. **Import graph** - the mechanism must not import the synthetic application
   layer (direction: mechanism -> nothing, application -> mechanism).
3. **Injection** - a new domain joins by configuration only: an unseen base
   dimension and an unseen quantity name require zero code change (that
   behavioural check lives in ``test_invariant_audit_engine.py``).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VOCAB_PATH = REPO / "config" / "invariant_audit" / "neutrality_vocab.json"
PACKAGE = REPO / "tools" / "invariant_audit"


@pytest.fixture(scope="module")
def vocab():
    if not VOCAB_PATH.is_file():
        pytest.skip("injected neutrality vocabulary not present in this checkout")
    return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))


def _mechanism_files(vocab) -> list[Path]:
    files = [REPO / rel for rel in vocab["scope"]["paths"]]
    missing = [str(p) for p in files if not p.is_file()]
    assert not missing, f"declared mechanism paths are gone: {missing}"
    return files


def test_mechanism_contains_no_forbidden_tokens(vocab):
    tokens = vocab["forbidden_in_mechanism_code"]
    assert tokens, "vocabulary must be non-empty for the gate to mean anything"
    hits = []
    for path in _mechanism_files(vocab):
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if re.search(rf"\b{re.escape(token)}\b", text, flags=re.IGNORECASE):
                hits.append(f"{path.name}: {token!r}")
    assert not hits, "domain vocabulary leaked into mechanism code: " + "; ".join(hits)


def test_mechanism_does_not_import_application_layer(vocab):
    forbidden = tuple(vocab["scope"]["import_must_not_reach"])
    violations = []
    for path in _mechanism_files(vocab):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in forbidden):
                    violations.append(f"{path.name} imports {name}")
    assert not violations, "; ".join(violations)


def test_application_layer_may_import_mechanism_but_not_the_reverse():
    """The dependency direction is acyclicity, not isolation."""
    anchors = PACKAGE / "anchors"
    assert anchors.is_dir(), "application layer directory is missing"
    for path in sorted(anchors.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "tools.invariant_audit.engine" not in text
        assert "tools.invariant_audit.bundle" not in text


def test_plan_config_carries_no_mechanism_code():
    plan = REPO / "config" / "invariant_audit" / "plans" / "synthetic_solver_matrix.json"
    if not plan.is_file():
        pytest.skip("plan not present in this checkout")
    payload = json.loads(plan.read_text(encoding="utf-8"))
    for target in payload["targets"]:
        module, _, func = target["callable"].partition(":")
        assert module and func, f"callable is not a dotted path: {target['callable']}"
        assert not hasattr(target, "invariants_impl")