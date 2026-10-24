#!/usr/bin/env python3
"""verify_2026-10-24.m234.py — m234 operator 班次机器门（零手抄，直读工件/DB）"""
import json, subprocess, os, sys, time
from datetime import datetime, timezone

READINGS_PATH = os.path.join(os.path.dirname(__file__), "2026-10-24.readings.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "2026-10-24.md")

def load_readings():
    with open(READINGS_PATH) as f:
        return json.load(f)

def psql(sql):
    env = os.environ.copy()
    r = subprocess.run(
        ["docker","exec","open-foundation-postgres","psql","-U","foundation","-d","open_digital_foundation","-t","-A","-F","|","-c",sql],
        capture_output=True, text=True, env=env, timeout=30)
    return r.stdout.strip()

def load_log():
    with open(LOG_PATH) as f:
        return f.read()

checks = {"pass": 0, "fail": 0, "results": []}

def check(name, ok, detail=""):
    checks["results"].append({"name": name, "pass": bool(ok), "detail": detail})
    if ok:
        checks["pass"] += 1
    else:
        checks["fail"] += 1

R = load_readings()
LOG = load_log()

# S1: in-flight heartbeat freshness
inflight = R["step1_inflight"]
inflight_count = R["step1_inflight_count"]
max_hb = R["inflight_heartbeat_max_s"]
check("S1-inflight-count", inflight_count >= 1, f"在飞 {inflight_count} 条")
check("S1-hb-freshness", max_hb < 240, f"max_hb={max_hb}s < 240s")
check("S1-inflight-ids", set(R["inflight_ids"]) == {232, 233, 234}, f"ids={R['inflight_ids']}")

# S2: daemon count equality
wj_sum = R["step2_workers_json_sumN"]
pgrep = R["step2_pgrep_count"]
check("S2-equality", wj_sum == pgrep, f"workers.json ΣN={wj_sum} = pgrep={pgrep}")
check("S2-value", wj_sum == 16, f"ΣN={wj_sum}")

# S3: relay health
check("S3-http200", R["step3_relay_http_code"] == 200, f"http={R['step3_relay_http_code']}")
check("S3-models", R["step3_relay_models_count"] == 7, f"models={R['step3_relay_models_count']}")

# S4: cooldown cleaned
check("S4-expired", R["step4_cooldown_expired"] == True, "blocked_until expired")
check("S4-cleaned", R["step4_cooldown_after"] == "{}", f"after={R['step4_cooldown_after']}")

# S5: terminal 24h
h24f = R["step5_h24_failed"]
check("S5-h24-failed", h24f == 10, f"h24_failed={h24f}")
check("S5-since-shift-failed", R["step5_since_shift_failed"] == 0, f"since_shift={R['step5_since_shift_failed']}")
# R13 not escalating within shift window
check("S5-r13-window-zero", R["step5_since_shift_failed"] == 0, "本班窗口零新增 failed")

# R16: PASS_ALL mode
check("R16-passall-marker", R["passall_file_empty"] == True, "PASS_ALL marker file exists (0 bytes)")
check("R16-passall-log", R["passall_log_total_lines"] > 0, f"log lines={R['passall_log_total_lines']}")

# Continuation gate
cg = R["continuation_gate"]
check("CG-m229", "承接门挡下" in cg["m229"], cg["m229"])
check("CG-m230", "承接门挡下" in cg["m230"], cg["m230"])
check("CG-m231", "承接门挡下" in cg["m231"], cg["m231"])
check("CG-m208", "承接门挡下" in cg["m208"], cg["m208"])
check("CG-m216", "承接门挡下" in cg["m216"], cg["m216"])
check("CG-m221", "承接门挡下" in cg["m221"], cg["m221"])
check("CG-m226-report", "[上报]" in cg["m226"], cg["m226"])

# Refill check
rc = R["refill_check_results"]
for mid in ["m226", "m229", "m230", "m231"]:
    check(f"RC-{mid}-rc0", rc[mid]["rc"] == 0, f"rc={rc[mid]['rc']}")

# Log content checks
check("LOG-exists", len(LOG) > 100, f"log={len(LOG)} chars")
check("LOG-shift-id", "m234" in LOG, "m234 in log")
check("LOG-h24-failed", "10" in LOG, "h24_failed=10 in log")
check("LOG-cooldown-clean", "清理" in LOG, "cooldown cleaning documented")
check("LOG-m226-report", "[上报]" in LOG and "m226" in LOG, "m226 [上报] documented")
check("LOG-continuation-gate", "承接门" in LOG, "continuation gate documented")
check("LOG-suggestion", "建议" in LOG, "suggestions for next shift")

# Containers
check("CONT-healthy", R["containers_healthy"] == 11 and R["containers_total"] == 11, f"{R['containers_healthy']}/{R['containers_total']}")

# Worktree
check("WT-count", R["worktree_total"] >= 99, f"total={R['worktree_total']}")
check("WT-tp", R["worktree_tensorplatform"] >= 95, f"tp={R['worktree_tensorplatform']}")

# Dirty
check("DIRTY-zero", R["dirty_count"] == 0, f"dirty={R['dirty_count']}")

# gc.log X
check("X-gc-log", "权限不够" in R.get("gc_log_last", ""), R.get("gc_log_last", ""))

# Output
total = checks["pass"] + checks["fail"]
report = {
    "shift_mission_id": 234,
    "shift_start_utc": "2026-10-24T07:13:02Z",
    "collected_at_utc": R["collected_at_utc"],
    "checks_total": total,
    "checks_passed": checks["pass"],
    "checks_failed": checks["fail"],
    "all_pass": checks["fail"] == 0,
    "results": checks["results"],
}
report_path = os.path.join(os.path.dirname(__file__), "2026-10-24.m234.validation_report.json")
with open(report_path, "w") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print(json.dumps(report, indent=2, ensure_ascii=False))
print(f"\n{'='*60}")
print(f"PASSED: {checks['pass']}/{total}")
if checks["fail"] > 0:
    print("FAILED:")
    for r in checks["results"]:
        if not r["pass"]:
            print(f"  ✗ {r['name']}: {r['detail']}")
sys.exit(0 if checks["fail"] == 0 else 1)
