"""v2 spec 装载与校验：基础台账 spec（数据）+ 可执行证据声明（数据）。

结构（``schema_version = 2.0``）::

    {
      "schema_version": "2.0",
      "title": "...",
      "base_spec": "config/<...>/spec.json",        // 复用既有 21 项能力清单
      "execution": {"timeout_s": 300, "env_markers": ["ModuleNotFoundError", ...]},
      "thresholds": {"probe_min_contradictions": 1, ...},
      "capabilities": {
        "<capability id>": {
          "pytest": {"selectors": ["tests/x.py::T::t"], "timeout_s": 300},
          "probe": {
            "target": "module.path:function",
            "path_prepend": ["backend"],
            "params": ["x", "y"],
            "points": [{"x": 0.1, "y": 1.0}, ...],
            "outputs": ["A", "B"],
            "checks": [{"kind": "identity", "id": "...", "expr": "...", "tolerance": 1e-9}]
          }
        }
      }
    }

``capabilities`` 的键必须是 ``base_spec`` 里已存在的能力 id（多一个 = 配置错误，
防止拼写错误变成"悄悄没有证据"）；少一些是允许的，缺证据的能力在报告里显式记
``NO_EXEC_BINDING``，不静默吞掉。

校验失败抛 :class:`SpecV2Error`（CLI 退出码 4：配置错误 ≠ 审计结论）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.capability_audit_gate.spec import SpecError, load_spec

CHECK_KINDS = ("identity", "sensitivity_required", "sensitivity_forbidden", "determinism")

#: v2 追加阈值（先于观测声明，见 docs/CAPABILITY_AUDIT_V2.md §判定级联）
DEFAULT_THRESHOLDS_V2: dict[str, Any] = {
    "probe_min_contradictions": 1,   # 可执行不变量被证伪 ≥ 该数 ⇒ MOCK
    "probe_rel_eps": 1e-12,          # 相对展布的分母保护
    "exec_numeric_assertions_min": 1,  # 计入 REAL 的执行证据需 ≥ 该数数值断言
}


class SpecV2Error(ValueError):
    """v2 spec 非法（CLI 退出码 4）。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SpecV2Error(msg)


def _validate_pytest(entry: dict, cap_id: str) -> dict:
    _require(isinstance(entry, dict), f"{cap_id}.pytest 必须是 object")
    sel = entry.get("selectors")
    _require(isinstance(sel, list) and sel and all(isinstance(s, str) and s for s in sel),
             f"{cap_id}.pytest.selectors 必须是非空字符串数组")
    return {"selectors": list(sel),
            "timeout_s": int(entry.get("timeout_s") or 0) or None,
            "confcutdir": (str(entry["confcutdir"]) if entry.get("confcutdir") else None),
            "minimal_plugins": bool(entry.get("minimal_plugins"))}


