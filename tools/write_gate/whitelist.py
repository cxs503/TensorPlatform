#!/usr/bin/env python3
"""白名单判定：一次写入是否落在本任务允许写的路径集合内。

判据顺序（顺序是安全属性，固定并有测试锁定）
--------------------------------------------
1. ``deny_globs``      — 显式禁区，优先于任何放行（不可被 allow 抵消）
2. ``read_only_globs`` — 只读区（可改内容不可写）
3. ``allow_globs``     — 白名单；命中即放行
4. 兜底                — 未命中白名单即拦（**默认拒绝**，不是默认放行）

``allow_globs`` 为空即视为策略非法（在 :mod:`.policy` 校验），避免"空名单=全放行"的
静默灾难。glob 语义见 :mod:`.pathnorm`（全串匹配，不做前缀放宽）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .pathnorm import first_match


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str = "allow"
    rule_id: str = ""
    detail: str = ""

    def to_json(self) -> dict:
        return {"allowed": self.allowed, "code": self.code, "rule_id": self.rule_id,
                "detail": self.detail}


def decide(rel_path: str, cfg: Dict[str, Any]) -> Decision:
    """对规范化后的相对路径做白名单判决（纯函数）。"""
    wl = cfg["whitelist"]
    case_sensitive = bool(wl.get("case_sensitive", True))

    denied = first_match(rel_path, wl["deny_globs"], case_sensitive=case_sensitive)
    if denied:
        return Decision(False, "deny_glob", denied, "path matched deny_globs")

    read_only = first_match(rel_path, wl["read_only_globs"], case_sensitive=case_sensitive)
    if read_only:
        return Decision(False, "read_only_glob", read_only, "path is declared read-only")

    if not wl["enabled"]:
        return Decision(True, "whitelist_disabled", "", "whitelist disabled by policy")

    allowed = first_match(rel_path, wl["allow_globs"], case_sensitive=case_sensitive)
    if allowed:
        return Decision(True, "allow_glob", allowed, "path inside edit whitelist")

    return Decision(False, "allow_list_miss", "",
                    "path outside the edit whitelist (default-deny)")


def whitelist_from_text(text: str) -> Dict[str, list]:
    """把 ``EDIT_WHITELIST`` 风格的字符串（逗号/分号/换行分隔）解析成 glob 列表。"""
    out = []
    for chunk in str(text).replace(";", ",").replace("\n", ",").split(","):
        item = chunk.strip()
        if item:
            out.append(item)
    return {"allow_globs": out}