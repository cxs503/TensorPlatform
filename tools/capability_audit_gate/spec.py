"""审计 spec 装载与校验（能力清单 / 路径绑定 / 基准定义都由外部注入）。

spec 是**数据**不是代码：21 项能力、各自证据绑定、基准工件口径全部写在
``config/capability_audit_gate/spec.json``（或 ``--spec`` 指定的任意 JSON）。
这样扫描器保持零领域语义，域内容全部留在配置/工件里。

spec 结构（v1）::

    {
      "schema_version": "1.0",
      "capabilities": [
        {
          "id": "cap.alpha",                     // 必需，唯一
          "bindings": {"code": [...], "tests": [...]},   // 可选；给了就只信它
          "patterns": {"code": [...], "tests": [...]},   // 可选；未给 bindings 时按路径匹配
          "benchmark": {"kind": "...", ...},              // 可选；见 benchmark.py
          "expected_level": "REAL"                       // 可选；仅标注语料（金标准）用
        }
      ],
      "thresholds": {"real_tolerance_pct": 3.0, ...}      // 可选；覆盖默认阈值
    }

校验失败一律抛 :class:`SpecError`，CLI 以退出码 4 退出（配置错误 ≠ 审计结论）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 默认阈值（**先于**观测结果声明，见 docs/CAPABILITY_AUDIT_GATE.md §判定规则）
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "real_tolerance_pct": 3.0,      # REAL 要求数值误差 ≤3%
    "acceptable_tolerance_pct": 15.0,  # >15% 视为不可接受（本项目精度标准）
    "stub_ratio_max": 0.5,          # 空壳函数占比 ≥ 该值 ⇒ STUB
    "sloc_min": 6,                  # 有效代码行 < 该值 ⇒ STUB
    "mock_marker_hits_min": 2,      # 硬编码/造假标记命中 ≥ 该值 ⇒ MOCK
    "constant_return_ratio_max": 0.5,  # 常量直返占比 ≥ 该值 ⇒（一个）MOCK 信号
    "mock_min_independent_signals": 2,
    "pattern_can_refute": False,     # 模式匹配证据是否允许做正面反证（默认否）  # 判 MOCK 需要的独立信号族数（单信号只 WARN）
    "min_numeric_assertions": 1,    # 数值断言数 < 该值 ⇒ 测试不计入证据
    "max_impl_files": 12,           # 每能力最多解析的实现文件数（防噪音放大）
    "max_test_files": 12,
}

BINDING_KEYS = ("code", "tests")
BENCHMARK_KINDS = ("json_cases", "json_values", "none")


class SpecError(ValueError):
    """spec 结构非法（CLI 退出码 4）。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SpecError(msg)


def validate_spec(raw: dict) -> dict:
    _require(isinstance(raw, dict), "spec 顶层必须是 JSON object")
    caps = raw.get("capabilities")
    _require(isinstance(caps, list) and caps, "spec.capabilities 必须是非空数组")
    seen: set[str] = set()
    out_caps = []
    for i, cap in enumerate(caps):
        _require(isinstance(cap, dict), f"capabilities[{i}] 必须是 object")
        cid = str(cap.get("id") or "").strip()
        _require(bool(cid), f"capabilities[{i}] 缺 id")
        _require(cid not in seen, f"能力 id 重复: {cid}")
        seen.add(cid)
        bindings = cap.get("bindings") or {}
        _require(isinstance(bindings, dict), f"{cid}.bindings 必须是 object")
        for k, v in bindings.items():
            _require(k in BINDING_KEYS, f"{cid}.bindings 只允许 {BINDING_KEYS}，出现 {k!r}")
            _require(isinstance(v, list) and all(isinstance(x, str) for x in v),
                     f"{cid}.bindings.{k} 必须是字符串数组")
        patterns = cap.get("patterns") or {}
        _require(isinstance(patterns, dict), f"{cid}.patterns 必须是 object")
        for k, v in patterns.items():
            _require(k in BINDING_KEYS, f"{cid}.patterns 只允许 {BINDING_KEYS}，出现 {k!r}")
            _require(isinstance(v, list) and all(isinstance(x, str) for x in v),
                     f"{cid}.patterns.{k} 必须是字符串数组")
        bench = cap.get("benchmark")
        if bench is not None:
            _require(isinstance(bench, dict), f"{cid}.benchmark 必须是 object 或 null")
            kind = bench.get("kind")
            _require(kind in BENCHMARK_KINDS,
                     f"{cid}.benchmark.kind 必须是 {BENCHMARK_KINDS}，出现 {kind!r}")
            if kind != "none":
                _require(bool(bench.get("path")), f"{cid}.benchmark 缺 path")
                _require(bool(bench.get("check_class")), f"{cid}.benchmark 缺 check_class")
        exp = cap.get("expected_level")
        if exp is not None:
            _require(str(exp).upper() in {"REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"},
                     f"{cid}.expected_level 非法: {exp!r}")
        out_caps.append({
            "id": cid,
            "bindings": {k: list(v) for k, v in bindings.items()},
            "patterns": {k: list(v) for k, v in patterns.items()},
            "benchmark": bench,
            "expected_level": (str(exp).upper() if exp is not None else None),
        })
    thresholds = dict(DEFAULT_THRESHOLDS)
    override = raw.get("thresholds") or {}
    _require(isinstance(override, dict), "spec.thresholds 必须是 object")
    thresholds.update(override)
    return {
        "schema_version": str(raw.get("schema_version") or "1.0"),
        "title": raw.get("title") or "",
        "capabilities": out_caps,
        "thresholds": thresholds,
    }


def load_spec(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SpecError(f"spec not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 直接报错即可
        raise SpecError(f"spec is not valid JSON: {exc}") from exc
    return validate_spec(raw)


def spec_from_registry(registry_path: str | Path) -> dict:
    """把能力定义注册表（``config/capabilities.json``）转成 spec。

    注册表只带模式匹配规则（零领域语义），因此转出的 spec 只有 ``patterns``、
    没有显式绑定与基准定义 —— 这类能力最多只能拿到 WITHHELD（证据链不足），
    这正是「仅凭模式命中不得判 REAL」的纪律。
    """
    p = Path(registry_path)
    if not p.is_file():
        raise SpecError(f"registry not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    caps = raw.get("capabilities") or []
    _require(bool(caps), "registry.capabilities 为空")
    out = []
    for c in caps:
        cid = str(c.get("capability_id") or c.get("id") or "").strip()
        _require(bool(cid), "registry entry 缺 capability_id")
        out.append({
            "id": cid,
            "bindings": {},
            "patterns": {
                "code": list(c.get("code_patterns") or []),
                "tests": list(c.get("test_patterns") or []),
            },
            "benchmark": None,
            "expected_level": None,
        })
    return validate_spec({"schema_version": "1.0", "title": raw.get("title", ""),
                          "capabilities": out})