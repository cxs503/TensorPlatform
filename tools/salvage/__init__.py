"""Salvage capability: turn the uncommitted output of dead missions into evidence.

Public entry points: :mod:`tools.salvage.cli` (replay / negctl / acceptance /
figures).  See ``docs/salvage/SALVAGE_CAPABILITY.md`` for the contract and
``artifacts/salvage/acceptance.json`` for the machine-checkable acceptance run.
"""

from . import classify, dedupe, evidence, gitutil, manifest, platform_api, probe, resume  # noqa: F401

__all__ = [
    "classify",
    "dedupe",
    "evidence",
    "gitutil",
    "manifest",
    "platform_api",
    "probe",
    "resume",
]