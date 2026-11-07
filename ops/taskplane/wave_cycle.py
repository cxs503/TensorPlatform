#!/usr/bin/env python3
# 波次循环常设线（owner 2026-10-30 放行：议事会常态化 + 任务池自助领取）
# 职责：议事会(session) → r3_plan.json 护栏校验 → 波次任务入池（不钉模型，worker 自助领取）
#       → 波次收官判定 → 冷却后自动开下一 session。
# 护栏：≤12 单/波、预算帽 140/5400s/4M、repo 白名单、≥1 session/20h、连续 3 场失败熔断、
#       入池前 in-flight<20、每波上下文自动重建（指南 KPI 现状+上波战果+owner 指令）。
import json, os, time, pathlib, subprocess, urllib.request, urllib.error

API = "http://127.0.0.1:8010/api/v1"
USER, PASS = os.environ.get("TP_API_USER", ""), os.environ.get("TP_API_PASS", "")
PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc"]
CROOT = pathlib.Path("/root/tp-agent/council")
STATE_F = pathlib.Path("/root/tp-agent/wave_cycle_state.json")
STATUS_F = pathlib.Path("/root/tp-agent/wave_cycle_status.json")
LOGF = "/var/log/tp-wave-cycle.log"
POLL = 600
MAX_WAVE, MAX_CALLS, MAX_WALL, MAX_TOKENS = 12, 140, 5400, 4000000
COOLDOWN = 7200            # 波收官后冷却
MIN_INTERVAL = 20 * 3600   # 两场议事会最小间隔
INFLIGHT_GUARD = 20        # 入池前闸
HALT_AFTER_FAILS = 3       # 议事会连败阈值：**只转兜底模式，不熔断等人**（渐退令）
HEARTBEAT_SECS = 3600      # 空转可见性：每小时一行心跳，杜绝"日志长期空转"式静默
FALLBACK_TASK_PREFIX = "cnode-"   # 兜底派发单的 task_key 前缀（幂等去重靠它）
# 白名单：ship-performance-platform 是 campaign_nodes.metadata.repo 的现名
# （ship-platform 为历史别名，两写法都收，否则兜底派发会把船单全裁掉）
REPOS = {"TensorLBM", "TensorPlatform", "ship-platform", "ship-performance-platform", "TensorPhysics",
         "TensorBEM", "TensorSPH", "TensorDG", "TensorCAD", "TensorFVM", "TensorFEM", "TensorDEM"}
FOOTER = ("\n★纪律：读文件 ≤8 次即动手；完成后必须调用 finish；没有产物不算完成；验收过自动推 GitHub 分支（tp_pusher），commit 只要最终成果。"
          "多卡测试可在脚本内 SDAA_VISIBLE_DEVICES=<N,M> 显式指定，计时窗口禁 co-tenancy；卡壳先查根因判例库 /root/tp-agent/playbooks/INDEX.md（按症状检索再动手）。")

def log(m): open(LOGF, "a").write(time.strftime("[%F %T] ") + str(m) + "\n")

def q(sql):
    return subprocess.run(PG + [sql], capture_output=True, text=True, env=PGENV).stdout.strip()

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

def statuses(ids):
    if not ids: return {}
    out = q("SELECT id||'='||status FROM tensor_dev_missions WHERE id IN (" + ",".join(map(str, ids)) + ")")
    st = {}
    for line in out.splitlines():
        if "=" in line:
            i, _, v = line.partition("="); st[int(i)] = v.strip()
    return st

def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {"sessions": [], "consec_fail": 0, "halt": None}

def save_state(st):
    STATE_F.write_text(json.dumps(st, ensure_ascii=False, indent=1))

def write_status(st, extra=""):
    STATUS_F.write_text(json.dumps({"ts": time.strftime("%F %T"), "state": st, "note": extra},
                                   ensure_ascii=False, indent=1))

