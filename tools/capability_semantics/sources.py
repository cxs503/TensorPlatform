"""声明源与交付面发现（全部输入经配置文件/工件注入，代码零领域语义）。

支持来源类型（自动识别，不要求改造既有产物）：

* **ledger**（能力台账）：``{"capabilities": [{"id", "claimed_level", "evidence": [...]}]}``
* **capability config**：``{"capabilities": [{"capability_id", "initial_level"}]}``
* **snapshot**（agent 工具快照）：``[{"id", "level"}]`` 或 ``{"capabilities": [...]}``

等级字段名做别名归一（``id``/``capability_id``/``name``；``claimed_level``/
``initial_level``/``declared_level``/``level``/``evidence_level``/``status``），
遇到不认识的值一律返回 ``None`` 并在报告里显式记 ``unparsable``，绝不猜。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .model import Declaration, EvidenceRecord, normalise_level

ID_KEYS = ("id", "capability_id", "name", "card_id")
LEVEL_KEYS = (
    "claimed_level",
    "initial_level",
    "declared_level",
    "evidence_level",
    "level",
    "status",
    "verification_status",
)
#: 非等级值 → 不解析（保持诚实：不把 NOT_VERIFIED 当 WITHHELD）
LEVEL_ALIASES = {
    "NOT_VERIFIED": "WITHHELD",
    "UNVERIFIED": "WITHHELD",
}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "numeric_pass_pct": 3.0,
    "numeric_accept_pct": 15.0,
    "entry_loss_max_ratio": 0.05,
    "withheld_ratio_max": 0.30,
    "orphan_ratio_max": 0.05,
    "min_graded_entries": 20,
    "min_misuse_fixes": 3,
    "min_numeric_assert_tests_mock": 1,
}

DEFAULT_DISCOVERY: dict[str, Any] = {
    "endpoint_roots": ["backend/api/domain"],
    "scan_subdirs": ["backend", "src", "tests", "docs"],
    "endpoint_decorator_regex": r"@(?:router|app)\.(?:get|post|put|patch|delete)\(",
    "unbound_module_allowlist": [],
    "exclude_glob_patterns": [],
}


def file_sha256(path: Path) -> str:
    """文件内容 sha256；读不动返回空串（不让 I/O 失败污染判定）。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def sha256_payload(payload: Any) -> str:
    """对可 JSON 化负载求稳定 sha256（键排序、UTF-8、无时间戳）。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _pick(record: dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in record and record[k] not in (None, ""):
            return record[k]
    return None


def parse_level(value: Any) -> str | None:
    """等级解析：先走别名表，再归一；不认识返回 None。"""
    if value is None:
        return None
    raw = str(value).strip().upper()
    if raw in LEVEL_ALIASES:
        return LEVEL_ALIASES[raw]
    return normalise_level(raw)


def _iter_records(payload: Any) -> Iterable[dict[str, Any]]:
    """把三种来源 JSON 形状统一成 dict 迭代。"""
    if isinstance(payload, list):
        items: Sequence[Any] = payload
    elif isinstance(payload, dict):
        for key in ("capabilities", "entries", "cards", "items", "records"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        else:
            items = [payload]
    else:
        return []
    return [r for r in items if isinstance(r, dict)]


def load_declarations(path: Path, source: str) -> tuple[list[Declaration], list[str]]:
    """从单个来源文件抽取声明；返回 (声明列表, 未解析条目 id 列表)。"""
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    decls: list[Declaration] = []
    unparsable: list[str] = []
    for rec in _iter_records(payload):
        cid = _pick(rec, ID_KEYS)
        if cid is None:
            continue
        cid = str(cid)
        lv = parse_level(_pick(rec, LEVEL_KEYS))
        if lv is None:
            unparsable.append(cid)
            continue
        note = str(_pick(rec, ("note", "action", "name")) or "")
        decls.append(Declaration(source=source, capability_id=cid, declared_level=lv,
                                 note=note[:200]))
    return decls, unparsable


def load_ledger_evidence(path: Path) -> dict[str, list[EvidenceRecord]]:
    """从台账条目抽取 evidence 层记录（审计裁决）。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[EvidenceRecord]] = {}
    for rec in _iter_records(payload):
        cid = _pick(rec, ID_KEYS)
        if cid is None:
            continue
        items = rec.get("evidence") or []
        if not isinstance(items, list):
            continue
        bucket: list[EvidenceRecord] = []
        for ev in items:
            if not isinstance(ev, dict):
                continue
            ref = ev.get("report_id", ev.get("ref", ""))
            bucket.append(EvidenceRecord(
                source="ledger_audit_report",
                ref=str(ref),
                stance=str(ev.get("stance", "neutral")),
                asserted_level=parse_level(ev.get("asserted_level")),
                as_of=(str(ev["as_of"]) if ev.get("as_of") else None),
                note=str(ev.get("note", ""))[:240],
            ))
        if bucket:
            out[str(cid)] = bucket
    return out


