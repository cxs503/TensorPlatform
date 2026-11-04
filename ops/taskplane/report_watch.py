#!/usr/bin/env python3
# 报告守望线 v2.1（owner 常设指令「每个工作都要写报告」）
# v1 事故：预算 18 太少/agent 自行 ls 探索/无工件目录瞎找 → m77-84,86 全烧。
# v2.1：预算 40+钉扎 qwen3.7-plus+工件清单与原单声明嵌入 goal+无工件 meta 配方
#      +重试上限 2+在飞去重+行解析只按 \n 切+缺字段跳过+单行异常不杀整轮。
import json, os, time, pathlib, subprocess, urllib.request, urllib.error

API = "http://127.0.0.1:8010/api/v1"
# PR-9（AOG P1）净化：凭据不入库，运行时注入（见 deploy/taskplane/taskplane.env.example）
USER, PASS = os.environ.get("ECO_ADMIN_USER", "eco_admin"), os.environ.get("ECO_ADMIN_PASS", "")
STATE_F = pathlib.Path("/root/tp-agent/report_watch_state.json")
WORK = pathlib.Path("/root/tp-agent/work")
REPOS = ("TensorLBM","TensorPhysics","TensorDEM","TensorFEM","TensorFVM",
         "TensorBEM","TensorSPH","TensorCAD","TensorPlatform","TensorDG")
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc"]
PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}
POLL = 300; DISPATCH_PER_CYCLE = 3; MAX_ATTEMPTS = 2

def log(m):
    with open("/var/log/tp-report-watch.log", "a") as f:
        f.write(time.strftime("[%m-%d %H:%M:%S] ") + m + "\n")

def api(method, path, payload=None, token=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def q(sql):
    return subprocess.run(PG + [sql], capture_output=True, text=True, env=PGENV).stdout

def inventory(mid):
    d = WORK / f"m{mid}"
    if not d.exists():
        return "（工件目录未留档——写诚实 meta-报告：缺失数字一律标注'未留档'，只依据下面嵌入的原单声明，不得编造）"
    lines = []
    for sub, pat in (("docs", "*.md"), ("artifacts", "*.json"), ("artifacts", "*.csv"), ("", "*.md")):
        p = d / sub if sub else d
        hits = sorted(x.name for x in p.glob(pat))[:12]
        if hits:
            lines.append(f"  {sub or '.'}/: " + ", ".join(hits))
    listing = "\n".join(lines) if lines else "  （目录存在但无 md/json 交付物——同 meta 配方处理）"
    return f"工件清单（直接 cat，不要 ls 探索）：\n{listing}\n工件目录: {d}"

def build_goal(mid, repo, headline, goal_excerpt):
    return (f"【补报单·mission {mid}】owner 常设指令：每个工作都要有论文级进展报告。"
            f"mission {mid}（repo={repo}）已成功交付但缺报告，你来补。\n"
            f"★预算纪律：你只有 80 次工具调用（每轮常 2 个并行调用≈40 轮）。分配：登录 1、"
            f"读文件 ≤12、写报告 1、POST 1、finish 1。第 55 次调用前必须已进入写报告阶段。\n"
            f"——背景已嵌入，不用再查——\n"
            f"原单声明: {headline}\n原单 goal 摘要: {goal_excerpt}\n{inventory(mid)}\n"
            f"步骤：\n"
            f"1. 登录: curl -s -X POST $TENSOR_API_BASE/auth/login -H 'Content-Type: application/json' "
            f"-d '{{\"username\":\"'\"$TENSOR_AGENT_USER\"'\",\"password\":\"'\"$TENSOR_AGENT_PASS\"'\"}}'；\n"
            f"2. 按清单 cat 关键文件（数字从工件程序化提取零手抄；json 可用 python3 -c json.load 打印键值）；\n"
            f"3. 写 worktree 内 docs/report_m{mid}.md（论文级六要素：案例介绍/怎么做的/关键结果数字表/结果图/注意点/误差判定——关键量误差显式回答是否<3%，无基准声明原因；工件含 npz/png 可用 matplotlib 画比较曲线/云图内嵌 figures，无图写明理由）；\n"
            f"4. python3 组 JSON 写 /tmp/rep.json（title/summary/scenario_key='report-backfill'/"
            f"mission_id={mid}/tags=[\"补报\"]/markdown=第3步全文/figures=[]），再 "
            f"curl -s -X POST $TENSOR_API_BASE/tensor/reports -H 'Authorization: Bearer <token>' "
            f"-H 'Content-Type: application/json' -d @/tmp/rep.json；\n"
            f"POST 成功一次即止（拿到返回 id 后绝不重复 POST，重复发布会被清理并记质检缺陷）。\n5. finish，summary 一句话报 POST 返回的 report id。\n"
            f"纪律：原单 FAIL 结论如实保留（honest-FAIL）；不改业务代码；报告未发不算完成。")

def main():
    tok = api("POST", "/auth/login", {"username": USER, "password": PASS})[1]["access_token"]
    state = json.loads(STATE_F.read_text()) if STATE_F.exists() else {"attempts": {}}
    state.setdefault("attempts", {})
    log(f"报告守望线 v2.1 启动")
    while True:
        try:
            raw = q("SELECT m.id||'|'||m.repo||'|'||left(replace(replace(coalesce(m.result::json->>'headline','(无声明)'),E'\n',' '),E'\r',' '),80)||'|'||"
                    "left(replace(replace(m.goal,E'\\n',' '),E'\\r',' '),260) FROM tensor_dev_missions m "
                    "WHERE m.status='succeeded' AND m.goal NOT LIKE '【补报单·mission%' AND m.goal NOT LIKE '【返工%' AND NOT EXISTS "
                    "(SELECT 1 FROM tensor_reports r WHERE r.mission_id=m.id) ORDER BY m.id")
            debts = []
            for line in raw.split("\n"):
                parts = line.strip().split("|", 3)
                if len(parts) == 4 and parts[0].strip().isdigit():
                    debts.append(parts)
            dispatched = 0
            for mid, repo, headline, excerpt in debts:
                try:
                    if dispatched >= DISPATCH_PER_CYCLE:
                        break
                    if state["attempts"].get(str(mid), 0) >= MAX_ATTEMPTS:
                        continue
                    inflight = q(f"SELECT count(*) FROM tensor_dev_missions WHERE status IN ('running','queued') "
                                 f"AND goal LIKE '%补报单·mission {mid}】%'").strip()
                    if inflight and inflight != "0":
                        continue
                    s, d = api("POST", "/tensor/missions", {
                        "goal": build_goal(mid, repo.strip(), headline.strip().replace("'", "`"),
                                           excerpt.strip().replace("'", "`")),
                        "repo": repo.strip() if repo.strip() in REPOS else "TensorLBM",
                        "runner": "native", "origin": "owner", "model": "qwen3.7-plus",
                        "budget": {"max_tool_calls": 80, "max_wall_seconds": 2700,
                                   "max_tokens": 2500000}}, tok)
                    if s in (200, 201):
                        state["attempts"][str(mid)] = state["attempts"].get(str(mid), 0) + 1
                        dispatched += 1
                        log(f"补报单 m{mid} → mission {d['id']} (attempt {state['attempts'][str(mid)]})")
                    else:
                        log(f"派发失败 {s} {str(d)[:100]}")
                except Exception as e:
                    log(f"m{mid} 行异常跳过: {e}")
            STATE_F.write_text(json.dumps(state))
        except Exception as e:
            log(f"轮异常: {e}")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
