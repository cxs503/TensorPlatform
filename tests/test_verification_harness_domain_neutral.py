"""Zero-domain-semantics gate for the verification harness (criterion A4).

Three independent checks:

1. **Vocabulary** — the injected forbidden-token list
   (``config/verification_harness/neutrality_vocab.json``) must not appear in any
   mechanism module (no real vessel, no real waterway, no real operating point).
2. **Import graph** — the mechanism modules must not import the synthetic anchor
   application (dependency direction: mechanism -> nothing, application -> mechanism).
3. **Injection** — a brand-new domain joins by configuration only: registering an
   unseen domain label and an unseen quantity name requires zero code change.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tools.verification_harness.registry import CaseRegistry
from tools.verification_harness.runner import run_case

REPO = Path(__file__).resolve().parents[1]
VOCAB_PATH = REPO / "config" / "verification_harness" / "neutrality_vocab.json"
PACKAGE = REPO / "tools" / "verification_harness"


@pytest.fixture(scope="module")
def vocab():
    if not VOCAB_PATH.exists():
        pytest.skip("injected neutrality vocabulary not present in this checkout")
    return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))


def _mechanism_files(vocab) -> list[Path]:
    files = [REPO / rel for rel in vocab["scope"]["paths"]]
    missing = [str(p) for p in files if not p.is_file()]
    assert not missing, f"declared mechanism paths are gone: {missing}"
    return files


def _offenders_in(text: str, tokens) -> list[str]:
    return [token for token in tokens
            if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE)]


def test_vocabulary_scanner_has_teeth(vocab):
    """The gate must actually bite: a fabricated leak has to be flagged."""
    tokens = vocab["forbidden_in_mechanism_code"]
    assert tokens, "the injected vocabulary is empty -- the gate would be vacuous"
    leak = "vessel_id = " + tokens[0] + "  # smuggled operating point"
    assert _offenders_in(leak, tokens) == [tokens[0]]
    assert _offenders_in("case_id, implementation, reference, quantities", tokens) == []


def test_mechanism_code_carries_no_domain_vocabulary(vocab):
    files = _mechanism_files(vocab)
    assert len(files) >= 4, "scope shrank -- the gate would be vacuous"
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        offenders.extend(f"{path.relative_to(REPO)}: {token}" for token in _offenders_in(text, vocab["forbidden_in_mechanism_code"]))
    assert not offenders, "domain vocabulary leaked into mechanism code:\n" + "\n".join(offenders)


def test_mechanism_import_graph_does_not_reach_the_anchor_application(vocab):
    forbidden = set(vocab["scope"]["import_must_not_reach"])
    offenders = []
    for path in _mechanism_files(vocab):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [module] + [f"{module}.{alias.name}" for alias in node.names]
            else:
                continue
            for name in names:
                for bad in forbidden:
                    if name == bad or name.startswith(bad + "."):
                        offenders.append(f"{path.name}: {name}")
    assert not offenders, f"mechanism imports the application layer: {offenders}"


def test_package_tree_keeps_mechanism_and_application_separated():
    """Structural check: anchors live in their own subpackage, not in the mechanism modules."""
    assert (PACKAGE / "anchors").is_dir()
    for name in ("model.py", "registry.py", "runner.py", "evidence.py"):
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert "anchors" not in text, (
            f"{name} refers to the anchor application; the mechanism must stay domain-free"
        )


def test_new_domain_joins_by_configuration_only():
    """A never-seen domain/quantity is registered and executed without touching code."""
    registry = CaseRegistry()
    registry.register({
        "case_id": "synth-opaque-domain-case",
        "domain": "synthetic-never-seen-domain",
        "implementation": "tools.verification_harness.anchors.synthetic:slab_closed_form",
        "reference": "tools.verification_harness.anchors.synthetic:slab_closed_form",
        "inputs": {"conductivity": 2.0, "source": 0.0, "length": 1.0,
                   "t_left": 1.0, "t_right": 3.0, "nodes": 7},
        "quantities": {"invented_quantity_name": {"rtol": 1e-12}},
    })
    # the declared quantity is not produced by either callable -> fail-closed ERROR,
    # which proves nothing silently coerces unknown names
    result = run_case(registry.get("synth-opaque-domain-case"))
    assert result.verdict.value == "ERROR"
    assert "invented_quantity_name" in (result.error or "")


def test_case_declarations_use_only_synthetic_labels():
    """The shipped case package must not smuggle in real-world operating points."""
    registry = CaseRegistry()
    registry.discover(PACKAGE / "cases")
    for spec in registry:
        assert spec.domain.startswith("synthetic-"), spec.domain
        assert spec.case_id.startswith("synth-"), spec.case_id
        assert "anchor" in spec.tags