"""KPI-REPRO-95 核心计量逻辑。

从 tensor_dev_missions 表提取复现任务全集，按三档「工件在」机检
(strong/medium/none) 判定通过性，计算通过率与缺口台账。

支持 PostgreSQL（生产）与 SQLite（测试/CI）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

# ─── 口径常量 ───

KPI_NAME = "KPI-REPRO-95"
KPI_TARGET = 95.0  # percent
REPRO_TASK_KEY_PATTERNS = [r"(^|-)audit2?(-|$)", r"repro-"]
TERMINAL_STATUSES = ("succeeded", "failed", "stopped", "revoked")

# 工件在判定三档
ARTIFACT_STRONG = "strong"
ARTIFACT_MEDIUM = "medium"
ARTIFACT_NONE = "none"

# 失败根因分类
ROOT_CAUSE_NO_ARTIFACT = "自称成功但工件不可证"
ROOT_CAUSE_DISK_FULL = "派发失败：worktree 创建失败（磁盘空间不足）"
ROOT_CAUSE_BUDGET_EXHAUSTED = "预算/进展门耗尽（未调用 finish）"
ROOT_CAUSE_LLM_ERROR = "llm_error"
ROOT_CAUSE_OTHER = "其他"

# 默认数据库 URL
DEFAULT_DB_URL = "postgresql://foundation:foundation@localhost:5432/open_digital_foundation"

# 基础路径
BASE_WORK_DIR = Path("/root/tp-agent/work")
BASE_ECO_DIR = Path("/root/tensor-ecosystem")


@dataclass
class MissionRow:
    """单条复现任务明细。"""
    id: int
    task_key: Optional[str]
    goal: Optional[str]
    repo: Optional[str]
    status: str
    runner: Optional[str]
    model: Optional[str]
    created_at: Optional[str]
    finished_at: Optional[str]
    result: Optional[dict] = None
    artifact_level: str = ARTIFACT_NONE
    worktree_exists: bool = False
    report_exists: bool = False
    head_commit_reachable: bool = False
    root_cause: Optional[str] = None


@dataclass
class KPIResult:
    """KPI 计量结果。"""
    kpi_name: str = KPI_NAME
    snapshot_t0: str = ""
    snapshot_t0_db_clock: str = ""
    target: float = KPI_TARGET
    # 主口径
    total: int = 0
    succeeded: int = 0
    passed_strong: int = 0
    passed_medium: int = 0
    passed: int = 0  # strong + medium
    current: float = 0.0
    gap: float = 0.0
    # 上界口径（含报告工件）
    total_upper: int = 0
    passed_upper: int = 0
    current_upper: float = 0.0
    gap_upper: float = 0.0
    # 灵敏度口径
    s1_total: int = 0
    s1_passed: int = 0
    s1_current: float = 0.0
    s2_total: int = 0
    s2_passed: int = 0
    s2_current: float = 0.0
    # 辅助指标
    succeeded_rate: float = 0.0
    verdict_coverage: float = 0.0
    artifact_parse_rate: float = 0.0
    worktree_retention: float = 0.0
    report_coverage: float = 0.0
    repro_verdicts_table_rows: int = 0
    report_id_backfill: int = 0
    # 失败根因
    failure_root_causes: dict = field(default_factory=dict)
    # 明细
    missions: list = field(default_factory=list)
    # 数据源
    data_sources: list = field(default_factory=lambda: [
        "tensor_dev_missions",
        "tensor_maturity_verdicts",
        "tensor_repro_verdicts",
        "tensor_reports",
    ])
    # 缺口与建议
    top_gaps: list = field(default_factory=list)
    proposed_anchors: list = field(default_factory=list)


def _get_db_engine(db_url: Optional[str] = None) -> Engine:
    """获取数据库引擎。"""
    url = db_url or os.environ.get("DATABASE_URL", DEFAULT_DB_URL)
    return create_engine(url)


def _get_t0(session: Session) -> str:
    """获取快照时间 T0（DB 时钟）。"""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        row = session.execute(text("SELECT now()::text")).first()
    else:
        row = session.execute(text("SELECT datetime('now')")).first()
    return row[0] if row else datetime.now(timezone.utc).isoformat()


def _is_repro_task(task_key: Optional[str], goal: Optional[str]) -> bool:
    """判断是否为复现任务（按 task_key 模式匹配）。"""
    if task_key:
        for pat in REPRO_TASK_KEY_PATTERNS:
            if re.search(pat, task_key):
                return True
    return False


def _check_worktree_exists(mission_id: int) -> bool:
    """检查 worktree 是否在磁盘上。"""
    wt_path = BASE_WORK_DIR / f"m{mission_id}"
    return wt_path.exists()


def _check_report_exists(mission_id: int) -> bool:
    """检查报告工件是否存在。"""
    report_path = BASE_WORK_DIR / f"m{mission_id}" / "report.json"
    if report_path.exists():
        return True
    # 也检查 tensor_reports 目录
    tr_path = BASE_WORK_DIR / f"m{mission_id}" / "tensor_reports"
    if tr_path.exists():
        return True
    return False


def _check_head_commit_reachable(repo: Optional[str], head_commit: Optional[str]) -> bool:
    """检查 head_commit 在仓 git 历史中是否可达。"""
    if not repo or not head_commit:
        return False
    repo_path = BASE_ECO_DIR / repo
    if not repo_path.exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "cat-file", "-e", f"{head_commit}^{commit}"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return False


def _parse_artifact_pointers(result: Optional[dict]) -> list[str]:
    """从 result 中提取工件指针。"""
    pointers = []
    if not result:
        return pointers

    # changed_files
    changed_files = result.get("changed_files", [])
    if isinstance(changed_files, list):
        pointers.extend(str(f) for f in changed_files)

    # evidence paths
    evidence = result.get("evidence", {})
    if isinstance(evidence, dict):
        for key, val in evidence.items():
            if isinstance(val, str):
                pointers.append(val)
            elif isinstance(val, list):
                pointers.extend(str(v) for v in val)

    # result 中的其他路径字段
    for key in ("entry", "impl_files", "tests", "repro_cmd", "artifacts"):
        val = result.get(key)
        if isinstance(val, str):
            pointers.append(val)
        elif isinstance(val, list):
            pointers.extend(str(v) for v in val)

    return pointers


def _clean_pointer(p: str) -> str:
    """清理指针字符串中的噪声。"""
    # 去除 ::test_name 后缀
    p = re.sub(r"::\w+$", "", p)
    # 去除行号
    p = re.sub(r":\d+$", "", p)
    # 去除括号
    p = p.strip("(){}")
    # 去除 OUT= 前缀
    p = re.sub(r"^OUT=", "", p)
    # 去除 (4/5 类似模式
    p = re.sub(r"^\(\d+/\d+", "", p)
    return p.strip()


def _check_artifact_strong(pointers: list[str]) -> bool:
    """检查是否有至少一条指针可解析（strong）。"""
    for p in pointers:
        cleaned = _clean_pointer(p)
        if not cleaned:
            continue
        # 尝试作为绝对路径
        if Path(cleaned).exists():
            return True
        # 尝试在 worktree 下
        if cleaned.startswith("m"):
            wt_path = BASE_WORK_DIR / cleaned
            if wt_path.exists():
                return True
    return False


def _classify_artifact(mission: MissionRow) -> str:
    """对单条任务进行工件在分类。"""
    result = mission.result or {}
    head_commit = result.get("head_commit")
    repo = mission.repo

    # 检查 strong
    pointers = _parse_artifact_pointers(result)
    if _check_artifact_strong(pointers):
        return ARTIFACT_STRONG

    # 检查 medium（head_commit 可达）
    if head_commit and _check_head_commit_reachable(repo, head_commit):
        return ARTIFACT_MEDIUM

    return ARTIFACT_NONE


def _classify_root_cause(mission: MissionRow) -> Optional[str]:
    """判定失败根因。"""
    if mission.status != "succeeded":
        return None

    result = mission.result or {}
    error_msg = result.get("error", "") or result.get("failure_reason", "") or ""

    if "No space left on device" in error_msg or "unable to create file" in error_msg:
        return ROOT_CAUSE_DISK_FULL
    if "llm_error" in error_msg.lower():
        return ROOT_CAUSE_LLM_ERROR

    # 检查是否工件不可证
    if mission.artifact_level == ARTIFACT_NONE:
        return ROOT_CAUSE_NO_ARTIFACT

    return None


def fetch_repro_missions(session: Session, t0: str) -> list[MissionRow]:
    """从数据库获取复现任务全集。"""
    dialect = session.get_bind().dialect.name

    if dialect == "postgresql":
        # PostgreSQL: 使用正则匹配
        query = text("""
            SELECT id, task_key, goal, repo, status, runner, model,
                   created_at::text as created_at,
                   finished_at::text as finished_at,
                   result
            FROM tensor_dev_missions
            WHERE (
                task_key ~ '(^|-)audit2?(-|$)'
                OR task_key LIKE 'repro-%'
            )
            AND status IN ('succeeded', 'failed', 'stopped', 'revoked')
            AND created_at <= :t0
            ORDER BY id
        """)
    else:
        # SQLite: 使用 LIKE 模拟
        query = text("""
            SELECT id, task_key, goal, repo, status, runner, model,
                   created_at, finished_at, result
            FROM tensor_dev_missions
            WHERE (
                task_key LIKE '%audit%'
                OR task_key LIKE 'repro-%'
            )
            AND status IN ('succeeded', 'failed', 'stopped', 'revoked')
            AND created_at <= :t0
            ORDER BY id
        """)

    rows = session.execute(query, {"t0": t0}).mappings().all()
    missions = []
    for row in rows:
        result_data = row.get("result")
        if isinstance(result_data, str):
            try:
                result_data = json.loads(result_data)
            except (json.JSONDecodeError, TypeError):
                result_data = None

        m = MissionRow(
            id=row["id"],
            task_key=row.get("task_key"),
            goal=row.get("goal"),
            repo=row.get("repo"),
            status=row["status"],
            runner=row.get("runner"),
            model=row.get("model"),
            created_at=row.get("created_at"),
            finished_at=row.get("finished_at"),
            result=result_data,
        )
        m.worktree_exists = _check_worktree_exists(m.id)
        m.report_exists = _check_report_exists(m.id)
        m.artifact_level = _classify_artifact(m)
        m.root_cause = _classify_root_cause(m)
        missions.append(m)

    return missions


def compute_kpi(missions: list[MissionRow], t0: str, t0_db: str) -> KPIResult:
    """计算 KPI-REPRO-95。"""
    result = KPIResult(
        snapshot_t0=t0,
        snapshot_t0_db_clock=t0_db,
    )

    total = len(missions)
    result.total = total
    result.total_upper = total

    succeeded = [m for m in missions if m.status == "succeeded"]
    result.succeeded = len(succeeded)

    passed_strong = [m for m in succeeded if m.artifact_level == ARTIFACT_STRONG]
    passed_medium = [m for m in succeeded if m.artifact_level == ARTIFACT_MEDIUM]
    result.passed_strong = len(passed_strong)
    result.passed_medium = len(passed_medium)
    result.passed = len(passed_strong) + len(passed_medium)

    # 主口径通过率
    if total > 0:
        result.current = round(result.passed / total * 100, 2)
        result.gap = round(KPI_TARGET - result.current, 2)
    else:
        result.gap = KPI_TARGET  # 无数据时缺口=目标值

    # succeeded 率
    if total > 0:
        result.succeeded_rate = round(result.succeeded / total * 100, 2)

    # 上界口径（succeeded 且 (工件在 或 报告存在)）
    passed_upper = [m for m in succeeded if m.artifact_level != ARTIFACT_NONE or m.report_exists]
    result.passed_upper = len(passed_upper)
    if total > 0:
        result.current_upper = round(result.passed_upper / total * 100, 2)
        result.gap_upper = round(KPI_TARGET - result.current_upper, 2)

    # S1: 有复现官判定记录的子集
    # 这里简化为有 maturity_verdict 记录的任务
    s1_missions = [m for m in missions if m.result and m.result.get("maturity_verdict")]
    s1_passed = [m for m in s1_missions if m.status == "succeeded" and m.artifact_level != ARTIFACT_NONE]
    result.s1_total = len(s1_missions)
    result.s1_passed = len(s1_passed)
    if result.s1_total > 0:
        result.s1_current = round(result.s1_passed / result.s1_total * 100, 2)

    # S2: goal 声明交差审计/复现官
    s2_missions = [m for m in missions if m.goal and ("交差审计" in (m.goal or "") or "REPRO" in (m.goal or "").upper())]
    s2_passed = [m for m in s2_missions if m.status == "succeeded" and m.artifact_level != ARTIFACT_NONE]
    result.s2_total = len(s2_missions)
    result.s2_passed = len(s2_passed)
    if result.s2_total > 0:
        result.s2_current = round(result.s2_passed / result.s2_total * 100, 2)

    # 辅助指标
    worktree_exists_count = sum(1 for m in missions if m.worktree_exists)
    report_exists_count = sum(1 for m in missions if m.report_exists)
    if total > 0:
        result.worktree_retention = round(worktree_exists_count / total * 100, 2)
        result.report_coverage = round(report_exists_count / total * 100, 2)

    # 工件指针可解析率
    all_pointers = []
    parseable = 0
    for m in missions:
        pointers = _parse_artifact_pointers(m.result)
        all_pointers.extend(pointers)
        for p in pointers:
            cleaned = _clean_pointer(p)
            if cleaned and (Path(cleaned).exists() or (cleaned.startswith("m") and (BASE_WORK_DIR / cleaned).exists())):
                parseable += 1
    if all_pointers:
        result.artifact_parse_rate = round(parseable / len(all_pointers) * 100, 2)

    # 失败根因统计
    failed_missions = [m for m in missions if m.status == "succeeded" and m.artifact_level == ARTIFACT_NONE]
    root_cause_counts = {}
    for m in failed_missions:
        rc = m.root_cause or ROOT_CAUSE_OTHER
        root_cause_counts[rc] = root_cause_counts.get(rc, 0) + 1
    result.failure_root_causes = root_cause_counts

    # 明细
    result.missions = [asdict(m) for m in missions]

    # 缺口与建议
    result.top_gaps = _compute_top_gaps(result)
    result.proposed_anchors = _propose_anchors(result)

    return result


def _compute_top_gaps(kpi: KPIResult) -> list[dict]:
    """计算 top gaps。"""
    gaps = []
    if kpi.gap > 0:
        gaps.append({
            "gap": f"复现官记录通过率缺口 {kpi.gap} pp（{kpi.current}% vs {kpi.target}%）",
            "severity": "high",
        })
    if kpi.verdict_coverage < 95:
        gaps.append({
            "gap": f"复现官判定记录覆盖率仅 {kpi.verdict_coverage}%",
            "severity": "high",
        })
    if kpi.artifact_parse_rate < 95:
        gaps.append({
            "gap": f"工件指针可解析率仅 {kpi.artifact_parse_rate}%",
            "severity": "medium",
        })
    if kpi.worktree_retention < 95:
        gaps.append({
            "gap": f"worktree 在盘留存仅 {kpi.worktree_retention}%",
            "severity": "medium",
        })
    return gaps


def _propose_anchors(kpi: KPIResult) -> list[dict]:
    """提出建议派生锚点。"""
    anchors = []
    if kpi.gap > 10:
        anchors.append({
            "anchor": "KPI-REPRO-ARTIFACT-PIPELINE",
            "description": "修复工件归档管道，确保 succeeded 任务工件指针持久化",
            "acceptance": "工件指针可解析率 >= 95%",
        })
    if kpi.verdict_coverage < 50:
        anchors.append({
            "anchor": "KPI-REPRO-VERDICT-AUTO",
            "description": "自动复现判定记录写入，消除手动遗漏",
            "acceptance": "复现官判定记录覆盖率 >= 95%",
        })
    return anchors


def measure_kpi_repro(
    db_url: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> KPIResult:
    """主计量入口。

    Args:
        db_url: 数据库连接字符串（默认从环境变量或 DEFAULT_DB_URL）
        output_dir: 输出目录（默认 runs/m15200_kpi_repro/）

    Returns:
        KPIResult 计量结果
    """
    engine = _get_db_engine(db_url)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()

    try:
        t0_db = _get_t0(session)
        t0 = datetime.now(timezone.utc).isoformat()

        logger.info("Fetching repro missions with T0=%s", t0_db)
        missions = fetch_repro_missions(session, t0_db)
        logger.info("Found %d repro missions", len(missions))

        kpi = compute_kpi(missions, t0, t0_db)

        # 输出
        out_dir = Path(output_dir) if output_dir else Path("runs/m15200_kpi_repro")
        out_dir.mkdir(parents=True, exist_ok=True)

        # result.json
        result_dict = asdict(kpi)
        # 移除明细中的大对象以减小文件
        result_dict["missions"] = result_dict["missions"][:100]  # 只保留前100条
        result_file = out_dir / "result.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2, default=str)
        logger.info("Wrote %s", result_file)

        # rows.jsonl
        rows_file = out_dir / "rows.jsonl"
        with open(rows_file, "w", encoding="utf-8") as f:
            for m in missions:
                f.write(json.dumps(asdict(m), ensure_ascii=False, default=str) + "\n")
        logger.info("Wrote %s", rows_file)

        # measure.sql
        sql_file = out_dir / "measure.sql"
        with open(sql_file, "w", encoding="utf-8") as f:
            f.write(f"-- KPI-REPRO-95 measurement SQL\n")
            f.write(f"-- T0 = {t0_db}\n")
            f.write(f"-- Generated at {t0}\n\n")
            f.write("""SELECT id, task_key, goal, repo, status, runner, model,
       created_at, finished_at, result
FROM tensor_dev_missions
WHERE (
    task_key ~ '(^|-)audit2?(-|$)'
    OR task_key LIKE 'repro-%'
)
AND status IN ('succeeded', 'failed', 'stopped', 'revoked')
AND created_at <= '""" + t0_db + """'
ORDER BY id;
""")
        logger.info("Wrote %s", sql_file)

        return kpi

    finally:
        session.close()
