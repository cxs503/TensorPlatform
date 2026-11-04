#!/usr/bin/env python3
"""fleet_governor —— 席位治理守护（owner 2026-11-01 批：「可以增加agent，把不干活的
agent淘汰的，新的可以增加，冗余原则」）
每小时一轮体检，机器判据（全部可审计、rate-limit、忙席与保护名单绝不动手）：
  zombie      进程不在但 fleet.json 在册（supervisor 应 30s 拉起；连续 3 轮仍在=记警待查）
  model_dead  模型不在中继 /models（如 glm-5.2 配额死）→ 换健康模型改册+重启
  drift_retag fleet.json 的 model/role ≠ resident 行（配额兜底残留等）→ 闲时重启对齐
  toxic       近 48h 领单 ≥5 且 0 成功 → 淘汰+补员
  idler       近 48h 0 领单且非新席、舰队中位领单 ≥5 → 连续 2 轮 → 淘汰+补员
动作：淘汰=出册+enabled=false+kill（保留 claimed_by 历史）；补员=同类别新席号+健康模型
入册（supervisor ≤30s 拉起）。每轮动作 ≤2（ACTION_CAP）。
保护名单（绝不淘汰绝不换模型）：EVO-1 四臂席 + 论文席。
状态 /root/tp-agent/state-governor.json；日志 /var/log/tp-fleet/fleet_governor.log
"""
import datetime, json, subprocess, time

FLEET = "/root/tp-agent/fleet.json"
STATE = "/root/tp-agent/state-governor.json"
LOG = "/var/log/tp-fleet/fleet_governor.log"
MODELS_CACHE = "/var/log/tp-fleet/relay_models.txt"
CYCLE = 3600
ACTION_CAP = 2
DRIFT_RESTART_CAP = 8  # 进程重启独立帽（闲席重启廉价，supervisor ≤30s 拉回）
WINDOW = "48 hours"
HEALTHY_MODEL = "deepseek-v4-flash"  # owner「DeepSeek 无限用」
PROTECTED = {"tp-gen-1", "tp-gen-2", "tp-gen-3", "tp-gen-4",
             "tp-paper-1", "tp-paper-2", "tp-paper-3", "tp-paper-4"}


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def q(sql):
    r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
                        "-t", "-A", "-F", "\x02", "-c", sql],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return [ln.split("\x02") for ln in r.stdout.splitlines() if ln.strip()]


