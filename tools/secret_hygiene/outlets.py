"""三出口秘密卫生门：提交（pre-commit）/ 报告（report gate）/ 工件（artifact writer）。

统一契约
--------
三个出口共用同一台引擎与同一套脱敏标记，返回同一个 :class:`Verdict`：

* ``allowed``：是否放行；
* ``hits``：**只含脱敏标记与定位**的命中清单（判决 JSON 可安全归档）；
* ``sanitized``：脱敏后的载荷（``redact`` 模式）——供出口在放行时改写内容；
* ``plaintext_leaks``：本出口是否仍有明文残留（内部校验字段，恒应为 0）。

出口差异只在"输入长什么样"：提交出口吃 ``(路径, 内容)`` 序列，报告出口吃报告
payload（递归到 ``figures[].png_base64`` 这类字符串叶子），工件出口在写盘前判定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .engine import Hit, HygieneConfig, scan_payload, scan_text
from .redact import contains_plaintext, redact_payload, redact_text

OUTLETS = ("precommit", "report", "artifact")


@dataclass
class Verdict:
    outlet: str
    allowed: bool
    hits: List[Dict[str, Any]] = field(default_factory=list)
    sanitized: Any = None
    plaintext_leaks: int = 0
    mode: str = "block"
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outlet": self.outlet,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "mode": self.mode,
            "hit_count": len(self.hits),
            "hits": self.hits,
            "plaintext_leaks": self.plaintext_leaks,
            "detail": self.detail,
        }


class SecretBlocked(RuntimeError):
    """工件出口在 block 模式下拒绝写盘。"""

    def __init__(self, verdict: Verdict):
        super().__init__(f"secret_blocked:{verdict.outlet}:{len(verdict.hits)}")
        self.verdict = verdict


def _hits_payload(hits: Iterable[Hit]) -> List[Dict[str, Any]]:
    return [hit.to_dict() for hit in hits]


# ------------------------------------------------------------------ 出口一


def precommit_outlet(changes: Sequence[Tuple[str, str]], cfg: HygieneConfig, *,
                     mode: str = "block") -> Verdict:
    """出口一：提交前判据。``changes`` = ``[(路径, 文本内容), ...]``（staged 快照）。"""
    hits: List[Hit] = []
    sanitized: Dict[str, str] = {}
    for path, text in changes:
        file_hits = scan_text(text, cfg, path=path)
        hits.extend(file_hits)
        if file_hits:
            sanitized[path] = redact_text(text, file_hits)
    allowed = not hits or mode == "redact"
    return Verdict(outlet="precommit", allowed=allowed, hits=_hits_payload(hits),
                   sanitized=sanitized or None, mode=mode,
                   detail=f"scanned {len(changes)} file(s)")


# ------------------------------------------------------------------ 出口二


def report_outlet(payload: Any, cfg: HygieneConfig, *,
                  mode: str = "block") -> Verdict:
    """出口二：报告发布前判据。递归扫 title/summary/markdown/figures 等全部字符串叶子。"""
    hits = scan_payload(payload, cfg)
    sanitized = redact_payload(payload, hits) if hits else None
    allowed = not hits or mode == "redact"
    return Verdict(outlet="report", allowed=allowed, hits=_hits_payload(hits),
                   sanitized=sanitized, mode=mode,
                   detail="payload fields scanned recursively")


# ------------------------------------------------------------------ 出口三


def artifact_outlet(path: str | Path, content: str, cfg: HygieneConfig, *,
                    mode: str = "block", audit_path: str | Path | None = None,
                    root: str | Path | None = None) -> Verdict:
    """出口三：工件写盘前判据（``root`` 为允许写入的根，防越界写）。"""
    target = Path(path)
    if root is not None:
        root_path = Path(root).resolve()
        resolved = (root_path / target).resolve() if not target.is_absolute() else target.resolve()
        if root_path not in resolved.parents and resolved != root_path:
            raise PermissionError(f"artifact_outside_root:{resolved}")
        target = resolved
    hits = scan_text(content, cfg, path=str(target))
    sanitized = redact_text(content, hits) if hits else None
    verdict = Verdict(outlet="artifact", allowed=not hits or mode == "redact",
                      hits=_hits_payload(hits), sanitized=sanitized, mode=mode,
                      detail=f"target={target}")
    if audit_path is not None:
        record = verdict.to_dict()
        record["target"] = str(target)
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    if hits and mode == "block":
        raise SecretBlocked(verdict)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sanitized if sanitized is not None else content, encoding="utf-8")
    return verdict


def probe_plaintext_residual(verdict: Verdict, tokens: Iterable[str]) -> int:
    """校验辅助：原始明文是否仍出现在脱敏结果里。

    ``verdict.sanitized`` 为 dict（提交出口）或任意载荷（报告出口）时按字段展开。
    """
    tokens = [tok for tok in tokens if tok]
    if not tokens:
        return 0
    texts: List[str] = []
    sanitized = verdict.sanitized
    if isinstance(sanitized, str):
        texts.append(sanitized)
    elif isinstance(sanitized, dict):
        for value in sanitized.values():
            if isinstance(value, str):
                texts.append(value)
    elif sanitized is not None:
        texts.append(json.dumps(sanitized, ensure_ascii=False))
    leaks = 0
    for text in texts:
        leaks += len(contains_plaintext(text, tokens))
    return leaks