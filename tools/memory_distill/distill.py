"""候选判例对象与蒸馏规则（从 succeeded 报告 / shadow 处置日志 → 判例候选）。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable

from .citations import CitationBundle, extract_citations

# 关键量上下文：判例里最值得保留的是「判据 vs 实测」那一句
_KEY_LINE = re.compile(
    r"(误差|相对误差|relative\s+error|判据|PASS|FAIL|达标|不达标|收敛|发散|"
    r"±|vs\s*基线|对比|benchmark)",
    re.IGNORECASE,
)
_SNAPSHOT_HEAD = 4000  # 报告 markdown 截断长度（够取判据句，控快照体积）


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _strip_md(s: str) -> str:
    """去 markdown/LaTeX 噪声（判例要人能一眼读懂，不留 $..$ 与反斜杠命令）。"""
    s = re.sub(r"`{1,3}", "", s or "")
    s = re.sub(r"\$([^$]{1,60})\$", r"\1", s)  # 行内公式 -> 内容
    s = re.sub(r"\\(?:int|frac|sqrt|partial|cdot|times|approx|sim|le|ge|mu|nu|rho|Delta|sigma|Omega)\b", "", s)
    s = s.replace("\\", "").replace("$", "")
    s = re.sub(r"[*_>#|]+", " ", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return _norm_ws(s)


@dataclass
class Boundary:
    """判例的适用边界（局限/适用范围）——不写边界的判例不许晋升。"""

    scope: str = ""

    def as_dict(self) -> dict:
        return {"scope": self.scope}


@dataclass
class Candidate:
    """一条候选判例。

    fingerprint = sha256(kind|source_ref|normalized_title|key_metric)
      —— 不含时间戳、不含随机量，保证同输入同指纹（可复算、可去重）。
    """

    candidate_id: str
    kind: str  # "report" | "shadow"
    source_ref: str  # 报告 mission_id / PR key
    title: str
    lesson: str  # 已压缩到 <=max_len 的判例正文
    citations: CitationBundle
    boundary: Boundary = field(default_factory=Boundary)
    origin: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "source_ref": self.source_ref,
            "title": self.title,
            "lesson": self.lesson,
            "citations": self.citations.as_dict(),
            "boundary": self.boundary.as_dict(),
            "origin": dict(self.origin),
        }

    def playbook_line(self, date_tag: str = "") -> str:
        """转成 bootstrap PLAYBOOK 行（append-only 单条 <=120 字）。"""
        tag = f"{date_tag} " if date_tag else ""
        return f"- {tag}{self.lesson}".strip()


_DATE_TAG = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def candidate_id_for(kind: str, source_ref: str, title: str, key: str) -> str:
    h = hashlib.sha256()
    for part in (kind, source_ref, _norm_ws(title), _norm_ws(key)):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return f"{kind[:2]}-{h.hexdigest()[:16]}"


_CJK = re.compile(r"[\u4e00-\u9fff]")
_ALPHA = re.compile(r"[A-Za-z]")


def _cjk_count(text: str) -> int:
    return len(_CJK.findall(text))


def _ascii_alpha_ratio(text: str) -> float:
    body = re.sub(r"\s+", "", text)
    if not body:
        return 0.0
    return len(_ALPHA.findall(body)) / len(body)


def _pick_key_line(markdown: str) -> str:
    """挑最像「判据/误差结论」的一句（优先带百分号且命中关键上下文的行）。"""
    lines = [ln for ln in (markdown or "").splitlines() if ln.strip()]
    scored: list[tuple[int, int, str]] = []
    for idx, ln in enumerate(lines):
        plain = _strip_md(ln)
        if not plain or len(plain) < 8:
            continue
        cit = extract_citations(plain)
        score = 0
        if _KEY_LINE.search(plain):
            score += 2
        if cit.numbers:
            score += 2
        if "%" in plain:
            score += 1
        if cit.paths:
            score += 1
        # 可读性（R2/R4）：中文成句加分，代码/表格残片/碎片短句扣分
        if _cjk_count(plain) >= 8:
            score += 2
        if _ascii_alpha_ratio(plain) > 0.60:
            score -= 2
        if plain.count("|") >= 2 or plain.startswith(("|", "```", "(", "（", "-", "+", "/")):
            score -= 2
        if len(plain) < 16:
            score -= 2
        if score >= 5:
            scored.append((score, idx, plain))
    if not scored:
        return ""
    # 同分取靠前者（报告惯例：结论前置）
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][2]


def _shrink(text: str, budget: int) -> str:
    """截断优先落在子句边界，避免把判例切成半句（R4 可读性）。"""
    text = _norm_ws(text)
    if len(text) <= budget:
        return text
    window = text[:budget]
    cut = max(window.rfind(ch) for ch in "，；。、,;：: ")
    frag = window[:cut].rstrip("，,；;、:： /·+-") if cut >= budget * 0.45 else ""
    # 悬空连接词（'与/和/及/的/在/对/于'）必须回退到上一个子句边界，否则判例读不成句
    for _ in range(4):
        if not frag or not re.search(r"(与|和|及|或|的|在|对|于|把|被|由|为)$", frag):
            break
        prev = max(frag.rfind(ch) for ch in "，；。、,;：: ")
        frag = frag[:prev].rstrip("，,；;、:： /·+-") if prev > 8 else ""
    if frag:
        return frag
    return window[: max(0, budget - 1)].rstrip("，,；;、 ") + "…"


def compose_lesson(
    title: str,
    key_line: str,
    citations: CitationBundle,
    max_len: int = 120,
    prefix: str = "",
) -> str:
    """在 <=max_len 内拼出「标题 + 判据数字 + 证据路径」的判例正文。

    分配策略（确定性）：先保标题（<=46 字），再贪心塞引用项（数字优先、路径其后），
    直到 budget 用尽；引用项顺序完全来自抽取顺序，不排序、不打乱。
    """
    head = (prefix + _shrink(_strip_md(title), 46)).strip()
    parts = [head]
    used = len(head)

    def push(item: str, sep: str) -> bool:
        nonlocal used
        item = _norm_ws(item)
        # 已在正文里的引用不再重复挂（避免「5 %，5 %」式噪声）
        if not item or re.sub(r"\s+", "", item) in re.sub(r"\s+", "", "".join(parts)):
            return True
        add = len(sep) + len(item)
        if used + add > max_len:
            return False
        parts.append(sep + item if parts and sep else item)
        used += add
        return True

    if key_line:
        push(_shrink(key_line, 54), "；" if parts else "")
    inline_nums = len(extract_citations(key_line).numbers) if key_line else 0
    budget_nums = 0 if inline_nums >= 2 else 2  # 主句已自带量时，不追加裸数（防无标签尾巴）
    for num in citations.numbers[:budget_nums]:
        if not push(num, "，"):
            break
    for p in citations.paths[:1]:
        if not push(p, "；"):
            break
    return "".join(parts).strip()


def derive_candidates(
    reports: Iterable[dict],
    shadow: Iterable[dict] = (),
    max_len: int = 120,
    limit: int | None = None,
) -> list[Candidate]:
    """从报告快照与 shadow 处置日志蒸馏候选判例（确定性，顺序稳定）。"""
    out: list[Candidate] = []

    for rep in reports:
        title = _norm_ws(rep.get("title") or "")
        if len(title) < 12:  # 标题过短（如 "probe shape"）用摘要补足语义，避免判例无主语
            title = _norm_ws(f"{title} {rep.get('summary') or ''}")
        md = (rep.get("markdown") or "")[:8000]
        mission = str(rep.get("mission_id") or "")
        scenario = rep.get("scenario_key") or ""
        if not title:
            continue
        key_line = _pick_key_line(md)
        # 引用项从「标题+判据句+结论区」联合抽取（结论区限 2500 字，控成本）
        window = f"{title}。{key_line}。{md}"
        cit = extract_citations(window)
        if len(cit) < 3:
            continue
        prefix = f"m{mission} " if mission else ""
        lesson = compose_lesson(title, key_line, cit, max_len=max_len, prefix=prefix)
        cid = candidate_id_for("report", mission or title, title, key_line)
        out.append(
            Candidate(
                candidate_id=cid,
                kind="report",
                source_ref=f"mission:{mission}" if mission else f"report:{title}",
                title=title,
                lesson=lesson,
                citations=cit,
                boundary=Boundary(scope=_shrink(scenario or "报告声明范围", 40)),
                origin={
                    "scenario_key": scenario,
                    "report_id": rep.get("report_id") or rep.get("id"),
                    "source": "tensor_reports⨝tensor_dev_missions(status=succeeded)",
                },
            )
        )

    for ent in shadow:
        reasons = ent.get("reasons") or []
        if isinstance(ent.get("verdict"), dict):
            reasons = ent["verdict"].get("reasons") or reasons
        reason_txt = "；".join(str(r) for r in reasons if r)
        pr_key = f"{ent.get('repo','?')}#{ent.get('pr','?')}"
        verdict = str(ent.get("verdict_text") or ent.get("verdict") or "")
        outcome = str(ent.get("outcome") or "")
        text = f"{pr_key} 处置={verdict} 结果={outcome} 理由={reason_txt}"
        cit = extract_citations(text)
        if len(cit) < 3:
            continue
        title = f"shadow 处置 {pr_key}"
        lesson = compose_lesson(title, f"{verdict}→{outcome}", cit, max_len=max_len)
        out.append(
            Candidate(
                candidate_id=candidate_id_for("shadow", pr_key, title, reason_txt),
                kind="shadow",
                source_ref=pr_key,
                title=title,
                lesson=lesson,
                citations=cit,
                boundary=Boundary(scope="PR 合并门禁处置，非数值结论"),
                origin={"source": "shadow_merge.json / coordinator_shadow"},
            )
        )

    if limit is not None:
        out = out[:limit]
    return out