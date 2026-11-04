#!/usr/bin/env python3
"""paper_line —— 顶刊论文生产线守护 v2（owner 2026-11-01 批；v2 同日防造假+隔离加固）。
薄守护：链条由挖矿 agent 自派（paper-draft-/paper-mat-/paper-review-），本守护只做：
  ① 挖矿波调度：无活跃 paper-mine-* 且距上波 ≥12h → 派下一波（钉 deepseek-v4-flash）
  ② 停链看门狗：paper-draft-<slug> succeeded 后 40min 仍无对应 paper-review-<slug> → 补派评审
     （评审钉 qwen3.6-plus；goal 注明 draft 执行席，同席领取=SELF-REVIEW-REFUSED 失格）
  ③ 帽与禁令：paper-mat-* 每日 ≤8 超帽告警；并发 draft ≤2 告警；所有代派 goal 注入 EVO-1 盲评禁读
  ④ 收割：succeeded 挖矿分支 ff 合回 master；draft 分支仅评审 PASS/MINOR 才合（FAIL 稿永不合并）
  ⑤ 隔离钉池（EVO-1 盲评期）：一切论文单只钉 qwen3.6-plus 或 deepseek-v4-flash；
     qwen3.7-plus/MiniMax-M2.5-AWQ/Qwen3.8-27B/glm-5.1 = EVO 臂席专用，禁钉至终局
  防造假纪律（v2 核心，源于 m1122 编造事故 report 912）：draft goal 必含逐数值溯源硬门。
状态 /root/tp-agent/state-paperline.json；日志 /var/log/tp-fleet/paper_line.log
"""
import datetime, json, subprocess, time

CYCLE = 300
MINE_GAP = 12 * 3600
WATCHDOG_S = 40 * 60
MAT_DAILY_CAP = 8
MINE_MODEL = "deepseek-v4-flash"
REVIEW_MODEL = "qwen3.6-plus"
REPO = "/root/tensor-ecosystem/TensorPapers"
LOG = "/var/log/tp-fleet/paper_line.log"
STATE_P = "/root/tp-agent/state-paperline.json"
EVO1_BAN = "【EVO-1 盲评期】禁读 task_key evo1-* 的报告与工件至终局（违者全线停）。"
PIN_RULE = ("model 只钉 qwen3.6-plus 或 deepseek-v4-flash；严禁钉 qwen3.7-plus / MiniMax-M2.5-AWQ / "
            "Qwen3.8-27B / glm-5.1（EVO-1 臂席专用模型，钉了会被臂席领走，至 EVO-1 终局解禁）")
NOFAB = ("【防造假硬门（先例 report 912 整稿 FAIL）】正文每个数值必须可溯源：tensor_reports id"
         "（须真实存在，GET /tensor/reports 核对）/ benchmark verified 路径 / git commit / 平台工件路径；"
         "无数据支撑的小节一律显式标 NO-DATA-TBD 或删除，缺数据可派 paper-mat-<slug>-<n> 补实验；"
         "编造数值/虚构引用/虚构源码 = 评审整稿 FAIL + 造假席登记（评审将逐数值溯源并算术复算）")


def log(m):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='minutes')} {m}\n")


def q(sql):
    r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
                        "-t", "-A", "-F", "\x02", "-c", sql],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return [ln.split("\x02") for ln in r.stdout.splitlines() if ln.strip()]


def scalar(sql):
    rows = q(sql)
    return rows[0][0] if rows else "0"


def insert_mission(goal, task_key, model, budget_json):
    org = "(SELECT organization_id FROM tensor_dev_missions ORDER BY id DESC LIMIT 1)"
    sql = ("INSERT INTO tensor_dev_missions (organization_id, goal, repo, runner, origin, model, "
           "task_key, status, report_owed, budget) SELECT " + org + ", $g$" + goal + "$g$, "
           "'TensorPapers', 'native', 'owner', '" + model + "', '" + task_key + "', "
           "'queued', true, '" + budget_json + "'::jsonb RETURNING id")
    r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
                        "-t", "-A", "-c", sql], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout.strip()


def active_count(prefix):
    return int(scalar(f"SELECT count(*) FROM tensor_dev_missions WHERE task_key LIKE '{prefix}%' "
                      f"AND status IN ('queued','running')"))


