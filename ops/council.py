#!/usr/bin/env python3
# 议事会派发器（多模型三轮议事 → 下一阶段任务安排计划）
# R1 三提案并行(独立,防锚定) → R2 双质询并行(读全部提案) → R3 综合主席成案
# 产物: /srv/tensor/agent/council/session-001/r3-plan.md —— 呈 owner 大方向把关
# 纪律: 议事会只管具体安排; 方向终审归 owner; 全程禁 GitHub/审批/派发
import json, os, time, pathlib, subprocess, urllib.request, urllib.error

API = "http://127.0.0.1:8010/api/v1"
USER, PASS = os.environ.get("TP_API_USER", ""), os.environ.get("TP_API_PASS", "")
BOARD = pathlib.Path("/srv/tensor/agent/council/session-001")
STATE_F = pathlib.Path("/srv/tensor/agent/council_state.json")
ROUND_TIMEOUT, POLL = 2700, 60
BUDGET = {"max_tool_calls": 80, "max_wall_seconds": 3000, "max_tokens": 3000000}
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc"]

ROUNDS = [
    [  # R1 提案人 ×3（不同模型独立提案，防相互锚定）
        {"slot": "r1-proposal-strategy", "model": "glm-5.2",
         "lens": "架构视角：跨库耦合阶梯、物理战役路线（参考 5090 线 wedge S0→S4 / SI 阶梯的范式）、长期技术债"},
        {"slot": "r1-proposal-models", "model": "deepseek-v4-flash",
         "lens": "模型与算法视角：求解器能力深化（3D/多相/自由面/DEM-FEM 等 10 库哪个先上卡）、SDAA 性能基准线"},
        {"slot": "r1-proposal-applications", "model": "MiniMax-M2.5-AWQ",
         "lens": "应用落地视角：船舶平台后端复活、工程场景案例库、对外展示与报告线自动化"},
    ],
    [  # R2 质询人 ×2（读全部提案，专挑错）
        {"slot": "r2-critique-verifier", "model": "qwen3.6-plus",
         "lens": "验证视角：判据是否机器可裁、honest-FAIL 是否可能、预算/墙钟是否现实"},
        {"slot": "r2-critique-infrastructure", "model": "glm-5.2",
         "lens": "基础设施视角：triton 无 SDAA 后端的硬约束、卡位/环境/git 断连等风险是否被低估"},
    ],
    [  # R3 综合主席（落地执行视角成案）
        {"slot": "r3-plan", "model": "qwen3.7-plus",
         "lens": "综合主席：合并提案+逐条处置质询 → 今天就能派发的任务安排"},
    ],
]

def log(m): print(time.strftime("[%m-%d %H:%M:%S]"), m, flush=True)

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
    assert s == 200, f"login {s} {d}"
    return d["access_token"]

def dispatch(tok, goal, model):
    s, d = api("POST", "/tensor/missions",
               {"goal": goal, "repo": "TensorLBM", "runner": "native",
                "origin": "owner", "model": model, "budget": BUDGET}, tok)
    if s not in (200, 201):
        log(f"dispatch FAIL {s} {d}"); return None
    log(f"dispatched m{d['id']} model={model}")
    return d["id"]

