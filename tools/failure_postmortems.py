#!/usr/bin/env python3
"""Failure postmortems: 表 + 自动收割 + 挑战/定稿 + PLAYBOOK 注入 + 规则检索.

本模块只做**平台机制**（零领域语义）：从 ``tensor_dev_missions`` 的 failed 终态
证据里机械地抽出一条失败事后复盘（postmortem），经**独立挑战**后才定稿，
定稿规则再以 append-only 方式注入 bootstrap PLAYBOOK 的失败判例节，并登记
“哪条规则被后续任务引用”。

七环（failure_stage）——把攻关链切成可裁决的七段：

======  ==========  ====================================================
代号    中文        该环失败的典型证据
======  ==========  ====================================================
intake  目标解析    目标不可判定 / 判据缺失
plan    方案假设    假设本身不成立（理论/量纲/适用域）
setup   环境准备    仓库/worktree/依赖/远端端点不可用
implement 实现落地   工具循环空转 / 零改动 / 预算或墙钟耗尽
verify  验证测试    测试红 / 与基准或解析解不符
deliver 交付留痕    commit/报告/PR/证据门缺失
accept  验收裁决    判据未满足、被独立审查裁 FAIL
======  ==========  ====================================================

error_class 五类：theory_error / implementation_error /
experiment_design_error / resource_limit / endpoint_disease。

设计纪律
--------
1. **不造假**：所有文本字段都从 mission 证据机械派生（reason / headline /
   stop_condition / tool_calls / changed_files / evidence_ignored），缺证据的
   mission 一律 WITHHELD（不产出草稿），绝不用模板空转凑数。
2. **防敷衍**：草稿必须过 ``challenge_draft``（结构闸门）与
   ``independent_review``（独立复算，与起草器不共享分类结论）双闸；两者
   任一不通过即停在 draft/disputed，不得定稿。
3. **append-only 注入**：复用 ``tools.precedent_library.library.inject_playbook``
   的锚点/幂等/字符预算契约，绝不改写既有 PLAYBOOK 字节。
4. **引用可审计**：每条规则的被引用都落 ``postmortem_rule_references``，
   带 reference_kind 与证据片段；只数真实记录，不估算。

CLI
---
    python3 -m tools.failure_postmortems --help
    python3 -m tools.failure_postmortems harvest --days 7 --limit 40 --apply --confirm
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.precedent_library.library import (
    PLAYBOOK_ANCHOR,
    PrecedentError,
    inject_playbook,
)

# ─────────────────────────────── 常量 ───────────────────────────────

#: 七环枚举（顺序即攻关链顺序；CHECK 约束与 API 校验共用这一处定义）。
STAGES: tuple[str, ...] = (
    "intake",
    "plan",
    "setup",
    "implement",
    "verify",
    "deliver",
    "accept",
)

STAGE_LABELS: Mapping[str, str] = {
    "intake": "目标解析",
    "plan": "方案假设",
    "setup": "环境准备",
    "implement": "实现落地",
    "verify": "验证测试",
    "deliver": "交付留痕",
    "accept": "验收裁决",
}

#: 平台既有 fail_stage 词表（tool_loop/build/test/acceptance/agent_lost）
#: 与运行器终局词（llm_error/no_progress/budget_exhausted/wall_exhausted/...）
#: 到七环的映射。映射是**数据**，改词表不动逻辑。
LEGACY_STAGE_MAP: Mapping[str, str] = {
    "tool_loop": "implement",
    "build": "setup",
    "test": "verify",
    "acceptance": "accept",
    "agent_lost": "implement",
    "llm_error": "implement",
    "no_progress": "implement",
    "budget_exhausted": "implement",
    "wall_exhausted": "implement",
    "worker-crash": "setup",
    "worker_crash": "setup",
    "honest_fail": "accept",
    "endpoint": "setup",
    "setup": "setup",
    "intake": "intake",
    "plan": "plan",
    "implement": "implement",
    "verify": "verify",
    "deliver": "deliver",
    "accept": "accept",
}

ERROR_CLASSES: tuple[str, ...] = (
    "theory_error",
    "implementation_error",
    "experiment_design_error",
    "resource_limit",
    "endpoint_disease",
)

STATUSES: tuple[str, ...] = ("draft", "disputed", "confirmed", "rejected")

#: 可定稿角色（挑战角色或协调者）——起草者不得自审。
CONFIRM_ROLES: tuple[str, ...] = ("challenger", "coordinator")

#: PLAYBOOK 单条字符预算，与 precedent_library 同一纪律（CJK 计 1）。
MAX_LINE_CHARS = 120

PLAYBOOK_SECTION_ANCHOR = "## 失败判例（postmortem 自动注入，append-only）"

_TABLE_PM = "failure_postmortems"
_TABLE_RULES = "postmortem_rules"
_TABLE_REFS = "postmortem_rule_references"
_TABLE_HYP = "hypothesis_registry"

_RULE_CODE_RE = re.compile(r"PM-\d+-R\d+")

#: 关掉“敷衍式规则”的黑名单：这些规则没有任何可执行信息。
GENERIC_RULE_BLACKLIST: frozenset[str] = frozenset(
    {
        "注意测试",
        "仔细检查",
        "多加小心",
        "下次注意",
        "提高质量",
        "认真一点",
        "做好验证",
    }
)


# ─────────────────────────────── DDL ───────────────────────────────

def ddl_for(dialect: str) -> str:
    """Return the DDL tailored to ``dialect`` ('sqlite' or 'postgresql').

    PostgreSQL 真表用 ``text[]`` 存两条路径字段（accounts 里另有迁移
    ``106_failure_postmortems.sql`` 做同样的事）；SQLite（测试）退化为 JSON 文本。
    两条 DDL 的列名/检查约束逐字一致，只有容器类型不同。
    """
    ddl = DDL
    if dialect.startswith("postgres"):
        ddl = ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        ddl = ddl.replace(
            "invalidated_paths  TEXT NOT NULL DEFAULT '[]'",
            "invalidated_paths  TEXT[] NOT NULL DEFAULT '{}'",
        )
        ddl = ddl.replace(
            "avoidance_rules    TEXT NOT NULL DEFAULT '[]'",
            "avoidance_rules    TEXT[] NOT NULL DEFAULT '{}'",
        )
    return ddl


DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE_HYP} (
    hypothesis_id   TEXT PRIMARY KEY,
    mission_id      BIGINT,
    statement       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'mission_goal',
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL,
    CHECK (status IN ('open','refuted','supported','inconclusive'))
);

CREATE TABLE IF NOT EXISTS {_TABLE_PM} (
    postmortem_id      TEXT PRIMARY KEY,
    mission_id         BIGINT NOT NULL UNIQUE,
    organization_id    BIGINT,
    repo               TEXT,
    hypothesis_id      TEXT,
    initial_hypothesis TEXT NOT NULL,
    failed_step        TEXT NOT NULL,
    failure_stage      TEXT NOT NULL,
    legacy_stage       TEXT,
    error_class        TEXT NOT NULL,
    invalidated_paths  TEXT NOT NULL DEFAULT '[]',
    avoidance_rules    TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'draft',
    harvested_by       TEXT NOT NULL,
    challenge          TEXT NOT NULL DEFAULT '{{}}',
    confirmed_by       TEXT,
    confirm_role       TEXT,
    confirmed_at       TEXT,
    rejected_reason    TEXT,
    evidence           TEXT NOT NULL DEFAULT '{{}}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    CHECK (failure_stage IN ('intake','plan','setup','implement','verify','deliver','accept')),
    CHECK (error_class IN ('theory_error','implementation_error','experiment_design_error',
                           'resource_limit','endpoint_disease')),
    CHECK (status IN ('draft','disputed','confirmed','rejected')),
    CHECK (confirmed_by IS NULL OR confirm_role IS NOT NULL),
    -- 起草者不得自审（DB 层硬拒，不靠调用方自觉）
    CHECK (confirmed_by IS NULL OR confirmed_by <> harvested_by)
);

CREATE INDEX IF NOT EXISTS idx_pm_stage ON {_TABLE_PM} (failure_stage, status);
CREATE INDEX IF NOT EXISTS idx_pm_class ON {_TABLE_PM} (error_class, status);
CREATE INDEX IF NOT EXISTS idx_pm_mission ON {_TABLE_PM} (mission_id);

CREATE TABLE IF NOT EXISTS {_TABLE_RULES} (
    rule_id        TEXT PRIMARY KEY,
    postmortem_id  TEXT NOT NULL,
    mission_id     BIGINT,
    rule_index     INTEGER NOT NULL,
    rule_text      TEXT NOT NULL,
    failure_stage  TEXT NOT NULL,
    error_class    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (postmortem_id, rule_index)
);

CREATE INDEX IF NOT EXISTS idx_rules_stage ON {_TABLE_RULES} (failure_stage, error_class);

CREATE TABLE IF NOT EXISTS {_TABLE_REFS} (
    reference_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id               TEXT NOT NULL,
    referencing_mission_id BIGINT,
    reference_kind        TEXT NOT NULL,
    evidence              TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL,
    UNIQUE (rule_id, referencing_mission_id, reference_kind),
    CHECK (reference_kind IN ('later_postmortem_consult','mission_text_citation','claim_injection'))
);
"""


