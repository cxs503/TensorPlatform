"""证据链批量入账（Evidence-chain bulk ingest）—— 自回归燃料②的喂证据工具。

见 ``tools/evidence_chain/evidence_chain.py`` 的模块文档与 ``README.md``。
"""

from .evidence_chain import (  # noqa: F401
    EXIT_FATAL,
    EXIT_ISOLATED,
    EXIT_OK,
    IngestConfig,
    IngestResult,
    build_manifest,
    ingest,
    verify_artifacts,
)

from .backfill_wiring import (  # noqa: F401
    CHAIN_E1,
    CHAIN_E2,
    CHAIN_E3,
    BackfillReport,
    RunBackfillResult,
    backfill_runs,
    backfill_single_run,
    write_report,
)

__all__ = [
    "EXIT_FATAL",
    "EXIT_ISOLATED",
    "EXIT_OK",
    "IngestConfig",
    "IngestResult",
    "build_manifest",
    "ingest",
    "verify_artifacts",
    "CHAIN_E1",
    "CHAIN_E2",
    "CHAIN_E3",
    "BackfillReport",
    "RunBackfillResult",
    "backfill_runs",
    "backfill_single_run",
    "write_report",
]
