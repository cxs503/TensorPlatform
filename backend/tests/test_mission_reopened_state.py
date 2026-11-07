"""REOPENED 状态正式化回归门禁（SPEC §4.2 / 战役 IN19-Q7 / 迁移 103）。

判据分五组（全部机器可裁）：
  A 契约   —— 状态词表 v3 含 reopened、迁移终态 CHECK == 契约、迁移图闭包
              （节点/边都落在词表内）、终态只许通向 reopened、reopened 入边只来自终态；
  B 迁移   —— 迁移 103 的证据约束（status='reopened' 必须带 from/ref/reason）与
              mission_lifecycle_log.action 棘轮（reopen 入词，历史词不缩）；
  C 判据   —— plan_reopen 规则矩阵（非终态/生产者/无引用/坏引用/短理由/好路径）；
  D 双实现 —— 后端 api.domain.mission_state_machine 与 ops/taskplane/mission_reopen
              在全矩阵上逐格判定相同（两薄层不许漂移），且重开 SQL 是单语句、
              被契约终态集守卫；
  E 守护   —— 库外私有旗标 main_red_hold 在生产 py 面清零（守护切换无漏）、
              tp_pusher 已接线 mission_reopen、新面零领域词（夹具用合成域名）。

TensorPhysics 侧对契约文件做字节镜像对拍（其 tests/test_platform_status_contracts.py）；
漂移必红一边，这是 diff=0 契约的机器形态。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CONTRACTS = BACKEND / "contracts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 合成夹具：零领域语义（域词禁硬编码，夹具用合成域名） ─────────────────────
SYNTH_ACTOR = "gov-acme"          # 治理侧（非生产者）
SYNTH_PRODUCER = "worker-bravo"   # 生产者
SYNTH_REF = "acceptance:9001"
SYNTH_REASON = "regressed after merge: regression suite red"


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text())


def _ops_module():
    """按路径装载 ops 侧实现（与 daemon 的装载方式一致，不依赖包名）。"""
    path = ROOT / "ops" / "taskplane" / "mission_reopen.py"
    spec = importlib.util.spec_from_file_location("ops_mission_reopen", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── A. 契约与迁移图 ────────────────────────────────────────────────────────
class TestContract:
    def test_vocab_has_reopened_and_stays_versioned(self):
        vocab = _load("mission_status_vocab.v1.json")
        sm = _load("mission_state_machine.v1.json")
        assert vocab["version"] >= 3, "词表增补必须 bump 版本（v2 -> v3）"
        assert "reopened" in vocab["statuses"]
        assert "reopened" not in vocab["terminal"], "reopened 是非终态（返工待放行）"
        assert sm["contract"] == "mission_state_machine"
        assert isinstance(sm["version"], int) and sm["version"] >= 1
        # 语言无关：状态词一律小写下划线，避免大小写双写
        assert all(re.fullmatch(r"[a-z_]+", s) for s in vocab["statuses"])

    def test_graph_is_closed_on_vocab_and_reopen_is_the_only_back_edge(self):
        vocab = _load("mission_status_vocab.v1.json")
        sm = _load("mission_state_machine.v1.json")
        states, terminal = set(vocab["statuses"]), set(vocab["terminal"])
        graph = {src: tuple(edge["to"]) for src, edge in sm["transitions"].items()}

        assert set(graph) == states, f"图节点缺/多: {set(graph) ^ states}"
        for src, dsts in graph.items():
            unknown = set(dsts) - states
            assert not unknown, f"{src} 指向词表外状态 {unknown}"
            assert src not in dsts, f"{src} 自环"

        reopen_from = set(sm["reopen"]["from"])
        assert reopen_from == terminal, "重开入口必须恰是终态集"
        # 终态不得直通「干活态」——「终态不可被库外守护悄悄复活」的机器形态。
        # （terminal->awaiting_pr_merge/failed 是 PR 记账边，不是复活，故不在禁列。）
        workable = set(sm["workable"])
        assert workable <= states
        declared = set(sm["audited_retry_edges"]["edges"])
        # 豁免边必须真存在于图里（不许写空头豁免）
        live_edges = {f"{a}->{b}" for a, dsts in graph.items() for b in dsts}
        assert declared <= live_edges, f"空头豁免边: {declared - live_edges}"
        # 未声明的「终态 -> 干活态」后门必须为 0（= 没有绕过 reopened 的复活通道）
        backdoors = {f"{a}->{b}" for a in terminal for b in graph.get(a, ())
                     if b in workable} - declared
        assert backdoors == set(), f"未声明的复活后门: {sorted(backdoors)}"
        # 干活态只能经 reopened 回到终态之外的活跃面：反过来，非终态不许进 reopened
        for src, dsts in graph.items():
            if "reopened" in dsts:
                assert src in terminal, f"{src} 非终态却可进 reopened（重开后门）"
        # reopened 的入边只来自终态
        inbound = {src for src, dsts in graph.items()
                   if "reopened" in dsts and src != "reopened"}
        assert inbound == terminal, f"reopened 入边来源非终态: {inbound - terminal}"
        assert "reopened" in graph and graph["reopened"], "reopened 必须是可离开的 parked 态"

    def test_runtime_constants_derive_from_contract(self):
        from api.domain.mission_state_machine import REOPEN_FROM, REOPENED, STATUSES
        from api.tensor_missions import MISSION_STATUSES
        assert tuple(MISSION_STATUSES) == STATUSES
        assert REOPENED == "reopened"
        assert set(REOPEN_FROM) == set(_load("mission_status_vocab.v1.json")["terminal"])

    def test_migration_terminal_check_equals_contract(self):
        """迁移终态（文件名字典序最后一份定义）恰等于契约词表。"""
        defs = []
        for sql in sorted((BACKEND / "migrations").glob("*.sql")):
            for stmt in sql.read_text(encoding="utf-8").split(";"):
                norm = re.sub(r"\s+", " ", stmt)
                if "TABLE tensor_dev_missions" not in norm or "CHECK (status IN" not in norm:
                    continue
                m = re.search(r"CHECK \(status IN \(([^)]*)\)", stmt)
                defs.append((sql.name,
                             {t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()}))
        assert defs
        contract = set(_load("mission_status_vocab.v1.json")["statuses"])
        for fname, states in defs:
            assert states <= contract, f"{fname} 定义超出契约：{states - contract}"
        assert defs[-1][1] == contract, f"终态定义 {defs[-1][0]} != 契约词表"


# ── B. 迁移 103：DB 级证据约束 + 审计动作棘轮 ──────────────────────────────
class TestMigration103:
    def _sql(self) -> str:
        return (BACKEND / "migrations" / "103_mission_reopened.sql").read_text(encoding="utf-8")

    def test_reopened_requires_cited_evidence_in_db(self):
        sql = re.sub(r"\s+", " ", self._sql())
        assert "ADD COLUMN IF NOT EXISTS reopen_ref" in sql
        assert "ADD COLUMN IF NOT EXISTS reopened_from" in sql
        assert "tensor_dev_missions_reopen_evidence_check" in sql
        # 约束体：reopened 行必须带 from/ref/reason 三者
        body = re.search(r"CHECK \(status <> 'reopened' OR \((.*?)\)\)", sql)
        assert body, "缺少 reopened 证据约束"
        for col in ("reopened_from", "reopen_ref", "reopen_reason"):
            assert f"{col} IS NOT NULL" in body.group(1)

    def test_lifecycle_action_ratchet_keeps_history(self):
        defs = []
        for sql in sorted((BACKEND / "migrations").glob("*.sql")):
            txt = sql.read_text(encoding="utf-8")
            for stmt in txt.split(";"):
                if "mission_lifecycle_log_action_check" not in stmt or "CHECK (action IN" not in stmt:
                    continue
                m = re.search(r"CHECK \(action IN \(([^)]*)\)", stmt)
                defs.append((sql.name,
                             {t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()}))
        assert defs, "未找到 mission_lifecycle_log 的 action CHECK 定义"
        first, last = defs[0][1], defs[-1][1]
        assert "reopen" in last, f"终态 action 词表缺 reopen：{last}"
        assert first <= last, f"棘轮缩词：{first - last}"


# ── C. plan_reopen 判据矩阵 ────────────────────────────────────────────────
class TestReopenDecision:
    def test_happy_path(self):
        from api.domain import mission_state_machine as sm
        d = sm.plan_reopen(from_status="succeeded", actor_role="ADMIN",
                           acceptance_ref=SYNTH_REF, reason=SYNTH_REASON,
                           actor_identity=SYNTH_ACTOR, producer_identity=SYNTH_PRODUCER)
        assert d["ok"] and d["code"] == sm.OK
        assert d["to_status"] == sm.REOPENED

    @pytest.mark.parametrize("overrides,code", [
        ({"from_status": "running"}, "not_reopenable_from"),      # 在飞的活不走重开
        ({"from_status": "no_such_state"}, "unknown_status"),
        ({"actor_role": "WORKER"}, "not_governance_actor"),        # 治理侧才可重开
        ({"actor_identity": SYNTH_PRODUCER}, "producer_cannot_reopen"),
        ({"acceptance_ref": None}, "acceptance_ref_missing"),
        ({"acceptance_ref": "succeeded-last-time"}, "acceptance_ref_malformed"),
        ({"reason": "fix"}, "reason_too_short"),
        ({"target": "succeeded"}, "target_not_allowed"),           # 不许直接回终态
    ])
    def test_rejections(self, overrides, code):
        from api.domain import mission_state_machine as sm
        kw = dict(from_status="failed", actor_role="MANAGER",
                  acceptance_ref=SYNTH_REF, reason=SYNTH_REASON,
                  actor_identity=SYNTH_ACTOR, producer_identity=SYNTH_PRODUCER)
        kw.update(overrides)
        d = sm.plan_reopen(**kw)
        assert not d["ok"] and d["code"] == code, d

    def test_transition_guard_and_release(self):
        from api.domain import mission_state_machine as sm
        sm.assert_transition("reopened", "queued")          # 治理放行后返工
        with pytest.raises(sm.InvalidTransition):
            sm.assert_transition("queued", "reopened")      # 只有终态可被重开
        with pytest.raises(sm.InvalidTransition):
            sm.assert_transition("succeeded", "running")    # 不许跳级复活
        assert sm.plan_release(from_status="reopened")["to_status"] == "queued"
        assert not sm.plan_release(from_status="succeeded")["ok"]


# ── D. 双实现不漂移 + SQL 形状 ─────────────────────────────────────────────
class TestOpsBackendParity:
    def test_decision_matrix_identical(self):
        from api.domain import mission_state_machine as be
        ops = _ops_module()
        roles = [None, "ADMIN", "MANAGER", "GOVERNANCE", "WORKER"]
        refs = [None, SYNTH_REF, "pr:acme/unit-x:inherited"]
        reasons = [None, "", "short", SYNTH_REASON]
        froms = list(be.STATUSES) + ["no_such_state"]
        checked = 0
        for f in froms:
            for role in roles:
                for ref in refs:
                    for reason in reasons:
                        for ident, prod in ((SYNTH_ACTOR, SYNTH_PRODUCER),
                                            (SYNTH_PRODUCER, SYNTH_PRODUCER)):
                            kw = dict(from_status=f, actor_role=role,
                                      acceptance_ref=ref, reason=reason,
                                      actor_identity=ident, producer_identity=prod)
                            a, b = be.plan_reopen(**kw), ops.plan_reopen(**kw)
                            assert (a["ok"], a["code"], a["to_status"]) == \
                                   (b["ok"], b["code"], b["to_status"]), (kw, a, b)
                            checked += 1
        assert checked >= 500, checked

    def test_reopen_sql_is_single_atomic_guarded_statement(self):
        ops = _ops_module()
        plan = ops.plan_reopen(from_status="succeeded", actor_role="ADMIN",
                               acceptance_ref=SYNTH_REF, reason=SYNTH_REASON)
        sql = ops.reopen_sql(7, plan, actor="guard-x")
        assert sql.count(";") == 0, "多语句=非原子"
        low = sql.lower()
        assert low.startswith("with upd as")
        assert "insert into mission_lifecycle_log" in low
        assert "'reopen'" in sql
        for state in ops.REOPEN_FROM:
            assert f"'{state}'" in sql, f"守卫缺终态 {state}"
        assert "status in (" in low
        with pytest.raises(ValueError):
            ops.reopen_sql(7, dict(plan, ok=False), actor="guard-x")


# ── E. 守护切换无漏 + 零领域语义 ───────────────────────────────────────────
LEGACY_SENTINEL = "main_red" + "_hold"      # 拆开写：本文件自身不制造字符串命中
SCAN_DIRS = ("ops", "backend/api", "tools", "scripts")


def _production_py_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            yield path


def _code_only(path: Path) -> str:
    """剥掉注释与字符串字面量后的源码（历史说明可以提及旧旗标，赋值/判断不许）。"""
    import io
    import tokenize
    out = []
    try:
        with path.open("rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                out.append(tok.string)
    except (tokenize.TokenError, SyntaxError):
        # 词法不可分（历史遗留 py2 风格/拼接片段）时退回逐行去注释
        return "\n".join(line.split("#", 1)[0]
                         for line in path.read_text(encoding="utf-8",
                                                    errors="ignore").splitlines())
    return " ".join(out)


def test_guard_switch_leaves_no_legacy_sentinel():
    hits = [str(p.relative_to(ROOT)) for p in _production_py_files()
            if LEGACY_SENTINEL in _code_only(p)]
    assert hits == [], f"库外私有旗标未清零（守护切换有漏）：{hits}"


def test_tp_pusher_wired_to_formal_state():
    src = (ROOT / "ops" / "taskplane" / "tp_pusher.py").read_text(encoding="utf-8")
    assert "mission_reopen" in src, "tp_pusher 未接线正式重开模块"
    assert "_reopen_inherited_red" in src
    # 私有旗标只允许出现在历史说明里，不允许再被赋值
    assert not re.search(r"""rec\[["']ci["']\]\s*=\s*["']%s["']""" % LEGACY_SENTINEL, src)


DOMAIN_WORDS = (
    "阻力", "推进", "耐波", "操纵", "稳性", "冰区", "螺旋桨", "船体", "船舶",
)
DOMAIN_WORDS_EN = ("resistance", "propulsion", "seakeeping", "manoeuvring",
                   "maneuvering", "hydrodynamic", "ship", "ice", "propeller")


def _new_surface_files():
    return [
        CONTRACTS / "mission_state_machine.v1.json",
        CONTRACTS / "mission_status_vocab.v1.json",
        CONTRACTS / "mission_task_status_map.v1.json",
        BACKEND / "api" / "domain" / "mission_state_machine.py",
        BACKEND / "migrations" / "103_mission_reopened.sql",
        ROOT / "ops" / "taskplane" / "mission_reopen.py",
    ]


def test_zero_domain_semantics_in_new_surface():
    """平台面不得硬编码任何领域词（夹具/理由用合成域名）。"""
    offenders = []
    for path in _new_surface_files():
        text = path.read_text(encoding="utf-8")
        for word in DOMAIN_WORDS:
            if word in text:
                offenders.append((path.name, word))
        for word in DOMAIN_WORDS_EN:
            if re.search(rf"\b{word}\b", text):
                offenders.append((path.name, word))
    assert offenders == [], f"领域词硬编码：{offenders}"
    assert re.search(r"[a-z]+-[a-z]+", SYNTH_ACTOR), "夹具应是合成域名"


def test_appended_guard_block_in_pusher_is_domain_neutral():
    """tp_pusher 既有域过滤门是既定边界逻辑，只要求本次新增的重开段零领域词。"""
    src = (ROOT / "ops" / "taskplane" / "tp_pusher.py").read_text(encoding="utf-8")
    start = src.index("def _reopen_module")
    end = src.index("def _main_red(")
    block = src[start:end]
    bad = [w for w in DOMAIN_WORDS if w in block]
    bad += [w for w in DOMAIN_WORDS_EN if re.search(rf"\b{w}\b", block)]
    assert bad == [], f"新增守护段出现领域词：{bad}"
    assert "REOPENED" in block or "mission_reopen" in block


# ── F. 普查器与迁移器（口径工件可复算；历史存量迁移是纯 dry-run 判据） ──────
def _tool_module(rel: str, name: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_census_tool_contract_invariants_hold_without_db():
    census = _tool_module("tools/audit/reopen_census.py", "m5022_census")
    inv = census.contract_invariants()
    assert inv["closure_ok"] is True, inv
    assert inv["terminal_to_workable_backdoors"] == []
    assert inv["reopen_inbound"] == sorted(inv["terminal"])


def test_legacy_state_migration_is_dry_run_and_maps_to_formal_word(tmp_path):
    import json as _json
    import subprocess
    state = tmp_path / "pusher_state.json"
    state.write_text(_json.dumps({
        "11": {"status": "pushed", "ci": LEGACY_SENTINEL, "pr": "x"},
        "12": {"status": "pushed", "ci": "green", "pr": "y"},
        "13": {"status": "pushed", "ci": LEGACY_SENTINEL},
    }))
    out = tmp_path / "migration.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "ops" / "taskplane" / "migrate_reopen_state.py"),
         "--dry-run", "--state-file", str(state), "--json-out", str(out)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    report = _json.loads(out.read_text())
    assert report["mode"] == "dry-run"
    assert report["candidates"] == 2, report
    assert report["to_state"] == "reopened"
    assert report["applied"] == 0
    # dry-run 不改源文件：旧旗标原样保留，等待 --apply 走正式通道
    after = _json.loads(state.read_text())
    assert after["11"]["ci"] == LEGACY_SENTINEL
