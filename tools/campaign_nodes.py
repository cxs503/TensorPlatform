#!/usr/bin/env python3
"""campaign_nodes —— 战役主计划的机器可验节点状态层（清偿规划线 D-R5-1 欠账）。

背景（任务规划师第 5 班实测）
------------------------------
``m611/artifacts/master_plan.json`` 有 27 个节点 / 6 个里程碑，但 27/27 节点**没有 status
字段**；任务书要的 ``SELECT plan_id,status,count(*) FROM campaign_nodes`` 在本地全部库中
「表不存在」。于是规划线 R1–R5 连续 5 班只能手抄态势，谱系追溯 KPI 无法机验。

本模块做三件事
--------------
1. **谱系状态层**：SQLite 表 ``campaign_nodes``（主键 plan_id+node_id），列
   plan_id/node_id/milestone/layer/risk/status/evidence_kind/evidence_ref/updated_utc
   （另含 evidence_count/match_rule/title 便于审计）。status 用 CHECK 约束显式限定为
   ``{not_started, in_progress, verified, blocked}``，evidence_kind ∈
   ``{mission, report, commit, none}``。本层与后端 PG 版
   ``backend/api/campaign_plans.py`` 的 campaign_nodes（pending/in-flight/done/...）**独立**：
   它是本地可复算的证据化视图，不依赖线上库是否迁移到位。
2. **证据回填器**：拉 ``/api/v1/tensor/missions``（全分页）与 ``/api/v1/tensor/reports``
   （全分页），按**可解释的归并规则**把 mission_id / report_id 挂到节点上。
3. **产物**：``artifacts/campaign_status.json``（机器可验）+ markdown 摘要（27 行表）+ PNG。

归并规则（代码即规则，无任何节点的结论硬编码）
----------------------------------------------
R0  自排除：本仪表自身的任务（goal 含 ``campaign_nodes`` / ``plan_id,status,count(*)``）
    与主计划自身的建单（mission_id == plan mission_id）不计为节点证据——它们描述的是
    「测量仪表」而非「被测量对象」。
R0b 盲评期：文本/标签出现 ``evo1-`` 的工件不参与归并、不落盘（EVO-1 盲评纪律）。
R1 **强证据**（权重 5）：出现节点 id 的独立 token（如 ``F01``、``SI-3``，大小写敏感、两侧
    不紧贴数字/字母）；或出现"长标识符指纹"——snake_case 度量名
    （如 ``pytest_test_added_mass_integration``）或 >=12 字符拉丁词。主计划
    acceptance_criteria.metric 常被任务书原文引用，故这类 token 近似节点唯一指纹。
R2 里程碑标签（权重 2）：mission.campaign_phase / campaign_id 恰为节点里程碑 id（如 ``M2``），
    且必须 R1 或 R3 亦有命中（否则单凭里程碑无法区分同里程碑内 6 个节点）。
R3 **弱证据**：节点签名 = 标题/目标/物理域里的拉丁词（len>=3）与 CJK 二元组（单字丢弃）；
    只保留判别性词项（在 N 个节点签名中文档频次 df <= max(2, ceil(0.15N))）：
    df<=2 记 3 分（稀有），2<df<=cap 记 2 分（一般）。df 过滤自动剔除「模型/验证/耦合」
    这类全节点通用词，规则自适应任意计划、无节点硬编码。
阈值 score >= 5：等价于 1 个强证据，或 2 个稀有词项，或 3 个一般词项。
排序/归属：强证据优先于纯词项重合 → 分数降序 → node_id 字典序（确定性 tie-break，
记 match_rule=strong_id+tie / keyword_terms+tie）。
报告若文本无强归属但其 mission_id 已被某节点归并 → 记 ``via_mission_link``（最弱规则）。

**状态只由强证据驱动**（显式节点号/指纹/报告挂靠该 mission）；弱词项证据仅在无强证据时
兜底（match_rule 带 ``+weak_only`` 后缀）。这样通用词噪声不会翻转节点状态。

状态推导（derive_status_full，纯函数）
--------------------------------------
- 任一被归并 mission 状态 ∈ VERIFIED_STATUSES → ``verified``
- 否则任一 ∈ ACTIVE_STATUSES → ``in_progress``
- 否则任一 ∈ BLOCKED_STATUSES → ``blocked``
- 否则有证据但状态未知 → ``in_progress``（match_rule=evidence_unknown_status，保守）
- 无任何证据 → ``not_started``

幂等性
------
输出 JSON 用 ``sort_keys=True`` + 固定缩进，列表定序 (score desc, kind, ref_id)；
``updated_utc`` / ``generated_utc`` 取**内容派生时钟**（该节点证据的最大 created_at，
无证据则取主计划 created_utc），不取 wall clock —— 同一输入连跑两次逐字节相同。

用法
----
    # 1) 刷新证据缓存（唯一联网步骤，只读 GET）
    python3 tools/campaign_nodes.py fetch --cache artifacts/campaign_evidence_cache.json

    # 2) 建库 + 回填 + 出 JSON/markdown/图（离线、可复算、幂等）
    python3 tools/campaign_nodes.py build --plan artifacts/m611_master_plan.json \
        --cache artifacts/campaign_evidence_cache.json \
        --out-json artifacts/campaign_status.json \
        --out-md docs/campaign_status_m611.md \
        --out-db artifacts/campaign_nodes.db \
        --out-figure artifacts/campaign_status_coverage.png

    # 3) 验收 SQL（任务书要求的形态）
    python3 tools/campaign_nodes.py sql --db artifacts/campaign_nodes.db
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# 取值域（显式约束：DDL 与 Python 常量同源）
# --------------------------------------------------------------------------- #
STATUS_DOMAIN: Tuple[str, ...] = ("not_started", "in_progress", "verified", "blocked")
EVIDENCE_KINDS: Tuple[str, ...] = ("mission", "report", "commit", "none")

# 真正闭环的状态才算 verified；awaiting_acceptance 是「已交未裁」，归在 ACTIVE
# （保守：不把「等裁决」当「已验讫」，否则谱系 KPI 会虚高）。
VERIFIED_STATUSES = frozenset({"accepted", "done", "completed", "merged", "verified"})
# 平台高频态 ``succeeded``：agent 跑完并交了 result，但尚未裁决 → 只能算「有交付证据」，
# 单列 DELIVERED，映射到 in_progress（domain 里没有 delivered；不得虚报 verified）。
DELIVERED_STATUSES = frozenset({"succeeded", "success", "finished", "delivered"})
ACTIVE_STATUSES = frozenset(
    {
        "running", "claimed", "in_progress", "pending", "queued", "dispatched",
        "awaiting_acceptance", "in-flight", "in_flight", "assigned", "review",
    }
)
BLOCKED_STATUSES = frozenset(
    {
        "failed", "blocked", "cancelled", "canceled", "revoked", "rejected", "timeout",
        "error", "abandoned", "stale",
        # stopped = 人工/系统中断的 runner 会话：无交付物，按「受阻」计（保守）
        "stopped", "halted", "paused",
    }
)

STATUS_CN = {
    "not_started": "未启动",
    "in_progress": "进行中",
    "verified": "已验讫",
    "blocked": "受阻",
}

SCHEMA_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# 归并规则常量
# --------------------------------------------------------------------------- #
SCORE_NODE_ID = 9            # 显式节点号（正文任意位置出现独立 token）
SCORE_NODE_ID_PRIMARY = 12   # 主播种标记：`|F01|` / `｜F01｜`（任务书标题里的节点号槽位）
SCORE_FINGERPRINT = 4        # 长标识符指纹（snake_case 度量名 / >=12 字符拉丁词）
SCORE_MILESTONE = 2
SCORE_TERM_RARE = 3          # df<=2 的稀有判别词项
SCORE_TERM_COMMON = 2        # df<=df_cap 的一般判别词项
MERGE_THRESHOLD = 6
EVIDENCE_DETAIL_CAP = 8   # JSON 里只留分数最高的若干条证据明细；全量留在 SQLite campaign_evidence
SELF_EXCLUDE_PATTERNS = ("campaign_nodes", "campaign_status", "plan_id,status,count(*)")
BLIND_TAG = "evo1-"

DDL = """
CREATE TABLE IF NOT EXISTS campaign_plans (
    plan_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    nodes_total  INTEGER NOT NULL,
    plan_sha256  TEXT NOT NULL,
    ingested_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_nodes (
    plan_id        TEXT NOT NULL,
    node_id        TEXT NOT NULL,
    milestone      TEXT NOT NULL,
    layer          TEXT NOT NULL,
    risk           TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('not_started','in_progress','verified','blocked')),
    evidence_kind  TEXT NOT NULL CHECK (evidence_kind IN ('mission','report','commit','none')),
    evidence_ref   TEXT,
    updated_utc    TEXT NOT NULL,
    title          TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    match_rule     TEXT NOT NULL DEFAULT 'none',
    PRIMARY KEY (plan_id, node_id)
);

