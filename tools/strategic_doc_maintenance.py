"""战略文档自动维护 daemon —— 定期刷新 AGENT_BOOTSTRAP WORLD 行与 PANORAMA 战役态节。

设计约束：
- 数据全 SQL 溯源，零手抄
- 人权线（HUMAN_RIGHTS_LINE_MARKER）内容 MUST NOT 被自动改写
- 连续两轮自动刷新幂等
- 断链降级不崩（外部 API/DB 不可用时优雅降级）
- 刷新审计留痕（JSON 审计日志）

用法：
    python tools/strategic_doc_maintenance.py run          # 单轮刷新
    python tools/strategic_doc_maintenance.py run --dry    # 干跑（不写文件）
    python tools/strategic_doc_maintenance.py daemon       # 守护模式（循环）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ───────────────────────── 常量 ─────────────────────────

HUMAN_RIGHTS_LINE_MARKER = "人权线"
HUMAN_RIGHTS_SECTION_RE = re.compile(
    r"(##\s*人权线.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
)
WORLD_LINE_RE = re.compile(r"^WORLD:\s.*$", re.MULTILINE)
PANORAMA_CAMPAIGN_RE = re.compile(
    r"(##\s*.*战役态.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
)

DEFAULT_HEALTH_URL = os.getenv("TENSOR_HEALTH_URL", "http://127.0.0.1:8010/health")
DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation",
)
DEFAULT_BOOTSTRAP_PATH = os.getenv(
    "AGENT_BOOTSTRAP_PATH", "/root/tp-agent/AGENT_BOOTSTRAP.md"
)
DEFAULT_PANORAMA_PATH = os.getenv("PANORAMA_PATH", "/root/tp-agent/PANORAMA.md")
DEFAULT_AUDIT_LOG = os.getenv(
    "STRATDOC_AUDIT_LOG", "/root/tp-agent/work/m5049/artifacts/stratdoc_audit.jsonl"
)

logger = logging.getLogger("strategic_doc_maintenance")


# ───────────────────────── 审计日志 ─────────────────────────

def write_audit_log(
    audit_path: str,
    action: str,
    status: str,
    details: dict | None = None,
) -> None:
    """追加一行审计日志（JSONL 格式）。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "status": status,
        "details": details or {},
    }
    p = Path(audit_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


# ───────────────────────── 外部数据源 ─────────────────────────

def fetch_health_timestamp(health_url: str = DEFAULT_HEALTH_URL) -> str | None:
    """从健康端点获取当前时间戳。失败返回 None（降级）。"""
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # 优先用 server 时间，其次用本地时间
            return data.get("timestamp") or data.get("server_time") or datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        logger.warning("health endpoint 不可用，降级使用本地时间: %s", exc)
        return datetime.now(timezone.utc).isoformat()


def _is_sqlite(db_url: str) -> bool:
    return db_url.startswith("sqlite")


def _sqlite_path(db_url: str) -> str:
    """sqlite:///path/to/db → path/to/db"""
    return db_url.replace("sqlite:///", "").replace("sqlite://", "")


def _get_db_connection(db_url: str):
    """获取数据库连接（PostgreSQL 或 SQLite）。"""
    if _is_sqlite(db_url):
        return sqlite3.connect(_sqlite_path(db_url))
    # PostgreSQL: 用 psycopg2 如果可用，否则降级
    try:
        import psycopg2
        # postgresql://user:pass@host:port/dbname
        m = re.match(
            r"postgresql://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(\S+)", db_url
        )
        if m:
            user, pwd, host, port, dbname = m.groups()
            return psycopg2.connect(
                host=host, port=int(port or 5432), dbname=dbname,
                user=user, password=pwd,
            )
    except ImportError:
        pass
    # 最终降级：返回 None 表示不可用
    return None


def query_campaign_state(db_url: str = DEFAULT_DB_URL) -> dict[str, Any]:
    """从数据库查询战役态：活跃计划、节点统计、最近门。

    返回结构：
    {
        "active_plans": [{"id", "name", "status", "node_count", "done_count", ...}],
        "node_stats": {"total": N, "done": N, "in_flight": N, "blocked": N, "failed": N},
        "recent_gates": [{"plan_name", "node_name", "status", "updated_at"}],
        "query_ts": "ISO timestamp",
    }
    """
    result: dict[str, Any] = {
        "active_plans": [],
        "node_stats": {"total": 0, "done": 0, "in_flight": 0, "blocked": 0, "failed": 0},
        "recent_gates": [],
        "query_ts": datetime.now(timezone.utc).isoformat(),
    }

    conn = _get_db_connection(db_url)
    if conn is None:
        logger.warning("数据库连接不可用，返回空战役态（降级）")
        return result

    try:
        if _is_sqlite(db_url):
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cur.fetchall()}
            if "campaign_plans" not in tables:
                logger.warning("campaign_plans 表不存在，返回空战役态")
                return result

            # 活跃计划
            cur.execute("""
                SELECT p.id, p.name, p.status,
                       (SELECT COUNT(*) FROM campaign_nodes n WHERE n.plan_id = p.id) as node_count,
                       (SELECT COUNT(*) FROM campaign_nodes n WHERE n.plan_id = p.id AND n.status = 'done') as done_count
                FROM campaign_plans p
                WHERE p.status IN ('active', 'draft')
                ORDER BY p.updated_at DESC
                LIMIT 20
            """)
            for row in cur.fetchall():
                result["active_plans"].append({
                    "id": row[0], "name": row[1], "status": row[2],
                    "node_count": row[3], "done_count": row[4],
                })

            # 节点统计
            cur.execute("""
                SELECT status, COUNT(*) FROM campaign_nodes GROUP BY status
            """)
            for row in cur.fetchall():
                status, count = row
                key = {"done": "done", "in-flight": "in_flight", "blocked": "blocked",
                       "failed": "failed", "pending": "pending"}.get(status, status)
                result["node_stats"][key] = count
            result["node_stats"]["total"] = sum(
                v for k, v in result["node_stats"].items() if k != "query_ts"
            )

            # 最近门（gate 类型节点）
            cur.execute("""
                SELECT p.name, n.name, n.status, n.updated_at
                FROM campaign_nodes n
                JOIN campaign_plans p ON n.plan_id = p.id
                WHERE n.node_type = 'gate'
                ORDER BY n.updated_at DESC
                LIMIT 10
            """)
            for row in cur.fetchall():
                result["recent_gates"].append({
                    "plan_name": row[0], "node_name": row[1],
                    "status": row[2], "updated_at": row[3],
                })
        else:
            # PostgreSQL
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.name, p.status,
                       (SELECT COUNT(*) FROM campaign_nodes n WHERE n.plan_id = p.id) as node_count,
                       (SELECT COUNT(*) FROM campaign_nodes n WHERE n.plan_id = p.id AND n.status = 'done') as done_count
                FROM campaign_plans p
                WHERE p.status IN ('active', 'draft')
                ORDER BY p.updated_at DESC
                LIMIT 20
            """)
            for row in cur.fetchall():
                result["active_plans"].append({
                    "id": row[0], "name": row[1], "status": row[2],
                    "node_count": row[3], "done_count": row[4],
                })

            cur.execute("""
                SELECT status, COUNT(*) FROM campaign_nodes GROUP BY status
            """)
            for row in cur.fetchall():
                status, count = row
                key = {"done": "done", "in-flight": "in_flight", "blocked": "blocked",
                       "failed": "failed", "pending": "pending"}.get(status, status)
                result["node_stats"][key] = count
            result["node_stats"]["total"] = sum(
                v for k, v in result["node_stats"].items() if k != "query_ts"
            )

            cur.execute("""
                SELECT p.name, n.name, n.status, n.updated_at
                FROM campaign_nodes n
                JOIN campaign_plans p ON n.plan_id = p.id
                WHERE n.node_type = 'gate'
                ORDER BY n.updated_at DESC
                LIMIT 10
            """)
            for row in cur.fetchall():
                result["recent_gates"].append({
                    "plan_name": row[0], "node_name": row[1],
                    "status": row[2], "updated_at": str(row[3]) if row[3] else "",
                })
    except Exception as exc:
        logger.warning("查询战役态失败，降级返回空: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


# ───────────────────────── 文档刷新 ─────────────────────────

def extract_human_rights_line(content: str) -> str | None:
    """提取人权线内容（如果存在）。"""
    m = HUMAN_RIGHTS_SECTION_RE.search(content)
    if m:
        return m.group(1)
    return None


def refresh_world_line(content: str, timestamp: str) -> str:
    """刷新 WORLD 行。如果不存在则追加到文件头（## 之前）。"""
    new_line = f"WORLD: {timestamp} dispatcher 心跳正常（战役/池实时态查 campaign_plans/nodes 表）"
    if WORLD_LINE_RE.search(content):
        return WORLD_LINE_RE.sub(new_line, content)
    else:
        # 在第一个 ## 之前插入
        m = re.search(r"\n##\s", content)
        if m:
            return content[:m.start()] + "\n" + new_line + "\n" + content[m.start():]
        else:
            return new_line + "\n\n" + content


