#!/usr/bin/env python3
"""credit_settler_loop.py — 评价→信用→档位（进化回路中段接线，owner 2026-11-07 满血harness令）。
每 5 分钟：confirmed/rejected 评价 → tensor_credit_ledger 幂等入账 → credit_tiers 视图。
delta（SPEC §7.2 几何×3）：S1=+1 S2=+3 S3=+9 S4=+27 S5=+81；rejected=−27 strike。
档位只给档禁排名：L1<10 L2<30 L3<90 L4<270 L5≥270。列名实测：evaluation_id/contributor/mission_id。"""
import subprocess, time

LOG = "/var/log/tp-fleet/credit_settler.log"
def log(m):
    with open(LOG, "a") as f:
        f.write(time.strftime("%F %T ") + m + "\n")

def q(sql):
    r = subprocess.run(["runuser","-u","postgres","--","psql","-d","tensorplatform",
                        "-t","-A","-F","\x02","-c",sql],capture_output=True,text=True,timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return [ln.split("\x02") for ln in r.stdout.splitlines() if ln.strip()]

DELTA = {"S1":1,"S2":3,"S3":9,"S4":27,"S5":81}
TIERS_SQL = """CREATE OR REPLACE VIEW credit_tiers AS
SELECT model AS seat, sum(delta)::int AS total_delta,
  CASE WHEN sum(delta)>=270 THEN 'L5' WHEN sum(delta)>=90 THEN 'L4'
       WHEN sum(delta)>=30 THEN 'L3' WHEN sum(delta)>=10 THEN 'L2' ELSE 'L1' END AS tier,
  count(*) AS entries, max(created_at) AS last_entry
FROM tensor_credit_ledger GROUP BY model"""

def settle():
    rows = q("SELECT e.evaluation_id, coalesce(e.grade,''), e.status, coalesce(e.contributor,''), "
             "coalesce(e.mission_id::text,'') FROM outcome_evaluations e "
             "WHERE e.status IN ('confirmed','rejected') "
             "AND NOT EXISTS (SELECT 1 FROM tensor_credit_ledger l WHERE l.reason LIKE 'eval:'||e.evaluation_id||'|%')")
    n_ok = 0
    for rid, grade, status, seat, mid in rows:
        d = -27 if status == "rejected" else DELTA.get((grade or "").upper(), 0)
        if d == 0:
            continue
        seat = (seat or "unknown").replace("'", "")[:60]
        try:
            q(f"INSERT INTO tensor_credit_ledger (organization_id, model, delta, reason, mission_id) "
              f"VALUES (1, '{seat}', {d}, 'eval:{rid}|{status}|{grade}', "
              f"{'NULL' if not (mid or '').isdigit() else mid})")
            n_ok += 1
        except Exception as e:
            log(f"WARN eval {rid} 入账失败: {str(e)[:120]}")
    q(TIERS_SQL)
    if n_ok:
        tiers = q("SELECT seat, total_delta, tier FROM credit_tiers ORDER BY total_delta DESC LIMIT 8")
        log(f"入账 {n_ok} 条；档位面: " + "; ".join(f"{t[0]}={t[1]}({t[2]})" for t in tiers))

if __name__ == "__main__":
    log("=== credit_settler v2 启动（evaluation_id 实列名；幂等 eval:id| 前缀）===")
    while True:
        try:
            settle()
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {str(e)[:200]}")
        time.sleep(300)
