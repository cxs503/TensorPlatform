#!/usr/bin/env python3
"""三合一写入边界门（平台共性机制，零领域语义）。

三道判据 + 一份可归档判决
------------------------
1. **白名单越界**（:mod:`.whitelist`）：默认拒绝；``deny`` → ``read_only`` → ``allow`` → 拦。
2. **跨仓污染**（:mod:`.crossrepo`）：越根 / 绝对路径 / 外部仓根 / 嵌套仓 / ``.git`` 内部 /
   符号链接逃逸 / 仓不符。
3. **凭证泄漏**（:mod:`.secrets`）：模式 + 熵闸 + 行级豁免，命中**必须脱敏**回显。

边界（纪律）
------------
* 只读 git（``diff --cached`` / ``show :path``），不写索引、不写工作树、不改 ``.git`` 配置；
* 白名单、仓边界、凭证模式全部外置到 ``data/write_gate/policy.json``，代码零领域语义；
* 判决纯函数、逐字节可复算；判不了一律 ``invalid`` + 拦截（fail-closed）。
"""
from __future__ import annotations

from .crossrepo import check_path, find_nested_marker, resolve_root, symlink_escapes
from .gate import (
    collect_staged,
    evaluate_changes,
    evaluate_paths,
    evaluate_staged,
    parse_name_status_z,
)
from .model import (
    ACCEPTANCE_SCHEMA,
    CATEGORIES,
    POLICY_SCHEMA,
    SCHEMA,
    Change,
    Verdict,
    Violation,
    WriteGateError,
    canonical_json,
    digest,
)
from .pathnorm import Canonical, canonicalize, glob_match
from .policy import DEFAULTS, load_policy, policy_ref
from .secrets import SecretHit, SecretRule, load_rules, redact, scan_text, shannon_entropy
from .whitelist import Decision, decide, whitelist_from_text

__all__ = [
    "ACCEPTANCE_SCHEMA", "CATEGORIES", "POLICY_SCHEMA", "SCHEMA", "Canonical", "Change",
    "Decision", "DEFAULTS", "SecretHit", "SecretRule", "Verdict", "Violation", "WriteGateError",
    "canonical_json", "canonicalize", "check_path", "collect_staged", "decide", "digest",
    "evaluate_changes", "evaluate_paths", "evaluate_staged", "find_nested_marker", "glob_match",
    "load_policy", "load_rules", "parse_name_status_z", "policy_ref", "redact", "resolve_root",
    "scan_text", "shannon_entropy", "symlink_escapes", "whitelist_from_text",
]