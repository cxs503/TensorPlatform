#!/usr/bin/env python3
"""tp_selfstarter v2 —— 全席饱和引擎（owner 2026-11-01「我们的目标是让工作饱和，
agent 一直忙着」+「只要不超过硬件承载能力，就让资源充分利用」）。
v1→v2 三改：
1. 覆盖面：generalist 4 席 → fleet.json 全席（空闲≥20min → 钉扎自驱单，只有该席能领）；
2. 修复：v1 origin='mechanism' 违反 CHECK(origin∈{owner,agent})，INSERT 每轮必败且被
   误标「唯一门拦截」（q() 不查 returncode）——v2 origin='owner'（owner 批的常设机制），
   q() 严格 raise；
3. 硬件水位门 + 滞留收割：
   - 容量门：load15 ≥1.0×nproc 或 MemAvailable<64G → 本周期 HOLD；≥0.85×nproc → 半速
     （隔周期派）。agent 主体是 LLM 调用+轻 shell，重算力留给实验单，不与本门冲突。
   - REAPER：queued >90min 且 model 不在任何 enabled 席 → 改钉 deepseek/qwen3.6 中在飞
     较低者（m1140=glm-5.2 死钉类缺陷的机制级治愈；EVO 专用模型绝不作改钉目标）。
门（必须能失败）：SEAT_IDLE=1200s；SESSION_COOLDOWN=3600s；CAP=inf（2026-11-03 owner 撤日配额）；
该席已有挂队 selfstart 单 → 停派只告警；容量门 HOLD 时全停。
状态：/root/tp-agent/selfstarter_state.json；日志：/var/log/tp-fleet/selfstarter.log
"""
import datetime, json, pathlib, subprocess, time

LOG = "/var/log/tp-fleet/selfstarter.log"
STATE_F = pathlib.Path("/root/tp-agent/selfstarter_state.json")
FLEET = "/root/tp-agent/fleet.json"
CYCLE, SEAT_IDLE, SESSION_COOLDOWN, CAP = 300, 1200, 3600, float("inf")
# owner 2026-11-03「按资源无限设计」：撤 6/席/日配额（n_today 只作遥测）；
# 容量门（load15/mem HOLD/HALF）与去重/消化门保留——那是机器保护不是配给。
WHITELIST = "TensorLBM/TensorDEM/TensorFEM/TensorFVM/TensorBEM/TensorSPH/TensorDG/TensorCAD/ship-performance-platform/TensorPapers"  # owner 2026-11-02 控仓令：摘 TensorPlatform/TensorPhysics
# 改钉候选：全员生产模型（PIN_RULE 禁钉 EVO 臂专用模型 qwen3.7-plus/MiniMax-M2.5-AWQ/Qwen3.8-27B/glm-5.1）
REPIN_MODELS = ["deepseek-v4-flash", "qwen3.6-plus"]
LOAD_HOLD, LOAD_HALF = 1.0, 0.85   # ×nproc（15min loadavg）
MEM_MIN_MB = 64 * 1024

