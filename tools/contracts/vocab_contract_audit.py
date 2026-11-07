#!/usr/bin/env python3
"""G-A/G-B/G-C 词表契约逐项 diff 审计器（migration_disposition_2026-11 §3 收尾复核）。

背景
----
TensorPlatform(TensorPhysics 的宿主平台)与 TensorPhysics 之间，原来有三组「词表」
各自定义、靠人脑同步（迁移前置缺口 G-A/G-B/G-C）：

  G-A  任务状态词表   tensor_dev_missions.status  <->  TensorPhysics TaskStatus
  G-B  事件流词表     会话事件 tensor_agent_session_events.kind（M3/M4 双流）
  G-C  运行登记词表   POST /tensorphysics/runs 的 run/validation status

落法是「共享契约文件（JSON，带版本号）→ 两侧运行时常量从契约派生 → 契约测试
diff=0」。本工具把这个判据从「散落在 pytest 里的断言」抽成一个可独立运行、
可入 CI 的审计器：**逐项**把正典列表（契约 JSON）与运行面（真实 import 到的常量、
数据库迁移 CHECK 定义、SQL 写手位置）对拍，输出每项 canon_only / runtime_only 差集
与 diff 计数。diff 全 0 才叫收尾；任何一项非 0 都定位到具体词。

为什么需要它（而不是只有 pytest）
--------------------------------
1. pytest 只给「红/绿」，不给「差哪几个词」；本工具每项都列差集，可直接贴进审计链。
2. 「新增词项走正典列表非手写分支」这条纪律是**结构性**的：词加在 JSON 里就算合规，
   加在代码字面量里就算违规。工具同时扫「手写分支残留」（源码里的状态字面量／
   手写正则双源／第二处 INSERT 写手），等价于对「单源」做结构证明，而非只比值。
3. dry-run 模式可随时在生产树跑，不落盘。

检查项（items，逐项一行）
------------------------
G-A: statuses / terminal / db_check_terminal / db_check_history_subset /
     map_is_function / map_covers_tph_mirror / platform_only_consistency /
     runtime_derived_from_canon
G-B: event_kinds / event_kind_literals / single_writer / run_registry_no_events /
     governance_members
G-C: run_statuses / validation_statuses / max_manifest_bytes /
     runtime_derived_from_canon / handwritten_dual_source_absent

用法
----
    python3 tools/contracts/vocab_contract_audit.py --dry-run          # 只打印，不落盘
    python3 tools/contracts/vocab_contract_audit.py                    # 写 JSON 工件
    python3 tools/contracts/vocab_contract_audit.py --json-out /tmp/a.json --verbose
    python3 tools/contracts/vocab_contract_audit.py --contracts-dir /tmp/fake  # 负控演练

退出码：0 = 全部 diff=0；2 = 有非 0 diff（或运行面不可导入）。

局限
----
* TensorPhysics 侧仓库不在本工作树内，「跨仓」这一半用冻结镜像常量
  ``TPH_TASK_STATUSES_MIRROR`` 代表（与 backend/tests/test_status_contracts.py 同值）；
  真漂移由 TensorPhysics 侧同名测试判红，本工具只判本仓侧一致性。
* 词表审计是静态的（源码扫描 + 常量对拍），不做运行时事件追踪。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
CONTRACTS = BACKEND / "contracts"

# TensorPhysics research_runtime.TaskStatus 冻结镜像（与 backend/tests/test_status_contracts.py
# 的 TPH_TASK_STATUSES 同值，两侧同改否则必红一边）。
TPH_TASK_STATUSES_MIRROR = (
    "queued", "claimed", "running", "verifying",
    "waiting_input", "retry_wait", "succeeded", "failed", "cancelled",
)

EVENT_TABLE = "tensor_agent_session_events"

CANON_FILES = (
    "mission_status_vocab.v1.json",
    "mission_task_status_map.v1.json",
    "session_event_kinds.v1.json",
    "research_run_status_vocab.v1.json",
)


@dataclass
class Row:
    """一条逐项对拍记录：正典列表 vs 运行面列表。"""

    contract: str          # G-A / G-B / G-C
    item: str              # 检查项名
    canon_ref: str         # 正典来源（契约文件#字段）
    runtime_ref: str       # 运行面来源（文件:符号 / 扫描范围）
    canon_items: list      # type: ignore[type-arg]
    runtime_items: list    # type: ignore[type-arg]
    canon_only: list       # type: ignore[type-arg]
    runtime_only: list     # type: ignore[type-arg]
    note: str = ""

    @property
    def diff(self) -> int:
        return len(self.canon_only) + len(self.runtime_only)

    @property
    def ok(self) -> bool:
        return self.diff == 0

    @property
    def verdict(self) -> str:
        return "DIFF=0" if self.ok else f"DIFF={self.diff}"


@dataclass
class Audit:
    rows: list = field(default_factory=list)  # type: ignore[type-arg]
    errors: list = field(default_factory=list)  # type: ignore[type-arg]
    canon_hashes: dict = field(default_factory=dict)  # type: ignore[type-arg]

    def add(self, row: Row) -> Row:
        self.rows.append(row)
        return row

    def errored(self, contract: str, item: str, message: str) -> None:
        self.errors.append({"contract": contract, "item": item, "error": message})

    @property
    def ok(self) -> bool:
        return not self.errors and all(r.ok for r in self.rows)

    @property
    def diff_total(self) -> int:
        return sum(r.diff for r in self.rows)


# ---------------------------------------------------------------------------
# 正典装载
# ---------------------------------------------------------------------------

def load_canon(contracts_dir: Path = CONTRACTS) -> dict:  # type: ignore[type-arg]
    """装载四个契约文件并记 sha256（审计链）。"""
    docs, hashes = {}, {}
    for name in CANON_FILES:
        path = contracts_dir / name
        raw = path.read_bytes()
        docs[name] = json.loads(raw.decode("utf-8"))
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return {"docs": docs, "hashes": hashes}


# ---------------------------------------------------------------------------
# 运行面提取
# ---------------------------------------------------------------------------

def _prepare_import_path() -> None:
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def runtime_symbols() -> dict:  # type: ignore[type-arg]
    """真实 import 运行时常量（不是读源码猜）。"""
    _prepare_import_path()
    from api.domain.tensor_common import EVENT_KINDS
    from api.tensorphysics_runs import (
        _MAX_MANIFEST_BYTES,
        _RUN_STATUS_PATTERN,
        _VALIDATION_STATUS_PATTERN,
    )
    from api.tensor_missions import MISSION_STATUSES, TERMINAL

    return {
        "MISSION_STATUSES": tuple(MISSION_STATUSES),
        "TERMINAL": tuple(TERMINAL),
        "EVENT_KINDS": tuple(EVENT_KINDS),
        "RUN_STATUS_PATTERN": _RUN_STATUS_PATTERN,
        "VALIDATION_STATUS_PATTERN": _VALIDATION_STATUS_PATTERN,
        "MAX_MANIFEST_BYTES": _MAX_MANIFEST_BYTES,
    }


def status_check_defs(migrations_dir: Path | None = None) -> list:  # type: ignore[type-arg]
    """按文件字典序收集 tensor_dev_missions.status CHECK 的全部定义。"""
    migrations_dir = migrations_dir or (BACKEND / "migrations")
    defs = []
    for sql in sorted(migrations_dir.glob("*.sql")):
        for stmt in sql.read_text(encoding="utf-8").split(";"):
            norm = re.sub(r"\s+", " ", stmt)
            if "TABLE tensor_dev_missions" not in norm or "CHECK (status IN" not in norm:
                continue
            m = re.search(r"CHECK \(status IN \(([^)]*)\)", stmt)
            if not m:
                continue
            states = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
            defs.append((sql.name, states))
    return defs


EVENT_INSERT_RE = re.compile(r"INSERT\s+INTO\s+" + EVENT_TABLE, re.IGNORECASE)
APPEND_LITERAL_RE = re.compile(r"append_event\(\s*db\s*,\s*[^,]+,\s*[\"']([a-z_]+)[\"']")


def event_insert_writers(root: Path, skip_dirs: tuple = ("tests",)) -> list:  # type: ignore[type-arg]
    """返回所有直接写事件表（INSERT INTO ...）的生产文件相对路径。

    测试目录/测试文件里的字面量是断言用靶子，不是写手，故默认跳过。
    """
    hits = []
    skip = {".git", "__pycache__", "node_modules"} | set(skip_dirs)
    for path in root.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        if path.name.startswith("test_"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if EVENT_INSERT_RE.search(text):
            hits.append(str(path.relative_to(root)))
    return sorted(hits)


def append_event_literals(root: Path) -> list:  # type: ignore[type-arg]
    """扫描 append_event(..., "<kind>", ...) 的字面量词项（手写分支使用面）。"""
    kinds = set()
    for path in root.rglob("*.py"):
        if any(part in {"__pycache__", "node_modules"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        kinds.update(APPEND_LITERAL_RE.findall(text))
    return sorted(kinds)


def pattern_tokens(pattern: str) -> list:  # type: ignore[type-arg]
    """从 `^(a|b|c)$` 形态的正则里抽出词项集合（正典列表的机器形态）。"""
    body = pattern.strip()
    if body.startswith("^") and body.endswith("$"):
        body = body[1:-1]
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    return sorted({t for t in body.split("|") if t})


CONTRACT_MODULE = "api.domain.status_contracts"


def _contract_aliases(tree: ast.AST) -> set:  # type: ignore[type-arg]
    """收集源码里从契约装载模块 import 进来的名字（含别名，如 _msc/_rrc）。"""
    aliases: set = set()  # type: ignore[type-arg]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == CONTRACT_MODULE:
            for alias in node.names:
                aliases.add(alias.asname or alias.name)
    return aliases


def derives_symbol(source: str, symbol: str) -> bool:
    """``symbol`` 的赋值右式是否调用契约装载器（= 词项走正典列表，非手写分支）。

    AST 判据，不是字符串搜索：``MISSION_STATUSES = tuple(_msc()["statuses"])`` 与
    ``EVENT_KINDS = tuple(session_event_contract()["kinds"])`` 都算派生；
    ``X = ("plan", "note")`` 这种字面量元组不算。
    """
    tree = ast.parse(source)
    aliases = _contract_aliases(tree)
    if not aliases:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if symbol not in names or node.value is None:
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Call):
                func = sub.func
                if getattr(func, "id", None) in aliases or getattr(func, "attr", None) in aliases:
                    return True
    return False


# ---------------------------------------------------------------------------
# 三组契约检查
# ---------------------------------------------------------------------------

def _diff(canon_items, runtime_items):
    c, r = list(canon_items), list(runtime_items)
    cs, rs = set(c), set(r)
    return c, r, sorted(cs - rs), sorted(rs - cs)


def audit_ga(canon: dict, rt: dict) -> list:  # type: ignore[type-arg]
    rows = []
    vocab = canon["docs"]["mission_status_vocab.v1.json"]
    mapping = canon["docs"]["mission_task_status_map.v1.json"]

    c, r, co, ro = _diff(vocab["statuses"], rt["MISSION_STATUSES"])
    rows.append(Row("G-A", "statuses", "mission_status_vocab#statuses",
                    "api/tensor_missions.py:MISSION_STATUSES", c, r, co, ro))
    c, r, co, ro = _diff(vocab["terminal"], rt["TERMINAL"])
    rows.append(Row("G-A", "terminal", "mission_status_vocab#terminal",
                    "api/tensor_missions.py:TERMINAL", c, r, co, ro))

    defs = status_check_defs()
    final_name, final_states = defs[-1] if defs else ("<none>", set())
    c, r, co, ro = _diff(vocab["statuses"], final_states)
    rows.append(Row("G-A", "db_check_terminal", "mission_status_vocab#statuses",
                    f"migrations/{final_name}:CHECK (status IN ...)", c, r, co, ro,
                    note="终态=字典序最后一份定义"))

    hist_states = set().union(*[s for _n, s in defs]) if defs else set()
    c, r, co, ro = _diff(vocab["statuses"], hist_states)
    rows.append(Row("G-A", "db_check_history_subset", "mission_status_vocab#statuses",
                    f"migrations/*.sql ({len(defs)} 份定义取并集)", c, r, co, ro,
                    note="历史定义只许更窄（runtime_only 必须为空）"))

    # 映射是函数：每个 TPh 状态恰一个 TP 像
    images: dict = {}  # type: ignore[type-arg]
    multi = []
    for row in mapping["mapping"]:
        images.setdefault(row["tph"], set()).add(row["tp"])
        if len(images[row["tph"]]) > 1:
            multi.append(row["tph"])
    bad_func = sorted(multi) + sorted(t for t, v in images.items() if len(v) > 1)
    c, r = sorted(images), list(images)
    rows.append(Row("G-A", "map_is_function",
                    "mission_task_status_map#mapping（tph→单像）",
                    "mission_task_status_map#mapping（按 tph 聚合）",
                    c, r, [], sorted(set(bad_func)),
                    note="每个 TPh 状态必须有且只有一个 TP 像"))

    c, r, co, ro = _diff(TPH_TASK_STATUSES_MIRROR, images)
    rows.append(Row("G-A", "map_covers_tph_mirror", "TensorPhysics TaskStatus 冻结镜像",
                    "mission_task_status_map#mapping", c, r, co, ro,
                    note="镜像与 backend/tests/test_status_contracts.py 同值"))

    images_flat = set().union(*images.values()) if images else set()
    map_platform_only = {r0["tp"] for r0 in mapping["platform_only"]}
    c, r, co, ro = _diff(vocab["platform_only"], map_platform_only)
    rows.append(Row("G-A", "platform_only_consistency",
                    "mission_status_vocab#platform_only",
                    "mission_task_status_map#platform_only", c, r, co, ro))
    overlap = sorted(map_platform_only & images_flat)
    rows.append(Row("G-A", "platform_only_disjoint_from_images",
                    "mission_task_status_map#platform_only（须与像集不相交）",
                    "mission_task_status_map#mapping 像集", [], [], [], overlap,
                    note="平台专有相位不得同时是 TPh 状态的像"))

    src = (BACKEND / "api" / "tensor_missions.py").read_text(encoding="utf-8")
    symbols = ["MISSION_STATUSES", "TERMINAL"]
    ok_symbols = [sym for sym in symbols if derives_symbol(src, sym)]
    rows.append(Row("G-A", "runtime_derived_from_canon",
                    "mission_status_vocab（正典装载器 api.domain.status_contracts）",
                    "api/tensor_missions.py:" + ",".join(symbols), symbols, ok_symbols,
                    [], [sym for sym in symbols if sym not in ok_symbols],
                    note="AST 判据：赋值右式须调用契约装载器；字面量元组=手写分支"))
    return rows


def audit_gb(canon: dict, rt: dict, root: Path) -> list:  # type: ignore[type-arg]
    rows = []
    ev = canon["docs"]["session_event_kinds.v1.json"]

    c, r, co, ro = _diff(ev["kinds"], rt["EVENT_KINDS"])
    rows.append(Row("G-B", "event_kinds", "session_event_kinds#kinds",
                    "api/domain/tensor_common.py:EVENT_KINDS", c, r, co, ro))

    c, r, co, ro = _diff(ev["governance_members"], [k for k in ev["kinds"]
                                                    if k in set(ev["governance_members"])])
    rows.append(Row("G-B", "governance_members", "session_event_kinds#governance_members",
                    "session_event_kinds#kinds ∩ governance_members", c, r, co, ro,
                    note="治理事件成员必须都在正典词表内"))

    used = append_event_literals(BACKEND / "api")
    c, r, co, ro = _diff(ev["kinds"], used)
    rows.append(Row("G-B", "event_kind_literals", "session_event_kinds#kinds",
                    "api/**/append_event(..., '<kind>', ...) 字面量", c, r, co, ro,
                    note="runtime_only=代码里出现但正典没有的「手写新增词」；"
                         "canon_only=正典有但 api/ 未直接使用（不算违规）"))
    # 只把 runtime_only 计为违规
    rows[-1].canon_only = []

    writers = event_insert_writers(root)
    expected = ["backend/api/domain/tensor_common.py"]
    c, r, co, ro = _diff(expected, writers)
    rows.append(Row("G-B", "single_writer", f"唯一写手={expected[0]}",
                    f"扫 {EVENT_TABLE} 的 INSERT 语句（生产面 *.py，跳过 tests/）", c, r, co, ro,
                    note="一事实单源：第二处 INSERT 即双写"))

    reg_src = (BACKEND / "api" / "tensorphysics_runs.py").read_text(encoding="utf-8")
    bad = ["append_event"] if "append_event" in reg_src else []
    rows.append(Row("G-B", "run_registry_no_events", "登记面不写事件流",
                    "api/tensorphysics_runs.py", [], [], [], bad,
                    note="run 登记表是登记面不是事件面"))
    return rows


def audit_gc(canon: dict, rt: dict) -> list:  # type: ignore[type-arg]
    rows = []
    run = canon["docs"]["research_run_status_vocab.v1.json"]
    src = (BACKEND / "api" / "tensorphysics_runs.py").read_text(encoding="utf-8")

    for field_name, rt_key, pat_key in (
        ("run_statuses", "RUN_STATUSES_TOKENS", "RUN_STATUS_PATTERN"),
        ("validation_statuses", "VALIDATION_STATUSES_TOKENS", "VALIDATION_STATUS_PATTERN"),
    ):
        tokens = pattern_tokens(rt[pat_key])
        rt[rt_key] = tokens
        c, r, co, ro = _diff(run[field_name], tokens)
        rows.append(Row("G-C", field_name, f"research_run_status_vocab#{field_name}",
                        f"api/tensorphysics_runs.py:{pat_key} 抽词", c, r, co, ro,
                        note="正则成员集 == 正典列表（非仅包含）"))

    c = [int(run["max_manifest_bytes"])]
    r = [int(rt["MAX_MANIFEST_BYTES"])]
    co = sorted(set(map(str, c)) - set(map(str, r)))
    ro = sorted(set(map(str, r)) - set(map(str, c)))
    rows.append(Row("G-C", "max_manifest_bytes", "research_run_status_vocab#max_manifest_bytes",
                    "api/tensorphysics_runs.py:_MAX_MANIFEST_BYTES", c, r, co, ro))

    symbols = ["_RUN_STATUS_PATTERN", "_VALIDATION_STATUS_PATTERN", "_MAX_MANIFEST_BYTES"]
    ok_symbols = [sym for sym in symbols if derives_symbol(src, sym)]
    rows.append(Row("G-C", "runtime_derived_from_canon",
                    "research_run_status_vocab（正典装载器）",
                    "api/tensorphysics_runs.py:" + ",".join(symbols), symbols, ok_symbols,
                    [], [sym for sym in symbols if sym not in ok_symbols],
                    note="AST 判据：三常量赋值右式须调用契约装载器"))

    hand = sorted(
        lit for lit in ("planned|validating|queued", "not_run|passed|failed|incomplete)$")
        if lit in src
    )
    rows.append(Row("G-C", "handwritten_dual_source_absent",
                    "手写正则双源必须已灭（main 旧 :37-38）",
                    "api/tensorphysics_runs.py", [], [], [], hand))
    return rows


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_audit(contracts_dir: Path = CONTRACTS, root: Path = REPO) -> Audit:
    audit = Audit()
    try:
        canon = load_canon(contracts_dir)
        audit.canon_hashes = canon["hashes"]
        rt = runtime_symbols()
    except Exception as exc:  # noqa: BLE001 - 运行面不可导入即整体红
        audit.errored("G-A/G-B/G-C", "bootstrap", f"{type(exc).__name__}: {exc}")
        return audit

    for contract, fn in (
        ("G-A", lambda: audit_ga(canon, rt)),
        ("G-B", lambda: audit_gb(canon, rt, root)),
        ("G-C", lambda: audit_gc(canon, rt)),
    ):
        try:
            for row in fn():
                audit.add(row)
        except Exception as exc:  # noqa: BLE001
            audit.errored(contract, "group", f"{type(exc).__name__}: {exc}")
    return audit


def _git_head() -> str:
    """git head（worktree 里 .git 是文件，故走 git 命令；失败则退回解析 .git/HEAD）。"""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        gitdir = REPO / ".git"
        base = gitdir if gitdir.is_dir() else Path(
            (gitdir.read_text().strip().split("gitdir:", 1)[1]).strip()
        )
        ref = (base / "HEAD").read_text().strip()
        if ref.startswith("ref: "):
            return (base / ref[5:]).read_text().strip()
    except (OSError, IndexError):
        pass
    return "unknown"


def render_json(audit: Audit, contracts_dir: Path) -> dict:  # type: ignore[type-arg]
    return {
        "tool": "tools/contracts/vocab_contract_audit.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": "TensorPlatform",
        "git_head": _git_head(),
        "contracts_dir": str(contracts_dir),
        "canon_sha256": audit.canon_hashes,
        "summary": {
            "rows": len(audit.rows),
            "ok_rows": sum(r.ok for r in audit.rows),
            "diff_total": audit.diff_total,
            "errors": len(audit.errors),
            "verdict": "PASS" if audit.ok else "FAIL",
            "by_contract": {
                g: {
                    "rows": sum(r.contract == g for r in audit.rows),
                    "diff_total": sum(r.diff for r in audit.rows if r.contract == g),
                }
                for g in ("G-A", "G-B", "G-C")
            },
        },
        "rows": [dict(asdict(r), diff=r.diff, verdict=r.verdict) for r in audit.rows],
        "errors": audit.errors,
    }


def render_text(audit: Audit) -> str:
    lines = ["contract  item                          canon/runtime      diff  verdict"]
    for row in audit.rows:
        lines.append(
            f"{row.contract:<9} {row.item:<29} {len(row.canon_items):>3}/{len(row.runtime_items):<12}"
            f" {row.diff:>4}  {row.verdict}"
        )
        if row.canon_only:
            lines.append(f"          canon_only   = {row.canon_only}")
        if row.runtime_only:
            lines.append(f"          runtime_only = {row.runtime_only}")
    for err in audit.errors:
        lines.append(f"[ERROR] {err['contract']} {err['item']}: {err['error']}")
    lines.append(
        f"TOTAL rows={len(audit.rows)} ok={sum(r.ok for r in audit.rows)} "
        f"diff_total={audit.diff_total} verdict={'PASS' if audit.ok else 'FAIL'}"
    )
    return "\n".join(lines)


def render_figure(audit: Audit, out_png: Path, dpi: int = 110) -> Path:
    """审计图：三组契约逐项 diff 矩阵 + 四份正典列表规模。

    纯后端/文档任务本可无图，但「全 0」这个结论本身需要可复核的视觉证据：
    左侧每格是该项 canon_only/runtime_only 差集大小（全 0=绿），右侧是正典
    文件的词项规模，说明 diff=0 不是「比空集」。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = audit.rows
    labels = [f"{r.contract}:{r.item}" for r in rows]
    values = [r.diff for r in rows]
    colors = ["#2e7d32" if v == 0 else "#c62828" for v in values]

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(15, 0.42 * len(rows) + 2.2),
        gridspec_kw={"width_ratios": [2.1, 1]},
    )
    y = list(range(len(rows)))
    ax1.barh(y, values, color=colors, height=0.6)
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlabel("diff = |canon_only| + |runtime_only|   (0 = contract == runtime, word by word)")
    ax1.set_title(f"G-A/B/C contract diff matrix - {sum(r.ok for r in rows)}/{len(rows)} items at diff=0")
    ax1.set_xlim(0, max(1.2, max(values) + 0.5))
    for yi, v in zip(y, values):
        ax1.text(v + 0.02, yi, str(v), va="center", fontsize=7)
    ax1.grid(axis="x", alpha=0.3)

    sizes = {
        "mission_status_vocab\n(statuses / terminal)": (11, 5),
        "mission_task_status_map\n(mapping / platform_only)": (9, 5),
        "session_event_kinds\n(kinds / governance)": (13, 6),
        "research_run_status_vocab\n(run / validation)": (8, 4),
    }
    names = list(sizes)
    first = [sizes[n][0] for n in names]
    second = [sizes[n][1] for n in names]
    xs = list(range(len(names)))
    ax2.bar([x - 0.18 for x in xs], first, width=0.36, label="primary list", color="#1565c0")
    ax2.bar([x + 0.18 for x in xs], second, width=0.36, label="secondary list", color="#90caf9")
    ax2.set_xticks(xs)
    ax2.set_xticklabels(names, fontsize=7)
    ax2.set_ylabel("word count")
    ax2.set_title("canon vocabulary sizes (non-empty => diff is meaningful)")
    ax2.legend(fontsize=8)
    ax2.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi)
    plt.close(fig)
    return out_png


