"""Suite 配置加载与校验（配置驱动；阈值只在数据里，不在代码里）。

配置语法（JSON 或 YAML，同一套 schema）::

    {
      "suite": "fx_integration",
      "version": 1,
      "description": "...",
      "drift_threshold": 0.03,          // 可选，缺省由 runner 的 --drift 决定
      "cases": [
        {
          "id": "fx_int_sin_n32",
          "title": "合成算例标题（不得出现领域词条）",
          "workload":       "tools.benchmark_runner.synthetic:numeric_integral",
          "inputs":         {"func": "sin", "a": 0.0, "b": 1.5, "n": 32},
          "reference":      {"workload": "tools.benchmark_runner.synthetic:integral_reference",
                             "inputs": {"func": "sin", "a": 0.0, "b": 1.5}},
          "criteria": [{"metric": "rel_error", "op": "<=", "value": 1e-10}]
        }
      ],
      "suite_criteria": [{"metric": "pass_ratio", "op": ">=", "value": 1.0}]
    }

校验策略
--------
* 结构错 / 未知算子 / 阈值类型错 → :class:`ConfigError`（退出码 3，配置问题不当算例失败）；
* **算例没写判据** 不算配置错——由 gate 在运行期判 FAIL（``no_criteria``），
  这样"忘记声明阈值"永远不会被读成"通过"；
* **suite 级 ``suite_criteria`` 必须非空**——整批结论同样要有预声明依据。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Dict, List, Mapping, Optional

from .gate import validate_criterion
from .model import ConfigError

_DEFAULT_DRIFT = 0.03
_CASE_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


def _check_dotted(path: Any, field: str, case_id: str) -> str:
    if not isinstance(path, str) or ":" not in path:
        raise ConfigError(f"case {case_id!r}: {field} must be 'module:function', got {path!r}")
    mod, _, fn = path.partition(":")
    if not mod or not fn:
        raise ConfigError(f"case {case_id!r}: {field} malformed {path!r}")
    return path


def load_config(path: str | pathlib.Path) -> Dict[str, Any]:
    """读 JSON/YAML 并校验；返回规范化后的 dict（附 ``_config_sha256``）。"""
    p = pathlib.Path(path)
    if not p.is_file():
        raise ConfigError(f"config not found: {p}")
    raw_bytes = p.read_bytes()
    text = raw_bytes.decode("utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # 局部导入：JSON 路径不强制依赖 PyYAML
        except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出可读错误
            raise ConfigError(f"PyYAML required to read {p}: {exc}") from exc
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # 文件坏了是**配置问题**（退出码 3），不是引擎内部错误
            raise ConfigError(f"invalid JSON in {p}: {exc}") from exc
    if data is None:
        raise ConfigError(f"empty config: {p}")
    cfg = validate_config(data)
    cfg["_config_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    cfg["_config_path"] = str(p)
    return cfg


def validate_config(data: Any) -> Dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}")
    suite = data.get("suite")
    if not isinstance(suite, str) or not suite.strip():
        raise ConfigError("config requires non-empty 'suite'")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ConfigError(f"suite {suite!r}: 'cases' must be a non-empty list")

    drift = data.get("drift_threshold", _DEFAULT_DRIFT)
    if not isinstance(drift, (int, float)) or isinstance(drift, bool) or not (0 < float(drift) < 1):
        raise ConfigError(f"suite {suite!r}: drift_threshold must be in (0,1), got {drift!r}")

    seen: set[str] = set()
    norm_cases: List[Dict[str, Any]] = []
    for i, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ConfigError(f"suite {suite!r}: case[{i}] must be a mapping")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid:
            raise ConfigError(f"suite {suite!r}: case[{i}] missing 'id'")
        if set(cid) - _CASE_ID_ALLOWED:
            raise ConfigError(f"suite {suite!r}: case id {cid!r} has illegal characters")
        if cid in seen:
            raise ConfigError(f"suite {suite!r}: duplicate case id {cid!r}")
        seen.add(cid)

        workload = _check_dotted(case.get("workload"), "workload", cid)
        ref = case.get("reference")
        if ref is not None:
            if not isinstance(ref, Mapping):
                raise ConfigError(f"case {cid!r}: 'reference' must be a mapping")
            _check_dotted(ref.get("workload"), "reference.workload", cid)
        metrics_dev = case.get("derived_metrics", [])
        if not isinstance(metrics_dev, list) or any(not isinstance(m, str) for m in metrics_dev):
            raise ConfigError(f"case {cid!r}: 'derived_metrics' must be a list of strings")

        crits = case.get("criteria", [])
        if crits is None:
            crits = []
        if not isinstance(crits, list):
            raise ConfigError(f"case {cid!r}: 'criteria' must be a list")
        crits = [validate_criterion(c) for c in crits]

        norm_cases.append({
            "id": cid,
            "title": str(case.get("title", cid)),
            "workload": workload,
            "inputs": dict(case.get("inputs") or {}),
            "reference": dict(ref) if ref else None,
            "derived_metrics": list(metrics_dev),
            "criteria": crits,
            "tags": list(case.get("tags") or []),
        })

    suite_crits = data.get("suite_criteria")
    if not isinstance(suite_crits, list) or not suite_crits:
        # fail-closed：suite 级也必须预声明聚合判据，否则"整批通过"没有可裁依据
        raise ConfigError(f"suite {suite!r}: non-empty 'suite_criteria' is required "
                          f"(a suite with no predeclared aggregate criterion cannot be called passed)")
    suite_crits = [validate_criterion(c) for c in suite_crits]

    return {
        "suite": suite.strip(),
        "version": int(data.get("version", 1) or 1),
        "description": str(data.get("description", "")),
        "drift_threshold": float(drift),
        "cases": norm_cases,
        "suite_criteria": suite_crits,
    }


def config_digest(cfg: Mapping[str, Any]) -> Optional[str]:
    """配置内容摘要（不依赖文件字节，可用于程序化注入的配置）。"""
    payload = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()