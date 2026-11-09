"""提案产物 schema 契约（双版本 + 显式 compat 层，fail-closed）。

两道口径
--------
* **v1（历史口径）**：存量提案池 in-repo 形态（``proposal_id/title/hypothesis/
  falsification_criteria/...``）。v1 只做结构校验，随后由 **compat 层**（字段映射写在
  ``config/proposal_gate.json`` 的 ``compat.v1_to_canonical``）映射到规范内部模型；
  v1 语义从不直接进入去重与仲裁，避免「口径 A 校验、口径 B 使用」的漂移。
* **v2（任务书口径）**：``{schema_version, title, repo, goal:{objective, criteria[]},
  is_maturity, ...}``，即规范模型的外部形态，无映射。

口径判据（每条都可机器裁）
--------------------------
1. ``title`` 非空（去空白后长度 ≥ ``contract.title_min_length``）
2. ``repo`` ∈ ``repo_whitelist``（白名单在配置里，不在代码里）
3. ``goal.criteria``（v1 经映射）条数 ≥ ``contract.min_criteria``
4. 每条判据含**可量化阈值**：命中 ``contract.numeric_pattern`` **且**（命中
   ``contract.comparator_pattern`` 或含 ``contract.unit_symbols``）——即「有数」不够，
   还必须有可裁的量纲/方向，杜绝「效果更好」这类不可裁判据
5. ``is_maturity`` 必须是 bool（Python ``bool``，不接受 0/1/"true"）

失败即 quarantine：不写提案池、不进仲裁，理由逐条落账。任何异常同样转 fail-closed。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import ConfigError, GateConfig

try:  # pragma: no cover - 依赖存在与否都在部署面上兜底
    import jsonschema
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]

V1 = "1.0"
V2 = "2.0"


@dataclass(frozen=True)
class ProposalDoc:
    """规范内部模型（v1/v2 归一后的唯一形态）。"""

    title: str
    repo: str
    objective: str
    criteria: tuple[str, ...]
    is_maturity: bool
    dependencies_hint: tuple[str, ...] = ()
    mission_id: int | None = None
    plan_id: int | None = None
    schema_version: str = V2
    proposal_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def blob(self) -> str:
        """用于相似度比较的规范文本（标题 + 目标 + 判据 + 依赖提示）。"""
        parts = [self.title, self.objective, *self.criteria, *self.dependencies_hint]
        return "\n".join(p for p in (str(x).strip() for x in parts) if p)

    def canonical(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "title": self.title,
            "repo": self.repo,
            "goal": {"objective": self.objective, "criteria": list(self.criteria)},
            "dependencies_hint": sorted(self.dependencies_hint),
            "is_maturity": self.is_maturity,
        }

    def sha256(self) -> str:
        """规范模型内容摘要（幂等键的一半：同一内容 → 同一 sha）。"""
        payload = json.dumps(self.canonical(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def deps_set(self) -> frozenset[str]:
        return frozenset(d.strip().casefold() for d in self.dependencies_hint if d.strip())


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    schema_version: str
    doc: ProposalDoc | None
    errors: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        return "accepted" if self.ok else "quarantined"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": self.schema_version,
            "errors": list(self.errors),
            "sha256": self.doc.sha256() if self.doc else None,
            "title": self.doc.title if self.doc else None,
            "repo": self.doc.repo if self.doc else None,
        }


# ── schema 装载 ─────────────────────────────────────────────────────────


class _SchemaCache:
    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, Any]] = {}

    def get(self, path: Path) -> dict[str, Any]:
        if path not in self._cache:
            self._cache[path] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[path]


_SCHEMAS = _SchemaCache()


def _schema_path(cfg: GateConfig, version: str) -> Path:
    rel = cfg.need(f"schemas.{version}")
    path = cfg.resolve_path(str(rel))
    if not path.exists():
        raise ConfigError(f"schema 文件不存在：{path}")
    return path


def detect_version(payload: Mapping[str, Any]) -> str | None:
    """判定口径：显式 ``schema_version`` 优先；否则按特征字段推断；都失败 → None。"""
    if not isinstance(payload, Mapping):
        return None
    declared = payload.get("schema_version")
    if isinstance(declared, str) and declared.strip():
        return V2 if declared.strip().startswith("2") else V1
    if "goal" in payload or "is_maturity" in payload:
        return V2
    if "hypothesis" in payload or "falsification_criteria" in payload or "proposal_id" in payload:
        return V1
    return None


# ── compat 层 ───────────────────────────────────────────────────────────


def _dig(payload: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v).strip())
    return (str(value),)


def to_canonical(payload: Mapping[str, Any], version: str, cfg: GateConfig) -> dict[str, Any]:
    """把任一版本载荷映射为规范模型字段（v1 走配置里的显式字段映射）。"""
    if version == V2:
        goal = payload.get("goal") or {}
        return {
            "title": str(payload.get("title") or ""),
            "repo": str(payload.get("repo") or ""),
            "objective": str(goal.get("objective") or ""),
            "criteria": _as_str_tuple(goal.get("criteria")),
            "dependencies_hint": _as_str_tuple(payload.get("dependencies_hint")),
            "is_maturity": payload.get("is_maturity"),
            "mission_id": payload.get("mission_id"),
            "plan_id": payload.get("plan_id"),
            "schema_version": str(payload.get("schema_version") or V2),
            "proposal_id": None,
        }

    mapping = dict(cfg.need("compat.v1_to_canonical"))
    defaults = dict(cfg.get("compat.v1_defaults", {}) or {})

    def take(field_name: str) -> Any:
        src = mapping.get(field_name)
        if src:
            value = _dig(payload, str(src))
            if value not in (None, "", [], {}):
                return value
        return defaults.get(field_name)

    mission_id = payload.get("mission_id", defaults.get("mission_id"))
    plan_id = payload.get("plan_id", defaults.get("plan_id"))
    return {
        "title": str(take("title") or ""),
        "repo": str(take("repo") or ""),
        "objective": str(take("objective") or ""),
        "criteria": _as_str_tuple(take("criteria")),
        "dependencies_hint": _as_str_tuple(take("dependencies_hint")),
        "is_maturity": take("is_maturity"),
        "mission_id": mission_id,
        "plan_id": plan_id,
        "schema_version": V1,
        "proposal_id": str(payload.get("proposal_id") or "") or None,
    }


# ── 语义判据 ────────────────────────────────────────────────────────────


def has_quantified_threshold(criterion: str, cfg: GateConfig) -> bool:
    """判据是否含可量化阈值（数 + 方向/量纲）。"""
    mode = str(cfg.get("contract.quantifier_requirement", "number_with_comparator_or_symbol"))
    if mode != "number_with_comparator_or_symbol":
        raise ConfigError(f"未知的 quantifier_requirement 模式：{mode}")
    text = str(criterion or "")
    if not re.search(str(cfg.need("contract.numeric_pattern")), text):
        return False
    if re.search(str(cfg.need("contract.comparator_pattern")), text):
        return True
    return any(sym in text for sym in (cfg.get("contract.unit_symbols", []) or []))


def validate_proposal(
    payload: Mapping[str, Any],
    cfg: GateConfig,
    *,
    version: str | None = None,
) -> ContractResult:
    """fail-closed 校验：结构（JSON Schema）→ compat → 口径判据。"""
    declared = version or detect_version(payload)
    if declared not in (V1, V2):
        return ContractResult(
            ok=False,
            schema_version=str(declared or "unknown"),
            doc=None,
            errors=("schema_version_unresolved: 载荷既无 schema_version 也无任一版本特征字段",),
        )

    errors: list[str] = []
    try:
        schema = _SCHEMAS.get(_schema_path(cfg, "v1" if declared == V1 else "v2"))
        if jsonschema is None:  # 部署面兜底：没有 jsonschema 也 fail-closed
            return ContractResult(
                ok=False,
                schema_version=declared,
                doc=None,
                errors=("jsonschema_unavailable: 结构校验器缺失，按 fail-closed 处理",),
            )
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(dict(payload)), key=lambda e: list(e.path)):
            loc = "/".join(str(p) for p in err.path) or "$"
            errors.append(f"schema:{loc}: {err.message}")

        fields = to_canonical(payload, declared, cfg)

        title = str(fields["title"]).strip()
        min_title = int(cfg.get("contract.title_min_length", 1))
        if len(title) < min_title:
            errors.append("title_empty: title 去空白后为空")

        repo = str(fields["repo"]).strip()
        whitelist = cfg.repo_whitelist
        if repo not in whitelist:
            errors.append(f"repo_not_whitelisted: {repo!r} ∉ repo_whitelist({len(whitelist)} 项)")

        criteria = tuple(c for c in fields["criteria"] if str(c).strip())
        min_criteria = int(cfg.need("contract.min_criteria"))
        if len(criteria) < min_criteria:
            errors.append(
                f"criteria_too_few: {len(criteria)} < min_criteria({min_criteria})"
            )
        for idx, criterion in enumerate(criteria):
            if not has_quantified_threshold(criterion, cfg):
                errors.append(f"criteria_not_quantified: criteria[{idx}]={criterion!r}")

        objective = str(fields["objective"]).strip()
        if not objective:
            errors.append("objective_empty: goal.objective 为空")

        is_maturity = fields["is_maturity"]
        if not isinstance(is_maturity, bool):
            errors.append(
                f"is_maturity_not_bool: 实为 {type(is_maturity).__name__}={is_maturity!r}"
            )

        if errors:
            return ContractResult(False, declared, None, tuple(errors))

        doc = ProposalDoc(
            title=title,
            repo=repo,
            objective=objective,
            criteria=criteria,
            is_maturity=bool(is_maturity),
            dependencies_hint=tuple(str(d).strip() for d in fields["dependencies_hint"] if str(d).strip()),
            mission_id=fields["mission_id"] if isinstance(fields["mission_id"], int) else None,
            plan_id=fields["plan_id"] if isinstance(fields["plan_id"], int) else None,
            schema_version=declared,
            proposal_id=fields["proposal_id"],
            raw=dict(payload),
        )
        return ContractResult(True, declared, doc)

    except Exception as exc:  # noqa: BLE001 - fail-closed：任何异常都进隔离区
        errors.append(f"contract_exception: {type(exc).__name__}: {exc}")
        return ContractResult(False, str(declared), None, tuple(errors))