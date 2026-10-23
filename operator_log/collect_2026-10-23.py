#!/usr/bin/env python3
"""operator 班次 m220（2026-10-23 第 78 班）巡检读数采集器——一条命令跑完六步 + 审计面。

判据数字零手抄（identity.md 纪律）：所有读数当场从 DB/进程/文件/端点直读，
stdout 即 readings JSON。凭据坑（m210 回退链）：中继复验必须在 bash 终端
`set -a; source review.env; set +a` 后由 curl 完成，本脚本只读环境变量
RELAY_PROBE（形如 "200 0.737 7"），绝不在 python 子进程内 source。

用法（在 worktree 根，即本文件上两级）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/review.env; set +a
  RELAY_PROBE="$(curl -s -o /tmp/relay.body -w '%{http_code} %{time_total}' \
      -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models") \
    $(python3 -c 'import json;print(len(json.load(open("/tmp/relay.body"))["data"]))')"
  python3 operator_log/collect_2026-10-23.py > operator_log/2026-10-23.readings.json
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ECO = Path("/srv/tensor/shared/tensor_eco")
AGENT_WORK = ECO / "agent_work"
REPO = Path(__file__).resolve().parents[1]
PSQL = ["docker", "exec", "open-foundation-postgres", "psql", "-U", "foundation",
        "-d", "open_digital_foundation", "-A", "-F", "|", "-t", "-c"]
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")


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
                       cwd="/srv/tensor/shared/tensor_eco", timeout=180)
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return {"id": mid, "rc": r.returncode,
            "last_line": lines[-1] if lines else ""}


def load_gate(mid: int) -> dict:
    """载荷门：检查点内全部 tool_call arguments 是否 json 可解（案例 6）。"""
    p = AGENT_WORK / f"m{mid}" / "state" / "native_messages.json"
    if not p.exists():
        return {"id": mid, "present": False}
    d = json.loads(p.read_text(encoding="utf-8"))
    msgs = d.get("messages", [])
    n = bad = 0
    for m in msgs:
        for tc in (m.get("tool_calls") or []):
            n += 1
            try:
                json.loads(tc.get("function", {}).get("arguments") or "{}")
            except Exception:  # noqa: BLE001
                bad += 1
    return {"id": mid, "present": True, "msgs": len(msgs),
            "tool_calls": n, "bad_args": bad}


def poses_legs(prefix_from: int, width: int) -> list[dict]:
    """承接门：同 goal 前缀的全部腿（playbook 案例 7 附加门②）。"""
    rows = psql(f"SELECT id,status FROM tensor_dev_missions WHERE "
                f"left(goal,{width})=left((SELECT goal FROM tensor_dev_missions "
                f"WHERE id={prefix_from}),{width}) ORDER BY id")
    return [{"id": int(r[0]), "status": r[1]} for r in rows]


def main() -> int:
    now = time.time()
    R: dict = {"mission": "m220", "shift": "第78班 (派发器 shift#46)",
               "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(now))}

    # ── 步骤 1：在飞心跳鲜度 ──
    rows = psql("SELECT id,status,coalesce(round(extract(epoch FROM "
                "(now()-heartbeat_at)))::text,'-'),repo,"
                "coalesce(result->>'stop_condition',''),coalesce(model,'-') "
                "FROM tensor_dev_missions WHERE status IN "
                "('queued','claimed','running') ORDER BY id")
    inflight = [{"id": int(r[0]), "status": r[1], "hb_age_s": int(r[2]),
                 "repo": r[3], "stop_condition": r[4] or None, "model": r[5]}
                for r in rows]
    R["step1_inflight"] = {
        "missions": inflight,
        "count": len(inflight),
        "max_hb_s": max((m["hb_age_s"] for m in inflight), default=0),
        "stale_over_240s": [m["id"] for m in inflight if m["hb_age_s"] > 240],
        "with_stop_condition": [m["id"] for m in inflight
                                if m["stop_condition"]],
    }

    # ── 步骤 2：守护计数 vs 权威源 ΣN（R18：剧本写 7，实况 16）──
    auth_path = AGENT_WORK / "workers.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    slots = {k: v for k, v in auth.items() if not k.startswith("_")}
    ps = shell("ps -eo args | awk '/[t]ensor_agent\\.py/ && !/bash -lc/ "
               "{print}'").splitlines()
    measured = len(ps)
    per_model = {m: {"authority": n,
                     "measured": sum(1 for ln in ps if m in ln)}
                 for m, n in slots.items()}
    R["step2_guardian"] = {
        "measured_total": measured,
        "authority_sum": sum(slots.values()),
        "authority_source": str(auth_path),
        "per_model": per_model,
        "per_model_all_equal": all(v["authority"] == v["measured"]
                                   for v in per_model.values()),
        "slot_workers": shell("ps -eo args | grep -Ec -- '--slot [2-9]' || true"),
    }

    # ── 步骤 3：中继健康（bash 侧 curl 传入）──
    probe = (os.environ.get("RELAY_PROBE") or "").split()
    R["step3_relay"] = {"http_code": int(probe[0]) if probe else None,
                        "time_total_s": float(probe[1]) if len(probe) > 1 else None,
                        "models": int(probe[2]) if len(probe) > 2 else None}
    # R17 同型坑：worktree 内的 tools/agent/*.json 可能是陈旧副本，
    # 守护进程实际 import 的是主仓 /srv/tensor/shared/tensor_eco/TensorPlatform 下的配置，
    # 故端点池口径一律读**主仓活体文件**，并把 worktree 副本一并留证以便判读偏差。
    ep_live_path = ECO / "TensorPlatform" / "tools/agent" / "llm_endpoints.json"
    ep_copy_path = REPO / "tools" / "agent" / "llm_endpoints.json"
    ep = json.loads(ep_live_path.read_text("utf-8"))
    ep_copy = json.loads(ep_copy_path.read_text("utf-8"))
    R["step3_relay"]["endpoints_n"] = len(ep.get("endpoints", []))
    R["step3_relay"]["cooldown_seconds"] = ep.get("cooldown_seconds")
    R["step3_relay"]["transient_threshold"] = ep.get("transient_threshold")
    R["step3_relay"]["config_source"] = str(ep_live_path)
    R["step3_relay"]["worktree_copy_cooldown_seconds"] = ep_copy.get(
        "cooldown_seconds")
    R["step3_relay"]["worktree_copy_stale"] = (
        ep_copy.get("cooldown_seconds") != ep.get("cooldown_seconds"))
    # 源码事实：report() 恒用 min(cooldown, 600)，故配置 >600 无意义
    gw_src = (ECO / "TensorPlatform/tools/agent/llm_gateway.py").read_text("utf-8")
    R["step3_relay"]["code_caps_600"] = "min(self.cooldown, 600)" in gw_src

    # ── 步骤 4：网关冷却态（含清理前快照与过期判定）──
    st_path = AGENT_WORK / "llm_gateway_state.json"
    state = json.loads(st_path.read_text(encoding="utf-8") or "{}")
    snap = AGENT_WORK / "probe_m220" / "gateway_state_before.json"
    before = json.loads(snap.read_text(encoding="utf-8")) if snap.exists() else {}
    R["step4_cooldown"] = {
        "path": str(st_path),
        "before_snapshot": before,
        "before_snapshot_path": str(snap) if snap.exists() else None,
        "before_delta_s": {k: round(v.get("blocked_until", 0) - now, 1)
                           for k, v in before.items()},
        "before_all_expired": all(v.get("blocked_until", 0) < now
                                  for v in before.values()) if before else True,
        "now_state": state,
        "now_active": {k: v for k, v in state.items()
                       if (v.get("blocked_until") or 0) > now},
        "mtime_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(st_path.stat().st_mtime)),
    }

    # ── 步骤 5：终态 mission 审计面（24h + 承接门 + 三关机械门）──
    t = psql("SELECT count(*) FILTER (WHERE status='failed'),"
             "count(*) FILTER (WHERE status='succeeded'),"
             "count(*) FILTER (WHERE status='failed' AND finished_at >= "
             "date_trunc('day', now())),"
             "coalesce(extract(epoch FROM (now()-max(finished_at)"
             " FILTER (WHERE status='failed')))::int::text,'-') "
             "FROM tensor_dev_missions WHERE finished_at > now()"
             "-interval '24 hours'")
    fails = psql("SELECT id,status,coalesce(result->>'stop_condition',''),"
                 "round(extract(epoch FROM (now()-finished_at)))::text,"
                 "coalesce(result->>'detail',''),coalesce(model,'-') "
                 "FROM tensor_dev_missions WHERE status='failed' AND "
                 "finished_at > now()-interval '24 hours' ORDER BY id")
    aw = psql("SELECT count(*) FROM tensor_dev_missions WHERE "
              "status='awaiting_acceptance'")
    R["step5_terminal"] = {
        "h24_failed": int(t[0][0]),
        "h24_succeeded": int(t[0][1]),
        "today_failed": int(t[0][2]),
        "last_failed_age_s": None if t[0][3] == "-" else int(t[0][3]),
        "failed_missions": [{"id": int(r[0]), "status": r[1],
                             "stop_condition": r[2] or None,
                             "age_s": int(r[3]), "detail": r[4],
                             "model": r[5]} for r in fails],
        "awaiting_acceptance": int(aw[0][0]),
        "refill_checks": [refill_check(i) for i in (208, 211, 216, 217, 218)],
        "load_gates": [load_gate(i) for i in (208, 211, 216, 217, 218)],
        "goal_211_legs": poses_legs(211, 32),
        "goal_216_legs": poses_legs(216, 32),
        "goal_217_legs": poses_legs(217, 32),
    }
    # refill 审计面（本班若有 refill，处置后自证）
    R["step5_terminal"]["refill_audit"] = [
        {"id": int(r[0]), "status": r[1], "max_tool_calls": r[2],
         "max_wall_seconds": r[3], "by": r[4], "added": r[5]}
        for r in psql(
            "SELECT id,status,budget->>'max_tool_calls',"
            "budget->>'max_wall_seconds',"
            "coalesce(result->'refill'->>'by',''),"
            "coalesce(result->'refill'->'added'->>'tool_calls','')||'/'||"
            "coalesce(result->'refill'->'added'->>'wall_seconds','') "
            "FROM tensor_dev_missions WHERE result ? 'refill' "
            "AND id >= 200 ORDER BY id")]

    # ── 步骤 5b：群杀窗口证据（直读守护日志，禁手抄）──
    # 判据：同一冷却事件前后的 "gateway: endpoint ... 冷却" 与 "无可用端点" 行 + 各腿终态时刻
    cluster = []
    for lg in sorted(AGENT_WORK.glob("agent-*.log")):
        txt = lg.read_text(encoding="utf-8", errors="replace")
        for ln in txt.splitlines():
            if "无可用端点" in ln or "冷却 600s" in ln:
                cluster.append(f"{lg.name}|{ln.strip()[:160]}")
    R["step5b_cluster"] = {
        "lines": cluster[-12:],
        "count": len(cluster),
        "cooling_events": sum(1 for x in cluster if "冷却 600s" in x),
        "no_endpoint_lines": sum(1 for x in cluster if "无可用端点" in x),
        # 本波窗口（本地时间 CST+8 21:1x = UTC 13:1x）：群杀当场证据
        "wave_2113_local": [x for x in cluster
                            if any(f"[21:1{n}:" in x for n in range(10))],
    }

    # ── 步骤 6：容器与非健康项 ──
    dstat = shell('DOCKER_HOST=unix:///run/user/1020/docker.sock docker ps '
                  '--format "{{.Names}}|{{.Status}}"')
    cl = [ln for ln in dstat.splitlines() if ln.strip()]
    R["step6_containers"] = {
        "total": len(cl),
        "healthy": sum(1 for ln in cl if "healthy" in ln),
        "unhealthy": [ln for ln in cl if "healthy" not in ln],
        "lines": cl,
    }

    # ── 审计面（审批 / 停车 / PASS_ALL / worktree / 脏树 / gc）──
    ap = psql("SELECT count(*) FILTER (WHERE status='approved'),"
              "count(*) FILTER (WHERE status='rejected'),"
              "count(*) FILTER (WHERE status='pending') "
              "FROM tensor_approval_requests")
    lots = AGENT_WORK / "approver_escalated.json"
    pa = AGENT_WORK / "auto_approver.PASS_ALL"
    alog = AGENT_WORK / "auto_approver.log"
    alines = alog.read_text(encoding="utf-8", errors="replace").splitlines()
    pall = [ln for ln in alines if "PASS_ALL" in ln]
    R["audit"] = {
        "approval": {"approved": int(ap[0][0]), "rejected": int(ap[0][1]),
                     "pending": int(ap[0][2])},
        "parking_lot": (lots.read_text(encoding="utf-8").strip()
                        if lots.exists() else "missing"),
        "pass_all": {
            "exists": pa.exists(),
            "mtime_local": (time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(pa.stat().st_mtime))
                            if pa.exists() else None),
            "rounds": len(pall),
            "last_line": pall[-1] if pall else None,
        },
        "approver_log_lines": len(alines),
        "approver_log_last": alines[-1] if alines else None,
        "worktree_total": int(shell("git worktree list | wc -l")),
        "worktree_tensorplatform": int(
            shell("git worktree list | grep -c '/TensorPlatform' || true")),
        # 主仓口径（worktree 之外的真仓库，注意别与本站 worktree 混读）
        "main_repo_dirty": int(shell(
            f"git -C {ECO}/TensorPlatform status --porcelain | wc -l")),
        "main_repo_dirty_lines": shell(
            f"git -C {ECO}/TensorPlatform status --porcelain").splitlines()[:20],
        "main_repo_branch": shell(f"git -C {ECO}/TensorPlatform branch --show-current"),
        "this_worktree_dirty": int(
            shell("git status --porcelain | wc -l")),
        "this_branch": shell("git branch --show-current"),
        "this_head": shell("git log --oneline -1"),
        "gc_log_last": (AGENT_WORK / "gc.log").read_text(
            encoding="utf-8", errors="replace").strip().splitlines()[-1],
        "operator_shift_log_tail": (AGENT_WORK / "operator_shift.log")
        .read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1],
        "benchmark_dispatch_tail": (AGENT_WORK / "benchmark_closure/"
                                    "benchmark_dispatch.log").read_text(
            encoding="utf-8", errors="replace").strip().splitlines()[-1],
    }
    print(json.dumps(R, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())