"""Knowledge search — retrieval and ranking for knowledge entries.

Supports:
- Full-text search via FTS5
- Filter by domain, outcome, source_type, repo
- Provenance chain traversal
- Uncertainty propagation
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from .store import KnowledgeEntry, KnowledgeStore


@dataclass
class SearchResult:
    """Search result with entry and relevance score."""
    entry: KnowledgeEntry
    score: float
    highlight: str = ""

    def to_dict(self) -> dict:
        return {
            "entry": self.entry.to_dict(),
            "score": self.score,
            "highlight": self.highlight,
        }


class KnowledgeSearch:
    """Search interface for knowledge store."""

    def __init__(self, store: KnowledgeStore):
        self.store = store

    def search(
        self,
        query: str,
        limit: int = 20,
        domain: Optional[str] = None,
        outcome: Optional[str] = None,
        source_type: Optional[str] = None,
        min_confidence: Optional[str] = None,
    ) -> list[SearchResult]:
        """Search for knowledge entries.

        Args:
            query: Search query text
            limit: Maximum results to return
            domain: Filter by domain
            outcome: Filter by outcome
            source_type: Filter by source type
            min_confidence: Minimum confidence level (high > medium > low)

        Returns:
            List of SearchResult objects
        """
        start_time = time.time()

        # Full-text search
        fts_results = self.store.search(query, limit=limit * 2)  # Get more for filtering

        results = []
        confidence_order = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
        min_conf_level = confidence_order.get(min_confidence, 0) if min_confidence else 0

        for entry_id, rank in fts_results:
            entry = self.store.get(entry_id)
            if not entry:
                continue

            # Apply filters
            if domain and entry.problem_statement.get("domain") != domain:
                continue
            if outcome and entry.conclusion.get("outcome") != outcome:
                continue
            if source_type and entry.provenance.get("source_type") != source_type:
                continue

            # Check confidence
            entry_conf = entry.uncertainty.get("confidence", "unknown")
            if confidence_order.get(entry_conf, 0) < min_conf_level:
                continue

            # Convert FTS rank to score (lower rank = better match)
            score = 1.0 / (1.0 + abs(rank))

            # Generate highlight
            highlight = self._generate_highlight(entry, query)

            results.append(SearchResult(entry=entry, score=score, highlight=highlight))

            if len(results) >= limit:
                break

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)

        return results

    def get_by_provenance(
        self,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> list[KnowledgeEntry]:
        """Get entries by provenance chain.

        Args:
            source_type: Filter by source type (finish_call, report_extract, etc.)
            source_id: Filter by source ID (mission ID, report path, etc.)
            repo: Filter by repository

        Returns:
            List of matching entries
        """
        return self.store.list_entries(
            limit=1000,
            source_type=source_type,
            repo=repo,
        )

    def get_related(self, entry_id: str, limit: int = 10) -> list[SearchResult]:
        """Get entries related to a given entry.

        Uses tags and domain for similarity matching.
        """
        entry = self.store.get(entry_id)
        if not entry:
            return []

        # Get tags and domain
        tags = entry.problem_statement.get("tags", [])
        domain = entry.problem_statement.get("domain", "")

        # Search for similar entries
        query = " ".join(tags) if tags else domain
        if not query:
            return []

        results = self.search(query, limit=limit + 1)
        # Remove the original entry
        return [r for r in results if r.entry.entry_id != entry_id][:limit]

    def trace_provenance(self, entry_id: str) -> list[dict]:
        """Trace the provenance chain for an entry.

        Returns list of provenance nodes from source to entry.
        """
        entry = self.store.get(entry_id)
        if not entry:
            return []

        chain = []

        # Add current entry
        chain.append({
            "entry_id": entry.entry_id,
            "source_type": entry.provenance.get("source_type"),
            "source_id": entry.provenance.get("source_id"),
            "timestamp": entry.created_at,
        })

        # Follow relations
        for rel in entry.relations:
            if rel.get("relation_type") == "derived_from":
                target_id = rel.get("target_id")
                if target_id:
                    target = self.store.get(target_id)
                    if target:
                        chain.append({
                            "entry_id": target.entry_id,
                            "source_type": target.provenance.get("source_type"),
                            "source_id": target.provenance.get("source_id"),
                            "timestamp": target.created_at,
                        })

        return chain

    def propagate_uncertainty(self, entry_id: str) -> dict:
        """Propagate uncertainty through the provenance chain.

        Returns aggregated uncertainty assessment.
        """
        chain = self.trace_provenance(entry_id)
        if not chain:
            return {"confidence": "unknown", "confidence_score": 0.0}

        # Get uncertainty from all entries in chain
        confidences = []
        for node in chain:
            entry = self.store.get(node["entry_id"])
            if entry:
                conf = entry.uncertainty.get("confidence", "unknown")
                score = entry.uncertainty.get("confidence_score", 0.5)
                confidences.append((conf, score))

        if not confidences:
            return {"confidence": "unknown", "confidence_score": 0.0}

        # Aggregate: take minimum confidence (weakest link)
        confidence_order = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
        min_conf = min(confidences, key=lambda x: confidence_order.get(x[0], 0))
        avg_score = sum(c[1] for c in confidences) / len(confidences)

        return {
            "confidence": min_conf[0],
            "confidence_score": avg_score,
            "chain_length": len(chain),
            "sources_of_uncertainty": [
                f"inherited_from_{node['entry_id']}" for node in chain[1:]
            ],
        }

    def get_knowledge_distribution(self) -> dict:
        """Get knowledge distribution statistics."""
        stats = self.store.get_stats()

        return {
            "total_entries": stats["total"],
            "by_domain": stats["by_domain"],
            "by_outcome": stats["by_outcome"],
            "by_source_type": stats["by_source_type"],
            "by_repo": stats["by_repo"],
        }

    def get_retrieval_heatmap(self, days: int = 30) -> dict:
        """Get retrieval heatmap data (simulated for now).

        In production, this would track actual retrieval patterns.
        """
        # For now, return distribution as proxy for "hot" areas
        stats = self.store.get_stats()

        return {
            "period_days": days,
            "hot_domains": sorted(
                stats["by_domain"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:10],
            "hot_repos": sorted(
                stats["by_repo"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:10],
        }

    def _generate_highlight(self, entry: KnowledgeEntry, query: str) -> str:
        """Generate a highlight snippet for search results."""
        query_lower = query.lower()
        query_terms = query_lower.split()

        # Try to find matching text in entry
        texts = [
            entry.problem_statement.get("goal", ""),
            entry.methodology.get("approach", ""),
            entry.conclusion.get("summary", ""),
        ]

        for text in texts:
            if not text:
                continue
            text_lower = text.lower()
            for term in query_terms:
                if term in text_lower:
                    # Extract context around match
                    idx = text_lower.find(term)
                    start = max(0, idx - 50)
                    end = min(len(text), idx + len(term) + 50)
                    snippet = text[start:end]
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(text):
                        snippet = snippet + "..."
                    return snippet

        # Fallback: return first 100 chars of goal
        goal = entry.problem_statement.get("goal", "")
        return goal[:100] + "..." if len(goal) > 100 else goal
