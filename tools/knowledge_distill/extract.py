"""报告解析：从验收报告/``report.json`` 抽取结构化证据条目 + 解析覆盖率。

两条通道
--------
1. **结构化字段通道**：递归遍历 JSON，命中 ``limitation(s)`` / ``next_step(s)`` /
   ``honest_fail(s)`` 及其别名（键名归一化后比较，见 ``_normalize_key``）。指针形如
   ``$.field_or_review.limitations[0]``，可回原文定位。
2. **markdown 小节通道**：真实 ``report.json`` 往往只有 ``markdown`` 正文，局限与
   下一步写在 §"注意点（局限…）/下一步/honest FAIL" 小节里。标题关键字**由参数注入**
   （:class:`~tools.knowledge_distill.params.DistillParams`），解析器不含领域词。

降级纪律
--------
* JSON 读不出 → ``status="unparsable"``，错误进 ``errors``，**不抛栈**：一个坏文件
  不能把整批蒸馏打断，但必须在覆盖率里体现（因此覆盖率是真判据，不是自述）。
* 字段为空/占位符（"-"、"无"、"none"、"n/a"）→ 计入 ``fields_present`` 但不产条目，
  并在 ``skipped`` 里留痕。

覆盖率
------
``parse_coverage = 一致解析的报告数 / 输入报告数``，其中"一致解析"要求：JSON 可读、
结构化字段全部被抽出（``fields_missing == 0``）、没有未分类字段告警。字段覆盖率单独
给（``field_coverage``），两者都进输出 JSON，避免只报一个好看的数。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .params import ITEM_KINDS, DistillParams

# 结构化字段键名别名（归一化后比较：小写、去非字母数字）
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "limitation": ("limitation", "limitations", "knownlimitation", "knownlimitations",
                   "caveat", "caveats", "局限", "限制", "不足", "局限性"),
    "next_step": ("nextstep", "nextsteps", "next", "followup", "followups", "todo",
                  "下一步", "后续", "后续工作"),
    "honest_fail": ("honestfail", "honestfails", "honestfailure", "honestfailures",
                    "fail", "fails", "failure", "failures", "失败", "未达成"),
}

# 敏感信息脱敏（真实报告里出现过明文数据库口令/令牌，产物会进仓库与报告，
# 必须在上游就抹掉；脱敏是硬要求，不是可选清理）
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 环境变量式：PGPASSWORD（xxx） / PGUSER=xxx / API_KEY: xxx
    (re.compile(r"(?i)(?<![A-Za-z0-9_])(PGPASSWORD|PGPASS|PGUSER|AWS_SECRET_ACCESS_KEY|"
                r"AWS_ACCESS_KEY_ID|API_KEY|ACCESS_TOKEN|SECRET_KEY|GITHUB_TOKEN|"
                r"GITLAB_TOKEN|DB_PASSWORD|REDIS_PASSWORD)(?![A-Za-z0-9_])"
                r"\s*[（(=:：]\s*([^\s，。；;、）)]{4,})"),
     r"\1=<REDACTED>"),
    # 键值式：password=... / token: ... / secret = ...
    (re.compile(r"(?i)(?<![A-Za-z0-9_])(password|passwd|pwd|secret|token|api[_-]?key|approval)"
                r"(?![A-Za-z0-9_])\s*[=:：]\s*([^\s，。；;、）)]{4,})"),
     r"\1=<REDACTED>"),
    # 授权头
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <REDACTED>"),
    # 私钥块
    (re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----[\s\S]*?-----END[^-]*PRIVATE KEY-----"),
     "<REDACTED-PRIVATE-KEY>"),
)

PLACEHOLDERS = frozenset({"", "-", "--", "—", "无", "none", "n/a", "na", "null", "tbd", "todo"})

_BULLET_RX = re.compile(r"^\s*(?:[-*+•·]|\d+[.)、]|[（(]\d+[）)])\s*")
_HEADING_RX = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_RX = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_CODE_RX = re.compile(r"`([^`]*)`")


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(key).lower())


_LEAD_MARKUP_RX = re.compile(r"^[\s*\-–—•·|>#]+")
_CHECK_MARKS = ("✅ ", "✅", "⚠️", "⚠ ", "❌", "✔", "✓")


def _clean(text: str) -> str:
    """去排版记号、折叠空白（保留原始语义字符）。"""
    s = _BOLD_RX.sub(lambda m: m.group(1) or m.group(2), str(text))
    s = _CODE_RX.sub(lambda m: m.group(1), s)
    s = s.replace("\u3000", " ")
    s = " ".join(s.split())
    for mark in _CHECK_MARKS:
        if s.startswith(mark):
            s = s[len(mark):].lstrip()
    s = _LEAD_MARKUP_RX.sub("", s)
    return s.rstrip("*").strip()


def redact(text: str) -> tuple[str, int]:
    """抹掉口令/令牌类明文；返回 ``(脱敏文本, 命中数)``。"""
    out = str(text)
    hits = 0
    for pattern, repl in SECRET_PATTERNS:
        out, n = pattern.subn(repl, out)
        hits += n
    return out, hits


def _usable(text: str, min_chars: int) -> bool:
    """条目可用性：长度、占位符、标题残片、表格行残片四类过滤。"""
    cleaned = _clean(text)
    if len(cleaned) < min_chars:
        return False
    low = cleaned.strip().lower()
    if low in PLACEHOLDERS or set(low) <= set("-— "):
        return False
    if cleaned.rstrip().endswith(("：", ":")):
        # 以冒号收尾的是小节小标题/条目引导语，不是一条完整结论
        return False
    if " | " in cleaned:
        return False
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in cleaned)
    has_word = bool(re.search(r"[A-Za-z]{2,}", cleaned))
    return has_cjk or has_word


def report_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 结构化字段通道
# --------------------------------------------------------------------------- #
def _kind_of_key(key: Any) -> str | None:
    norm = _normalize_key(key)
    if not norm:
        return None
    for kind in ITEM_KINDS:
        for alias in FIELD_ALIASES[kind]:
            if norm == _normalize_key(alias):
                return kind
    return None


def _flatten_value(value: Any, pointer: str) -> Iterator[tuple[str, str]]:
    """把任意嵌套值摊成 ``(text, pointer)``；dict 走其可读文本键，其余按键名兜底。"""
    if isinstance(value, str):
        yield _clean(value), pointer
        return
    if isinstance(value, (int, float, bool)) or value is None:
        return
    if isinstance(value, Mapping):
        for key in ("text", "detail", "item", "description", "summary", "title"):
            if key in value:
                yield from _flatten_value(value[key], f"{pointer}.{key}")
                return
        for key, sub in value.items():
            yield from _flatten_value(sub, f"{pointer}.{key}")
        return
    if isinstance(value, Iterable):
        for idx, sub in enumerate(value):
            yield from _flatten_value(sub, f"{pointer}[{idx}]")


def _walk_structured(node: Any, pointer: str, out: list[dict[str, Any]],
                     stats: dict[str, int], params: DistillParams) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{pointer}.{key}"
            kind = _kind_of_key(key)
            if kind is not None:
                stats[f"present:{kind}"] += 1
                found = False
                for text, ptr in _flatten_value(value, child):
                    text, hits = redact(text)
                    if hits:
                        stats["redacted"] = stats.get("redacted", 0) + hits
                    if _usable(text, params.min_item_chars):
                        out.append({"kind": kind, "text": text, "quote": text,
                                    "pointer": ptr, "redacted": bool(hits)})
                        found = True
                    else:
                        stats["skipped_placeholder"] += 1
                if found:
                    stats[f"extracted:{kind}"] += 1
                continue
            _walk_structured(value, child, out, stats, params)
    elif isinstance(node, Iterable) and not isinstance(node, (str, bytes, bytearray)):
        for idx, sub in enumerate(node):
            _walk_structured(sub, f"{pointer}[{idx}]", out, stats, params)


# --------------------------------------------------------------------------- #
# markdown 小节通道
# --------------------------------------------------------------------------- #
def _section_kind(heading: str, params: DistillParams) -> str | None:
    low = heading.lower()
    for kind in ITEM_KINDS:
        for alias in params.markdown_section_aliases.get(kind, ()):
            if alias.lower() in low:
                return kind
    return None


def _split_blocks(body: Sequence[str]) -> list[str]:
    """按空行/标题层级把小节正文切成条目块（bullet 一行一条，段落一块）。"""
    blocks: list[str] = []
    buf: list[str] = []
    for line in body:
        stripped = line.strip()
        # markdown 表格行/分隔行：不是结论条目（实测会把表头串成簇）
        if stripped.startswith("|") or (stripped and set(stripped) <= set("|-: ")):
            if buf:
                blocks.append(" ".join(buf))
                buf = []
            continue
        if not line.strip():
            if buf:
                blocks.append(" ".join(buf))
                buf = []
            continue
        if _BULLET_RX.match(line):
            if buf:
                blocks.append(" ".join(buf))
                buf = []
            blocks.append(line)
        else:
            buf.append(line)
    if buf:
        blocks.append(" ".join(buf))
    out: list[str] = []
    for block in blocks:
        text = _clean(_BULLET_RX.sub("", block))
        if text:
            out.append(text)
    return out


def _walk_markdown(md: str, pointer: str, out: list[dict[str, Any]],
                   stats: dict[str, int], params: DistillParams) -> None:
    lines = md.splitlines()
    current: str | None = None
    level = 0
    body: list[str] = []
    idx = 0

    def flush() -> None:
        nonlocal body, idx
        if current is None:
            body = []
            return
        found = 0
        for text in _split_blocks(body):
            text, hits = redact(text)
            if hits:
                stats["redacted"] = stats.get("redacted", 0) + hits
            if _usable(text, params.min_item_chars):
                out.append({"kind": current, "text": text, "quote": text,
                            "pointer": f"{pointer}#section[{idx}]",
                            "redacted": bool(hits)})
                found += 1
        if found:
            stats[f"extracted:{current}"] += 1
        idx += 1
        body = []

    for line in lines:
        m = _HEADING_RX.match(line)
        if m:
            new_level = len(m.group(1))
            new_kind = _section_kind(m.group(2), params)
            if current is not None and new_level <= level:
                flush()
                current = None
                level = 0
            if new_kind is not None and current is None:
                current = new_kind
                level = new_level
                stats[f"present:{new_kind}"] += 1
                continue
            if new_kind is not None and current is not None and new_level > level:
                # 小节内换了类别（如局限小节里嵌「下一步」子节）：先收口再切类
                flush()
                current = new_kind
                level = new_level
                stats[f"present:{new_kind}"] += 1
                continue
            continue
        if current is not None:
            body.append(line)
    flush()


# --------------------------------------------------------------------------- #
# 报告级解析
# --------------------------------------------------------------------------- #
def _first_present(doc: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in doc and doc[key] not in (None, "", [], {}):
            return doc[key]
    return None


def _guess_node_key(doc: Mapping[str, Any], path: Path) -> str:
    for key in ("node_key", "node_id", "node", "mission_id", "mission", "id"):
        value = _first_present(doc, (key,))
        if value is not None:
            return str(value)
    return path.parent.name


def extract_report(path: str | Path, params: DistillParams | None = None) -> dict[str, Any]:
    """解析单份报告 → 报告级记录（含条目、字段统计、错误、sha256）。"""
    params = (params or DistillParams()).validate()
    p = Path(path)
    record: dict[str, Any] = {
        "path": str(p),
        "sha256": "",
        "status": "unparsable",
        "errors": [],
        "warnings": [],
        "node_key": "",
        "mission_id": None,
        "campaign": None,
        "repo_hint": None,
        "domain_hint": None,
        "title": None,
        "items": [],
        "fields_present": 0,
        "fields_extracted": 0,
        "unclassified_fields": [],
    }
    try:
        record["sha256"] = report_sha256(p)
        raw = p.read_text(encoding="utf-8", errors="replace")
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        record["errors"].append(f"{type(exc).__name__}: {exc}")
        return record
    if not isinstance(doc, Mapping):
        record["errors"].append(f"top-level 不是 JSON object（实际 {type(doc).__name__}）")
        return record

    # 单键包裹（{"report": {...}} / {"payload": {...}}）解包一层：键名不得是结构化字段名
    wrapper = None
    while (len(doc) == 1 and _kind_of_key(next(iter(doc))) is None
           and isinstance(next(iter(doc.values())), Mapping)):
        wrapper = next(iter(doc))
        doc = doc[wrapper]
    record["wrapped_in"] = wrapper

    record["node_key"] = _guess_node_key(doc, p)
    for key in ("mission_id", "mission", "id"):
        value = _first_present(doc, (key,))
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            record["mission_id"] = int(value)
            break
    for key in ("campaign", "campaign_key", "plan_id", "wave"):
        value = _first_present(doc, (key,))
        if value is not None:
            record["campaign"] = value if key == "campaign" else f"{key}:{value}"
            if key == "campaign":
                break
    record["repo_hint"] = _first_present(doc, ("repo", "repository", "repo_hint"))
    record["domain_hint"] = _first_present(
        doc, ("physics_domain", "domain", "domain_hint"))
    record["title"] = _first_present(doc, ("title", "name"))

    structured: list[dict[str, Any]] = []
    stats: dict[str, int] = {f"present:{k}": 0 for k in ITEM_KINDS}
    stats.update({f"extracted:{k}": 0 for k in ITEM_KINDS})
    stats["skipped_placeholder"] = 0
    for key, value in doc.items():
        if key == "markdown":
            continue
        # 包一层：键名本身可能是结构化字段名（顶层不可能被字段名遮蔽）
        _walk_structured({key: value}, "$", structured, stats, params)

    markdown = doc.get("markdown")
    sectioned: list[dict[str, Any]] = []
    if isinstance(markdown, str) and markdown.strip():
        _walk_markdown(markdown, "$.markdown", sectioned, stats, params)

    # 结构化通道优先；同一文本在 markdown 通道重复出现时以结构化指针为准
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for entry in structured + sectioned:
        key = entry["text"]
        if key in seen:
            continue
        seen.add(key)
        items.append(entry)

    record["items"] = items
    record["fields_present"] = sum(stats[f"present:{k}"] for k in ITEM_KINDS)
    record["fields_extracted"] = sum(stats[f"extracted:{k}"] for k in ITEM_KINDS)
    # 硬错误（读不出/类型不可解析）与软告警（字段是占位符、条目被过滤）分开记：
    # 解析覆盖率只由硬错误决定，占位符属"输入数据质量"，由字段覆盖率体现。
    record["status"] = "ok"
    if record["fields_present"] and record["fields_extracted"] < record["fields_present"]:
        record["warnings"].append(
            f"结构化字段未全部抽出: {record['fields_extracted']}/{record['fields_present']}")
        record["status"] = "degraded"
    if not items and not record["fields_present"]:
        record["warnings"].append("未命中任何局限/下一步/FAIL 字段或小节")
        record["status"] = "empty"
    return record


def dedupe_items(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按条目文本 sha256 全局去重，保留全部来源（同一局限在多个节点重复出现是证据强度）。

    不合并会把"复制粘贴的同一份报告"聚成几十条同文条目，既污染簇规模也会把
    k-距离分布压到 0（自适应半径失效）。去重后每个条目带 ``aliases``（同文条目 ID）
    与 ``occurrences``（出现次数），溯源链仍保留每一处来源。
    """
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = str(item["text_sha256"])
        if key not in groups:
            kept = dict(item)
            kept["aliases"] = [item["item_id"]]
            kept["occurrences"] = 1
            kept["source_paths"] = [item["source_path"]]
            groups[key] = kept
            order.append(key)
            continue
        kept = groups[key]
        kept["aliases"].append(item["item_id"])
        kept["occurrences"] += 1
        if item["source_path"] not in kept["source_paths"]:
            kept["source_paths"].append(item["source_path"])
    unique = [groups[k] for k in order]
    return {
        "items": unique,
        "stats": {
            "items_total": len(items),
            "items_distinct": len(unique),
            "duplicate_items": len(items) - len(unique),
            "duplicate_groups": sum(1 for u in unique if u["occurrences"] > 1),
        },
    }


