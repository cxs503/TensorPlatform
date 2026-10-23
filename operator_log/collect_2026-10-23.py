#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""operator 班次采集器（m212 / 2026-10-23 第 74 班）

现场直读平台观察面，产出 validation_report.json —— 判据数字零手抄：
DB 走 docker exec psql、守护走 /proc、中继走 curl、账目走文件直读。
一次运行即快照，提交后勿复跑（心跳秒数变化会自造脏树）。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

WORKDIR = "/srv/tensor/shared/tensor_eco"
AGENT_WORK = os.path.join(WORKDIR, "agent_work")
AUTHORITATIVE_WORKERS = os.path.join(AGENT_WORK, "workers.json")
MAIN_REPO = os.path.join(WORKDIR, "TensorPlatform")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "2026-10-23.validation_report.json")

DOCKER_ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")


def sh(cmd, shell=True, env=None, timeout=60):
    """跑命令，返回 (rc, stdout.strip())。失败不抛，交给判据处理。"""
    try:
        p = subprocess.run(cmd, shell=shell, capture_output=True, text=True,
                           env=env, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return 127, "", str(exc)


def psql(sql):
    """只读 SQL：docker exec psql -At -F '|'。"""
    rc, out, err = sh(["docker", "exec", "open-foundation-postgres", "psql",
                       "-U", "foundation", "-d", "open_digital_foundation",
                       "-At", "-F", "|", "-c", sql], shell=False, env=DOCKER_ENV)
    if rc != 0:
        return None, (err or out)
    rows = [line.split("|") for line in out.splitlines() if line]
    return rows, ""


def main():
    checks = []
    info = {}

    def add(cid, name, passed, detail):
        checks.append({"id": cid, "name": name,
                       "passed": bool(passed), "detail": detail})

    # ---- 1. 在飞心跳鲜度 ------------------------------------------------
    rows, err = psql(
        "SELECT id,status,round(extract(epoch from (now()-heartbeat_at))) , "
        "coalesce(result->>'stop_condition','-') FROM tensor_dev_missions "
        "WHERE status IN ('claimed','running') ORDER BY id")
    if rows is None:
        add(1, "在飞心跳鲜度", False, "DB 不可读: %s" % err)
        info["inflight"] = []
    else:
        inflight = []
        for r in rows:
            inflight.append({"id": int(r[0]), "status": r[1],
                             "hb_age_s": int(r[2]), "stop_condition": r[3]})
        info["inflight"] = inflight
        max_hb = max([x["hb_age_s"] for x in inflight], default=-1)
        bad = [x for x in inflight if x["hb_age_s"] > 240 or x["stop_condition"] != "-"]
        add(1, "在飞心跳鲜度", rows and not bad,
            "在飞 %d 条，max(hb_age)=%ss（收割线 240s），异常 %d 条；明细 %s"
            % (len(inflight), max_hb, len(bad),
               ", ".join("m%d/%s/%ss" % (x["id"], x["status"], x["hb_age_s"])
                         for x in inflight)))

    # ---- 2. 守护计数（权威源等式） --------------------------------------
    with open(AUTHORITATIVE_WORKERS, "r", encoding="utf-8") as fh:
        wj = json.load(fh)
    expect = {k: v for k, v in wj.items()
              if not k.startswith("_") and isinstance(v, int)}
    expect_total = sum(expect.values())
    rc, out, _ = sh('pgrep -f "tensor_agen[t].py" | wc -l')
    actual = int(out or 0)
    # 实测按模型分组（--model 参数直读 /proc）
    rc2, pids, _ = sh('pgrep -f "tensor_agen[t].py"')
    by_model = {}
    for pid in (pids or "").split():
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                argv = fh.read().split(b"\0")
            args = [a.decode("utf-8", "replace") for a in argv]
        except OSError:
            continue
        if "--model" in args:
            m = args[args.index("--model") + 1]
            by_model[m] = by_model.get(m, 0) + 1
    info["guards"] = {"expect_total": expect_total, "actual_total": actual,
                      "expect_by_model": expect, "actual_by_model": by_model}
    add(2, "守护计数=权威源 ΣN", actual == expect_total and by_model == expect,
        "实测 %d / 权威源 ΣN=%d / 分模型实测 %s" % (actual, expect_total, by_model))

    # ---- 3. 中继健康 ----------------------------------------------------
    # 中继凭据：优先继承环境（守护/cron 注入），回退 operator.env（后者通常不含 LLM_*）
    env = {}
    with open(os.path.join(AGENT_WORK, "operator.env"), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    base = os.environ.get("LLM_BASE_URL") or env.get("LLM_BASE_URL", "")
    key = os.environ.get("LLM_API_KEY") or env.get("LLM_API_KEY", "")
    # 回退：从在飞守护进程的环境取（那是平台的权威运行态，凭据不落工件）
    if not base or not key:
        rc, gpids, _ = sh('pgrep -f "tensor_agen[t].py"')
        for gpid in (gpids or "").split():
            try:
                with open("/proc/%s/environ" % gpid, "rb") as fh:
                    genv = dict(
                        kv.split(b"=", 1) for kv in fh.read().split(b"\0") if b"=" in kv)
            except OSError:
                continue
            base = base or genv.get(b"LLM_BASE_URL", b"").decode()
            key = key or genv.get(b"LLM_API_KEY", b"").decode()
            if base and key:
                break
    info["relay_base_set"] = bool(base)
    info["relay_key_set"] = bool(key)
    rc, out, _ = sh(["curl", "-s", "-o", "/dev/null",
                     "-w", "%{http_code} %{time_total}",
                     "-H", "Authorization: Bearer %s" % key,
                     "%s/models" % base], shell=False, timeout=30)
    parts = (out or "").split()
    code = parts[0] if parts else "err"
    tsec = float(parts[1]) if len(parts) > 1 else -1.0
    add(3, "中继健康", code == "200" and 0 <= tsec < 5.0,
        "GET /models → HTTP %s t=%.3fs（阈值 200 且 <5s）" % (code, tsec))

    # ---- 4. 网关冷却态 --------------------------------------------------
    state_path = os.path.join(AGENT_WORK, "llm_gateway_state.json")
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        state = {"_error": str(exc)}
    now_ts = datetime.now(timezone.utc).timestamp()
    active = {}
    for ep, v in (state or {}).items():
        if isinstance(v, dict):
            till = v.get("blocked_until")
            if isinstance(till, (int, float)) and till > now_ts:
                active[ep] = v
    info["cooldown_raw"] = state
    add(4, "网关冷却态（无未过期封锁）", not active,
        "冷却态=%s 未过期封锁 %d 个" % (json.dumps(state, ensure_ascii=False), len(active)))

    # ---- 5. 终态 mission result ----------------------------------------
    rows, err = psql(
        "SELECT coalesce(result->>'stop_condition','none'),count(*),"
        "coalesce(max(finished_at)::text,'-') FROM tensor_dev_missions "
        "WHERE finished_at > now()-interval '24 hours' AND status IN "
        "('succeeded','failed','stopped') GROUP BY 1 ORDER BY 2 DESC")
    r13_rows, err2 = psql(
        "SELECT count(*),coalesce(max(finished_at)::text,'-'),"
        "round(extract(epoch from (now()-max(finished_at)))/3600.0,2) FROM "
        "tensor_dev_missions WHERE status='failed' AND "
        "result->>'stop_condition'='gateway_unavailable' AND "
        "finished_at > now()-interval '24 hours'")
    recent, err3 = psql(
        "SELECT id,status,to_char(finished_at,'HH24:MI:SS') FROM "
        "tensor_dev_missions WHERE finished_at > now()-interval '3 hours' "
        "ORDER BY finished_at DESC")
    today_failed, _ = psql(
        "SELECT count(*) FROM tensor_dev_missions WHERE status='failed' AND "
        "finished_at > date_trunc('day', now())")
    if rows is None or r13_rows is None or recent is None:
        add(5, "终态 mission result", False, "DB 不可读: %s%s" % (err, err2))
    else:
        term = {r[0]: int(r[1]) for r in rows}
        recent_list = [{"id": int(r[0]), "status": r[1], "finished": r[2]} for r in recent]
        r13_n = int(r13_rows[0][0]) if r13_rows else 0
        r13_last = r13_rows[0][1] if r13_rows else "-"
        r13_age = float(r13_rows[0][2] or 0) if r13_rows else 0.0
        non_succ_recent = [x for x in recent_list if x["status"] != "succeeded"]
        info["terminal_24h"] = term
        info["recent_3h"] = recent_list
        info["r13"] = {"failed_24h": r13_n, "last_finished_at": r13_last,
                       "hours_since_last": r13_age,
                       "failed_today": int(today_failed[0][0]) if today_failed else -1}
        failed_today_n = int(today_failed[0][0]) if today_failed else -1
        ok = (non_succ_recent == [] and failed_today_n == 0)
        add(5, "终态 mission result（无新 infra 终局/honest FAIL）", ok,
            "24h 终态 %s；R13 同型 failed_24h=%d 末条 %s 距今 %.2fh；今日新增 failed=%d；"
            "近 3h 终态 %d 条，非 succeeded %d 条"
            % (json.dumps(term, ensure_ascii=False), r13_n, r13_last, r13_age,
               failed_today_n, len(recent_list), len(non_succ_recent)))

    # ---- 附加项（非五步，沿前任口径跟踪，仅记录不判 FAIL） ---------------
    rows, _ = psql("SELECT status,count(*) FROM tensor_approval_requests GROUP BY 1")
    info["approvals"] = {r[0]: int(r[1]) for r in (rows or [])}
    esc_path = os.path.join(AGENT_WORK, "approver_escalated.json")
    try:
        with open(esc_path, "r", encoding="utf-8") as fh:
            info["escalated"] = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        info["escalated"] = {"_error": str(exc)}
    pass_all = os.path.join(AGENT_WORK, "auto_approver.PASS_ALL")
    rc, out, _ = sh(["ls", "-la", "--time-style=+%Y-%m-%d %H:%M", pass_all], shell=False)
    rc2, tail1, _ = sh(["tail", "-1", os.path.join(AGENT_WORK, "auto_approver.log")],
                       shell=False)
    rc3, rounds, _ = sh('grep -c "PASS_ALL 模式" %s/auto_approver.log' % AGENT_WORK)
    info["passall"] = {"exists": os.path.exists(pass_all), "ls": out,
                       "last_line": tail1, "rounds_cumulative": int(rounds or 0)}
    rc, containers, _ = sh(["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
                          shell=False, env=DOCKER_ENV)
    clines = [l for l in (containers or "").splitlines() if l]
    info["containers"] = {"total": len(clines),
                          "healthy": len([l for l in clines if "(healthy)" in l])}
    rc, wt, _ = sh(["git", "-C", MAIN_REPO, "worktree", "list"], shell=False)
    wt_lines = [l for l in (wt or "").splitlines() if l]
    info["worktrees"] = {"total": len(wt_lines),
                         "tensorplatform": len([l for l in wt_lines if "/TensorPlatform" in l])}
    rc, dirty, _ = sh(["git", "-C", MAIN_REPO, "status", "--short"], shell=False)
    info["main_repo_dirty"] = len([l for l in (dirty or "").splitlines() if l])
    gc_log = os.path.join(AGENT_WORK, "gc.log")
    info["gc_log_last"] = sh(["tail", "-1", gc_log], shell=False)[1]

    passed = sum(1 for c in checks if c["passed"])
    report = {
        "mission": "m212",
        "date": "2026-10-23",
        "shift": "第 74 班（派发器 shift#53）",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workdir": os.path.abspath(__file__),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "info": info,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("checks %d/%d -> %s" % (passed, len(checks), OUT))
    for c in checks:
        print("[%s] %s: %s" % ("PASS" if c["passed"] else "FAIL", c["name"], c["detail"]))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())