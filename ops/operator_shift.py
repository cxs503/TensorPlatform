#!/usr/bin/env python3
"""值班长（operator）班次派发器——常设智能分析与调度线（2026-10-29）。
与 sdaa_sweep.py 分工：补货线维持队列深度；值班长分析完工结果、处置 FAIL、
里程碑出报告。班次即 mission（repo=TensorPlatform），20 分钟一班，无在飞班次才派。"""
import json, os, pathlib, subprocess, time, urllib.request

BASE = "http://127.0.0.1:8010/api/v1"
STATE = pathlib.Path("/srv/tensor/agent/operator_state.json")
LOG = open("/var/log/tp-operator.log", "a", buffering=1)
SHIFT_INTERVAL = 1200  # 班次间隔（秒）
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-t", "-c"]
PGENV = {"PATH": "/usr/bin:/bin", "PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "")}

GOAL = """值班长班次（分析与调度，不改业务代码）。本班次职责与硬上限：
1) 用 run 工具 curl 登录本平台（POST /api/v1/auth/login，账号密码见环境变量 TENSOR_AGENT_USER/TENSOR_AGENT_PASS），GET /api/v1/tensor/missions?limit=50 拉近期 mission。
2) 分析扫荡线（goal 含「SDAA 验证案例扫荡」）完工结果：逐单读 result 摘要，统计各案例 PASS/FAIL、失败原因归类（环境/triton 绕行/判据真红/其他）。数字全部来自 API 返回，零手抄。
3) 处置（硬上限：本班次最多派 3 单、只准派 TensorLBM 扫荡类 goal）：
   - FAIL 案例 → 派「诊断+修复绕行」mission（goal 写明失败证据与诊断方向）
   - 队列将空（in-flight<4）→ 按案例字母序派下探案例
   派发用 POST /api/v1/tensor/missions（runner=native, repo=TensorLBM）。禁止派其他类型、禁止改审批、禁止碰 GitHub。
4) 里程碑：若扫荡线累计完工 ≥10 单且尚无合并报告，POST /api/v1/tensor/reports 发布论文级合并报告（标题含「SDAA 扫荡」，逐案例判据表+FAIL 如实+设备证据汇总+局限；figures 可省略）。
5) 交付：docs/operator_shift_<班次号>.md（本班分析表+派发清单+下一班建议），调用 finish。
边界：只分析不伪造；任何判据红不得改口径；发现系统性问题写入「升级事项」节留给 owner。"""

def log(m):
    print(time.strftime("[%F %T]"), m, file=LOG, flush=True)

def login():
    req = urllib.request.Request(BASE + "/auth/login",
        data=json.dumps({"username": os.environ.get("TP_API_USER", ""), "password": os.environ.get("TP_API_PASS", "")}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["access_token"]

def psql(q):
    return subprocess.run(PG + [q], capture_output=True, text=True, env=PGENV).stdout.strip()

def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"shifts": []}

def main():
    log("operator shift dispatcher start")
    while True:
        try:
            st = load_state()
            running = psql("SELECT count(*) FROM tensor_dev_missions WHERE status IN ('queued','running') AND goal LIKE '%值班长班次%';")
            if int(running or 0) == 0:
                tok = login()
                body = {"goal": GOAL, "repo": "TensorPlatform", "runner": "native",
                        "budget": {"max_tool_calls": 120, "max_wall_seconds": 2400, "max_tokens": 3000000}}
                req = urllib.request.Request(BASE + "/tensor/missions",
                    data=json.dumps(body).encode(), method="POST",
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
                mid = json.loads(urllib.request.urlopen(req).read())["id"]
                st["shifts"].append({"mission_id": mid, "at": time.strftime("%F %T")})
                STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
                log(f"shift #{len(st['shifts'])} → m{mid}")
                time.sleep(SHIFT_INTERVAL)
            else:
                time.sleep(300)
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {e}")
            time.sleep(120)

if __name__ == "__main__":
    main()