def build_campaign_section(campaign_state: dict[str, Any]) -> str:
    """从战役态数据构建 PANORAMA 战役态节内容。"""
    lines = [
        "## 战役态（自动刷新，SQL 溯源）",
        "",
        f"> 刷新时间: {campaign_state.get('query_ts', 'N/A')}",
        "",
        "### 活跃计划",
        "",
    ]
    if campaign_state["active_plans"]:
        lines.append("| ID | 名称 | 状态 | 节点数 | 完成数 |")
        lines.append("|---|---|---|---|---|")
        for p in campaign_state["active_plans"]:
            lines.append(f"| {p['id']} | {p['name']} | {p['status']} | {p['node_count']} | {p['done_count']} |")
    else:
        lines.append("*无活跃计划（或数据库不可用）*")

    lines.append("")
    lines.append("### 节点统计")
    lines.append("")
    stats = campaign_state["node_stats"]
    lines.append(f"- 总计: {stats.get('total', 0)}")
    lines.append(f"- 完成: {stats.get('done', 0)}")
    lines.append(f"- 进行中: {stats.get('in_flight', 0)}")
    lines.append(f"- 阻塞: {stats.get('blocked', 0)}")
    lines.append(f"- 失败: {stats.get('failed', 0)}")

    lines.append("")
    lines.append("### 最近门")
    lines.append("")
    if campaign_state["recent_gates"]:
        lines.append("| 计划 | 门名称 | 状态 | 更新时间 |")
        lines.append("|---|---|---|---|")
        for g in campaign_state["recent_gates"]:
            lines.append(f"| {g['plan_name']} | {g['node_name']} | {g['status']} | {g['updated_at']} |")
    else:
        lines.append("*无门节点（或数据库不可用）*")

    lines.append("")
    return "\n".join(lines)


