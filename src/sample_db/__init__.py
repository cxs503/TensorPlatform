"""样本库（samples 表）入库准入层。

背景：样本库是优化库 / 代理模型的直接训练数据源。历史入库路径
`tools/sample_db/backfill.py::backfill_file()` 只做字段抽取 + INSERT，
不读 RunManifest、不复算工件哈希、不看来源 mission 终态 —— 未验证产物
（伪造 verdict="PASS" 的裸 JSON、哈希被篡改的工件、来自 failed mission 的
产物）可以静默入库，污染下游训练集（门缺陷 B1 / m810 台账 D3）。

本包提供三门准入：
    门 1  RunManifest 有效（契约 Draft2020 + validation_status=passed）
    门 2  来源 mission 终态 succeeded
    门 3  关键文件 sha256 复算 + size_bytes 一致
全过才准入；任一不过 → 结构化错误（`AdmissionRejected`）+ JSONL 审计留痕。

入口：
    `src.sample_db.admission.admit_sample`  纯判定（返回 AdmissionDecision）
    `src.sample_db.ingest.ingest_artifact`  判定通过后才调用抽取/INSERT
"""

from src.sample_db.admission import (  # noqa: F401
    AdmissionDecision,
    AdmissionRejected,
    ApiMissionResolver,
    GateResult,
    NullMissionResolver,
    StaticMissionResolver,
    admit_sample,
    sha256_file,
)

__all__ = [
    "AdmissionDecision",
    "AdmissionRejected",
    "ApiMissionResolver",
    "GateResult",
    "NullMissionResolver",
    "StaticMissionResolver",
    "admit_sample",
    "sha256_file",
]