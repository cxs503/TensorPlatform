#!/usr/bin/env python3
"""coordinator_shadow.py — 继承者A·影子协调官 v0（owner 2026-11-07 继承令）。
每 30 分钟：收平台脉搏（时钟快照+dispatcher WARN+零流器官）→ LLM 判读「若我是当值协调者会做什么」
→ 结构化落 coordinator_shadow 表（append-only）。影子纪律：只诊断不动手；IN27-H7 评估流以本表为源算 SR1 分歧率。"""
import json, os, subprocess, time, urllib.request, urllib.error

CFGP = "/root/tp-agent/agent.env"
for ln in open(CFGP):
    if "=" in ln and not ln.startswith("#"):
        k, _, v = ln.strip().partition("=")
        os.environ.setdefault(k, v)
LLM = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "glm-5.2")
LOG = "/var/log/tp-fleet/coordinator_shadow.log"

def log(m):
    with open(LOG, "a") as f:
        f.write(time.strftime("%F %T ") + m + "\n")

def q(sql):
    r = subprocess.run(["runuser","-u","postgres","--","psql","-d","tensorplatform","-t","-A","-F","\x02","-c",sql],
                       capture_output=True,text=True,timeout=30)
    return [ln.split("\x02") for ln in r.stdout.splitlines() if ln.strip()]

def gather_pulse():
    pulse = {}
    r = q("SELECT ts::text, payload->'s1_supply', payload->'s2_campaign', payload->'s4_fleet_pulse', payload->'s5_outcomes', payload->'s7_voice' FROM chronicle_snapshots ORDER BY ts DESC LIMIT 1")
    if r and r[0][0]:
        pulse["clock_ts"] = r[0][0]
        for i, k in enumerate(["s1_supply","s2_campaign","s4_fleet","s5_outcomes","s7_voice"], 1):
            try: pulse[k] = json.loads(r[0][i]) if r[0][i] else "absent"
            except Exception: pulse[k] = (r[0][i] or "")[:200]
    pulse["nodes_pending_active_plans"] = q("SELECT count(*) FROM campaign_nodes WHERE status='pending'")[0][0]
    pulse["nodes_inflight"] = q("SELECT count(*) FROM campaign_nodes WHERE status='in-flight'")[0][0]
    pulse["credit_backlog"] = q("SELECT count(*) FROM outcome_evaluations e WHERE e.status IN ('confirmed','rejected') AND NOT EXISTS (SELECT 1 FROM tensor_credit_ledger l WHERE l.reason LIKE 'eval:'||e.evaluation_id||'|%')")[0][0]
    pulse["capability_deltas_24h"] = q("SELECT count(*) FROM tensor_capability_deltas WHERE created_at > now()-interval '24 hours'")[0][0]
    pulse["awaiting_pr_merge"] = q("SELECT count(*) FROM tensor_dev_missions WHERE status='awaiting_pr_merge'")[0][0]
    pulse["queued_stuck_2h"] = q("SELECT count(*) FROM tensor_dev_missions WHERE status='queued' AND created_at < now()-interval '2 hours'")[0][0]
    try:
        with open("/var/log/tp-fleet/campaign_dispatcher.log") as f:
            warns = [l.strip() for l in f.readlines()[-400:] if "WARN" in l or "ERROR" in l][-12:]
        pulse["dispatcher_warns_tail"] = warns
    except Exception:
        pulse["dispatcher_warns_tail"] = "log-unreadable"
    return pulse

SYSTEM = ("你是 TensorPlatform 的影子协调官（继承者A，owner 2026-11-07 继承令）。你观察平台脉搏，"
          "判断「若我是当值协调者，现在会做什么」。纪律：只诊断不动手；不夸大不捏造；severity 按对平台产出的影响定。"
          '输出严格 JSON（不要 markdown 围栏）：{"findings":[{"organ":"","symptom":"","severity":"high|mid|low"}],'
          '"would_do":[{"action":"monitor|file_mission|escalate","target":"","reason":""}],"urgent":bool,"rationale":"一句话"}。'
          "器官字典：s1_supply 供应/s2_campaign 战役/s4_fleet 舰队/s5_outcomes 评价/s7_voice 声音/credit_backlog 信用积压/"
          "capability_deltas 能力增量/awaiting_pr_merge 待并/dispatcher_warns 调度异警/queued_stuck 积压。")

def think(pulse):
    body = {"model": MODEL, "messages": [{"role":"system","content":SYSTEM},
            {"role":"user","content":"平台脉搏（JSON）：\n" + json.dumps(pulse, ensure_ascii=False)[:12000]}],
            "max_tokens": 2000, "temperature": 0.2}
    req = urllib.request.Request(LLM + "/chat/completions", data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + LLM_KEY)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=300) as resp:
        txt = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
    txt = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except Exception:
        return {"raw": txt[:3000], "parse_error": True}

def ensure_table():
    q("CREATE TABLE IF NOT EXISTS coordinator_shadow (id bigint, ts timestamptz default now(), model text, urgent boolean, verdict jsonb, pulse_digest jsonb)")

def beat():
    pulse = gather_pulse()
    v = think(pulse)
    ensure_table()
    verdict = json.dumps(v, ensure_ascii=False).replace("'", "''")
    digest = {k: pulse.get(k) for k in ("clock_ts","credit_backlog","capability_deltas_24h","queued_stuck_2h","awaiting_pr_merge","nodes_pending_active_plans")}
    dj = json.dumps(digest, ensure_ascii=False).replace("'", "''")
    urgent = "true" if v.get("urgent") else "false"
    q(f"INSERT INTO coordinator_shadow (id, model, urgent, verdict, pulse_digest) "
      f"VALUES ((SELECT coalesce(max(id),0)+1 FROM coordinator_shadow), '{MODEL}', {urgent}, '{verdict}'::jsonb, '{dj}'::jsonb)")
    log(f"影子拍：urgent={urgent} findings={len(v.get('findings',[]))} would_do={len(v.get('would_do',[]))}"
        f"{' PARSE_FAIL' if v.get('parse_error') else ''} | {(v.get('rationale') or v.get('raw',''))[:120]}")

if __name__ == "__main__":
    log(f"=== 继承者A 影子协调官 v0 启动（model={MODEL}；30min 一拍；只诊断不动手）===")
    while True:
        try:
            beat()
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {str(e)[:200]}")
        time.sleep(1800)
