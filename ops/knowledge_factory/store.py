"""存储层：走 psql（本机 loong64 沙箱里 psycopg2 是 stub，平台既有守护也走 psql）。

对外只有三个动作：q（查询返回行）、execute（写）、ensure_schema（幂等建表）。
参数一律经 lit() 渲染成 SQL 字面量（单引号转义 + jsonb 自动 cast），调用方不拼裸串。
"""
import json
import os
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION = os.path.join(REPO_ROOT, "backend", "migrations",
                         "121_knowledge_factory_work_packages.sql")
SEP = "\x01"
REC = "\x02"  # 记录分隔符（换行不安全）


def lit(v):
    """Python 值 → SQL 字面量。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dict, list)):
        return lit(json.dumps(v, ensure_ascii=False)) + "::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def bind(sql, params):
    """把 %s 依次替换为字面量（按出现顺序，不支持复用同一参数）。"""
    out, params = [], list(params)
    for chunk in sql.split("%s"):
        out.append(chunk)
        if params:
            out.append(lit(params.pop(0)))
    if params:
        raise ValueError("参数多于占位符")
    return "".join(out)


class Store:
    """薄 psql 封装。runner 可注入（测试用假 runner，不碰 DB）。"""

    def __init__(self, db=None, runner=None):
        self.db = db or os.getenv("TP_PG_DB", "tensorplatform")
        self.runner = runner or self._psql
        self.calls = []

    # ---------- 基础 ----------
    def _psql(self, sql):
        # -R 显式记录分隔：psql 输出里 markdown 自带换行，不能拿 \n 分帧（踩过：json_agg 被腰斩）
        cmd = ["runuser", "-u", "postgres", "--", "psql", "-d", self.db,
               "-tA", "-F", SEP, "-R", REC, "-v", "ON_ERROR_STOP=1", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError("psql failed: " + (r.stderr or r.stdout)[:400])
        out = r.stdout.rstrip("\n")
        if not out:
            return []
        return [rec.split(SEP) for rec in out.split(REC) if rec.strip() != ""]

    def q(self, sql, params=()):
        sql = bind(sql, params) if params else sql
        self.calls.append(sql)
        return self.runner(sql)

    def execute(self, sql, params=()):
        return self.q(sql, params)

    def scalar(self, sql, params=(), default=None):
        rows = self.q(sql, params)
        if not rows or not rows[0]:
            return default
        return rows[0][0]

    def json_rows(self, sql, params=()):
        return [json.loads(r[0]) for r in self.q(sql, params) if r and r[0]]

    def qj(self, sql, params=()):
        """多行文本安全的查询：整表聚成 JSON 再解析（psql -tA 的换行分帧扛不住 markdown）。"""
        wrapped = ("SELECT COALESCE(json_agg(t), '[]'::json) FROM ("
                   + (bind(sql, params) if params else sql) + ") t")
        self.calls.append(wrapped)
        rows = self.runner(wrapped)
        if not rows or not rows[0] or not rows[0][0]:
            return []
        return json.loads(rows[0][0])

    # ---------- 生命周期 ----------
    def ensure_schema(self):
        with open(MIGRATION, encoding="utf-8") as f:
            self._psql(f.read())

    # ---------- 报告面（只读） ----------
    def report(self, report_id):
        rows = self.qj(
            "SELECT id, title, scenario_key, markdown, summary, tags, status "
            "FROM tensor_reports WHERE id = %s", (int(report_id),))
        if not rows:
            return None
        r = rows[0]
        return {"id": int(r["id"]), "title": r["title"], "scenario_key": r["scenario_key"],
                "markdown": r["markdown"] or "", "summary": r["summary"] or "",
                "tags": r["tags"] or [], "status": r["status"]}

    def find_result_report(self, package_id):
        """结果信道：标题前缀 KF-RESULT 或 tags 含 kf-wp:<package_id>，取最新一条。"""
        rows = self.qj(
            "SELECT id, markdown FROM tensor_reports "
            "WHERE title LIKE %s OR tags @> %s ORDER BY id DESC LIMIT 1",
            (f"%{package_id}%", json.dumps([f"kf-wp:{package_id}"])))
        if not rows:
            return None
        return {"id": int(rows[0]["id"]), "markdown": rows[0]["markdown"] or ""}

    # ---------- 工包面 ----------
    def upsert_package(self, pkg):
        cols = ["package_id", "report_id", "task_type", "lane", "status", "mission_id",
                "skill_id", "stratum", "attempt", "gates", "gates_ok", "cost",
                "contract", "fail_reason"]
        vals = [pkg.get(c) for c in cols]
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
        self.execute(
            f"INSERT INTO tk_work_packages ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (package_id) DO UPDATE SET {upd}, updated_at = now()", vals)

    def set_status(self, package_id, status, **fields):
        sets = ["status = %s", "updated_at = now()"]
        vals = [status]
        for k, v in fields.items():
            sets.append(f"{k} = %s")
            vals.append(v)
        vals.append(package_id)
        self.execute(f"UPDATE tk_work_packages SET {', '.join(sets)} "
                     f"WHERE package_id = %s", vals)

    def packages(self, status=None, task_type=None, lane=None, limit=1000):
        where, vals = [], []
        if status:
            where.append("status = %s")
            vals.append(status)
        if task_type:
            where.append("task_type = %s")
            vals.append(task_type)
        if lane:
            where.append("lane = %s")
            vals.append(lane)
        w = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.q(
            f"SELECT package_id, report_id, task_type, lane, status, mission_id, "
            f"reviewer_mission_id, attempt, stratum FROM tk_work_packages {w} "
            f"ORDER BY package_id LIMIT {int(limit)}", vals)
        keys = ["package_id", "report_id", "task_type", "lane", "status", "mission_id",
                "reviewer_mission_id", "attempt", "stratum"]
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["report_id"] = int(d["report_id"])
            d["attempt"] = int(d["attempt"] or 0)
            d["mission_id"] = int(d["mission_id"]) if d["mission_id"] else None
            d["reviewer_mission_id"] = (int(d["reviewer_mission_id"])
                                        if d["reviewer_mission_id"] else None)
            out.append(d)
        return out

    def package(self, package_id):
        rows = self.q(
            "SELECT package_id, report_id, task_type, lane, status, mission_id, "
            "reviewer_mission_id, attempt, stratum, gates, contract, fail_reason "
            "FROM tk_work_packages WHERE package_id = %s", (package_id,))
        if not rows:
            return None
        r = rows[0]
        return {"package_id": r[0], "report_id": int(r[1]), "task_type": r[2], "lane": r[3],
                "status": r[4], "mission_id": int(r[5]) if r[5] else None,
                "reviewer_mission_id": int(r[6]) if r[6] else None,
                "attempt": int(r[7] or 0), "stratum": r[8],
                "gates": json.loads(r[9]) if r[9] else {},
                "contract": json.loads(r[10]) if r[10] else None,
                "fail_reason": r[11]}

    def seen_claim_ids(self):
        rows = self.q("SELECT ko_id FROM tk_ingest_drafts")
        return {r[0].split(":", 1)[-1] for r in rows if r}

    def draft_exists(self, ko_id):
        return bool(self.q("SELECT 1 FROM tk_ingest_drafts WHERE ko_id = %s", (ko_id,)))

    def insert_draft(self, d):
        self.execute(
            "INSERT INTO tk_ingest_drafts (ko_id, package_id, report_id, lane, claim_text, "
            "payload, verification_status, ko_review_status, science_verification_status, "
            "mission_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (ko_id) DO NOTHING",
            (d["ko_id"], d["package_id"], int(d["report_id"]), d.get("lane", "C"),
             d.get("claim_text"), d.get("payload", {}),
             "unverified", d.get("ko_review_status", "unreviewed"),
             d.get("science_verification_status", "not_verified"),
             d.get("mission_status", "done")))

    def mission(self, mission_id):
        rows = self.q(
            "SELECT id, status, repo, task_key, created_at, finished_at, "
            "EXTRACT(EPOCH FROM (COALESCE(finished_at, now()) - created_at))::int "
            "FROM tensor_dev_missions WHERE id = %s", (int(mission_id),))
        if not rows:
            return None
        r = rows[0]
        return {"id": int(r[0]), "status": r[1], "repo": r[2], "task_key": r[3],
                "created_at": r[4], "finished_at": r[5], "wall_seconds": int(r[6] or 0)}

    def mission_metering(self, mission_id):
        rows = self.q(
            "SELECT COALESCE(sum(tokens_used),0), COALESCE(sum(tool_calls),0), "
            "COALESCE(sum(wall_seconds),0) FROM metered_usage WHERE mission_id = %s",
            (int(mission_id),))
        if not rows or not rows[0]:
            return {"tokens": 0, "tool_calls": 0, "wall_seconds": 0, "metered": False}
        t, tc, ws = (int(float(x or 0)) for x in rows[0])
        return {"tokens": t, "tool_calls": tc, "wall_seconds": ws, "metered": t > 0 or tc > 0}

    def snapshot(self, round_label, metrics):
        self.execute("INSERT INTO tk_metric_snapshots (round, metrics) VALUES (%s, %s)",
                     (round_label, metrics))