"""21-capability structured registry.

Each capability has:
- capability_id: unique identifier
- domain: one of 9 ship performance domains
- aspect: algorithm | verification | service
- label: human-readable name
- expected_io: expected input/output description
- evidence_level: current assessed level (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED)
- code_patterns: keywords for code detection
- test_patterns: keywords for test detection

The registry is loaded from config/capabilities.json (zero domain semantics in code).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 9 ship performance domains
CAPABILITY_DOMAINS = [
    "resistance",
    "propulsion",
    "seakeeping",
    "manoeuvring",
    "stability",
    "structure",
    "vibration_noise",
    "green_navigation",
    "ice_navigation",
]

# 3 aspects per capability
CAPABILITY_ASPECTS = ["algorithm", "verification", "service"]

VALID_LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")


@dataclass
class Capability:
    """Single capability entry."""
    capability_id: str
    domain: str
    aspect: str
    label: str
    description: str
    expected_io: dict[str, str]
    evidence_level: str = "WITHHELD"
    code_patterns: list[str] = field(default_factory=list)
    test_patterns: list[str] = field(default_factory=list)
    report_patterns: list[str] = field(default_factory=list)
    bindings_code: list[str] = field(default_factory=list)
    bindings_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "domain": self.domain,
            "aspect": self.aspect,
            "label": self.label,
            "description": self.description,
            "expected_io": self.expected_io,
            "evidence_level": self.evidence_level,
            "code_patterns": self.code_patterns,
            "test_patterns": self.test_patterns,
            "report_patterns": self.report_patterns,
            "bindings_code": self.bindings_code,
            "bindings_tests": self.bindings_tests,
        }


# Domain mapping: capability_id prefix → domain
_DOMAIN_MAP = {
    "calc.resistance": "resistance",
    "calc.stability": "stability",
    "propeller.open_water": "propulsion",
    "geometry.generate": "resistance",  # geometry supports resistance calculations
    "geometry.hydro_curves": "stability",
    "geometry.list_models": "resistance",
    "geometry.verify_official": "resistance",
    "shiphull.generate": "resistance",
    "stdmodels.lookup": "resistance",
    "gov.stats": "green_navigation",
    "knowledge.docs": "green_navigation",
    "dev.gate_5415": "vibration_noise",
    "dev.git": "structure",
    "dev.logs": "structure",
    "dev.nginx_audit": "manoeuvring",
    "dev.probe": "seakeeping",
    "dev.restart": "propulsion",
    "dev.services": "ice_navigation",
    "ai.predict": "green_navigation",
    "methods.computation": "seakeeping",
    "misc.stubs": "manoeuvring",
}

# Aspect mapping based on capability type
_ASPECT_MAP = {
    "calc.": "algorithm",
    "propeller.": "algorithm",
    "geometry.": "algorithm",
    "shiphull.": "algorithm",
    "stdmodels.": "service",
    "gov.": "service",
    "knowledge.": "service",
    "dev.": "verification",
    "ai.": "algorithm",
    "methods.": "verification",
    "misc.": "verification",
}


class CapabilityRegistry:
    """Load and manage the 21-capability registry."""

    def __init__(self, config_path: str | Path | None = None,
                 spec_path: str | Path | None = None):
        self._capabilities: dict[str, Capability] = {}
        self._config_path = Path(config_path) if config_path else None
        self._spec_path = Path(spec_path) if spec_path else None
        self._load()

    def _load(self):
        """Load capabilities from config files."""
        caps_data = {}

        # Load from capabilities.json
        if self._config_path and self._config_path.exists():
            with open(self._config_path) as f:
                data = json.load(f)
            for cap in data.get("capabilities", []):
                cid = cap["capability_id"]
                caps_data[cid] = {
                    "label": cap.get("label", cid),
                    "description": cap.get("description", ""),
                    "code_patterns": cap.get("code_patterns", []),
                    "test_patterns": cap.get("test_patterns", []),
                    "report_patterns": cap.get("report_patterns", []),
                    "initial_level": cap.get("initial_level", "WITHHELD"),
                }

        # Load bindings from spec.json
        spec_data = {}
        if self._spec_path and self._spec_path.exists():
            with open(self._spec_path) as f:
                spec = json.load(f)
            for cap in spec.get("capabilities", []):
                cid = cap["id"]
                bindings = cap.get("bindings", {})
                spec_data[cid] = {
                    "bindings_code": bindings.get("code", []),
                    "bindings_tests": bindings.get("tests", []),
                    "expected_level": cap.get("expected_level"),
                }

        # Build Capability objects for all 21
        all_ids = set(caps_data.keys()) | set(spec_data.keys())
        for cid in sorted(all_ids):
            cd = caps_data.get(cid, {})
            sd = spec_data.get(cid, {})

            # Determine domain
            domain = _DOMAIN_MAP.get(cid, "resistance")

            # Determine aspect
            aspect = "algorithm"
            for prefix, asp in _ASPECT_MAP.items():
                if cid.startswith(prefix):
                    aspect = asp
                    break

            cap = Capability(
                capability_id=cid,
                domain=domain,
                aspect=aspect,
                label=cd.get("label", cid),
                description=cd.get("description", ""),
                expected_io={
                    "input": f"Parameters for {cd.get('label', cid)}",
                    "output": f"Results for {cd.get('label', cid)}",
                },
                evidence_level=cd.get("initial_level", "WITHHELD"),
                code_patterns=cd.get("code_patterns", []),
                test_patterns=cd.get("test_patterns", []),
                report_patterns=cd.get("report_patterns", []),
                bindings_code=sd.get("bindings_code", []),
                bindings_tests=sd.get("bindings_tests", []),
            )
            self._capabilities[cid] = cap

    @property
    def capabilities(self) -> list[Capability]:
        """Return all 21 capabilities."""
        return list(self._capabilities.values())

    @property
    def count(self) -> int:
        return len(self._capabilities)

    def get(self, capability_id: str) -> Capability | None:
        return self._capabilities.get(capability_id)

    def get_by_domain(self, domain: str) -> list[Capability]:
        return [c for c in self._capabilities.values() if c.domain == domain]

    def get_by_aspect(self, aspect: str) -> list[Capability]:
        return [c for c in self._capabilities.values() if c.aspect == aspect]

    def update_level(self, capability_id: str, level: str):
        """Update the evidence level for a capability."""
        if level not in VALID_LEVELS:
            raise ValueError(f"Invalid level: {level}. Must be one of {VALID_LEVELS}")
        if capability_id in self._capabilities:
            self._capabilities[capability_id].evidence_level = level

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "capability_count": self.count,
            "domains": CAPABILITY_DOMAINS,
            "aspects": CAPABILITY_ASPECTS,
            "capabilities": [c.to_dict() for c in self.capabilities],
        }

    def save(self, path: str | Path):
        """Save registry to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