# ─────────────────────────── 证据抽取 ───────────────────────────

@dataclass(frozen=True)
class MissionView:
    """mission 行到纯数据的投影（API/测试/CLI 共用，不依赖 ORM）。"""

    mission_id: int
    goal: str = ""
    repo: str | None = None
    status: str = "failed"
    organization_id: int | None = None
    fail_stage: str | None = None
    finished_at: str | None = None
    claimed_by: str | None = None
    model: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MissionView":
        result = row.get("result") or {}
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = {"_raw": result}
        return cls(
            mission_id=int(row["id"]),
            goal=str(row.get("goal") or ""),
            repo=row.get("repo"),
            status=str(row.get("status") or ""),
            organization_id=row.get("organization_id"),
            fail_stage=row.get("fail_stage"),
            finished_at=_iso(row.get("finished_at")),
            claimed_by=row.get("claimed_by"),
            model=row.get("model"),
            result=result if isinstance(result, Mapping) else {"_raw": str(result)},
        )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(result: Mapping[str, Any]) -> Mapping[str, Any]:
    ev = result.get("evidence")
    return ev if isinstance(ev, Mapping) else {}


def raw_stage_tokens(m: MissionView) -> list[str]:
    """所有出现过的 stage/终局词（按可信度排序），用于七环映射。"""
    tokens: list[str] = []
    checks = _evidence(m.result).get("checks") or {}
    if isinstance(checks, Mapping):
        # 顺序即优先级：显式 fail_stage > reason 的"终局"词 > 交付门标签（gate）
        stage_val = checks.get("fail_stage")
        if isinstance(stage_val, str) and stage_val:
            tokens.append(stage_val)
        reason = checks.get("reason")
        if isinstance(reason, str):
            for tok in re.findall(r"终局:\s*([a-z_]+)", reason):
                tokens.append(tok)
            if reason.startswith("worktree 失败"):
                tokens.append("worktree_failure")
            if "仓库不存在或不是 git" in reason:
                tokens.append("repo_missing")
            if "工作树无任何改动" in reason:
                tokens.append("no_worktree_change")
        gate_val = checks.get("gate")
        if isinstance(gate_val, str) and gate_val:
            tokens.append(gate_val)
    for key in ("fail_stage", "stop_condition"):
        val = m.result.get(key)
        if isinstance(val, str) and val:
            tokens.append(val)
    if m.fail_stage:
        tokens.append(m.fail_stage)
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