OWNER_DIRECTIVES = """## owner 指令（2026-10-30 放行，长期有效）
1. 议事会常态化：波次计划产出后直接机器入池派发，不经 owner 逐单审批；任务不钉模型，worker 自助领取。
2. 软件功能成熟化（owner 原话「我们还需要开发相应的软件功能，目前还不成熟」）：每波必须含 ≥2 条真功能开发单
   （repo=TensorPlatform 或 ship-platform；交付=代码+测试+文档，端到端可用，不是一次性脚本演示；
   候选方向：工况批量调度/样本库管理/优化工作流/谱系追溯查询/复现一键/看板页面/报告自动汇编——议事会自定优先级）。
3. 指南 KPI 缺口冲刺（见下方 KPI 现状）：千工况/万样本/10 对标算例/精度实测/计时基线。
4. 纪律：honest-FAIL 不可覆盖；判据必须机器可裁；预算帽 140 调用/5400s；开发项成熟（succeeded/验收过）由 tp_pusher 自动推 agent 分支上 GitHub（owner 2026-10-30「每项开发假如成熟了就推送github」），main 合并与 PR 仍归 owner，秘密绝不入仓库；
   triton 无 SDAA 后端（只能 CPU 解释，绕行须如实记录）；报告纪律照旧（工件+论文级报告）。
5. 平台定位三层（owner 2026-11-01 纠偏+裁判法）：TensorPlatform=智能体运行共性平台（任务/席位/审批/报告/
   审计/验收机制），不背任何行业板块；领域业务归各自平台——船舶业务=ship-performance-platform
   （本地私有仓 :8002，owner 裁定不上 GitHub），求解器物理=tensor 系列库。船舶功能开发单 repo 必须
   标 ship-performance-platform；落 TensorPlatform 仓的船舶功能代码=错标（边界审计报告 1388，
   G1-G3 机器门将拒）。
6. 四板块语义仅对 repo=ship-performance-platform 的任务生效（owner 2026-11-01：四板块是 ship 平台
   自有板块定义，非 TensorPlatform 定位）。ship 侧优先级与操作细节照旧：
   ①性能模型验证=标准试验基准（ITTC/KVLCC2/KCS/DTMB/Series60）全量精度对标+修已知缺陷
     （potential-flow 500=Re 负数、propulsion/design schema 不匹配、heave/pitch RAO 同数组、
     form factor/CA 实船换算修正 5.4%→≤5%）；
   ②CAE=tensor 系列替换 ship 演示级 CAE 端点（势流会师=TensorBEM v0.5.0 做引擎）；
   ③数据治理与ML=以共性平台真件做实（样本库 m378/优化库 m377/谱系-门禁-报告三层）；
   ④设计=TensorCAD+geometry 会师，全参数化船体建模（几何验收对标标准船型）。
   资产清点台账续用（INV 五单 m419-m423，work/m*/docs/inventory/ + artifacts/inventory_*.json），
   R1 提案先读对应板块台账，优化单从其提案队列取，不重复清点。
7. 质量复核转向（owner 2026-10-31 批准，长期有效）：进度主指标不再是 PR/合并数量，
   转向「已合并功能的跨项目质量复核」。已合并≠已验证：实现+自测+自报告可能共享同一错误，
   验收必须引独立参考（解析解/文献实验数据/异离散方法交叉验证），自洽测试不算独立验证。
   P0：TensorPlatform 真实任务闭环自证（派发→求解→产物落库→验证→API 回读，全链台账可回放）；
       TensorLBM/TensorSPH 液滴入水与自由面独立误差指标（对标文献实验数据）；TensorFVM 仓库体积
       审计+新库迁移方案（PR#22 删 1.78GiB 后 server-side 仍 1188MiB；owner 已定=新库浅历史+
       旧库归档只读）。P1：TensorDEM/TensorBEM 断裂能量/冲量/RAO 独立验证依据；TensorDG 收敛阶/
       激波振荡/matrix-free scaling；TensorCAD 局部编辑保真度+下游网格质量+STEP 往返误差。
   系统性佐证（同类审计推广）：TensorSPH#9 Morris 黏性缺因子2（早期算例物理半强度）、
   #10 假门禁（mass_conserved 张量自比较）——「能跑」不等于「物理对」，各库按此标准自查。
   看板同步：guide_tracker 增「已合并功能复核通过率」KPI（独立参考复核通过/复核总数）。

8. 对标吸收方针（owner 2026-10-31 定：先吸收长处达到，再创新超越）：有世界顶级对标的项目按此路径推进——
   OpenFOAM 验证体系（tutorial+文献参考+门禁三件套）→ 各库 validation/ 框架；deal.II 收敛表纪律 → 收敛阶
   自动断言+GCI；NASA TMR → TensorFVM 验证算例族；CAESES 参数化方法论 → TensorCAD；AlphaEvolve 生成器/
   验证器分离 → held-out 出题+独立验收；SWE-bench → fleet 能力基线。达到=对标件判据同级复现；超越=判据
   严于对标件（门必须能失败+判例库回流）。每波 ≥1 条对标吸收单（先导八单 对标吸收①-⑧ 已直派 m533-540）。
"""

