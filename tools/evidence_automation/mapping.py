"""能力↔测试/探针 映射配置的装载与静态校验。

零领域语义：本模块不认识任何具体能力名，``capability_id`` 只作为不透明的键；
一切具体绑定（测试路径、探针名、阈值、代码 glob）来自配置文件。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_RANK = {level: i for i, level in enumerate(LEVELS)}

MAPPING_SCHEMA = "capability_evidence_map/1.0"
KNOWN_PROBES = (
    "git_plumbing",
    "compose_service_inventory",
    "nginx_config_inventory",
    "http_health_probe",
    "hull_geometry_identity",
)
ALLOWED_SCOPES = ("in_repo_tests", "runtime_probe", "external")


class MappingError(ValueError):
    """映射配置不合法（缺字段/未知探针/测试模块不存在等）。"""


@dataclass(frozen=True)
class CapabilityBinding:
    """单条能力的证据绑定。"""

    capability_id: str
    evidence_scope: str
    test_modules: tuple[str, ...] = ()
    probes: tuple[str, ...] = ()
    code_globs: tuple[str, ...] = ()
    acceptance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "evidence_scope": self.evidence_scope,
            "test_modules": list(self.test_modules),
            "probes": list(self.probes),
            "code_globs": list(self.code_globs),
            "acceptance": dict(self.acceptance),
        }


@dataclass(frozen=True)
class EvidenceMap:
    schema: str
    artifact_root: str
    defaults: dict[str, Any]
    bindings: dict[str, CapabilityBinding]
    source_path: str

    @property
    def count(self) -> int:
        return len(self.bindings)

    def acceptance_for(self, capability_id: str) -> dict[str, Any]:
        """默认阈值与能力级覆盖合并后的验收口径。"""
        merged = dict(self.defaults)
        merged.update(self.bindings[capability_id].acceptance)
        return merged

    def test_modules(self) -> tuple[str, ...]:
        """全部映射测试模块（去重、保序）—— runner 一次跑完。"""
        seen: dict[str, None] = {}
        for binding in self.bindings.values():
            for module in binding.test_modules:
                seen.setdefault(module, None)
        return tuple(seen)


def load_mapping(path: str | Path, *, root: str | Path = ".",
                 require_tests_exist: bool = True) -> EvidenceMap:
    """装载映射配置。

    Parameters
    ----------
    path
        映射 JSON 路径。
    root
        仓库根（用于校验测试模块存在性）。
    require_tests_exist
        为 True 时，映射里声明的测试模块必须真实存在于磁盘，否则抛
        :class:`MappingError`（防止"映射一条不存在的测试"虚增覆盖率）。
    """
    path = Path(path)
    if not path.is_absolute():
        path = Path(root) / path
    if not path.exists():
        raise MappingError(f"mapping_not_found:{path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 配置错误路径
        raise MappingError(f"mapping_not_json:{exc}") from exc

    if raw.get("schema") != MAPPING_SCHEMA:
        raise MappingError(f"mapping_schema_mismatch:{raw.get('schema')}")
    root_path = Path(root)
    defaults = dict(raw.get("acceptance_defaults", {}))
    artifact_root = str(raw.get("artifact_root", "artifacts/capability_evidence"))

    bindings: dict[str, CapabilityBinding] = {}
    for cap_id, entry in (raw.get("capabilities") or {}).items():
        if not isinstance(entry, dict):
            raise MappingError(f"binding_not_object:{cap_id}")
        scope = str(entry.get("evidence_scope", "in_repo_tests"))
        if scope not in ALLOWED_SCOPES:
            raise MappingError(f"binding_bad_scope:{cap_id}:{scope}")
        modules = tuple(str(m) for m in entry.get("test_modules", []))
        probes = tuple(str(p) for p in entry.get("probes", []))
        for probe in probes:
            if probe not in KNOWN_PROBES:
                raise MappingError(f"binding_unknown_probe:{cap_id}:{probe}")
        if require_tests_exist:
            _assert_tests_exist(root_path, cap_id, modules)
        bindings[str(cap_id)] = CapabilityBinding(
            capability_id=str(cap_id),
            evidence_scope=scope,
            test_modules=modules,
            probes=probes,
            code_globs=tuple(str(g) for g in entry.get("code_globs", [])),
            acceptance=dict(entry.get("acceptance", {})),
        )
    if not bindings:
        raise MappingError("mapping_empty")
    return EvidenceMap(schema=MAPPING_SCHEMA, artifact_root=artifact_root,
                       defaults=defaults, bindings=bindings, source_path=str(path))


def _assert_tests_exist(root: Path, cap_id: str, modules: Iterable[str]) -> None:
    missing = [m for m in modules if not (root / m).exists()]
    if missing:
        raise MappingError(f"binding_test_missing:{cap_id}:{','.join(missing)}")


def uninmapped_capabilities(capability_ids: Iterable[str],
                            mapping: EvidenceMap) -> list[str]:
    """注册表里有、映射里没有的能力（覆盖率分母的另一半）。"""
    return sorted(cid for cid in capability_ids if cid not in mapping.bindings)