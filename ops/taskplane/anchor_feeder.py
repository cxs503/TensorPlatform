#!/usr/bin/env python3
"""anchor_feeder —— 主线锚长设供给守护（FEAT-ANCHOR-FEEDER；case_feeder 的泛化）

背景（近 24h 类型结构实测）：自驱 55.9% / 知识工厂 16.5% / 算例池 14.8% /
主线（REPRO-+XVAL-+JOUR-）<3%——失衡根因不是主线没活，而是**主线锚没有长设供给**：
case_feeder 只喂 tensor_case_pool（CASE-），主线锚全靠人肉规划/提案，于是自驱成了默认主粮。

机制（照抄 case_feeder，区别只在锚源查询）：
  1. 闲位门：idle = 工位总数 − 在飞单；idle < IDLE_FLOOR(8) 不供给（不空转刷单）
  2. 每轮帽 min(MAX_PER_CYCLE=8, idle//4)，其中 ≥MAINLINE_FLOOR(=50%) 给主线锚源
  3. 六仓（TensorLBM/FVM/FEM/DEM/BEM/SPH，见 REPOS）轮转取例，源间也轮转
  4. 幂等：feature_ledger 已有该 slug / 已有挂该锚点的在飞单 → 跳过（只补账，不重派）
  5. 覆盖账回流：锚点消账（ledger done/verified）→ 对应池行置 passed；每轮 log 覆盖账
锚源（按优先级）：
  repro  ← repro_pool（新增表，由 tensor_repro_papers 论文清单 + tensor_benchmark_cases
           的 paper 类行播种）→ 锚点 REPRO-*
  xval   ← oss_inventory × oss_solutions（开源对标）→ 锚点 XVAL-*
  case   ← tensor_case_pool（既有）→ 锚点 CASE-*
  ledger ← 任务榜既有开放锚（feature_ledger.status=open，TIER_A 族）→ 兜底，不新造锚

用法：
  python3 anchor_feeder.py            # 常驻（30min 轮）
  python3 anchor_feeder.py --once     # 单轮后退出
  python3 anchor_feeder.py --once --dry-run   # 只读 DB、只打印计划，不写库不发单
日志 /var/log/tp-anchor-feeder.log；状态 /root/tp-agent/anchor_feeder_state.json。
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

INTERVAL = 1800
MAX_PER_CYCLE = 8
IDLE_FLOOR = 8
MAINLINE_FLOOR = 0.5           # 本轮配额里主线锚（repro/xval）最低占比
LOG = os.environ.get("ANCHOR_FEEDER_LOG", "/var/log/tp-anchor-feeder.log")
STATE = os.environ.get("ANCHOR_FEEDER_STATE", "/root/tp-agent/anchor_feeder_state.json")
LOCK = os.environ.get("ANCHOR_FEEDER_LOCK", "/root/tp-agent/anchor_feeder.lock")
ENV_FILE = os.environ.get("ANCHOR_FEEDER_ENV", "/root/tp-agent/agent.env")
API_BASE = os.environ.get("TENSOR_API_BASE_INTERNAL", "http://127.0.0.1:8010/api/v1")
PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

REPOS = ("TensorLBM", "TensorFVM", "TensorFEM", "TensorDEM", "TensorBEM", "TensorSPH")
MAINLINE_SOURCES = ("repro", "xval")
OTHER_SOURCES = ("case", "ledger")
SOURCE_ORDER = MAINLINE_SOURCES + OTHER_SOURCES
TIER_A = re.compile(r"^(GAP-|H[0-9]+-|C[0-9]+-|CAP-|KPI-|REPRO-|XVAL-|KBM-|CASE-|JOUR-)")
# 领域识别（优先级从特异到宽）：OSS 仓名/方法名里领域词常嵌在中间（pylbm / listLBM / openLBMPM），
# 故不用 "两侧非字母" 的强边界；但对 DG/CAD/BEM/SPH 这类短词用 \b 防误命中。
DOMAIN_PATTERNS = (
    ("LBM", re.compile(r"(?i)lbm|lattice.?boltzmann")),
    ("FVM", re.compile(r"(?i)fvm|openfoam|finite.?volume|fluent")),
    ("FEM", re.compile(r"(?i)fem\b|abaqus|ansys|fenics|deal\.?ii|calculix")),
    ("DEM", re.compile(r"(?i)dem\b|discrete.?element|liggghts|yade|lammps")),
    ("BEM", re.compile(r"(?i)bem\b|boundary.?element")),
    ("SPH", re.compile(r"(?i)sph\b|smoothed.?particle")),
    ("DG", re.compile(r"(?i)\bdg\b|discontinuous.?galerkin")),
    ("CAD", re.compile(r"(?i)\bcad\b|opencascade|step.?file")),
)


# ---------------------------------------------------------------- 基础工具
ECHO = False           # --dry-run/--once 时打开，便于人工/CI 看单轮审计输出


def log(m):
    line = time.strftime("[%m-%d %H:%M:%S] ") + m
    if ECHO or sys.stdout.isatty():
        print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_env(path=ENV_FILE):
    try:
        return dict(l.strip().split("=", 1) for l in open(path)
                    if l.strip() and not l.startswith("#") and "=" in l)
    except FileNotFoundError:
        return {}


def e164(s):
    """str 去空 → 可安全嵌入单引号 SQL。"""
    return str(s if s is not None else "").replace("'", "''")


def slug_norm(s, maxlen=40):
    """稳定 slug 片段：大写、非字母数字折成 '-'、去首尾 '-'。"""
    out = re.sub(r"[^A-Za-z0-9]+", "-", str(s or "")).strip("-").upper()
    return out[:maxlen].strip("-")


class Pg:
    """psql -tA 只读/写入通道；q() 返回 list[list[str]]。"""

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


def api(method, path, payload=None, token=None):
    req = urllib.request.Request(API_BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login(env):
    if not env.get("TENSOR_AGENT_USER"):
        return None
    c, d = api("POST", "/auth/login", {"username": env["TENSOR_AGENT_USER"],
                                       "password": env.get("TENSOR_AGENT_PASS", "")})
    return d.get("access_token") if c == 200 else None


# ---------------------------------------------------------------- 锚源纯函数（可测）
@dataclass
class Candidate:
    slug: str
    repo: str
    source: str
    title: str
    ref: str
    tol: str
    goal: str
    pool_key: str = ""      # 回流用池主键（case_code / paper_key），空=无池
    extra: dict = field(default_factory=dict)


def domain_repo(*texts):
    """从任意文本里认领域 → Tensor{TOKEN}；认不出返回 None（宁可少供，不造垃圾锚）。"""
    blob = " ".join(str(t or "") for t in texts)
    for tok, rx in DOMAIN_PATTERNS:
        if rx.search(blob):
            return "Tensor" + tok
    return None


def split_quota(n, mainline_available, other_available, floor=MAINLINE_FLOOR):
    """主线配额下限：mainline_n = clamp(ceil(n*floor), 0, mainline_available)，其余给其它源。"""
    if n <= 0:
        return 0, 0
    m = min(mainline_available, max(1 if n > 0 else 0, int(-(-n * floor // 1))))
    m = min(m, n)
    if mainline_available < m:            # 主线源枯竭 → 空位让给其它源
        m = mainline_available
    o = min(other_available, n - m)
    if o < n - m:                          # 其它源也枯竭 → 回填主线
        m = min(mainline_available, n - o)
    return m, o


def round_robin(buckets, quota, order=SOURCE_ORDER):
    """源间轮转 + 源内仓间轮转（照抄 case_feeder「六仓轮转防一仓独占」，按仓游标实现）。"""
    picked = []
    if quota <= 0:
        return picked
    srcs = [s for s in order if buckets.get(s)]
    cursor = {s: 0 for s in srcs}
    while len(picked) < quota and srcs:
        for s in list(srcs):
            if len(picked) >= quota:
                break
            b = buckets[s]
            if not b:
                srcs.remove(s)
                continue
            repos = list(b.keys())
            i = cursor.get(s, 0) % len(repos)
            rep = repos[i]
            picked.append(b[rep].pop(0))
            if not b[rep]:                 # 该仓取空 → 删桶，游标停在原位（等价下一仓）
                del b[rep]
                cursor[s] = i
            else:
                cursor[s] = i + 1
            if not b:
                srcs.remove(s)
    return picked


def goals_for(cand_kind):
    """各锚源的报告要求（六段 + 零手抄 + honest-FAIL），与 case_feeder 同源措辞。"""
    core = ("报告六段（①问题+ref 出处 ②方法与实现 ③参数表（无量纲数完整）④对照表（ref vs got+误差打勾叉）"
            "⑤收敛性/参数敏感性（至少两点）⑥失败模式与工件）+生态摩擦如实记。"
            "失败=honest-FAIL 留 fail_stage+failure_reason（两轮法接棒）。")
    three = (f"流程：开工三查（knowledge_base 同域 capability+negative_knowledge_ledger keywords="
             f"域仓名+oss_solutions 同域）；跑不动=生态病记 friction，不另起炉灶；"
             f"计算>10min 走 cluster_queue（禁指定节点）。")
    return core, three


def goal_repro(c, header="【锚点=%s】【第1轮·任务榜·论文复现】"):
    core, three = goals_for("repro")
    return (header % c.slug +
            "owner 2026-11-12 主线锚长设供给令（FEAT-ANCHOR-FEEDER）：本单=repro_pool 中一篇文献的定量复现。\n"
            f"文献：{c.title}｜ref：{c.ref}——**目标数值从文献原文/原表查得（零手抄，报告引用原表原页）**。\n"
            f"容差判据：{c.tol}。\n" + three + core)


def goal_xval(c, header="【锚点=%s】【第1轮·任务榜·开源对标】"):
    core, three = goals_for("xval")
    return (header % c.slug +
            "owner 2026-11-12 主线锚长设供给令（FEAT-ANCHOR-FEEDER）：本单=与成熟开源实现的交叉对标（XVAL）。\n"
            f"对标对象：{c.title}｜来源：{c.ref}——**对标实现的关键做法从其源码/文档查得（零手抄）**。\n"
            f"判据：{c.tol}。\n" + three + core +
            "\n对标特别要求：给出本仓 vs OSS 的差异清单（做法/参数/边界条件），并说明移植取舍。\n")


def goal_case(c, header="【锚点=%s】【第1轮·任务榜·算例池】"):
    core, three = goals_for("case")
    return (header % c.slug +
            "owner 2026-11-12 算例池令：benchmark 以百计铺开长设供给，本单=池中一例。\n"
            f"算例：{c.title}（{c.repo}）。ref：{c.ref}——**目标数值从 ref 原文/手册查得（零手抄，报告引用原表原页）**。\n"
            f"容差判据：{c.tol}。\n" + three + core)


def goal_ledger(c, header="【锚点=%s】【保供轮·任务榜·开放锚兜底】"):
    core, three = goals_for("ledger")
    return (header % c.slug +
            "owner 2026-11-12 主线锚长设供给令：主线源枯竭时以任务榜既有开放锚兜底（不新造锚）。\n"
            f"开放锚：{c.title}（{c.repo}）。验收：{c.tol}\n" + three + core)


def case_candidates(rows):
    out = []
    for r in rows:
        code, repo, name, ref, tol = (list(r) + [""] * 5)[:5]
        c = Candidate(slug=f"CASE-{code}", repo=repo, source="case", title=name,
                      ref=ref, tol=tol, goal="", pool_key=code)
        c.goal = goal_case(c)
        out.append(c)
    return out


def repro_candidates(rows):
    """rows: (paper_key, title, ref_source, target_repo, tol_policy, anchor_slug)"""
    out = []
    for r in rows:
        paper_key, title, ref, repo, tol, anchor = (list(r) + [""] * 6)[:6]
        slug = anchor or f"REPRO-{slug_norm(paper_key)}"
        repo = repo or domain_repo(paper_key, ref)
        if not repo:
            continue
        c = Candidate(slug=slug, repo=repo, source="repro", title=title, ref=ref,
                      tol=tol, goal="", pool_key=paper_key)
        c.goal = goal_repro(c)
        out.append(c)
    return out


XVAL_BONUS = re.compile(r"(?i)valid|bench|reference|analytic|convergence|ghia|order|conserv|"
                        r"comparison|baseline|verif|test")


def _xval_score(tag, method, feature_slug):
    """可对标性打分：有验证/基准证据的方法排在前面（0-3），分数相同按 slug 稳定排序。"""
    return sum(1 for t in (tag, method, feature_slug) if t and XVAL_BONUS.search(t))


def xval_candidates(sol_rows, existing_slugs=(), inventory=None):
    """oss_solutions × oss_inventory → XVAL-*；同域同法去重，已存在锚跳过。"""
    inv = {r[0]: r for r in (inventory or [])}
    seen = set(existing_slugs)
    out = []
    for r in sol_rows:
        repo, feature_slug, tag = (list(r) + [""] * 4)[:3]
        method = (list(r) + [""] * 4)[3] if len(r) > 3 else ""
        dom = domain_repo(feature_slug, method, tag, repo)
        if not dom:
            continue
        key = slug_norm(tag or method or feature_slug.replace("NEW:", ""))
        if not key:
            continue
        slug = f"XVAL-{dom[len('Tensor'):]}-{key}"
        if slug in seen:
            continue
        seen.add(slug)
        focus = (inv.get(repo) or [None] * 4)[3] or ""
        ref = f"OSS {repo}" + (f"（{focus}）" if focus else "")
        tol = "<15%（owner 精度标准：对标误差 >15% 不可接受）"
        c = Candidate(slug=slug, repo=dom, source="xval",
                      title=f"{repo} 的 {tag or key} 做法", ref=ref, tol=tol, goal="",
                      pool_key=repo)
        c.goal = goal_xval(c)
        c.extra["score"] = _xval_score(tag, method, feature_slug)
        out.append(c)
    return sorted(out, key=lambda x: (-x.extra["score"], x.slug))


LEDGER_PRIORITY = ("REPRO-", "XVAL-", "JOUR-")   # 兜底时主线族优先，其余按 id


def ledger_candidates(rows, covered=()):
    """rows: (slug, title, domain, acceptance) —— 任务榜既有开放锚兜底（不新造锚）。"""
    out = []
    for r in rows:
        slug, title, domain, acc = (list(r) + [""] * 4)[:4]
        if slug in covered or not TIER_A.match(slug):
            continue
        repo = domain_repo(domain) or (domain if domain.startswith("Tensor") else None)
        if not repo:
            continue
        c = Candidate(slug=slug, repo=repo, source="ledger", title=title,
                      ref="任务榜既有开放锚", tol=acc or "按锚点原文验收", goal="")
        c.goal = goal_ledger(c)
        c.extra["score"] = 0 if slug.startswith(LEDGER_PRIORITY) else 1
        out.append(c)
    return sorted(out, key=lambda x: (x.extra["score"], x.slug))


def bucket_by_repo(cands, prefer=REPOS):
    """源内按仓分桶；六仓优先排序，其余仓按名排在后面（仍参与轮转）。"""
    order = {r: i for i, r in enumerate(prefer)}
    b = {}
    for c in sorted(cands, key=lambda x: (order.get(x.repo, 99), x.slug)):
        b.setdefault(c.repo, []).append(c)
    return b


# ---------------------------------------------------------------- repro_pool
REPRO_POOL_DDL = """
CREATE TABLE IF NOT EXISTS repro_pool (
    id           serial PRIMARY KEY,
    paper_key    text UNIQUE NOT NULL,
    kind         text NOT NULL DEFAULT 'paper',
    title        text NOT NULL,
    ref_source   text NOT NULL,
    target_repo  text,
    tol_policy   text,
    anchor_slug  text,
    status       text NOT NULL DEFAULT 'pending',
    evidence     text,
    created_at   timestamptz DEFAULT now(),
    updated_at   timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS repro_pool_status_idx ON repro_pool (status, target_repo);
"""


def seed_rows(papers, bench):
    """论文清单 + benchmark_cases paper 类 → repro_pool 行（纯函数，幂等靠 ON CONFLICT）。

    papers: (paper_key, title, ref_source, target_repo, quantitative_targets, anchor_slug, status)
    bench : (anchor_slug, case_name, ref_source, metric, tolerance, repo)
    """
    rows = []
    for r in papers:
        paper_key, title, ref, repo, targets, anchor, status = (list(r) + [""] * 7)[:7]
        if not paper_key or str(status) == "passed":
            continue
        tol = targets if isinstance(targets, str) else json.dumps(targets, ensure_ascii=False)
        rows.append({"paper_key": paper_key, "kind": "paper", "title": title or paper_key,
                     "ref": ref or "", "repo": repo or domain_repo(paper_key, ref) or "",
                     "tol": (tol or "")[:800],
                     "anchor": anchor or f"REPRO-{slug_norm(paper_key)}"})
    for r in bench:
        anchor, name, ref, metric, tol, repo = (list(r) + [""] * 6)[:6]
        key = anchor or f"BENCHPAPER-{slug_norm(name)}"
        rows.append({"paper_key": key, "kind": "bench-paper", "title": name or key,
                     "ref": ref or "", "repo": repo or domain_repo(anchor, ref) or "",
                     "tol": f"{metric}：{tol}"[:800], "anchor": anchor or ""})
    # 去重（后到者不覆盖先到者，与 ON CONFLICT DO NOTHING 同语义）
    seen, out = set(), []
    for r in rows:
        if r["paper_key"] in seen or not r["repo"]:
            continue
        seen.add(r["paper_key"])
        out.append(r)
    return out


def seed_sql(rows):
    if not rows:
        return ""
    vals = ",".join(
        "('{k}','{kind}','{t}','{r}','{repo}','{tol}','{a}')".format(
            k=e164(x["paper_key"]), kind=e164(x["kind"]), t=e164(x["title"]),
            r=e164(x["ref"]), repo=e164(x["repo"]), tol=e164(x["tol"]), a=e164(x["anchor"]))
        for x in rows)
    return ("INSERT INTO repro_pool (paper_key,kind,title,ref_source,target_repo,tol_policy,anchor_slug) "
            f"VALUES {vals} ON CONFLICT (paper_key) DO NOTHING")


# ---------------------------------------------------------------- 轮次
class Feeder:
    def __init__(self, db, dry_run=False, env=None, do_api=True):
        self.db = db
        self.seeded = []
        self.dry = dry_run
        self.env = env if env is not None else load_env()
        self.do_api = do_api and not dry_run
        self.plan = []

    # --- 读数
    def idle(self):
        w = self.db.q("SELECT count(*) FROM factory_resident_workers")
        m = self.db.q("SELECT count(*) FROM tensor_dev_missions WHERE status IN ('claimed','running')")
        total = int(w[0][0]) if w else 0
        busy = int(m[0][0]) if m else 0
        return total, busy, max(0, total - busy)

    def ensure_schema(self):
        if self.dry:
            return
        for stmt in [s.strip() for s in REPRO_POOL_DDL.split(";") if s.strip()]:
            self.db.q(stmt)

    def seed_repro_pool(self):
        papers = self.db.q(
            "SELECT coalesce(paper_key,''), coalesce(title,''), coalesce(ref_source,''), "
            "coalesce(target_repo,''), coalesce(quantitative_targets::text,''), coalesce(anchor_slug,''), "
            "coalesce(status,'') FROM tensor_repro_papers")
        bench = self.db.q(
            "SELECT anchor_slug, case_name, ref_source, metric, coalesce(tolerance,''), coalesce(repo,'') "
            "FROM tensor_benchmark_cases "
            "WHERE anchor_slug ~ '^REPRO-' OR ref_source ~ '(19|20)[0-9][0-9]'")
        rows = seed_rows(papers, bench)
        self.seeded = rows            # dry-run 时用作 repro 源的预演行
        if not self.dry and rows:
            self.db.q(seed_sql(rows))
        return len(rows)

    def backflow(self):
        """覆盖账回流：锚消账 → 池行 passed（照抄 case_feeder 语义，多两个池）。"""
        if self.dry:
            return
        try:
            self.db.q("UPDATE tensor_case_pool p SET status='passed', updated_at=now() "
                      "FROM feature_ledger f WHERE f.slug='CASE-'||p.case_code "
                      "AND f.status IN ('done','verified') AND p.status='dispatched'")
        except RuntimeError:
            pass
        try:
            self.db.q("UPDATE repro_pool p SET status='passed', updated_at=now() "
                      "FROM feature_ledger f WHERE f.slug=p.anchor_slug "
                      "AND f.status IN ('done','verified') AND p.status='dispatched'")
        except RuntimeError:
            pass

    # --- 覆盖账
    def coverage(self):
        cov = {}
        try:
            for r in self.db.q("SELECT repo, count(*) FILTER (WHERE status='pending'), "
                               "count(*) FILTER (WHERE status='dispatched'), "
                               "count(*) FILTER (WHERE status='passed') "
                               "FROM tensor_case_pool GROUP BY repo ORDER BY repo"):
                cov.setdefault("case", []).append(f"{r[0]}:{r[1]}待/{r[2]}派/{r[3]}过")
        except RuntimeError:
            pass
        try:
            for r in self.db.q("SELECT coalesce(target_repo,'?'), count(*) FILTER (WHERE status='pending'), "
                               "count(*) FILTER (WHERE status='dispatched'), "
                               "count(*) FILTER (WHERE status='passed') "
                               "FROM repro_pool GROUP BY 1 ORDER BY 1"):
                cov.setdefault("repro", []).append(f"{r[0]}:{r[1]}待/{r[2]}派/{r[3]}过")
        except RuntimeError:
            cov.setdefault("repro", []).append("(repro_pool 未建)")
        try:
            rows = self.db.q("SELECT count(*) FILTER (WHERE status='open'), "
                             "count(*) FILTER (WHERE status IN ('done','verified')) "
                             "FROM feature_ledger WHERE slug ~ '^XVAL-'")
            if rows:
                cov["xval"] = [f"xval锚:{rows[0][0]}开/{rows[0][1]}过"]
        except RuntimeError:
            pass
        try:
            rows = self.db.q(
                "SELECT count(*) FROM feature_ledger f WHERE f.status='open' "
                "AND f.slug ~ '^(GAP-|REPRO-|XVAL-|CASE-|JOUR-|KBM-)' AND NOT EXISTS ("
                "SELECT 1 FROM tensor_dev_missions i WHERE i.status IN ('queued','running','claimed') "
                "AND i.goal LIKE '%'||f.slug||'%')")
            if rows:
                cov["ledger"] = [f"开放锚无在飞:{rows[0][0]}"]
        except RuntimeError:
            pass
        return cov

    # --- 候选
    def existing_slugs(self):
        s = set()
        for r in self.db.q("SELECT slug FROM feature_ledger"):
            s.add(r[0])
        return s

    def in_flight_slugs(self):
        """已有非活跃挂锚单的 slug 集合（交差审计单不算占位）。"""
        out = set()
        for r in self.db.q("SELECT goal FROM tensor_dev_missions "
                           "WHERE status IN ('queued','running','claimed') "
                           "AND goal LIKE '%锚点=%' AND goal NOT LIKE '%交差审计%'"):
            m = re.search(r"锚点=([A-Za-z0-9\-]+)", r[0])
            if m:
                out.add(m.group(1))
        return out

    def collect(self):
        exist = self.existing_slugs()
        inflight = self.in_flight_slugs()
        covered = exist | inflight
        b = {}
        # repro：池表缺失（首轮/干跑）→ 直接用播种结果预演，不中断供给
        try:
            rows = self.db.q("SELECT paper_key, title, ref_source, target_repo, coalesce(tol_policy,''), "
                             "coalesce(anchor_slug,'') FROM repro_pool "
                             "WHERE status='pending' ORDER BY id")
        except RuntimeError:
            rows = [[r["paper_key"], r["title"], r["ref"], r["repo"], r["tol"], r["anchor"]]
                    for r in self.seeded]
        b["repro"] = bucket_by_repo([c for c in repro_candidates(rows) if c.slug not in covered])
        # xval
        sol = self.db.q("SELECT repo, coalesce(feature_slug,''), coalesce(tag,''), coalesce(method,'') "
                        "FROM oss_solutions ORDER BY id")
        inv = self.db.q("SELECT repo, tier, coalesce(focus,''), status FROM oss_inventory")
        b["xval"] = bucket_by_repo([c for c in xval_candidates(sol, exist | inflight, inv)
                                    if c.slug not in inflight])
        # case
        rows = self.db.q("SELECT case_code, repo, name, ref_source, tol_policy FROM tensor_case_pool "
                         "WHERE status='pending' AND (linked_anchor IS NULL OR linked_anchor='') "
                         "ORDER BY id")
        b["case"] = bucket_by_repo([c for c in case_candidates(rows) if c.slug not in covered])
        # ledger 兜底
        rows = self.db.q("SELECT slug, title, domain, acceptance FROM feature_ledger "
                         "WHERE status='open' ORDER BY id")
        b["ledger"] = bucket_by_repo([c for c in ledger_candidates(rows, inflight)])
        return b

    # --- 供给
    def run_cycle(self):
        self.ensure_schema()
        seeded = self.seed_repro_pool()
        self.backflow()
        cov = self.coverage()
        total, busy, idle = self.idle()
        line = "  ".join(f"[{k}] " + " ".join(v) for k, v in cov.items())
        log(f"覆盖账 {line}｜repro_pool 播种 {seeded} 行｜工位 {total} 在飞 {busy} 闲 {idle}")
        if idle < IDLE_FLOOR:
            log(f"闲位 {idle} < {IDLE_FLOOR}，本轮不供给")
            return []
        n = min(MAX_PER_CYCLE, idle // 4)
        buckets = self.collect()
        ml_avail = sum(len(v) for s in MAINLINE_SOURCES for v in buckets.get(s, {}).values())
        case_avail = sum(len(v) for v in buckets.get("case", {}).values())
        m_n, o_n = split_quota(n, ml_avail, case_avail)
        plan = round_robin({s: buckets.get(s, {}) for s in MAINLINE_SOURCES}, m_n,
                           order=MAINLINE_SOURCES)
        plan += round_robin({"case": buckets.get("case", {})}, o_n, order=("case",))
        if len(plan) < n:      # 兜底：主线+算例池都不足额，才用任务榜开放锚（不新造锚）
            plan += round_robin({"ledger": buckets.get("ledger", {})}, n - len(plan),
                                order=("ledger",))
        log(f"本轮计划 {len(plan)} 单（主线 {m_n}/算例池 {o_n}/兜底 {len(plan) - m_n - o_n}；"
            f"候选 主线{ml_avail} 算例池{case_avail} 兜底{sum(len(v) for v in buckets.get('ledger', {}).values())}）"
            + ("｜".join(c.slug for c in plan) if plan else "｜无候选"))
        self.plan = plan
        for c in plan:
            self.dispatch(c)
        return plan

    def dispatch(self, c):
        if self.dry:
            log(f"[dry-run] 将供给 {c.slug}（{c.source}/{c.repo}）")
            return None
        if c.slug not in self.existing_slugs():
            self.db.q(
                "INSERT INTO feature_ledger (slug,title,domain,acceptance,status,source) VALUES "
                f"('{e164(c.slug)}','{e164(c.title)[:200]}','{e164(c.repo)}',"
                f"'{e164(c.tol)[:400]}','open','anchor-feeder')")
        if c.source == "case" and c.pool_key:
            self.db.q(f"UPDATE tensor_case_pool SET status='dispatched', updated_at=now() "
                      f"WHERE case_code='{e164(c.pool_key)}'")
        if c.source == "repro" and c.pool_key:
            self.db.q(f"UPDATE repro_pool SET status='dispatched', updated_at=now() "
                      f"WHERE paper_key='{e164(c.pool_key)}'")
        if c.source == "xval" and c.pool_key:
            self.db.q(f"UPDATE oss_solutions SET transfer=coalesce(transfer,'')||' xval:{e164(c.slug)}' "
                      f"WHERE repo='{e164(c.pool_key)}' AND instr(coalesce(transfer,''),'xval:')=0")
        tok = login(self.env) if self.do_api else None
        if not tok:
            log(f"登录失败，{c.slug} 只建锚不派单")
            return None
        code, d = api("POST", "/tensor/missions",
                      {"goal": c.goal, "repo": c.repo, "runner": "native", "origin": "agent",
                       "task_key": f"anchor-{c.slug.lower()}"[:60],
                       "budget": {"compute_hours": 400, "wall_hours": 24}}, tok)
        if code == 200:
            if c.source in ("case", "repro") and c.pool_key:
                tbl, key = ("tensor_case_pool", "case_code") if c.source == "case" else ("repro_pool", "paper_key")
                try:
                    self.db.q(f"UPDATE {tbl} SET mission_id={int(d.get('id'))}, updated_at=now() "
                              f"WHERE {key}='{e164(c.pool_key)}'")
                except RuntimeError:
                    pass  # repro_pool 无 mission_id 列时静默（状态已置 dispatched）
            log(f"供给 {c.slug}（{c.source}/{c.repo}）→ m{d.get('id')}")
            save_state(c.slug, c.source, d.get("id"))
        else:
            log(f"供给失败 {c.slug}: {code} {str(d)[:120]}")
        return d.get("id")


def save_state(slug, source, mid):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    st.setdefault("dispatched", {})[slug] = {"source": source, "mission_id": mid,
                                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    st["last_cycle"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(STATE, "w") as f:
            json.dump(st, f, ensure_ascii=False)
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="anchor_feeder 主线锚长设供给")
    ap.add_argument("--once", action="store_true", help="跑一轮即退出")
    ap.add_argument("--dry-run", action="store_true", help="只读 DB、只打印计划，不写库不发单")
    args = ap.parse_args(argv)
    global ECHO
    ECHO = bool(args.once or args.dry_run)
    if not args.dry_run:
        try:
            lock = open(LOCK, "w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("另一实例持锁，退出")
            return 0
    feeder = Feeder(Pg(), dry_run=args.dry_run)
    while True:
        try:
            feeder.run_cycle()
        except Exception as e:
            log(f"cycle error: {type(e).__name__} {e}")
        if args.once or args.dry_run:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())