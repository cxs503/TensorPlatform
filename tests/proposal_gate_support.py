"""提案门测试夹具：合成域名（foo/bar/widget）+ 合成配置，零领域语义。

夹具纪律（判例 #608 域渗漏）：
* 本文件只使用合成域名 ``foo`` / ``bar`` / ``widget``，不出现任何学科域词；
* 仓库白名单用合成三仓，不依赖真仓库清单；
* 配置从 ``config/proposal_gate.json`` 派生后**显式覆盖**（阈值仍是配置里的那套，
  只有白名单/域名换成合成的），因此被验的是同一份判定代码而不是另一套逻辑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - 运行面便利
    sys.path.insert(0, str(REPO_ROOT))

from tools.proposal_gate.config import GateConfig  # noqa: E402

SYNTH_REPOS = ("foo", "bar", "widget")


def load_real_config() -> GateConfig:
    return GateConfig.load(REPO_ROOT / "config" / "proposal_gate.json")


def synth_config(**overrides) -> GateConfig:
    """真配置 + 合成白名单 + 可选覆盖（阈值覆盖只用于边界实验，主用例用真阈值）。"""
    base = load_real_config()
    data = json.loads(json.dumps(base.data))
    data["repo_whitelist"] = list(SYNTH_REPOS)
    # v1 compat 层的兜底仓也要落在合成白名单内（否则 v1 夹具必然 quarantine）
    data.setdefault("compat", {}).setdefault("v1_defaults", {})["repo"] = "bar"
    for key, value in overrides.items():
        cur = data
        parts = key.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
    return GateConfig.from_dict(data, path=base.path)


def v2_payload(
    *,
    title: str = "foo 通道三道机器门",
    repo: str = "foo",
    objective: str = "为 foo 通道建立产物契约、去重器与覆盖率账本三道机器门",
    criteria: tuple[str, ...] | None = None,
    deps: tuple[str, ...] = ("门-契约", "门-去重"),
    is_maturity: object = True,
    mission_id: int = 7001,
    plan_id: int | None = 11,
    schema_version: str = "2.0",
    **extra: object,
) -> dict:
    if criteria is None:
        criteria = (
            "重复率 < 0.15",
            "播种覆盖率 ≥ 0.80",
            "误判率 ≤ 0.05",
        )
    payload = {
        "schema_version": schema_version,
        "mission_id": mission_id,
        "plan_id": plan_id,
        "title": title,
        "repo": repo,
        "goal": {"objective": objective, "criteria": list(criteria)},
        "dependencies_hint": list(deps),
        "is_maturity": is_maturity,
    }
    payload.update(extra)
    return payload


def v1_payload(
    *,
    proposal_id: str = "20260101-widget-gate",
    title: str = "widget 链路覆盖率账本",
    hypothesis: str = "widget 链路缺少面向播种的覆盖率账本，无法判定供给是否到位",
    criteria: tuple[str, ...] | None = None,
    artifacts: tuple[str, ...] = ("ledger.json", "report.md"),
    **extra: object,
) -> dict:
    if criteria is None:
        criteria = (
            "四态计数可由 SQL 复算，偏差 = 0",
            "重复检出率 ≥ 0.90",
            "误判率 ≤ 0.05",
        )
    payload = {
        "proposal_id": proposal_id,
        "title": title,
        "hypothesis": hypothesis,
        "falsification_criteria": list(criteria),
        "expected_artifacts": list(artifacts),
        "gap_rule": "R1",
        "budget_hint": {"hours": 4},
        "triggered_by": {"rule_id": "R1", "detail": "bar 链路缺账本"},
        "generated_at": "2026-01-01T00:00:00Z",
        "generator_version": "test",
    }
    payload.update(extra)
    return payload


# 同义改写族：只做「措辞/语序/从句增删」的改写，不改变主题（用于反作弊 (b)）
PARAPHRASE_VARIANTS: tuple[str, ...] = (
    "建立 foo 通道的三道机器门：产物 schema 契约、跨提案去重器与播种覆盖率账本",
    "foo 通道三道机器门：产物契约（schema）、去重器、覆盖率账本，一次建齐",
    "为 foo 通道建三道机器门——产物契约、去重器、以及播种覆盖率账本",
    "foo 通道需要三道机器门：一是产物契约，二是去重器，三是覆盖率账本",
)

# 不同主题的合法提案（precision 门：措辞完全不同、主题不同，不得误判）
DISTINCT_TOPICS: tuple[dict, ...] = (
    {
        "title": "bar 报表延迟归因",
        "objective": "把 bar 报表的端到端延迟拆到各阶段，定位主要阻滞点",
        "criteria": ("P95 延迟 ≤ 2.5 秒", "归因覆盖率 ≥ 0.90", "误归因率 ≤ 0.10"),
    },
    {
        "title": "widget 缓存命中率提升",
        "objective": "对 widget 查询路径引入分层缓存并度量收益",
        "criteria": ("命中率 ≥ 0.75", "回源次数 ≤ 1000/min", "内存增量 ≤ 8%"),
    },
    {
        "title": "foo 权限审计留痕",
        "objective": "为 foo 的写操作补全审计留痕并验证不可篡改",
        "criteria": ("留痕完整率 = 1.00", "篡改检出率 ≥ 0.99", "额外延迟 ≤ 5ms"),
    },
)


def distinct_payload(idx: int) -> dict:
    spec = DISTINCT_TOPICS[idx % len(DISTINCT_TOPICS)]
    return v2_payload(
        title=spec["title"],
        objective=spec["objective"],
        criteria=spec["criteria"],
        deps=(f"依赖-{idx}",),
    )