_REASON_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "repo_missing": ("仓库不存在", "not a git", "不是 git"),
    "network": (
        "unable to access",
        "could not fetch",
        "Failed to connect",
        "RPC failed",
        "promisor remote",
        "timed out",
        "timeout",
    ),
    "worktree_broken": ("worktree 失败", "not a valid object name", "could not lock config file"),
    "no_change": ("工作树无任何改动", "不能报成功", "工作树为空"),
}


def _one_line(text: str, limit: int = 400) -> str:
    """把多行原始报错压成一行（postmortem 字段要可读、可 diff、可入库）。"""
    return " ".join(text.split())[:limit]


def failure_reason(m: MissionView) -> str:
    """最可判的失败说明（reason > headline > stop_condition > fail_stage 列）。"""
    checks = _evidence(m.result).get("checks") or {}
    if isinstance(checks, Mapping):
        reason = checks.get("reason")
        if isinstance(reason, str) and reason.strip():
            return _one_line(reason)
    headline = m.result.get("headline")
    if isinstance(headline, str) and headline.strip():
        return _one_line(headline)
    if m.result.get("stop_condition"):
        return str(m.result["stop_condition"])
    return (m.fail_stage or "").strip()


def failure_stage(m: MissionView) -> str:
    """七环归位：先看证据关键词，再看 legacy 词表映射；无信号即 'unknown'。"""
    reason = failure_reason(m)
    tokens = raw_stage_tokens(m)
    if any(k in reason for k in _REASON_KEYWORDS["repo_missing"]):
        return "setup"
    if any(k in reason for k in _REASON_KEYWORDS["worktree_broken"]):
        return "setup"
    if any(k in reason for k in _REASON_KEYWORDS["network"]):
        return "setup"
    if any(k in reason for k in _REASON_KEYWORDS["no_change"]):
        return "implement"
    if "worktree_failure" in tokens or "repo_missing" in tokens:
        return "setup"
    if "no_worktree_change" in tokens:
        return "implement"
    for tok in tokens:
        stage = LEGACY_STAGE_MAP.get(tok) or LEGACY_STAGE_MAP.get(tok.lower())
        if stage:
            return stage
    return "unknown"


_THEORY_SIGNALS = (
    "假设不成立",
    "理论错误",
    "物理不自洽",
    "量纲不一致",
    "量纲不统一",
    "与解析解不符",
    "与理论不符",
    "推导错误",
)
_DESIGN_SIGNALS = (
    "判据无法验证",
    "无法机器裁",
    "不可判定",
    "基准缺失",
    "没有基准",
    "无法对标",
    "无法复现",
    "口径不一致",
    "缺参考值",
)


def _self_report_text(m: MissionView) -> str:
    """起草者自述（诚实 FAIL 文本）——只从这里读 theory/design 信号，避免 goal 误命中。"""
    parts: list[str] = []
    for key in ("declared_summary", "summary_text", "notes", "honest_fail"):
        val = m.result.get(key)
        if isinstance(val, str):
            parts.append(val)
    summary = _evidence(m.result).get("summary")
    if isinstance(summary, Mapping):
        for key in ("declared_summary", "notes"):
            val = summary.get(key)
            if isinstance(val, str):
                parts.append(val)
    return "\n".join(parts)


