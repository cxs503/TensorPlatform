#!/usr/bin/env python3
"""SDAA 验证案例扫荡派发器（常设线，2026-10-29）。
维持 in-flight 深度，从 TensorLBM benchmarks/verified/ 逐案例派发上卡验证 mission。
状态文件 sweep_state.json 记 dispatched 防重派；日志 /var/log/tp-sweep-dispatcher.log。"""
import json, os, pathlib, subprocess, time, urllib.request

BASE = "http://127.0.0.1:8010/api/v1"
CASES_DIR = pathlib.Path("/srv/tensor/workspace/TensorLBM/benchmarks/verified")
STATE = pathlib.Path("/srv/tensor/agent/sweep_state.json")
LOG = open("/var/log/tp-sweep-dispatcher.log", "a", buffering=1)
IN_FLIGHT_MAX = 12
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-t", "-c"]
PGENV = {"PATH": "/usr/bin:/bin", "PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "")}

GOAL_TMPL = """SDAA 验证案例扫荡——案例 {case}（TensorLBM benchmarks/verified/{case}）。步骤：
1) 读该案例目录的 README/运行入口，弄清其既有判据口径（这些案例在 NVIDIA 侧已有成熟判据，按原口径）。
2) 写独立运行脚本 scripts/sdaa_sweep_{case}.py（不改业务代码）：计算张量放 'sdaa' 设备；若入口依赖 triton，用 TRITON_INTERPRET=1 并如实记录绕行。网格/步数按案例文档推荐值或可行较小档，墙钟 ≤20 分钟。
3) 运行并把机器输出存 artifacts/sdaa_{case}.json：设备证据行（torch.sdaa.get_device_name(0) 输出）、判据数值、逐条与门的比对 PASS/FAIL。
4) 交付 docs/sdaa_sweep/{case}.md：案例/配置/结果表/判据逐条 PASS/FAIL/墙钟/绕行记录。
判据：artifacts JSON 存在且含设备证据行；判据数值全部脚本算出零手抄。
honest-FAIL：任何判据不过或跑不通，如实记 FAIL 与原因；禁止降判据、禁止用 CPU 结果冒充 sdaa。"""

def log(m):
    print(time.strftime("[%F %T]"), m, file=LOG, flush=True)

def login():
    req = urllib.request.Request(BASE + "/auth/login",
        data=json.dumps({"username": os.environ.get("TP_API_USER", ""), "password": os.environ.get("TP_API_PASS", "")}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["access_token"]

def inflight():
    out = subprocess.run(PG + ["SELECT count(*) FROM tensor_dev_missions WHERE status IN ('queued','running');"],
                         capture_output=True, text=True, env=PGENV).stdout.strip()
    return int(out or 0)

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"dispatched": {}}

def main():
    log("sdaa sweep dispatcher start")
    while True:
        try:
            st = load_state()
            cases = sorted(c.name for c in CASES_DIR.iterdir() if c.is_dir())
            todo = [c for c in cases if c not in st["dispatched"]]
            n = inflight()
            if not todo:
                time.sleep(120); continue
            if n >= IN_FLIGHT_MAX:
                time.sleep(60); continue
            tok = login()
            case = todo[0]
            body = {"goal": GOAL_TMPL.format(case=case), "repo": "TensorLBM", "runner": "native"}
            req = urllib.request.Request(BASE + "/tensor/missions",
                data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
            mid = json.loads(urllib.request.urlopen(req).read())["id"]
            st["dispatched"][case] = mid
            STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
            log(f"dispatch {case} → m{mid} (in-flight {n}→{n+1}, todo {len(todo)-1})")
            time.sleep(5)
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
