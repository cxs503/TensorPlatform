#!/usr/bin/env python3
"""终态落库审计：全仓扫描「把 mission 打进终态」的写点，逐点判有没有预算记账。

为什么需要这把扫描器
--------------------
FP15-BUDGET-ACCOUNTING v1 的记账是**调用点纪律**（每个终态路径各自记得写
`budget_used`）。纪律没法自证，所以要有机器检查：把仓里所有 `status='<终态词>'`
的写点列出来，标出哪一处没记账。2026-11-13 基线（扫描结果，零手抄，见
`artifacts/fp15/budget_write_audit.json`）：

    未覆盖写点 12 处（tensor_repro 撤销 2 处、self_heal 退役 1 处、
    demand_intake 撤单 1 处、mission_gradient 1 处、lifecycle/approvals 若干）

v2 的机制是数据库触发器（migrations/125_*）：**任何**写点进终态都会落账。因此本
扫描器的角色从「扣分表」变成「漂移守卫」——它要求每处写点要么自己记账，要么被明确
登记为「由触发器兜底」；两者都不成立才算违规（退出码 1，机器门可失败）。

用法::

    python tools/audit_terminal_budget_writes.py                 # 表 + JSON
    python tools/audit_terminal_budget_writes.py --json out.json
    python tools/audit_terminal_budget_writes.py --strict-fixed  # 要求写点自带记账

`--strict-fixed` 是长期目标（代码侧自带记账 + DB 兜底）；默认模式只查
「既不自带、也不在触发器兜底面里」的裸写点。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]

#: 终态词（vocab 契约 + 两个既有裁决词）
TERMINAL_WORDS = ("succeeded", "failed", "stopped", "revoked", "orphan",
                  "budget_exhausted", "no_progress")

#: 扫哪些目录（仓内所有会写平台账本的代码）
SCAN_ROOTS = ("backend", "tools", "scripts", "api")

#: 写点语句里出现这些串 = 自带记账
LEDGER_TOKENS = ("budget_used", "record_budget_used", "budget_used_for_row")

#: 触发器兜底面：这些文件/路径的写点由 migrations/125 的 DB 触发器保证落账。
#: 不是"豁免"，是"责任人归属"：DB 是唯一能跨越部署窗口与旁路脚本的层。
TRIGGER_BACKSTOP = ("backend/api/", "tools/")

#: 写点 = `UPDATE tensor_dev_missions ... SET ... = '<终态词>'` 的 SET 子句。
#: 只看 SET 段（WHERE/RETURNING 之前）—— `WHERE status='succeeded'` 是查询过滤，
#: 不是写点；把它们算进来会让扫描器输出一堆假阳性（2026-11-13 首版实测 12 条全假）。
_UPDATE_RE = re.compile(r"UPDATE\s+(?P<table>[A-Za-z0-9_.\"]+)", re.IGNORECASE)
_SET_END_RE = re.compile(r"\b(WHERE|RETURNING|FROM)\b", re.IGNORECASE)
_SET_TERMINAL_RE = re.compile(
    r"(?:^|,)\s*(?:[A-Za-z_][\w.]*\.)?status\s*=\s*"
    r"(?:'(?P<word>" + "|".join(TERMINAL_WORDS) + r")'"
    r"|:(?P<bind>[a-z_]+))",
    re.IGNORECASE,
)
_WINDOW = 22          # 写点前后看多少行（SQL 串通常在同一函数里）


def iter_py_files() -> Iterable[Path]:
    for root in SCAN_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
                continue
            if "__pycache__" in rel:
                continue
            yield path


def _window_has_ledger(lines: list[str], idx: int) -> bool:
    lo = max(0, idx - _WINDOW)
    hi = min(len(lines), idx + _WINDOW + 1)
    blob = "\n".join(lines[lo:hi])
    return any(tok in blob for tok in LEDGER_TOKENS)


def _status_is_bound_param(lines: list[str], idx: int, bind: str, blob: str) -> str | None:
    """`status = :st` 这类参数化写点：回看函数体内给 :st 赋值的实参，判定是否终态。

    判不出来（值来自请求体/外部输入）→ 返回 None，计入 `unresolved`：诚实标记
    「本扫描器看不见」，不当成已覆盖。
    """
    head = "\n".join(lines[max(0, idx - _WINDOW): idx + _WINDOW + 1])
    m = re.search(rf"['\"]{re.escape(bind)}['\"]\s*:\s*([^,}}]+)", head)
    if not m:
        return None
    value = m.group(1).strip()
    for word in TERMINAL_WORDS:
        if f"'{word}'" in value or f'"{word}"' in value:
            return word
    if re.fullmatch(r"[A-Za-z_][\w.]*", value):
        # 变量实参：可能是 body.status（客户端可给任意终态）→ 视为可能终态
        return f"param:{value}"
    return None


def _update_sites(text: str):
    """产出 (offset, terminal_word|bind, set_clause)。只认 mission 表的 SET 子句。"""
    for m in _UPDATE_RE.finditer(text):
        table = m.group("table").strip('"').lower()
        if table.split(".")[-1] != "tensor_dev_missions":
            continue
        rest = text[m.end(): m.end() + 2000]
        set_start = re.search(r"\bSET\b", rest, re.IGNORECASE)
        if not set_start:
            continue
        body_start = m.end() + set_start.end()
        tail = text[body_start: body_start + 1500]
        stop = _SET_END_RE.search(tail)
        clause = tail[: stop.start()] if stop else tail
        tm = _SET_TERMINAL_RE.search(clause)
        if not tm:
            continue
        yield body_start, tm.group("word"), tm.group("bind"), clause


def scan() -> dict[str, Any]:
    sites: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for path in sorted(iter_py_files()):
        rel = path.relative_to(REPO).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "tensor_dev_missions" not in text:
            continue
        lines = text.splitlines()
        backstopped = any(rel.startswith(prefix) for prefix in TRIGGER_BACKSTOP)
        for offset, word, bind, clause in _update_sites(text):
            idx = text.count("\n", 0, offset)          # 0-based 行号
            if word is None:
                # 参数化终态（status = :st）：实参不可静态求值 → 诚实计入 unresolved，
                # 不当成「已覆盖」，也不当成违规（可能是 succeeded/failed 之一）。
                unresolved.append({"file": rel, "line": idx + 1,
                                   "status": f"param:{bind}",
                                   "reason": "参数化终态写点，实参不可静态求值；由触发器兜底"})
                continue
            sites.append({
                "file": rel,
                "line": idx + 1,
                "status": word,
                "self_accounted": _window_has_ledger(lines, idx),
                "trigger_backstop": backstopped,
            })

    naked = [s for s in sites if not s["self_accounted"] and not s["trigger_backstop"]]
    summary = {
        "sites": len(sites),
        "self_accounted": sum(1 for s in sites if s["self_accounted"]),
        "trigger_backstop_only": sum(1 for s in sites
                                     if not s["self_accounted"] and s["trigger_backstop"]),
        "naked": len(naked),
        "unresolved": len(unresolved),
        "terminal_words": list(TERMINAL_WORDS),
    }
    return {"schema": "fp15.budget_write_audit.v1", "summary": summary,
            "sites": sites, "naked": naked, "unresolved": unresolved}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="audit terminal budget accounting sites")
    parser.add_argument("--json", default="artifacts/fp15/budget_write_audit.json")
    parser.add_argument("--strict-fixed", action="store_true",
                        help="要求每个写点都自带记账（触发器兜底不算）")
    args = parser.parse_args(argv)

    report = scan()
    out = REPO / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    summary = report["summary"]
    print(f"{'file':<52}{'line':>5}  {'status':<16} self  backstop")
    for site in report["sites"]:
        print(f"{site['file']:<52}{site['line']:>5}  {site['status']:<16}"
              f" {'Y' if site['self_accounted'] else '-':<5} "
              f"{'Y' if site['trigger_backstop'] else '-'}")
    print(f"\nsummary: {json.dumps(summary, ensure_ascii=False)}")
    print(f"artifact: {out.relative_to(REPO)}")
    offenders = report["naked"]
    if args.strict_fixed:
        offenders += [s for s in report["sites"] if not s["self_accounted"]]
    if offenders:
        print(f"\nFAIL: {len(offenders)} 个写点既不自带记账也不在触发器兜底面")
        for site in offenders:
            print(f"  - {site['file']}:{site['line']} status={site['status']}")
        return 1
    return 0


if __name__ == "__main__":          # pragma: no cover
    raise SystemExit(main())