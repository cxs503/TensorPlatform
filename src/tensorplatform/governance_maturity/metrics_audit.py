"""度量工具（pulse / chronicle / portrait）与三契约源的**联合一致性审计**。

判据（节点 p465w1-N07 验收项 (a)，逐项 diff 可算）
--------------------------------------------------
M1 ``declared_tools_exist``      声明里的工具目录必须真实存在（声明不能空转）
M2 ``event_kinds_in_contract``   工具声明触及的会话事件种类 ⊆ 事件流契约 ``kinds``
M3 ``event_kinds_scan_parity``   源码扫出的「契约命名空间字面量」== 声明（双向）
M4 ``internal_kinds_parity``     工具自有 kind 命名空间（报表 token / 图元）== 声明（双向）
M5 ``payload_subtypes_parity``   源码扫出的 payload subtype 字面量 == 声明（双向）
M6 ``table_surface_parity``      源码扫出的表名字面量 == 声明（双向）
M7 ``pulse_runtime_payload``     运行时真跑 ``pulse.compute`` 得到的 payload/row 键 == 声明
M8 ``governance_membership``     标注为治理消费者的工具，其事件种类 ⊆ 契约 ``governance_members``

为什么要"扫源码"而不是只读声明
------------------------------
声明文件是人写的，会和代码漂移。本审计把声明与**源码字面量**对拍：
有人在工具里新写一个 ``kind='…'`` 就会让 M3/M4/M5 变红，直到声明与契约同步。
这把"度量工具事件类型与契约词表完全匹配"从口头约定变成结构性事实。

零领域语义：本模块只认识契约名/文件名/正则，不认识任何学科量。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import contracts as C

# 源码扫描用的正则（规则集中在此，别处不得再写一套）
_KIND_RE = re.compile(r"""kind\s*(?:=|==|:)\s*["']([A-Za-z0-9_]+)["']""")
_SUBTYPE_RE = re.compile(r"""subtype'?\s*(?:=|:)\s*["']([A-Za-z0-9_]+)["']""")
_TABLE_RE = re.compile(r"""\b(tensor_[a-z_]+|saturation_pulse)\b""")

PULSE_MODULE = "tools.pulse.saturation_pulse"
PORTRAIT_MODULE = "tools.worker_portrait.portrait"


class MetricsAuditError(RuntimeError):
    """声明文件结构非法（fail-closed）。"""


@dataclass
class AuditItem:
    """一条可算的审计项。``diff`` 为对称差集大小，0 才算过。"""

    id: str
    description: str
    diff: int
    detail: Dict[str, Any] = field(default_factory=dict)
    status: str = ""

    def __post_init__(self) -> None:
        # 状态由 diff 推导，避免"手写 PASS 掩盖非 0 diff"
        if not self.status:
            self.status = "PASS" if self.diff == 0 else "FAIL"

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "description": self.description, "diff": self.diff,
                "status": self.status, "detail": self.detail}


@dataclass
class MetricsAuditResult:
    items: List[AuditItem]
    scanned: Dict[str, Dict[str, Any]]
    contract_versions: Dict[str, Optional[int]]

    @property
    def total_diff(self) -> int:
        return sum(i.diff for i in self.items)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "items": [i.as_dict() for i in self.items],
            "total_diff": self.total_diff,
            "contract_versions": self.contract_versions,
            "scanned": self.scanned,
            "status": "PASS" if self.total_diff == 0 else "FAIL",
        }


