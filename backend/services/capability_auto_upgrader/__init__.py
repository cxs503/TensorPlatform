"""Evidence-driven automatic upgrade/downgrade of capability evidence levels.

Public surface::

    from services.capability_auto_upgrader import (
        CapabilityAutoUpgrader, UpgradePolicy, EvidenceRun, TestEvidence,
        BenchmarkEvidence, LEVELS,
    )

    up = CapabilityAutoUpgrader("runs/auto_upgrader")
    up.ingest_audit_snapshot(api_payload)                    # listen to the audit API
    up.record_run(EvidenceRun(...))                          # one measured run
    up.levels(), up.verify(), up.status()

Documentation and the acceptance run live in
``docs/p362w2_n01_capability_auto_upgrader.md`` and
``runs/p362w2_n01_capability_auto_upgrader/``.
"""
from .model import (  # noqa: F401
    LEVELS,
    LEVEL_INDEX,
    TARGET_LEVEL,
    UPGRADABLE_LEVELS,
    BenchmarkEvidence,
    EvidenceError,
    EvidenceRun,
    TestEvidence,
    canonical_json,
    dedupe_runs,
    sha256_file,
    sha256_obj,
)
from .policy import UpgradePolicy  # noqa: F401
from .streak import (  # noqa: F401
    ACTION_DEMOTE,
    ACTION_HOLD,
    ACTION_PROMOTE,
    Decision,
    StreakAnalysis,
    analyse,
    decide,
    judge_run,
)
from .ledger import FeatureLedger  # noqa: F401
from .store import RunStore  # noqa: F401
from .watcher import AuditSnapshot, AuditWatcher  # noqa: F401
from .upgrader import CapabilityAutoUpgrader  # noqa: F401

__all__ = [
    "LEVELS",
    "LEVEL_INDEX",
    "TARGET_LEVEL",
    "UPGRADABLE_LEVELS",
    "BenchmarkEvidence",
    "EvidenceError",
    "EvidenceRun",
    "TestEvidence",
    "canonical_json",
    "dedupe_runs",
    "sha256_file",
    "sha256_obj",
    "UpgradePolicy",
    "Decision",
    "StreakAnalysis",
    "analyse",
    "decide",
    "judge_run",
    "ACTION_PROMOTE",
    "ACTION_DEMOTE",
    "ACTION_HOLD",
    "FeatureLedger",
    "RunStore",
    "AuditSnapshot",
    "AuditWatcher",
    "CapabilityAutoUpgrader",
]