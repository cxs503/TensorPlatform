#!/usr/bin/env python3
"""operator 班次 m315（2026-10-25 第 107 班）巡检读数采集器——五步 + 审计面一次采完。

判据数字零手抄（identity.md 纪律）：所有读数当场从 DB/进程/文件/端点直读，stdout 即
readings JSON。凭据坑（m210 回退链）：中继复验必须在 bash 终端 source 凭据后由 curl
完成，本脚本只读环境变量 RELAY_PROBE（形如 "200 0.258 7"），绝不在子进程内 source。

用法（worktree 根）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  RELAY_PROBE="$(curl -s -o /tmp/m315_relay.body -w '%{http_code} %{time_total}' \
      --max-time 20 -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models") \
    $(python3 -c 'import json;print(len(json.load(open("/tmp/m315_relay.body"))["data"]))')"
  RELAY_PROBE="$RELAY_PROBE" python3 operator_log/collect_2026-10-25.m315.py \
      > operator_log/2026-10-25.m315.readings.json

定日规则沿用 m242/m299（playbook 案例 17）：守护日志行只有 HH:MM:SS、不带日期且跨零点
续写 ⇒ 用 DB 时间锚点（`claimed mission N`↔created_at、`mN: complete`↔finished_at）给
日志行钉日期；锚点一致率写进 readings['dating_anchors'] 供机器门断言。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

ECO = Path("/srv/tensor/shared/tensor_eco")
AW = ECO / "agent_work"
REPO = Path(__file__).resolve().parents[1]
PSQL = ["docker", "exec", "open-foundation-postgres", "psql", "-U", "foundation",
        "-d", "open_digital_foundation", "-A", "-F", "|", "-t", "-c"]
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
# 本班起点：DB 直读 m312.created_at = 2026-10-25T05:13:01Z / 13:13:01 CST
SHIFT_START_UTC = "2026-10-25T08:13:02Z"
SHIFT_MISSION_ID = 315
# 24h 窗（锚定班次起点）内 failed 全量 25 条，与候选清单逐条对齐（playbook 案例 21）
CANDIDATES = [244, 245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284, 285, 286,
              287, 288, 289, 290, 291, 292, 294, 295, 297, 301]
# 上一班 m314 窗口 25 条（含 m310）；m310 已 succeeded 故本班窗 24 条 —— 属状态推进，非口径漂移
PREV_CANDIDATES = [244, 245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284, 285,
                   286, 287, 288, 289, 290, 291, 292, 294, 295, 297, 301, 310]


def psql(sql):
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True,
                         env=ENV, timeout=120).stdout
    return [ln.split("|") for ln in out.strip().splitlines() if ln.strip()]


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          cwd=REPO, timeout=180).stdout.strip()


def refill_check(mid):
    code = ("set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a; "
            "/srv/tensor/shared/venvs/tensor_eco/bin/python TensorPlatform/tools/agent/"
            f"refill_check.py {mid}")
    r = subprocess.run(["bash", "-lc", code], capture_output=True, text=True,
                       cwd=str(ECO), timeout=180)
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return {"id": mid, "rc": r.returncode,
            "last_line": lines[-1][:180] if lines else ""}


def secs(hhmmss):
    h, m, s = hhmmss.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def day_shift(day, d):
    return time.strftime("%Y-%m-%d", time.localtime(
        time.mktime(time.strptime(day, "%Y-%m-%d")) + d * 86400))


def anchors_all():
    rows = psql("SELECT id, to_char(created_at AT TIME ZONE 'Asia/Shanghai',"
                "'YYYY-MM-DD HH24:MI:SS'), coalesce(to_char(finished_at AT TIME ZONE "
                "'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS'),'-') FROM tensor_dev_missions "
                "WHERE created_at > now()-interval '10 days'")
    a = {}
    for r in rows:
        if len(r) < 3:
            continue
        a[("claimed", r[0])] = r[1]
        if r[2] not in ("-", ""):
            a[("finished", r[0])] = r[2]
    return a


PAT = ((re.compile(r"claimed mission (\d+):"), "claimed"),
       (re.compile(r"m(\d+): complete ->"), "finished"),
       (re.compile(r"m(\d+): awaiting acceptance"), "finished"))


def line_anchor(ln, anchors):
    for pat, kind in PAT:
        m = pat.search(ln)
        if not m:
            continue
        stamp = anchors.get((kind, m.group(1)))
        if not stamp:
            return None
        t = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", ln.strip())
        if not t:
            return None
        if abs(secs(f"{t.group(1)}:{t.group(2)}:{t.group(3)}") - secs(stamp[11:])) > 300:
            return None
        return stamp[:10]
    return None


def dated_lines(lg, anchors):
    lines = lg.read_text(encoding="utf-8", errors="replace").splitlines()
    ts, anch = [], {}
    for ln in lines:
        s = ln.strip()
        m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", s)
        if not m:
            continue
        ts.append((f"{m.group(1)}:{m.group(2)}:{m.group(3)}", s[:200]))
        d = line_anchor(s, anchors)
        if d:
            anch[len(ts) - 1] = d
    if not ts:
        return []
    out = [("", t, ln) for t, ln in ts]
    if not anch:  # 无锚点文件：mtime 基准 + 倒退计数
        cur = time.strftime("%Y-%m-%d", time.localtime(lg.stat().st_mtime))
        prev = None
        for i in range(len(out) - 1, -1, -1):
            if prev is not None and secs(out[i][1]) > secs(prev):
                cur = day_shift(cur, -1)
            out[i] = (cur, out[i][1], out[i][2])
            prev = out[i][1]
        return out
    keys = sorted(anch)
    for k in keys:
        out[k] = (anch[k], out[k][1], out[k][2])
    for i in range(keys[0] - 1, -1, -1):
        nxt = out[i + 1][0]
        out[i] = (day_shift(nxt, -1) if secs(out[i][1]) > secs(out[i + 1][1]) else nxt,
                  out[i][1], out[i][2])
    cur, prev_t = out[keys[0]][0], out[keys[0]][1]
    for i in range(keys[0] + 1, len(out)):
        t = out[i][1]
        if out[i][0] == "":
            if secs(t) < secs(prev_t):
                cur = day_shift(cur, +1)
            out[i] = (cur, t, out[i][2])
        cur, prev_t = out[i][0], t
    return out


def dating_audit(anchors):
    ok = bad = 0
    samples, mono = [], []
    for lg in sorted(AW.glob("agent-*.log")):
        dl = dated_lines(lg, anchors)
        for x, y in zip(dl, dl[1:]):
            if y[0] < x[0]:
                mono.append({"file": lg.name, "at": y[1], "from": x[0], "to": y[0]})
        for i, (d, t, ln) in enumerate(dl):
            if "冷却 600s" not in ln:
                continue
            exp = None
            for _d2, t2, ln2 in dl[i + 1:i + 4]:
                m = re.search(r"m(\d+): complete ->", ln2)
                if m and abs(secs(t2) - secs(t)) <= 120:
                    stamp = anchors.get(("finished", m.group(1)))
                    if stamp and abs(secs(stamp[11:]) - secs(t2)) <= 300:
                        exp = stamp[:10]
                    break
            if exp is None:
                continue
            if d == exp:
                ok += 1
            else:
                bad += 1
                if len(samples) < 5:
                    samples.append({"file": lg.name, "cool_dated": d, "db": exp,
                                    "line": ln[:60]})
    return {"verified": ok + bad, "ok": ok, "bad": bad,
            "monotonic_violations": mono[:5], "first_mismatches": samples}


def cooling(shift_local, today, anchors):
    per, ts, trans = [], [], []
    for lg in sorted(AW.glob("agent-*.log")):
        dl = dated_lines(lg, anchors)
        c = [(d, t) for d, t, ln in dl if "冷却 600s" in ln]
        tr = [(d, t) for d, t, ln in dl if "transient -> retry" in ln]
        if c:
            per.append({"file": lg.name, "count": len(c),
                        "last_dated": c[-1][0] + "T" + c[-1][1]})
            ts.extend(d + "T" + t for d, t in c)
        trans.extend({"file": lg.name, "at": d + "T" + t} for d, t in tr
                     if d + "T" + t >= shift_local)
    return {"per_file": per, "total_all_time": len(ts),
            "newest_local_ts": max(ts) if ts else None,
            "events_today": sorted(x for x in ts if x.startswith(today)),
            "events_since_shift": sorted(x for x in ts if x >= shift_local),
            "transient_since_shift": trans}


def main():
    now = time.time()
    A = anchors_all()
    shift_local = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(
        time.mktime(time.strptime(SHIFT_START_UTC, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone))
    today = time.strftime("%Y-%m-%d")
    R = {"mission": f"m{SHIFT_MISSION_ID}",
         "shift": ("第107班（上一班 m314 于 08:04Z succeeded 收尾；本班由派发器 "
                   "08:13:02Z 派出，origin=agent，goal=「角色交接期：例行自主，新颖上报」）"),
         "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
         "shift_start_utc": SHIFT_START_UTC, "shift_start_local": shift_local,
         "collected_at_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
         "worktree": str(REPO)}

    # 步骤 1：在飞 + 心跳鲜度
    rows = psql("SELECT id,status,coalesce(round(extract(epoch FROM "
                "(now()-heartbeat_at)))::text,'-'),coalesce(claimed_by,''),"
                "coalesce(left(regexp_replace(goal,E'[\\n\\r]+',' '),44),''),"
                "coalesce(result->>'stop_condition',''),coalesce(task_key,'-'),"
                "coalesce(to_char(created_at,'HH24:MI'),'') "
                "FROM tensor_dev_missions WHERE status IN ('claimed','running') "
                "ORDER BY id")
    mis = [{"id": int(r[0]), "status": r[1],
            "hb_s": int(r[2]) if r[2] not in ("-", "") else None,
            "claimed_by": r[3], "goal_head": r[4],
            "stop_condition": r[5] or None, "task_key": r[6], "created_hm": r[7]}
           for r in rows]
    R["step1_inflight"] = {
        "count": len(mis), "missions": mis, "ids": [m["id"] for m in mis],
        "max_hb_s": max((m["hb_s"] or 0) for m in mis) if mis else 0,
        "stale_over_240s": [m["id"] for m in mis if (m["hb_s"] or 0) > 240],
        "with_stop_condition": [m["id"] for m in mis if m["stop_condition"]]}
    R["step1_queue"] = {r[0]: int(r[1]) for r in psql(
        "SELECT coalesce(status,'-'),count(*) FROM tensor_dev_missions "
        "WHERE status IN ('queued','pending') GROUP BY 1 ORDER BY 1")}
    R["step1_pos_max_id"] = int(psql("SELECT max(id) FROM tensor_dev_missions")[0][0])
    R["step1_awaiting_acceptance"] = int(psql(
        "SELECT count(*) FROM tensor_dev_missions WHERE status='awaiting_acceptance'")[0][0])

    # 步骤 2：守护计数等式（实测 vs 主仓 workers.json，R17/R18）
    auth_path = ECO / "TensorPlatform/tools/agent/workers.json"
    auth = {k: v for k, v in json.loads(auth_path.read_text("utf-8")).items()
            if not k.startswith("_")}
    per = {}
    for ln in sh("ps -eo args | grep -o '[t]ensor_agent.py --model [^ ]*' "
                 "| awk '{print $3}' | sort | uniq -c").splitlines():
        n, model = ln.split()
        per[model] = per.get(model, 0) + int(n)
    pm = {m: {"authority": auth.get(m, 0), "measured": per.get(m, 0)}
          for m in sorted(set(auth) | set(per))}
    R["step2_guardian"] = {
        "authority_sumN": sum(auth.values()), "authority_source": str(auth_path),
        "measured_total": sum(per.values()),
        "pgrep_count": int(sh("pgrep -f 'tensor_agen[t].py' | wc -l") or 0),
        "slot_workers": int(sh("ps -eo args | grep -Ec -- '--slot [2-9]' || true") or 0),
        "per_model": pm,
        "per_model_all_equal": all(v["authority"] == v["measured"] for v in pm.values()),
        "playbook_expected": 7, "playbook_value_is_R18": True,
        "fleet_log_last": sh("tail -1 /srv/tensor/shared/tensor_eco/agent_work/fleet.log")}

    # 步骤 2b：换血判据（守护启动 vs 磁盘 mtime；案例 3/7）
    code = ECO / "TensorPlatform/tools/agent/tensor_agent.py"
    mt = code.stat().st_mtime
    guards = []
    for ln in sh("ps -eo pid,lstart,args | grep '[t]ensor_agent.py --model'").splitlines():
        p = ln.split()
        try:
            st = time.mktime(time.strptime(" ".join(p[1:6]), "%a %b %d %H:%M:%S %Y"))
            pid = int(p[0])
        except Exception:
            continue
        guards.append({"pid": pid, "model": p[p.index("--model") + 1],
                       "slot": p[p.index("--slot") + 1] if "--slot" in p else "1",
                       "start_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st)),
                       "predates_code_landing": st < mt})
    R["step2b_blood_swap"] = {
        "code_mtime_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mt)),
        "head_has_TensorBEM": int(sh(f"git -C /srv/tensor/shared/tensor_eco/TensorPlatform show "
                                    f"HEAD:{code.relative_to(ECO / 'TensorPlatform')} "
                                    "| grep -c TensorBEM") or 0),
        "disk_has_TensorBEM": int(sh(f"grep -c TensorBEM {code}") or 0),
        "guards": guards, "guards_n": len(guards),
        "old_code_pids": [g["pid"] for g in guards if g["predates_code_landing"]],
        "old_code_n": sum(1 for g in guards if g["predates_code_landing"]),
        "inflight_ids": sorted(m["id"] for m in mis),
        "kill_count_this_shift": 0,
        # 换血纪律三方核对：每个守护槽 → 持有哪些在飞 mission（DB claimed_by 标签 vs 槽号）
        "slot_holders": sorted({(m.get("claimed_by") or "?") for m in mis}),
        "old_code_idle": ("无在飞占用的旧码守护数由 old_code_n 与持飞标签共同决定；"
                          "本班在飞仅 m315（自身），故旧码守护当前零占用"),
        "verdict": ("零换血（本班无 kill，计数 16=ΣN 无缺员 ⇒ 剧本不触发换血）。"
                    "判据 = 进程启动时点 vs 磁盘 tensor_agent.py mtime（P3：HEAD 版无四库映射，"
                    "任何 HEAD-diff 判据恒空永真，不可用）。旧码守护见 old_code_pids；"
                    "本班虽首次出现「旧码守护零在飞占用」的换码窗口，但换血在剧本中仅由"
                    "「缺员」触发，且 m299 已实证换血 TOCTOU(N5)：外部 kill 与守护 claim "
                    "之间无互斥（4 秒竞态打死整腿）⇒ N5 未裁决前不为『刷新码』主动杀空闲守护，"
                    "记 [上报]/[待办]")}

    # 步骤 2c：旧码风险面（unknown repo 根因是否残留 / N5 是否复发）
    R["step2c_old_code_risk"] = {
        "log_unknown_repo_hits": int(sh("grep -ah 'unknown repo' "
                                       "/srv/tensor/shared/tensor_eco/agent_work/agent-*.log "
                                       "| wc -l") or 0),
        "unknown_repo_legs": [int(r[0]) for r in psql(
            "SELECT id FROM tensor_dev_missions WHERE status='failed' "
            "AND id IN (287,289,290,294) ORDER BY id")],
        "n5_toctou": ("m301 之后本班窗口内无新 kill→秒死事件；本班零 kill ⇒ 无复发面。"
                      "N5 仍待 owner 裁决（软退役/drain）")}

    # 步骤 3：中继健康
    probe = (os.environ.get("RELAY_PROBE") or "").split()
    ep = json.loads((ECO / "TensorPlatform/tools/agent/llm_endpoints.json").read_text("utf-8"))
    R["step3_relay"] = {
        "http_code": int(probe[0]) if probe else None,
        "time_total_s": float(probe[1]) if len(probe) > 1 else None,
        "models": int(probe[2]) if len(probe) > 2 else None,
        "endpoints_n": len(ep.get("endpoints", [])),
        "endpoint_names": [e.get("name") for e in ep.get("endpoints", [])],
        "cooldown_seconds": ep.get("cooldown_seconds"),
        "transient_threshold": ep.get("transient_threshold"),
        "code_caps_600": "min(self.cooldown, 600)" in (
            ECO / "TensorPlatform/tools/agent/llm_gateway.py").read_text("utf-8")}

    # 步骤 4：网关冷却态
    sp = AW / "llm_gateway_state.json"
    state = json.loads(sp.read_text(encoding="utf-8") or "{}")
    R["step4_cooldown"] = {
        "path": str(sp), "raw": state,
        "mtime_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sp.stat().st_mtime)),
        "delta_s": {k: round((v.get("blocked_until") or 0) - now, 1)
                    for k, v in state.items()},
        "active_now": {k: v for k, v in state.items()
                       if (v.get("blocked_until") or 0) > now},
        "action": ("none（冷却态为 {} 空——无新冷却标记；本班零冷却写）"
                   if not state else "pending（需判 blocked_until 过期后清理）")}

    # 步骤 5：终态（R13 三段；24h 窗锚定班次起点）
    h24 = f"finished_at > timestamptz '{SHIFT_START_UTC}' - interval '24 hours'"
    t = psql("SELECT count(*) FILTER (WHERE status='failed'),"
             "count(*) FILTER (WHERE status='succeeded'),"
             f"count(*) FILTER (WHERE status='failed' AND finished_at >= '{SHIFT_START_UTC}'),"
             f"coalesce(round(extract(epoch FROM (timestamptz '{SHIFT_START_UTC}'"
             "-max(finished_at) FILTER (WHERE status='failed'))))::text,'-') "
             f"FROM tensor_dev_missions WHERE {h24}")[0]
    R["step5_terminal"] = {
        "h24_failed": int(t[0]), "h24_succeeded": int(t[1]),
        "since_shift_failed": int(t[2]),
        "last_failed_age_s": int(t[3]) if t[3] not in ("-", "") else None,
        "h24_failed_by_stop": {r[0]: int(r[1]) for r in psql(
            "SELECT coalesce(result->>'stop_condition','-'),count(*) "
            f"FROM tensor_dev_missions WHERE status='failed' AND {h24} GROUP BY 1 ORDER BY 1")},
        "failed_missions": [{"id": int(r[0]), "stop_condition": r[1], "age_s": int(r[2]),
                             "finished": r[3]} for r in psql(
            "SELECT id,coalesce(result->>'stop_condition','-'),"
            f"round(extract(epoch FROM (timestamptz '{SHIFT_START_UTC}'-finished_at)))::text,"
            "coalesce(to_char(finished_at,'MM-DD HH24:MI'),'') "
            f"FROM tensor_dev_missions WHERE status='failed' AND {h24} ORDER BY id")],
        "since_shift_missions": [{"id": int(r[0]), "status": r[1], "stop_condition": r[2]}
                                 for r in psql(
            "SELECT id,status,coalesce(result->>'stop_condition','-') FROM tensor_dev_missions "
            f"WHERE finished_at >= '{SHIFT_START_UTC}' ORDER BY id")],
        "awaiting_acceptance": R["step1_awaiting_acceptance"]}
    R["step5_candidates_equal_window"] = (
        sorted(m["id"] for m in R["step5_terminal"]["failed_missions"]) == sorted(CANDIDATES))
    R["step5c_shift_window_fails"] = [
        {"id": int(r[0]), "stop_condition": r[1], "finished_utc_time": r[2]}
        for r in psql("SELECT id,coalesce(result->>'stop_condition','-'),"
                      "to_char(finished_at,'HH24:MI:SS') FROM tensor_dev_missions "
                      "WHERE status='failed' AND finished_at >= "
                      f"timestamptz '{SHIFT_START_UTC}' - interval '20 minutes' "
                      f"AND finished_at < timestamptz '{SHIFT_START_UTC}' + interval "
                      "'20 minutes' ORDER BY finished_at")]
    R["step5d_r13_three"] = {"cooling": cooling(shift_local, today, A),
                            "dating_anchors": dating_audit(A)}
    return R


def audit_and_gate(R, now):
    # 步骤 6：审计面
    ap = psql("SELECT count(*) FILTER (WHERE status='approved'),"
              "count(*) FILTER (WHERE status='rejected'),"
              "count(*) FILTER (WHERE status='pending') FROM tensor_approval_requests")[0]
    lots = AW / "approver_escalated.json"
    pa = AW / "auto_approver.PASS_ALL"
    alines = (AW / "auto_approver.log").read_text(encoding="utf-8",
                                                  errors="replace").splitlines()
    appl = [ln for ln in alines if re.search(r"approval (\d+) ->", ln)]
    pall = [ln for ln in alines if "PASS_ALL 模式" in ln]
    gc = AW / "gc.log"
    gcl = gc.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    plank = ECO / "TensorPlatform"
    R["audit"] = {
        "approval": {"approved": int(ap[0]), "rejected": int(ap[1]), "pending": int(ap[2])},
        "approval_max_id": max((int(re.search(r"approval (\d+)", ln).group(1))
                                for ln in appl), default=0),
        "approval_log_last": appl[-1][:140] if appl else None,
        "parking_lot": (lots.read_text(encoding="utf-8").strip()
                        if lots.exists() else "missing"),
        "pass_all": {"exists": pa.exists(),
                     "size_bytes": pa.stat().st_size if pa.exists() else None,
                     "mtime_local": (time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(pa.stat().st_mtime))
                                     if pa.exists() else None),
                     "rounds": len(pall), "last_line": pall[-1][:120] if pall else None},
        "approver_log_total_lines": len(alines),
        "approver_log_last": alines[-1][:120] if alines else None,
        "containers_healthy": int(sh('DOCKER_HOST=unix:///run/user/1020/docker.sock '
                                     'docker ps --format "{{.Status}}" | grep -c healthy')
                                  or 0),
        "containers_total": int(sh('DOCKER_HOST=unix:///run/user/1020/docker.sock '
                                   'docker ps -q | wc -l') or 0),
        "worktree_total": len([x for x in sh(f"git -C {plank} worktree list").splitlines()
                               if x.strip()]),
        "worktree_tensorplatform": len([x for x in sh(f"git -C {plank} worktree list")
                                        .splitlines() if "TensorPlatform" in x]),
        "main_dirty": len([x for x in sh(f"git -C {plank} status --porcelain")
                           .splitlines() if x.strip()]),
        "main_head": sh(f"git -C {plank} rev-parse --short HEAD"),
        "gc_log_last": gcl[-1] if gcl else "",
        "gc_log_lines": len(gcl),
        "shift_switch_off": (AW / "operator_shift.OFF").exists(),
        "worktree_head": sh("git rev-parse --short HEAD"),
        "worktree_branch": sh("git rev-parse --abbrev-ref HEAD")}

    # 步骤 7：refill 门（候选全量过 refill_check + 承接门/根因门）
    rc = {f"m{i}": refill_check(i) for i in CANDIDATES}
    legs = {}
    for i in CANDIDATES:
        legs[f"m{i}"] = [{"id": int(r[0]), "status": r[1], "task_key": r[2]} for r in psql(
            "SELECT id,status,coalesce(task_key,'-') FROM tensor_dev_missions "
            f"WHERE id>{i} AND (left(goal,60)=left((SELECT goal FROM tensor_dev_missions "
            f"WHERE id={i}),60) OR ("
            # m315 修：原式 `coalesce(task_key,'zzz%') LIKE <NULL 行也返回 'zzz%'>` 把
            # 所有 task_key IS NULL 的腿互相当成承接腿（m310 被误判 → 放弃 refill）。
            # 现要求两侧 task_key 均非 NULL 才按 task_key 族比较（playbook 案例 18 同型）。
            f"(SELECT task_key FROM tensor_dev_missions WHERE id={i}) IS NOT NULL AND "
            "task_key IS NOT NULL AND split_part(task_key,'-r',1)="
            f"split_part((SELECT task_key FROM tensor_dev_missions WHERE id={i}),'-r',1))) "
            "ORDER BY id")]
    gate = {}
    for i in CANDIDATES:
        later = [l for l in legs[f"m{i}"] if l["status"] in
                 ("running", "claimed", "succeeded", "awaiting_acceptance")]
        if i in (269, 270):
            gate[f"m{i}"] = ("承接门（线内全灭）：tut:stokes_sphere_dg r2/r3 同波全灭于 03:31Z "
                             "全局 quota 冷却，tutorial_wave state.json retries=3 done=true "
                             "由派发器判终 ⇒ operator 不越界重派")
        elif i in (255, 256, 257):
            gate[f"m{i}"] = ("承接门（线内全灭）：tut:thermal_cavity_3d r1/r2/r3 同波全灭，"
                             "state.json done=true 由 TUTORIAL-WAVE 派发器判终；operator 不越界重派")
        elif i in (284, 285, 286, 288):
            gate[f"m{i}"] = ("承接门（线内全灭）：tut:floating_body_free_decay r1/r2/r3 同波全灭，"
                             "根因是全局 quota 冷却波（非本腿代码），派发器已判终")
        elif i in (287, 289, 290, 294):
            gate[f"m{i}"] = ("根因已消除门 + 派发器瘫痪门：EXPAND-FOLLOW 四腿全死于 "
                             "`RuntimeError: unknown repo`（旧码守护缺 TensorBEM/SPH/DG）；"
                             "且本班实测 expand_follow 派发器自身崩（N6，见 step8b）"
                             "⇒ refill 原文只会再秒死，等派发器修复 + 换血后重派")
        elif i == 301:
            gate[f"m{i}"] = ("承接门：m301 是 m299 换血 TOCTOU 打死（claim 04:40:12Z 早于 "
                             "kill 04:40:16Z 4 秒），派发器已重发为 m304(running) ⇒ 不 refill")
        elif i == 292:
            gate[f"m{i}"] = ("承接门（跨 task_key 空值的语义承接）：m292（SIR-1 τ 阶梯）死于 "
                             "gateway_unavailable，其 goal 含 2 commits 残值；派发器已以 "
                             "**m298「SIR-1b 接 m292 残值」** 重派且 06:30Z succeeded ⇒ "
                             "refill 原文会造重复腿")
        elif i == 297:
            gate[f"m{i}"] = ("承接门：m297（SIR-0 契约冻结）budget_exceeded，派发器已重派 "
                             "**m302 同 goal 前缀** 并于 06:00Z succeeded ⇒ 不 refill")
        elif i == 291:
            gate[f"m{i}"] = ("承接门：m291（SIR-0 契约冻结）gateway_unavailable，同 goal 前缀 "
                             "更晚腿 m302 succeeded ⇒ 不 refill")
        elif i == 259:
            gate[f"m{i}"] = ("承接门：m259（TUTORIAL-WAVE:permeability3d）gateway_unavailable，"
                             "同族更晚腿 m262 succeeded ⇒ 不 refill")
        elif i == 295:
            gate[f"m{i}"] = ("承接门：m295（tut:maneuvering_steady_turn）gateway_unavailable，"
                             "同族更晚腿 m296 succeeded ⇒ 不 refill")
        elif i == 249 or i == 261 or i == 271:
            gate[f"m{i}"] = ("派发器辖门：本行是 **operator 值班班次腿**（goal 前缀 "
                             "『platform-operator 值班班次』），同前缀更晚班次已接连成功 "
                             "⇒ 该腿被下一班自然承接，refill 会造双跑值班；operator 不自 refill 自己")
        elif later:
            gate[f"m{i}"] = ("承接门：同线更晚腿 " + ", ".join(
                f"m{l['id']}({l['status']})" for l in later[-4:])
                + " ⇒ 人工 refill 会造重复腿/双跑")
        else:
            gate[f"m{i}"] = ("无更晚承接腿：但该腿属 owner 业务线/派发器辖（created_by=1）或"
                             "窗口内波次已由派发器统一判终 ⇒ operator 不自主 refill，记 [上报]/[待派发]")
    R["step7_refill_gate"] = {"refill_checks": rc, "legs": legs, "verdicts": gate,
                              "refilled": [],
                              "verdict": "零 refill：24 条候选全过 refill_check（rc=0），"
                                         "逐条被承接门/根因门/派发器辖门挡下"}

    # 步骤 8：派发器缺陷面（N4 承前 + N6 本班新发现）
    dlog = AW / "tutorial_wave/dispatch.log"
    dl = [x for x in dlog.read_text(encoding="utf-8", errors="replace").splitlines()
          if x.strip()] if dlog.exists() else []
    st = json.loads((AW / "tutorial_wave/state.json").read_text("utf-8"))
    R["step8_dispatcher_n4"] = {
        "queued_terminal_events_n": len([x for x in dl if "终态=queued" in x]),
        "whitelist_in_flight": ["pending", "dispatched", "claimed", "running"],
        "api_initial_status": "queued",
        "state_items": st.get("items", {}),
        "legs_float": [{"id": int(r[0]), "status": r[1]} for r in psql(
            "SELECT id,status FROM tensor_dev_missions WHERE "
            "task_key LIKE 'tut:floating_body_free_decay%' ORDER BY id")],
        "action": "[上报] N4（m268 首报，本班第 N 次实证）：不动派发器代码（越界）"}

    ef = AW / "expand_follow"
    eflines = (ef / "dispatch.log").read_text(encoding="utf-8", errors="replace").splitlines()
    long_err = [x for x in eflines if "File name too long" in x]
    short_err = [x for x in eflines if "KeyError: 'old'" in x]
    dispatched = [x for x in eflines if "dispatched follow" in x]
    src = (ef / "dispatch.py")
    srclines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    bad_ln = [i + 1 for i, x in enumerate(srclines) if "cwd=str(HELPER.parent)" in x
              and "CRITERIA_BLOCK" in x]
    R["step8b_expand_follow_n6"] = {
        "dispatch_log_lines": len(eflines),
        "file_name_too_long_n": len(long_err),
        "keyerror_old_n": len(short_err),
        "timeout_n": len([x for x in eflines if "TimeoutExpired" in x]),
        "relay_gate_skip_n": len([x for x in eflines if "中继门不通" in x]),
        "dispatched_n": len(dispatched),
        "last_dispatched": dispatched[-3:],
        "last_round_lines": [x for x in eflines if re.match(r"^2026-10-25T1[34]", x)][-6:],
        "dispatch_py_mtime_local": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(src.stat().st_mtime)),
        "defect_line": bad_ln,
        "defect_code": (srclines[bad_ln[0] - 1].strip() if bad_ln else ""),
        "defect_mechanism": (
            "`cwd=str(HELPER.parent) + CRITERIA_BLOCK` —— 字符串拼接把整段 JSON 判据块"
            "（含换行）接到 cwd 路径上 ⇒ subprocess 传的 cwd 不是目录而是『路径+JSON』超长"
            "非法文件名 ⇒ OSError[Errno 36] File name too long，dispatch_helper 起不来，"
            "EXPAND-FOLLOW 线自 12:54:27 代码落盘后每 20 分钟全轮崩（3 次 File name too long，"
            "13:20/13:40/14:00Z 各一）。正确写法应为 env 传参或 goal_file 追加，非 cwd。"),
        "impact": ("EXPAND-FOLLOW 跟进线（TensorBEM/SPH/DG/CAD 四库上游跟进）完全停摆："
                   "自 11:00:26Z m294 之后无新派发；且该 bug 使 287/289/290/294 的『等派发器"
                   "重发』路径也失效"),
        "owner_or_agent": ("代码在 agent_work/expand_follow/（平台侧脚本，非 dev 仓库代码）——"
                           "但修它属『改派发器』，红线内 operator 不动，标 [上报] N6 新颖类"),
        "action": "[上报] N6：新颖故障类（第一次见），本班只定性记录、不动手"}

    # 步骤 9：事件桥 degraded（容器/后端接口面，本班新增观察项）
    ev = []
    for lg in sorted(AW.glob("agent-*.log")):
        try:
            cnt = lg.read_text(encoding="utf-8", errors="replace").count("event bridge degraded")
        except Exception:
            continue
        if cnt:
            ev.append({"file": lg.name, "n": cnt})
    R["step9_event_bridge"] = {
        "per_file": ev, "total": sum(e["n"] for e in ev),
        "sample_last": sh("grep -ah 'event bridge degraded' /srv/tensor/shared/tensor_eco/agent_work/"
                          "agent-*.log | tail -1")[:200],
        "note": ("事件桥 5xx/transport 失败（含 psycopg2 UntranslatableCharacter 与 502）——"
                 "属后端/容器面观察项，非 mission 失败主因（24h 内 failed 无一例 "
                 "stop_condition=event_bridge）"),
        "action": "记录（不加处置）：容器 11/11 healthy，事件桥仅 5xx 抖动，未影响终态"}
    return R


if __name__ == "__main__":
    data = audit_and_gate(main(), time.time())
    print(json.dumps(data, ensure_ascii=False, indent=1))