GOAL_TMPL = """【自找活·{seat}（{role} 席）】（owner 2026-11-01：全席饱和；2026-11-02 控仓令：自驱开发照常，绝不动 TensorPlatform）
你是研究开发 agent，本单=你的自主工时（model 钉扎 {model}，只有本席能领）。
1) 扫描（全只读）：/root/tp-agent/proposals/ 提案池；GET /api/v1/tensor/reports?limit=20 的
   「局限/待办」节；guide_tracker 缺口（/root/tp-agent/guide* 状态 json）；各仓未合并 PR；
   以及本席上一张 selfstart-* 单的产出（防自我重复）；各仓缺口账
   （backlog_*/expand_audit_*/dev_report_* 文件——求解器仓选题优先锚，先读账再选题）；
2) 选定 1 件最有价值、可单干、不与在飞/近 24h 终态单重复的实质增量——优先「验证欠账」类
   （把已合并功能对文献/解析基准复核），其次功能补全、失效测试补齐；
3) 直接开工做完：测试绿 + 平台报告发布 + git commit 落盘（goal 首行自声明 EDIT_WHITELIST，
   按所选仓常规路径 docs/, src/, tests/, examples/, outputs/）；
4) 若该活超出单干范围：改为 POST /api/v1/tensor/missions 派单（body 必须显式含
   "runner":"native"——表默认 openhands=无人领死单），并在 summary 说明拆分逻辑；
5) finish summary：干了什么/证据指针（commit+报告 id）/给下次自找活的建议线索。
容量纪律：重计算任务先看 /proc/loadavg（本机 nproc=128），load15 已 ≥100 时只做轻量
（文档/测试/分析）活，不起新的重算力进程。
仓白名单：{wl}。归仓口诀（详见 /root/tp-agent/AGENT_BOOTSTRAP.md ECOSYSTEM-MAP）：求解器→对应 Tensor* 库（当前主战场）；船舶→ship-performance-platform（本地私有仓）；论文素材→TensorPapers。TensorPlatform 与 TensorPhysics 由 owner 指定专人重构（2026-11-02），agent 不建单/不改码/不落 commit，平台问题只写报告留证。禁碰 GitHub 推送与 main 分支。honest FAIL 不得覆盖。"""


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def q(sql):
    r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
                        "-t", "-A", "-F", "\x02", "-c", sql],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"psql rc={r.returncode}: {r.stderr.strip()[:200]}")
    return [ln.split("\x02") for ln in r.stdout.splitlines() if ln.strip()]


def load_state():
    try:
        return json.loads(STATE_F.read_text())
    except Exception:
        return {"seats": {}}


def all_seats():
    try:
        fl = json.load(open(FLEET))
        return {w["id"]: (w.get("role", "platform"), w["model"]) for w in fl}
    except Exception:
        return {}


def capacity():
    """返回 'ok' | 'half' | 'hold'（owner：不超过硬件承载能力为界）"""
    try:
        n = int(open("/sys/devices/system/cpu/online").read().split("-")[-1]) + 1
    except Exception:
        n = 128
    try:
        load15 = float(open("/proc/loadavg").read().split()[2])
    except Exception:
        return "hold"
    try:
        memavail = int(open("/proc/meminfo").read().split("MemAvailable:")[1].split("kB")[0])
    except Exception:
        memavail = 1 << 62
    if memavail < MEM_MIN_MB or load15 >= LOAD_HOLD * n:
        return "hold"
    if load15 >= LOAD_HALF * n:
        return "half"
    return "ok"


def reaper():
    """滞留收割：queued>90min 且死模型钉扎 → 改钉活模型（帽 4/轮）"""
    live = {r[0] for r in q("SELECT DISTINCT model FROM factory_resident_workers WHERE enabled")}
    rows = q("SELECT id, coalesce(model,''), extract(epoch from now()-created_at)::int "
             "FROM tensor_dev_missions WHERE status='queued' AND model IS NOT NULL "
             "AND created_at < now() - interval '90 minutes' LIMIT 12")
    n = 0
    for mid, model, age in rows:
        age = int(age)
        if model in live:
            continue
        load = {m: int(q(f"SELECT count(*) FROM tensor_dev_missions WHERE status IN "
                         f"('queued','running') AND model='{m}'")[0][0]) for m in REPIN_MODELS}
        tgt = min(REPIN_MODELS, key=lambda m: load[m])
        q(f"UPDATE tensor_dev_missions SET model='{tgt}' WHERE id={mid} AND status='queued'")
        log(f"REPIN m{mid} {model}→{tgt}（queued {age//60}min，原模型无 enabled 席）")
        n += 1
        if n >= 4:
            break
    return n


