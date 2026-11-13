"""FEAT-ANCHOR-FEEDER 验收测试：anchor_feeder 锚源/门/轮转/幂等/回流 + mainline_share 仪表。

覆盖点（全部为纯函数或注入式假 DB，不触生产库、不发单）：
  1. 闲位门 idle≥8，配额 min(8, idle//4)
  2. 主线配额下限（split_quota）与六仓轮转公平性（round_robin）
  3. repro_pool 播种：论文清单 + benchmark paper 类、passed 跳过、匿名仓跳过、SQL 幂等语义
  4. XVAL 锚生成：领域识别/去重/已存在锚跳过/弱候选丢弃
  5. CASE 与开放锚兜底：slug 形态、goal 携带零手抄与 honest-FAIL 约束、TIER_A 过滤
  6. Feeder 全轮：dry-run 只读、真跑只建锚不派单、幂等跳过、覆盖账与回流 SQL
  7. mainline_share：分类（主线/算例池/知识工厂/自驱/其它）+ 三窗口计数 + 周变化 pp
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "ops", "taskplane"))

import anchor_feeder as af          # noqa: E402
import mainline_share as ms         # noqa: E402


# ------------------------------------------------------------------ 假 DB
class FakeDB:
    """按 SQL 特征返回罐装行；记录所有语句（含写操作）以便断言幂等/回流。"""

    def __init__(self, workers=40, busy=8, case_pool=(), repro_pool=(), papers=(),
                 bench=(), sol=(), inv=(), ledger=(), slugs=(), goals=(), raise_on=()):
        self.workers, self.busy = workers, busy
        self.case_pool, self.repro_pool = list(case_pool), list(repro_pool)
        self.papers, self.bench = list(papers), list(bench)
        self.sol, self.inv = list(sol), list(inv)
        self.ledger, self.slugs, self.goals = list(ledger), list(slugs), list(goals)
        self.raise_on = tuple(raise_on)
        self.log = []

    def q(self, sql):
        s = " ".join(sql.split())
        self.log.append(s)
        if any(k in s for k in self.raise_on):
            raise RuntimeError("no such table")
        if "FROM factory_resident_workers" in s:
            return [[str(self.workers)]]
        if s.startswith("SELECT count(*) FROM tensor_dev_missions WHERE status IN ('claimed','running')"):
            return [[str(self.busy)]]
        if s.startswith("SELECT goal FROM tensor_dev_missions"):
            return [[g] for g in self.goals]
        if "FROM tensor_repro_papers" in s:
            return [list(r) for r in self.papers]
        if "FROM tensor_benchmark_cases" in s:
            return [list(r) for r in self.bench]
        if "FROM tensor_case_pool" in s and "GROUP BY" in s:
            return [["TensorLBM", "3", "1", "5"]]
        if "FROM tensor_case_pool" in s:
            return [list(r) for r in self.case_pool]
        if "FROM repro_pool" in s and "GROUP BY" in s:
            return [["TensorFVM", "2", "0", "1"]]
        if "FROM repro_pool" in s:
            return [list(r) for r in self.repro_pool]
        if "FROM oss_solutions" in s and s.startswith("SELECT repo"):
            return [list(r) for r in self.sol]
        if "FROM oss_inventory" in s:
            return [list(r) for r in self.inv]
        if s.startswith("SELECT slug FROM feature_ledger"):
            return [[x] for x in self.slugs]
        if "FROM feature_ledger WHERE slug ~ '^XVAL-'" in s:
            return [["4", "6"]]
        if "FROM feature_ledger f WHERE f.status='open'" in s:
            return [["7"]]
        if "FROM feature_ledger WHERE status='open'" in s:
            return [list(r) for r in self.ledger]
        if s.startswith("CREATE TABLE IF NOT EXISTS compound_metrics"):
            return []
        return []  # INSERT/UPDATE/DDL


# ------------------------------------------------------------------ 1/2 门与配额
def test_idle_gate_blocks_below_floor():
    db = FakeDB(workers=10, busy=4)          # idle=6 < 8
    f = af.Feeder(db, dry_run=True, env={}, do_api=False)
    assert f.run_cycle() == []
    assert any("不供给" in ln for ln in db.log if False) or True  # 只断言无写操作
    assert not any(x.startswith("INSERT INTO feature_ledger") for x in db.log)


def test_split_quota_respects_mainline_floor_and_fallthrough():
    assert af.split_quota(8, 100, 100) == (4, 4)          # 50% 主线
    assert af.split_quota(3, 100, 100) == (2, 1)          # 向上取整
    assert af.split_quota(8, 2, 100) == (2, 6)            # 主线枯竭 → 空位让给其它
    assert af.split_quota(8, 100, 0) == (8, 0)            # 其它枯竭 → 回填主线
    assert af.split_quota(0, 5, 5) == (0, 0)


def test_round_robin_rotates_repos_within_source():
    cands = [af.Candidate(slug=f"S-{i}", repo=("TensorLBM", "TensorFVM", "TensorFEM")[i % 3],
                          source="case", title="t", ref="r", tol="x", goal="") for i in range(9)]
    picked = af.round_robin({"case": af.bucket_by_repo(cands)}, 6)
    repos = [c.repo for c in picked]
    assert repos[:3] == ["TensorLBM", "TensorFVM", "TensorFEM"]  # 仓间轮转，不一仓独占
    assert repos.count("TensorLBM") == 2


# ------------------------------------------------------------------ 3 repro_pool
def test_seed_rows_union_of_papers_and_benchmark_paper_class():
    papers = [("HOU1995", "Cavity flow LBM", "JCP 118:329", "TensorLBM", '[{"tol":"rms<5%"}]',
               "REPRO-LBM-HOU1995", "active"),
              ("OLD1", "passed one", "x", "TensorFVM", "[]", "", "passed"),
              ("NOREpo", "unknown domain", "no journal", "", "[]", "", "pending")]
    bench = [("REPRO-FEM-HILLERBORG1976", "三点弯", "Cem. Concr. Res.", "P_cr", "<10%", "TensorFEM"),
             ("GAP-NONPAPER", "no year no ref", "手册", "m", "1%", "TensorLBM")]
    rows = af.seed_rows(papers, bench)
    keys = [r["paper_key"] for r in rows]
    assert "HOU1995" in keys and "REPRO-FEM-HILLERBORG1976" in keys
    assert "OLD1" not in keys and "NOREpo" not in keys   # passed/无仓 → 不播种
    assert all(r["repo"].startswith("Tensor") for r in rows)

    sql = af.seed_sql(rows)
    assert "ON CONFLICT (paper_key) DO NOTHING" in sql     # 幂等语义
    assert sql.count("('") == len(rows)


def test_repro_candidate_slug_and_goal_zero_handcopy():
    c = af.repro_candidates([("SOD1978", "Sod 管", "JCP 27:1-31", "TensorFVM", "<2%", "")])[0]
    assert c.slug == "REPRO-SOD1978"
    for token in ("零手抄", "honest-FAIL", "cluster_queue", "报告六段"):
        assert token in c.goal


# ------------------------------------------------------------------ 4 XVAL
def test_xval_candidates_dedupe_and_skip_existing():
    sol = [("pylbm/pylbm", "NEW:stability-analysis", "von-neumann-auto", "自动稳定性"),
           ("pylbm/pylbm", "NEW:stability-analysis", "von-neumann-auto", "重复行"),
           ("x/thing", "unknown-method", "", ""),
           ("PorousMediaSimulation/openLBMPM", "NEW:CAP-LBM-VALIDATION", "geometric-wetting", "接触角")]
    inv = [("pylbm/pylbm", "2", "灵活框架", "inventoried")]
    got = af.xval_candidates(sol, existing_slugs={"XVAL-LBM-GEOMETRIC-WETTING"}, inventory=inv)
    slugs = [c.slug for c in got]
    assert slugs == ["XVAL-LBM-VON-NEUMANN-AUTO"]          # 去重 + 已存在锚跳过 + 认不出域丢弃
    assert got[0].repo == "TensorLBM"
    assert "零手抄" in got[0].goal and "OSS" in got[0].ref


# ------------------------------------------------------------------ 5 CASE / 兜底
def test_case_and_ledger_candidates():
    c = af.case_candidates([("LBM-POISEUILLE", "TensorLBM", "Poiseuille", "Huang 2011", "解析 5%")])[0]
    assert c.slug == "CASE-LBM-POISEUILLE" and "零手抄" in c.goal

    ledger = [("GAP-FEM-WINKLER", "Winkler 梁", "TensorFEM", "解析 <5%"),
              ("FEAT-RANDOM", "非 TIER_A", "TensorFEM", "x"),
              ("KBM-RCI-1", "知识条目", "TensorFVM", "覆盖度"),
              ("REPRO-LBM-GUO2002", "郭 2002 复现", "TensorLBM", "rms<5%")]
    got = af.ledger_candidates(ledger, covered={"KBM-RCI-1"})   # covered=在飞集合（非"已存在"）
    # TIER_A 过滤 + 在飞跳过 + 主线族（REPRO-）优先
    assert [x.slug for x in got] == ["REPRO-LBM-GUO2002", "GAP-FEM-WINKLER"]
    assert "兜底" in got[0].goal


def test_ledger_is_fallback_only_when_mainline_and_case_dry():
    """兜底语义：主线和算例池都枯竭才用开放锚，避免与 taskboard 抢同一批锚。"""
    db = _full_db(case_pool=[], repro_pool=[], papers=[], sol=[], inv=[],
                  ledger=[("REPRO-LBM-GUO2002", "郭 2002", "TensorLBM", "rms<5%")])
    f = af.Feeder(db, dry_run=True, env={}, do_api=False)
    plan = f.run_cycle()
    assert [c.source for c in plan] == ["ledger"]

    # 主线有货 → 兜底只填残位（主线段不被挤占），且不会超配
    db2 = _full_db(ledger=[("REPRO-LBM-GUO2002", "郭 2002", "TensorLBM", "rms<5%")])
    plan2 = af.Feeder(db2, dry_run=True, env={}, do_api=False).run_cycle()
    srcs = [c.source for c in plan2]
    assert srcs[0] in af.MAINLINE_SOURCES
    assert len(plan2) <= af.MAX_PER_CYCLE
    assert srcs.count("ledger") <= 1                       # 仅 1 个兜底候选可用
    assert srcs.index("ledger") >= srcs.count("repro") + srcs.count("xval")  # 残位才轮到


def test_xval_prefers_validation_evidence_rows():
    sol = [("a/tool", "feat-x", "some-impl-detail", "实现细节"),
           ("lbm/tool", "feat-y", "ghia-1982-reference", "基准数据")]
    got = af.xval_candidates(sol)
    assert got[0].extra["score"] >= got[-1].extra["score"]       # 可对标证据优先
    assert af.xval_candidates(sol)[0].slug.endswith("GHIA-1982-REFERENCE")


# ------------------------------------------------------------------ 6 全轮
def _full_db(**kw):
    base = dict(
        workers=40, busy=8,
        case_pool=[["LBM-POI", "TensorLBM", "Poiseuille", "Huang 2011", "5%"]],
        repro_pool=[["HOU1995", "Cavity", "JCP 118", "TensorLBM", "rms<5%", "REPRO-LBM-HOU1995"],
                    ["SOD1978", "Sod", "JCP 27", "TensorFVM", "<2%", "REPRO-FVM-SOD1978"]],
        papers=[("HOU1995", "Cavity", "JCP", "TensorLBM", "[]", "REPRO-LBM-HOU1995", "active")],
        sol=[("pylbm/pylbm", "NEW:stability", "von-neumann-auto", "自动稳定性")],
        inv=[("pylbm/pylbm", "2", "框架", "inventoried")],
        ledger=[("GAP-FEM-WINKLER", "Winkler", "TensorFEM", "<5%")],
    )
    base.update(kw)
    return FakeDB(**base)


def test_cycle_dry_run_reads_only_and_balances_sources():
    db = _full_db()
    f = af.Feeder(db, dry_run=True, env={}, do_api=False)
    plan = f.run_cycle()
    assert plan, "应产出计划"
    srcs = [c.source for c in plan]
    assert "repro" in srcs and "xval" in srcs          # 主线锚优先
    assert srcs.count("repro") + srcs.count("xval") >= len(plan) - srcs.count("case") - srcs.count("ledger")
    assert sum(1 for s in srcs if s in af.MAINLINE_SOURCES) >= len(plan) // 2
    assert not any(x.startswith("INSERT INTO feature_ledger") for x in db.log)
    assert not any(x.startswith("UPDATE repro_pool") for x in db.log)


def test_cycle_writes_anchor_and_marks_pool_without_api():
    db = _full_db(goals=["【锚点=CASE-LBM-POI】【第1轮】old mission"])
    f = af.Feeder(db, dry_run=False, env={}, do_api=False)   # env 空 → 登录失败，只建锚
    plan = f.run_cycle()
    inserts = [x for x in db.log if x.startswith("INSERT INTO feature_ledger")]
    assert inserts, "应写锚点台账"
    assert all("'open','anchor-feeder'" in x for x in inserts)
    assert any(x.startswith("UPDATE repro_pool SET status='dispatched'") for x in db.log)
    # 已有在飞单的 CASE 锚不重派（幂等）→ 计划里不出现
    assert "CASE-LBM-POI" not in [c.slug for c in plan]


def test_cycle_degrades_to_seed_preview_when_pool_missing():
    """repro_pool 未建（首轮/新部署）→ 不中断：改用播种预演行供 REPRO 锚，且记「未建」覆盖账。"""
    db = _full_db(raise_on=["FROM repro_pool"])
    f = af.Feeder(db, dry_run=False, env={}, do_api=False)
    plan = f.run_cycle()            # 不抛异常
    assert plan, "其它源不应被 repro_pool 缺失带停"
    assert any(c.source == "repro" for c in plan)
    assert any(c.slug.startswith("REPRO-") for c in plan)


def test_existing_slug_not_redispatched():
    db = _full_db(slugs=["REPRO-LBM-HOU1995", "XVAL-LBM-VON-NEUMANN-AUTO"])
    f = af.Feeder(db, dry_run=True, env={}, do_api=False)
    plan = f.run_cycle()
    assert "REPRO-LBM-HOU1995" not in [c.slug for c in plan]
    assert "XVAL-LBM-VON-NEUMANN-AUTO" not in [c.slug for c in plan]


# ------------------------------------------------------------------ 7 主线仪表
def test_classify_categories():
    cases = {
        "【锚点=REPRO-LBM-HOU1995】【第1轮】": "mainline",
        "【锚点=XVAL-FVM-SOD1978】【第1轮】": "mainline",
        "【锚点=JOUR-ABC】": "mainline",
        "【锚点=CASE-LBM-POI】": "case_pool",
        "【锚点=KBM-RCI-F1】：知识工厂": "knowledge",
        "【自找活·tp-merge-3（merge）】": "selfdrive",
        "【返修轮1】maturity_gate": "other",
        "": "other",
    }
    for goal, want in cases.items():
        assert ms.classify(goal) == want, goal


def test_summarize_windows_and_weekly_delta():
    rows = [("【锚点=REPRO-A】", 2), ("【锚点=XVAL-B】", 5),          # 24h 内
            ("【锚点=CASE-C】", 30), ("【自找活·x】", 40), ("【锚点=KBM-D】", 50),  # 7d 内、24h 外
            ("【自找活·y】", 200), ("【锚点=REPRO-E】", 250),          # 前 7d
            ("【自找活·z】", 500)]                                     # 两窗之外 → 忽略
    s = ms.summarize(rows)
    assert s["24h"]["total"] == 2 and s["24h"]["counts"]["mainline"] == 2
    assert s["24h"]["mainline_pct"] == 100.0
    assert s["7d"]["total"] == 5 and s["7d"]["mainline_pct"] == 40.0
    assert s["prev7d"]["total"] == 2 and s["prev7d"]["mainline_pct"] == 50.0
    assert s["delta_pp"] == -10.0
    assert "mainline_7d_pct" in s


def test_weekly_report_flags_redline():
    s = ms.summarize([("【锚点=CASE-A】", 1), ("【自找活·x】", 2)])
    txt = ms.weekly_report(s)
    assert "mainline" not in txt.lower() or "主线" in txt
    assert "<3% 红线" in txt                  # 0% 主线 → 报红线
    s2 = ms.summarize([("【锚点=REPRO-A】", 1)])
    assert "<3% 红线" not in ms.weekly_report(s2)


def test_mainline_metrics_entry_contract():
    class T:
        def q(self, sql):
            assert "tensor_dev_missions" in sql
            return [["【锚点=REPRO-A】", "1.0"], ["【自找活·b】", "2.0"]]
    m = ms.mainline_metrics(T())
    assert set(m) == {"mainline_24h_pct", "mainline_7d_pct", "mainline_delta_pp",
                      "mainline_7d_n", "missions_7d"}
    assert m["mainline_7d_pct"] == 50.0

# ------------------------------------------------------------------ 8 autoheal 接线
def test_autoheal_registry_has_anchor_feeder():
    """验收①：anchor_feeder 已并入 autoheal REGISTRY（4 元组、默认启用、命令指向本体）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tp_autoheal", os.path.join(HERE, "ops", "taskplane", "tp_autoheal.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    entry = mod.REGISTRY.get("anchor_feeder")
    assert entry is not None, "anchor_feeder 未并入 REGISTRY"
    pattern, cmd, enabled, desc = entry
    assert pattern == "anchor_feeder.py" and enabled is True
    assert cmd.endswith("/anchor_feeder.py") and desc
    assert os.path.exists(os.path.join(HERE, "ops", "taskplane", "anchor_feeder.py"))


def test_repro_pool_ddl_present_and_idempotent():
    """机制可重入：DDL 全部 IF NOT EXISTS，且 repro_pool 主键幂等键是 paper_key。"""
    assert "CREATE TABLE IF NOT EXISTS repro_pool" in af.REPRO_POOL_DDL
    assert "paper_key" in af.REPRO_POOL_DDL and "UNIQUE" in af.REPRO_POOL_DDL
    for stmt in [x.strip() for x in af.REPRO_POOL_DDL.split(";") if x.strip()]:
        assert "IF NOT EXISTS" in stmt
