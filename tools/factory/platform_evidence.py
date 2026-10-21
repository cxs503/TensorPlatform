"""Bounded source reads for platform exploration; no credentials or business records."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re

ALLOWED = ("frontend-vue/src/", "backend/api/", "backend/services/", "backend/models/")
BOOTSTRAP = ["frontend-vue/src/config/modules.ts", "frontend-vue/src/config/maturity.ts",
             "frontend-vue/src/router/index.ts", "backend/main.py",
             "backend/api/domain/data.py", "backend/api/domain/model.py"]
SENSITIVE = re.compile(r"password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization|credential", re.I)
LONG_LITERAL = re.compile(r"(['\"])[A-Za-z0-9_+/=.-]{40,}\1")


def sanitize(text: str) -> str:
    return "\n".join("[REDACTED: sensitive line]" if SENSITIVE.search(line)
                     else LONG_LITERAL.sub('"[REDACTED: long literal]"', line)
                     for line in text.splitlines())


def safe_path(root: Path, relative: str) -> Path:
    if relative not in BOOTSTRAP and not relative.startswith(ALLOWED):
        raise ValueError("source path outside exploration allowlist")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix not in {".py", ".ts", ".vue"}:
        raise ValueError("invalid source path")
    if any(part.startswith(".") or SENSITIVE.search(part) for part in Path(relative).parts):
        raise ValueError("sensitive source path")
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("source missing or too large")
    return path


def read_source(root: Path, relative: str, start: int = 1, end: int = 180) -> dict:
    path = safe_path(root, relative)
    if start < 1 or end < start or end - start >= 200:
        raise ValueError("read limited to 200 lines")
    payload = path.read_bytes()
    lines = payload.decode("utf-8").splitlines()
    content = sanitize("\n".join(lines[start - 1:end]))
    # Keep line references intact. A requested window must fit in full.
    if len(content) > 24000:
        raise ValueError("source window exceeds character budget")
    return {"kind": "source", "path": relative, "start_line": start,
            "end_line": min(end, len(lines)), "total_lines": len(lines),
            "sha256": hashlib.sha256(payload).hexdigest(), "redacted": True,
            "content": content}


def inventory(root: Path) -> list[dict]:
    rows = []
    for prefix in ALLOWED:
        for path in sorted((root / prefix).rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".vue"}:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                safe_path(root, relative)
            except ValueError:
                continue
            rows.append({"path": relative, "lines": len(path.read_text().splitlines())})
    return rows


def route_catalog(root: Path) -> list[dict]:
    rows = []
    for path in sorted((root / "backend/api").glob("*.py")):
        relative = path.relative_to(root).as_posix()
        try:
            safe_path(root, relative)
            tree = ast.parse(path.read_text())
        except (ValueError, SyntaxError):
            continue
        routes = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr not in {"get", "post", "put", "delete", "patch"}:
                    continue
                arg = decorator.args[0] if decorator.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    routes.append({"method": decorator.func.attr.upper(), "path": arg.value,
                                   "function": node.name, "line": node.lineno})
        if routes:
            rows.append({"file": relative, "count": len(routes), "sample": routes[:5]})
    return rows


def validate_report(report: dict, evidence: list[dict]) -> None:
    ids = {item["id"] for item in evidence}
    if not isinstance(report.get("findings"), list) or not 3 <= len(report["findings"]) <= 16:
        raise ValueError("expected 3–16 cited findings")
    for finding in report["findings"]:
        refs = finding.get("evidence_ids")
        if not refs or not set(refs).issubset(ids):
            raise ValueError("finding cites missing evidence")
        if finding.get("verification") not in {"source-only", "http-observed", "inferred", "unverified"}:
            raise ValueError("missing verification level")
        if finding["verification"] == "http-observed" and not any(
            item["id"] in refs and item["kind"] == "http-metadata" and item.get("http_status") == 200
            for item in evidence
        ):
            raise ValueError("HTTP finding lacks a successful HTTP observation")
    if not all(isinstance(report.get(key), str) for key in ("summary", "recommended_first_workflow")):
        raise ValueError("missing report sections")
    if not isinstance(report.get("next_steps"), list) or not isinstance(report.get("limitations"), list):
        raise ValueError("missing report limits or next steps")
