"""升级验收判据（**动手前预声明**；判据文本 + sha256 入账，禁止看完结果再调阈值）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple

CRITERIA_VERSION = "capability-maturity-criteria/1.0"


@dataclass(frozen=True)
class AcceptanceCriteria:
    """升级到 REAL 的机器判据。

    tolerance_pct
        逐例相对误差上限（%）。15.0 来自平台精度标准「对标参考值误差 >15% 不可接受」。
    min_cases
        基准用例数下界（少于此不能声称能力）。
    required_chain
        证据链必须齐的工件种类：测试报告 / 基准数据 / 误差分析。
    require_independent_reference
        参考值必须由**不同提供方**给出（实现与基线同源的"自己证自己"不算数）。
    require_baseline_improvement
        升级后误差必须严格优于原实现（否则谈不上"升级"），且原实现须超阈。
    zero_reference_floor
        参考值的零点尺度：|ref| ≤ floor 时相对误差无定义（分母为 0），
        改用 floor 作分母的**声明尺度**误差。该尺度在判据里预声明，不随后验调整。

    """

    tolerance_pct: float = 15.0
    min_cases: int = 3
    required_chain: Tuple[str, ...] = ("test_report", "benchmark_data", "error_analysis")
    require_independent_reference: bool = True
    require_baseline_improvement: bool = True
    zero_reference_floor: float = 1e-3
    version: str = CRITERIA_VERSION

    def canonical(self) -> Dict[str, Any]:
        d = asdict(self)
        d["required_chain"] = list(self.required_chain)
        return d

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        d = self.canonical()
        d["sha256"] = self.sha256()
        return d


#: 出厂预声明判据（P250-N02 动手前固定；本包运行期不得改写）
PREDECLARED = AcceptanceCriteria()


def predeclaration(criteria: AcceptanceCriteria = PREDECLARED) -> Dict[str, Any]:
    """预声明工件：判据 + 指纹 + 生成时点（先落盘、后跑基准）。"""
    from datetime import datetime, timezone

    return {
        "kind": "capability_maturity_criteria_predeclaration",
        "declared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "criteria": criteria.as_dict(),
        "note": ("判据在观测任何基准结果之前落盘；运行期任何改动都会改变 sha256，"
                 "报告据此可机器判定「是否看完结果再调阈值」。"),
    }
