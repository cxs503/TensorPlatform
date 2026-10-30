#!/usr/bin/env python3
# 动态编制引擎（owner 2026-10-30「实时转换角色/合适的留下不合适的淘汰/冗余策略」）
# 每 2h：48h 滚动评测各模型 → 阈值裁决（淘汰/观察/主力）→ 冗余席位模板改写 fleet.json
# → 只杀空闲且编制变更的 worker（supervisor 30s 重生新模型）。护栏：单轮≤8 席变更、
# 忙绿不杀、淘汰留痕、人工可复位（fleet.json.bak_*）。
import json, os, time, pathlib, subprocess, collections, shutil

FLEET = pathlib.Path("/srv/tensor/agent/fleet.json")
CHANGELOG = pathlib.Path("/srv/tensor/agent/fleet_change.json")
LOG = "/var/log/tp-fleet-optimizer.log"
POLL = 7200
ENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}

# 冗余席位模板：32 卡 = 主力 + 各角色影子（shadow=当前最优模型，主模型劣化时接管）
TEMPLATE = [
    *[("tp-impl-%d" % i, "platform", "qwen3.7-plus") for i in range(1, 9)],
    *[("tp-ver-%d" % i, "verifier", "qwen3.6-plus" if i <= 5 else "qwen3.7-plus") for i in range(1, 9)],
    *[("tp-arch-%d" % i, "strategy", "glm-5.2" if i <= 3 else "qwen3.7-plus") for i in range(1, 5)],
    *[("tp-mod-%d" % i, "models", "deepseek-v4-flash" if i <= 3 else "qwen3.7-plus") for i in range(1, 5)],
    *[("tp-doc-%d" % i, "data", "glm-5.1" if i <= 2 else "qwen3.6-plus") for i in range(1, 5)],
    ("tp-app-1", "applications", "MiniMax-M2.5-AWQ"), ("tp-app-2", "applications", "qwen3.7-plus"),
    ("tp-infra-1", "infrastructure", "qwen3.6-plus"), ("tp-infra-2", "infrastructure", "qwen3.6-plus"),
]

def log(m):
    with open(LOG, "a") as f:
        f.write(time.strftime("[%m-%d %H:%M:%S] ") + m + "\n")

def q(sql):
    return subprocess.run(["psql","-h","127.0.0.1","-U","foundation","-d","tensorplatform","-tAc",sql],
                          capture_output=True, text=True, env=ENV).stdout

def _map_at(when):
    """when 时刻的 agent->model 编制（换血史感知：换血前=prev 表）"""
    fleet = {w["id"]: w["model"] for w in json.loads(FLEET.read_text())}
    if not CHANGELOG.exists():
        return fleet
    ents = sorted(json.loads(CHANGELOG.read_text()), key=lambda x: x["ts"])
    hit = None
    for e in ents:
        if e["ts"] <= when: hit = e
        else: break
    if hit is not None:
        cur = dict(fleet)          # 换血后新增席位按当前表
        cur.update(hit["map"])
        return cur
    return dict(ents[0]["prev"])

def metrics(hours=48):
    since = f"now() - interval '{hours} hours'"
    rows = q(f"SELECT status||'|'||coalesce(claimed_by,'-')||'|'||"
             f"coalesce(result::json->>'tool_calls','')||'|'||"
             f"coalesce(budget::json->>'max_tool_calls','')||'|'||"
             f"coalesce(model,'')||'|'||to_char(created_at,'YYYY-MM-DD HH24:MI:SS') "
             f"FROM tensor_dev_missions "
             f"WHERE status IN ('succeeded','failed') AND finished_at > {since}")
    st = collections.defaultdict(lambda: {"s":0,"f":0,"exh":0,"give":0,"calls":[]})
    for line in rows.strip().split("\n"):
        if not line.strip(): continue
        s, cb, tc, mb, pin, ca = line.split("|")
        m = pin if pin else _map_at(ca).get(cb)
        if not m: continue
        d = st[m]
        if s == "succeeded":
            d["s"] += 1
            if tc.isdigit(): d["calls"].append(int(tc))
        else:
            d["f"] += 1
            if tc.isdigit() and mb.isdigit() and int(tc) >= int(mb): d["exh"] += 1
            else: d["give"] += 1
    return st

