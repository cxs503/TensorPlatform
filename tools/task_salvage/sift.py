"""Three-gate sift: is a leftover file *inheritable material* or a liability?

Gate A — ``parse``      the file must be structurally readable: ``.py`` through
                        ``ast.parse`` (syntax tree), ``.json``/``.toml`` through
                        their parsers, text through strict UTF-8 decoding,
                        binary containers through a magic-byte check.  A file
                        that cannot be parsed cannot be re-applied by a
                        successor worker, so it is never inheritable.
Gate B — ``smoke``      for executable text (``.py``) the module is imported
                        once in an isolated subprocess (fresh ``cwd``, bounded
                        timeout, ``PYTHONPATH`` pointing at the rescued root).
                        Import-time failure is the cheapest available signal
                        that the partial file is not usable as-is.  Non-Python
                        sources are ``not_applicable`` (documented, not silently
                        passed) and the gate falls back to the parse verdict.
Gate C — ``structural`` the path must map into the target repository layout:
                        its first path segment has to be a real top-level entry
                        of the destination checkout (or an allowed artefact
                        root).  Files that address the repository from outside
                        (absolute paths, ``..`` escapes) are unmappable.

Every gate returns ``{"gate", "status", "detail"}`` with status in
``pass`` / ``fail`` / ``not_applicable``.  There is no timing, no address, no
clock in the output: the sift is a pure function of (path, bytes, layout), which
is what makes ``salvage.json`` byte-reproducible.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

GATES = ("parse", "smoke", "structural")

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NA = "not_applicable"

INHERITABLE = "INHERITABLE"
BLOCKED = "BLOCKED"

#: file kinds whose smoke gate is not applicable (no interpreter contract).
_PY_EXT = "py"
_TEXT_EXT = frozenset(
    """md markdown rst tex bib org adoc txt py pyi sh bash zsh sql toml yaml yml
    ini cfg conf json jsonl ndjson csv tsv xml html css js mjs ts jsx tsx vue
    c h cc cpp cxx hpp go rs jl f90 f95 for lean r m lua pl rb java kt scala
    swift dart cs proto graphql patch diff""".split()
)
_JSON_EXT = frozenset("json".split())
_TOML_EXT = frozenset("toml".split())
_BINARY_MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n",
    "pdf": b"%PDF-",
    "zip": b"PK\x03\x04",
    "gz": b"\x1f\x8b",
}


def ext_of(path: str) -> str:
    name = str(path).rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def normalize_rel(raw: str, worktree: Path) -> str | None:
    """Repository-relative path for a recorded path, or ``None`` if it escapes.

    Accepts both ``a/b.py`` and the absolute form runners sometimes record
    (``<worktree>/a/b.py``).  Anything outside the worktree is dropped: a single
    file must not be able to point the salvage package at another checkout.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    wt = Path(worktree).resolve()
    if text.startswith("/"):
        try:
            return Path(text).resolve().relative_to(wt).as_posix()
        except (ValueError, OSError):
            return None
    parts = [seg for seg in text.split("/") if seg not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


# ─────────────────────────────── gate A: parse ───────────────────────────────

def gate_parse(path: Path) -> dict:
    ext = ext_of(path.name)
    if not path.is_file():
        return {"gate": "parse", "status": STATUS_FAIL, "detail": "file_missing"}
    data = path.read_bytes()
    if ext == _PY_EXT:
        try:
            ast.parse(data.decode("utf-8", errors="replace"))
        except SyntaxError as exc:
            return {"gate": "parse", "status": STATUS_FAIL, "detail": f"syntax:{exc.lineno}:{exc.msg}"[:200]}
        return {"gate": "parse", "status": STATUS_PASS, "detail": "ast_ok"}
    if ext in _JSON_EXT:
        try:
            json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return {"gate": "parse", "status": STATUS_FAIL, "detail": f"json:{type(exc).__name__}"}
        return {"gate": "parse", "status": STATUS_PASS, "detail": "json_ok"}
    if ext in _TOML_EXT:
        try:
            import tomllib

            tomllib.loads(data.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is a fail
            return {"gate": "parse", "status": STATUS_FAIL, "detail": f"toml:{type(exc).__name__}"}
        return {"gate": "parse", "status": STATUS_PASS, "detail": "toml_ok"}
    if ext in _BINARY_MAGIC:
        ok = data.startswith(_BINARY_MAGIC[ext])
        return {
            "gate": "parse",
            "status": STATUS_PASS if ok else STATUS_FAIL,
            "detail": "magic_ok" if ok else "magic_mismatch",
        }
    if ext in _TEXT_EXT:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return {"gate": "parse", "status": STATUS_FAIL, "detail": "utf8_decode_failed"}
        return {"gate": "parse", "status": STATUS_PASS, "detail": "utf8_ok"}
    # unknown container: a NUL byte is the cheap proof that it is not text.
    if b"\x00" in data[:4096]:
        return {"gate": "parse", "status": STATUS_FAIL, "detail": "opaque_binary"}
    return {"gate": "parse", "status": STATUS_PASS, "detail": "no_contract"}


# ─────────────────────────────── gate B: smoke ───────────────────────────────

_SMOKE_SNIPPET = (
    "import importlib, sys\n"
    "worktree, path, name = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "sys.path.insert(0, worktree)\n"
    "try:\n"
    "    importlib.import_module(name)\n"
    "except ModuleNotFoundError as exc:\n"
    "    if exc.name != name:\n"
    "        raise\n"
    "    spec = importlib.util.spec_from_file_location(name, path)\n"
    "    module = importlib.util.module_from_spec(spec)\n"
    "    spec.loader.exec_module(module)\n"
)


def dotted_name(rel: str) -> str | None:
    """Dotted import name for a repo-relative ``.py`` path, or ``None``.

    Package-aware naming is what makes the smoke gate fair: a module that uses
    relative imports only imports successfully when it is imported *as a member
    of its package*, not as a bare file.
    """
    if not rel.endswith(".py"):
        return None
    parts = [seg for seg in rel.split("/") if seg]
    if not parts:
        return None
    stem = parts[-1][:-3]
    if stem == "__init__":
        parts = parts[:-1]
        if not parts:
            return None
    else:
        parts = parts[:-1] + [stem]
    if not all(seg.isidentifier() for seg in parts):
        return None
    return ".".join(parts)


def smoke_env(worktree: Path) -> dict:
    """Environment for the smoke subprocess: inherited, with a pinned import path."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    env["PYTHONPATH"] = str(Path(worktree).resolve())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    return env


def gate_smoke(path: Path, worktree: Path, rel: str | None = None, timeout: int = 20, retry: bool = True) -> dict:
    """Import the module once in an isolated subprocess (cwd = temp dir).

    A timeout is retried once: the first attempt may run under CPU contention
    from concurrent smoke checks, and a contention-induced timeout would make the
    verdict irreproducible (which would break the idempotency gate).
    """
    if ext_of(path.name) != _PY_EXT:
        return {"gate": "smoke", "status": STATUS_NA, "detail": f"ext:{ext_of(path.name) or 'none'}"}
    if not path.is_file():
        return {"gate": "smoke", "status": STATUS_FAIL, "detail": "file_missing"}
    rel = rel or path.name
    name = dotted_name(rel) or ("salvage_smoke_" + hashlib.sha256(rel.encode()).hexdigest()[:12])
    with tempfile.TemporaryDirectory(prefix="task-salvage-smoke-") as cwd:
        try:
            proc = subprocess.run(
                ["python3", "-B", "-c", _SMOKE_SNIPPET, str(Path(worktree).resolve()), str(path.resolve()), name],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=smoke_env(worktree),
            )
        except subprocess.TimeoutExpired:
            if retry:
                return gate_smoke(path, worktree, rel, timeout=timeout, retry=False)
            return {"gate": "smoke", "status": STATUS_FAIL, "detail": "timeout"}
        except OSError as exc:
            return {"gate": "smoke", "status": STATUS_FAIL, "detail": f"{type(exc).__name__}"}
    if proc.returncode == 0:
        return {"gate": "smoke", "status": STATUS_PASS, "detail": "import_ok"}
    tail = (proc.stderr or "").strip().splitlines()
    last = tail[-1] if tail else f"exit{proc.returncode}"
    return {"gate": "smoke", "status": STATUS_FAIL, "detail": last[:200]}


# ──────────────────────────── gate C: structural ────────────────────────────

def gate_structural(rel: str | None, allowed_roots) -> dict:
    """Does the path land inside the destination checkout layout?"""
    if rel is None:
        return {"gate": "structural", "status": STATUS_FAIL, "detail": "outside_worktree"}
    roots = set(allowed_roots or ())
    segments = [seg for seg in rel.split("/") if seg]
    if not segments:
        return {"gate": "structural", "status": STATUS_FAIL, "detail": "empty_path"}
    head = segments[0]
    if head in roots:
        return {"gate": "structural", "status": STATUS_PASS, "detail": f"root:{head}"}
    if len(segments) == 1:
        # a root-level file is delivered to the checkout root: always mappable
        return {"gate": "structural", "status": STATUS_PASS, "detail": "root:file"}
    return {"gate": "structural", "status": STATUS_FAIL, "detail": f"unmapped_root:{head}"[:200]}


# ─────────────────────────────── orchestration ──────────────────────────────

def smoke_gate(path: Path, rel: str | None, worktree: Path, smoke: bool) -> dict:
    """Smoke gate with the "never pass silently" rule folded in."""
    if smoke:
        return gate_smoke(path, worktree, rel)
    if ext_of(path.name) == _PY_EXT:
        return {"gate": "smoke", "status": STATUS_FAIL, "detail": "smoke_not_run"}
    return {"gate": "smoke", "status": STATUS_NA, "detail": "disabled"}


def assemble(gates: dict) -> dict:
    """Verdict + failed-gate list from a complete gate triple."""
    failed = [name for name in GATES if gates[name]["status"] == STATUS_FAIL]
    return {"gates": gates, "verdict": BLOCKED if failed else INHERITABLE, "failed_gates": failed}


def sift_file(path: Path, rel: str | None, worktree: Path, allowed_roots, smoke: bool = True) -> dict:
    """Run all three gates for one candidate file; return the gate record."""
    return assemble(
        {
            "parse": gate_parse(path),
            "smoke": smoke_gate(path, rel, worktree, smoke),
            "structural": gate_structural(rel, allowed_roots),
        }
    )


def dominant_reason(gate_records: list[dict]) -> str:
    """Blocking code for a candidate set: the gate that fails most often."""
    tally = {gate: 0 for gate in GATES}
    for record in gate_records:
        for gate in record.get("failed_gates", ()):
            tally[gate] = tally.get(gate, 0) + 1
    worst, count = max(tally.items(), key=lambda kv: (kv[1], -GATES.index(kv[0])))
    if count == 0:
        return "no_blocking_gate"
    return f"{worst}_failed"