# --------------------------------------------------------------------------- #
# 源码扫描
# --------------------------------------------------------------------------- #
def scan_tool_source(root: Path, tool_root: str,
                     contract_kinds: Sequence[str]) -> Dict[str, Any]:
    """扫描一个度量工具目录下的 ``*.py``。

    返回 ``kind_literals``（全部 kind 字面量）、``event_kinds``（落在契约命名空间
    里的那些）、``other_kinds``（工具自有命名空间）、``subtypes``、``tables``。
    """
    base = root / tool_root
    if not base.is_dir():
        raise MetricsAuditError(f"工具目录不存在: {tool_root}")
    kinds: Set[str] = set()
    subtypes: Set[str] = set()
    tables: Set[str] = set()
    files = sorted(base.rglob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        kinds.update(_KIND_RE.findall(text))
        subtypes.update(_SUBTYPE_RE.findall(text))
        tables.update(_TABLE_RE.findall(text))
    contract_set = set(contract_kinds)
    return {
        "tool_root": tool_root,
        "files_scanned": len(files),
        "kind_literals": sorted(kinds),
        "event_kinds": sorted(kinds & contract_set),
        "other_kinds": sorted(kinds - contract_set),
        "subtypes": sorted(subtypes),
        "tables": sorted(tables),
    }


def pulse_runtime_surface(root: Optional[Path] = None) -> Dict[str, Any]:
    """真跑一次 ``pulse.compute``（纯函数，无 DB），取运行时的 payload/row 键。"""
    import importlib
    import sys
    from datetime import datetime, timedelta, timezone

    root = Path(root) if root else C.repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module = importlib.import_module(PULSE_MODULE)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    workers = tuple(
        {"worker_id": f"agent-{i}", "enabled": True, "state": "running" if i % 2 else "idle",
         "heartbeat_at": now - timedelta(seconds=5 * i), "current_mission_id": None}
        for i in range(4)
    )
    missions = tuple(
        {"id": 1000 + i, "status": "running", "claimed_by": f"agent-{i}",
         "heartbeat_at": now - timedelta(seconds=3 * i)}
        for i in range(2)
    )
    nodes = ({"table": "campaign_nodes", "id": "n1", "status": "held"},)
    snapshot = module.compute(module.RawState(workers=workers, missions=missions, nodes=nodes),
                              now=now, window_s=300)
    return {
        "available": True,
        "collector": getattr(module, "COLLECTOR_ID", None),
        "payload_keys": sorted(snapshot.note_payload().keys()),
        "row_keys": sorted(snapshot.row().keys()),
        "online": snapshot.online,
        "fresh_workers": snapshot.fresh_workers,
    }


def portrait_runtime_surface(root: Optional[Path] = None) -> Dict[str, Any]:
    """真跑一次 ``portrait.build_profile``（纯函数），取运行时画像键。"""
    import importlib
    import sys
    from datetime import datetime, timedelta, timezone

    root = Path(root) if root else C.repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module = importlib.import_module(PORTRAIT_MODULE)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(3):
        created = t0 + timedelta(minutes=10 * i)
        rows.append({
            "mission_id": 500 + i, "worker": "agent-7", "status": "succeeded",
            "repo": "repo-x", "created_at": created, "claim_at": created + timedelta(seconds=5),
            "finished_at": created + timedelta(seconds=65), "model": "m", "runner": "r",
            "worker_role": "w", "campaign_id": "c",
        })
    prof = module.build_profile("agent-7", [module.normalise_row(r) for r in rows])
    return {"available": True, "profile_keys": sorted(prof.keys()),
            "n_missions": prof.get("n_missions")}


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #
def _sym_diff(a: Iterable[str], b: Iterable[str]) -> List[str]:
    sa, sb = set(a), set(b)
    return sorted(sa ^ sb)


def audit_metrics_contract(root: Optional[Path] = None,
                           *, runtime: bool = True) -> MetricsAuditResult:
    """执行 M1–M8 全部判据。"""
    root = Path(root) if root else C.repo_root()
    vocabs = C.load_vocab_contracts(root)
    emission = C.load_metrics_emission(root)
    event_contract = vocabs["session_event_kinds"].data
    contract_kinds = list(event_contract.get("kinds") or [])
    governance_members = set(event_contract.get("governance_members") or [])
    if not contract_kinds:
        raise MetricsAuditError("事件流契约缺 kinds")

    tools_decl: Dict[str, Dict[str, Any]] = emission.data["tools"]
    items: List[AuditItem] = []
    scanned: Dict[str, Dict[str, Any]] = {}

    # M1 工具目录存在
    missing_dirs = [name for name, decl in tools_decl.items()
                    if not (root / str(decl.get("root", ""))).is_dir()]
    items.append(AuditItem("M1_declared_tools_exist",
                           "声明中的度量工具目录必须存在", len(missing_dirs),
                           {"missing": missing_dirs}))

    for name, decl in tools_decl.items():
        if name in missing_dirs:
            continue
        scanned[name] = scan_tool_source(root, str(decl["root"]), contract_kinds)

    # M2 声明事件种类 ⊆ 契约 kinds
    declared_kinds = sorted({k for decl in tools_decl.values()
                             for k in (decl.get("event_kinds") or [])})
    non_contract = [k for k in declared_kinds if k not in contract_kinds]
    items.append(AuditItem("M2_event_kinds_in_contract",
                           "工具声明触及的会话事件种类必须全部在契约词表内",
                           len(non_contract), {"declared": declared_kinds,
                                               "non_contract": non_contract,
                                               "contract_kinds": contract_kinds}))

    # M3 扫描出的契约命名空间字面量 == 声明（逐工具）
    m3_detail: Dict[str, Any] = {}
    m3_diff = 0
    for name, scan in scanned.items():
        declared = sorted(set(tools_decl[name].get("event_kinds") or []))
        d = _sym_diff(scan["event_kinds"], declared)
        if d:
            m3_diff += len(d)
        m3_detail[name] = {"scanned": scan["event_kinds"], "declared": declared, "diff": d}
    items.append(AuditItem("M3_event_kinds_scan_parity",
                           "源码扫出的契约命名空间 kind 字面量必须与声明一致（双向）",
                           m3_diff, m3_detail))

    # M4 工具自有命名空间 == 声明（双向）
    m4_detail: Dict[str, Any] = {}
    m4_diff = 0
    for name, scan in scanned.items():
        declared = sorted(set(tools_decl[name].get("internal_kinds") or []))
        d = _sym_diff(scan["other_kinds"], declared)
        if d:
            m4_diff += len(d)
        m4_detail[name] = {"scanned": scan["other_kinds"], "declared": declared, "diff": d}
    items.append(AuditItem("M4_internal_kinds_parity",
                           "工具自有 kind 命名空间必须与声明一致（双向）", m4_diff, m4_detail))

    # M5 payload subtype == 声明
    m5_detail: Dict[str, Any] = {}
    m5_diff = 0
    for name, scan in scanned.items():
        declared = sorted(set(tools_decl[name].get("payload_subtypes") or []))
        d = _sym_diff(scan["subtypes"], declared)
        if d:
            m5_diff += len(d)
        m5_detail[name] = {"scanned": scan["subtypes"], "declared": declared, "diff": d}
    items.append(AuditItem("M5_payload_subtypes_parity",
                           "payload subtype 字面量必须与声明一致（双向）", m5_diff, m5_detail))

    # M6 表名面 == 声明
    m6_detail: Dict[str, Any] = {}
    m6_diff = 0
    for name, scan in scanned.items():
        declared = sorted(set(tools_decl[name].get("tables") or []))
        d = _sym_diff(scan["tables"], declared)
        if d:
            m6_diff += len(d)
        m6_detail[name] = {"scanned": scan["tables"], "declared": declared, "diff": d}
    items.append(AuditItem("M6_table_surface_parity",
                           "源码触及的表名字面量必须与声明一致（双向）", m6_diff, m6_detail))

    # M7 运行时 payload 键（真跑工具）
    if runtime:
        pulse_decl = tools_decl.get("pulse", {})
        try:
            surface = pulse_runtime_surface(root)
            want_payload = sorted(set(pulse_decl.get("runtime_payload_keys") or []))
            want_row = sorted(set(pulse_decl.get("runtime_row_keys") or []))
            d_payload = _sym_diff(surface["payload_keys"], want_payload)
            d_row = _sym_diff(surface["row_keys"], want_row)
            items.append(AuditItem(
                "M7_pulse_runtime_payload",
                "运行时 pulse.compute 的 payload/row 键必须与声明一致（双向）",
                len(d_payload) + len(d_row),
                {"payload_diff": d_payload, "row_diff": d_row,
                 "online": surface["online"], "fresh_workers": surface["fresh_workers"]}))
        except Exception as exc:  # 工具跑不起来 → 显式红，不静默跳过
            items.append(AuditItem("M7_pulse_runtime_payload",
                                   "运行时 pulse.compute 的 payload/row 键必须与声明一致",
                                   1, {"error": f"{type(exc).__name__}: {exc}"}, status="ERROR"))
    else:
        items.append(AuditItem("M7_pulse_runtime_payload", "运行时 payload 键检查（已跳过）",
                               0, {"skipped": True}))

    # M8 治理消费者的事件种类 ⊆ governance_members
    m8_detail: Dict[str, Any] = {}
    m8_diff = 0
    for name, decl in tools_decl.items():
        if not decl.get("governance_consumer"):
            continue
        ks = set(decl.get("event_kinds") or [])
        extra = sorted(ks - governance_members)
        if extra:
            m8_diff += len(extra)
        m8_detail[name] = {"event_kinds": sorted(ks), "outside_governance": extra}
    items.append(AuditItem("M8_governance_membership",
                           "治理消费者工具的事件种类必须是契约 governance_members 的子集",
                           m8_diff, m8_detail))

    return MetricsAuditResult(
        items=items,
        scanned=scanned,
        contract_versions={
            "session_event_kinds": vocabs["session_event_kinds"].version,
            "mission_status_vocab": vocabs["mission_status_vocab"].version,
            "research_run_status_vocab": vocabs["research_run_status_vocab"].version,
            "metrics_emission": emission.version,
        },
    )


def bootstrap_declaration(root: Optional[Path] = None,
                          tool_roots: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """从当前源码**生成**声明草稿（仅在新增工具/初建契约时用，人工审后提交）。

    这不是"审计通过"的来源：生成的声明只是把现状记录下来，随后
    :func:`audit_metrics_contract` 才负责防止未来漂移。
    """
    root = Path(root) if root else C.repo_root()
    vocabs = C.load_vocab_contracts(root)
    contract_kinds = list(vocabs["session_event_kinds"].data.get("kinds") or [])
    tool_roots = tool_roots or {"pulse": "tools/pulse",
                                "chronicle": "tools/chronicle",
                                "portrait": "tools/worker_portrait"}
    tools: Dict[str, Any] = {}
    for name, rel in tool_roots.items():
        scan = scan_tool_source(root, rel, contract_kinds)
        decl: Dict[str, Any] = {
            "root": rel,
            "event_kinds": scan["event_kinds"],
            "payload_subtypes": scan["subtypes"],
            "internal_kinds": scan["other_kinds"],
            "tables": scan["tables"],
            "governance_consumer": bool(scan["event_kinds"]),
        }
        if name == "pulse":
            surface = pulse_runtime_surface(root)
            decl["runtime_payload_keys"] = surface["payload_keys"]
            decl["runtime_row_keys"] = surface["row_keys"]
        tools[name] = decl
    return {
        "contract": "metrics_emission",
        "version": 1,
        "owner_repo": "TensorPlatform",
        "consumer_repos": [],
        "description": (
            "度量工具（pulse/chronicle/portrait）的会话事件面与工具自有 kind 命名空间声明。"
            "由 metrics_audit 与 backend/contracts/session_event_kinds.v1.json 对拍：工具触及的"
            "会话事件种类必须在契约词表内，且源码字面量与声明双向 diff=0；新增 kind 必须先改契约与声明。"),
        "event_stream_contract": "session_event_kinds",
        "tools": tools,
    }