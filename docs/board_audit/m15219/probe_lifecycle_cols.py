#!/usr/bin/env python3
"""一次性探针：读 mission_lifecycle_log 列名与 pr_gate 流水分布（供 verify_m15050.py 选列）。"""
import subprocess


def sql(q):
    r = subprocess.run(["su", "postgres", "-c", f'psql -d tensorplatform -t -A -F "|" -c "{q}"'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return ("ERR", r.stderr.strip()[:300])
    return [ln.split("|") for ln in r.stdout.strip().splitlines() if ln.strip()]


print("COLS:", sql("SELECT string_agg(column_name, ', ' ORDER BY ordinal_position) "
                   "FROM information_schema.columns WHERE table_name='mission_lifecycle_log'"))
print("ACTIONS:", sql("SELECT action, count(*) FROM mission_lifecycle_log GROUP BY 1 ORDER BY 2 DESC LIMIT 15"))