def jload(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def jsave(p, o):
    json.dump(o, open(p, "w"), ensure_ascii=False, indent=1)


def alive(seat):
    r = subprocess.run(["pgrep", "-f", f"tp_worker.py --agent-id {seat} "],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def proc_model(seat):
    """运行进程 cmdline 里的 --model（行修正不动进程；进程修正才重启）。"""
    r = subprocess.run(["pgrep", "-af", f"tp_worker.py --agent-id {seat} "],
                       capture_output=True, text=True)
    for ln in r.stdout.splitlines():
        if "--model" in ln:
            return ln.split("--model")[1].split()[0]
    return ""


def busy(seat):
    rows = q(f"SELECT count(*) FROM tensor_dev_missions "
             f"WHERE claimed_by='{seat}' AND status IN ('claimed','running')")
    return rows and rows[0][0] != "0"


def relay_models():
    try:
        return {ln.strip() for ln in open(MODELS_CACHE) if ln.strip()}
    except Exception:
        return set()


def next_id(fleet, cls):
    n = 1
    while any(s["id"] == f"tp-{cls}-{n}" for s in fleet):
        n += 1
    return f"tp-{cls}-{n}"


def cls_of(seat_id):
    parts = seat_id.split("-")
    return parts[1] if len(parts) == 3 else "impl"


def write_fleet(fleet):
    tmp = FLEET + ".gov.tmp"
    json.dump(fleet, open(tmp, "w"), ensure_ascii=False, indent=1)
    subprocess.run(["mv", tmp, FLEET], check=True)


def restart_seat(seat):
    subprocess.run(["pkill", "-f", f"tp_worker.py --agent-id {seat} "],
                   capture_output=True)
    log(f"RESTART {seat}（supervisor ≤30s 按新册拉起）")


def sweep():
    st = jload(STATE, {"idler_strikes": {}, "zombie_strikes": {}, "retired": [], "sweeps": 0})
    st["sweeps"] += 1
    fleet = jload(FLEET, [])
    models_ok = relay_models()
    actions = 0
    drift_restarts = 0

    stats = {}
    for seat in fleet:
        sid = seat["id"]
        rows = q(f"SELECT count(*), count(*) FILTER (WHERE status='succeeded') "
                 f"FROM tensor_dev_missions WHERE claimed_by='{sid}' "
                 f"AND created_at > now() - interval '{WINDOW}'")
        claimed, succ = (rows[0] if rows else ["0", "0"])
        rows = q(f"SELECT count(*) FROM factory_resident_workers WHERE worker_id='{sid}' "
                 f"AND started_at > now() - interval '{WINDOW}'")
        fresh = rows and rows[0][0] != "0"
        rows = q(f"SELECT coalesce(model,''), coalesce(role,'') FROM factory_resident_workers "
                 f"WHERE worker_id='{sid}'")
        row_model, row_role = (rows[0] if rows else ["", ""])
        stats[sid] = dict(claimed=int(claimed), succ=int(succ), fresh=fresh,
                          row_model=row_model, row_role=row_role,
                          fleet_model=seat["model"], fleet_role=seat.get("role", ""),
                          proc=alive(sid), is_busy=busy(sid))
    claims = sorted(v["claimed"] for v in stats.values())
    median = claims[len(claims) // 2] if claims else 0

    for sid, s in stats.items():
        tag = f"[{sid}] claimed48h={s['claimed']} succ={s['succ']} proc={'Y' if s['proc'] else 'N'}"
        # 1) zombie：册上有名无进程
        if not s["proc"]:
            st["zombie_strikes"][sid] = st["zombie_strikes"].get(sid, 0) + 1
            if st["zombie_strikes"][sid] >= 3:
                log(f"ZOMBIE-PERSIST {tag} —— 拉起连续 3 轮失败，记警待查")
            else:
                log(f"ZOMBIE {tag}（supervisor 应 30s 内拉起，下轮复核）")
            continue
        if sid in PROTECTED or s["is_busy"]:
            continue
        want_model = s["fleet_model"] if (not models_ok or s["fleet_model"] in models_ok) \
            else HEALTHY_MODEL
        # 2) model_dead：模型不在中继清单（清单可用时才判）→ 换健康模型改册
        if models_ok and s["fleet_model"] not in models_ok:
            for seat in fleet:
                if seat["id"] == sid:
                    seat["model"] = HEALTHY_MODEL
            write_fleet(fleet)
            fleet = jload(FLEET, [])
            want_model = HEALTHY_MODEL
            log(f"MODEL-DEAD {tag} → {HEALTHY_MODEL} 已改册")
        # 3a) 行漂移：行≠册 → SQL 直改行（worker 的启动自愈实证不生效；行级 claim
        #     修正=owner 早前批类目）。元数据零扰动，不占动作帽。
        if s["row_model"] != want_model or s["row_role"] != s["fleet_role"]:
            q(f"UPDATE factory_resident_workers SET model='{want_model}', "
              f"role='{s['fleet_role']}' WHERE worker_id='{sid}'")
            log(f"DRIFT-ROW-FIX {tag} row=({s['row_model']}/{s['row_role']}) "
                f"→ ({want_model}/{s['fleet_role']})")
        # 3b) 进程漂移：进程 cmdline 模型≠册 → 闲时重启（独立帽）
        pm = proc_model(sid)
        if pm and pm != want_model:
            if drift_restarts < DRIFT_RESTART_CAP:
                restart_seat(sid)
                log(f"DRIFT-PROC-RETAG {tag} proc={pm} want={want_model}，重启对齐")
                drift_restarts += 1
            else:
                log(f"DRIFT-PROC-PENDING {tag} proc={pm}（重启帽满）")
            continue
        # 4) toxic：干了很多、全砸
        if s["claimed"] >= 5 and s["succ"] == 0:
            if actions >= ACTION_CAP:
                log(f"TOXIC-PENDING {tag}（动作帽满，下轮处理）")
                continue
            fleet = jload(FLEET, [])
            fleet = [x for x in fleet if x["id"] != sid]
            new = {"id": next_id(fleet, cls_of(sid)), "role": s["fleet_role"] or "platform",
                   "model": want_model}
            fleet.append(new)
            write_fleet(fleet)
            q(f"UPDATE factory_resident_workers SET enabled=false WHERE worker_id='{sid}'")
            restart_seat(sid)
            st["retired"].append({"seat": sid, "reason": "toxic",
                                  "at": datetime.datetime.now().isoformat(timespec="seconds"),
                                  "replacement": new["id"]})
            log(f"TOXIC-RETIRE {tag} → 补员 {new['id']}({new['model']})")
            actions += 1
            continue
        # 5) idler：不干活（0 领单、非新席、舰队中位 ≥5）连续 2 轮 → 淘汰+补员
        if s["claimed"] == 0 and not s["fresh"] and median >= 5:
            st["idler_strikes"][sid] = st["idler_strikes"].get(sid, 0) + 1
            if st["idler_strikes"][sid] >= 2:
                if actions >= ACTION_CAP:
                    log(f"IDLER-PENDING {tag} strike={st['idler_strikes'][sid]}（动作帽满）")
                    continue
                fleet = jload(FLEET, [])
                fleet = [x for x in fleet if x["id"] != sid]
                new = {"id": next_id(fleet, cls_of(sid)), "role": s["fleet_role"] or "platform",
                       "model": want_model}
                fleet.append(new)
                write_fleet(fleet)
                q(f"UPDATE factory_resident_workers SET enabled=false WHERE worker_id='{sid}'")
                restart_seat(sid)
                st["retired"].append({"seat": sid, "reason": "idler",
                                      "at": datetime.datetime.now().isoformat(timespec="seconds"),
                                      "replacement": new["id"]})
                log(f"IDLER-RETIRE {tag}（中位={median}）→ 补员 {new['id']}({new['model']})")
                actions += 1
            else:
                log(f"IDLER-STRIKE {tag}（中位={median}，再一轮即淘汰）")
        else:
            st["idler_strikes"].pop(sid, None)

    jsave(STATE, st)
    log(f"=== sweep #{st['sweeps']} 完成：{len(fleet)} 席，动作 {actions}/{ACTION_CAP}，"
        f"中位领单 {median} ===")


if __name__ == "__main__":
    log("=== fleet_governor 启动（owner 2026-11-01：淘汰不干活+补员+冗余原则）===")
    while True:
        try:
            sweep()
        except Exception as e:
            log(f"SWEEP-ERROR {type(e).__name__}: {e}")
        time.sleep(CYCLE)