def error_class(m: MissionView, stage: str | None = None) -> str:
    """五类归因：端点病 > 资源上限 > 理论 > 实验设计 > 实现（默认）。"""
    stage = stage or failure_stage(m)
    reason = failure_reason(m)
    tokens = [t.lower() for t in raw_stage_tokens(m)]
    self_text = _self_report_text(m)
    joined = (reason + " " + " ".join(tokens)).lower()

    if any(k in reason for k in _REASON_KEYWORDS["repo_missing"] + _REASON_KEYWORDS["network"]):
        return "endpoint_disease"
    # ref 不可解析 = 远端仓库端点状态病（不是本地实现错）
    if "not a valid object name" in reason:
        return "endpoint_disease"
    if any(k in joined for k in ("llm_error", "endpoint", "connection", "500", "502", "503", "404")):
        return "endpoint_disease"
    if any(k in joined for k in ("budget_exhausted", "wall_exhausted", "agent_lost", "no_progress")):
        return "resource_limit"
    if any(k in self_text for k in _THEORY_SIGNALS):
        return "theory_error"
    if any(k in self_text for k in _DESIGN_SIGNALS):
        return "experiment_design_error"
    return "implementation_error"


# ────────────────────────── 假设与复盘草稿 ──────────────────────────

_GOAL_HEAD_RE = re.compile(r"^【([^】]{4,120})】")


def hypothesis_id_for(mission_id: int) -> str:
    return f"H-{mission_id}"


def initial_hypothesis(m: MissionView) -> str:
    """该单在派发时隐含的假设（从 goal 机械抽取，不外加语义）。"""
    goal = " ".join(m.goal.split())
    head = _GOAL_HEAD_RE.search(goal)
    title = head.group(1) if head else (goal[:80] or f"mission {m.mission_id}")
    return (
        f"假设：在单内预算/墙钟内，按「{title}」可在 {m.repo or 'repo?'} 内产出"
        f"可验收工件并通过其判据。"
    )


def _tool_calls(m: MissionView) -> int:
    val = m.result.get("tool_calls")
    if isinstance(val, int):
        return val
    summary = _evidence(m.result).get("summary")
    if isinstance(summary, Mapping) and isinstance(summary.get("tool_calls"), int):
        return int(summary["tool_calls"])
    return 0


def _changed_files(m: MissionView) -> list[str]:
    files = m.result.get("changed_files")
    if not isinstance(files, list):
        summary = _evidence(m.result).get("summary")
        files = summary.get("changed_files") if isinstance(summary, Mapping) else []
    return [str(f) for f in (files or [])]


def _ignored_evidence(m: MissionView) -> list[str]:
    checks = _evidence(m.result).get("checks") or {}
    ignored = checks.get("evidence_ignored") if isinstance(checks, Mapping) else None
    return [str(x) for x in (ignored or [])]


def failed_step_text(m: MissionView, stage: str, reason: str) -> str:
    """哪一步失败：环位 + 原始证据 + 量化上下文（全部来自 mission）。"""
    ctx = [f"tool_calls={_tool_calls(m)}", f"changed_files={len(_changed_files(m))}"]
    ignored = _ignored_evidence(m)
    if ignored:
        ctx.append(f"evidence_ignored={len(ignored)}")
    if m.model:
        ctx.append(f"model={m.model}")
    label = STAGE_LABELS.get(stage, stage)
    idx = STAGES.index(stage) + 1 if stage in STAGES else 0
    short = _one_line(reason, 180)
    if len(_one_line(reason, 10 ** 6)) > 180:
        short = short.rstrip("，,;； ") + "…"
    return f"第{idx}环「{label}」失败：{short}（{', '.join(ctx)}）"


def invalidated_paths(m: MissionView, stage: str, klass: str, reason: str) -> list[str]:
    """已证无效路径：只写本单证据**实际排除**的做法，不写猜测。"""
    paths: list[str] = []
    tokens = [t.lower() for t in raw_stage_tokens(m)]
    ignored = _ignored_evidence(m)
    if any(k in reason for k in _REASON_KEYWORDS["repo_missing"]):
        paths.append(
            f"在路径 {_repo_path_hint(reason) or m.repo} 上直接开工（该路径不存在或非 git 仓库）"
        )
    if "not a valid object name" in reason:
        paths.append("以原基线 ref 建 worktree（远端 ref 不可解析）")
    if "could not lock config file" in reason:
        paths.append("并行建多个 worktree（.git/config 抢锁失败）")
    if any(k in reason for k in _REASON_KEYWORDS["network"]):
        paths.append("在远端不可达时继续 fetch/建 worktree（github 443 连接超时 130s+）")
    if _changed_files(m) and "no_change" not in tokens:
        if ignored:
            paths.append(
                f"先写 {len(_changed_files(m))} 个文件再补 finish（{len(ignored)} 个改动未被交付门纳入）"
            )
        else:
            paths.append(f"先写 {len(_changed_files(m))} 个文件再补 finish（未调用 finish，交付门未过）")
    if "no_worktree_change" in tokens:
        paths.append("只做只读侦察后直接报成功（工作树零改动，被交付门拒绝）")
    if "no_progress" in tokens:
        paths.append("在同一策略上连续重试（no_progress 终局：无进展调用堆积）")
    if any(k in tokens for k in ("budget_exhausted", "wall_exhausted")):
        paths.append(
            f"把整单目标压在 {_tool_calls(m)} 次工具调用的单窗口内完成（预算/墙钟耗尽）"
        )
    if "llm_error" in tokens:
        paths.append("原模型原单一字不改地重跑（首轮 llm_error 且 tool_calls=0）")
    if "agent_lost" in tokens:
        paths.append("无心跳兜底的长时间单步执行（agent_lost 收割）")
    if not paths:
        paths.append(f"按原判据直接推进：{reason[:80]}")
    return paths[:4]


