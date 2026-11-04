#!/usr/bin/env python3
"""生态巡更派发器（owner 2026-11-01 批：1.2.3 往前推）。

每 30min 扫 11 库：同库无在飞 + 冷却≥6h + 全局在飞<50 + 日帽≤8 → 派 1 张巡更单。
焦点四轮转：上游差集/测试基线漂移/文档一致性/吸收候选。
"""
import json, os, time, urllib.request, pathlib

API = "http://127.0.0.1:8010/api/v1"
STATE = "/root/tp-agent/eco_patrol_state.json"
# 2026-11-03 owner 令执行：TensorPhysics/TensorPlatform 重构归专人，agent 禁入
# （11-01 放行的 11 库巡更面被 11-02 冻结+重构令收窄；API 冻结门只挂 TP 仓，
# TensorPhysics 无门——在此摘源。m2162 已 suspend，可逆）
REPOS = ["TensorLBM", "TensorDEM", "TensorFEM", "TensorFVM",
         "TensorSPH", "TensorDG", "TensorCAD", "TensorBEM", "TensorPapers"]
COOLDOWN_H, DAILY_CAP, MAX_INFLIGHT = 6, 8, 50

def token():
    # PR-9（AOG P1）净化：凭据不入库。环境变量优先，eco_admin.env（600）兜底，
    # 与 merge_governor v1.3 的 _papi_token() 同一约定。
    user = os.environ.get("ECO_ADMIN_USER", "eco_admin")
    pw = os.environ.get("ECO_ADMIN_PASS", "")
    if not pw:
        try:
            for ln in open(pathlib.Path("/root/tp-agent/eco_admin.env")):
                if ln.startswith("ECO_ADMIN_PASS="):
                    pw = ln.split("=", 1)[1].strip()
        except Exception:
            pass
    d = json.dumps({"username": user, "password": pw}).encode()
    r = urllib.request.Request(f"{API}/auth/login", data=d, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))["access_token"]

def req(path, tok, data=None):
    r = urllib.request.Request(f"{API}{path}",
        data=json.dumps(data).encode() if data else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method="POST" if data else "GET")
    return json.load(urllib.request.urlopen(r))

FOCI = [
    ("上游差集", "对比本仓 remote 上游（若有）与本地 HEAD 的提交差集；无上游则盘点最近 15 提交主题演化，产出变更主题清单与风险标注。"),
    ("测试基线漂移", "重跑核心测试套件，与仓库内既有基线记录（若有）对照；产出通过/失败签名清单，区分环境红与真红；发现新增真红即修。"),
    ("文档一致性", "README/示例与实际代码 API 抽查对照≥10 项，列出不一致处并修复最关键 3 处；示例可运行性验证。"),
    ("吸收候选", "找出一个可吸收的外部改进（上游/对标库做法），以最小增量落到本仓并配测试；先红后绿给证据。"),
]
TPL = """【生态巡更·{repo}·{focus}】{focus_desc}
纪律：先查任务池避免重复目标；改动最小增量；不推 GitHub（归 tp_pusher 门禁线）；报告论文级入库 docs/ 或 reports/；工作落 agent/m<id> 分支。"""

def main():
    tok = token()
    st = json.loads(pathlib.Path(STATE).read_text()) if pathlib.Path(STATE).exists() else {"repos": {}, "today": "", "day_count": 0}
    today = time.strftime("%F")
    if st.get("today") != today:
        st.update({"today": today, "day_count": 0})
    ms = req("/tensor/missions?limit=200&offset=0", tok)
    items = ms.get("items") or []
    total_active = sum(1 for m in items if m.get("status") in ("running", "queued"))
    if total_active >= MAX_INFLIGHT:
        return
    busy = set()
    for m in items:
        if m.get("status") in ("running", "queued"):
            busy.add(m.get("repo"))
    for repo in REPOS:
        if st["day_count"] >= DAILY_CAP: break
        if repo in busy: continue
        last = st["repos"].get(repo, {}).get("ts", 0)
        if time.time() - last < COOLDOWN_H * 3600: continue
        n = st["repos"].get(repo, {}).get("n", 0)
        focus, desc = FOCI[n % len(FOCI)]
        d = req("/tensor/missions", tok, {"goal": TPL.format(repo=repo, focus=focus, focus_desc=desc),
            "repo": repo, "origin": "owner", "runner": "native", "priority": 1})
        st["repos"][repo] = {"ts": time.time(), "n": n + 1}
        st["day_count"] += 1
        with open("/root/tp-agent/eco_patrol.log", "a") as f:
            f.write(f"{time.strftime('%F %T')} 派 {repo}#{d.get('id')} 焦点={focus}\n")
    pathlib.Path(STATE).write_text(json.dumps(st, ensure_ascii=False))

while True:
    try:
        main()
    except Exception as e:
        with open("/root/tp-agent/eco_patrol.log", "a") as f:
            f.write(f"{time.strftime('%F %T')} 异常: {e}\n")
    time.sleep(1800)
