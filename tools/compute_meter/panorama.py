"""PANORAMA 注入：把看板块幂等写进 PANORAMA.md 的标记区。

幂等性靠一对标记：``COMPUTE-POLICY:BEGIN`` / ``COMPUTE-POLICY:END``
  - 有标记 → 只替换标记之间（重复注入不会长块，内容更新为最新）；
  - 无标记 → 追加到文件末尾（一次）；
  - 只有 BEGIN 没 END（半截块）→ 报错而不是瞎猜（防把文档切坏）。
"""
from __future__ import annotations

import pathlib

from tools.compute_meter.dashboard import BEGIN, END


class InjectionError(RuntimeError):
    pass


def _split(text: str) -> tuple[str, str, str] | None:
    if BEGIN not in text and END not in text:
        return None
    if BEGIN not in text:
        raise InjectionError("发现 END 标记但没有 BEGIN（文档被切坏）")
    head, rest = text.split(BEGIN, 1)
    if END not in rest:
        raise InjectionError("发现 BEGIN 标记但没有 END（半截块，拒绝写入）")
    body, tail = rest.split(END, 1)
    return head, body, tail


def inject(text: str, block: str) -> str:
    """纯函数：返回注入后的全文。block 必须自带 BEGIN/END 标记。"""
    if BEGIN not in block or END not in block:
        raise InjectionError("block 必须包含 BEGIN/END 标记（请用 dashboard.render_markdown）")
    block = block if block.endswith("\n") else block + "\n"
    parts = _split(text)
    if parts is None:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    head, _body, tail = parts
    return head + block + tail.lstrip("\n")


def inject_file(path: str | pathlib.Path, block: str, *, dry_run: bool = False) -> dict:
    p = pathlib.Path(path)
    before = p.read_text() if p.exists() else ""
    after = inject(before, block)
    changed = after != before
    if changed and not dry_run:
        p.write_text(after)
    return {"path": str(p), "changed": changed, "bytes_before": len(before),
            "bytes_after": len(after), "dry_run": dry_run}


def check_file(path: str | pathlib.Path) -> dict:
    """只读校验：PANORAMA 里有没有本看板的块，块内新鲜度如何。"""
    p = pathlib.Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False, "has_block": False}
    text = p.read_text()
    parts = _split(text)
    block = parts[1] if parts else ""
    return {"path": str(p), "exists": True, "has_block": parts is not None,
            "block_bytes": len(block),
            "has_policy_version": "策略版本" in block}