def _repo_path_hint(reason: str) -> str | None:
    match = re.search(r"(/[^\s:]+)", reason)
    return match.group(1) if match else None


#: 规则模板：键是 (stage, error_class)，值是 (规则文本, 适用条件说明)。
#: 文本刻意写成“当…则…”的可执行形式，过 challenge 的触发条件闸门。
_RULE_TEMPLATES: Mapping[tuple[str, str], tuple[str, str]] = {
    ("setup", "endpoint_disease"): (
        "当开工首步仓库路径/git 解析失败（仓库不存在、not a valid object name、443 超时）时，先报 setup 类 FAIL 并核对路径与基线 ref，勿进入实现环。",
        "setup 端点病：远端/仓库/ref 不可用",
    ),
    ("setup", "implementation_error"): (
        "当 worktree 建立报错（锁冲突/ref 缺失）时，先串行化 worktree 创建并显式指定可解析基线，勿并行抢 .git 配置。",
        "setup 实现类：worktree 建不起来",
    ),
    ("implement", "resource_limit"): (
        "当单内工具调用已超 60 次仍无 finish 迹象时，先落最小可验收工件（一个测试+一份结果 JSON）并 finish，勿继续加码实现。",
        "implement 预算/墙钟耗尽或 no_progress",
    ),
    ("implement", "endpoint_disease"): (
        "当首轮即 llm_error 且 tool_calls=0 时，先做一次最小模型/端点探活并换端点或降复杂度重派，勿原样重跑同一单。",
        "implement 模型端点病（llm_error/0 调用）",
    ),
    ("implement", "implementation_error"): (
        "当交付门因工作树零改动拒绝时，先写 honest FAIL 并列出已读证据，勿用只读侦察冒充交付。",
        "implement 零改动 / 证据未落地",
    ),
    ("verify", "experiment_design_error"): (
        "当判据无法机器裁（缺基准/口径不明）时，先补一个可复算基准或把判据改写成等式，勿先跑实现再找判据。",
        "verify 判据不可判定",
    ),
    ("verify", "implementation_error"): (
        "当测试红且与基准不符时，先定位到最小复现用例并给出误差数字，勿靠放宽阈值转绿。",
        "verify 测试/基准不符",
    ),
    ("plan", "theory_error"): (
        "当推导出现量纲不一致或与解析解不符时，先做量纲/极限自检（小参数展开）再落代码，勿把拟合式当解析锚。",
        "plan 理论/量纲自洽性失败",
    ),
    ("deliver", "implementation_error"): (
        "当准备报成功时，先跑 git status 与测试自检并把产物路径写进 finish，零改动物件一律报 FAIL。",
        "deliver 交付门/报告门未过",
    ),
    ("accept", "experiment_design_error"): (
        "当验收判据缺失或不可复算时，先按战役 metadata 写出数字判据与产物路径，再谈实现完成度。",
        "accept 判据未满足/口径含糊",
    ),
}


def avoidance_rules_for(
    m: MissionView, stage: str, klass: str, *, consulted: Sequence[Mapping[str, Any]] = ()
) -> list[tuple[str, str]]:
    """该单可复用判断规则 [(rule_text, situation)]；命中模板则复用，否则按证据合成。"""
    key = (stage, klass)
    rules: list[tuple[str, str]] = []
    if key in _RULE_TEMPLATES:
        text, situation = _RULE_TEMPLATES[key]
        rules.append((text, situation))
    else:
        reason = failure_reason(m)[:60]
        label = STAGE_LABELS.get(stage, stage)
        rules.append(
            (
                f"当{label}环出现「{reason}」类失败时，先记录最小复现证据再改方案，勿直接重派同一单。",
                f"{stage}/{klass} 未入模板",
            )
        )
    # 前序判例只登记为“引用”（见 harvest 的 later_postmortem_consult），
    # 不把旧规则原文再抄进本单 avoidance_rules——否则规则库按单线性膨胀，
    # 且“同文复用”信号被稀释。规则库收敛靠 rule_text 去重。
    return rules


