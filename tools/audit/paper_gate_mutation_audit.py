#!/usr/bin/env python3
"""Mutation audit for the manuscript traceability gate (PAPER-TRACE).

Why this exists
---------------
``backend/tests/test_paper_traceability_gate.py`` pins 21 behaviours of
``tools/paper_gate/traceability_gate.py`` (PG1-PG8).  Green tests prove the
suite *runs*; they do not prove the suite would *notice* if the gate stopped
checking something.  A verifier that silently stops verifying is indistinguishable
from a passing manuscript — the exact failure class the gate was written to kill
(H2 WITHHELD / H3 synthetic data), one level up.

This tool answers the only question that matters about a verification gate:
**mutate the gate and see whether its own tests go red.**  A surviving mutant is
a documented blind spot of the suite, not of the gate.

Method
------
1. Parse the gate source with :mod:`ast`, enumerate single-site mutants
   (comparison / arithmetic / boolean operator swaps, dropped ``findings.append``
   calls, ``return findings`` demoted to an empty list, numeric literal scaling,
   ``FAIL``<->``WARN`` severity flips, ``not`` removal).
2. For each mutant: rewrite the module in a scratch tree, run the gate's test
   file against it, and record killed / survived.
3. Emit JSON: kill rate overall + per operator class + the surviving mutants
   with their source line, so follow-up tests can be written against the real
   blind spots rather than guessed ones.

Usage
-----
    python3 tools/audit/paper_gate_mutation_audit.py \
        --module tools/paper_gate/traceability_gate.py \
        --tests backend/tests/test_paper_traceability_gate.py \
        --json-out artifacts/paper_gate_mutation/audit.json [--jobs 4]

Exit code 0 = audit completed (kill rate is data, not a verdict); 2 = usage.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

CMP_SWAP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
BIN_SWAP = {
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.Div, ast.Div: ast.Mult,
}
BOOL_SWAP = {ast.And: ast.Or, ast.Or: ast.And}
SEVERITY_SWAP = {"FAIL": "WARN", "WARN": "FAIL"}


@dataclass
class Mutant:
    op: str            # operator class
    index: int         # site index inside the op class
    line: int          # 1-based source line of the mutated node
    snippet: str       # source text of the mutated node (pre-mutation)
    patch: str = ""    # short description of the change
    killed: bool = False
    detail: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"{self.op}[{self.index}]"


class _Collector(ast.NodeVisitor):
    """Enumerate mutable sites in a stable, deterministic order."""

    def __init__(self, src: str):
        self.src = src
        self.lines = src.splitlines()
        self.sites: list[tuple[str, int, ast.AST]] = []

    def _snip(self, node) -> str:
        try:
            return ast.get_source_segment(self.src, node) or ""
        except Exception:  # pragma: no cover - defensive
            return ""

    # ── operator swaps ────────────────────────────────────────────────
    def visit_Compare(self, node):
        for i, op in enumerate(node.ops):
            if type(op) in CMP_SWAP:
                new = CMP_SWAP[type(op)]
                self.sites.append(("cmp_swap", i, _RewriteCompare(node, i, new)))
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if type(node.op) in BIN_SWAP:
            self.sites.append(("binop_swap", 0, _RewriteBinOp(node, BIN_SWAP[type(node.op)])))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        if type(node.op) in BOOL_SWAP:
            self.sites.append(("boolop_swap", 0, _RewriteBoolOp(node, BOOL_SWAP[type(node.op)])))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not):
            self.sites.append(("not_removal", 0, _RewriteDropNot(node)))

    # ── silent-drop mutants ───────────────────────────────────────────
    def visit_Expr(self, node):
        self.generic_visit(node)
        c = node.value
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr in ("append", "extend")
                and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "findings"):
            self.sites.append(("append_drop", 0, _RewriteToPass(node)))

    def visit_Return(self, node):
        self.generic_visit(node)
        v = node.value
        if (isinstance(v, ast.Tuple) and v.elts
                and isinstance(v.elts[0], ast.Name) and v.elts[0].id == "findings"):
            self.sites.append(("return_empty", 0, _RewriteReturnEmpty(node)))

    # ── literal mutants ───────────────────────────────────────────────
    def visit_Constant(self, node):
        v = node.value
        if isinstance(v, str) and v in SEVERITY_SWAP:
            self.sites.append(("severity_flip", 0,
                               _RewriteConstant(node, SEVERITY_SWAP[v])))
        elif isinstance(v, bool):
            return
        elif isinstance(v, (int, float)) and v not in (0, 1):
            self.sites.append(("const_scale", 0, _RewriteConstant(node, v * 10)))


class _Rewrite(ast.NodeTransformer):
    """Base: replace exactly one target node, leave everything else alone."""

    target: ast.AST
    replacements: int = 0

    def _hit(self, node) -> bool:
        if node is self.target and self.replacements == 0:
            self.replacements += 1
            return True
        return False


class _RewriteCompare(_Rewrite):
    def __init__(self, node, i, new_op):
        self.target, self.i, self.new_op = node, i, new_op

    def visit_Compare(self, node):
        if self._hit(node):
            ops = list(node.ops)
            ops[self.i] = self.new_op()
            return ast.Compare(left=node.left, ops=ops, comparators=node.comparators)
        return self.generic_visit(node)


class _RewriteBinOp(_Rewrite):
    def __init__(self, node, new_op):
        self.target, self.new_op = node, new_op

    def visit_BinOp(self, node):
        if self._hit(node):
            return ast.BinOp(left=node.left, op=self.new_op(), right=node.right)
        return self.generic_visit(node)


class _RewriteBoolOp(_Rewrite):
    def __init__(self, node, new_op):
        self.target, self.new_op = node, new_op

    def visit_BoolOp(self, node):
        if self._hit(node):
            return ast.BoolOp(op=self.new_op(), values=node.values)
        return self.generic_visit(node)


class _RewriteDropNot(_Rewrite):
    def __init__(self, node):
        self.target = node

    def visit_UnaryOp(self, node):
        if self._hit(node):
            return node.operand
        return self.generic_visit(node)


class _RewriteToPass(_Rewrite):
    def __init__(self, node):
        self.target = node

    def visit_Expr(self, node):
        if self._hit(node):
            return ast.Pass()
        return self.generic_visit(node)


class _RewriteReturnEmpty(_Rewrite):
    def __init__(self, node):
        self.target = node

    def visit_Return(self, node):
        if self._hit(node):
            elts = [ast.List(elts=[], ctx=ast.Load())]
            elts += [e for e in node.value.elts[1:]]
            return ast.Return(value=ast.Tuple(elts=elts, ctx=ast.Load()))
        return self.generic_visit(node)


class _RewriteConstant(_Rewrite):
    def __init__(self, node, new_value):
        self.target, self.new_value = node, new_value

    def visit_Constant(self, node):
        if self._hit(node):
            return ast.Constant(value=self.new_value)
        return self.generic_visit(node)


def enumerate_mutants(src: str) -> list[Mutant]:
    tree = ast.parse(src)
    col = _Collector(src)
    col.visit(tree)
    out: list[Mutant] = []
    counts: dict[str, int] = {}
    for op, idx, rw in col.sites:
        i = counts.get(op, 0)
        counts[op] = i + 1
        node = rw.target
        new_node = rw.visit(node)
        new_line = ast.unparse(new_node).splitlines() if new_node is not None else ["<removed>"]
        old_line = ast.unparse(node).splitlines()
        out.append(Mutant(
            op=op, index=i, line=getattr(node, "lineno", 0),
            snippet=(ast.get_source_segment(src, node) or ast.unparse(node))[:160],
            patch=(" | ".join(old_line)[:80] + "  =>  " + " | ".join(new_line)[:80]),
            extra={"site": idx},
        ))
    return out


def apply_mutant(src: str, mutant: Mutant) -> str:
    tree = ast.parse(src)
    col = _Collector(src)
    col.visit(tree)
    by_op: dict[str, list] = {}
    for op, idx, rw in col.sites:
        by_op.setdefault(op, []).append(rw)
    rw = by_op[mutant.op][mutant.index]
    new_tree = rw.visit(tree)
    return ast.unparse(ast.fix_missing_locations(new_tree)) + "\n"


def _run_pytest(workdir: Path, tests, timeout: int) -> tuple[int, str]:
    tests = [tests] if isinstance(tests, (str, Path)) else list(tests)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-m", "pytest", *[str(t) for t in tests], "-q", "--noconftest",
           "-p", "no:cacheprovider", "-x"]
    try:
        p = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-800:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def audit(module: Path, tests, jobs: int = 4, timeout: int = 180) -> dict:
    tests = [tests] if isinstance(tests, (str, Path)) else list(tests)
    src = module.read_text(encoding="utf-8")
    root = module.resolve().parents[2] if len(module.resolve().parents) > 2 else Path.cwd()
    mutants = enumerate_mutants(src)
    base = Path(tempfile.mkdtemp(prefix="pgmut-"))
    try:
        # scratch tree preserves the repo-relative layout the test file expects
        for rel in [module.relative_to(root)] + [t.relative_to(root) for t in tests]:
            dst = base / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(module.parent, base / module.parent.relative_to(root),
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        for t in tests:
            (base / t.relative_to(root)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(t, base / t.relative_to(root))
        mod_rel = module.relative_to(root)
        tests_rel = [t.relative_to(root) for t in tests]
        mod_path = base / mod_rel
        pristine = mod_path.read_text(encoding="utf-8")

        # sanity: pristine tree must be green, else every mutant looks "killed"
        rc, out = _run_pytest(base, [base / r for r in tests_rel], timeout)
        if rc != 0:
            raise SystemExit(f"baseline suite is not green in scratch tree:\n{out}")

        def work(m: Mutant) -> Mutant:
            local = Path(tempfile.mkdtemp(prefix="pgmut-run-", dir=str(base)))
            shutil.copytree(base, local, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("pgmut-run-*", "__pycache__"))
            try:
                (local / mod_rel).write_text(apply_mutant(pristine, m), encoding="utf-8")
                rc, out = _run_pytest(local, [local / r for r in tests_rel], timeout)
                m.killed = rc != 0
                m.detail = "" if m.killed else out.strip().splitlines()[-1][:200] if out.strip() else ""
                if rc == 124:
                    m.detail = "timeout"
                return m
            finally:
                shutil.rmtree(local, ignore_errors=True)

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            mutants = list(ex.map(work, mutants))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    by_op: dict[str, dict[str, int]] = {}
    for m in mutants:
        d = by_op.setdefault(m.op, {"total": 0, "killed": 0})
        d["total"] += 1
        d["killed"] += int(m.killed)
    killed = sum(1 for m in mutants if m.killed)
    return {
        "module": str(module),
        "tests": [str(t) for t in tests],
        "baseline_green": True,
        "total": len(mutants),
        "killed": killed,
        "survived": len(mutants) - killed,
        "kill_rate": round(killed / len(mutants), 4) if mutants else 0.0,
        "by_operator": {k: {**v, "kill_rate": round(v["killed"] / v["total"], 4)}
                        for k, v in sorted(by_op.items())},
        "survivors": [
            {"name": m.name, "line": m.line, "snippet": m.snippet, "patch": m.patch}
            for m in mutants if not m.killed
        ],
        "mutants": [
            {"name": m.name, "op": m.op, "line": m.line, "killed": m.killed,
             "snippet": m.snippet, "patch": m.patch, "detail": m.detail}
            for m in mutants
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="mutation audit for PAPER-TRACE gate")
    ap.add_argument("--module", default="tools/paper_gate/traceability_gate.py")
    ap.add_argument("--tests", default="backend/tests/test_paper_traceability_gate.py,"
                                      "backend/tests/test_paper_traceability_gate_kills.py")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)

    module = Path(a.module).resolve()
    tests = [Path(t).resolve() for t in a.tests.split(",") if t.strip()]
    a.tests = tests[0]
    if not module.is_file() or not all(t.is_file() for t in tests):
        print("module or test file not found", file=sys.stderr)
        return 2
    res = audit(module, tests, jobs=a.jobs, timeout=a.timeout)
    if a.json_out:
        p = Path(a.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("total", "killed", "survived", "kill_rate",
                                          "by_operator")}, ensure_ascii=False, indent=2))
    print(f"survivors: {len(res['survivors'])}")
    for s in res["survivors"][:40]:
        print(f"  {s['name']:>16} L{s['line']:<4} {s['snippet'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())