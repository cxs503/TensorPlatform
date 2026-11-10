"""Pre-submission domain-semantics gate built on the platform leak scanner.

The mechanism itself stays domain-neutral: the vocabulary is DATA
(``config/domain_word_denylist.json``) and the matching engine is the shared
scanner ``tools/audit/domain_leak_scan.py`` so there is exactly one definition of
"domain wording" on the platform. Default policy is fail-closed (any hit rejects
the staged change); ``mode='warn'`` only downgrades the verdict for callers that
explicitly opt in.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence

ROOT = Path(__file__).resolve().parents[3]
SCANNER_PATH = ROOT / "tools" / "audit" / "domain_leak_scan.py"
DEFAULT_LEXICON = ROOT / "config" / "domain_word_denylist.json"
TEXT_SUFFIXES = (
    ".py", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh",
    ".js", ".ts", ".sql",
)


def _load_scanner():
    spec = importlib.util.spec_from_file_location("tenant_isolation_leak_scanner", SCANNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load shared scanner at {SCANNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateVerdict(NamedTuple):
    allowed: bool
    mode: str
    files_checked: int
    total_hits: int
    violations: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "files_checked": self.files_checked,
            "total_hits": self.total_hits,
            "violations": self.violations,
        }


class DomainTextGate:
    def __init__(
        self,
        lexicon: Mapping[str, Any],
        mode: str = "enforce",
        exempt_prefixes: Sequence[str] = (),
    ):
        if mode not in ("enforce", "warn"):
            raise ValueError(f"unknown gate mode {mode!r}")
        self._scanner = _load_scanner()
        self.lexicon = dict(lexicon)
        self.mode = mode
        self.exempt_prefixes = tuple(exempt_prefixes)
        self.patterns = self._scanner.compile_patterns(self.lexicon)

    @classmethod
    def from_config(
        cls,
        lexicon_path: str | Path | None = None,
        mode: str = "enforce",
        exempt_prefixes: Sequence[str] = (),
    ) -> "DomainTextGate":
        path = Path(lexicon_path) if lexicon_path is not None else DEFAULT_LEXICON
        doc = cls._load_lexicon(path)
        exempt = tuple(doc.get("exempt") or ()) + tuple(exempt_prefixes)
        return cls(doc, mode=mode, exempt_prefixes=exempt)

    @staticmethod
    def _load_lexicon(path: Path) -> Dict[str, Any]:
        import json

        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- scanning --------------------------------------------------------
    def is_exempt(self, name: str) -> bool:
        norm = str(name).replace("\\", "/")
        for prefix in self.exempt_prefixes:
            p = str(prefix).replace("\\", "/")
            if p and (norm == p or norm.startswith(p.rstrip("/") + "/")):
                return True
        return False

    def check_text(self, name: str, text: str) -> List[Dict[str, Any]]:
        hits = self._scanner.scan_text(text, self.patterns)
        for h in hits:
            h["file"] = str(name)
        return hits

    def check_submission(self, files: Mapping[str, str]) -> GateVerdict:
        """Check staged in-memory files (path -> text); reject on any hit."""
        violations: List[Dict[str, Any]] = []
        checked = 0
        for name, text in files.items():
            if self.is_exempt(name):
                continue
            checked += 1
            violations.extend(self.check_text(name, text))
        return self._verdict(checked, violations)

    def check_paths(self, paths: Iterable[str | Path]) -> GateVerdict:
        violations: List[Dict[str, Any]] = []
        checked = 0
        for raw in paths:
            p = Path(raw)
            if not p.is_absolute():
                p = ROOT / p
            if not p.is_file():
                continue
            rel = str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) else str(p)
            if self.is_exempt(rel) or p.suffix not in TEXT_SUFFIXES:
                continue
            checked += 1
            violations.extend(self.check_text(rel, p.read_text(encoding="utf-8", errors="ignore")))
        return self._verdict(checked, violations)

    def _verdict(self, checked: int, violations: List[Dict[str, Any]]) -> GateVerdict:
        allowed = (not violations) or self.mode == "warn"
        return GateVerdict(
            allowed=allowed,
            mode=self.mode,
            files_checked=checked,
            total_hits=len(violations),
            violations=violations,
        )