def decide(st):
    """返回 {model: verdict} strong/probation/eliminate + 证据"""
    v = {}
    for m, d in st.items():
        n = d["s"] + d["f"]
        if n < 8: v[m] = ("insufficient", n, None); continue
        rate = d["s"] / n
        give_ratio = d["give"] / max(d["f"], 1)
        if (n >= 15 and rate < 0.40) or (n >= 10 and give_ratio > 0.60 and rate < 0.50):
            v[m] = ("eliminate", n, f"成功率{rate:.0%}/弃疗比{give_ratio:.0%}")
        elif rate >= 0.70: v[m] = ("strong", n, f"成功率{rate:.0%}")
        else: v[m] = ("probation", n, f"成功率{rate:.0%}")
    return v

def apply_roster(changes_note=""):
    old = json.loads(FLEET.read_text())
    old_by_id = {w["id"]: w for w in old}
    missing = [i for i,_,_ in TEMPLATE if i not in old_by_id]
    assert not missing, f"模板含不存在席位: {missing}"
    ts = time.strftime("%Y%m%d_%H%M%S")
    shutil.copy(FLEET, str(FLEET) + f".bak_{ts}")
    new = [{"id": i, "role": r, "model": m, "sdaa": old_by_id[i]["sdaa"]} for i, r, m in TEMPLATE]
    tmp = FLEET.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1))
    tmp.replace(FLEET)
    ch = json.loads(CHANGELOG.read_text()) if CHANGELOG.exists() else []
    ch.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "prev": {w["id"]: w["model"] for w in old},
               "map": {i: m for i, _, m in TEMPLATE}})
    CHANGELOG.write_text(json.dumps(ch, ensure_ascii=False))
    changed = [i for i, r, m in TEMPLATE if old_by_id[i]["model"] != m]
    log(f"编制改写 {changes_note}: 变更席位 {len(changed)}: {','.join(changed) if changed else '无'}")
    busy = set(x for x in q("SELECT DISTINCT claimed_by FROM tensor_dev_missions "
                            "WHERE status IN ('running','queued') AND claimed_by IS NOT NULL").split())
    killed = 0
    for p in subprocess.run(["pgrep","-f","tp_worker.py"],capture_output=True,text=True).stdout.split():
        toks = open(f"/proc/{p}/cmdline").read().split("\0")
        aid = toks[toks.index("--agent-id")+1] if "--agent-id" in toks else ""
        if aid in changed and aid not in busy:
            try: subprocess.run(["kill", p]); killed += 1
            except Exception: pass
    log(f"已换血 {killed} 只空闲 worker（忙绿保护：{len(busy)} 在飞）")
    return changed, killed

def main():
    log("动态编制引擎启动")
    while True:
        try:
            st = metrics()
            verdicts = decide(st)
            for m, (verdict, n, ev) in sorted(verdicts.items()):
                log(f"评测 {m}: {verdict} (n={n} {ev or ''})")
            eliminated = {m for m,(v,_,_) in verdicts.items() if v == "eliminate"}
            # 模板中淘汰模型 → 该席位影子化（qwen3.7 接管）
            global TEMPLATE
            new_tpl = []
            for sid, role, model in TEMPLATE:
                new_tpl.append((sid, role, "qwen3.7-plus" if model in eliminated else model))
            if new_tpl != TEMPLATE:
                TEMPLATE = new_tpl
                changed, killed = apply_roster(f"(淘汰 {sorted(elimited)})")
            else:
                changed, killed = [], 0
                log("本轮无编制变更")
        except Exception as e:
            log(f"异常: {e}")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
