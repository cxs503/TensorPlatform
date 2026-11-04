#!/usr/bin/env python3
# 信用账本 v1 (owner 2026-10-31 批准自治机制第一月套餐)
# 每 30min: 按 worker 汇总 7d 成功率/累计成功/QR 复核归属 -> credit_ledger.json
# v1 口径: 只计量+发布(议事会上下文引用); 预算/领取优先级接入=v2(需 claim 层改造)
import json, os, pathlib, subprocess, time
OUT = pathlib.Path("/root/tp-agent/credit_ledger.json")
LOG = "/var/log/tp-credit-ledger.log"
POLL = 1800
PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}

def log(m): open(LOG, "a").write(time.strftime("[%F %T] ") + m + "\n")

def q(sql):
    r = subprocess.run(["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc", sql],
                       capture_output=True, text=True, env=PGENV)
    return r.stdout.strip()

def compute():
    ws = {}
    def w(name):
        if name not in ws:
            role = "-"
            for r in ("impl", "ver", "arch", "ops"):
                if name.startswith("tp-" + r + "-"): role = r
            ws[name] = {"worker": name, "role": role, "succ7": 0, "fail7": 0,
                        "succ_total": 0, "qr_claimed": 0, "qr_succ": 0}
        return ws[name]
    rows = q("SELECT claimed_by, status, count(*) FROM tensor_dev_missions "
             "WHERE claimed_by IS NOT NULL AND status IN ('succeeded','failed') "
             "AND finished_at > now()-interval '7 days' GROUP BY 1,2")
    for line in rows.split("\n"):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not parts[0] or not parts[2].isdigit(): continue
        name, st, n = parts[0], parts[1], int(parts[2])
        e = w(name)
        if st == "succeeded": e["succ7"] += n
        else: e["fail7"] += n
    for line in q("SELECT claimed_by, count(*) FROM tensor_dev_missions "
                  "WHERE claimed_by IS NOT NULL AND status='succeeded' GROUP BY 1").split("\n"):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 2 and parts[0] and parts[1].isdigit():
            w(parts[0])["succ_total"] = int(parts[1])
    for line in q("SELECT claimed_by, status, count(*) FROM tensor_dev_missions "
                  "WHERE claimed_by IS NOT NULL AND goal LIKE '%【QR-P%' GROUP BY 1,2").split("\n"):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not parts[0] or not parts[2].isdigit(): continue
        e = w(parts[0]); e["qr_claimed"] += int(parts[2])
        if parts[1] == "succeeded": e["qr_succ"] += int(parts[2])
    workers = []
    for e in ws.values():
        tot = e["succ7"] + e["fail7"]
        e["rate7"] = round(100.0 * e["succ7"] / tot, 1) if tot else None
        workers.append(e)
    workers.sort(key=lambda x: (-x["succ7"], -x["succ_total"]))
    return {"ts": time.strftime("%F %T"), "workers": workers,
            "note": "v1=计量+发布; 信用入预算调度=v2; verifier 抓种子计酬待 mutation 轮上线"}

def main():
    log("credit ledger 线启动")
    while True:
        try:
            led = compute()
            OUT.write_text(json.dumps(led, ensure_ascii=False, indent=1))
            log(f"snapshot: {len(led['workers'])} workers, top={led['workers'][0]['worker'] if led['workers'] else '-'}")
        except Exception as e:
            log(f"ERR {e}")
        time.sleep(POLL)

main()
