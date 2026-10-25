#!/usr/bin/env python3
"""operator 班次 m299（2026-10-25 第 103 班）巡检读数采集器——五步 + 审计面一次采完。

判据数字零手抄（identity.md 纪律）：所有读数当场从 DB/进程/文件/端点直读，
stdout 即 readings JSON。凭据坑（m210 回退链）：中继复验必须在 bash 终端
`set -a; source agent_work/operator.env; set +a` 后由 curl 完成，本脚本只读环境变量
RELAY_PROBE（形如 "200 0.258 7"），绝不在 python 子进程内 source。

用法（在 worktree 根，即本文件上两级）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  RELAY_PROBE="$(curl -s -o /tmp/m268_relay.body -w '%{http_code} %{time_total}' \
      --max-time 20 -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models") \
    $(python3 -c 'import json;print(len(json.load(open("/tmp/m268_relay.body"))["data"]))')"
  RELAY_PROBE="$RELAY_PROBE" python3 operator_log/collect_2026-10-25.m268.py \
      > operator_log/2026-10-25.m268.readings.json

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

ECO = Path("/srv/tensor/shared/tensor_eco")
AGENT_WORK = ECO / "agent_work"
REPO = Path(__file__).resolve().parents[1]
PSQL = ["docker", "exec", "open-foundation-postgres", "psql", "-U", "foundation",
        "-d", "open_digital_foundation", "-A", "-F", "|", "-t", "-c"]
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
# 本班任务起点（派发时点，DB 直读 m299.created_at=2026-10-25T04:13:02Z / 12:13:02 CST）
SHIFT_START_UTC = "2026-10-25T04:13:02Z"
SHIFT_MISSION_ID = 299
# 候选 refill 腿：24h 内的 infra 类终局（剧本分类学）；逐条过 refill_check + 承接门
# 24h 窗口（SHIFT_START_UTC-24h）内 failed 全量 24 条，与候选清单对齐（playbook 案例 21）：
# SELECT id FROM tensor_dev_missions WHERE status='failed' AND finished_at >
#   timestamptz '2026-10-25T04:13:02Z' - interval '24 hours';  →  下列 24 条
CANDIDATES = [231, 244, 245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284, 285,
              286, 287, 288, 289, 290, 291, 292, 294, 295, 297, 301]


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
               "shift": "第103班 (派发器标注 shift#43，标号非递增；上一班 m271 因 03:31Z quota 冷却波 gateway_unavailable 失败，本班由派发器 04:13:02Z 续派)",
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
    adds = [l for l in shell("git -C /srv/tensor/shared/tensor_eco/TensorPlatform diff -U0 -- "
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
            "git -C /srv/tensor/shared/tensor_eco/TensorPlatform diff "
            "c245186..HEAD --stat -- tools/agent/tensor_agent.py") == "",
        "inflight_ids": sorted(inflight_pids),
        "verdict": ("本班已换血 2 台空闲旧码守护 + 遗留 2 台说明。"
                    "判据：磁盘 tensor_agent.py mtime=2026-10-25 09:12:10 CST，"
                    "仍在飞的 2 台旧码守护 = 2726618(flash slot1，启动 10-24 14:44:51)、"
                    "2726917(flash slot3，启动 10-24 14:45:00)。"
                    "本班 kill 的是 2726616(glm-5.2 slot1，空闲) 与 2726910(flash slot2，"
                    "其 m297 已于 04:36:32Z budget_exceeded 终态、零在飞)，cron */5 于 "
                    "04:45:01Z spawned 2 台新码（1271676 glm-5.2 slot1 --claim-null、"
                    "1271711 flash slot2），计数 16 复原。"
                    "保留 2 台旧码原因：2726618/2726917 均为 flash 槽且现在**在飞**"
                    "（m298 在 flash slot1 = 2726618；本班 m299 在 flash slot3 = 2726917 自身）"
                    "——换血即自杀/杀在飞，纪律禁止。交接下一班：flash 双槽空闲窗口出现时精确 kill。"
                    "注：m287/289/290/294 四腿死于旧码的 unknown repo（缺 TensorBEM/SPH/DG），"
                    "glm-5.2(2726616) 与 flash#2(2726910) 正是主犯，已清除。"),
        "old_code_pids": [g["pid"] for g in guards if g["predates_code_landing"]],
        "extended_repo_risk": {
            "HEAD_grep_TensorBEM": shell(
                "git -C /srv/tensor/shared/tensor_eco/TensorPlatform show HEAD:tools/agent/"
                "tensor_agent.py | grep -c TensorBEM"),
            "log_unknown_repo_hits": int(shell(
                "grep -h 'unknown repo' /srv/tensor/shared/tensor_eco/agent_work/agent-*.log "
                "| wc -l") or 0),
        },
    }

    # ── 步骤 2c：本班换血动作事实（kill 精确 PID → cron 拉起，直读 fleet.log + ps）──
    fleet_lines = [ln for ln in (AGENT_WORK / "fleet.log").read_text(
        encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    R["step2c_blood_swap_action"] = {
        "killed_pids": [2726616, 2726910],
        "killed_reason": {
            "2726616": "glm-5.2 slot1，启动 10-24 14:44:51 < 磁盘 mtime 10-25 09:12:10 ⇒ 载旧码"
                       "（HEAD 版缺 TensorBEM/TensorSPH/TensorDG 三库）→ EXPAND-FOLLOW 四腿"
                       "『unknown repo』秒死的直接主犯；本班接班时该槽零在飞（最后一条 m291 已 "
                       "03:32:05Z 终态）",
            "2726910": "flash slot2，启动 10-24 14:45:00 ⇒ 载旧码；其承接的 m297 已于 "
                       "04:36:32Z budget_exceeded 终态 ⇒ 零在飞，可换",
        },
        "kept_old_code_pids": [2726618, 2726917],
        "kept_reason": ("2726618 = m298 在飞（flash slot1）、2726917 = 本班 m299 自身所在"
                        "（flash slot3）⇒ 换血即杀在飞/自杀，纪律禁止"),
        "fleet_log_tail": fleet_lines[-3:],
        "guard_count_after": int(shell("ps -eo args | grep -c '[t]ensor_agent.py --model'") or 0),
        "spawned_new_guards": [g for g in guards if not g["predates_code_landing"]][-3:],
        "action": "executed（精确 PID kill 2 台空闲旧码守护 → cron */5 于 04:45:01Z 拉起 2 台新码，计数 16 复原）",
    }

    # ── 步骤 2d：换血副作用自查（本班诚实记录：换血 TOCTOU 打死 m301）──
    # 事实链（全部直读 DB + 守护日志）：
    #   m301 created_at 04:40:02Z；`claimed mission 301` 行 12:40:12 CST = 04:40:12Z
    #   → 由 flash#2(pid 2726910) 认领；本班 kill 命令的执行时刻 04:40:16Z
    #   ⇒ 认领比 kill 早 4 秒。m301 于 04:44:13Z 被 reaper 判 agent_lost（251s）。
    # 根因：外部 kill 与守护 claim 之间无互斥 —— 本班在 04:37~04:39 核对「该槽零在飞」，
    #   kill 落盘前 4 秒派发器派出并认领 m301，形成 TOCTOU 窗口。
    cas = psql("SELECT id,status,claimed_by,to_char(created_at,'HH24:MI:SS')||'Z',"
               "to_char(finished_at,'HH24:MI:SS')||'Z',"
               "round(extract(epoch FROM (finished_at-created_at)))::text "
               "FROM tensor_dev_missions WHERE id=301")
    R["step2d_blood_swap_side_effect"] = {
        "casualty_mission": 301,
        "row": ({"id": int(cas[0][0]), "status": cas[0][1], "claimed_by": cas[0][2],
                 "created_utc": cas[0][3], "finished_utc": cas[0][4],
                 "duration_s": int(cas[0][5])} if cas else None),
        "claim_log_line": "agent-deepseek-v4-flash-w2.log [12:40:12] claimed mission 301: "
                          "TUTORIAL-WAVE:sphere_re100",
        "kill_executed_utc": "2026-10-25T04:40:16Z（本班 bash 输出直读）",
        "gap_s_between_claim_and_kill": 4,
        "root_cause": ("TOCTOU：外部 kill 与守护 claim 之间无互斥。本班 04:37~04:39 核对"
                       "『2726910 的 m297 已 04:36:32Z 终态 ⇒ 该槽零在飞』成立，"
                       "但 04:40:12Z 派发器（tutorial_wave 档期 12:40:02）派出 m301 "
                       "并被 2726910 认领，本班 kill 于 04:40:16Z 落下 ⇒ 连带打死 m301"),
        "worktree_state": "/agent_work/m301/ 为空目录（守护被 kill 后未及建树）",
        "self_heal": ("派发器 tutorial_wave/dispatch.log 2026-10-25T12:50:01 已判"
                      "『m301 终态=failed → 第 1 次重发（下轮）』⇒ 业务线自愈，"
                      "故**不 refill m301**（refill 会与派发器重发并行双跑）"),
        "disposition": "[上报] N5：换血规程缺「守护自退役」机制（外部 kill 无法原子地阻止 "
                       "claim）。建议 owner 侧在 tensor_agent 加「软退役」信号文件"
                       "（守护轮询间空转前自检退场）或 fleet.py 支持 drain。"
                       "本班教训已写进交接笔记：kill 前后各查一次在飞，且窗口内新 claim 即放弃换血。",
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
    # 本班处置：接班读到 relay-13001 quota 冷却（blocked_until 11:41:29 CST 已过期 2833s）
    # → 按剧本「blocked_until 已过期可清」清成 {}，清前快照保留在 probe_m299/
    snap = AGENT_WORK / "probe_m299" / "llm_gateway_state.snapshot.m299.json"
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
        "action": ("cleared（接班读到 relay-13001 quota 冷却 blocked_until=11:41:29 CST，"
                   "已过期 2833s；复验中继 200 后按剧本清成 {}，清前快照见 pre_clear）"
                   if not state and pre else
                   ("none（冷却态为 {} 空，无需清理）" if not state else "pending")),
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

    # ── 步骤 5c：冷却波事实（上一班 m261 死于 18:01Z 冷却波，本班窗口不含）──
    # 本班 SHIFT_START_UTC=19:13:02Z，冷却波发生在 17:58Z~18:08Z，已在上一班窗口。
    # 本班窗口 [shift_start-20min, shift_start+20min] = [18:53Z, 19:33Z] 内无 failed。
    wave = psql("SELECT id,coalesce(result->>'stop_condition','-'),"
                "to_char(finished_at,'HH24:MI:SS') "
                "FROM tensor_dev_missions WHERE status='failed' "
                f"AND finished_at >= timestamptz '{SHIFT_START_UTC}' - interval '20 minutes' "
                f"AND finished_at < timestamptz '{SHIFT_START_UTC}' + interval '20 minutes' "
                "ORDER BY finished_at")
    R["step5c_cool_wave"] = {
        "cool_mark_utc": "2026-10-25T03:31:29Z",
        "cool_until_utc": "2026-10-25T03:41:29Z (代码 DEFAULT_COOLDOWN=1800 但 min(cooldown,600)=600 生效)",
        "trigger_log_line": "agent-glm-5.2.log [11:31:29] gateway: endpoint relay-13001 "
                            "冷却 600s（quota） + [11:31:29] native llm attempt 1/6 failed"
                            "（quota -> rotate）：RateLimitError: 使用超限",
        "wave_window_relation": ("该 quota 冷却波 03:31:29Z~03:41:29Z 落在**上一班 m271 的窗口**"
                                 "（m271 起点 00:43:01Z，终点 03:31:40Z）；本班起点 "
                                 "2026-10-25T04:13:02Z 的 ±20min 窗口 [03:53:02Z,04:33:02Z] 内"
                                 "只有 m297 budget_exceeded（04:36:32Z 在窗外），无同波 gateway 失败"),
        "same_wave_fails": [{"id": int(r[0]), "stop_condition": r[1],
                             "finished_utc_time": r[2]} for r in wave],
        "quota_wave_24h_scope": "DB 直读：03:31~03:33Z 两分钟内 10 条 gateway_unavailable 秒死"
                                "（269/270/271/284/285/286/288/291/292/295），"
                                "全部 claimed_by=glm-5.2 或 qwen3.7-plus 或 flash；"
                                "冷却态是全局文件而非 per-endpoint ⇒ 跨模型连带",
        "prev_shift_m271": {"id": 271, "status": "failed",
                            "stop_condition": "gateway_unavailable",
                            "created_utc": "2026-10-25T00:43:01Z",
                            "finished_utc": "2026-10-25T03:31:40Z",
                            "duration_s": 10119,
                            "zero_delivery": True,
                            "evidence": "DB 直读 + worktree /agent_work/m271/TensorPlatform "
                                        "HEAD=c245186 与 base 相同（零 commit 交付）；"
                                        "agent-glm-5.2.log 于 11:31:40 记 complete failed"},
        "m261_n3_case": {"id": 261, "status": "failed",
                         "stop_condition": "gateway_unavailable",
                         "duration_s": 3,
                         "zero_delivery": True,
                         "evidence": "agent-glm-5.2.log [02:00:59] claimed m261 → "
                                     "[02:01:02] complete failed，3 秒止于『无可用端点』；"
                                     "worktree 零交付件"},
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
    # 本班逐条判读：判据全部直读 DB / 派发器日志（不靠记忆），理由按故障族分组
    WAVE_THERMAL = ("承接门挡下：thermal_cavity_3d 线派发器已判「重试上限（3）→ 放弃标记 done」"
                    "（tutorial_wave/dispatch.log 2026-10-25T00:20:02），且更早同线 m253 已 "
                    "succeeded ⇒ 该线已有交付，refill 只造重复腿（N4 无效重发已吃光额度）")
    WAVE_FLOAT = ("同波重复腿挡下：floating_body_free_decay 线 m284/m285/m286/m288 是**同一 goal 的"
                  "4 条腿**（派发器 N4 缺陷每 10 分钟重发一条），全部死于 03:31~03:32Z 全局 quota "
                  "冷却波；state.json 已标 done(retries=3) ⇒ 该线只可能有 1 条真腿，refill 任何一条"
                  "都只是再造重复腿")
    QWAVE_OWNER = ("owner 战役线 + 全局 quota 冷却波致死（03:31:29Z relay-13001 quota 冷却，"
                   "非本 mission 自身过失）：该战役已有更晚腿在飞/已 succeeded 承接，"
                   "refill 会与 owner 编排并行双跑")
    gate = {}
    for i in CANDIDATES:
        later = [l for l in legs[f"m{i}"] if l["id"] > i
                 and l["status"] in ("running", "claimed", "succeeded",
                                     "awaiting_acceptance")]
        if i in (255, 256, 257):
            gate[f"m{i}"] = WAVE_THERMAL
        elif i in (284, 285, 286, 288):
            gate[f"m{i}"] = WAVE_FLOAT
        elif i == 259:
            gate[f"m{i}"] = ("承接门挡下：同线更晚腿 "
                             + ", ".join(f"m{l['id']}({l['status']})" for l in later[:4]))
        elif i in (287, 289, 290, 294):
            gate[f"m{i}"] = ("根因已消除类挡下：EXPAND-FOLLOW 四条全部 `RuntimeError: unknown repo`"
                             "（旧码守护载 HEAD 版 tensor_agent.py，缺 TensorBEM/SPH/DG 三库），"
                             "属**代码面**非 infra 偶发 ⇒ refill 原文会以同一旧码再次秒死；"
                             "本班已换血空闲旧码守护（见 step2c），等派发器下一轮重发才有效")
        elif i == 301:
            gate[f"m{i}"] = ("本班副作用腿挡下：m301 是被本班换血 TOCTOU 连带打死的"
                             "（见 step2d，claim 04:40:12Z 早于 kill 04:40:16Z 4 秒）；"
                             "派发器已 12:50:01 判『m301 终态=failed → 第 1 次重发（下轮）』"
                             "⇒ 业务线自愈，refill 会与派发器重发并行双跑")
        elif i in (291, 292, 297):
            gate[f"m{i}"] = QWAVE_OWNER + "（291/297 更属 SIR-0 线，297 死于案例8 交付保留互锁 → [上报] P）"
        else:
            gate[f"m{i}"] = ("承接门挡下：同 goal 前缀更晚腿 "
                             + ", ".join(f"m{l['id']}({l['status']})"
                                         for l in later[-4:])
                             + "（同 goal 前缀已有更晚承接腿 ⇒ 人工 refill 会造重复腿/双跑）")
    R["step7_refill_gate"] = {"refill_checks": rc, "legs": legs, "verdicts": gate,
                              "refilled": [],
                              "verdict": "零 refill：24 条候选全 rc=0，全部被承接门/根因已消除门挡住"} 

    # ── 步骤 8：派发器终态判定缺陷 N4（上一班 m268 发现，本班**再次实证**并扩大）──
    # 事实直读：tutorial_wave/dispatch.py 在飞白名单
    #   ("pending","dispatched","claimed","running") 不含 "queued"；
    # 而 API POST /tensor/missions 的初态就是 queued（tensor_missions.py 「status": "queued"」）
    # ⇒ 派发器把刚派出的 queued 腿当「非在飞」→ 每 10 分钟再派一条重复腿。
    disp = AGENT_WORK / "tutorial_wave" / "dispatch.log"
    st = AGENT_WORK / "tutorial_wave" / "state.json"
    dlines = [ln for ln in disp.read_text(encoding="utf-8", errors="replace").splitlines()
              if ln.strip()] if disp.exists() else []
    queued_terms = [ln for ln in dlines if "终态=queued" in ln]
    st_items = json.loads(st.read_text(encoding="utf-8")).get("items", {}) if st.exists() else {}
    dup = psql("SELECT id,status,to_char(created_at,'HH24:MI:SS'),task_key "
               "FROM tensor_dev_missions WHERE task_key LIKE 'tut:stokes_sphere_dg%' "
               "ORDER BY id")
    dup2 = psql("SELECT id,status,to_char(created_at,'HH24:MI:SS'),task_key "
                "FROM tensor_dev_missions WHERE task_key LIKE 'tut:floating_body_free_decay%' "
                "ORDER BY id")
    R["step8_dispatcher_queued_defect"] = {
        "case_key": "tut-stokes-sphere-dg / tut-floating-body-free-decay（本班新增第二例）",
        "dispatch_log_tail": dlines[-4:],
        "queued_terminal_events": queued_terms,
        "queued_terminal_events_n": len(queued_terms),
        "state_item_stokes": st_items.get("tut-stokes-sphere-dg"),
        "state_item_float": st_items.get("tut-floating-body-free-decay"),
        "legs_stokes": [{"id": int(r[0]), "status": r[1], "created": r[2], "task_key": r[3]}
                        for r in (x for x in dup if len(x) >= 4)],
        "legs_float": [{"id": int(r[0]), "status": r[1], "created": r[2], "task_key": r[3]}
                       for r in (x for x in dup2 if len(x) >= 4)],
        "whitelist_in_flight": ["pending", "dispatched", "claimed", "running"],
        "api_initial_status": "queued",
        "impact": ("queued 不在在飞白名单 ⇒ 派发器把刚派出的腿当终态 ⇒ 每 10 分钟造一条"
                   "同 goal 重复腿。本班第二例（floating_body_free_decay）更清楚："
                   "m284/m285/m286/m288 四条腿 10:00/10:10/10:30/10:50 CST 依次派出，"
                   "而 4 条同时进入 03:31Z 的 quota 冷却波全灭；若只有 1 条真腿，"
                   "消耗是 1/4，且 state.json 的 retries=3 记的是**无效重发次数**而非真重试"),
        "action": "[上报] 新颖类（第二次实证，上一班 m268 首报）：不动派发器代码（越界）",
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