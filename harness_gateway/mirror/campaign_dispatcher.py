#!/usr/bin/env python3
"""campaign_dispatcher v5（owner 2026-10-31 自主播种三连令）
「应该由上面的智能体播种，不应该只靠外部推动」「任务没有了应该促发他们派发或者思考新任务，
不应该是 workflow 型，应该是自主思考的」「可以冗余，多渠道」「相互扶持」。
30s 循环：SYNC（mission 终态→node 终态/infra 免费重排/里程碑进度）
        → DISPATCH（契约并行定点迭代，多计划）
        → AUTONOMY（收官/池空双渠道触发**提案波**：K 个异构模型提案者独立思考
          → 仲裁者聚合去重播种 draft 战役 → 本守护**机器门**验证后激活）。
战役内容 100% 由上层智能体生成；本守护只编排「谁思考」，不写「思考什么」。
"""
import json, os, subprocess, time, urllib.request, urllib.error, datetime

API = os.getenv("TP_API", "http://127.0.0.1:8010")
PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-t", "-A",
        "-F", "\x02", "-c"]   # 字段分隔符 chr(2)：result/goal 文本含 | 时 split("|") 会撑爆解包（m727 事故）
LOG = "/var/log/tp-fleet/campaign_dispatcher.log"
PROPOSAL_POOL = "/root/tp-agent/proposals"       # 相互扶持：全舰队可读提案池
FREE_STATE = "/root/tp-agent/autonomy_free.json" # 池空自由波状态（无挂靠计划时）
BUDGET = {"max_tokens": 5000000, "max_tool_calls": 220, "max_wall_seconds": 7200}
PLAN_ID = 1
TERMINAL = {"succeeded", "failed", "stopped", "revoked"}
READY = {"done", "in-flight"}  # 契约并行：in-flight 也算就绪
NODE_TERMINAL = {"done", "failed", "blocked"}

PROPOSER_MODELS = ["qwen3.7-plus", "glm-5.1", "deepseek-v4-flash"]  # 异构独立思考（中继实测活）
ARBITER_MODEL = "qwen3.6-plus"
K_PROPOSERS = 3          # 冗余多渠道：允许重叠，仲裁聚合
MAX_WAVES = 3            # 每计划提案波上限（防无限烧预算）
MAX_ARBITER_ATTEMPTS = 2 # 仲裁者每波重试上限
MIN_NODES, MAX_NODES = 5, 12
REPO_WHITELIST = {"TensorPhysics", "TensorDEM", "TensorLBM", "TensorPlatform", "ship-performance-platform",
                   "TensorFEM", "TensorBEM", "TensorSPH", "TensorDG", "TensorCAD", "TensorFVM"}  # owner 2026-11-07 联动令：扩十仓（TensorFEM 真仓活跃，旧并入教义废止）
REPO_ALIAS = {"ship-platform": "ship-performance-platform"}   # 历史别名（TensorFEM 并入教义 2026-11-07 废止：真仓活跃）

REPO = {  # 旧节点 ext_id → repo（v5 起新节点一律走 node metadata.repo）
    "F01": "TensorPhysics", "F02": "TensorPhysics", "F03": "TensorPhysics", "F08": "TensorPhysics",
    "F04": "TensorDEM", "F05": "TensorDEM", "F06": "TensorDEM", "F07": "TensorDEM",
    "V01": "TensorPhysics", "V04": "TensorPhysics", "V02": "TensorDEM", "V03": "TensorDEM",
    "H01": "TensorLBM", "H02": "TensorLBM", "H03": "TensorLBM", "E01": "TensorLBM", "E02": "TensorLBM",
    "I01": "TensorPlatform", "I02": "TensorDEM", "I03": "TensorPlatform", "I04": "TensorPlatform",
    "P01": "TensorPhysics", "P02": "TensorPhysics",
    "M01": "ship-performance-platform", "M02": "TensorDEM",
    "X01": "ship-performance-platform", "X02": "TensorPlatform",
}


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def q(sql):
    r = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("SQL: " + r.stderr.strip()[:200])
    return [ln.split("\x02") for ln in r.stdout.strip().split("\n") if ln]


def get_token():
    # 2026-11-02 修：auto-login 已被 #500 全站禁写处决——改正式账密（creds 600），
    # auto-login 仅作最后回退（预期 404/403）。
    try:
        creds = json.load(open("/root/.eco_admin_creds.json"))
        req = urllib.request.Request(API + "/api/v1/auth/login",
                                     data=json.dumps(creds).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read().decode() or "{}")
            if d.get("access_token"):
                return d["access_token"]
    except Exception:
        pass
    for path in ("/api/v1/auth/auto-login", "/auth/auto-login"):
        try:
            req = urllib.request.Request(API + path, data=b"{}", method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read().decode() or "{}")
                if d.get("access_token"):
                    return d["access_token"]
        except Exception:
            continue
    return None


def api(path, data, token):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def jbd(obj):
    return "$jd$" + json.dumps(obj, ensure_ascii=False) + "$jd$::jsonb"


_EV_ALLOWED = {"node_status_change", "milestone_status_change", "block_detected",
               "block_resolved", "spinning_detected", "escalation_created",
               "snapshot_taken", "plan_updated", "mission_dispatched",
               "mission_completed", "guerrilla_detected"}
_EV_MAP = {"node_done": "mission_completed", "node_requeued": "node_status_change",
           "node_blocked": "node_status_change", "node_blocked_permanent": "block_detected"}


def event(etype, detail, pid=PLAN_ID):
    # CHECK 白名单映射（2026-10-31 事故教训：类型不合法会中断整个 SYNC 周期）
    et = _EV_MAP.get(etype, etype)
    if et not in _EV_ALLOWED:
        et = "plan_updated"
    try:
        q(f"INSERT INTO campaign_events (organization_id, plan_id, event_type, detail) "
          f"VALUES (1, {int(pid)}, '{et}', {jbd(detail)})")
    except Exception as e:
        log(f"WARN event {etype}→{et} 写入失败（不中断）: {str(e)[:120]}")


