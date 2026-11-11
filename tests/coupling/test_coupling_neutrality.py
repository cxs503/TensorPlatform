"""Iron-law gate for ``backend/coupling``: zero domain semantics, stdlib + numpy only.

Extends the platform-wide evidence-domain-scan to the coupling mechanism package
(the same lexicon the rest of the platform uses, so "domain vocabulary" means the
same thing everywhere).  Also enforces the public-surface discipline for the
space-time exchange modules added by node IN387-PLAT01.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evidence_domain_scan import load_lexicon, scan_paths  # noqa: E402

MECHANISM_PACKAGE = "backend/coupling"
ALLOWED_IMPORT_ROOTS = {
    "abc", "argparse", "ast", "collections", "dataclasses", "datetime", "enum",
    "functools", "hashlib", "itertools", "json", "math", "os", "pathlib",
    "platform", "re", "subprocess", "sys", "textwrap", "time", "typing",
    "warnings", "numpy", "__future__",
}
NEW_MODULES = ("temporal_interp", "moment_remap", "spacetime_exchange")


def _python_files(rel: str):
    return sorted((ROOT / rel).rglob("*.py"))


def test_no_domain_vocabulary_in_the_coupling_package():
    result = scan_paths([MECHANISM_PACKAGE], lexicon=load_lexicon(), root=ROOT)
    assert result["scanned_files"] >= 8
    offenders = [f'{hit["path"]}:{hit["line"]} {hit["term"]}' for hit in result["hits"]]
    assert not offenders, offenders


def test_coupling_imports_stay_inside_stdlib_plus_numpy():
    problems = []
    for path in _python_files(MECHANISM_PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                roots = [(node.module or "").split(".")[0]]
            for root in roots:
                if root and root not in ALLOWED_IMPORT_ROOTS:
                    problems.append(f"{path.relative_to(ROOT)}: imports {root}")
    assert not problems, problems


@pytest.mark.parametrize("module", list(NEW_MODULES))
def test_new_modules_expose_a_documented_public_surface(module: str):
    mod = importlib.import_module(f"coupling.{module}")
    exported = getattr(mod, "__all__", None)
    assert exported, f"coupling.{module} must declare __all__"
    for name in exported:
        assert hasattr(mod, name), f"{module}.{name} listed in __all__ but missing"
    assert len((mod.__doc__ or "").strip()) > 400, "module docstring must be substantive"


def test_public_surface_rejects_domain_specific_names():
    """The mechanism may not grow a domain vocabulary through its API either.

    The forbidden tokens are taken from the platform lexicon (single source of
    truth) instead of a local word list, so this gate cannot drift from the rest
    of the platform.
    """
    lexicon = load_lexicon()
    ascii_terms = [t for t in lexicon.get("ascii_terms", []) if len(t) > 3]
    cjk_terms = list(lexicon.get("cjk_terms", []))
    for module in NEW_MODULES:
        mod = importlib.import_module(f"coupling.{module}")
        text = (mod.__doc__ or "").lower()
        for name in getattr(mod, "__all__", []):
            lowered = name.lower()
            for term in ascii_terms:
                assert term.lower() not in lowered.split("_"), f"{module}.{name} carries {term}"
            for term in cjk_terms:
                assert term not in name, f"{module}.{name} carries {term}"
        for term in ascii_terms:
            assert f" {term} " not in f" {text} ", f"{module} docstring carries {term}"
        for term in cjk_terms:
            assert term not in text, f"{module} docstring carries {term}"