def main():
    st = load_state()
    log("=== tp_selfstarter v2 启动（全席饱和：空闲 %ds→钉扎自驱单，冷却 %ds，帽 %s/席/日，"
        "容量门 hold@%.1fx/half@%.1fx）===" % (SEAT_IDLE, SESSION_COOLDOWN, CAP, LOAD_HOLD, LOAD_HALF))
    half_parity = 0
    while True:
        try:
            cap = capacity()
            seats = all_seats()
            today = datetime.date.today().isoformat()
            run_rows = q("SELECT coalesce(claimed_by,''), count(*) FROM tensor_dev_missions "
                         "WHERE status IN ('claimed','running') GROUP BY 1")
            running_by = {r[0]: int(r[1]) for r in run_rows}
            if cap == "hold":
                log("CAPACITY-HOLD load15/mem 超界，本周期停派（在飞不受影响）")
            elif cap == "half":
                half_parity ^= 1
                if half_parity == 0:
                    log("CAPACITY-HALF 半速：本周期跳过新派")
            if cap == "ok" or (cap == "half" and half_parity == 1):
                now = time.time()
                for seat, (role, model) in seats.items():
                    s = st["seats"].setdefault(seat, {"last_active": now, "last_session": 0.0,
                                                      "today": today, "n_today": 0})
                    if s["today"] != today:
                        s.update(today=today, n_today=0)
                    if running_by.get(seat, 0) > 0:
                        s["last_active"] = now
                        continue
                    idle = now - float(s["last_active"])
                    stuck = q(f"SELECT count(*) FROM tensor_dev_missions WHERE status='queued' "
                              f"AND model='{model}' AND task_key LIKE 'selfstart-{seat}-%'")
                    if int(stuck[0][0]) > 0:
                        log(f"WARN {seat} 有挂队 selfstart 单（中继/配额疑断）停派待消化")
                        continue
                    if (idle >= SEAT_IDLE
                            and now - float(s["last_session"]) >= SESSION_COOLDOWN
                            and int(s["n_today"]) < CAP):
                        wl_list = WHITELIST.split("/")
                        doy = datetime.date.today().timetuple().tm_yday
                        # 2026-11-02 修：repo 列硬编码 'TensorPlatform' 违控仓令（直插 DB 绕过 API 冻结门）
                        # → 白名单确定性轮换（日+席），永不落 TP/TensorPhysics
                        repo_pick = wl_list[(doy + sum(ord(c) for c in seat)) % len(wl_list)]
                        org = q("SELECT organization_id FROM tensor_dev_missions "
                                "ORDER BY id DESC LIMIT 1")[0][0]
                        tk = f"selfstart-{seat}-{datetime.datetime.now().strftime('%m%d-%H%M')}"
                        goal = GOAL_TMPL.format(seat=seat, role=role, model=model, wl=WHITELIST)
                        sql = ("INSERT INTO tensor_dev_missions (organization_id, goal, repo, runner, "
                               "origin, model, task_key, status, report_owed, budget) SELECT " + org +
                               ", $g$" + goal + "$g$, '" + repo_pick + "', 'native', 'owner', '" +
                               model + "', '" + tk +
                               "', 'queued', true, '{\"max_tokens\":3000000,\"max_tool_calls\":60,"
                               "\"max_wall_seconds\":5400}'::jsonb "
                               "WHERE NOT EXISTS (SELECT 1 FROM tensor_dev_missions "
                               "WHERE task_key='" + tk + "') RETURNING id")
                        rows = q(sql)
                        if rows:
                            s.update(last_session=now, n_today=int(s["n_today"]) + 1)
                            log(f"SELF-DIRECT {seat}（{model}，{role}）空闲 {int(idle)}s → 自驱单 m{rows[0][0]}")
                        else:
                            log(f"WARN {seat} 自驱单 {tk} 唯一门拦截（真重复）")
                st["idle_snapshot"] = {k: v for k, v in running_by.items()}
            reaper()
            STATE_F.write_text(json.dumps(st))
        except Exception as e:
            log(f"异常: {type(e).__name__}: {str(e)[:150]}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
