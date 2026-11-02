#!/usr/bin/env python3
"""Test-surface census — static detection of *silently dead* test code.

Motivation
----------
A test module can be collected by pytest and still contribute nothing: a
``test_*.py`` file whose checks live in a ``main()`` / ``check_*()`` helper is
collected as a module, yields zero items, and reports green forever.  The same
class of defect hides in assertion-free test functions and in "oracle"
functions that no test ever calls.

This tool performs a **pure-AST** census (no imports, no test execution, no
network, no DB) so it is cheap enough to run on every commit and safe to run
under heavy host load.  Categories:

``vacuous_module``      test-named file that pytest collects but that defines
                        zero collectible test symbols (TestCase subclasses and
                        ``Test*`` classes are honoured, so the count matches
                        pytest semantics rather than a naive ``def test_`` grep)
``assertion_free``      test function whose body contains no oracle (no
                        ``assert``, no ``pytest.raises/warns/approx``, no
                        ``self.assert*``, no ``fail()``)
``orphan_oracle``       non-test helper in a test file that contains asserts
                        but is referenced by no test and no other helper ->
                        dead oracle
``module_level_skip``   ``pytest.skip(..., allow_module_level=True)``
``unconditional_skip``  a bare ``@pytest.mark.skip`` decorator, or a
                        ``pytest.skip(...)`` as the first statement of a test
``import_side_effect``  module-level mutation of process-global state
                        (``os.environ[...] = ...`` / ``database.config.engine =
                        ...``) in a test file — collection-time contamination

Usage
-----
    python3 tools/audit/test_surface_census.py                     # human table
    python3 tools/audit/test_surface_census.py --json out.json \\
        --markdown out.md                                          # artifacts
    python3 tools/audit/test_surface_census.py --baseline b.json --check
                                                                   # CI gate

``--check`` exits non-zero when a countable defect appears that is not recorded
in the baseline allowlist (baseline entries carry a ``reason``).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Sequence

TEST_FILE_PREFIX = "test_"
TEST_FILE_SUFFIX = "_test.py"

# Call names that constitute an oracle even without an `assert` statement.
ORACLE_CALL_NAMES = frozenset(
    {
        "raises",
        "warns",
        "deprecated_call",
        "approx",
        "fail",
        "exit",
        "assert_allclose",
        "assert_array_equal",
        "assert_array_almost_equal",
        "assert_equal",
        "assert_almost_equal",
        # validators that fail by raising (jsonschema.validate, json.dumps on a
        # non-serialisable payload, ...) — the raise *is* the assertion
        "validate",
        "validate_all",
        "dumps",
    }
)

DEFECT_KINDS = (
    "vacuous_module",
    "assertion_free",
    "orphan_oracle",
    "module_level_skip",
    "unconditional_skip",
    "import_side_effect",
)


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _callee_name(node: ast.AST) -> str:
    func = getattr(node, "func", None)
    if func is None:
        return ""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _call_source(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - very old AST shapes
        return ""


def is_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith(TEST_FILE_PREFIX) or name.endswith(TEST_FILE_SUFFIX)


def _has_testcase_base(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name and "TestCase" in name:
            return True
    return False


def _test_methods(cls: ast.ClassDef) -> list[ast.AST]:
    return [
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    ]


def collectible_test_symbols(tree: ast.Module) -> list[ast.AST]:
    """Test functions/classes pytest would actually collect (no execution)."""
    found: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test") and not node.name.startswith("_"):
                found.append(node)
        elif isinstance(node, ast.ClassDef):
            # pytest collects `Test*` classes (unless they define __init__)
            # and, via the unittest plugin, any TestCase subclass.
            names_init = any(
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name == "__init__"
                for stmt in node.body
            )
            if _has_testcase_base(node) or (node.name.startswith("Test") and not names_init):
                found.extend(_test_methods(node))
    return found


def _strip_docstring(body: Sequence[ast.stmt]) -> list[ast.stmt]:
    out = list(body)
    if out and isinstance(out[0], ast.Expr) and isinstance(out[0].value, ast.Constant):
        if isinstance(out[0].value.value, str):
            out = out[1:]
    return out


def has_oracle(func: ast.AST) -> bool:
    """True when the callable body contains something that can fail."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            name = _callee_name(node)
            if not name:
                continue
            if name.startswith("assert"):
                return True
            if name in ORACLE_CALL_NAMES:
                return True
    return False


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every bare/attribute call or reference name appearing in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def has_delegated_oracle(func: ast.AST, defs: dict[str, ast.AST]) -> bool:
    """True when ``func`` delegates its oracle to a module-level helper.

    Keeps the census from punishing the (good) style of factoring a predicate
    into a named helper: ``def test_x(): assert_matches_reference(x)``.
    Reachability is transitive, mirroring the orphan check.
    """
    if has_oracle(func):
        return True
    import re as _re

    seen: set[str] = set()
    frontier = [_call_source(func)]
    while frontier:
        source = frontier.pop()
        for name, node in defs.items():
            if name in seen:
                continue
            if not _re.search(rf"\b{_re.escape(name)}\b", source):
                continue
            seen.add(name)
            if has_oracle(node):
                return True
            frontier.append(_call_source(node))
    return False