def refresh_panorama_campaign_section(content: str, campaign_state: dict[str, Any]) -> str:
    """刷新 PANORAMA 中的战役态节。如果不存在则追加到末尾。"""
    new_section = build_campaign_section(campaign_state)
    if PANORAMA_CAMPAIGN_RE.search(content):
        return PANORAMA_CAMPAIGN_RE.sub(new_section, content)
    else:
        return content + "\n" + new_section


def protect_human_rights(content: str, original_rights: str | None) -> str:
    """确保人权线内容不被改写。如果原始有人权线，恢复它。"""
    if original_rights is None:
        return content
    # 如果当前内容有人权线，替换为原始的
    if HUMAN_RIGHTS_SECTION_RE.search(content):
        return HUMAN_RIGHTS_SECTION_RE.sub(original_rights, content)
    else:
        # 追加到末尾
        return content + "\n" + original_rights


def compute_content_hash(content: str) -> str:
    """计算内容 MD5 用于幂等检测。"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


# ───────────────────────── 主刷新循环 ─────────────────────────

def run_refresh_cycle(
    bootstrap_path: str = DEFAULT_BOOTSTRAP_PATH,
    panorama_path: str = DEFAULT_PANORAMA_PATH,
    db_url: str = DEFAULT_DB_URL,
    health_url: str = DEFAULT_HEALTH_URL,
    audit_path: str = DEFAULT_AUDIT_LOG,
    dry_run: bool = False,
) -> dict[str, Any]:
    """执行一轮刷新。返回结果字典。"""
    result = {
        "success": False,
        "bootstrap_updated": False,
        "panorama_updated": False,
        "human_rights_preserved": True,
        "errors": [],
        "hashes_before": {},
        "hashes_after": {},
    }

    # 1. 获取时间戳
    timestamp = fetch_health_timestamp(health_url)
    if timestamp is None:
        result["errors"].append("health endpoint 不可用")
        write_audit_log(audit_path, "refresh_world", "degraded", {"error": "health_unavailable"})
    else:
        write_audit_log(audit_path, "fetch_health", "ok", {"timestamp": timestamp})

    # 2. 查询战役态
    campaign_state = query_campaign_state(db_url)
    write_audit_log(audit_path, "query_campaign", "ok" if campaign_state["active_plans"] else "empty",
                    {"plan_count": len(campaign_state["active_plans"])})

    # 3. 读取并刷新 AGENT_BOOTSTRAP.md
    bp = Path(bootstrap_path)
    if bp.exists():
        original_content = bp.read_text(encoding="utf-8")
        result["hashes_before"]["bootstrap"] = compute_content_hash(original_content)
        original_rights = extract_human_rights_line(original_content)

        new_content = refresh_world_line(original_content, timestamp or "")
        new_content = protect_human_rights(new_content, original_rights)

        if new_content != original_content:
            result["bootstrap_updated"] = True
            if not dry_run:
                bp.write_text(new_content, encoding="utf-8")
            result["hashes_after"]["bootstrap"] = compute_content_hash(new_content)
        else:
            result["hashes_after"]["bootstrap"] = result["hashes_before"]["bootstrap"]

        # 验证人权线
        if original_rights is not None:
            final_rights = extract_human_rights_line(new_content)
            if final_rights != original_rights:
                result["human_rights_preserved"] = False
                result["errors"].append("人权线被意外修改")
    else:
        result["errors"].append(f"AGENT_BOOTSTRAP.md 不存在: {bootstrap_path}")

    # 4. 读取并刷新 PANORAMA.md
    pp = Path(panorama_path)
    if pp.exists():
        original_content = pp.read_text(encoding="utf-8")
        result["hashes_before"]["panorama"] = compute_content_hash(original_content)
        original_rights = extract_human_rights_line(original_content)

        new_content = refresh_panorama_campaign_section(original_content, campaign_state)
        new_content = protect_human_rights(new_content, original_rights)

        if new_content != original_content:
            result["panorama_updated"] = True
            if not dry_run:
                pp.write_text(new_content, encoding="utf-8")
            result["hashes_after"]["panorama"] = compute_content_hash(new_content)
        else:
            result["hashes_after"]["panorama"] = result["hashes_before"]["panorama"]

        # 验证人权线
        if original_rights is not None:
            final_rights = extract_human_rights_line(new_content)
            if final_rights != original_rights:
                result["human_rights_preserved"] = False
                result["errors"].append("PANORAMA 人权线被意外修改")
    else:
        result["errors"].append(f"PANORAMA.md 不存在: {panorama_path}")

    result["success"] = len(result["errors"]) == 0
    write_audit_log(audit_path, "refresh_cycle",
                    "ok" if result["success"] else "partial",
                    {"bootstrap_updated": result["bootstrap_updated"],
                     "panorama_updated": result["panorama_updated"],
                     "human_rights_preserved": result["human_rights_preserved"],
                     "errors": result["errors"]})

    return result


# ───────────────────────── CLI ─────────────────────────

def main():
    parser = argparse.ArgumentParser(description="战略文档自动维护")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="单轮刷新")
    run_p.add_argument("--dry", action="store_true", help="干跑模式")
    run_p.add_argument("--bootstrap", default=DEFAULT_BOOTSTRAP_PATH)
    run_p.add_argument("--panorama", default=DEFAULT_PANORAMA_PATH)
    run_p.add_argument("--db-url", default=DEFAULT_DB_URL)
    run_p.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    run_p.add_argument("--audit", default=DEFAULT_AUDIT_LOG)

    daemon_p = sub.add_parser("daemon", help="守护模式（循环刷新）")
    daemon_p.add_argument("--interval", type=int, default=300, help="刷新间隔（秒）")
    daemon_p.add_argument("--bootstrap", default=DEFAULT_BOOTSTRAP_PATH)
    daemon_p.add_argument("--panorama", default=DEFAULT_PANORAMA_PATH)
    daemon_p.add_argument("--db-url", default=DEFAULT_DB_URL)
    daemon_p.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    daemon_p.add_argument("--audit", default=DEFAULT_AUDIT_LOG)

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "run":
        result = run_refresh_cycle(
            bootstrap_path=args.bootstrap,
            panorama_path=args.panorama,
            db_url=args.db_url,
            health_url=args.health_url,
            audit_path=args.audit,
            dry_run=args.dry,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        sys.exit(0 if result["success"] else 1)

    elif args.command == "daemon":
        logger.info("启动战略文档自动维护 daemon（间隔 %ds）", args.interval)
        write_audit_log(args.audit, "daemon_start", "ok", {"interval": args.interval})
        prev_hashes = {}
        consecutive_idempotent = 0
        while True:
            try:
                result = run_refresh_cycle(
                    bootstrap_path=args.bootstrap,
                    panorama_path=args.panorama,
                    db_url=args.db_url,
                    health_url=args.health_url,
                    audit_path=args.audit,
                    dry_run=False,
                )
                # 幂等检测
                current_hashes = result.get("hashes_after", {})
                if current_hashes == prev_hashes:
                    consecutive_idempotent += 1
                    logger.info("幂等轮次: %d", consecutive_idempotent)
                else:
                    consecutive_idempotent = 0
                prev_hashes = current_hashes
            except Exception as exc:
                logger.error("daemon 循环异常: %s", exc)
                write_audit_log(args.audit, "daemon_error", "error", {"error": str(exc)})
            time.sleep(args.interval)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