def draft_from_mission(
    m: MissionView,
    *,
    harvested_by: str,
    consulted: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """失败单 → postmortem 草稿；证据不足（无 stage 信号）返回 None（WITHHELD）。"""
    if m.status != "failed":
        return None
    stage = failure_stage(m)
    if stage not in STAGES:
        return None
    klass = error_class(m, stage)
    reason = failure_reason(m)
    rules = avoidance_rules_for(m, stage, klass, consulted=consulted)
    tokens = raw_stage_tokens(m)
    statement = initial_hypothesis(m)
    return {
        "postmortem_id": f"PM-{m.mission_id}",
        "mission_id": m.mission_id,
        "organization_id": m.organization_id,
        "repo": m.repo,
        "hypothesis_id": hypothesis_id_for(m.mission_id),
        "hypothesis_statement": statement,
        "initial_hypothesis": statement,
        "failed_step": failed_step_text(m, stage, reason),
        "failure_stage": stage,
        "legacy_stage": tokens[0] if tokens else None,
        "error_class": klass,
        "invalidated_paths": invalidated_paths(m, stage, klass, reason),
        "avoidance_rules": [text for text, _ in rules],
        "rule_situations": [situation for _, situation in rules],
        "consulted_rule_ids": [str(r.get("rule_id")) for r in consulted if r.get("rule_id")],
        "status": "draft",
        "harvested_by": harvested_by,
        "evidence": {
            "reason": reason,
            "stage_tokens": tokens,
            "tool_calls": _tool_calls(m),
            "changed_files": _changed_files(m)[:20],
            "evidence_ignored": _ignored_evidence(m)[:20],
            "model": m.model,
            "finished_at": m.finished_at,
            "repo": m.repo,
            "goal_digest": " ".join(m.goal.split())[:280],
        },
        "created_at": _now(),
        "updated_at": _now(),
    }


# ────────────────────────── 挑战与定稿 ──────────────────────────

@dataclass(frozen=True)
class ChallengeVerdict:
    ok: bool
    objections: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "objections": list(self.objections)}


_RULE_TRIGGER_WORDS = ("当", "若", "先", "勿", "一律", "必须")


def challenge_draft(draft: Mapping[str, Any]) -> ChallengeVerdict:
    """结构闸门：草稿必须带可裁决证据、可执行规则、自洽归因。"""
    objections: list[str] = []
    stage = str(draft.get("failure_stage") or "")
    klass = str(draft.get("error_class") or "")
    if stage not in STAGES:
        objections.append(f"failure_stage 不在七环枚举：{stage!r}")
    if klass not in ERROR_CLASSES:
        objections.append(f"error_class 不在五类枚举：{klass!r}")
    evidence = draft.get("evidence") or {}
    if not isinstance(evidence, Mapping) or not evidence.get("reason"):
        objections.append("缺失败原因证据（evidence.reason 为空）")
    failed_step = str(draft.get("failed_step") or "")
    if len(failed_step.strip()) < 20:
        objections.append("failed_step 过于笼统（<20 字符）")
    paths = [str(p) for p in (draft.get("invalidated_paths") or [])]
    if not paths:
        objections.append("无已证无效路径（invalidated_paths 为空）")
    elif any(len(p.strip()) < 8 for p in paths):
        objections.append("已证无效路径过于笼统（<8 字符）")
    rules = [str(r) for r in (draft.get("avoidance_rules") or [])]
    if not rules:
        objections.append("无 avoidance_rule")
    for idx, rule in enumerate(rules, 1):
        text = rule.strip()
        if text in GENERIC_RULE_BLACKLIST:
            objections.append(f"第{idx}条规则是空话（黑名单）：{text}")
        elif len(text) < 20:
            objections.append(f"第{idx}条规则过短（<20 字符）：{text}")
        elif len(text) > MAX_LINE_CHARS:
            objections.append(f"第{idx}条规则超 PLAYBOOK 120 字符预算")
        elif not any(word in text for word in _RULE_TRIGGER_WORDS):
            objections.append(f"第{idx}条规则缺触发条件（当/若/先/勿/一律/必须）")
    if not str(draft.get("harvested_by") or "").strip():
        objections.append("缺 harvested_by（无收割主体）")
    if klass == "theory_error" and stage not in ("plan", "verify", "accept"):
        objections.append(f"归因不自洽：theory_error 落在 {stage} 环")
    if klass == "endpoint_disease" and stage not in ("setup", "implement", "deliver"):
        objections.append(f"归因不自洽：endpoint_disease 落在 {stage} 环")
    return ChallengeVerdict(not objections, tuple(objections))


def independent_review(m: MissionView, draft: Mapping[str, Any]) -> ChallengeVerdict:
    """独立复算：只用 mission 原始证据重推 stage/class，与草稿逐项比对。

    与 ``draft_from_mission`` 不共享缓存/中间态；实现上独立走一遍
    ``failure_stage``/``error_class``（输入只有 MissionView），因此它能发现
    “起草环节把阶段写歪”的情形。
    """
    objections: list[str] = []
    stage = failure_stage(m)
    klas = error_class(m, stage)
    if draft.get("failure_stage") != stage:
        objections.append(
            f"独立复算环位不一致：草稿={draft.get('failure_stage')!r} 复算={stage!r}"
        )
    if draft.get("error_class") != klas:
        objections.append(
            f"独立复算归因不一致：草稿={draft.get('error_class')!r} 复算={klas!r}"
        )
    evidence = draft.get("evidence") or {}
    if isinstance(evidence, Mapping):
        if int(evidence.get("tool_calls") or 0) != _tool_calls(m):
            objections.append("证据字段 tool_calls 与 mission 不一致（草稿不可复算）")
    return ChallengeVerdict(not objections, tuple(objections))


@dataclass(frozen=True)
class ConfirmResult:
    ok: bool
    status: str
    reason: str | None = None


