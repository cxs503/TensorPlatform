"""四契约的知识学形状（字段、章节词表、完整性判据、摘要）。

为什么词表在这里而不在配置里
----------------------------
章节词表描述的是**平台自身的报告写作契约**（"案例介绍/怎么做的/计算结果/注意点"），
不是任何一个学科的词条。它是机制的常量，不是领域数据 —— 领域内容只经报告正文注入，
域标签经 ``KM_SEDIMENT_DOMAIN_MAP`` 注入。因此词表落地在代码里，同时接受
``section_aliases`` 参数让调用方追加别名（可配置扩展性，不牺牲零领域语义）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable, Mapping, Sequence

#: 四契约字段（顺序即报告 JSON 里的展示顺序）。
CONTRACT_FIELDS: tuple[str, ...] = ("problem", "method", "evidence", "limitations")

#: 一条知识条目的完整字段集（机器可裁的 schema）。
ENTRY_FIELDS: tuple[str, ...] = (
    "id",
    "mission_id",
    "domain",
    "problem",
    "method",
    "evidence",
    "limitations",
    "tags",
    "sha256",
)

#: 最小必填集：缺任何一个即判「不完整」→ 不入库。
#: （``created_at``/``extractor`` 由存储层补，属记账字段，不算知识契约。）
REQUIRED_ENTRY_FIELDS: tuple[str, ...] = ENTRY_FIELDS + ("related", "source")

#: 章节 → 契约的默认别名表（报告写作模板用词，非领域词）。
SECTION_ALIASES: Mapping[str, tuple[str, ...]] = {
    "problem": (
        "案例介绍", "背景", "问题", "目标", "任务背景", "引言", "定位", "概述",
        "case introduction", "introduction", "background", "problem", "objective", "context",
    ),
    "method": (
        "方法", "怎么做的", "解决方法", "实现", "实现路径", "路径", "流程", "设置",
        "工具链", "做法", "策略", "方案",
        "method", "approach", "implementation", "procedure", "workflow", "how",
    ),
    "evidence": (
        "计算结果", "结果", "证据", "验证", "误差判定", "结论", "指标", "数据",
        "实测", "判据", "测量", "图", "核心发现", "机器块", "发现", "校验", "门校验",
        "results", "result", "evidence", "validation", "metrics", "measurement",
        "outcome", "error", "findings", "gate",
    ),
    "limitations": (
        "注意点", "局限", "适用范围", "坑", "风险", "限制", "已知问题", "未完成", "边界", "未覆盖",
        "caveat", "limitations", "limitation", "scope", "risk", "pitfall", "known issue",
    ),
}

#: 判「文本里有可核对证据」的过程性标记（数值/校验/工件），非领域词。
EVIDENCE_MARKERS: tuple[str, ...] = (
    "pytest", "sha", "commit", "artifact", "fig", "%", "±", "error", "误差", "判据",
    "通过", "test", "bench", "log",
)

#: 单字段正文上限（控制单条知识的体积；不是语义裁剪，截断处记 ``truncated``）。
MAX_FIELD_CHARS = 1800
#: 判「有实质内容」的最短正文长度。
MIN_FIELD_CHARS = 8

_NUMBER_RX = re.compile(r"\d")
_HEADER_RX = re.compile(r"^(#{1,6})[ \t]*(.+?)[ \t]*$", re.M)
_LEAD_NUM_RX = re.compile(r"^[\s\u3000]*(?:[0-9]+(?:\.[0-9]+)*|[①-⑳]|[一二三四五六七八九十]+)[.、\):：\s]*")


def normalize_text(text: str | None) -> str:
    """折叠空白、去首尾空行 —— 让同一份报告重复抽取得到同一个 sha256。"""
    if not text:
        return ""
    lines = [ln.rstrip() for ln in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = False
    for ln in lines:
        if not ln.strip():
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(ln.strip() if not out else ln)
    return "\n".join(out).strip()


def clip(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def section_field(title: str, aliases: Mapping[str, Sequence[str]] = SECTION_ALIASES) -> str | None:
    """章节标题 → 契约字段名（``None`` = 该章节不承载四契约）。

    先剥掉标题前导编号（``1.`` / ``①``），再做大小写无关的别名匹配。
    匹配优先「整词包含」，同一标题命中多个契约时按 ``CONTRACT_FIELDS`` 顺序取首个；
    标题里带括号补充说明（如「注意点（踩过的坑/局限）」）也能被命中。
    """
    if not title:
        return None
    probe = _LEAD_NUM_RX.sub("", str(title)).strip().lower()
    if not probe:
        return None
    for field in CONTRACT_FIELDS:
        for alias in aliases.get(field, ()):
            if alias.lower() in probe:
                return field
    return None


#: 凭据/密钥的掩码标记（知识库不得把报告里的口令原样搬运出去）。
REDACTION_MARK = "***REDACTED***"

#: 掩码规则：键值式口令 + 常见平台令牌形状 + 带口令的连接串。
#: 这些是**凭据形状**的正则（安全机制），不是领域词条。
_SECRET_RULES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # 说明：CJK 与 ASCII 同属「词字符」，`\b` 在「中文紧邻凭据关键词」时会失效，
    # 因此边界一律用显式 ASCII lookaround，而不是 \b。
    (
        "kv_secret",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(pass(word|wd)?|pwd|pgpassword|secret|token|api[_-]?key|"
            r"access[_-]?key|private[_-]?key|密钥|口令|密码)(?![A-Za-z0-9_])"
            r"\s*[:=：]\s*[\x22\x27]?[^\s,;\x22\x27）)]{3,}[\x22\x27]?"
        ),
    ),
    (
        "kv_secret_bracket",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(pass(word|wd)?|pwd|pgpassword|secret|token|api[_-]?key|"
            r"access[_-]?key|private[_-]?key|密钥|口令|密码)(?![A-Za-z0-9_])"
            r"\s*[（(]\s*[^\s（）()]{3,}\s*[)）]"
        ),
    ),
    ("gh_token", re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])")),
    ("sk_token", re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_])")),
    ("bearer", re.compile(r"(?i)(?<![A-Za-z0-9_])Bearer\s+[A-Za-z0-9._\-]{16,}")),
    ("dsn_password", re.compile(r"(?i)(postgres(?:ql)?|mysql|redis|amqp)://[^\s:/@]+:[^\s@]+@")),
)


def redact_secrets(text: str | None) -> tuple[str, int]:
    """把正文里的凭据掩码掉，返回 ``(新正文, 命中条数)``。

    为什么在机制层做：mission 报告是自由文本，作者可能把口令/令牌粘进「注意点」段；
    知识库一旦吸收就变成**永久性凭据泄露**（还会被检索接口放大）。因此在入库前统一掩码，
    并在条目里记账（``source.redactions``），而不是依赖作者自觉。
    """
    body = str(text or "")
    if not body:
        return "", 0
    hits = 0
    for _name, rx in _SECRET_RULES:
        def _sub(match: "re.Match[str]") -> str:
            nonlocal hits
            hits += 1
            whole = match.group(0)
            if _name == "kv_secret":
                sep = "=" if "=" in whole else (":" if ":" in whole else "：")
                key = whole.split(sep)[0].rstrip()
                return f"{key}{sep}{REDACTION_MARK}"
            if _name == "kv_secret_bracket":
                key = re.split(r"[（(]", whole)[0].strip()
                return f"{key}({REDACTION_MARK})"
            if _name == "dsn_password":
                return re.sub(r"://([^\s:/@]+):[^\s@]+@", rf"://\1:{REDACTION_MARK}@", whole)
            return REDACTION_MARK
        body = rx.sub(_sub, body)
    return body, hits


def has_evidence_signal(text: str | None) -> bool:
    """质量门：evidence 必须「有可核对的东西」。

    判据（任一即可）：正文里有数字；或出现过程性标记词（pytest/sha/误差/判据…）。
    只有形容词和结论词（"效果良好"）的段落不算证据 —— 这类条目入库只会污染检索。
    """
    body = normalize_text(text)
    if len(body) < MIN_FIELD_CHARS:
        return False
    if _NUMBER_RX.search(body):
        return True
    low = body.lower()
    return any(marker in low for marker in EVIDENCE_MARKERS)


def content_sha256(mission_id: object, contracts: Mapping[str, str]) -> str:
    """四契约正文的稳定摘要：同一 mission 重复触发 → 同一 sha256 → 去重不增条目。"""
    payload = {
        "mission_id": str(mission_id),
        "contracts": {f: normalize_text(contracts.get(f) or "") for f in CONTRACT_FIELDS},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def entry_is_complete(entry: Mapping[str, object]) -> bool:
    """schema 完整性：全部字段在、关键文本非空、sha256 形状合法。"""
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            return False
    for field in CONTRACT_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or len(normalize_text(value)) < MIN_FIELD_CHARS:
            return False
    sha = entry.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        return False
    if not isinstance(entry.get("id"), str) or not entry["id"]:
        return False
    if entry.get("mission_id") in (None, "", 0):
        return False
    if not isinstance(entry.get("tags"), list):
        return False
    if not isinstance(entry.get("related"), list):
        return False
    return True


def split_sections(markdown: str) -> list[dict]:
    """把 markdown 切成 ``[{level, title, body}]``（正文为两个标题之间的文本）。"""
    text = (markdown or "").replace("\r\n", "\n")
    marks = list(_HEADER_RX.finditer(text))
    sections: list[dict] = []
    for idx, m in enumerate(marks):
        start = m.end()
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        sections.append(
            {
                "level": len(m.group(1)),
                "title": m.group(2).strip(),
                "body": normalize_text(text[start:end]),
            }
        )
    return sections


def compact_joiner(chunks: Iterable[str], budget: int = MAX_FIELD_CHARS) -> str:
    """把多个候选段落拼成一个契约正文，总长不超过 budget。"""
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        piece = normalize_text(chunk)
        if not piece:
            continue
        if used + len(piece) + 2 > budget:
            piece = piece[: max(0, budget - used - 1)].rstrip() + "…"
        parts.append(piece)
        used += len(piece) + 2
        if used >= budget:
            break
    return "\n\n".join(parts)