def build_context(n):
    d = pathlib.Path(CROOT / f"session-{n:03d}"); d.mkdir(parents=True, exist_ok=True)
    lines = [f"# 议事会 session-{n:03d} 上下文（自动生成 {time.strftime('%F %T')}，数字 SQL/看板直取）", ""]
    lines.append(OWNER_DIRECTIVES)
    # 指南 KPI 现状
    try:
        dash = json.loads(pathlib.Path("/root/tp-agent/guide_dashboard.json").read_text())
        lines.append("## 指南 KPI 现状（guide_tracker 快照）\n")
        lines.append("| 指标 | 现状 | 进度 |")
        lines.append("|---|---|---|")
        for k in dash["kpis"]:
            lines.append(f"| {k['kpi']} | {k['cur']} | {k['pct'] if k['pct'] is not None else '—'}% |")
        m = dash["meta"]
        lines.append(f"\n运行面：利用率 {m['busy']}/{m['seats']}，在飞 {m['inflight']}，成功率 {m['rate']}%。\n")
    except Exception as e:
        lines.append(f"(guide_dashboard 不可读: {e})\n")
    # worker 信用（credit_ledger）
    try:
        led = json.loads(pathlib.Path("/root/tp-agent/credit_ledger.json").read_text())
        top = sorted(led.get("workers", []), key=lambda w: -w.get("succ7", 0))[:8]
        lines.append("## worker 信用（近 7d；QR=独立复核单归属）\n")
        lines.append("| worker | 角色 | 7d成功 | 7d失败 | 7d率 | 累计 | QR认领 | QR成 |\n|---|---|---|---|---|---|---|---|")
        for w in top:
            lines.append(f"| {w['worker']} | {w.get('role','-')} | {w.get('succ7',0)} | {w.get('fail7',0)} | "
                         f"{w.get('rate7') if w.get('rate7') is not None else '—'} | {w.get('succ_total',0)} | "
                         f"{w.get('qr_claimed',0)} | {w.get('qr_succ',0)} |")
        lines.append("")
    except Exception as e:
        lines.append(f"(credit_ledger 不可读: {e})\n")
    # 根因判例库索引
    try:
        idx = pathlib.Path("/root/tp-agent/playbooks/INDEX.md").read_text()
        lines.append("## 根因判例库索引（卡壳先查；全文 docs/playbooks/ 或 /root/tp-agent/playbooks/）\n")
        lines.append(idx.strip()[:1500] + "\n")
    except Exception as e:
        lines.append(f"(playbooks 不可读: {e})\n")
    # 上一波战果
    st = load_state()
    if st["sessions"]:
        last = st["sessions"][-1]
        if last.get("wave_ids"):
            ss = statuses(last["wave_ids"])
            ok = sum(1 for v in ss.values() if v == "succeeded")
            lines.append(f"## 上一波（session-{last['n']:03d}）战果：{ok}/{len(last['wave_ids'])} 成功\n")
        # 上次议事会的「下次议题」建议
        prev_plan = CROOT / f"session-{last['n']:03d}" / "r3-plan.md"
        if prev_plan.exists():
            txt = prev_plan.read_text()
            i = txt.find("下次议事会建议议题")
            if i >= 0:
                lines.append("### 上次议事会留给本场的议题建议\n" + txt[i:i + 800] + "\n")
    (d / "00_context.md").write_text("\n".join(lines))
    log(f"context built session-{n:03d} ({len(lines)} lines)")

