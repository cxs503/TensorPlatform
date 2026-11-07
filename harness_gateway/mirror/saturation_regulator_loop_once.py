#!/usr/bin/env python3
"""饱和调节器 daemon 单跳（owner 2026-11-07 饱和令；03:2x 从会话 cron 迁容器 daemon——常设机制不挂人类会话）"""
import os
import subprocess

# IN27-H2 门控仓补丁：口令只从环境/.pgpass 取（源码内不得有明文）；
# PATH 不再写死（写死会让沙箱 shim 失效、也让 daemon 行为随宿主漂移）；
# psql 单次调用加硬上限，daemon 不会被「锁等待中的 psql」拖成僵尸。
PGPASSWORD = os.environ.get("TP_PG_PASSWORD", "")
PGENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
if PGPASSWORD:
    PGENV["PGPASSWORD"] = PGPASSWORD
PSQL_TIMEOUT_S = float(os.environ.get("TP_PG_TIMEOUT_S", "15"))


def _int(v, default=0):
    """psql 输出解析容错：空串/告警串不再让 daemon 崩在 ValueError。"""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def q(sql):
    try:
        p = subprocess.run(["psql","-U","postgres","-d","tensorplatform","-h","/var/run/postgresql","-t","-A","-c",sql],
                           capture_output=True,text=True,env={**os.environ, **PGENV},timeout=PSQL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"psql 超时 {PSQL_TIMEOUT_S}s，跳过本跳")
        return ""
    return p.stdout.strip() if not p.returncode else ""
online = q("SELECT count(*) FROM factory_resident_workers WHERE heartbeat_at > now() - interval '5 minutes';")
busy = q("SELECT count(*) FROM tensor_dev_missions WHERE status='running';")
held = q("SELECT count(*) FROM campaign_nodes WHERE status='blocked' AND node_type='task' AND metadata->>'ext_id' LIKE 'IN16-%';")
if not online:
    print("db 不可达，跳过本跳"); raise SystemExit(0)
online, busy, held = _int(online), _int(busy), _int(held)
idle = max(0, online - busy)
rel = 0
if idle >= 6 and held > 0:
    rel = min(idle - 4, held)
    q("UPDATE campaign_nodes SET status='pending', block_reason=NULL WHERE id IN "
      "(SELECT id FROM campaign_nodes WHERE status='blocked' AND node_type='task' "
      "AND metadata->>'ext_id' LIKE 'IN16-%' ORDER BY id LIMIT " + str(rel) + ")")
q(f"INSERT INTO saturation_pulse (online, busy, held, released, note) VALUES ({online}, {busy or 0}, {int(held or 0)-rel}, {rel}, 'daemon loop')")
print(f"online={online} busy={busy} idle={idle} held={held} released={rel}")