CREATE TABLE IF NOT EXISTS campaign_evidence (
    plan_id    TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ref_id     TEXT NOT NULL,
    mission_id TEXT,
    score      INTEGER NOT NULL,
    match_rule TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, node_id, kind, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_nodes_status ON campaign_nodes (plan_id, status);
CREATE INDEX IF NOT EXISTS idx_campaign_evidence_node ON campaign_evidence (plan_id, node_id);
"""


@dataclass
class Match:
    """一条证据归并记录。"""

    kind: str          # mission | report | commit
    ref_id: str
    mission_id: Optional[str]
    score: int
    match_rule: str
    created_at: str
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "ref_id": self.ref_id,
            "mission_id": self.mission_id,
            "score": self.score,
            "match_rule": self.match_rule,
            "created_at": self.created_at,
            "reasons": list(self.reasons),
        }

# --------------------------------------------------------------------------- #
# 词项抽取（不依赖分词器：拉丁词 + CJK 二元组 + df 判别性过滤）
# --------------------------------------------------------------------------- #
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
EXPLICIT_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,3}-?\d{1,3}(?![0-9])")
TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")

# 极常用二元组：即便 df 过滤没剔掉也不作判别信号（保证跨语料稳定）
CJK_STOP_BIGRAMS = frozenset(
    {
        "实现", "验证", "框架", "模块", "模型", "误差", "对比", "解析", "检查", "支持",
        "通过", "闭环", "集成", "参数", "数据", "时间", "以及", "并且", "任务", "目标",
        "标准", "要求", "输出", "输入", "基础", "问题", "方法", "结果", "分析", "系统",
        "数值", "实验", "文献", "条件", "单元", "测试", "文件", "接口", "同步", "耦合",
    }
)
LATIN_STOP = frozenset(
    {"the", "and", "for", "with", "from", "that", "this", "are", "was", "not", "all", "any"}
)


def _norm_text(text: Any) -> str:
    return "" if text is None else str(text)


def cjk_bigrams(text: str) -> List[str]:
    out: List[str] = []
    for run in CJK_RE.findall(text):
        if len(run) < 2:
            continue  # 单字不构成判别词项（_在/_的/_是 之类噪声）
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in CJK_STOP_BIGRAMS:
                out.append(bg)
    return out


def latin_terms(text: str) -> List[str]:
    return [t.lower() for t in LATIN_RE.findall(text) if t.lower() not in LATIN_STOP]


def explicit_ids(text: str) -> List[str]:
    return EXPLICIT_ID_RE.findall(text or "")


FINGERPRINT_RE = re.compile(r"[a-z][a-z0-9]*_[a-z0-9_]{3,}")


def is_fingerprint(term: str) -> bool:
    """长标识符指纹：snake_case 度量名（如 pytest_test_added_mass_integration）或
    >=12 字符的拉丁词。主计划 acceptance_criteria.metric 常被任务书原文引用，
    因此这类 token 近似节点唯一指纹 → 权重等同显式节点号。"""
    t = term or ""
    if FINGERPRINT_RE.fullmatch(t):
        return True
    return len(t) >= 12 and all(c.isalnum() or c in "_-" for c in t)


def text_terms(text: str) -> set:
    """待匹配文本 -> 词项集合（与节点签名同构）。"""
    text = _norm_text(text)
    terms: set = set()
    terms.update(cjk_bigrams(text))
    terms.update(latin_terms(text))
    terms.update(explicit_ids(text))
    return terms


def node_text(node: Dict[str, Any]) -> str:
    parts = [
        _norm_text(node.get("id")),
        _norm_text(node.get("title")),
        _norm_text(node.get("goal")),
        _norm_text(node.get("physics_domain")),
    ]
    for crit in node.get("acceptance_criteria") or []:
        if isinstance(crit, dict):
            parts.append(_norm_text(crit.get("metric")))
    return "\n".join(parts)


def build_signatures(
    nodes: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, int]], Dict[str, int]]:
    """返回 (每节点 判别性词项->权重, 词项->文档频次)。自适应任意计划，无节点硬编码。"""
    n = max(1, len(nodes))
    df_cap = max(2, math.ceil(0.15 * n))
    milestone_ids = {_norm_text(node.get("milestone")) for node in nodes}
    layer_tokens = set()
    for node in nodes:
        for tok in TOKEN_SPLIT_RE.split(_norm_text(node.get("layer"))):
            if tok:
                layer_tokens.add(tok.lower())

    raw: Dict[str, set] = {}
    df: Dict[str, int] = {}
    for node in nodes:
        text = node_text(node)
        terms: set = set()
        terms.update(cjk_bigrams(text))
        terms.update(
            t for t in latin_terms(text) if not EXPLICIT_ID_RE.fullmatch(t.upper())
        )
        for eid in explicit_ids(text):
            if eid in milestone_ids or eid.lower() in layer_tokens:
                continue  # 里程碑号/层名不是节点号，避免 M2/L1 抢分
            terms.add(eid)
        nid = _norm_text(node.get("id"))
        raw[nid] = terms
        for term in terms:
            df[term] = df.get(term, 0) + 1

    signatures: Dict[str, Dict[str, int]] = {}
    for nid, terms in raw.items():
        kept: Dict[str, int] = {}
        for term in sorted(terms):  # 定序：跨进程 hash 随机化不影响产物字节
            freq = df.get(term, 0)
            if freq > df_cap:
                continue
            if EXPLICIT_ID_RE.fullmatch(term):
                kept[term] = SCORE_NODE_ID
            elif is_fingerprint(term):
                kept[term] = SCORE_FINGERPRINT
            elif freq <= 2:
                kept[term] = SCORE_TERM_RARE
            else:
                kept[term] = SCORE_TERM_COMMON
        signatures[nid] = kept
    return signatures, df


def score_evidence(
    text: str,
    signatures: Dict[str, Dict[str, int]],
    milestones: Dict[str, str],
    phases: Sequence[str] = (),
) -> Dict[str, Dict[str, Any]]:
    """对一段文本给所有节点打分：{node_id: {score, explicit, reasons}}。"""
    terms = text_terms(text)
    phase_set = set(phases or ())
    out: Dict[str, Dict[str, Any]] = {}
    for node_id, sig in signatures.items():
        primary_re = re.compile(
            r"[|\uff5c]\s*" + re.escape(node_id) + r"\s*[|\uff5c]"
        )
        score = 0
        reasons: List[str] = []
        node_id_hit = False
        node_id_primary = bool(primary_re.search(text))
        fingerprint_hit = False
        term_hits = 0
        for term, weight in sorted(sig.items()):  # 定序，保证 reasons 与产物可复算
            if term not in terms:
                continue
            score += weight
            if weight >= SCORE_NODE_ID:
                node_id_hit = True
                reasons.append(f"node_id:{term}")
            elif weight >= SCORE_FINGERPRINT:
                fingerprint_hit = True
                if len(reasons) < 8:
                    reasons.append(f"fingerprint:{term}")
            else:
                term_hits += 1
                if term_hits <= 5:
                    reasons.append(f"term:{term}")
        if not (node_id_hit or fingerprint_hit or term_hits or node_id_primary):
            continue
        if node_id_primary:
            # 主播种槽位（|F01|）优先级最高：比正文里随口提到一次节点号更硬
            score += SCORE_NODE_ID_PRIMARY - (SCORE_NODE_ID if node_id_hit else 0)
            node_id_hit = True
            reasons.insert(0, f"node_id_primary:{node_id}")
        ms = milestones.get(node_id)
        if ms and ms in phase_set:  # R2 需 R1/R3 亦命中
            score += SCORE_MILESTONE
            reasons.append(f"milestone:{ms}")
        out[node_id] = {
            "score": score,
            "explicit": node_id_hit or fingerprint_hit,
            "node_id_hit": node_id_hit,
            "node_id_primary": node_id_primary,
            "fingerprint_hit": fingerprint_hit,
            "reasons": reasons,
        }
    return out


def assign_nodes(
    scores: Dict[str, Dict[str, Any]], threshold: int = MERGE_THRESHOLD
) -> Tuple[Optional[str], Dict[str, Any]]:
    """选唯一归属节点（确定性 tie-break：同分取 node_id 字典序最小）。"""
    candidates = [(nid, v) for nid, v in scores.items() if v["score"] >= threshold]
    if not candidates:
        return None, {}
    # 归属优先级：显式节点号 > 长标识符指纹 > 纯词项重合（任务书标题里写死的节点号是
    # 最强信号，不能被一堆指标名指纹的分数反超）；同档内分数降序，再按 node_id 字典序。
    def _rank(item):
        nid, v = item
        tier = (
            0 if v.get("node_id_primary")
            else (1 if v.get("node_id_hit") else (2 if v.get("fingerprint_hit") else 3))
        )
        return (tier, -v["score"], nid)

    candidates.sort(key=_rank)
    best_id, best = candidates[0]
    tied = sorted(
        nid for nid, v in candidates
        if v["score"] == best["score"] and _rank((nid, v))[0] == _rank(candidates[0])[0]
    )
    if best.get("node_id_primary"):
        rule = "node_id_primary"
    elif best.get("node_id_hit"):
        rule = "node_id"
    elif best.get("fingerprint_hit"):
        rule = "fingerprint"
    else:
        rule = "keyword_terms"
    if len(tied) > 1:
        rule += "+tie"
    detail = dict(best)
    detail["rule"] = rule
    detail["tied_with"] = tied[1:] if len(tied) > 1 else []
    return best_id, detail


# --------------------------------------------------------------------------- #
# 输入准备
# --------------------------------------------------------------------------- #
def load_plan(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    if not isinstance(plan, dict) or "nodes" not in plan:
        raise ValueError(f"not a master plan (missing nodes): {path}")
    if not plan["nodes"]:
        raise ValueError(f"master plan has zero nodes: {path}")
    return plan


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_self_instrument(item: Dict[str, Any]) -> bool:
    blob = " ".join(
        _norm_text(item.get(k)) for k in ("goal", "title", "summary", "tags", "name")
    )
    return any(pat in blob for pat in SELF_EXCLUDE_PATTERNS)


def is_blind_period(item: Dict[str, Any]) -> bool:
    tags = item.get("tags")
    blob = " ".join(
        [
            _norm_text(item.get("goal")),
            _norm_text(item.get("title")),
            _norm_text(item.get("summary")),
            " ".join(tags) if isinstance(tags, list) else _norm_text(tags),
            _norm_text(item.get("task_key")),
        ]
    ).lower()
    return BLIND_TAG in blob


def prepare_evidence_pool(
    cache: Dict[str, Any], plan_mission_id: Optional[str] = None
) -> Dict[str, Any]:
    """过滤缓存：自排除 / 盲评期剔除，返回参与归并的池 + 排除计数（可审计）。"""
    raw_missions = list(cache.get("missions") or [])
    raw_reports = list(cache.get("reports") or [])

    missions: List[Dict[str, Any]] = []
    excl = {"missions_self": 0, "missions_blind": 0, "reports_self": 0, "reports_blind": 0}
    for m in raw_missions:
        if is_blind_period(m):
            excl["missions_blind"] += 1
            continue
        if plan_mission_id is not None and str(m.get("id")) == str(plan_mission_id):
            excl["missions_self"] += 1
            continue
        if is_self_instrument(m):
            excl["missions_self"] += 1
            continue
        missions.append(m)

    reports: List[Dict[str, Any]] = []
    for r in raw_reports:
        if is_blind_period(r):
            excl["reports_blind"] += 1
            continue
        if is_self_instrument(r):
            excl["reports_self"] += 1
            continue
        reports.append(r)

    return {
        "missions": missions,
        "reports": reports,
        "excluded": excl,
        "raw_counts": {"missions": len(raw_missions), "reports": len(raw_reports)},
        "cache_meta": {
            "missions_total_reported": cache.get("missions_total_reported"),
            "reports_total_reported": cache.get("reports_total_reported"),
            "reports_offset_ignored": cache.get("reports_offset_ignored", False),
            "missions_offset_ignored": cache.get("missions_offset_ignored", False),
            "api_base": cache.get("api_base"),
        },
    }


# --------------------------------------------------------------------------- #
# 归并 + 状态推导
# --------------------------------------------------------------------------- #
def mission_text(mission: Dict[str, Any]) -> str:
    return _norm_text(mission.get("goal")) + "\n" + _norm_text(mission.get("result"))


def report_text(report: Dict[str, Any]) -> str:
    tags = report.get("tags")
    tag_str = " ".join(tags) if isinstance(tags, list) else _norm_text(tags)
    return " ".join(
        [
            _norm_text(report.get("title")),
            _norm_text(report.get("summary")),
            _norm_text(report.get("scenario_key")),
            tag_str,
        ]
    )


def derive_status_full(
    evidence: Sequence[Match], statuses: Sequence[str]
) -> Tuple[str, str]:
    """把节点上的证据 + mission 状态序列映射到状态取值域。"""
    sts = [s.lower() for s in statuses if s]
    if any(s in VERIFIED_STATUSES for s in sts):
        return "verified", "status:verified_mission"
    if any(s in ACTIVE_STATUSES for s in sts):
        return "in_progress", "status:active_mission"
    if any(s in DELIVERED_STATUSES for s in sts):
        # 有交付但未裁决：证据充分、结论未定 → in_progress（不得虚报 verified）
        return "in_progress", "status:delivered_unverified"
    if any(s in BLOCKED_STATUSES for s in sts):
        return "blocked", "status:blocked_mission"
    if evidence:
        return "in_progress", "evidence_unknown_status"
    return "not_started", "no_evidence"


def _phases(item: Dict[str, Any]) -> List[str]:
    vals: List[str] = []
    for key in ("campaign_phase", "campaign_id", "milestone"):
        v = item.get(key)
        if v is None or str(v) == "None":
            continue
        vals.extend(str(v).split())
    return vals


def build_status(
    plan: Dict[str, Any],
    pool: Dict[str, Any],
    plan_mission_id: Optional[str] = None,
) -> Dict[str, Any]:
    """核心：主计划 + 证据池 -> campaign_status 文档（纯函数、确定性）。"""
    nodes = plan["nodes"]
    plan_id = _norm_text(plan.get("mission_id")) or "plan"
    plan_created = _norm_text(plan.get("created_utc")) or "1970-01-01T00:00:00Z"
    milestones = {_norm_text(n.get("id")): _norm_text(n.get("milestone")) for n in nodes}
    signatures, _df = build_signatures(nodes)

    missions = pool.get("missions") or []
    reports = pool.get("reports") or []
    mission_status = {str(m.get("id")): _norm_text(m.get("status")).lower() for m in missions}

    per_node: Dict[str, List[Match]] = {nid: [] for nid in milestones}
    mission_to_node: Dict[str, str] = {}
    dropped: List[Dict[str, Any]] = []
    unassigned_missions = 0
    unassigned_reports = 0

    for mission in missions:
        scores = score_evidence(mission_text(mission), signatures, milestones, _phases(mission))
        node_id, detail = assign_nodes(scores)
        if node_id is None:
            unassigned_missions += 1
            if len(dropped) < 200:
                dropped.append(
                    {"kind": "mission", "ref_id": str(mission.get("id")),
                     "best_score": max([v["score"] for v in scores.values()] or [0])}
                )
            continue
        mid = str(mission.get("id"))
        mission_to_node[mid] = node_id
        per_node[node_id].append(
            Match(
                kind="mission",
                ref_id=mid,
                mission_id=mid,
                score=detail["score"],
                match_rule=detail["rule"],
                created_at=_norm_text(mission.get("created_at")),
                reasons=detail["reasons"],
            )
        )

    for report in reports:
        scores = score_evidence(report_text(report), signatures, milestones)
        node_id, detail = assign_nodes(scores)
        rep_mid = report.get("mission_id")
        linked = str(rep_mid) if rep_mid is not None and str(rep_mid) != "None" else None
        if node_id is None and linked and linked in mission_to_node:
            node_id = mission_to_node[linked]
            detail = {
                "score": MERGE_THRESHOLD,
                "rule": "via_mission_link",
                "reasons": [f"mission:{linked}"],
            }
        if node_id is None:
            unassigned_reports += 1
            if len(dropped) < 200:
                dropped.append(
                    {"kind": "report", "ref_id": str(report.get("id")),
                     "best_score": max([v["score"] for v in scores.values()] or [0])}
                )
            continue
        per_node[node_id].append(
            Match(
                kind="report",
                ref_id=str(report.get("id")),
                mission_id=linked,
                score=detail["score"],
                match_rule=detail["rule"],
                created_at=_norm_text(report.get("created_at")),
                reasons=detail["reasons"],
            )
        )

    node_rows: List[Dict[str, Any]] = []
    for node in sorted(nodes, key=lambda n: _norm_text(n.get("id"))):
        nid = _norm_text(node.get("id"))
        evs = sorted(
            per_node[nid],
            key=lambda m: (
                0 if m.match_rule.startswith("node_id_primary")
                else (1 if m.match_rule.startswith("node_id")
                      else (2 if m.match_rule.startswith("fingerprint") else 3)),
                -m.score, m.kind, m.ref_id,
            ),
        )
        # 状态只由**强证据**驱动（显式节点号 / 长标识符指纹 / 报告挂到该 mission）；
        # 纯词项重合属弱证据，仅在没有强证据时兜底 —— 否则通用词噪声会把状态翻错。
        strong = [e for e in evs
                  if e.match_rule.startswith("node_id") or e.match_rule.startswith("fingerprint")]
        basis = strong if strong else evs
        basis_statuses = [
            mission_status.get(e.mission_id or "", "") if e.kind == "mission" else ""
            for e in basis
        ]
        status, status_rule = derive_status_full(basis, basis_statuses)
        status_rule += "+strong" if strong else ("+weak_only" if evs else "")
        report_evs = [e for e in evs if e.kind == "report"]
        mission_evs = [e for e in evs if e.kind == "mission"]
        commit_evs = [e for e in evs if e.kind == "commit"]
        if report_evs:
            evidence_kind, top = "report", report_evs[0]
        elif mission_evs:
            evidence_kind, top = "mission", mission_evs[0]
        elif commit_evs:
            evidence_kind, top = "commit", commit_evs[0]
        else:
            evidence_kind, top = "none", None
        stamps = [e.created_at for e in evs if e.created_at]
        node_rows.append(
            {
                "plan_id": plan_id,
                "node_id": nid,
                "milestone": _norm_text(node.get("milestone")),
                "layer": _norm_text(node.get("layer")),
                "risk": _norm_text(node.get("risk")),
                "status": status,
                "evidence_kind": evidence_kind,
                "evidence_ref": f"{top.kind}:{top.ref_id}" if top else None,
                "updated_utc": max(stamps) if stamps else plan_created,
                "title": _norm_text(node.get("title")),
                "evidence_count": len(evs),
                "evidence_strong": len(strong),
                "evidence_missions": sorted({e.mission_id for e in mission_evs if e.mission_id}),
                "evidence_reports": sorted({e.ref_id for e in report_evs}),
                "merge_rule": top.match_rule if top else "no_evidence",
                "match_rule": status_rule,
                "evidence": [e.as_dict() for e in evs[:EVIDENCE_DETAIL_CAP]],
                "evidence_detail_truncated": max(0, len(evs) - EVIDENCE_DETAIL_CAP),
            }
        )

    counts: Dict[str, int] = {s: 0 for s in STATUS_DOMAIN}
    for row in node_rows:
        counts[row["status"]] += 1
    kind_counts: Dict[str, int] = {k: 0 for k in EVIDENCE_KINDS}
    for row in node_rows:
        kind_counts[row["evidence_kind"]] += 1

    mission_ids = set(mission_status)
    dangling: List[Dict[str, Any]] = []
    for row in node_rows:
        for ev in row["evidence"]:
            if ev["kind"] == "report":
                mid = ev["mission_id"]
                if mid and mid not in mission_ids:
                    dangling.append({"node_id": row["node_id"], "kind": "report",
                                     "ref_id": ev["ref_id"], "missing_mission_id": mid})
            elif ev["kind"] == "mission" and ev["ref_id"] not in mission_ids:
                dangling.append({"node_id": row["node_id"], "kind": "mission",
                                 "ref_id": ev["ref_id"], "missing_mission_id": ev["ref_id"]})

    plan_nodes = len(nodes)
    generated = max([r["updated_utc"] for r in node_rows] + [plan_created])

    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "plan_title": _norm_text(plan.get("title")),
        "generated_utc": generated,
        "generated_utc_note": (
            "content-derived clock = max(node evidence created_at, plan.created_utc); "
            "NOT wall clock, so reruns are byte-identical"
        ),
        "status_domain": list(STATUS_DOMAIN),
        "evidence_kinds": list(EVIDENCE_KINDS),
        "merge_rules": {
            "threshold": MERGE_THRESHOLD,
            "node_id_weight": SCORE_NODE_ID,
            "node_id_primary_weight": SCORE_NODE_ID_PRIMARY,
            "milestone_weight": SCORE_MILESTONE,
            "term_weight_rare": SCORE_TERM_RARE,
            "term_weight_common": SCORE_TERM_COMMON,
            "fingerprint_weight": SCORE_FINGERPRINT,
            "df_cap": max(2, math.ceil(0.15 * len(nodes))),
            "tie_break": "same score -> smallest node_id (lexicographic)",
            "self_exclude_patterns": list(SELF_EXCLUDE_PATTERNS),
            "blind_period_tag": BLIND_TAG,
        },
        "plan": {
            "nodes_total": plan_nodes,
            "milestones_total": len(plan.get("milestones") or {}),
            "sha256": plan.get("_sha256"),
            "source_name": plan.get("_source_name"),
        },
        "inputs": {
            "missions_raw": pool["raw_counts"]["missions"],
            "reports_raw": pool["raw_counts"]["reports"],
            "missions_used": len(missions),
            "reports_used": len(reports),
            "missions_assigned": len(mission_to_node),
            "reports_assigned": sum(len(r["evidence_reports"]) for r in node_rows),
            "excluded": pool["excluded"],
            "cache": pool.get("cache_meta", {}),
            "unassigned_missions": unassigned_missions,
            "unassigned_reports": unassigned_reports,
            "unassigned_samples": dropped,
        },
        "reconciliation": {
            "plan_nodes": plan_nodes,
            "status_nodes": len(node_rows),
            "diff": len(node_rows) - plan_nodes,
            "dangling_refs": dangling,
            "dangling_count": len(dangling),
        },
        "evidence_detail_cap": EVIDENCE_DETAIL_CAP,
        "counts": {"by_status": counts, "by_evidence_kind": kind_counts},
        "coverage": {
            "with_evidence": sum(1 for r in node_rows if r["evidence_kind"] != "none"),
            "with_evidence_ratio": round(
                sum(1 for r in node_rows if r["evidence_kind"] != "none") / max(1, len(node_rows)), 4
            ),
            "with_report": kind_counts["report"],
            "with_mission": kind_counts["mission"],
            "by_milestone": {
                ms: {
                    s: sum(1 for r in node_rows if r["milestone"] == ms and r["status"] == s)
                    for s in STATUS_DOMAIN
                }
                for ms in sorted({r["milestone"] for r in node_rows})
            },
        },
        "nodes": node_rows,
    }


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def dumps_json(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def write_status(db_path: str | Path, doc: Dict[str, Any]) -> None:
    """状态文档写入 SQLite（重建库、事务内插入，可重复执行）。"""
    db_path = Path(db_path)
    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(DDL)
        with con:
            con.execute(
                "INSERT OR REPLACE INTO campaign_plans"
                " (plan_id,title,nodes_total,plan_sha256,ingested_utc) VALUES (?,?,?,?,?)",
                (
                    doc["plan_id"],
                    doc["plan_title"],
                    doc["plan"]["nodes_total"],
                    doc["plan"]["sha256"] or "",
                    doc["generated_utc"],
                ),
            )
            for row in doc["nodes"]:
                con.execute(
                    "INSERT OR REPLACE INTO campaign_nodes (plan_id,node_id,milestone,layer,risk,"
                    "status,evidence_kind,evidence_ref,updated_utc,title,evidence_count,match_rule)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["plan_id"], row["node_id"], row["milestone"], row["layer"], row["risk"],
                        row["status"], row["evidence_kind"], row["evidence_ref"], row["updated_utc"],
                        row["title"], row["evidence_count"], row["match_rule"],
                    ),
                )
                for ev in row["evidence"]:
                    con.execute(
                        "INSERT OR REPLACE INTO campaign_evidence (plan_id,node_id,kind,ref_id,"
                        "mission_id,score,match_rule,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            row["plan_id"], row["node_id"], ev["kind"], ev["ref_id"],
                            ev["mission_id"], ev["score"], ev["match_rule"], ev["created_at"],
                        ),
                    )
    finally:
        con.close()


def read_status_counts(db_path: str | Path, plan_id: str) -> List[Tuple[str, str, int]]:
    """任务书要求的查询形态：SELECT plan_id,status,count(*) FROM campaign_nodes。"""
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT plan_id, status, count(*) FROM campaign_nodes WHERE plan_id = ?"
            " GROUP BY plan_id, status ORDER BY status",
            (plan_id,),
        ).fetchall()
    finally:
        con.close()


def read_node_rows(db_path: str | Path, plan_id: str) -> List[Dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT plan_id,node_id,milestone,layer,risk,status,evidence_kind,evidence_ref,"
            "updated_utc,title,evidence_count,match_rule FROM campaign_nodes"
            " WHERE plan_id = ? ORDER BY node_id",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def render_markdown(doc: Dict[str, Any]) -> str:
    nodes = doc["nodes"]
    counts = doc["counts"]["by_status"]
    kinds = doc["counts"]["by_evidence_kind"]
    total = len(nodes)
    L: List[str] = []
    L.append(f"# 战役主计划节点状态摘要 —— {doc['plan_id']} {doc.get('plan_title') or ''}".rstrip())
    L.append("")
    L.append(
        f"> 机器可验状态层 `campaign_nodes`（SQLite） · 生成时钟 `{doc['generated_utc']}`（内容派生，非 wall clock）"
        f" · 计划 sha256 `{(doc['plan'].get('sha256') or '')[:16]}…` · schema `{doc['schema_version']}`"
    )
    L.append("")
    L.append("## 1. 总览")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(
        f"| 节点数（计划 / 状态层） | {doc['reconciliation']['plan_nodes']} / "
        f"{doc['reconciliation']['status_nodes']}（差 {doc['reconciliation']['diff']}） |"
    )
    for s in STATUS_DOMAIN:
        L.append(f"| status=`{s}`（{STATUS_CN[s]}） | {counts[s]} / {total} |")
    L.append(
        f"| 有证据节点 | {doc['coverage']['with_evidence']} / {total}"
        f"（{doc['coverage']['with_evidence_ratio'] * 100:.1f}%） |"
    )
    L.append(
        f"| evidence_kind=report / mission / commit / none | {kinds['report']} / "
        f"{kinds['mission']} / {kinds['commit']} / {kinds['none']} |"
    )
    L.append(f"| 悬空引用 | {doc['reconciliation']['dangling_count']} |")
    cache_meta = doc["inputs"].get("cache", {}) or {}
    if cache_meta.get("reports_offset_ignored"):
        L.append(
            f"| reports API 分页 | 服务端忽略 offset，只能取到最新 "
            f"{doc['inputs']['reports_raw']} 条（total={cache_meta.get('reports_total_reported')}）："
            f"更早报告不在证据池内 |"
        )
    L.append(
        f"| 未归并 missions / reports | {doc['inputs']['unassigned_missions']} / "
        f"{doc['inputs']['unassigned_reports']} |"
    )
    L.append("")
    L.append("## 2. 节点状态表（逐节点）")
    L.append("")
    L.append(
        "| # | node | milestone | layer | risk | status | evidence_kind | evidence_ref | 证据数 | status_rule | merge_rule | 标题 |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, row in enumerate(nodes, 1):
        L.append(
            "| {i} | `{nid}` | {ms} | {ly} | {rk} | **{st}** | {ek} | {ref} | {cnt} | `{rule}` | `{mrule}` | {title} |".format(
                i=i, nid=row["node_id"], ms=row["milestone"], ly=row["layer"], rk=row["risk"],
                st=row["status"], ek=row["evidence_kind"], ref=row["evidence_ref"] or "—",
                cnt=row["evidence_count"], rule=row["match_rule"], mrule=row["merge_rule"],
                title=(row["title"] or "").replace("|", "\\|"),
            )
        )
    L.append("")
    L.append("## 3. 里程碑 × 状态 交叉表")
    L.append("")
    L.append("| 里程碑 | " + " | ".join(STATUS_DOMAIN) + " | 合计 |")
    L.append("|---" * (len(STATUS_DOMAIN) + 2) + "|")
    for ms, row in doc["coverage"]["by_milestone"].items():
        cells = [str(row.get(s, 0)) for s in STATUS_DOMAIN]
        L.append(f"| {ms} | " + " | ".join(cells) + f" | {sum(row.values())} |")
    L.append("")
    L.append("## 4. 归并规则与局限")
    L.append("")
    mr = doc["merge_rules"]
    L.append(
        f"- 阈值 {mr['threshold']}：主播种节点号槽位 `|F01|` +{mr['node_id_primary_weight']}、"
        f"正文节点号 +{mr['node_id_weight']}、长标识符指纹 "
        f"+{mr['fingerprint_weight']}、里程碑标签 +{mr['milestone_weight']}、稀有判别词项 (df≤2) "
        f"+{mr['term_weight_rare']}、一般判别词项 +{mr['term_weight_common']}"
        f"（df ≤ {mr['df_cap']}）；归属优先级 节点号 > 指纹 > 词项，同档按 node_id 字典序。"
    )
    L.append(
        f"- 自排除 `{mr['self_exclude_patterns']}` 与盲评标签 `{mr['blind_period_tag']}` 不参与归并。"
    )
    strong_n = sum(1 for r in nodes if r["evidence_strong"] > 0)
    L.append(
        f"- 证据强度分布：有强证据（节点号/指纹）的节点 {strong_n}/{total}；"
        f"逐条证据 {kinds.get('report', 0)} 条报告类、"
        f"{len(doc['nodes']) and sum(n['evidence_count'] for n in nodes)} 条证据记录"
        f"（明细在 SQLite `campaign_evidence`，JSON 只留每条节点前 "
        f"{doc.get('evidence_detail_cap', 8)} 条）。"
    )
    L.append(
        "- **局限（须声明）**：**关键词-only 归并的误配率仍未量化**（无人工 golden set，"
        "precision/recall 未知，状态=WITHHELD）。可机器复算的只有「主播种槽位 `|Fxx|` 子集」上的"
        "优先档位校验（见 `tests/test_campaign_nodes.py::"
        "test_real_artifact_labelled_subset_has_zero_misassignment`）；该标注真值与规则同源，"
        "故它证明的是「节点号不被词项分数反超」，**不等于**独立精度。"
        "`keyword_terms`、`keyword_terms+tie`、`via_mission_link` 三档仍最弱，需人工抽查。"
    )
    L.append(
        f"- **局限**：`verified` 计 {counts['verified']}/{total} 是平台现状的函数——"
        "missions API 全量里没有任何 `accepted` 态（裁决不入库），"
        "故「已验讫」在本层结构性地取不到；`succeeded` 只作「已交付未裁决」→ in_progress。"
    )
    L.append(
        "- **局限**：`not_started` 只代表「API 无可见证据」，不等于确实未开工；主计划 milestone 的 "
        "status 字段（in_progress/planned）是规划意图，不作为节点证据。"
    )
    L.append("")
    L.append("## 5. 复算方式")
    L.append("")
    L.append("```bash")
    L.append("python3 tools/campaign_nodes.py fetch --cache artifacts/campaign_evidence_cache.json")
    L.append("python3 tools/campaign_nodes.py build \\")
    L.append("  --plan artifacts/m611_master_plan.json --cache artifacts/campaign_evidence_cache.json \\")
    L.append("  --out-json artifacts/campaign_status.json --out-md docs/campaign_status_m611.md \\")
    L.append("  --out-db artifacts/campaign_nodes.db")
    L.append("python3 tools/campaign_nodes.py sql --db artifacts/campaign_nodes.db")
    L.append("```")
    L.append("")
    return "\n".join(L)


def render_coverage_figure(doc: Dict[str, Any], png_path: str | Path) -> Optional[str]:
    """覆盖率图（三联）：逐节点强/弱证据、里程碑×状态、evidence_kind 覆盖。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # pragma: no cover
        return None

    colors = {"not_started": "#d9d9d9", "in_progress": "#4e79a7",
              "verified": "#59a14f", "blocked": "#e15759"}
    nodes = doc["nodes"]
    fig = plt.figure(figsize=(15.5, 6.4))
    gs = fig.add_gridspec(1, 3, width_ratios=[2.1, 1.0, 0.85], wspace=0.32)

    # (a) 逐节点：强证据（实心）/弱证据（浅色）堆叠，标签写 status
    ax = fig.add_subplot(gs[0, 0])
    labels = [f"{r['node_id']}" for r in nodes]
    strong = [r.get("evidence_strong", 0) for r in nodes]
    weak = [max(0, r["evidence_count"] - r.get("evidence_strong", 0)) for r in nodes]
    y = list(range(len(nodes)))
    ax.barh(y, strong, color=[colors.get(r["status"], "#888") for r in nodes],
            edgecolor="black", linewidth=0.4, label="strong evidence (node id / fingerprint)")
    ax.barh(y, weak, left=strong, color=[colors.get(r["status"], "#888") for r in nodes],
            alpha=0.32, edgecolor="none", label="weak evidence (term overlap)")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("evidence records per node")
    ax.set_title("(a) Evidence per node — solid = strong, faded = weak\n"
                 "color = status (plan %s, N=%d)" % (doc["plan_id"], len(nodes)), fontsize=9)
    ax.legend(fontsize=7, frameon=False, loc="lower right")

    # (b) 里程碑 × 状态
    ax2 = fig.add_subplot(gs[0, 1])
    by_ms = doc["coverage"]["by_milestone"]
    ms_keys = list(by_ms.keys())
    bottoms = [0] * len(ms_keys)
    for st in STATUS_DOMAIN:
        vals = [by_ms[ms].get(st, 0) for ms in ms_keys]
        ax2.bar(ms_keys, vals, bottom=bottoms, color=colors[st], label=st, edgecolor="white")
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    for i, tot in enumerate(bottoms):
        ax2.text(i, tot + 0.05, str(tot), ha="center", va="bottom", fontsize=8)
    ax2.set_ylabel("node count")
    ax2.set_title("(b) Status by milestone", fontsize=9)
    ax2.legend(fontsize=7, frameon=False)
    ax2.set_ylim(0, max(bottoms + [1]) + 1)

    # (c) evidence_kind
    ax3 = fig.add_subplot(gs[0, 2])
    kinds = doc["counts"]["by_evidence_kind"]
    kind_labels = list(EVIDENCE_KINDS)
    vals = [kinds[k] for k in kind_labels]
    ax3.bar(kind_labels, vals, color=["#59a14f", "#4e79a7", "#b07aa1", "#d9d9d9"],
            edgecolor="white")
    for i, v in enumerate(vals):
        ax3.text(i, v + 0.05, str(v), ha="center", va="bottom", fontsize=8)
    ax3.set_ylabel("node count")
    ax3.set_title("(c) evidence_kind\n(dangling refs=%d)"
                  % doc["reconciliation"]["dangling_count"], fontsize=9)
    ax3.set_ylim(0, max(vals + [1]) + 1)

    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return str(png_path)


# --------------------------------------------------------------------------- #
# 取数（唯一联网步骤，只读 GET）
# --------------------------------------------------------------------------- #
def _http_json(url: str, token: Optional[str] = None, payload: Optional[dict] = None) -> Any:
    data = None
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if payload is not None else "GET"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


MISSION_FIELDS = (
    "id", "status", "created_at", "campaign_id", "campaign_phase", "repo", "model",
    "parent_mission_id", "session_id",
)
REPORT_FIELDS = ("id", "mission_id", "title", "summary", "scenario_key", "tags", "created_at")


def _project_mission(m: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: m.get(k) for k in MISSION_FIELDS}
    row["goal"] = _norm_text(m.get("goal"))[:900]
    row["result"] = _norm_text(m.get("result"))[:300]
    return row


def _project_report(r: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: r.get(k) for k in REPORT_FIELDS}
    row["summary"] = _norm_text(r.get("summary"))[:500]
    return row


def fetch_pool(
    base: str,
    user: str,
    password: str,
    page_size: int = 200,
    max_items: int = 20000,
) -> Dict[str, Any]:
    base = base.rstrip("/")
    login = _http_json(base + "/auth/login", payload={"username": user, "password": password})
    token = login.get("access_token") or login.get("token")
    if not token:
        raise RuntimeError("login returned no token")

    def drain(path: str, projector, id_key: str = "id"):
        out: List[Dict[str, Any]] = []
        seen = set()
        offset = 0
        total = None
        offset_ignored = False
        while True:
            q = urllib.parse.urlencode({"limit": page_size, "offset": offset})
            d = _http_json(f"{base}{path}?{q}", token=token)
            if isinstance(d, list):
                items = d
                total = len(d)
            else:
                items = d.get("items") or []
                total = d.get("total", len(items))
            new = 0
            for it in items:
                key = str(it.get(id_key))
                if key in seen:
                    continue
                seen.add(key)
                out.append(projector(it))
                new += 1
            if items and new == 0:
                # 服务端忽略 offset：再翻也是同一页 → 停手并如实记录（不假装取全）
                offset_ignored = True
                break
            offset += len(items)
            if not items or offset >= int(total or 0) or offset >= max_items:
                break
        if not offset_ignored:
            # 探针：total 可能只是服务端截断上限。offset >= total 仍返回条目 =
            # 服务端把末页重复给你 → 更早的记录取不到，如实标记。
            q = urllib.parse.urlencode({"limit": page_size, "offset": offset})
            d = _http_json(f"{base}{path}?{q}", token=token)
            probe = d if isinstance(d, list) else (d.get("items") or [])
            if probe:
                offset_ignored = True
        return out, int(total or len(out)), offset_ignored

    missions, mt, m_ignored = drain("/tensor/missions", _project_mission)
    reports, rt, r_ignored = drain("/tensor/reports", _project_report)
    return {
        "cache_note": "fetch is the only networked step; cache is metadata-only (no report bodies)",
        "api_base": base,
        "missions_total_reported": mt,
        "reports_total_reported": rt,
        "missions_offset_ignored": m_ignored,
        "reports_offset_ignored": r_ignored,
        "missions": missions,
        "reports": reports,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_cache(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"missions": [], "reports": [], "missions_total_reported": 0,
                "reports_total_reported": 0}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_build(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = load_plan(plan_path)
    plan["_sha256"] = file_sha256(plan_path)
    plan["_source_name"] = plan_path.name
    plan_id = _norm_text(plan.get("mission_id"))
    cache = _load_cache(args.cache)
    pool = prepare_evidence_pool(cache, plan_mission_id=plan_id)
    doc = build_status(plan, pool, plan_mission_id=plan_id)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(dumps_json(doc), encoding="utf-8")

    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(render_markdown(doc), encoding="utf-8")
    if args.out_db:
        write_status(args.out_db, doc)
    if args.out_figure:
        render_coverage_figure(doc, args.out_figure)

    rec = doc["reconciliation"]
    print(
        f"[campaign_nodes] plan={doc['plan_id']} nodes={rec['status_nodes']}/{rec['plan_nodes']} "
        f"diff={rec['diff']} status={doc['counts']['by_status']} "
        f"kinds={doc['counts']['by_evidence_kind']} dangling={rec['dangling_count']}"
    )
    return 0 if (rec["diff"] == 0 and rec["dangling_count"] == 0) else 2


def cmd_fetch(args: argparse.Namespace) -> int:
    base = args.api_base or os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = args.user or os.environ.get("TENSOR_AGENT_USER", "")
    password = args.password or os.environ.get("TENSOR_AGENT_PASS", "")
    if not user or not password:
        print("ERROR: TENSOR_AGENT_USER / TENSOR_AGENT_PASS not set", file=sys.stderr)
        return 2
    pool = fetch_pool(base, user, password, page_size=args.page_size)
    out = Path(args.cache)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(pool, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"[campaign_nodes] cached missions={len(pool['missions'])}/{pool['missions_total_reported']} "
        f"reports={len(pool['reports'])}/{pool['reports_total_reported']} -> {out}"
    )
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    rows = read_status_counts(args.db, args.plan_id)
    print("plan_id | status | count")
    for pid, st, c in rows:
        print(f"{pid} | {st} | {c}")
    print(f"# total nodes = {sum(c for _, _, c in rows)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="campaign_nodes: 战役主计划节点状态层")
    sub = ap.add_subparsers(dest="cmd", required=True)

    fp = sub.add_parser("fetch", help="拉 missions/reports 元数据到本地缓存（只读）")
    fp.add_argument("--cache", default="artifacts/campaign_evidence_cache.json")
    fp.add_argument("--api-base", default=None)
    fp.add_argument("--user", default=None)
    fp.add_argument("--password", default=None)
    fp.add_argument("--page-size", type=int, default=200)
    fp.set_defaults(func=cmd_fetch)

    bp = sub.add_parser("build", help="建库 + 回填 + 出 JSON/markdown/图")
    bp.add_argument("--plan", default="artifacts/m611_master_plan.json")
    bp.add_argument("--cache", default="artifacts/campaign_evidence_cache.json")
    bp.add_argument("--out-json", default="artifacts/campaign_status.json")
    bp.add_argument("--out-md", default=None)
    bp.add_argument("--out-db", default=None)
    bp.add_argument("--out-figure", default=None)
    bp.set_defaults(func=cmd_build)

    sp = sub.add_parser("sql", help="打印 plan_id/status/count(*) 验收查询")
    sp.add_argument("--db", default="artifacts/campaign_nodes.db")
    sp.add_argument("--plan-id", default="m611")
    sp.set_defaults(func=cmd_sql)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