MINE_TMPL = """【论文生产线·选题挖矿 W{wave}】（owner 2026-11-01 批顶刊论文生产线：与进展报告分开的单独战线；读本 worktree 的 README.md 与 papers/registry.md）
你是选题矿工（论文生产线席位）。本波重点：{focus}
1) 盘点平台研究进展：GET $TENSOR_API_BASE/tensor/reports 分页读 succeeded 报告；
2) 核实 registry.md 既有课题证据面：每课题列已有报告/图/数据/判据数字清单（零手抄，引 report id 与工件路径）；
3) 收新课题候选（≥3 份量化证据支撑才收，写明贡献点与目标期刊档位）；
4) 每课题列发表缺口清单（缺基线/对比表/消融/云图/可复现细节，逐条可派）；
5) 更新 papers/registry.md（append-only），git add -A && git commit（成果在分支，守护负责回合 master）；
6) 为最成熟 ≤2 课题派成稿单：POST $TENSOR_API_BASE/tensor/missions，body 显式含 runner 值 native，
   task_key=paper-draft-<slug>，{pin}，budget ≤80 调用/14400 秒；
   draft goal 必含论文结构（Abstract/Intro/Methods/Results/Discussion/Conclusion/References/Data Availability）
   与零手抄纪律，并逐字注入以下防造假硬门：
   {nofab}
   素材缺口需跑实验的 → 派 paper-mat-<slug>-<n>（model 钉 deepseek-v4-flash，repo 用对应代码仓（禁 TensorPlatform——平台仓冻结 2026-11-02，素材在领域库/船本体/TensorPapers 里找），budget ≤60 调用/7200 秒，派前查重）；
7) finish 报告=挖矿总结（课题表/证据面/缺口/已派子单清单），论文级，python3 submit_report.py 发。
{ban}
纪律：EDIT_WHITELIST: papers/, docs/, figures/, data/；判据数字零手抄；honest FAIL 不得覆盖；
POST http://127.0.0.1:8011/directory 更新本席 current_goal；成果 git add -A && git commit（0 提交=空转拦截）。"""

REVIEW_TMPL = """【论文生产线·对抗评审】{slug}（owner 2026-11-01 批；读本 worktree 的 papers/{slug}/manuscript.md 与 papers/registry.md）
**回避硬门**：本稿 draft 执行席={draft_seat}。若你就是该席，立即 finish 且报告标题写 SELF-REVIEW-REFUSED（同席自评=失格，v2 已堵此洞）。
以顶刊审稿人立场审：①数字溯源（正文每个数值能否溯源到 tensor_reports id/工件路径/git commit；引用的报告 id 必须 GET /tensor/reports 核对存在；手抄或不存在即 FAIL）；②算术复算（表格均值/拟合参数/百分比自算核对，不一致即 FAIL）；③贡献表述是否被证据支撑；④方法可复现性；⑤图表完备与质量；⑥相关工作定位；⑦局限披露。
结论三选一：PASS / MINOR（列必改点）/ FAIL（列硬伤）。评审意见写入 papers/{slug}/review-<MMDD>.md 并 git commit；
MINOR 必改点直接在 manuscript.md 上修订（另 commit）。finish 报告标题格式「Review: {slug} — <PASS|MINOR|FAIL>」。
{ban}
纪律：判据数字零手抄；honest FAIL 不得覆盖；成果 git add -A && git commit。"""


def load_state():
    try:
        return json.load(open(STATE_P))
    except Exception:
        return {"mine_wave": 0, "last_mine_ts": 0, "draft_saw": {}, "review_sent": {},
                "harvested": []}


def save_state(st):
    json.dump(st, open(STATE_P, "w"), ensure_ascii=False)


def git(args):
    r = subprocess.run(["git", "-C", REPO] + args, capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:150]


def branch_exists(br):
    ok, out = git(["rev-parse", "--verify", "--quiet", br])
    return ok


def ff_merge(br):
    ok, out = git(["merge", "--ff-only", br])
    log(f"HARVEST {'OK' if ok else 'SKIP/CONFLICT'} {br} {out}")
    return ok


def harvest(st):
    done = st.setdefault("harvested", [])
    # ① 挖矿分支：succeeded 即收
    for row in q("SELECT id FROM tensor_dev_missions WHERE task_key LIKE 'paper-mine-w%' "
                 "AND status='succeeded'"):
        mid = int(row[0])
        br = f"agent/m{mid}"
        if mid in done or not branch_exists(br):
            continue
        if ff_merge(br):
            done.append(mid)
    # ② draft 分支：评审 PASS/MINOR 才收（FAIL 稿永不合并）
    for row in q("SELECT id, task_key FROM tensor_dev_missions WHERE task_key LIKE 'paper-draft-%' "
                 "AND status='succeeded'"):
        mid, tk = int(row[0]), row[1]
        slug = tk[len("paper-draft-"):]
        if slug.startswith("w") and "-" not in slug or mid in done:
            continue
        v = scalar(f"SELECT left(title,140) FROM tensor_reports WHERE title LIKE 'Review: {slug} —%' "
                   "ORDER BY id DESC LIMIT 1")
        if "FAIL" in v or "PASS" not in v and "MINOR" not in v:
            continue
        br = f"agent/m{mid}"
        if branch_exists(br) and ff_merge(br):
            done.append(mid)


