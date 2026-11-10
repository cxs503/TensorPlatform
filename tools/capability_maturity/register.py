"""审计锚：把 ``audit_capabilities`` 的**同一份渲染正文**解析成机器可裁决的审计表。

为什么要解析正文而不是另读一份台账：平台的 ``audit_capabilities`` 工具正文由
``tools.audit.ledger_consumer_audit_tool.audit_capabilities()`` 渲染，它的候选序
（env > resolved > harness > canonical > falsification）与台账契约门绑定。
只要审计表**从这段正文派生**，A1（「审计表与 audit_capabilities 输出一致」）就是
结构性成立、可被测试断言逐字核对的，而不是靠人抄一遍。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import AuditRow, AuditTable

ROOT = Path(__file__).resolve().parents[2]

#: 一行能力：``cap.id  自述 REAL  对账 WITHHELD  UNVERIFIED 域 几何``
_ROW = re.compile(
    r"^(?P<cap>[A-Za-z0-9_.\-]+)\s+自述\s+(?P<decl>[A-Z_]+)\s+对账\s+(?P<recon>[A-Z_]+)"
    r"\s+(?P<div>[A-Z_]+)\s+域\s+(?P<domain>.+?)\s*$"
)
#: 表头：``能力清单 (21 个；证据行 10；分歧 ...):``
_HEAD = re.compile(r"^能力清单 \((?P<count>\d+) 个")
_SOURCE = re.compile(r"^\[真值来源\]\s*(?P<path>.+?)\s*$")
_CONTRACT = re.compile(r"^\[台账契约\]\s*(?P<body>.+?)\s*$")


def capture_audit_text() -> str:
    """跑一次本仓的 ``audit_capabilities`` 渲染器，拿逐字正文（同一渲染路径）。"""
    from tools.audit.ledger_consumer_audit_tool import audit_capabilities

    return audit_capabilities()


def parse_audit_text(text: str) -> AuditTable:
    rows: List[AuditRow] = []
    counted: Optional[int] = None
    source = ""
    contract: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEAD.match(line)
        if m:
            counted = int(m.group("count"))
            continue
        m = _SOURCE.match(line)
        if m:
            source = m.group("path")
            continue
        m = _CONTRACT.match(line)
        if m:
            for tok in m.group("body").split():
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    contract[k] = v
            continue
        m = _ROW.match(line)
        if m:
            rows.append(AuditRow(cap_id=m.group("cap"), declared=m.group("decl"),
                                 reconciled=m.group("recon"), divergence=m.group("div"),
                                 domain=m.group("domain")))
    return AuditTable(rows=rows, counted=counted, source_path=source, contract=contract,
                      text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def load_audit_table(text: Optional[str] = None) -> AuditTable:
    """默认走真实捕获；测试/离线复核可注入 ``text``。"""
    return parse_audit_text(text if text is not None else capture_audit_text())


def renderer_ids(text: Optional[str] = None) -> List[str]:
    """对照口径：正文里出现的全部能力 id（A1 对称差用）。"""
    return sorted(r.cap_id for r in load_audit_table(text).rows)


def compare_with_renderer(table: AuditTable, text: Optional[str] = None) -> Dict[str, Any]:
    """A1 判据：审计表 vs ``audit_capabilities`` 正文的 id 集合与等级一致性。"""
    ref = parse_audit_text(text if text is not None else capture_audit_text())
    mine = {r.cap_id: (r.declared, r.reconciled) for r in table.rows}
    theirs = {r.cap_id: (r.declared, r.reconciled) for r in ref.rows}
    only_mine = sorted(set(mine) - set(theirs))
    only_theirs = sorted(set(theirs) - set(mine))
    mismatched = {k: {"table": mine[k], "renderer": theirs[k]}
                  for k in sorted(set(mine) & set(theirs)) if mine[k] != theirs[k]}
    return {
        "table_rows": len(mine),
        "renderer_rows": len(theirs),
        "symmetric_difference": len(only_mine) + len(only_theirs),
        "only_in_table": only_mine,
        "only_in_renderer": only_theirs,
        "level_mismatch": mismatched,
        "agreement": (len(set(mine) & set(theirs)) - len(mismatched)),
        "pass": (not only_mine and not only_theirs and not mismatched
                 and len(theirs) == len(mine)),
    }
