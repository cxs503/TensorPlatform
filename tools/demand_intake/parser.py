"""parser —— finish 结果 → 候选锚（convention-first，文本宽松扫描兜底）。

约定字段（推荐，一个 JSON 块即可）
----------------------------------
agent 在 ``finish`` 的 ``summary`` / ``checks`` 里贴任意一个 JSON 块：:

    ```json
    {"proposed_anchors": [
       {"slug": "FEAT-SYNTH-DEMO", "title": "…", "domain": "platform",
        "repo": "TensorPlatform", "acceptance": "pytest 全绿且相对偏差 <3%（实测 1.2%）"}
    ]}
    ```

别名/同义字段一并接受：``suggested_anchors`` / ``anchor_proposals`` / ``proposed_features``；
列表元素可以是对象，也可以是字符串（``"FEAT-X — 标题：验收…"``）。
结构化字段可以出现在 result 的任意深度（dispatcher 把 finish 之外的工具结果也塞进
``result``，例如 ``capability_delta``）。

文本扫描兜底（无 JSON 时）
--------------------------
按行扫「锚/anchor 语境行」里的 slug 形状 token；标题取该行 slug 之后的文本，
验收条款取同一行或紧邻 2 行内**确实存在**的量化片段（含数字 + 比较符/百分号）。
一句话：parser 只做「抽取」，**不做裁决**——是否合规范、验收是否量化、是否与既有
锚重复，全部交 :mod:`tools.demand_intake.gate` 判。抽取与裁决分离，是为了让
「垃圾锚拦截红队」能对着 gate 的判据逐条打靶。

诚实边界（未做/做不到，写在这里防误读）
--------------------------------------
- 不做语义去重（不调 embedding）：只看 slug 精确相同 + 标题 token 的 Jaccard 重叠。
  近义改写会漏检，落成 ``pending_review`` 后仍需人/planner 采认。
- 不解析图片/附件；只读 result 的文本字段与 JSON 结构。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .slugs import canonical_slug, is_valid_slug, SLUG_TOKEN_RE

#: 结构化候选字段名（含中英别名）
STRUCTURED_KEYS = (
    "proposed_anchors", "suggested_anchors", "anchor_proposals",
    "proposed_features", "anchor_suggestions", "建议新锚", "新锚建议",
)

#: 候选对象的键别名（值 → 规范字段）
FIELD_ALIASES = {
    "slug": ("slug", "id", "anchor", "锚", "slug_id"),
    "title": ("title", "name", "标题", "headline"),
    "domain": ("domain", "repo", "域", "仓库"),
    "acceptance": ("acceptance", "acceptance_criteria", "criteria", "验收", "验收判据", "验收标准"),
    "repo": ("repo", "repository", "仓库名"),
    "rationale": ("rationale", "why", "reason", "理由", "依据"),
}

#: 参与文本扫描的 result 文本字段（按重要性排序，先出现者优先）
TEXT_FIELDS = ("headline", "summary", "declared_summary", "checks",
               "decision_note", "note", "notes", "next_steps")

#: 「锚」语境词：行内必须命中其一，才把该行的 slug 形状 token 当候选
CONTEXT_WORDS = ("锚", "anchor", "proposal", "提案")

#: 量化验收的机器判据：数字 + (比较符 | 百分号 | 计量词)
_QUANT_NUM_RE = re.compile(r"\d")
_QUANT_OP_RE = re.compile(r"[<>≤≥=%]|以上|以下|不低于|不超过|达到|误差|偏差|覆盖|通过率|条数|项数|小时|分钟")
#: 行内「验收」引导词（提取 acceptance 片段时优先截这里之后的内容）
_ACC_HINT_RE = re.compile(r"(?:验收(?:判据|标准)?|acceptance|判据|门)\s*[:：=]?\s*", re.I)

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.+?)```", re.S)


@dataclass
class Candidate:
    """一条候选锚（parser 产物，未裁决）。"""

    slug_raw: str
    slug: str
    title: str
    acceptance: str = ""
    domain: str = ""
    repo: str = ""
    rationale: str = ""
    source_kind: str = "mission_finish"
    source_ref: str = ""
    src_field: str = ""
    confidence: str = "structured"   # structured | json_in_text | text
    raw: Dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> Dict[str, Any]:
        return {
            "slug": self.slug, "title": self.title, "acceptance": self.acceptance,
            "domain": self.domain or "platform", "repo": self.repo,
            "rationale": self.rationale, "source_kind": self.source_kind,
            "source_ref": self.source_ref, "field": self.src_field,
            "confidence": self.confidence, "slug_raw": self.slug_raw,
            "raw": self.raw,
        }


# ── JSON 抽取 ───────────────────────────────────────────────────────────────
def iter_json_blobs(text: str) -> Iterator[Any]:
    """从文本里抽出所有能 ``json.loads`` 的 JSON 值（fenced 块优先，再平衡括号扫描）。

    只为「把 agent 随手贴的 JSON 捞出来」，不做容错修补（半截 JSON 直接跳过）——
    修补会引入猜测，违背证据纪律。
    """
    if not text:
        return
    bodies: List[str] = [m.group(1) for m in _FENCE_RE.finditer(text)]
    bodies.append(text)  # 裸内联 JSON 也在原文里扫
    seen = set()
    for body in bodies:
        for chunk in _balanced_json_chunks(body):
            if chunk in seen:
                continue
            seen.add(chunk)
            try:
                yield json.loads(chunk)
            except (ValueError, TypeError):
                continue


def _balanced_json_chunks(text: str) -> Iterator[str]:
    """扫出平衡的 ``{...}`` / ``[...]`` 片段（跳过字符串内的括号）。"""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in "{[":
            i += 1
            continue
        depth, in_str, esc = 0, False, False
        j = i
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    break
            j += 1
        i = j + 1 if depth == 0 else i + 1


def _pick(mapping: Dict[str, Any], names: Sequence[str]) -> str:
    for name in names:
        if name in mapping and isinstance(mapping[name], (str, int, float)):
            return str(mapping[name]).strip()
    return ""


def _walk_structured(node: Any, path: str = "") -> Iterator[tuple]:
    """在任意深度找结构化候选字段，产出 ``(key, path, value)``。"""
    if isinstance(node, dict):
        for k, v in node.items():
            kk = str(k)
            if kk in STRUCTURED_KEYS:
                yield kk, f"{path}.{kk}" if path else kk, v
            else:
                yield from _walk_structured(v, f"{path}.{kk}" if path else kk)
    elif isinstance(node, list):
        for idx, v in enumerate(node):
            yield from _walk_structured(v, f"{path}[{idx}]")


def _candidate_from_item(item: Any, *, source_kind: str, source_ref: str,
                         field_name: str, confidence: str) -> Optional[Candidate]:
    """列表元素（dict 或 字符串）→ Candidate；结构信息不足时返回 None。"""
    if isinstance(item, dict):
        slug_raw = _pick(item, FIELD_ALIASES["slug"])
        title = _pick(item, FIELD_ALIASES["title"])
        acc = _pick(item, FIELD_ALIASES["acceptance"])
        domain = _pick(item, FIELD_ALIASES["domain"])
        repo = _pick(item, FIELD_ALIASES["repo"])
        why = _pick(item, FIELD_ALIASES["rationale"])
        if not slug_raw and not title:
            return None
        return Candidate(slug_raw=slug_raw, slug=canonical_slug(slug_raw),
                         title=title or slug_raw, acceptance=acc, domain=domain,
                         repo=repo, rationale=why, source_kind=source_kind,
                         source_ref=source_ref, src_field=field_name,
                         confidence=confidence, raw={"item": item})
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        slug_raw, title, acc = split_anchor_text(text)
        return Candidate(slug_raw=slug_raw, slug=canonical_slug(slug_raw),
                         title=title or text[:80], acceptance=acc,
                         source_kind=source_kind, source_ref=source_ref,
                         src_field=field_name, confidence=confidence,
                         raw={"item": text})
    return None


_SEP_SPLIT_RE = re.compile(r"\s*[—–]\s*|\s+-\s+|\s*[:：|]\s*")


def split_anchor_text(text: str) -> tuple:
    """``"FEAT-X — 标题：验收…"`` → ``(slug_raw, title, acceptance)``。"""
    parts = [p.strip() for p in _SEP_SPLIT_RE.split(text) if p and p.strip()]
    if not parts:
        return "", "", ""
    slug_raw = parts[0]
    rest = parts[1:]
    acc = ""
    for i, p in enumerate(rest):
        if _looks_quantified(p):
            acc = p
            rest = rest[:i]
            break
    acc2 = ""
    m = _ACC_HINT_RE.search(text)
    if m:
        acc2 = text[m.end():].strip()
    return slug_raw, " ".join(rest), (acc or acc2)


def _looks_quantified(text: str) -> bool:
    return bool(text) and bool(_QUANT_NUM_RE.search(text)) and bool(_QUANT_OP_RE.search(text))


# ── 主入口：result → 候选 ───────────────────────────────────────────────────
def candidates_from_result(result: Any, mission_id: Any = None,
                           mission_goal: str = "") -> List[Candidate]:
    """从一张 mission 的 ``result`` 抽候选锚（同 slug 合并，结构化优先）。

    ``mission_goal`` 只用于补 title 缺口与填 raw 溯源，不参与裁决。
    """
    source_ref = f"mission:{mission_id}" if mission_id is not None else "mission:?"
    found: Dict[str, Candidate] = {}
    order: List[str] = []

    def _add(cand: Optional[Candidate], rank: int) -> None:
        if cand is None:
            return
        if not cand.slug:
            # 无 slug 的候选无法入榜（榜以 slug 为唯一键），保留原始文本供审计
            key = f"__noslug__{len(order)}"
            cand.slug = ""
            found[key] = cand
            order.append(key)
            return
        prev = found.get(cand.slug)
        if prev is None:
            found[cand.slug] = cand
            order.append(cand.slug)
            return
        # 合并：结构化信息补齐文本候选的空字段
        prev.title = prev.title or cand.title
        prev.acceptance = prev.acceptance or cand.acceptance
        prev.domain = prev.domain or cand.domain
        prev.repo = prev.repo or cand.repo
        prev.rationale = prev.rationale or cand.rationale
        if _rank(cand.confidence) < _rank(prev.confidence):
            prev.confidence, prev.src_field = cand.confidence, cand.src_field

    # ① 结构化字段（任意深度）
    for key, path, value in _walk_structured(result):
        items = value if isinstance(value, list) else [value]
        for item in items:
            _add(_candidate_from_item(item, source_kind="mission_finish",
                                      source_ref=source_ref, field_name=path,
                                      confidence="structured"), 0)

    # ② 文本里的 JSON 块
    for fname, text in iter_text_fields(result):
        for blob in iter_json_blobs(text):
            for key, path, value in _walk_structured(blob):
                items = value if isinstance(value, list) else [value]
                for item in items:
                    _add(_candidate_from_item(item, source_kind="mission_finish",
                                              source_ref=source_ref,
                                              field_name=f"{fname}:{path}",
                                              confidence="json_in_text"), 1)

    # ③ 宽松文本扫描（仅锚语境行）
    for fname, text in iter_text_fields(result):
        for cand in _scan_text_anchors(text, source_kind="mission_finish",
                                       source_ref=source_ref, field_name=fname):
            _add(cand, 2)

    out = [found[k] for k in order]
    for c in out:
        if not c.title and mission_goal:
            c.title = mission_goal[:120]
        c.raw.setdefault("mission_id", mission_id)
        if mission_goal:
            c.raw.setdefault("mission_goal", mission_goal[:300])
    return out


def _rank(confidence: str) -> int:
    return {"structured": 0, "json_in_text": 1, "text": 2}.get(confidence, 3)


def iter_text_fields(result: Any) -> Iterator[tuple]:
    """result 里的文本字段（白名单字段 + 一层嵌套的字符串值），按优先级产出。"""
    if not isinstance(result, dict):
        if isinstance(result, str):
            yield "result", result
        return
    for name in TEXT_FIELDS:
        val = result.get(name)
        if isinstance(val, str) and val.strip():
            yield name, val
    for k, v in result.items():
        if k in TEXT_FIELDS or not isinstance(v, str):
            continue
        if len(v) > 20 and ("锚" in v or "anchor" in v.lower() or "{" in v):
            yield str(k), v


def _scan_text_anchors(text: str, *, source_kind: str, source_ref: str,
                       field_name: str) -> Iterator[Candidate]:
    """锚语境行扫描：行内有语境词 + slug 形状 token → 候选（验收片段须行内真实存在）。"""
    if not text:
        return
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        low = line.lower()
        if not any(w.lower() in low for w in CONTEXT_WORDS):
            continue
        for m in SLUG_TOKEN_RE.finditer(line):
            token = m.group(0)
            slug = canonical_slug(token)
            if not is_valid_slug(slug):
                continue
            tail = line[m.end():].strip(" :：—-|,，.。")
            title = tail.split("：")[0].split(":", 1)[0].strip()
            if not title:
                title = tail[:80] or slug
            acc = ""
            probe = " ".join([tail] + lines[idx + 1: idx + 3])
            hit = _ACC_HINT_RE.search(probe)
            if hit:
                acc = probe[hit.end():].strip()[:400]
            yield Candidate(slug_raw=token, slug=slug, title=title[:200],
                            acceptance=acc, source_kind=source_kind,
                            source_ref=source_ref, src_field=field_name,
                            confidence="text", raw={"line": line[:400]})


def parse_mission_row(row: Dict[str, Any]) -> List[Candidate]:
    """``{id, goal, result}`` 行 → 候选列表（store 层读库后调这里）。"""
    result = row.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = {"summary": result}
    return candidates_from_result(result, mission_id=row.get("id"),
                                  mission_goal=str(row.get("goal") or ""))


def dedupe_candidates(cands: Iterable[Candidate]) -> List[Candidate]:
    """跨来源去重（同 slug 保留首个），用于批处理统计。"""
    seen: Dict[str, Candidate] = {}
    out: List[Candidate] = []
    for c in cands:
        if c.slug and c.slug in seen:
            continue
        if c.slug:
            seen[c.slug] = c
        out.append(c)
    return out

# ── tensor_reports 正文：「局限/待办」节 → 候选清单 ─────────────────────────
#: 认定「局限/待办」节的标题关键词（平台中性：只认组织结构词，不认领域词）
BACKLOG_HEADINGS = ("局限", "待办", "未完成", "后续", "下一步", "待补", "边界",
                    "limitation", "todo", "next step", "follow-up", "followup")

_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")


def parse_report_sections(markdown: str) -> List[Dict[str, str]]:
    """按 markdown 标题切节，抽出「局限/待办」类小节下的条目（bullet 或整段）。

    返回 ``[{"section": <标题原文>, "item": <条目文本>}]``；不判优劣、不合并近义项——
    判据交给 coordinator 周批（本函数只保证「结构化、可幂等入库」）。
    """
    if not markdown:
        return []
    lines = str(markdown).splitlines()
    out: List[Dict[str, str]] = []
    cur_heading, cur_level, buf = "", 0, []

    def flush_section() -> None:
        if not cur_heading:
            return
        low = cur_heading.lower()
        if not any(k.lower() in low for k in BACKLOG_HEADINGS):
            buf.clear()
            return
        bullets = [ln for ln in buf if _BULLET_RE.match(ln)]
        if bullets:
            for ln in bullets:
                item = _BULLET_RE.match(ln).group(1).strip()
                if item:
                    out.append({"section": cur_heading, "item": item[:800]})
        else:
            text = " ".join(x.strip() for x in buf if x.strip())
            if text.strip():
                out.append({"section": cur_heading, "item": text[:800]})
        buf.clear()

    for ln in lines:
        m = _HEADING_RE.match(ln)
        if m:
            flush_section()             # 任何标题都结束上一个小节（含子标题：一节一收）
            cur_heading, cur_level = m.group(2).strip(), len(m.group(1))
            continue
        buf.append(ln)
    flush_section()
    return out


def backlog_items_from_report(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``tensor_reports`` 行 → ``demand_backlog`` 条目（带 source_ref）。"""
    rid = row.get("id")
    items = parse_report_sections(str(row.get("markdown") or ""))
    out: List[Dict[str, Any]] = []
    for it in items:
        out.append({"source_kind": "tensor_report", "source_ref": f"report:{rid}",
                    "section": it["section"], "item": it["item"],
                    "report_title": str(row.get("title") or "")[:200]})
    return out
