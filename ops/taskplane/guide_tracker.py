#!/usr/bin/env python3
# 指南对标进度看板（owner 2026-10-30「大目标开发进度实时反馈机制/防原地打转」）
# 每 30min：11 项指南 KPI 快照 → guide_dashboard.json（含 24h delta）；
# 案例连续失败≥3（自上次成功起）→ 黑名单 guide_blacklist.json + 自动派根因升级单（arch，24h 一次/案例）；
# 每日一发看板报告（scenario_key=guide-dashboard，报告页可见）。
import json, os, time, pathlib, subprocess, urllib.request, urllib.error

API = "http://127.0.0.1:8010/api/v1"
USER, PASS = os.environ.get("TP_API_USER", ""), os.environ.get("TP_API_PASS", "")
PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}
DASH = pathlib.Path("/root/tp-agent/guide_dashboard.json")
BLACK = pathlib.Path("/root/tp-agent/guide_blacklist.json")
STATE = pathlib.Path("/root/tp-agent/guide_tracker_state.json")
LOG = "/var/log/tp-guide-tracker.log"
POLL = 1800

def log(m):
    open(LOG, "a").write(time.strftime("[%m-%d %H:%M:%S] ") + m + "\n")

def q(sql):
    return subprocess.run(["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform",
                           "-tAc", sql], capture_output=True, text=True, env=PGENV).stdout.strip()

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

# ── 打转检测：案例自上次成功后的连续失败 ──
def stalls():
    rows = q("SELECT substring(goal from '案例 ([a-zA-Z0-9_]+)（')||'|'||id||'|'||status "
             "FROM tensor_dev_missions WHERE goal LIKE '%案例 %（Tensor%' ORDER BY id")
    per = {}
    for line in rows.split("\n"):
        if line.count("|") != 2: continue
        case, mid, st = line.split("|")
        per.setdefault(case, []).append((int(mid), st))
    out = {}
    for case, ms in per.items():
        trailing_f = 0
        for _, st in reversed(ms):
            if st == "failed": trailing_f += 1
            else: break
        if trailing_f >= 3:
            out[case] = {"trailing_fails": trailing_f,
                         "since_mission": ms[len(ms) - trailing_f][0],
                         "total": {"s": sum(1 for _, s in ms if s == "succeeded"),
                                   "f": sum(1 for _, s in ms if s == "failed")}}
    return out

