"""三出口与工作流的实际挂接（integration）。

为什么单独一层
--------------
``outlets`` 只做"给我输入、我判"；真正挂到工作流上需要知道"输入从哪来"：

* **提交出口**：从 git 索引（staged）取快照，而不是工作树（工作树里可能是未定稿内容，
  也可能在门之后被改）；
* **报告出口**：报告发布前对 payload 递归判定，``redact`` 模式返回可直接发布的脱敏件；
* **工件出口**：写盘前判定，``block`` 模式抛 :class:`SecretBlocked`。

这一层保证"三出口 100% 生效"不是口头承诺：``tests/test_secret_hygiene.py`` 里用
临时 git 仓库、真实报告 payload、真实写盘路径分别验一遍。
"""
from __future__ import annotations

import subprocess  # nosec B404 - 需要读 git 索引
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .engine import HygieneConfig, load_config
from .outlets import Verdict, artifact_outlet, precommit_outlet, report_outlet

DEFAULT_RULES = "config/secret_hygiene/rules.json"


def staged_snapshot(root: str | Path = ".") -> List[Tuple[str, str]]:
    """读取 git 索引里的文本快照：``[(路径, 内容), ...]``。

    只取可解码的文本文件（二进制/超大文件跳过，由 ``artifact`` 出口的字节判据兜底）。
    """
    root_path = Path(root)
    names = subprocess.run(  # nosec B603,B607
        ["git", "diff", "--cached", "--name-only", "-z"], cwd=str(root_path),
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.split("\x00")
    snapshot: List[Tuple[str, str]] = []
    for rel in [n for n in names if n.strip()]:
        try:
            blob = subprocess.run(  # nosec B603,B607
                ["git", "show", f":{rel}"], cwd=str(root_path),
                capture_output=True, timeout=60, check=True,
            ).stdout
        except subprocess.SubprocessError:  # pragma: no cover - 删除/重命名竞态
            continue
        if b"\x00" in blob[:8192] or len(blob) > 2_000_000:
            continue
        snapshot.append((rel, blob.decode("utf-8", "replace")))
    return snapshot


def scan_staged(root: str | Path = ".", cfg: Optional[HygieneConfig] = None, *,
                mode: str = "block", rules_path: str = DEFAULT_RULES) -> Verdict:
    """提交出口：对 git 索引快照判定（pre-commit 钩子调用这个）。"""
    cfg = cfg or load_config(rules_path, root=root)
    return precommit_outlet(staged_snapshot(root), cfg, mode=mode)


@dataclass
class ReportGuard:
    """报告出口：发布前判定 + 可按需脱敏。"""

    cfg: HygieneConfig
    mode: str = "block"

    def check(self, payload: Any) -> Verdict:
        return report_outlet(payload, self.cfg, mode=self.mode)

    def sanitize(self, payload: Any) -> Dict[str, Any]:
        """返回脱敏后的 payload（即便无命中，也返回结构一致的副本）。"""
        verdict = report_outlet(payload, self.cfg, mode="redact")
        return verdict.sanitized if verdict.sanitized is not None else payload

    def assert_publishable(self, payload: Any) -> Any:
        """block 模式：命中即抛 ``SecretBlocked``；否则原样返回 payload。"""
        verdict = self.check(payload)
        if not verdict.allowed:
            from .outlets import SecretBlocked

            raise SecretBlocked(verdict)
        return payload


def write_artifact(path: str | Path, content: str, *,
                   cfg: Optional[HygieneConfig] = None,
                   root: str | Path = ".", mode: str = "block",
                   audit_path: str | Path | None = None) -> Verdict:
    """工件出口：写盘前判定（``block`` 抛错、``redact`` 写脱敏件并留审计）。"""
    cfg = cfg or load_config(DEFAULT_RULES, root=root)
    return artifact_outlet(path, content, cfg, mode=mode, audit_path=audit_path, root=root)


def audit_paths(paths: Iterable[str | Path], cfg: HygieneConfig) -> Dict[str, Any]:
    """批量审计（仓库基线扫描）：返回逐文件脱敏命中，可直接归档成工件。"""
    from .engine import scan_file

    rows: List[Dict[str, Any]] = []
    for path in paths:
        hits = scan_file(path, cfg)
        if hits:
            rows.append({"path": str(path), "hits": [h.to_dict() for h in hits]})
    return {"files_with_hits": len(rows), "rows": rows}