def launch_session(n):
    build_context(n)
    board = CROOT / f"session-{n:03d}"
    lf = open(board / "council.log", "a")
    p = subprocess.Popen(["python3", "/root/tp-agent/council_run.py", "--session", str(n)],
                         stdout=lf, stderr=lf, start_new_session=True,
                         env={**os.environ, "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/tecoai/bin"})
    log(f"session-{n:03d} launched pid={p.pid}")
    return p.pid

def dispatch_wave(n):
    board = CROOT / f"session-{n:03d}"
    plan = json.loads((board / "r3_plan.json").read_text())
    ms = plan.get("missions", [])
    if not ms:
        log(f"session-{n:03d} r3_plan.json 空 missions —— 不派发"); return []
    tok = login()
    ids, skipped = [], []
    for m in ms[:MAX_WAVE]:
        repo = m.get("repo", "")
        if repo not in REPOS:
            skipped.append(f"{m.get('key','?')}(repo={repo})"); continue
        b = m.get("budget", {}) or {}
        budget = {"max_tool_calls": min(int(b.get("max_tool_calls", MAX_CALLS)), MAX_CALLS),
                  "max_wall_seconds": min(int(b.get("max_wall_seconds", MAX_WALL)), MAX_WALL),
                  "max_tokens": min(int(b.get("max_tokens", MAX_TOKENS)), MAX_TOKENS)}
        goal = m["goal"].rstrip() + FOOTER
        s, d = api("POST", "/tensor/missions",
                   {"goal": goal, "repo": repo, "runner": "native", "origin": "owner", "budget": budget}, tok)
        if s in (200, 201):
            ids.append(d["id"]); log(f"wave m{d['id']} [{m.get('key','?')}] repo={repo} 自助领取池")
        else:
            skipped.append(f"{m.get('key','?')}(POST {s})")
    if len(ms) > MAX_WAVE: skipped.append(f"超帽截除 {len(ms)-MAX_WAVE} 条")
    if skipped: log(f"session-{n:03d} 截除/跳过: {skipped}")
    return ids

def fallback_rows(rows, max_wave=MAX_WAVE):
    """把 campaign_nodes 就绪行（SQL 结果）转成派发载荷列表（纯函数，可单测）。

    row = [node_id, plan_id, ext_id, repo, goal, acceptance, plan_status]
    纪律：repo 白名单外跳过；缺 goal 跳过；只取前 max_wave 条（预算帽）。
    """
    out, skipped = [], []
    for r in rows or []:
        r = (list(r) + [""] * 7)[:7]
        nid, pid, ext, repo, goal, accept, pst = r
        repo = {"ship-platform": "ship-performance-platform",
                "TensorFEM": "TensorPhysics"}.get(repo, repo)
        if repo not in REPOS:
            skipped.append(f"node{nid}(repo={repo})")
            continue
        if not (goal or "").strip():
            skipped.append(f"node{nid}(空 goal)")
            continue
        crit = ""
        acc = (accept or "").strip()
        if acc not in ("", "[]", "null", "None"):   # 空数组=节点没写判据，不注空标题
            crit = f"\n★节点机器验收判据（campaign_nodes.acceptance_criteria）：{acc[:1200]}"
        out.append({"task_key": f"{FALLBACK_TASK_PREFIX}{nid}",
                    "repo": repo,
                    "goal": goal.strip() + crit + FOOTER,
                    "budget": {"max_tool_calls": MAX_CALLS, "max_wall_seconds": MAX_WALL,
                               "max_tokens": MAX_TOKENS},
                    "node": int(nid), "plan": int(pid) if str(pid).isdigit() else pid,
                    "ext_id": ext})
        if len(out) >= max_wave:
            skipped.append(f"超帽截除 {len(rows) - len(out)} 条")
            break
    return out, skipped


