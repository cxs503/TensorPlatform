"""报告工件 → 四契约提取器（模板 + 正则，来源全程可溯源）。

输入面（三件套，按可用性取并集）
--------------------------------
* ``report.json``：``{title, summary, scenario_key, mission_id, tags, markdown}``
  —— markdown 是四契约的主来源；
* ``REPORT_GATE_SUMMARY.md``：报告门禁自述 —— `problem`/`method` 的兜底来源，
  **仅当该文件在全库唯一（sha256 只出现一次）时才算本 mission 的工件**；
* ``acceptance*.json``：机器验收判据与实测数字 —— `evidence` 的兜底来源。

归属纪律（为什么必须有「归属门」）
----------------------------------
实测本仓语料：``work/m<id>/`` 是整仓检出，仓根自带的 ``report.json``/
``REPORT_GATE_SUMMARY.md`` 会跟着进每个工作树 —— 1860 份 ``report.json`` 只有
104 份内容互不相同，1588 份 gate 自述只有 **1** 份内容。若不以「源报文自述的
mission_id == 目录号」做归属判定，就会把别人 mission 的结论记到本 mission 头上
（错误的溯源比没有溯源更坏）。

因此两条归属规则：

1. ``report.json``：``mission_id`` 字段必须等于目录号（目录形如 ``m<数字>``）；
2. 无自述 id 的工件（gate 自述等）：sha256 在全库唯一才算（``shared_shas`` 由
   ``pipeline.scan_root`` 的索引遍传入）。

不归属 → 不参与契约提取，拒绝原因 ``no_attributable_source``。

零领域语义
----------
只按**章节契约**切分（``contracts.SECTION_ALIASES``）；``domain`` 只从源报文自述
或外部注入的映射取，代码里没有任何学科词条。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    CONTRACT_FIELDS,
    redact_secrets,
    MAX_FIELD_CHARS,
    MIN_FIELD_CHARS,
    SECTION_ALIASES,
    compact_joiner,
    content_sha256,
    has_evidence_signal,
    normalize_text,
    section_field,
    split_sections,
)

#: 报告工件候选文件名（按优先级）。
REPORT_FILES: tuple[str, ...] = ("report.json", "REPORT_GATE_SUMMARY.md")
#: 验收工件 glob。**故意收窄**：``work/`` 下散落着大量与本 mission 无关的
#: ``validation_report_*.json``（整仓检出带进来的），宽 glob 会把它们当证据。
ACCEPTANCE_GLOBS: tuple[str, ...] = ("acceptance.json", "acceptance_*.json", "*_acceptance.json")
#: 拒绝原因码。
REASON_NO_EVIDENCE = "no_evidence"
REASON_NO_SOURCE = "no_source"
REASON_NO_ATTRIBUTION = "no_attributable_source"
REASON_INCOMPLETE = "incomplete_contracts"

_MISSION_ID_RX = re.compile(r"^\d+$")


@dataclass
class Extraction:
    """一次抽取的结果（可能被判拒绝；拒绝时 ``accepted=False`` + ``reason``）。"""

    mission_id: str
    mission_dir: str
    contracts: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    domain: str = "unspecified"
    sources: list[dict] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    filled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    accepted: bool = False
    reason: str | None = None
    sha256: str | None = None
    title: str | None = None
    scenario_key: str | None = None
    attributed: bool = False
    redactions: int = 0

    @property
    def fill_ratio(self) -> float:
        return len(self.filled) / len(CONTRACT_FIELDS)

    def to_entry(self) -> dict[str, Any]:
        """转成可入库的知识条目（schema 见 ``contracts.REQUIRED_ENTRY_FIELDS``）。"""
        payload = {f: self.contracts.get(f, "") for f in CONTRACT_FIELDS}
        sha = self.sha256 or content_sha256(self.mission_id, payload)
        return {
            "id": f"ke-{sha[:16]}",
            "mission_id": self.mission_id,
            "domain": self.domain,
            "problem": payload["problem"],
            "method": payload["method"],
            "evidence": payload["evidence"],
            "limitations": payload["limitations"],
            "tags": list(self.tags),
            "sha256": sha,
            "title": self.title,
            "scenario_key": self.scenario_key,
            "related": [],
            "extractor": "template+regex/v1",
            "source": {
                "mission_dir": self.mission_dir,
                "files": [dict(s) for s in self.sources],
                "sections": {k: list(v) for k, v in self.sections.items() if v},
                "redactions": self.redactions,
            },
        }

    def to_reject(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "mission_dir": self.mission_dir,
            "reason": self.reason or REASON_INCOMPLETE,
            "missing": list(self.missing),
            "filled": list(self.filled),
            "sources": [dict(s) for s in self.sources],
            "title": self.title,
        }


def dir_mission_id(path: Path) -> str:
    name = path.name
    if name.startswith("m") and _MISSION_ID_RX.match(name[1:] or ""):
        return name[1:]
    return name


def _digest(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None


def _acceptance_paths(mission_dir: Path, globs: Sequence[str] = ACCEPTANCE_GLOBS) -> list[Path]:
    found: list[Path] = []
    for pattern in globs:
        for p in sorted(mission_dir.glob(pattern)):
            if p.is_file() and p not in found:
                found.append(p)
    return found


def _acceptance_evidence(paths: Sequence[Path]) -> str:
    """把验收工件的关键数字压成一段可核对的 evidence 文本。"""
    chunks: list[str] = []
    for path in paths:
        doc = _load_json(path)
        if doc is None:
            continue
        if isinstance(doc, dict):
            items: list[str] = []
            for key, value in doc.items():
                if isinstance(value, (int, float, str, bool)) and str(value).strip():
                    items.append(f"{key}={value}")
                elif isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if isinstance(sub_value, (int, float, str, bool)):
                            items.append(f"{key}.{sub_key}={sub_value}")
                if len(items) >= 12:
                    break
            if items:
                chunks.append(f"[{path.name}] " + "; ".join(items))
        elif isinstance(doc, list) and doc:
            chunks.append(f"[{path.name}] {len(doc)} 项记录；首项={json.dumps(doc[0], ensure_ascii=False)[:200]}")
    return compact_joiner(chunks, budget=MAX_FIELD_CHARS // 2)


def _blocks_with(markdown: str, aliases: Mapping[str, Sequence[str]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """按章节契约把 markdown 分组：``({field: [body]}, {field: [title]})``。"""
    blocks: dict[str, list[str]] = {f: [] for f in CONTRACT_FIELDS}
    titles: dict[str, list[str]] = {f: [] for f in CONTRACT_FIELDS}
    for sec in split_sections(markdown):
        target = section_field(sec["title"], aliases)
        if not target:
            continue
        titles[target].append(sec["title"])
        if sec["body"]:
            blocks[target].append(sec["body"])
    return blocks, titles


def resolve_domain(meta: Mapping[str, Any], domain_map: Mapping[str, str] | None = None) -> str:
    """领域归属：先看源报文自述，再查配置映射，最后 ``unspecified``。

    ``domain_map`` 的键是标签/scenario 值，值是领域名 —— 领域词表作为**数据**
    从外部注入（``KM_SEDIMENT_DOMAIN_MAP``），代码里不出现任何领域名。
    """
    explicit = meta.get("domain")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    labels: list[str] = []
    scenario = meta.get("scenario_key")
    if isinstance(scenario, str) and scenario.strip():
        labels.append(scenario.strip())
    tags = meta.get("tags")
    if isinstance(tags, (list, tuple)):
        labels.extend(str(t).strip() for t in tags if str(t).strip())
    if domain_map:
        for label in labels:
            hit = domain_map.get(label)
            if hit:
                return str(hit)
    return "unspecified"


def load_domain_map(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """读领域映射配置（缺文件/坏 JSON → 空映射，降级为 ``unspecified``，不抛）。"""
    raw = path or os.environ.get("KM_SEDIMENT_DOMAIN_MAP")
    if not raw:
        return {}
    try:
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    return {str(k): str(v) for k, v in doc.items() if isinstance(v, str) and v.strip()}


def extract_from_dir(
    mission_dir: str | os.PathLike[str],
    domain_map: Mapping[str, str] | None = None,
    aliases: Mapping[str, Sequence[str]] | None = None,
    shared_shas: Sequence[str] | set[str] | None = None,
    acceptance_globs: Sequence[str] | None = None,
    require_attribution: bool = True,
) -> Extraction:
    """从一个 mission 工作目录抽四契约。

    ``require_attribution=False`` 只用于单元测试/离线工件（把任意目录当输入）；
    生产扫描一律开归属门。
    """
    path = Path(mission_dir)
    mid = dir_mission_id(path)
    result = Extraction(mission_id=mid, mission_dir=str(path))
    shared = set(shared_shas or ())

    report_path = path / "report.json"
    gate_path = path / "REPORT_GATE_SUMMARY.md"
    acc_paths = _acceptance_paths(path, acceptance_globs or ACCEPTANCE_GLOBS)

    meta: dict[str, Any] = {}
    markdown = ""
    summary = ""
    owned_report = False
    if report_path.is_file():
        doc = _load_json(report_path)
        if isinstance(doc, dict):
            meta = doc
            markdown = str(doc.get("markdown") or "")
            summary = str(doc.get("summary") or "")
        sha, size = _digest(report_path)
        declared = str(meta.get("mission_id") or "").strip()
        owned_report = (not require_attribution) or declared == mid
        result.sources.append(
            {
                "path": str(report_path),
                "sha256": sha,
                "bytes": size,
                "role": "report" if owned_report else "report_unattributed",
                "declared_mission_id": declared or None,
            }
        )

    gate_text = ""
    owned_gate = False
    if gate_path.is_file():
        raw = gate_path.read_text(encoding="utf-8", errors="replace")
        sha, size = _digest(gate_path)
        owned_gate = (not require_attribution) or sha not in shared
        if owned_gate:
            gate_text = normalize_text(raw)
        result.sources.append(
            {
                "path": str(gate_path),
                "sha256": sha,
                "bytes": size,
                "role": "gate_summary" if owned_gate else "gate_summary_shared",
            }
        )

    owned_acc: list[Path] = []
    for acc in acc_paths:
        sha, size = _digest(acc)
        keep = (not require_attribution) or sha not in shared
        result.sources.append(
            {"path": str(acc), "sha256": sha, "bytes": size, "role": "acceptance" if keep else "acceptance_shared"}
        )
        if keep:
            owned_acc.append(acc)

    if owned_report or gate_text or owned_acc:
        result.attributed = True
    else:
        result.reason = REASON_NO_SOURCE if not result.sources else REASON_NO_ATTRIBUTION
        result.missing = list(CONTRACT_FIELDS)
        return result

    if owned_report:
        result.title = str(meta.get("title") or "").strip() or None
        result.scenario_key = str(meta.get("scenario_key") or "").strip() or None
        raw_tags = meta.get("tags")
        result.tags = (
            [str(t).strip() for t in raw_tags if str(t).strip()] if isinstance(raw_tags, (list, tuple)) else []
        )
        declared = str(meta.get("mission_id") or "").strip()
        if declared.isdigit():
            result.mission_id = declared
        result.domain = resolve_domain(meta, domain_map)

    lookup = SECTION_ALIASES if aliases is None else aliases
    blocks: dict[str, list[str]] = {f: [] for f in CONTRACT_FIELDS}
    result.sections = {f: [] for f in CONTRACT_FIELDS}
    if owned_report and markdown:
        found, titles = _blocks_with(markdown, lookup)
        for f in CONTRACT_FIELDS:
            blocks[f].extend(found[f])
            result.sections[f].extend(titles[f])

    # ---- 兜底 1：report.json 的 summary（同一份归属工件内，可溯源）----
    if not blocks["problem"] and owned_report and summary:
        blocks["problem"].append(summary)
        result.sections["problem"].append("report.summary")

    # ---- 兜底 2：gate 自述（仅归属时参与）----
    if gate_text:
        if not blocks["problem"]:
            head = [ln for ln in gate_text.split("\n") if ln.strip()][:12]
            blocks["problem"].append("\n".join(head))
            result.sections["problem"].append("gate_summary.head")
        if not blocks["method"]:
            lines = [ln for ln in gate_text.split("\n") if ln.strip()]
            picked = [ln for ln in lines if re.search(r"实现|方法|路径|修改|新增|步骤|代码", ln)]
            chunk = "\n".join(picked[:20])
            if len(chunk) < MIN_FIELD_CHARS * 2:      # 只命中标题行时，退到正文前若干行
                chunk = "\n".join(lines[:20])
            if len(chunk) >= MIN_FIELD_CHARS:
                blocks["method"].append(chunk)
                result.sections["method"].append("gate_summary.implementation")

    # ---- 兜底 3：验收工件数字 ----
    acc_evidence = _acceptance_evidence(owned_acc)
    if acc_evidence:
        blocks["evidence"].append(acc_evidence)
        result.sections["evidence"].append("acceptance")

    redactions = 0
    for f in CONTRACT_FIELDS:
        body = compact_joiner(blocks[f], budget=MAX_FIELD_CHARS)
        body, hits = redact_secrets(body)          # 凭证掩码：知识库不搬运口令/令牌
        redactions += hits
        result.contracts[f] = body if len(body) >= MIN_FIELD_CHARS else ""
        result.sections.setdefault(f, [])
    result.redactions = redactions

    result.filled = [f for f in CONTRACT_FIELDS if result.contracts[f]]
    result.missing = [f for f in CONTRACT_FIELDS if not result.contracts[f]]
    result.sha256 = content_sha256(result.mission_id, result.contracts)

    # ---- 质量门：契约不全 / 无证据 → 拒绝入库（落 rejected 日志）----
    if result.missing:
        result.reason = REASON_INCOMPLETE
        return result
    if not has_evidence_signal(result.contracts["evidence"]):
        result.reason = REASON_NO_EVIDENCE
        return result
    result.accepted = True
    return result