def parse_reports(paths: Iterable[str | Path], params: DistillParams | None = None,
                  *, max_reports: int | None = None) -> dict[str, Any]:
    """批量解析 → ``{"reports": [...], "coverage": {...}, "items": [...]}``。

    覆盖率口径（两条都给，不允许只报好看那条）：
    * ``parse_coverage`` = 一致解析（status ok/empty/degraded 且 errors 为空）报告数 / 总数；
    * ``field_coverage`` = 抽出的结构化字段数 / 出现的结构化字段数。
    """
    params = (params or DistillParams()).validate()
    path_list = [Path(p) for p in paths]
    if max_reports is not None:
        path_list = path_list[:max_reports]
    reports = [extract_report(p, params) for p in path_list]

    consistent = [r for r in reports if not r["errors"]]
    present = sum(r["fields_present"] for r in reports)
    extracted = sum(r["fields_extracted"] for r in reports)
    items: list[dict[str, Any]] = []
    for rec in reports:
        for idx, item in enumerate(rec["items"]):
            items.append({
                "item_id": f"{rec['node_key']}#{item['kind']}#{idx}",
                "kind": item["kind"],
                "text": item["text"],
                "quote": item["quote"],
                "pointer": item["pointer"],
                "node_key": rec["node_key"],
                "mission_id": rec["mission_id"],
                "campaign": rec["campaign"],
                "repo_hint": rec["repo_hint"],
                "domain_hint": rec["domain_hint"],
                "source_path": rec["path"],
                "source_sha256": rec["sha256"],
                "text_sha256": text_digest(item["text"]),
                "redacted": item.get("redacted", False),
            })
    coverage = {
        "reports_total": len(reports),
        "reports_consistent": len(consistent),
        "reports_unparsable": sum(1 for r in reports if r["status"] == "unparsable"),
        "reports_degraded": sum(1 for r in reports if r["status"] == "degraded"),
        "reports_empty": sum(1 for r in reports if r["status"] == "empty"),
        "warnings": sum(len(r.get("warnings", [])) for r in reports),
        "reports_with_items": sum(1 for r in reports if r["items"]),
        "fields_present": present,
        "fields_extracted": extracted,
        "parse_coverage": round(len(consistent) / len(reports), 6) if reports else 0.0,
        "field_coverage": round(extracted / present, 6) if present else 1.0,
        "items": len(items),
        "items_redacted": sum(1 for i in items if i.get("redacted")),
        "items_by_kind": {k: sum(1 for i in items if i["kind"] == k) for k in ITEM_KINDS},
        "errors": [{"path": r["path"], "errors": r["errors"]}
                   for r in reports if r["errors"]],
    }
    return {"reports": reports, "coverage": coverage, "items": items}