#!/usr/bin/env python3
"""配置装载（行为全配置化，代码内不写任何业务常量）。

三层合并：:data:`DEFAULTS` ← 配置文件 ← 调用方 dict 覆盖。未知键**报错**而不是忽略
——拼错的配置项静默失效比报错危险得多（会让门看起来在工作其实没生效）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .model import TenancyError

DEFAULTS: dict = {
    "schema": "tenancy_gate/config/v1",
    "lease": {
        "on_conflict": "refuse",            # refuse | downgrade_readonly
        "ttl_seconds": 21600,               # 租约生存期；过期租约不再参与冲突判定
        "writable_roots": ["**"],           # 允许预留的路径根（模式列表）
        "protected_globs": [".git/**"],     # 任何情况下不许预留
        "allow_read_leases": True,
    },
    "intake": {
        "window_seconds": 86400,            # "同窗口"= 收单时间差不超过该值
        "on_conflict": "block",             # block | report_only
        "require_lease_coverage": True,     # 收单时校验 changed_files 是否都在自己的租约内
        "cross_repo_check": False,          # 跨仓是否也做路径比对（默认否：不同仓不共享工作树）
    },
    "rehearsal": {
        "timeout_seconds": 30,              # 单条 git 子命令超时
        "max_merge_tree_calls": 600,        # 批量预演的 merge-tree 调用预算
        "total_budget_seconds": 120,        # 批量预演总预算（超预算的候选对记 unknown）
        "hunk_detail": True,                # 是否产出 hunk 级冲突区间
        "require_clean": True,
        "prescreen": True,                  # 先用 changed_files 交集筛候选对
        "detail_limit": 2000,               # 报告里逐对明细上限（其余只计数，防 40 并发下报告爆内存）
    },
    "ledger": {"path": "artifacts/tenancy_gate/ledger.jsonl"},
    "semantics": {"denylist_path": "tools/tenancy_gate/domain_denylist.json"},
}


def _merge(base: dict, override: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        where = f"{path}.{key}" if path else key
        if key not in out:
            raise TenancyError(f"unknown_config_key:{where}")
        if isinstance(out[key], dict):
            if not isinstance(value, dict):
                raise TenancyError(f"config_type_error:{where}")
            out[key] = _merge(out[key], value, where)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    """装载配置； ``path`` 为 None 时仅用默认值 + ``overrides``。"""
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if str(raw.get("schema", DEFAULTS["schema"])) != DEFAULTS["schema"]:
            raise TenancyError(f"unsupported_config_schema:{raw.get('schema')}")
        cfg = _merge(cfg, {k: v for k, v in raw.items() if k != "schema"})
    if overrides:
        cfg = _merge(cfg, overrides)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    if cfg["lease"]["on_conflict"] not in ("refuse", "downgrade_readonly"):
        raise TenancyError("bad_lease_on_conflict")
    if cfg["intake"]["on_conflict"] not in ("block", "report_only"):
        raise TenancyError("bad_intake_on_conflict")
    for key in ("ttl_seconds", "window_seconds"):
        group = "lease" if key == "ttl_seconds" else "intake"
        if int(cfg[group][key]) < 0:
            raise TenancyError(f"negative_{key}")
    if int(cfg["rehearsal"]["timeout_seconds"]) <= 0:
        raise TenancyError("bad_timeout")
    if not cfg["lease"]["writable_roots"]:
        raise TenancyError("empty_writable_roots")