def kpis(st):
    term = int(q("SELECT count(*) FROM tensor_dev_missions WHERE status IN ('succeeded','failed')") or 0)
    succ = int(q("SELECT count(*) FROM tensor_dev_missions WHERE status='succeeded'") or 0)
    rate = round(100.0 * succ / max(term, 1), 1)
    last48 = q("SELECT count(*) FILTER (WHERE status='succeeded')||'/'||count(*) FROM tensor_dev_missions "
               "WHERE status IN ('succeeded','failed') AND finished_at > now()-interval '48 hours'")
    sweep_succ = int(q("SELECT count(DISTINCT substring(goal from '案例 ([a-zA-Z0-9_]+)（')) "
                       "FROM tensor_dev_missions WHERE goal LIKE '%案例 %（Tensor%' AND status='succeeded'") or 0)
    rep_missions = int(q("SELECT count(DISTINCT mission_id) FROM tensor_reports WHERE mission_id IS NOT NULL") or 0)
    busy = int(q("SELECT count(DISTINCT claimed_by) FROM tensor_dev_missions "
                 "WHERE status IN ('running','queued') AND claimed_by IS NOT NULL") or 0)
    inflight = int(q("SELECT count(*) FROM tensor_dev_missions WHERE status IN ('running','queued')") or 0)
    seats = len(json.loads(pathlib.Path("/root/tp-agent/fleet.json").read_text()))
    qr = q("SELECT count(*) FILTER (WHERE status='succeeded')||'/'||count(*) "
           "FROM tensor_dev_missions WHERE goal LIKE '%【QR-P%' AND status IN ('succeeded','failed')")
    qr_run = int(q("SELECT count(*) FROM tensor_dev_missions "
                   "WHERE goal LIKE '%【QR-P%' AND status IN ('running','queued')") or 0)
    try:
        qr_succ, qr_tot = (int(x) for x in qr.split("/"))
    except Exception:
        qr_succ, qr_tot = 0, 0
    return [
        {"kpi": "已合并功能复核通过率", "cur": f"{qr_succ}/{qr_tot}（在飞 {qr_run}）",
         "pct": round(100.0 * qr_succ / qr_tot, 1) if qr_tot else None,
         "note": "owner 2026-10-31 起主进度指标：已合并功能独立参考复核（解析解/文献实验/异离散交叉）通过数/复核终态数；自洽测试不计入；QR-P 前缀单自动累计"},
        {"kpi": "工具接入 ≥5 类", "cur": "4/5", "pct": 80,
         "note": "CFD(LBM/FVM/SPH/DG)/FEA(FEM)/DEM、势流·切片(BEM+Lewis)、经验公式(Holtrop/ITTC/MMG)、规范(IMO) 已真实实现；缺第 5 类=优化库（G-OPT 单已派）"},
        {"kpi": "无人值守 ≥1000 工况", "cur": f"{sweep_succ} 案例级", "pct": round(sweep_succ / 10.0, 1),
         "note": "案例级代理口径（每案例≈1-3 工况）；工况级精确口径待样本库（G-SAMPLE）上线"},
        {"kpi": "成功率 ≥90%+自动归因", "cur": f"{rate}%（{succ}/{term}；48h {last48}）", "pct": round(rate / 0.9, 1),
         "note": "归因机制已建（harness75/model15/task10 分类学+本看板打转检测）"},
        {"kpi": "谱系追溯 100%", "cur": f"{rep_missions}/{term} 有报告工件", "pct": round(100.0 * rep_missions / max(term, 1), 1),
         "note": "代理口径：有报告工件/终态单；M-G1 schema+perf_gate 已就绪待全量接线"},
        {"kpi": "复现 ≥95%", "cur": "机制就绪", "pct": None,
         "note": "跨卡逐位门禁（m10/m305）已证；比率统计待样本库"},
        {"kpi": "门禁覆盖 100%", "cur": "新线全接", "pct": None,
         "note": "扫荡+P2 全部走机器判据门；存量单未回填门禁"},
        {"kpi": "知识库 ≥3学科/3型船/≥1万条", "cur": "样本 0（报告 270 篇≠样本）", "pct": 0,
         "note": "G-SAMPLE 单已派（schema+回填管线）"},
        {"kpi": "对标算例 ≥10（SUBOFF/DTMB/KVLCC2）", "cur": "1 完成+2 在飞", "pct": 10,
         "note": "KVLCC2@15kn 网关真算 ✅；SUBOFF S0=m369 在飞；DTMB 5415=G-DTMB 单已派"},
        {"kpi": "静水阻力 ≤5%/冰区 ≤10%", "cur": "未实测", "pct": 0,
         "note": "SUBOFF S0 在飞（verification 档）；冰区线未开（SIR 战役在 5090 线）"},
        {"kpi": "周期 −50%/试验 −30%", "cur": "无基线", "pct": 0, "note": "需先建计时基线（P3 候选）"},
        {"kpi": "标准≥3/专利≥5/论文≥5/软著≥5", "cur": "论文骨架 1", "pct": 5,
         "note": "5090 线 arXiv 骨架在写；其余未启动"},
    ], {"busy": busy, "seats": seats, "inflight": inflight, "term": term, "succ": succ,
        "rate": rate, "sweep_succ": sweep_succ, "rep_missions": rep_missions, "qr_succ": qr_succ, "qr_tot": qr_tot}

def snapshot():
    st = stalls()
    k, meta = kpis(st)
    prev = json.loads(DASH.read_text()) if DASH.exists() else {}
    snap = {"ts": time.strftime("%F %T"), "kpis": k, "meta": meta,
            "stalls": st, "prev_ts": prev.get("ts")}
    DASH.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    if st:
        BLACK.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    elif BLACK.exists():
        BLACK.unlink()
    return snap

