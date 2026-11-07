"""周聚合作业（SPEC §6.5）：把账本按 ISO 周聚合成周报并落盘。

纯函数实现，不依赖 Celery/Redis，便于在任何环境（CI、脚本、后台 worker）复算；
Celery 调度外壳见 tasks/review_budget_tasks.py。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from services.review_budget_ledger import (
    ReviewBudgetLedger,
    build_weekly_report,
    render_weekly_markdown,
)

WEEKLY_TASK_NAME = "review_budget.weekly_aggregate"
WEEKLY_INTERVAL_S = 7 * 24 * 3600


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def write_weekly_report(
    week_start: Optional[str] = None, ledger_path: Optional[str] = None
) -> Dict[str, Any]:
    """生成并落盘周报：docs/governance/review_budget_weekly_<ISO周>.md + artifacts 摘要。"""
    ledger = ReviewBudgetLedger(ledger_path)
    report = build_weekly_report(ledger, week_start)
    markdown = render_weekly_markdown(report)

    docs_dir = Path(os.getenv("TP_REVIEW_BUDGET_DOCS_DIR", repo_root() / "docs" / "governance"))
    artifacts_dir = Path(
        os.getenv("TP_REVIEW_BUDGET_ARTIFACTS_DIR", repo_root() / "artifacts" / "review_budget")
    )
    docs_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    md_path = docs_dir / f"review_budget_weekly_{report['iso_week']}.md"
    md_path.write_text(markdown, encoding="utf-8")
    json_path = artifacts_dir / f"review_budget_weekly_{report['iso_week']}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown_path": str(md_path), "json_path": str(json_path), "report": report}