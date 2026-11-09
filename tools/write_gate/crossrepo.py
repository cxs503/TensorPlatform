#!/usr/bin/env python3
"""跨仓污染防护 —— 写入不得越出本仓工作树，也不得钻进别人的仓/本仓 .git。

五种污染面（全部机制化，配置在 ``policy.cross_repo``）
-----------------------------------------------------
1. ``root_escape``         —— 路径回溯越出工作树根（``../../x``）
2. ``absolute_path``       —— 绝对路径（指向任意位置，含别的仓）
3. ``foreign_root``        —— 命中策略声明的外部仓根模式（``../**`` / ``/**`` / ``~/**``）
4. ``nested_repo_marker``  —— 工作树内某祖先目录自带 ``.git`` 标记（submodule / 嵌套仓）
5. ``git_internals``       —— 写 ``.git/`` 内部（config/hooks/refs）＝ 改仓元数据
6. ``symlink_escape``      —— 路径经符号链接解析后落到工作树外
7. ``repo_mismatch``       —— 变更自称属于另一个仓（API/收单层面的污染面）

``.git`` 一律只读：本仓纪律「不改 .git 配置」在此处被机器化，``git_internals`` 判红。

判定依赖文件系统（真实存在性/符号链接解析），因此 :func:`resolve_root` 之后的所有
检查都对 ``realpath`` 工作，避免"根目录本身是符号链接"造成的假阴性。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .model import Violation, WriteGateError
from .pathnorm import Canonical, first_match

MAX_LINK_HOPS = 40


def resolve_root(root: Optional[str | Path]) -> Optional[Path]:
    """解析工作树根；``None`` 表示不做文件系统级检查（纯路径判据）。"""
    if root is None:
        return None
    path = Path(root)
    if not path.exists():
        raise WriteGateError(f"root_missing:{path}")
    if not path.is_dir():
        raise WriteGateError(f"root_not_dir:{path}")
    return Path(os.path.realpath(path))


def _ancestors(segments: Sequence[str]) -> List[str]:
    """文件路径的所有祖先相对路径（不含文件自身，含最深父目录）。"""
    out: List[str] = []
    for depth in range(1, len(segments)):
        out.append("/".join(segments[:depth]))
    return out


def find_nested_marker(root: Path, segments: Sequence[str], markers: Sequence[str]) -> str:
    """返回工作树内最早"自带仓标记"的祖先相对路径（空串=无嵌套仓）。"""
    for ancestor in reversed(_ancestors(segments)):     # 由深到浅：报最贴近的那个
        for marker in markers:
            if (root / ancestor / marker).exists():
                return ancestor
    return ""


def symlink_escapes(root: Path, segments: Sequence[str]) -> bool:
    """该路径解析后是否落在工作树之外（符号链接逃逸 / 挂载点逃逸）。"""
    target = root.joinpath(*segments)
    real = Path(os.path.realpath(target))
    root_s = str(root)
    real_s = str(real)
    if real_s == root_s:
        return False
    return not real_s.startswith(root_s + os.sep)


def check_path(canon: Canonical, cfg: Dict[str, Any], *,
               root: Optional[Path] = None,
               change_repo: Optional[str] = None,
               active_repo: Optional[str] = None) -> List[Violation]:
    """对单条规范化路径做跨仓判据，返回违规列表（可能为空）。"""
    cr = cfg["cross_repo"]
    out: List[Violation] = []
    if not cr["enabled"]:
        return out
    path = canon.text

    if canon.absolute and cr["forbid_absolute"]:
        out.append(Violation("absolute_path", "cross_repo", path, rule_id="cross_repo.forbid_absolute",
                             detail="绝对路径写入：不接受工作树外锚点"))
    if canon.escaped_parent and cr["forbid_escaped_root"]:
        out.append(Violation("root_escape", "cross_repo", path, rule_id="cross_repo.forbid_escaped_root",
                             detail="路径回溯越出工作树根"))
    foreign = first_match(path, cr.get("foreign_root_globs", []))
    if foreign:
        out.append(Violation("foreign_root", "cross_repo", path, rule_id=foreign,
                             detail="命中策略声明的外部仓根模式"))

    markers = list(cr.get("repo_boundary_markers", [".git"]))
    if cr["forbid_git_internals"] and canon.segments:
        if canon.segments[0] in markers:
            out.append(Violation("git_internals", "cross_repo", path,
                                 rule_id="cross_repo.forbid_git_internals",
                                 detail="写入仓元数据目录（.git 内部只读）"))

    if root is not None and cr["forbid_nested_repo_markers"]:
        nested = find_nested_marker(root, canon.segments, markers)
        if nested:
            out.append(Violation("nested_repo_marker", "cross_repo", path,
                                 rule_id=f"cross_repo.nested@{nested}",
                                 detail=f"祖先目录 '{nested}' 自带仓标记：属另一个仓的边界内"))

    if root is not None and cr["check_symlinks"] and not canon.absolute and not canon.escaped_parent:
        if symlink_escapes(root, canon.segments):
            out.append(Violation("symlink_escape", "cross_repo", path,
                                 rule_id="cross_repo.check_symlinks",
                                 detail="符号链接解析后落到工作树之外"))

    if cr["require_repo_match"] and active_repo and change_repo and change_repo != active_repo:
        out.append(Violation("repo_mismatch", "cross_repo", path, rule_id="cross_repo.require_repo_match",
                             detail=f"变更自称属仓 '{change_repo}'，本工作树属仓 '{active_repo}'"))
    return out