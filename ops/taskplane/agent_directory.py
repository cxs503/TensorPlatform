#!/usr/bin/env python3
"""tp_agent_directory —— 生态「发现面+个体记忆」边车（owner 2026-11-01 批：自主生态两原语之一）。
127.0.0.1:8011（仅容器内，不对外），PG 表 agent_directory，stdlib http.server。
路由：
  GET  /health                       → {"ok":true}
  GET  /directory                    → 全席条目（含信用分+近况，发现面）
  GET  /directory/<seat>             → 单席条目（席位记忆，claim 时注入）
  POST /directory                    → upsert 本席描述字段（seat_id/model/role/self_desc/
                                        skills/current_goal/needs/offers/recent_missions）
  POST /directory/<seat>/lesson      → append-only 追加一条教训/心得（个体记忆，只增不删）
边界：无删除/改史路由；lessons 只增；表不在平台业务库键上（独立表，出问题不伤 missions）。
autoheal 纳管；日志 /var/log/tp-fleet/agent_directory.log
"""
import datetime, json, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8011
LOG = "/var/log/tp-fleet/agent_directory.log"
FIELDS = ["model", "role", "self_desc", "skills", "current_goal", "needs", "offers", "recent_missions"]


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def psql(sql, vals=None):
    """vals 用 %s 占位；借 psql --csv/-A 通道避免文本转义地狱。"""
    if vals:
        import shlex
        lit = []
        for v in vals:
            if v is None:
                lit.append("NULL")
            elif isinstance(v, (int, float)):
                lit.append(str(v))
            else:
                lit.append("$q$" + str(v) + "$q$")
        sql = sql.replace("%s", "{}").format(*lit) if "%s" in sql else sql
    r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
                        "-t", "-A", "-F", "\x02", "-c", sql],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return [ln.split("\x02") for ln in r.stdout.splitlines() if ln.strip()]


def ensure_table():
    psql("""CREATE TABLE IF NOT EXISTS agent_directory (
        seat_id text PRIMARY KEY,
        model text, role text,
        self_desc text, skills text, current_goal text, needs text, offers text,
        recent_missions text, lessons text DEFAULT '',
        created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now())""")
    log("table ready")


def seat_stats(seat):
    rows = psql("SELECT count(*) FILTER (WHERE status='succeeded'), count(*) "
                f"FROM tensor_dev_missions WHERE claimed_by='{seat}'")
    succ, tot = (rows[0] if rows else ["0", "0"])
    return int(succ), int(tot)


def get_row(seat):
    rows = psql(f"SELECT seat_id, coalesce(model,''), coalesce(role,''), coalesce(self_desc,''), "
                f"coalesce(skills,''), coalesce(current_goal,''), coalesce(needs,''), "
                f"coalesce(offers,''), coalesce(recent_missions,''), coalesce(lessons,''), "
                f"to_char(updated_at,'MM-DD HH24:MI') FROM agent_directory WHERE seat_id='{seat}'")
    if not rows:
        return None
    r = rows[0]
    succ, tot = seat_stats(seat)
    return {"seat_id": r[0], "model": r[1], "role": r[2], "self_desc": r[3], "skills": r[4],
            "current_goal": r[5], "needs": r[6], "offers": r[7], "recent_missions": r[8],
            "lessons": r[9], "updated_at": r[10], "missions_done": succ, "missions_total": tot}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path == "/health":
                return self._send(200, {"ok": True})
            if self.path == "/directory":
                seats = psql("SELECT seat_id FROM agent_directory ORDER BY updated_at DESC")
                return self._send(200, {"agents": [get_row(s[0]) for s in seats]})
            if self.path.startswith("/directory/"):
                seat = self.path.split("/")[2].split("?")[0]
                row = get_row(seat)
                return self._send(200, row if row else {"error": "no such seat", "seat_id": seat})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/directory":
                seat = (body.get("seat_id") or "").strip()
                if not seat or not seat.replace("-", "").replace("_", "").isalnum():
                    return self._send(400, {"error": "seat_id required (alnum/-/_)"})
                sets, vals = ["seat_id"], [seat]
                for f in FIELDS:
                    if f in body:
                        # 消毒换行：psql 按行解析，字段值带 \n 会拆断行（冒烟测试实证）
                        sets.append(f)
                        vals.append(str(body[f])[:2000].replace("\n", " ⏎ ").replace("\r", ""))
                pairs = [f"{sets[0]}='{vals[0]}'"] + [f"{sets[i]}={ '$q$'+vals[i]+'$q$' if vals[i]!='NULL' else 'NULL'}"
                                                     for i in range(1, len(sets))]
                psql(f"INSERT INTO agent_directory ({','.join(sets)}) VALUES "
                     f"({','.join('$q$'+v+'$q$' if v!='NULL' else 'NULL' for v in vals)}) "
                     f"ON CONFLICT (seat_id) DO UPDATE SET {','.join(pairs[1:])}, updated_at=now()")
                log(f"UPSERT {seat} fields={[f for f in FIELDS if f in body]}")
                return self._send(200, {"ok": True, "seat_id": seat})
            parts = self.path.split("/")
            if len(parts) == 4 and parts[3] == "lesson":
                seat, text = parts[2], (body.get("text") or "").strip()[:1000]
                text = text.replace("\n", " ⏎ ").replace("\r", "")
                if not text:
                    return self._send(400, {"error": "text required"})
                stamp = datetime.datetime.now().strftime("%m-%d %H:%M")
                psql(f"INSERT INTO agent_directory (seat_id, lessons) VALUES ('{seat}', "
                     f"$q$[{stamp}] {text}$q$) ON CONFLICT (seat_id) DO UPDATE SET "
                     f"lessons=agent_directory.lessons || ' ⏎ ' || $q$[{stamp}] {text}$q$, "
                     f"updated_at=now()")
                log(f"LESSON {seat}: {text[:80]}")
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ensure_table()
    log(f"=== tp_agent_directory 启动 @127.0.0.1:{PORT}（发现面+个体记忆；upsert+append-only lessons）===")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
