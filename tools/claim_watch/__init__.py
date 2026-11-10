"""Claim freshness watch: machine-decidable expiry of numeric claims.

Public surface
--------------
``plans``     none of this module reads a domain; ``unit`` and ``artifact_uri``
``checker``   three fault classes (BROKEN / STALE / SUSPECT) + automatic grade
              downgrade, deterministic and offline.
``fingerprint`` environment fingerprint and its architectural comparison.
``claims``    claim/1.0 records, schema validation, read-only URI resolution.
``harvest``   build claims from the ``(path, sha256)`` pairs repositories already
              record; scan raw pairs for replay.
``retro``     git-labelled historical drift replay (STALE recall measurement).
``audit``     cross-worktree batch with the acceptance numbers (A1..A5).
"""
from __future__ import annotations

__all__ = ["checker", "claims", "fingerprint", "harvest", "retro", "audit"]
__version__ = "1.0.0"