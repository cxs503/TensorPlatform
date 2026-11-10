"""SimHash fingerprinting for duplicate detection.

Implements SimHash for near-duplicate detection of proposal/node titles+goals.
Used by the admission gate to reject proposals that duplicate negated hypotheses.

Algorithm:
  1. Normalize text: lowercase → remove stop words → 3-gram shingles
  2. Hash each shingle to a 64-bit integer
  3. For each bit position, sum +1 if bit=1, -1 if bit=0
  4. Final hash: bit=1 if sum>0, else 0
  5. Similarity: 1 - (hamming_distance / 64)

Zero domain semantics: stop words are generic English/Chinese, not domain-specific.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# Generic stop words (English + common Chinese particles)
# Zero domain semantics: these are linguistic, not scientific
_STOP_WORDS: Set[str] = {
    # English
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "it", "its", "this",
    "that", "these", "those", "i", "you", "he", "she", "we", "they", "me",
    "him", "her", "us", "them", "my", "your", "his", "our", "their",
    "what", "which", "who", "whom", "when", "where", "why", "how",
    "not", "no", "nor", "as", "if", "then", "than", "too", "very",
    # Chinese particles
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
}


def _normalize(text: str) -> str:
    """Normalize text: lowercase, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    # Lowercase
    s = text.lower()
    # Replace punctuation/symbols with space
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    # Collapse whitespace
    s = " ".join(s.split())
    return s


def _tokenize(text: str) -> List[str]:
    """Tokenize text into words, removing stop words."""
    normalized = _normalize(text)
    words = normalized.split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _shingles(tokens: List[str], n: int = 3) -> List[str]:
    """Generate character n-gram shingles from tokens."""
    # Join tokens and generate character n-grams
    text = "".join(tokens)
    if len(text) < n:
        return [text] if text else []
    return [text[i:i + n] for i in range(len(text) - n + 1)]


def _hash_shingle(shingle: str) -> int:
    """Hash a shingle to a 64-bit integer."""
    h = hashlib.md5(shingle.encode("utf-8")).digest()
    # Take first 8 bytes as uint64
    return struct.unpack("<Q", h[:8])[0]


@dataclass
class SimHash:
    """SimHash fingerprint for near-duplicate detection.

    Attributes:
        hash_value: 64-bit integer fingerprint
        shingle_count: number of shingles used
    """
    hash_value: int
    shingle_count: int = 0

    @classmethod
    def from_text(cls, text: str, shingle_size: int = 3) -> "SimHash":
        """Compute SimHash from text."""
        tokens = _tokenize(text)
        shingles = _shingles(tokens, shingle_size)

        if not shingles:
            return cls(hash_value=0, shingle_count=0)

        # Vector of bit sums
        v = [0] * 64

        for shingle in shingles:
            h = _hash_shingle(shingle)
            for i in range(64):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1

        # Convert to fingerprint
        fingerprint = 0
        for i in range(64):
            if v[i] > 0:
                fingerprint |= (1 << i)

        return cls(hash_value=fingerprint, shingle_count=len(shingles))

    def hamming_distance(self, other: "SimHash") -> int:
        """Compute Hamming distance to another SimHash."""
        xor = self.hash_value ^ other.hash_value
        return bin(xor).count("1")

    def similarity(self, other: "SimHash") -> float:
        """Compute similarity (0.0 to 1.0) based on Hamming distance."""
        dist = self.hamming_distance(other)
        return 1.0 - (dist / 64.0)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SimHash):
            return False
        return self.hash_value == other.hash_value

    def __hash__(self) -> int:
        return hash(self.hash_value)


@dataclass
class FingerprintGate:
    """Admission gate using SimHash for duplicate detection.

    Maintains a registry of known fingerprints (from negated hypotheses).
    New proposals/nodes are checked against this registry.

    Configuration:
        similarity_threshold: above this → reject as duplicate
        review_threshold: above this → flag for review
    """

    similarity_threshold: float = 0.85
    review_threshold: float = 0.70
    _registry: Dict[str, SimHash] = field(default_factory=dict)  # id → fingerprint
    _metadata: Dict[str, Dict] = field(default_factory=dict)  # id → metadata

    def register(self, entry_id: str, text: str, metadata: Dict | None = None) -> SimHash:
        """Register a fingerprint (e.g., from a negated hypothesis)."""
        fp = SimHash.from_text(text)
        self._registry[entry_id] = fp
        self._metadata[entry_id] = metadata or {}
        return fp

    def check(self, text: str) -> Tuple[bool, str, List[Tuple[str, float]]]:
        """Check text against registered fingerprints.

        Returns:
            (passed, action, hits)
            - passed: True if no duplicate detected
            - action: "pass", "review", or "reject"
            - hits: list of (entry_id, similarity) for matches above review_threshold
        """
        fp = SimHash.from_text(text)
        hits = []

        for entry_id, registered_fp in self._registry.items():
            sim = fp.similarity(registered_fp)
            if sim >= self.review_threshold:
                hits.append((entry_id, sim))

        # Sort by similarity descending
        hits.sort(key=lambda x: x[1], reverse=True)

        if not hits:
            return True, "pass", []

        max_sim = hits[0][1]
        if max_sim >= self.similarity_threshold:
            return False, "reject", hits

        return True, "review", hits

    def find_duplicates(self, pairs: List[Tuple[str, str, str, str]]) -> List[Tuple[str, str, float]]:
        """Check a list of known duplicate pairs.

        Args:
            pairs: list of (id1, text1, id2, text2)

        Returns:
            list of (id1, id2, similarity) for pairs that match
        """
        results = []
        for id1, text1, id2, text2 in pairs:
            fp1 = SimHash.from_text(text1)
            fp2 = SimHash.from_text(text2)
            sim = fp1.similarity(fp2)
            results.append((id1, id2, sim))
        return results

    @property
    def registry_size(self) -> int:
        return len(self._registry)

    def clear(self) -> None:
        self._registry.clear()
        self._metadata.clear()
