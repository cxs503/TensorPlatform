"""欠账（FAIL/WITHHELD debt）结构化 schema —— P44-N09 的唯一数据契约。

本模块只定义**数据形状与不变量**，不含任何提取逻辑，便于离线评测和下游消费者
（提案草案生成器、审计脚本）共用同一份契约。

欠账行字段（任务书原文）
------------------------
``{repo, case, 量, 实测, 基准, 误差, 门槛, 是否<3%, 根因类, 再派判据, 依赖}``

字段落地为：``repo / case / metric / measured / reference / error_pct / threshold_pct
/ lt_3pct / rootcause / requeue_criterion / depends_on / source``。

根因类取值受 CHECK 约束（四选一）
--------------------------------
* ``reference_limited``     —— 参考值受限（二手引用、不确定带宽、实验点稀疏）
* ``discretization_resolution`` —— 离散/分辨率（网格 GCI、阶数、格间差、面元数）
* ``implementation_defect`` —— 实现缺陷（代码/契约/回归/未实现分支）
* ``reference_missing``     —— 参考缺（真值未知、无实验/无解析解可对标）

设计纪律
--------
1. **不可变**：``DebtRow`` 为 frozen dataclass，提取器只能构造不能就地改，避免评测时
   一侧被静默修改导致「召回率自我实现」。
2. **指纹稳定**：``fingerprint`` 只由 (repo, case, metric_normalized, error_bucket) 决定，
   与产线/评测的先后顺序无关，因此同一欠账在多次提取中指纹相同——去重的机器判据。
3. **可审计**：每条欠账自带 ``source``（文件 + 行号 + 报告/仓归属），任何结论可回溯到
   原文一行；``evidence`` 保留触发判定的原文片段（cues）。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

ROOT_CAUSES: tuple[str, ...] = (
    "reference_limited",
    "discretization_resolution",
    "implementation_defect",
    "reference_missing",
)
ROOT_CAUSE_ZH: dict[str, str] = {
    "reference_limited": "参考受限",
    "discretization_resolution": "离散/分辨率",
    "implementation_defect": "实现缺陷",
    "reference_missing": "参考缺",
}
VERDICTS: tuple[str, ...] = ("FAIL", "WITHHELD")
DEFAULT_THRESHOLD_PCT = 3.0


class SchemaError(ValueError):
    """欠账行违反 schema 不变量。"""


def normalize_metric(text: str) -> str:
    """量名归一：全角→半角、去 markdown 强调/反引号、压空白、小写化拉丁字母。

    注意：**不做**中文分词、**不做**同义词替换（那是根因分类器的职责），只做
    「同一字符串的不同书写」归一，保证指纹对格式稳定。
    """
    s = unicodedata.normalize("NFKC", text or "")
    for token in ("**", "__", "`", "*", "_", "~~"):
        s = s.replace(token, "")
    s = s.replace("\u3000", " ")
    s = " ".join(s.split())
    return s.casefold()


def error_bucket(error_pct: float | None, rel: float = 0.1) -> str:
    """误差分桶（对数分桶，rel=10%）：用于指纹里容忍微小读数抖动。"""
    if error_pct is None:
        return "-"
    v = abs(float(error_pct))
    if v < 1e-9:
        return "0"
    import math

    exp = math.floor(math.log10(v) / math.log10(1.0 + rel))
    return f"e{exp}"


@dataclass(frozen=True)
class Source:
    """证据出处。``kind`` ∈ {report, pending_verdict}。"""

    kind: str
    file: str
    line: int = 0
    report_id: str = ""
    mission_id: str = ""
    repo: str = ""
    benchmark: str = ""
    node: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "report_id": self.report_id,
            "mission_id": self.mission_id,
            "repo": self.repo,
            "benchmark": self.benchmark,
            "node": self.node,
        }

    @property
    def locator(self) -> str:
        """行级定位符 ``file:line``——评测里的行级匹配键。"""
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class DebtRow:
    repo: str
    case: str
    metric: str
    measured: float | None
    reference: float | None
    error_pct: float | None
    threshold_pct: float | None
    lt_3pct: bool | None
    rootcause: str
    requeue_criterion: str
    depends_on: tuple[str, ...]
    verdict: str
    source: Source
    evidence: tuple[str, ...] = field(default=())
    raw: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ 校验
    def validate(self) -> None:
        if self.rootcause not in ROOT_CAUSES:
            raise SchemaError(f"rootcause {self.rootcause!r} not in {ROOT_CAUSES}")
        if self.verdict not in VERDICTS:
            raise SchemaError(f"verdict {self.verdict!r} not in {VERDICTS}")
        if not self.metric.strip():
            raise SchemaError("metric must be non-empty")
        if not self.repo.strip():
            raise SchemaError("repo must be non-empty")
        if self.threshold_pct is not None and self.threshold_pct <= 0:
            raise SchemaError(f"threshold_pct must be > 0, got {self.threshold_pct}")
        if self.lt_3pct is not None and self.error_pct is None:
            raise SchemaError("lt_3pct requires error_pct")
        if not self.requeue_criterion.strip():
            raise SchemaError("requeue_criterion must be non-empty")

    # -------------------------------------------------------------- 派生属性
    @property
    def over_threshold(self) -> bool | None:
        if self.error_pct is None or self.threshold_pct is None:
            return None
        return abs(self.error_pct) > self.threshold_pct

    @property
    def fingerprint(self) -> str:
        key = "|".join(
            (
                self.repo.casefold(),
                normalize_metric(self.case),
                normalize_metric(self.metric),
                error_bucket(self.error_pct),
            )
        )
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def row_id(self) -> str:
        """行级唯一 id：``<file>:<line>``（评测匹配键，不含内容）。"""
        return self.source.locator

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "fingerprint": self.fingerprint,
            "repo": self.repo,
            "case": self.case,
            "metric": self.metric,
            "measured": self.measured,
            "reference": self.reference,
            "error_pct": self.error_pct,
            "threshold_pct": self.threshold_pct,
            "lt_3pct": self.lt_3pct,
            "over_threshold": self.over_threshold,
            "verdict": self.verdict,
            "rootcause": self.rootcause,
            "rootcause_zh": ROOT_CAUSE_ZH[self.rootcause],
            "requeue_criterion": self.requeue_criterion,
            "depends_on": list(self.depends_on),
            "source": self.source.to_dict(),
            "evidence": list(self.evidence),
            "raw": self.raw,
        }


REQUIRED_KEYS = (
    "row_id",
    "fingerprint",
    "repo",
    "case",
    "metric",
    "measured",
    "reference",
    "error_pct",
    "threshold_pct",
    "lt_3pct",
    "verdict",
    "rootcause",
    "requeue_criterion",
    "depends_on",
    "source",
)


def validate_jsonl_row(obj: dict[str, Any]) -> None:
    """对落盘后的 JSON 行做同样的不变量检查（防止序列化层偷偷改字段）。"""
    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        raise SchemaError(f"missing keys: {missing}")
    if obj["rootcause"] not in ROOT_CAUSES:
        raise SchemaError(f"bad rootcause {obj['rootcause']!r}")
    if obj["verdict"] not in VERDICTS:
        raise SchemaError(f"bad verdict {obj['verdict']!r}")
    if not str(obj["metric"]).strip():
        raise SchemaError("empty metric")


def dedupe_rows(rows: Iterable[DebtRow]) -> list[DebtRow]:
    """按指纹去重（保留行号最大者=最新报告里的那次记录），顺序稳定。"""
    best: dict[str, DebtRow] = {}
    for row in rows:
        prev = best.get(row.fingerprint)
        if prev is None or (row.source.file, row.source.line) > (
            prev.source.file,
            prev.source.line,
        ):
            best[row.fingerprint] = row
    return [best[k] for k in sorted(best)]


def dumps_row(row: DebtRow) -> str:
    return json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True)