"""lint —— 派单契约缺陷体检（口径不符/无判据/输出类别缺失）。

用途：契约覆盖率是就绪门的硬上限（无契约 = 只能降级放行），所以体检的第一个指标
就是「有多少派单自带机器可解析的 input_contract」。其余缺陷码：

===================  ====================================================
码                   含义
===================  ====================================================
NO_INPUT_CONTRACT    任务书没有任何机器可解析的上游依赖 → 门只能降级放行
NO_OUTPUT_CONTRACT   没有任何机器可解析的输出工件声明 → 交付无法被机器验收
NO_CRITERIA          没有任何判据标记（比较符/pytest/assert）→ 验收口径缺失
ORPHAN_INPUT_ARTIFACT 声明的输入工件在派单时刻无任何上游产出
SELF_REFERENCE       依赖里出现自己的任务号（口径错误）
===================  ====================================================

全部判定只看平台级事实（任务号、路径前缀、判据标记、注册表时间序），零领域语义。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contract_schema import load_config

LINT_SCHEMA = "dispatch-contract/lint/v1"


def lint_record(record: Mapping[str, Any], cfg: Mapping[str, Any] | None = None) -> list[str]:
    """单条语料记录的缺陷码列表。"""
    cfg = cfg or load_config()
    codes: list[str] = []
    declared = record.get("declared") or {}
    dispatch = record.get("dispatch") or {}
    contract = dispatch.get("contract")
    if contract is None:
        codes.append("NO_INPUT_CONTRACT")
    if not declared.get("output_paths"):
        codes.append("NO_OUTPUT_CONTRACT")
    if int(declared.get("n_criteria") or 0) == 0:
        codes.append("NO_CRITERIA")
    mid = str(record.get("mission_id"))
    if mid in [str(r) for r in declared.get("refs", [])]:
        codes.append("SELF_REFERENCE")
    upstream = dispatch.get("upstream") or {}
    for req in (contract or {}).get("input_contract", []):
        if req.get("kind") == "artifact":
            entry = upstream.get(str(req.get("ref"))) or {}
            if not entry.get("found"):
                codes.append("ORPHAN_INPUT_ARTIFACT")
                break
    return codes


def lint_corpus(records: Iterable[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None) -> dict:
    """全语料体检，返回缺陷计数与逐条明细摘要。"""
    cfg = cfg or load_config()
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    n = 0
    for r in records:
        n += 1
        for c in lint_record(r, cfg):
            counts[c] = counts.get(c, 0) + 1
            examples.setdefault(c, [])
            if len(examples[c]) < 5:
                examples[c].append(str(r.get("mission_id")))
    return {
        "schema": LINT_SCHEMA,
        "n_records": n,
        "defect_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "defect_rates": {k: round(v / max(1, n), 6) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])},
        "examples": examples,
        "all_clean": len(counts) == 0,
    }