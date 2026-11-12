"""contract_schema —— 派单契约 v1 的静态校验与工件口径校验。

契约文档（``dispatch-contract/v1``）
-----------------------------------
::

    {
      "node_id": "<str>",
      "contract_version": "dispatch-contract/v1",
      "input_contract":  [requirement, ...],
      "output_contract": [requirement, ...],
      "version": "1.0.0",          # 可选，契约自身版本
      "context_package": {         # 可选，干货包声明（context-package/v1）
        "task_class": "dev",       # 可选：强制任务类（不填=按目标文本判定）
        "slots": ["ko_index", "negative_knowledge"],
        "budget_tokens": 1200,
        "modes": ["report"]
      }
    }

requirement::

    {
      "kind": "upstream_delivery" | "artifact" | "schema",
      "ref":  "<上游 mission id 或 工件路径或 工件类别>",
      "required": true,            # 缺省 true
      "fields":   ["<字段名>", ...],   # 可选：载荷必需字段
      "units":    {"<字段>": "<单位>"},  # 可选：单位/口径
      "version":  {"major": 2, "min_minor": 0},  # 可选：版本兼容下界
      "note": "..."                # 可选：人类可读说明（不参与判定）
    }

全部阈值/状态集/单位别名来自 ``config/dispatch_contract.v1.json``；本模块不含任何
领域词条。函数均不抛异常（校验结果以 ``(ok, errors)`` 返回），保证派单链路安全。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "dispatch_contract.v1.json"

CONTRACT_VERSION = "dispatch-contract/v1"


def load_config(path: Path | str | None = None) -> dict:
    """读取门配置；缺文件时退回内置最小配置（不崩，标记 degraded）。"""
    p = Path(path) if path else DEFAULT_CONFIG
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {
            "schema_version": "dispatch-contract-gate/v1",
            "contract_version": CONTRACT_VERSION,
            "required_top_level": ["node_id", "contract_version", "input_contract", "output_contract"],
            "requirement_required_keys": ["kind", "ref"],
            "requirement_kinds": ["upstream_delivery", "artifact", "schema"],
            "satisfied_upstream_status": ["succeeded"],
            "non_terminal_status": ["queued", "running", "awaiting_pr_merge"],
            "blocked_status": ["failed", "stopped"],
            "unit_aliases": {},
            "version_compat_rule": "major_equal_and_minor_at_least",
            "gate": {"block_when_any_required_unmet": True, "score_threshold": 0.0,
                     "weights": {"upstream_delivery": 1.0, "artifact": 1.0, "schema": 1.0},
                     "degrade_on_missing_contract": "identity_pass",
                     "degrade_on_invalid_dispatch": "identity_pass"},
            "_degraded_config": True,
        }
    cfg.setdefault("_config_path", str(p))
    return cfg


# --------------------------------------------------------------------------- #
# 单位口径
# --------------------------------------------------------------------------- #
def canonical_unit(unit: Any, cfg: Mapping[str, Any] | None = None) -> str | None:
    """把单位串归一到配置里的规范形；未知单位返回 ``None``（调用方按不符处理）。"""
    cfg = cfg or load_config()
    if unit is None:
        return None
    raw = str(unit).strip()
    aliases = cfg.get("unit_aliases") or {}
    if raw in aliases:                     # 已是规范形
        return raw
    low = raw.lower()
    for canon, forms in aliases.items():
        for f in forms:
            if str(f).lower() == low or str(f) == raw:
                return canon
    return None


def units_match(declared: Any, provided: Any, cfg: Mapping[str, Any] | None = None) -> bool:
    """声明单位 vs 实际单位是否同一口径（按规范形比较）。"""
    cfg = cfg or load_config()
    cd, cp = canonical_unit(declared, cfg), canonical_unit(provided, cfg)
    if cd is None or cp is None:
        return str(declared).strip().lower() == str(provided).strip().lower()
    return cd == cp


# --------------------------------------------------------------------------- #
# 版本兼容
# --------------------------------------------------------------------------- #
_VER = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(v: Any) -> tuple[int, int, int] | None:
    if v is None:
        return None
    m = _VER.match(str(v).strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def version_compatible(required: Any, provided: Any, cfg: Mapping[str, Any] | None = None) -> bool:
    """``required`` 为 ``{major, min_minor}`` 或版本串；``provided`` 为上游版本串。

    规则（配置项 ``version_compat_rule``）：主版本相等且次版本不低于下界。
    任一侧不可解析 → ``False``（fail-closed，交由上层记为 CONTRACT 缺陷）。
    """
    cfg = cfg or load_config()
    if required is None:
        return True
    if isinstance(required, Mapping):
        req_major = required.get("major")
        req_minor = required.get("min_minor", 0)
        if req_major is None:
            return True
    else:
        pv = parse_version(required)
        if pv is None:
            return False
        req_major, req_minor = pv[0], pv[1]
    pv = parse_version(provided)
    if pv is None:
        return False
    if cfg.get("version_compat_rule") == "major_equal_and_minor_at_least":
        return pv[0] == int(req_major) and pv[1] >= int(req_minor)
    return pv >= (int(req_major), int(req_minor), 0)


# --------------------------------------------------------------------------- #
# 契约静态校验
# --------------------------------------------------------------------------- #
def validate_requirement(req: Any, cfg: Mapping[str, Any] | None = None) -> list[str]:
    """校验单条需求声明，返回错误码列表（空列表=合法）。"""
    cfg = cfg or load_config()
    errs: list[str] = []
    if not isinstance(req, Mapping):
        return ["REQ_NOT_OBJECT"]
    for key in cfg.get("requirement_required_keys", ["kind", "ref"]):
        if key not in req or req.get(key) in (None, ""):
            errs.append(f"REQ_MISSING_KEY:{key}")
    kind = req.get("kind")
    if kind is not None and kind not in cfg.get("requirement_kinds", []):
        errs.append(f"REQ_UNKNOWN_KIND:{kind}")
    if "required" in req and not isinstance(req["required"], bool):
        errs.append("REQ_REQUIRED_NOT_BOOL")
    if "fields" in req and not isinstance(req["fields"], (list, tuple)):
        errs.append("REQ_FIELDS_NOT_LIST")
    if "units" in req and not isinstance(req["units"], Mapping):
        errs.append("REQ_UNITS_NOT_OBJECT")
    v = req.get("version")
    if v is not None and not isinstance(v, (Mapping, str)):
        errs.append("REQ_VERSION_NOT_OBJECT")
    return errs


def validate_contract(contract: Any, cfg: Mapping[str, Any] | None = None) -> tuple[bool, list[str]]:
    """校验整份契约文档；返回 ``(ok, errors)``，永不抛异常。"""
    cfg = cfg or load_config()
    try:
        if not isinstance(contract, Mapping):
            return False, ["CONTRACT_NOT_OBJECT"]
        errs: list[str] = []
        for key in cfg.get("required_top_level", []):
            if key not in contract:
                errs.append(f"CONTRACT_MISSING_KEY:{key}")
        cv = contract.get("contract_version")
        if cv is not None and cv != cfg.get("contract_version", CONTRACT_VERSION):
            errs.append(f"CONTRACT_VERSION_UNSUPPORTED:{cv}")
        for side in ("input_contract", "output_contract"):
            reqs = contract.get(side)
            if reqs is None:
                continue
            if not isinstance(reqs, (list, tuple)):
                errs.append(f"CONTRACT_{side.upper()}_NOT_LIST")
                continue
            for i, req in enumerate(reqs):
                for e in validate_requirement(req, cfg):
                    errs.append(f"{side}[{i}]:{e}")
        # 干货包声明（context-package/v1，可选字段；校验器自带 fail-open，永不抛）
        if "context_package" in contract:
            try:
                from tools.context_package.package_schema import validate_decl
                for e in validate_decl(contract.get("context_package")):
                    errs.append(f"context_package:{e}")
            except Exception:  # noqa: BLE001 —— 声明校验缺失不得拖死派单链路
                pass
        return (len(errs) == 0), errs
    except Exception as exc:  # noqa: BLE001 —— 校验器不得拖死派单链路
        return False, [f"CONTRACT_VALIDATE_ERROR:{type(exc).__name__}"]


# --------------------------------------------------------------------------- #
# 工件载荷口径校验
# --------------------------------------------------------------------------- #
def check_payload(payload: Any, req: Mapping[str, Any], cfg: Mapping[str, Any] | None = None) -> list[dict]:
    """按需求声明校验一个上游工件载荷；返回未满足项列表（空=满足）。"""
    cfg = cfg or load_config()
    unmet: list[dict] = []
    fields = req.get("fields") or []
    units = req.get("units") or {}
    ver = req.get("version")
    if payload is None:
        if fields or units or ver is not None:
            unmet.append({"code": "PAYLOAD_MISSING", "ref": req.get("ref"),
                          "detail": "需求声明了字段/单位/版本，但上游未提供载荷"})
        return unmet
    if not isinstance(payload, Mapping):
        unmet.append({"code": "PAYLOAD_NOT_OBJECT", "ref": req.get("ref"), "detail": type(payload).__name__})
        return unmet
    for f in fields:
        if f not in payload:
            unmet.append({"code": f"FIELD_MISSING:{f}", "ref": req.get("ref"),
                          "detail": "载荷缺必需字段"})
    for f, want in units.items():
        if f not in payload:
            continue  # 字段缺失已单独记；此处不重复报
        got = payload[f]
        if isinstance(got, Mapping):
            got_unit = got.get("unit")
        else:
            got_unit = payload.get(f"{f}.unit", payload.get("unit"))
        if not units_match(want, got_unit, cfg):
            unmet.append({"code": f"UNIT_MISMATCH:{f}", "ref": req.get("ref"),
                          "detail": f"声明={want} 上游={got_unit}"})
    if ver is not None:
        pv = payload.get("version") if isinstance(payload, Mapping) else None
        if not version_compatible(ver, pv, cfg):
            unmet.append({"code": "VERSION_INCOMPATIBLE", "ref": req.get("ref"),
                          "detail": f"要求={ver} 上游={pv}"})
    return unmet


def iter_requirements(contract: Mapping[str, Any], side: str = "input_contract") -> Iterable[Mapping[str, Any]]:
    reqs = contract.get(side) if isinstance(contract, Mapping) else None
    if isinstance(reqs, (list, tuple)):
        for r in reqs:
            if isinstance(r, Mapping):
                yield r