def load_capability_meta(path: Path) -> dict[str, dict[str, Any]]:
    """从台账条目抽取 capability 层元信息（label / domain）。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    meta: dict[str, dict[str, Any]] = {}
    for rec in _iter_records(payload):
        cid = _pick(rec, ID_KEYS)
        if cid is None:
            continue
        meta[str(cid)] = {
            "label": str(rec.get("label") or rec.get("title") or ""),
            "domain": str(rec.get("domain") or rec.get("discipline") or ""),
        }
    return meta


@dataclass
class SemanticsConfig:
    """语义分层配置（域数据的唯一入口）。"""

    path: Path | None = None
    schema_version: str = ""
    level_rank: dict[str, int] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    discovery: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_DISCOVERY))
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    verification_runs: list[dict[str, Any]] = field(default_factory=list)
    debt_policy: dict[str, Any] = field(default_factory=dict)
    raw_sha256: str = ""

    def cap(self, capability_id: str) -> dict[str, Any]:
        for c in self.capabilities:
            if str(c.get("id", "")) == capability_id:
                return c
        return {}

    def threshold(self, key: str) -> float:
        val = self.thresholds.get(key, DEFAULT_THRESHOLDS.get(key, 0.0))
        try:
            return float(val)
        except (TypeError, ValueError):
            return float(DEFAULT_THRESHOLDS.get(key, 0.0))

    def depends_on(self, capability_id: str) -> list[str]:
        deps = self.cap(capability_id).get("depends_on") or []
        return [str(d) for d in deps if isinstance(d, (str, int))]

    def role(self, capability_id: str) -> str:
        role = self.cap(capability_id).get("graph_role")
        return str(role) if role in ("domain", "tooling") else "domain"


def load_semantics_config(path: Path) -> SemanticsConfig:
    """读取语义分层配置；缺文件时退回默认阈值 + 空能力表（降级不崩）。"""
    cfg = SemanticsConfig(path=path)
    if not path.exists():
        return cfg
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg
    if not isinstance(payload, dict):
        return cfg
    cfg.schema_version = str(payload.get("schema_version", ""))
    cfg.raw_sha256 = file_sha256(path)
    if isinstance(payload.get("level_rank"), dict):
        cfg.level_rank = {str(k): int(v) for k, v in payload["level_rank"].items()
                          if isinstance(v, int)}
    if isinstance(payload.get("thresholds"), dict):
        merged = dict(DEFAULT_THRESHOLDS)
        merged.update({str(k): v for k, v in payload["thresholds"].items()})
        cfg.thresholds = merged
    if isinstance(payload.get("delivery_discovery"), dict):
        merged = dict(DEFAULT_DISCOVERY)
        merged.update(payload["delivery_discovery"])
        cfg.discovery = merged
    if isinstance(payload.get("capabilities"), list):
        cfg.capabilities = [c for c in payload["capabilities"] if isinstance(c, dict)]
    if isinstance(payload.get("verification_runs"), list):
        cfg.verification_runs = [r for r in payload["verification_runs"] if isinstance(r, dict)]
    if isinstance(payload.get("verification_debt"), dict):
        cfg.debt_policy = dict(payload["verification_debt"])
    return cfg


@dataclass(frozen=True)
class EndpointModule:
    """在线端点模块（delivery 层粒度：一个可调用面）。"""

    path: str
    endpoint_count: int
    sha256: str = ""
    verbs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "endpoint_count": self.endpoint_count,
            "sha256": self.sha256,
            "verbs": list(self.verbs),
        }


VERB_RE = re.compile(r"\.(get|post|put|patch|delete)\(", re.I)


def inventory_endpoint_modules(root: Path, cfg: SemanticsConfig) -> list[EndpointModule]:
    """扫描配置指定的端点根目录 → 每模块端点数（确定性排序）。"""
    roots = cfg.discovery.get("endpoint_roots") or []
    try:
        deco = re.compile(str(cfg.discovery.get("endpoint_decorator_regex")
                              or DEFAULT_DISCOVERY["endpoint_decorator_regex"]))
    except re.error:
        deco = re.compile(DEFAULT_DISCOVERY["endpoint_decorator_regex"])
    allow = {str(p) for p in (cfg.discovery.get("unbound_module_allowlist") or [])}
    out: list[EndpointModule] = []
    for rel_root in roots:
        base = root / str(rel_root)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if rel in allow:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hits = deco.findall(text)
            if not hits:
                continue
            verbs = tuple(sorted({m.group(1).upper() for m in VERB_RE.finditer(text)}))
            out.append(EndpointModule(path=rel, endpoint_count=len(hits),
                                      sha256=file_sha256(path), verbs=verbs))
    return sorted(out, key=lambda m: m.path)


__all__ = [
    "ID_KEYS",
    "LEVEL_KEYS",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_DISCOVERY",
    "SemanticsConfig",
    "EndpointModule",
    "file_sha256",
    "sha256_payload",
    "parse_level",
    "load_declarations",
    "load_ledger_evidence",
    "load_capability_meta",
    "load_semantics_config",
    "inventory_endpoint_modules",
]