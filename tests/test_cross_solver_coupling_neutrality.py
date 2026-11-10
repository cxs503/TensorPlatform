"""Iron-law gate for the cross-solver coupling infrastructure (zero domain semantics).

The mapping package, the clock package and the benchmark must stay domain-agnostic:
the platform lexicon (``tools/evidence_domain_scan``) is reused instead of a new
word list, so the same notion of "domain vocabulary" applies platform-wide.
Additionally, the mechanism may not import a domain repository or a heavy
framework — standard library plus numpy only.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tools.evidence_domain_scan import load_lexicon, scan_paths  # noqa: E402

MECHANISM_PATHS = [
    "tools/field_mapper",
    "tensorphysics",
    "benchmarks/verified/cross_solver_coupling",
]
TEST_PATHS = [
    "tests/test_field_mapper_geometry.py",
    "tests/test_field_mapper_conservation.py",
    "tests/test_field_mapper_errors.py",
    "tests/test_clock_sync_clock.py",
    "tests/test_clock_sync_subcycling.py",
    "tests/test_clock_sync_lyapunov.py",
    "tests/test_clock_sync_manifest.py",
    "tests/test_cross_solver_coupling_benchmark.py",
    "tests/test_cross_solver_coupling_neutrality.py",
]

ALLOWED_IMPORT_ROOTS = {
    "abc", "argparse", "ast", "collections", "dataclasses", "datetime", "enum", "functools",
    "hashlib", "itertools", "json", "math", "os", "pathlib", "platform", "re", "subprocess",
    "sys", "textwrap", "typing", "warnings", "numpy", "matplotlib", "jsonschema", "__future__",
    "tools", "tensorphysics", "benchmarks", "src",
}


def _python_files(rel_paths):
    files = []
    for rel in rel_paths:
        base = ROOT / rel
        if base.is_file():
            files.append(base)
        else:
            files.extend(sorted(base.rglob("*.py")))
    return files


def test_no_domain_vocabulary_in_mechanism_or_benchmark():
    result = scan_paths(MECHANISM_PATHS + TEST_PATHS, lexicon=load_lexicon(), root=ROOT)
    assert result["scanned_files"] > 10
    offenders = [f'{hit["path"]}:{hit["line"]} {hit["term"]}' for hit in result["hits"]]
    assert not offenders, offenders


def test_mechanism_imports_stay_inside_the_allowed_set():
    problems = []
    for path in _python_files(MECHANISM_PATHS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                if root and root not in ALLOWED_IMPORT_ROOTS:
                    problems.append(f"{path.relative_to(ROOT)}: imports {root}")
    assert not problems, problems


@pytest.mark.parametrize(
    "module",
    ["tools.field_mapper", "tensorphysics.clock_sync"],
)
def test_packages_expose_documented_public_surfaces(module: str):
    import importlib

    mod = importlib.import_module(module)
    exported = getattr(mod, "__all__", None)
    assert exported, f"{module} must declare __all__"
    for name in exported:
        assert hasattr(mod, name), f"{module}.{name} is listed in __all__ but missing"
    doc = (mod.__doc__ or "").strip()
    assert len(doc) > 100, f"{module} needs a substantive module docstring"