def _validate_probe(entry: dict, cap_id: str) -> dict:
    _require(isinstance(entry, dict), f"{cap_id}.probe 必须是 object")
    target = str(entry.get("target") or "").strip()
    _require(":" in target, f"{cap_id}.probe.target 需形如 module:function")
    params = entry.get("params")
    _require(isinstance(params, list) and params and all(isinstance(p, str) for p in params),
             f"{cap_id}.probe.params 必须是非空字符串数组")
    points = entry.get("points")
    _require(isinstance(points, list) and len(points) >= 1, f"{cap_id}.probe.points 非空")
    for i, pt in enumerate(points):
        _require(isinstance(pt, dict) and set(params) <= set(pt),
                 f"{cap_id}.probe.points[{i}] 必须含全部 params")
    outputs = entry.get("outputs")
    _require(isinstance(outputs, list) and outputs and all(isinstance(o, str) for o in outputs),
             f"{cap_id}.probe.outputs 必须是非空字符串数组")
    checks = entry.get("checks")
    _require(isinstance(checks, list) and checks, f"{cap_id}.probe.checks 非空")
    out_checks = []
    for i, ck in enumerate(checks):
        _require(isinstance(ck, dict), f"{cap_id}.probe.checks[{i}] 必须是 object")
        kind = ck.get("kind")
        _require(kind in CHECK_KINDS, f"{cap_id}.probe.checks[{i}].kind 非法: {kind!r}")
        cid = str(ck.get("id") or "").strip()
        _require(bool(cid), f"{cap_id}.probe.checks[{i}] 缺 id")
        if kind == "identity":
            _require(bool(str(ck.get("expr") or "").strip()),
                     f"{cap_id}.probe.checks[{i}] identity 缺 expr")
            _require(isinstance(ck.get("tolerance"), (int, float)),
                     f"{cap_id}.probe.checks[{i}] identity 缺 tolerance")
        elif kind in ("sensitivity_required", "sensitivity_forbidden"):
            _require(str(ck.get("input") or "") in params,
                     f"{cap_id}.probe.checks[{i}].input 必须是 params 之一")
            _require(str(ck.get("output") or "") in outputs,
                     f"{cap_id}.probe.checks[{i}].output 必须是 outputs 之一")
            _require(isinstance(ck.get("threshold"), (int, float)),
                     f"{cap_id}.probe.checks[{i}] 缺 threshold")
        out_checks.append(dict(ck))
    path_prepend = entry.get("path_prepend") or []
    _require(isinstance(path_prepend, list) and all(isinstance(p, str) for p in path_prepend),
             f"{cap_id}.probe.path_prepend 必须是字符串数组")
    return {
        "target": target,
        "path_prepend": list(path_prepend),
        "params": list(params),
        "points": [dict(p) for p in points],
        "outputs": list(outputs),
        "checks": out_checks,
    }


def load_spec_v2(path: str | Path, root: str | Path = ".") -> dict:
    """装载并校验 v2 spec；``base_spec`` 相对 ``root`` 解析。"""
    p = Path(path)
    _require(p.is_file(), f"v2 spec not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 防御
        raise SpecV2Error(f"v2 spec JSON 解析失败: {exc}") from exc
    _require(isinstance(raw, dict), "v2 spec 顶层必须是 object")
    base_rel = str(raw.get("base_spec") or "").strip()
    _require(bool(base_rel), "v2 spec 缺 base_spec")
    base_path = Path(root) / base_rel
    _require(base_path.is_file(), f"base_spec not found: {base_path}")
    try:
        base = load_spec(base_path)
    except SpecError as exc:
        raise SpecV2Error(f"base_spec 非法: {exc}") from exc

    known = {c["id"] for c in base["capabilities"]}
    caps = raw.get("capabilities") or {}
    _require(isinstance(caps, dict), "v2 spec.capabilities 必须是 object（capability id → 声明）")
    orphans = sorted(set(caps) - known)
    _require(not orphans, f"capabilities 声明了 base_spec 里不存在的能力 id: {orphans}")

    exec_map: dict[str, dict] = {}
    for cap_id, entry in caps.items():
        _require(isinstance(entry, dict), f"{cap_id} 的声明必须是 object")
        item: dict[str, Any] = {}
        if entry.get("pytest") is not None:
            item["pytest"] = _validate_pytest(entry["pytest"], cap_id)
        if entry.get("probe") is not None:
            item["probe"] = _validate_probe(entry["probe"], cap_id)
        if entry.get("control_probe") is not None:
            item["control_probe"] = _validate_probe(entry["control_probe"], cap_id)
        exec_map[cap_id] = item

    thresholds = dict(DEFAULT_THRESHOLDS_V2)
    override = raw.get("thresholds") or {}
    _require(isinstance(override, dict), "v2 spec.thresholds 必须是 object")
    thresholds.update(override)

    execution = dict(raw.get("execution") or {})
    markers = execution.get("env_markers") or []
    _require(isinstance(markers, list) and all(isinstance(m, str) for m in markers),
             "execution.env_markers 必须是字符串数组")

    return {
        "schema_version": str(raw.get("schema_version") or "2.0"),
        "title": raw.get("title") or base.get("title") or "",
        "base_spec": base,
        "base_spec_path": Path(base_rel).as_posix(),
        "exec": exec_map,
        "thresholds": thresholds,
        "execution": {
            "timeout_s": int(execution.get("timeout_s") or 300),
            "env_markers": list(markers),
            "confcutdir": (str(execution["confcutdir"]) if execution.get("confcutdir") else None),
            "minimal_plugins": bool(execution.get("minimal_plugins")),
        },
    }