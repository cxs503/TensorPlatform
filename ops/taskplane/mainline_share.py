#!/usr/bin/env python3
"""mainline_share —— 主线类占比仪表（FEAT-ANCHOR-FEEDER 验收②，挂进 compound_meter）

背景：近 24h 类型结构 = 自驱 55.9% / 知识工厂 16.5% / 算例池 14.8% / 主线（REPRO-+XVAL-+JOUR-）<3%。
只靠"总量在涨"看不见结构失衡——本仪表把**主线占比**做成可读、可存档、可周报复测的度量：

  分类（对 tensor_dev_missions.goal 的前缀/锚点，规则外置为 CAT_RULES，可配置）：
    mainline    锚点 REPRO- / XVAL- / JOUR-（论文复现 / 开源对标 / 期刊成稿）
    case_pool   锚点 CASE-（算例池）
    knowledge   锚点 KBM- / 知识工厂工序包（kf）
    selfdrive   【自找活 / 自驱席位
    other       其余（战役 DAG、返修轮、治理等）

  窗口：24h / 7d / 前 7d（7d−前7d = 周变化，pp），主线占比 = mainline / 全部任务。
  落表：compound_metrics（metric=mainline_share_pct_24h|7d，state=meter）→ compound_meter 台账可读；
        mainline_share_metrics（自有历史表，逐类计数，供周报做变化曲线）。

用法：
  python3 mainline_share.py            # 打一次 24h/7d/周变化 + 落表（compound_meter 调用同参）
  python3 mainline_share.py --json     # 机读
  python3 mainline_share.py --weekly   # 周报文本（主线占比变化）
  python3 mainline_share.py --post     # 周报计量落 chronicle（机制化周报，不等人工）
"""
import json
import os
import re
import subprocess
import sys
import time

PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
LOG = os.environ.get("MAINLINE_SHARE_LOG", "/var/log/tp-mainline-share.log")

# 顺序敏感：先认锚点族，再认自驱/知识工厂，最后兜底 other
CAT_RULES = (
    ("mainline", re.compile(r"锚点=(REPRO|XVAL|JOUR)-|^【锚点=(REPRO|XVAL|JOUR)-")),
    ("case_pool", re.compile(r"锚点=CASE-")),
    ("knowledge", re.compile(r"锚点=KBM-|知识工厂|knowledge_factory|【工序包")),
    ("selfdrive", re.compile(r"【自找活|自驱")),
)
CATS = ("mainline", "case_pool", "knowledge", "selfdrive", "other")
WINDOWS = (("24h", 0, 24), ("7d", 0, 168), ("prev7d", 168, 336))  # (名, 起h, 止h)


def classify(goal):
    g = goal or ""
    for cat, rx in CAT_RULES:
        if rx.search(g):
            return cat
    return "other"


class Pg:
    def __init__(self, db="tensorplatform"):
        self.db = db

    def q(self, sql):
        r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", self.db,
                            "-tA", "-F", "\x01", "-R", "\x02",
                            "-v", "ON_ERROR_STOP=1", "-c", sql],
                           capture_output=True, text=True, env=PGENV)
        if r.returncode != 0:
            raise RuntimeError(f"psql rc={r.returncode}: {r.stderr.strip()[:200]}")
        # 记录分隔符必须用 \x02：多行 goal/title 里含换行，按 \n 切会字段错位
        return [rec.split("\x01") for rec in r.stdout.strip().split("\x02") if rec.strip()]

def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def summarize(rows):
    """rows: [(goal, hours_ago)] → 三窗口（24h / 7d / 前7d）逐类计数与主线占比（纯函数）。"""
    out = {}
    for name, lo, hi in WINDOWS:
        cnt = {c: 0 for c in CATS}
        for goal, h in rows:
            if h is None or not (lo <= h <= hi):
                continue
            cnt[classify(goal)] += 1
        tot = sum(cnt.values())
        out[name] = {"counts": cnt, "total": tot, "mainline_pct": pct(cnt["mainline"], tot),
                     "case_pool_pct": pct(cnt["case_pool"], tot),
                     "selfdrive_pct": pct(cnt["selfdrive"], tot),
                     "knowledge_pct": pct(cnt["knowledge"], tot)}
    out["delta_pp"] = round(out["7d"]["mainline_pct"] - out["prev7d"]["mainline_pct"], 1)
    out["mainline_24h_pct"] = out["24h"]["mainline_pct"]
    out["mainline_7d_pct"] = out["7d"]["mainline_pct"]
    return out