def mine_due(st):
    if active_count("paper-mine-") > 0:
        return False, "active mine exists"
    last = scalar("SELECT to_char(max(created_at),'YYYY-MM-DD HH24:MI') FROM tensor_dev_missions "
                  "WHERE task_key LIKE 'paper-mine-%'")
    try:
        top = scalar("SELECT max(substring(task_key from 'paper-mine-w([0-9]+)')::int) "
                     "FROM tensor_dev_missions WHERE task_key LIKE 'paper-mine-%'")
        st["mine_wave"] = max(int(st.get("mine_wave", 0) or 0), int(top) if top and top != "0" else 0)
    except Exception:
        pass
    now = time.time()
    if last == "0" or not last:
        return True, "no mine ever"
    age = now - datetime.datetime.strptime(last, "%Y-%m-%d %H:%M").timestamp()
    if age < MINE_GAP:
        return False, f"last mine {last} ({int(age//60)}min ago)"
    return True, f"last mine {last}"


def dispatch_mine(st):
    st["mine_wave"] = int(st.get("mine_wave", 0)) + 1
    wave = st["mine_wave"]
    focus = "W1 首波：三种子课题全核实 + 新候选扫描（近 30 天报告优先）。" if wave == 1 else \
            f"W{wave}：增量扫描上一波（见 registry 更新时间）之后的新报告 + 既有课题证据面补全。"
    goal = MINE_TMPL.format(wave=wave, focus=focus, ban=EVO1_BAN, pin=PIN_RULE, nofab=NOFAB)
    mid = insert_mission(goal, f"paper-mine-w{wave}", MINE_MODEL,
                         '{"max_tokens":4000000,"max_tool_calls":100,"max_wall_seconds":14400}')
    st["last_mine_ts"] = time.time()
    log(f"DISPATCH paper-mine-w{wave} m{mid} model={MINE_MODEL}")
    return mid


def watchdog(st):
    rows = q("SELECT id, task_key, coalesce(claimed_by, chr(45)), coalesce(model, chr(45)) "
             "FROM tensor_dev_missions "
             "WHERE task_key LIKE 'paper-draft-%' AND status='succeeded'")
    now = time.time()
    for mid, tk, seat, dmodel in rows:
        slug = tk[len("paper-draft-"):]
        if slug.startswith("w") and "-" not in slug:
            continue  # 挖矿波本身不算 draft 链
        has_review = int(scalar(f"SELECT count(*) FROM tensor_dev_missions "
                                f"WHERE task_key='paper-review-{slug}'"))
        if has_review:
            st["draft_saw"].pop(slug, None)
            continue
        first = st["draft_saw"].setdefault(slug, now)
        if now - first >= WATCHDOG_S and st.get("review_sent", {}).get(slug, 0) < 2:
            goal = REVIEW_TMPL.format(slug=slug, ban=EVO1_BAN, draft_seat=seat)
            # 机器级回避：评审钉扎与 draft 异族模型（同模型=可能同席，m1290 教训）
            rv_model = "deepseek-v4-flash" if dmodel == "qwen3.6-plus" else "qwen3.6-plus"
            rid = insert_mission(goal, f"paper-review-{slug}", rv_model,
                                 '{"max_tokens":3000000,"max_tool_calls":80,"max_wall_seconds":10800}')
            st.setdefault("review_sent", {})[slug] = st.get("review_sent", {}).get(slug, 0) + 1
            log(f"WATCHDOG dispatch paper-review-{slug} m{rid} model={rv_model} "
                f"(draft m{mid} by {seat}/{dmodel}, chain stalled)")
    cut = now - 7 * 86400
    for d in (st["draft_saw"],):
        for k in [k for k, v in d.items() if isinstance(v, (int, float)) and v < cut]:
            d.pop(k, None)


def caps():
    today = datetime.date.today().isoformat()
    mat = int(scalar(f"SELECT count(*) FROM tensor_dev_missions WHERE task_key LIKE 'paper-mat-%' "
                     f"AND created_at::date='{today}'"))
    if mat >= MAT_DAILY_CAP:
        log(f"WARN paper-mat 今日 {mat} 单已达帽 {MAT_DAILY_CAP}（挖矿侧应停止再派素材单）")
    act_d = active_count("paper-draft-")
    if act_d > 2:
        log(f"WARN 并发 draft {act_d} > 2 帽")


def main():
    log("=== paper_line v2 守护启动（防造假+回避+收割+隔离钉池；事故根因 report 912）===")
    st = load_state()
    while True:
        try:
            due, why = mine_due(st)
            if due:
                dispatch_mine(st)
            else:
                log(f"mine skip: {why}")
            watchdog(st)
            harvest(st)
            caps()
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {e}")
        save_state(st)
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
