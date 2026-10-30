#!/usr/bin/env python3
# 报告质检常设线（owner 2026-10-29「需要有 agent 专门检查报告质量」）：
# 新发布报告 → 专职质检席（qwen3.6-plus 钉扎）逐批复审 → PASS/RESUBMIT → RESUBMIT 自动派返工单
# 状态: report_qa_state.json / 日志: /var/log/tp-report-qa.log
import json, os, time, pathlib, subprocess, urllib.request, urllib.error

API = "http://127.0.0.1:8010/api/v1"
USER, PASS = os.environ.get("TP_API_USER", ""), os.environ.get("TP_API_PASS", "")
STATE_F = pathlib.Path("/srv/tensor/agent/report_qa_state.json")
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc"]
PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}
POLL = 300; BATCH = 3; MAX_BATCH_PER_CYCLE = 2

def log(m):
    with open("/var/log/tp-report-qa.log", "a") as f:
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

def login():
    s, d = api("POST", "/auth/login", {"username": USER, "password": PASS})
    assert s == 200, f"login {s}"
    return d["access_token"]

def q(sql):
    return subprocess.run(PG + [sql], capture_output=True, text=True, env=PGENV).stdout.strip()

def main():
    tok = login()
    state = {"watermark": 0, "qa_missions": []}
    if STATE_F.exists():
        try: state = json.loads(STATE_F.read_text())
        except Exception: pass
    if not state["watermark"]:
        # 初始化：从已有报告最大 id 起（存量 1-3 号人工已审范畴，先盯新增）
        state["watermark"] = int(q("SELECT coalesce(max(id),0) FROM tensor_reports") or 0)
        log(f"初始化水位={state['watermark']}（存量不追审）")
    log("报告质检线启动")
    while True:
        try:
            rows = q(f"SELECT id||'|'||coalesce(mission_id::text,'')||'|'||replace(left(title,50),E'\\n',' ') "
                     f"FROM tensor_reports WHERE id > {state['watermark']} "
                     f"AND coalesce(scenario_key,'') <> 'report-qa' ORDER BY id").splitlines()
            reports = [r.split("|", 2) for r in rows if r.strip()]
            batches = [reports[i:i+BATCH] for i in range(0, len(reports), BATCH)][:MAX_BATCH_PER_CYCLE]
            if batches:
                log(f"新报告 {len(reports)} 篇，本轮质检 {len(batches)} 批")
            for bi, batch in enumerate(batches):
                rid_list = ",".join(b[0] for b in batch)
                mid_list = ",".join((b[1] or "无") for b in batch)
                goal = (f"【质检席·报告复审 batch {rid_list}】你是专职报告质检 agent（owner 常设指令）。逐篇复审：\n"
                        f"报告 id: {rid_list}（对应工作单 mission_id: {mid_list}）。\n"
                        f"步骤：\n"
                        f"1. curl 登录平台（$TENSOR_AGENT_USER/$TENSOR_AGENT_PASS @ $TENSOR_API_BASE）后 GET /tensor/reports/{{id}} 拿 markdown 全文；\n"
                        f"2. 逐篇过质检单（每篇给判定 PASS/MINOR/RESUBMIT+理由）：\n"
                        f"  ①结构五节：目的/方法/关键结果（含数字表）/审计/局限——缺节降级；\n"
                        f"  ②数字溯源抽查：抽 3 个关键数字，到 /srv/tensor/agent/work/m{{mission_id}}/ 的 docs/*.md 与 artifacts/*.json 里 grep 复核；3/3 命中=过，对不上=RESUBMIT（列出对不上的数字与文件证据）；mission 无工件可查就标 MINOR 并注明'工件缺失'；\n"
                        f"  ③honest-FAIL 保留：原单结论若为 FAIL，报告不得粉饰为 PASS——违者 RESUBMIT；\n"
                        f"  ④图件（owner 升级要求）：场量云图+比较曲线（与解析/文献/基线）两类齐备；缺任一类须有明确理由（纯文档/后端任务可豁免但必须写明）；\n"
                        f"  ⑤误差判定节（owner 升级要求）：关键量 vs 基准误差逐项列出且显式回答是否<3%；无基准须声明原因——缺失或含糊=RESUBMIT；\n"
                        f"  ⑥结论不超证据：超出数据支撑的断言列出来；\n"
                        f"3. 交付：worktree 内 docs/qa_batch_{rid_list.replace(',','_')}.md（逐篇判定表+缺陷清单）；\n"
                        f"4. 发布质检汇总报告 POST /tensor/reports：title=【QA】批次质检 {rid_list}，scenario_key='report-qa'，tags=[\"质检\"]，mission_id=本单 id（此报告即你的报告纪律交付），markdown=第3步全文；\n"
                        f"5. 处置：RESUBMIT 的篇目 → curl POST /tensor/missions 派返工单（本批最多 3 单）：repo 用原报告对应 mission 的 repo，budget 60 调用/2400s，goal 写明【返工·报告 {id}】缺陷清单（逐条）+要求重写并重发报告（mission_id 仍引原工作单）+原报告 id。MINOR 只记录不派单。\n"
                        f"纪律：禁碰 GitHub；返工单只准重写报告文件与报告 POST，禁改业务代码；你的判定也要有证据（引用原文行/工件路径）。完成后 finish，summary 报各篇判定。")
                s, d = api("POST", "/tensor/missions", {
                    "goal": goal, "repo": "TensorLBM", "runner": "native", "origin": "owner",
                    "model": "qwen3.6-plus",
                    "budget": {"max_tool_calls": 100, "max_wall_seconds": 3600, "max_tokens": 3000000}}, tok)
                if s in (200, 201):
                    state["qa_missions"].append(d["id"])
                    log(f"质检批 {rid_list} → mission {d['id']}")
                else:
                    log(f"质检批派发失败 {s} {str(d)[:120]}")
            if reports:
                state["watermark"] = int(reports[-1][0])
            STATE_F.write_text(json.dumps(state))
        except Exception as e:
            log(f"异常: {e}")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
