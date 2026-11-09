#!/usr/bin/env python3
"""策略装载：行为全配置化，代码内不写业务常量。

三层合并：:data:`DEFAULTS` ← 策略文件 ← 调用方 dict 覆盖。未知键**报错**而不是忽略
——拼错的配置项静默失效比报错危险得多（会让门看起来在跑其实没生效）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .model import POLICY_SCHEMA, WriteGateError

DEFAULTS: Dict[str, Any] = {
    "schema": POLICY_SCHEMA,
    "repo": {"name": None, "root_markers": [".git"]},
    "whitelist": {
        "enabled": True,
        "allow_globs": ["**"],
        "deny_globs": [".git", ".git/**"],
        "read_only_globs": [],
        "case_sensitive": True,
        "treat_deny_as": "cross_repo",
    },
    "cross_repo": {
        "enabled": True,
        "forbid_absolute": True,
        "forbid_escaped_root": True,
        "forbid_git_internals": True,
        "forbid_nested_repo_markers": True,
        "check_symlinks": True,
        "require_repo_match": True,
        "foreign_root_globs": ["../**", "/**", "~/**"],
        "repo_boundary_markers": [".git"],
    },
    "secrets": {
        "enabled": True,
        "max_blob_bytes": 2000000,
        "max_line_chars": 4000,
        "allow_line_patterns": [],
        "entropy": {
            "enabled": True,
            "min_length": 40,
            "min_entropy_bits_per_char": 4.2,
            "require_mixed_case": True,
            "require_digit": True,
            "min_digit_ratio": 0.1,
        },
        "rules": [],
    },
    "output": {"max_excerpt_chars": 60, "redact_keep_prefix": 4, "max_violations_per_file": 50},
}


def _merge(base: Dict[str, Any], override: Dict[str, Any], path: str = "") -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        where = f"{path}.{key}" if path else key
        if key not in out:
            raise WriteGateError(f"unknown_policy_key:{where}")
        if isinstance(out[key], dict):
            if not isinstance(value, dict):
                raise WriteGateError(f"policy_type_error:{where}")
            out[key] = _merge(out[key], value, where)
        else:
            out[key] = value
    return out


def _validate(cfg: Dict[str, Any]) -> None:
    for group in ("whitelist", "cross_repo", "secrets", "output"):
        if not isinstance(cfg.get(group), dict):
            raise WriteGateError(f"missing_policy_group:{group}")
    if not cfg["whitelist"]["allow_globs"]:
        raise WriteGateError("empty_allow_globs")
    for name, globs in (("allow_globs", cfg["whitelist"]["allow_globs"]),
                        ("deny_globs", cfg["whitelist"]["deny_globs"]),
                        ("read_only_globs", cfg["whitelist"]["read_only_globs"])):
        if not isinstance(globs, list) or any(not isinstance(g, str) or not g for g in globs):
            raise WriteGateError(f"bad_glob_list:{name}")
    for rule in cfg["secrets"]["rules"]:
        if not isinstance(rule, dict) or "id" not in rule or "pattern" not in rule:
            raise WriteGateError("bad_secret_rule")
        if not isinstance(rule.get("allow", []), list):
            raise WriteGateError(f"bad_secret_rule_allow:{rule.get('id')}")
    if int(cfg["output"]["max_excerpt_chars"]) < 8:
        raise WriteGateError("bad_max_excerpt_chars")
    if cfg["whitelist"]["treat_deny_as"] not in ("cross_repo", "whitelist"):
        raise WriteGateError("bad_treat_deny_as")


def load_policy(path: Optional[str | Path] = None,
                overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """装载策略；``path`` 为 None 时仅用默认值 + ``overrides``。"""
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        p = Path(path)
        if not p.is_file():
            raise WriteGateError(f"policy_missing:{p}")
        raw = json.loads(p.read_text(encoding="utf-8"))
        if str(raw.get("schema", POLICY_SCHEMA)) != POLICY_SCHEMA:
            raise WriteGateError(f"unsupported_policy_schema:{raw.get('schema')}")
        cfg = _merge(cfg, {k: v for k, v in raw.items() if k != "schema"})
    if overrides:
        cfg = _merge(cfg, overrides)
    _validate(cfg)
    return cfg


def policy_ref(cfg: Dict[str, Any], path: Optional[str | Path] = None) -> Dict[str, Any]:
    """策略溯源引用：路径 + 内容 sha256（审计链；策略变即摘要变）。"""
    ref: Dict[str, Any] = {"sha256": hashlib.sha256(
        json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()}
    if path:
        ref["path"] = str(path)
    return ref