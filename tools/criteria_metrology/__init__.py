"""判据度量学（criterion metrology）：四元组 / 退化分类 / 自适应判据族 / 回放重判。

平台共性机制，零领域语义：所有领域数值一律经工件（报告表格、注册表、案件 JSON）
注入；代码里只有度量学术语（被测量、基准、容差、口径）。
"""

from .model import (  # noqa: F401
    Criterion,
    DegeneracyClass,
    Interval,
    Metric,
    Verdict,
    normalize_criterion,
    relative_error,
)
from .degeneracy import classify_degeneracy, degeneracy_report  # noqa: F401
from .adaptive import AdaptiveResult, evaluate_family, family_for  # noqa: F401
from .parse import parse_markdown_criteria, parse_record  # noqa: F401
from .rejudge import rejudge_corpus, replay_summary  # noqa: F401
from .retrieval import (  # noqa: F401
    Receipt,
    build_receipt,
    negative_space_map,
    replay_receipt,
)
from .triad import CapabilityEntry, Delivery, Evidence, triad_validate  # noqa: F401

__all__ = [
    "Criterion",
    "DegeneracyClass",
    "Interval",
    "Metric",
    "Verdict",
    "normalize_criterion",
    "relative_error",
    "classify_degeneracy",
    "degeneracy_report",
    "AdaptiveResult",
    "evaluate_family",
    "family_for",
    "parse_markdown_criteria",
    "parse_record",
    "rejudge_corpus",
    "replay_summary",
    "Receipt",
    "build_receipt",
    "replay_receipt",
    "negative_space_map",
    "CapabilityEntry",
    "Delivery",
    "Evidence",
    "triad_validate",
]