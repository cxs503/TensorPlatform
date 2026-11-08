#!/usr/bin/env python3
"""Domain boundary gates for the TensorPlatform core surface (G1/G2/G3).

TensorPlatform (TP) is a *domain-neutral* agent-runtime platform.  Ship-domain
payloads (resistance / propulsion / seakeeping / manoeuvring / ice loads ...)
are being migrated out of TP into the ``ship`` body.  "Migration done" is only
credible when it is *machine-checkable*, so this script turns the migration
contract into three gates:

G1  import boundary gate
    AST-parse the core surface and flag any import of a ship-domain module
    (``ship*``, ``polar``, ``holtrop``, ``resistance``, ``propeller``,
    ``seakeep*``, ``manoeuvr*``, ``maneuver``, ``ice_uq``).

G2  endpoint vocabulary gate
    Pull the live OpenAPI document from the local API (default :8010) and count
    endpoint paths whose URL carries ship-domain vocabulary
    (ship|holtrop|wigley|kvlcc|suboff|seakeep|manoeuv|maneuver|propeller|
    hydrostatic|ice-uq|ice-load|polar).  Frozen baseline is recorded; the gate
    is expected to ratchet down to 0 after migration.

G3  constant hoisting gate
    Text-scan the core surface for hard-coded hydrodynamics constants
    (WATER_DENSITY, KINEMATIC_VISCOSITY, KVLCC, SUBOFF, DTMB).  Physics data
    belongs to the domain body, not the platform.

Mode
----
Non-blocking baseline mode is the default: the script always exits 0, writes a
JSON baseline (violation list + counts per gate) and a markdown snapshot
report.  ``--enforce`` flips it into a blocking gate (exit 2 when any gate has
violations or cannot be evaluated), to be switched on once migration finished.

Usage
-----
    python tools/audit/boundary_gates.py            # baseline report, exit 0
    python tools/audit/boundary_gates.py --enforce  # blocking gate, exit 2
    python tools/audit/boundary_gates.py --openapi-file /tmp/oa.json  # offline

Exit codes: 0 = ok (baseline mode always), 2 = gate red in ``--enforce`` mode,
3 = internal error.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import sys
import tokenize
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA = "boundary_gates/v1"

# ---------------------------------------------------------------------------
# Gate surface definitions
# ---------------------------------------------------------------------------

#: Individual core-surface files (paths relative to the repository root).
CORE_SURFACE_FILES: tuple[str, ...] = (
    "backend/main.py",
    "backend/api/domain/tensor.py",
    "backend/api/domain/iam.py",
    "backend/api/domain/platform.py",
    "backend/api/domain/observability.py",
    "backend/api/domain/data.py",
    "backend/api/domain/model.py",
    "backend/api/domain/deployment.py",
    "backend/api/domain/ops.py",
    "backend/api/domain/tools.py",
    "backend/api/domain/misc.py",
    "backend/services/benchmark_canon.py",
)

#: Directories whose ``*.py`` files are part of the core surface.
CORE_SURFACE_DIRS: tuple[str, ...] = ("tools/audit",)

#: Ship-domain vocabulary used by the G1 import gate.
SHIP_IMPORT_TOKENS: tuple[str, ...] = (
    "ship",
    "polar",
    "holtrop",
    "resistance",
    "propeller",
    "seakeep",
    "manoeuvr",
    "maneuver",
    "ice_uq",
)

#: Endpoint URL vocabulary used by the G2 vocabulary gate.
SHIP_ENDPOINT_PATTERN = (
    r"ship|holtrop|wigley|kvlcc|suboff|seakeep|manoeuv|maneuver|propeller"
    r"|hydrostatic|ice-uq|ice-load|polar"
)

#: Hydrodynamics constants that must not live in the platform core (G3).
HOIST_CONSTANTS: tuple[str, ...] = (
    "WATER_DENSITY",
    "KINEMATIC_VISCOSITY",
    "KVLCC",
    "SUBOFF",
    "DTMB",
)

#: Baseline recorded in the G1-G3 gate spec (audit report §7, 2026-11-01).
SPEC_STATED_G2_BASELINE = 7

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_matches(segment: str, token: str) -> bool:
    """Boundary-aware match of a ship-domain ``token`` inside a module name.

    Segments are split on non-alphanumerics *and* camelCase is not split (module
    names are snake_case in this repo).  A token matches when a word equals the
    token or starts with it, so ``manoeuvr`` catches ``manoeuvring`` while
    ``ship`` does **not** fire on ``sqlalchemy.orm.relationship``.
    """
    seg = segment.lower()
    tok = token.lower()
    words = [w for w in re.split(r"[^a-z0-9]+", seg) if w]
    words.append(seg)
    for word in words:
        if word == tok or word.startswith(tok):
            return True
    return False


def _ship_import_hit(
    module: str,
    names: Sequence[str],
    tokens: Optional[Sequence[str]] = None,
) -> Optional[tuple[str, str]]:
    """Return ``(matched_segment, matched_token)`` for a module+names spec."""
    candidates: List[str] = []
    if module:
        candidates.append(module)
        # ``from pkg import sub`` may resolve to ``pkg.sub``.
        for name in names:
            candidates.append(f"{module}.{name}")
    else:
        candidates.extend(names)
    for candidate in candidates:
        for segment in str(candidate).split("."):
            for token in (tokens or SHIP_IMPORT_TOKENS):
                if _token_matches(segment, token):
                    return segment, token
    return None


def collect_surface(
    repo_root: Path,
    extra_files: Iterable[str] = (),
    surface_globs: Optional[Sequence[str]] = None,
) -> tuple[List[Path], List[str], List[str]]:
    """Resolve the core surface to concrete ``*.py`` files under ``repo_root``.

    ``surface_globs`` extends the closed whitelist (``CORE_SURFACE_FILES`` +
    ``CORE_SURFACE_DIRS``) with glob-discovered files such as
    ``backend/services/*.py``.  Without it a violation planted in a file nobody
    enumerated is simply invisible, which is a *coverage* blind spot rather than
    a detection blind spot; the glob is operator data, not detection logic.
    """
    files: List[Path] = []
    missing: List[str] = []
    seen: set[str] = set()

    candidates = list(CORE_SURFACE_FILES) + list(extra_files)
    for rel in candidates:
        path = repo_root / rel
        if path.is_file():
            if rel not in seen:
                seen.add(rel)
                files.append(path)
        else:
            missing.append(rel)

    for rel_dir in CORE_SURFACE_DIRS:
        directory = repo_root / rel_dir
        if not directory.is_dir():
            missing.append(rel_dir + "/")
            continue
        for path in sorted(directory.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if rel not in seen:
                seen.add(rel)
                files.append(path)

    for pattern in surface_globs or ():
        for path in sorted(repo_root.glob(pattern)):
            if not path.is_file() or path.suffix != ".py":
                continue
            rel = path.relative_to(repo_root).as_posix()
            if rel not in seen:
                seen.add(rel)
                files.append(path)
    return files, missing, sorted(seen)


def _self_path() -> Path:
    return Path(__file__).resolve()


# ---------------------------------------------------------------------------
# G1 — import boundary gate
# ---------------------------------------------------------------------------

#: Functions whose first argument is a module path (runtime import forms).
DYNAMIC_IMPORT_FUNCS: tuple[str, ...] = ("import_module", "__import__")


def _call_name(func: ast.AST) -> str:
    """``importlib.import_module`` -> ``import_module``; ``__import__`` -> itself."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _fold_str(node: Optional[ast.AST], table: Dict[str, str]) -> tuple[str, bool]:
    """Fold a constant string expression to ``(text, fully_resolved)``.

    Handles string literals, module-level ``NAME = "..."`` assignments, ``+``
    concatenation and f-strings whose placeholders are literals.  A partially
    known f-string returns its literal parts with ``resolved`` False so the
    token matcher still sees the visible half of the path.
    """
    if node is None:
        return "", False
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value, True
        return "", False
    if isinstance(node, ast.Name):
        if node.id in table:
            return table[node.id], True
        return "", False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, left_ok = _fold_str(node.left, table)
        right, right_ok = _fold_str(node.right, table)
        return left + right, left_ok and right_ok
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        resolved = True
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            literal, ok = _fold_str(getattr(value, "value", None), table)
            parts.append(literal)
            resolved = resolved and ok
        return "".join(parts), resolved
    return "", False


def _string_assignment_table(tree: ast.AST) -> Dict[str, str]:
    """Module-level ``NAME = <foldable string>`` lookup for indirect imports."""
    table: Dict[str, str] = {}
    body = getattr(tree, "body", [])
    for node in body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        folded, ok = _fold_str(node.value, table)
        if ok and folded:
            table[target.id] = folded
    return table


def _import_machinery_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to the import machinery (``from importlib import ... as im``)."""
    aliases: set[str] = set(DYNAMIC_IMPORT_FUNCS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not (module == "importlib" or module.endswith(".importlib")):
                continue
            for alias in node.names:
                if alias.name in DYNAMIC_IMPORT_FUNCS:
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
            if node.value.attr not in DYNAMIC_IMPORT_FUNCS:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    return aliases


def _call_argument(node: ast.Call) -> Optional[ast.AST]:
    """First positional argument, else a known keyword (``name=``/``module=``/``path=``)."""
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg in ("name", "module", "path"):
            return keyword.value
    return None


def _dynamic_import_calls(tree: ast.AST) -> List[Dict[str, Any]]:
    """Every runtime import call site with its (partially) folded target."""
    table = _string_assignment_table(tree)
    aliases = _import_machinery_aliases(tree)
    calls: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if isinstance(node.func, ast.Call) and _call_name(node.func.func) == "getattr":
            # ``getattr(importlib, 'import_module')(...)`` -- machinery fetched by name
            if len(node.func.args) > 1:
                attr_name, _ok = _fold_str(node.func.args[1], table)
                if attr_name in aliases:
                    name = attr_name
        if name not in aliases:
            continue
        target, resolved = _fold_str(_call_argument(node), table)
        calls.append(
            {
                "line": node.lineno,
                "callee": name,
                "target": target or None,
                "resolved": bool(resolved and target),
                "source": ast.unparse(node)[:200],
            }
        )
    return calls


#: Functions whose first argument is *code* rather than a module path.
DYNAMIC_EXEC_FUNCS: tuple[str, ...] = ("exec", "eval", "compile")


def _dynamic_exec_calls(tree: ast.AST) -> List[Dict[str, Any]]:
    """``exec``/``eval``/``compile`` calls whose source text (partially) folds."""
    table = _string_assignment_table(tree)
    calls: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) not in DYNAMIC_EXEC_FUNCS:
            continue
        text, resolved = _fold_str(_call_argument(node), table)
        if not text:
            continue
        calls.append({"line": node.lineno, "callee": _call_name(node.func),
                      "text": text, "resolved": resolved})
    return calls


def gate_g1_imports(
    repo_root: Path,
    extra_files: Iterable[str] = (),
    *,
    tokens: Optional[Sequence[str]] = None,
    dynamic_imports: bool = True,
    surface_globs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    tokens = tuple(tokens or SHIP_IMPORT_TOKENS)
    files, missing, surface_rel = collect_surface(repo_root, extra_files, surface_globs)
    violations: List[Dict[str, Any]] = []
    parse_errors: List[Dict[str, Any]] = []
    advisories: List[Dict[str, Any]] = []
    self_file = _self_path()
    scanned = 0

    for path in files:
        if path.resolve() == self_file:
            # A linter does not lint its own vocabulary table.
            continue
        scanned += 1
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:  # pragma: no cover - defensive
            parse_errors.append({"file": rel, "error": str(exc)})
            continue

        if dynamic_imports:
            for call in _dynamic_import_calls(tree):
                hit = _ship_import_hit(call["target"] or "", (), tokens)
                if hit:
                    violations.append(
                        {
                            "file": rel,
                            "line": call["line"],
                            "kind": "dynamic-import",
                            "module": call["target"],
                            "callee": call["callee"],
                            "resolved": call["resolved"],
                            "matched_segment": hit[0],
                            "matched_token": hit[1],
                            "source": call["source"],
                        }
                    )
                elif not call["resolved"]:
                    advisories.append(
                        {
                            "file": rel,
                            "line": call["line"],
                            "kind": "dynamic-import-opaque",
                            "callee": call["callee"],
                            "source": call["source"],
                            "note": "module path cannot be folded; leak not machine-checkable",
                        }
                    )
            for call in _dynamic_exec_calls(tree):
                matched = [tok for tok in tokens if _token_matches(call["text"], tok)]
                if not matched:
                    continue
                violations.append(
                    {
                        "file": rel,
                        "line": call["line"],
                        "kind": "dynamic-exec",
                        "callee": call["callee"],
                        "matched_token": matched[0],
                        "source": call["text"][:200],
                    }
                )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    hit = _ship_import_hit(alias.name, (), tokens)
                    if hit:
                        violations.append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "kind": "import",
                                "module": alias.name,
                                "matched_segment": hit[0],
                                "matched_token": hit[1],
                                "source": ast.unparse(node),
                            }
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                # ``from . import ship_x`` inside a package resolves via module='';
                # the alias names themselves are then the candidates.
                hit = _ship_import_hit(module, names, tokens)
                if hit:
                    violations.append(
                        {
                            "file": rel,
                            "line": node.lineno,
                            "kind": "from-import",
                            "module": module,
                            "names": names,
                            "matched_segment": hit[0],
                            "matched_token": hit[1],
                            "source": ast.unparse(node),
                        }
                    )

    violations.sort(key=lambda v: (v["file"], v["line"]))
    return {
        "id": "G1_import_boundary",
        "title": "core surface must not import ship-domain modules",
        "files_scanned": scanned,
        "surface": surface_rel,
        "missing_surface": missing,
        "tokens": list(tokens),
        "surface_globs": list(surface_globs or ()),
        "dynamic_imports_enabled": bool(dynamic_imports),
        "count": len(violations),
        "status": "red" if violations else "green",
        "violations": violations,
        "advisories": advisories,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# G2 — endpoint vocabulary gate
# ---------------------------------------------------------------------------

def load_openapi(
    api_base: str, openapi_file: Optional[str] = None, timeout: float = 8.0
) -> Dict[str, Any]:
    """Load an OpenAPI document from disk or over HTTP (never raises)."""
    if openapi_file:
        path = Path(openapi_file)
        try:
            return {
                "status": "ok",
                "source": str(path),
                "doc": json.loads(path.read_text(encoding="utf-8")),
                "error": None,
            }
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "withheld",
                "source": str(path),
                "doc": None,
                "error": f"openapi file unreadable: {exc}",
            }

    url = api_base.rstrip("/") + "/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        return {"status": "ok", "source": url, "doc": payload, "error": None}
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "withheld",
            "source": url,
            "doc": None,
            "error": f"openapi unavailable: {exc}",
        }


#: Operation-document fields that are published in the live OpenAPI document.
VOCAB_METADATA_FIELDS: tuple[str, ...] = (
    "tags",
    "summary",
    "description",
    "operationId",
    "parameters",
)

#: Document-level fields (outside ``paths``) that are published as well.
VOCAB_DOCUMENT_FIELDS: tuple[str, ...] = ("title", "description", "summary")


def normalise_vocab(text: Any) -> str:
    """Case-fold and strip separators so ``sea-keeping``/``sea_keeping``/``seakeep`` agree."""
    return re.sub(r"[\s_\-/.]+", "", str(text)).lower()


class VocabMatcher:
    """Match a vocabulary alternation against text, with optional normalisation."""

    def __init__(self, pattern: str, normalize_separators: bool = True) -> None:
        self.pattern = pattern
        self.normalize_separators = normalize_separators
        self.tokens = [token for token in pattern.split("|") if token]
        self.regex = re.compile(pattern, re.IGNORECASE)

    def search(self, text: Any) -> Optional[str]:
        if not self.normalize_separators:
            match = self.regex.search(str(text))
            return match.group(0).lower() if match else None
        haystack = normalise_vocab(text)
        for token in self.tokens:
            needle = normalise_vocab(token)
            if needle and needle in haystack:
                return token.lower()
        return None


def _metadata_items(value: Any) -> List[Any]:
    """Flatten a metadata field into the strings that actually get published."""
    items = value if isinstance(value, list) else [value]
    flat: List[Any] = []
    for item in items:
        if isinstance(item, dict):
            # OpenAPI nests publishable names one level down (parameter.name,
            # tag.name, tag.description ...).
            for key in ("name", "description", "summary"):
                if key in item:
                    flat.append(item[key])
        elif item is not None:
            flat.append(item)
    return flat


def _gate_g2_metadata_hits(path: str, operations: Dict[str, Any], matcher: VocabMatcher):
    hits: List[Dict[str, Any]] = []
    for method, operation in sorted(operations.items()):
        if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
            continue
        for field in VOCAB_METADATA_FIELDS:
            if field not in operation:
                continue
            for item in _metadata_items(operation[field]):
                token = matcher.search(item)
                if token:
                    hits.append(
                        {
                            "path": path,
                            "method": method.upper(),
                            "field": field,
                            "value": str(item)[:120],
                            "matched_token": token,
                        }
                    )
    return hits


def _gate_g2_document_hits(doc: Dict[str, Any], matcher: VocabMatcher):
    """Document-level metadata (``info.title`` etc.) is published vocabulary too."""
    hits: List[Dict[str, Any]] = []
    info = doc.get("info") or {}
    for field in VOCAB_DOCUMENT_FIELDS:
        if field not in info:
            continue
        token = matcher.search(info[field])
        if token:
            hits.append(
                {
                    "path": "<document>",
                    "method": "-",
                    "field": f"info.{field}",
                    "value": str(info[field])[:120],
                    "matched_token": token,
                }
            )
    for tag in doc.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        for item in _metadata_items(tag):
            token = matcher.search(item)
            if token:
                hits.append(
                    {
                        "path": "<document>",
                        "method": "-",
                        "field": "tags",
                        "value": str(item)[:120],
                        "matched_token": token,
                    }
                )
    return hits


def gate_g2_endpoints(
    api_base: str,
    openapi_file: Optional[str] = None,
    timeout: float = 8.0,
    *,
    vocab_pattern: Optional[str] = None,
    metadata_scan: bool = True,
    normalize_separators: bool = True,
) -> Dict[str, Any]:
    loaded = load_openapi(api_base, openapi_file, timeout)
    matcher = VocabMatcher(vocab_pattern or SHIP_ENDPOINT_PATTERN, normalize_separators)
    endpoints: List[Dict[str, Any]] = []
    metadata_hits: List[Dict[str, Any]] = []
    total_paths = 0

    if loaded["status"] == "ok":
        paths = loaded["doc"].get("paths") or {}
        total_paths = len(paths)
        if metadata_scan:
            metadata_hits.extend(_gate_g2_document_hits(loaded["doc"], matcher))
        for path in sorted(paths):
            operations = paths[path] or {}
            if metadata_scan:
                metadata_hits.extend(_gate_g2_metadata_hits(path, operations, matcher))
            token = matcher.search(path)
            if not token:
                continue
            methods = [m.upper() for m in operations if m.lower() in HTTP_METHODS]
            endpoints.append(
                {
                    "path": path,
                    "methods": sorted(methods),
                    "matched_token": token,
                }
            )

    count = sum(max(1, len(e["methods"])) for e in endpoints)
    if loaded["status"] != "ok":
        return {
            "id": "G2_endpoint_vocabulary",
            "title": "live API must not expose ship-domain endpoint vocabulary",
            "source": loaded["source"],
            "status": "withheld",
            "count": None,
            "path_count": len(endpoints),
            "total_openapi_paths": total_paths,
            "spec_stated_baseline": SPEC_STATED_G2_BASELINE,
            "pattern": matcher.pattern,
            "normalize_separators": bool(normalize_separators),
            "metadata_scan_enabled": bool(metadata_scan),
            "endpoints": endpoints,
            "metadata_hits": metadata_hits,
            "violations": [],
            "error": loaded["error"],
        }

    return {
        "id": "G2_endpoint_vocabulary",
        "title": "live API must not expose ship-domain endpoint vocabulary",
        "source": loaded["source"],
        "status": "red" if endpoints else "green",
        "count": count,
        "path_count": len(endpoints),
        "total_openapi_paths": total_paths,
        "spec_stated_baseline": SPEC_STATED_G2_BASELINE,
        "pattern": matcher.pattern,
        "normalize_separators": bool(normalize_separators),
        "metadata_scan_enabled": bool(metadata_scan),
        "endpoints": endpoints,
        "metadata_hits": metadata_hits,
        "violations": [
            {"file": e["path"], "line": 0, "matched_token": e["matched_token"]}
            for e in endpoints
        ]
        + [
            {
                "file": h["path"],
                "line": 0,
                "matched_token": h["matched_token"],
                "field": h["field"],
                "kind": "metadata",
            }
            for h in metadata_hits
        ],
        "error": None,
    }


def _literal_spans(path: Path) -> tuple[List[tuple[int, int, int]], List[tuple[int, int, int]]]:
    """Return ``(comment_spans, string_spans)`` as ``(line, col_start, col_end)``.

    Used to triage G3 hits: a benchmark id inside a comment or docstring is a
    documentation duty, a bare constant inside executable code is the real
    domain leak.  Tokenisation failures degrade to "no spans" (everything is
    classified as code) instead of dropping the hit.
    """
    comments: List[tuple[int, int, int]] = []
    strings: List[tuple[int, int, int]] = []
    try:
        with path.open("rb") as handle:
            for tok in tokenize.tokenize(handle.readline):
                if tok.type == tokenize.COMMENT:
                    comments.append((tok.start[0], tok.start[1], tok.end[1]))
                elif tok.type == tokenize.STRING:
                    strings.append((tok.start[0], tok.start[1], tok.end[1]))
    except (tokenize.TokenError, SyntaxError, OSError):  # pragma: no cover
        pass
    return comments, strings


def _hit_context(line: int, col: int, comments, strings) -> str:
    for start_line, start_col, _end_col in comments:
        if start_line == line and col >= start_col:
            return "comment"
    for start_line, start_col, end_col in strings:
        if start_line == line and start_col <= col < max(end_col, start_col + 1):
            return "string"
    return "code"


# ---------------------------------------------------------------------------
# G3 — constant hoisting gate
# ---------------------------------------------------------------------------

def constant_hits(
    line: str,
    constants: Sequence[str],
    *,
    variants: bool = True,
    embedded: bool = True,
) -> List[tuple[int, str, str]]:
    """Locate hoisted-name occurrences in ``line`` as ``(col, constant, form)``.

    ``form``
        ``exact``        declared case, word-delimited both sides
        ``embedded``     declared case, glued to other word characters
                         (``SEA_WATER_DENSITY`` on the left, ``KVLCC2`` on the right)
        ``case_variant`` different case (``gadget_rho``)

    ``variants=False`` keeps only declared-case hits; ``embedded=False`` keeps
    only left-delimited hits.  Both off reproduces the historical ``\b(NAME)``
    regex, which is what makes the before/after comparison honest.
    """
    hits: List[tuple[int, str, str]] = []
    for constant in constants:
        flags = re.IGNORECASE if variants else 0
        for match in re.finditer(re.escape(constant), line, flags):
            start, end = match.span()
            left_delimited = start == 0 or not (
                line[start - 1].isalnum() or line[start - 1] == "_"
            )
            if not left_delimited and not embedded:
                continue
            if match.group(0) != constant:
                form = "case_variant"
            elif left_delimited and (
                end >= len(line) or not (line[end].isalnum() or line[end] == "_")
            ):
                form = "exact"
            else:
                form = "embedded"
            hits.append((start, constant, form))
    return hits


def gate_g3_constants(
    repo_root: Path,
    extra_files: Iterable[str] = (),
    *,
    constants: Optional[Sequence[str]] = None,
    variants: bool = True,
    embedded: bool = True,
    constant_folding: bool = True,
    surface_globs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    constants = tuple(constants or HOIST_CONSTANTS)
    files, missing, surface_rel = collect_surface(repo_root, extra_files, surface_globs)
    violations: List[Dict[str, Any]] = []
    self_file = _self_path()
    scanned = 0

    for path in files:
        if path.resolve() == self_file:
            continue
        scanned += 1
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        comments, strings = _literal_spans(path)
        seen: set[tuple[int, str]] = set()
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            for col, constant, form in constant_hits(
                line, constants, variants=variants, embedded=embedded
            ):
                seen.add((lineno, constant))
                violations.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "constant": constant,
                        "form": form,
                        "context": _hit_context(lineno, col, comments, strings),
                        "text": line.strip()[:200],
                    }
                )

        if constant_folding:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - defensive
                tree = None
            if tree is not None:
                table = _string_assignment_table(tree)
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                        continue
                    folded, _resolved = _fold_str(node, table)
                    if not folded:
                        continue
                    for _col, constant, form in constant_hits(
                        folded, constants, variants=variants, embedded=embedded
                    ):
                        key = (node.lineno, constant)
                        if key in seen:
                            continue
                        seen.add(key)
                        violations.append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "constant": constant,
                                "form": "folded",
                                "context": _hit_context(
                                    node.lineno, node.col_offset, comments, strings
                                ),
                                "text": ast.unparse(node)[:200],
                            }
                        )

    violations.sort(key=lambda v: (v["file"], v["line"], v["constant"]))
    by_constant: Dict[str, int] = {}
    by_context: Dict[str, int] = {}
    by_form: Dict[str, int] = {}
    for v in violations:
        by_constant[v["constant"]] = by_constant.get(v["constant"], 0) + 1
        by_context[v["context"]] = by_context.get(v["context"], 0) + 1
        by_form[v["form"]] = by_form.get(v["form"], 0) + 1
    return {
        "id": "G3_constant_hoisting",
        "title": "core surface must not hard-code hydrodynamics constants",
        "files_scanned": scanned,
        "surface": surface_rel,
        "missing_surface": missing,
        "constants": list(constants),
        "variants_enabled": bool(variants),
        "embedded_enabled": bool(embedded),
        "constant_folding_enabled": bool(constant_folding),
        "count": len(violations),
        "by_constant": by_constant,
        "by_context": by_context,
        "by_form": by_form,
        "status": "red" if violations else "green",
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_gates(
    repo_root: Path,
    api_base: str = "http://127.0.0.1:8010",
    openapi_file: Optional[str] = None,
    only: Optional[Sequence[str]] = None,
    timeout: float = 8.0,
    legacy_detectors: bool = False,
) -> Dict[str, Any]:
    wanted = {g.upper() for g in (only or ("G1", "G2", "G3"))}
    gates: Dict[str, Dict[str, Any]] = {}
    if "G1" in wanted:
        gates["G1_import_boundary"] = gate_g1_imports(
            repo_root, dynamic_imports=not legacy_detectors
        )
    if "G2" in wanted:
        gates["G2_endpoint_vocabulary"] = gate_g2_endpoints(
            api_base,
            openapi_file,
            timeout,
            metadata_scan=not legacy_detectors,
            normalize_separators=not legacy_detectors,
        )
    if "G3" in wanted:
        gates["G3_constant_hoisting"] = gate_g3_constants(
            repo_root,
            variants=not legacy_detectors,
            embedded=not legacy_detectors,
            constant_folding=not legacy_detectors,
        )

    red = [g for g in gates.values() if g["status"] == "red"]
    withheld = [g for g in gates.values() if g["status"] == "withheld"]
    total = sum(int(g["count"] or 0) for g in gates.values())
    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "repo_root": str(repo_root),
        "gate_spec": {
            "import_tokens": list(SHIP_IMPORT_TOKENS),
            "endpoint_pattern": SHIP_ENDPOINT_PATTERN,
            "hoist_constants": list(HOIST_CONSTANTS),
            "core_surface_files": list(CORE_SURFACE_FILES),
            "core_surface_dirs": list(CORE_SURFACE_DIRS),
            "self_excluded": "tools/audit/boundary_gates.py",
        },
        "gates": gates,
        "totals": {
            "violations": total,
            "gates": len(gates),
            "gates_red": len(red),
            "gates_green": sum(1 for g in gates.values() if g["status"] == "green"),
            "gates_withheld": len(withheld),
        },
    }


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|")[:160]


def render_markdown(result: Dict[str, Any], mode: str) -> str:
    gates = result["gates"]
    totals = result["totals"]
    lines: List[str] = []
    lines.append("# TensorPlatform 领域边界门 G1-G3 — 基线快照")
    lines.append("")
    lines.append(f"- 生成时间 (UTC): `{result['generated_at']}`")
    lines.append(f"- 模式: **{mode}**({'阻断门' if mode == 'enforce' else '非阻断基线，仅报告'})")
    lines.append(f"- 仓库根: `{result['repo_root']}`")
    lines.append(f"- 汇总: 违规 {totals['violations']} 条 / 红门 {totals['gates_red']} 道"
                 f" / 绿门 {totals['gates_green']} 道 / 证据不足 {totals['gates_withheld']} 道")
    lines.append("")
    lines.append("## 门总览")
    lines.append("")
    lines.append("| 门 | 语义 | 计数 | 状态 |")
    lines.append("| --- | --- | --- | --- |")
    for key, gate in gates.items():
        count = "WITHHELD" if gate.get("count") is None else gate["count"]
        lines.append(
            f"| {key} | {_md_escape(gate['title'])} | {count} | {gate['status'].upper()} |"
        )
    lines.append("")

    g1 = gates.get("G1_import_boundary")
    if g1:
        lines.append("## G1 import 边界门")
        lines.append("")
        lines.append(f"扫描核心面文件 {g1['files_scanned']} 个；命中 token: "
                     f"`{', '.join(g1['tokens'])}`。")
        lines.append("")
        if g1["violations"]:
            lines.append("| 文件 | 行 | 语句 | 命中 |")
            lines.append("| --- | --- | --- | --- |")
            for v in g1["violations"]:
                lines.append(
                    f"| `{v['file']}` | {v['line']} | `{_md_escape(v['source'])}` "
                    f"| `{v['matched_token']}` |"
                )
        else:
            lines.append("绿灯：核心面无船舶域 import。")
        lines.append("")
        if g1["missing_surface"]:
            lines.append(f"缺失的核心面路径（记录但不计违规）: "
                         f"`{', '.join(g1['missing_surface'])}`")
            lines.append("")

    g2 = gates.get("G2_endpoint_vocabulary")
    if g2:
        lines.append("## G2 端点语汇门")
        lines.append("")
        lines.append(f"- OpenAPI 源: `{g2['source']}`")
        if g2["status"] == "withheld":
            lines.append(f"- 状态: WITHHELD（无法取到 openapi.json：`{g2['error']}`）")
        else:
            lines.append(f"- 语汇正则: `{g2['pattern']}`")
            lines.append(f"- OpenAPI 路径总数: {g2['total_openapi_paths']}")
            lines.append(f"- 命中路径数 / 端点数: {g2['path_count']} / {g2['count']}")
            lines.append(f"- 规格书声明基线: {g2['spec_stated_baseline']}"
                         f"（实测 {g2['count']}，差异见报告『影响点』）")
        lines.append("")
        if g2["endpoints"]:
            lines.append("| 路径 | 方法 | 命中语汇 |")
            lines.append("| --- | --- | --- |")
            for e in g2["endpoints"]:
                lines.append(
                    f"| `{e['path']}` | {', '.join(e['methods']) or '-'} | {e['matched_token']} |"
                )
            lines.append("")
            delta = g2["count"] - g2["spec_stated_baseline"]
            if delta:
                lines.append(
                    f"**基线与规格书差异**: 规格书（审计 §7）记 {g2['spec_stated_baseline']} 个活端点，"
                    f"本机实测 {g2['count']} 个，差 {delta:+d}。测量口径：OpenAPI `paths` 中 URL "
                    f"命中语汇正则、且方法为合法 HTTP 动词的 **operation** 数（同上表）。"
                    f"差异来源为规格书成文后 `calculations/seakeeping_*` 一族端点增补；"
                    f"棘轮以实测值为锚，规格书值仅作对照留档。"
                )
                lines.append("")

    g3 = gates.get("G3_constant_hoisting")
    if g3:
        lines.append("## G3 常数外置门")
        lines.append("")
        lines.append(f"扫描文件 {g3['files_scanned']} 个；命中 {g3['count']} 处；"
                     f"按常数分布: `{json.dumps(g3['by_constant'], ensure_ascii=False)}`；"
                     f"按语境分布: `{json.dumps(g3.get('by_context', {}), ensure_ascii=False)}`"
                     f"（code=可执行代码，string=字面量/文档串，comment=注释）")
        lines.append("")
        if g3["violations"]:
            lines.append("| 文件 | 行 | 常数 | 语境 | 片段 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for v in g3["violations"][:60]:
                lines.append(
                    f"| `{v['file']}` | {v['line']} | `{v['constant']}` "
                    f"| {v.get('context', '?')} | `{_md_escape(v['text'])}` |"
                )
            if len(g3["violations"]) > 60:
                lines.append(f"| ... | | | 其余 {len(g3['violations']) - 60} 处见 JSON 基线 |")
            lines.append("")

    # --- programmatic triage / interpretation (no hand-copied numbers) -------
    lines.append("## 影响面分布（程序化统计）")
    lines.append("")
    if g1 and g1["violations"]:
        areas: Dict[str, int] = {}
        for v in g1["violations"]:
            area = "backend 核心面" if v["file"].startswith("backend/") else "tools/audit 审计面"
            areas[area] = areas.get(area, 0) + 1
        lines.append("G1 违规按面分布: " + "、".join(
            f"{k} {v} 条" for k, v in sorted(areas.items(), key=lambda kv: -kv[1])))
        lines.append("")
    if g3 and g3["violations"]:
        per_file: Dict[str, int] = {}
        for v in g3["violations"]:
            per_file[v["file"]] = per_file.get(v["file"], 0) + 1
        lines.append("| G3 文件 | 命中数 |")
        lines.append("| --- | --- |")
        for fname, cnt in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{fname}` | {cnt} |")
        lines.append("")
    if g2 and g2["status"] != "withheld":
        sorted_e = sorted(g2["endpoints"], key=lambda e: e["path"])
        by_prefix: Dict[str, int] = {}
        for e in sorted_e:
            prefix = "/".join(e["path"].split("/")[:4])
            by_prefix[prefix] = by_prefix.get(prefix, 0) + max(1, len(e["methods"]))
        lines.append("G2 端点按路由前缀分布: " + "、".join(
            f"`{k}` {v} 个" for k, v in sorted(by_prefix.items(), key=lambda kv: -kv[1])))
        lines.append("")

    lines.append("## 判定口径与翻绿方式")
    lines.append("")
    lines.append("- G1/G3 为静态扫描（AST / 文本），可离线复算，确定性。")
    lines.append("- G2 需要活服务；取不到 openapi.json 时记 WITHHELD（基线模式下不计违规，"
                 "`--enforce` 模式下判红——无法验证即不放行）。")
    lines.append("- 基线模式恒 exit 0，不阻断既有 PR 流；迁移完成后加 `--enforce` 翻为阻断门。")
    lines.append("- G3 的计数是裸 grep 语义（`KVLCC` 亦命中 `KVLCC2` 族），并额外做"
                 "「语境」三分（code/string/comment）：只有 `code` 才是真正的物理常数外泄，"
                 "`string`/`comment` 多为算例名与文档表述，属清理优先级较低的一类。")
    lines.append("- 退出码：基线模式恒 0；`--enforce` 下红门或不可评估（WITHHELD）返回 2；"
                 "内部错误返回 3。")
    lines.append("- 门脚本自身 `tools/audit/boundary_gates.py` 从扫描面剔除"
                 "（其词汇表必然含被检 token，等价于 linter 不 lint 自身词表）。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TensorPlatform domain boundary gates G1-G3 (non-blocking baseline by default)."
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="repository root (default: two levels above this script)",
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8010",
        help="API base URL whose /openapi.json is inspected (G2)",
    )
    parser.add_argument(
        "--openapi-file",
        default=None,
        help="read the OpenAPI document from a file instead of HTTP (offline/CI)",
    )
    parser.add_argument(
        "--json",
        default="artifacts/boundary_gates/baseline.json",
        help="JSON baseline output path (relative to repo root unless absolute)",
    )
    parser.add_argument(
        "--report",
        default="docs/audit/boundary_gates_baseline.md",
        help="markdown snapshot report path; empty string disables it",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="blocking mode: exit 2 when any gate is red or cannot be evaluated",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=0,
        help="allowed violations per gate in --enforce mode (ratchet head-room)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma separated subset of gates, e.g. G1,G3",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--legacy-detectors",
        action="store_true",
        help=(
            "reproduce the pre-hardening detectors (static imports only, path-only "
            "vocabulary, case-sensitive left-delimited constants) for A/B recall runs"
        ),
    )
    parser.add_argument(
        "--surface-glob",
        action="append",
        default=None,
        help="extra glob for core-surface files, e.g. 'backend/services/*.py' (repeatable)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    only = [g.strip() for g in args.only.split(",")] if args.only else None
    mode = "enforce" if args.enforce else "baseline"

    try:
        result = run_gates(
            repo_root,
            api_base=args.api_base,
            openapi_file=args.openapi_file,
            only=only,
            timeout=args.timeout,
            legacy_detectors=args.legacy_detectors,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[boundary_gates] internal error: {exc}", file=sys.stderr)
        return 3

    result["mode"] = mode
    result["enforce"] = bool(args.enforce)
    result["max_violations"] = args.max_violations
    result["legacy_detectors"] = bool(args.legacy_detectors)

    json_path = Path(args.json)
    if not json_path.is_absolute():
        json_path = repo_root / json_path
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_written = None
    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = repo_root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(result, mode), encoding="utf-8")
        report_written = report_path

    if not args.quiet:
        print(f"[boundary_gates] mode={mode} json={json_path}"
              + (f" report={report_written}" if report_written else ""))
        for key, gate in result["gates"].items():
            count = gate["count"]
            print(f"  {key}: status={gate['status']} count="
                  f"{'WITHHELD' if count is None else count}")
        print(f"  totals: {result['totals']}")

    if not args.enforce:
        return 0

    blocking: List[str] = []
    for key, gate in result["gates"].items():
        if gate["status"] == "withheld":
            blocking.append(f"{key}: withheld ({gate.get('error')})")
        elif int(gate["count"] or 0) > args.max_violations:
            blocking.append(
                f"{key}: {gate['count']} violations > max {args.max_violations}"
            )
    if blocking:
        print("[boundary_gates] ENFORCE FAIL:", file=sys.stderr)
        for item in blocking:
            print(f"  - {item}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())