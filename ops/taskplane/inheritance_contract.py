#!/usr/bin/env python3
"""谱系继承契约机检（lineage inheritance contract）v1。

把战役播种子句「本波继承父波局限节与 KPI 缺口」从**文字声明**变成**可复算门**：

1. **提取**：父计划的 honest-FAIL / KPI 缺口 / 已知局限，全部落成结构化 gap 记录
   （每条带 kind / severity / 溯源 pointer / 稳定 gap_id），一条不漏；
2. **覆盖矩阵**：父缺口 × 子节点验收判据，机检「子波是否有判据重新度量该缺口」，
   证据分两级——`explicit`（子节点 metadata ``covers_gaps`` 显式引用 gap_id）与
   `lexical`（判据文本与该缺口共享 metric 锚点 + 词覆盖达标），
   **判据必须机器可算**（含比较算符/数值锚），散文覆盖不算覆盖；
3. **播种拦截**：blocking 级缺口未覆盖 → 拦截播种（返回非空 reasons）；
   warn 级缺口未覆盖 → 记风险不拦截；
4. **可复算**：纯函数 + 规范序列化 sha256 digest，同一输入逐字节同结果，
   与节点输入顺序无关（矩阵行按 gap_id / ext_id 排序）。

设计纪律（TensorPlatform 铁律）：**零领域语义**——本模块不认识任何船型/物理量名，
只做通用的 gap→判据覆盖裁决；哪些键算缺口、哪些 kind 算阻塞、词覆盖阈值全部由
config/Metadata 注入（见 ``DEFAULT_CONFIG``）。合成数据测试见
``tests/test_inheritance_contract.py``；契约文档 ``docs/specs/inheritance_gap_contract.md``。

输入载荷（离线快照，纯 JSON）：

    {"plan":  {"id": 12, "status": "active", "metadata": {...}},
     "nodes": [{"id": 101, "status": "failed", "ext_id": "P12-N1",
                "milestone_id": 3, "metadata": {"goal": "...", "acceptance_criteria": [...]}}]}

CLI::

    python -m ops.taskplane.inheritance_contract \
        --parent parent.json --child child.json --json-out out.json --md-out out.md
    # 退出码：0 = pass/warn（不拦截），2 = block（拦截播种），3 = 输入错误
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA = "inheritance_coverage_matrix/v1"
GATE_SCHEMA = "inheritance_seeding_gate/v1"

# metadata 键 → gap kind（顺序即提取顺序，保证确定性）
GAP_KEY_KINDS: tuple[tuple[str, str], ...] = (
    ("honest_fail", "honest_fail"),
    ("honest_fails", "honest_fail"),
    ("kpi_gap", "kpi_gap"),
    ("kpi_gaps", "kpi_gap"),
    ("inheritance_limitations", "limitation"),
    ("known_limitations", "limitation"),
    ("limitations", "limitation"),
    ("debts", "limitation"),
)

# 单条 gap 内部字段别名（写作者可用任意一种）
_TEXT_FIELDS = ("text", "description", "statement", "gap", "summary", "value_text")
_METRIC_FIELDS = ("metric", "quantity", "key")
_EXPLICIT_COVER_KEYS = ("covers_gaps", "inherits_gaps", "covered_gaps")

# goal 正文里的「局限节」标题（【已知局限】/【继承局限】/【未完成】…）
_HEADING_RE = re.compile(r"【([^】]{2,16})】")
_LIMITATION_HEADING_WORDS = ("局限", "未偿", "欠账", "未完成", "缺口", "FAIL")
_HONEST_FAIL_HEADING_WORDS = ("FAIL", "未完成")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")
_NUM_RE = re.compile(r"\d")
_CMP_RE = re.compile(r"(<=|>=|≤|≥|<|>|=|±)")
_NUMERIC_ANCHOR_RE = re.compile(r"(\d+(?:\.\d+)?\s*%|误差|残差|准确率|覆盖率|偏差|通过)")

DEFAULT_CONFIG: dict[str, Any] = {
    # 覆盖判定
    "min_token_coverage": 0.5,      # |gap∩判据| / |gap 词| 下限
    "min_shared_terms": 2,          # 至少共享几个词（防单词巧合）
    "anchor_coverage": 0.5,         # gap 的 latin/指标锚点被判据覆盖的比例下限（且至少 1 个）
    "require_machine_checkable": True,  # 判据必须含算符/数值锚，散文不算覆盖
    "include_goal": False,          # 词覆盖只看 acceptance_criteria（任务书散文不顶判据）
    # 严重度 → 是否拦截
    "severity_by_kind": {
        "honest_fail": "block",
        "kpi_gap": "block",
        "limitation": "warn",
        "status_failed": "warn",
        "status_blocked": "warn",
    },
    "blocking_severities": ["block"],
    # 从节点状态派生缺口（父波有 failed/blocked 节点而没写局限 = 欠账没记账，也要揪出来）
    "derive_from_status": True,
    "status_kinds": {"failed": "status_failed", "blocked": "status_blocked"},
    # 未验收节点（failed/blocked）的局限 = 未偿欠账而非「适用范围声明」→ 升格为阻塞级
    "escalate_unaccepted_gaps": True,
    "unaccepted_statuses": ["failed", "blocked"],
    "escalated_severity": "block",
    "max_goal_section_chars": 600,
    "max_gap_text_chars": 400,
}


# ─────────────────────────── 基础工具 ───────────────────────────

def norm_text(text: Any) -> str:
    """归一化文本：去首尾空白、压缩空白（digest / 去重用）。"""
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _merge_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in dict(config or {}).items():
        if isinstance(v, Mapping) and isinstance(cfg.get(k), Mapping):
            cfg[k] = {**dict(cfg[k]), **dict(v)}
        else:
            cfg[k] = v
    return cfg


def terms(text: Any) -> set[str]:
    """词袋：latin 词（≥3 字符）+ 中文二元组（去纯数字）。确定性、无外部依赖。"""
    s = str(text if text is not None else "").lower()
    out: set[str] = set(_LATIN_RE.findall(s))
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


# 通用工程词（非指标名）：不能当覆盖锚点，否则 "pytest/api/json" 会把任何判据算成覆盖
ANCHOR_STOP = {"pytest", "tests", "test", "api", "json", "csv", "http", "https", "none",
               "true", "false", "null"}


def anchor_terms(text: Any) -> set[str]:
    """锚点词：latin 指标名/变量名（≥4 字符，可含下划线/数字）——覆盖必须命中锚点。"""
    s = str(text if text is not None else "").lower()
    anchors = {t for t in _LATIN_RE.findall(s) if len(t) >= 4 and t not in ANCHOR_STOP}
    return anchors


def machine_checkable(criterion: Any) -> bool:
    """判据是否机器可算：含数值锚 + 比较算符（或百分比/误差等量化词）。

    只有机器可算的判据才允许「词覆盖」记成覆盖——散文提到指标名但无阈值，
    等于没有判据（历史上「有理解无判据」的假通过就出在这里）。
    """
    s = str(criterion if criterion is not None else "")
    if not _NUM_RE.search(s):
        return False
    if _CMP_RE.search(s):
        return True
    return bool(_NUMERIC_ANCHOR_RE.search(s))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s[0] in "[{":
            try:
                parsed = json.loads(s)
            except ValueError:
                return [value]
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, Mapping):
                return [parsed]
            return [parsed]
        return [value]
    return [value]


def _join_texts(items: Iterable[Any]) -> str:
    parts: list[str] = []
    for it in items:
        if isinstance(it, Mapping):
            for f in _TEXT_FIELDS:
                if it.get(f):
                    parts.append(str(it[f]))
                    break
        elif it is not None:
            parts.append(str(it))
    return " ".join(parts)


# ─────────────────────────── 缺口提取 ───────────────────────────

def _gap_text_of(item: Any) -> str:
    if isinstance(item, Mapping):
        for f in _TEXT_FIELDS:
            if item.get(f):
                return norm_text(item[f])
        return norm_text(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return norm_text(item)


def _gap_metric_of(item: Any) -> str | None:
    if isinstance(item, Mapping):
        for f in _METRIC_FIELDS:
            if item.get(f):
                return norm_text(item[f])
    return None


def _new_gap(kind: str, text: str, source: str, *, metric: str | None = None,
             severity: str | None = None, alias: str | None = None,
             detail: Mapping[str, Any] | None = None, cfg: Mapping[str, Any] | None = None) -> dict:
    cfg = cfg or DEFAULT_CONFIG
    text = norm_text(text)[: int(cfg.get("max_gap_text_chars", 400))]
    base = f"{kind}|{text}"
    gid = f"{kind}-{hashlib.sha256(base.encode('utf-8')).hexdigest()[:10]}"
    gap = {
        "gap_id": gid,
        "kind": kind,
        "severity": severity or cfg["severity_by_kind"].get(kind, "warn"),
        "text": text,
        "metric": metric,
        "sources": [source],
        "alias": norm_text(alias) if alias else None,
        "detail": dict(detail or {}),
    }
    return gap


def _escalate(gap: dict, node: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict:
    """未验收节点（failed/blocked）的缺口 → 升格为阻塞级，并留痕 escalation 理由。"""
    if not cfg.get("escalate_unaccepted_gaps"):
        return gap
    status = str(node.get("status") or "")
    if status not in (cfg.get("unaccepted_statuses") or []):
        return gap
    target = cfg.get("escalated_severity") or "block"
    order = {"warn": 0, "block": 1}
    if order.get(target, 1) <= order.get(gap["severity"], 0):
        return gap
    gap = dict(gap)
    gap["severity"] = target
    gap["escalated_from"] = {"severity": "warn", "reason": f"node_not_accepted:{status}"}
    gap["sources"] = list(gap["sources"])
    return gap


def _goal_sections(goal: Any, cfg: Mapping[str, Any]) -> list[tuple[str, str]]:
    """把 goal 正文按【标题】切段，返回 [(标题, 正文)]；无标题返回 []。"""
    text = str(goal or "")
    marks = list(_HEADING_RE.finditer(text))
    if not marks:
        return []
    out: list[tuple[str, str]] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = norm_text(text[m.end():end])[: int(cfg.get("max_goal_section_chars", 600))]
        out.append((m.group(1), body))
    return out


def extract_gaps(payload: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[dict]:
    """从父计划载荷里 100% 提取未偿缺口（去重合并，逐条可溯源）。

    提取面（四路，全部落账，不做取舍）：
      A. plan.metadata 的缺口键（GAP_KEY_KINDS）；
      B. 每个 node.metadata 的缺口键；
      C. node.metadata.goal 正文的「局限节」（【已知局限】/【继承局限】/【未完成】…）；
      D. 派生：node.status ∈ status_kinds 且没有任何显式缺口声明 → 记一条状态缺口
         （父波有 failed/blocked 节点却没写局限 = 欠账没记账，不能静默）。
    """
    cfg = _merge_config(config)
    raw: list[dict] = []

    plan = dict(payload.get("plan") or {})
    pmeta = dict(plan.get("metadata") or {})
    pid = plan.get("id")
    for key, kind in GAP_KEY_KINDS:
        if key not in pmeta:
            continue
        for idx, item in enumerate(_as_list(pmeta.get(key))):
            text = _gap_text_of(item)
            if not text:
                continue
            raw.append(_new_gap(kind, text, f"plan:{pid}.metadata.{key}[{idx}]",
                                metric=_gap_metric_of(item),
                                alias=(item.get("id") if isinstance(item, Mapping) else None),
                                detail=(item if isinstance(item, Mapping) else None), cfg=cfg))

    nodes = sorted(payload.get("nodes") or [], key=lambda n: int(n.get("id") or 0))
    for node in nodes:
        nid = node.get("id")
        ext = node.get("ext_id") or (node.get("metadata") or {}).get("ext_id") or f"node{nid}"
        nmeta = dict(node.get("metadata") or {})
        declared = False
        for key, kind in GAP_KEY_KINDS:
            if key not in nmeta:
                continue
            for idx, item in enumerate(_as_list(nmeta.get(key))):
                if isinstance(item, Mapping) and item.get("text") in (None, "", False) \
                        and not any(item.get(f) for f in _TEXT_FIELDS):
                    # honest_fail: true 之类布尔旗标 → 用 fail_reason/goal 兜底成文
                    item = nmeta.get("fail_reason") or nmeta.get("honest_fail_reason") or \
                        f"{ext} 声明 {key} 但未写原因"
                text = _gap_text_of(item)
                if not text:
                    continue
                declared = True
                raw.append(_escalate(_new_gap(kind, text, f"node:{nid}.metadata.{key}[{idx}]",
                                              metric=_gap_metric_of(item),
                                              alias=(item.get("id") if isinstance(item, Mapping) else None),
                                              detail=(item if isinstance(item, Mapping) else None),
                                              cfg=cfg), node, cfg))
        # C. goal 局限节
        for heading, body in _goal_sections(nmeta.get("goal"), cfg):
            if not body:
                continue
            if not any(w.lower() in heading.lower() for w in _LIMITATION_HEADING_WORDS):
                continue
            kind = "honest_fail" if any(w.lower() in heading.lower()
                                        for w in _HONEST_FAIL_HEADING_WORDS) else "limitation"
            declared = True
            raw.append(_escalate(_new_gap(kind, f"{heading}：{body}",
                                          f"node:{nid}.metadata.goal#【{heading}】", cfg=cfg),
                                 node, cfg))
        # D. 状态派生
        status = str(node.get("status") or "")
        if cfg.get("derive_from_status") and status in (cfg.get("status_kinds") or {}) and not declared:
            kind = cfg["status_kinds"][status]
            reason = (nmeta.get("fail_reason") or nmeta.get("honest_fail_reason")
                      or nmeta.get("blocked_reason") or "")
            if isinstance(reason, (list, tuple)):
                reason = _join_texts(reason)
            text = norm_text(reason) or f"{ext} 状态 {status}，无书面局限声明（欠账未记账）"
            raw.append(_new_gap(kind, text, f"node:{nid}.status={status}", cfg=cfg))

    # 去重合并（kind+text），sources 合并排序
    merged: dict[str, dict] = {}
    for g in raw:
        key = g["gap_id"]
        if key in merged:
            src = g["sources"][0]
            if src not in merged[key]["sources"]:
                merged[key]["sources"].append(src)
            if not merged[key].get("metric") and g.get("metric"):
                merged[key]["metric"] = g["metric"]
        else:
            merged[key] = g
    out = list(merged.values())
    for g in out:
        g["sources"] = sorted(g["sources"])
    out.sort(key=lambda g: (g["severity"] != "block", g["kind"], g["gap_id"]))
    return out


# ─────────────────────────── 覆盖矩阵 ───────────────────────────

def _node_cover_text(node: Mapping[str, Any], cfg: Mapping[str, Any]) -> str:
    meta = dict(node.get("metadata") or {})
    crits = _as_list(meta.get("acceptance_criteria"))
    parts = [str(c) if not isinstance(c, Mapping) else _gap_text_of(c) for c in crits]
    if cfg.get("include_goal"):
        parts.append(str(meta.get("goal") or ""))
    return " ".join(p for p in parts if p)


def _explicit_refs(node: Mapping[str, Any]) -> list[str]:
    meta = dict(node.get("metadata") or {})
    refs: list[str] = []
    for key in _EXPLICIT_COVER_KEYS:
        for it in _as_list(meta.get(key)):
            if isinstance(it, Mapping):
                for f in ("id", "gap_id", "text"):
                    if it.get(f):
                        refs.append(norm_text(it[f]))
            elif it is not None:
                refs.append(norm_text(it))
    return [r for r in refs if r]


def _node_label(node: Mapping[str, Any]) -> tuple[str, str]:
    meta = dict(node.get("metadata") or {})
    ext = str(node.get("ext_id") or meta.get("ext_id") or f"node{node.get('id')}")
    return ext, str(node.get("id"))


def _match_gap_node(gap: Mapping[str, Any], node: Mapping[str, Any],
                    cfg: Mapping[str, Any]) -> dict:
    """机检一个缺口在某一子节点上的覆盖证据（explicit > lexical，逐判据取最优）。"""
    ext, nid = _node_label(node)
    # 1) 显式引用
    refs = _explicit_refs(node)
    if gap["gap_id"] in refs or (gap.get("alias") and gap["alias"] in refs):
        return {"node_id": nid, "ext_id": ext, "level": "explicit",
                "criterion": gap["gap_id"], "coverage": 1.0, "shared_terms": [],
                "reason": "covers_gaps 显式引用"}
    # 2) 判据词覆盖
    gtext = gap["text"]
    if gap.get("metric"):
        gtext = f"{gtext} {gap['metric']}"
    gterms = terms(gtext)
    ganch = anchor_terms(gtext)
    if gap.get("metric"):
        ganch |= anchor_terms(gap["metric"])
    crits = _as_list((node.get("metadata") or {}).get("acceptance_criteria"))
    best: dict | None = None
    near: dict | None = None
    for idx, crit in enumerate(crits):
        ctext = crit if not isinstance(crit, Mapping) else _gap_text_of(crit)
        cterms = terms(ctext)
        shared = gterms & cterms
        if not shared or not gterms:
            continue
        cov = len(shared) / len(gterms)
        anchor_hit = len(shared & ganch)
        anchor_cov = (anchor_hit / len(ganch)) if ganch else 1.0
        ev = {"node_id": nid, "ext_id": ext, "criterion": norm_text(ctext),
              "coverage": round(cov, 4), "shared_terms": sorted(shared),
              "anchor_coverage": round(anchor_cov, 4),
              "machine_checkable": machine_checkable(ctext), "criterion_index": idx}
        if ganch:
            # 有指标锚点：判据必须命中锚点（锚点覆盖≥阈值且至少 1 个）——散文无法顶判据
            ok = (anchor_hit >= 1 and anchor_cov >= cfg["anchor_coverage"]
                  and len(shared) >= min(cfg["min_shared_terms"], len(gterms), 1)
                  and cov > 0)
        else:
            # 无数值/指标锚点的纯文字缺口：靠中文二元组覆盖率（阈值更高，防巧合）
            ok = cov >= cfg["min_token_coverage"] and len(shared) >= cfg["min_shared_terms"]
        if ok and cfg.get("require_machine_checkable") and not ev["machine_checkable"]:
            ev = {**ev, "reason": "criterion_not_machine_checkable"}
            ok = False
        if ok:
            if best is None or ev["coverage"] > best["coverage"]:
                best = {**ev, "level": "lexical"}
        else:
            reason = ev.get("reason") or ("anchor_not_covered" if anchor_cov < cfg["anchor_coverage"]
                                          else "token_coverage_below_threshold")
            ev = {**ev, "reason": reason}
            if near is None or ev["coverage"] > near["coverage"]:
                near = ev
    if best:
        return best
    if near:
        return {**near, "level": "miss"}
    return {"node_id": nid, "ext_id": ext, "level": "none", "criterion": None,
            "coverage": 0.0, "shared_terms": [], "reason": "no_shared_terms"}


def build_coverage_matrix(gaps: Sequence[Mapping[str, Any]], child: Mapping[str, Any],
                          config: Mapping[str, Any] | None = None) -> dict:
    """父缺口 × 子节点判据的覆盖矩阵（确定性：行按 gap 排序、列按 ext_id/id 排序）。"""
    cfg = _merge_config(config)
    nodes = sorted(child.get("nodes") or [], key=lambda n: (str(_node_label(n)[0]), int(n.get("id") or 0)))
    plan = dict(child.get("plan") or {})
    rows: list[dict] = []
    for gap in gaps:
        covered_by: list[dict] = []
        near_misses: list[dict] = []
        for node in nodes:
            ev = _match_gap_node(gap, node, cfg)
            if ev["level"] in ("explicit", "lexical"):
                covered_by.append(ev)
            elif ev["level"] == "miss":
                near_misses.append(ev)
        covered = bool(covered_by)
        blocking = gap["severity"] in (cfg.get("blocking_severities") or [])
        rows.append({
            "gap_id": gap["gap_id"],
            "kind": gap["kind"],
            "severity": gap["severity"],
            "text": gap["text"],
            "metric": gap.get("metric"),
            "sources": list(gap.get("sources") or []),
            "status": "covered" if covered else "uncovered",
            "covered_by": covered_by,
            "near_misses": near_misses[:3],
            "intercept": bool(blocking and not covered),
        })
    n_gaps = len(rows)
    n_cov = sum(1 for r in rows if r["status"] == "covered")
    matrix = {
        "schema": SCHEMA,
        "config_echo": {
            "min_token_coverage": cfg["min_token_coverage"],
            "min_shared_terms": cfg["min_shared_terms"],
            "anchor_coverage": cfg["anchor_coverage"],
            "require_machine_checkable": cfg["require_machine_checkable"],
            "include_goal": cfg["include_goal"],
            "blocking_severities": list(cfg["blocking_severities"]),
            "derive_from_status": cfg["derive_from_status"],
            "escalate_unaccepted_gaps": cfg["escalate_unaccepted_gaps"],
        },
        "parent": {"id": plan.get("id"), "gaps_total": n_gaps},
        "child": {"id": plan.get("id"), "nodes_total": len(nodes),
                  "node_index": [{"ext_id": _node_label(n)[0], "node_id": _node_label(n)[1],
                                  "criteria_declared": len(_as_list((n.get("metadata") or {})
                                                                    .get("acceptance_criteria")))}
                                 for n in nodes]},
        "rows": rows,
        "summary": {
            "n_gaps": n_gaps,
            "n_covered": n_cov,
            "n_uncovered": n_gaps - n_cov,
            "n_intercepted": sum(1 for r in rows if r["intercept"]),
            "coverage_ratio": round(n_cov / n_gaps, 4) if n_gaps else None,
            "by_kind": {},
        },
    }
    by_kind: dict[str, dict] = {}
    for r in rows:
        slot = by_kind.setdefault(r["kind"], {"n": 0, "covered": 0})
        slot["n"] += 1
        slot["covered"] += 1 if r["status"] == "covered" else 0
    matrix["summary"]["by_kind"] = {k: by_kind[k] for k in sorted(by_kind)}
    matrix["digest"] = _digest(matrix)
    return matrix


# ─────────────────────────── 播种门 ───────────────────────────

def evaluate_seeding(parent: Mapping[str, Any], child: Mapping[str, Any],
                     config: Mapping[str, Any] | None = None) -> dict:
    """播种门：父波未偿缺口 → 子波判据覆盖 → 拦截/放行裁决（纯函数，可复算）。"""
    cfg = _merge_config(config)
    gaps = extract_gaps(parent, cfg)
    matrix = build_coverage_matrix(gaps, child, cfg)
    # 矩阵的 parent/child 身份由调用方载荷决定（build_coverage_matrix 只看到 child）
    matrix["parent"] = {"id": (parent.get("plan") or {}).get("id"), "gaps_total": len(gaps)}
    matrix["digest"] = _digest(matrix)
    blockers = [
        {"gap_id": r["gap_id"], "kind": r["kind"], "severity": r["severity"],
         "text": r["text"], "reason": "blocking_gap_uncovered",
         "best_near_miss": (r["near_misses"][0] if r["near_misses"] else None)}
        for r in matrix["rows"] if r["intercept"]
    ]
    warnings = [
        {"gap_id": r["gap_id"], "kind": r["kind"], "severity": r["severity"],
         "text": r["text"], "reason": "nonblocking_gap_uncovered"}
        for r in matrix["rows"] if r["status"] == "uncovered" and not r["intercept"]
    ]
    if blockers:
        verdict = "block"
    elif warnings:
        verdict = "warn"
    else:
        verdict = "pass"
    report = {
        "schema": GATE_SCHEMA,
        "verdict": verdict,
        "intercepted": verdict == "block",
        "parent_plan_id": (parent.get("plan") or {}).get("id"),
        "child_plan_id": (child.get("plan") or {}).get("id"),
        "summary": dict(matrix["summary"]),
        "blockers": blockers,
        "warnings": warnings,
        "matrix": matrix,
        "matrix_digest": matrix["digest"],
        "gaps_digest": _digest(gaps),
        "config_echo": matrix["config_echo"],
    }
    report["digest"] = _digest({k: v for k, v in report.items() if k != "digest"})
    return report


def coverage_heatmap(report: Mapping[str, Any]) -> dict:
    """回放用的矩阵稠密表示：rows=gaps, cols=nodes, 值=最优覆盖度（显式=1.0，miss<阈值）。"""
    matrix = report.get("matrix") or {}
    node_index = [n["ext_id"] for n in (matrix.get("child") or {}).get("node_index", [])]
    grid: list[list[float]] = []
    labels: list[str] = []
    for row in matrix.get("rows") or []:
        ev = {e["ext_id"]: e for e in row["covered_by"]}
        near = {e["ext_id"]: e for e in row["near_misses"]}
        line: list[float] = []
        for ext in node_index:
            if ext in ev:
                line.append(1.0 if ev[ext]["level"] == "explicit" else float(ev[ext]["coverage"]))
            elif ext in near:
                line.append(float(near[ext]["coverage"]))
            else:
                line.append(0.0)
        grid.append(line)
        labels.append(row["gap_id"])
    return {"node_labels": node_index, "gap_labels": labels, "grid": grid}


def seeding_block_reasons(parent: Mapping[str, Any], child: Mapping[str, Any],
                          config: Mapping[str, Any] | None = None) -> tuple[bool, list[str], dict]:
    """播种器接口：返回 (ok, reasons, report)；ok=False 即播种拦截。"""
    report = evaluate_seeding(parent, child, config)
    reasons: list[str] = []
    for b in report["blockers"]:
        reasons.append(f"继承缺口未覆盖(播种拦截): {b['gap_id']}[{b['kind']}] {b['text'][:80]}")
    if report["warnings"]:
        reasons.append(f"继承缺口未覆盖(风险标记 {len(report['warnings'])} 条，不拦截)")
    return (not report["intercepted"]), reasons, report


# ─────────────────────────── 线上载荷 ───────────────────────────

def parse_row_json(rows: Sequence[Sequence[Any]]) -> list[dict]:
    """把 q() 返回的 row_to_json 单列结果解析成 dict 列表。

    用 row_to_json::text 单列而不是 '\\x02' 分隔多列：metadata/goal 文本可含任意字符，
    分隔符解包迟早被文本撑爆（campaign_dispatcher m727 事故教训）。
    """
    out: list[dict] = []
    for r in rows or []:
        if r is None:
            continue
        cell = r[0] if isinstance(r, (list, tuple)) else r
        if cell in (None, ""):
            continue
        if isinstance(cell, Mapping):
            out.append(dict(cell))
            continue
        text = str(cell)
        try:
            out.append(json.loads(text))
        except ValueError:
            # 单列 row_to_json 文本若自身含分隔符会被 q() 切成多段：拼回整行再解析
            if isinstance(r, (list, tuple)) and len(r) > 1:
                out.append(json.loads("\x02".join(str(x) for x in r)))
            else:
                raise
    return out


def fetch_plan_payload(plan_id: int, q: Callable[[str], Sequence[Sequence[Any]]]) -> dict:
    """从平台库取一个计划的机检载荷（只读，不写任何表）。"""
    prow = parse_row_json(q(
        "SELECT row_to_json(t)::text FROM (SELECT id, coalesce(status,'') AS status, "
        "coalesce(metadata,'{}'::jsonb) AS metadata FROM campaign_plans "
        f"WHERE id={int(plan_id)}) t"))
    if not prow:
        raise LookupError(f"plan {plan_id} 不存在")
    plan = prow[0]
    nodes = parse_row_json(q(
        "SELECT row_to_json(t)::text FROM (SELECT id, coalesce(status,'') AS status, "
        "coalesce(milestone_id::text,'') AS milestone_id, coalesce(metadata,'{}'::jsonb) AS metadata, "
        f"coalesce(metadata->>'ext_id','') AS ext_id FROM campaign_nodes WHERE plan_id={int(plan_id)} "
        "ORDER BY id) t"))
    return {"plan": plan, "nodes": nodes}


def parent_plan_id_of(child_payload: Mapping[str, Any]) -> int | None:
    meta = dict(((child_payload.get("plan") or {}).get("metadata")) or {})
    val = meta.get("parent_plan")
    try:
        return int(val) if val not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def seeding_gate_from_db(child_plan_id: int, q: Callable[[str], Sequence[Sequence[Any]]],
                         config: Mapping[str, Any] | None = None,
                         parent_plan_id: int | None = None) -> tuple[bool, list[str], dict]:
    """播种流程钩子：按 child.metadata.parent_plan 自动找父计划并裁决。

    无父计划（或父计划不存在）→ 放行、空矩阵（负控：无缺口不拦截）。
    """
    child = fetch_plan_payload(child_plan_id, q)
    pid = parent_plan_id if parent_plan_id is not None else parent_plan_id_of(child)
    if pid is None or int(pid) == int(child_plan_id):
        return True, [], {"schema": GATE_SCHEMA, "verdict": "skip",
                          "reason": "no_parent_plan", "matrix": {"rows": [], "summary": {}}}
    parent = fetch_plan_payload(int(pid), q)
    return seeding_block_reasons(parent, child, config)


# ─────────────────────────── Markdown ───────────────────────────

def render_markdown(report: Mapping[str, Any]) -> str:
    matrix = report.get("matrix") or {}
    s = matrix.get("summary") or {}
    cfg = report.get("config_echo") or {}
    lines = [
        f"# 谱系继承契约门 · 父 {report.get('parent_plan_id')} → 子 {report.get('child_plan_id')}",
        "",
        f"- 裁决：**{report.get('verdict')}**（intercepted={report.get('intercepted')}）",
        f"- 缺口：{s.get('n_gaps')}；覆盖 {s.get('n_covered')}；未覆盖 {s.get('n_uncovered')}；"
        f"拦截 {s.get('n_intercepted')}；覆盖率 {s.get('coverage_ratio')}",
        f"- 矩阵 digest：`{report.get('matrix_digest')}`；门 digest：`{report.get('digest')}`",
        f"- 覆盖判据：词覆盖≥{cfg.get('min_token_coverage')}、共享词≥{cfg.get('min_shared_terms')}、"
        f"锚点覆盖≥{cfg.get('anchor_coverage')}、判据须机器可算={cfg.get('require_machine_checkable')}",
        "",
        "## 覆盖矩阵",
        "",
        "| gap_id | kind | severity | 状态 | 覆盖节点(级别) | 缺口文本 |",
        "|---|---|---|---|---|---|",
    ]
    for row in matrix.get("rows") or []:
        cov = ", ".join(f"{e['ext_id']}({e['level']})" for e in row["covered_by"]) or "—"
        lines.append(f"| {row['gap_id']} | {row['kind']} | {row['severity']} | {row['status']} | "
                     f"{cov} | {row['text'][:60]} |")
    lines += ["", "## 拦截项", ""]
    lines += [f"- `{b['gap_id']}` [{b['kind']}] {b['text'][:100]}" for b in report.get("blockers") or []] or ["- 无"]
    lines += ["", "## 风险项（不拦截）", ""]
    lines += [f"- `{w['gap_id']}` [{w['kind']}] {w['text'][:100]}" for w in report.get("warnings") or []] or ["- 无"]
    return "\n".join(lines) + "\n"


# ─────────────────────────── CLI ───────────────────────────

def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="谱系继承契约门：父缺口→子判据覆盖矩阵 + 播种拦截")
    ap.add_argument("--parent", help="父计划载荷 JSON")
    ap.add_argument("--child", help="子计划载荷 JSON")
    ap.add_argument("--config", help="门配置 JSON")
    ap.add_argument("--json-out", help="裁决报告 JSON 输出")
    ap.add_argument("--md-out", help="裁决报告 Markdown 输出")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if not (args.parent and args.child):
        print("需要 --parent 与 --child 载荷 JSON", file=sys.stderr)
        return 3
    try:
        parent = _load_json(args.parent)
        child = _load_json(args.child)
        cfg = _load_json(args.config) if args.config else {}
    except (OSError, ValueError) as exc:
        print(f"输入读取失败: {exc}", file=sys.stderr)
        return 3
    report = evaluate_seeding(parent, child, cfg)
    if args.json_out:
        _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.md_out:
        _write(args.md_out, render_markdown(report))
    if not args.quiet:
        print(render_markdown(report))
    return 2 if report["intercepted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())