def _module_level_import_side_effects(tree: ast.Module) -> list[str]:
    """Detect collection-time mutation of process-global state."""
    hits: list[str] = []
    for node in tree.body:
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for target in targets:
            src = _call_source(target)
            if not src:
                continue
            if src.startswith("os.environ[") or src.startswith("environ["):
                hits.append(f"module-level env mutation: {src} = ...")
            elif src.endswith(".engine") or src.endswith(".SessionLocal"):
                if "." in src:  # attribute rebinding of a shared config object
                    hits.append(f"module-level engine rebinding: {src} = ...")
        del value
    return hits


def _unconditional_skip(func: ast.AST) -> list[str]:
    hits: list[str] = []
    for decorator in getattr(func, "decorator_list", []):
        src = _call_source(decorator)
        if "skip" not in src:
            continue
        if "skipif" in src or "skipUnless" in src:
            continue
        hits.append(f"bare skip decorator @{src}")
    body = _strip_docstring(getattr(func, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Call):
        call = body[0].value
        name = _callee_name(call)
        if name in ("skip", "xfail") and "skipif" not in _call_source(call):
            hits.append(f"first-statement pytest.{name}: {_call_source(call)[:80]}")
    return hits


def _module_level_skip(tree: ast.Module) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee_name(node) == "skip":
            src = _call_source(node)
            if "allow_module_level" in src:
                hits.append(src[:140])
    return hits


# --------------------------------------------------------------------------- #
# Census
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    kind: str
    path: str
    symbol: str
    detail: str = ""


@dataclass
class Census:
    roots: list[str] = field(default_factory=list)
    files: int = 0
    test_files: int = 0
    test_functions: int = 0
    oracle_asserts: int = 0
    findings: list[Finding] = field(default_factory=list)

    def by_kind(self) -> dict[str, int]:
        counts = {kind: 0 for kind in DEFECT_KINDS}
        for f in self.findings:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        return counts

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["counts"] = self.by_kind()
        return payload


def iter_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def census_file(path: Path, root: Path) -> tuple[int, int, int, list[Finding]]:
    """Return (is_test_file, n_test_symbols, n_asserts, findings)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return (0, 0, 0, [])
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    findings: list[Finding] = []
    asserts = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Assert))

    if not is_test_file(path):
        return (0, 0, asserts, findings)

    symbols = collectible_test_symbols(tree)

    if not symbols:
        findings.append(
            Finding(
                "vacuous_module",
                rel,
                "<module>",
                "pytest collects the module but it yields zero test items"
                + (" (checks exist only under __main__)" if has_oracle(tree) else ""),
            )
        )

    module_defs = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test")
    }

    for sym in symbols:
        if not has_delegated_oracle(sym, module_defs):
            findings.append(
                Finding("assertion_free", rel, getattr(sym, "name", "<anon>"), "no oracle in body")
            )
        for detail in _unconditional_skip(sym):
            findings.append(Finding("unconditional_skip", rel, getattr(sym, "name", "?"), detail))

    for detail in _module_level_skip(tree):
        findings.append(Finding("module_level_skip", rel, "<module>", detail))

    for detail in _module_level_import_side_effects(tree):
        findings.append(Finding("import_side_effect", rel, "<module>", detail))

    # Orphan oracles: assert-bearing helpers that no test can reach.
    # Reachability is transitive (test -> helper -> helper): a helper called
    # only by another helper is still live.
    defs = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test")
    }
    import re as _re

    reachable: set[str] = set()
    frontier = [_call_source(sym) for sym in symbols]
    # a function only reachable from the ``if __name__ == "__main__":`` block is a
    # script entry point, not a dead oracle (e.g. artifact writers)
    for node in tree.body:
        if isinstance(node, ast.If):
            guard = _call_source(node.test)
            if "__name__" in guard:
                frontier.extend(_call_source(stmt) for stmt in node.body)
    while frontier:
        source = frontier.pop()
        for name, node in defs.items():
            if name in reachable:
                continue
            if not _re.search(rf"\b{_re.escape(name)}\b", source):
                continue
            reachable.add(name)
            frontier.append(_call_source(node))

    for name, node in defs.items():
        if name in reachable or not has_oracle(node):
            continue
        findings.append(
            Finding(
                "orphan_oracle",
                rel,
                name,
                "assert-bearing helper not reachable from any test (dead oracle)",
            )
        )

    return (1, len(symbols), asserts, findings)


def run_census(roots: Sequence[Path], base: Path) -> Census:
    census = Census(roots=[str(r) for r in roots])
    for path in iter_files(roots):
        is_test, n_symbols, n_asserts, findings = census_file(path, base)
        census.files += 1
        if is_test:
            census.test_files += 1
            census.test_functions += n_symbols
        census.oracle_asserts += n_asserts
        census.findings.extend(findings)
    census.findings.sort(key=lambda f: (f.kind, f.path, f.symbol))
    return census


# --------------------------------------------------------------------------- #
# Reporting / gating
# --------------------------------------------------------------------------- #
def render_markdown(census: Census) -> str:
    counts = census.by_kind()
    lines = [
        "# Test-surface census",
        "",
        f"- roots: {', '.join(census.roots)}",
        f"- python files scanned: {census.files}",
        f"- test files: {census.test_files}",
        f"- collectible test functions: {census.test_functions}",
        f"- `assert` statements in scanned tree: {census.oracle_asserts}",
        "",
        "| defect kind | count |",
        "| --- | --- |",
    ]
    lines += [f"| `{kind}` | {counts.get(kind, 0)} |" for kind in DEFECT_KINDS]
    if census.findings:
        lines += ["", "| kind | file | symbol | detail |", "| --- | --- | --- | --- |"]
        for f in census.findings:
            detail = f.detail.replace("|", "\\|")
            lines.append(f"| `{f.kind}` | `{f.path}` | `{f.symbol}` | {detail} |")
    return "\n".join(lines) + "\n"


def load_baseline(path: Path) -> dict:
    if not path.exists():
        return {"entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def debt_keys(findings: Iterable[Finding]) -> set[str]:
    """Baseline identity of a finding: kind::path::symbol (detail may drift)."""
    return {f"{f.kind}::{f.path}::{f.symbol}" for f in findings}


def check_against_baseline(census: Census, baseline: dict) -> list[str]:
    allowed = {e["key"] for e in baseline.get("entries", [])}
    return sorted(debt_keys(census.findings) - allowed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", action="append", default=None, help="root dir to scan (repeatable)")
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument("--markdown", dest="md_out", default=None)
    parser.add_argument("--baseline", default=None, help="baseline allowlist json")
    parser.add_argument("--check", action="store_true", help="exit 1 on new un-baselined debt")
    args = parser.parse_args(argv)

    base = Path.cwd()
    roots = [Path(r) for r in (args.root or ["tests"])]
    roots = [r if r.is_absolute() else (base / r) for r in roots]

    census = run_census(roots, base)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(census.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.md_out:
        Path(args.md_out).write_text(render_markdown(census), encoding="utf-8")

    counts = census.by_kind()
    print(
        f"test files={census.test_files} test functions={census.test_functions} "
        + " ".join(f"{k}={v}" for k, v in counts.items())
    )
    for f in census.findings:
        print(f"  [{f.kind}] {f.path}::{f.symbol} — {f.detail}")

    if args.check:
        if not args.baseline:
            print("--check requires --baseline", file=sys.stderr)
            return 2
        new_debt = check_against_baseline(census, load_baseline(Path(args.baseline)))
        if new_debt:
            print(f"\nNEW test-surface debt ({len(new_debt)}):", file=sys.stderr)
            for key in new_debt:
                print(f"  {key}", file=sys.stderr)
            return 1
        print("\ntest-surface census: no new debt vs baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())