#!/usr/bin/env python3
"""propose_channel_guard —— 自主提案通道守护（渐退令：机制替身独立于人类会话与 AI 协调者会话）

背景（IN20-R4 根因，见 docs/IN20_R4_propose_channel_rootcause.md）：
  campaign_dispatcher 的提案波把 3 个异构提案者钉在固定模型名上
  （PROPOSER_MODELS = qwen3.7-plus / glm-5.1 / deepseek-v4-flash）。
  领取端（backend/api/tensor_missions.py claim）的模型过滤是
  `AND (model = :claim_model OR model IS NULL)` —— **钉住的模型必须有一个活体
  worker 进程以同名 model 去 claim，否则该单永远 queued**。
  glm-5.1 的在册 worker 心跳早已消失（enabled=true 但无 daemon），于是 2026-11-07
  00:35 起 9 张 propose-* 单（全是 glm-5.1 槽位）无人领；而 dispatcher 的
  `all(p.status in TERMINAL)` 又把这些非终态单当"还在思考"，仲裁者永不点火
  → 自主提案（创新入口）整条链路堵死。

本守护做什么（三件，全部 session 无关，容器 daemon 可常驻）：
  S1 领取路径守护：扫 queued 的 propose-*/arbiter-* 单，若其钉住的模型在当前
     worker 名册里没有活体心跳（> stale_secs 未心跳/未启用）且已等超过 stall_secs，
     则按策略处置——`withdraw`（带理由撤销，冗余提案位）或 `repin`（改钉活体模型
     让常规 claim 通道把它领走）。
  S2 仲裁推进诊断：逐 plan 汇总 autonomy.proposers 的终态/非终态，指出"谁在堵
     仲裁"（≥2 成功即够仲裁），供 dispatcher 与人工审计对账。
  S3 审计+回滚：每次动作写 JSONL（before/after/理由/证据 SQL），另写 rollback
     文件；`--rollback` 按审计把单恢复 queued（回滚路径必须在，不可只进不出）。

用法：
  python3 propose_channel_guard.py                 # dry-run（默认，只打印判定）
  python3 propose_channel_guard.py --apply         # 执行处置（默认 policy=withdraw）
  python3 propose_channel_guard.py --apply --policy repin   # 改钉活体模型让 worker 领走
  python3 propose_channel_guard.py --rollback artifacts/.../rollback-20261107T*.json
  python3 propose_channel_guard.py --loop --interval 300    # daemon 常驻（防复发）

设计纪律：判定逻辑全部是纯函数（parse_ts/live_models/classify/plan_diagnostics），
DB 只在 I/O 层，pytest 可脱库验证；负例（活体模型在册 → 不许误伤）在测试里钉死。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

# 终态集合：与 campaign_dispatcher.TERMINAL 保持一致（suspended 必须算终态：
# 2026-11-01 起 p9-w1-2 停在 suspended，dispatcher 不认 → plan 9 堵了 6 天）
TERMINAL = frozenset({"succeeded", "failed", "stopped", "revoked", "suspended", "orphan"})

STALL_SECS = 1800        # 等 30min 仍无活体可领 → 可疑（正常 claim 是秒级）
HARD_SECS = 6 * 3600     # 钉活体模型但 6h 没人领 → 也算异常
STALE_SECS = 600         # worker 心跳新鲜度阈值（与 claim 端 reap 240s 同量级，取宽松）
TASK_PREFIXES = ("propose-", "arbiter-")   # 本守护只碰自主提案通道的单
PSQL_BASE = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
             "-t", "-A", "-F", "\x02", "-c"]


# ────────────────────────────── 纯函数层（可单测） ──────────────────────────────
def parse_ts(v):
    """把 DB 时间（epoch float / 'YYYY-MM-DD HH:MM:SS.ffffff+00' / ISO）化成 epoch 秒。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    t = s.replace("Z", "+00:00")
    for fmt in (None,):
        try:
            dt = datetime.fromisoformat(t)
        except ValueError:
            try:
                dt = datetime.strptime(s.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
            return dt.replace(tzinfo=timezone.utc).timestamp()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()


def live_models(worker_rows, now, stale_secs=STALE_SECS):
    """在册且心跳新鲜的 worker 模型集合（领取路径的"能领者"全集）。

    worker_rows: [{'model','enabled','heartbeat_at','state'}]（也接受 (model, enabled, hb) 三元组）
    规则：enabled 且 heartbeat_at 在 stale_secs 内且 state 不是停机态。
    """
    out = set()
    for r in worker_rows or []:
        if isinstance(r, dict):
            model, enabled = r.get("model"), r.get("enabled")
            hb, state = r.get("heartbeat_at"), (r.get("state") or "")
        else:
            model, enabled, hb = (list(r) + [None, None, None])[:3]
            state = ""
        if not model or enabled in (False, "f", "false", "False", 0):
            continue
        if str(state).lower() in ("stopped", "dead", "offline", "exited"):
            continue
        t = parse_ts(hb)
        if t is None or now - t > stale_secs:
            continue
        out.add(str(model))
    return out


def activity_models(rows, now, window=3600):
    """行为级活体证据：该 model 名下有被 claim 过的单且心跳在 window 内。

    为什么不能只看 factory_resident_workers.enabled+heartbeat：实测（2026-11-07）
    tp-gen-4 的名册行 model 被改成 glm-5.1、心跳紧跟，但进程实际跑
    deepseek-v4-flash —— **名册行心跳不证明这个 model 有 daemon**。
    而 glm-5.1 名下单最近一次 claim 心跳停在 2026-11-01 04:35（5d23h 前），
    与"9 张单等 3 小时无人领"互为佐证：领取路径对该模型是死的。
    rows: [{'model','last_hb'}]
    """
    out = set()
    for r in rows or []:
        model = r.get("model") if isinstance(r, dict) else (list(r) + [None])[0]
        hb = r.get("last_hb") if isinstance(r, dict) else (list(r) + [None, None])[1]
        if not model:
            continue
        t = parse_ts(hb)
        if t is not None and now - t <= window:
            out.add(str(model))
    return out


def is_proposal_channel(task_key):
    return bool(task_key) and str(task_key).startswith(TASK_PREFIXES)


def classify(missions, live, now, stall_secs=STALL_SECS, hard_secs=HARD_SECS,
             repin_target=None, policy="withdraw", active=None):
    """判定每张 queued 提案单的处置。返回动作列表（keep 也返回，便于审计留痕）。

    missions: [{'id','task_key','model','status','created_at'}]
    动作 verb ∈ {'keep','withdraw','repin'}：
      - keep     : 模型有活体领取者且未超硬阈值（等正常 claim，不误伤）
      - withdraw : 模型无活体领取者且已超 stall_secs；或活体但超 hard_secs
      - repin    : policy=repin 且模型无活体（改钉 repin_target 让 worker 领走）
    """
    active = set(live) if active is None else set(active)
    acts = []
    for m in missions or []:
        st = (m.get("status") or "")
        tk = m.get("task_key")
        if not is_proposal_channel(tk):
            continue
        if st in ("suspended", "orphan"):
            # suspended 在旧 dispatcher 的 TERMINAL 之外 → 与 queued 一样把仲裁堵死
            # （p9-w1-2 自 2026-11-01 停此状态，plan 9 堵 6 天）。同样按龄撤销，可回滚。
            created = parse_ts(m.get("created_at"))
            age = (now - created) if created is not None else 0.0
            if age < stall_secs:
                continue
            acts.append({"mission_id": int(m["id"]), "task_key": tk,
                         "model": m.get("model"), "age_secs": round(age, 1),
                         "claimable": False, "repin_target": None,
                         "prev_status": st, "verb": "withdraw",
                         "reason": (f"non_terminal_status({st})_blocks_arbitration"
                                    f"（旧 TERMINAL 集不含 {st}，仲裁者永不点火，age={int(age)}s）")})
            continue
        if st != "queued":
            continue
        created = parse_ts(m.get("created_at"))
        age = (now - created) if created is not None else 0.0
        model = m.get("model")
        # 可领取 = 名册有活体**且**有行为证据（真在领这个 model 的单）
        claimable = (model in live and model in active) if model else True
        act = {"mission_id": int(m["id"]), "task_key": tk, "model": model,
               "age_secs": round(age, 1), "claimable": claimable,
               "repin_target": None, "reason": ""}
        if claimable and age <= hard_secs:
            act["verb"] = "keep"
            act["reason"] = "claimable_or_unpinned"
        elif claimable and age > hard_secs:
            act["verb"] = "withdraw"
            act["reason"] = (f"live_model_no_claim_{int(age)}s"
                             f"（模型 {model} 有活体 worker 但 {int(hard_secs)}s 未领）")
        else:
            if policy == "repin" and repin_target:
                act["verb"] = "repin"
                act["repin_target"] = repin_target
                act["reason"] = (f"unclaimable_model({model} 无活体 worker>={int(stall_secs)}s)"
                                 f"→ 改钉 {repin_target}")
            else:
                act["verb"] = "withdraw"
                why = ("no_live_roster_row" if model not in live
                       else "roster_says_live_but_no_claim_activity")
                act["reason"] = (f"unclaimable_model/{why}(model={model} 钉死后无人领取 "
                                 f">={int(stall_secs)}s，age={int(age)}s)；任务从未被 claim，非 honest-FAIL")
        if act["verb"] != "keep" and age < stall_secs:
            # 宽限期：刚入池的单不碰（防与正常 claim 抢跑）
            act["verb"] = "keep"
            act["reason"] = f"grace(<{int(stall_secs)}s)"
        acts.append(act)
    return acts


def plan_diagnostics(plans, missions_by_id, now, stall_secs=STALL_SECS):
    """逐 plan 指出"谁堵住仲裁"。plans: [{'id','status','proposers':[ids],'arbiter':id|None}]。"""
    out = []
    for p in plans or []:
        props = []
        for mid in p.get("proposers") or []:
            m = missions_by_id.get(int(mid)) or {}
            props.append({"id": int(mid), "status": m.get("status", "missing"),
                          "model": m.get("model"), "task_key": m.get("task_key")})
        succ = sum(1 for x in props if x["status"] == "succeeded")
        blocking = [x for x in props if x["status"] not in TERMINAL]
        out.append({"plan": p.get("id"), "status": p.get("status"),
                    "proposers": props, "succeeded": succ,
                    "blocking": blocking, "arbiter": p.get("arbiter"),
                    # 够仲裁=≥2 份成功提案（dispatcher 的判据），堵点全在"非终态"上
                    "advance_ready": succ >= 2 and not blocking and not p.get("arbiter")})
    return out


def withdraw_sql(act, now_iso, guard="propose_channel_guard"):
    """带理由撤销的 SQL（只对 queued 生效，幂等；result.guard 独立键便于回滚）。"""
    prev = act.get("prev_status") or "queued"
    cond = "unclaimable_model" if prev == "queued" else "arbiter_starved"
    guard_obj = json.dumps({"action": "withdraw", "reason": act["reason"],
                            "model": act.get("model"), "prev_status": prev,
                            "ts": now_iso, "by": guard}, ensure_ascii=False)
    return ("UPDATE tensor_dev_missions SET status='stopped', finished_at=now(), "
            "result = coalesce(result,'{}'::jsonb) || jsonb_build_object("
            f"'stop_condition',{lit(cond)},'guard',{lit(guard_obj)}::jsonb) "
            f"WHERE id={int(act['mission_id'])} AND status IN ('queued','suspended') RETURNING id")


def repin_sql(act, now_iso, guard="propose_channel_guard"):
    """改钉活体模型的 SQL（原模型记进 result.guard.prev_model，回滚可取回）。"""
    guard_obj = json.dumps({"action": "repin", "prev_model": act.get("model"),
                            "new_model": act["repin_target"], "reason": act["reason"],
                            "ts": now_iso, "by": guard}, ensure_ascii=False)
    return ("UPDATE tensor_dev_missions SET model=" + lit(act["repin_target"]) +
            ", result = coalesce(result,'{}'::jsonb) || jsonb_build_object("
            f"'guard',{lit(guard_obj)}::jsonb) "
            f"WHERE id={int(act['mission_id'])} AND status='queued' RETURNING id")


def rollback_sql(record):
    """按审计回滚：撤销的单恢复 queued；改钉的单恢复原模型；并摘掉 result.guard。"""
    mid = int(record["mission_id"])
    if record.get("verb") == "repin" and record.get("model"):
        return ("UPDATE tensor_dev_missions SET model=" + lit(record["model"]) +
                ", result = coalesce(result,'{}'::jsonb) - 'guard' "
                f"WHERE id={mid} AND status='queued' RETURNING id")
    prev = record.get("prev_status") or "queued"
    return ("UPDATE tensor_dev_missions SET status=" + lit(prev) + ", finished_at=NULL, "
            "result = coalesce(result,'{}'::jsonb) - 'guard' - 'stop_condition' "
            f"WHERE id={mid} AND status='stopped' RETURNING id")


def lit(s):
    """SQL 字面量转义（审计文本可能含引号）。"""
    return "'" + str(s).replace("'", "''") + "'"


# ────────────────────────────── I/O 层（DB / 审计） ──────────────────────────────
def psql(sql, timeout=60):
    """以 postgres 身份执行（peer 认证，与 campaign_dispatcher 同路子）。"""
    p = subprocess.run(PSQL_BASE + [sql], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"psql rc={p.returncode}: {p.stderr.strip()[:300]}")
    return [ln.split("\x02") for ln in p.stdout.splitlines() if ln.strip()]


def fetch_workers():
    rows = psql("SELECT model, enabled, coalesce(heartbeat_at::text,''), coalesce(state,'') "
                "FROM factory_resident_workers")
    return [{"model": r[0], "enabled": r[1], "heartbeat_at": r[2], "state": r[3]} for r in rows]


def fetch_missions(limit=200):
    rows = psql("SELECT id, coalesce(task_key,''), coalesce(model,''), status, "
                "coalesce(created_at::text,'') FROM tensor_dev_missions "
                "WHERE status IN ('queued','claimed','running','suspended') "
                "AND (task_key LIKE 'propose-%' OR task_key LIKE 'arbiter-%') "
                f"ORDER BY id DESC LIMIT {int(limit)}")
    return [{"id": int(r[0]), "task_key": r[1], "model": r[2], "status": r[3],
             "created_at": r[4]} for r in rows]


def fetch_activity():
    """各 model 名下的 claim 活动证据（最近一次心跳）。"""
    rows = psql("SELECT coalesce(model,''), coalesce(max(heartbeat_at)::text,'') "
                "FROM tensor_dev_missions WHERE claimed_by IS NOT NULL "
                "AND coalesce(model,'') <> '' GROUP BY 1")
    return [{"model": r[0], "last_hb": r[1]} for r in rows]


def fetch_plans():
    """从 campaign_plans.metadata.autonomy 取提案波状态（dispatcher 的权威状态源）。"""
    rows = psql("SELECT id, coalesce(status,''), coalesce(metadata::text,'{}') "
                "FROM campaign_plans ORDER BY id DESC LIMIT 200")
    out = []
    for rid, st, meta in rows:
        try:
            aut = (json.loads(meta or "{}") or {}).get("autonomy") or {}
        except Exception:
            aut = {}
        if not aut.get("proposers"):
            continue
        out.append({"id": int(rid), "status": st, "proposers": aut.get("proposers"),
                    "arbiter": aut.get("arbiter")})
    return out


def fetch_missions_by_id(ids):
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    rows = psql("SELECT id, status, coalesce(model,''), coalesce(task_key,'') "
                f"FROM tensor_dev_missions WHERE id IN ({','.join(map(str, ids))})")
    return {int(r[0]): {"id": int(r[0]), "status": r[1], "model": r[2], "task_key": r[3]}
            for r in rows}


def apply_actions(actions, audit_dir, policy="withdraw", dry_run=True):
    """执行处置 + 写审计/回滚文件。返回 (changed, shift_path)。"""
    todo = [a for a in actions if a["verb"] in ("withdraw", "repin")]
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_dir = pathlib.Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    changed = []
    for act in todo:
        sql = withdraw_sql(act, now_iso) if act["verb"] == "withdraw" else repin_sql(act, now_iso)
        if dry_run:
            changed.append({**act, "applied": False, "sql": sql})
            continue
        hit = psql(sql)
        rec = {**act, "applied": bool(hit), "ts": now_iso, "sql": sql}
        changed.append(rec)
        with open(audit_dir / f"audit-{stamp[:8]}.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    shift_path = audit_dir / f"rollback-{stamp}.json"
    if not dry_run:
        shift_path.write_text(json.dumps(
            {"ts": now_iso, "policy": policy,
             "records": [{"mission_id": c["mission_id"], "verb": c["verb"],
                          "model": c.get("model"), "repin_target": c.get("repin_target"),
                          "prev_status": c.get("prev_status") or "queued",
                          "task_key": c.get("task_key"), "reason": c.get("reason")}
                         for c in changed if c.get("applied")]},
            ensure_ascii=False, indent=1))
    return changed, shift_path


def apply_rollback(records, audit_dir):
    done = []
    for r in records:
        try:
            hit = psql(rollback_sql(r))
            done.append({"mission_id": r["mission_id"], "verb": r.get("verb"),
                         "restored": bool(hit)})
        except Exception as e:  # 单条失败不掀桌：如实记
            done.append({"mission_id": r["mission_id"], "error": str(e)[:200]})
    d = pathlib.Path(audit_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (d / f"rollback-applied-{stamp}.json").write_text(
        json.dumps({"ts": stamp, "records": done}, ensure_ascii=False, indent=1))
    return done


def run_once(audit_dir, policy="withdraw", dry_run=True, now=None, log=print):
    """一轮守护：采集 → 判定 → 处置 → 诊断。返回摘要 dict（可落报告）。"""
    now = time.time() if now is None else now
    workers = fetch_workers()
    live = live_models(workers, now)
    active = activity_models(fetch_activity(), now)
    missions = fetch_missions()
    # 改钉目标只认"名册活体 ∩ 行为证据"（名册撒谎的模型不许再被钉）
    repin_pool = sorted(live & active) or sorted(active)
    repin_target = repin_pool[0] if repin_pool else None
    acts = classify(missions, live, now, repin_target=repin_target, policy=policy,
                    active=active)
    changed, shift = apply_actions(acts, audit_dir, policy=policy, dry_run=dry_run)
    plans = fetch_plans()
    all_ids = [m for p in plans for m in (p.get("proposers") or [])]
    diag = plan_diagnostics(plans, fetch_missions_by_id(all_ids), now)
    summary = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "live_models": sorted(live), "claim_activity_models": sorted(active),
               "roster_without_activity": sorted(live - active), "policy": policy,
               "scanned": len(missions), "actions": acts,
               "changed": changed, "rollback": str(shift),
               "plans_blocked": [d for d in diag if d["blocking"]],
               "plans_advance_ready": [d for d in diag if d["advance_ready"]]}
    log(f"[guard] scanned={len(missions)} live_models={sorted(live)} "
        f"roster_without_activity={sorted(live - active)} "
        f"to_change={sum(1 for a in acts if a['verb'] != 'keep')} "
        f"blocked_plans={len(summary['plans_blocked'])} "
        f"advance_ready={len(summary['plans_advance_ready'])}")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="自主提案通道守护（S1 领取路径/S2 仲裁推进/S3 审计回滚）")
    ap.add_argument("--apply", action="store_true", help="执行处置（缺省 dry-run）")
    ap.add_argument("--policy", choices=["withdraw", "repin"], default="withdraw")
    ap.add_argument("--audit-dir", default="/var/log/tp-guard/propose_channel_guard")
    ap.add_argument("--rollback", help="回滚文件（rollback-*.json）")
    ap.add_argument("--loop", action="store_true", help="daemon 常驻（session 无关）")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--summary-json", help="把本轮摘要写到该文件（报告取数用）")
    a = ap.parse_args(argv)

    def log(m):
        print(time.strftime("[%F %T] ") + str(m), flush=True)

    if a.rollback:
        recs = json.loads(pathlib.Path(a.rollback).read_text()).get("records", [])
        for r in apply_rollback(recs, a.audit_dir):
            log(f"rollback {r}")
        return 0
    while True:
        try:
            s = run_once(a.audit_dir, policy=a.policy, dry_run=not a.apply, log=log)
            if a.summary_json:
                pathlib.Path(a.summary_json).write_text(json.dumps(s, ensure_ascii=False, indent=1))
            for act in s["actions"]:
                if act["verb"] != "keep":
                    log(f"  {act['verb']} m{act['mission_id']} [{act['task_key']}] "
                        f"model={act['model']} → {act['reason']}")
        except Exception as e:
            log(f"[guard] 轮异常 {type(e).__name__}: {e}")
        if not a.loop:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())