def confirm_draft(
    draft: Mapping[str, Any],
    *,
    reviewer: str,
    role: str,
    mission: MissionView | None = None,
    challenge: ChallengeVerdict | None = None,
    override_reason: str | None = None,
) -> ConfirmResult:
    """定稿闸门：角色合规 + 非同体自审 + 结构与独立复算双通过。"""
    if role not in CONFIRM_ROLES:
        return ConfirmResult(False, "draft", f"角色 {role!r} 不可定稿（只允许 {CONFIRM_ROLES}）")
    if not reviewer or reviewer == draft.get("harvested_by"):
        return ConfirmResult(False, "draft", "起草者不得自审定稿（harvested_by == reviewer）")
    verdict = challenge or challenge_draft(draft)
    objections = list(verdict.objections)
    if mission is not None:
        objections.extend(independent_review(mission, draft).objections)
    if objections:
        return ConfirmResult(False, "disputed", "；".join(objections[:4]))
    return ConfirmResult(True, "confirmed")


def reject_draft(draft: Mapping[str, Any], *, reviewer: str, reason: str) -> dict[str, Any]:
    if len(reason.strip()) < 10:
        raise PrecedentError("驳回必须写明理由（>=10 字符），防静默丢弃")
    updated = dict(draft)
    updated["status"] = "rejected"
    updated["rejected_reason"] = f"{reviewer}: {reason.strip()}"
    updated["updated_at"] = _now()
    return updated


# ────────────────────────── 规则行与引用 ──────────────────────────

