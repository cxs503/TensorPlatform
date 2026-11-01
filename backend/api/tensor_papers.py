"""Tensor console: paper production line — a first-class board parallel to
research progress reports. Aggregates the TensorPapers append-only registry
(topic ledger) with paper-* missions (mine waves / drafts / blind reviews /
material runs) and review verdicts, so the whole chain
挖矿 → 成稿 → 对抗盲审 → 回合 master is visible from the homepage."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db

router = APIRouter(prefix="/tensor/papers", tags=["科研运行台·论文生产线"])

PAPERS_DIR = Path(os.environ.get("TENSOR_PAPERS_DIR", "/srv/tensor/workspace/TensorPapers"))

_TOPIC_RE = re.compile(r"^-\s*\[([A-Za-z0-9_.-]+)\]\s*status=([^\s（(]+)\s*[（(]?(.*)$")
_GAP_RE = re.compile(r"^-\s*\[\s*\]\s*(.*)$")
_VERDICT_RE = re.compile(r"^Review:\s*(.+)\s*—\s*([A-Za-z-]+)")


def _registry() -> tuple[list[dict], list[str]]:
    """Parse papers/registry.md (append-only ledger) into topics + open gaps."""
    topics: list[dict] = []
    gaps: list[str] = []
    try:
        raw = (PAPERS_DIR / "papers" / "registry.md").read_text(encoding="utf-8")
    except OSError:
        return topics, gaps
    for ln in raw.splitlines():
        ln = ln.strip()
        m = _TOPIC_RE.match(ln)
        if m:
            topics.append({"slug": m.group(1), "status": m.group(2),
                           "note": m.group(3).rstrip("）)")})
            continue
        g = _GAP_RE.match(ln)
        if g:
            gaps.append(g.group(1))
    return topics, gaps


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(PAPERS_DIR), *args],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


@router.get("/line")
def paper_line(db: Session = Depends(get_db), _u: dict = Depends(require_auth)):
    topics, gaps = _registry()

    def rows(sql: str) -> list[dict]:
        return [dict(r._mapping) for r in db.execute(text(sql))]

    mines = rows("SELECT id, task_key, status, coalesce(claimed_by,'') AS claimed_by, "
                 "to_char(created_at,'MM-DD HH24:MI') AS created "
                 "FROM tensor_dev_missions WHERE task_key LIKE 'paper-mine-%' ORDER BY id")
    drafts = rows("SELECT id, task_key, status, coalesce(claimed_by,'') AS claimed_by, "
                  "to_char(created_at,'MM-DD HH24:MI') AS created "
                  "FROM tensor_dev_missions WHERE task_key LIKE 'paper-draft-%' ORDER BY id")
    mats = rows("SELECT id, task_key, status, coalesce(claimed_by,'') AS claimed_by, "
                "to_char(created_at,'MM-DD HH24:MI') AS created "
                "FROM tensor_dev_missions WHERE task_key LIKE 'paper-mat-%' "
                "ORDER BY id DESC LIMIT 12")

    reviews = {r["task_key"][len("paper-review-"):]: r for r in rows(
        "SELECT id, task_key, status, coalesce(claimed_by,'') AS claimed_by, "
        "to_char(created_at,'MM-DD HH24:MI') AS created "
        "FROM tensor_dev_missions WHERE task_key LIKE 'paper-review-%' ORDER BY id")}
    verdicts: dict[str, str] = {}
    for r in db.execute(text("SELECT title FROM tensor_reports "
                             "WHERE title LIKE 'Review: %' ORDER BY id")):
        m = _VERDICT_RE.match(r[0] or "")
        if m:
            v = m.group(2).upper()
            if v.startswith("SELF"):
                v = "SELF"
            verdicts.setdefault(m.group(1).strip(), v)
    for d in drafts:
        slug = d["task_key"][len("paper-draft-"):]
        d["slug"] = slug
        d["review"] = reviews.get(slug)
        d["verdict"] = verdicts.get(slug)

    mat_total = db.execute(text("SELECT count(*) FROM tensor_dev_missions "
                                "WHERE task_key LIKE 'paper-mat-%'")).scalar() or 0
    stats = {
        "topics": len(topics),
        "gaps": len(gaps),
        "drafts_total": len(drafts),
        "drafts_active": sum(1 for d in drafts
                             if d["status"] in ("queued", "claimed", "running")),
        "verdict_pass": sum(1 for d in drafts if d.get("verdict") == "PASS"),
        "verdict_minor": sum(1 for d in drafts if d.get("verdict") == "MINOR"),
        "verdict_fail": sum(1 for d in drafts if d.get("verdict") == "FAIL"),
        "mat_total": mat_total,
    }
    return {
        "registry_master": _git("log", "-1", "--format=%h %s"),
        "registry_updated": _git("log", "-1", "--format=%ci", "--", "papers/registry.md"),
        "topics": topics,
        "gaps": gaps,
        "mines": mines,
        "drafts": drafts,
        "mats": mats,
        "stats": stats,
    }
