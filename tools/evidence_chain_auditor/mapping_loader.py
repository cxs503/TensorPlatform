"""Capability-to-test mapping loader with fail-soft semantics.

Loads a JSON configuration that maps each capability_id to its test modules.
If the mapping file is missing or malformed, degrades gracefully to WITHHELD
rather than crashing (fail-soft principle).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAPPING_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "evidence_chain_auditor"
    / "capability_test_mapping.json"
)


@dataclass
class CapabilityTestMapping:
    """Mapping from a capability_id to its test modules and minimum coverage."""

    capability_id: str
    test_modules: List[str]  # pytest module paths relative to worktree root
    min_test_cases: int = 3  # minimum number of test cases required
    domain: str = ""  # domain label for reporting (optional)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "test_modules": self.test_modules,
            "min_test_cases": self.min_test_cases,
            "domain": self.domain,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CapabilityTestMapping":
        return cls(
            capability_id=d["capability_id"],
            test_modules=d.get("test_modules", []),
            min_test_cases=d.get("min_test_cases", 3),
            domain=d.get("domain", ""),
        )


class MappingLoader:
    """Loads and validates capability-test mappings.

    Fail-soft behavior:
      - Missing file → returns empty mapping (all capabilities → WITHHELD)
      - Malformed JSON → returns empty mapping + logs warning
      - Missing fields in entry → skipped with warning
    """

    def __init__(self, mapping_path: Optional[Path] = None):
        self.mapping_path = mapping_path or DEFAULT_MAPPING_PATH
        self._mappings: Dict[str, CapabilityTestMapping] = {}
        self._load_status: str = "not_loaded"
        self._load_error: Optional[str] = None

    @property
    def load_status(self) -> str:
        """Returns: 'loaded', 'missing_file', 'parse_error', 'not_loaded'."""
        return self._load_status

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def load(self) -> Dict[str, CapabilityTestMapping]:
        """Load the mapping file. Returns empty dict on any failure (fail-soft)."""
        if not self.mapping_path.exists():
            self._load_status = "missing_file"
            self._load_error = f"Mapping file not found: {self.mapping_path}"
            logger.warning(self._load_error)
            return {}

        try:
            with open(self.mapping_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self._load_status = "parse_error"
            self._load_error = f"Failed to parse mapping file: {e}"
            logger.warning(self._load_error)
            return {}

        mappings = {}
        entries = data.get("mappings", [])
        for entry in entries:
            try:
                ctm = CapabilityTestMapping.from_dict(entry)
                mappings[ctm.capability_id] = ctm
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed mapping entry: {e}")
                continue

        self._mappings = mappings
        self._load_status = "loaded"
        self._load_error = None
        return mappings

    def get_mapping(self, capability_id: str) -> Optional[CapabilityTestMapping]:
        """Get mapping for a specific capability. Returns None if not found."""
        return self._mappings.get(capability_id)

    def all_capability_ids(self) -> List[str]:
        """Return all capability_ids in the mapping."""
        return list(self._mappings.keys())
