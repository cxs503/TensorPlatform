"""Whole-library WITHHELD scanner (domain-neutral).

The node criterion is a *library-wide* scan, not a sample-of-reports scan. The
scanner therefore walks a configured set of library sources:

``ledger_rows``
    A JSON document holding one record per capability. Field names, the level
    field and the value that means "withheld" all come from configuration, so no
    subject-domain vocabulary is baked into the code.
``ledger_rows_aux``
    A second tabular source joined by id, used to enrich the declaration text
    (notes / remediation text). Optional.
``text_corpus``
    Free-text documents keyed by id in nested containers. Sentences carrying the
    configured withheld token are attached to the item whose id appears in the
    same document. This recovers declarations that live in prose rather than in
    a structured column.

Everything is deterministic: records are sorted by ``(source_id, capability_id)``,
set-like fields are sorted and de-duplicated, and no wall-clock value enters the
output.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import LibraryItem, ScanResult

ROOT = Path(__file__).resolve().parents[2]

_MAX_SNIPPETS = 3


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _get(record: Dict[str, Any], dotted: Optional[str]) -> Any:
    if not dotted:
        return None
    cur: Any = record
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _sentences(text: str) -> List[str]:
    """Split prose into sentences without language assumptions."""
    parts = re.split(r"(?<=[.;!?。；！？\n])\s*", text)
    return [p.strip() for p in parts if p and p.strip()]


class LibraryScanner:
    """Scan configured library sources for WITHHELD entries."""

    def __init__(self, config: Dict[str, Any], root: Path | str | None = None):
        self.config = config
        self.root = Path(root or ROOT)
        self.withheld_level = str(config.get("withheld_level", "WITHHELD"))
        self.withheld_token = str(config.get("withheld_token", "WITHHELD"))
        self.sources: List[Dict[str, Any]] = list(config.get("library_sources", []))
        self._class_overrides = self._load_class_map()

    # ------------------------------------------------------------------ inputs
    def _load_class_map(self) -> Dict[str, Dict[str, Any]]:
        """Optional injected classification of library rows (criticality etc.).

        Kept out of code on purpose: "is this row evidence-bearing?" is domain
        knowledge, so it is supplied as data through the config path.
        """
        spec = self.config.get("capability_class_map")
        if not spec:
            return {}
        path = self.root / spec["path"]
        if not path.exists():
            return {}
        payload = _read_json(path)
        rows = payload.get(spec.get("record_path", "capabilities"), payload)
        if isinstance(rows, dict):
            return {str(k): dict(v) for k, v in rows.items()}
        return {str(r.get("id", "")): dict(r) for r in rows if isinstance(r, dict)}

    def _load_source(self, spec: Dict[str, Any]) -> Any:
        path = self.root / spec["path"]
        if not path.exists():
            raise FileNotFoundError(f"library source missing: {spec['path']}")
        return _read_json(path)

    def _rows(self, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        payload = self._load_source(spec)
        rows = _get(payload, spec.get("record_path")) if spec.get("record_path") else payload
        if isinstance(payload, dict) and rows is None:
            rows = payload
        if isinstance(rows, dict):
            rows = [dict(v, **{"id": k}) for k, v in rows.items()]
        return [r for r in rows if isinstance(r, dict)]

    # -------------------------------------------------------------------- scan
    def scan(self) -> ScanResult:
        aux_text: Dict[str, Dict[str, str]] = {}
        aux_spec = next(
            (s for s in self.sources if s.get("kind") == "ledger_rows_aux"), None
        )
        if aux_spec:
            aux_id = aux_spec.get("id_field", "id")
            fields = list(aux_spec.get("text_fields", []))
            for row in self._rows(aux_spec):
                rid = str(row.get(aux_id, ""))
                aux_text[rid] = {
                    f: str(row[f]) for f in fields if row.get(f) not in (None, "")
                }

        corpus = self._corpus_index(
            [s for s in self.sources if s.get("kind") == "text_corpus"]
        )

        items: List[LibraryItem] = []
        stats: Dict[str, Any] = {"sources": {}, "corpus_documents": len(corpus)}

        for spec in self.sources:
            kind = spec.get("kind")
            if kind != "ledger_rows":
                continue
            source_id = spec.get("id", spec.get("path", "ledger"))
            rows = self._rows(spec)
            id_field = spec.get("id_field", "id")
            level_field = spec.get("level_field")
            claimed_field = spec.get("claimed_field")
            evidence_field = spec.get("evidence_field")
            text_fields = list(spec.get("text_fields", []))
            n_seen = 0
            n_selected = 0
            for row in rows:
                n_seen += 1
                level = str(_get(row, level_field) or "") if level_field else ""
                if level_field and level != self.withheld_level:
                    continue
                rid = str(_get(row, id_field) or row.get(id_field, ""))
                if not rid:
                    continue
                n_selected += 1
                texts: Dict[str, str] = {}
                for f in text_fields:
                    value = _get(row, f)
                    if value not in (None, ""):
                        texts[f] = str(value)
                texts.update(aux_text.get(rid, {}))
                override = self._class_overrides.get(rid, {})
                snippets = corpus.get(rid, [])
                items.append(LibraryItem(
                    capability_id=rid,
                    source_id=source_id,
                    claimed_level=str(_get(row, claimed_field) or "UNKNOWN"),
                    reconciled_level=level or self.withheld_level,
                    evidence_refs=list(_get(row, evidence_field) or []),
                    text_fields=texts,
                    corpus_snippets=snippets,
                    criticality=float(override.get("criticality", 0.5)),
                    requires_evidence=bool(override.get("requires_evidence", True)),
                ))
            stats["sources"][source_id] = {
                "kind": kind,
                "path": spec["path"],
                "rows_seen": n_seen,
                "rows_selected": n_selected,
            }

        items.sort(key=lambda i: (i.source_id, i.capability_id))
        stats["corpus_snippets_attached"] = sum(len(i.corpus_snippets) for i in items)
        return ScanResult(items=items, source_stats=stats)

    # ------------------------------------------------------------------ corpus
    def _corpus_index(self, specs: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Map capability id -> withheld-token sentences found in prose."""
        index: Dict[str, List[str]] = {}
        token_re = re.compile(re.escape(self.withheld_token), re.IGNORECASE)
        id_re = re.compile(r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+")
        for spec in specs:
            payload = self._load_source(spec)
            for container in spec.get("containers", []):
                docs = _get(payload, container) or {}
                if not isinstance(docs, dict):
                    continue
                for key, doc in docs.items():
                    if not isinstance(doc, dict):
                        continue
                    blob = " ".join(
                        str(doc.get(f, "")) for f in spec.get("text_fields", [])
                    )
                    blob = f"{key} {blob}"
                    if not token_re.search(blob):
                        continue
                    ids_here = set(spec.get("id_aliases", {}).get(key, [])) | set(
                        m.group(0) for m in id_re.finditer(blob)
                    )
                    hit_sentences = [
                        s for s in _sentences(blob) if token_re.search(s)
                    ][:_MAX_SNIPPETS]
                    for cid in sorted(ids_here):
                        bucket = index.setdefault(cid, [])
                        for s in hit_sentences:
                            if s not in bucket and len(bucket) < _MAX_SNIPPETS:
                                bucket.append(s)
        return index