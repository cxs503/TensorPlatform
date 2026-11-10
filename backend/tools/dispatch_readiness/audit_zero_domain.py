"""AC4 guard: make sure the readiness mechanism carries no domain vocabulary.

The forbidden-token list lives in configuration, never in code
(``config/dispatch_readiness_domain_terms.json`` by default). When the file is
missing or unreadable the audit degrades to identity — it reports
``status="degraded_identity"`` and no violations — instead of failing or
crashing, so a missing config can never block a merge or the dispatch loop.
"""

import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_TERMS_PATH = os.path.join(_REPO_ROOT, "config", "dispatch_readiness_domain_terms.json")


def load_terms(path: Optional[str] = None) -> Optional[Mapping[str, Any]]:
    target = path or os.getenv("TP_DISPATCH_READINESS_TERMS") or DEFAULT_TERMS_PATH
    if not target or not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _iter_files(roots: Iterable[str]) -> Iterable[str]:
    for root in roots:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name.endswith((".py", ".json", ".md", ".sql")):
                    yield os.path.join(dirpath, name)


def _patterns(terms: Mapping[str, Any]) -> List[re.Pattern]:
    out: List[re.Pattern] = []
    for term in terms.get("substring_terms") or []:
        if term:
            out.append(re.compile(re.escape(str(term)), re.IGNORECASE))
    for term in terms.get("word_terms") or []:
        if term:
            out.append(re.compile(r"\b" + re.escape(str(term)) + r"\b", re.IGNORECASE))
    return out


def audit(roots: Iterable[str], terms_path: Optional[str] = None) -> Dict[str, Any]:
    """Scan ``roots`` for configured domain vocabulary."""
    terms = load_terms(terms_path)
    roots = list(roots)
    if terms is None:
        return {
            "status": "degraded_identity",
            "reason": f"domain term configuration not found ({terms_path or DEFAULT_TERMS_PATH}); audit is a no-op",
            "scanned_roots": roots,
            "files_scanned": 0,
            "violations": [],
        }

    patterns = _patterns(terms)
    files = list(_iter_files(roots))
    violations: List[Dict[str, Any]] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    violations.append(
                        {
                            "file": path,
                            "line": lineno,
                            "token": match.group(0),
                            "text": line.strip()[:160],
                        }
                    )
    return {
        "status": "clean" if not violations else "violations",
        "terms_source": terms_path or DEFAULT_TERMS_PATH,
        "term_count": len(patterns),
        "scanned_roots": roots,
        "files_scanned": len(files),
        "violations": violations,
    }


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import sys

    targets = sys.argv[1:] or ["backend/tools/dispatch_readiness", "backend/tests"]
    print(json.dumps(audit(targets), ensure_ascii=False, indent=2))