def statuses(ids):
    q = "SELECT id||'='||status FROM tensor_dev_missions WHERE id IN (" + ",".join(map(str, ids)) + ")"
    out = subprocess.run(PG + [q], capture_output=True, text=True,
                         env={"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}).stdout
    st = {}
    for line in out.strip().splitlines():
        if "=" in line:
            i, _, v = line.partition("="); st[int(i)] = v.strip()
    return st

def save(state):
    tmp = str(STATE_F) + ".tmp"
    STATE_F.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    pathlib.Path(tmp).write_text(json.dumps(state, ensure_ascii=False))
    pathlib.Path(tmp).replace(STATE_F)

COMMON = """【议事会纪律】只写你自己的槽位文件，严禁改动议事板其他任何文件；禁碰 GitHub/审批/不得自行派发任务；不得编造平台不存在的记录或数字；不确定处诚实标注。
产出两份同内容文件（缺一不可）：① /srv/tensor/agent/council/session-001/{slot}.md（共享议事板）；② worktree 内 docs/council/session-001/{slot}.md（验收必需）。
文档头一行写 author-model: $LLM_MODEL（读环境变量，取不到写 unknown）。完成后 finish，summary 一句话。"""

def goal_r1(m):
    return f"""【议事会 session-001 · 提案人 · {m['lens']}】
议题：SDAA 平台（node099，32 卡满编）下一阶段任务安排。owner 只把大方向，具体安排归议事会。
第一步：读 /srv/tensor/agent/council/session-001/00_context.md（平台现状/已有战果/硬约束/owner 方向，数字全部属实，不要重复罗列，直接用）。
你独立提出 2-4 个候选任务方向，每个必须含：①目标（一句话）②mission 粒度拆解（每条给 repo、机器判据、预算量级）③卡位分配（32 卡怎么分）④风险与依赖⑤放弃条件（什么情况下不做）。
你的视角：{m['lens']}。提案要具体到今天就能派发，不要空泛口号。
{COMMON.format(slot=m['slot'])}"""

def goal_r2(m):
    return f"""【议事会 session-001 · 质询人 · {m['lens']}】
议题：对提案人的候选方向做交叉质询。
第一步：ls /srv/tensor/agent/council/session-001/ 然后 读 00_context.md 和全部 r1-proposal-*.md。
你的视角：{m['lens']}。
逐提案输出质询：①致命伤（若有）②被低估的风险 ③判据是否机器可裁（能否自动 PASS/FAIL）④预算/墙钟是否现实 ⑤缺失项；最后给你的修正建议（若有比全部提案更好的第四方向，明确说）。
专挑错，不客气；但每条质疑必须具体、可查证，不许泛泛而谈。允许的提案缺陷要明说「非阻断」。
{COMMON.format(slot=m['slot'])}"""

def goal_r3(m):
    return f"""【议事会 session-001 · 综合主席】
议题：合并全部提案与质询，产出最终任务安排。
第一步：ls /srv/tensor/agent/council/session-001/ 然后 读全部文件（00_context.md、全部 r1-proposal-*、全部 r2-critique-*）。
产出 r3-plan.md，结构固定：
## 推荐方案（P0/P1/P2 优先级）——每条 mission 粒度：repo / goal 要点 / 机器判据 / 预算 / 卡位数。P0 必须今天就能派发。
## 质询意见处置——逐条「采纳/驳回+理由」。
## 分歧保留区——未收敛的分歧，写明谁坚持什么、建议 owner 裁决点。
## 下次议事会建议议题。
你的立场：落地执行视角——方案被采纳后立刻进入派发队列，所以每条都要能执行；避免需要 owner 额外解释才能懂的表述。
{COMMON.format(slot=m['slot'])}"""

GOALERS = {1: goal_r1, 2: goal_r2, 3: goal_r3}

def main():
    tok = login()
    state = {"session": 1, "phase": "running", "round": 0, "missions": [], "notes": []}
    save(state)
    for rnd, missions in enumerate(ROUNDS, 1):
        state["round"] = rnd; save(state)
        log(f"=== R{rnd} 派发 {len(missions)} 单 ===")
        for m in missions:
            mid = dispatch(tok, GOALERS[rnd](m), m["model"])
            entry = {"slot": m["slot"], "model": m["model"], "id": mid, "status": "dispatched"}
            state["missions"].append(entry); save(state)
            time.sleep(5)
        t0 = time.time()
        while time.time() - t0 < ROUND_TIMEOUT:
            time.sleep(POLL)
            live = [e["id"] for e in state["missions"][-len(missions):] if e["id"]]
            st = statuses(live) if live else {}
            busy = False
            for e in state["missions"][-len(missions):]:
                if e["id"] and e["status"] not in ("succeeded", "failed", "cancelled"):
                    e["status"] = st.get(e["id"], e["status"])
                    if e["status"] not in ("succeeded", "failed", "cancelled"): busy = True
            save(state)
            if not busy: break
        for e in state["missions"][-len(missions):]:
            if e["status"] != "succeeded":
                state["notes"].append(f"R{rnd} {e['slot']} 未成功: {e['status']}")
            if e["status"] == "succeeded" and not (BOARD / f"{e['slot']}.md").exists():
                state["notes"].append(f"R{rnd} {e['slot']} 成功但议事板文件缺失")
        save(state)
        log(f"=== R{rnd} 完成: " + " ".join(f"{e['slot']}={e['status']}" for e in state["missions"][-len(missions):]) + " ===")
    state["phase"] = "done"; save(state)
    log("COUNCIL session-001 DONE → /srv/tensor/agent/council/session-001/r3-plan.md 待呈 owner 把关")

if __name__ == "__main__":
    main()