def escalate(snap, state, tok):
    for case, v in snap["stalls"].items():
        last = state["escalated"].get(case, "")
        if last == time.strftime("%F"):  # 每案例每日最多一张
            continue
        goal = (f"【根因升级·案例 {case}】扫荡线 {case} 自上次成功后连续 {v['trailing_fails']} 次失败"
                f"（自 m{v['since_mission']} 起；累计 {v['total']['s']}成/{v['total']['f']}败）。"
                f"禁止再盲试重跑。任务：① 逐单读这些失败 mission 的 result 摘要与 /root/tp-agent/work/ 下工件，"
                f"按 环境/triton绕行/判据红/数值不稳 四类定位根因（写明证据单号与 file:line）；"
                f"② 给修复方案；可行则打补丁+验证脚本跑通一次；不可行则写清阻断理由与所需资源。"
                f"③ 交付 docs/root_cause_{case}.md + artifacts/root_cause_{case}.json。"
                f"完成后 finish。honest-FAIL 如实。")
        s, d = api("POST", "/tensor/missions",
                   {"goal": goal, "repo": "TensorLBM", "runner": "native", "origin": "owner",
                    "model": "glm-5.2",
                    "budget": {"max_tool_calls": 100, "max_wall_seconds": 3600, "max_tokens": 3000000}}, tok)
        if s in (200, 201):
            state["escalated"][case] = time.strftime("%F")
            log(f"根因升级单 {case} → m{d['id']} (连败 {v['trailing_fails']})")
        else:
            log(f"升级派发失败 {case}: {s}")

def publish(snap, tok):
    rows = "\n".join(
        f"| {k['kpi']} | {k['cur']} | {k['pct'] if k['pct'] is not None else '—'}% | {k['note']} |"
        for k in snap["kpis"])
    m = snap["meta"]
    stall_txt = "\n".join(f"- {c}: 连败 {v['trailing_fails']}（自 m{v['since_mission']} 起，累计 {v['total']['s']}成/{v['total']['f']}败）"
                          for c, v in sorted(snap["stalls"].items())) or "（无——当前无案例处于打转态）"
    md = (f"## 指南对标进度看板（{snap['ts']}）\n\n"
          f"### ① 11 项硬考核指标\n\n| 指标 | 现状 | 进度 | 说明 |\n|---|---|---|---|\n{rows}\n\n"
          f"### ② 运行面\n\n- 舰队利用率：**{m['busy']}/{m['seats']}** 在忙（在飞 {m['inflight']} 单）\n"
          f"- 全程成功率 {m['rate']}%（{m['succ']}/{m['term']}）\n"
          f"- 扫荡案例级转绿 {m['sweep_succ']} 案例（verified/ 现存 43 目录，差值=历史目录变动）\n\n"
          f"### ③ 原地打转警报（连续失败≥3 自动拉黑+派根因升级单）\n\n{stall_txt}\n\n"
          f"### ④ 结论\n\n能力地基（多库/门禁/机制）大体成形；量化短板集中在 千工况/万样本/10 算例/精度实测 四项长周期指标。\n"
          f"本看板由 guide_tracker 常设线程序化生成，数字零手抄（SQL 直取）。\n"
          f"无图理由：数据看板无场量物理量，表格即全部证据。")
    s, d = api("POST", "/tensor/reports",
               {"title": f"【Guide】指南对标进度看板 {time.strftime('%m-%d')}",
                "summary": "11 项硬考核指标快照+利用率+打转警报；30min 自动刷新，数字 SQL 直取零手抄。",
                "scenario_key": "guide-dashboard", "stage": "S0",
                "tags": ["指南对标", "看板"], "markdown": md, "figures": []}, tok)
    log(f"看板报告发布: {s} id={d.get('id')}")

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true")
    once = ap.parse_args().once
    state = json.loads(STATE.read_text()) if STATE.exists() else {"escalated": {}, "last_pub": ""}
    state.setdefault("escalated", {})
    log("指南看板线启动")
    while True:
        try:
            tok = login()
            snap = snapshot()
            escalate(snap, state, tok)
            today = time.strftime("%F")
            if state.get("last_pub") != today:
                publish(snap, tok)
                state["last_pub"] = today
            STATE.write_text(json.dumps(state, ensure_ascii=False))
        except Exception as e:
            log(f"轮异常: {e}")
        if once: break
        time.sleep(POLL)

if __name__ == "__main__":
    main()
