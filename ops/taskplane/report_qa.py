#!/usr/bin/env python3
# 报告质检常设线（owner 2026-10-29「需要有 agent 专门检查报告质量」）：
# 新发布报告 → 专职质检席（qwen3.6-plus 钉扎）逐批复审 → PASS/RESUBMIT → RESUBMIT 自动派返工单
# 状态: report_qa_state.json / 日志: /var/log/tp-report-qa.log
import json, os, time, pathlib, subprocess, urllib.request, urllib.error

API = "http://127.0.0.1:8010/api/v1"
# PR-9（AOG P1）净化：凭据不入库，运行时注入（见 deploy/taskplane/taskplane.env.example）
USER, PASS = os.environ.get("ECO_ADMIN_USER", "eco_admin"), os.environ.get("ECO_ADMIN_PASS", "")
STATE_F = pathlib.Path("/root/tp-agent/report_qa_state.json")
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

def _family(m):
    """模型族归并（评审独立性路由用，借 ARIS review_independence 2026-10-31）。"""
    m = (m or "").lower()
    for f in ("qwen", "glm", "deepseek", "minimax"):
        if f in m:
            return f
    return "other"


# 质检席候选（反同族路由）：优先选与批内作者不同族的评审模型；
# 三族都被作者占满才回落 qwen3.6-plus，且判定一律标 provisional。
REVIEWERS = ["qwen3.6-plus", "glm-5.1", "deepseek-v4-flash"]


