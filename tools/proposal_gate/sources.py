"""索引数据源：从真库读 ``campaign_nodes``／从磁盘读历史提案池。

两条来源都只提供**文本与结构化键**，不含任何域词表：

* :func:`load_campaign_nodes` —— SQLAlchemy Session 上执行一条只读 SQL，取
  ``id/name/metadata``，把 ``metadata.goal``（若有）拼进文本；node 名里若以白名单仓库名
  开头，则把该仓库名记为结构化 ``repo``（白名单来自配置，不是代码）。
* :func:`load_historical_proposals` —— 扫 ``**/proposals/**/*.json``。

DB 方言差异（PG 的 JSONB → Python dict；SQLite 的 TEXT → JSON 字符串）在解析层兜住，
因此本模块可对 sqlite 夹具单测，无需 PG 驱动。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import GateConfig

NODE_SQL = (
    "SELECT id, name, metadata FROM campaign_nodes ORDER BY id LIMIT :limit"
)


def _as_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def infer_repo(text: str, whitelist: Sequence[str]) -> str | None:
    """节点名以白名单仓库名开头即视为该仓节点（白名单来自配置）。"""
    head = (text or "").strip()
    for repo in sorted(whitelist, key=len, reverse=True):
        if head.startswith(repo):
            return repo
    return None


def load_campaign_nodes(session: Any, cfg: GateConfig, *, limit: int = 5000) -> list[dict[str, Any]]:
    """从真库读战役节点（只读一条 SQL），返回 ``[{id, name, text, repo}]``。"""
    rows = session.execute(_sql_text(NODE_SQL), {"limit": int(limit)}).fetchall()
    whitelist = cfg.repo_whitelist
    out: list[dict[str, Any]] = []
    for row in rows:
        node_id, name, metadata = row[0], row[1], row[2]
        goal = _as_metadata(metadata).get("goal")
        goal_text = goal if isinstance(goal, str) else ""
        text = f"{name}\n{goal_text}".strip() if goal_text else str(name or "")
        out.append(
            {
                "id": node_id,
                "name": str(name or ""),
                "text": text,
                "repo": infer_repo(str(name or ""), whitelist),
            }
        )
    return out


def _sql_text(sql: str) -> Any:
    from sqlalchemy import text

    return text(sql)


def load_historical_proposals(root: str | Path, pattern: str = "**/proposals/**/*.json") -> list[Path]:
    """历史提案池清单（跳过隐藏目录；目录不存在 → 空表，不崩）。"""
    base = Path(root)
    if not base.exists():
        return []
    out: list[Path] = []
    for path in sorted(base.glob(pattern)):
        rel = path.relative_to(base).parts
        if any(part.startswith(".") for part in rel):
            continue
        if path.is_file():
            out.append(path)
    return out


def populate_index(index: Any, node_rows: Iterable[dict[str, Any]]) -> int:
    """把真库节点灌进索引（返回灌入条数）。"""
    n = 0
    for row in node_rows:
        index.add_node(f"node:{row['id']}", row["text"], repo=row.get("repo"))
        n += 1
    return n