def _crit_txt(meta):
    crit = meta.get("acceptance_criteria") or []
    return "; ".join(
        f"{c.get('metric')} {c.get('operator')} {c.get('threshold')}[{c.get('unit')}]"
        for c in crit if isinstance(c, dict)) or "见战役表 metadata"


def build_goal(ext, name, meta, deps_done, deps_live, attempt):
    goal = meta.get("goal", "")   # TensorFEM 字符串改写已随并入教义废止一并撤销
    repo = meta.get("repo") or REPO.get(ext, "TensorPlatform")
    repo = REPO_ALIAS.get(repo, repo)
    parts = [f"【战役 DAG 节点|{ext}|{name}】（上层智能体播种的自主战役，"
             f"物理域 {meta.get('physics_domain', '')}，风险 {meta.get('risk', '')}，层 {meta.get('layer', '')}）。"
             f"{goal}。落库 {repo}（若模块不存在则按领域目录新建，附 pytest 用例）。",
             f"机器验收判据（全部满足，判据数字与产物路径落报告 JSON）：{_crit_txt(meta)}。",
             "纪律：全新前沿节点（禁止重复 verified/ 已验证案例）；worktree 内提交自己分支；"
             "论文级报告（物理表述+图+判据数字+审计链+局限）；honest FAIL 不可覆盖。"]
    if deps_done:
        refs = "；".join(
            f"{de}=平台任务 m{mid}（同库：git fetch /root/tp-agent/work/m{mid} agent/m{mid}；"
            f"跨库：worktree 只读参考其验收报告）" for de, mid in deps_done)
        parts.append(f"上游已完成：{refs}。先读上游接口定义与验收报告再集成，禁止重做上游已完成的工作。")
    if deps_live:
        lines = []
        for de, mid, drepo, dmeta in deps_live:
            lines.append(f"- {de}（在飞 m{mid}，{drepo}）：目标「{dmeta.get('goal', '')}」；"
                         f"验收「{_crit_txt(dmeta)}」")
        parts.append("上游在飞（契约并行模式，不要等待）：按上述上游目标与验收判据**推定接口契约**"
                     "（数据结构/单位/调用面/文件格式），把你认定的契约写进报告 JSON 字段 interface_contract；"
                     "所有对上游的依赖收窄到显式**适配层**（adapter 模块），上游落地后若接口漂移只改适配层。\n"
                     + "\n".join(lines))
    if meta.get("hollow_requeue"):
        parts.append("（成果落盘警告：本节点前次尝试实质完成但零 commit 被判空转成功、成果未银行。"
                     "本次无论复用还是重做，finish 前必须把 worktree 全部新增/修改文件 git add -A && git commit "
                     "提交到任务分支——测试再绿，零新 commit 一律判失败。）")
    if attempt >= 2:
        parts.append(f"（第 {attempt} 次尝试：先读任务报告吸取教训，修正后重做，不要原样重跑。）")
    return "\n".join(parts)