def fetch_rows(db, days=14):
    rows = db.q("SELECT coalesce(goal,''), "
                "EXTRACT(EPOCH FROM (now()-created_at))/3600.0 FROM tensor_dev_missions "
                f"WHERE created_at > now()-interval '{int(days)} days'")
    out = []
    for r in rows:
        try:
            out.append((r[0], float(r[1])))
        except (IndexError, ValueError, TypeError):
            continue          # 脏行跳过，不拖垮整表（宁可少算不造假）
    return out


def mainline_metrics(db):
    """compound_meter 调用入口：返回可并入 metrics 的标量字典。"""
    try:
        s = summarize(fetch_rows(db))
    except Exception as e:
        log_once(f"mainline_metrics 失败: {type(e).__name__} {e}")
        return {}
    return {"mainline_24h_pct": s["mainline_24h_pct"], "mainline_7d_pct": s["mainline_7d_pct"],
            "mainline_delta_pp": s["delta_pp"],
            "mainline_7d_n": s["7d"]["counts"]["mainline"], "missions_7d": s["7d"]["total"]}


def record(db, s=None):
    """落台账：compound_metrics（并入既有仪表）+ mainline_share_metrics（自有历史）。"""
    s = s or summarize(fetch_rows(db))
    db.q("CREATE TABLE IF NOT EXISTS compound_metrics (ts timestamptz DEFAULT now(),"
         " metric text, value bigint, state text)")
    for key, val in (("mainline_share_pct_24h", s["mainline_24h_pct"]),
                     ("mainline_share_pct_7d", s["mainline_7d_pct"])):
        db.q(f"INSERT INTO compound_metrics (metric, value, state) VALUES ('{key}', {val}, 'meter')")
    db.q("CREATE TABLE IF NOT EXISTS mainline_share_metrics (ts timestamptz DEFAULT now(),"
         " win text, cat text, n integer, share_pct numeric)   -- win: window 是 PG 保留字")
    for name in ("24h", "7d", "prev7d"):
        for cat in CATS:
            n = s[name]["counts"][cat]
            db.q(f"INSERT INTO mainline_share_metrics (win, cat, n, share_pct) "
                 f"VALUES ('{name}','{cat}',{n},{pct(n, s[name]['total'])})")
    return s


def weekly_report(s):
    """周报计量：主线占比与结构变化（只计量，不改进）。"""
    def line(name):
        c = s[name]["counts"]
        return (f"{name:>6} 总{s[name]['total']:>4}｜主线 {c['mainline']:>3} ({s[name]['mainline_pct']:>5}%)"
                f"｜算例池 {c['case_pool']:>3} ({s[name]['case_pool_pct']:>5}%)"
                f"｜知识工厂 {c['knowledge']:>3} ({s[name]['knowledge_pct']:>5}%)"
                f"｜自驱 {c['selfdrive']:>3} ({s[name]['selfdrive_pct']:>5}%)"
                f"｜其它 {c['other']:>3}")
    return ("主线占比仪表（window 内新建任务按锚点族分类）\n"
            + line("24h") + "\n" + line("7d") + "\n" + line("prev7d") + "\n"
            + f"周变化 7d−前7d = {s['delta_pp']:+.1f} pp"
            + ("  ← 主线供给不足（<3% 红线，检查 anchor_feeder）" if s["mainline_7d_pct"] < 3.0 else ""))


def log_once(m, echo=True):
    line = time.strftime("[%m-%d %H:%M:%S] ") + m
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if echo and sys.stdout.isatty():
        print(line, flush=True)


def post_weekly(db, txt, s):
    """周报计量机制化：写 chronicle_snapshots（与 compound_meter 同表，审计可溯）。"""
    payload = {"note": "mainline-share-weekly", "mainline_24h_pct": s["mainline_24h_pct"],
               "mainline_7d_pct": s["mainline_7d_pct"], "delta_pp": s["delta_pp"],
               "report": txt}
    db.q("INSERT INTO chronicle_snapshots (ts, payload) VALUES (now(), "
         + "'" + json.dumps(payload, ensure_ascii=False).replace("'", "''") + "'::jsonb)")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    db = Pg()
    s = summarize(fetch_rows(db))
    if "--post" in argv:
        txt = weekly_report(s)
        post_weekly(db, txt, s)
        log_once("周报计量已落 chronicle：" + txt.replace("\n", " ｜ "))
        return 0
    if "--weekly" in argv:
        print(weekly_report(s))
        return 0
    if "--json" in argv:
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return 0
    record(db, s)
    txt = weekly_report(s)
    print(txt, flush=True)                       # CLI 单次运行：人读输出
    log_once(txt.replace("\n", " ｜ "), echo=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())