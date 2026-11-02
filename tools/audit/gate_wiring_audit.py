#!/usr/bin/env python3
"""Gate wiring reality audit (m2049).

Motivation
----------
The platform's governance KPI reads "M-G1 schema+perf_gate 已就绪待全量接线" —
i.e. gates that *exist* and are *declared ready* but may never execute on the
live path.  A gate that only tests import is paper-ready, not protection.

This tool answers, machine-decided and rerunnable offline:

    for every gate module in the repo, is it reachable from a production
    entrypoint (runtime app, CI-invoked script), from tools only, from tests
    only, or from nothing at all?

Method (pure stdlib, static, no imports of audited code)
--------------------------------------------------------
1. ``inventory``   — discover gate modules by filename pattern (``*gate*``)
                     under production roots, excluding test trees.
2. ``import graph``— AST-parsed local import edges for every .py in the repo;
                     dotted names resolved against a package-suffix index.
3. ``entrypoints`` — runtime apps (``backend/main.py``, ``*factory_app.py``) and
                     scripts/commands invoked by ``.github/workflows/*.yml`` and
                     ``Makefile`` (CI surface), plus ``tools/`` scripts.
4. ``verdict``     — transitive closure from each entrypoint class decides the
                     verdict ladder WIRED_RUNTIME > WIRED_CI > WIRED_TOOL >
                     TEST_ONLY > ORPHAN.
5. ``claims``      — docs lines asserting a gate is wired/ready, cross-checked
                     against the verdict => CONTRADICTION list.

Verdicts are *static reachability*, not runtime proof: they can prove absence of
wiring, and can only suggest presence.  This limitation is reported, not hidden.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from pathlib import Path

GATE_NAME_RE = re.compile(r"gate", re.IGNORECASE)
PROD_ROOTS = ("backend", "src", "api", "optimizer", "scripts", "tools")
TEST_ROOTS = ("tests", "backend/tests", "test", "tests_broken_ice", "eval_set")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build",
             "dist", ".mypy_cache", ".pytest_cache", "site-packages"}

CLAIM_RE = re.compile(r"(已接线|已就绪|已挂载|已接入|已生效|已启用|ready\s*(?:to|for)\s*wir|"
                      r"wired|mounted|已上线)")

VERDICT_ORDER = ("WIRED_RUNTIME", "WIRED_CI", "TOOL_ONLY", "TEST_ONLY", "ORPHAN")


# --------------------------------------------------------------------------- #
# repo model
# --------------------------------------------------------------------------- #
@dataclass
class PyFile:
    rel: str                     # posix path relative to repo root
    is_test: bool
    imports: set[str] = field(default_factory=set)   # resolved rel paths
    raw_imports: list[str] = field(default_factory=list)
    parse_error: str | None = None


def _iter_py(root: Path):
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0] in TEST_ROOTS or "/".join(parts[:2]) in TEST_ROOTS:
        return True
    name = parts[-1]
    # filename heuristics apply only INSIDE test trees: a production module such
    # as ``backend/services/test_gate.py`` is not a test (that mis-classification
    # silently dropped a gate from the inventory in the first version).
    return name == "conftest.py"


def _package_suffixes(rel: str, root: Path | None = None) -> list[str]:
    """Dotted names a module can be imported under.

    Every ancestor directory that is a package (``__init__.py``) or one of the
    declared package roots gives a different sys.path cut, so a single file may
    be importable as several dotted names, e.g.

    ``backend/services/truth_gates_aggregator.py`` →
    ``backend.services.truth_gates_aggregator`` (repo root on sys.path) and
    ``services.truth_gates_aggregator`` (``backend`` on sys.path).

    Resolution must prefer these *exact* dotted names; matching on the bare stem
    is only a last-resort fallback (it is ambiguous by construction: three
    different ``truth_gates.py`` files live in this repo).
    """
    parts = rel[:-3].split("/")            # drop .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    out: list[str] = []
    # a cut parts[i:] is importable whenever directories[:i] can sit on sys.path,
    # i.e. parts[i] is a real package directory (has __init__.py).  The parent of
    # that package need NOT be a package itself — this repo runs with ``backend``
    # on sys.path, so ``models.truth_gates`` is valid although ``backend`` lacks
    # ``__init__.py``.  Missing this cut was a real mis-resolution bug (it made
    # ``backend/models/truth_gates.py`` look unreferenced).
    for i in range(len(parts) - 1):
        d = root / "/".join(parts[: i + 1])
        if (d / "__init__.py").is_file():
            out.append(".".join(parts[i:]))
    if parts:
        out.append(parts[-1])              # last-resort stem
    seen, uniq = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x); uniq.append(x)
    return uniq


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.files: dict[str, PyFile] = {}
        self.index: dict[str, list[str]] = defaultdict(list)   # dotted → rel paths
        self._build()

    def _build(self):
        for p in _iter_py(self.root):
            rel = p.relative_to(self.root).as_posix()
            pf = PyFile(rel=rel, is_test=_is_test_path(rel))
            self.files[rel] = pf
            for suf in _package_suffixes(rel, self.root):
                self.index[suf].append(rel)
        for pf in self.files.values():
            self._parse(pf)

    def _resolve(self, dotted: str, from_rel: str) -> str | None:
        exact = self.index.get(dotted)
        cands = exact or self.index.get(dotted.split(".")[-1])
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        if exact:
            # exact dotted name matched several files (duplicate package roots):
            # prefer the one whose package prefix the importing file shares
            stem_path = dotted.replace(".", "/")
            pref = [c for c in cands if c.endswith(stem_path + ".py")]
            if len(pref) == 1:
                return pref[0]
            cands = pref or cands
        # prefer same top-level package as the importing file, then shallowest
        top = from_rel.split("/")[0]
        same = [c for c in cands if c.split("/")[0] == top]
        pool = same or cands
        return sorted(pool, key=lambda c: (c.count("/"), c))[0]

    def _parse(self, pf: PyFile):
        path = self.root / pf.rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError) as exc:   # pragma: no cover - data dependent
            pf.parse_error = f"{type(exc).__name__}: {exc}"
            return
        pkg_parts = pf.rel[:-3].split("/")[:-1]

        def add(dotted: str):
            pf.raw_imports.append(dotted)
            target = self._resolve(dotted, pf.rel)
            if target and target != pf.rel:
                pf.imports.add(target)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    add(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:                      # relative import
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                    mod = ".".join(base + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                if not mod:
                    continue
                add(mod)
                for a in node.names:                # `from pkg import mod`
                    add(f"{mod}.{a.name}")

    # ---- reachability ---------------------------------------------------- #
    def closure(self, roots: list[str]) -> set[str]:
        seen: set[str] = set()
        queue = deque(r for r in roots if r in self.files)
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in self.files[cur].imports:
                if nxt not in seen:
                    queue.append(nxt)
        return seen

    def direct_importers(self, rel: str) -> list[str]:
        return sorted(f.rel for f in self.files.values() if rel in f.imports)


# --------------------------------------------------------------------------- #
# entrypoints from CI surface
# --------------------------------------------------------------------------- #
CMD_RE = re.compile(r"(?:python3?(?:\s+-m)?\s+)?([\w./-]+\.py)\b|"
                    r"python3?\s+-m\s+pytest\s+([\w./-]+)")
PYTEST_RE = re.compile(r"\bpytest\b([^|;&\n]*)")


def ci_invoked(repo_root: Path) -> tuple[list[str], list[str], list[str]]:
    """Return (script_paths, test_paths, raw_command_lines) *actually executed* by CI.

    Only command bodies count: ``run:`` blocks in workflow YAML and Makefile
    recipes (tab-indented).  Trigger **path filters** (``- 'scripts/foo.py'``
    under ``paths:``) are NOT invocations — treating them as such was the first
    version's false positive (it "wired" ``scripts/verify_soul_phase1.py``).
    """
    scripts: list[str] = []
    tests: list[str] = []
    lines: list[str] = []
    sources: list[Path] = []
    wf = repo_root / ".github" / "workflows"
    if wf.is_dir():
        sources += sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml"))
    mk = repo_root / "Makefile"
    if mk.is_file():
        sources.append(mk)
    for src in sources:
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:   # pragma: no cover
            continue
        is_mk = src.name == "Makefile"
        in_run = False
        run_indent = -1
        for line in text.splitlines():
            if is_mk:
                if not line.startswith("\t"):
                    in_run = False
                    continue
                body, active = line.strip(), True
            else:
                stripped = line.strip()
                indent = len(line) - len(line.lstrip())
                if stripped.startswith("- run:"):      # inline step form
                    in_run, run_indent = False, indent
                    body, active = stripped[6:].strip(), True
                elif stripped.startswith("run:"):
                    in_run, run_indent = True, indent
                    body, active = stripped[4:].strip(), True
                elif in_run and stripped and indent > run_indent:
                    body, active = stripped, True
                elif in_run and (not stripped or indent <= run_indent):
                    in_run = False
                    active = False
                else:
                    active = False
                    body = ""
                if active and (body.startswith("- ") or body.startswith("#")):
                    active = False      # YAML list item / comment, not a command
            if not active or not body:
                continue
            if ".py" not in body and "pytest" not in body:
                continue
            lines.append(f"{src.name}: {body}")
            for m in PYTEST_RE.finditer(body):          # suite-level targets
                for tok in m.group(1).split():
                    if tok.startswith(("tests", "backend/tests")):
                        tests.append(tok.rstrip("/"))
            for m in CMD_RE.finditer(body):
                path = m.group(1) or m.group(2)
                if not path:
                    continue
                if path.endswith(".py"):
                    if "test" in path.split("/")[-1] or path.startswith(("tests/", "backend/tests/")):
                        tests.append(path)
                    else:
                        scripts.append(path)
                elif path.startswith(("tests", "backend/tests")):
                    tests.append(path)
    return sorted(set(scripts)), sorted(set(tests)), lines


def doc_invoked_tools(repo_root: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Tools referenced as *commands* in docs (out-of-band, non-CI invocation).

    Returns (tools_invoked_by_docs, test_paths_invoked_by_docs) mapping the
    referenced path to the doc:line sites.  This is weaker evidence than CI (a
    doc can be stale) but it is the only trace for hand-run gates such as
    ``python3 tools/perf_gate.py``.
    """
    tools: dict[str, list[str]] = defaultdict(list)
    tests: dict[str, list[str]] = defaultdict(list)
    pat = re.compile(r"(?:python3?|uv run python)\s+(?:-m\s+)?([\w./-]+\.py)")
    for md in repo_root.rglob("*.md"):
        if any(part in SKIP_DIRS for part in md.relative_to(repo_root).parts):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:   # pragma: no cover
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                path = m.group(1).lstrip("./")
                site = f"{md.relative_to(repo_root).as_posix()}:{i}"
                if path.startswith("tests/") or path.startswith("backend/tests/"):
                    tests.setdefault(path, []).append(site)
                elif path.startswith("tools/") or path.startswith("scripts/"):
                    tools.setdefault(path, []).append(site)
    return dict(tools), dict(tests)


