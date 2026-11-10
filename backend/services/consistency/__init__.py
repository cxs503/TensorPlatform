"""Cross-solver consistency gate (TensorPlatform, node P218-N02).

The ecosystem runs several independent solver families on the same physical
problem.  Their outputs are quoted side by side in reports, but "the solvers
agree" has so far been a sentence rather than a computation.  This package turns
it into one: N arm records plus a JSON configuration go in, one digest-stamped
report with a solver x quantity difference matrix and a per-dimension verdict
comes out.

Three dimensions are adjudicated, all of them mechanically:

``scalar``   one number per arm (e.g. a non-dimensional coefficient): symmetric
             relative difference for every pair, mean / std / max deviation, and
             an optional comparison against a reference value injected by config.
``profile``  one curve per arm sampled on an arbitrary abscissa (e.g. a
             distribution along a surface): both curves are resampled on the
             shared interval and compared with a relative L2 distance.
``energy``   each arm's ledger (input term vs dissipation term): does the ledger
             close *per arm*, and do the arms close to the same residual.

Verdict bands are declared in configuration, not in code: ``< 5 %`` consistent,
``5-15 %`` acceptable only if the arms declare the origin of the deviation,
``> 15 %`` inconsistent and an investigation is queued.  A dimension with fewer
than two comparable arms is ``withheld`` -- absence of evidence is never
reported as agreement.

Zero domain semantics: no quantity name, geometry, unit or reference value lives
in this package.  Domain content is injected through ``config/consistency/*.json``
and through the arm records themselves.
"""
from __future__ import annotations

from .gate import (CAPABILITY_KEY, ConfigError, ArmRecordError, ConsistencyGate,
                   load_arms, load_config, validate_arm, validate_config,
                   verify_report, worst, write_report)
from .metrics import (canonical_json, pair_stats, pairwise, profile_l2_rel,
                      rel_diff, sha256_of, sha256_of_file, verdict_of)

__all__ = [
    "CAPABILITY_KEY",
    "ArmRecordError",
    "ConfigError",
    "ConsistencyGate",
    "canonical_json",
    "load_arms",
    "load_config",
    "pair_stats",
    "pairwise",
    "profile_l2_rel",
    "rel_diff",
    "sha256_of",
    "sha256_of_file",
    "validate_arm",
    "validate_config",
    "verdict_of",
    "verify_report",
    "worst",
    "write_report",
]