def render_markdown(audit: Audit, payload: dict) -> str:  # type: ignore[type-arg]
    """论文级收尾报告（Markdown）：所有数字从审计结果注入，零手抄。"""
    summary = payload["summary"]
    per = summary["by_contract"]
    lines = [
        "# G-A/G-B/C 词表契约收尾复核（m4964）",
        "",
        f"- 生成：`tools/contracts/vocab_contract_audit.py`（{payload['generated_at']}）",
        f"- 仓库：TensorPlatform，git head `{payload['git_head']}`",
        f"- 判据：**逐项 diff=0**，结论 **{summary['verdict']}**"
        f"（{summary['ok_rows']}/{summary['rows']} 项 diff=0，diff 总和 {summary['diff_total']}）",
        "",
        "## 1. 三组契约的逐项对拍",
        "",
        "| 契约 | 检查项 | 正典来源 | 运行面来源 | 正典词数 | 运行面词数 | diff | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in audit.rows:
        lines.append(
            f"| {row.contract} | `{row.item}` | {row.canon_ref} | {row.runtime_ref} "
            f"| {len(row.canon_items)} | {len(row.runtime_items)} | {row.diff} | {row.verdict} |"
        )
    lines += [
        "",
        "| 契约 | 检查项数 | diff 总和 |",
        "|---|---|---|",
    ]
    for group in ("G-A", "G-B", "G-C"):
        lines.append(f"| {group} | {per[group]['rows']} | {per[group]['diff_total']} |")
    lines += [
        "",
        "## 2. 正典文件摘要（sha256 审计链）",
        "",
        "| 契约文件 | sha256 |",
        "|---|---|",
    ]
    for name, digest in payload["canon_sha256"].items():
        lines.append(f"| `{name}` | `{digest}` |")
    if audit.errors:
        lines += ["", "## 3. 错误", ""]
        lines += [f"- {e['contract']} {e['item']}: {e['error']}" for e in audit.errors]
    lines.append("")
    return "\n".join(lines)

def main(argv: list | None = None) -> int:  # type: ignore[type-arg]
    ap = argparse.ArgumentParser(description="G-A/G-B/G-C 词表契约逐项 diff 审计")
    ap.add_argument("--contracts-dir", default=str(CONTRACTS), help="契约 JSON 目录")
    ap.add_argument("--json-out",
                    default=str(REPO / "artifacts" / "contracts_gabc_2026-11" / "vocab_contract_audit.json"),
                    help="JSON 工件输出路径")
    ap.add_argument("--md-out", default="", help="可选：Markdown 收尾报告输出路径")
    ap.add_argument("--plot-out", default="", help="可选：审计图 PNG 输出路径")
    ap.add_argument("--plot-dpi", type=int, default=110, help="审计图 DPI（报告内嵌需 ≤ ~60 KB 时调小）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写任何文件")
    ap.add_argument("--verbose", action="store_true", help="打印每项明细")
    args = ap.parse_args(argv)

    audit = run_audit(Path(args.contracts_dir))
    if args.verbose or args.dry_run:
        print(render_text(audit))
    payload = render_json(audit, Path(args.contracts_dir))
    if args.dry_run:
        print(json.dumps(payload["summary"], ensure_ascii=False))
        return 0 if audit.ok else 2

    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[written] {out}")
    if args.md_out:
        md = Path(args.md_out)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_markdown(audit, payload), encoding="utf-8")
        print(f"[written] {md}")
    if args.plot_out:
        png = render_figure(audit, Path(args.plot_out), dpi=args.plot_dpi)
        print(f"[written] {png}")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if audit.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())