def ready_nodes_sql(max_wave=MAX_WAVE):
    """就绪节点 SQL：active 计划里未终态、且从未派过（task_key 幂等）的节点。"""
    return ("SELECT n.id, n.plan_id, coalesce(n.metadata->>'ext_id',''), "
            "coalesce(n.metadata->>'repo',''), coalesce(n.metadata->>'goal',''), "
            "coalesce(n.metadata->>'acceptance_criteria',''), coalesce(p.status,'active') "
            "FROM campaign_nodes n JOIN campaign_plans p ON p.id = n.plan_id "
            "WHERE coalesce(p.status,'active') = 'active' "
            "AND n.status IN ('in-flight','blocked') "
            "AND NOT EXISTS (SELECT 1 FROM tensor_dev_missions m "
            f"                WHERE m.task_key = '{FALLBACK_TASK_PREFIX}' || n.id) "
            f"ORDER BY n.plan_id DESC, n.id LIMIT {int(max_wave)}")


def dispatch_fallback_wave(n, api_fn=None, q_fn=None, login_fn=None, logf=None):
    """无议事会兜底派发（渐退令替身）：session 挂了也不让任务池空转。

    返回派出的 mission id 列表（可能为空=池里真没活）。
    """
    api_fn, q_fn, login_fn, logf = api_fn or api, q_fn or q, login_fn or login, logf or log
    try:
        rows = q_fn(ready_nodes_sql())
    except Exception as e:
        logf(f"session-{n:03d} 兜底派发探测失败 {type(e).__name__}: {str(e)[:150]}")
        return []
    if not rows:
        return []
    payloads, skipped = fallback_rows(rows)
    if skipped:
        logf(f"session-{n:03d} 兜底截除/跳过: {skipped[:6]}")
    try:
        tok = login_fn()
    except Exception as e:
        logf(f"session-{n:03d} 兜底派发登录失败 {type(e).__name__}: {str(e)[:120]}")
        return []
    ids = []
    for pl in payloads:
        code, d = api_fn("POST", "/tensor/missions",
                         {"goal": pl["goal"], "repo": pl["repo"], "runner": "native",
                          "origin": "agent", "task_key": pl["task_key"],
                          "budget": pl["budget"]}, tok)
        if code in (200, 201) and (d.get("id") or (d.get("mission") or {}).get("id")):
            mid = d.get("id") or (d.get("mission") or {}).get("id")
            ids.append(int(mid))
            logf(f"fallback-wave m{mid} [{pl['task_key']}] node={pl['ext_id']} "
                 f"repo={pl['repo']}（无议事会兜底，plan {pl['plan']}）")
        else:
            logf(f"fallback-wave 跳过 {pl['task_key']}（POST {code}）")
    return ids