def degenerate_score(md):
    """退化输出机检（借 owner 另一平台 Mavis「退化审议循环」发现 2026-10-31：
    量化 行重复率/占位符密度，识别「模板占位循环」型报告；**只报告不处置**——
    观察期后视命中质量再决定是否接处置）。返回 (score, reasons)；score≥0.4 视为退化。"""
    if not md:
        return 0.0, []
    reasons = []
    lines = [l.strip() for l in md.splitlines() if len(l.strip()) >= 8]
    line_dup = (len(lines) - len(set(lines))) / max(len(lines), 1)
    if len(lines) >= 10 and line_dup > 0.30:
        reasons.append(f"行重复率{line_dup:.0%}")
    ph = sum(md.count(p) for p in ("TODO", "XXX", "占位", "待补充", "此处省略", "PLACEHOLDER"))
    if ph >= 5:
        reasons.append(f"占位符{ph}处")
    if not reasons:
        return 0.0, []
    return round(min(1.0, line_dup * 1.5 + ph / 20), 2), reasons

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
            tok = login()   # 2026-10-31 修：token 只在启动取一次会过期 401，改每轮刷新
            rows = q(f"SELECT id||chr(2)||coalesce(mission_id::text,'')||chr(2)||"
                     f"replace(left(title,50),chr(10),' ')||chr(2)||"
                     f"replace(left(coalesce(markdown,''),4000),chr(10),' ') "
                     f"FROM tensor_reports WHERE id > {state['watermark']} "
                     f"AND coalesce(scenario_key,'') NOT IN ('report-qa','guide-dashboard') ORDER BY id").splitlines()
            reports = [r.split(chr(2)) for r in rows if r.strip()]
            # 机检前置（2026-10-31）：退化输出不烧 LLM 质检预算，出机检报告（只报告不处置）
            deg = []
            for b in reports:
                sc, why = degenerate_score(b[3] if len(b) > 3 else "")
                if sc >= 0.4:
                    deg.append((b[0], b[1], sc, why))
            if deg:
                md_txt = "\n".join(f"- 报告 {d[0]}（mission {d[1] or '无'}）：退化分 {d[2]}，{('；'.join(d[3])) or '复合命中'}"
                                   for d in deg)
                payload = {"title": f"【QA·机检】退化输出检测 {','.join(d[0] for d in deg)}",
                           "scenario_key": "report-qa", "tags": ["质检", "机检"],
                           "markdown": f"# 退化输出机检（量化指标，只报告不处置）\n{md_txt}\n\n"
                                       f"判定规则：行重复率>30% 计 1.5×，占位符每 5 处计 0.25，退化分≥0.4 命中。"
                                       f"命中篇目待质检席/人工关注；未自动派返工（观察期）。"}
                mid0 = next((d[1] for d in deg if d[1].isdigit()), "")
                if mid0:
                    payload["mission_id"] = int(mid0)
                s, _ = api("POST", "/tensor/reports", payload, tok)
                log(f"机检退化命中 {len(deg)} 篇 {[d[0] for d in deg]} 报告发布={s}")
                _seen = {d[0] for d in deg}
                reports = [b for b in reports if b[0] not in _seen]
            batches = [reports[i:i+BATCH] for i in range(0, len(reports), BATCH)][:MAX_BATCH_PER_CYCLE]
            if batches:
                log(f"新报告 {len(reports)} 篇，本轮质检 {len(batches)} 批")
            for bi, batch in enumerate(batches):
                rid_list = ",".join(b[0] for b in batch)
                mid_list = ",".join((b[1] or "无") for b in batch)
                # 反同族路由（借 ARIS review_independence 2026-10-31）：
                # 查批内作者模型族 → 优先跨族质检席；同族不可避时判定标 provisional
                _mids = [b[1] for b in batch if (b[1] or "").isdigit()]
                fams = set()
                if _mids:
                    for _r in q("SELECT DISTINCT model FROM tensor_dev_missions "
                                "WHERE id IN (%s)" % ",".join(_mids)).splitlines():
                        if _r.strip():
                            fams.add(_family(_r.strip()))
                rev = next((c for c in REVIEWERS if _family(c) not in fams), "qwen3.6-plus")
                indep = "cross-family" if _family(rev) not in fams else "same-family"
                goal = (f"【质检席·报告复审 batch {rid_list}】你是专职报告质检 agent（owner 常设指令）。逐篇复审：\n"
                        f"报告 id: {rid_list}（对应工作单 mission_id: {mid_list}）。\n"
                        f"步骤：\n"
                        f"1. curl 登录平台（$TENSOR_AGENT_USER/$TENSOR_AGENT_PASS @ $TENSOR_API_BASE）后 GET /tensor/reports/{{id}} 拿 markdown 全文；\n"
                        f"2. 逐篇过质检单（每篇给判定 PASS/MINOR/RESUBMIT+理由）：\n"
                        f"  ①结构五节：目的/方法/关键结果（含数字表）/审计/局限——缺节降级；\n"
                        f"  ②数字溯源抽查：抽 3 个关键数字，到 /root/tp-agent/work/m{{mission_id}}/ 的 docs/*.md 与 artifacts/*.json 里 grep 复核；3/3 命中=过，对不上=RESUBMIT（列出对不上的数字与文件证据）；mission 无工件可查就标 MINOR 并注明'工件缺失'；\n"
                        f"  ③honest-FAIL 保留：原单结论若为 FAIL，报告不得粉饰为 PASS——违者 RESUBMIT；\n"
                        f"  ④图件（owner 升级要求）：场量云图+比较曲线（与解析/文献/基线）两类齐备；缺任一类须有明确理由（纯文档/后端任务可豁免但必须写明）；\n"
                        f"  ⑤误差判定节（owner 升级要求）：关键量 vs 基准误差逐项列出且显式回答是否<3%；无基准须声明原因——缺失或含糊=RESUBMIT；\n"
                        f"  ⑥结论不超证据：超出数据支撑的断言列出来；\n"
                        f"3. 交付：worktree 内 docs/qa_batch_{rid_list.replace(',','_')}.md（逐篇判定表+缺陷清单）；\n"
                        f"4. 发布质检汇总报告 POST /tensor/reports：title=【QA】批次质检 {rid_list}，scenario_key='report-qa'，tags=[\"质检\"]，mission_id=本单 id（此报告即你的报告纪律交付），markdown=第3步全文；\n"
                        f"5. 处置：RESUBMIT 的篇目 → curl POST /tensor/missions 派返工单（本批最多 3 单）：repo 用原报告对应 mission 的 repo，POST body 必须显式包含 runner 字段且值为 native（表默认 openhands=无人领死单），budget 60 调用/2400s，goal 首行必须写「EDIT_WHITELIST: docs/, artifacts/」（write_file 机器门据此只放行报告/工件路径），正文写明【返工·报告 {id}】缺陷清单（逐条）+要求重写并重发报告（mission_id 仍引原工作单）+原报告 id。MINOR 只记录不派单。\n"
                        f"纪律：禁碰 GitHub；返工单只准重写报告文件与报告 POST，禁改业务代码；你的判定也要有证据（引用原文行/工件路径）。完成后 finish，summary 报各篇判定。"
                        f"\n评审独立性={indep}（作者族 {sorted(fams) if fams else '未知'} vs 质检席 {rev}；借 ARIS）："
                        + ("same-family——你的全部判定一律标注 provisional，最终判定需跨族复核或人工确认。"
                           if indep == "same-family" else
                           "cross-family——跨族评审，判定可终局。"))
                s, d = api("POST", "/tensor/missions", {
                    "goal": goal, "repo": "TensorLBM", "runner": "native", "origin": "owner",
                    "model": rev,
                    "budget": {"max_tool_calls": 100, "max_wall_seconds": 3600, "max_tokens": 3000000}}, tok)
                if s in (200, 201):
                    state["qa_missions"].append(d["id"])
                    log(f"质检批 {rid_list} → mission {d['id']}（质检席 {rev} [{indep}]）")
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