def rule_rows(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    """定稿草稿 → postmortem_rules 行（rule_id 稳定可引用）。"""
    rows: list[dict[str, Any]] = []
    for idx, text in enumerate(draft.get("avoidance_rules") or [], 1):
        rows.append(
            {
                "rule_id": f"{draft['postmortem_id']}-R{idx}",
                "postmortem_id": draft["postmortem_id"],
                "mission_id": int(draft["mission_id"]),
                "rule_index": idx,
                "rule_text": str(text),
                "failure_stage": draft["failure_stage"],
                "error_class": draft["error_class"],
                "created_at": _now(),
            }
        )
    return rows


def _tokens(text: str) -> set[str]:
    """轻量分词：ascii 词 + CJK 二元组（检索用，无需外部分词器）。"""
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for a, b in zip(cjk, cjk[1:]):
        words.add(a + b)
    words.update(cjk)
    return words


def score_rule(query: str, rule: Mapping[str, Any], *, stage: str | None = None,
               klass: str | None = None) -> float:
    """规则相关性打分：词面重叠 + 同环/同类加成（确定性，可复算）。"""
    q = _tokens(query)
    r = _tokens(str(rule.get("rule_text") or ""))
    if not q or not r:
        overlap = 0.0
    else:
        overlap = len(q & r) / len(q)
    bonus = 0.0
    if stage and rule.get("failure_stage") == stage:
        bonus += 0.35
    if klass and rule.get("error_class") == klass:
        bonus += 0.35
    return round(overlap + bonus, 4)


def retrieve_rules(
    query: str,
    rules: Sequence[Mapping[str, Any]],
    *,
    stage: str | None = None,
    klass: str | None = None,
    top_k: int = 3,
    min_score: float = 0.2,
) -> list[dict[str, Any]]:
    scored = []
    for rule in rules:
        s = score_rule(query, rule, stage=stage, klass=klass)
        if s >= min_score:
            scored.append({**dict(rule), "score": s})
    scored.sort(key=lambda r: (-float(r["score"]), str(r["rule_id"])))
    return scored[:top_k]


def reference_row(
    rule_id: str,
    *,
    referencing_mission_id: int | None,
    kind: str,
    evidence: str,
) -> dict[str, Any]:
    if kind not in ("later_postmortem_consult", "mission_text_citation", "claim_injection"):
        raise PrecedentError(f"未知引用类别：{kind}")
    return {
        "rule_id": rule_id,
        "referencing_mission_id": referencing_mission_id,
        "reference_kind": kind,
        "evidence": evidence[:400],
        "created_at": _now(),
    }


def scan_text_citations(
    missions: Sequence[MissionView], rules: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """后续任务文本里显式引用 rule_id 的，登记为 mission_text_citation。"""
    refs: list[dict[str, Any]] = []
    codes = {str(r["rule_id"]) for r in rules}
    for m in missions:
        if m.status == "failed" and not m.goal:
            continue
        blob = (m.goal or "") + "\n" + json.dumps(m.result, ensure_ascii=False)[:4000]
        for code in _RULE_CODE_RE.findall(blob):
            if code in codes:
                refs.append(
                    reference_row(
                        code,
                        referencing_mission_id=m.mission_id,
                        kind="mission_text_citation",
                        evidence=f"mission {m.mission_id} 文本命中 {code}",
                    )
                )
    return refs


# ───────────────────────── PLAYBOOK 渲染/注入 ─────────────────────────

_SHORTEN_SUFFIX = "…"


def playbook_line(rule: Mapping[str, Any]) -> str:
    """单条 ≤120 字符的 PLAYBOOK 注入行：rule_id 前置（幂等 token）。"""
    code = str(rule["rule_id"])
    text = str(rule["rule_text"]).strip()
    prefix = f"- {code} "
    budget = MAX_LINE_CHARS - len(prefix) - len(_SHORTEN_SUFFIX)
    if len(text) > budget:
        text = text[:budget].rstrip("，、；; ") + _SHORTEN_SUFFIX
    return prefix + text


def render_playbook_lines(rules: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        line = playbook_line(rule)
        if len(line) > MAX_LINE_CHARS:
            raise PrecedentError(f"{rule['rule_id']}: 注入行超 120 字符")
        token = str(rule["rule_id"])
        if token in seen:
            continue
        seen.add(token)
        lines.append(line)
    return lines


def inject_into_playbook(
    target: str | Path,
    rules: Sequence[Mapping[str, Any]],
    *,
    anchor: str = PLAYBOOK_ANCHOR,
    apply: bool = False,
) -> dict[str, Any]:
    """append-only 注入（复用 precedent_library 的锚点/幂等/回执契约）。"""
    lines = render_playbook_lines(rules)
    receipt = inject_playbook(target, lines, anchor=anchor, apply=apply)
    receipt["line_count"] = len(lines)
    return receipt


PLAYBOOK_SECTION_TEMPLATE = """{anchor}

> 机器生成：`python3 -m tools.failure_postmortems publish --target /root/tp-agent/AGENT_BOOTSTRAP.md`
> 定稿 postmortem 规则，单条 ≤120 字符，append-only（按 rule_id 幂等去重）。

```text
{rules}
```
"""


def render_section(rules: Sequence[Mapping[str, Any]]) -> str:
    lines = render_playbook_lines(rules)
    return PLAYBOOK_SECTION_TEMPLATE.format(
        anchor=PLAYBOOK_SECTION_ANCHOR, rules="\n".join(lines)
    )


# ───────────────────────────── 统计 ─────────────────────────────

def summarize(
    postmortems: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """验收口径计数（不加权、不排名，逐项可复算）。"""
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for pm in postmortems:
        by_status[str(pm["status"])] = by_status.get(str(pm["status"]), 0) + 1
        if pm["status"] == "confirmed":
            by_stage[str(pm["failure_stage"])] = by_stage.get(str(pm["failure_stage"]), 0) + 1
            by_class[str(pm["error_class"])] = by_class.get(str(pm["error_class"]), 0) + 1
    refs_by_kind: dict[str, int] = {}
    for ref in references:
        refs_by_kind[str(ref["reference_kind"])] = refs_by_kind.get(str(ref["reference_kind"]), 0) + 1
    referenced_rules = {str(r["rule_id"]) for r in references}
    return {
        "postmortems": len(postmortems),
        "by_status": by_status,
        "confirmed": by_status.get("confirmed", 0),
        "confirmed_by_stage": by_stage,
        "confirmed_by_class": by_class,
        "rules": len(rules),
        "references": len(references),
        "references_by_kind": refs_by_kind,
        "referenced_rules": len(referenced_rules),
        "criteria": {
            "postmortems_min": 10,
            "postmortems_ok": by_status.get("confirmed", 0) >= 10,
            "referenced_rules_min": 3,
            "referenced_rules_ok": len(referenced_rules) >= 3,
            "stages_covered": len(by_stage),
            "classes_covered": len(by_class),
        },
    }


# ───────────────────────────── CLI ─────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI sugar
    parser = argparse.ArgumentParser(description="failure postmortems: harvest / publish")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="渲染 PLAYBOOK 注入块（读 rules JSON）")
    p_render.add_argument("--rules", required=True, type=Path)

    p_inject = sub.add_parser("publish", help="append-only 注入 bootstrap PLAYBOOK")
    p_inject.add_argument("--target", required=True, type=Path)
    p_inject.add_argument("--rules", type=Path, help="rules JSON（缺省=从 DB 读确认规则）")
    p_inject.add_argument("--anchor", default=PLAYBOOK_ANCHOR)
    p_inject.add_argument("--apply", action="store_true")

    p_harvest = sub.add_parser("harvest", help="从 failed mission 收割草稿并挑战定稿")
    p_harvest.add_argument("--days", type=int, default=7)
    p_harvest.add_argument("--limit", type=int, default=40)
    p_harvest.add_argument("--apply", action="store_true")
    p_harvest.add_argument("--confirm", action="store_true")
    p_harvest.add_argument("--out", type=Path, default=None)
    p_harvest.add_argument("--refresh", action="store_true",
                           help="重写已存在的 postmortem（含其规则与引用）")

    args = parser.parse_args(argv)
    if args.cmd == "render":
        rules = json.loads(args.rules.read_text(encoding="utf-8"))
        print(render_section(rules))
        return 0
    if args.cmd == "publish":
        if args.rules:
            rules = json.loads(args.rules.read_text(encoding="utf-8"))
        else:
            from tools.harvest_failure_postmortems import load_confirmed_rules  # 延迟导入

            rules = load_confirmed_rules()
        receipt = inject_into_playbook(args.target, rules, anchor=args.anchor, apply=args.apply)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "harvest":
        from tools.harvest_failure_postmortems import run_harvest

        summary = run_harvest(
            days=args.days, limit=args.limit, apply=args.apply, confirm=args.confirm,
            out=args.out, refresh=args.refresh
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())