def main():
    st = load_state()
    log("wave_cycle 启动（议事会常态化+自助领取，owner 2026-10-30 放行）")
    while True:
        try:
            if st.get("halt"):
                write_status(st, f"HALTED: {st['halt']}（仅显式 owner 令；议事会连败自动转兜底模式）")
                time.sleep(POLL); continue
            inflight = int(q("SELECT count(*) FROM tensor_dev_missions WHERE status IN ('queued','running')") or 0)
            cur = st["sessions"][-1] if st["sessions"] else None
            if cur is None:  # 首发 session-002（001 为手跑史前场）
                st["sessions"].append({"n": 2, "pid": launch_session(2), "started": time.time(),
                                       "wave_ids": [], "wave_dispatched": None, "wave_done": None})
                save_state(st); continue
            n, board = cur["n"], CROOT / f"session-{cur['n']:03d}"
            bs = json.loads((board / "state.json").read_text()) if (board / "state.json").exists() else {}
            phase = bs.get("phase") or "starting"   # 无 state.json=council_run 刚起步，按在飞处理
            timed_out = time.time() - cur["started"] > 3 * 2700 + 900
            if phase in ("starting", "running") and not timed_out:
                write_status(st, f"session-{n:03d} 议事中 R{bs.get('round', '?')}")
                time.sleep(POLL); continue
            if timed_out and phase in ("starting", "running"):
                log(f"session-{n:03d} 超时熔断（phase={phase}，按终局处理）")
            # 议事终局：派波或记失败
            if not cur["wave_dispatched"]:
                if (board / "r3_plan.json").exists() and inflight < INFLIGHT_GUARD:
                    cur["wave_ids"] = dispatch_wave(n)
                    cur["wave_dispatched"] = time.time()
                    st["consec_fail"] = 0
                    save_state(st); continue
                if (board / "r3_plan.json").exists() and inflight >= INFLIGHT_GUARD:
                    write_status(st, f"session-{n:03d} 计划就绪，等 in-flight<={INFLIGHT_GUARD}（现 {inflight}）")
                    time.sleep(POLL); continue
                # 无 plan_json：议题会失败场 → 先走**session 无关兜底派发**（渐退令），
                # 有活可派就当这一波成立；真没活才计失败，且**绝不熔断等人**。
                fids = dispatch_fallback_wave(n) if inflight < INFLIGHT_GUARD else []
                if fids:
                    cur["wave_ids"] = fids
                    cur["wave_dispatched"] = time.time()
                    cur["fallback"] = True
                    st["consec_fail"] = 0
                    log(f"session-{n:03d} 无议事会计划 → 兜底派发 {len(fids)} 单"
                        f"（session 无关机制替身，不依赖人类/AI 协调者会话）")
                    save_state(st); continue
                st["consec_fail"] += 1
                log(f"session-{n:03d} 无 r3_plan.json 且兜底无活可派，失败计 {st['consec_fail']}")
                if st["consec_fail"] >= HALT_AFTER_FAILS and not st.get("fallback_mode"):
                    st["fallback_mode"] = True
                    log(f"议事会连败 {st['consec_fail']} 场 → 转兜底模式（不熔断、不等人工）："
                        f"每轮只靠 campaign_nodes 就绪节点续火，议事会自愈即回主轨")
                cur["wave_dispatched"] = "failed"
                save_state(st); continue
            # 波在飞：判收官
            if cur["wave_done"] is None and cur["wave_dispatched"] != "failed":
                if cur["wave_ids"]:
                    ss = statuses(cur["wave_ids"])
                    term = [v for v in ss.values() if v in ("succeeded", "failed", "cancelled")]
                    if len(term) == len(cur["wave_ids"]):
                        cur["wave_done"] = time.time()
                        ok = sum(1 for v in ss.values() if v == "succeeded")
                        log(f"session-{n:03d} 波收官 {ok}/{len(cur['wave_ids'])}")
                        save_state(st); continue
                write_status(st, f"session-{n:03d} 波在飞 ids={cur['wave_ids']}")
                time.sleep(POLL); continue
            # 波已收官：冷却+间隔→开下一场
            wd = cur.get("wave_done") or 0
            hb_ts = float(st.get("last_hb", 0) or 0)
            if time.time() - hb_ts > HEARTBEAT_SECS:
                st["last_hb"] = time.time()
                save_state(st)
                log(f"idle-heartbeat session-{n:03d} phase=冷却 fallback_mode="
                    f"{bool(st.get('fallback_mode'))} consec_fail={st['consec_fail']} "
                    f"in-flight={inflight}（机制存活；日志不再是空转黑箱）")
            ready = (wd > 0 and time.time() - wd > COOLDOWN
                     and time.time() - cur["started"] > MIN_INTERVAL and inflight < INFLIGHT_GUARD)
            if cur["wave_dispatched"] == "failed":
                ready = time.time() - cur["started"] > MIN_INTERVAL and inflight < INFLIGHT_GUARD
            if ready:
                if st.get("fallback_mode"):
                    fids = dispatch_fallback_wave(n + 1) if inflight < INFLIGHT_GUARD else []
                    if fids:
                        st["sessions"].append({"n": n + 1, "pid": None, "started": time.time(),
                                               "wave_ids": fids, "wave_dispatched": time.time(),
                                               "wave_done": None, "fallback": True})
                        save_state(st)
                        log(f"fallback-wave session-{n+1:03d} 兜底派发 {len(fids)} 单（连续兜底巡航）")
                        continue
                st["sessions"].append({"n": n + 1, "pid": launch_session(n + 1), "started": time.time(),
                                       "wave_ids": [], "wave_dispatched": None, "wave_done": None})
                save_state(st)
            else:
                write_status(st, f"session-{n:03d} 冷却中（in-flight {inflight}）")
        except Exception as e:
            log(f"轮异常 {type(e).__name__}: {e}")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
