"""KPI-GATE-100: machine-checkable precision ledger for benchmark comparisons."""

from .baselines import CanonicalRegistry, load_registry, ship_type_of
from .check import check_entry, check_file, check_ledger
from .ledger import (ANCHOR, ATTRIBUTION_RULES, SCHEMA, build_entries, build_ledger,
                     gate_adjudication, summarize, write_ledger)
from .model import (ADOPTED_GATE_BASIS, GATE_BASIS_OPTIONS, GRADES, REL_ERR_TOL,
                    TOL_HARD, TOL_TARGET, Baseline, Entry)
from .sources import RawRecord, scan_all

__all__ = [
    "ANCHOR", "ADOPTED_GATE_BASIS", "ATTRIBUTION_RULES", "Baseline",
    "CanonicalRegistry", "Entry", "GATE_BASIS_OPTIONS", "GRADES", "REL_ERR_TOL",
    "RawRecord", "SCHEMA", "TOL_HARD", "TOL_TARGET", "build_entries",
    "build_ledger", "check_entry", "check_file", "check_ledger",
    "gate_adjudication", "load_registry", "scan_all", "ship_type_of",
    "summarize", "write_ledger",
]