"""盲预测校准账本（blind calibration ledger）—— 零领域语义的可计分预测技能层。

本包解决的事
============
平台已有「预测盲」的**流程**合规（``tools.abm.blind``：先承诺、后观测，事后不可改预测），
但流程合规不等于**科研预测技能**：预测普遍写成点值 + 容差，只能判命中/未命中，无法回答
「说 80% 会命中的那批，实际命中率是多少」——即无法计分、无法诊断偏乐观/偏保守、无法
把「预测能力」本身当成可交付能力来成熟化。

本包补三段：

1. ``metrics``  —— 可靠性曲线（10 箱，含空箱与二项区间）/ Brier 分数 / Murphy 三分分解
   （``BS = REL - RES + UNC``，恒等式残差 ≤1e-10）/ skill score / 自助区间 / 分箱间距。
2. ``ledger``   —— 预测计分账本：每条 ``(p, y)`` 入哈希链（改一个概率即链断），
   并把 ABM 预测盲卡（承诺置信度 + 容差命中）桥接成计分记录。
3. ``episode``  —— 真实的「先承诺概率、后观测结局」程序：按注入配置出题、写承诺哈希、
   再跑确定性检查器写观测，``integrity()`` 复算 commit 早于 observe。
4. ``evidence`` —— run-manifest → 五元证据链（E1–E5）回填：E1 工件内容锚定（与交付提交
   里的 blob 复算 sha256 比对）由回填器机检即授予；E2–E5 必须有生产者工件/外部声明，
   **回填 E1 封顶**；5 槽全部有机器可读状态（``slot_coverage`` = 完整率）。
5. ``harvest``  —— 真实外部预测源接入（平台派发前先验的时间留出背测样本），
   作为外部效度检验：本包的机器必须能在它上面算出**不合格**的诚实结论。

纪律
====
* 零领域语义：物理量、算例、阈值一律经 ``config/`` 与工件注入；包内无域词条。
* 判据阈值全部可注入（``metrics.score(..., max_deviation_max=, brier_max=)``），
  不把某个域的习惯写成全局默认以外的东西。
* 宁可空箱、不可造箱：没有依据的中间概率不声明；空箱如实计数并报告。
"""

from . import metrics  # noqa: F401
from .episode import (  # noqa: F401
    check,
    commit,
    declare_probability,
    forecasts as episode_forecasts,
    group_summary,
    integrity,
    observe,
    plan,
)
from .evidence import (  # noqa: F401
    BACKFILL_CAP,
    EVIDENCE_SLOTS,
    anchor_artifacts,
    audit_table,
    backfill,
    resolve_evidence_chain,
)
from .ledger import (  # noqa: F401
    CalibrationLedger,
    ForecastRecord,
    blind_skips,
    forecasts_from_blind_cards,
    load_forecasts,
    write_forecasts,
)
from .metrics import (  # noqa: F401
    DEFAULT_N_BINS,
    IDENTITY_TOL,
    RANDOM_BASELINE_BRIER,
    binned_brier,
    brier_score,
    brier_skill_score,
    climatology_brier,
    curve_with_intervals,
    groupwise,
    log_loss,
    murphy_decomposition,
    reliability_curve,
    score,
    wilson_interval,
)
from .harvest import harvest  # noqa: F401

__all__ = [
    "metrics",
    "DEFAULT_N_BINS",
    "IDENTITY_TOL",
    "RANDOM_BASELINE_BRIER",
    "reliability_curve",
    "curve_with_intervals",
    "brier_score",
    "binned_brier",
    "climatology_brier",
    "brier_skill_score",
    "log_loss",
    "murphy_decomposition",
    "wilson_interval",
    "groupwise",
    "score",
    "CalibrationLedger",
    "ForecastRecord",
    "load_forecasts",
    "write_forecasts",
    "forecasts_from_blind_cards",
    "blind_skips",
    "plan",
    "commit",
    "observe",
    "check",
    "integrity",
    "episode_forecasts",
    "group_summary",
    "declare_probability",
    "EVIDENCE_SLOTS",
    "BACKFILL_CAP",
    "anchor_artifacts",
    "resolve_evidence_chain",
    "backfill",
    "audit_table",
    "harvest",
]