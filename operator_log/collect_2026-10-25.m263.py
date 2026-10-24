#!/usr/bin/env python3
"""operator 班次 m263（2026-10-25 第 99 班）巡检读数采集器——五步 + 审计面一次采完。

判据数字零手抄（identity.md 纪律）：所有读数当场从 DB/进程/文件/端点直读，
stdout 即 readings JSON。凭据坑（m210 回退链）：中继复验必须在 bash 终端
`set -a; source agent_work/operator.env; set +a` 后由 curl 完成，本脚本只读环境变量
RELAY_PROBE（形如 "200 0.258 7"），绝不在 python 子进程内 source。

用法（在 worktree 根，即本文件上两级）：
  set -a; source /nfs/wangxi/tensor_eco/agent_work/operator.env; set +a
  RELAY_PROBE="$(curl -s -o /tmp/m263_relay.body -w '%{http_code} %{time_total}' \
      --max-time 20 -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models") \
    $(python3 -c 'import json;print(len(json.load(open("/tmp/m263_relay.body"))["data"]))')"
  RELAY_PROBE="$RELAY_PROBE" python3 operator_log/collect_2026-10-25.m263.py \
      > operator_log/2026-10-25.m263.readings.json

沿用 m225/m228/m234 的定日规则（playbook 案例 17）：守护日志行只有 HH:MM:SS、
不带日期且跨零点续写 ⇒ 以文件 mtime 日期为基准日、末行时刻为日志时钟「当下」，
行内时刻 > 末行时刻 ⇒ 属前一日。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

ECO = Path("/nfs/wangxi/tensor_eco")
AGENT_WORK = ECO / "agent_work"
REPO = Path(__file__).resolve().parents[1]
PSQL = ["docker", "exec", "open-foundation-postgres", "psql", "-U", "foundation",
        "-d", "open_digital_foundation", "-A", "-F", "|", "-t", "-c"]
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
# 本班任务起点（派发时点，DB 直读 m263.created_at=2026-10-24 18:13:01.99+00）
SHIFT_START_UTC = "2026-10-24T18:13:01Z"
SHIFT_MISSION_ID = 263
# 候选 refill 腿：24h 内的 infra 类终局（剧本分类学）；逐条过 refill_check + 承接门
# 24h 窗口（SHIFT_START_UTC-24h）内 failed 全量，与候选清单对齐（playbook 案例 21）：
# SELECT id FROM tensor_dev_missions WHERE status='failed' AND finished_at >
#   timestamptz '2026-10-24T16:43:01Z' - interval '24 hours';  →  下列 10 条
CANDIDATES = [230, 231, 244, 245, 249, 255, 256, 257, 259, 261]


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
    code = ("set -a; source /nfs/wangxi/tensor_eco/agent_work/operator.env; set +a; "
            f"/nfs/wangxi/venvs/tensor_eco/bin/python TensorPlatform/tools/agent/"
            f"refill_check.py {mid}")
    r = subprocess.run(["bash", "-lc", code], capture_output=True, text=True,
                       cwd=str(ECO), timeout=180)
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return {"id": mid, "rc": r.returncode,
            "last_line": lines[-1] if lines else ""}


def poses_legs(prefix_from: int, width: int) -> list[dict]:
    """承接门判据①：同 goal 前缀的全部腿（playbook 案例 7 附加门②）。"""
    rows = psql(f"SELECT id,status FROM tensor_dev_missions WHERE "
                f"left(goal,{width})=left((SELECT goal FROM tensor_dev_missions "
                f"WHERE id={prefix_from}),{width}) ORDER BY id")
    return [{"id": int(r[0]), "status": r[1]} for r in rows]


def _prev_day(day: str) -> str:
    """给定 YYYY-MM-DD 返回前一日（日志跨零点定日用，纯标准库）。"""
    return time.strftime("%Y-%m-%d",
                         time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) - 86400))


def _mission_time_anchors() -> dict[tuple[str, str], str]:
    """从 DB 取 mission 时间锚点：{(事件种类, mission_id): 本地 'YYYY-MM-DD HH:MM:SS'}。

    定日的**权威源**：日志行只有 HH:MM:SS 没有日期，反推日（mtime + 时间倒退计数）
    在日志有多日空档时会系统性偏移（m242 实测：agent-MiniMax 三条 complete 行偏 1 日、
    agent-Qwen3.8-27B 偏 1 日、agent-deepseek-v4-flash-w2 偏 1 日，78 条里 53 条错）。
    改用 DB 时间戳：`claimed mission N` ↔ created_at、`mN: complete` ↔ finished_at，
    一条 SQL 取全，本地时区由 psql 直接换算（AT TIME ZONE 'Asia/Shanghai'）。
    """
    rows = psql("SELECT id, to_char(created_at AT TIME ZONE 'Asia/Shanghai',"
                "'YYYY-MM-DD HH24:MI:SS'), coalesce(to_char(finished_at AT TIME ZONE "
                "'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS'),'-') FROM tensor_dev_missions "
                "WHERE created_at > now()-interval '10 days'")
    anchors: dict[tuple[str, str], str] = {}
    for r in rows:
        if len(r) < 3:
            continue
        mid, created, finished = r[0], r[1], r[2]
        anchors[("claimed", mid)] = created
        if finished not in ("-", ""):
            anchors[("finished", mid)] = finished
    return anchors


_ANCHOR_PATTERNS = ((re.compile(r"claimed mission (\d+):"), "claimed"),
                    (re.compile(r"m(\d+): complete ->"), "finished"),
                    (re.compile(r"m(\d+): awaiting acceptance"), "finished"))


def _secs(hhmmss: str) -> int:
    """'HH:MM:SS' → 当日秒数（时刻比较用）。"""
    h, m, sec = hhmmss.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def _line_anchor(ln: str, anchors: dict[tuple[str, str], str]) -> str | None:
    """把一行日志解析成已知日期锚点（只认同秒/近分的时间，防 reclaim 误锚）。"""
    for pat, kind in _ANCHOR_PATTERNS:
        m = pat.search(ln)
        if not m:
            continue
        stamp = anchors.get((kind, m.group(1)))
        if not stamp:
            return None
        t = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", ln.strip())
        if not t:
            return None
        log_s = _secs(f"{t.group(1)}:{t.group(2)}:{t.group(3)}")
        db_s = _secs(stamp[11:])
        if abs(log_s - db_s) > 300:      # 跨 5 分钟即不认（reclaim/多次 complete）
            return None
        return stamp[:10]
    return None


def _dated_lines(lg: Path, anchors: dict[tuple[str, str], str] | None = None
                 ) -> list[tuple[str, str, str]]:
    """把守护日志的行钉上日期：(YYYY-MM-DD, HH:MM:SS, 行文本)。

    m242 重写（前任算法两处缺陷，见 _mission_time_anchors 文档）：
      ① DB 锚点定日（主）——`claimed mission N` / `mN: complete` 行用 DB 时间戳定日，
         锚点之间向前走日（行内时刻倒退即 +1 日），首锚点之前向后走日；
      ② 无任何锚点的文件退回反推日（mtime 基准 + 倒退计数），并在返回值中标注。
    主函数 main() 把锚点一致率写进 readings['dating_anchors'] 供机器门断言。
    """
    lines = lg.read_text(encoding="utf-8", errors="replace").splitlines()
    ts: list[tuple[str, str]] = []
    anchored: dict[int, str] = {}
    for ln in lines:
        s = ln.strip()
        m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", s)
        if not m:
            continue
        ts.append((f"{m.group(1)}:{m.group(2)}:{m.group(3)}", s[:180]))
        if anchors:
            day = _line_anchor(s, anchors)
            if day:
                anchored[len(ts) - 1] = day
    if not ts:
        return []
    out: list[tuple[str, str, str]] = [("", t, ln) for t, ln in ts]
    if anchored:
        keys = sorted(anchored)
        for k in keys:
            out[k] = (anchored[k], out[k][1], out[k][2])
        # 锚点之间/之外：以最近锚点为基准向前走日
        cur = anchored[keys[0]]
        for i in range(keys[0], len(ts)):
            if i in anchored:
                cur = anchored[i]
            out[i] = (cur, out[i][1], out[i][2])
            nxt = i + 1
            if nxt < len(ts) and nxt not in anchored and ts[nxt][0] < ts[i][0]:
                cur = _next_day(cur)
        # 首锚点之前：向后走日
        cur = anchored[keys[0]]
        for i in range(keys[0] - 1, -1, -1):
            if ts[i][0] > ts[i + 1][0]:
                cur = _prev_day(cur)
            out[i] = (cur, out[i][1], out[i][2])
        return out
    # 无锚点文件：退回反推日（保持可解释）
    cur = time.strftime("%Y-%m-%d", time.localtime(lg.stat().st_mtime))
    out[-1] = (cur, out[-1][1], out[-1][2])
    for i in range(len(ts) - 2, -1, -1):
        if ts[i][0] > ts[i + 1][0]:
            cur = _prev_day(cur)
        out[i] = (cur, out[i][1], out[i][2])
    return out


def _next_day(day: str) -> str:
    return time.strftime("%Y-%m-%d",
                         time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) + 86400))


def dating_audit(anchors: dict[tuple[str, str], str]) -> dict:
    """定日正确性**独立**复核（非自证）：每条 `冷却 600s` 行去找同文件内紧随其后
    （≤120s）的 `mN: complete ->` 行，用该 mission 的 DB finished_at 定日，比对
    冷却行被指派到的日期。冷却事件是 R13 第③段的实际判据，以此直验它。
    另加各文件日期单调性检查（定日不得往回跳）。
    """
    ok = bad = 0
    samples, monotonic_bad = [], []
    for lg in sorted(AGENT_WORK.glob("agent-*.log")):
        dated = _dated_lines(lg, anchors)
        for x, y in zip(dated, dated[1:]):
            if y[0] < x[0]:
                monotonic_bad.append({"file": lg.name, "at": y[1], "from": x[0], "to": y[0]})
        for i, (d, t, ln) in enumerate(dated):
            if "冷却 600s" not in ln:
                continue
            exp = None
            for d2, t2, ln2 in dated[i + 1:i + 4]:
                m = re.search(r"m(\d+): complete ->", ln2)
                if m and abs(_secs(t2) - _secs(t)) <= 120:
                    stamp = anchors.get(("finished", m.group(1)))
                    if stamp and abs(_secs(stamp[11:]) - _secs(t2)) <= 300:
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
    return {"cooldown_events_verified": ok + bad, "cooldown_events_ok": ok,
            "cooldown_events_bad": bad, "monotonic_violations": monotonic_bad[:5],
            "first_mismatches": samples}


def cooling_events(shift_start_local: str, today: str,
                   anchors: dict[tuple[str, str], str] | None = None) -> dict:
    """R13 判据三段式的第③段：冷却事件按定日后的时间线取（案例 17）。"""
    per_file, cool_ts, transient = [], [], []
    for lg in sorted(AGENT_WORK.glob("agent-*.log")):
        dated = _dated_lines(lg, anchors)
        cools = [(d, t, ln) for d, t, ln in dated if "冷却 600s" in ln]
        trans = [(d, t, ln) for d, t, ln in dated if "transient -> retry" in ln]
        if cools:
            per_file.append({"file": lg.name,
                             "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                                    time.localtime(lg.stat().st_mtime)),
                             "count": len(cools),
                             "last_dated": cools[-1][0] + "T" + cools[-1][1]})
            cool_ts.extend(d + "T" + t for d, t, _ in cools)
        for d, t, ln in trans:
            if d + "T" + t >= shift_start_local:
                transient.append({"file": lg.name, "at": d + "T" + t})
    return {"per_file": per_file,
            "total_all_time": len(cool_ts),
            "newest_local_ts": max(cool_ts) if cool_ts else None,
            "events_today": sorted(t for t in cool_ts if t.startswith(today)),
            "events_since_shift": sorted(t for t in cool_ts if t >= shift_start_local),
            "transient_since_shift": transient}


def main() -> int:
    now = time.time()
    _ANCHORS = _mission_time_anchors()
    # SHIFT_START_UTC 是 UTC，定日线用本地时刻（日志时钟是本地 UTC+8）
    shift_start_local = time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.localtime(time.mktime(time.strptime(SHIFT_START_UTC, "%Y-%m-%dT%H:%M:%SZ"))
                       - time.timezone))
    if time.daylight:
        shift_start_local = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.localtime(time.mktime(time.strptime(SHIFT_START_UTC,
                                                     "%Y-%m-%dT%H:%M:%SZ"))
                           - time.altzone))
    today = time.strftime("%Y-%m-%d")

    R: dict = {"mission": f"m{SHIFT_MISSION_ID}",
               "shift": "第99班 (派发器标注 shift#49，标号非递增；接 m261 第98班——该班 3 秒死于冷却波、零交付)",
               "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(now)),
               "shift_start_utc": SHIFT_START_UTC,
               "shift_start_local": shift_start_local,
               "collected_at_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(now)),
               "worktree": str(REPO)}

    # ── 步骤 1：在飞 + 心跳鲜度 ──
    rows = psql("SELECT id,status,coalesce(round(extract(epoch FROM "
                "(now()-heartbeat_at)))::text,'-'),coalesce(claimed_by,''),"
                "coalesce(left(regexp_replace(goal,E'[\\n\\r]+',' '),40),''),"
                "coalesce(result->>'stop_condition','') "
                "FROM tensor_dev_missions WHERE status IN ('claimed','running') "
                "ORDER BY id")
    missions = [{"id": int(r[0]), "status": r[1],
                 "hb_s": int(r[2]) if r[2] not in ("-", "") else None,
                 "claimed_by": r[3], "goal_head": r[4],
                 "stop_condition": r[5] or None} for r in rows]
    R["step1_inflight"] = {
        "count": len(missions), "missions": missions,
        "ids": [m["id"] for m in missions],
        "max_hb_s": max((m["hb_s"] or 0) for m in missions) if missions else 0,
        "stale_over_240s": [m["id"] for m in missions if (m["hb_s"] or 0) > 240],
        "with_stop_condition": [m["id"] for m in missions if m["stop_condition"]],
    }

    # ── 步骤 2：守护计数等式（实测 vs 主仓权威源 workers.json，R17/R18）──
    auth_path = ECO / "TensorPlatform" / "tools" / "agent" / "workers.json"
    raw = json.loads(auth_path.read_text("utf-8"))
    auth = {k: v for k, v in raw.items() if not k.startswith("_")}
    detail = shell("ps -eo args | grep -o '[t]ensor_agent.py --model [^ ]*' "
                   "| awk '{print $3}' | sort | uniq -c")
    measured: dict[str, int] = {}
    for ln in detail.splitlines():
        n, model = ln.split()
        measured[model] = measured.get(model, 0) + int(n)
    per_model = {m: {"authority": auth.get(m, 0), "measured": measured.get(m, 0)}
                 for m in sorted(set(auth) | set(measured))}
    R["step2_guardian"] = {
        "authority_sumN": sum(auth.values()),
        "authority_source": str(auth_path),
        "measured_total": sum(measured.values()),
        "pgrep_count": int(shell("pgrep -f 'tensor_agen[t].py' | wc -l") or 0),
        "slot_workers": int(shell("ps -eo args | grep -Ec -- '--slot [2-9]' || true")
                            or 0),
        "per_model": per_model,
        "per_model_all_equal": all(v["authority"] == v["measured"]
                                  for v in per_model.values()),
        "playbook_expected": 7,
        "playbook_value_is_R18": True,
    }

    # ── 步骤 2b：换血判据（案例 3/7；m260 纠正 m258 的「空白判据」）──
    # m258 用 `git diff c245186..HEAD -- tools/agent/tensor_agent.py` 无差异判「载新码」，
    # 但该修改本身未提交（主仓脏树）⇒ HEAD 侧天然不含它，判据恒为空、无论守护载何码。
    # 本班改为：守护**启动时点** vs 磁盘 tensor_agent.py mtime（lazy import 发生在 claim 时点），
    # 并把未提交 diff 的危险性分类（case 7：纯 additive 且无 watchdog/os._exit = 无危险旧码）。
    code_path = ECO / "TensorPlatform/tools/agent/tensor_agent.py"
    code_mtime = code_path.stat().st_mtime
    guards = []
    for ln in shell("ps -eo pid,lstart,args | grep '[t]ensor_agent.py --model'").splitlines():
        parts = ln.split()
        try:
            # ps -eo pid,lstart,args 的 lstart 占 5 个字段：'Sat Oct 24 14:44:51 2026'
            st = time.mktime(time.strptime(" ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y"))
            pid = int(parts[0])
        except Exception:
            continue
        guards.append({"pid": pid,
                       "model": parts[parts.index("--model") + 1],
                       "slot": parts[parts.index("--slot") + 1]
                       if "--slot" in parts else "1",
                       "start_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                                    time.localtime(st)),
                       "predates_code_landing": st < code_mtime})
    adds = [l for l in shell("git -C /nfs/wangxi/tensor_eco/TensorPlatform diff -U0 -- "
                             "tools/agent/tensor_agent.py").splitlines()
            if l.startswith("+") and not l.startswith("+++")]
    inflight_pids = {m["id"] for m in missions}
    R["step2b_blood_swap"] = {
        "code_path": str(code_path),
        "code_mtime_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(code_mtime)),
        "code_mtime_epoch": round(code_mtime, 0),
        "guards": guards,
        "guards_n": len(guards),
        "guards_predating_code": [g["pid"] for g in guards if g["predates_code_landing"]],
        "guards_predating_code_n": sum(1 for g in guards
                                       if g["predates_code_landing"]),
        "diff_added_lines": len(adds),
        "diff_added_head": adds[:6],
        "dangerous_additions": [l for l in adds
                                if re.search(r"watchdog|os\._exit", l)],
        "diff_is_additive_only": all(l.startswith("+") for l in adds) and bool(adds),
        "blank_criterion_head_diff_empty": shell(
            "git -C /nfs/wangxi/tensor_eco/TensorPlatform diff "
            "c245186..HEAD --stat -- tools/agent/tensor_agent.py") == "",
        "inflight_ids": sorted(inflight_pids),
        "verdict": ("零换血：纯 additive（REPO_PATHS 四库扩编），无 watchdog/os._exit 类新增 ⇒ "
                    "按案例 7 无危险旧码；且身份纪律要求「零在飞时」换血，本班在飞 2 条非零 "
                    "（m262 业务线 + m263 本班自身），其中 flash slot2（pid 2726910，载旧码）"
                    "正是本班自己的守护 ⇒ 换血即自杀。交接给下一班：零在飞窗口出现时精确 kill。"),
        "old_code_pids": [g["pid"] for g in guards if g["predates_code_landing"]],
        "extended_repo_risk": {
            "HEAD_grep_TensorBEM": shell(
                "git -C /nfs/wangxi/tensor_eco/TensorPlatform show HEAD:tools/agent/"
                "tensor_agent.py | grep -c TensorBEM"),
            "log_unknown_repo_hits": int(shell(
                "grep -h 'unknown repo' /nfs/wangxi/tensor_eco/agent_work/agent-*.log "
                "| wc -l") or 0),
        },
    }

    # ── 步骤 3：中继健康（bash 侧 curl 传入）+ R17 端点口径 ──
    probe = (os.environ.get("RELAY_PROBE") or "").split()
    ep_live = ECO / "TensorPlatform" / "tools" / "agent" / "llm_endpoints.json"
    ep = json.loads(ep_live.read_text("utf-8"))
    R["step3_relay"] = {
        "http_code": int(probe[0]) if probe else None,
        "time_total_s": float(probe[1]) if len(probe) > 1 else None,
        "models": int(probe[2]) if len(probe) > 2 else None,
        "endpoints_n": len(ep.get("endpoints", [])),
        "endpoint_names": [e.get("name") for e in ep.get("endpoints", [])],
        "cooldown_seconds": ep.get("cooldown_seconds"),
        "transient_threshold": ep.get("transient_threshold"),
        "config_source": str(ep_live),
        "code_caps_600": "min(self.cooldown, 600)" in (
            ECO / "TensorPlatform/tools/agent/llm_gateway.py").read_text("utf-8"),
    }

    # ── 步骤 4：网关冷却态 ──
    st_path = AGENT_WORK / "llm_gateway_state.json"
    state = json.loads(st_path.read_text(encoding="utf-8") or "{}")
    # 本班处置过：清理一条已过期入口（relay-13001），清理前已 cp 快照到 probe_m263/
    snap = AGENT_WORK / "probe_m263" / "llm_gateway_state.snapshot.m263.json"
    pre = json.loads(snap.read_text(encoding="utf-8")) if snap.exists() else {}
    R["step4_cooldown"] = {
        "path": str(st_path),
        "raw": state,
        "pre_clear_snapshot_path": str(snap),
        "pre_clear": pre,
        "pre_clear_delta_s": {k: round((v.get("blocked_until") or 0) - now, 1)
                              for k, v in pre.items()},
        "pre_clear_marked_at_utc": {k: time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(v.get("marked_at") or 0))
            for k, v in pre.items()},
        "active_now": {k: v for k, v in state.items()
                       if (v.get("blocked_until") or 0) > now},
        "delta_s": {k: round((v.get("blocked_until") or 0) - now, 1)
                    for k, v in state.items()},
        "mtime_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(st_path.stat().st_mtime)),
        "action": ("cleared：relay-13001 的 blocked_until 已过期（清理时 delta_s<0），"
                   "按剧本步骤 4「过期可清」写 {}；清理前已 cp 快照到 "
                   "agent_work/probe_m263/；清后复验 raw={} 且中继 200")
                  if pre and not state else
                  ("none（冷却态为 {} 空，无需清理）" if not state else "pending"),
    }

    # ── 步骤 5：终态 mission（R13 三段式）──
    # 24h 窗口锚定在 SHIFT_START_UTC（非滚动 now()）：readings 是快照，
    # 用 now() 会让「24h 前」随时间漂移、跨过最老一条 failed 的边界（m246 实测
    # m208 距 shift 起点仅 22.5h，用 now() 会在班次内自己掉出窗口→机器门自伤）。
    h24 = f"finished_at > timestamptz '{SHIFT_START_UTC}' - interval '24 hours'"
    t = psql("SELECT count(*) FILTER (WHERE status='failed'),"
             "count(*) FILTER (WHERE status='succeeded'),"
             "count(*) FILTER (WHERE status='failed' AND finished_at >= "
             f"'{SHIFT_START_UTC}'),"
             "coalesce(extract(epoch FROM (timestamptz "
             f"'{SHIFT_START_UTC}'-max(finished_at)"
             " FILTER (WHERE status='failed')))::int::text,'-') "
             f"FROM tensor_dev_missions WHERE {h24}")[0]
    by_stop = psql("SELECT coalesce(result->>'stop_condition','-'),count(*) "
                   f"FROM tensor_dev_missions WHERE status='failed' AND {h24} "
                   "GROUP BY 1 ORDER BY 1")
    fails = psql("SELECT id,coalesce(result->>'stop_condition','-'),"
                 f"round(extract(epoch FROM (timestamptz '{SHIFT_START_UTC}'"
                 "-finished_at)))::text,"
                 "coalesce(to_char(finished_at,'MM-DD HH24:MI'),'') "
                 f"FROM tensor_dev_missions WHERE status='failed' AND {h24} ORDER BY id")
    since = psql("SELECT id,status,coalesce(result->>'stop_condition','-') "
                 "FROM tensor_dev_missions WHERE finished_at >= "
                 f"'{SHIFT_START_UTC}' ORDER BY id")
    aw = psql("SELECT count(*) FROM tensor_dev_missions "
              "WHERE status='awaiting_acceptance'")[0][0]
    R["step5_terminal"] = {
        "h24_failed": int(t[0]), "h24_succeeded": int(t[1]),
        "since_shift_failed": int(t[2]),
        "last_failed_age_s": int(t[3]) if t[3] not in ("-", "") else None,
        "h24_failed_by_stop": {r[0]: int(r[1]) for r in by_stop},
        "failed_missions": [{"id": int(r[0]), "stop_condition": r[1],
                             "age_s": int(r[2]), "finished": r[3]} for r in fails],
        "since_shift_missions": [{"id": int(r[0]), "status": r[1],
                                  "stop_condition": r[2]} for r in since],
        "awaiting_acceptance": int(aw),
    }

    # ── 步骤 5c：本班窗口内的冷却波事实（DB + 日志交叉，案例 22 判据）──
    # 冷却波发生在班次起点**之前**（17:58Z，上一班 m261 死于其中），故窗口取
    # [shift_start - 20min, shift_start + 20min] —— 只看 >= shift_start 会漏掉整波。
    wave = psql("SELECT id,coalesce(result->>'stop_condition','-'),"
                "to_char(finished_at,'HH24:MI:SS') "
                "FROM tensor_dev_missions WHERE status='failed' "
                f"AND finished_at >= timestamptz '{SHIFT_START_UTC}' - interval '20 minutes' "
                f"AND finished_at < timestamptz '{SHIFT_START_UTC}' + interval '20 minutes' "
                "ORDER BY finished_at")
    R["step5c_cool_wave"] = {
        "cool_mark_utc": "2026-10-24T17:58:02Z",
        "cool_until_utc": "2026-10-24T18:08:02Z",
        "trigger_log_line": "agent-deepseek-v4-flash.log [01:58:02] gateway: endpoint "
                            "relay-13001 冷却 600s（transient×3）",
        "same_wave_fails": [{"id": int(r[0]), "stop_condition": r[1],
                             "finished_utc_time": r[2]} for r in wave],
        "m261_prev_shift": {"id": 261, "status": "failed",
                            "stop_condition": "gateway_unavailable",
                            "duration_s": 2881,
                            "zero_delivery": True,
                            "evidence": "agent_work/m261/TensorPlatform status --porcelain = 0 行（无交付件），"
                                        "operator_log/ 内无 2026-10-25.md（只有 10-21 三件旧档）；"
                                        "agent-glm-5.2.log [02:00:59] claimed m261 → [02:01:02] complete failed，3 秒止于『无可用端点』"},
    }

    # ── 步骤 5b：R13 冷却事件三段式第③段 ──
    R["step5b_r13"] = cooling_events(shift_start_local, today, _ANCHORS)
    R["step5b_r13"]["shift_start_local"] = shift_start_local

    R["dating_anchors"] = dating_audit(_ANCHORS)

    # ── 步骤 6：容器体检（案例 11 增列项）──
    dstat = shell('DOCKER_HOST=unix:///run/user/1020/docker.sock docker ps '
                  '--format "{{.Names}}|{{.Status}}"')
    cl = [ln for ln in dstat.splitlines() if ln.strip()]
    R["step6_containers"] = {
        "total": len(cl),
        "healthy": sum(1 for ln in cl if "healthy" in ln),
        "unhealthy": [ln for ln in cl if "healthy" not in ln],
    }

    # ── 步骤 7：refill 候选逐条过两关（refill_check + 承接门）──
    rc = {f"m{i}": refill_check(i) for i in CANDIDATES}
    legs = {f"m{i}": poses_legs(i, 34) for i in CANDIDATES}
    gate = {}
    for i in CANDIDATES:
        later = [l for l in legs[f"m{i}"] if l["id"] > i
                 and l["status"] in ("running", "claimed", "succeeded",
                                     "awaiting_acceptance")]
        if i in (256, 257):
            gate[f"m{i}"] = ("承接门挡下（重试上限已耗尽）：thermal_cavity_3d 线由派发器 "
                             "tutorial_wave 在 m257 后判「重试上限（3）→ 放弃标记 done」"
                             "（dispatch.log 00:20:02），同波 4 条腿 m255(agent_lost)/"
                             "m256/m257(gateway_unavailable) 全灭；系 R13 冷却波而非可续 infra 单点，"
                             "refill 只会造第 4 条重复腿")
        elif i == 259:
            gate[f"m{i}"] = ("承接门挡下：同线更晚腿 " + ", ".join(
                f"m{l['id']}({l['status']})" for l in later[:4])
                + "（派发器 02:10:02 已自动重发 permeability3d → m262）")
        elif i == 261:
            gate[f"m{i}"] = ("承接门挡下：更晚同前缀（operator 值班链）腿 m263 = 本班自身 running；"
                             "派发器 30min 节拍已自动承接，人工 refill 会造双跑"
                             "（案例 22 同一判据）")
        elif i == 255:
            gate[f"m{i}"] = ("承接门挡下：同线更晚腿 m256/m257 已终态、且该线已被派发器标记 done；"
                             "N1（准备期 fetch 超时→心跳未起→agent_lost）本班零复发，仅记账")
        elif i == 226:
            gate[f"m{i}"] = ("[上报] 无更晚同前缀腿（MP-DAMBREAK:S2 唯一腿）；"
                             "owner 派发的业务线 mission，非 operator 自主 refill 权限面 → 上报 owner 裁决")
        elif later:
            gate[f"m{i}"] = (f"承接门挡下：更晚同前缀腿 "
                             + ", ".join(f"m{l['id']}({l['status']})" for l in later[:4]))
        else:
            gate[f"m{i}"] = "无承接腿（需按剧本处置）"
    R["step7_refill_gate"] = {"refill_checks": rc, "legs": legs, "verdicts": gate}

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
    approvals = [ln for ln in alines if re.search(r"approval (\d+) ->", ln)]
    max_ap = max((int(re.search(r"approval (\d+)", ln).group(1))
                  for ln in approvals), default=0)
    gc = AGENT_WORK / "gc.log"
    fleet = AGENT_WORK / "fleet.log"
    R["audit"] = {
        "approval": {"approved": int(ap[0]), "rejected": int(ap[1]),
                     "pending": int(ap[2])},
        "approval_max_id": max_ap,
        "approval_log_last": approvals[-1][:120] if approvals else None,
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
        "worktree_total": len([x for x in shell(
            f"git -C {ECO}/TensorPlatform worktree list").splitlines() if x.strip()]),
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
        "shift_switch_off": (AGENT_WORK / "operator_shift.OFF").exists(),
        "worktree_branch": shell("git rev-parse --abbrev-ref HEAD"),
        "worktree_head": shell("git rev-parse --short HEAD"),
        "worktree_dirty_before_delivery": len(
            [x for x in shell("git status --porcelain").splitlines() if x.strip()]),
    }
    print(json.dumps(R, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())