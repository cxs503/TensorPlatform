#!/usr/bin/env python3
"""大文件/生成物拦截（R4）：防止二进制产物或大文件意外进入提交。

判据
----
* 文件大小超过 ``binary.max_bytes`` 且路径不在 ``binary.allow_globs`` 内 → 拦截。
* 二进制检测（NUL 字节 / 解码失败）且路径不在 ``binary.allow_globs`` 内 → 拦截。
* 纯路径判据（不需要文件存在），用于 CI/预提交阶段对 ``changed_files`` 列表做预判。

设计要点
--------
* 配置全外置（``policy.binary``），代码不含具体阈值或路径。
* 与白名单/跨仓/凭证并列的第四道判据，独立分类 ``binary``。
* 允许 ``artifacts/**`` 等目录通过 ``allow_globs`` 豁免。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .model import Violation
from .pathnorm import first_match


@dataclass(frozen=True)
class BinaryDecision:
    allowed: bool
    code: str = "allow"
    rule_id: str = ""
    detail: str = ""

    def to_json(self) -> dict:
        return {"allowed": self.allowed, "code": self.code, "rule_id": self.rule_id,
                "detail": self.detail}


def check_binary(rel_path: str, cfg: Dict[str, Any], *,
                 file_size: Optional[int] = None,
                 is_binary: bool = False) -> BinaryDecision:
    """对单条路径做大文件/二进制判据（纯函数）。

    Parameters
    ----------
    rel_path : str
        规范化后的相对路径。
    cfg : dict
        完整策略配置（需含 ``binary`` 组）。
    file_size : int, optional
        文件大小（字节）。None 表示未知（跳过大小判据）。
    is_binary : bool
        是否为二进制内容（由调用方检测后传入）。
    """
    bin_cfg = cfg.get("binary", {})
    if not bin_cfg.get("enabled", False):
        return BinaryDecision(True, "binary_disabled", "", "binary gate disabled by policy")

    allow_globs = bin_cfg.get("allow_globs", [])
    case_sensitive = bool(bin_cfg.get("case_sensitive", True))

    # 先查豁免：命中 allow_globs 即放行（无论大小/二进制）
    if allow_globs and first_match(rel_path, allow_globs, case_sensitive=case_sensitive):
        return BinaryDecision(True, "binary_allow_glob", allow_globs[0],
                              "path inside binary allow list")

    # 大文件判据
    max_bytes = int(bin_cfg.get("max_bytes", 5 * 1024 * 1024))  # 默认 5MB
    if file_size is not None and file_size > max_bytes:
        return BinaryDecision(False, "file_too_large", "binary.max_bytes",
                              f"file size {file_size} bytes exceeds limit {max_bytes} bytes")

    # 二进制判据
    if is_binary:
        return BinaryDecision(False, "binary_content", "binary.forbid_binary",
                              "binary content detected outside allow list")

    return BinaryDecision(True, "allow", "", "within binary limits")


def check_binary_violations(rel_path: str, cfg: Dict[str, Any], *,
                            file_size: Optional[int] = None,
                            is_binary: bool = False) -> List[Violation]:
    """返回违规列表（可能为空）。"""
    decision = check_binary(rel_path, cfg, file_size=file_size, is_binary=is_binary)
    if decision.allowed:
        return []
    return [Violation(decision.code, "binary", rel_path, rule_id=decision.rule_id,
                      detail=decision.detail)]