# --------------------------------------------------------------------------- #
# inventory + verdicts
# --------------------------------------------------------------------------- #
def discover_gates(repo: Repo) -> list[str]:
    out = []
    for rel, pf in repo.files.items():
        if pf.is_test:
            continue
        if not rel.split("/")[0] in PROD_ROOTS and not rel.startswith("backend/"):
            continue
        if GATE_NAME_RE.search(Path(rel).stem):
            out.append(rel)
    return sorted(out)


def audit(root: Path) -> dict:
    repo = Repo(root)
    gates = discover_gates(repo)

    runtime_eps = [p for p in ("backend/main.py", "backend/factory_app.py",
                               "factory_app.py", "backend/app.py") if p in repo.files]
    ci_scripts, ci_tests, ci_lines = ci_invoked(root)
    ci_script_rels = [p for p in ci_scripts if p in repo.files]
    tool_rels = sorted(r for r in repo.files if r.startswith("tools/"))

    doc_tools, doc_tests = doc_invoked_tools(root)
    rt = repo.closure(runtime_eps)
    ci = repo.closure(ci_script_rels)
    doc_roots = [p for p in doc_tools if p in repo.files]
    doc = repo.closure(doc_roots)
    tool = repo.closure(tool_rels)
    test_roots = sorted(r for r, f in repo.files.items() if f.is_test)
    tst = repo.closure(test_roots)

    # attribute doc-invocation sites to every module reached through that root
    doc_sites: dict[str, list[str]] = defaultdict(list)
    for _r in doc_roots:
        for _m in repo.closure([_r]):
            doc_sites[_m].extend(doc_tools[_r])

    rows = []
    for g in gates:
        if g in rt:
            verdict = "WIRED_RUNTIME"
        elif g in ci:
            verdict = "WIRED_CI"
        elif g in tool:
            verdict = "TOOL_ONLY"
        elif g in tst:
            verdict = "TEST_ONLY"
        else:
            verdict = "ORPHAN"
        rows.append({
            "module": g,
            "verdict": verdict,
            "importers_non_test": [p for p in repo.direct_importers(g) if not repo.files[p].is_test],
            "importers_test": [p for p in repo.direct_importers(g) if repo.files[p].is_test],
            "in_runtime_closure": g in rt,
            "in_ci_closure": g in ci,
            "in_tools_closure": g in tool,
            "in_doc_invoked_closure": g in doc,
            "doc_invoked_sites": sorted(set(doc_sites.get(g, []))),
            "test_invoked_sites": doc_tests.get(g, []),
        })

    # documentation claims vs verdict
    claims = []
    gate_tokens = {Path(r["module"]).stem: r for r in rows}
    for md in root.rglob("*.md"):
        if any(part in SKIP_DIRS for part in md.relative_to(root).parts):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:   # pragma: no cover
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not CLAIM_RE.search(line):
                continue
            hits = [tok for tok in gate_tokens if tok and tok.lower() in line.lower()]
            if not hits:
                continue
            for tok in hits:
                claims.append({
                    "module": gate_tokens[tok]["module"],
                    "verdict": gate_tokens[tok]["verdict"],
                    "doc": md.relative_to(root).as_posix(),
                    "line": i,
                    "text": line.strip()[:220],
                    "contradiction": gate_tokens[tok]["verdict"] in ("TEST_ONLY", "ORPHAN"),
                })

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_ORDER}
    return {
        "repo": str(root),
        "n_py_files": len(repo.files),
        "n_gate_modules": len(rows),
        "entrypoints": {
            "runtime": runtime_eps,
            "ci_scripts": ci_script_rels,
            "ci_test_paths": ci_tests,
            "doc_invoked_tools": len(doc_tools),
            "n_tools_roots": len(tool_rels),
        },
        "parse_errors": sorted(
            f"{p.rel}: {p.parse_error}" for p in repo.files.values() if p.parse_error
        ),
        "verdict_counts": counts,
        "gates": rows,
        "claims": claims,
        "contradictions": sum(1 for c in claims if c["contradiction"]),
        "ci_command_lines": ci_lines,
    }


