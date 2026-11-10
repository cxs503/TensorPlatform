"""Knowledge Engine — four-contract structured knowledge store.

Mature pipeline: finish() → extract four contracts → validate → store → index.
Supports retrieval, provenance tracing, and uncertainty propagation.
"""
from .store import KnowledgeStore, KnowledgeEntry
from .extractor import FourContractExtractor, extract_from_finish, extract_from_report
from .validator import validate_entry, SchemaValidationError
from .search import KnowledgeSearch, SearchResult

__all__ = [
    "KnowledgeStore",
    "KnowledgeEntry",
    "FourContractExtractor",
    "extract_from_finish",
    "extract_from_report",
    "validate_entry",
    "SchemaValidationError",
    "KnowledgeSearch",
    "SearchResult",
]
