"""Static usability probes for salvaged code files.

Requirement A4 forbids "assume it works": every probed file gets an explicit
verdict.  Three probe levels are recorded per file:

``py_compile``   syntax check via :mod:`py_compile` with the ``.pyc`` written to a
                 temporary directory — never next to the source.  (Writing byte
                 code into a dead worktree would *create* untracked files and
                 destroy scan idempotency; that trap is covered by a test.)
``imports``      AST-level top-level import collection, then a resolution probe
                 (``importlib.util.find_spec``) executed in an isolated
                 subprocess with ``PYTHONDONTWRITEBYTECODE=1``.
``dynamic_import``
                 Only attempted when every top-level import resolves.  The module
                 is loaded by path in a subprocess with a wall-clock timeout, so
                 a test module with heavy import side effects cannot hang the
                 scan.  Non-Python files are reported as ``not_applicable``.

Verdicts: ``compiled``/``compile_error``, ``imports_resolved``/
``imports_missing``/``imports_partial``, ``import_ok``/``import_error``/
``skipped_missing_deps``/``not_applicable``.
"""
from __future__ import annotations

import ast
import json
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_TIMEOUT = 30

_IMPORT_PROBE = r"""
import importlib.util, json, sys
names = json.loads(sys.argv[1])
missing = []
for name in names:
    try:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    except Exception:
        missing.append(name)
print(json.dumps({"missing": missing}))
"""

_IMPORT_EXEC = r"""
import importlib.util, json, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("salvage_probe_target", path)
if spec is None or spec.loader is None:
    print(json.dumps({"ok": False, "error": "no_loader"}))
    raise SystemExit(0)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({"ok": True, "error": None}))
"""


def collect_top_level_imports(source: str) -> list[str]:
    """Top-level module names imported by the file (stdlib AST only)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: no absolute module name to probe
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return sorted(names)


def _py_compile_ok(path: Path, tmpdir: str) -> tuple[bool, str]:
    target = Path(tmpdir) / (path.name + "c")
    try:
        py_compile.compile(str(path), cfile=str(target), doraise=True)
    except py_compile.PyCompileError as exc:
        return False, f"{exc.msg.strip()[:300]}"
    except OSError as exc:  # pragma: no cover - defensive
        return False, f"os_error:{exc}"
    return True, ""


def _resolve_imports(names: list[str], cwd: Path) -> list[str]:
    if not names:
        return []
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, json.dumps(names)],
        cwd=str(cwd),
        capture_output=True,
        timeout=PROBE_TIMEOUT,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
    )
    if proc.returncode != 0:
        return list(names)
    try:
        return list(json.loads(proc.stdout.strip() or "{}").get("missing", []))
    except json.JSONDecodeError:  # pragma: no cover - defensive
        return list(names)


def _dynamic_import(path: Path, cwd: Path) -> dict:
    import os

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _IMPORT_EXEC, str(path)],
            cwd=str(cwd),
            capture_output=True,
            timeout=PROBE_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
        )
    except subprocess.TimeoutExpired:
        return {"status": "import_error", "error": f"timeout>{PROBE_TIMEOUT}s"}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"status": "import_error", "error": (proc.stderr or "no output").strip()[-300:]}
    if payload.get("ok"):
        return {"status": "import_ok", "error": None}
    return {"status": "import_error", "error": str(payload.get("error"))[:300]}


def probe_file(worktree: str | Path, rel_path: str) -> dict:
    """Probe one salvaged file; always returns an explicit verdict dict."""
    worktree = Path(worktree)
    path = worktree / rel_path
    record: dict = {
        "path": rel_path,
        "probe": "py_compile+import",
        "py_compile": "skipped",
        "compile_error": None,
        "imports": [],
        "import_missing": [],
        "imports_verdict": "not_applicable",
        "dynamic_import": {"status": "not_applicable", "error": None},
        "verdict": "unusable",
    }
    if not path.is_file():
        record["compile_error"] = "file_missing"
        return record
    if path.suffix != ".py":
        record["py_compile"] = "not_applicable"
        record["imports_verdict"] = "not_applicable"
        record["verdict"] = "not_python"
        return record

    with tempfile.TemporaryDirectory(prefix="salvage-probe-") as tmpdir:
        ok, err = _py_compile_ok(path, tmpdir)
    record["py_compile"] = "compiled" if ok else "compile_error"
    record["compile_error"] = err or None
    if not ok:
        record["verdict"] = "compile_error"
        return record

    source = path.read_text(encoding="utf-8", errors="replace")
    names = collect_top_level_imports(source)
    missing = _resolve_imports(names, worktree)
    record["imports"] = names
    record["import_missing"] = missing
    if not names:
        record["imports_verdict"] = "imports_resolved"
    elif not missing:
        record["imports_verdict"] = "imports_resolved"
    elif len(missing) == len(names):
        record["imports_verdict"] = "imports_missing"
    else:
        record["imports_verdict"] = "imports_partial"

    if record["imports_verdict"] == "imports_resolved":
        record["dynamic_import"] = _dynamic_import(path, worktree)
        record["verdict"] = "usable" if record["dynamic_import"]["status"] == "import_ok" else "import_error"
    else:
        record["dynamic_import"] = {
            "status": "skipped_missing_deps",
            "error": "missing:" + ",".join(missing[:6]),
        }
        record["verdict"] = "compiled_deps_unresolved"
    return record