def to_markdown(result: dict) -> str:
    L = []
    L.append("# Gate wiring reality audit — machine ledger\n")
    L.append(f"- repo: `{result['repo']}`")
    L.append(f"- python files parsed: **{result['n_py_files']}** "
             f"(parse errors: {len(result['parse_errors'])})")
    L.append(f"- gate modules discovered: **{result['n_gate_modules']}**")
    L.append("- runtime entrypoints: " + ", ".join(f"`{e}`" for e in result["entrypoints"]["runtime"]))
    L.append("- CI-invoked scripts: " + (", ".join(f"`{e}`" for e in result["entrypoints"]["ci_scripts"]) or "_(none)_"))
    L.append("")
    L.append("## Verdict distribution\n")
    L.append("| verdict | count | meaning |")
    L.append("|---|---|---|")
    meaning = {
        "WIRED_RUNTIME": "reachable from live app import closure",
        "WIRED_CI": "executed by CI/Makefile-invoked script",
        "TOOL_ONLY": "reachable only from tools/ scripts (no CI/runtime path)",
        "TEST_ONLY": "imported by tests only — **no production path**",
        "ORPHAN": "no inbound reference at all",
    }
    for v in VERDICT_ORDER:
        L.append(f"| `{v}` | {result['verdict_counts'][v]} | {meaning[v]} |")
    L.append("")
    L.append("## Per-gate verdicts\n")
    L.append("| gate module | verdict | non-test importers | test importers | doc-invoked |")
    L.append("|---|---|---|---|---|")
    for r in sorted(result["gates"], key=lambda r: (VERDICT_ORDER.index(r["verdict"]), r["module"])):
        ni = ", ".join(f"`{p}`" for p in r["importers_non_test"]) or "—"
        ti = ", ".join(f"`{p}`" for p in r["importers_test"]) or "—"
        di = ", ".join(f"`{p}`" for p in r.get("doc_invoked_sites", [])) or "—"
        L.append(f"| `{r['module']}` | `{r['verdict']}` | {ni} | {ti} | {di} |")
    L.append("")
    L.append(f"## Documentation claims vs verdict ({len(result['claims'])} claim lines, "
             f"{result['contradictions']} contradicting)\n")
    if result["claims"]:
        L.append("| gate module | verdict | doc:line | claim text |")
        L.append("|---|---|---|---|")
        for c in result["claims"]:
            flag = "❌" if c["contradiction"] else "·"
            txt = c["text"].replace("|", "\\|")[:160]
            L.append(f"| `{Path(c['module']).stem}` | {flag} `{c['verdict']}` | `{c['doc']}:{c['line']}` | {txt} |")
    else:
        L.append("_no claim lines matched_")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    result = audit(root)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_md:
        Path(args.out_md).write_text(to_markdown(result), encoding="utf-8")
    if not args.quiet:
        print(json.dumps({k: result[k] for k in
                          ("n_py_files", "n_gate_modules", "verdict_counts",
                           "contradictions")}, ensure_ascii=False, indent=2))
        for r in result["gates"]:
            if r["verdict"] in ("TEST_ONLY", "ORPHAN"):
                print(f"  {r['verdict']:12s} {r['module']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())