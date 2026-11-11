"""采集规格（spec）加载与校验。

规格是**数据**：能力 → 面（surface 端点）/ 探针（probes）/ 参考工件（reference）/
测试模块（tests）。代码侧不含任何领域词条，所以换一套 spec 就能采集另一组能力。

schema 要点（``evidence_collection.spec.v1``）::

    {
      "schema": "evidence_collection.spec.v1",
      "thresholds": {"real_tolerance_pct": 3.0, "accept_tolerance_pct": 15.0, ...},
      "sources": {"ledger": "...", "runtime": {...}, "out_dir": "..."},
      "capabilities": {
        "<capability_id>": {
          "surface": ["/api/v1/..."],                    # 期望在在线 OpenAPI 里出现的路径
          "reference": {"path": "...", "value_path": "results.*.error_pct",
                        "aggregate": "worst", "tolerance_pct": 3.0, "metric": "..."},
          "tests": ["tests/..."],
          "probes": [{"id": "...", "method": "POST", "path": "...", "body": {...},
                      "criteria": [{"id": "...", "kind": "...", "class": "contract"}]}]
        }
      }
    }
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "evidence_collection.spec.v1"

CRITERION_KINDS: tuple[str, ...] = (
    "status",          # HTTP 状态码
    "field_present",   # 字段存在
    "field_range",     # 数值字段落在 [min, max]
    "list_min_len",    # 列表字段长度下限
    "identity",        # lhs/rhs 表达式比对（绝对/相对误差、上下界）
    "monotonic",       # 参数扫描下单调性
    "non_constant",    # 参数扫描下必须有可测变化（防硬编码常量）
)

#: 判据类别：决定自动定级的"证据力度"
#: * ``contract``   契约面（端点存在、字段齐、有数值）——只能支撑 WITHHELD
#: * ``invariant``  自洽/物理不变量——违反即正面反证（MOCK）
#: * ``reference``  与外部参考值的误差——唯一能把等级顶到 REAL 的类别
CRITERION_CLASSES: tuple[str, ...] = ("contract", "invariant", "reference")

AGGREGATES: tuple[str, ...] = ("worst", "best", "mean")


class SpecError(ValueError):
    """规格非法（宁可红，不静默降级）。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SpecError(msg)


def _validate_criterion(cap_id: str, probe_id: str, crit: dict, idx: int) -> None:
    tag = f"{cap_id}/{probe_id}/criteria[{idx}]"
    _require(isinstance(crit, dict), f"{tag}: must be an object")
    kind = crit.get("kind")
    _require(kind in CRITERION_KINDS, f"{tag}: unsupported kind {kind!r}")
    _require(crit.get("id"), f"{tag}: missing id")
    _require(crit.get("class", "contract") in CRITERION_CLASSES,
             f"{tag}: class must be one of {CRITERION_CLASSES}")
    if kind == "status":
        _require(isinstance(crit.get("expect"), list) and crit["expect"], f"{tag}: expect must be a list")
    if kind in {"field_present", "field_range", "list_min_len", "monotonic", "non_constant"}:
        # 根路径（"" = 整个响应体）只对"检查响应本身"的两种判据有意义
        allow_root = kind in {"field_present", "list_min_len"}
        has_path = isinstance(crit.get("path"), str) and (crit["path"] or allow_root)
        _require(has_path, f"{tag}: missing path")
    if kind == "identity":
        _require(isinstance(crit.get("lhs"), str) and crit["lhs"], f"{tag}: missing lhs")
        _require(isinstance(crit.get("rhs"), str) and crit["rhs"], f"{tag}: missing rhs")
        _require(crit.get("mode", "abs_rel") in {"abs_rel", "lte", "gte", "abs"},
                 f"{tag}: unsupported mode")
    if kind == "monotonic":
        _require(crit.get("direction", "increasing") in {"increasing", "decreasing"},
                 f"{tag}: direction must be increasing/decreasing")


def validate_spec(raw: dict) -> dict:
    _require(isinstance(raw, dict), "spec must be an object")
    _require(raw.get("schema") == SCHEMA, f"schema must be {SCHEMA}")
    thresholds = raw.get("thresholds") or {}
    for key in ("real_tolerance_pct", "accept_tolerance_pct"):
        _require(isinstance(thresholds.get(key), (int, float)), f"thresholds.{key} must be numeric")
    _require(thresholds["real_tolerance_pct"] <= thresholds["accept_tolerance_pct"],
             "real_tolerance_pct must be <= accept_tolerance_pct (否则 REAL 口径宽于可接受口径)")
    sources = raw.get("sources") or {}
    for key in ("ledger", "out_dir"):
        _require(isinstance(sources.get(key), str) and sources[key], f"sources.{key} required")
    _require(isinstance(sources.get("runtime"), dict), "sources.runtime required")
    caps = raw.get("capabilities")
    _require(isinstance(caps, dict) and caps, "capabilities must be a non-empty object")
    for cap_id, entry in caps.items():
        _require(isinstance(entry, dict), f"{cap_id}: entry must be an object")
        surface = entry.get("surface") or []
        _require(isinstance(surface, list), f"{cap_id}: surface must be a list")
        tests = entry.get("tests") or []
        _require(isinstance(tests, list), f"{cap_id}: tests must be a list")
        ref = entry.get("reference")
        if ref is not None:
            _require(isinstance(ref, dict), f"{cap_id}: reference must be an object")
            _require(bool(ref.get("path")) and bool(ref.get("value_path")),
                     f"{cap_id}: reference needs path + value_path")
            _require(ref.get("aggregate", "worst") in AGGREGATES,
                     f"{cap_id}: reference.aggregate must be one of {AGGREGATES}")
            _require(isinstance(ref.get("tolerance_pct", 3.0), (int, float)),
                     f"{cap_id}: reference.tolerance_pct must be numeric")
        probes = entry.get("probes") or []
        _require(isinstance(probes, list), f"{cap_id}: probes must be a list")
        for probe in probes:
            _require(isinstance(probe, dict), f"{cap_id}: probe must be an object")
            _require(bool(probe.get("id")), f"{cap_id}: probe missing id")
            _require(probe.get("method", "GET") in {"GET", "POST"}, f"{cap_id}: probe method must be GET/POST")
            _require(bool(probe.get("path")), f"{cap_id}: probe missing path")
            for i, crit in enumerate(probe.get("criteria") or []):
                _validate_criterion(cap_id, probe["id"], crit, i)
        if not (surface or probes or tests or ref):
            raise SpecError(f"{cap_id}: entry declares no evidence source at all")
    return raw


def load_spec(path: str | Path) -> dict:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return validate_spec(raw)


def spec_sha256(spec: dict) -> str:
    """规格内容摘要（判据/阈值的"版本号"，进每条验收工件）。"""
    payload = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()