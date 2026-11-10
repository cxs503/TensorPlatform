"""Registry injection: capability slots + acceptance criteria come from config.

Zero-domain-semantics: the platform code knows nothing about what a capability
*is*.  A registry document (JSON) declares the slots and their acceptance
criteria; the same machinery then serves any domain.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import Criterion
from .store import Store


def load_registry(path: Path | str) -> Dict[str, Any]:
    """Read and minimally validate a registry document."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    caps = doc.get("capabilities") or []
    criteria = doc.get("criteria") or []
    if not isinstance(caps, list) or not isinstance(criteria, list):
        raise ValueError("registry: 'capabilities' and 'criteria' must be lists")
    seen: set[str] = set()
    for cap in caps:
        cid = cap.get("capability_id") or cap.get("id")
        if not cid:
            raise ValueError(f"registry: capability without id: {cap!r}")
        if cid in seen:
            raise ValueError(f"registry: duplicate capability id {cid!r}")
        seen.add(cid)
    for crit in criteria:
        cid = crit.get("capability_id")
        if cid not in seen:
            raise ValueError(f"registry: criterion {crit.get('criterion_id')!r} for unknown {cid!r}")
    return doc


def apply_registry(store: Store, doc: Dict[str, Any]) -> Dict[str, int]:
    """Declare slots and criteria from a registry document into ``store``."""
    n_slots = 0
    for cap in doc.get("capabilities") or []:
        store.upsert_slot(
            capability_id=cap.get("capability_id") or cap["id"],
            label=cap.get("label", ""),
            initial_level=str(cap.get("initial_level", "WITHHELD")).upper(),
            metadata=cap.get("metadata") or {},
        )
        n_slots += 1
    n_criteria = 0
    for crit in doc.get("criteria") or []:
        store.upsert_criterion(
            Criterion(
                criterion_id=crit["criterion_id"],
                capability_id=crit["capability_id"],
                spec=crit.get("spec") or {},
                target_level=str(crit.get("target_level", "REAL")).upper(),
                description=crit.get("description", ""),
            )
        )
        n_criteria += 1
    return {"slots": n_slots, "criteria": n_criteria}


def apply_registry_path(store: Store, path: Optional[Path | str]) -> Dict[str, int]:
    if not path:
        return {"slots": 0, "criteria": 0}
    return apply_registry(store, load_registry(path))


def slots_of(doc: Dict[str, Any]) -> List[str]:
    return [c.get("capability_id") or c["id"] for c in doc.get("capabilities") or []]