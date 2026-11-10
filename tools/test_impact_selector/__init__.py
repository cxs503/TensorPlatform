"""Change-impact driven test subset selection (platform capability).

Maturity track: p299w4-n05-test-subset-maturity.

Public surface::

    from tools.test_impact_selector import ImportGraph, ImpactSelector, Ledger

    graph = ImportGraph.build(REPO_ROOT)
    sel = ImpactSelector(graph).select(["tools/failure_minimizer/ddmin.py"])
    sel.mode      # "subset" | "full"
    sel.tests     # repo-relative test file list (never empty)
"""

from .config import SelectorConfig
from .graph import ImportGraph
from .ledger import Ledger
from .selector import ImpactSelector, Selection

__all__ = [
    "ImportGraph",
    "ImpactSelector",
    "Ledger",
    "Selection",
    "SelectorConfig",
]