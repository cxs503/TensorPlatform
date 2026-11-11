"""Automatic failure attribution for KPI-SUCCESS-90 (platform-generic).

Public surface
--------------
``load_config`` / ``classify`` / ``evaluate``  — rule engine (`engine.py`)
``run`` / ``success_metrics``                  — measurement (`measure.py`)
"""

from .engine import (  # noqa: F401
    UNKNOWN_KIND,
    UNKNOWN_STAGE,
    Attribution,
    AttributionConfig,
    ConfigError,
    Mission,
    classify,
    emit_backfill_sql,
    evaluate,
    load_config,
    mask_label_tokens,
    mission_from_row,
)
from .measure import (  # noqa: F401
    TERMINAL,
    attribute_failures,
    load_missions,
    run,
    snapshot_digest,
    success_metrics,
)

__all__ = [
    "UNKNOWN_KIND",
    "UNKNOWN_STAGE",
    "Attribution",
    "AttributionConfig",
    "ConfigError",
    "Mission",
    "TERMINAL",
    "attribute_failures",
    "classify",
    "emit_backfill_sql",
    "evaluate",
    "load_config",
    "load_missions",
    "mask_label_tokens",
    "mission_from_row",
    "run",
    "snapshot_digest",
    "success_metrics",
]