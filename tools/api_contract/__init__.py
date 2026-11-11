"""Generic OpenAPI contract-testing harness (TensorPlatform shared tooling).

Public surface (all domain agnostic):

* :mod:`tools.api_contract.spec`      -- load a spec, degrade to WITHHELD/ERROR
* :mod:`tools.api_contract.generate`  -- spec-driven case generation
* :mod:`tools.api_contract.targets`   -- live HTTP / in-process ASGI / stubs
* :mod:`tools.api_contract.checks`    -- schema, status, numeric-precision checks
* :mod:`tools.api_contract.breaking`  -- incompatible-change detection + mutations
* :mod:`tools.api_contract.runner`    -- orchestration (cases, matrix, invariants)
* :mod:`tools.api_contract.report`    -- acceptance criteria and artifacts
* :mod:`tools.api_contract.cli`       -- command line entry point

Anything application specific (spec source, base URL, request payloads, numeric
invariant expressions) is **injected as data** at run time; see
``runs/<mission>/contract_config.json`` style configs.
"""
from __future__ import annotations

__all__ = [
    "spec",
    "generate",
    "targets",
    "checks",
    "breaking",
    "runner",
    "report",
    "cli",
]

__version__ = "2.0.0"