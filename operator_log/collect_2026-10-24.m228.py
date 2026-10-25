#!/usr/bin/env python3
"""operator 班次 m228（2026-10-24 第 83 班）巡检读数采集器——五步 + 审计面一次采完。

判据数字零手抄（identity.md 纪律）：所有读数当场从 DB/进程/文件/端点直读，
stdout 即 readings JSON。凭据坑（m210 回退链）：中继复验必须在 bash 终端
`set -a; source agent_work/operator.env; set +a` 后由 curl 完成，本脚本只读环境变量
RELAY_PROBE（形如 "200 0.258 7"），绝不在 python 子进程内 source。

用法（在 worktree 根，即本文件上两级）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  RELAY_PROBE="$(curl -s -o /tmp/relay.body -w '%{http_code} %{time_total}' \
      --max-time 20 -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models") \
    $(python3 -c 'import json;print(len(json.load(open("/tmp/relay.body"))["data"]))')"
  python3 operator_log/collect_2026-10-24.py > operator_log/2026-10-24.readings.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

ECO = Path("/srv/tensor/shared/tensor_eco")
AGENT_WORK = ECO / "agent_work"
REPO = Path(__file__).resolve().parents[1]
PSQL = ["docker", "exec", "open-foundation-postgres", "psql", "-U", "foundation",
        "-d", "open_digital_foundation", "-A", "-F", "|", "-t", "-c"]
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
# 本班任务起点（派发时点），用于区分「本班巡检窗口内新增」
SHIFT_START_UTC = "2026-10-23T17:13:02Z"  # 本班 m228 created_at（DB 直读）


def psql(sql: str) -> list[list[str]]:
    """只读查询：单条 SQL 一次取全部列，少调用。"""
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True,
                         env=ENV, timeout=120).stdout
    return [ln.split("|") for ln in out.strip().splitlines() if ln.strip()]


def shell(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          cwd=REPO, timeout=180).stdout.strip()


def refill_check(mid: int) -> dict:
    """机械门 refill_check（playbook 案例 2）：rc + 末行文字。"""
    code = ("set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a; "
            f"/srv/tensor/shared/venvs/tensor_eco/bin/python TensorPlatform/tools/agent/"
            f"refill_check.py {mid}")
    r = subprocess.run(["bash", "-lc", code], capture_output=True, text=True,
                       cwd=str(ECO), timeout=180)
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return {"id": mid, "rc": r.returncode,
            "last_line": lines[-1] if lines else ""}


def poses_legs(prefix_from: int, width: int) -> list[dict]:
    """承接门：同 goal 前缀的全部腿（playbook 案例 7 附加门②）。"""
    rows = psql(f"SELECT id,status FROM tensor_dev_missions WHERE "
                f"left(goal,{width})=left((SELECT goal FROM tensor_dev_missions "
                f"WHERE id={prefix_from}),{width}) ORDER BY id")
    return [{"id": int(r[0]), "status": r[1]} for r in rows]


def _dated_lines(lg: Path) -> list[tuple[str, str, str]]:
    """把守护日志的行钉上日期：(YYYY-MM-DD, HH:MM:SS, 行文本)。

    行内只有 HH:MM:SS、不带日期，文件跨零点续写 ⇒ 用「文件 mtime 日期」作基准日，
    再以**末行时刻**为日志时钟的当下：行内时刻 > 末行时刻 ⇒ 属前一日。
    （m225 班跨 0 点踩到的坑：若一律用 today 拼日期，会伪造出「今天 21:13」的未来时戳。）
    """
    base_day = time.strftime("%Y-%m-%d", time.localtime(lg.stat().st_mtime))
    lines = lg.read_text(encoding="utf-8", errors="replace").splitlines()
    t_last = None
    for ln in reversed(lines):
        m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", ln.strip())
        if m:
            t_last = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
            break
    out = []
    for ln in lines:
        m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", ln.strip())
        if not m:
            continue
        hhmmss = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
        day = base_day if (t_last and hhmmss <= t_last) else _prev_day(base_day)
        out.append((day, hhmmss, ln.strip()[:180]))
    return out


def _prev_day(day: str) -> str:
    """给定 YYYY-MM-DD 返回前一日（日志跨零点定日用，纯标准库）。"""
    return time.strftime("%Y-%m-%d",
                         time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) - 86400))


def cooling_events(shift_start_local: str, today: str) -> dict:
    """R13 判据：冷却事件 + 本班窗口内的 transient 事件（按定日后的时间线取）。"""
    per_file, cool_ts, transient = [], [], []
    for lg in sorted(AGENT_WORK.glob("agent-*.log")):
        dated = _dated_lines(lg)
        cools = [(d, t, ln) for d, t, ln in dated if "冷却 600s" in ln]
        trans = [(d, t, ln) for d, t, ln in dated if "transient -> retry" in ln]
        if cools:
            per_file.append({"file": lg.name,
                             "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                                    time.localtime(lg.stat().st_mtime)),
                             "count": len(cools), "last_dated": cools[-1][0] + "T" + cools[-1][1],
                             "last": cools[-1][2]})
            cool_ts.extend(d + "T" + t for d, t, _ in cools)
        for d, t, ln in trans:
            if d + "T" + t >= shift_start_local:
                transient.append({"file": lg.name, "at": d + "T" + t, "line": ln})
    return {"per_file": per_file,
            "total": len(cool_ts),
            "newest_local_ts": max(cool_ts) if cool_ts else None,
            "events_today": sorted(t for t in cool_ts if t.startswith(today)),
            "files_today": [x["file"] for x in per_file
                            if x["last_dated"].startswith(today)],
            "stale_files_excluded": [x["file"] for x in per_file
                                     if not x["last_dated"].startswith(today)],
            "transient_since_shift": transient,
            "transient_since_shift_n": len(transient)}


def main() -> int:
    now = time.time()
    R: dict = {"mission": "m228", "shift": "第83班 (派发器标注 shift#48，倒序非递增)",
               "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(now)),
               "t0_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
               "shift_start_utc": SHIFT_START_UTC,
               "collected_at_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(now)),
               "worktree": str(REPO)}

    # ── 步骤 1：在飞 + 心跳鲜度 ──
    rows = psql("SELECT id,status,coalesce(round(extract(epoch FROM "
                "(now()-heartbeat_at)))::text,'-'),coalesce(repo,''),"
                "coalesce(result->>'stop_condition',''),"
                "coalesce(left(regexp_replace(goal,E'[\\n\\r]+',' '),34),'') "
                "FROM tensor_dev_missions WHERE status IN ('claimed','running') "
                "ORDER BY id")
    missions = [{"id": int(r[0]), "status": r[1],
                 "hb_s": int(r[2]) if r[2] not in ("-", "") else None,
                 "repo": r[3], "stop_condition": r[4] or None, "goal_head": r[5]}
                for r in rows]
    R["step1_inflight"] = {
        "count": len(missions), "missions": missions,
        "max_hb_s": max((m["hb_s"] or 0) for m in missions) if missions else 0,
        "stale_over_240s": [m["id"] for m in missions if (m["hb_s"] or 0) > 240],
        "with_stop_condition": [m["id"] for m in missions if m["stop_condition"]],
    }

    # ── 步骤 2：守护计数等式（实测 vs 主仓权威源 workers.json，R17/R18）──
    auth_path = AGENT_WORK / "workers.json"
    auth = {k: v for k, v in json.loads(auth_path.read_text("utf-8")).items()
            if not k.startswith("_")}
    detail = shell("ps -eo args | grep -o '[t]ensor_agent.py --model [^ ]*' "
                   "| awk '{print $3}' | sort | uniq -c")
    measured = {}
    for ln in detail.splitlines():
        n, model = ln.split()
        measured[model] = measured.get(model, 0) + int(n)
    per_model = {m: {"authority": auth.get(m, 0), "measured": measured.get(m, 0)}
                 for m in sorted(set(auth) | set(measured))}
    R["step2_guardian"] = {
        "measured_total": sum(measured.values()),
        "authority_sum": sum(auth.values()),
        "authority_source": str(auth_path),
        "per_model": per_model,
        "per_model_all_equal": all(v["authority"] == v["measured"]
                                   for v in per_model.values()),
        "slot_workers": shell("ps -eo args | grep -Ec -- '--slot [2-9]' || true"),
        "pgrep_count": shell("pgrep -f 'tensor_agen[t].py' | wc -l"),
    }

    # ── 步骤 3：中继健康（bash 侧 curl 传入）──
    probe = (os.environ.get("RELAY_PROBE") or "").split()
    R["step3_relay"] = {"http_code": int(probe[0]) if probe else None,
                        "time_total_s": float(probe[1]) if len(probe) > 1 else None,
                        "models": int(probe[2]) if len(probe) > 2 else None}
    # R17 同型坑：worktree 内 tools/agent/*.json 可能陈旧，端点口径读主仓活体文件
    ep_live = ECO / "TensorPlatform" / "tools" / "agent" / "llm_endpoints.json"
    ep_copy = REPO / "tools" / "agent" / "llm_endpoints.json"
    ep = json.loads(ep_live.read_text("utf-8"))
    epc = json.loads(ep_copy.read_text("utf-8"))
    R["step3_relay"].update({
        "endpoints_n": len(ep.get("endpoints", [])),
        "endpoint_names": [e.get("name") for e in ep.get("endpoints", [])],
        "cooldown_seconds": ep.get("cooldown_seconds"),
        "transient_threshold": ep.get("transient_threshold"),
        "config_source": str(ep_live),
        "worktree_copy_cooldown_seconds": epc.get("cooldown_seconds"),
        "worktree_copy_stale": epc.get("cooldown_seconds") != ep.get("cooldown_seconds"),
        "code_caps_600": "min(self.cooldown, 600)" in (
            ECO / "TensorPlatform/tools/agent/llm_gateway.py").read_text("utf-8"),
    })

    # ── 步骤 4：网关冷却态 ──
    st_path = AGENT_WORK / "llm_gateway_state.json"
    state = json.loads(st_path.read_text(encoding="utf-8") or "{}")
    R["step4_cooldown"] = {
        "path": str(st_path),
        "raw": state,
        "now_active": {k: v for k, v in state.items()
                       if (v.get("blocked_until") or 0) > now},
        "delta_s": {k: round((v.get("blocked_until") or 0) - now, 1)
                    for k, v in state.items()},
        "mtime_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(st_path.stat().st_mtime)),
    }

    # ── 步骤 5：终态 mission ──
    t = psql("SELECT count(*) FILTER (WHERE status='failed'),"
             "count(*) FILTER (WHERE status='succeeded'),"
             "count(*) FILTER (WHERE status='failed' AND finished_at >= "
             "date_trunc('day', now())),"
             "count(*) FILTER (WHERE status='failed' AND finished_at >= "
             f"'{SHIFT_START_UTC}'),"
             "coalesce(extract(epoch FROM (now()-max(finished_at)"
             " FILTER (WHERE status='failed')))::int::text,'-') "
             "FROM tensor_dev_missions WHERE finished_at > now()"
             "-interval '24 hours'")[0]
    fails = psql("SELECT id,status,coalesce(result->>'stop_condition',''),"
                 "round(extract(epoch FROM (now()-finished_at)))::text,"
                 "coalesce(to_char(finished_at,'MM-DD HH24:MI'),''),"
                 "coalesce(left(regexp_replace(goal,E'[\\n\\r]+',' '),34),'') "
                 "FROM tensor_dev_missions WHERE status='failed' AND finished_at > "
                 "now()-interval '24 hours' ORDER BY id")
    aw = psql("SELECT count(*) FROM tensor_dev_missions "
              "WHERE status='awaiting_acceptance'")
    R["step5_terminal"] = {
        "h24_failed": int(t[0]), "h24_succeeded": int(t[1]),
        "today_failed": int(t[2]), "since_shift_failed": int(t[3]),
        "last_failed_age_s": int(t[4]) if t[4] not in ("-", "") else None,
        "failed_missions": [{"id": int(r[0]), "status": r[1],
                             "stop_condition": r[2] or None,
                             "age_s": int(r[3]), "finished": r[4],
                             "goal_head": r[5]} for r in fails],
        "awaiting_acceptance": int(aw[0][0]),
        "refill_checks": [refill_check(i) for i in (208, 216, 217, 218)],
        "goal_208_legs": poses_legs(208, 34),
        "goal_216_legs": poses_legs(216, 34),
        "goal_217_legs": poses_legs(217, 34),
        "goal_218_legs": poses_legs(218, 34),
        "inflight_budget": [{"id": int(r[0]), "status": r[1],
                             "max_tool_calls": r[2], "max_wall_seconds": r[3]}
                            for r in psql("SELECT id,status,budget->>'max_tool_calls',"
                                          "budget->>'max_wall_seconds' FROM "
                                          "tensor_dev_missions WHERE status IN "
                                          "('claimed','running') ORDER BY id")],
        "claimed_by": [{"id": int(r[0]), "by": r[1], "model": r[2],
                        "origin": r[3], "created": r[4]} for r in psql(
                            "SELECT id,coalesce(claimed_by,''),coalesce(model,''),"
                            "origin,to_char(created_at,'MM-DD HH24:MI:SS') "
                            "FROM tensor_dev_missions WHERE status IN "
                            "('claimed','running') ORDER BY id")],
        "refill_audit": [{"id": int(r[0]), "by": r[1], "added": r[2]}
                         for r in psql("SELECT id,"
                                       "coalesce(result->'refill'->>'by',''),"
                                       "coalesce(result->'refill'->'added'->>"
                                       "'tool_calls','')||'/'||coalesce("
                                       "result->'refill'->'added'->>'wall_seconds','') "
                                       "FROM tensor_dev_missions "
                                       "WHERE result ? 'refill' AND id >= 200 "
                                       "ORDER BY id")],
    }

    # ── 步骤 5b：R13 群杀证据（冷却事件 vs 终态时刻）──
    ce = cooling_events(time.strftime("%Y-%m-%dT%H:%M:%S",
                                  time.localtime(
                                      time.mktime(time.strptime(SHIFT_START_UTC, "%Y-%m-%dT%H:%M:%SZ"))
                                      - time.timezone)),
                    today=time.strftime("%Y-%m-%d"))
    g_live = {lg.name: lg.read_text(encoding="utf-8", errors="replace")
             .count("压缩摘要为空") for lg in AGENT_WORK.glob("agent-*.log")}
    R["step5b_cluster"] = {
        "cooling": ce,
        "g_compress_counts": {k: v for k, v in g_live.items() if v},
        "no_endpoint_lines": sum(
            lg.read_text(encoding="utf-8", errors="replace").count("无可用端点")
            for lg in AGENT_WORK.glob("agent-*.log")),
    }

    # ── 步骤 6：容器 ──
    dstat = shell('DOCKER_HOST=unix:///run/user/1020/docker.sock docker ps '
                  '--format "{{.Names}}|{{.Status}}"')
    cl = [ln for ln in dstat.splitlines() if ln.strip()]
    R["step6_containers"] = {
        "total": len(cl), "healthy": sum(1 for ln in cl if "healthy" in ln),
        "unhealthy": [ln for ln in cl if "healthy" not in ln],
    }

    # ── 审计面 ──
    ap = psql("SELECT count(*) FILTER (WHERE status='approved'),"
              "count(*) FILTER (WHERE status='rejected'),"
              "count(*) FILTER (WHERE status='pending') "
              "FROM tensor_approval_requests")[0]
    lots = AGENT_WORK / "approver_escalated.json"
    pa = AGENT_WORK / "auto_approver.PASS_ALL"
    alog = AGENT_WORK / "auto_approver.log"
    alines = alog.read_text(encoding="utf-8", errors="replace").splitlines()
    pall = [ln for ln in alines if "PASS_ALL" in ln]
    gc = AGENT_WORK / "gc.log"
    fleet = AGENT_WORK / "fleet.log"
    R["audit"] = {
        "approval": {"approved": int(ap[0]), "rejected": int(ap[1]),
                     "pending": int(ap[2])},
        "parking_lot": (lots.read_text(encoding="utf-8").strip()
                        if lots.exists() else "missing"),
        "pass_all": {"exists": pa.exists(),
                     "mtime_local": (time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(pa.stat().st_mtime))
                                     if pa.exists() else None),
                     "rounds": len(pall), "last_line": pall[-1] if pall else None},
        "approver_log_last": alines[-1] if alines else None,
        "worktree_total": len([x for x in shell("git -C "
                                                f"{ECO}/TensorPlatform worktree list")
                               .splitlines() if x.strip()]),
        "worktree_tensorplatform": len([x for x in shell(
            f"git -C {ECO}/TensorPlatform worktree list").splitlines()
            if "/TensorPlatform" in x]),
        "main_dirty": len([x for x in shell(f"git -C {ECO}/TensorPlatform status "
                                            "--porcelain").splitlines() if x.strip()]),
        "main_branch": shell(f"git -C {ECO}/TensorPlatform rev-parse --abbrev-ref HEAD"),
        "main_head": shell(f"git -C {ECO}/TensorPlatform rev-parse --short HEAD"),
        "gc_log_last": (gc.read_text(encoding="utf-8", errors="replace")
                        .strip().splitlines() or [""])[-1],
        "fleet_log_last": (fleet.read_text(encoding="utf-8", errors="replace")
                           .strip().splitlines() or [""])[-1],
        "worktree_dirty_before_delivery": len(
            [x for x in shell("git status --porcelain").splitlines() if x.strip()]),
        "worktree_branch": shell("git rev-parse --abbrev-ref HEAD"),
        "worktree_head": shell("git rev-parse --short HEAD"),
    }
    print(json.dumps(R, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())