def _hollow(mid, started_ep):
    """成果落盘门（m763 事故 2026-10-31；v5.4 竞态修正）：任务报成功但零提交=空转成功。
    修正：tp_pusher 会自动收编未跟踪成果（m779 实证 09:02 银行化晚于 08:57 判罚）——
    有实质未跟踪/未提交文件（≥1KB，排除 MISSION_COMPLETE.md/__pycache__）=「待银行」不算空转。
    查不了/无 started/无 work 目录一律不误伤（返回 False）。"""
    try:
        if not started_ep or int(started_ep) <= 0:
            return False
        d = f"/root/tp-agent/work/m{int(mid)}"
        r = subprocess.run(["git", "-C", d, "log", "--oneline", f"--since=@{int(started_ep)}"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or r.stdout.strip():
            return False   # 有提交=非空转（或 git 查不了=不误伤）
        # 零提交：看是否有实质未银行成果（pusher 会收编）
        s = subprocess.run(["git", "-C", d, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=15)
        if s.returncode == 0:
            for ln in s.stdout.splitlines():
                p = ln[3:].strip().strip('"')
                if not p or "__pycache__" in p or p.endswith("MISSION_COMPLETE.md"):
                    continue
                try:
                    if os.path.getsize(os.path.join(d, p)) >= 1024:
                        return False   # 实质成果待银行（pusher 收编），非空转
                except OSError:
                    continue   # 已删/重命名行等：跳过
        return True
    except Exception:
        return False


def sync_phase():
    rows = q("SELECT n.id, coalesce(n.metadata->>'ext_id','?'), n.mission_id, m.status, "
             "coalesce(n.consecutive_fails,0), coalesce(m.result->>'stop_condition',''), "
             "coalesce(m.result->'evidence'->'checks'->>'fail_stage',''), "
             "coalesce(m.result->'evidence'->'summary'->>'tool_calls','0'), "
             "coalesce(n.metadata->>'infra_retries','0'), "
             "coalesce(extract(epoch from m.created_at)::bigint::text,'0') FROM campaign_nodes n "
             "JOIN tensor_dev_missions m ON m.id = n.mission_id "
             "WHERE n.status='in-flight' AND m.status IN ('succeeded','failed','stopped','revoked')")
    for nid, ext, mid, mst, fails, stop, stage, tcs, infr, sep in rows:
        fails = int(fails or 0)
        # 基础设施失败（worker 猝死/LLM 中继瞬时）不烧任务尝试预算：免费重排，上限 4 次
        if mst == "failed" and (stop == "agent_lost" or (stage == "llm_error" and int(tcs or 0) == 0)):
            if int(infr or 0) < 4:
                q(f"UPDATE campaign_nodes SET status='pending', mission_id=NULL, "
                  f"metadata = jsonb_set(coalesce(metadata,'{{}}'::jsonb), '{{infra_retries}}', "
                  f"to_jsonb({int(infr or 0) + 1})) WHERE id={nid}")
                event("node_requeued", {"node": ext, "mission_id": int(mid),
                                        "infra": True, "cause": stop or stage,
                                        "infra_retry": int(infr or 0) + 1})
                log(f"SYNC node {ext} INFRA({stop or stage}) → free requeue #{int(infr or 0) + 1}")
            else:
                q(f"UPDATE campaign_nodes SET status='blocked', actual_end=now(), "
                  f"block_reason='infra x{int(infr or 0) + 1} ({stop or stage})' WHERE id={nid}")
                event("node_blocked", {"node": ext, "infra_exhausted": True})
                log(f"SYNC node {ext} BLOCKED infra x{int(infr or 0) + 1}")
            continue
        if mst == "succeeded":
            # 成果落盘门：报成功但任务时段零提交=空转成功（m763 型），按任务失败重排/熔断
            if _hollow(mid, sep):
                if fails + 1 < 2:
                    q(f"UPDATE campaign_nodes SET status='pending', mission_id=NULL, "
                      f"consecutive_fails={fails + 1}, "
                      f"metadata = jsonb_set(coalesce(metadata,'{{}}'::jsonb), '{{hollow_requeue}}', 'true'::jsonb) "
                      f"WHERE id={nid}")
                    event("node_requeued", {"node": ext, "mission_id": int(mid),
                                            "hollow_success": True, "attempt": fails + 2})
                    log(f"SYNC node {ext} 空转成功（零提交）→ requeue attempt {fails + 2}")
                else:
                    q(f"UPDATE campaign_nodes SET status='blocked', actual_end=now(), "
                      f"consecutive_fails={fails + 1}, block_reason='hollow success x2' WHERE id={nid}")
                    event("node_blocked", {"node": ext, "hollow_success": True})
                    log(f"SYNC node {ext} BLOCKED 空转成功 x2")
                continue
            q(f"UPDATE campaign_nodes SET status='done', actual_end=now(), consecutive_fails=0 "
              f"WHERE id={nid}")
            event("node_done", {"node": ext, "mission_id": int(mid)})
            log(f"SYNC node {ext} done (m{mid})")
        else:
            if fails + 1 < 2:
                q(f"UPDATE campaign_nodes SET status='pending', mission_id=NULL, "
                  f"consecutive_fails={fails + 1} WHERE id={nid}")
                event("node_requeued", {"node": ext, "mission_id": int(mid),
                                        "mission_status": mst, "attempt": fails + 2})
                log(f"SYNC node {ext} {mst} → requeue attempt {fails + 2}")
            else:
                q(f"UPDATE campaign_nodes SET status='blocked', actual_end=now(), "
                  f"consecutive_fails={fails + 1}, "
                  f"block_reason='mission {mst} x{fails + 1}' WHERE id={nid}")
                event("node_blocked", {"node": ext, "mission_id": int(mid),
                                       "mission_status": mst})
                log(f"SYNC node {ext} BLOCKED ({mst} x{fails + 1})")
    for mid_, tot, done in q(
            "SELECT m.id, count(n.id), count(n.id) FILTER (WHERE n.status='done') "
            "FROM campaign_milestones m LEFT JOIN campaign_nodes n ON n.milestone_id=m.id "
            "GROUP BY m.id ORDER BY m.id"):
        pct = int(100 * int(done) / max(int(tot), 1))
        st = "done" if pct == 100 else "in-flight"   # CHECK 枚举修复：done（非 completed）
        q(f"UPDATE campaign_milestones SET progress_pct={pct}, status='{st}'"
          + (", actual_end=now()" if st == "done" else "") + f" WHERE id={mid_}")


def dispatch_one(nodes, st, nid, r, token):
    _, ext, name, status, mission_id, deps_txt, meta_txt, fails, plan_id = r
    try:
        deps = [int(x) for x in deps_txt.strip("{}").split(",") if x]
    except ValueError:
        deps = []
    meta = json.loads(meta_txt) if meta_txt != "{}" else {}
    deps_done = [(nodes[d][1], int(nodes[d][4])) for d in deps
                 if st.get(d) == "done" and nodes[d][4] != "-"]
    deps_live = [(nodes[d][1], int(nodes[d][4]),
                  (json.loads(nodes[d][6]) or {}).get("repo") or REPO.get(nodes[d][1], "?"),
                  json.loads(nodes[d][6]) if nodes[d][6] != "{}" else {})
                 for d in deps if st.get(d) == "in-flight" and nodes[d][4] != "-"]
    task_key = f"p{plan_id}-{ext}-a{int(fails or 0) + 1}"
    repo = meta.get("repo") or REPO.get(ext, "TensorPlatform")
    repo = REPO_ALIAS.get(repo, repo)
    payload = {"goal": build_goal(ext, name, meta, deps_done, deps_live, int(fails or 0) + 1),
               "repo": repo, "runner": "native",
               "origin": "owner", "task_key": task_key, "budget": BUDGET}
    code, d = api("/api/v1/tensor/missions", payload, token)
    mid = d.get("id") or (d.get("mission") or {}).get("id")
    if code == 409:
        hit = q(f"SELECT id FROM tensor_dev_missions WHERE task_key='{task_key}' "
                f"ORDER BY id DESC LIMIT 1")
        mid = int(hit[0][0]) if hit else None
    if mid is None:
        log(f"DISPATCH {ext} FAIL http={code} {json.dumps(d, ensure_ascii=False)[:200]}")
        return None
    mode = "contract" if deps_live else "strict"
    q(f"UPDATE campaign_nodes SET mission_id={mid}, status='in-flight', "
      f"actual_start=now() WHERE id={nid}")
    event("mission_dispatched", {"node": ext, "mission_id": int(mid),
                                 "repo": repo, "plan": int(plan_id),
                                 "attempt": int(fails or 0) + 1, "mode": mode}, pid=int(plan_id))
    log(f"DISPATCH {ext} → m{mid} ({repo}, {mode}, plan {plan_id})")
    return int(mid)


def dispatch_phase(token):
    _sql = (
        "SELECT n.id, coalesce(n.metadata->>'ext_id','?'), n.name, n.status, "
        "coalesce(n.mission_id::text,'-'), coalesce(n.dependencies::text,'{}'), "
        "coalesce(n.metadata::text,'{}'), coalesce(n.consecutive_fails,0), n.plan_id "
        "FROM campaign_nodes n JOIN campaign_plans p ON p.id=n.plan_id "
        "WHERE coalesce(p.status,'active')='active'")
    nodes = {int(r[0]): r for r in q(_sql)}
    st = {nid: r[3] for nid, r in nodes.items()}
    # 定点迭代：本周期内派发即视为 in-flight，立即解锁下游（激进全链铺开）
    for _ in range(len(nodes) + 1):
        progressed = False
        for nid, r in nodes.items():
            if st.get(nid) != "pending" or r[4] != "-":
                continue
            try:
                deps = [int(x) for x in r[5].strip("{}").split(",") if x]
            except ValueError:
                deps = []
            if deps and not all(st.get(d) in READY for d in deps):
                continue
            if dispatch_one(nodes, st, nid, r, token) is not None:
                st[nid] = "in-flight"
                progressed = True
        if not progressed:
            break


# ══════════════════ v5 自主提案波（owner：一群 agent 提议，非 workflow） ══════════════════

PROPOSER_GOAL = """【自主提案者|AUTONOMY-PROPOSER】owner 2026-10-31：任务不应只靠外部推动——任务清零时由上层智能体**自主思考**提议下一波。你是提案者之一（多模型并行独立提案，允许重叠：冗余由仲裁聚合）。
对象战役 plan_id={pid}「{title}」已全节点终态。请独立完成：
① 情报读取：SELECT n.metadata->>'ext_id', n.name, n.status, n.mission_id FROM campaign_nodes n WHERE n.plan_id={pid}；逐单读 /root/tp-agent/work/m<mission_id>/ 验收报告（尤其「局限/下一步」节）；读 m611 master_plan.json 里程碑谱系；读 tensor_guide_tracker 最新 KPI 缺口；读提案池 {pool}/（同侪与历史提案——可借鉴思路、可站在其上深化，禁止照抄）；扫 GitHub 各库未合并 agent/m* 分支清单。
② 自主思考：从证据出发提出 4-6 条**从未做过**的任务提案。每条必含：title / repo（TensorPhysics|TensorDEM|TensorLBM|TensorPlatform|ship-performance-platform）/ physics_domain / why_frontier（凭什么判断没人做过：对照 verified/、已有 plan 节点、提案池）/ goal（完整任务书：物理目标+实现路径+机器可算验收判据）/ dependencies_hint / is_maturity（是否软件功能成熟化单）。
硬禁：重复 verified/ 已验证案例；重复任何已有 plan 节点或已交付能力；原地打转。
③ 交付：把 {{"proposals": [...]}} 写进本任务 result JSON，同时写文件 {pool}/p{pid}/w{wave}/prop-<本任务id>.json（mkdir -p）。提案将交仲裁者聚合播种，你写得多清楚，被采纳的概率就越大。"""


ARBITER_GOAL = """【仲裁播种者|AUTONOMY-ARBITER】owner 2026-10-31「相互扶持」：你的职责不是挑选赢家，而是**聚合同侪之长**播种下一场战役。
输入：{n} 位提案者的提案——SELECT id, result FROM tensor_dev_missions WHERE id IN ({mids})（result->'proposals'），以及提案池 {pool}/p{pid}/w{wave}/。
任务：
① 聚合去重：语义重叠的提案合并为一条（吸收各家可取细节），互补提案全部保留；每节点 metadata 记 proposal_src=[来源提案者任务id] 以示传承。
② 播种 draft 战役（直接 SQL；先查 information_schema.columns 与 pg_constraint 核对列名/CHECK 枚举；**禁 ALTER 表结构**，只 INSERT/UPDATE 数据）：
   - INSERT campaign_plans 一行：status='draft'，title 承谱系命名，metadata 含 {{"parent_plan": {pid}}} 与 {{"seed_report": {{"arbiters": [{self_id}], "wave": {wave}}}}}；
   - INSERT campaign_milestones 2-4 个（归属该 plan）；
   - INSERT campaign_nodes {minn}-{maxn} 个：每节点 metadata **必含** ext_id（全库唯一新前缀）/repo（白名单内）/goal（≥80 字完整任务书：物理目标+机器可算判据+纪律=全新前沿、worktree 内提交自己分支、论文级报告、honest FAIL 不可覆盖）/acceptance_criteria（机器可算数组）；dependencies=同 plan 节点 id 数组；milestone_id 归属。
③ 机器门（派发器会硬校验，不过=不激活重派你）：节点数 {minn}-{maxn}；ext_id 全库唯一；repo 在白名单；DAG 无环；dependencies 只引用本 plan 节点；每里程碑≥1 节点；≥2 条成熟化单（goal 含「成熟化」字样）。
④ 回写：UPDATE 新 plan 的 metadata.seed_report 加 nodes 数与 new_plan_id；本任务 result JSON 亦写 {{"seed_report": {{"new_plan_id": <id>, "nodes": <n>, "sources": [...]}}}}。
背景：战役 plan {pid} 已收官，其局限节与 guide_tracker 缺口是提案的原材料；对未合并 GitHub 分支上的新模块做集成节点时，goal 须注入上游接口契约并注明「基线=main，上游未合并」。"""


def _mission(mid):
    rows = q(f"SELECT id, status, coalesce(result::text,'{{}}') FROM tensor_dev_missions "
             f"WHERE id={int(mid)}")
    if not rows:
        return None
    rid, st, rt = rows[0]
    try:
        res = json.loads(rt) if rt else {}
    except Exception:
        res = {}
    return {"id": int(rid), "status": st, "result": res}


def _dispatch_mission(token, goal, task_key, model=None, origin="agent"):
    payload = {"goal": goal, "repo": "TensorPlatform", "runner": "native",
               "origin": origin, "task_key": task_key, "budget": BUDGET}
    if model:
        payload["model"] = model
    code, d = api("/api/v1/tensor/missions", payload, token)
    mid = d.get("id") or (d.get("mission") or {}).get("id")
    if code == 409:
        hit = q(f"SELECT id FROM tensor_dev_missions WHERE task_key='{task_key}' "
                f"ORDER BY id DESC LIMIT 1")
        mid = int(hit[0][0]) if hit else None
    return int(mid) if mid else None


def _plan_meta(pid):
    rows = q(f"SELECT coalesce(metadata::text,'{{}}') FROM campaign_plans WHERE id={int(pid)}")
    try:
        return json.loads(rows[0][0]) if rows else {}
    except Exception:
        return {}


def _set_plan_autonomy(pid, autonomy):
    q("UPDATE campaign_plans SET updated_at=now(), "
      "metadata=jsonb_set(coalesce(metadata,'{}'::jsonb), '{autonomy}', %s::jsonb) "
      "WHERE id=%d" % (jbd(autonomy), int(pid)))


def _launch_wave(token, pid, title, wave):
    """派 K 个异构提案者（独立思考，允许重叠=冗余多渠道）。"""
    mids = []
    for i, model in enumerate(PROPOSER_MODELS[:K_PROPOSERS], 1):
        goal = PROPOSER_GOAL.format(pid=pid, title=(title or f"plan-{pid}")[:60],
                                    pool=PROPOSAL_POOL, wave=wave)
        mid = _dispatch_mission(token, goal, f"propose-p{pid}-w{wave}-{i}", model=model)
        if mid:
            mids.append(mid)
    if not mids:
        log(f"AUTONOMY plan {pid} w{wave} 提案者派发全败（下轮重试）")
        return None
    log(f"AUTONOMY plan {pid} w{wave} 提案波点火：{len(mids)} 位异构提案者 {mids}（模型 {PROPOSER_MODELS[:K_PROPOSERS]}）")
    return {"wave": wave, "proposers": mids, "arbiter": None, "new_plan": None,
            "arbiter_attempts": 0, "exhausted": False}


def _validate_new_plan(pid):
    """机器门（owner R1：必须能失败）。返回 (ok, reasons)。"""
    reasons = []
    rows = q(f"SELECT coalesce(status,''), coalesce(metadata::text,'{{}}') FROM campaign_plans WHERE id={int(pid)}")
    if not rows:
        return False, ["plan 不存在"]
    if rows[0][0] not in ("draft", "active"):
        reasons.append(f"status={rows[0][0]} 非 draft/active")
    nodes = q(f"SELECT id, replace(coalesce(metadata->>'ext_id',''), chr(10), ' '), "
              f"replace(coalesce(metadata->>'repo',''), chr(10), ' '), "
              f"replace(replace(coalesce(metadata->>'goal',''), chr(10), ' '), chr(10), ' '), coalesce(milestone_id::text,''), "
              f"coalesce(dependencies::text,'{{}}') FROM campaign_nodes WHERE plan_id={int(pid)}")
    n = len(nodes)
    if not (MIN_NODES <= n <= MAX_NODES):
        reasons.append(f"节点数 {n} 不在 [{MIN_NODES},{MAX_NODES}]")
    ids = {r[0] for r in nodes}
    exts = [r[1] for r in nodes if r[1]]
    if len(exts) != n:
        reasons.append("存在缺 ext_id 的节点")
    if exts:
        inlist = ",".join("'" + e.replace("'", "") + "'" for e in exts)
        dup = q(f"SELECT metadata->>'ext_id' FROM campaign_nodes "
                f"WHERE metadata->>'ext_id' IN ({inlist}) GROUP BY 1 HAVING count(*)>1")
        if dup:
            reasons.append(f"ext_id 跨库撞车: {[r[0] for r in dup]}")
    bad_repo = [r[1] for r in nodes if REPO_ALIAS.get(r[2], r[2]) not in REPO_WHITELIST]
    if bad_repo:
        reasons.append(f"repo 越白名单: {bad_repo}")
    thin = [r[1] for r in nodes if len(r[3] or "") < 80]
    if thin:
        reasons.append(f"goal 过薄(<80字): {thin}")
    orphans = []
    adj = {}
    for r in nodes:
        try:
            ds = [int(x) for x in r[5].strip("{}").split(",") if x]
        except Exception:
            ds = []
        adj[r[0]] = [d for d in ds if d in ids]
        if not set(str(d) for d in ds) <= {str(i) for i in ids}:
            orphans.append(r[1])
    if orphans:
        reasons.append(f"dependencies 引用 plan 外节点: {orphans}")
    no_ms = [r[1] for r in nodes if r[4] in ("", "None")]
    if no_ms:
        reasons.append(f"节点未挂里程碑: {no_ms}")
    # DAG 无环（Kahn 拓扑：入度=依赖数，0 依赖先出队）
    deg = {i: len(adj[i]) for i in adj}
    queue = [i for i, d in deg.items() if d == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for i in adj:
            if cur in adj[i]:
                deg[i] -= 1
                if deg[i] == 0:
                    queue.append(i)
    if n and seen != n:
        reasons.append("DAG 有环")
    ms = int(q(f"SELECT count(*) FROM campaign_milestones WHERE plan_id={int(pid)}")[0][0] or 0)
    if ms < 2:
        reasons.append(f"里程碑 {ms} < 2")
    mat = sum(1 for r in nodes if "成熟化" in (r[3] or ""))
    if mat < 2:
        reasons.append(f"成熟化单 {mat} < 2")
    return (not reasons), reasons


def _collect_new_plan(pid, arbiter_mid):
    """从计划 metadata 或仲裁者 result 里找 new_plan_id。
    v5.5.1（m782 事故）：候选逐级校验——须 ≠ 父计划且真实存在（越权/写错值不作裁决依据），
    干净候选优先：父 metadata → 仲裁者 result → draft 查询兜底。"""
    cands = []
    meta = _plan_meta(pid)
    sr = meta.get("seed_report") or {}
    if sr.get("new_plan_id"):
        cands.append(sr["new_plan_id"])
    m = _mission(arbiter_mid)
    if m:
        sr2 = (m.get("result") or {}).get("seed_report") or {}
        if sr2.get("new_plan_id"):
            cands.append(sr2["new_plan_id"])
    rows = q(f"SELECT id FROM campaign_plans WHERE coalesce(metadata->>'parent_plan','')='{int(pid)}' "
             f"AND coalesce(status,'')='draft' ORDER BY id DESC LIMIT 1")
    if rows:
        cands.append(rows[0][0])
    for c in cands:
        try:
            c = int(c)
        except (TypeError, ValueError):
            continue
        if c == int(pid):
            continue
        # v5.5.3：继任候选必须仍是 draft——completed/active 历史计划不可复活为继任
        ex = q(f"SELECT count(*) FROM campaign_plans WHERE id={c} AND coalesce(status,'')='draft'")
        if int(ex[0][0] or 0) > 0:
            return c
    return None


def _resolve_arbiter_outcome(token, pid, title, aut):
    """仲裁者终态→机器门验证→激活/重试/耗尽。"""
    am = _mission(aut["arbiter"])
    if not am:
        return aut
    if am["status"] not in TERMINAL:
        return aut
    if am["status"] == "succeeded":
        npid = _collect_new_plan(pid, aut["arbiter"])
        if npid:
            ok, reasons = _validate_new_plan(npid)
            if ok:
                # 守卫（m763 空转重排竞态 2026-10-31）：旧战役尚有非终态节点（重排在飞等）时
                # 推迟继任激活，防止收官 UPDATE 把在飞节点变孤儿；下轮再看
                pend = q(f"SELECT count(*) FROM campaign_nodes WHERE plan_id={int(pid)} "
                         f"AND status NOT IN ('done','failed','blocked')")
                if int(pend[0][0] or 0) > 0:
                    return aut
                q(f"UPDATE campaign_plans SET status='active', updated_at=now() WHERE id={int(npid)}")
                q(f"UPDATE campaign_plans SET status='completed', updated_at=now() WHERE id={int(pid)}")
                aut["new_plan"] = npid
                event("plan_updated", {"plan": int(pid), "successor_plan": int(npid),
                                       "note": "自主提案波→机器门通过→继任战役激活"}, pid=int(pid))
                event("plan_updated", {"plan": int(npid), "note": "由提案波激活（上层智能体播种）",
                                       "parent": int(pid)}, pid=int(npid))
                log(f"AUTONOMY plan {pid} → 继任 plan {npid} 机器门通过并激活（自主播种闭环）")
                return aut
            reason_txt = "; ".join(reasons)
        else:
            reason_txt = "仲裁者成功但未产出 new_plan_id（查 seed_report/result/draft 均无）"
    else:
        reason_txt = f"仲裁者 {am['status']}"
    aut["arbiter_attempts"] += 1
    if aut["arbiter_attempts"] >= MAX_ARBITER_ATTEMPTS:
        aut["wave"] += 1
        aut["arbiter"] = None
        aut["arbiter_attempts"] = 0
        if aut["wave"] > MAX_WAVES:
            aut["exhausted"] = True
            q(f"UPDATE campaign_plans SET status='completed', updated_at=now() WHERE id={int(pid)}")
            event("plan_updated", {"plan": int(pid), "autonomy_exhausted": True,
                                   "note": f"{MAX_WAVES} 波提案未成出继任，转池空自由波"}, pid=int(pid))
            log(f"AUTONOMY plan {pid} 提案波耗尽（{MAX_WAVES} 波），收官并让位自由波")
            return aut
        log(f"AUTONOMY plan {pid} 仲裁失败×{MAX_ARBITER_ATTEMPTS}，开第 {aut['wave']} 波")
        return aut
    # 同波重派仲裁者，注入失败原因
    goal = ARBITER_GOAL.format(n=len(aut["proposers"]),
                               mids=",".join(str(m) for m in aut["proposers"]),
                               pool=PROPOSAL_POOL, pid=pid, wave=aut["wave"],
                               self_id="本任务", minn=MIN_NODES, maxn=MAX_NODES) \
           + f"\n（第 {aut['arbiter_attempts'] + 1} 次尝试：前次机器门未过/失败：{reason_txt[:400]}。修正后重播种。）"
    mid = _dispatch_mission(token, goal, f"arbiter-p{pid}-w{aut['wave']}-a{aut['arbiter_attempts'] + 1}",
                            model=ARBITER_MODEL)
    if mid:
        aut["arbiter"] = mid
        log(f"AUTONOMY plan {pid} 仲裁重派 m{mid}（原因：{reason_txt[:150]}）")
    return aut


def _adopt_orphan_drafts():
    """孤儿继任收养（v5.5.2，m782 事故善后）：仲裁播种的 draft 继任只在「父计划 active
    的 resolve 路径」被激活；若父计划先行收官（越权代写/竞态触发错误自激活等），
    继任 draft 永无人认领=舰队停摆。→ 父已终态或父供给耗尽（无 pending/in-flight 节点，仅剩
    blocked 门也算——2026-11-07 owner 全放开令）+ 机器门通过即收养激活。
    机器门能失败：半成品（节点不足/坏 DAG/白名单外）不收养只告警。"""
    try:
        rows = q("SELECT d.id, coalesce((d.metadata->>'parent_plan')::int, 0) FROM campaign_plans d "
                 "WHERE coalesce(d.status,'')='draft' "
                 "AND (EXISTS (SELECT 1 FROM campaign_plans p WHERE p.id=(d.metadata->>'parent_plan')::int "
                 "AND (coalesce(p.status,'') IN ('completed','failed') "
                 "OR NOT EXISTS (SELECT 1 FROM campaign_nodes c WHERE c.plan_id=p.id "
                 "AND c.status IN ('pending','in-flight')))) "
                 "OR NOT EXISTS (SELECT 1 FROM campaign_plans p2 WHERE p2.id=(d.metadata->>'parent_plan')::int)) "
                 "ORDER BY d.id")
        for did, par in rows:
            try:
                did, par = int(did), int(par)
                ok, reasons = _validate_new_plan(did)
                if not ok:
                    log(f"WARN 孤儿 draft plan {did}（父 {par}）机器门未过不收养: {'; '.join(reasons)[:200]}")
                    continue
                q(f"UPDATE campaign_plans SET status='active', updated_at=now() WHERE id={int(did)}")
                event("plan_updated", {"plan": int(did), "note": "孤儿继任收养激活（父终态或父供给耗尽 v5.5.4，机器门通过）",
                                       "parent": int(par)}, pid=int(did))
                log(f"AUTONOMY 孤儿继任 plan {did}（父 {par}）机器门通过收养激活（v5.5.4 全放开令）")
            except Exception as e:
                log(f"WARN 孤儿收养单draft异常 plan {did}: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:
        log(f"WARN 孤儿收养异常: {type(e).__name__}: {str(e)[:150]}")


def autonomy_phase(token):
    """双渠道触发：① 挂靠计划全节点终态→提案波；② 池空自由波（无任何 active 计划时）。"""
    rows = q("SELECT p.id, coalesce(p.title, p.name, '') FROM campaign_plans p "
             "WHERE coalesce(p.status,'active')='active' "
             "AND EXISTS (SELECT 1 FROM campaign_nodes n WHERE n.plan_id=p.id) "
             "AND NOT EXISTS (SELECT 1 FROM campaign_nodes n WHERE n.plan_id=p.id "
             "                AND n.status NOT IN ('done','failed','blocked'))")
    for pid, ptitle in rows:
        pid = int(pid)
        aut = _plan_meta(pid).get("autonomy")
        if not aut:
            aut = _launch_wave(token, pid, ptitle, wave=1)
            if aut:
                _set_plan_autonomy(pid, aut)
                event("plan_updated", {"plan": pid, "autonomy_wave": 1,
                                       "note": "收官触发自主提案波（一群 agent 提议）"}, pid=pid)
            continue
        if aut.get("exhausted"):
            continue
        if aut.get("new_plan"):
            # 一致性守卫（m782 事故 2026-10-31）：flag 声称已激活但目标计划仍非 active
            # = 仲裁者越权代写调度器状态的残留（可伪造 flag 不作裁决依据），清除重走机器门；
            # 真激活（目标 active）不误清。
            try:
                npid2 = int(aut["new_plan"])
                row = q(f"SELECT coalesce(status,'') FROM campaign_plans WHERE id={npid2}")
                st = row[0][0] if row else "missing"
            except Exception:
                npid2, st = -1, "error"
            # v5.5.3：自指残留（new_plan==自身 id，m782 类越权代写副本）同按残留清除，
            # 否则「目标 active」恒真（目标就是自己）→ 每轮 continue 死锁
            why = "自指残留" if npid2 == pid else "越权代写"
            if st != "active" or npid2 == pid:
                aut["new_plan"] = None
                _set_plan_autonomy(pid, aut)
                log(f"WARN plan {pid} autonomy.new_plan={npid2} 残留（目标状态={st}，{why}）→ 清除重走机器门激活")
            else:
                continue
        if not aut.get("proposers"):
            continue
        props = [_mission(m) for m in aut["proposers"]]
        if not all(p and p["status"] in TERMINAL for p in props):
            continue  # 提案者仍在思考
        if not aut.get("arbiter"):
            succ = [p for p in props if p["status"] == "succeeded"]
            if len(succ) >= 2 or (len(succ) >= 1 and len(props) < 2):
                goal = ARBITER_GOAL.format(n=len(succ),
                                           mids=",".join(str(p["id"]) for p in succ),
                                           pool=PROPOSAL_POOL, pid=pid, wave=aut["wave"],
                                           self_id="本任务", minn=MIN_NODES, maxn=MAX_NODES)
                mid = _dispatch_mission(token, goal,
                                        f"arbiter-p{pid}-w{aut['wave']}-a1", model=ARBITER_MODEL)
                if mid:
                    aut["arbiter"] = mid
                    _set_plan_autonomy(pid, aut)
                    log(f"AUTONOMY plan {pid} w{aut['wave']} 提案齐（{len(succ)}/{len(props)} 成）→ 仲裁者 m{mid}")
            else:
                aut["wave"] += 1
                if aut["wave"] > MAX_WAVES:
                    aut["exhausted"] = True
                    q(f"UPDATE campaign_plans SET status='completed', updated_at=now() WHERE id={int(pid)}")
                    log(f"AUTONOMY plan {pid} 提案者连败 {MAX_WAVES} 波，耗尽让位自由波")
                else:
                    nxt = _launch_wave(token, pid, ptitle, wave=aut["wave"])
                    if nxt:
                        nxt["wave"] = aut["wave"]
                        aut = nxt
                _set_plan_autonomy(pid, aut)
            continue
        aut = _resolve_arbiter_outcome(token, pid, ptitle, aut)
        _set_plan_autonomy(pid, aut)
    _idle_free_wave(token)


def _idle_free_wave(token):
    """渠道②（可以冗余，多渠道）：任务池全空且无 active 计划 → 自由提案波（无挂靠，仲裁播种全新战役）。"""
    try:
        busy = int(q("SELECT count(*) FROM tensor_dev_missions "
                     "WHERE status IN ('queued','claimed','running')")[0][0] or 0)
        act = int(q("SELECT count(*) FROM campaign_plans "
                    "WHERE coalesce(status,'active')='active'")[0][0] or 0)
    except Exception as e:
        log(f"WARN 自由波探测失败: {str(e)[:120]}")
        return
    if busy > 0 or act > 0:
        return
    state = {}
    if os.path.exists(FREE_STATE):
        try:
            state = json.load(open(FREE_STATE))
        except Exception:
            state = {}
    if state.get("done"):
        return
    wave = int(state.get("wave", 0)) + 1
    if wave > MAX_WAVES:
        state["done"] = True
        json.dump(state, open(FREE_STATE, "w"))
        log(f"AUTONOMY 自由波 {MAX_WAVES} 波耗尽——舰队空转，需 owner 注入方向（事件已留痕）")
        event("plan_updated", {"autonomy_idle_exhausted": True,
                               "note": "自由提案波耗尽，舰队无任务可派"}, pid=1)
        return
    if not state.get("proposers"):
        mids = []
        for i, model in enumerate(PROPOSER_MODELS[:K_PROPOSERS], 1):
            goal = PROPOSER_GOAL.format(pid=0, title="池空自由波（无挂靠战役，面向全平台）",
                                        pool=PROPOSAL_POOL, wave=wave) \
                   + "\n（自由波：无前序战役，情报面=提案池+guide_tracker+verified/ 全景+近期已合并 PR。）"
            mid = _dispatch_mission(token, goal, f"propose-free-w{wave}-{i}", model=model)
            if mid:
                mids.append(mid)
        if mids:
            state = {"wave": wave, "proposers": mids, "arbiter": None, "attempts": 0}
            json.dump(state, open(FREE_STATE, "w"))
            log(f"AUTONOMY 池空自由波 w{wave} 点火：提案者 {mids}（任务清零触发自主思考）")
        return
    props = [_mission(m) for m in state["proposers"]]
    if not all(p and p["status"] in TERMINAL for p in props):
        return
    succ = [p for p in props if p["status"] == "succeeded"]
    if not state.get("arbiter"):
        if len(succ) >= 2:
            goal = ARBITER_GOAL.format(n=len(succ), mids=",".join(str(p["id"]) for p in succ),
                                       pool=PROPOSAL_POOL, pid=0, wave=wave, self_id="本任务",
                                       minn=MIN_NODES, maxn=MAX_NODES) \
                   + "\n（自由波：新战役 plan 的 metadata.parent_plan=0。）"
            mid = _dispatch_mission(token, goal, f"arbiter-free-w{wave}-a{int(state.get('attempts', 0)) + 1}",
                                    model=ARBITER_MODEL)
            if mid:
                state["arbiter"] = mid
                json.dump(state, open(FREE_STATE, "w"))
                log(f"AUTONOMY 自由波 w{wave} 提案齐 → 仲裁者 m{mid}")
        else:
            state = {"wave": wave}
            json.dump(state, open(FREE_STATE, "w"))
        return
    am = _mission(state["arbiter"])
    if not am or am["status"] not in TERMINAL:
        return
    if am["status"] == "succeeded":
        npid = _collect_new_plan(0, state["arbiter"])
        if npid:
            ok, reasons = _validate_new_plan(npid)
            if ok:
                q(f"UPDATE campaign_plans SET status='active', updated_at=now() WHERE id={int(npid)}")
                state["done"] = True
                json.dump(state, open(FREE_STATE, "w"))
                event("plan_updated", {"plan": int(npid), "note": "池空自由波激活",
                                       "parent": 0}, pid=int(npid))
                log(f"AUTONOMY 自由波 → 新战役 plan {npid} 机器门通过激活（舰队自主重启开发）")
                return
            reason_txt = "; ".join(reasons)
        else:
            reason_txt = "自由波仲裁未产出 new_plan_id"
    else:
        reason_txt = f"自由波仲裁 {am['status']}"
    state["attempts"] = int(state.get("attempts", 0)) + 1
    if state["attempts"] >= MAX_ARBITER_ATTEMPTS:
        state = {"wave": wave}  # 下一波
    else:
        goal = ARBITER_GOAL.format(n=len(succ), mids=",".join(str(p["id"]) for p in succ),
                                   pool=PROPOSAL_POOL, pid=0, wave=wave, self_id="本任务",
                                   minn=MIN_NODES, maxn=MAX_NODES) \
               + f"\n（第 {state['attempts'] + 1} 次尝试：前次未过：{reason_txt[:400]}。）"
        mid = _dispatch_mission(token, goal, f"arbiter-free-w{wave}-a{state['attempts'] + 1}",
                                model=ARBITER_MODEL)
        if mid:
            state["arbiter"] = mid
    json.dump(state, open(FREE_STATE, "w"))


def _touch_world_line():
    """冷启动包 WORLD 行心跳刷新（借 /hello 世界状态注入设计 2026-10-31）：
    每轮把 AGENT_BOOTSTRAP.md 首部 WORLD 行刷到当前时刻——冷启动代理见时间戳
    落后 >10min 即知守护侧异常，先校准 :8010 再行动（防基于化石状态决策）。"""
    try:
        p = "/root/tp-agent/AGENT_BOOTSTRAP.md"
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        for i, ln in enumerate(lines):
            if ln.startswith("WORLD: "):
                lines[i] = time.strftime("WORLD: %F %T dispatcher 心跳正常（战役/池实时态查 campaign_plans/nodes 表）\n")
                break
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass


def main():
    log("=== campaign_dispatcher v5 启动（owner 自主播种令：收官/池空双渠道→异构提案波→仲裁聚合→机器门激活）===")
    while True:
        try:
            sync_phase()
            _touch_world_line()
            token = get_token()
            if token:
                dispatch_phase(token)
                _adopt_orphan_drafts()
                autonomy_phase(token)
            else:
                log("WARN auto-login 失败，本轮跳过派单")
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {str(e)[:300]}")
        time.sleep(30)


if __name__ == "__main__":
    main()
